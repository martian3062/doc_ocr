"""Medical short-form and drug-name normalization for handwritten orders."""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List

try:
    from rapidfuzz import process
except ImportError:  # pragma: no cover - rapidfuzz is in requirements
    process = None

from .pdf_extractor import normalize_text


SHORT_FORM_MAP = {
    "inj": "injection",
    "i/v": "intravenous",
    "iv": "intravenous",
    "tab": "tablet",
    "cap": "capsule",
    "syp": "syrup",
    "syr": "syrup",
    "od": "once daily",
    "bd": "twice daily",
    "tds": "three times daily",
    "qid": "four times daily",
    "hs": "at bedtime",
    "sos": "as needed",
    "stat": "immediately",
    "ac": "before meals",
    "pc": "after meals",
    "q4h": "every 4 hours",
    "q6h": "every 6 hours",
    "q8h": "every 8 hours",
    "q12h": "every 12 hours",
    "ns": "normal saline",
    "dns": "dextrose normal saline",
    "d5": "5% dextrose",
    "rl": "ringer lactate",
    "po": "oral",
    "sc": "subcutaneous",
    "im": "intramuscular",
}

DRUG_TERMS = [
    "palonosetron",
    "pantoprazole",
    "trastuzumab",
    "docetaxel",
    "paclitaxel",
    "carboplatin",
    "cisplatin",
    "doxorubicin",
    "cyclophosphamide",
    "fluorouracil",
    "5-fluorouracil",
    "leucovorin",
    "ondansetron",
    "dexamethasone",
    "aprepitant",
    "fosaprepitant",
    "bevacizumab",
    "rituximab",
    "nivolumab",
    "pembrolizumab",
    "pertuzumab",
    "gemcitabine",
    "oxaliplatin",
    "irinotecan",
    "capecitabine",
    "methotrexate",
    "vincristine",
    "vinblastine",
    "etoposide",
    "filgrastim",
    "pegfilgrastim",
    "paracetamol",
    "amoxicillin",
    "ceftriaxone",
    "metronidazole",
    "omeprazole",
    "ranitidine",
]

COMMON_MISREADS = {
    "palo": "palonosetron",
    "palono": "palonosetron",
    "palononail": "palonosetron",
    "palonos": "palonosetron",
    "pan": "pantoprazole",
    "pantop": "pantoprazole",
    "trastu": "trastuzumab",
    "trast": "trastuzumab",
    "docet": "docetaxel",
    "dexa": "dexamethasone",
    "ondan": "ondansetron",
    "carbo": "carboplatin",
    "cis": "cisplatin",
    "pacli": "paclitaxel",
}

TOKEN_RE = re.compile(r"\b[a-z][a-z0-9./-]{1,24}\b", re.I)
DOSE_RE = re.compile(r"\b\d+(?:\.\d+)?\s*(?:mg|mcg|g|gm|ml|mL|iu|IU|units?)\b", re.I)
VOLUME_RE = re.compile(r"\b\d+(?:\.\d+)?\s*(?:ml|mL|l|L)\b", re.I)


def normalize_order_text(text: str) -> Dict[str, Any]:
    raw = normalize_text(text)
    expanded = expand_short_forms(raw)
    drug_matches = find_drug_candidates(raw)
    return {
        "raw_text": raw,
        "expanded_text": expanded,
        "drug_candidates": drug_matches,
        "dose": _first(DOSE_RE.findall(raw)),
        "volume": _first(VOLUME_RE.findall(raw)),
        "short_forms": [
            {"short": short, "expanded": full}
            for short, full in SHORT_FORM_MAP.items()
            if re.search(rf"\b{re.escape(short)}\.?\b", raw, flags=re.I)
        ],
    }


def normalize_orders(items: Iterable[Dict[str, Any] | str]) -> List[Dict[str, Any]]:
    normalized = []
    for item in items:
        if isinstance(item, str):
            text = item
            source = {"raw": item}
        else:
            source = dict(item)
            text = " ".join(
                normalize_text(str(item.get(key) or ""))
                for key in ("raw_text", "text", "drug", "dose", "route", "fluid", "frequency", "duration", "instruction")
                if normalize_text(str(item.get(key) or ""))
            )
        if not normalize_text(text):
            continue
        norm = normalize_order_text(text)
        normalized.append({**source, **norm})
    return normalized


def expand_short_forms(text: str) -> str:
    out = normalize_text(text)
    for short, full in sorted(SHORT_FORM_MAP.items(), key=lambda item: -len(item[0])):
        out = re.sub(rf"\b{re.escape(short)}\.?\b", f"{short.upper()} ({full})", out, flags=re.I)
    return normalize_text(out)


def find_drug_candidates(text: str, *, limit: int = 4) -> List[Dict[str, Any]]:
    lowered = normalize_text(text).lower()
    candidates: Dict[str, Dict[str, Any]] = {}

    for needle, canonical in COMMON_MISREADS.items():
        if _matches_common_misread(lowered, needle):
            candidates[canonical] = {
                "raw": needle,
                "normalized": canonical,
                "score": 96,
                "method": "misread_map",
            }

    for term in DRUG_TERMS:
        if term.lower() in lowered:
            candidates[term] = {
                "raw": term,
                "normalized": term,
                "score": 100,
                "method": "exact",
            }

    if process is not None:
        for token in TOKEN_RE.findall(lowered):
            clean = token.strip("./-")
            if len(clean) < 4 or clean in SHORT_FORM_MAP:
                continue
            match = process.extractOne(clean, DRUG_TERMS)
            if match and match[1] >= 82:
                normalized, score, _ = match
                current = candidates.get(normalized)
                if current is None or score > current["score"]:
                    candidates[normalized] = {
                        "raw": clean,
                        "normalized": normalized,
                        "score": int(score),
                        "method": "fuzzy",
                    }

    return sorted(candidates.values(), key=lambda item: item["score"], reverse=True)[:limit]


def _matches_common_misread(text: str, needle: str) -> bool:
    if needle == "pan":
        return bool(re.search(r"\bpan\b|\bpan\s*\d", text, flags=re.I))
    return bool(re.search(rf"\b{re.escape(needle)}[a-z]*\b", text, flags=re.I))


def _first(values: List[str]) -> str:
    return values[0] if values else ""
