"""Optional Docling parser adapter."""

from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List

from .base import DocumentParser, ParserArtifact, ParserResult

logger = logging.getLogger("pipeline")


class DoclingParser(DocumentParser):
    backend_name = "docling"

    def is_available(self) -> bool:
        try:
            from docling.document_converter import DocumentConverter  # noqa: F401
            return True
        except Exception:
            return False

    def parse(self, pdf_path: str) -> ParserResult:
        if not self.is_available():
            return ParserResult(backend=self.backend_name, available=False, error="docling_not_installed")

        try:
            from docling.document_converter import DocumentConverter

            result = DocumentConverter().convert(pdf_path)
            document = getattr(result, "document", None)
            exported: Dict[str, Any] = {}
            if document is not None and hasattr(document, "export_to_dict"):
                exported = document.export_to_dict()

            artifacts = list(_artifacts_from_docling_dict(exported))
            return ParserResult(
                backend=self.backend_name,
                available=True,
                artifacts=artifacts,
                metadata={"artifact_count": len(artifacts)},
            )
        except Exception as exc:
            logger.warning("Docling parse failed: %s", exc)
            return ParserResult(backend=self.backend_name, available=True, error=str(exc))


def _artifacts_from_docling_dict(payload: Dict[str, Any]) -> Iterable[ParserArtifact]:
    """Best-effort extraction from Docling's exported document dictionary."""
    texts = payload.get("texts") or []
    tables = payload.get("tables") or []
    pictures = payload.get("pictures") or []

    order = 0
    for item in texts:
        order += 1
        text = _text_from_item(item)
        if not text:
            continue
        page_num, bbox = _location_from_item(item)
        yield ParserArtifact(
            artifact_type="text_block",
            role=_role_from_label(item.get("label", "")),
            backend="docling",
            text=text,
            confidence=0.85,
            bbox=bbox,
            page_num=page_num,
            reading_order=order,
            metadata={"label": item.get("label", ""), "source": "docling.texts"},
        )

    for item in tables:
        order += 1
        text = _text_from_item(item)
        page_num, bbox = _location_from_item(item)
        yield ParserArtifact(
            artifact_type="table",
            role="table",
            backend="docling",
            text=text,
            confidence=0.8,
            bbox=bbox,
            page_num=page_num,
            reading_order=order,
            metadata={"label": item.get("label", "table"), "source": "docling.tables"},
        )

    for item in pictures:
        order += 1
        page_num, bbox = _location_from_item(item)
        yield ParserArtifact(
            artifact_type="figure",
            role="embedded_figure",
            backend="docling",
            confidence=0.7,
            bbox=bbox,
            page_num=page_num,
            reading_order=order,
            metadata={"label": item.get("label", "picture"), "source": "docling.pictures"},
        )


def _text_from_item(item: Dict[str, Any]) -> str:
    for key in ("text", "orig", "content"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _role_from_label(label: str) -> str:
    label = (label or "").lower()
    if "header" in label or "title" in label:
        return "header"
    if "footer" in label:
        return "footer"
    if "table" in label:
        return "table"
    return "body"


def _location_from_item(item: Dict[str, Any]) -> tuple[int, List[float]]:
    prov = item.get("prov")
    if isinstance(prov, list) and prov:
        first = prov[0] or {}
        page_num = int(first.get("page_no") or first.get("page_num") or 0)
        bbox_obj = first.get("bbox") or {}
        bbox = _bbox_to_list(bbox_obj)
        return page_num, bbox
    return 0, []


def _bbox_to_list(value: Any) -> List[float]:
    if isinstance(value, list) and len(value) >= 4:
        return [float(v) for v in value[:4]]
    if isinstance(value, dict):
        keys = ("l", "t", "r", "b")
        if all(k in value for k in keys):
            return [float(value[k]) for k in keys]
        keys = ("x0", "y0", "x1", "y1")
        if all(k in value for k in keys):
            return [float(value[k]) for k in keys]
    return []
