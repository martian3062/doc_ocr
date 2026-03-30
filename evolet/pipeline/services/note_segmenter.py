"""
Note Segmenter — Split page text into clinical notes, score, and triage.
=========================================================================
A "note" in this pipeline is the smallest unit of clinical text that is
sent to either regex extraction or the LLM.  One PDF page typically
contains 1–4 notes, separated by section headers (DATE:, CLINICAL NOTE
DETAILS, REMARKS DETAILS, etc.).

Module responsibilities
-----------------------
1. split_into_notes()       — regex-split a page into note-sized chunks
2. segment_pages_to_notes() — run (1) over all pages + deduplicate by hash
3. triage_notes()           — classify notes as resolved (regex-only) or
                              unresolved (needs LLM)

Supporting utilities
--------------------
extract_dates()             — pull date strings from text
is_low_value_note()         — detect boilerplate / too-short notes
clinical_signal_score()     — count clinical keyword hits → signal score

Optimisation note
-----------------
The section-split regex and all hint/date/low-value patterns are compiled
once at module level (not rebuilt per note call) to avoid repeated
re.compile() overhead when processing hundreds of notes per PDF.
"""

import re
import logging
from typing import Any, Dict, List

from . import config
from .pdf_extractor import normalize_text, word_count, text_hash

logger = logging.getLogger("pipeline")


# ─────────────────────────────────────────────────────────────────────────────
# Module-level compiled patterns
# Building these once avoids re.compile() on every note (100s per PDF).
# ─────────────────────────────────────────────────────────────────────────────

# Combined section-split pattern — matches any section header that marks the
# start of a new clinical note within a page
_SPLIT_RE: re.Pattern = re.compile(
    "(" + "|".join(config.SECTION_SPLIT_PATTERNS) + ")",
    flags=re.I,
)

# Pre-compiled low-value and hint patterns (sourced from config)
# config._LOW_VALUE_RE and config._HINT_RE are already compiled lists;
# import them here for clarity.
_LOW_VALUE_RE = config._LOW_VALUE_RE
_HINT_RE      = config._HINT_RE
_DATE_RE      = config._DATE_RE


# ─────────────────────────────────────────────────────────────────────────────
# Utility helpers
# ─────────────────────────────────────────────────────────────────────────────

def extract_dates(text: str) -> List[str]:
    """
    Find all date strings in *text* using the patterns from config.

    Returns a deduplicated list preserving the order of first appearance.
    Used both here (signal scoring) and by regex_extractor for date_text.
    """
    hits = []
    for pat in _DATE_RE:
        hits.extend(pat.findall(text))

    # Deduplicate while preserving order
    seen: set = set()
    unique: List[str] = []
    for x in hits:
        if x not in seen:
            unique.append(x)
            seen.add(x)
    return unique


def is_low_value_note(text: str) -> bool:
    """
    Return True if this note is not worth extracting from.

    A note is low-value if:
    - It has fewer than NOTE_MIN_WORDS words (too short to contain mentions)
    - Two or more boilerplate patterns match (hospital header, fax, URL…)
    - It looks like a prescription footer (< 45 words, prescription phrase)
    """
    t = normalize_text(text).lower()

    if word_count(t) < config.NOTE_MIN_WORDS:
        return True

    # Count boilerplate pattern hits
    hits = sum(1 for pat in _LOW_VALUE_RE if pat.search(t))
    if hits >= 2:
        return True

    # Prescription footers: short note that mentions "prescription has been issued"
    if "prescription has been issued" in t and word_count(t) < 45:
        return True

    return False


def clinical_signal_score(text: str) -> int:
    """
    Heuristic score estimating how clinically rich a note is.

    Components (all additive):
    - Length bonus: up to +4 for long notes (every 400 chars)
    - Date bonus:   up to +3 for notes with multiple dates
    - Keyword hits: +1 for each clinical keyword found (diagnosis, stage, etc.)

    Higher scores → more likely to contain extractable mentions →
    prioritised for LLM processing.
    """
    t = normalize_text(text).lower()
    score = 0

    # Length signal: longer notes tend to have more content
    score += min(len(t) // 400, 4)

    # Date signal: clinical notes usually reference specific dates
    score += min(len(extract_dates(t)), 3)

    # Clinical keyword hits (each CLINICAL_HINT_PATTERNS entry contributes 1)
    score += sum(1 for pat in _HINT_RE if pat.search(t))

    return score


# ─────────────────────────────────────────────────────────────────────────────
# Core segmentation
# ─────────────────────────────────────────────────────────────────────────────

def split_into_notes(page_num: int, text: str) -> List[Dict[str, Any]]:
    """
    Split a single page's text into note-sized units.

    Algorithm
    ---------
    1. Split on section-header patterns (DATE:, CLINICAL NOTE DETAILS, etc.)
       using a capturing group so the header itself is kept with the note.
    2. Re-merge adjacent chunks: the header chunk starts a new note; all
       non-header chunks are appended to the current note buffer.
    3. Discard notes shorter than NOTE_MIN_WORDS words.
    4. Annotate each note with its hash, extracted dates, signal score,
       and low-value flag.

    Returns
    -------
    List of note dicts:
      page_num  : int   — source page (1-based)
      note_ix   : int   — 1-based index within the page
      note_id   : str   — stable ID like "p0001_n002"
      text      : str
      hash      : str   — MD5 of normalised text (for dedup)
      dates     : list[str]
      signal    : int   — clinical_signal_score
      low_value : bool
    """
    text = normalize_text(text)
    if not text:
        return []

    # Split while keeping delimiters (capturing group)
    parts = _SPLIT_RE.split(text)

    # Re-merge: group header + its following content into one note buffer
    merged: List[str] = []
    current = ""
    for chunk in parts:
        chunk = normalize_text(chunk)
        if not chunk:
            continue
        if _SPLIT_RE.match(chunk):
            # Flush the previous buffer before starting a new note
            if current.strip():
                merged.append(current.strip())
            current = chunk
        else:
            # Append non-header content to the current note
            current = (current + "\n" + chunk).strip() if current else chunk

    if current.strip():
        merged.append(current.strip())

    # Build output list
    out: List[Dict[str, Any]] = []
    for i, note_text in enumerate(merged, start=1):
        note_text = normalize_text(note_text)
        if word_count(note_text) < config.NOTE_MIN_WORDS:
            continue   # too short — skip
        out.append({
            "page_num":  page_num,
            "note_ix":   i,
            "note_id":   f"p{page_num:04d}_n{i:03d}",
            "text":      note_text,
            "hash":      text_hash(note_text),
            "dates":     extract_dates(note_text),
            "signal":    clinical_signal_score(note_text),
            "low_value": is_low_value_note(note_text),
        })
    return out


def segment_pages_to_notes(
    page_rows: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Convert a list of page-row dicts into deduplicated note-level units.

    Iterates over every page, calls split_into_notes(), and skips any
    note whose MD5 hash has already been seen (controlled by DEDUP_EXACT_NOTES).

    Deduplication is important because clinical notes often span multiple
    pages or are repeated in summary pages — processing the same note twice
    wastes GPU time and creates duplicate mentions.

    Parameters
    ----------
    page_rows : output of extract_pdf_pages()

    Returns
    -------
    List of deduplicated note dicts ready for triage.
    """
    notes: List[Dict[str, Any]] = []
    seen_hashes: set = set()

    for row in page_rows:
        for note in split_into_notes(row["page_num"], row["text"]):
            if config.DEDUP_EXACT_NOTES and note["hash"] in seen_hashes:
                continue   # exact duplicate — skip
            seen_hashes.add(note["hash"])
            notes.append(note)

    return notes


# ─────────────────────────────────────────────────────────────────────────────
# Triage
# ─────────────────────────────────────────────────────────────────────────────

def triage_notes(
    notes: List[Dict[str, Any]],
    regex_results: Dict[str, List],
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Classify notes into two buckets:

    resolved   → regex extraction was sufficient; no LLM needed.
    unresolved → note has high clinical signal but regex found too few
                 mentions; send to the LLM for deep extraction.

    Unresolved notes are sorted by signal score (desc) and capped at
    MAX_NOTES_PER_PATIENT_FOR_LLM to prevent runaway GPU usage.

    Parameters
    ----------
    notes         : from segment_pages_to_notes()
    regex_results : {note_id: [mention_dicts]} from extract_all_notes()

    Returns
    -------
    {"resolved": [...], "unresolved": [...]}
    """
    resolved:   List[Dict[str, Any]] = []
    unresolved: List[Dict[str, Any]] = []

    for note in notes:
        note_id        = note["note_id"]
        regex_mentions = regex_results.get(note_id, [])

        if _note_needs_llm(note, regex_mentions):
            unresolved.append({**note, "regex_mentions": regex_mentions})
        else:
            resolved.append({**note, "mentions": regex_mentions})

    # Prioritise notes with strongest clinical signal; apply safety cap
    unresolved.sort(
        key=lambda x: (x.get("signal", 0), word_count(x["text"])),
        reverse=True,
    )
    unresolved = unresolved[: config.MAX_NOTES_PER_PATIENT_FOR_LLM]

    return {"resolved": resolved, "unresolved": unresolved}


def _note_needs_llm(note: Dict[str, Any], regex_mentions: List) -> bool:
    """
    Decide whether a single note should be sent to the LLM.

    Returns False (skip LLM) when:
    - The note is flagged as low-value (boilerplate, too short)
    - Clinical signal is below the minimum threshold
    - Regex already found enough mentions (MIN_REGEX_MENTIONS_TO_SKIP_LLM)
    - Note is very short (< 40 words) and SEND_SHORT_NOTES_TO_LLM is False

    Returns True (send to LLM) otherwise.
    """
    if note.get("low_value"):
        return False
    if note.get("signal", 0) < config.MIN_SIGNAL_FOR_LLM:
        return False
    if regex_mentions and len(regex_mentions) >= config.MIN_REGEX_MENTIONS_TO_SKIP_LLM:
        return False
    if not config.SEND_SHORT_NOTES_TO_LLM and word_count(note["text"]) < 40:
        return False
    return True
