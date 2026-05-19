"""Optional capped PaddleOCR PP-StructureV3 parser adapter."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
from io import BytesIO
from pathlib import Path
from typing import Any, Iterable

import fitz
from PIL import Image

from .. import config
from ..pdf_extractor import normalize_text
from .base import DocumentParser, ParserArtifact, ParserResult

logger = logging.getLogger("pipeline")


ROLE_MAP = {
    "doc_title": ("text_block", "title"),
    "paragraph_title": ("text_block", "section_header"),
    "text": ("text_block", "body"),
    "table": ("table", "table"),
    "image": ("figure", "figure"),
    "figure": ("figure", "figure"),
    "figure_title": ("text_block", "caption"),
    "formula": ("text_block", "formula"),
    "seal": ("stamp", "stamp"),
    "header": ("header", "header"),
    "footer": ("footer", "footer"),
}


class PaddleStructureParser(DocumentParser):
    backend_name = "paddle_structure"
    _pipeline: Any = None

    def is_available(self) -> bool:
        try:
            from paddleocr import PPStructureV3  # noqa: F401

            return True
        except Exception:
            return False

    def parse(self, pdf_path: str) -> ParserResult:
        if not self.is_available():
            return ParserResult(
                backend=self.backend_name,
                available=False,
                error="paddleocr_ppstructurev3_not_installed",
                metadata=_settings(),
            )

        gpu_error = _gpu_preflight_error()
        if gpu_error:
            return ParserResult(
                backend=self.backend_name,
                available=False,
                error=gpu_error,
                metadata=_settings(),
            )

        artifacts: list[ParserArtifact] = []
        errors: list[str] = []
        try:
            pipeline = self._load_pipeline()
        except Exception as exc:
            return ParserResult(
                backend=self.backend_name,
                available=False,
                error=f"pipeline_load_failed: {exc}",
                metadata=_settings(),
            )

        doc = fitz.open(pdf_path)
        total_pages = len(doc)
        try:
            page_limit = min(total_pages, max(1, config.PADDLE_STRUCTURE_MAX_PAGES_PER_DOCUMENT))
            with tempfile.TemporaryDirectory(prefix="doc_reader_paddle_") as tmp:
                tmp_path = Path(tmp)
                for page_index in range(page_limit):
                    page = doc[page_index]
                    page_num = page_index + 1
                    try:
                        image_path, scale = _render_page(page, tmp_path, page_num)
                        page_artifacts = _predict_page(
                            pipeline=pipeline,
                            image_path=image_path,
                            page_num=page_num,
                            scale=scale,
                        )
                        artifacts.extend(page_artifacts[: config.PADDLE_STRUCTURE_MAX_ARTIFACTS_PER_PAGE])
                        _release_paddle_cache()
                    except Exception as exc:
                        errors.append(f"p{page_num}: {exc}")
                        logger.warning("Paddle structure skipped page %s for %s: %s", page_num, pdf_path, exc)
        finally:
            doc.close()
            _release_paddle_cache()

        return ParserResult(
            backend=self.backend_name,
            available=True,
            artifacts=artifacts,
            error=" | ".join(errors[:3]),
            metadata={**_settings(), "pages_processed": min(total_pages, config.PADDLE_STRUCTURE_MAX_PAGES_PER_DOCUMENT)},
        )

    @classmethod
    def _load_pipeline(cls):
        if cls._pipeline is not None:
            return cls._pipeline

        os.environ.setdefault("OMP_NUM_THREADS", str(config.PADDLE_STRUCTURE_CPU_THREADS))
        os.environ.setdefault("MKL_NUM_THREADS", str(config.PADDLE_STRUCTURE_CPU_THREADS))
        os.environ.setdefault("FLAGS_paddle_num_threads", str(config.PADDLE_STRUCTURE_CPU_THREADS))
        os.environ.setdefault("FLAGS_fraction_of_gpu_memory_to_use", str(config.PADDLE_STRUCTURE_GPU_MEMORY_FRACTION))
        os.environ.setdefault("CUDA_VISIBLE_DEVICES", str(config.PADDLE_STRUCTURE_GPU_ID))

        from paddleocr import PPStructureV3
        try:
            import paddle

            paddle.set_device(config.PADDLE_STRUCTURE_DEVICE)
        except Exception:
            pass

        kwargs = {
            "device": config.PADDLE_STRUCTURE_DEVICE,
            "use_doc_orientation_classify": False,
            "use_doc_unwarping": False,
            "use_textline_orientation": False,
            "use_table_recognition": config.PADDLE_STRUCTURE_USE_TABLE_RECOGNITION,
            "use_formula_recognition": False,
            "use_seal_recognition": False,
        }
        try:
            cls._pipeline = PPStructureV3(**kwargs)
        except TypeError:
            kwargs.pop("device", None)
            try:
                cls._pipeline = PPStructureV3(**kwargs)
            except TypeError:
                cls._pipeline = PPStructureV3()
        return cls._pipeline


def _settings() -> dict[str, Any]:
    return {
        "pipeline": "PPStructureV3",
        "device": config.PADDLE_STRUCTURE_DEVICE,
        "cpu_threads": config.PADDLE_STRUCTURE_CPU_THREADS,
        "max_pages_per_document": config.PADDLE_STRUCTURE_MAX_PAGES_PER_DOCUMENT,
        "render_dpi": config.PADDLE_STRUCTURE_RENDER_DPI,
        "max_artifacts_per_page": config.PADDLE_STRUCTURE_MAX_ARTIFACTS_PER_PAGE,
        "use_table_recognition": config.PADDLE_STRUCTURE_USE_TABLE_RECOGNITION,
        "gpu_memory_fraction": config.PADDLE_STRUCTURE_GPU_MEMORY_FRACTION,
        "gpu_stop_fraction": config.PADDLE_STRUCTURE_GPU_STOP_FRACTION,
        "gpu_id": config.PADDLE_STRUCTURE_GPU_ID,
    }


def _gpu_preflight_error() -> str:
    if not config.PADDLE_STRUCTURE_DEVICE.lower().startswith("gpu"):
        return ""
    usage = _gpu_usage_fraction(config.PADDLE_STRUCTURE_GPU_ID)
    if usage is None:
        return ""
    if usage >= config.PADDLE_STRUCTURE_GPU_STOP_FRACTION:
        return f"gpu_memory_above_limit: {usage:.2%}"
    return ""


def _gpu_usage_fraction(gpu_id: int) -> float | None:
    try:
        proc = subprocess.run(
            [
                "nvidia-smi",
                f"--id={gpu_id}",
                "--query-gpu=memory.used,memory.total",
                "--format=csv,noheader,nounits",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return None
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    try:
        used, total = [float(part.strip()) for part in proc.stdout.strip().splitlines()[0].split(",")[:2]]
    except Exception:
        return None
    if total <= 0:
        return None
    return used / total


def _release_paddle_cache() -> None:
    try:
        import paddle

        cuda = getattr(getattr(paddle, "device", None), "cuda", None)
        empty_cache = getattr(cuda, "empty_cache", None)
        if callable(empty_cache):
            empty_cache()
    except Exception:
        return


def _render_page(page, output_dir: Path, page_num: int) -> tuple[Path, tuple[float, float]]:
    pix = page.get_pixmap(dpi=config.PADDLE_STRUCTURE_RENDER_DPI, alpha=False)
    image = Image.open(BytesIO(pix.tobytes("png"))).convert("RGB")
    image_path = output_dir / f"page_{page_num}.png"
    image.save(image_path)
    scale_x = float(page.rect.width) / float(image.width or 1)
    scale_y = float(page.rect.height) / float(image.height or 1)
    return image_path, (scale_x, scale_y)


def _predict_page(pipeline, image_path: Path, page_num: int, scale: tuple[float, float]) -> list[ParserArtifact]:
    output = pipeline.predict(str(image_path))
    artifacts: list[ParserArtifact] = []
    reading_order = 0
    for res in output:
        payload = _result_payload(res)
        for item in _iter_result_items(payload):
            artifact = _item_to_artifact(item, page_num=page_num, scale=scale, reading_order=reading_order + 1)
            if artifact:
                reading_order += 1
                artifacts.append(artifact)
    return artifacts


def _result_payload(res: Any) -> dict[str, Any]:
    raw = getattr(res, "json", None)
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}
    if hasattr(res, "res") and isinstance(res.res, dict):
        return {"res": res.res}
    if isinstance(res, dict):
        return res
    return {}


def _iter_result_items(payload: dict[str, Any]) -> Iterable[dict[str, Any]]:
    res = payload.get("res") if isinstance(payload.get("res"), dict) else payload
    for box in ((res.get("layout_det_res") or {}).get("boxes") or []):
        yield {"kind": "layout", **box}
    for item in res.get("parsing_res_list") or []:
        if isinstance(item, dict):
            yield {"kind": "parsing", **item}
    ocr_res = res.get("overall_ocr_res") or {}
    texts = ocr_res.get("rec_texts") or []
    scores = ocr_res.get("rec_scores") or []
    boxes = ocr_res.get("rec_boxes") or ocr_res.get("dt_polys") or []
    for idx, text in enumerate(texts):
        yield {
            "kind": "ocr_line",
            "label": "text",
            "text": text,
            "score": _at(scores, idx, 0.0),
            "coordinate": _box_from_poly(_at(boxes, idx, [])),
        }


def _item_to_artifact(
    item: dict[str, Any],
    *,
    page_num: int,
    scale: tuple[float, float],
    reading_order: int,
) -> ParserArtifact | None:
    label = str(item.get("label") or item.get("block_label") or item.get("type") or "text").lower()
    artifact_type, role = ROLE_MAP.get(label, ("page_region", label.replace(" ", "_")))
    bbox = _scale_box(item.get("coordinate") or item.get("bbox") or item.get("box"), scale)
    text = normalize_text(
        item.get("text")
        or item.get("content")
        or item.get("rec_text")
        or item.get("html")
        or ""
    )
    if not text and artifact_type not in {"table", "figure", "page_region"}:
        return None
    return ParserArtifact(
        artifact_type=artifact_type,
        role=role,
        backend="paddle_structure",
        text=text,
        confidence=float(item.get("score") or item.get("confidence") or 0.0),
        bbox=bbox,
        polygon=[],
        page_num=page_num,
        reading_order=reading_order,
        metadata={
            "raw_label": label,
            "kind": item.get("kind", ""),
            "settings": _settings(),
        },
    )


def _scale_box(box: Any, scale: tuple[float, float]) -> list[float]:
    if not box:
        return []
    if isinstance(box, (list, tuple)) and len(box) == 4 and all(isinstance(x, (int, float)) for x in box):
        x1, y1, x2, y2 = [float(x) for x in box]
    else:
        flat = _box_from_poly(box)
        if not flat:
            return []
        x1, y1, x2, y2 = flat
    sx, sy = scale
    return [round(x1 * sx, 2), round(y1 * sy, 2), round(x2 * sx, 2), round(y2 * sy, 2)]


def _box_from_poly(poly: Any) -> list[float]:
    try:
        points = poly.tolist() if hasattr(poly, "tolist") else poly
        if not points:
            return []
        if len(points) == 4 and all(isinstance(x, (int, float)) for x in points):
            return [float(x) for x in points]
        xs = [float(p[0]) for p in points if len(p) >= 2]
        ys = [float(p[1]) for p in points if len(p) >= 2]
        return [min(xs), min(ys), max(xs), max(ys)] if xs and ys else []
    except Exception:
        return []


def _at(values: Any, idx: int, default: Any) -> Any:
    try:
        return values[idx]
    except Exception:
        return default
