"""Crop-level doctor-handwriting extraction for medication orders and vitals."""

from __future__ import annotations

import base64
import json
import logging
import re
from io import BytesIO
from statistics import pstdev
from typing import Any, Dict, Iterable, List, Tuple
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import fitz
from PIL import Image, ImageEnhance, ImageFilter, ImageOps

from . import config
from .json_utils import parse_json_loose
from .medical_short_forms import normalize_order_text, normalize_orders
from .medocr_reference import get_medocr_reference_context
from .pdf_extractor import normalize_text

logger = logging.getLogger("pipeline")

GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"


def extract_handwriting_order_artifacts(pdf_path: str, artifacts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not config.ENABLE_HANDWRITING_ORDER_EXTRACTOR:
        return []
    if not config.ENABLE_GROQ_VISION_OCR:
        return []
    if not config.GROQ_API_KEY:
        logger.warning("Handwriting order extractor enabled but Groq key is missing")
        return []

    reference = get_medocr_reference_context()
    out: List[Dict[str, Any]] = []
    doc = fitz.open(pdf_path)
    try:
        page_limit = min(len(doc), max(1, config.HANDWRITING_ORDER_MAX_PAGES_PER_DOCUMENT))
        for page_index in range(page_limit):
            page = doc[page_index]
            page_num = page_index + 1
            candidates = _candidate_boxes(page, artifacts, page_num)
            for order, (role, bbox, source) in enumerate(candidates[: config.HANDWRITING_ORDER_MAX_CROPS_PER_PAGE], start=1):
                try:
                    crop = _render_crop(page, bbox)
                    if _is_blankish(crop):
                        continue
                    prepared = _prepare_crop(crop)
                    result = _groq_read_order_crop(prepared, role=role, page_num=page_num, reference=reference)
                    artifact = _artifact_from_result(result, bbox=bbox, page_num=page_num, reading_order=7600 + order, source=source, reference=reference)
                    if artifact:
                        out.append(artifact)
                except Exception as exc:
                    logger.debug("Handwriting order crop skipped p%s %s: %s", page_num, role, exc)
    finally:
        doc.close()

    logger.info("Handwriting order extractor generated %d artifacts for %s", len(out), pdf_path)
    return out


def _candidate_boxes(page, artifacts: List[Dict[str, Any]], page_num: int) -> List[Tuple[str, List[float], Dict[str, Any]]]:
    width = float(page.rect.width)
    height = float(page.rect.height)
    candidates: List[Tuple[str, List[float], Dict[str, Any]]] = []
    seen = set()
    page_texts: List[str] = []

    for artifact in artifacts:
        if int(artifact.get("page_num") or 0) != page_num:
            continue
        bbox = _valid_bbox(artifact.get("bbox"), width, height)
        if not bbox:
            continue
        role = str(artifact.get("role") or "").lower()
        artifact_type = str(artifact.get("artifact_type") or "")
        text = normalize_text(artifact.get("text") or artifact.get("normalized_text") or "")
        if text:
            page_texts.append(text)
        if role in {"header", "footer", "title", "figure"}:
            continue
        if artifact_type not in {"page_region", "table", "text_block", "handwriting"}:
            continue
        if text and len(text) > 140 and not _has_order_signal(text):
            continue
        expanded = _expand_box(bbox, width, height, pad_x=18, pad_y=14)
        area = max(0.0, expanded[2] - expanded[0]) * max(0.0, expanded[3] - expanded[1])
        if area < config.HANDWRITING_ORDER_MIN_CROP_AREA:
            continue
        key = tuple(round(v / 8) for v in expanded)
        if key in seen:
            continue
        seen.add(key)
        candidates.append((
            role if role in {"orders", "vitals", "body", "table"} else "orders",
            expanded,
            {
                "source_backend": artifact.get("backend", ""),
                "source_type": artifact_type,
                "source_role": role,
                "source_text": text[:250],
            },
        ))

    if _has_chart_signal(" ".join(page_texts)):
        for role, rel in _chart_zones():
            bbox = _relative_to_page_bbox(rel, width, height)
            key = tuple(round(v / 8) for v in bbox)
            if key not in seen:
                seen.add(key)
                candidates.append((role, bbox, {"source_backend": "chart_page_signal", "source_type": "page_region", "source_role": role, "source_text": "staff nurse medicines injections chart"}))

    for role, rel in _fallback_zones():
        bbox = _relative_to_page_bbox(rel, width, height)
        key = tuple(round(v / 8) for v in bbox)
        if key not in seen:
            seen.add(key)
            candidates.append((role, bbox, {"source_backend": "fallback_zone", "source_type": "region", "source_role": role}))

    return sorted(candidates, key=lambda item: (_priority(item[0], item[2]), item[1][1], item[1][0]))


def _artifact_from_result(
    result: Dict[str, Any],
    *,
    bbox: List[float],
    page_num: int,
    reading_order: int,
    source: Dict[str, Any],
    reference: Dict[str, Any],
) -> Dict[str, Any] | None:
    raw_text = normalize_text(result.get("raw_text") or result.get("text") or "")
    orders = [
        item
        for item in normalize_orders(result.get("medication_orders") or result.get("medications") or [])
        if _is_meaningful_order(item)
    ]
    vitals = _normalize_vitals(result.get("vitals") or [])
    fields = result.get("fields") or []
    normalizer = normalize_order_text(raw_text) if raw_text else {"expanded_text": "", "drug_candidates": [], "short_forms": []}
    lines: List[str] = []

    for item in orders:
        drug = _best_drug(item)
        display = normalize_text(item.get("expanded_text") or item.get("raw_text") or item.get("text") or "")
        if drug and drug.lower() not in display.lower():
            display = f"{drug}: {display}"
        if display:
            lines.append(f"Medication order: {display}")
    for item in vitals:
        if item.get("label") and item.get("value"):
            lines.append(f"Vital: {item['label']} {item['value']}")
    for item in fields:
        if isinstance(item, dict) and item.get("label") and item.get("value"):
            lines.append(f"{item['label']}: {item['value']}")

    if raw_text and not lines:
        lines.append(normalizer.get("expanded_text") or raw_text)
    text = normalize_text("\n".join(lines))
    if not text:
        return None

    return {
        "artifact_type": "handwriting",
        "role": "orders",
        "backend": "groq_handwriting_order",
        "text": raw_text or text,
        "normalized_text": text,
        "confidence": float(result.get("confidence") or 0.74),
        "bbox": bbox,
        "polygon": [],
        "page_num": page_num,
        "reading_order": reading_order,
        "metadata": {
            **source,
            "model": config.GROQ_VISION_MODEL,
            "extractor": "handwriting_order_extractor",
            "medocr_reference_dataset": reference.get("dataset_id"),
            "medocr_reference_enabled": reference.get("enabled"),
            "raw_result": result,
            "order_items": orders,
            "vitals": vitals,
            "fields": fields,
            "short_forms": normalizer.get("short_forms", []),
            "drug_candidates": normalizer.get("drug_candidates", []),
        },
    }


def _groq_read_order_crop(image: Image.Image, *, role: str, page_num: int, reference: Dict[str, Any]) -> Dict[str, Any]:
    examples = "\n".join(f"- {text}" for text in reference.get("examples", [])[:3])
    prompt = f"""
Read this hospital case-sheet crop. It may contain doctor handwriting, chemo orders, vitals, short forms, arrows, or overwritten text.

Return JSON only:
{{
  "raw_text": "verbatim transcription, preserve uncertain words with [?]",
  "confidence": 0.0,
  "medication_orders": [],
  "vitals": [],
  "fields": [],
  "unreadable": []
}}

Rules:
- Extract every medicine/order/vital you can see, even if spelling is imperfect.
- Expand common short forms mentally: Inj, IV/I.V., Tab, OD, BD, TDS, SOS, STAT, NS, DNS, D5, RL.
- For oncology/case-sheet handwriting, look hard for Palonosetron, Pantoprazole/Pan, Trastuzumab, Docetaxel, Paclitaxel, Carboplatin, Cisplatin, Ondansetron, Dexamethasone.
- Do not invent values. If unsure, keep the raw spelling and mark uncertain true.
- Region role={role}; page={page_num}.

MedOCR reference patterns from {reference.get("dataset_id")}:
{examples}
""".strip()
    body = {
        "model": config.GROQ_VISION_MODEL,
        "temperature": 0,
        "max_completion_tokens": 700,
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
            "User-Agent": "doc-reader-handwriting-order/1.0",
        },
        method="POST",
    )
    try:
        with urlopen(req, timeout=config.GROQ_VISION_TIMEOUT_SECONDS) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"Groq handwriting HTTP {exc.code}: {detail[:500]}") from exc
    except URLError as exc:
        raise RuntimeError(f"Groq handwriting request failed: {exc}") from exc

    content = data["choices"][0]["message"].get("content", "")
    parsed = parse_json_loose(content)
    if isinstance(parsed, dict):
        return parsed
    return {"raw_text": content, "confidence": 0.45, "medication_orders": [], "vitals": [], "fields": []}


def _normalize_vitals(items: Iterable[Dict[str, Any] | str]) -> List[Dict[str, Any]]:
    out = []
    for item in items:
        if isinstance(item, str):
            text = normalize_text(item)
            label, value = _split_label_value(text)
            out.append({"label": label, "value": value, "unit": "", "raw_text": text})
        elif isinstance(item, dict):
            label = normalize_text(str(item.get("label") or item.get("name") or ""))
            value = normalize_text(str(item.get("value") or ""))
            if label or value:
                out.append({**item, "label": label, "value": value})
    return out


def _is_meaningful_order(item: Dict[str, Any]) -> bool:
    text = normalize_text(
        " ".join(
            str(item.get(key) or "")
            for key in ("raw_text", "text", "drug", "dose", "route", "fluid", "frequency", "duration", "instruction")
        )
    )
    candidates = item.get("drug_candidates") or []
    short_forms = item.get("short_forms") or []
    return bool(text and (candidates or short_forms or re.search(r"\b(?:mg|mcg|gm?|ml|iv|i/v|inj|tab|bd|tds|od|stat|ns|dns|rl)\b", text, re.I)))


def _best_drug(item: Dict[str, Any]) -> str:
    if item.get("drug"):
        return normalize_text(str(item["drug"]))
    candidates = item.get("drug_candidates") or []
    if candidates:
        return str(candidates[0].get("normalized") or "")
    return ""


def _split_label_value(text: str) -> Tuple[str, str]:
    parts = re.split(r"[:=-]", text, maxsplit=1)
    if len(parts) == 2:
        return normalize_text(parts[0]), normalize_text(parts[1])
    return "", text


def _has_order_signal(text: str) -> bool:
    lowered = text.lower()
    return any(token in lowered for token in ("inj", "injection", "medicine", "medicines", "staff nurse", "chemotherapy", "tab", "iv", "i/v", "mg", "ml", "bp", "spo", "pulse", "docet", "trastu", "palo", "pan"))


def _has_chart_signal(text: str) -> bool:
    lowered = text.lower()
    return (
        "staff nurse" in lowered
        and ("medicine" in lowered or "medicines" in lowered or "injection" in lowered or "injections" in lowered)
    )


def _priority(role: str, source: Dict[str, Any]) -> int:
    text = str(source.get("source_text") or "").lower()
    if role == "orders" or _has_order_signal(text):
        return 0
    if role == "vitals":
        return 1
    if source.get("source_type") in {"table", "page_region"}:
        return 2
    return 3


def _fallback_zones() -> Iterable[Tuple[str, Tuple[float, float, float, float]]]:
    return [
        ("vitals", (0.00, 0.20, 0.24, 0.92)),
        ("orders", (0.20, 0.20, 0.98, 0.92)),
        ("orders", (0.20, 0.24, 0.98, 0.52)),
        ("orders", (0.20, 0.48, 0.98, 0.76)),
        ("orders", (0.20, 0.68, 0.98, 0.95)),
    ]


def _chart_zones() -> Iterable[Tuple[str, Tuple[float, float, float, float]]]:
    return [
        ("orders", (0.02, 0.12, 0.98, 0.95)),
        ("orders", (0.18, 0.16, 0.98, 0.88)),
        ("orders", (0.00, 0.24, 0.42, 0.96)),
    ]


def _valid_bbox(value: Any, width: float, height: float) -> List[float]:
    if not isinstance(value, list) or len(value) != 4:
        return []
    try:
        x1, y1, x2, y2 = [float(v) for v in value]
    except Exception:
        return []
    if x2 <= x1 or y2 <= y1:
        return []
    return [max(0.0, min(width, x1)), max(0.0, min(height, y1)), max(0.0, min(width, x2)), max(0.0, min(height, y2))]


def _expand_box(bbox: List[float], width: float, height: float, *, pad_x: float, pad_y: float) -> List[float]:
    return [
        round(max(0.0, bbox[0] - pad_x), 2),
        round(max(0.0, bbox[1] - pad_y), 2),
        round(min(width, bbox[2] + pad_x), 2),
        round(min(height, bbox[3] + pad_y), 2),
    ]


def _relative_to_page_bbox(rel_box: Tuple[float, float, float, float], width: float, height: float) -> List[float]:
    x1, y1, x2, y2 = rel_box
    return [round(x1 * width, 2), round(y1 * height, 2), round(x2 * width, 2), round(y2 * height, 2)]


def _render_crop(page, bbox: List[float]) -> Image.Image:
    pix = page.get_pixmap(clip=fitz.Rect(bbox), dpi=config.HANDWRITING_ORDER_RENDER_DPI, alpha=False)
    return Image.open(BytesIO(pix.tobytes("png"))).convert("RGB")


def _prepare_crop(image: Image.Image) -> Image.Image:
    gray = ImageOps.grayscale(image)
    gray = ImageOps.autocontrast(gray)
    gray = ImageEnhance.Contrast(gray).enhance(2.0)
    gray = gray.filter(ImageFilter.SHARPEN)
    if max(gray.size) < 1200:
        gray = gray.resize((gray.width * 2, gray.height * 2))
    return gray.convert("RGB")


def _is_blankish(image: Image.Image) -> bool:
    small = ImageOps.grayscale(image.resize((max(1, image.width // 10), max(1, image.height // 10))))
    values = list(small.getdata())
    return not values or pstdev(values) < config.PAGE_VISION_MIN_CROP_STDDEV


def _image_data_url(image: Image.Image) -> str:
    bounded = image.convert("RGB")
    max_side = 1400
    if max(bounded.size) > max_side:
        bounded.thumbnail((max_side, max_side))
    buf = BytesIO()
    bounded.save(buf, format="JPEG", quality=84, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
