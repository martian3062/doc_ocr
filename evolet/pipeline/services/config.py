"""
doc-ocr Pipeline — Runtime Configuration
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


def env(name: str, default: str) -> str:
    """Read DOC_READER_* first, then legacy EVOLET_* for old deployments."""
    if name.startswith("EVOLET_"):
        return os.environ.get(f"DOC_READER_{name.removeprefix('EVOLET_')}", os.environ.get(name, default))
    return os.environ.get(name, default)


def env_bool(name: str, default: str = "0") -> bool:
    return env(name, default).strip().lower() in {"1", "true", "yes", "on"}

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
MODEL_ID = env("EVOLET_MODEL_ID", "Qwen/Qwen2.5-1.5B-Instruct")
LLM_PROVIDER = env("EVOLET_LLM_PROVIDER", "groq").strip().lower()
ENABLE_LOCAL_HF_LLM = env_bool("EVOLET_ENABLE_LOCAL_HF_LLM", "0")
GROQ_EXTRACTION_MODEL = env("EVOLET_GROQ_EXTRACTION_MODEL", "llama-3.3-70b-versatile")
GROQ_EXTRACTION_TIMEOUT_SECONDS = int(env("EVOLET_GROQ_EXTRACTION_TIMEOUT_SECONDS", "60"))

# Fallback if primary model download fails
FALLBACK_MODEL_ID = "HuggingFaceTB/SmolLM2-1.7B-Instruct"

# 4-bit NF4 quantisation via bitsandbytes — halves GPU memory at minimal quality cost
USE_4BIT = env_bool("EVOLET_USE_4BIT", "1")

# Set to "1" when model weights are already cached locally (air-gapped envs)
LOCAL_FILES_ONLY = env_bool("EVOLET_LOCAL_ONLY", "0")

# HuggingFace token — checked across three common env-var names
HF_TOKEN = (
    os.environ.get("HF_TOKEN")
    or os.environ.get("HUGGING_FACE_HUB_TOKEN")
    or os.environ.get("HUGGINGFACEHUB_API_TOKEN")
)
if HF_TOKEN:
    os.environ.setdefault("HF_TOKEN", HF_TOKEN)
    os.environ.setdefault("HUGGING_FACE_HUB_TOKEN", HF_TOKEN)


# ─────────────────────────────────────────────────────────────────────────────
# 2. OCR (DocTR)
# ─────────────────────────────────────────────────────────────────────────────

# Master switch. Default is LLM-oriented native text extraction only.
USE_DOCTR_OCR = env_bool("EVOLET_USE_DOCTR_OCR", "0")

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
USE_EMBEDDED_IMAGE_OCR = env_bool("EVOLET_USE_EMBEDDED_IMAGE_OCR", "0")

# Minimum pixel area (width × height) for an embedded image to be worth OCR-ing.
# Skips small decorative images, logos, and icons.
EMBEDDED_IMAGE_MIN_PIXELS = 40_000   # ~200×200 px

# Hybrid advanced OCR backends
ENABLE_HANDWRITING_OCR = env_bool("EVOLET_ENABLE_HANDWRITING_OCR", "0")
ENABLE_GOT_VERIFICATION = env_bool("EVOLET_ENABLE_GOT_VERIFICATION", "0")
ENABLE_MEDICAL_HANDWRITING_OCR = env_bool("EVOLET_ENABLE_MEDICAL_HANDWRITING_OCR", "0")
ENABLE_LOCAL_HF_VISION_MODELS = env_bool("EVOLET_ENABLE_LOCAL_HF_VISION_MODELS", "0")
DOC_READER_ENABLE_ADVANCED_PARSERS = env_bool("EVOLET_ENABLE_ADVANCED_PARSERS", "0")
DOC_READER_PARSER_BACKENDS = env("EVOLET_PARSER_BACKENDS", "")

# Full-page / crop-level VLM OCR backends. These are intentionally off by
# default because they are heavy; docker-compose or one-off tests can enable
# whichever mix fits the current GPU.
ENABLE_OLMOCR_PARSER = env_bool("EVOLET_ENABLE_OLMOCR_PARSER", "0")
OLMOCR_MODEL_ID = env("EVOLET_OLMOCR_MODEL_ID", "allenai/olmOCR-2-7B-1025")
OLMOCR_USE_4BIT = env_bool("EVOLET_OLMOCR_USE_4BIT", "0")
OLMOCR_MAX_PAGES_PER_DOCUMENT = int(env("EVOLET_OLMOCR_MAX_PAGES_PER_DOCUMENT", "2"))
OLMOCR_RENDER_DPI = int(env("EVOLET_OLMOCR_RENDER_DPI", "180"))
OLMOCR_MAX_NEW_TOKENS = int(env("EVOLET_OLMOCR_MAX_NEW_TOKENS", "1024"))

ENABLE_CHANDRA_OCR = env_bool("EVOLET_ENABLE_CHANDRA_OCR", "0")
CHANDRA_OCR_MODEL_ID = env("EVOLET_CHANDRA_OCR_MODEL_ID", "datalab-to/chandra-ocr-2")
CHANDRA_USE_4BIT = env_bool("EVOLET_CHANDRA_USE_4BIT", "1")
CHANDRA_MAX_NEW_TOKENS = int(env("EVOLET_CHANDRA_MAX_NEW_TOKENS", "512"))
ENABLE_MISTRAL_OCR = env_bool("EVOLET_ENABLE_MISTRAL_OCR", "0")

ENABLE_SAHI_PRESCRIPTION_SEGMENTATION = env_bool("EVOLET_ENABLE_SAHI_PRESCRIPTION_SEGMENTATION", "0")
SAHI_PRESCRIPTION_MODEL_TYPE = env("EVOLET_SAHI_PRESCRIPTION_MODEL_TYPE", "ultralytics")
SAHI_PRESCRIPTION_MODEL_ID = env("EVOLET_SAHI_PRESCRIPTION_MODEL_ID", "Armaggheddon/yolo11-document-layout")
SAHI_PRESCRIPTION_MODEL_FILE = env("EVOLET_SAHI_PRESCRIPTION_MODEL_FILE", "yolo11n_doc_layout.pt")
SAHI_PRESCRIPTION_DEVICE = env("EVOLET_SAHI_PRESCRIPTION_DEVICE", "cuda:0" if GPU_AVAILABLE else "cpu")
SAHI_PRESCRIPTION_DPI = int(env("EVOLET_SAHI_PRESCRIPTION_DPI", "180"))
SAHI_PRESCRIPTION_CONFIDENCE = float(env("EVOLET_SAHI_PRESCRIPTION_CONFIDENCE", "0.25"))
SAHI_PRESCRIPTION_SLICE_HEIGHT = int(env("EVOLET_SAHI_PRESCRIPTION_SLICE_HEIGHT", "768"))
SAHI_PRESCRIPTION_SLICE_WIDTH = int(env("EVOLET_SAHI_PRESCRIPTION_SLICE_WIDTH", "768"))
SAHI_PRESCRIPTION_OVERLAP = float(env("EVOLET_SAHI_PRESCRIPTION_OVERLAP", "0.22"))
SAHI_PRESCRIPTION_MAX_PAGES_PER_DOCUMENT = int(env("EVOLET_SAHI_PRESCRIPTION_MAX_PAGES_PER_DOCUMENT", "5"))
PADDLE_STRUCTURE_DEVICE = env("EVOLET_PADDLE_STRUCTURE_DEVICE", "cpu")
PADDLE_STRUCTURE_CPU_THREADS = int(env("EVOLET_PADDLE_STRUCTURE_CPU_THREADS", "2"))
PADDLE_STRUCTURE_MAX_PAGES_PER_DOCUMENT = int(env("EVOLET_PADDLE_STRUCTURE_MAX_PAGES_PER_DOCUMENT", "2"))
PADDLE_STRUCTURE_RENDER_DPI = int(env("EVOLET_PADDLE_STRUCTURE_RENDER_DPI", "180"))
PADDLE_STRUCTURE_MAX_ARTIFACTS_PER_PAGE = int(env("EVOLET_PADDLE_STRUCTURE_MAX_ARTIFACTS_PER_PAGE", "60"))
PADDLE_STRUCTURE_USE_TABLE_RECOGNITION = env_bool("EVOLET_PADDLE_STRUCTURE_USE_TABLE_RECOGNITION", "1")
PADDLE_STRUCTURE_GPU_MEMORY_FRACTION = float(env("EVOLET_PADDLE_STRUCTURE_GPU_MEMORY_FRACTION", "0.65"))
PADDLE_STRUCTURE_GPU_STOP_FRACTION = float(env("EVOLET_PADDLE_STRUCTURE_GPU_STOP_FRACTION", "0.80"))
PADDLE_STRUCTURE_GPU_ID = int(env("EVOLET_PADDLE_STRUCTURE_GPU_ID", "0"))
TROCR_MODEL_ID = env("EVOLET_TROCR_MODEL_ID", "microsoft/trocr-large-handwritten")
MEDICAL_HANDWRITING_MODEL_ID = env("EVOLET_MEDICAL_HANDWRITING_MODEL_ID", "microsoft/trocr-base-handwritten")
MEDICAL_HANDWRITING_CANDIDATE_MODEL_IDS = [
    item.strip()
    for item in env(
        "EVOLET_MEDICAL_HANDWRITING_CANDIDATE_MODEL_IDS",
        "Teklia/pylaia-iam,espnet/iam_handwriting_ocr,ismatsamadov/handwriting-recognition-iam,Riksarkivet/satrn_htr,Emeritus-21/Finetuned-full-HTR-model,DungHugging/vietocr-handwritten-finetune,Valerii02/ukr-htr-convtext",
    ).split(",")
    if item.strip()
]
MEDICAL_HANDWRITING_TRUST_REMOTE_CODE = env_bool("EVOLET_MEDICAL_HANDWRITING_TRUST_REMOTE_CODE", "0")
MEDOCR_VISION_DATASET_ID = env("EVOLET_MEDOCR_VISION_DATASET_ID", "naazimsnh02/medocr-vision-dataset")
GOT_OCR_MODEL_ID = env("EVOLET_GOT_OCR_MODEL_ID", "stepfun-ai/GOT-OCR-2.0-hf")
YOLO_LAYOUT_MODEL_ID = env("EVOLET_YOLO_LAYOUT_MODEL_ID", "Armaggheddon/yolo11-document-layout")
YOLO_LAYOUT_MODEL_FILE = env("EVOLET_YOLO_LAYOUT_MODEL_FILE", "yolo11n_doc_layout.pt")
YOLO_LAYOUT_DPI = int(env("EVOLET_YOLO_LAYOUT_DPI", "144"))
YOLO_LAYOUT_CONFIDENCE = float(env("EVOLET_YOLO_LAYOUT_CONFIDENCE", "0.25"))
TROCR_MAX_NEW_TOKENS = int(env("EVOLET_TROCR_MAX_NEW_TOKENS", "128"))
GOT_OCR_MAX_NEW_TOKENS = int(env("EVOLET_GOT_OCR_MAX_NEW_TOKENS", "1024"))
HANDWRITING_MIN_NATIVE_CHARS = int(env("EVOLET_HANDWRITING_MIN_NATIVE_CHARS", "48"))
LAYOUT_NOTE_VERTICAL_GAP = float(env("EVOLET_LAYOUT_NOTE_VERTICAL_GAP", "42"))
HANDWRITING_MAX_CROPS_PER_DOCUMENT = int(env("EVOLET_HANDWRITING_MAX_CROPS_PER_DOCUMENT", "24"))
ENABLE_PAGE_VISION_SWEEP = env_bool("EVOLET_ENABLE_PAGE_VISION_SWEEP", "0")
PAGE_VISION_SWEEP_DPI = int(env("EVOLET_PAGE_VISION_SWEEP_DPI", "240"))
PAGE_VISION_MAX_PAGES_PER_DOCUMENT = int(env("EVOLET_PAGE_VISION_MAX_PAGES_PER_DOCUMENT", "4"))
PAGE_VISION_MAX_CROPS_PER_PAGE = int(env("EVOLET_PAGE_VISION_MAX_CROPS_PER_PAGE", "16"))
PAGE_VISION_MIN_TEXT_CHARS = int(env("EVOLET_PAGE_VISION_MIN_TEXT_CHARS", "3"))
PAGE_VISION_MIN_CROP_STDDEV = float(env("EVOLET_PAGE_VISION_MIN_CROP_STDDEV", "7.0"))
ENABLE_GROQ_VISION_OCR = env_bool("EVOLET_ENABLE_GROQ_VISION_OCR", "0")
GROQ_VISION_MODEL = env("EVOLET_GROQ_VISION_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")
GROQ_VISION_TIMEOUT_SECONDS = int(env("EVOLET_GROQ_VISION_TIMEOUT_SECONDS", "75"))
ENABLE_HANDWRITING_ORDER_EXTRACTOR = env_bool("EVOLET_ENABLE_HANDWRITING_ORDER_EXTRACTOR", "1")
HANDWRITING_ORDER_MAX_PAGES_PER_DOCUMENT = int(env("EVOLET_HANDWRITING_ORDER_MAX_PAGES_PER_DOCUMENT", "5"))
HANDWRITING_ORDER_MAX_CROPS_PER_PAGE = int(env("EVOLET_HANDWRITING_ORDER_MAX_CROPS_PER_PAGE", "8"))
HANDWRITING_ORDER_RENDER_DPI = int(env("EVOLET_HANDWRITING_ORDER_RENDER_DPI", "240"))
HANDWRITING_ORDER_MIN_CROP_AREA = float(env("EVOLET_HANDWRITING_ORDER_MIN_CROP_AREA", "9000"))
ENABLE_MULTIMODAL_MEDICINE_EXTRACTOR = env_bool("EVOLET_ENABLE_MULTIMODAL_MEDICINE_EXTRACTOR", "1")
MULTIMODAL_MEDICINE_BACKENDS = {
    item.strip().lower()
    for item in env("EVOLET_MULTIMODAL_MEDICINE_BACKENDS", "keracare,donut,phi3,dictionary").split(",")
    if item.strip()
}
KERACARE_MEDICINE_MODEL_ID = env("EVOLET_KERACARE_MEDICINE_MODEL_ID", "KeraCare/keras-dots-ocr-finetuned-v1")
DONUT_PRESCRIPTION_MODEL_ID = env("EVOLET_DONUT_PRESCRIPTION_MODEL_ID", "chinmays18/medical-prescription-ocr")
PHI3_PRESCRIPTION_MODEL_ID = env("EVOLET_PHI3_PRESCRIPTION_MODEL_ID", "Muizzzz8/phi3-prescription-reader")
MULTIMODAL_MEDICINE_MAX_NEW_TOKENS = int(env("EVOLET_MULTIMODAL_MEDICINE_MAX_NEW_TOKENS", "256"))
MULTIMODAL_MEDICINE_MIN_SCORE = int(env("EVOLET_MULTIMODAL_MEDICINE_MIN_SCORE", "82"))
MULTIMODAL_MEDICINE_MAX_DRUGS = int(env("EVOLET_MULTIMODAL_MEDICINE_MAX_DRUGS", "12"))
MULTIMODAL_MEDICINE_IMAGE_MAX_SIDE = int(env("EVOLET_MULTIMODAL_MEDICINE_IMAGE_MAX_SIDE", "960"))
MULTIMODAL_MEDICINE_MAX_LOCAL_HF_CROPS_PER_PROCESS = int(env("EVOLET_MULTIMODAL_MEDICINE_MAX_LOCAL_HF_CROPS_PER_PROCESS", "2"))
ENABLE_MEDOCR_REFERENCE_LAYER = env_bool("EVOLET_ENABLE_MEDOCR_REFERENCE_LAYER", "1")
MEDOCR_REFERENCE_MAX_EXAMPLES = int(env("EVOLET_MEDOCR_REFERENCE_MAX_EXAMPLES", "6"))

# LLM-first extraction and validation. The default path sends every meaningful
# grouped note to the extraction LLM, then audits the merged patient record with
# a validation LLM. Both stages degrade gracefully if model access is missing.
LLM_EXTRACT_ALL_NOTES = env_bool("EVOLET_LLM_EXTRACT_ALL_NOTES", "1")
LLM_EXTRACT_PER_BACKEND_ARTIFACTS = env_bool("EVOLET_LLM_EXTRACT_PER_BACKEND_ARTIFACTS", "1")
LLM_BACKEND_NOTE_MIN_CHARS = int(env("EVOLET_LLM_BACKEND_NOTE_MIN_CHARS", "40"))
LLM_BACKEND_NOTE_MAX_CHARS = int(env("EVOLET_LLM_BACKEND_NOTE_MAX_CHARS", "6000"))
LLM_BACKEND_NOTE_BACKENDS = {
    item.strip().lower()
    for item in env(
        "EVOLET_LLM_BACKEND_NOTE_BACKENDS",
        "native,mixed,olmocr,chandra_ocr,sahi_prescription,groq_vision,groq_handwriting_order,handwriting_ensemble",
    ).split(",")
    if item.strip()
}
ENABLE_MEDICAL_VALIDATION = env_bool("EVOLET_ENABLE_MEDICAL_VALIDATION", "1")
REQUIRE_MEDICAL_VALIDATION = env_bool("EVOLET_REQUIRE_MEDICAL_VALIDATION", "1")
VALIDATION_BACKEND = env("EVOLET_VALIDATION_BACKEND", "heuristic")
VALIDATION_MODEL_ID = env("EVOLET_VALIDATION_MODEL_ID", "google/medgemma-1.5-4b-it")
VALIDATION_FALLBACK_MODEL_ID = env("EVOLET_VALIDATION_FALLBACK_MODEL_ID", "Qwen/Qwen2.5-1.5B-Instruct")
VALIDATION_MAX_NEW_TOKENS = int(env("EVOLET_VALIDATION_MAX_NEW_TOKENS", "512"))

# Auto-schema extraction. Groq is used for fast document-type detection and
# adaptive schema construction when configured. The deterministic schema
# builder remains the fallback so records are still produced without a key.
ENABLE_AUTO_SCHEMA = env_bool("EVOLET_ENABLE_AUTO_SCHEMA", "1")
SCHEMA_PROVIDER = env("EVOLET_SCHEMA_PROVIDER", "groq").strip().lower()
SCHEMA_MODEL = env("EVOLET_SCHEMA_MODEL", "llama-3.3-70b-versatile")
SCHEMA_LOCAL_MODEL_ID = env("EVOLET_SCHEMA_LOCAL_MODEL_ID", MODEL_ID)
SCHEMA_LOCAL_USE_4BIT = env_bool("EVOLET_SCHEMA_LOCAL_USE_4BIT", "0")
SCHEMA_MAX_SOURCE_CHARS = int(env("EVOLET_SCHEMA_MAX_SOURCE_CHARS", "5500"))
SCHEMA_MAX_NEW_TOKENS = int(env("EVOLET_SCHEMA_MAX_NEW_TOKENS", "900"))
SCHEMA_TIMEOUT_SECONDS = int(env("EVOLET_SCHEMA_TIMEOUT_SECONDS", "60"))
GROQ_API_KEY = os.environ.get("DOC_READER_GROQ_API_KEY") or os.environ.get("GROQ_API_KEY", "")
ENABLE_TRANSFORMER_VALIDATION = env_bool("EVOLET_ENABLE_TRANSFORMER_VALIDATION", "0")
STORE_FULL_SOURCE_TEXT = env_bool("EVOLET_STORE_FULL_SOURCE_TEXT", "1")
ENABLE_SPARK_AGENTIC_SCHEMA = env_bool("EVOLET_ENABLE_SPARK_AGENTIC_SCHEMA", "1")
SPARK_AGENTIC_MIN_EVIDENCE_COVERAGE = float(env("EVOLET_SPARK_AGENTIC_MIN_EVIDENCE_COVERAGE", "0.70"))
SPARK_AGENTIC_MIN_MEDICATION_FIELDS = int(env("EVOLET_SPARK_AGENTIC_MIN_MEDICATION_FIELDS", "3"))

# Hybrid schema cleaning runs after OCR/mention extraction and after the
# deterministic duplicate merge. It is CPU-safe by default: unavailable optional
# NLP/HF libraries are reported in FinalRecord.stats instead of failing the run.
ENABLE_HYBRID_SCHEMA_CLEANER = env_bool("EVOLET_ENABLE_HYBRID_SCHEMA_CLEANER", "0")
SCHEMA_CLEANER_BACKENDS = {
    item.strip().lower()
    for item in env("EVOLET_SCHEMA_CLEANER_BACKENDS", "rapidfuzz,symspell,medspacy,scispacy,posos,d4data,openmed").split(",")
    if item.strip()
}
SCHEMA_CLEANER_HF_MAX_MENTIONS = int(env("EVOLET_SCHEMA_CLEANER_HF_MAX_MENTIONS", "24"))
SCHEMA_CLEANER_HF_MIN_SCORE = float(env("EVOLET_SCHEMA_CLEANER_HF_MIN_SCORE", "0.45"))
SCHEMA_CLEANER_HF_MODELS = {
    "posos": env("EVOLET_SCHEMA_CLEANER_POSOS_MODEL", "Posos/ClinicalNER"),
    "d4data": env("EVOLET_SCHEMA_CLEANER_D4DATA_MODEL", "d4data/biomedical-ner-all"),
    "openmed": env("EVOLET_SCHEMA_CLEANER_OPENMED_MODEL", "OpenMed/OpenMed-NER-PharmaDetect-BioClinical-108M"),
}


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
MAX_NOTES_PER_PATIENT_FOR_LLM = int(env("EVOLET_MAX_NOTES_PER_PATIENT_FOR_LLM", "40"))

# A note needs at least this clinical signal score to be worth sending to the LLM.
# Indian hospital forms score low on English-only patterns so keep this low.
MIN_SIGNAL_FOR_LLM = int(env("EVOLET_MIN_SIGNAL_FOR_LLM", "3"))

# If regex already found this many mentions in a note, skip the LLM for that note.
# Set high so LLM always runs — regex alone misses handwritten/abbreviation-heavy text.
MIN_REGEX_MENTIONS_TO_SKIP_LLM = int(env("EVOLET_MIN_REGEX_MENTIONS_TO_SKIP_LLM", "50"))

# Always send short notes to LLM — Indian case sheets have dense short handwritten orders.
SEND_SHORT_NOTES_TO_LLM = env_bool("EVOLET_SEND_SHORT_NOTES_TO_LLM", "1")


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
MAX_NEW_TOKENS = 1024

# Increased budget used when the first pass produces unparseable JSON (retry)
RETRY_MAX_NEW_TOKENS = 1536

# Greedy decoding (do_sample=False) gives deterministic, reproducible output
DO_SAMPLE = False


# ─────────────────────────────────────────────────────────────────────────────
# 7. Parallelism
# ─────────────────────────────────────────────────────────────────────────────

# Maximum worker threads for I/O-bound stages (extraction, photo, etc.)
MAX_WORKERS = int(env("EVOLET_MAX_WORKERS", "4"))

# Number of documents processed concurrently in Phase 1.
# CPU phases (native extract, segmentation, regex) run in parallel;
# GPU phases (DocTR, EasyOCR) are serialised by a lock inside pdf_extractor.py.
# For single-GPU servers, keep at 4 — increasing beyond this adds thread overhead.
DOC_WORKERS = int(env("EVOLET_DOC_WORKERS", "4"))

# Skip re-processing PDFs that already have extracted output in the database.
# Default=0 (always reprocess) — set EVOLET_SKIP_EXISTING=1 in production to cache results.
SKIP_EXISTING = env_bool("EVOLET_SKIP_EXISTING", "0")


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
    # Oncology / pathology
    r"\bdiagnosis\b", r"\bcarcinoma\b", r"\brcc\b",     r"\besophagus\b",
    r"\bmetast",       r"\bstage\b",     r"\bgrade\b",   r"\bbiopsy\b",
    r"\bhistopath\b",  r"\bhpr\b",       r"\bihc\b",     r"\bpet\b",
    r"\bc?ect\b",      r"\bmri\b",       r"\bradiotherapy\b", r"\brt\b",
    r"\bsurgery\b",    r"\bnephrectomy\b", r"\bsunitinib\b",
    r"\bchemo",        r"\btablet\b",    r"\btab\b",     r"\bplan\b",
    r"\bfollow[- ]?up\b", r"\bpain\b",  r"\bcreat\b",   r"\bhb\b",
    r"\bplatelet\b",   r"\blymph",       r"\bnodule\b",  r"\blesion\b",
    r"\btumou?r\b",    r"\bancology\b",  r"\boncology\b",
    # Indian hospital abbreviations (medication / route / frequency)
    r"\binj\b",        r"\binjection\b", r"\binfusion\b",
    r"\b(?:bd|tds|od|sos|stat|qid|prn)\b",
    r"\b(?:iv|i\.v|im|i\.m|sc|s\.c|po|p\.o)\b",
    r"\bdose\b",       r"\bmg\b",        r"\bml\b",      r"\bmcg\b",
    r"\bunit[s]?\b",   r"\bvial\b",      r"\bamp\b",     r"\bcap\b",
    # Vitals / clinical signs
    r"\bvital",        r"\b(?:bp|spo2|spo₂|pr|rr|hr)\b",
    r"\bpulse\b",      r"\btemperature\b", r"\btemp\b",
    r"\boxygen\b",     r"\bsaturation\b",
    r"\bblood pressure\b",
    # Indian hospital form identifiers
    r"\buhid\b",       r"\bipd\b",       r"\bopd\b",
    r"\bward\b",       r"\bbed\b",       r"\bdepartment\b",
    r"\badmission\b",  r"\bdischarge\b", r"\bcase sheet\b",
    r"\bcase history\b", r"\bconsultation\b",
    # Doctors / orders
    r"\bdr\.?\b",      r"\bdoctor\b",    r"\bconsultant\b",
    r"\border\b",      r"\bprescri",     r"\btreatment\b",
    r"\bmedication\b", r"\bdrug\b",      r"\btherapy\b",
    # Labs / imaging
    r"\breport\b",     r"\btest\b",      r"\blab\b",
    r"\bx[- ]?ray\b",  r"\bultrasound\b", r"\busg\b",
    r"\bwbc\b",        r"\brbc\b",       r"\bsgot\b",    r"\bsgpt\b",
    r"\bcreatinine\b", r"\burea\b",      r"\bsodium\b",  r"\bpotassium\b",
]

# Date extraction patterns — cover the most common formats found in doc-ocr reports
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
You extract structured content from one hospital document note. This is an Indian
hospital EHR system (Tata Memorial Hospital / similar). Documents include case
sheets, admission/discharge forms, prescription slips, doctor's orders, nursing
notes, lab reports, imaging reports, consent forms, and billing documents.

Text may be:
- Printed English form fields with handwritten fill-ins
- Handwritten doctor orders in abbreviated Indian medical shorthand
- Mixed printed + handwritten on the same page

Return exactly one JSON object:
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
- Be exhaustive — every field, table row, order, vital, identifier.
- Indian medical abbreviations to recognise:
  Inj = Injection, Tab = Tablet, Cap = Capsule, Amp = Ampoule
  BD = twice daily, TDS = three times daily, OD = once daily,
  SOS = as needed, STAT = immediately, IV = intravenous, IM = intramuscular,
  SC = subcutaneous, PO = by mouth, N/S = normal saline, RL = Ringer lactate
  UHID = hospital ID, IPD = inpatient, OPD = outpatient
  BP = blood pressure, PR = pulse rate, RR = respiratory rate,
  SpO2 = oxygen saturation, Temp = temperature
- Extract each medication order as its own mention with: drug name, dose, route,
  frequency, duration, and infusion volume if applicable.
- Extract each vital sign as its own mention: label (BP/PR/SpO2/Temp/RR/Weight),
  value with units, and date/time.
- Extract patient identifiers: name, UHID, IPD number, age, sex, ward, bed.
- Extract diagnosis (primary + secondary), stage, grade, histopathology findings.
- Extract dates for admission, discharge, procedures, follow-up appointments.
- Uncertain/unclear handwriting: set certainty="possible", put best guess in
  normalized_value, raw text in value.
- Do not invent facts. evidence_quote = short verbatim snippet.
- If text contains only admin headings (no clinical data), still extract those
  as identifier/admission/demographics mentions.
- If truly no readable text, return {"mentions":[]}.
- Categories: diagnosis, medication, vital_sign, lab, imaging, pathology,
  procedure, follow_up, identifier, admission, discharge, demographics,
  doctor_order, nursing_note, billing, consent, instruction, other.
""".strip()
