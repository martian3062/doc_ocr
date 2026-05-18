"""Shared parser result types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class ParserArtifact:
    artifact_type: str
    role: str
    backend: str
    text: str = ""
    confidence: float = 0.0
    bbox: List[float] = field(default_factory=list)
    polygon: List[Any] = field(default_factory=list)
    page_num: int = 0
    reading_order: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "artifact_type": self.artifact_type,
            "role": self.role,
            "backend": self.backend,
            "text": self.text,
            "normalized_text": self.text,
            "confidence": self.confidence,
            "bbox": self.bbox,
            "polygon": self.polygon,
            "page_num": self.page_num,
            "reading_order": self.reading_order,
            "metadata": self.metadata,
        }


@dataclass
class ParserResult:
    backend: str
    available: bool
    artifacts: List[ParserArtifact] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    error: str = ""


class DocumentParser:
    backend_name = "base"

    def is_available(self) -> bool:
        return False

    def parse(self, pdf_path: str) -> ParserResult:  # pragma: no cover - interface only
        raise NotImplementedError
