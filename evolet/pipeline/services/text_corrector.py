"""
Text Corrector — Spell-check and grammar-clean extracted mention values.
=========================================================================
This layer runs AFTER regex + LLM extraction and BEFORE schema building.

Problem it solves
-----------------
Extracted `value`, `label`, and `evidence_quote` fields often carry
OCR-introduced typos and spacing errors:
    "carcinorma"  →  "carcinoma"
    "nephrect omy" →  "nephrectomy"
    "sunitinlb"   →  "sunitinib"

Strategy (three-pass pipeline per text field)
----------------------------------------------
Pass 1 — Rule-based pre-fixes   (fast, zero dependencies)
    Fix specific OCR patterns: broken hyphens, double spaces,
    capitalisation of first word, stray punctuation.

Pass 2 — SymSpell spell correction   (fast, pure Python)
    Uses symmetric-delete algorithm (symspellpy library).
    Medical terms and abbreviations are whitelisted so they are NEVER
    "corrected" to common English words.
    Falls back gracefully if symspellpy is not installed.

Pass 3 — Rule-based post-fixes  (fast, zero dependencies)
    Capitalise sentence starts, normalise units (mg, mcg, OD, BD…).

What is NOT corrected
---------------------
- The `category` field  — controlled vocabulary, must not change
- The `origin` field    — "regex" / "llm" / "merged"
- Source pages / evidence IDs — never text
- Fields shorter than 4 characters — too risky to auto-correct

Usage
-----
    from pipeline.services.text_corrector import correct_all_mentions
    clean_mentions = correct_all_mentions(raw_mentions)
"""

import re
import logging
from copy import deepcopy
from typing import Any, Dict, List, Optional

logger = logging.getLogger("pipeline")

# ── SymSpell optional import ──────────────────────────────────────────────────
try:
    from symspellpy import SymSpell, Verbosity as SymVerbosity
    _SYMSPELL_AVAILABLE = True
except ImportError:
    _SYMSPELL_AVAILABLE = False
    logger.info("symspellpy not installed — spell correction will use rules only")


# ─────────────────────────────────────────────────────────────────────────────
# Medical term whitelist
# These words must NEVER be "corrected" — they are valid clinical/drug terms
# that spell checkers would otherwise mangle.
# ─────────────────────────────────────────────────────────────────────────────
_MEDICAL_WHITELIST = {
    # Oncology / pathology
    "carcinoma", "adenocarcinoma", "squamous", "metastasis", "metastatic",
    "nephrectomy", "nephrectomised", "lymphadenopathy", "lymphadenopathies",
    "lymphoma", "sarcoma", "melanoma", "thymoma", "glioma", "glioblastoma",
    "hepatocellular", "cholangiocarcinoma", "papillary", "follicular",
    "medullary", "anaplastic", "oncocytoma", "chromophobe", "transitional",
    "urothelial", "seminoma", "teratoma", "mesothelioma",
    "histopathology", "histopathological", "cytology", "biopsy",
    "immunohistochemistry", "ihc", "hpr", "ngs", "wes", "rna",
    "egfr", "alk", "ros1", "braf", "kras", "pik3ca", "her2", "pdl1",
    "msi", "tmb", "brca", "brca1", "brca2", "tp53", "vegf", "pdgfr",
    "rcc", "nsclc", "sclc", "hcc", "crc", "cca", "ucc", "mcc",
    # Staging / grading
    "tnm", "fuhrman", "isup",
    # Drugs
    "sunitinib", "everolimus", "pazopanib", "nivolumab", "pembrolizumab",
    "cabozantinib", "axitinib", "sorafenib", "lenvatinib", "regorafenib",
    "bevacizumab", "cetuximab", "trastuzumab", "pertuzumab",
    "capecitabine", "gemcitabine", "cisplatin", "carboplatin", "oxaliplatin",
    "paclitaxel", "docetaxel", "irinotecan", "etoposide", "vinorelbine",
    "cyclophosphamide", "doxorubicin", "epirubicin", "vincristine",
    "methotrexate", "fluorouracil", "leucovorin", "imatinib", "dasatinib",
    "erlotinib", "gefitinib", "osimertinib", "alectinib", "crizotinib",
    "vemurafenib", "dabrafenib", "trametinib", "ipilimumab", "atezolizumab",
    "durvalumab", "avelumab", "olaparib", "niraparib", "rucaparib",
    "palbociclib", "ribociclib", "abemaciclib", "letrozole", "anastrozole",
    "exemestane", "tamoxifen", "fulvestrant", "enzalutamide", "abiraterone",
    "prednisolone", "dexamethasone", "methylprednisolone", "hydrocortisone",
    "zoledronic", "denosumab", "filgrastim", "pegfilgrastim",
    # Imaging modalities
    "cect", "petct", "fdgpet", "spect", "fnac", "mri", "hrct", "sbrt",
    # Abbreviations / dosing
    "od", "bd", "tds", "qid", "sos", "hs", "ac", "pc",
    "mg", "mcg", "gm", "ml", "iu", "mci", "gy", "cgy",
    "ps", "kps", "ecog",
    # Anatomy
    "paraaortic", "peripancreatic", "retroperitoneal", "mediastinal",
    "supraclavicular", "infraclavicular", "axillary", "inguinal",
    "hilar", "subcarinal", "paratracheal", "paraesophageal",
    # Hospital / report
    "tata", "tmh", "hpr", "opd", "ipd", "icu", "ot",
}

# ── Compiled patterns for rule-based fixes ───────────────────────────────────

# Broken hyphenated words: "nephrect- omy" → "nephrectomy"
_BROKEN_HYPHEN_RE = re.compile(r"(\w+)-\s+(\w+)")

# Multiple spaces within a value
_MULTI_SPACE_RE = re.compile(r" {2,}")

# Hanging punctuation at start: ": value" → "value"
_LEADING_PUNCT_RE = re.compile(r"^[\s:;\-|,\.]+")

# Common unit normalisation map (lowercase key → display form)
_UNIT_NORM = {
    " mg ": " mg ", " mcg ": " mcg ", " gm ": " gm ",
    " ml ": " mL ", " iu ": " IU ", " gy ": " Gy ", " cgy ": " cGy ",
}


# ─────────────────────────────────────────────────────────────────────────────
# SymSpell singleton loader
# ─────────────────────────────────────────────────────────────────────────────

_sym_spell: Optional[Any] = None


def _get_symspell() -> Optional[Any]:
    """
    Lazy-load a SymSpell instance with the English frequency dictionary.

    Uses max_edit_distance=2 which catches most single-character OCR errors
    (dropped letter, swapped letter, misread character) without over-correcting.

    Returns None if symspellpy is not installed or dictionary load fails.
    """
    global _sym_spell
    if _sym_spell is not None:
        return _sym_spell
    if not _SYMSPELL_AVAILABLE:
        return None

    try:
        import pkg_resources
        sym = SymSpell(max_dictionary_edit_distance=2, prefix_length=7)

        # symspellpy ships a frequency dictionary — load it from the package
        dict_path = pkg_resources.resource_filename(
            "symspellpy", "frequency_dictionary_en_82_765.txt"
        )
        sym.load_dictionary(dict_path, term_index=0, count_index=1)
        _sym_spell = sym
        logger.info("SymSpell dictionary loaded (%d entries)", len(sym.words))
    except Exception as exc:
        logger.warning("SymSpell load failed: %s — using rules only", exc)
        _sym_spell = None

    return _sym_spell


# ─────────────────────────────────────────────────────────────────────────────
# Correction passes
# ─────────────────────────────────────────────────────────────────────────────

def _rule_pre_fix(text: str) -> str:
    """
    Pass 1 — fast rule-based pre-processing before spell check.

    Fixes:
    - Broken hyphenated words ("nephrect- omy" → "nephrectomy")
    - Multiple internal spaces
    - Leading punctuation/whitespace artefacts
    """
    text = text.strip()
    text = _BROKEN_HYPHEN_RE.sub(r"\1\2", text)      # rejoin split words
    text = _MULTI_SPACE_RE.sub(" ", text)              # collapse spaces
    text = _LEADING_PUNCT_RE.sub("", text).strip()     # strip leading junk
    return text


def _symspell_correct(text: str) -> str:
    """
    Pass 2 — SymSpell word-level spell correction.

    Each word is:
    1. Checked against the medical whitelist → kept unchanged if found
    2. Short (≤ 3 chars) or numeric → kept unchanged
    3. Otherwise → SymSpell closest suggestion (edit distance ≤ 2)

    CLOSEST suggestion is chosen (Verbosity.CLOSEST) to avoid changing
    a plausibly correct word into something completely different.
    """
    sym = _get_symspell()
    if sym is None:
        return text

    words = text.split()
    corrected = []

    for word in words:
        # Strip trailing punctuation for lookup, preserve it after
        suffix = ""
        clean = word
        m = re.match(r"^(\w[\w\-]*)([^\w]*)$", word)
        if m:
            clean, suffix = m.group(1), m.group(2)

        # Never correct: whitelisted medical terms, abbreviations, short tokens,
        # numbers, and words that already look properly formatted (have capitals)
        if (
            clean.lower() in _MEDICAL_WHITELIST
            or len(clean) <= 3
            or re.fullmatch(r"\d+[\w.]*", clean)       # numbers / doses
            or re.search(r"\d", clean)                  # contains digit
            or clean[0].isupper() and len(clean) <= 6  # uppercase abbreviation
        ):
            corrected.append(word)
            continue

        # Run SymSpell lookup
        suggestions = sym.lookup(
            clean.lower(), SymVerbosity.CLOSEST, max_edit_distance=2
        )

        if suggestions and suggestions[0].term != clean.lower():
            # Only accept if edit distance is 1–2 AND the correction looks
            # plausibly better (longer common words are more likely correct)
            suggestion = suggestions[0].term
            # Preserve original capitalisation
            if clean[0].isupper():
                suggestion = suggestion.capitalize()
            corrected.append(suggestion + suffix)
        else:
            corrected.append(word)

    return " ".join(corrected)


def _rule_post_fix(text: str) -> str:
    """
    Pass 3 — light post-processing after spell correction.

    - Capitalise first character of the string
    - Normalise common medical unit formatting
    """
    if not text:
        return text

    # Capitalise first letter
    text = text[0].upper() + text[1:]

    # Normalise units (e.g. " ml " → " mL ")
    for src, dst in _UNIT_NORM.items():
        text = text.replace(src, dst)

    return text.strip()


# ─────────────────────────────────────────────────────────────────────────────
# Fields we run correction on
# ─────────────────────────────────────────────────────────────────────────────

# These fields contain free-text extracted from clinical notes — correction is
# safe and useful.  Other fields (category, origin, source_pages…) are
# structured/controlled and must not be touched.
_CORRECTABLE_FIELDS = ("value", "normalized_value", "label", "evidence_quote")

# Minimum length — strings shorter than this are likely abbreviations or
# codes that should not be spell-checked (e.g. "PS 1", "RT", "OD")
_MIN_CORRECTION_LEN = 8


def _correct_text(text: str) -> str:
    """
    Run the full three-pass correction pipeline on a single text string.

    Returns the original string unchanged if it is too short to safely
    correct (reduces the risk of mangling clinical abbreviations).
    """
    if not text or len(text.strip()) < _MIN_CORRECTION_LEN:
        return text

    text = _rule_pre_fix(text)
    text = _symspell_correct(text)
    text = _rule_post_fix(text)
    return text


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def correct_mention(mention: Dict[str, Any]) -> Dict[str, Any]:
    """
    Run spelling / grammar correction on the free-text fields of a single
    mention dict.  Returns a new dict (original is not mutated).

    Only `value`, `normalized_value`, `label`, and `evidence_quote` are
    corrected.  All structural fields (category, origin, source_pages, etc.)
    are copied through unchanged.
    """
    corrected = deepcopy(mention)
    for field in _CORRECTABLE_FIELDS:
        raw = corrected.get(field, "")
        if isinstance(raw, str):
            corrected[field] = _correct_text(raw)
    return corrected


def correct_all_mentions(
    mentions: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Apply spell/grammar correction to every mention in the list.

    This is the main entry point called by the orchestrator after the
    merge phase and before schema building.

    Parameters
    ----------
    mentions : list of merged mention dicts from merger.merge_mentions()

    Returns
    -------
    New list of corrected mention dicts (originals not mutated).
    """
    if not mentions:
        return mentions

    corrected = [correct_mention(m) for m in mentions]

    changed = sum(
        1 for orig, corr in zip(mentions, corrected)
        if any(orig.get(f) != corr.get(f) for f in _CORRECTABLE_FIELDS)
    )
    if changed:
        logger.info("Text corrector: %d/%d mentions had corrections applied",
                    changed, len(mentions))

    return corrected
