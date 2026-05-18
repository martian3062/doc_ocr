"""Optional YOLO/DocLayNet-style document layout detector."""

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
    "caption": ("text_block", "caption"),
    "figure": ("figure", "figure"),
    "formula": ("text_block", "formula"),
    "list-item": ("text_block", "list_item"),
    "page-footer": ("footer", "footer"),
    "page-header": ("header", "header"),
    "picture": ("figure", "figure"),
    "section-header": ("header", "section_header"),
    "table": ("table", "table"),
    "text": ("text_block", "body"),
    "title": ("header", "title"),
}


class YOLOLayoutParser(DocumentParser):
    backend_name = "yolo_layout"

    def is_available(self) -> bool:
        try:
            import ultralytics  # noqa: F401

            return True
        except Exception:
            return False

    def parse(self, pdf_path: str) -> ParserResult:
        if not self.is_available():
            return ParserResult(
                backend=self.backend_name,
                available=False,
                error="ultralytics_not_installed",
                metadata={"model_id": config.YOLO_LAYOUT_MODEL_ID},
            )

        try:
            from ultralytics import YOLO

            model_path = _resolve_model_path()
            model = YOLO(model_path)
        except Exception as exc:
            return ParserResult(
                backend=self.backend_name,
                available=False,
                error=f"model_load_failed: {exc}",
                metadata={
                    "model_id": config.YOLO_LAYOUT_MODEL_ID,
                    "model_file": config.YOLO_LAYOUT_MODEL_FILE,
                },
            )

        artifacts: list[ParserArtifact] = []
        doc = fitz.open(pdf_path)
        try:
            for page_index in range(len(doc)):
                page = doc[page_index]
                page_num = page_index + 1
                pix = page.get_pixmap(dpi=config.YOLO_LAYOUT_DPI, alpha=False)
                image = Image.open(BytesIO(pix.tobytes("png"))).convert("RGB")
                scale_x = float(page.rect.width) / float(image.width or 1)
                scale_y = float(page.rect.height) / float(image.height or 1)

                results = model.predict(
                    source=image,
                    conf=config.YOLO_LAYOUT_CONFIDENCE,
                    verbose=False,
                )
                reading_order = 0
                for result in results:
                    names: dict[int, Any] = getattr(result, "names", {}) or {}
                    boxes = getattr(result, "boxes", None)
                    if boxes is None:
                        continue

                    for box in boxes:
                        raw_xyxy = box.xyxy[0].tolist()
                        class_id = int(box.cls[0].item()) if getattr(box, "cls", None) is not None else -1
                        label = str(names.get(class_id, class_id)).lower()
                        confidence = float(box.conf[0].item()) if getattr(box, "conf", None) is not None else 0.0
                        artifact_type, role = ROLE_MAP.get(label, ("page_region", label.replace(" ", "_")))
                        bbox = [
                            round(float(raw_xyxy[0]) * scale_x, 2),
                            round(float(raw_xyxy[1]) * scale_y, 2),
                            round(float(raw_xyxy[2]) * scale_x, 2),
                            round(float(raw_xyxy[3]) * scale_y, 2),
                        ]
                        reading_order += 1
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
                                    "model_id": config.YOLO_LAYOUT_MODEL_ID,
                                    "model_file": config.YOLO_LAYOUT_MODEL_FILE,
                                    "raw_label": label,
                                    "render_dpi": config.YOLO_LAYOUT_DPI,
                                },
                            )
                        )
        except Exception as exc:
            logger.warning("YOLO layout parsing failed for %s: %s", pdf_path, exc)
            return ParserResult(
                backend=self.backend_name,
                available=True,
                artifacts=artifacts,
                error=f"inference_failed: {exc}",
                metadata={"model_id": config.YOLO_LAYOUT_MODEL_ID},
            )
        finally:
            doc.close()

        return ParserResult(
            backend=self.backend_name,
            available=True,
            artifacts=artifacts,
            metadata={
                "model_id": config.YOLO_LAYOUT_MODEL_ID,
                "model_file": config.YOLO_LAYOUT_MODEL_FILE,
            },
        )


def _resolve_model_path() -> str:
    model_id = config.YOLO_LAYOUT_MODEL_ID
    if model_id.endswith(".pt") or Path(model_id).exists():
        return model_id

    from huggingface_hub import hf_hub_download

    return hf_hub_download(
        repo_id=model_id,
        filename=config.YOLO_LAYOUT_MODEL_FILE,
        token=config.HF_TOKEN,
    )
