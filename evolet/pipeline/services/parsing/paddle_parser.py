"""Optional PaddleOCR/PP-Structure parser adapter."""

from __future__ import annotations

import logging

from .base import DocumentParser, ParserResult

logger = logging.getLogger("pipeline")


class PaddleStructureParser(DocumentParser):
    backend_name = "paddle_structure"

    def is_available(self) -> bool:
        try:
            import paddleocr  # noqa: F401
            return True
        except Exception:
            return False

    def parse(self, pdf_path: str) -> ParserResult:
        if not self.is_available():
            return ParserResult(backend=self.backend_name, available=False, error="paddleocr_not_installed")
        return ParserResult(
            backend=self.backend_name,
            available=True,
            metadata={
                "status": "installed",
                "note": "PaddleOCR installed; PP-Structure/PaddleOCR-VL can be enabled per deployment.",
                "path": pdf_path,
            },
        )
