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
_runner_kind = ""
_lock = threading.Lock()


class MedicalHandwritingBackend(OCRBackend):
    backend_name = "medical_handwriting"

    def is_available(self) -> bool:
        if not config.MEDICAL_HANDWRITING_MODEL_ID:
            return False
        try:
            import transformers  # noqa: F401
            return True
        except Exception:
            return False

    def _load(self) -> tuple[Optional[object], Optional[object], str]:
        global _model, _processor, _runner_kind
        if _model is not None and _processor is not None:
            return _processor, _model, _runner_kind

        with _lock:
            if _model is not None and _processor is not None:
                return _processor, _model, _runner_kind
            if not config.MEDICAL_HANDWRITING_MODEL_ID:
                return None, None, ""
            try:
                from transformers import AutoModelForImageTextToText, AutoProcessor, VisionEncoderDecoderModel, pipeline

                common_kwargs = {
                    "local_files_only": config.LOCAL_FILES_ONLY,
                    "trust_remote_code": config.MEDICAL_HANDWRITING_TRUST_REMOTE_CODE,
                }
                if config.HF_TOKEN:
                    common_kwargs["token"] = config.HF_TOKEN
                kwargs = dict(common_kwargs)
                if torch.cuda.is_available():
                    kwargs["torch_dtype"] = detect_compute_dtype() or torch.float16
                    kwargs["device_map"] = "auto"

                load_errors = []
                for kind, model_cls in (
                    ("image_text_to_text", AutoModelForImageTextToText),
                    ("vision_encoder_decoder", VisionEncoderDecoderModel),
                ):
                    try:
                        _processor = AutoProcessor.from_pretrained(config.MEDICAL_HANDWRITING_MODEL_ID, **common_kwargs)
                        _model = model_cls.from_pretrained(config.MEDICAL_HANDWRITING_MODEL_ID, **kwargs)
                        _runner_kind = kind
                        break
                    except Exception as exc:
                        load_errors.append(f"{kind}: {str(exc)[:180]}")
                        _processor = None
                        _model = None

                if _model is None:
                    device = 0 if torch.cuda.is_available() else -1
                    _model = pipeline(
                        "image-to-text",
                        model=config.MEDICAL_HANDWRITING_MODEL_ID,
                        device=device,
                        **common_kwargs,
                    )
                    _processor = _model
                    _runner_kind = "pipeline_image_to_text"

                if hasattr(_model, "eval"):
                    _model.eval()
                logger.info(
                    "Loaded medical handwriting backend: %s kind=%s",
                    config.MEDICAL_HANDWRITING_MODEL_ID,
                    _runner_kind,
                )
            except Exception as exc:
                logger.warning("Medical handwriting backend unavailable: %s", exc)
                _processor = None
                _model = None
                _runner_kind = ""

        return _processor, _model, _runner_kind

    def recognize(self, image) -> OCRBackendResult:
        processor, model, runner_kind = self._load()
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
            if runner_kind == "pipeline_image_to_text":
                result = model(image)
                first = result[0] if isinstance(result, list) and result else result
                text = str((first or {}).get("generated_text") if isinstance(first, dict) else first).strip()
            else:
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
                    "runner_kind": runner_kind,
                    "candidate_models": config.MEDICAL_HANDWRITING_CANDIDATE_MODEL_IDS,
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
                    "runner_kind": runner_kind,
                    "candidate_models": config.MEDICAL_HANDWRITING_CANDIDATE_MODEL_IDS,
                    "reference_dataset": config.MEDOCR_VISION_DATASET_ID,
                },
            )
