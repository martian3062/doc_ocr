"""Document extraction validation layer.

This module audits the merged patient/document record after regex/LLM extraction.  It is
designed to run safely on every deployment: heuristic validation is always
available, and a MedGemma/Gemma-style model pass is required by default after
the main extraction/schema stage. The record still persists if the model cannot
load, but the validation payload carries a failed/required status so the UI and
exports cannot mistake it for a completed medical audit.
"""

from __future__ import annotations

import json
import logging
import threading
from collections import Counter
from typing import Any, Dict, List

from . import config
from .json_utils import parse_json_loose

logger = logging.getLogger("pipeline")


OPTIONAL_MEDICAL_CATEGORIES = {
    "diagnosis", "medication", "procedure", "imaging", "lab", "pathology", "follow_up",
}

_VALIDATION_PIPELINE_CACHE: dict[tuple[str, str], Any] = {}
_FAILED_VALIDATION_MODELS: set[str] = set()
_VALIDATION_PIPELINE_LOCK = threading.Lock()


def validate_final_record(
    *,
    patient_code: str,
    source_pdf: str,
    mentions: List[Dict[str, Any]],
    grouped_record: Dict[str, Any],
    stats: Dict[str, Any],
    review_flags: List[str],
) -> Dict[str, Any]:
    """Return validation metadata for a final patient record."""
    heuristic = _heuristic_validation(mentions, grouped_record, stats, review_flags)
    payload = {
        "backend": "heuristic",
        "model_id": "",
        "status": "completed",
        "confidence": heuristic["confidence"],
        "flags": heuristic["flags"],
        "missing_categories": heuristic["missing_categories"],
        "checks": heuristic["checks"],
        "model_notes": "",
    }

    if not config.ENABLE_MEDICAL_VALIDATION:
        payload["status"] = "disabled"
        if config.REQUIRE_MEDICAL_VALIDATION:
            payload["status"] = "required_but_disabled"
            payload["flags"] = sorted(set(payload["flags"] + ["medical_validation_disabled"]))
        return payload

    if config.VALIDATION_BACKEND.strip().lower() != "model":
        payload["backend"] = config.VALIDATION_BACKEND.strip().lower() or "heuristic"
        payload["model_notes"] = "Safe medical validation completed with heuristic rules."
        return payload

    if not getattr(config, "ENABLE_TRANSFORMER_VALIDATION", False):
        payload["model_notes"] = "Transformer validation disabled; heuristic validation applied."
        if config.REQUIRE_MEDICAL_VALIDATION:
            payload["status"] = "required_but_transformer_disabled"
            payload["flags"] = sorted(set(payload["flags"] + ["medical_validation_transformer_disabled"]))
        return payload

    model_payload = _model_validation(
        patient_code=patient_code,
        source_pdf=source_pdf,
        mentions=mentions,
        grouped_record=grouped_record,
        heuristic=heuristic,
    )
    if model_payload:
        payload.update(model_payload)
    if config.REQUIRE_MEDICAL_VALIDATION and payload.get("status") != "completed":
        payload["flags"] = sorted(set(payload.get("flags", []) + ["medical_validation_required_not_completed"]))
    return payload


def _heuristic_validation(
    mentions: List[Dict[str, Any]],
    grouped_record: Dict[str, Any],
    stats: Dict[str, Any],
    review_flags: List[str],
) -> Dict[str, Any]:
    flags = list(review_flags or [])
    categories = {str(m.get("category", "")).lower() for m in mentions if m.get("category")}
    category_counts = Counter(str(m.get("category", "")).lower() for m in mentions if m.get("category"))
    missing_categories = sorted(OPTIONAL_MEDICAL_CATEGORIES - categories) if categories.intersection(OPTIONAL_MEDICAL_CATEGORIES) else []
    evidence_count = sum(1 for m in mentions if m.get("evidence_quote") or m.get("evidence_ids") or m.get("evidence_artifact_ids"))
    mentions_after_merge = int(stats.get("mentions_after_merge") or len(mentions) or 0)

    if mentions_after_merge == 0:
        flags.append("no_mentions_after_merge")
    if evidence_count == 0 and mentions_after_merge:
        flags.append("mentions_without_source_evidence")
    if not categories:
        flags.append("no_categories_detected")
    if categories.intersection(OPTIONAL_MEDICAL_CATEGORIES) and "diagnosis" not in categories:
        flags.append("diagnosis_not_detected")
    if len(categories) <= 1 and mentions_after_merge >= 5:
        flags.append("low_category_diversity")

    confidence = 0.88
    confidence -= 0.18 if "no_mentions_after_merge" in flags else 0
    confidence -= 0.12 if "mentions_without_source_evidence" in flags else 0
    confidence -= 0.08 if "diagnosis_not_detected" in flags else 0
    confidence -= min(len(missing_categories), 4) * 0.015
    confidence = round(max(0.25, min(confidence, 0.98)), 3)

    return {
        "confidence": confidence,
        "flags": sorted(set(flags)),
        "missing_categories": missing_categories,
        "checks": {
            "mention_count": mentions_after_merge,
            "evidence_backed_mentions": evidence_count,
            "category_counts": dict(category_counts),
            "grouped_sections": sorted(k for k in grouped_record.keys() if not str(k).startswith("_")),
        },
    }


def _model_validation(
    *,
    patient_code: str,
    source_pdf: str,
    mentions: List[Dict[str, Any]],
    grouped_record: Dict[str, Any],
    heuristic: Dict[str, Any],
) -> Dict[str, Any] | None:
    try:
        from transformers import pipeline
    except Exception as exc:
        logger.warning("Model validation unavailable: %s", exc)
        return {
            "backend": "model",
            "model_id": config.VALIDATION_MODEL_ID,
            "status": "unavailable",
            "model_notes": f"transformers backend unavailable: {exc}",
        }

    prompt = _validation_prompt(patient_code, source_pdf, mentions, grouped_record, heuristic)
    model_ids = [
        model_id for model_id in [
            config.VALIDATION_MODEL_ID,
            getattr(config, "VALIDATION_FALLBACK_MODEL_ID", ""),
            config.FALLBACK_MODEL_ID,
        ]
        if model_id and model_id not in _FAILED_VALIDATION_MODELS
    ]
    errors: list[str] = []

    for model_id in dict.fromkeys(model_ids):
        try:
            generator, task = _build_validation_pipeline(pipeline, model_id)
            output = _run_validation_pipeline(generator, task, prompt)
            parsed = parse_json_loose(output)
            if not isinstance(parsed, dict):
                raise ValueError(f"validator did not return a JSON object: {output[:500]}")
            if not any(key in parsed for key in ("confidence", "flags", "missing_categories", "notes")):
                raise ValueError(f"validator JSON missing required keys: {str(parsed)[:500]}")
            return {
                "backend": "model",
                "model_id": model_id,
                "status": "completed",
                "confidence": float(parsed.get("confidence", heuristic["confidence"])),
                "flags": sorted(set(heuristic["flags"] + list(parsed.get("flags", [])))),
                "missing_categories": parsed.get("missing_categories", heuristic["missing_categories"]),
                "model_notes": str(parsed.get("notes", output[:1000])),
            }
        except Exception as exc:
            _FAILED_VALIDATION_MODELS.add(model_id)
            errors.append(f"{model_id}: {exc}")
            logger.warning("Model validation failed for %s: %s", model_id, exc)

    return {
        "backend": "model",
        "model_id": config.VALIDATION_MODEL_ID,
        "status": "failed",
        "flags": sorted(set(heuristic["flags"] + ["medical_validation_model_failed"])),
        "missing_categories": heuristic["missing_categories"],
        "confidence": heuristic["confidence"],
        "model_notes": " | ".join(errors[-3:]),
    }


def _build_validation_pipeline(pipeline_fn, model_id: str):
    image_cache_key = (model_id, "image-text-to-text")
    text_cache_key = (model_id, "text-generation")
    if image_cache_key in _VALIDATION_PIPELINE_CACHE:
        return _VALIDATION_PIPELINE_CACHE[image_cache_key], "image-text-to-text"
    if text_cache_key in _VALIDATION_PIPELINE_CACHE:
        return _VALIDATION_PIPELINE_CACHE[text_cache_key], "text-generation"

    with _VALIDATION_PIPELINE_LOCK:
        if image_cache_key in _VALIDATION_PIPELINE_CACHE:
            return _VALIDATION_PIPELINE_CACHE[image_cache_key], "image-text-to-text"
        if text_cache_key in _VALIDATION_PIPELINE_CACHE:
            return _VALIDATION_PIPELINE_CACHE[text_cache_key], "text-generation"

        common_kwargs = {
            "model": model_id,
            "token": config.HF_TOKEN,
            "device_map": "auto",
        }
        if config.LOCAL_FILES_ONLY:
            common_kwargs["model_kwargs"] = {"local_files_only": True}
        try:
            generator = pipeline_fn("image-text-to-text", **common_kwargs)
            _VALIDATION_PIPELINE_CACHE[image_cache_key] = generator
            return generator, "image-text-to-text"
        except Exception:
            generator = pipeline_fn("text-generation", **common_kwargs)
            _VALIDATION_PIPELINE_CACHE[text_cache_key] = generator
            return generator, "text-generation"


def _run_validation_pipeline(generator, task: str, prompt: str) -> str:
    if task == "image-text-to-text":
        response = generator(
            [{"role": "user", "content": [{"type": "text", "text": prompt}]}],
            do_sample=False,
            max_new_tokens=config.VALIDATION_MAX_NEW_TOKENS,
        )
        return _extract_generated_text(response)

    response = generator(
        [
            {
                "role": "system",
                "content": (
                    "You are a strict JSON API for document extraction validation. "
                    "Return only one JSON object with keys confidence, flags, "
                    "missing_categories, and notes. No markdown. No prose."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        do_sample=False,
        max_new_tokens=config.VALIDATION_MAX_NEW_TOKENS,
        return_full_text=False,
    )
    return _extract_generated_text(response)


def _extract_generated_text(response: Any) -> str:
    item = response[0] if isinstance(response, list) and response else response
    if isinstance(item, dict):
        value = item.get("generated_text") or item.get("text") or item.get("output_text")
        if isinstance(value, list):
            assistant_parts = []
            for msg in value:
                if isinstance(msg, dict) and str(msg.get("role", "")).lower() == "assistant":
                    content = msg.get("content", "")
                    if isinstance(content, list):
                        assistant_parts.extend(str(part.get("text", "")) for part in content if isinstance(part, dict))
                    else:
                        assistant_parts.append(str(content))
            if assistant_parts:
                return "\n".join(part for part in assistant_parts if part)

            parts = []
            for msg in value:
                if isinstance(msg, dict):
                    content = msg.get("content", "")
                    if isinstance(content, list):
                        parts.extend(str(part.get("text", "")) for part in content if isinstance(part, dict))
                    else:
                        parts.append(str(content))
            return "\n".join(part for part in parts if part)
        if value is not None:
            return str(value)
    return str(item)


def _validation_prompt(
    patient_code: str,
    source_pdf: str,
    mentions: List[Dict[str, Any]],
    grouped_record: Dict[str, Any],
    heuristic: Dict[str, Any],
) -> str:
    compact_mentions = [
        {
            "category": m.get("category"),
            "label": m.get("label"),
            "value": m.get("value"),
            "date_text": m.get("date_text"),
            "evidence_quote": (m.get("evidence_quote") or "")[:240],
        }
        for m in mentions[:80]
    ]
    payload = {
        "patient_code": patient_code,
        "source_pdf": source_pdf,
        "mentions": compact_mentions,
        "document_profile": grouped_record.get("document_profile", {}),
        "major_info": grouped_record.get("major_info", {}),
        "section_names": list((grouped_record.get("sections") or {}).keys())[:80],
        "all_extracted_content": {
            "status": (grouped_record.get("all_extracted_content") or {}).get("status"),
            "page_count": (grouped_record.get("all_extracted_content") or {}).get("page_count"),
            "source_text_word_count": (grouped_record.get("all_extracted_content") or {}).get("source_text_word_count"),
        },
        "grouped_keys": list(grouped_record.keys())[:60],
        "heuristic": heuristic,
    }
    return (
        "You are validating full-text document extraction and adaptive schema generation for a new PDF type. Return exactly one JSON object and no reasoning. "
        "Do not include markdown, thoughts, explanations, or source payload echo. The JSON must contain "
        "confidence (0-1), flags (array), missing_categories (array), and notes. "
        "Check whether the auto-defined schema fits the document type, whether major info and sections are evidence-backed, "
        "whether the extraction appears shallow versus the full text, and whether MedGemma-style medical validation should flag missing or uncertain fields.\n\n"
        f"{json.dumps(payload, ensure_ascii=False)}"
    )
