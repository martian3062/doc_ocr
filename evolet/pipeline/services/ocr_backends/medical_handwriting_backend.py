"""Optional medical handwriting / prescription vision OCR backend.

This backend is intentionally generic: the MedOCR Vision Hugging Face dataset
is used as the reference/evaluation corpus, while the inference model is chosen
by DOC_READER_MEDICAL_HANDWRITING_MODEL_ID. This avoids hardcoding a dataset as
if it were a runnable model.
"""

from __future__ import annotations

import logging
import threading
from typing import Optional

import torch

from .base import OCRBackend, OCRBackendResult
from .. import config
from ..gpu_utils import detect_compute_dtype

logger = logging.getLogger("pipeline")

_model = None
_processor = None
_lock = threading.Lock()


class MedicalHandwritingBackend(OCRBackend):
    backend_name = "medical_handwriting"

    def is_available(self) -> bool:
        if not config.MEDICAL_HANDWRITING_MODEL_ID:
            return False
        try:
            from transformers import AutoProcessor, AutoModelForImageTextToText  # noqa: F401
            return True
        except Exception:
            return False

    def _load(self) -> tuple[Optional[object], Optional[object]]:
        global _model, _processor
        if _model is not None and _processor is not None:
            return _processor, _model

        with _lock:
            if _model is not None and _processor is not None:
                return _processor, _model
            if not config.MEDICAL_HANDWRITING_MODEL_ID:
                return None, None
            try:
                from transformers import AutoProcessor, AutoModelForImageTextToText

                common_kwargs = {"local_files_only": config.LOCAL_FILES_ONLY}
                if config.HF_TOKEN:
                    common_kwargs["token"] = config.HF_TOKEN
                _processor = AutoProcessor.from_pretrained(
                    config.MEDICAL_HANDWRITING_MODEL_ID,
                    **common_kwargs,
                )
                kwargs = dict(common_kwargs)
                if torch.cuda.is_available():
                    kwargs["torch_dtype"] = detect_compute_dtype() or torch.float16
                    kwargs["device_map"] = "auto"
                _model = AutoModelForImageTextToText.from_pretrained(
                    config.MEDICAL_HANDWRITING_MODEL_ID,
                    **kwargs,
                )
                _model.eval()
                logger.info("Loaded medical handwriting backend: %s", config.MEDICAL_HANDWRITING_MODEL_ID)
            except Exception as exc:
                logger.warning("Medical handwriting backend unavailable: %s", exc)
                _processor = None
                _model = None

        return _processor, _model

    def recognize(self, image) -> OCRBackendResult:
        processor, model = self._load()
        if processor is None or model is None:
            return OCRBackendResult(
                text="",
                backend=self.backend_name,
                metadata={
                    "available": False,
                    "model_id": config.MEDICAL_HANDWRITING_MODEL_ID,
                    "reference_dataset": config.MEDOCR_VISION_DATASET_ID,
                },
            )

        prompt = (
            "Read this medical handwriting or prescription crop. Return only the "
            "visible text. Preserve drug names, doses, frequencies, dates, doctor "
            "names, and patient identifiers when present."
        )
        try:
            device = next(model.parameters()).device
            try:
                inputs = processor(
                    images=image,
                    text=prompt,
                    return_tensors="pt",
                ).to(device)
            except TypeError:
                inputs = processor(images=image, return_tensors="pt").to(device)
            with torch.inference_mode():
                generated_ids = model.generate(
                    **inputs,
                    do_sample=False,
                    max_new_tokens=config.GOT_OCR_MAX_NEW_TOKENS,
                )
            text = processor.batch_decode(generated_ids, skip_special_tokens=True)[0].strip()
            if prompt in text:
                text = text.replace(prompt, "").strip()
            return OCRBackendResult(
                text=text,
                confidence=0.0,
                backend=self.backend_name,
                metadata={
                    "model_id": config.MEDICAL_HANDWRITING_MODEL_ID,
                    "reference_dataset": config.MEDOCR_VISION_DATASET_ID,
                },
            )
        except Exception as exc:
            logger.warning("Medical handwriting inference failed: %s", exc)
            return OCRBackendResult(
                text="",
                backend=self.backend_name,
                metadata={
                    "error": str(exc),
                    "model_id": config.MEDICAL_HANDWRITING_MODEL_ID,
                    "reference_dataset": config.MEDOCR_VISION_DATASET_ID,
                },
            )
