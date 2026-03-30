"""
Text Cleaner — Denoise OCR / native-PDF text before extraction.
================================================================
Applied once per page, immediately after PDF text is obtained.
Goal: remove visual noise without losing any clinical content.

Cleaning pipeline (in order):
  1. Normalise whitespace (tabs → space, multiple blank lines collapsed)
  2. Repair common OCR spacing artifacts ("the ." → "the.")
  3. Collapse duplicate consecutive words ("the the" → "the")
  4. Remove known boilerplate lines (hospital header, page numbers, URLs)
  5. Deduplicate repeated lines within the same block
  6. Final whitespace normalisation

Patterns are compiled once at module import — not on every function call —
so looping over thousands of notes stays fast.
"""

import re
from typing import List


# ─────────────────────────────────────────────────────────────────────────────
# Module-level compiled patterns
# Compiled once here → reused on every clean_text_block() call.
# ─────────────────────────────────────────────────────────────────────────────

# Boilerplate lines to drop entirely (case-insensitive)
_NOISE_PATTERNS: List[re.Pattern] = [
    re.compile(r"tata memorial hospital[- ]electronic medical record", re.I),
    re.compile(r"page\s+\d+\s+of\s+\d+",                              re.I),
    re.compile(r"https?://\S+",                                        re.I),
    re.compile(r"\be-?mail\s*:",                                       re.I),
    re.compile(r"\bfax\s*:",                                           re.I),
]

# Matches "word word" repetition (e.g. "the the", "patient patient")
_DUPLICATE_WORD_RE = re.compile(r"\b(\w+)(\s+\1\b)+", re.I)

# Broken OCR separators: "a | b" or "a ¦ b" → "a b"
_PIPE_SEP_RE = re.compile(r"\s+[|¦]\s+")

# Normalise multiple spaces within a line
_MULTI_SPACE_RE = re.compile(r"[ ]{2,}")

# Collapse 3+ consecutive blank lines to double blank
_MULTI_NL_RE = re.compile(r"\n{3,}")

# Whitespace normalisation for line-key comparison (dedupe check)
_WS_RE = re.compile(r"\s+")


# ─────────────────────────────────────────────────────────────────────────────
# Private helpers
# ─────────────────────────────────────────────────────────────────────────────

def _repair_ocr_spacing(text: str) -> str:
    """
    Fix punctuation spacing errors introduced by OCR segmentation.

    Examples of what gets repaired:
      " ,"  → ","    (space before comma)
      " ."  → "."    (space before period)
      "( "  → "("    (space after open paren)
      " - " → "-"    (spaced hyphens in compound words)
    """
    replacements = (
        (" ,", ","),
        (" .", "."),
        (" ;", ";"),
        (" :", ":"),
        (" )", ")"),
        ("( ", "("),
        (" - ", "-"),
    )
    for src, dst in replacements:
        text = text.replace(src, dst)

    # Inline pipe/broken-bar separators add visual noise and split tokens
    text = _PIPE_SEP_RE.sub(" ", text)
    return text


def _remove_noise_lines(lines: List[str]) -> List[str]:
    """
    Drop any line that matches a known boilerplate pattern.
    Uses pre-compiled _NOISE_PATTERNS for speed.
    """
    out = []
    for line in lines:
        low = line.lower()
        if not any(pat.search(low) for pat in _NOISE_PATTERNS):
            out.append(line)
    return out


def _dedupe_lines(lines: List[str]) -> List[str]:
    """
    Remove exact duplicate lines and consecutive near-duplicate lines.

    Strategy:
    - Normalise each line (collapse whitespace, lowercase) → line_key
    - Keep a *set* of all seen keys → drop exact duplicates anywhere
    - Also track the previous key → drop immediate repetitions even if
      they differ only in casing/spacing (avoids "seen set" false positives
      for legitimately repeated medical phrases across sections)
    """
    cleaned = []
    seen: set = set()
    previous = ""

    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        line_key = _WS_RE.sub(" ", line).lower()

        # Skip if exact duplicate of any previously seen line, or of the
        # immediately preceding line (catches consecutive duplicates).
        if line_key in seen or line_key == previous:
            continue

        seen.add(line_key)
        previous = line_key
        cleaned.append(line)

    return cleaned


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def clean_text_block(text: str) -> str:
    """
    Clean noisy OCR / native-PDF text while preserving clinical meaning.

    Pipeline
    --------
    1. Normalise null bytes, carriage returns, tabs, multiple blank lines.
    2. Repair common OCR spacing artifacts and broken separators.
    3. Collapse duplicated words ("the the" → "the").
    4. Remove boilerplate lines (hospital header, page footer, URLs, fax/email).
    5. Deduplicate repeated lines.
    6. Final whitespace clean-up.

    Parameters
    ----------
    text : raw string from PyMuPDF or DocTR

    Returns
    -------
    Cleaned string, ready for note segmentation.
    """
    if not text:
        return ""

    # Step 1 — normalise raw whitespace
    txt = text.replace("\x00", " ").replace("\r", "\n")
    txt = re.sub(r"[ \t]+", " ", txt)
    txt = _MULTI_NL_RE.sub("\n\n", txt)
    txt = txt.strip()

    # Step 2 — fix OCR spacing artifacts
    txt = _repair_ocr_spacing(txt)

    # Step 3 — collapse duplicate adjacent words
    txt = _DUPLICATE_WORD_RE.sub(r"\1", txt)

    # Steps 4 & 5 — remove noise lines, then deduplicate
    lines = txt.split("\n")
    lines = _remove_noise_lines(lines)
    lines = _dedupe_lines(lines)

    # Step 6 — final normalisation
    txt = "\n".join(lines)
    txt = _MULTI_SPACE_RE.sub(" ", txt)
    txt = _MULTI_NL_RE.sub("\n\n", txt)
    return txt.strip()
