"""olmOCR-2 full-page parser: reads both printed text AND handwriting in one VLM pass.

Uses allenai/olmOCR-2-7B-1025 (Qwen2.5-VL-7B fine-tuned on 270k PDF pages including
medical records, handwritten documents, and mixed printed+handwriting forms).
"""
from __future__ import annotations

import logging
import threading
from io import BytesIO

import fitz
from PIL import Image

from .. import config
from .base import DocumentParser, ParserArtifact, ParserResult

logger = logging.getLogger("pipeline")

_model = None
_processor = None
_lock = threading.Lock()

OLMOCR_PROMPT = (
    "Using the image, recover ALL text and layout. Extract every piece of text "
    "including handwritten doctor orders, printed form fields, medication names, "
    "doses, routes, frequencies, vitals (BP, SpO2, pulse, temp), dates, and "
    "clinical observations. Output plain text preserving reading order."
)


def _patch_qwen25vl_get_text_config() -> None:
    """Patch Qwen2_5_VLConfig.get_text_config to return a PretrainedConfig object.

    transformers 4.51.x stores olmOCR-2's text_config as a raw dict, so
    GenerationConfig.from_model_config crashes when it calls .to_dict() on it.
    """
    try:
        from transformers.models.qwen2_5_vl.configuration_qwen2_5_vl import Qwen2_5_VLConfig
        from transformers.configuration_utils import PretrainedConfig

        if getattr(Qwen2_5_VLConfig, "_olmocr_gtc_patched", False):
            return

        _orig = Qwen2_5_VLConfig.get_text_config

        def _safe_get_text_config(self, decoder=False):
            result = _orig(self, decoder=decoder)
            if isinstance(result, dict):
                pc = PretrainedConfig()
                for k, v in result.items():
                    try:
                        setattr(pc, k, v)
                    except Exception:
                        pass
                return pc
            return result

        Qwen2_5_VLConfig.get_text_config = _safe_get_text_config
        Qwen2_5_VLConfig._olmocr_gtc_patched = True
    except Exception as exc:
        logger.debug("Qwen2_5_VLConfig patch skipped: %s", exc)


class OlmOCRParser(DocumentParser):
    backend_name = "olmocr"

    def is_available(self) -> bool:
        if not config.ENABLE_OLMOCR_PARSER:
            return False
        try:
            from transformers import Qwen2_5_VLForConditionalGeneration  # noqa: F401
            return True
        except Exception:
            return False

    def _load(self):
        global _model, _processor
        if _model is not None:
            return _processor, _model
        with _lock:
            if _model is not None:
                return _processor, _model
            try:
                import torch
                from transformers import AutoProcessor
                from transformers.models.qwen2_5_vl.modeling_qwen2_5_vl import (
                    Qwen2_5_VLForConditionalGeneration,
                )

                _patch_qwen25vl_get_text_config()

                kwargs: dict = {
                    "token": config.HF_TOKEN,
                    "local_files_only": config.LOCAL_FILES_ONLY,
                }
                if torch.cuda.is_available():
                    kwargs["torch_dtype"] = torch.bfloat16
                    kwargs["device_map"] = "auto"

                _processor = AutoProcessor.from_pretrained(
                    config.OLMOCR_MODEL_ID,
                    token=config.HF_TOKEN,
                    local_files_only=config.LOCAL_FILES_ONLY,
                )
                _model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
                    config.OLMOCR_MODEL_ID,
                    **kwargs,
                )
                if hasattr(_model, "eval"):
                    _model.eval()
                logger.info(
                    "Loaded olmOCR-2: %s",
                    config.OLMOCR_MODEL_ID,
                )
            except Exception as exc:
                logger.warning("olmOCR-2 load failed: %s", exc)
                _model = None
                _processor = None
        return _processor, _model

    def parse(self, pdf_path: str) -> ParserResult:
        if not config.ENABLE_OLMOCR_PARSER:
            return ParserResult(
                backend=self.backend_name,
                available=False,
                error="olmocr_disabled",
            )

        processor, model = self._load()
        if processor is None or model is None:
            return ParserResult(
                backend=self.backend_name,
                available=False,
                error="model_load_failed",
                metadata={"model_id": config.OLMOCR_MODEL_ID},
            )

        import torch

        artifacts: list[ParserArtifact] = []
        doc = fitz.open(pdf_path)
        try:
            for page_index in range(min(len(doc), config.OLMOCR_MAX_PAGES_PER_DOCUMENT)):
                page = doc[page_index]
                page_num = page_index + 1
                pix = page.get_pixmap(dpi=config.OLMOCR_RENDER_DPI, alpha=False)
                image = Image.open(BytesIO(pix.tobytes("png"))).convert("RGB")
                try:
                    messages = [
                        {
                            "role": "user",
                            "content": [
                                {"type": "image", "image": image},
                                {"type": "text", "text": OLMOCR_PROMPT},
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
                            text=[OLMOCR_PROMPT],
                            images=[image],
                            return_tensors="pt",
                        )

                    device = next(model.parameters()).device
                    inputs = {
                        k: v.to(device) if hasattr(v, "to") else v
                        for k, v in inputs.items()
                    }
                    input_len = inputs.get("input_ids", torch.tensor([[]])).shape[-1]

                    with torch.inference_mode():
                        out = model.generate(
                            **inputs,
                            max_new_tokens=config.OLMOCR_MAX_NEW_TOKENS,
                            do_sample=False,
                        )

                    text = processor.batch_decode(
                        out[:, input_len:], skip_special_tokens=True
                    )[0].strip()

                    if len(text) >= 10:
                        artifacts.append(
                            ParserArtifact(
                                artifact_type="page_text",
                                role="body",
                                backend=self.backend_name,
                                text=text,
                                confidence=0.88,
                                bbox=[
                                    0.0,
                                    0.0,
                                    float(page.rect.width),
                                    float(page.rect.height),
                                ],
                                page_num=page_num,
                                reading_order=page_num * 100,
                                metadata={
                                    "model_id": config.OLMOCR_MODEL_ID,
                                    "method": "olmocr_full_page_vlm",
                                    "render_dpi": config.OLMOCR_RENDER_DPI,
                                    "char_count": len(text),
                                },
                            )
                        )
                        logger.info(
                            "olmOCR-2 page %d/%s: %d chars", page_num, pdf_path, len(text)
                        )
                except Exception as exc:
                    logger.warning(
                        "olmOCR-2 page %d failed for %s: %s", page_num, pdf_path, exc
                    )
        finally:
            doc.close()

        return ParserResult(
            backend=self.backend_name,
            available=True,
            artifacts=artifacts,
            metadata={
                "model_id": config.OLMOCR_MODEL_ID,
                "pages_processed": len(artifacts),
            },
        )
