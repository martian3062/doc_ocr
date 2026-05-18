"""OCR backend abstractions for the advanced hybrid reader."""

from .factory import get_got_ocr_backend, get_trocr_backend

__all__ = ["get_got_ocr_backend", "get_trocr_backend"]
