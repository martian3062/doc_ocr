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
import re
from collections import defaultdict
from difflib import SequenceMatcher
from typing import Any, Dict, List

from .pdf_extractor import normalize_text

logger = logging.getLogger("pipeline")


def _list_union(*values) -> List:
    merged = set()
    for value in values:
        if isinstance(value, (list, tuple, set)):
            merged.update(item for item in value if item not in ("", None))
        elif value not in ("", None):
            merged.add(value)
    return sorted(merged)


def _compact_key(value: Any) -> str:
    text = normalize_text(str(value or "")).lower()
    text = re.sub(r"\b(uncertain|possible|confirmed|diagnosis|on|admission|final|provisional)\b", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _dose_key(attributes: Dict[str, Any]) -> str:
    dose = _compact_key(attributes.get("dose") or attributes.get("dosage") or "")
    route = _compact_key(attributes.get("route") or "")
    frequency = _compact_key(attributes.get("frequency_or_time") or attributes.get("frequency") or "")
    return "|".join(part for part in [dose, route, frequency] if part)


def _semantic_key(mention: Dict[str, Any]) -> str:
    category = _compact_key(mention.get("category") or "unknown")
    attrs = mention.get("attributes") if isinstance(mention.get("attributes"), dict) else {}
    if category == "medication":
        drug = _compact_key(attrs.get("drug_name") or mention.get("normalized_value") or "")
        dose = _dose_key(attrs)
        if drug:
            return f"{category}|{drug}|{dose}"
    value = _compact_key(mention.get("normalized_value") or mention.get("value") or "")
    label = _compact_key(mention.get("label") or "")
    if value:
        return f"{category}|{value}"
    return f"{category}|{label}"


def _looks_duplicate(left: Dict[str, Any], right: Dict[str, Any]) -> bool:
    if _compact_key(left.get("category")) != _compact_key(right.get("category")):
        return False
    left_key = _semantic_key(left)
    right_key = _semantic_key(right)
    if left_key == right_key:
        return True
    left_value = _compact_key(left.get("normalized_value") or left.get("value"))
    right_value = _compact_key(right.get("normalized_value") or right.get("value"))
    if not left_value or not right_value:
        return False
    shorter, longer = sorted([left_value, right_value], key=len)
    if len(shorter) >= 14 and shorter in longer:
        return True
    return SequenceMatcher(None, left_value, right_value).ratio() >= 0.9


def _merge_duplicate_into(existing: Dict[str, Any], duplicate: Dict[str, Any]) -> Dict[str, Any]:
    existing["source_pages"] = _list_union(existing.get("source_pages"), duplicate.get("source_pages"))
    existing["evidence_ids"] = _list_union(existing.get("evidence_ids"), duplicate.get("evidence_ids"))
    existing["evidence_artifact_ids"] = _list_union(
        existing.get("evidence_artifact_ids"),
        duplicate.get("evidence_artifact_ids"),
    )
    existing["origins"] = _list_union(existing.get("origins"), duplicate.get("origins"))
    existing["duplicate_count"] = int(existing.get("duplicate_count") or 1) + int(duplicate.get("duplicate_count") or 1)

    existing_attrs = existing.get("attributes") if isinstance(existing.get("attributes"), dict) else {}
    duplicate_attrs = duplicate.get("attributes") if isinstance(duplicate.get("attributes"), dict) else {}
    existing["attributes"] = {**duplicate_attrs, **existing_attrs}

    for field in ("value", "normalized_value", "evidence_quote"):
        current = normalize_text(existing.get(field, ""))
        incoming = normalize_text(duplicate.get(field, ""))
        if incoming and len(incoming) > len(current):
            existing[field] = incoming
    return existing


def clean_duplicate_mentions(mentions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Collapse near-identical schema items after exact-key merging."""
    cleaned: List[Dict[str, Any]] = []
    for mention in mentions:
        match = next((item for item in cleaned if _looks_duplicate(item, mention)), None)
        if match:
            _merge_duplicate_into(match, mention)
        else:
            mention.setdefault("duplicate_count", 1)
            cleaned.append(mention)
    cleaned.sort(key=lambda x: (x["category"], x["date_text"], x["label"], x["normalized_value"]))
    return cleaned


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
            "evidence_artifact_ids": sorted(set(m.get("evidence_artifact_ids", []))),
            "evidence_quote":   normalize_text(m.get("evidence_quote", "")),
            "origins":          [m.get("origin", "unknown")],
            "duplicate_count":   1,
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
            existing["evidence_artifact_ids"] = sorted(
                set(existing.get("evidence_artifact_ids", [])) | set(entry["evidence_artifact_ids"])
            )
            existing["origins"] = sorted(
                set(existing["origins"]) | set(entry["origins"])
            )
            existing["duplicate_count"] = int(existing.get("duplicate_count") or 1) + 1
            # Prefer keeping existing non-empty evidence_quote / attributes
            if not existing["evidence_quote"] and entry["evidence_quote"]:
                existing["evidence_quote"] = entry["evidence_quote"]
            if not existing["attributes"] and entry["attributes"]:
                existing["attributes"] = entry["attributes"]

    # Sort for deterministic output
    out = list(merged.values())
    out.sort(key=lambda x: (x["category"], x["date_text"], x["label"], x["normalized_value"]))
    return clean_duplicate_mentions(out)


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
