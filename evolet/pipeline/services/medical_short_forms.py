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
    "lnj": "injection",
    "1nj": "injection",
    "i/v": "intravenous",
    "iv": "intravenous",
    "i.v": "intravenous",
    "ivhr": "intravenous infusion over hours",
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
    "n/s": "normal saline",
    "n saline": "normal saline",
    "normal saline": "normal saline",
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
    "folfox",
    "irinotecan",
    "cetuximab",
    "pemetrexed",
    "imatinib",
    "sunitinib",
    "gefitinib",
    "erlotinib",
    "letrozole",
    "tamoxifen",
    "anastrozole",
    "zoledronic",
    "zoledronic acid",
    "zoledronate",
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
    "oxali": "oxaliplatin",
    "oxal": "oxaliplatin",
    "ox": "oxaliplatin",
    "hetro": "hetronifly",
    "hetroni": "hetronifly",
    "hetronifly": "hetronifly",
}

TOKEN_RE = re.compile(r"\b[a-z][a-z0-9./-]{1,24}\b", re.I)
DOSE_RE = re.compile(r"\b\d+(?:\.\d+)?\s*(?:mg|mcg|g|gm|ml|mL|iu|IU|units?)\b", re.I)
VOLUME_RE = re.compile(r"\b\d+(?:\.\d+)?\s*(?:ml|mL|l|L)\b", re.I)
ROUTE_RE = re.compile(r"\b(?:ivhr|i/v|i\.v\.?|iv|po|oral|sc|s/c|im|intravenous|subcutaneous|intramuscular)\b", re.I)
FREQUENCY_RE = re.compile(r"\b(?:stat|od|bd|tds|qid|hs|sos|q\d+h|q\d+\s*hr|daily|weekly|cycle\s*\d+)\b", re.I)
FUZZY_DRUG_STOPWORDS = {
    "note", "table", "doctor", "consultant", "patient", "name", "age", "sex", "ward", "room",
    "bed", "date", "time", "hospital", "cancer", "care", "signature", "treatment", "external",
    "diagnosis", "address", "meerut", "limited", "ltd", "pvt", "form", "case", "sheet",
}


def canonicalize_order_text(text: str) -> str:
    raw = normalize_text(text)
    replacements = [
        (r"\b[1lI]\s*n\s*j\.?\b", "Inj"),
        (r"\b[1lI]\s*nj\.?\b", "Inj"),
        (r"\binj\s*[:;,-]?\s*", "Inj ",),
        (r"\b[1lI]\s*/\s*v\b", "IV"),
        (r"\bi\s*/\s*v\b", "IV"),
        (r"\bi\s*\.?\s*v\.?\b", "IV"),
        (r"\bn\s*/\s*saline\b", "NS"),
        (r"\bn\s*/\s*s\b", "NS"),
        (r"\bn\s*saline\b", "NS"),
        (r"\b(\d+(?:\.\d+)?)\s*m\s*g\b", r"\1 mg"),
        (r"\b(\d+(?:\.\d+)?)\s*m\s*l\b", r"\1 ml"),
        (r"\b(\d+(?:\.\d+)?)\s*mc\s*g\b", r"\1 mcg"),
    ]
    out = raw
    for pattern, repl in replacements:
        out = re.sub(pattern, repl, out, flags=re.I)
    return normalize_text(out)


def normalize_order_text(text: str) -> Dict[str, Any]:
    raw = canonicalize_order_text(text)
    expanded = expand_short_forms(raw)
    drug_matches = find_drug_candidates(raw)
    dose = _dose_near_drug(raw, drug_matches) or _first(DOSE_RE.findall(raw))
    return {
        "raw_text": raw,
        "expanded_text": expanded,
        "drug_candidates": drug_matches,
        "dose": dose,
        "volume": _first(VOLUME_RE.findall(raw)),
        "route": _first(ROUTE_RE.findall(raw)),
        "frequency": _first(FREQUENCY_RE.findall(raw)),
        "short_forms": [
            {"short": short, "expanded": full}
            for short, full in SHORT_FORM_MAP.items()
            if _has_short_form(raw, short)
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
    out = canonicalize_order_text(text)
    for short, full in sorted(SHORT_FORM_MAP.items(), key=lambda item: -len(item[0])):
        out = re.sub(_short_form_pattern(short), f"{short.upper()} ({full})", out, flags=re.I)
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
            if len(clean) < 5 or clean in SHORT_FORM_MAP or clean in FUZZY_DRUG_STOPWORDS:
                continue
            match = process.extractOne(clean, DRUG_TERMS)
            if match and match[1] >= 88:
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


def _short_form_pattern(short: str) -> str:
    if " " in short:
        return rf"\b{re.escape(short)}\b"
    return rf"(?<![A-Za-z0-9]){re.escape(short)}\.?(?![A-Za-z0-9])"


def _has_short_form(text: str, short: str) -> bool:
    return bool(re.search(_short_form_pattern(short), text, flags=re.I))


def _dose_near_drug(text: str, drug_matches: List[Dict[str, Any]]) -> str:
    if not drug_matches:
        doses = DOSE_RE.findall(text)
        if len(doses) > 1 and VOLUME_RE.fullmatch(doses[0] or ""):
            return doses[1]
        return ""
    lowered = text.lower()
    for item in drug_matches:
        needles = [str(item.get("raw") or ""), str(item.get("normalized") or "")]
        for needle in needles:
            needle = needle.lower().strip()
            if not needle:
                continue
            pos = lowered.find(needle)
            if pos < 0:
                continue
            forward = text[pos : min(len(text), pos + 90)]
            match = DOSE_RE.search(forward)
            if match:
                return match.group(0)
    doses = DOSE_RE.findall(text)
    if len(doses) > 1 and VOLUME_RE.fullmatch(doses[0] or ""):
        return doses[1]
    return ""


def _first(values: List[str]) -> str:
    return values[0] if values else ""
