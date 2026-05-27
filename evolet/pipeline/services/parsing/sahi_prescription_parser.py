"""SAHI-style sliced prescription region parser.

This is the production adapter for the SAHI-BAR-inspired lane. Public
SAHI-BAR prescription weights are not exposed as a drop-in package, so the
adapter is deliberately model-configurable: use prescription-specific
Ultralytics/SAHI weights when available, otherwise fall back to the configured
document-layout detector and keep provenance in artifact metadata.
"""

from __future__ import annotations

import logging
from io import BytesIO
from pathlib import Path
from typing import Any

import fitz
from PIL import Image

from .. import config
from .base import DocumentParser, ParserArtifact, ParserResult

logger = logging.getLogger("pipeline")


ROLE_MAP = {
    "medicine": ("handwriting", "medicine"),
    "medication": ("handwriting", "medicine"),
    "drug": ("handwriting", "medicine"),
    "dose": ("handwriting", "dose"),
    "dosage": ("handwriting", "dose"),
    "frequency": ("handwriting", "frequency"),
    "instruction": ("handwriting", "instruction"),
    "prescription": ("page_region", "prescription_region"),
    "signature": ("signature", "signature"),
    "stamp": ("stamp", "stamp"),
    "table": ("table", "medicine_table"),
    "text": ("text_block", "body"),
    "title": ("header", "title"),
}


class SAHIPrescriptionParser(DocumentParser):
    backend_name = "sahi_prescription"

    def is_available(self) -> bool:
        if not config.ENABLE_SAHI_PRESCRIPTION_SEGMENTATION:
            return False
        try:
            import sahi  # noqa: F401
            import ultralytics  # noqa: F401

            return True
        except Exception:
            return False

    def parse(self, pdf_path: str) -> ParserResult:
        if not config.ENABLE_SAHI_PRESCRIPTION_SEGMENTATION:
            return ParserResult(
                backend=self.backend_name,
                available=False,
                error="sahi_prescription_disabled",
                metadata={"model_id": config.SAHI_PRESCRIPTION_MODEL_ID},
            )
        if not self.is_available():
            return ParserResult(
                backend=self.backend_name,
                available=False,
                error="sahi_or_ultralytics_not_installed",
                metadata={"model_id": config.SAHI_PRESCRIPTION_MODEL_ID},
            )

        try:
            from sahi import AutoDetectionModel
            from sahi.predict import get_sliced_prediction

            model_path = _resolve_model_path()
            detection_model = AutoDetectionModel.from_pretrained(
                model_type=config.SAHI_PRESCRIPTION_MODEL_TYPE,
                model_path=model_path,
                confidence_threshold=config.SAHI_PRESCRIPTION_CONFIDENCE,
                device=config.SAHI_PRESCRIPTION_DEVICE,
            )
        except Exception as exc:
            return ParserResult(
                backend=self.backend_name,
                available=False,
                error=f"model_load_failed: {exc}",
                metadata={
                    "model_id": config.SAHI_PRESCRIPTION_MODEL_ID,
                    "model_file": config.SAHI_PRESCRIPTION_MODEL_FILE,
                    "device": config.SAHI_PRESCRIPTION_DEVICE,
                },
            )

        artifacts: list[ParserArtifact] = []
        doc = fitz.open(pdf_path)
        try:
            for page_index in range(min(len(doc), config.SAHI_PRESCRIPTION_MAX_PAGES_PER_DOCUMENT)):
                page = doc[page_index]
                page_num = page_index + 1
                pix = page.get_pixmap(dpi=config.SAHI_PRESCRIPTION_DPI, alpha=False)
                image = Image.open(BytesIO(pix.tobytes("png"))).convert("RGB")
                scale_x = float(page.rect.width) / float(image.width or 1)
                scale_y = float(page.rect.height) / float(image.height or 1)
                result = get_sliced_prediction(
                    image,
                    detection_model,
                    slice_height=config.SAHI_PRESCRIPTION_SLICE_HEIGHT,
                    slice_width=config.SAHI_PRESCRIPTION_SLICE_WIDTH,
                    overlap_height_ratio=config.SAHI_PRESCRIPTION_OVERLAP,
                    overlap_width_ratio=config.SAHI_PRESCRIPTION_OVERLAP,
                    verbose=0,
                )
                for reading_order, pred in enumerate(result.object_prediction_list, start=1):
                    category = getattr(pred, "category", None)
                    raw_label = str(getattr(category, "name", "") or getattr(category, "id", "region")).lower()
                    score = getattr(pred, "score", None)
                    confidence = float(getattr(score, "value", 0.0) or 0.0)
                    box = pred.bbox.to_xyxy()
                    bbox = [
                        round(float(box[0]) * scale_x, 2),
                        round(float(box[1]) * scale_y, 2),
                        round(float(box[2]) * scale_x, 2),
                        round(float(box[3]) * scale_y, 2),
                    ]
                    artifact_type, role = ROLE_MAP.get(raw_label, ("page_region", raw_label.replace(" ", "_")))
                    artifacts.append(
                        ParserArtifact(
                            artifact_type=artifact_type,
                            role=role,
                            backend=self.backend_name,
                            confidence=confidence,
                            bbox=bbox,
                            page_num=page_num,
                            reading_order=reading_order,
                            metadata={
                                "model_id": config.SAHI_PRESCRIPTION_MODEL_ID,
                                "model_file": config.SAHI_PRESCRIPTION_MODEL_FILE,
                                "device": config.SAHI_PRESCRIPTION_DEVICE,
                                "raw_label": raw_label,
                                "render_dpi": config.SAHI_PRESCRIPTION_DPI,
                                "slice_height": config.SAHI_PRESCRIPTION_SLICE_HEIGHT,
                                "slice_width": config.SAHI_PRESCRIPTION_SLICE_WIDTH,
                                "overlap": config.SAHI_PRESCRIPTION_OVERLAP,
                                "method": "sahi_sliced_instance_inference",
                            },
                        )
                    )
        except Exception as exc:
            logger.warning("SAHI prescription parsing failed for %s: %s", pdf_path, exc)
            return ParserResult(
                backend=self.backend_name,
                available=True,
                artifacts=artifacts,
                error=f"inference_failed: {exc}",
                metadata={"model_id": config.SAHI_PRESCRIPTION_MODEL_ID},
            )
        finally:
            doc.close()

        return ParserResult(
            backend=self.backend_name,
            available=True,
            artifacts=artifacts,
            metadata={
                "model_id": config.SAHI_PRESCRIPTION_MODEL_ID,
                "model_file": config.SAHI_PRESCRIPTION_MODEL_FILE,
                "device": config.SAHI_PRESCRIPTION_DEVICE,
                "method": "SAHI-BAR-inspired sliced prescription segmentation",
            },
        )


def _resolve_model_path() -> str:
    model_id = config.SAHI_PRESCRIPTION_MODEL_ID
    if model_id.endswith(".pt") or Path(model_id).exists():
        return model_id

    from huggingface_hub import hf_hub_download

    return hf_hub_download(
        repo_id=model_id,
        filename=config.SAHI_PRESCRIPTION_MODEL_FILE,
        token=config.HF_TOKEN,
    )
