"""Singleton accessors for OCR backends."""

from .got_ocr_backend import GOTOCRBackend
from .medical_handwriting_backend import MedicalHandwritingBackend
from .trocr_backend import TrOCRBackend

_trocr = TrOCRBackend()
_got = GOTOCRBackend()
_medical_handwriting = MedicalHandwritingBackend()


def get_trocr_backend() -> TrOCRBackend:
    return _trocr


def get_got_ocr_backend() -> GOTOCRBackend:
    return _got


def get_medical_handwriting_backend() -> MedicalHandwritingBackend:
    return _medical_handwriting
