"""Primary handwritten OCR backend based on TrOCR."""

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


class TrOCRBackend(OCRBackend):
    backend_name = "trocr"

    def is_available(self) -> bool:
        try:
            from transformers import TrOCRProcessor, VisionEncoderDecoderModel  # noqa: F401
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
            try:
                from transformers import TrOCRProcessor, VisionEncoderDecoderModel

                _processor = TrOCRProcessor.from_pretrained(config.TROCR_MODEL_ID)
                kwargs = {}
                if torch.cuda.is_available():
                    kwargs["torch_dtype"] = detect_compute_dtype() or torch.float16
                    kwargs["device_map"] = "auto"
                _model = VisionEncoderDecoderModel.from_pretrained(
                    config.TROCR_MODEL_ID,
                    **kwargs,
                )
                _model.eval()
                logger.info("Loaded TrOCR backend: %s", config.TROCR_MODEL_ID)
            except Exception as exc:
                logger.warning("TrOCR backend unavailable: %s", exc)
                _processor = None
                _model = None

        return _processor, _model

    def recognize(self, image) -> OCRBackendResult:
        processor, model = self._load()
        if processor is None or model is None:
            return OCRBackendResult(text="", backend=self.backend_name, metadata={"available": False})

        try:
            device = next(model.parameters()).device
            pixel_values = processor(images=image, return_tensors="pt").pixel_values.to(device)
            with torch.inference_mode():
                generated_ids = model.generate(pixel_values, max_new_tokens=config.TROCR_MAX_NEW_TOKENS)
            text = processor.batch_decode(generated_ids, skip_special_tokens=True)[0].strip()
            return OCRBackendResult(
                text=text,
                confidence=0.0,
                backend=self.backend_name,
                metadata={"model_id": config.TROCR_MODEL_ID},
            )
        except Exception as exc:
            logger.warning("TrOCR inference failed: %s", exc)
            return OCRBackendResult(
                text="",
                backend=self.backend_name,
                metadata={"error": str(exc), "model_id": config.TROCR_MODEL_ID},
            )
