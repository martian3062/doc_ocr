"""
PDF Extractor — Native text extraction + DocTR OCR fallback.
=============================================================
Implements a two-phase, lossless-first strategy for every page:

  Phase 1 (native):
    Use PyMuPDF (fitz) to read text directly from the PDF's internal streams.
    Fast and lossless — produces perfect text when the PDF was digitally created.

  Phase 2 (OCR fallback):
    For pages where native text is sparse, garbled, or missing (scanned pages /
    image-heavy layouts), render the page to a PNG and run DocTR OCR on it.
    The OCR result replaces the native text only if it is longer (more content).

Utility helpers exported for use by other modules:
  normalize_text(text) → clean up whitespace artifacts
  word_count(text)     → count whitespace-separated word tokens
  alpha_ratio(text)    → fraction of alphabetic characters (0.0–1.0)
  text_hash(text)      → MD5 hex string for deduplication
"""

import io
import re
import hashlib
import logging
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

import fitz          # PyMuPDF
import numpy as np
import torch
from PIL import Image

from . import config
from .text_cleaner import clean_text_block

logger = logging.getLogger("pipeline")

# ── Module-level compiled regexes ────────────────────────────────────────────
# Compiled once here; word_count() is called thousands of times per run.
_WORD_RE     = re.compile(r"\b\w+\b")
_WHITESPACE  = re.compile(r"[ \t]+")
_MULTI_NL    = re.compile(r"\n{3,}")

# ── OCR engine singletons ────────────────────────────────────────────────────
# DocTR is heavy (~500 MB GPU); load it once and reuse across all pages.
_ocr_engine: Optional[Any] = None

# EasyOCR reader singleton — used for embedded image OCR.
_easy_ocr_reader: Optional[Any] = None

# Threading primitives — protect singleton initialisation and GPU access.
# A single lock serialises both DocTR and EasyOCR GPU calls so only one
# inference kernel runs at a time on a single-GPU server (prevents OOM).
_gpu_lock  = threading.Lock()   # serialises DocTR + EasyOCR forward passes
_init_lock = threading.Lock()   # prevents double-initialisation of singletons


# ─────────────────────────────────────────────────────────────────────────────
# Exported utilities
# ─────────────────────────────────────────────────────────────────────────────

def normalize_text(text: str) -> str:
    """
    Light normalisation of raw page/note text.

    What it does:
    - Replace null bytes and carriage returns with safe equivalents
    - Collapse runs of spaces/tabs into a single space
    - Collapse 3+ consecutive blank lines to 2 (preserve paragraph breaks)
    - Pass through text_cleaner.clean_text_block for OCR artifact repair

    Called on every piece of text before it enters the extraction pipeline.
    """
    if not text:
        return ""
    text = str(text).replace("\x00", " ").replace("\r", "\n")
    text = _WHITESPACE.sub(" ", text)
    text = _MULTI_NL.sub("\n\n", text)
    return clean_text_block(text)


def word_count(text: str) -> int:
    """Count whole-word tokens in *text* (fast, regex-based)."""
    return len(_WORD_RE.findall(text or ""))


def alpha_ratio(text: str) -> float:
    """
    Return the fraction of alphabetic characters in *text*.

    Used to detect pages that are almost entirely numeric / symbolic
    (tables, barcodes, report IDs) — these usually need OCR even if
    native extraction produced a non-empty string.
    """
    if not text:
        return 0.0
    alpha = sum(ch.isalpha() for ch in text)
    return alpha / max(len(text), 1)


def text_hash(text: str) -> str:
    """MD5 hex digest of normalised *text* — used for exact-note dedup."""
    return hashlib.md5(normalize_text(text).encode("utf-8")).hexdigest()


# ─────────────────────────────────────────────────────────────────────────────
# DocTR OCR engine — lazy singleton
# ─────────────────────────────────────────────────────────────────────────────

def _init_ocr() -> Optional[Any]:
    """
    Lazy-load the DocTR OCR predictor on first use.

    Loading is deferred so that running the Django server without a GPU
    doesn't crash at startup.  The model is moved to GPU and set to half-
    precision (float16) for ~2× faster inference when CUDA is available.

    Returns the engine object, or None if import/load failed.
    Thread-safe: uses a lock to prevent concurrent initialisation.
    """
    global _ocr_engine
    if _ocr_engine is not None:
        return _ocr_engine

    with _init_lock:
        if _ocr_engine is not None:   # double-checked locking
            return _ocr_engine
        try:
            from doctr.models import ocr_predictor
            engine = ocr_predictor(pretrained=True, assume_straight_pages=True)

            if torch.cuda.is_available():
                engine = engine.cuda().half()  # FP16 on GPU for 2× speed
                logger.info("DocTR OCR engine loaded (GPU / fp16)")
            else:
                logger.info("DocTR OCR engine loaded (CPU)")

            engine.eval()
            _ocr_engine = engine

        except Exception as exc:
            logger.warning("Failed to load DocTR OCR engine: %s", exc)
            _ocr_engine = None

    return _ocr_engine


def _doctr_page_to_text(page_export: Dict[str, Any]) -> str:
    """
    Convert a DocTR page export dict into a plain-text string.

    DocTR export structure:
      page → blocks → lines → words → {"value": "..."}
    We join words into lines, then lines into a page string.
    """
    lines_out = []
    for block in page_export.get("blocks", []) or []:
        for line in block.get("lines", []) or []:
            words = []
            for word in line.get("words", []) or []:
                # DocTR uses "value"; older versions may use "text"
                val = normalize_text(word.get("value") or word.get("text") or "")
                if val:
                    words.append(val)
            line_text = " ".join(words).strip()
            if line_text:
                lines_out.append(line_text)
    return "\n".join(lines_out).strip()


def _ocr_images(image_paths: List[str]) -> List[str]:
    """
    Run DocTR OCR on a batch of page-image paths.

    Batching is important: DocTR processes multiple images in a single
    forward pass, which amortises the GPU kernel overhead vs. one-at-a-time.

    Returns a list of text strings, one per input image.
    Empty strings are returned for any page that fails.
    """
    engine = _init_ocr()
    if engine is None:
        return [""] * len(image_paths)

    try:
        from doctr.io import DocumentFile
        doc    = DocumentFile.from_images(image_paths)
        with _gpu_lock:
            result = engine(doc).export()
        pages  = result.get("pages", []) or []
        return [_doctr_page_to_text(p) for p in pages]
    except Exception as exc:
        logger.error("OCR batch failed: %s", exc)
        return [""] * len(image_paths)


# ─────────────────────────────────────────────────────────────────────────────
# EasyOCR engine — lazy singleton (embedded image OCR)
# ─────────────────────────────────────────────────────────────────────────────

def _init_easy_ocr() -> Optional[Any]:
    """
    Lazy-load the EasyOCR reader on first use.

    EasyOCR handles a wider range of embedded-image document types than DocTR:
    rotated text, mixed fonts, and low-contrast clinical images.  Loaded only
    when USE_EMBEDDED_IMAGE_OCR is True and at least one embedded image meets
    the minimum pixel-area threshold.
    Thread-safe: uses a lock to prevent concurrent initialisation.
    """
    global _easy_ocr_reader
    if _easy_ocr_reader is not None:
        return _easy_ocr_reader

    if not config.USE_EMBEDDED_IMAGE_OCR:
        return None

    with _init_lock:
        if _easy_ocr_reader is not None:   # double-checked locking
            return _easy_ocr_reader
        try:
            import easyocr
            use_gpu = torch.cuda.is_available()
            _easy_ocr_reader = easyocr.Reader(["en"], gpu=use_gpu, verbose=False)
            logger.info("EasyOCR reader loaded (%s)", "GPU" if use_gpu else "CPU")
        except Exception as exc:
            logger.warning("Failed to load EasyOCR: %s", exc)
            _easy_ocr_reader = None

    return _easy_ocr_reader


def _extract_embedded_image_text(fitz_doc, page_idx: int) -> str:
    """
    Extract text from images that are embedded inside a PDF page.

    This is the *third* extraction path, complementing:
      - Native text extraction   (digitally created PDFs)
      - DocTR whole-page OCR     (fully scanned pages)

    This path handles PDFs where clinical data (lab results, handwritten notes)
    is stored as individual images EMBEDDED WITHIN an otherwise-text PDF.
    DocTR doesn't touch these because the page itself passes the native-text
    quality threshold — only the embedded sub-images are image-only.

    Filtering
    ---------
    Images smaller than EMBEDDED_IMAGE_MIN_PIXELS (width×height) are skipped
    to avoid OCR on logos, icons, and decorative elements.

    Returns a single string containing all text found across every qualifying
    image on the page, or "" if none found / EasyOCR unavailable.
    """
    if not config.USE_EMBEDDED_IMAGE_OCR:
        return ""

    reader = _init_easy_ocr()
    if reader is None:
        return ""

    page       = fitz_doc[page_idx]
    image_list = page.get_images(full=True)
    if not image_list:
        return ""

    texts: List[str] = []
    for img_info in image_list:
        xref = img_info[0]
        try:
            base_image = fitz_doc.extract_image(xref)
            img_bytes  = base_image["image"]
            img_pil    = Image.open(io.BytesIO(img_bytes)).convert("RGB")
            w, h       = img_pil.size

            if w * h < config.EMBEDDED_IMAGE_MIN_PIXELS:
                continue   # skip small decorative images

            img_array = np.array(img_pil)
            # detail=0 → plain text list (no bounding boxes); paragraph=True
            # merges nearby words into lines for cleaner output
            with _gpu_lock:
                results   = reader.readtext(img_array, detail=0, paragraph=True)
            page_text = " ".join(str(r) for r in results).strip()
            if page_text:
                texts.append(page_text)

        except Exception as exc:
            logger.debug("Embedded image OCR skipped (xref=%d): %s", xref, exc)
            continue

    return "\n".join(texts)


# ─────────────────────────────────────────────────────────────────────────────
# Per-page extraction helpers
# ─────────────────────────────────────────────────────────────────────────────

def _get_native_page_text(page) -> str:
    """
    Extract text from a single PyMuPDF page object.

    Strategy:
    1. Use get_text("blocks") with sort=True — returns text blocks in
       reading order, which is usually correct for clinical reports.
    2. Fall back to get_text("text") if blocks returns nothing.

    Returns a single normalised string.
    """
    parts = []

    try:
        # Each block is a tuple; block[4] is the text string when it exists
        for block in page.get_text("blocks", sort=True):
            if len(block) >= 5 and isinstance(block[4], str):
                t = normalize_text(block[4])
                if t:
                    parts.append(t)
    except Exception:
        pass

    if not parts:
        # Fallback: simpler plain-text extraction (less reading-order aware)
        try:
            t = normalize_text(page.get_text("text", sort=True))
            if t:
                parts.append(t)
        except Exception:
            pass

    return "\n".join(parts).strip()


def _page_needs_ocr(text: str) -> bool:
    """
    Decide whether a page's native text is good enough or needs OCR.

    A page is considered "weak" if ANY of these conditions hold:
    - fewer than NATIVE_TEXT_MIN_CHARS characters → too short, likely image-only
    - fewer than NATIVE_TEXT_MIN_WORDS words      → garbled or table-heavy
    - alpha ratio below NATIVE_TEXT_MIN_ALPHA_RATIO → mostly numbers/symbols

    Thresholds live in config so they can be tuned without code changes.
    """
    text = normalize_text(text)
    if len(text) < config.NATIVE_TEXT_MIN_CHARS:
        return True
    if word_count(text) < config.NATIVE_TEXT_MIN_WORDS:
        return True
    if alpha_ratio(text) < config.NATIVE_TEXT_MIN_ALPHA_RATIO:
        return True
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def extract_pdf_pages(pdf_path: str) -> List[Dict[str, Any]]:
    """
    Extract text from every page of a PDF.

    Two-phase approach
    ------------------
    Phase 1 — Native extraction (all pages, fast):
      Read text via PyMuPDF; record quality metrics per page.
      Pages that fail the quality threshold are queued for OCR.

    Phase 2 — DocTR OCR (weak pages only, GPU-batched):
      Render each weak page to a PNG at OCR_RENDER_DPI DPI, then run
      DocTR in batches of OCR_BATCH_PAGES pages.
      OCR text replaces native text only when it is longer
      (i.e., OCR extracted more content → better quality).

    PNG images are written to a temp directory and cleaned up automatically.

    Returns
    -------
    List of page dicts, one per PDF page, each containing:
      page_num       : int    — 1-based page number
      text           : str    — best text (native or OCR)
      selected_source: str    — "native" or "ocr"
      char_count     : int
      word_count     : int
      alpha_ratio    : float
      need_ocr       : bool   — True if OCR was attempted for this page
    """
    doc = fitz.open(pdf_path)
    page_rows: List[Dict[str, Any]] = []
    weak_indices: List[int] = []     # indices into page_rows that need OCR

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)

        # ── Phase 1: native extraction ────────────────────────────────────
        for i in range(len(doc)):
            page       = doc[i]
            page_num   = i + 1
            native_text = normalize_text(_get_native_page_text(page))

            row: Dict[str, Any] = {
                "page_num":        page_num,
                "selected_source": "native",
                "text":            native_text,
                "char_count":      len(native_text),
                "word_count":      word_count(native_text),
                "alpha_ratio":     round(alpha_ratio(native_text), 4),
                "need_ocr":        False,
            }

            # If page text quality is poor and OCR is enabled, render to PNG
            if config.USE_DOCTR_OCR and _page_needs_ocr(native_text):
                img_path = tmp_path / f"page_{page_num:04d}.png"
                pix = page.get_pixmap(dpi=config.OCR_RENDER_DPI, alpha=False)
                pix.save(str(img_path))
                row["need_ocr"]          = True
                row["_tmp_image_path"]   = str(img_path)   # cleaned up below
                weak_indices.append(len(page_rows))

            page_rows.append(row)

        # ── Phase 2: batch OCR on weak pages ─────────────────────────────
        if config.USE_DOCTR_OCR and weak_indices:
            weak_paths = [page_rows[idx]["_tmp_image_path"] for idx in weak_indices]

            for start in range(0, len(weak_paths), config.OCR_BATCH_PAGES):
                batch_paths = weak_paths[start : start + config.OCR_BATCH_PAGES]
                batch_idxs  = weak_indices[start : start + config.OCR_BATCH_PAGES]
                batch_texts = _ocr_images(batch_paths)

                for j, idx in enumerate(batch_idxs):
                    ocr_text = normalize_text(
                        batch_texts[j] if j < len(batch_texts) else ""
                    )
                    # Only promote OCR result if it produced more content
                    if len(ocr_text) > len(page_rows[idx]["text"]):
                        page_rows[idx].update({
                            "text":            ocr_text,
                            "char_count":      len(ocr_text),
                            "word_count":      word_count(ocr_text),
                            "alpha_ratio":     round(alpha_ratio(ocr_text), 4),
                            "selected_source": "ocr",
                        })

        # Remove temp path keys before returning (not part of public schema)
        for row in page_rows:
            row.pop("_tmp_image_path", None)

        # ── Phase 3: Embedded image OCR ───────────────────────────────────
        # Run EasyOCR on any images embedded WITHIN each PDF page.
        # This captures clinical data stored as embedded image objects
        # (e.g. lab-result scans inside an otherwise-text PDF) — a gap that
        # neither native extraction nor DocTR whole-page OCR covers.
        # Text is appended to the existing page text, not replaced.
        if config.USE_EMBEDDED_IMAGE_OCR:
            for row in page_rows:
                img_text = normalize_text(
                    _extract_embedded_image_text(doc, row["page_num"] - 1)
                )
                if img_text:
                    combined = (
                        (row["text"] + "\n\n" + img_text).strip()
                        if row["text"] else img_text
                    )
                    row.update({
                        "text":            combined,
                        "char_count":      len(combined),
                        "word_count":      word_count(combined),
                        "alpha_ratio":     round(alpha_ratio(combined), 4),
                        "selected_source": row["selected_source"] + "+img",
                    })

    doc.close()
    return page_rows


def extract_text_from_uploaded_file(file_bytes: bytes, filename: str) -> str:
    """
    Extract raw text from an in-memory uploaded file.

    Supports:
    - .pdf  → PyMuPDF native extraction (no OCR fallback for quick preview)
    - .txt / other → decode as UTF-8, fall back to Latin-1

    Used by the upload view for quick text preview / JSON import matching.
    Does NOT write any temporary files.
    """
    if filename.lower().endswith(".pdf"):
        doc = fitz.open(stream=file_bytes, filetype="pdf")
        pages_text = [
            normalize_text(_get_native_page_text(doc[i]))
            for i in range(len(doc))
        ]
        doc.close()
        return "\n\n".join(pages_text)

    # Plain-text fallback: try UTF-8 first, then Latin-1
    try:
        return file_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return file_bytes.decode("latin-1")
