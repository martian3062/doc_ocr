"""Multimodal medicine-name ensemble for handwritten prescription crops.

This layer is intentionally crop-level. It does not try to OCR whole PDFs.
It combines:
- KeraCare/Dots OCR fine-tuned for prescription drug names
- Donut prescription OCR text
- Phi3 prescription text interpretation over the available OCR/context text
- local drug dictionary and common doctor-handwriting normalization
"""

from __future__ import annotations

import json
import logging
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List

from PIL import Image

from . import config
from .json_utils import parse_json_loose
from .medical_short_forms import find_drug_candidates, normalize_order_text
from .pdf_extractor import normalize_text

logger = logging.getLogger("pipeline")

_MODEL_CACHE: Dict[str, Any] = {}


@dataclass
class MedicineCandidate:
    raw: str
    normalized: str
    score: int
    source: str
    evidence: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "raw": self.raw,
            "normalized": self.normalized,
            "score": self.score,
            "source": self.source,
            "evidence": self.evidence,
        }


def extract_multimodal_medicines(image: Image.Image, *, context_text: str = "") -> Dict[str, Any]:
    if not config.ENABLE_MULTIMODAL_MEDICINE_EXTRACTOR:
        return _empty_result("disabled")

    context_text = normalize_text(context_text)
    image = _bounded_hf_image(image)
    sources: List[Dict[str, Any]] = []
    candidates: List[MedicineCandidate] = []

    for reader in (_run_keracare_reader, _run_donut_reader, _run_phi_reader):
        try:
            result = reader(image, context_text=context_text)
        except Exception as exc:
            _release_cuda()
            logger.info("Medicine ensemble reader %s skipped: %s", reader.__name__, exc)
            result = {"source": reader.__name__, "status": "failed", "error": str(exc)[:300], "text": "", "drug_names": []}
        sources.append(result)
        candidates.extend(_candidates_from_result(result))

    if context_text:
        sources.append({"source": "context_dictionary", "status": "ok", "text": context_text[:500], "drug_names": []})
        candidates.extend(_dictionary_candidates(context_text, "context_dictionary"))

    merged = _merge_candidates(candidates)
    order_items = [
        {
            "raw_text": item.raw,
            "drug": item.normalized,
            "dose": _nearby_dose(item),
            "route": "",
            "fluid": "",
            "frequency": "",
            "duration": "",
            "instruction": "",
            "uncertain": item.score < config.MULTIMODAL_MEDICINE_MIN_SCORE,
            "source": item.source,
            "ensemble_score": item.score,
            "evidence": item.evidence,
        }
        for item in merged
        if item.score >= config.MULTIMODAL_MEDICINE_MIN_SCORE
    ]
    return {
        "status": "ok",
        "drug_names": [item.normalized for item in merged if item.score >= config.MULTIMODAL_MEDICINE_MIN_SCORE],
        "candidates": [item.as_dict() for item in merged],
        "order_items": order_items,
        "sources": sources,
    }


def _nearby_dose(item: MedicineCandidate) -> str:
    evidence = normalize_text(item.evidence)
    if not evidence:
        return normalize_order_text(item.raw).get("dose", "")
    needles = [item.raw, item.normalized, item.raw[:6], item.normalized[:6]]
    match_pos = -1
    lowered = evidence.lower()
    for needle in needles:
        needle = normalize_text(needle).lower()
        if len(needle) >= 3 and needle in lowered:
            match_pos = lowered.find(needle)
            break
    if match_pos < 0:
        return ""
    forward = evidence[match_pos : min(len(evidence), match_pos + 90)]
    dose = normalize_order_text(forward).get("dose", "")
    if dose:
        return dose
    start = max(0, match_pos - 20)
    end = min(len(evidence), match_pos + 60)
    return normalize_order_text(evidence[start:end]).get("dose", "")


def _run_keracare_reader(image: Image.Image, *, context_text: str) -> Dict[str, Any]:
    if "keracare" not in config.MULTIMODAL_MEDICINE_BACKENDS:
        return {"source": "keracare", "status": "disabled", "drug_names": [], "text": ""}
    if not config.ENABLE_LOCAL_HF_VISION_MODELS:
        return {"source": "keracare", "status": "local_hf_vision_disabled", "drug_names": [], "text": ""}

    torch = _import_torch()
    AutoModelForCausalLM, AutoProcessor = _import_auto_causal_and_processor()
    device = _device(torch)
    dtype = torch.bfloat16 if device == "cuda" else torch.float32
    model_key = f"keracare:{config.KERACARE_MEDICINE_MODEL_ID}:{device}"

    if model_key not in _MODEL_CACHE:
        processor = AutoProcessor.from_pretrained(config.KERACARE_MEDICINE_MODEL_ID, trust_remote_code=True, token=config.HF_TOKEN)
        model = AutoModelForCausalLM.from_pretrained(
            config.KERACARE_MEDICINE_MODEL_ID,
            trust_remote_code=True,
            torch_dtype=dtype,
            token=config.HF_TOKEN,
            attn_implementation="sdpa",
        ).to(device)
        model.eval()
        _MODEL_CACHE[model_key] = (processor, model)
    processor, model = _MODEL_CACHE[model_key]

    prompt = """
You are an assistant that extracts drug names from prescription images (primarily French, sometimes English), even if noisy, blurry, or with background clutter.
Rules: return only drug names. Normalize spelling to the closest valid INN/brand as written (e.g., preserve brand vs. generic identity and combination names), deduplicate, and sort the drug names in lexical order. Do not invent or map to equivalents; if none are found, return an empty list.
Strip accent marks and special characters, and convert to lowercase.

Output strict JSON only in the following format:
{
    "drug_names": [
        "<drug_name_1>",
        "<drug_name_2>"
    ]
}
""".strip()
    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": [{"type": "image", "image": image.convert("RGB")}]},
    ]
    text = processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=False)
    try:
        inputs = processor(text=[text], images=[image.convert("RGB")], padding=True, return_tensors="pt").to(device)
    except Exception:
        process_vision_info = _import_qwen_vl_utils()
        with _temp_image(image) as image_path:
            file_messages = [
                {"role": "system", "content": prompt},
                {"role": "user", "content": [{"type": "image", "image": str(image_path)}]},
            ]
            text = processor.apply_chat_template(file_messages, tokenize=False, add_generation_prompt=False)
            images, videos = process_vision_info(file_messages)
            if videos:
                inputs = processor(text=[text], images=images, videos=videos, padding=True, return_tensors="pt").to(device)
            else:
                inputs = processor(text=[text], images=images, padding=True, return_tensors="pt").to(device)
    with torch.no_grad():
        outputs = model.generate(
            **inputs,
            max_new_tokens=config.MULTIMODAL_MEDICINE_MAX_NEW_TOKENS,
            repetition_penalty=1.1,
            eos_token_id=processor.tokenizer.eos_token_id,
            do_sample=False,
        )
    input_len = inputs["input_ids"].shape[-1]
    decoded = processor.batch_decode(outputs[:, input_len:], skip_special_tokens=True)[0]
    parsed = _parse_optional_json(decoded)
    return {"source": "keracare", "status": "ok", "text": decoded, "drug_names": _drug_names(parsed, decoded)}


def _run_donut_reader(image: Image.Image, *, context_text: str) -> Dict[str, Any]:
    if "donut" not in config.MULTIMODAL_MEDICINE_BACKENDS:
        return {"source": "donut", "status": "disabled", "drug_names": [], "text": ""}
    if not config.ENABLE_LOCAL_HF_VISION_MODELS:
        return {"source": "donut", "status": "local_hf_vision_disabled", "drug_names": [], "text": ""}

    torch = _import_torch()
    from transformers import DonutProcessor, VisionEncoderDecoderModel

    device = _device(torch)
    model_key = f"donut:{config.DONUT_PRESCRIPTION_MODEL_ID}:{device}"
    if model_key not in _MODEL_CACHE:
        processor = DonutProcessor.from_pretrained(config.DONUT_PRESCRIPTION_MODEL_ID, token=config.HF_TOKEN)
        model = VisionEncoderDecoderModel.from_pretrained(config.DONUT_PRESCRIPTION_MODEL_ID, token=config.HF_TOKEN).to(device)
        model.eval()
        _MODEL_CACHE[model_key] = (processor, model)
    processor, model = _MODEL_CACHE[model_key]

    pixel_values = processor(images=image.convert("RGB"), return_tensors="pt").pixel_values.to(device)
    decoder_input_ids = processor.tokenizer("<s_ocr>", return_tensors="pt").input_ids.to(device)
    with torch.no_grad():
        generated_ids = model.generate(
            pixel_values,
            decoder_input_ids=decoder_input_ids,
            max_length=config.MULTIMODAL_MEDICINE_MAX_NEW_TOKENS,
            num_beams=1,
            early_stopping=True,
        )
    text = processor.batch_decode(generated_ids, skip_special_tokens=True)[0]
    return {"source": "donut", "status": "ok", "text": text, "drug_names": []}


def _run_phi_reader(image: Image.Image, *, context_text: str) -> Dict[str, Any]:
    if "phi3" not in config.MULTIMODAL_MEDICINE_BACKENDS:
        return {"source": "phi3", "status": "disabled", "drug_names": [], "text": ""}
    if not config.ENABLE_LOCAL_HF_LLM:
        return {"source": "phi3", "status": "local_hf_llm_disabled", "drug_names": [], "text": ""}
    if not context_text:
        return {"source": "phi3", "status": "no_context_text", "drug_names": [], "text": ""}

    torch = _import_torch()
    from transformers import AutoModelForCausalLM, AutoTokenizer

    device = _device(torch)
    dtype = torch.bfloat16 if device == "cuda" else torch.float32
    model_key = f"phi3:{config.PHI3_PRESCRIPTION_MODEL_ID}:{device}"
    if model_key not in _MODEL_CACHE:
        tokenizer = AutoTokenizer.from_pretrained(config.PHI3_PRESCRIPTION_MODEL_ID, token=config.HF_TOKEN, trust_remote_code=True)
        model = AutoModelForCausalLM.from_pretrained(
            config.PHI3_PRESCRIPTION_MODEL_ID,
            token=config.HF_TOKEN,
            trust_remote_code=True,
            torch_dtype=dtype,
        ).to(device)
        model.eval()
        _MODEL_CACHE[model_key] = (tokenizer, model)
    tokenizer, model = _MODEL_CACHE[model_key]

    prompt = (
        "Read the following prescription/OCR text and extract medicine names, dosages, routes, fluids, and instructions. "
        "Return strict JSON with drug_names and medication_orders only.\n\n"
        f"{context_text[:2500]}"
    )
    inputs = tokenizer(prompt, return_tensors="pt").to(device)
    with torch.no_grad():
        outputs = model.generate(
            **inputs,
            max_new_tokens=config.MULTIMODAL_MEDICINE_MAX_NEW_TOKENS,
            do_sample=False,
            use_cache=False,
        )
    input_len = inputs["input_ids"].shape[-1]
    text = tokenizer.decode(outputs[0][input_len:], skip_special_tokens=True)
    parsed = _parse_optional_json(text)
    return {"source": "phi3", "status": "ok", "text": text, "drug_names": _drug_names(parsed, text)}


def _candidates_from_result(result: Dict[str, Any]) -> List[MedicineCandidate]:
    candidates: List[MedicineCandidate] = []
    source = str(result.get("source") or "unknown")
    for name in result.get("drug_names") or []:
        candidates.extend(_dictionary_candidates(str(name), source, base_score=90))
    text = normalize_text(result.get("text") or "")
    if text:
        candidates.extend(_dictionary_candidates(text, source, base_score=82))
        candidates.extend(_raw_prescription_candidates(text, source))
    return candidates


def _dictionary_candidates(text: str, source: str, *, base_score: int = 88) -> List[MedicineCandidate]:
    out = []
    for item in find_drug_candidates(text, limit=8):
        score = min(100, int(item.get("score", base_score)))
        if item.get("method") == "fuzzy":
            score = min(score, base_score)
        out.append(
            MedicineCandidate(
                raw=str(item.get("raw") or item.get("normalized") or ""),
                normalized=str(item.get("normalized") or item.get("raw") or ""),
                score=score,
                source=source,
                evidence=text[:300],
            )
        )
    return out


def _raw_prescription_candidates(text: str, source: str) -> List[MedicineCandidate]:
    if source not in {"keracare", "donut"}:
        return []
    out: List[MedicineCandidate] = []
    stop = {
        "name", "patient", "address", "date", "time", "disease", "diagnosis", "doctor",
        "staff", "nurse", "drugs", "table", "body", "html", "thead", "tbody", "formula",
        "brand", "will", "initial", "medicines", "injections", "administered", "male",
        "female", "room", "ward", "bed", "sex", "age",
    }
    for raw_line in re.split(r"[\n<>]+", text):
        line = normalize_text(raw_line)
        lowered = line.lower()
        if not re.search(r"\b(?:inj|iv|mg|mcg|gm?|ml|tab|cap|drug)\b", lowered):
            continue
        for token in re.findall(r"\b[A-Za-z][A-Za-z/-]{3,24}\b", line):
            clean = token.strip("/-").lower()
            if clean in stop or clean.endswith("ml") or clean.endswith("mg"):
                continue
            if any(ch.isdigit() for ch in clean):
                continue
            out.append(MedicineCandidate(raw=token, normalized=clean, score=83, source=source, evidence=line[:300]))
    return out[:8]


def _merge_candidates(candidates: Iterable[MedicineCandidate]) -> List[MedicineCandidate]:
    merged: Dict[str, MedicineCandidate] = {}
    votes: Dict[str, int] = {}
    for item in candidates:
        if not item.normalized:
            continue
        key = item.normalized.lower()
        votes[key] = votes.get(key, 0) + 1
        current = merged.get(key)
        score = min(100, item.score + max(0, votes[key] - 1) * 4)
        candidate = MedicineCandidate(item.raw, item.normalized, score, item.source, item.evidence)
        if current is not None:
            current_has_dose = bool(_nearby_dose(current))
            candidate_has_dose = bool(_nearby_dose(candidate))
            if candidate_has_dose and not current_has_dose:
                candidate.score = max(candidate.score, current.score)
                candidate.source = f"{current.source}+{candidate.source}" if current.source != candidate.source else candidate.source
                merged[key] = candidate
                continue
        if current is None or candidate.score > current.score:
            merged[key] = candidate
    return sorted(merged.values(), key=lambda item: item.score, reverse=True)[: config.MULTIMODAL_MEDICINE_MAX_DRUGS]


def _drug_names(parsed: Any, text: str) -> List[str]:
    if isinstance(parsed, dict):
        names = parsed.get("drug_names") or parsed.get("medicines") or parsed.get("medications") or []
        if isinstance(names, list):
            return [normalize_text(str(item.get("drug") if isinstance(item, dict) else item)) for item in names if normalize_text(str(item))]
    return [item["normalized"] for item in find_drug_candidates(text, limit=8)]


def _parse_optional_json(text: str) -> Any:
    try:
        return parse_json_loose(text)
    except Exception:
        return None


def _empty_result(status: str) -> Dict[str, Any]:
    return {"status": status, "drug_names": [], "candidates": [], "order_items": [], "sources": []}


def _bounded_hf_image(image: Image.Image) -> Image.Image:
    bounded = image.convert("RGB").copy()
    max_side = max(320, config.MULTIMODAL_MEDICINE_IMAGE_MAX_SIDE)
    if max(bounded.size) > max_side:
        bounded.thumbnail((max_side, max_side))
    return bounded


def _release_cuda() -> None:
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


def _import_torch():
    import torch

    return torch


def _import_auto_causal_and_processor():
    from transformers import AutoModelForCausalLM, AutoProcessor

    return AutoModelForCausalLM, AutoProcessor


def _import_qwen_vl_utils():
    from qwen_vl_utils import process_vision_info

    return process_vision_info


def _device(torch) -> str:
    return "cuda" if torch.cuda.is_available() else "cpu"


class _temp_image:
    def __init__(self, image: Image.Image):
        self.image = image
        self.path: Path | None = None

    def __enter__(self) -> Path:
        handle = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
        handle.close()
        self.path = Path(handle.name)
        self.image.convert("RGB").save(self.path, format="JPEG", quality=90)
        return self.path

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.path:
            try:
                self.path.unlink(missing_ok=True)
            except Exception:
                pass
