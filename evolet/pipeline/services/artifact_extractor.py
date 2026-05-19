"""CPU extraction from merged visual/text artifacts.

This is the bridge between OCR/layout collection and model extraction. It pulls
obvious case-sheet fields, vitals, and medication orders directly from the
side-by-side evidence layer so a weak LLM/OCR response cannot drop them.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List

from .medical_short_forms import normalize_order_text
from .pdf_extractor import normalize_text


FIELD_PATTERNS = [
    ("identifier", "uhid", re.compile(r"\b(?:U\.?H\.?I\.?D\.?|UHID)\s*(?:No\.?)?\s*[:\-]?\s*([A-Za-z0-9/-]+)", re.I)),
    ("identifier", "ipd_no", re.compile(r"\b(?:I\.?P\.?D\.?|IPD)\s*(?:No\.?)?\s*[:\-]?\s*([A-Za-z0-9/-]+)", re.I)),
    ("demographics", "patient_name", re.compile(r"\bName\s*[:\-]?\s*([A-Za-z .'-]{3,80})", re.I)),
    ("demographics", "sex", re.compile(r"\bSex\s*[:\-]?\s*([MF]|Male|Female)\b", re.I)),
    ("demographics", "age", re.compile(r"\bAge\s*[:\-]?\s*(\d{1,3})\b", re.I)),
    ("contact", "address", re.compile(r"\bAddress\s*[:\-]?\s*([^\n]{5,160})", re.I)),
    ("admission", "date_of_admission", re.compile(r"\bDate of Admission\s*[:\-]?\s*([0-9./-]{6,12})", re.I)),
    ("discharge", "date_of_discharge", re.compile(r"\bDate of Discharge\s*[:\-]?\s*([0-9./-]{6,12})", re.I)),
    ("provider", "consultant", re.compile(r"\b(?:Name of Consultant|Consultant)\s*[:\-]?\s*([^\n]{3,120})", re.I)),
    ("diagnosis", "disease_diagnosis", re.compile(r"\b(?:DISEASE\s*/\s*DIAGNOSIS|Diagnosis)\s*[:\-]?\s*([^\n]{3,160})", re.I)),
    ("billing", "category", re.compile(r"\bCategory\s*[:\-]?\s*([A-Za-z0-9 /-]{2,60})", re.I)),
]

VITAL_PATTERNS = [
    ("temperature", re.compile(r"\b(?:Temp|T)\s*[:\-]?\s*([0-9.]+\s*(?:F|C)?)", re.I)),
    ("pulse", re.compile(r"\b(?:Pulse|P)\s*[:\-]?\s*([0-9]{2,3}\s*/?\s*(?:min|mt)?)", re.I)),
    ("respiratory_rate", re.compile(r"\b(?:Resp|RR)\s*[:\-]?\s*([0-9]{1,3}\s*/?\s*(?:min|mt)?)", re.I)),
    ("blood_pressure", re.compile(r"\b(?:BP|B\.P\.)\s*[:\-]?\s*([0-9]{2,3}\s*/\s*[0-9]{2,3})", re.I)),
    ("spo2", re.compile(r"\b(?:SpO2|spo\s*2|spo2)\s*[:\-]?\s*([0-9]{2,3}\s*%?)", re.I)),
]

MEDICATION_HINTS = [
    "trastuzumab", "docetaxel", "palon", "palono", "palonosetron", "pan", "pantoprazole",
    "dexamethasone", "ondansetron", "paclitaxel", "carboplatin", "cisplatin", "doxorubicin",
    "cyclophosphamide", "fluorouracil", "5fu", "bevacizumab", "rituximab", "nivolumab",
]

MED_LINE_RE = re.compile(
    r"(?P<prefix>(?:\d+\s*ml|inj\.?|iv|tab\.?|cap\.?)?[^\n]{0,30})"
    r"(?P<drug>trastuzumab|docetaxel|palon[a-z]*|pan\b|pantop[a-z]*|pantoprazole|paclitaxel|carboplatin|cisplatin|"
    r"doxorubicin|cyclophosphamide|ondansetron|dexamethasone|bevacizumab|rituximab|nivolumab)"
    r"(?P<tail>[^\n]{0,90})",
    re.I,
)


def extract_artifact_mentions(artifacts: Iterable[Dict[str, Any]], page_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    mentions: List[Dict[str, Any]] = []
    seen = set()
    for artifact in artifacts:
        text = normalize_text(artifact.get("normalized_text") or artifact.get("text") or "")
        if not text:
            continue
        _extract_fields(mentions, seen, artifact, text)
        _extract_vitals(mentions, seen, artifact, text)
        _extract_medications(mentions, seen, artifact, text)

    for row in page_rows:
        text = normalize_text(row.get("text") or "")
        if not text:
            continue
        pseudo_artifact = {
            "id": "",
            "page_num": row.get("page_num", 0),
            "artifact_type": "page_text",
            "role": "page_text",
            "backend": row.get("selected_source", "native"),
            "bbox": [],
        }
        _extract_fields(mentions, seen, pseudo_artifact, text)
        _extract_vitals(mentions, seen, pseudo_artifact, text)
        _extract_medications(mentions, seen, pseudo_artifact, text)

    return mentions


def _extract_fields(out, seen, artifact, text: str) -> None:
    for category, label, pattern in FIELD_PATTERNS:
        for match in pattern.finditer(text):
            value = _clean_value(match.group(1))
            _add(out, seen, artifact, category, label, value, match.group(0))


def _extract_vitals(out, seen, artifact, text: str) -> None:
    for label, pattern in VITAL_PATTERNS:
        for match in pattern.finditer(text):
            _add(out, seen, artifact, "vital_sign", label, _clean_value(match.group(1)), match.group(0))


def _extract_medications(out, seen, artifact, text: str) -> None:
    metadata = artifact.get("metadata") or {}
    for item in metadata.get("order_items") or []:
        if not isinstance(item, dict):
            continue
        raw = _clean_value(item.get("expanded_text") or item.get("raw_text") or item.get("text") or "")
        if not raw:
            continue
        drug_candidates = item.get("drug_candidates") or []
        normalized_drug = (
            item.get("drug")
            or (drug_candidates[0].get("normalized") if drug_candidates else "")
        )
        attrs = {
            "drug_name": normalized_drug or item.get("drug", ""),
            "dose": item.get("dose", ""),
            "route": item.get("route", ""),
            "frequency_or_time": item.get("frequency") or item.get("duration", ""),
            "fluid": item.get("fluid") or item.get("volume", ""),
            "instruction": item.get("instruction", ""),
            "short_forms": item.get("short_forms", []),
            "drug_candidates": drug_candidates,
        }
        normalized = _clean_value(normalized_drug or raw)
        _add(
            out,
            seen,
            artifact,
            "medication",
            "handwritten_medication_order",
            raw,
            item.get("raw_text") or raw,
            attributes=attrs,
            normalized_value=normalized,
        )

    for item in metadata.get("vitals") or []:
        if not isinstance(item, dict):
            continue
        label = _clean_value(item.get("label", ""))
        value = _clean_value(item.get("value", ""))
        if label and value:
            _add(
                out,
                seen,
                artifact,
                "vital_sign",
                label.lower().replace(" ", "_"),
                value,
                item.get("raw_text") or f"{label}: {value}",
                attributes={"unit": item.get("unit", ""), "source": "handwriting_order_extractor"},
            )

    if artifact.get("backend") == "groq_handwriting_order" and metadata.get("order_items"):
        return

    lines = [line.strip() for line in re.split(r"[\n|]+", text) if line.strip()]
    if not lines and any(hint in text.lower() for hint in MEDICATION_HINTS):
        lines = [text]
    for line in lines:
        for match in MED_LINE_RE.finditer(line):
            raw = _clean_value(match.group(0))
            drug = _clean_value(match.group("drug"))
            normalized = normalize_order_text(raw)
            drug_candidates = normalized.get("drug_candidates", [])
            normalized_drug = drug_candidates[0]["normalized"] if drug_candidates else drug
            attrs = {
                "drug_name": normalized_drug,
                "route": _find_first(r"\b(iv|i/v|inj\.?|tab\.?|cap\.?)\b", raw),
                "dose": _find_first(r"\b\d+(?:\.\d+)?\s*(?:mg|mcg|g|gm|ml)\b", raw),
                "frequency_or_time": _find_first(r"\b(?:\d+\s*hrs?|stat|od|bd|tds|q[0-9]+h)\b", raw),
                "expanded_text": normalized.get("expanded_text", ""),
                "short_forms": normalized.get("short_forms", []),
                "drug_candidates": drug_candidates,
            }
            _add(out, seen, artifact, "medication", "medication_order", raw, line, attributes=attrs, normalized_value=normalized_drug or raw)


def _add(
    out,
    seen,
    artifact,
    category: str,
    label: str,
    value: str,
    evidence: str,
    attributes: Dict[str, Any] | None = None,
    normalized_value: str | None = None,
) -> None:
    value = _clean_value(value)
    if not value:
        return
    key = (category.lower(), label.lower(), value.lower(), int(artifact.get("page_num") or 0))
    if key in seen:
        return
    seen.add(key)
    artifact_id = str(artifact.get("id") or "")
    out.append({
        "category": category,
        "label": label,
        "value": value,
        "normalized_value": _clean_value(normalized_value or value),
        "date_text": "",
        "certainty": "confirmed",
        "attributes": {
            **(attributes or {}),
            "artifact_type": artifact.get("artifact_type", ""),
            "artifact_role": artifact.get("role", ""),
            "backend": artifact.get("backend", ""),
            "bbox": artifact.get("bbox", []),
        },
        "evidence_quote": normalize_text(evidence)[:300],
        "source_pages": [int(artifact.get("page_num") or 0)] if artifact.get("page_num") else [],
        "evidence_ids": [artifact_id] if artifact_id else [],
        "evidence_artifact_ids": [artifact_id] if artifact_id else [],
        "origin": "artifact",
    })


def _clean_value(value: str) -> str:
    value = normalize_text(value)
    value = re.sub(r"\s+", " ", value).strip(" .,:;-")
    return value[:500]


def _find_first(pattern: str, text: str) -> str:
    match = re.search(pattern, text, flags=re.I)
    return match.group(0) if match else ""
