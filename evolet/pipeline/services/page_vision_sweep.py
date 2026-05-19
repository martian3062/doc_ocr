"""Page-level visual OCR sweep for scanned medical forms.

The normal PDF text path can miss handwritten orders because PyMuPDF sees only
the printed template, while whole-page OCR loses reading order. This module
renders each page, crops predictable clinical zones, OCRs them independently,
and returns coordinate-bearing artifacts that are merged beside native text.
"""

from __future__ import annotations

import base64
import json
import logging
from io import BytesIO
from statistics import pstdev
from typing import Any, Dict, Iterable, List, Tuple
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import fitz
from PIL import Image, ImageEnhance, ImageFilter, ImageOps

from . import config
from .json_utils import parse_json_loose
from .ocr_backends import get_got_ocr_backend, get_medical_handwriting_backend, get_trocr_backend
from .pdf_extractor import normalize_text

logger = logging.getLogger("pipeline")
GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"


Zone = Tuple[str, str, Tuple[float, float, float, float]]


def collect_page_vision_artifacts(pdf_path: str) -> List[Dict[str, Any]]:
    if not config.ENABLE_PAGE_VISION_SWEEP:
        return []

    if config.ENABLE_GROQ_VISION_OCR:
        return _collect_groq_vision_artifacts(pdf_path)

    if not (config.ENABLE_HANDWRITING_OCR and config.ENABLE_LOCAL_HF_VISION_MODELS):
        return []

    artifacts: List[Dict[str, Any]] = []
    trocr = get_trocr_backend()
    got = get_got_ocr_backend()
    medical = get_medical_handwriting_backend()

    doc = fitz.open(pdf_path)
    try:
        for page_index in range(len(doc)):
            page = doc[page_index]
            page_num = page_index + 1
            page_width = float(page.rect.width)
            page_height = float(page.rect.height)

            for order, (role, label, rel_box) in enumerate(_zones(), start=1):
                if order > config.PAGE_VISION_MAX_CROPS_PER_PAGE:
                    break
                bbox = _relative_to_page_bbox(rel_box, page_width, page_height)
                try:
                    crop = _render_crop(page, bbox)
                    if _is_blankish(crop):
                        continue
                    prepared = _prepare_for_handwriting(crop)
                    primary = trocr.recognize(prepared)
                    medical_result = (
                        medical.recognize(prepared)
                        if config.ENABLE_MEDICAL_HANDWRITING_OCR and config.MEDICAL_HANDWRITING_MODEL_ID
                        else None
                    )
                    verifier = got.recognize(prepared) if config.ENABLE_GOT_VERIFICATION else None
                    text, confidence, candidates = _choose_text(primary, medical_result, verifier)
                    text = normalize_text(text)
                    if len(text) < config.PAGE_VISION_MIN_TEXT_CHARS:
                        continue
                    artifacts.append({
                        "artifact_type": "handwriting" if role in {"orders", "vitals", "identity"} else "text_block",
                        "role": role,
                        "backend": "page_vision_sweep",
                        "text": text,
                        "normalized_text": text,
                        "confidence": confidence,
                        "bbox": bbox,
                        "polygon": [],
                        "page_num": page_num,
                        "reading_order": 5000 + order,
                        "metadata": {
                            "zone": label,
                            "page_width": page_width,
                            "page_height": page_height,
                            "render_dpi": config.PAGE_VISION_SWEEP_DPI,
                            "trocr_model_id": config.TROCR_MODEL_ID,
                            "medical_handwriting_model_id": config.MEDICAL_HANDWRITING_MODEL_ID,
                            "medocr_reference_dataset": config.MEDOCR_VISION_DATASET_ID,
                            "candidates": candidates,
                        },
                    })
                except Exception as exc:
                    logger.debug("Page vision sweep skipped p%s %s: %s", page_num, label, exc)
    finally:
        doc.close()

    logger.info("Page vision sweep generated %d artifacts for %s", len(artifacts), pdf_path)
    return artifacts


def _collect_groq_vision_artifacts(pdf_path: str) -> List[Dict[str, Any]]:
    if not config.GROQ_API_KEY:
        logger.warning("Groq vision OCR enabled but GROQ key is missing")
        return []

    artifacts: List[Dict[str, Any]] = []
    doc = fitz.open(pdf_path)
    try:
        page_limit = min(len(doc), max(1, config.PAGE_VISION_MAX_PAGES_PER_DOCUMENT))
        for page_index in range(page_limit):
            page = doc[page_index]
            page_num = page_index + 1
            page_width = float(page.rect.width)
            page_height = float(page.rect.height)
            for order, (role, label, rel_box) in enumerate(_zones(), start=1):
                if order > config.PAGE_VISION_MAX_CROPS_PER_PAGE:
                    break
                bbox = _relative_to_page_bbox(rel_box, page_width, page_height)
                try:
                    crop = _render_crop(page, bbox)
                    if _is_blankish(crop):
                        continue
                    prepared = _prepare_for_handwriting(crop)
                    result = _groq_read_image(prepared, role=role, label=label, page_num=page_num)
                    text = normalize_text(result.get("text", ""))
                    if len(text) < config.PAGE_VISION_MIN_TEXT_CHARS:
                        continue
                    artifacts.append({
                        "artifact_type": "handwriting" if role in {"orders", "vitals", "identity"} else "text_block",
                        "role": role,
                        "backend": "groq_vision",
                        "text": text,
                        "normalized_text": text,
                        "confidence": float(result.get("confidence") or 0.72),
                        "bbox": bbox,
                        "polygon": [],
                        "page_num": page_num,
                        "reading_order": 5000 + order,
                        "metadata": {
                            "zone": label,
                            "page_width": page_width,
                            "page_height": page_height,
                            "render_dpi": config.PAGE_VISION_SWEEP_DPI,
                            "model": config.GROQ_VISION_MODEL,
                            "fields": result.get("fields", []),
                            "medications": result.get("medications", []),
                            "vitals": result.get("vitals", []),
                            "python_preprocess": ["pymupdf_render", "pillow_grayscale", "pillow_autocontrast", "pillow_sharpen"],
                        },
                    })
                except Exception as exc:
                    logger.debug("Groq page vision skipped p%s %s: %s", page_num, label, exc)
    finally:
        doc.close()

    logger.info("Groq page vision generated %d artifacts for %s", len(artifacts), pdf_path)
    return artifacts


def _groq_read_image(image: Image.Image, *, role: str, label: str, page_num: int) -> Dict[str, Any]:
    prompt = (
        "You are a medical OCR vision model. Read this crop exactly. "
        "Return JSON only with keys: text, confidence, fields, medications, vitals. "
        "Preserve handwritten drug names, dosages, vitals, identifiers, dates, and unclear words with [?]. "
        f"Context: page={page_num}, region_role={role}, region_label={label}."
    )
    body = {
        "model": config.GROQ_VISION_MODEL,
        "temperature": 0,
        "max_completion_tokens": 450,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": _image_data_url(image)}},
                ],
            }
        ],
    }
    req = Request(
        GROQ_CHAT_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {config.GROQ_API_KEY}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "doc-reader-groq-vision/1.0",
        },
        method="POST",
    )
    try:
        with urlopen(req, timeout=config.GROQ_VISION_TIMEOUT_SECONDS) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"Groq vision HTTP {exc.code}: {detail[:500]}") from exc
    except URLError as exc:
        raise RuntimeError(f"Groq vision request failed: {exc}") from exc

    content = data["choices"][0]["message"].get("content", "")
    parsed = parse_json_loose(content)
    if not isinstance(parsed, dict):
        return {"text": normalize_text(content), "confidence": 0.55, "fields": [], "medications": [], "vitals": []}
    return parsed


def _image_data_url(image: Image.Image) -> str:
    bounded = image.convert("RGB")
    max_side = 1400
    if max(bounded.size) > max_side:
        bounded.thumbnail((max_side, max_side))
    buf = BytesIO()
    bounded.save(buf, format="JPEG", quality=82, optimize=True)
    encoded = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def _zones() -> Iterable[Zone]:
    return [
        ("identity", "full_header", (0.02, 0.00, 0.98, 0.24)),
        ("identity", "case_header_left", (0.00, 0.00, 0.36, 0.24)),
        ("identity", "case_header_right", (0.30, 0.00, 0.98, 0.24)),
        ("vitals", "left_vitals_column", (0.00, 0.20, 0.22, 0.92)),
        ("orders", "main_order_area", (0.20, 0.20, 0.98, 0.92)),
        ("orders", "upper_orders", (0.20, 0.24, 0.98, 0.50)),
        ("orders", "middle_orders", (0.20, 0.42, 0.98, 0.70)),
        ("orders", "lower_orders", (0.20, 0.62, 0.98, 0.92)),
        ("orders", "center_left_orders", (0.20, 0.25, 0.62, 0.82)),
        ("orders", "center_right_orders", (0.55, 0.25, 0.98, 0.82)),
        ("table", "full_body_grid", (0.00, 0.20, 0.98, 0.98)),
    ]


def _relative_to_page_bbox(rel_box: Tuple[float, float, float, float], width: float, height: float) -> List[float]:
    x1, y1, x2, y2 = rel_box
    return [
        round(max(0.0, min(width, x1 * width)), 2),
        round(max(0.0, min(height, y1 * height)), 2),
        round(max(0.0, min(width, x2 * width)), 2),
        round(max(0.0, min(height, y2 * height)), 2),
    ]


def _render_crop(page, bbox: List[float]) -> Image.Image:
    rect = fitz.Rect(bbox)
    pix = page.get_pixmap(clip=rect, dpi=config.PAGE_VISION_SWEEP_DPI, alpha=False)
    return Image.open(BytesIO(pix.tobytes("png"))).convert("RGB")


def _is_blankish(image: Image.Image) -> bool:
    gray = ImageOps.grayscale(image.resize((max(1, image.width // 8), max(1, image.height // 8))))
    values = list(gray.getdata())
    if not values:
        return True
    return pstdev(values) < config.PAGE_VISION_MIN_CROP_STDDEV


def _prepare_for_handwriting(image: Image.Image) -> Image.Image:
    gray = ImageOps.grayscale(image)
    gray = ImageOps.autocontrast(gray)
    gray = ImageEnhance.Contrast(gray).enhance(1.8)
    gray = gray.filter(ImageFilter.SHARPEN)
    if max(gray.size) < 1200:
        gray = gray.resize((gray.width * 2, gray.height * 2))
    return gray.convert("RGB")


def _choose_text(*results) -> tuple[str, float, List[Dict[str, Any]]]:
    candidates = [result for result in results if result and result.text]
    if not candidates:
        return "", 0.0, []
    scored = []
    for item in candidates:
        text = normalize_text(item.text)
        score = (item.confidence or 0.0) + min(len(text), 120) / 120.0
        scored.append((score, item, text))
    scored.sort(key=lambda row: row[0], reverse=True)
    chosen = scored[0]
    return (
        chosen[2],
        max(item.confidence for _, item, _ in scored),
        [
            {
                "backend": item.backend,
                "text": text,
                "confidence": item.confidence,
                "metadata": item.metadata,
            }
            for _, item, text in scored
        ],
    )
