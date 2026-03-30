"""
Regex Extractor — Deterministic (rule-based) clinical mention extraction.
=========================================================================
This is Stage 3 of the pipeline: fast, zero-GPU extraction using hand-crafted
regular expressions.  It runs on every note BEFORE the LLM phase so that:

  a) Notes already well-covered by regex are skipped by the LLM (cost saving).
  b) Regex mentions are merged with LLM mentions in the final record.

Categories extracted
--------------------
  diagnosis        Final diagnosis / impression / "case of"
  pathology        HPR, histopathology, IHC results
  imaging          PET CT, CECT, MRI, CT
  medication       Tab *, named drugs (sunitinib, everolimus, …)
  symptom          C/O (complaints of), pain mentions
  performance_status PS and KPS scores
  surgery          Nephrectomy, surgery mentions
  radiotherapy     RT, radiotherapy, SBRT
  lab              Serum creatinine
  genomics         NGS mentions
  plan             Structured plan block (multi-line)
  follow_up        "Review after", "R/S", "follow-up" lines

Performance note
----------------
ALL regex patterns are compiled once at module level (not inside the
function body) so repeated calls over thousands of notes remain fast.
"""

import re
import logging
from typing import Any, Dict, List, Optional, Tuple

from .pdf_extractor import normalize_text, word_count
from .note_segmenter import extract_dates

logger = logging.getLogger("pipeline")


# ─────────────────────────────────────────────────────────────────────────────
# Module-level compiled patterns
# Tuples of (compiled_pattern, label_string) where applicable.
# ─────────────────────────────────────────────────────────────────────────────

# Splits a text block into individual lines
_LINE_BREAK_RE = re.compile(r"\n+")

# ── Diagnosis ────────────────────────────────────────────────────────────────
# Tried in order; first match wins.  All capture group 1 = the diagnosis text.
_DIAGNOSIS_PATTERNS: List[re.Pattern] = [
    re.compile(r"final diagnosis\s*[:\-]\s*(.+)",  re.I),
    re.compile(r"diagnosis\s*[:\-]\s*(.+)",        re.I),
    re.compile(r"imp\s*[-:]\s*(.+)",               re.I),
    re.compile(r"impression\s*[:\-]\s*(.+)",       re.I),
    re.compile(r"case of\s+(.+)",                  re.I),
]

# ── Pathology ────────────────────────────────────────────────────────────────
_PATHOLOGY_PATTERNS: List[Tuple[re.Pattern, str]] = [
    (re.compile(r"(hpr[^\n:]*[:\-]\s*.+)",       re.I), "HPR"),
    (re.compile(r"(histopath[^\n:]*[:\-]\s*.+)", re.I), "histopathology"),
    (re.compile(r"(ihc[^\n:]*[:\-]\s*.+)",       re.I), "IHC"),
]

# ── Imaging ──────────────────────────────────────────────────────────────────
_IMAGING_PATTERNS: List[Tuple[re.Pattern, str]] = [
    (re.compile(r"(pet ct[^\n]*[:\-]?\s*.+)",       re.I), "PET CT"),
    (re.compile(r"(c?ect[^\n]*[:\-]?\s*.+)",        re.I), "CECT"),
    (re.compile(r"(mri[^\n]*[:\-]?\s*.+)",          re.I), "MRI"),
    (re.compile(r"(ct study reveals[^\n]*[:\-]?\s*.+)", re.I), "CT"),
]

# ── Medications ──────────────────────────────────────────────────────────────
# Pattern 1: generic "Tab X …" or "T. X …" format
_MED_TAB_RE = re.compile(
    r"((tab|t\.)\s*[A-Z][A-Za-z0-9.+-]*(?:\s+[A-Za-z0-9.+-]+){0,4}"
    r"\s*(?:\d+(?:\.\d+)?)?\s*(?:mg|mcg|gm)?[^\n]*)",
    re.I,
)
# Pattern 2: known drug names (searched on lowercase text)
_KNOWN_DRUGS: List[str] = [
    "sunitinib", "everolimus", "pazopanib",
    "nivolumab", "cabozantinib", "chemotherapy",
]

# ── Symptoms ─────────────────────────────────────────────────────────────────
_COMPLAINT_RE = re.compile(r"(c/o\s*[-:]?\s*[^\n]+)", re.I)
_PAIN_RE      = re.compile(r"(pain[^\n]{0,120})",      re.I)

# ── Performance status ───────────────────────────────────────────────────────
_PS_RE  = re.compile(r"\b(PS\s*[-:]?\s*\d)\b",  re.I)
_KPS_RE = re.compile(r"\b(KPS\s*\d{2,3})\b",    re.I)

# ── Surgery / Radiotherapy ───────────────────────────────────────────────────
# Tuples of (keyword, category)
_PROCEDURE_KEYWORDS: List[Tuple[str, str]] = [
    ("nephrectomy", "surgery"),
    ("surgery",     "surgery"),
    ("rt",          "radiotherapy"),
    ("radiotherapy","radiotherapy"),
    ("sbrt",        "radiotherapy"),
]

# ── Labs ─────────────────────────────────────────────────────────────────────
_CREAT_RE = re.compile(r"\b(serum creat\s*\d+(?:\.\d+)?)\b", re.I)

# ── Genomics ─────────────────────────────────────────────────────────────────
_NGS_RE = re.compile(r"\b(NGS\s*[:\-]?\s*[^\n]+)\b", re.I)

# ── Follow-up ────────────────────────────────────────────────────────────────
_FOLLOWUP_RE = re.compile(
    r"(review after[^\n]+|r/s[^\n]+|follow[- ]?up[^\n]+)", re.I
)

# Plan section terminator keywords
_PLAN_END_RE = re.compile(
    r"^(date|clinical note details|patient assessment details|"
    r"joint-clinic details|entered by|seen by)\b",
    re.I,
)
# Plan section starter keywords
_PLAN_START_RE = re.compile(
    r"^(plan|tentative plan|adv|remarks / others)\s*[:\-]?$", re.I
)


# ─────────────────────────────────────────────────────────────────────────────
# Private helpers
# ─────────────────────────────────────────────────────────────────────────────

def _first_date_or_blank(text: str) -> str:
    """Return the first date string found in *text*, or an empty string."""
    dates = extract_dates(text)
    return dates[0] if dates else ""


def _add_mention(
    out: List[Dict[str, Any]],
    note: Dict[str, Any],
    category: str,
    label: str,
    value: str,
    certainty: str = "confirmed",
    attributes: Optional[Dict[str, Any]] = None,
    evidence_quote: Optional[str] = None,
) -> None:
    """
    Append a single mention dict to *out*, skipping empty values.

    All string fields are normalised via normalize_text() so downstream
    deduplication comparisons are stable.
    """
    value = normalize_text(value)
    label = normalize_text(label)
    if not value:
        return

    out.append({
        "category":        category or "unknown",
        "label":           label,
        "value":           value,
        "normalized_value":value,
        "date_text":       _first_date_or_blank(note["text"]),
        "certainty":       certainty,
        "attributes":      attributes or {},
        "source_pages":    [note["page_num"]],
        "evidence_ids":    [note["note_id"]],
        "evidence_quote":  normalize_text(evidence_quote or value[:240]),
        "origin":          "regex",
    })


def _extract_plan_block(text: str) -> str:
    """
    Extract the free-text "Plan" block from a clinical note.

    Looks for a line that is exactly a plan header (e.g. "PLAN:", "Adv:"),
    then captures up to 6 subsequent lines until the next section header is
    encountered.  Returns the captured lines joined by " | ".
    """
    lines = [normalize_text(x) for x in _LINE_BREAK_RE.split(text) if x.strip()]
    out: List[str] = []
    capture = False

    for line in lines:
        low = line.lower()

        if _PLAN_START_RE.match(low):
            # Start of a plan block — begin capturing
            capture = True
            continue

        if capture:
            if _PLAN_END_RE.match(low):
                break   # Hit a section header — stop
            out.append(line)
            if len(out) >= 6:
                break   # Safety cap to avoid capturing the entire page

    return normalize_text(" | ".join(out))


def _first_line_containing(keyword: str, text: str) -> str:
    """
    Return the first line of *text* that contains *keyword* (case-insensitive).
    Falls back to returning *keyword* itself if no matching line is found.
    """
    kw_lower = keyword.lower()
    for line in text.splitlines():
        if kw_lower in line.lower():
            return line.strip()
    return keyword


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def extract_note_mentions(note: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Run all deterministic regex extractors on a single note.

    Extraction order matches clinical importance (diagnosis first) so that
    if deduplication trims the list, the most important mentions survive.

    Returns a deduplicated list of mention dicts.
    """
    text = note["text"]
    low  = text.lower()
    mentions: List[Dict[str, Any]] = []

    # ── Diagnosis ────────────────────────────────────────────────────────────
    # Only the first matching pattern contributes (avoid duplicate diagnoses)
    for pat in _DIAGNOSIS_PATTERNS:
        m = pat.search(text)
        if m:
            val = normalize_text(m.group(1))
            val = re.split(r"\n|\|", val)[0]   # take first line only
            _add_mention(mentions, note, "diagnosis", "diagnosis", val, evidence_quote=val)
            break

    # ── Pathology ────────────────────────────────────────────────────────────
    for pat, label in _PATHOLOGY_PATTERNS:
        for m in pat.finditer(text):
            val = normalize_text(m.group(1))
            _add_mention(mentions, note, "pathology", label, val, evidence_quote=val)

    # ── Imaging ──────────────────────────────────────────────────────────────
    for pat, label in _IMAGING_PATTERNS:
        for m in pat.finditer(text):
            val = normalize_text(m.group(1))
            _add_mention(mentions, note, "imaging", label, val, evidence_quote=val)

    # ── Medications (Tab / T. prefix) ────────────────────────────────────────
    for m in _MED_TAB_RE.finditer(text):
        val = normalize_text(m.group(1))
        if len(val) >= 5:   # filter out single-character noise
            _add_mention(mentions, note, "medication", "medication", val, evidence_quote=val)

    # ── Medications (known drug names) ───────────────────────────────────────
    for drug in _KNOWN_DRUGS:
        if drug in low:
            line = _first_line_containing(drug, text)
            _add_mention(mentions, note, "medication", "drug", line, evidence_quote=line)

    # ── Symptoms ─────────────────────────────────────────────────────────────
    for m in _COMPLAINT_RE.finditer(text):
        _add_mention(mentions, note, "symptom", "complaint", m.group(1), evidence_quote=m.group(1))
    for m in _PAIN_RE.finditer(text):
        _add_mention(mentions, note, "symptom", "pain", m.group(1), evidence_quote=m.group(1))

    # ── Performance status ───────────────────────────────────────────────────
    for m in _PS_RE.finditer(text):
        _add_mention(mentions, note, "performance_status", "PS", m.group(1), evidence_quote=m.group(1))
    for m in _KPS_RE.finditer(text):
        _add_mention(mentions, note, "performance_status", "KPS", m.group(1), evidence_quote=m.group(1))

    # ── Surgery / Radiotherapy ───────────────────────────────────────────────
    for keyword, category in _PROCEDURE_KEYWORDS:
        if re.search(rf"\b{re.escape(keyword)}\b", low):
            line = _first_line_containing(keyword, text)
            _add_mention(mentions, note, category, keyword, line, evidence_quote=line)

    # ── Labs ─────────────────────────────────────────────────────────────────
    for m in _CREAT_RE.finditer(low):
        _add_mention(mentions, note, "lab", "serum creatinine", m.group(1), evidence_quote=m.group(1))

    # ── Genomics ─────────────────────────────────────────────────────────────
    for m in _NGS_RE.finditer(text):
        _add_mention(mentions, note, "genomics", "NGS", m.group(1), evidence_quote=m.group(1))

    # ── Plan block ───────────────────────────────────────────────────────────
    plan_block = _extract_plan_block(text)
    if plan_block:
        _add_mention(mentions, note, "plan", "plan", plan_block, evidence_quote=plan_block)

    # ── Follow-up ────────────────────────────────────────────────────────────
    for m in _FOLLOWUP_RE.finditer(text):
        _add_mention(mentions, note, "follow_up", "follow_up", m.group(1), evidence_quote=m.group(1))

    # ── Deduplicate by (category, label, value) ──────────────────────────────
    # Preserves first occurrence; drops exact duplicates introduced by
    # overlapping regex patterns.
    seen: set = set()
    unique: List[Dict[str, Any]] = []
    for x in mentions:
        key = (x["category"], x["label"].lower(), x["value"].lower())
        if key not in seen:
            seen.add(key)
            unique.append(x)

    return unique


def extract_all_notes(notes: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """
    Run regex extraction on every note and return results keyed by note_id.

    Parameters
    ----------
    notes : list of note dicts from segment_pages_to_notes()

    Returns
    -------
    {note_id: [mention_dicts]}  — empty list for notes with no matches
    """
    return {note["note_id"]: extract_note_mentions(note) for note in notes}
