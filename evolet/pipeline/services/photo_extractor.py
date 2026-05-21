"""
Photo Extractor — Extract patient profile photos from PDF page 1.
==================================================================
doc-ocr patient reports typically include a small passport-style photo of
the patient in the upper-left area of the first page.  This module
attempts to extract it via two methods, tried in order:

  Method 1 — Embedded image detection:
    Inspect every image embedded in page 1.  Score each candidate by
    size, aspect ratio, and position (left column, below header).
    Pick the highest-scoring candidate and extract its raw bytes.

  Method 2 — Fixed-coordinate crop (fallback):
    If no suitable embedded image is found, render a known sub-region
    of page 1 (6%–25% x, 24%–43% y) at 220 DPI and use that crop.
    This works even when the photo is part of the page raster rather
    than a distinct PDF image object.

Both methods trim white borders from the extracted image before saving.

Public API
----------
extract_profile_photo(pdf_path, output_path) → metadata dict
"""

import io
import re
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import fitz                         # PyMuPDF
from PIL import Image, ImageChops

logger = logging.getLogger("pipeline")


# ─────────────────────────────────────────────────────────────────────────────
# Utility
# ─────────────────────────────────────────────────────────────────────────────

def safe_stem(name: str) -> str:
    """
    Sanitise a filename stem for use in paths.
    Replaces non-alphanumeric characters with underscores.
    """
    name = str(name or "").strip()
    name = re.sub(r"[^\w.\- ]+", "_", name)
    name = re.sub(r"\s+", "_", name).strip("._ ")
    return name or "unknown_pdf"


def _trim_white_border(img: Image.Image) -> Image.Image:
    """
    Auto-crop white borders from a PIL image.

    Computes a pixel-difference image against a pure-white background;
    the bounding box of non-zero pixels gives the content bounds.
    Returns the original image unchanged if cropping would produce a
    degenerate result (< 20 px in either dimension).
    """
    if img.mode != "RGB":
        img = img.convert("RGB")

    bg   = Image.new("RGB", img.size, (255, 255, 255))
    diff = ImageChops.difference(img, bg)
    bbox = diff.getbbox()

    if bbox:
        cropped = img.crop(bbox)
        # Guard against near-empty crops caused by a very light photo
        if cropped.size[0] >= 20 and cropped.size[1] >= 20:
            return cropped

    return img


# ─────────────────────────────────────────────────────────────────────────────
# Method 1 — Embedded image candidate selection
# ─────────────────────────────────────────────────────────────────────────────

def _score_photo_candidate(
    info: Dict[str, Any],
    page_width: float,
    page_height: float,
) -> Optional[Dict[str, Any]]:
    """
    Score a single embedded image as a patient-photo candidate.

    Scoring heuristics
    ------------------
    - Position: must be in the left 35% of the page and below 12% from top
      (avoids logo/header images at the very top)
    - Area: must occupy between 0.08% and 5% of the page area
      (filters out icons and full-page raster scans)
    - Aspect: width/height between 0.35 and 1.35 (portrait or square)
    - Bonus +3  if the image is in the left 25% (ideal column)
    - Bonus +3  if y-position is in the 18%–50% band (typical location)
    - Bonus +2  if height ≥ width (portrait orientation preferred)
    - Bonus ≤ 2 from relative area size

    Returns a scored candidate dict, or None if any hard constraint fails.
    """
    xref = info.get("xref", 0)
    bbox = info.get("bbox")
    if not xref or not bbox:
        return None

    rect = fitz.Rect(bbox)
    bw, bh = float(rect.width), float(rect.height)
    if bw <= 0 or bh <= 0:
        return None

    area_ratio = (bw * bh) / max(page_width * page_height, 1.0)
    aspect     = bw / max(bh, 1.0)

    # Hard constraints — reject if any fail
    if rect.y0 < 0.12 * page_height:          return None   # too close to top
    if rect.x0 > 0.35 * page_width:           return None   # too far right
    if not (0.0008 <= area_ratio <= 0.05):     return None   # too small or too large
    if not (0.35   <= aspect     <= 1.35):     return None   # wrong shape

    # Soft scoring
    score = 0.0
    if rect.x0 <= 0.25 * page_width:                    score += 3
    if 0.18 * page_height <= rect.y0 <= 0.50 * page_height: score += 3
    if bh >= bw:                                         score += 2   # portrait
    score += min(area_ratio * 100, 2)

    return {"score": score, "xref": xref, "bbox": bbox}


def _pick_patient_photo(page) -> Optional[Dict[str, Any]]:
    """
    Find the best patient-photo candidate among all embedded images on *page*.

    Returns the highest-scoring candidate dict (with "xref" and "bbox"),
    or None if no candidates pass the scoring threshold.
    """
    try:
        infos: List[Dict] = page.get_image_info(xrefs=True)
    except Exception:
        infos = []

    pw = float(page.rect.width)
    ph = float(page.rect.height)

    candidates = [
        c for info in infos
        if (c := _score_photo_candidate(info, pw, ph)) is not None
    ]

    if not candidates:
        return None

    candidates.sort(key=lambda x: x["score"], reverse=True)
    return candidates[0]


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def extract_profile_photo(pdf_path: str, output_path: str) -> Dict[str, Any]:
    """
    Extract the patient profile photo from the first page of a PDF.

    Tries Method 1 (embedded image) first; falls back to Method 2
    (fixed-coordinate crop) if no suitable embedded image is found.

    Parameters
    ----------
    pdf_path    : absolute path to the PDF file
    output_path : where to save the extracted photo (PNG format)

    Returns
    -------
    Meta dict:
      photo_found : bool   — True if a photo was saved
      method      : str    — "embedded_image" | "page_crop_fallback" | ""
      error       : str    — exception repr if something went wrong
    """
    meta: Dict[str, Any] = {
        "photo_found": False,
        "method":      "",
        "error":       "",
    }

    try:
        doc  = fitz.open(pdf_path)
        page = doc[0]

        # ── Method 1: Extract an embedded image object ────────────────────
        candidate = _pick_patient_photo(page)
        if candidate is not None:
            try:
                img_info   = doc.extract_image(candidate["xref"])
                image_bytes = img_info.get("image")
                if image_bytes:
                    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
                    img = _trim_white_border(img)
                    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
                    img.save(output_path, format="PNG")
                    meta["photo_found"] = True
                    meta["method"]      = "embedded_image"
            except Exception:
                pass   # fall through to Method 2

        # ── Method 2: Render a fixed-coordinate crop at high DPI ─────────
        if not meta["photo_found"]:
            try:
                r = page.rect
                # Region known to contain the patient photo in doc-ocr reports:
                # x: 6%–25% of page width, y: 24%–43% of page height
                clip = fitz.Rect(
                    r.x0 + 0.06 * r.width,
                    r.y0 + 0.24 * r.height,
                    r.x0 + 0.25 * r.width,
                    r.y0 + 0.43 * r.height,
                )
                pix = page.get_pixmap(clip=clip, dpi=220, alpha=False)
                img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                img = _trim_white_border(img)
                Path(output_path).parent.mkdir(parents=True, exist_ok=True)
                img.save(output_path, format="PNG")
                meta["photo_found"] = True
                meta["method"]      = "page_crop_fallback"
            except Exception:
                pass

        doc.close()

    except Exception as exc:
        meta["error"] = repr(exc)

    return meta
