"""Chandra OCR backend — crop-level reading of handwriting AND printed text.

Uses datalab-to/chandra-ocr-2 (Qwen3-VL 9B fine-tuned specifically to fix
pipeline-based OCR failures on handwriting in forms and complex layouts).
"""
from __future__ import annotations

import logging
import threading
from typing import Optional

from .base import OCRBackend, OCRBackendResult
from .. import config

logger = logging.getLogger("pipeline")

_model = None
_processor = None
_runner_kind = ""
_lock = threading.Lock()

CHANDRA_PROMPT = (
    "Extract ALL visible text from this image exactly as written. "
    "Include handwritten doctor orders, drug names, doses (mg/ml/mcg), "
    "frequencies (OD/BD/TDS/SOS/STAT), routes (IV/IM/PO/SC), "
    "vitals (BP, SpO2, pulse, temp, RR), dates, and clinical notes. "
    "Mark uncertain words with [?]. Return only the extracted text."
)


class ChandraOCRBackend(OCRBackend):
    backend_name = "chandra_ocr"

    def is_available(self) -> bool:
        if not config.ENABLE_CHANDRA_OCR:
            return False
        try:
            import transformers  # noqa: F401
            return True
        except Exception:
            return False

    def _load(self) -> tuple[Optional[object], Optional[object], str]:
        global _model, _processor, _runner_kind
        if _model is not None:
            return _processor, _model, _runner_kind
        with _lock:
            if _model is not None:
                return _processor, _model, _runner_kind
            try:
                import torch
                from transformers import AutoModelForImageTextToText, AutoProcessor

                load_kwargs: dict = {
                    "token": config.HF_TOKEN,
                    "local_files_only": config.LOCAL_FILES_ONLY,
                }
                if torch.cuda.is_available():
                    load_kwargs["device_map"] = "auto"
                    if config.CHANDRA_USE_4BIT:
                        try:
                            from transformers import BitsAndBytesConfig

                            load_kwargs["quantization_config"] = BitsAndBytesConfig(
                                load_in_4bit=True,
                                bnb_4bit_compute_dtype=torch.bfloat16,
                            )
                        except Exception as exc:
                            logger.warning("Chandra 4-bit config unavailable: %s", exc)
                            load_kwargs["torch_dtype"] = torch.bfloat16
                    else:
                        load_kwargs["torch_dtype"] = torch.bfloat16

                _processor = AutoProcessor.from_pretrained(
                    config.CHANDRA_OCR_MODEL_ID,
                    token=config.HF_TOKEN,
                    local_files_only=config.LOCAL_FILES_ONLY,
                )
                try:
                    _model = AutoModelForImageTextToText.from_pretrained(
                        config.CHANDRA_OCR_MODEL_ID, **load_kwargs
                    )
                    _runner_kind = "image_text_to_text"
                except Exception as exc:
                    if load_kwargs.pop("quantization_config", None) is None:
                        raise
                    logger.warning("Chandra 4-bit load failed, retrying BF16: %s", exc)
                    load_kwargs["torch_dtype"] = torch.bfloat16
                    _model = AutoModelForImageTextToText.from_pretrained(
                        config.CHANDRA_OCR_MODEL_ID, **load_kwargs
                    )
                    _runner_kind = "image_text_to_text_bf16"

                if hasattr(_model, "eval"):
                    _model.eval()
                logger.info(
                    "Loaded Chandra OCR: %s kind=%s 4bit=%s",
                    config.CHANDRA_OCR_MODEL_ID,
                    _runner_kind,
                    config.CHANDRA_USE_4BIT,
                )
            except Exception as exc:
                logger.warning("Chandra OCR load failed: %s", exc)
                _model = None
                _processor = None
                _runner_kind = ""
        return _processor, _model, _runner_kind

    def recognize(self, image) -> OCRBackendResult:
        processor, model, runner_kind = self._load()
        if processor is None or model is None:
            return OCRBackendResult(
                text="",
                backend=self.backend_name,
                metadata={"available": False, "model_id": config.CHANDRA_OCR_MODEL_ID},
            )

        try:
            import torch

            messages = [
                {
                    "role": "user",
                    "content": [
                        {"type": "image", "image": image},
                        {"type": "text", "text": CHANDRA_PROMPT},
                    ],
                }
            ]
            try:
                from qwen_vl_utils import process_vision_info

                text_input = processor.apply_chat_template(
                    messages, tokenize=False, add_generation_prompt=True
                )
                image_inputs, video_inputs = process_vision_info(messages)
                inputs = processor(
                    text=[text_input],
                    images=image_inputs,
                    videos=video_inputs,
                    padding=True,
                    return_tensors="pt",
                )
            except Exception:
                inputs = processor(
                    text=[CHANDRA_PROMPT],
                    images=[image],
                    return_tensors="pt",
                )

            device = next(model.parameters()).device
            inputs = {
                k: v.to(device) if hasattr(v, "to") else v for k, v in inputs.items()
            }
            input_len = inputs.get("input_ids", torch.tensor([[]])).shape[-1]

            with torch.inference_mode():
                out = model.generate(
                    **inputs,
                    max_new_tokens=config.CHANDRA_MAX_NEW_TOKENS,
                    do_sample=False,
                )

            text = processor.batch_decode(
                out[:, input_len:], skip_special_tokens=True
            )[0].strip()

            return OCRBackendResult(
                text=text,
                confidence=0.88,
                backend=self.backend_name,
                metadata={
                    "model_id": config.CHANDRA_OCR_MODEL_ID,
                    "runner_kind": runner_kind,
                },
            )
        except Exception as exc:
            logger.warning("Chandra OCR inference failed: %s", exc)
            return OCRBackendResult(
                text="",
                backend=self.backend_name,
                metadata={
                    "error": str(exc),
                    "model_id": config.CHANDRA_OCR_MODEL_ID,
                },
            )
