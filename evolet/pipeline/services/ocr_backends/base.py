"""Common OCR backend primitives."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict


@dataclass
class OCRBackendResult:
    """Normalized OCR output from a recognizer or verifier."""

    text: str
    confidence: float = 0.0
    backend: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


class OCRBackend:
    """Minimal interface implemented by all OCR backends."""

    backend_name = "base"

    def is_available(self) -> bool:
        return False

    def recognize(self, image) -> OCRBackendResult:  # pragma: no cover - interface only
        raise NotImplementedError
