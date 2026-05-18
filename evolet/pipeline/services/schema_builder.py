"""
Schema Builder — Deep hierarchical JSON schema with clinical sub-parts.
=========================================================================
The standard pipeline output is a flat list of mentions grouped by category.
This module takes that flat structure and builds a RICHER nested record
with clinically meaningful sub-schemas per category group.

Why this matters
----------------
A flat list of 40 mentions is hard to consume programmatically or display
in a structured way.  The deep schema organises the same data into named
clinical sections with parsed sub-fields:

  Flat mentions (before)
  ----------------------
  [
    {category:"diagnosis", value:"RCC Stage III Clear Cell"},
    {category:"medication", value:"Tab Sunitinib 50mg OD"},
    {category:"imaging",    value:"PET CT: hypermetabolic lesion L kidney"},
    ...
  ]

  Deep schema (after)
  -------------------
  {
    "oncology_summary": {
      "primary_diagnosis": {
        "full_text":   "RCC Stage III Clear Cell",
        "stage":       "Stage III",
        "histology":   "Clear Cell",
        "certainty":   "confirmed"
      },
      "performance_status": { "ecog": null, "kps": null }
    },
    "treatment": {
      "medications": [
        {
          "drug_name":  "Sunitinib",
          "dose":       "50mg",
          "frequency":  "OD",
          "route":      "oral",
          "raw_value":  "Tab Sunitinib 50mg OD",
          "date_text":  "..."
        }
      ],
      "surgery":       [...],
      "radiotherapy":  [...]
    },
    "investigations": {
      "imaging":   [...],
      "pathology": [...],
      "labs":      [...],
      "genomics":  [...]
    },
    "clinical_notes": {
      "symptoms":  [...],
      "plan":      [...],
      "follow_up": [...]
    },
    "other": [...],

    -- preserved originals --
    "mentions_flat":    [...],   ← full corrected mention list
    "grouped_record":   {...},   ← original category grouping
    "traceability":     {...},
    "stats":            {...},
    "review_flags":     [...],
    "schema_version":   "2.0"
  }

Sub-field extraction
--------------------
Each category has a dedicated parser that uses lightweight regex to pull
named sub-fields from the `value` string.  If a field cannot be parsed,
it is set to null (never raises, never invents data).

Schema version
--------------
"schema_version": "2.0" is stamped on every deep record so downstream
consumers know which format to expect when the schema evolves.
"""

import re
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("pipeline")

# ── Schema version ────────────────────────────────────────────────────────────
SCHEMA_VERSION = "3.0-adaptive"


# ─────────────────────────────────────────────────────────────────────────────
# Category → top-level group mapping
# ─────────────────────────────────────────────────────────────────────────────
# Maps every pipeline category name to the section it belongs to in the deep
# schema.  Categories not listed here fall into "other".

_CATEGORY_GROUP: Dict[str, str] = {
    # Oncology summary
    "diagnosis":          "oncology_summary",
    "status":             "oncology_summary",
    "performance_status": "oncology_summary",
    # Treatment
    "medication":         "treatment",
    "surgery":            "treatment",
    "radiotherapy":       "treatment",
    "procedure":          "treatment",
    # Investigations
    "imaging":            "investigations",
    "pathology":          "investigations",
    "lab":                "investigations",
    "genomics":           "investigations",
    # Clinical notes
    "symptom":            "clinical_notes",
    "plan":               "clinical_notes",
    "follow_up":          "clinical_notes",
}


# ─────────────────────────────────────────────────────────────────────────────
# Regex helpers shared across parsers
# ─────────────────────────────────────────────────────────────────────────────

# Stage detection: "Stage III", "stage 3", "T3N1M0"-style
_STAGE_RE   = re.compile(r"\bstage\s+(i{1,3}v?|[1-4][abc]?)\b", re.I)
_TNM_RE     = re.compile(r"\b(t[0-4][abc]?\s*n[0-3][abc]?\s*m[01])\b", re.I)

# Grade
_GRADE_RE   = re.compile(r"\bgrade\s+([1-4]|i{1,3}v?)\b", re.I)

# Histology keywords
_HISTO_KEYWORDS = [
    "clear cell", "papillary", "chromophobe", "oncocytoma",
    "adenocarcinoma", "squamous", "transitional", "urothelial",
    "medullary", "anaplastic", "follicular", "sarcomatoid",
]

# Dose: "50mg", "1.5g", "200mcg"
_DOSE_RE      = re.compile(r"\b(\d+(?:\.\d+)?)\s*(mg|mcg|gm|g|ml|iu)\b", re.I)

# Frequency abbreviations
_FREQ_MAP = {
    r"\bod\b":  "Once daily",
    r"\bbd\b":  "Twice daily",
    r"\btds\b": "Three times daily",
    r"\bqid\b": "Four times daily",
    r"\bsos\b": "As needed",
    r"\bhs\b":  "At bedtime",
    r"\bac\b":  "Before meals",
    r"\bpc\b":  "After meals",
}

# Route keywords
_ROUTE_MAP = {
    "oral": "oral", "tab": "oral", "cap": "oral",
    "iv":   "intravenous", "intravenous": "intravenous",
    "im":   "intramuscular", "intramuscular": "intramuscular",
    "sc":   "subcutaneous", "subcutaneous": "subcutaneous",
    "topical": "topical",
}

# Lab value: "creat 1.2", "hb 9.5"
_LAB_VALUE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:mg/dl|g/dl|u/l|iu/l|mmol|meq)?", re.I)

# Imaging modality normalisation
_IMAGING_MODALITY_MAP = {
    r"\bpet\s*ct\b": "PET CT",
    r"\bpet\b":      "PET",
    r"\bcect\b":     "CECT",
    r"\bct\b":       "CT",
    r"\bmri\b":      "MRI",
    r"\busgs?\b":    "USG",
    r"\bx-?ray\b":   "X-Ray",
    r"\bhrct\b":     "HRCT",
}


# ─────────────────────────────────────────────────────────────────────────────
# Per-category sub-parsers
# Each function takes one mention dict and returns a structured sub-dict.
# NEVER raises — returns null/empty fields on failure.
# ─────────────────────────────────────────────────────────────────────────────

def _safe(fn, *args, default=None):
    """Call fn(*args) and return default on any exception."""
    try:
        return fn(*args)
    except Exception:
        return default


def _parse_diagnosis(mention: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract structured sub-fields from a diagnosis mention.

    Sub-fields
    ----------
    full_text    : the complete raw value string
    stage        : e.g. "Stage III", "T3N1M0"
    histology    : e.g. "Clear Cell", "Papillary"
    grade        : e.g. "Grade 3"
    laterality   : "Right" | "Left" | "Bilateral" | null
    certainty    : from the mention
    date_text    : from the mention
    """
    val = mention.get("value", "")
    low = val.lower()

    # Stage
    stage = None
    m = _STAGE_RE.search(low)
    if m:
        stage = f"Stage {m.group(1).upper()}"
    else:
        m = _TNM_RE.search(low)
        if m:
            stage = m.group(1).upper()

    # Histology
    histology = next(
        (kw.title() for kw in _HISTO_KEYWORDS if kw in low),
        None,
    )

    # Grade
    grade = None
    m = _GRADE_RE.search(low)
    if m:
        grade = f"Grade {m.group(1).upper()}"

    # Laterality
    if "bilateral" in low:
        laterality = "Bilateral"
    elif "right" in low or " rt " in low or low.startswith("rt "):
        laterality = "Right"
    elif "left" in low or " lt " in low or low.startswith("lt "):
        laterality = "Left"
    else:
        laterality = None

    return {
        "full_text":  val,
        "stage":      stage,
        "histology":  histology,
        "grade":      grade,
        "laterality": laterality,
        "certainty":  mention.get("certainty"),
        "date_text":  mention.get("date_text", ""),
        "source_pages": mention.get("source_pages", []),
        "evidence_quote": mention.get("evidence_quote", ""),
    }


def _parse_medication(mention: Dict[str, Any]) -> Dict[str, Any]:
    """
    Parse dose, frequency, route from a medication mention value.

    Example: "Tab Sunitinib 50mg OD for 4 weeks"
    → drug_name="Sunitinib", dose="50mg", frequency="Once daily",
      route="oral", duration="4 weeks"
    """
    val = mention.get("value", "")
    low = val.lower()

    # Drug name: word after "tab" / "t." / "cap" / "inj" prefixes
    drug_name = None
    m = re.search(r"\b(?:tab|t\.|cap|inj|inj\.)\s*([A-Za-z][A-Za-z0-9\-]+)", val, re.I)
    if m:
        drug_name = m.group(1).strip()
    else:
        # First capitalised word that isn't a prefix
        m = re.search(r"\b([A-Z][a-z]{3,})\b", val)
        if m:
            drug_name = m.group(1)

    # Dose
    dose = None
    m = _DOSE_RE.search(val)
    if m:
        dose = m.group(0).strip()

    # Frequency
    frequency = None
    for pat, label in _FREQ_MAP.items():
        if re.search(pat, low):
            frequency = label
            break

    # Route (infer from prefix keyword)
    route = None
    for kw, label in _ROUTE_MAP.items():
        if kw in low:
            route = label
            break

    # Duration: "for N weeks/months/days"
    duration = None
    m = re.search(r"for\s+(\d+\s+(?:week|month|day)s?)", low)
    if m:
        duration = m.group(1)

    return {
        "drug_name":  drug_name,
        "dose":       dose,
        "frequency":  frequency,
        "route":      route,
        "duration":   duration,
        "raw_value":  val,
        "date_text":  mention.get("date_text", ""),
        "certainty":  mention.get("certainty"),
        "source_pages": mention.get("source_pages", []),
        "evidence_quote": mention.get("evidence_quote", ""),
    }


def _parse_imaging(mention: Dict[str, Any]) -> Dict[str, Any]:
    """
    Parse imaging modality and key finding from an imaging mention.

    Example: "PET CT: hypermetabolic lesion left kidney 4.2cm"
    → modality="PET CT", finding="hypermetabolic lesion left kidney 4.2cm"
    """
    val = mention.get("value", "")
    low = val.lower()

    # Modality
    modality = None
    for pat, label in _IMAGING_MODALITY_MAP.items():
        if re.search(pat, low):
            modality = label
            break

    # Finding: text after modality keyword and colon/dash
    finding = None
    m = re.search(r"(?:pet\s*ct|cect|mri|ct|usgs?|hrct)[^\n:]*[:\-]\s*(.+)", val, re.I)
    if m:
        finding = m.group(1).strip()[:300]
    elif modality and ":" in val:
        finding = val.split(":", 1)[1].strip()[:300]

    return {
        "modality":   modality,
        "finding":    finding,
        "raw_value":  val,
        "date_text":  mention.get("date_text", ""),
        "certainty":  mention.get("certainty"),
        "source_pages": mention.get("source_pages", []),
        "evidence_quote": mention.get("evidence_quote", ""),
    }


def _parse_pathology(mention: Dict[str, Any]) -> Dict[str, Any]:
    """
    Parse pathology report type and result from a pathology mention.

    Example: "HPR: Clear Cell RCC Grade 3"
    → report_type="HPR", result="Clear Cell RCC Grade 3"
    """
    val   = mention.get("value", "")
    label = mention.get("label", "")
    low   = val.lower()

    report_type = label or next(
        (t for t in ("HPR", "IHC", "histopathology", "cytology") if t.lower() in low),
        None,
    )

    # Result: text after the report type prefix
    result = None
    m = re.search(r"(?:hpr|ihc|histopath)[^\n:]*[:\-]\s*(.+)", val, re.I)
    if m:
        result = m.group(1).strip()[:300]

    grade = None
    gm = _GRADE_RE.search(low)
    if gm:
        grade = f"Grade {gm.group(1).upper()}"

    return {
        "report_type": report_type,
        "result":      result,
        "grade":       grade,
        "raw_value":   val,
        "date_text":   mention.get("date_text", ""),
        "certainty":   mention.get("certainty"),
        "source_pages": mention.get("source_pages", []),
        "evidence_quote": mention.get("evidence_quote", ""),
    }


def _parse_lab(mention: Dict[str, Any]) -> Dict[str, Any]:
    """
    Parse test name and numeric result from a lab mention.

    Example: "serum creat 1.2"
    → test="Serum Creatinine", value_raw="1.2", unit=null
    """
    val = mention.get("value", "")

    # Numeric value
    numeric = None
    m = _LAB_VALUE_RE.search(val)
    if m:
        numeric = m.group(1)

    return {
        "test":      mention.get("label", "lab"),
        "value_raw": numeric,
        "full_text": val,
        "date_text": mention.get("date_text", ""),
        "source_pages": mention.get("source_pages", []),
        "evidence_quote": mention.get("evidence_quote", ""),
    }


def _parse_genomics(mention: Dict[str, Any]) -> Dict[str, Any]:
    """
    Parse genomic test and finding from a genomics mention.

    Example: "NGS: EGFR exon 19 deletion"
    → test="NGS", finding="EGFR exon 19 deletion"
    """
    val = mention.get("value", "")

    test = mention.get("label", "genomics")
    finding = None
    m = re.search(r"(?:ngs|wes|fish|ihc)[^\n:]*[:\-]\s*(.+)", val, re.I)
    if m:
        finding = m.group(1).strip()[:300]

    return {
        "test":    test,
        "finding": finding or val,
        "date_text": mention.get("date_text", ""),
        "source_pages": mention.get("source_pages", []),
        "evidence_quote": mention.get("evidence_quote", ""),
    }


def _parse_performance_status(mention: Dict[str, Any]) -> Dict[str, Any]:
    """
    Parse PS/KPS score from a performance status mention.

    Examples:
      "PS 1"   → {"ecog": 1, "kps": null}
      "KPS 80" → {"ecog": null, "kps": 80}
    """
    val = mention.get("value", "")

    ecog, kps = None, None

    m = re.search(r"\bps\s*[-:]?\s*([0-4])\b", val, re.I)
    if m:
        ecog = int(m.group(1))

    m = re.search(r"\bkps\s*(\d{2,3})\b", val, re.I)
    if m:
        kps = int(m.group(1))

    return {
        "ecog":    ecog,
        "kps":     kps,
        "raw_value": val,
        "date_text": mention.get("date_text", ""),
        "source_pages": mention.get("source_pages", []),
    }


def _parse_surgery(mention: Dict[str, Any]) -> Dict[str, Any]:
    """
    Parse surgery/procedure sub-fields from a surgery mention.

    Example: "Right nephrectomy done 12.03.2023"
    → procedure="nephrectomy", laterality="Right", date="12.03.2023"
    """
    val = mention.get("value", "")
    low = val.lower()

    # Procedure type
    procedure = mention.get("label", "surgery")
    for kw in ("nephrectomy", "cystectomy", "prostatectomy", "mastectomy",
               "lobectomy", "colectomy", "gastrectomy", "hepatectomy",
               "pancreatectomy", "thyroidectomy", "adrenalectomy"):
        if kw in low:
            procedure = kw
            break

    # Laterality
    if "bilateral" in low:
        laterality = "Bilateral"
    elif "right" in low or " rt " in low:
        laterality = "Right"
    elif "left" in low or " lt " in low:
        laterality = "Left"
    else:
        laterality = None

    return {
        "procedure":  procedure,
        "laterality": laterality,
        "raw_value":  val,
        "date_text":  mention.get("date_text", ""),
        "certainty":  mention.get("certainty"),
        "source_pages": mention.get("source_pages", []),
        "evidence_quote": mention.get("evidence_quote", ""),
    }


def _parse_radiotherapy(mention: Dict[str, Any]) -> Dict[str, Any]:
    """
    Parse radiotherapy sub-fields.

    Example: "SBRT spine L2 30Gy/5#"
    → modality="SBRT", target="spine L2", dose="30Gy", fractions="5"
    """
    val = mention.get("value", "")
    low = val.lower()

    # Modality
    rt_modality = None
    for kw in ("sbrt", "imrt", "vmat", "igrt", "3dcrt", "brachytherapy", "rt"):
        if kw in low:
            rt_modality = kw.upper()
            break

    # Dose: "30Gy" or "30 Gy"
    dose_rt = None
    m = re.search(r"(\d+(?:\.\d+)?)\s*gy\b", low)
    if m:
        dose_rt = f"{m.group(1)} Gy"

    # Fractions: "5#", "5 fractions", "5fr"
    fractions = None
    m = re.search(r"(\d+)\s*(?:#|fr(?:actions?)?)\b", low)
    if m:
        fractions = int(m.group(1))

    return {
        "modality":  rt_modality,
        "dose":      dose_rt,
        "fractions": fractions,
        "raw_value": val,
        "date_text": mention.get("date_text", ""),
        "source_pages": mention.get("source_pages", []),
        "evidence_quote": mention.get("evidence_quote", ""),
    }


def _parse_generic(mention: Dict[str, Any]) -> Dict[str, Any]:
    """Minimal passthrough for categories without a dedicated parser."""
    return {
        "label":     mention.get("label", ""),
        "value":     mention.get("value", ""),
        "date_text": mention.get("date_text", ""),
        "certainty": mention.get("certainty"),
        "source_pages": mention.get("source_pages", []),
        "evidence_quote": mention.get("evidence_quote", ""),
    }


# Maps category name → its sub-parser function
_CATEGORY_PARSERS = {
    "diagnosis":          _parse_diagnosis,
    "medication":         _parse_medication,
    "imaging":            _parse_imaging,
    "pathology":          _parse_pathology,
    "lab":                _parse_lab,
    "genomics":           _parse_genomics,
    "performance_status": _parse_performance_status,
    "surgery":            _parse_surgery,
    "radiotherapy":       _parse_radiotherapy,
}


# ─────────────────────────────────────────────────────────────────────────────
# Oncology summary builder
# ─────────────────────────────────────────────────────────────────────────────

def _build_oncology_summary(mentions: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Build the oncology_summary section of the deep schema.

    Structure
    ---------
    primary_diagnosis : parsed diagnosis (highest-confidence, first one)
    secondary_diagnoses: remaining diagnosis mentions
    disease_status     : from "status" category mentions
    performance_status : parsed ECOG/KPS
    """
    diag_mentions = [m for m in mentions if m.get("category") == "diagnosis"]
    status_mentions = [m for m in mentions if m.get("category") == "status"]
    ps_mentions = [m for m in mentions if m.get("category") == "performance_status"]

    primary = (
        _safe(_parse_diagnosis, diag_mentions[0]) if diag_mentions else None
    )
    secondary = [
        _safe(_parse_diagnosis, m) for m in diag_mentions[1:]
    ]

    # Merge PS mentions into one record
    ps_record: Dict[str, Any] = {"ecog": None, "kps": None, "mentions": []}
    for m in ps_mentions:
        parsed = _safe(_parse_performance_status, m, default={})
        if parsed.get("ecog") is not None and ps_record["ecog"] is None:
            ps_record["ecog"] = parsed["ecog"]
        if parsed.get("kps") is not None and ps_record["kps"] is None:
            ps_record["kps"] = parsed["kps"]
        ps_record["mentions"].append(parsed)

    return {
        "primary_diagnosis":    primary,
        "secondary_diagnoses":  secondary,
        "disease_status":       [_safe(_parse_generic, m) for m in status_mentions],
        "performance_status":   ps_record,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Treatment section builder
# ─────────────────────────────────────────────────────────────────────────────

def _build_treatment(mentions: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "medications": [
            _safe(_parse_medication, m) for m in mentions if m.get("category") == "medication"
        ],
        "surgery": [
            _safe(_parse_surgery, m) for m in mentions if m.get("category") == "surgery"
        ],
        "radiotherapy": [
            _safe(_parse_radiotherapy, m) for m in mentions if m.get("category") == "radiotherapy"
        ],
        "procedures": [
            _safe(_parse_generic, m) for m in mentions if m.get("category") == "procedure"
        ],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Investigations section builder
# ─────────────────────────────────────────────────────────────────────────────

def _build_investigations(mentions: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "imaging": [
            _safe(_parse_imaging, m) for m in mentions if m.get("category") == "imaging"
        ],
        "pathology": [
            _safe(_parse_pathology, m) for m in mentions if m.get("category") == "pathology"
        ],
        "labs": [
            _safe(_parse_lab, m) for m in mentions if m.get("category") == "lab"
        ],
        "genomics": [
            _safe(_parse_genomics, m) for m in mentions if m.get("category") == "genomics"
        ],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Clinical notes section builder
# ─────────────────────────────────────────────────────────────────────────────

def _build_clinical_notes(mentions: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "symptoms": [
            _safe(_parse_generic, m) for m in mentions if m.get("category") == "symptom"
        ],
        "plan": [
            _safe(_parse_generic, m) for m in mentions if m.get("category") == "plan"
        ],
        "follow_up": [
            _safe(_parse_generic, m) for m in mentions if m.get("category") == "follow_up"
        ],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def _canonical_category(category: Any) -> str:
    value = re.sub(r"[^a-zA-Z0-9]+", "_", str(category or "uncategorized").strip().lower())
    return value.strip("_") or "uncategorized"


def _adaptive_item(mention: Dict[str, Any]) -> Dict[str, Any]:
    parsed = _safe(parse_mention_subfields, mention, default=_parse_generic(mention))
    return {
        "category": _canonical_category(mention.get("category")),
        "label": mention.get("label") or "",
        "value": mention.get("value") or "",
        "normalized_value": mention.get("normalized_value") or mention.get("value") or "",
        "date_text": mention.get("date_text") or "",
        "certainty": mention.get("certainty") or "unknown",
        "attributes": mention.get("attributes") or {},
        "evidence_quote": mention.get("evidence_quote") or "",
        "source_pages": mention.get("source_pages") or [],
        "evidence_ids": mention.get("evidence_ids") or [],
        "evidence_artifact_ids": mention.get("evidence_artifact_ids") or [],
        "origin": mention.get("origin") or "",
        "parsed": parsed,
    }


def _build_adaptive_sections(grouped_record: Dict[str, List]) -> Dict[str, Any]:
    sections: Dict[str, Any] = {}
    for raw_category, mentions in sorted((grouped_record or {}).items()):
        key = _canonical_category(raw_category)
        section_mentions = mentions if isinstance(mentions, list) else []
        sections[key] = {
            "title": str(raw_category or "Uncategorized").replace("_", " ").title(),
            "count": len(section_mentions),
            "items": [_adaptive_item(m) for m in section_mentions if isinstance(m, dict)],
        }
    return sections


def _build_optional_clinical_summary(mentions: List[Dict[str, Any]]) -> Dict[str, Any]:
    clinical_categories = {
        "diagnosis", "medication", "procedure", "imaging", "lab", "pathology",
        "genomics", "symptom", "plan", "follow_up", "radiotherapy", "surgery",
        "status", "performance_status",
    }
    if not {_canonical_category(m.get("category")) for m in mentions}.intersection(clinical_categories):
        return {}
    return {
        "diagnoses": [_adaptive_item(m) for m in mentions if _canonical_category(m.get("category")) == "diagnosis"],
        "treatments": [
            _adaptive_item(m) for m in mentions
            if _canonical_category(m.get("category")) in {"medication", "procedure", "radiotherapy", "surgery"}
        ],
        "investigations": [
            _adaptive_item(m) for m in mentions
            if _canonical_category(m.get("category")) in {"imaging", "lab", "pathology", "genomics"}
        ],
        "notes": [
            _adaptive_item(m) for m in mentions
            if _canonical_category(m.get("category")) in {"symptom", "plan", "follow_up", "status", "performance_status"}
        ],
    }


def build_deep_schema(
    patient_code:   str,
    source_pdf:     str,
    mentions_flat:  List[Dict[str, Any]],
    grouped_record: Dict[str, List],
    traceability:   Dict[str, Any],
    stats:          Dict[str, Any],
    review_flags:   List[str],
    page_count:     int = 0,
    note_count:     int = 0,
) -> Dict[str, Any]:
    """
    Build a complete deep-schema record from pipeline outputs.

    Parameters
    ----------
    patient_code    : unique patient identifier
    source_pdf      : original PDF filename
    mentions_flat   : corrected, merged mention list (from text_corrector)
    grouped_record  : {category: [mentions]} from merger
    traceability    : {source_pages, evidence_ids} from merger
    stats           : {raw_mentions_before_merge, mentions_after_merge, …}
    review_flags    : ["no_mentions_after_merge", …]
    page_count      : total pages processed
    note_count      : total notes segmented

    Returns
    -------
    A nested dict following the adaptive schema:

      identity          : patient/document identifiers and source info
      document_summary  : dynamic category counts from the LLM mentions
      sections          : category-keyed sections built from the actual PDF data
      clinical_summary  : optional convenience grouping when clinical categories exist
      mentions_flat     : complete corrected mention list (preserved)
      grouped_record    : original category-keyed grouping (preserved)
      traceability      : source pages + evidence IDs
      stats             : extraction statistics
      review_flags      : QC flags
      schema_version    : current adaptive schema version
    """
    # Build sections from the categories the LLM actually found.
    sections = _build_adaptive_sections(grouped_record)
    category_counts = {category: section["count"] for category, section in sections.items()}
    clinical_summary = _build_optional_clinical_summary(mentions_flat)

    schema: Dict[str, Any] = {
        # ── Identity ──────────────────────────────────────────────────────
        "identity": {
            "patient_code": patient_code,
            "source_pdf":   source_pdf,
            "page_count":   page_count,
            "note_count":   note_count,
        },

        # ── Clinical sections ─────────────────────────────────────────────
        "document_summary": {
            "category_count": len(sections),
            "mention_count": len(mentions_flat),
            "categories": sorted(sections.keys()),
            "category_counts": category_counts,
        },
        "sections": sections,
        "clinical_summary": clinical_summary,

        # ── Uncategorised mentions ────────────────────────────────────────
        # ── Preserved originals (for backward compatibility) ──────────────
        "mentions_flat":  mentions_flat,
        "grouped_record": grouped_record,

        # ── QC & meta ────────────────────────────────────────────────────
        "traceability":  traceability,
        "stats":         stats,
        "review_flags":  review_flags,
        "schema_version": SCHEMA_VERSION,
    }

    logger.info(
        "Deep schema built for %s: %d mentions → %d sections",
        patient_code,
        len(mentions_flat),
        len(sections),
    )

    return schema


def parse_mention_subfields(mention: Dict[str, Any]) -> Dict[str, Any]:
    """
    Parse sub-fields for a single mention using its category parser.

    Useful for on-demand parsing outside the full schema build
    (e.g. rendering a single mention in the patient detail view).
    """
    category = mention.get("category", "other")
    parser   = _CATEGORY_PARSERS.get(category, _parse_generic)
    return _safe(parser, mention, default=_parse_generic(mention))
