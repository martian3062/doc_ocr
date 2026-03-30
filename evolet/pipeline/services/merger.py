"""
Merger — Consolidate and deduplicate mentions into patient-level records.
=========================================================================
After regex + LLM extraction, each note may have produced overlapping
mentions (same diagnosis mentioned on three pages, same drug in two notes).
This module collapses those duplicates and assembles the final record
structure that gets written to FinalRecord in the database.

Public API
----------
merge_mentions(mentions)         → deduplicated, sorted list of mentions
group_mentions(mentions)         → {category: [mentions]} dict
build_final_record(...)          → complete FinalRecord payload dict

Deduplication key
-----------------
(category.lower(), label.lower(), normalized_value.lower(), date_text.lower())

When two mentions share the same key, their source_pages, evidence_ids,
and origins lists are merged (union).  The first non-empty evidence_quote
and attributes dict are kept.
"""

import logging
from collections import defaultdict
from typing import Any, Dict, List

from .pdf_extractor import normalize_text

logger = logging.getLogger("pipeline")


# ─────────────────────────────────────────────────────────────────────────────
# Core deduplication
# ─────────────────────────────────────────────────────────────────────────────

def merge_mentions(mentions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Deduplicate and merge a flat list of mention dicts.

    Algorithm
    ---------
    1. Build a dedup key: (category, label, normalized_value, date_text)
       all lowercased for case-insensitive matching.
    2. First occurrence of a key seeds the merged dict.
    3. Subsequent occurrences with the same key:
       - Union source_pages and evidence_ids (both stay sorted)
       - Union origins list (tracks "regex", "llm", or both)
       - Fill in missing evidence_quote / attributes from the duplicate
         if the primary entry has empty fields.
    4. Sort the output by (category, date_text, label, normalized_value)
       for deterministic, human-readable ordering.

    Parameters
    ----------
    mentions : flat list from regex + LLM extraction (may contain dicts
               from DB .values() queries — all fields expected)

    Returns
    -------
    Sorted, deduplicated list of mention dicts.
    """
    merged: Dict[tuple, Dict[str, Any]] = {}

    for m in mentions:
        if not isinstance(m, dict):
            continue

        # Normalise all string fields before keying
        category   = normalize_text(m.get("category", "")) or "unknown"
        label      = normalize_text(m.get("label", ""))
        norm_value = normalize_text(m.get("normalized_value", "")) or normalize_text(m.get("value", ""))
        date_text  = normalize_text(m.get("date_text", ""))

        key = (category.lower(), label.lower(), norm_value.lower(), date_text.lower())

        # Build the canonical mention for this key
        entry: Dict[str, Any] = {
            "category":         category,
            "label":            label or category,
            "value":            normalize_text(m.get("value", "")) or norm_value,
            "normalized_value": norm_value,
            "date_text":        date_text,
            "certainty":        str(m.get("certainty", "unknown")).lower(),
            "attributes":       m.get("attributes", {}) if isinstance(m.get("attributes", {}), dict) else {},
            "source_pages":     sorted(set(m.get("source_pages", []))),
            "evidence_ids":     sorted(set(m.get("evidence_ids", []))),
            "evidence_quote":   normalize_text(m.get("evidence_quote", "")),
            "origins":          [m.get("origin", "unknown")],
        }

        if key not in merged:
            merged[key] = entry
        else:
            # Merge into the existing entry
            existing = merged[key]
            existing["source_pages"] = sorted(
                set(existing["source_pages"]) | set(entry["source_pages"])
            )
            existing["evidence_ids"] = sorted(
                set(existing["evidence_ids"]) | set(entry["evidence_ids"])
            )
            existing["origins"] = sorted(
                set(existing["origins"]) | set(entry["origins"])
            )
            # Prefer keeping existing non-empty evidence_quote / attributes
            if not existing["evidence_quote"] and entry["evidence_quote"]:
                existing["evidence_quote"] = entry["evidence_quote"]
            if not existing["attributes"] and entry["attributes"]:
                existing["attributes"] = entry["attributes"]

    # Sort for deterministic output
    out = list(merged.values())
    out.sort(key=lambda x: (x["category"], x["date_text"], x["label"], x["normalized_value"]))
    return out


def group_mentions(
    mentions: List[Dict[str, Any]],
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Group a flat mention list by category.

    Returns an alphabetically sorted dict so template rendering is stable:
      {"diagnosis": [...], "imaging": [...], "medication": [...], …}
    """
    groups: Dict[str, List] = defaultdict(list)
    for m in mentions:
        groups[m["category"]].append(m)
    return dict(sorted(groups.items()))


# ─────────────────────────────────────────────────────────────────────────────
# Final record assembly
# ─────────────────────────────────────────────────────────────────────────────

def build_final_record(
    patient_code: str,
    source_pdf: str,
    page_count: int,
    note_count: int,
    all_mentions: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Build the complete patient-level output record after all extractions.

    Steps
    -----
    1. Merge and deduplicate all mentions.
    2. Group the merged mentions by category.
    3. Compute aggregate traceability (all referenced pages + evidence IDs).
    4. Compute stats (raw vs. merged count, category count).
    5. Flag the record for review if no mentions survived the merge.

    This dict is what gets serialised to FinalRecord.grouped_record etc.
    in the database and to JSON on download.

    Returns
    -------
    A dict with keys: patient_code, source_pdf, page_count, note_count,
    mentions, grouped_record, review_flags, traceability, stats.
    """
    merged  = merge_mentions(all_mentions)
    grouped = group_mentions(merged)

    # Flag for human review if extraction produced nothing useful
    review_flags = []
    if not merged:
        review_flags.append("no_mentions_after_merge")

    # Aggregate traceability across all merged mentions
    all_pages   = sorted({p for m in merged for p in m.get("source_pages", [])})
    all_evidence = sorted({e for m in merged for e in m.get("evidence_ids", [])})

    return {
        "patient_code":   patient_code,
        "source_pdf":     source_pdf,
        "page_count":     page_count,
        "note_count":     note_count,
        "mentions":       merged,
        "grouped_record": grouped,
        "review_flags":   review_flags,
        "traceability": {
            "source_pages":  all_pages,
            "evidence_ids":  all_evidence,
        },
        "stats": {
            "raw_mentions_before_merge": len(all_mentions),
            "mentions_after_merge":      len(merged),
            "categories_after_merge":    len(grouped),
        },
    }
