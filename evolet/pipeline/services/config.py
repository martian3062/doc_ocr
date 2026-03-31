"""
Evolet Pipeline — Runtime Configuration
=========================================
Single source of truth for all tuneable knobs.
Every constant can be overridden via an environment variable so that
production deployments (Docker, Colab, bare-metal) never need to edit code.

Layout
------
  1. Model / quantisation settings
  2. OCR (DocTR) settings
  3. Note segmentation thresholds
  4. Triage logic (when to use regex vs. LLM)
  5. Adaptive-batch sizing for the LLM phase
  6. Generation parameters
  7. Parallelism
  8. Pre-compiled regex pattern lists used across the pipeline
  9. LLM system prompt
"""

import os
import re

# ── GPU probe (safe import) ──────────────────────────────────────────────────
# We probe for CUDA here once so every downstream module can import GPU_AVAILABLE
# directly from config rather than re-importing torch just to check.
try:
    import torch
    GPU_AVAILABLE = torch.cuda.is_available()
except ImportError:
    torch = None
    GPU_AVAILABLE = False


# ─────────────────────────────────────────────────────────────────────────────
# 1. Model / Quantisation
# ─────────────────────────────────────────────────────────────────────────────

# Primary LLM — Qwen 2.5 1.5B Instruct (small, fast, good at JSON output)
MODEL_ID = os.environ.get("EVOLET_MODEL_ID", "Qwen/Qwen2.5-1.5B-Instruct")

# Fallback if primary model download fails
FALLBACK_MODEL_ID = "HuggingFaceTB/SmolLM2-1.7B-Instruct"

# 4-bit NF4 quantisation via bitsandbytes — halves GPU memory at minimal quality cost
USE_4BIT = os.environ.get("EVOLET_USE_4BIT", "1").strip() == "1"

# Set to "1" when model weights are already cached locally (air-gapped envs)
LOCAL_FILES_ONLY = os.environ.get("EVOLET_LOCAL_ONLY", "0").strip() == "1"

# HuggingFace token — checked across three common env-var names
HF_TOKEN = (
    os.environ.get("HF_TOKEN")
    or os.environ.get("HUGGING_FACE_HUB_TOKEN")
    or os.environ.get("HUGGINGFACEHUB_API_TOKEN")
)


# ─────────────────────────────────────────────────────────────────────────────
# 2. OCR (DocTR)
# ─────────────────────────────────────────────────────────────────────────────

# Master switch — disable to use native PDF text only (no GPU OCR)
USE_DOCTR_OCR = True

# Page render resolution for OCR images; 130 DPI is fast and accurate enough
OCR_RENDER_DPI = 130

# Batch size when feeding page images to DocTR (GPU memory vs. throughput trade-off)
OCR_BATCH_PAGES = 10

# Thresholds that trigger OCR fallback for a page:
#   - too few characters  → probably an image-only page
#   - too few words       → garbled/table-heavy page
#   - low alpha ratio     → mostly numbers/special chars (no readable text)
NATIVE_TEXT_MIN_CHARS = 120
NATIVE_TEXT_MIN_WORDS = 22
NATIVE_TEXT_MIN_ALPHA_RATIO = 0.28


# ── Embedded image OCR (EasyOCR) ────────────────────────────────────────────
# When True, images embedded inside PDF pages (e.g. lab-result scans, chart
# images) are extracted and run through EasyOCR separately from the DocTR
# whole-page OCR path.  Catches clinical data that lives inside images embedded
# in otherwise-text PDFs — a gap DocTR doesn't cover.
USE_EMBEDDED_IMAGE_OCR = True

# Minimum pixel area (width × height) for an embedded image to be worth OCR-ing.
# Skips small decorative images, logos, and icons.
EMBEDDED_IMAGE_MIN_PIXELS = 40_000   # ~200×200 px


# ─────────────────────────────────────────────────────────────────────────────
# 3. Note Segmentation
# ─────────────────────────────────────────────────────────────────────────────

# Notes shorter than this (in words) are discarded as too sparse for extraction
NOTE_MIN_WORDS = 12

# Deduplicate identical notes (same MD5 hash) across pages of the same PDF
DEDUP_EXACT_NOTES = True


# ─────────────────────────────────────────────────────────────────────────────
# 4. Triage — Regex vs. LLM
# ─────────────────────────────────────────────────────────────────────────────

# Safety cap: never send more than this many notes to the LLM per patient
# (prevents runaway GPU time on very long PDFs)
MAX_NOTES_PER_PATIENT_FOR_LLM = 16

# A note needs at least this clinical signal score to be worth sending to the LLM
MIN_SIGNAL_FOR_LLM = 3

# If regex already found this many mentions in a note, skip the LLM for that note
MIN_REGEX_MENTIONS_TO_SKIP_LLM = 3

# Short notes (< 40 words) rarely contain enough context for the LLM to add value
SEND_SHORT_NOTES_TO_LLM = False


# ─────────────────────────────────────────────────────────────────────────────
# 5. Adaptive Batching
# ─────────────────────────────────────────────────────────────────────────────
# Notes are bucketed by word count so we can fill GPU memory more efficiently:
#   short  → pack more notes per batch (higher throughput)
#   long   → single note per batch (avoid OOM)

SHORT_NOTE_WORDS  = 120   # ≤ this → "short"  bucket
MEDIUM_NOTE_WORDS = 220   # ≤ this → "medium" bucket
LONG_NOTE_WORDS   = 380   # ≤ this → "long"   bucket
                          # > 380  → "xlong"  bucket

# Batch sizes differ between GPU and CPU to stay within memory limits.
# L4 / A100 class GPUs (≥16 GB VRAM) can sustain higher throughput;
# the larger values below are tuned for 24 GB L4 + Qwen 2.5 1.5B 4-bit.
SHORT_BATCH_SIZE  = 6 if GPU_AVAILABLE else 1
MEDIUM_BATCH_SIZE = 4 if GPU_AVAILABLE else 1
LONG_BATCH_SIZE   = 2 if GPU_AVAILABLE else 1
XLONG_BATCH_SIZE  = 1   # one note at a time for extra-long notes


# ─────────────────────────────────────────────────────────────────────────────
# 6. Generation Parameters
# ─────────────────────────────────────────────────────────────────────────────

# Hard cap on input tokens (prompt + padding); prompts beyond this are truncated
MAX_INPUT_TOKENS = 3584

# Normal token budget for a generation pass
MAX_NEW_TOKENS = 640

# Increased budget used when the first pass produces unparseable JSON (retry)
RETRY_MAX_NEW_TOKENS = 768

# Greedy decoding (do_sample=False) gives deterministic, reproducible output
DO_SAMPLE = False


# ─────────────────────────────────────────────────────────────────────────────
# 7. Parallelism
# ─────────────────────────────────────────────────────────────────────────────

# Maximum worker threads for I/O-bound stages (extraction, photo, etc.)
MAX_WORKERS = int(os.environ.get("EVOLET_MAX_WORKERS", "4"))

# Number of documents processed concurrently in Phase 1.
# CPU phases (native extract, segmentation, regex) run in parallel;
# GPU phases (DocTR, EasyOCR) are serialised by a lock inside pdf_extractor.py.
# For single-GPU servers, keep at 4 — increasing beyond this adds thread overhead.
DOC_WORKERS = int(os.environ.get("EVOLET_DOC_WORKERS", "4"))

# Skip re-processing PDFs that already have PageLedger rows in the database
SKIP_EXISTING = True


# ─────────────────────────────────────────────────────────────────────────────
# 8. Pre-compiled Regex Pattern Lists
# ─────────────────────────────────────────────────────────────────────────────
# All patterns are compiled once at import time for maximum throughput.
# Downstream modules import these compiled objects directly.

# Patterns that mark the start of a new clinical section within a page.
# Used by note_segmenter to split page text into discrete note units.
SECTION_SPLIT_PATTERNS = [
    r"\bDATE\s*:\s*\d{1,2}[./-]\d{1,2}[./-]\d{2,4}",
    r"\bCLINICAL NOTE DETAILS\b",
    r"\bPATIENT ASSESSMENT DETAILS\b",
    r"\bJOINT-CLINIC DETAILS\b",
    r"\bREMARKS DETAILS\b",
    r"\bDETAILS OF JOINT CLINIC\b",
]

# Lines matching any of these indicate the note has no clinical value
# (boilerplate, prescription footers, contact info, etc.)
LOW_VALUE_PATTERNS = [
    r"prescription has been issued by",
    r"cost certificate issued",
    r"https?://",
    r"tata memorial hospital[- ]electronic medical record",
    r"fax:",
    r"e-mail:",
]

# Clinical keyword hints used to score notes — more hits → higher signal → more
# likely to be sent to the LLM for deep extraction
CLINICAL_HINT_PATTERNS = [
    r"\bdiagnosis\b", r"\bcarcinoma\b", r"\brcc\b", r"\besophagus\b",
    r"\bmetast",       r"\bstage\b",     r"\bgrade\b",   r"\bbiopsy\b",
    r"\bhistopath\b",  r"\bhpr\b",       r"\bihc\b",     r"\bpet ct\b",
    r"\bc?ect\b",      r"\bmri\b",       r"\bradiotherapy\b", r"\brt\b",
    r"\bsurgery\b",    r"\bnephrectomy\b", r"\bsunitinib\b",
    r"\bchemo",        r"\btablet\b",    r"\btab\b",     r"\bplan\b",
    r"\bfollow[- ]?up\b", r"\bpain\b",  r"\bcreat\b",   r"\bhb\b",
    r"\bplatelet\b",   r"\blymph",       r"\bnodule\b",  r"\blesion\b",
]

# Date extraction patterns — cover the most common formats found in TMH reports
DATE_PATTERNS = [
    r"\b\d{1,2}[./-]\d{1,2}[./-]\d{2,4}\b",          # 12/05/2023 or 12.05.23
    r"\b\d{1,2}\s+[A-Za-z]{3,9}\s+\d{2,4}\b",         # 12 May 2023
]

# Pre-compiled versions of the above for fast repeated matching.
# Import these in modules that loop over notes to avoid re-compiling per call.
_LOW_VALUE_RE   = [re.compile(p, re.I) for p in LOW_VALUE_PATTERNS]
_HINT_RE        = [re.compile(p, re.I) for p in CLINICAL_HINT_PATTERNS]
_DATE_RE        = [re.compile(p, re.I) for p in DATE_PATTERNS]


# ─────────────────────────────────────────────────────────────────────────────
# 9. LLM System Prompt
# ─────────────────────────────────────────────────────────────────────────────
# Kept here so it's versioned alongside model/generation config and can be
# swapped without touching the engine code.

SYSTEM_PROMPT = """
You extract structured clinical mentions from one medical note.

Return exactly one JSON object with this shape:
{
  "mentions": [
    {
      "category": "",
      "label": "",
      "value": "",
      "normalized_value": "",
      "date_text": "",
      "certainty": "confirmed|possible|ruled_out|unknown",
      "attributes": {},
      "evidence_quote": ""
    }
  ]
}

Rules:
- JSON only. No markdown. No commentary.
- Max 8 mentions.
- Do not invent facts.
- evidence_quote must be a short verbatim snippet from the note.
- If no clinically useful mention exists, return {"mentions":[]}.
- Allowed categories:
  diagnosis, medication, plan, symptom, imaging, pathology, genomics,
  procedure, lab, status, follow_up, surgery, radiotherapy,
  performance_status, other
""".strip()
