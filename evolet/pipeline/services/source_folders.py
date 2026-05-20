"""Source-folder labels for folder-wise document runs."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from ..models import PDFDocument


def source_folder_label(path: str) -> str:
    raw = str(path or "").strip()
    if not raw:
        return "Upload"
    lowered = raw.lower()
    if "radiation" in lowered:
        return "Radiation"
    if "chemo" in lowered or "chenmo" in lowered:
        return "Chemotherapy"
    parent = Path(raw).parent.name
    return parent or "Folder import"


def document_source_payload(doc: PDFDocument) -> dict[str, Any]:
    path = doc.folder_path or (doc.file.name if doc.file else "")
    return {
        "folder_name": source_folder_label(path),
        "folder_path": doc.folder_path or "",
        "filename": doc.original_filename,
    }


def documents_source_summary(documents: Iterable[PDFDocument]) -> dict[str, Any]:
    items = [document_source_payload(doc) for doc in documents]
    labels = []
    for item in items:
        label = item["folder_name"]
        if label not in labels:
            labels.append(label)
    return {
        "folder_names": labels,
        "folder_name": " + ".join(labels) if labels else "",
        "documents": items,
    }
