"""Lightweight relation extraction over merged mentions."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List


def build_relation_payload(mentions: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Build timeline events and relation edges from merged mentions.

    This is intentionally deterministic: it upgrades the current pipeline from
    flat mention groups into a graph without requiring another heavy model pass.
    """
    edges: List[Dict[str, Any]] = []
    timeline_events: List[Dict[str, Any]] = []

    diagnoses = [m for m in mentions if m.get("category") == "diagnosis"]
    medications = [m for m in mentions if m.get("category") == "medication"]
    labs = [m for m in mentions if m.get("category") == "lab"]
    imaging = [m for m in mentions if m.get("category") == "imaging"]
    follow_ups = [m for m in mentions if m.get("category") == "follow_up"]

    for diagnosis in diagnoses:
        timeline_events.append(_timeline_event("diagnosis", diagnosis))
        for medication in medications:
            edges.append(_edge(diagnosis, medication, "treated_with"))
        for study in imaging:
            edges.append(_edge(diagnosis, study, "evidenced_by"))
        for lab in labs:
            edges.append(_edge(diagnosis, lab, "test_result"))

    for medication in medications:
        timeline_events.append(_timeline_event("treatment", medication))

    for follow_up in follow_ups:
        timeline_events.append(_timeline_event("follow_up", follow_up))

    timeline_events = sorted(
        timeline_events,
        key=lambda item: (item.get("date_text") == "", item.get("date_text", ""), item.get("label", "")),
    )

    grouped_edges = defaultdict(list)
    for edge in edges:
        grouped_edges[(edge["source_key"], edge["target_key"], edge["relation_type"])].append(edge)

    deduped_edges = []
    for group in grouped_edges.values():
        edge = group[0]
        evidence_pages = sorted({page for item in group for page in item.get("evidence_pages", [])})
        edge["evidence_pages"] = evidence_pages
        deduped_edges.append(edge)

    return {"edges": deduped_edges, "timeline_events": timeline_events}


def _edge(source: Dict[str, Any], target: Dict[str, Any], relation_type: str) -> Dict[str, Any]:
    return {
        "source_key": _mention_key(source),
        "target_key": _mention_key(target),
        "source_label": source.get("label", source.get("category", "")),
        "target_label": target.get("label", target.get("category", "")),
        "source_value": source.get("value", ""),
        "target_value": target.get("value", ""),
        "relation_type": relation_type,
        "confidence": 0.72,
        "evidence_pages": sorted(set(source.get("source_pages", []) + target.get("source_pages", []))),
        "source_artifact_ids": source.get("evidence_artifact_ids", []),
        "target_artifact_ids": target.get("evidence_artifact_ids", []),
    }


def _timeline_event(event_type: str, mention: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "event_type": event_type,
        "label": mention.get("label", mention.get("category", "")),
        "value": mention.get("value", ""),
        "date_text": mention.get("date_text", ""),
        "category": mention.get("category", ""),
        "source_pages": mention.get("source_pages", []),
        "artifact_ids": mention.get("evidence_artifact_ids", []),
    }


def _mention_key(mention: Dict[str, Any]) -> str:
    return "::".join(
        [
            str(mention.get("category", "")).lower(),
            str(mention.get("label", "")).lower(),
            str(mention.get("normalized_value") or mention.get("value", "")).lower(),
            str(mention.get("date_text", "")).lower(),
        ]
    )
