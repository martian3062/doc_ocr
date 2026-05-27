"""SPARK-style agentic schema verification for extracted records.

The Nature Medicine SPARK workflow is built around idea generation, parameter
coding and verification for pathology. This module applies the same pattern to
doc-ocr records: generate clinically useful document concepts from extracted
evidence, turn them into measurable parameters, then verify that each parameter
is normalized, evidence-backed and safe to expose.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Dict, Iterable, List

from . import config


def build_spark_agentic_analysis(
    *,
    patient_code: str,
    source_pdf: str,
    mentions: List[Dict[str, Any]],
    artifacts: Iterable[Dict[str, Any]] = (),
    page_count: int = 0,
) -> Dict[str, Any]:
    if not config.ENABLE_SPARK_AGENTIC_SCHEMA:
        return {"status": "disabled", "concepts": [], "parameters": [], "verification": {}}

    artifact_rows = list(artifacts or [])
    concept_rows = _generate_concepts(mentions, artifact_rows)
    parameter_rows = [_code_parameter(row, mentions, artifact_rows, page_count) for row in concept_rows]
    verification_rows = [_verify_parameter(row) for row in parameter_rows]
    accepted = [row for row in verification_rows if row["status"] == "accepted"]
    rejected = [row for row in verification_rows if row["status"] != "accepted"]

    return {
        "status": "completed",
        "method": "SPARK-inspired idea_generation_parameter_coding_verification",
        "patient_code": patient_code,
        "source_pdf": source_pdf,
        "concepts": concept_rows,
        "parameters": parameter_rows,
        "verification": {
            "accepted_count": len(accepted),
            "rejected_count": len(rejected),
            "accepted": accepted,
            "rejected": rejected,
        },
    }


def _generate_concepts(mentions: List[Dict[str, Any]], artifacts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    categories = {str(m.get("category") or "").lower() for m in mentions}
    roles = {str(a.get("role") or "").lower() for a in artifacts}
    backends = {str(a.get("backend") or "").lower() for a in artifacts}
    concepts = [
        {
            "key": "evidence_coverage",
            "idea": "Quantify how much extracted clinical content is tied to explicit page or artifact evidence.",
            "clinical_use": "Review readiness and hallucination-risk triage.",
        },
        {
            "key": "document_structure_richness",
            "idea": "Measure whether extraction captured page regions beyond plain native text.",
            "clinical_use": "Flags scanned prescriptions and table-heavy reports that need vision/layout review.",
        },
    ]
    if {"medication", "treatment", "medicine"} & categories or {"medicine", "medicine_table", "dose", "frequency"} & roles:
        concepts.append({
            "key": "medication_regimen_completeness",
            "idea": "Check medication/order extraction for drug, dose, frequency, route and instruction coverage.",
            "clinical_use": "Prescription review and downstream RxNorm/SNOMED coding readiness.",
        })
    if {"diagnosis", "pathology", "biomarker", "staging"} & categories:
        concepts.append({
            "key": "oncology_pathology_readiness",
            "idea": "Check whether diagnosis, staging and biomarker evidence are present enough for oncology summary use.",
            "clinical_use": "Cancer-report abstraction and biomarker review.",
        })
    if any("sahi" in backend for backend in backends):
        concepts.append({
            "key": "sahi_prescription_segmentation_yield",
            "idea": "Measure whether sliced instance inference found prescription-specific regions.",
            "clinical_use": "SAHI-BAR-style region evidence for medication/order OCR.",
        })
    return concepts


def _code_parameter(
    concept: Dict[str, Any],
    mentions: List[Dict[str, Any]],
    artifacts: List[Dict[str, Any]],
    page_count: int,
) -> Dict[str, Any]:
    key = concept["key"]
    mention_total = max(1, len(mentions))
    evidence_count = sum(
        1 for m in mentions
        if m.get("evidence_quote") or m.get("source_pages") or m.get("evidence_artifact_ids")
    )
    category_counts = Counter(str(m.get("category") or "unknown").lower() for m in mentions)
    role_counts = Counter(str(a.get("role") or "unknown").lower() for a in artifacts)
    backend_counts = Counter(str(a.get("backend") or "unknown").lower() for a in artifacts)

    if key == "evidence_coverage":
        value = evidence_count / mention_total
        return _parameter(concept, value, "fraction", {
            "mentions_with_evidence": evidence_count,
            "mention_total": len(mentions),
        })
    if key == "document_structure_richness":
        structured_artifacts = sum(
            count for role, count in role_counts.items()
            if role not in {"body", "header", "footer", "embedded_figure", "unknown"}
        )
        denom = max(1, page_count)
        return _parameter(concept, structured_artifacts / denom, "regions_per_page", {
            "structured_artifacts": structured_artifacts,
            "page_count": page_count,
            "role_counts": dict(role_counts),
            "backend_counts": dict(backend_counts),
        })
    if key == "medication_regimen_completeness":
        terms = " ".join(
            f"{m.get('label', '')} {m.get('value', '')} {m.get('normalized_value', '')}"
            for m in mentions
            if str(m.get("category") or "").lower() in {"medication", "treatment", "medicine"}
        ).lower()
        fields = {
            "drug": bool(terms.strip()) or bool(role_counts.get("medicine")),
            "dose": any(token in terms for token in ("mg", "mcg", "ml", "tablet", "tab", "cap")) or bool(role_counts.get("dose")),
            "frequency": any(token in terms for token in ("bd", "od", "tds", "q", "daily", "weekly")) or bool(role_counts.get("frequency")),
            "route": any(token in terms for token in ("oral", "iv", "inj", "sc", "po")),
            "instruction": any(token in terms for token in ("after", "before", "continue", "stop", "days")) or bool(role_counts.get("instruction")),
        }
        return _parameter(concept, sum(fields.values()) / len(fields), "field_fraction", {
            "fields": fields,
            "minimum_fields": config.SPARK_AGENTIC_MIN_MEDICATION_FIELDS,
            "category_counts": dict(category_counts),
        })
    if key == "oncology_pathology_readiness":
        needed = {
            "diagnosis": category_counts.get("diagnosis", 0) > 0,
            "stage": category_counts.get("staging", 0) > 0 or any("stage" in str(m.get("label", "")).lower() for m in mentions),
            "biomarker": category_counts.get("biomarker", 0) > 0 or any("er " in str(m.get("value", "")).lower() or "her2" in str(m.get("value", "")).lower() for m in mentions),
            "treatment": category_counts.get("treatment", 0) > 0 or category_counts.get("medication", 0) > 0,
        }
        return _parameter(concept, sum(needed.values()) / len(needed), "field_fraction", {"fields": needed})
    if key == "sahi_prescription_segmentation_yield":
        sahi_artifacts = sum(count for backend, count in backend_counts.items() if "sahi" in backend)
        return _parameter(concept, sahi_artifacts / max(1, page_count), "regions_per_page", {
            "sahi_artifacts": sahi_artifacts,
            "page_count": page_count,
            "role_counts": dict(role_counts),
        })
    return _parameter(concept, 0.0, "unknown", {})


def _parameter(concept: Dict[str, Any], value: float, unit: str, details: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "key": concept["key"],
        "idea": concept["idea"],
        "clinical_use": concept["clinical_use"],
        "value": round(float(value), 4),
        "unit": unit,
        "details": details,
    }


def _verify_parameter(parameter: Dict[str, Any]) -> Dict[str, Any]:
    key = parameter["key"]
    value = float(parameter.get("value") or 0.0)
    reasons = []
    if parameter.get("unit") not in {"fraction", "field_fraction", "regions_per_page"}:
        reasons.append("non_normalized_unit")
    if key == "evidence_coverage" and value < config.SPARK_AGENTIC_MIN_EVIDENCE_COVERAGE:
        reasons.append("low_evidence_coverage")
    if key == "medication_regimen_completeness":
        fields = parameter.get("details", {}).get("fields", {})
        if sum(1 for ok in fields.values() if ok) < config.SPARK_AGENTIC_MIN_MEDICATION_FIELDS:
            reasons.append("incomplete_medication_fields")
    if key == "sahi_prescription_segmentation_yield" and value <= 0:
        reasons.append("no_sahi_regions_detected")
    return {
        "key": key,
        "status": "accepted" if not reasons else "review",
        "reasons": reasons,
        "value": value,
        "unit": parameter.get("unit"),
    }
