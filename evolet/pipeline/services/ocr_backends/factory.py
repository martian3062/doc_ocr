"""Singleton accessors for OCR backends."""

from .got_ocr_backend import GOTOCRBackend
from .trocr_backend import TrOCRBackend

_trocr = TrOCRBackend()
_got = GOTOCRBackend()


def get_trocr_backend() -> TrOCRBackend:
    return _trocr


def get_got_ocr_backend() -> GOTOCRBackend:
    return _got
