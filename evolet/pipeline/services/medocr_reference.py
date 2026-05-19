"""Optional MedOCR-Vision reference layer for prompts and normalization."""

from __future__ import annotations

import logging
import json
from functools import lru_cache
from typing import Dict, List
from urllib.parse import urlencode
from urllib.request import urlopen

from . import config

logger = logging.getLogger("pipeline")

FALLBACK_REFERENCE = [
    "doctor_name, clinic_name, patient_name, patient_age, date, medications, signature",
    "medications are commonly rendered as bullet rows with drug, dose, timing, and instruction",
    "prescription OCR labels often wrap output in <s_ocr> ... </s>",
    "medical crop text can contain drug names plus short directions such as before meals, after meals, at bedtime",
]


@lru_cache(maxsize=1)
def get_medocr_reference_context() -> Dict[str, object]:
    if not config.ENABLE_MEDOCR_REFERENCE_LAYER:
        return {"dataset_id": config.MEDOCR_VISION_DATASET_ID, "enabled": False, "examples": []}

    examples = _load_dataset_examples()
    if not examples:
        examples = FALLBACK_REFERENCE[: config.MEDOCR_REFERENCE_MAX_EXAMPLES]

    return {
        "dataset_id": config.MEDOCR_VISION_DATASET_ID,
        "enabled": True,
        "examples": examples[: config.MEDOCR_REFERENCE_MAX_EXAMPLES],
        "purpose": "prompt grounding for prescription-style fields and medication row normalization",
    }


def _load_dataset_examples() -> List[str]:
    examples = _load_dataset_viewer_examples()
    if examples:
        return examples

    try:
        from datasets import load_dataset  # type: ignore
    except Exception:
        return []

    try:
        ds = load_dataset(
            config.MEDOCR_VISION_DATASET_ID,
            split=f"train[:{max(1, config.MEDOCR_REFERENCE_MAX_EXAMPLES * 4)}]",
            streaming=False,
        )
    except Exception as exc:
        logger.debug("MedOCR reference dataset unavailable: %s", exc)
        return []

    examples = []
    for row in ds:
        text = str(row.get("text") or "").strip()
        if "medication" in text.lower() or "doctor_name" in text.lower():
            examples.append(text[:700])
        if len(examples) >= config.MEDOCR_REFERENCE_MAX_EXAMPLES:
            break
    return examples


def _load_dataset_viewer_examples() -> List[str]:
    params = urlencode({
        "dataset": config.MEDOCR_VISION_DATASET_ID,
        "config": "default",
        "split": "train",
        "offset": "0",
        "length": str(max(10, config.MEDOCR_REFERENCE_MAX_EXAMPLES * 4)),
    })
    try:
        with urlopen(f"https://datasets-server.huggingface.co/rows?{params}", timeout=8) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        logger.debug("MedOCR dataset viewer unavailable: %s", exc)
        return []

    examples = []
    for row in payload.get("rows", []):
        data = row.get("row") or {}
        text = str(data.get("text") or "").strip()
        if "medication" in text.lower() or "doctor_name" in text.lower():
            examples.append(text[:700])
        if len(examples) >= config.MEDOCR_REFERENCE_MAX_EXAMPLES:
            break
    return examples
