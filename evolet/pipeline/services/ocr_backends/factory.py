"""Lazy singleton accessors for optional OCR backends.

The Django-only runtime does not install torch/transformers. Importing the
backend classes lazily keeps the web app and Groq/dictionary path usable while
still allowing full OCR backends in GPU builds.
"""

_trocr = None
_got = None
_medical_handwriting = None


def get_trocr_backend():
    global _trocr
    if _trocr is None:
        from .trocr_backend import TrOCRBackend

        _trocr = TrOCRBackend()
    return _trocr


def get_got_ocr_backend():
    global _got
    if _got is None:
        from .got_ocr_backend import GOTOCRBackend

        _got = GOTOCRBackend()
    return _got


def get_medical_handwriting_backend():
    global _medical_handwriting
    if _medical_handwriting is None:
        from .medical_handwriting_backend import MedicalHandwritingBackend

        _medical_handwriting = MedicalHandwritingBackend()
    return _medical_handwriting
