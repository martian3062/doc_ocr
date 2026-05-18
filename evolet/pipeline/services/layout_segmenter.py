"""Layout-aware artifact extraction and note segmentation."""

from __future__ import annotations

import logging
from collections import defaultdict
from io import BytesIO
from typing import Any, Dict, List, Tuple

import fitz
from PIL import Image

from . import config
from .note_segmenter import (
    clinical_signal_score,
    extract_dates,
    is_low_value_note,
)
from .pdf_extractor import normalize_text, text_hash, word_count
from .ocr_backends import get_got_ocr_backend, get_trocr_backend
from .parsing import parse_document

logger = logging.getLogger("pipeline")


def _classify_block_role(page_height: float, bbox: List[float], text: str) -> str:
    top = bbox[1]
    bottom = bbox[3]
    lowered = text.lower()
    if top <= page_height * 0.12:
        return "header"
    if bottom >= page_height * 0.92:
        return "footer"
    if any(token in lowered for token in ("table", "investigation", "lab", "result")):
        return "table"
    if any(token in lowered for token in ("stamp", "signed", "signature")):
        return "stamp"
    return "body"


def _render_crop(page, bbox: List[float], dpi: int = 220) -> Image.Image:
    rect = fitz.Rect(bbox)
    pix = page.get_pixmap(clip=rect, dpi=dpi, alpha=False)
    return Image.open(BytesIO(pix.tobytes("png"))).convert("RGB")


def extract_document_artifacts(pdf_path: str, page_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Build first-class page artifacts from native layout blocks and OCR crops.

    The advanced reader keeps these artifacts so downstream mention and relation
    extraction can point back to exact page regions, not just a flattened page.
    """
    parser_payload = parse_document(pdf_path)
    artifacts: List[Dict[str, Any]] = list(parser_payload.get("artifacts", []))
    parser_results = parser_payload.get("parsers", [])
    trocr = get_trocr_backend()
    got = get_got_ocr_backend()
    page_row_map = {row["page_num"]: row for row in page_rows}

    doc = fitz.open(pdf_path)
    try:
        for page_index in range(len(doc)):
            page = doc[page_index]
            page_num = page_index + 1
            page_row = page_row_map.get(page_num, {})
            page_width = float(page.rect.width)
            page_height = float(page.rect.height)
            blocks = page.get_text("blocks", sort=True) or []
            page_row["parser_results"] = parser_results

            reading_order = 0
            handwriting_crop_count = 0
            native_blocks = []
            for block in blocks:
                if len(block) < 5 or not isinstance(block[4], str):
                    continue
                text = normalize_text(block[4])
                if not text:
                    continue
                bbox = [round(float(block[0]), 2), round(float(block[1]), 2), round(float(block[2]), 2), round(float(block[3]), 2)]
                native_blocks.append({"bbox": bbox, "text": text})
                reading_order += 1
                role = _classify_block_role(page_height, bbox, text)

                artifact = {
                    "artifact_type": "text_block",
                    "role": role,
                    "backend": "native" if page_row.get("selected_source", "native").startswith("native") else "mixed",
                    "text": text,
                    "normalized_text": text,
                    "confidence": 1.0 if page_row.get("selected_source", "native").startswith("native") else 0.65,
                    "bbox": bbox,
                    "polygon": [],
                    "page_num": page_num,
                    "reading_order": reading_order,
                    "metadata": {
                        "page_width": page_width,
                        "page_height": page_height,
                        "selected_source": page_row.get("selected_source", "native"),
                        "parser_results": parser_results,
                    },
                }

                selected_source = page_row.get("selected_source", "native")
                should_try_handwriting = (
                    config.ENABLE_HANDWRITING_OCR
                    and not selected_source.startswith("native")
                    and len(text) < config.HANDWRITING_MIN_NATIVE_CHARS
                    and handwriting_crop_count < config.HANDWRITING_MAX_CROPS_PER_DOCUMENT
                )
                if should_try_handwriting:
                    try:
                        handwriting_crop_count += 1
                        crop = _render_crop(page, bbox)
                        primary = trocr.recognize(crop)
                        verify = got.recognize(crop) if config.ENABLE_GOT_VERIFICATION else None
                        chosen_text = primary.text or text
                        if verify and verify.text and len(verify.text) > len(chosen_text):
                            chosen_text = verify.text
                        artifact["artifact_type"] = "handwriting"
                        artifact["backend"] = primary.backend or "trocr"
                        artifact["text"] = normalize_text(chosen_text) or text
                        artifact["normalized_text"] = artifact["text"]
                        artifact["confidence"] = max(primary.confidence, verify.confidence if verify else 0.0)
                        artifact["metadata"].update({
                            "verification_backend": verify.backend if verify else "",
                            "verification_text": verify.text if verify else "",
                            "primary_text": primary.text,
                        })
                    except Exception as exc:
                        logger.debug("Handwriting crop OCR skipped p%s: %s", page_num, exc)

                artifacts.append(artifact)

            for image_idx, image_info in enumerate(page.get_images(full=True), start=1):
                xref = image_info[0]
                artifacts.append({
                    "artifact_type": "image",
                    "role": "embedded_figure",
                    "backend": "embedded_image",
                    "text": "",
                    "normalized_text": "",
                    "confidence": 0.0,
                    "bbox": [],
                    "polygon": [],
                    "page_num": page_num,
                    "reading_order": reading_order + image_idx,
                    "metadata": {"xref": xref},
                })

            page_row["layout_blocks"] = native_blocks
            page_row["page_width"] = page_width
            page_row["page_height"] = page_height
    finally:
        doc.close()

    return artifacts


def segment_layout_aware_notes(
    page_rows: List[Dict[str, Any]],
    artifacts: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Group body-like artifacts by page and vertical spacing to form notes.

    This replaces the old regex-only splitter with a layout-aware pass that
    respects block order, page zones, and region groupings.
    """
    artifact_groups: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    for artifact in artifacts:
        if artifact["artifact_type"] not in {"text_block", "handwriting"}:
            continue
        if artifact.get("role") in {"header", "footer"}:
            continue
        artifact_groups[artifact["page_num"]].append(artifact)

    notes: List[Dict[str, Any]] = []
    seen_hashes = set()
    for page_num, page_artifacts in sorted(artifact_groups.items()):
        sorted_artifacts = sorted(page_artifacts, key=lambda item: (item["bbox"][1] if item["bbox"] else 0.0, item["reading_order"]))
        current_group: List[Dict[str, Any]] = []
        previous_bottom = None
        note_ix = 0

        for artifact in sorted_artifacts:
            top = artifact["bbox"][1] if artifact["bbox"] else 0.0
            bottom = artifact["bbox"][3] if artifact["bbox"] else top
            gap = top - previous_bottom if previous_bottom is not None else 0.0
            if current_group and gap > config.LAYOUT_NOTE_VERTICAL_GAP:
                note_ix += 1
                note = _flush_note_group(page_num, note_ix, current_group)
                if note and note["hash"] not in seen_hashes:
                    notes.append(note)
                    seen_hashes.add(note["hash"])
                current_group = []

            current_group.append(artifact)
            previous_bottom = bottom

        if current_group:
            note_ix += 1
            note = _flush_note_group(page_num, note_ix, current_group)
            if note and note["hash"] not in seen_hashes:
                notes.append(note)
                seen_hashes.add(note["hash"])

    return notes


def _flush_note_group(page_num: int, note_ix: int, artifacts: List[Dict[str, Any]]) -> Dict[str, Any] | None:
    text = normalize_text("\n".join(artifact.get("text", "") for artifact in artifacts if artifact.get("text")))
    if not text or word_count(text) < config.NOTE_MIN_WORDS:
        return None
    layout_roles = sorted({artifact.get("role", "") for artifact in artifacts if artifact.get("role")})
    artifact_ids = [str(artifact.get("id", "")) for artifact in artifacts if artifact.get("id")]
    return {
        "page_num": page_num,
        "note_ix": note_ix,
        "note_id": f"p{page_num:04d}_n{note_ix:03d}",
        "text": text,
        "hash": text_hash(text),
        "dates": extract_dates(text),
        "signal": clinical_signal_score(text),
        "low_value": is_low_value_note(text),
        "layout_hint": {
            "roles": layout_roles,
            "artifact_count": len(artifacts),
        },
        "artifact_ids": artifact_ids,
    }
