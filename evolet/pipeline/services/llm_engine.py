"""
LLM Engine — Qwen model loading, prompt construction, and batch inference.
===========================================================================
This module owns everything GPU-related for the extraction phase:

  Model management (singleton pattern)
  -------------------------------------
  load_model()        Load (or return cached) Qwen 2.5 1.5B + tokenizer.
  unload_model()      Free GPU memory when the LLM phase is done.
  is_model_loaded()   Simple flag check used by views / orchestrator.

  Inference pipeline
  ------------------
  process_unresolved_notes() — public entry point.  Takes a list of notes
    that the triage stage flagged for LLM processing, splits them into
    adaptive batches (by word count), runs generation, parses JSON output,
    and returns a flat list of extracted mention dicts.

  Adaptive batching strategy
  --------------------------
  Notes are sorted into four buckets based on word count:
    short  (≤120 words) → batch_size=3  (pack more to fill GPU)
    medium (≤220 words) → batch_size=2
    long   (≤380 words) → batch_size=1
    xlong  (>380 words) → batch_size=1, higher token budget

  This approach avoids both GPU OOM (from packing too many long notes) and
  idle GPU time (from under-utilising it with tiny notes one-at-a-time).
"""

import json
import time
import logging
import threading
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import torch

from . import config
from .pdf_extractor import normalize_text, word_count
from .note_segmenter import extract_dates
from .json_utils import parse_json_loose
from .gpu_utils import detect_compute_dtype, release_cuda

logger = logging.getLogger("pipeline")
GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"


# ─────────────────────────────────────────────────────────────────────────────
# Singleton model holder
# ─────────────────────────────────────────────────────────────────────────────
# Stored at module level so the heavy model is loaded once per process and
# reused across all batches (loading Qwen takes ~30 s).

_model     = None
_tokenizer = None
_model_id  = None
_MODEL_LOCK = threading.RLock()

# Ordered bucket names used for sorting and index lookup
_BUCKET_ORDER = ["short", "medium", "long", "xlong"]
_BUCKET_INDEX = {b: i for i, b in enumerate(_BUCKET_ORDER)}


# ─────────────────────────────────────────────────────────────────────────────
# Model management
# ─────────────────────────────────────────────────────────────────────────────

def load_model(model_id: str = None, force_reload: bool = False, use_4bit: Optional[bool] = None):
    """
    Load the LLM and tokenizer, returning (tokenizer, model).

    If the requested model is already loaded, return the cached instance
    immediately (fast path).  Set force_reload=True to swap models.

    Quantisation
    ------------
    When USE_4BIT=True and CUDA is available, loads in 4-bit NF4 via
    bitsandbytes.  This cuts VRAM usage from ~3 GB (fp16) to ~1.5 GB,
    allowing the model to coexist with DocTR on a 24 GB GPU.

    Tokenizer settings
    ------------------
    - padding_side = "left"     → required for left-padded batch generation
    - truncation_side = "left"  → keeps the end of long notes (usually richer)
    """
    global _model, _tokenizer, _model_id

    with _MODEL_LOCK:
        return _load_model_locked(model_id, force_reload, use_4bit)


def _load_model_locked(model_id: str = None, force_reload: bool = False, use_4bit: Optional[bool] = None):
    global _model, _tokenizer, _model_id

    model_id = model_id or config.MODEL_ID

    # Fast path: already loaded with the same ID
    if _model is not None and _model_id == model_id and not force_reload:
        return _tokenizer, _model

    unload_model()   # Free any previously loaded model first

    from transformers import AutoTokenizer, AutoModelForCausalLM, GenerationConfig

    compute_dtype = detect_compute_dtype()
    use_4bit = config.USE_4BIT if use_4bit is None else bool(use_4bit)
    logger.info("Loading model: %s | dtype=%s | 4bit=%s", model_id, compute_dtype, use_4bit)

    # Common kwargs forwarded to both tokenizer and model
    common_kwargs: Dict[str, Any] = {"local_files_only": config.LOCAL_FILES_ONLY}
    if config.HF_TOKEN:
        common_kwargs["token"] = config.HF_TOKEN

    # Tokenizer — left-padded for generation, left-truncated to keep tail
    tokenizer = AutoTokenizer.from_pretrained(model_id, use_fast=True, **common_kwargs)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token   # many instruct models lack a pad token
    tokenizer.padding_side   = "left"
    tokenizer.truncation_side = "left"

    # Quantisation config (skip if no GPU or 4-bit disabled)
    quant_config = None
    if use_4bit and torch.cuda.is_available():
        from transformers import BitsAndBytesConfig

        quant_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_compute_dtype=compute_dtype,
            bnb_4bit_quant_type="nf4",          # NF4 is better than int4 for activations
            bnb_4bit_use_double_quant=True,      # extra ~0.5 bit savings
        )

    # Model — SDPA attention is faster than standard on PyTorch ≥ 2.0
    model = AutoModelForCausalLM.from_pretrained(
        model_id,
        quantization_config=quant_config,
        torch_dtype=compute_dtype,
        device_map="auto",          # let accelerate pick GPU/CPU placement
        low_cpu_mem_usage=True,     # stream weights from disk, reduces peak RAM
        attn_implementation="sdpa", # scaled dot-product attention (PyTorch 2.0+)
        **common_kwargs,
    )
    model.eval()
    model.config.use_cache = True   # enable KV-cache for faster generation

    # Attach a GenerationConfig so generate() inherits these defaults
    gen_cfg = GenerationConfig.from_model_config(model.config)
    gen_cfg.do_sample      = config.DO_SAMPLE
    gen_cfg.max_new_tokens = config.MAX_NEW_TOKENS
    gen_cfg.use_cache      = True
    gen_cfg.pad_token_id   = tokenizer.pad_token_id
    gen_cfg.eos_token_id   = tokenizer.eos_token_id
    model.generation_config = gen_cfg

    _model     = model
    _tokenizer = tokenizer
    _model_id  = model_id
    logger.info("Model loaded: %s", model_id)
    return _tokenizer, _model


def unload_model() -> None:
    """
    Delete the model/tokenizer globals and release GPU memory.

    Should be called by the orchestrator after the LLM phase completes
    so that subsequent pipeline stages (merge, QC, photo) have full RAM.
    """
    with _MODEL_LOCK:
        _unload_model_locked()


def _unload_model_locked() -> None:
    global _model, _tokenizer, _model_id
    if _model is not None:
        del _model
    if _tokenizer is not None:
        del _tokenizer
    _model     = None
    _tokenizer = None
    _model_id  = None
    release_cuda()
    logger.info("Model unloaded and GPU memory released")


def is_model_loaded() -> bool:
    """Return True if a model is currently held in memory."""
    return _model is not None


# ─────────────────────────────────────────────────────────────────────────────
# Prompt construction
# ─────────────────────────────────────────────────────────────────────────────

def _trim_note_text(text: str, max_chars: int = 9000) -> str:
    """
    Trim *text* to *max_chars* while keeping both head and tail.

    Head-biased split (65% head, 35% tail) because clinical notes
    typically open with diagnosis/history (most important) and close
    with plan (also important), while the middle often contains
    repetitive or administrative text.
    """
    text = normalize_text(text)
    if len(text) <= max_chars:
        return text

    head = int(max_chars * 0.65)
    tail = max_chars - head - 32   # 32 chars for the truncation marker

    return (
        text[:head].rstrip()
        + "\n\n...[middle truncated for token budget]...\n\n"
        + text[-tail:].lstrip()
    )


def _build_prompt(note: Dict[str, Any]) -> str:
    """
    Build the user-turn prompt for a single note.

    Includes note_id and page_num so the model can populate evidence_ids
    correctly, and a hint about how many regex mentions were already found
    (helps the model avoid duplicating well-covered information).
    """
    note_text = _trim_note_text(note["text"])
    return (
        f"note_id={note['note_id']}\n"
        f"page_num={note['page_num']}\n"
        f"regex_mentions_already_found={len(note.get('regex_mentions', []))}\n\n"
        "Extract all meaningful structured content from this note in depth and return JSON only. "
        "Do not summarize only the highlights; preserve every identifiable field, table row, "
        "date, value, instruction, observation, medication, investigation, diagnosis, plan, "
        "identifier, and administrative detail as separate mentions when possible.\n\n"
        f"note_text:\n{note_text}"
    ).strip()


# ─────────────────────────────────────────────────────────────────────────────
# Adaptive batching
# ─────────────────────────────────────────────────────────────────────────────

def _note_bucket(note: Dict[str, Any]) -> str:
    """Assign a note to a word-count bucket for adaptive batch sizing."""
    wc = word_count(note.get("text", ""))
    if wc <= config.SHORT_NOTE_WORDS:
        return "short"
    if wc <= config.MEDIUM_NOTE_WORDS:
        return "medium"
    if wc <= config.LONG_NOTE_WORDS:
        return "long"
    return "xlong"


def _batch_size_for(bucket: str) -> int:
    """Return the max number of notes per GPU batch for *bucket*."""
    return {
        "short":  config.SHORT_BATCH_SIZE,
        "medium": config.MEDIUM_BATCH_SIZE,
        "long":   config.LONG_BATCH_SIZE,
        "xlong":  config.XLONG_BATCH_SIZE,
    }.get(bucket, 1)


def _max_tokens_for(bucket: str) -> int:
    """
    Return the max_new_tokens budget for *bucket*.

    Long/xlong notes need a larger output budget because they tend to
    contain more mentions (more JSON to generate).
    """
    return (
        config.RETRY_MAX_NEW_TOKENS
        if bucket in {"long", "xlong"}
        else config.MAX_NEW_TOKENS
    )


def _make_batches(notes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Group notes into homogeneous batches for efficient GPU utilisation.

    Notes are sorted by (bucket, word_count) so shorter notes are processed
    first (allows early progress reporting) and same-bucket notes are grouped
    together.  A new batch is started whenever the bucket changes or the
    current batch reaches its size limit.

    Returns list of batch dicts:
      {"bucket": str, "max_new_tokens": int, "notes": List[Dict]}
    """
    if not notes:
        return []

    # Sort: bucket order first, then word count ascending within bucket
    ordered = sorted(
        notes,
        key=lambda n: (
            _BUCKET_INDEX.get(_note_bucket(n), 99),
            word_count(n.get("text", "")),
        ),
    )

    batches: List[Dict[str, Any]] = []
    current: List[Dict[str, Any]] = []
    current_bucket: Optional[str] = None

    for note in ordered:
        bucket = _note_bucket(note)
        limit  = _batch_size_for(bucket)

        # Flush current batch if bucket changed or batch is full
        if current and (bucket != current_bucket or len(current) >= limit):
            batches.append({
                "bucket":         current_bucket,
                "max_new_tokens": _max_tokens_for(current_bucket),
                "notes":          current,
            })
            current = []

        current_bucket = bucket
        current.append(note)

    if current:
        batches.append({
            "bucket":         current_bucket,
            "max_new_tokens": _max_tokens_for(current_bucket),
            "notes":          current,
        })

    return batches


# ─────────────────────────────────────────────────────────────────────────────
# Generation
# ─────────────────────────────────────────────────────────────────────────────

def _generate_batch(
    prompts: List[str],
    max_new_tokens: int = config.MAX_NEW_TOKENS,
) -> List[str]:
    """
    Run a single batched generation pass on the loaded model.

    Steps:
    1. Apply the model's chat template to each prompt (adds special tokens).
    2. Tokenise + left-pad the batch to uniform length.
    3. Move tensors to the model's device.
    4. Run model.generate() under torch.inference_mode (no grad tracking).
    5. Slice off the prompt tokens from the output; decode only new tokens.

    Returns a list of decoded strings, one per input prompt.
    """
    if config.LLM_PROVIDER == "groq":
        return _generate_batch_groq(prompts, max_new_tokens=max_new_tokens)

    return _generate_batch_local(prompts, max_new_tokens=max_new_tokens)


def _generate_batch_local(
    prompts: List[str],
    max_new_tokens: int = config.MAX_NEW_TOKENS,
    system_prompt: str | None = None,
) -> List[str]:
    """Run a batched generation pass on the loaded local HF model."""
    with _MODEL_LOCK:
        return _generate_batch_local_locked(
            prompts,
            max_new_tokens=max_new_tokens,
            system_prompt=system_prompt,
        )


def _generate_batch_local_locked(
    prompts: List[str],
    max_new_tokens: int = config.MAX_NEW_TOKENS,
    system_prompt: str | None = None,
) -> List[str]:
    if _tokenizer is None or _model is None:
        raise RuntimeError("Model not loaded — call load_model() before inference.")
    system_prompt = system_prompt or config.SYSTEM_PROMPT

    # Format as chat turns using the model's own chat template
    rendered = [
        _tokenizer.apply_chat_template(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": p},
            ],
            tokenize=False,
            add_generation_prompt=True,
        )
        for p in prompts
    ]

    # Tokenise with left-padding (required for batched left-to-right generation)
    inputs = _tokenizer(
        rendered,
        return_tensors="pt",
        padding=True,
        truncation=True,
        max_length=config.MAX_INPUT_TOKENS,
    )
    prompt_len = inputs["input_ids"].shape[1]   # length of the padded prompt

    device = next(_model.parameters()).device
    inputs = {k: v.to(device) for k, v in inputs.items()}

    t0 = time.time()
    with torch.inference_mode():
        outputs = _model.generate(
            **inputs,
            max_new_tokens=max_new_tokens,
            do_sample=config.DO_SAMPLE,
            use_cache=True,
            pad_token_id=_tokenizer.pad_token_id,
            eos_token_id=_tokenizer.eos_token_id,
        )
    elapsed = time.time() - t0

    logger.info(
        "LLM batch=%d  prompt_tokens=%d  max_new=%d  time=%.1fs",
        len(prompts), prompt_len, max_new_tokens, elapsed,
    )

    # Decode only the newly generated tokens (slice off the prompt prefix)
    decoded = []
    for i in range(outputs.shape[0]):
        new_tokens = outputs[i][prompt_len:]
        decoded.append(_tokenizer.decode(new_tokens, skip_special_tokens=True).strip())
    return decoded


def generate_local_prompts(
    prompts: List[str],
    *,
    model_id: str | None = None,
    max_new_tokens: int = config.MAX_NEW_TOKENS,
    use_4bit: Optional[bool] = None,
    unload_after: bool = False,
    system_prompt: str | None = None,
) -> List[str]:
    """Generate with a local HF model even when the main provider is Groq."""
    with _MODEL_LOCK:
        load_model(model_id=model_id or config.MODEL_ID, use_4bit=use_4bit)
        try:
            return _generate_batch_local(
                prompts,
                max_new_tokens=max_new_tokens,
                system_prompt=system_prompt,
            )
        finally:
            if unload_after:
                unload_model()


def _generate_batch_groq(
    prompts: List[str],
    max_new_tokens: int = config.MAX_NEW_TOKENS,
) -> List[str]:
    if not config.GROQ_API_KEY:
        raise RuntimeError("Groq extraction requested but DOC_READER_GROQ_API_KEY/GROQ_API_KEY is missing")

    outputs: List[str] = []
    for prompt in prompts:
        body = {
            "model": config.GROQ_EXTRACTION_MODEL,
            "temperature": 0,
            "max_completion_tokens": max_new_tokens,
            "messages": [
                {"role": "system", "content": config.SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
        }
        t0 = time.time()
        data = _post_groq(body)
        elapsed = time.time() - t0
        logger.info(
            "Groq LLM note model=%s max_new=%d time=%.1fs",
            config.GROQ_EXTRACTION_MODEL,
            max_new_tokens,
            elapsed,
        )
        outputs.append(data["choices"][0]["message"]["content"].strip())
    return outputs


def _post_groq(body: Dict[str, Any]) -> Dict[str, Any]:
    last_error = ""
    for attempt in range(4):
        req = Request(
            GROQ_CHAT_URL,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {config.GROQ_API_KEY}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "doc-reader-groq-extraction/1.0",
            },
            method="POST",
        )
        try:
            with urlopen(req, timeout=config.GROQ_EXTRACTION_TIMEOUT_SECONDS) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            last_error = f"Groq HTTP {exc.code}: {detail[:500]}"
            if exc.code != 429 or attempt == 3:
                raise RuntimeError(last_error) from exc
            time.sleep(_retry_delay_seconds(exc, detail, attempt))
        except URLError as exc:
            raise RuntimeError(f"Groq request failed: {exc}") from exc
    raise RuntimeError(last_error or "Groq request failed")


def _retry_delay_seconds(exc: HTTPError, detail: str, attempt: int) -> float:
    retry_after = exc.headers.get("Retry-After")
    if retry_after:
        try:
            return min(float(retry_after), 70.0)
        except ValueError:
            pass
    import re

    match = re.search(r"try again in ([0-9.]+)s", detail, flags=re.IGNORECASE)
    if match:
        return min(float(match.group(1)) + 0.75, 70.0)
    return 2.5 * (attempt + 1)


# ─────────────────────────────────────────────────────────────────────────────
# Mention post-processing
# ─────────────────────────────────────────────────────────────────────────────

def _clean_llm_mention(
    raw: Dict[str, Any],
    note: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """
    Validate and normalise a single mention dict from the LLM output.

    Rejects the mention if both value and label are empty (noise).
    Normalises the certainty field to the allowed vocabulary.
    Falls back to the note's first date if the mention carries no date.

    Returns a clean mention dict, or None if the mention is invalid.
    """
    if not isinstance(raw, dict):
        return None

    value = normalize_text(raw.get("value", ""))
    label = normalize_text(raw.get("label", ""))
    if not value and not label:
        return None   # empty mention — discard

    category  = normalize_text(raw.get("category", "")) or "unknown"
    certainty = str(raw.get("certainty", "unknown")).strip().lower()
    if certainty not in {"confirmed", "possible", "ruled_out", "unknown"}:
        certainty = "unknown"

    # Fall back to the note's first detected date if the LLM left date_text blank
    date_text = normalize_text(raw.get("date_text", ""))
    if not date_text:
        dates = extract_dates(note["text"])
        date_text = dates[0] if dates else ""

    return {
        "category":         category,
        "label":            label or category,
        "value":            value or label,
        "normalized_value": normalize_text(raw.get("normalized_value", "")) or value or label,
        "date_text":        date_text,
        "certainty":        certainty,
        "attributes":       raw.get("attributes", {}) if isinstance(raw.get("attributes"), dict) else {},
        "source_pages":     [note["page_num"]],
        "evidence_ids":     [note["note_id"]],
        "evidence_quote":   normalize_text(raw.get("evidence_quote", ""))[:240],
        "origin":           "llm",
    }


def _parse_mentions(raw_text: str, note: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Parse the LLM's raw text output into a list of cleaned mention dicts.

    1. parse_json_loose() handles malformed JSON (code fences, trailing commas…)
    2. Each raw mention is cleaned/validated via _clean_llm_mention()
    3. None results (invalid mentions) are filtered out
    """
    parsed   = parse_json_loose(raw_text)
    raw_list = parsed.get("mentions", []) if isinstance(parsed, dict) else []
    cleaned  = [_clean_llm_mention(m, note) for m in raw_list]
    return [m for m in cleaned if m is not None]


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def process_unresolved_notes(
    unresolved_notes: List[Dict[str, Any]],
    progress_callback=None,
) -> List[Dict[str, Any]]:
    """
    Process all unresolved notes through the LLM with adaptive batching.

    Flow
    ----
    1. Group notes into buckets by word count → build batch list.
    2. For each batch:
       a. Build prompts from the notes.
       b. Run _generate_batch() → list of raw text responses.
       c. For each note/response pair:
          - Try to parse mentions from the response.
          - On parse failure, retry once with a larger token budget.
          - Record failures for final logging.
    3. Call progress_callback(done, total) after each batch.
    4. Return a flat list of all successfully extracted mention dicts.

    Parameters
    ----------
    unresolved_notes  : notes from triage_notes()["unresolved"]
    progress_callback : optional callable(done: int, total: int) for UI updates

    Returns
    -------
    List of mention dicts (origin="llm") ready for the merge phase.
    """
    if not unresolved_notes:
        return []

    all_mentions: List[Dict[str, Any]] = []
    failures:     List[Dict[str, Any]] = []
    batches     = _make_batches(unresolved_notes)
    total_batches = len(batches)

    for batch_idx, batch_info in enumerate(batches):
        batch_notes = batch_info["notes"]
        prompts     = [_build_prompt(n) for n in batch_notes]

        # Generate responses for the whole batch
        try:
            outputs = _generate_batch(prompts, max_new_tokens=batch_info["max_new_tokens"])
        except Exception as exc:
            logger.error("LLM batch %d/%d failed: %s", batch_idx + 1, total_batches, exc)
            failures.extend({"note_id": n["note_id"], "error": str(exc)} for n in batch_notes)
            continue

        # Parse each response individually
        for note, raw_text in zip(batch_notes, outputs):
            try:
                mentions = _parse_mentions(raw_text, note)
                all_mentions.extend(mentions)
            except Exception as exc:
                # Retry once with a larger token budget (the first pass may have been truncated)
                try:
                    retry_text = _generate_batch(
                        [_build_prompt(note)],
                        max_new_tokens=config.RETRY_MAX_NEW_TOKENS,
                    )[0]
                    mentions = _parse_mentions(retry_text, note)
                    all_mentions.extend(mentions)
                except Exception as retry_exc:
                    failures.append({
                        "note_id":     note["note_id"],
                        "error":       str(exc),
                        "retry_error": str(retry_exc),
                    })

        if progress_callback:
            progress_callback(batch_idx + 1, total_batches)

    if failures:
        logger.warning("LLM processing had %d failures", len(failures))

    return all_mentions
