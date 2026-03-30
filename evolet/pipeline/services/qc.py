"""
QC — Quality control metrics for extraction runs.
===================================================
Computes per-patient and aggregate metrics after the merge phase, giving
operators a quick way to identify patients that may need manual review
(zero mentions, missing diagnosis, etc.).

Functions
---------
compute_qc_metrics(final_record)  → per-patient metric dict
compute_run_summary(qc_rows)      → aggregate stats across all patients
"""

import logging
from typing import Any, Dict, List

logger = logging.getLogger("pipeline")


def compute_qc_metrics(final_record: Dict[str, Any]) -> Dict[str, Any]:
    """
    Compute quality metrics for a single patient's final record.

    Checks
    ------
    - Mention count and category count  (low → possible extraction failure)
    - Traceability coverage             (how many pages / notes cited)
    - Review flags                      (from merger, e.g. no_mentions_after_merge)
    - Key category presence             (diagnosis, medication, imaging, pathology)

    Parameters
    ----------
    final_record : dict returned by build_final_record()

    Returns
    -------
    Dict of scalar metrics, ready for the QC summary view or CSV export.
    """
    mentions = final_record.get("mentions", [])
    grouped  = final_record.get("grouped_record", {})
    trace    = final_record.get("traceability", {})
    stats    = final_record.get("stats", {})

    return {
        "patient_code":             final_record.get("patient_code", ""),
        "source_pdf":               final_record.get("source_pdf", ""),
        # Core extraction counts
        "mention_count":            len(mentions),
        "category_count":           len(grouped),
        # Traceability — how much of the document was cited
        "trace_pages":              len(trace.get("source_pages", [])),
        "trace_evidence_ids":       len(trace.get("evidence_ids", [])),
        # Review flags from the merger (non-zero = needs human attention)
        "review_flags":             len(final_record.get("review_flags", [])),
        # Raw vs. merged counts for dedup efficiency insight
        "raw_mentions_before_merge":stats.get("raw_mentions_before_merge", 0),
        "mentions_after_merge":     stats.get("mentions_after_merge", 0),
        # List of extracted categories (useful for filtering in the UI)
        "categories":               list(grouped.keys()),
        # Presence of the four most clinically important categories
        "has_diagnosis":  "diagnosis"  in grouped,
        "has_medication": "medication" in grouped,
        "has_imaging":    "imaging"    in grouped,
        "has_pathology":  "pathology"  in grouped,
    }


def compute_run_summary(qc_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Compute aggregate quality statistics across all patients in a run.

    Parameters
    ----------
    qc_rows : list of dicts returned by compute_qc_metrics()

    Returns
    -------
    Dict of run-level aggregate stats for display on the QC summary page.
    """
    if not qc_rows:
        return {"total_patients": 0}

    total          = len(qc_rows)
    mention_counts = [r["mention_count"] for r in qc_rows]

    return {
        "total_patients":           total,
        "total_mentions":           sum(mention_counts),
        "avg_mentions_per_patient": round(sum(mention_counts) / total, 1),
        "min_mentions":             min(mention_counts),
        "max_mentions":             max(mention_counts),
        # Patients with at least one review flag → likely need inspection
        # bool is a subclass of int (True=1, False=0) so sum() works directly
        "patients_with_flags":      sum(r["review_flags"] > 0   for r in qc_rows),
        # Coverage across the four key clinical categories
        "patients_with_diagnosis":  sum(r["has_diagnosis"]       for r in qc_rows),
        "patients_with_medication": sum(r["has_medication"]      for r in qc_rows),
        "patients_with_imaging":    sum(r["has_imaging"]         for r in qc_rows),
        "patients_with_pathology":  sum(r["has_pathology"]       for r in qc_rows),
    }
