"""Hybrid schema cleaner for final patient mentions.

This layer is deliberately post-extraction. It enriches and normalizes mentions
after the deterministic merge/SymSpell pass, while preserving original evidence.
Optional clinical NLP/HF backends are loaded lazily and failures are captured in
the report instead of blocking the whole OCR run.
"""

from __future__ import annotations

import logging
import re
import threading
from functools import lru_cache
from typing import Any, Dict, Iterable, List, Tuple

from rapidfuzz import fuzz

from . import config
from .pdf_extractor import normalize_text

logger = logging.getLogger("pipeline")
_PIPELINE_LOCK = threading.Lock()


FREQUENCY_RE = re.compile(
    r"\b(?P<frequency>(?:OD|BD|TDS|QID|HS|SOS|STAT|PRN|once daily|twice daily|daily|weekly|nightly|"
    r"\d+\s*(?:times|time)\s*(?:a|per)?\s*day))\b",
    re.I,
)
STRENGTH_RE = re.compile(r"\b(?P<strength>\d+(?:\.\d+)?\s*(?:mg|mcg|g|gm|ml|iu|units?|%)\b)", re.I)
DURATION_RE = re.compile(r"\b(?P<duration>\d+\s*(?:days?|weeks?|months?|cycles?)\b)", re.I)
ROUTE_RE = re.compile(r"\b(?P<route>IV|IM|SC|PO|oral|intravenous|subcutaneous|intramuscular|topical)\b", re.I)

HF_LABEL_MAP = {
    "drug": "drug_name",
    "drug_name": "drug_name",
    "medication": "drug_name",
    "chemical": "drug_name",
    "strength": "strength",
    "dosage": "dosage",
    "dose": "dosage",
    "frequency": "frequency",
    "duration": "duration",
    "form": "form",
    "route": "route",
    "disease": "diagnosis",
    "disorder": "diagnosis",
    "diagnosis": "diagnosis",
    "procedure": "procedure",
    "treatment": "treatment",
}


def clean_schema_mentions(mentions: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Return enriched mentions plus a compact backend report."""
    if not mentions:
        return mentions, {"enabled": bool(config.ENABLE_HYBRID_SCHEMA_CLEANER), "backends": {}}

    enabled = bool(config.ENABLE_HYBRID_SCHEMA_CLEANER)
    backends = sorted(config.SCHEMA_CLEANER_BACKENDS)
    report: Dict[str, Any] = {
        "enabled": enabled,
        "requested_backends": backends,
        "backends": {},
        "mentions_seen": len(mentions),
        "mentions_enriched": 0,
        "hf_mentions_scanned": 0,
    }
    if not enabled:
        return mentions, report

    cleaned = [_base_clean_mention(item) for item in mentions]
    _run_rule_layers(cleaned, report)
    _run_optional_spacy_layers(cleaned, report)
    _run_hf_layers(cleaned, report)

    report["mentions_enriched"] = sum(
        1 for item in cleaned if (item.get("attributes") or {}).get("schema_cleaner")
    )
    return cleaned, report


def _base_clean_mention(mention: Dict[str, Any]) -> Dict[str, Any]:
    item = dict(mention)
    item["value"] = normalize_text(item.get("value") or item.get("normalized_value") or "")
    item["normalized_value"] = normalize_text(item.get("normalized_value") or item.get("value") or "")
    attrs = item.get("attributes") if isinstance(item.get("attributes"), dict) else {}
    item["attributes"] = dict(attrs)
    return item


def _run_rule_layers(mentions: List[Dict[str, Any]], report: Dict[str, Any]) -> None:
    rule_hits = 0
    fuzzy_hits = 0
    seen_values: List[str] = []
    for mention in mentions:
        text = _mention_text(mention)
        attrs = mention.setdefault("attributes", {})
        medicine_parts = _parse_medicine_parts(text)
        if medicine_parts:
            attrs.update({k: v for k, v in medicine_parts.items() if v and not attrs.get(k)})
            _mark(attrs, "medicine_regex")
            rule_hits += 1

        compact = _compact(text)
        if compact:
            match = next((value for value in seen_values if fuzz.token_set_ratio(compact, value) >= 92), "")
            if match:
                attrs["similar_to_existing_schema_value"] = match
                _mark(attrs, "rapidfuzz")
                fuzzy_hits += 1
            else:
                seen_values.append(compact)

    report["backends"]["medicine_regex"] = {"status": "completed", "hits": rule_hits}
    report["backends"]["rapidfuzz"] = {"status": "completed", "hits": fuzzy_hits}
    if "symspell" in config.SCHEMA_CLEANER_BACKENDS:
        report["backends"]["symspell"] = {"status": "completed", "mode": "already_applied_before_hybrid_cleaner"}


def _run_optional_spacy_layers(mentions: List[Dict[str, Any]], report: Dict[str, Any]) -> None:
    if "medspacy" in config.SCHEMA_CLEANER_BACKENDS:
        _run_medspacy(mentions, report)
    if "scispacy" in config.SCHEMA_CLEANER_BACKENDS:
        _run_scispacy(mentions, report)


def _run_medspacy(mentions: List[Dict[str, Any]], report: Dict[str, Any]) -> None:
    try:
        import medspacy  # type: ignore

        nlp = medspacy.load()
        hits = 0
        for mention in mentions[: config.SCHEMA_CLEANER_HF_MAX_MENTIONS]:
            doc = nlp(_mention_text(mention)[:500])
            sections = [getattr(ent._, "section_category", "") for ent in getattr(doc, "ents", [])]
            if sections:
                attrs = mention.setdefault("attributes", {})
                attrs["medspacy_sections"] = sorted({s for s in sections if s})
                _mark(attrs, "medspacy")
                hits += 1
        report["backends"]["medspacy"] = {"status": "completed", "hits": hits}
    except Exception as exc:
        report["backends"]["medspacy"] = {"status": "unavailable", "error": str(exc)[:160]}


def _run_scispacy(mentions: List[Dict[str, Any]], report: Dict[str, Any]) -> None:
    try:
        import spacy  # type: ignore

        nlp = spacy.load("en_core_sci_sm")
        hits = 0
        for mention in mentions[: config.SCHEMA_CLEANER_HF_MAX_MENTIONS]:
            doc = nlp(_mention_text(mention)[:500])
            ents = [ent.text for ent in doc.ents[:8]]
            if ents:
                attrs = mention.setdefault("attributes", {})
                attrs["scispacy_entities"] = ents
                _mark(attrs, "scispacy")
                hits += 1
        report["backends"]["scispacy"] = {"status": "completed", "hits": hits}
    except Exception as exc:
        report["backends"]["scispacy"] = {"status": "unavailable", "error": str(exc)[:160]}


def _run_hf_layers(mentions: List[Dict[str, Any]], report: Dict[str, Any]) -> None:
    for backend in ("posos", "d4data", "openmed"):
        if backend not in config.SCHEMA_CLEANER_BACKENDS:
            continue
        model_id = config.SCHEMA_CLEANER_HF_MODELS.get(backend, "")
        _run_hf_backend(backend, model_id, mentions, report)


def _run_hf_backend(backend: str, model_id: str, mentions: List[Dict[str, Any]], report: Dict[str, Any]) -> None:
    try:
        with _PIPELINE_LOCK:
            pipe = _hf_pipeline(model_id)
        hits = 0
        scanned = 0
        for mention in _candidate_mentions(mentions):
            scanned += 1
            text = _mention_text(mention)[:500]
            if not text:
                continue
            entities = pipe(text)
            mapped = _map_hf_entities(entities)
            if mapped:
                attrs = mention.setdefault("attributes", {})
                hf_payload = attrs.setdefault("hf_schema_cleaner", {})
                hf_payload[backend] = mapped
                for key, values in mapped.items():
                    if key in {"drug_name", "strength", "frequency", "duration", "dosage", "form", "route"}:
                        attrs.setdefault(key, values[0])
                _mark(attrs, backend)
                hits += 1
        report["hf_mentions_scanned"] = max(int(report.get("hf_mentions_scanned") or 0), scanned)
        report["backends"][backend] = {"status": "completed", "model": model_id, "hits": hits, "scanned": scanned}
    except Exception as exc:
        report["backends"][backend] = {"status": "unavailable", "model": model_id, "error": str(exc)[:180]}
        logger.warning("Hybrid schema cleaner backend %s unavailable: %s", backend, exc)


@lru_cache(maxsize=4)
def _hf_pipeline(model_id: str):
    from transformers import pipeline  # type: ignore

    return pipeline(
        "token-classification",
        model=model_id,
        aggregation_strategy="simple",
        device=-1,
    )


def _candidate_mentions(mentions: List[Dict[str, Any]]) -> Iterable[Dict[str, Any]]:
    priority = sorted(
        mentions,
        key=lambda item: 0 if str(item.get("category", "")).lower() in {"medication", "diagnosis", "procedure"} else 1,
    )
    return priority[: config.SCHEMA_CLEANER_HF_MAX_MENTIONS]


def _map_hf_entities(entities: Any) -> Dict[str, List[str]]:
    mapped: Dict[str, List[str]] = {}
    if not isinstance(entities, list):
        return mapped
    for ent in entities:
        if not isinstance(ent, dict):
            continue
        score = float(ent.get("score") or 0)
        if score < config.SCHEMA_CLEANER_HF_MIN_SCORE:
            continue
        raw_label = str(ent.get("entity_group") or ent.get("entity") or "").lower().replace("b-", "").replace("i-", "")
        key = HF_LABEL_MAP.get(raw_label) or HF_LABEL_MAP.get(raw_label.replace("_", " "))
        word = normalize_text(str(ent.get("word") or ""))
        if not key or not word:
            continue
        values = mapped.setdefault(key, [])
        if word not in values:
            values.append(word)
    return mapped


def _parse_medicine_parts(text: str) -> Dict[str, str]:
    parts: Dict[str, str] = {}
    for key, pattern in [
        ("strength", STRENGTH_RE),
        ("frequency", FREQUENCY_RE),
        ("duration", DURATION_RE),
        ("route", ROUTE_RE),
    ]:
        match = pattern.search(text)
        if match:
            parts[key] = normalize_text(match.group(key))
    return parts


def _mention_text(mention: Dict[str, Any]) -> str:
    return normalize_text(" ".join([
        str(mention.get("label") or ""),
        str(mention.get("normalized_value") or mention.get("value") or ""),
        str(mention.get("evidence_quote") or ""),
    ]))


def _compact(text: str) -> str:
    text = normalize_text(text).lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _mark(attrs: Dict[str, Any], backend: str) -> None:
    markers = attrs.setdefault("schema_cleaner", [])
    if backend not in markers:
        markers.append(backend)
