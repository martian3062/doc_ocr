"""Optional Surya parser adapter."""

from __future__ import annotations

import logging

from .base import DocumentParser, ParserResult

logger = logging.getLogger("pipeline")


class SuryaParser(DocumentParser):
    backend_name = "surya"

    def is_available(self) -> bool:
        try:
            import surya  # noqa: F401
            return True
        except Exception:
            return False

    def parse(self, pdf_path: str) -> ParserResult:
        """
        Surya's public Python APIs move quickly, so this adapter is intentionally
        conservative. The current pipeline uses Surya as an optional installed
        capability marker and keeps PyMuPDF/TrOCR as the stable fallback unless
        a concrete Surya predictor API is available in the runtime.
        """
        if not self.is_available():
            return ParserResult(backend=self.backend_name, available=False, error="surya_not_installed")
        return ParserResult(
            backend=self.backend_name,
            available=True,
            metadata={
                "status": "installed",
                "note": "Surya installed; stable adapter hook ready for line/layout predictor wiring.",
                "path": pdf_path,
            },
        )
