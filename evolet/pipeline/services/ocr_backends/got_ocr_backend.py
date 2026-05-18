"""Full-page OCR verification backend powered by GOT-OCR 2.0."""

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


class GOTOCRBackend(OCRBackend):
    backend_name = "got_ocr"

    def is_available(self) -> bool:
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
            try:
                from transformers import AutoProcessor, AutoModelForImageTextToText

                _processor = AutoProcessor.from_pretrained(config.GOT_OCR_MODEL_ID)
                kwargs = {}
                if torch.cuda.is_available():
                    kwargs["torch_dtype"] = detect_compute_dtype() or torch.float16
                    kwargs["device_map"] = "auto"
                _model = AutoModelForImageTextToText.from_pretrained(
                    config.GOT_OCR_MODEL_ID,
                    **kwargs,
                )
                _model.eval()
                logger.info("Loaded GOT-OCR backend: %s", config.GOT_OCR_MODEL_ID)
            except Exception as exc:
                logger.warning("GOT-OCR backend unavailable: %s", exc)
                _processor = None
                _model = None

        return _processor, _model

    def recognize(self, image) -> OCRBackendResult:
        processor, model = self._load()
        if processor is None or model is None:
            return OCRBackendResult(text="", backend=self.backend_name, metadata={"available": False})

        try:
            device = next(model.parameters()).device
            inputs = processor(image, return_tensors="pt").to(device)
            with torch.inference_mode():
                generate_ids = model.generate(
                    **inputs,
                    do_sample=False,
                    max_new_tokens=config.GOT_OCR_MAX_NEW_TOKENS,
                    tokenizer=getattr(processor, "tokenizer", None),
                    stop_strings="<|im_end|>",
                )
            if hasattr(processor, "batch_decode"):
                text = processor.batch_decode(generate_ids, skip_special_tokens=True)[0].strip()
            else:
                text = ""
            return OCRBackendResult(
                text=text,
                confidence=0.0,
                backend=self.backend_name,
                metadata={"model_id": config.GOT_OCR_MODEL_ID},
            )
        except TypeError:
            try:
                outputs = model.generate(**inputs, do_sample=False, max_new_tokens=config.GOT_OCR_MAX_NEW_TOKENS)
                text = processor.batch_decode(outputs, skip_special_tokens=True)[0].strip()
                return OCRBackendResult(
                    text=text,
                    confidence=0.0,
                    backend=self.backend_name,
                    metadata={"model_id": config.GOT_OCR_MODEL_ID},
                )
            except Exception as exc:
                logger.warning("GOT-OCR inference failed: %s", exc)
        except Exception as exc:
            logger.warning("GOT-OCR inference failed: %s", exc)

        return OCRBackendResult(
            text="",
            backend=self.backend_name,
            metadata={"error": "inference_failed", "model_id": config.GOT_OCR_MODEL_ID},
        )
