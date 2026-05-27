"""Page-level visual OCR sweep for scanned medical forms.

Crop-level OCR with fallback chain per zone:
  1. Chandra OCR  (datalab-to/chandra-ocr-2 — best open-source crop handwriting)
  2. Groq Vision  (cloud API, no GPU needed — fast fallback)
  3. TrOCR / Medical HW  (local small models — final fallback)
"""

from __future__ import annotations

import base64
import json
import logging
from io import BytesIO
from statistics import pstdev
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import fitz
from PIL import Image, ImageEnhance, ImageFilter, ImageOps

from . import config
from .json_utils import parse_json_loose
from .ocr_backends import get_medical_handwriting_backend, get_trocr_backend
from .pdf_extractor import normalize_text

logger = logging.getLogger("pipeline")
GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"

Zone = Tuple[str, str, Tuple[float, float, float, float]]

_GROQ_CROP_PROMPT = (
    "You are a medical OCR model reading a cropped region of an Indian hospital form "
    "(Tata Memorial Hospital / similar). The image may contain handwritten doctor "
    "orders, medication prescriptions, vital signs, or patient identifiers.\n\n"
    "Extract ALL visible text exactly as written. Common Indian abbreviations:\n"
    "Inj=Injection, Tab=Tablet, BD=twice daily, TDS=3x daily, OD=once daily, "
    "SOS/PRN=as needed, STAT=immediately, IV=intravenous, IM=intramuscular, "
    "SC=subcutaneous, N/S=normal saline, RL=Ringer lactate, "
    "BP=blood pressure, PR=pulse, SpO2=oxygen saturation, RR=resp rate, "
    "UHID=hospital ID, IPD=inpatient, OPD=outpatient.\n\n"
    "Return JSON only:\n"
    '{\"text\": \"<all text>\", \"confidence\": 0.0-1.0, '
    '\"fields\": [], \"medications\": [], \"vitals\": []}\n\n'
    "Mark unclear words with [?]. Do NOT invent text."
)


def collect_page_vision_artifacts(pdf_path: str) -> List[Dict[str, Any]]:
    if not config.ENABLE_PAGE_VISION_SWEEP:
        return []
    return _collect_artifacts_with_fallback_chain(pdf_path)


def _collect_artifacts_with_fallback_chain(pdf_path: str) -> List[Dict[str, Any]]:
    """Process each crop with: Chandra → Groq Vision → TrOCR."""
    chandra_backend = _try_get_chandra()
    groq_enabled = config.ENABLE_GROQ_VISION_OCR and bool(config.GROQ_API_KEY)
    trocr = get_trocr_backend()
    medical = get_medical_handwriting_backend()

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

                    text, confidence, backend_used, extra_meta = _read_crop(
                        prepared,
                        role=role,
                        label=label,
                        page_num=page_num,
                        chandra=chandra_backend,
                        groq_enabled=groq_enabled,
                        trocr=trocr,
                        medical=medical,
                    )

                    text = normalize_text(text)
                    if len(text) < config.PAGE_VISION_MIN_TEXT_CHARS:
                        continue

                    artifacts.append({
                        "artifact_type": "handwriting" if role in {"orders", "vitals", "identity"} else "text_block",
                        "role": role,
                        "backend": backend_used,
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
                            **extra_meta,
                        },
                    })
                except Exception as exc:
                    logger.debug("Page vision sweep skipped p%s %s: %s", page_num, label, exc)
    finally:
        doc.close()

    logger.info("Groq page vision generated %d artifacts for %s", len(artifacts), pdf_path)
    return artifacts


def _read_crop(
    image: Image.Image,
    *,
    role: str,
    label: str,
    page_num: int,
    chandra,
    groq_enabled: bool,
    trocr,
    medical,
) -> Tuple[str, float, str, Dict]:
    """Try each OCR backend in order, return (text, confidence, backend_name, extra_meta)."""

    # 1. Chandra OCR (best for Indian medical handwriting crops)
    if chandra is not None:
        try:
            result = chandra.recognize(image)
            if result.text and len(result.text.strip()) >= config.PAGE_VISION_MIN_TEXT_CHARS:
                return result.text, result.confidence or 0.88, "chandra_ocr", {
                    "model_id": config.CHANDRA_OCR_MODEL_ID,
                }
        except Exception as exc:
            logger.debug("Chandra crop failed p%s %s: %s", page_num, label, exc)

    # 2. Groq Vision (cloud fallback — good at reading context + handwriting)
    if groq_enabled:
        try:
            result = _groq_read_image(image, role=role, label=label, page_num=page_num)
            text = normalize_text(result.get("text", ""))
            if len(text) >= config.PAGE_VISION_MIN_TEXT_CHARS:
                return text, float(result.get("confidence") or 0.72), "groq_vision", {
                    "model": config.GROQ_VISION_MODEL,
                    "fields": result.get("fields", []),
                    "medications": result.get("medications", []),
                    "vitals": result.get("vitals", []),
                }
        except Exception as exc:
            logger.debug("Groq crop failed p%s %s: %s", page_num, label, exc)

    # 3. Local fallback: Medical HW → TrOCR
    primary = trocr.recognize(image)
    medical_result: Optional[Any] = None
    if config.ENABLE_MEDICAL_HANDWRITING_OCR and config.MEDICAL_HANDWRITING_MODEL_ID:
        try:
            medical_result = medical.recognize(image)
        except Exception:
            pass

    text, confidence, _ = _choose_text(primary, medical_result)
    return text, confidence, "trocr_fallback", {
        "trocr_model_id": config.TROCR_MODEL_ID,
        "medical_handwriting_model_id": config.MEDICAL_HANDWRITING_MODEL_ID,
    }


def _try_get_chandra():
    """Return Chandra backend if enabled and available, else None."""
    if not config.ENABLE_CHANDRA_OCR:
        return None
    try:
        from .ocr_backends import get_chandra_ocr_backend
        backend = get_chandra_ocr_backend()
        if backend.is_available():
            return backend
    except Exception as exc:
        logger.debug("Chandra backend unavailable: %s", exc)
    return None


def _groq_read_image(image: Image.Image, *, role: str, label: str, page_num: int) -> Dict[str, Any]:
    body = {
        "model": config.GROQ_VISION_MODEL,
        "temperature": 0,
        "max_completion_tokens": 600,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": _GROQ_CROP_PROMPT + f"\n\nContext: page={page_num}, zone={label}, role={role}"},
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
            "User-Agent": "doc-ocr-groq-vision/1.0",
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
        ("orders", "chart_medicine_table_full", (0.02, 0.12, 0.98, 0.96)),
        ("orders", "main_order_area", (0.20, 0.20, 0.98, 0.92)),
        ("orders", "middle_orders", (0.20, 0.42, 0.98, 0.70)),
        ("orders", "lower_orders", (0.20, 0.62, 0.98, 0.92)),
        ("orders", "upper_orders", (0.20, 0.24, 0.98, 0.50)),
        ("orders", "center_left_orders", (0.20, 0.25, 0.62, 0.82)),
        ("orders", "center_right_orders", (0.55, 0.25, 0.98, 0.82)),
        ("table", "full_body_grid", (0.00, 0.20, 0.98, 0.98)),
        ("vitals", "left_vitals_column", (0.00, 0.20, 0.22, 0.92)),
        ("identity", "full_header", (0.02, 0.00, 0.98, 0.24)),
        ("identity", "case_header_left", (0.00, 0.00, 0.36, 0.24)),
        ("identity", "case_header_right", (0.30, 0.00, 0.98, 0.24)),
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
