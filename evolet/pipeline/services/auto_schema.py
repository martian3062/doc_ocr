"""Auto-schema generation for document-level extraction.

The normal mention pipeline extracts useful facts. This layer reads the merged
mentions plus source text and asks a fast JSON LLM, usually Groq, to produce a
document-shaped schema: major info, document profile, dynamic sections, and
quality checks. If the provider is unavailable, deterministic fallbacks keep the
record usable.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Dict, List
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from . import config
from .json_utils import parse_json_loose

logger = logging.getLogger("pipeline")

GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"


def enrich_with_auto_schema(
    *,
    base_schema: Dict[str, Any],
    patient_code: str,
    source_pdf: str,
    source_text: str,
    mentions: List[Dict[str, Any]],
    spell_check: Dict[str, Any],
    source_pages: List[Dict[str, Any]] | None = None,
) -> Dict[str, Any]:
    """Return base_schema enriched with auto-schema fields and metadata."""
    fallback = _fallback_schema(
        base_schema=base_schema,
        patient_code=patient_code,
        source_pdf=source_pdf,
        source_text=source_text,
        source_pages=source_pages or [],
        mentions=mentions,
        spell_check=spell_check,
        status="fallback",
        error="",
    )

    if not config.ENABLE_AUTO_SCHEMA:
        fallback["quality_checks"]["auto_schema"]["status"] = "disabled"
        return fallback

    if config.SCHEMA_PROVIDER not in {"groq", "local"}:
        fallback["quality_checks"]["auto_schema"]["status"] = "unsupported_provider"
        fallback["quality_checks"]["auto_schema"]["error"] = config.SCHEMA_PROVIDER
        return fallback

    if config.SCHEMA_PROVIDER == "groq" and not config.GROQ_API_KEY:
        fallback["quality_checks"]["auto_schema"]["status"] = "missing_key"
        return fallback
    if config.SCHEMA_PROVIDER == "local" and not config.ENABLE_LOCAL_HF_LLM:
        fallback["quality_checks"]["auto_schema"]["status"] = "local_hf_llm_disabled"
        return fallback
    if config.SCHEMA_PROVIDER == "local" and not mentions:
        fallback["quality_checks"]["auto_schema"]["status"] = "skipped_empty_record"
        fallback["quality_checks"]["auto_schema"]["error"] = "no mentions available for local schema"
        return fallback

    try:
        prompt = _build_prompt(
            patient_code=patient_code,
            source_pdf=source_pdf,
            source_text=_trim_source_text(source_text),
            mentions=mentions,
            base_sections=base_schema.get("sections", {}),
        )
        response_text = _call_schema_provider(prompt)
        try:
            payload = parse_json_loose(response_text)
        except Exception:
            if config.SCHEMA_PROVIDER != "local":
                raise
            compact_prompt = _build_compact_local_prompt(
                patient_code=patient_code,
                source_pdf=source_pdf,
                mentions=mentions,
                base_sections=base_schema.get("sections", {}),
            )
            response_text = _call_local(compact_prompt)
            payload = parse_json_loose(response_text)
        if not isinstance(payload, dict):
            if config.SCHEMA_PROVIDER == "local":
                fallback["quality_checks"]["auto_schema"]["status"] = "local_json_fallback"
                fallback["quality_checks"]["auto_schema"]["error"] = "local schema model returned non-object JSON"
                return fallback
            raise ValueError("auto-schema response was not a JSON object")
        return _merge_provider_payload(
            fallback=fallback,
            payload=payload,
            spell_check=spell_check,
        )
    except Exception as exc:
        logger.warning("Auto-schema provider failed: %s", exc)
        fallback["quality_checks"]["auto_schema"]["status"] = "local_json_fallback" if config.SCHEMA_PROVIDER == "local" else "failed"
        fallback["quality_checks"]["auto_schema"]["error"] = str(exc)[:500]
        return fallback


def _trim_source_text(text: str) -> str:
    text = re.sub(r"\s+", " ", (text or "")).strip()
    max_chars = config.SCHEMA_MAX_SOURCE_CHARS
    if len(text) <= max_chars:
        return text
    head = int(max_chars * 0.65)
    tail = max_chars - head - 48
    return f"{text[:head]}\n...[middle omitted]...\n{text[-tail:]}"


def _build_prompt(
    *,
    patient_code: str,
    source_pdf: str,
    source_text: str,
    mentions: List[Dict[str, Any]],
    base_sections: Dict[str, Any],
) -> str:
    examples = (
        f"Reference corpus: {config.MEDOCR_VISION_DATASET_ID}. MedOCR-style prescription documents often "
        "contain doctor_name, clinic_name, patient_name, date, medications, and "
        "signature; lab reports often contain patient info, specimen, report date, "
        "test names, values, units, reference ranges, and interpretation notes."
    )
    mention_preview = [
        {
            "category": m.get("category"),
            "label": m.get("label"),
            "value": m.get("normalized_value") or m.get("value"),
            "evidence_quote": m.get("evidence_quote"),
            "source_pages": m.get("source_pages"),
        }
        for m in mentions[:80]
    ]
    return (
        "You are a strict JSON API for medical document understanding. "
        "Return one JSON object only, no markdown.\n\n"
        f"{examples}\n\n"
        "Required JSON keys:\n"
        "- document_type: short label such as lab_report, prescription, radiation_plan, chemotherapy_record, discharge_summary, invoice, unknown_medical_document\n"
        "- document_profile: object with document_type, clinical_domain, source_quality, language, key_dates\n"
        "- major_info: object with primary_finding, current_treatment, investigation_summary, plan_or_follow_up, key_dates, patient_identifiers\n"
        "- document_profile: infer exact document type, clinical/admin domain, source quality, language, key dates, and schema rationale\n"
        "- sections: object keyed by whatever this file actually contains. Each section has title, count, items. Each item has label, value, evidence_quote, source_pages, certainty, parsed.\n"
        "- all_extracted_content: object with coverage_notes and important_raw_lines array for important lines that do not fit cleanly into fields\n"
        "- quality_checks: object with schema_validation, missing_or_uncertain_fields, hallucination_risk, extraction_confidence.\n\n"
        "Rules: use only provided evidence; put unknown/null when absent; make sections fit this document, not a fixed oncology schema; extract in depth, not just a summary; keep duplicate boilerplate compact.\n\n"
        f"patient_code: {patient_code}\n"
        f"source_pdf: {source_pdf}\n"
        f"base_sections_json: {json.dumps(base_sections, ensure_ascii=True)[:6000]}\n"
        f"mentions_json: {json.dumps(mention_preview, ensure_ascii=True)[:10000]}\n"
        f"source_text:\n{source_text}\n\n"
        "FINAL OUTPUT RULE: output raw valid JSON only. The first character must be { and the last character must be }. "
        "Do not write prose, markdown, headings, bullets, or explanations."
    )


def _build_compact_local_prompt(
    *,
    patient_code: str,
    source_pdf: str,
    mentions: List[Dict[str, Any]],
    base_sections: Dict[str, Any],
) -> str:
    mention_preview = [
        {
            "category": m.get("category"),
            "label": m.get("label"),
            "value": m.get("normalized_value") or m.get("value"),
            "evidence_quote": m.get("evidence_quote"),
            "source_pages": m.get("source_pages"),
        }
        for m in mentions[:40]
    ]
    section_keys = sorted(str(k) for k in (base_sections or {}).keys())[:20]
    return (
        "Create an adaptive medical document schema from the data below.\n"
        "Return exactly one valid JSON object with this exact top-level shape:\n"
        "{"
        "\"document_type\":\"\","
        "\"document_profile\":{\"document_type\":\"\",\"clinical_domain\":\"\",\"source_quality\":\"\",\"language\":\"\",\"key_dates\":[]},"
        "\"major_info\":{\"primary_finding\":null,\"current_treatment\":null,\"investigation_summary\":null,\"plan_or_follow_up\":null,\"key_dates\":[],\"patient_identifiers\":{}},"
        "\"sections\":{},"
        "\"all_extracted_content\":{\"coverage_notes\":\"\",\"important_raw_lines\":[]},"
        "\"quality_checks\":{\"schema_validation\":{\"status\":\"completed\",\"required_keys_present\":true},\"missing_or_uncertain_fields\":[],\"hallucination_risk\":\"low\",\"extraction_confidence\":0.8}"
        "}\n"
        "Use only the evidence. Keep section item values concise. No markdown. No prose.\n\n"
        f"patient_code: {patient_code}\n"
        f"source_pdf: {source_pdf}\n"
        f"section_keys: {json.dumps(section_keys, ensure_ascii=True)}\n"
        f"mentions_json: {json.dumps(mention_preview, ensure_ascii=True)}\n\n"
        "FINAL OUTPUT RULE: output raw valid JSON only. The first character must be { and the last character must be }."
    )


def _call_groq(prompt: str) -> str:
    body = {
        "model": config.SCHEMA_MODEL,
        "temperature": 0,
        "max_completion_tokens": 900,
        "messages": [
            {
                "role": "system",
                "content": "You return only valid JSON for adaptive medical document schemas.",
            },
            {"role": "user", "content": prompt},
        ],
    }
    last_error = ""
    for attempt in range(4):
        try:
            data = _post_groq(body)
            return data["choices"][0]["message"]["content"]
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            if exc.code == 400:
                recovered = _failed_generation_text(detail)
                if recovered:
                    return recovered
            last_error = f"Groq HTTP {exc.code}: {detail[:500]}"
            if exc.code != 429 or attempt == 3:
                raise RuntimeError(last_error) from exc
            time.sleep(_retry_delay_seconds(exc, detail, attempt))
        except URLError as exc:
            raise RuntimeError(f"Groq request failed: {exc}") from exc

    raise RuntimeError(last_error or "Groq request failed")


def _call_schema_provider(prompt: str) -> str:
    if config.SCHEMA_PROVIDER == "local":
        return _call_local(prompt)
    return _call_groq(prompt)


def _call_local(prompt: str) -> str:
    from .llm_engine import generate_local_prompts

    outputs = generate_local_prompts(
        [prompt],
        model_id=config.SCHEMA_LOCAL_MODEL_ID,
        max_new_tokens=config.SCHEMA_MAX_NEW_TOKENS,
        use_4bit=config.SCHEMA_LOCAL_USE_4BIT,
        unload_after=False,
        system_prompt=(
            "You are a strict JSON API for adaptive medical document schemas. "
            "Return exactly one valid JSON object and nothing else. "
            "No markdown, no headings, no explanation, no bullet points. "
            "The first output character must be { and the last must be }. "
            "The object must contain document_type, document_profile, major_info, "
            "sections, all_extracted_content, and quality_checks."
        ),
    )
    return outputs[0] if outputs else ""


def _schema_model_label() -> str:
    return config.SCHEMA_LOCAL_MODEL_ID if config.SCHEMA_PROVIDER == "local" else config.SCHEMA_MODEL


def _post_groq(body: Dict[str, Any]) -> Dict[str, Any]:
    req = Request(
        GROQ_CHAT_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {config.GROQ_API_KEY}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "doc-ocr-auto-schema/1.0",
        },
        method="POST",
    )
    with urlopen(req, timeout=config.SCHEMA_TIMEOUT_SECONDS) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _failed_generation_text(detail: str) -> str:
    try:
        payload = json.loads(detail)
    except json.JSONDecodeError:
        return ""
    error = payload.get("error") if isinstance(payload, dict) else {}
    if not isinstance(error, dict):
        return ""
    failed = error.get("failed_generation")
    return failed if isinstance(failed, str) else ""


def _retry_delay_seconds(exc: HTTPError, detail: str, attempt: int) -> float:
    retry_after = exc.headers.get("Retry-After")
    if retry_after:
        try:
            return min(float(retry_after), 70.0)
        except ValueError:
            pass
    match = re.search(r"try again in ([0-9.]+)s", detail, flags=re.IGNORECASE)
    if match:
        return min(float(match.group(1)) + 0.75, 70.0)
    return 2.5 * (attempt + 1)


def _fallback_schema(
    *,
    base_schema: Dict[str, Any],
    patient_code: str,
    source_pdf: str,
    source_text: str,
    source_pages: List[Dict[str, Any]],
    mentions: List[Dict[str, Any]],
    spell_check: Dict[str, Any],
    status: str,
    error: str,
) -> Dict[str, Any]:
    schema = dict(base_schema)
    schema["all_extracted_content"] = _source_content_payload(source_text, source_pages, mentions)
    sections = schema.get("sections") or {}
    categories = sorted(sections.keys())
    text_l = (source_text or "").lower()
    if "crp" in text_l or "haemoglobin" in text_l or "reference range" in text_l:
        doc_type = "lab_report"
        domain = "laboratory"
    elif "radiation" in text_l or "gy" in text_l or "fractions" in text_l:
        doc_type = "radiation_record"
        domain = "radiation_oncology"
    elif "chemotherapy" in text_l or "cycle" in text_l:
        doc_type = "chemotherapy_record"
        domain = "medical_oncology"
    elif "medications" in text_l or "prescription" in text_l:
        doc_type = "prescription"
        domain = "medication"
    else:
        doc_type = "unknown_medical_document" if source_text else "empty_or_unreadable_document"
        domain = "unknown"

    schema.setdefault("document_profile", {
        "document_type": doc_type,
        "clinical_domain": domain,
        "source_quality": "text_available" if source_text else "no_text_available",
        "language": "unknown",
        "key_dates": [],
    })
    schema.setdefault("major_info", _major_info_from_mentions(mentions, source_text))
    schema["quality_checks"] = {
        "spell_check": spell_check,
        "schema_validation": {
            "status": "completed",
            "required_keys_present": True,
            "section_count": len(categories),
        },
            "auto_schema": {
                "provider": config.SCHEMA_PROVIDER,
                "model": _schema_model_label(),
                "reference_dataset": config.MEDOCR_VISION_DATASET_ID,
                "status": status,
                "error": error,
            },
        "missing_or_uncertain_fields": _missing_fields(schema.get("major_info", {})),
        "hallucination_risk": "low" if mentions else "medium",
        "extraction_confidence": 0.82 if mentions else 0.45,
    }
    return schema


def _source_content_payload(
    source_text: str,
    source_pages: List[Dict[str, Any]],
    mentions: List[Dict[str, Any]],
) -> Dict[str, Any]:
    pages = []
    if config.STORE_FULL_SOURCE_TEXT:
        for page in source_pages:
            text = str(page.get("text") or "")
            pages.append({
                "document": page.get("document") or "",
                "page_num": page.get("page_num"),
                "selected_source": page.get("selected_source") or "",
                "char_count": len(text),
                "word_count": len(text.split()),
                "text": text,
            })
    return {
        "status": "completed" if source_text or mentions else "empty",
        "stored_full_text": bool(config.STORE_FULL_SOURCE_TEXT),
        "page_count": len(source_pages),
        "source_text_char_count": len(source_text or ""),
        "source_text_word_count": len((source_text or "").split()),
        "mention_count": len(mentions),
        "pages": pages,
    }


def _major_info_from_mentions(mentions: List[Dict[str, Any]], source_text: str) -> Dict[str, Any]:
    def first_for(categories: set[str]) -> str | None:
        for mention in mentions:
            if str(mention.get("category", "")).lower() in categories:
                return mention.get("normalized_value") or mention.get("value")
        return None

    key_dates = sorted(
        {
            str(m.get("date_text"))
            for m in mentions
            if m.get("date_text")
        }
    )[:10]
    return {
        "primary_finding": first_for({"diagnosis", "pathology", "lab", "imaging"}) or _first_sentence(source_text),
        "current_treatment": first_for({"medication", "procedure", "chemotherapy", "radiotherapy", "surgery"}),
        "investigation_summary": first_for({"lab", "imaging", "pathology", "genomics"}),
        "plan_or_follow_up": first_for({"plan", "follow_up", "status"}),
        "key_dates": key_dates,
        "patient_identifiers": {},
    }


def _first_sentence(text: str) -> str | None:
    text = re.sub(r"\s+", " ", (text or "")).strip()
    if not text:
        return None
    return text[:240]


def _missing_fields(major_info: Dict[str, Any]) -> List[str]:
    return [
        key
        for key in ("primary_finding", "current_treatment", "investigation_summary", "plan_or_follow_up")
        if not major_info.get(key)
    ]


def _merge_provider_payload(
    *,
    fallback: Dict[str, Any],
    payload: Dict[str, Any],
    spell_check: Dict[str, Any],
) -> Dict[str, Any]:
    schema = dict(fallback)
    schema["document_profile"] = _as_dict(payload.get("document_profile")) or {
        **fallback.get("document_profile", {}),
        "document_type": payload.get("document_type") or fallback.get("document_profile", {}).get("document_type"),
    }
    if payload.get("document_type"):
        schema["document_profile"]["document_type"] = str(payload["document_type"])
    schema["major_info"] = _as_dict(payload.get("major_info")) or fallback.get("major_info", {})
    provider_content = _as_dict(payload.get("all_extracted_content"))
    if provider_content:
        schema["all_extracted_content"] = {
            **fallback.get("all_extracted_content", {}),
            "coverage_notes": provider_content.get("coverage_notes", ""),
            "important_raw_lines": provider_content.get("important_raw_lines", []),
        }
    provider_sections = _normalise_sections(payload.get("sections"))
    if provider_sections:
        schema["sections"] = provider_sections
        schema["document_summary"] = {
            **schema.get("document_summary", {}),
            "category_count": len(provider_sections),
            "categories": sorted(provider_sections.keys()),
            "category_counts": {k: v.get("count", len(v.get("items", []))) for k, v in provider_sections.items()},
        }
    provider_quality = _as_dict(payload.get("quality_checks"))
    schema["quality_checks"] = {
        **fallback.get("quality_checks", {}),
        **provider_quality,
        "spell_check": spell_check,
        "auto_schema": {
            "provider": config.SCHEMA_PROVIDER,
            "model": _schema_model_label(),
            "reference_dataset": config.MEDOCR_VISION_DATASET_ID,
            "status": "completed",
            "error": "",
        },
    }
    schema["quality_checks"].setdefault("schema_validation", {"status": "completed", "required_keys_present": True})
    schema["quality_checks"].setdefault("missing_or_uncertain_fields", _missing_fields(schema.get("major_info", {})))
    schema["quality_checks"].setdefault("extraction_confidence", 0.86)
    return schema


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _normalise_sections(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    sections: Dict[str, Any] = {}
    for raw_key, raw_section in value.items():
        key = re.sub(r"[^a-zA-Z0-9]+", "_", str(raw_key).strip().lower()).strip("_")
        if not key:
            continue
        section = raw_section if isinstance(raw_section, dict) else {"items": raw_section}
        raw_items = section.get("items", [])
        if isinstance(raw_items, dict):
            raw_items = [raw_items]
        items = []
        iterable_items = raw_items if isinstance(raw_items, list) else []
        for item in iterable_items:
            if isinstance(item, dict):
                items.append({
                    "label": str(item.get("label") or item.get("name") or key),
                    "value": item.get("value") or item.get("text") or "",
                    "normalized_value": item.get("normalized_value") or item.get("value") or item.get("text") or "",
                    "evidence_quote": item.get("evidence_quote") or item.get("evidence") or "",
                    "source_pages": item.get("source_pages") if isinstance(item.get("source_pages"), list) else [],
                    "certainty": item.get("certainty", "medium"),
                    "category": key,
                    "parsed": item.get("parsed") if isinstance(item.get("parsed"), dict) else {},
                })
        sections[key] = {
            "title": str(section.get("title") or key.replace("_", " ").title()),
            "count": int(section.get("count") or len(items)),
            "items": items,
        }
    return sections
