"""Limited four-approach OCR bake-off for medicine/order pages.

This command is intentionally safe for the django-only VM image. It avoids
importing torch, transformers, or paddle at module import time and only runs
heavy local models when explicitly requested.
"""

from __future__ import annotations

import importlib.util
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List

import fitz
from django.core.management.base import BaseCommand, CommandError

from pipeline.services import config
from pipeline.services.handwriting_order_extractor import (
    _candidate_boxes,
    _groq_read_order_crop,
    _prepare_crop,
    _render_crop,
    _target_page_indexes,
)
from pipeline.services.medocr_reference import get_medocr_reference_context
from pipeline.services.medical_short_forms import find_drug_candidates
from pipeline.services.pdf_extractor import normalize_text


@dataclass
class CropProbe:
    report: str
    page_num: int
    role: str
    bbox: List[float]
    source_backend: str
    source_text: str


class Command(BaseCommand):
    help = "Run a resource-limited top-4 OCR approach fit test on medicine/order crops."

    def add_arguments(self, parser):
        parser.add_argument("--pdf-dir", default="", help="Directory containing report PDFs.")
        parser.add_argument("--limit-reports", type=int, default=5)
        parser.add_argument("--max-pages", type=int, default=2)
        parser.add_argument("--max-crops", type=int, default=4)
        parser.add_argument("--run-cloud", action="store_true", help="Run Groq on the first selected crop per report.")
        parser.add_argument("--run-local-models", action="store_true", help="Allow local TrOCR/HTR-VT style model loading.")
        parser.add_argument("--run-paddle", action="store_true", help="Allow PaddleOCR/PP-Structure execution if installed.")
        parser.add_argument("--json", action="store_true")

    def handle(self, *args, **options):
        pdf_dir = _resolve_pdf_dir(options["pdf_dir"])
        limit_reports = max(1, int(options["limit_reports"]))
        max_pages = max(1, int(options["max_pages"]))
        max_crops = max(1, int(options["max_crops"]))
        reports = sorted(pdf_dir.glob("*.pdf"))[:limit_reports]
        if not reports:
            raise CommandError(f"No PDFs found in {pdf_dir}")

        probes_by_report = _collect_probes(reports, max_pages=max_pages, max_crops=max_crops)
        rows = _evaluate_approaches(
            reports=reports,
            probes_by_report=probes_by_report,
            run_cloud=bool(options["run_cloud"]),
            run_local_models=bool(options["run_local_models"]),
            run_paddle=bool(options["run_paddle"]),
        )
        summary = _summarize(reports, probes_by_report, rows)
        payload = {
            "settings": {
                "pdf_dir": str(pdf_dir),
                "limit_reports": limit_reports,
                "max_pages": max_pages,
                "max_crops": max_crops,
                "run_cloud": bool(options["run_cloud"]),
                "run_local_models": bool(options["run_local_models"]),
                "run_paddle": bool(options["run_paddle"]),
                "local_hf_enabled": config.ENABLE_LOCAL_HF_VISION_MODELS,
                "paddle_device": config.PADDLE_STRUCTURE_DEVICE,
            },
            "reports": [str(path.name) for path in reports],
            "crop_probes": {name: [asdict(probe) for probe in probes] for name, probes in probes_by_report.items()},
            "approach_results": rows,
            "summary": summary,
        }

        if options["json"]:
            self.stdout.write(json.dumps(payload, indent=2))
            return

        self.stdout.write(f"Top-4 OCR smoke on {len(reports)} report(s) from {pdf_dir}")
        self.stdout.write(f"Anchor hit reports: {summary['anchor_hit_reports']}/{summary['report_count']}")
        self.stdout.write("rank\tapproach\tstatus\tfit\tseconds\tnotes")
        for row in sorted(rows, key=lambda item: item["rank"]):
            self.stdout.write(
                f"{row['rank']}\t{row['approach']}\t{row['status']}\t"
                f"{row['fit_for_project']}\t{row['seconds']}\t{row['notes']}"
            )


def _resolve_pdf_dir(value: str) -> Path:
    candidates = []
    if value:
        candidates.append(Path(value))
    candidates.extend([
        Path("/data/django_only_10pdf_smoke"),
        Path("/data/drive-download-chenmotherapy data"),
        Path.cwd().parent / "django_only_10pdf_smoke",
        Path.cwd().parent / "drive-download-chenmotherapy data",
        Path.cwd() / "django_only_10pdf_smoke",
    ])
    for candidate in candidates:
        if candidate.exists() and candidate.is_dir():
            return candidate
    raise CommandError("Could not find a PDF directory. Pass --pdf-dir explicitly.")


def _collect_probes(reports: List[Path], *, max_pages: int, max_crops: int) -> Dict[str, List[CropProbe]]:
    original_page_limit = config.HANDWRITING_ORDER_MAX_PAGES_PER_DOCUMENT
    original_crop_limit = config.HANDWRITING_ORDER_MAX_CROPS_PER_PAGE
    config.HANDWRITING_ORDER_MAX_PAGES_PER_DOCUMENT = max_pages
    config.HANDWRITING_ORDER_MAX_CROPS_PER_PAGE = max_crops
    try:
        out: Dict[str, List[CropProbe]] = {}
        for pdf_path in reports:
            out[pdf_path.name] = _collect_report_probes(pdf_path, max_crops=max_crops)
        return out
    finally:
        config.HANDWRITING_ORDER_MAX_PAGES_PER_DOCUMENT = original_page_limit
        config.HANDWRITING_ORDER_MAX_CROPS_PER_PAGE = original_crop_limit


def _collect_report_probes(pdf_path: Path, *, max_crops: int) -> List[CropProbe]:
    doc = fitz.open(str(pdf_path))
    probes: List[CropProbe] = []
    try:
        page_text_map = {
            index + 1: normalize_text(doc[index].get_text("text") or "")
            for index in range(len(doc))
        }
        page_indexes = _target_page_indexes(doc, [], page_text_map)
        for page_index in page_indexes:
            page = doc[page_index]
            page_num = page_index + 1
            for role, bbox, source in _candidate_boxes(page, [], page_num, page_text=page_text_map.get(page_num, ""))[:max_crops]:
                probes.append(
                    CropProbe(
                        report=pdf_path.name,
                        page_num=page_num,
                        role=role,
                        bbox=[float(v) for v in bbox],
                        source_backend=str(source.get("source_backend") or ""),
                        source_text=normalize_text(str(source.get("source_text") or ""))[:180],
                    )
                )
    finally:
        doc.close()
    return probes


def _evaluate_approaches(
    *,
    reports: List[Path],
    probes_by_report: Dict[str, List[CropProbe]],
    run_cloud: bool,
    run_local_models: bool,
    run_paddle: bool,
) -> List[Dict[str, Any]]:
    jobs = [
        ("paddleocr_ppocrv5", lambda: _eval_paddle(reports, run_paddle=run_paddle)),
        ("trocr_large_handwritten", lambda: _eval_trocr(probes_by_report, run_local_models=run_local_models)),
        ("htr_vt", lambda: _eval_htr_vt(run_local_models=run_local_models)),
        ("full_page_cloud_vlm", lambda: _eval_cloud_vlm(reports, probes_by_report, run_cloud=run_cloud)),
    ]
    rows: List[Dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=len(jobs)) as executor:
        future_map = {executor.submit(fn): name for name, fn in jobs}
        for future in as_completed(future_map):
            name = future_map[future]
            try:
                rows.append(future.result())
            except Exception as exc:
                rows.append(_row(name, "error", "no", f"{type(exc).__name__}: {str(exc)[:180]}", rank=_rank(name)))
    return rows


def _eval_paddle(reports: List[Path], *, run_paddle: bool) -> Dict[str, Any]:
    started = time.time()
    installed = importlib.util.find_spec("paddleocr") is not None
    if not installed:
        return _row("paddleocr_ppocrv5", "not_installed", "best_router_when_installed", "Use for detection/layout/table boxes; not present in django-only image.", started)
    if not run_paddle:
        return _row("paddleocr_ppocrv5", "skipped_limited_vm", "best_router_when_enabled", "Installed, but execution was disabled to avoid CPU/GPU load.", started)

    from pipeline.services.parsing.paddle_parser import PaddleStructureParser

    parser = PaddleStructureParser()
    total_artifacts = 0
    errors = []
    for report in reports[:2]:
        result = parser.parse(str(report))
        total_artifacts += len(result.artifacts)
        if result.error:
            errors.append(result.error)
    status = "ok" if total_artifacts else "no_artifacts"
    note = f"{total_artifacts} layout/OCR artifact(s) across <=2 reports"
    if errors:
        note += f"; errors: {' | '.join(errors)[:180]}"
    return _row("paddleocr_ppocrv5", status, "high_for_layout_detection", note, started)


def _eval_trocr(probes_by_report: Dict[str, List[CropProbe]], *, run_local_models: bool) -> Dict[str, Any]:
    started = time.time()
    installed = importlib.util.find_spec("torch") is not None and importlib.util.find_spec("transformers") is not None
    crop_count = sum(len(items) for items in probes_by_report.values())
    if not installed:
        return _row("trocr_large_handwritten", "not_installed", "medium_for_cropped_lines", f"Need torch+transformers; {crop_count} crop candidates are ready.", started)
    if not run_local_models or not config.ENABLE_LOCAL_HF_VISION_MODELS:
        return _row("trocr_large_handwritten", "skipped_limited_vm", "medium_for_cropped_lines", f"Local HF vision disabled; {crop_count} crop candidates are ready.", started)

    from pipeline.services.ocr_backends.trocr_backend import TrOCRBackend

    backend = TrOCRBackend()
    return _row(
        "trocr_large_handwritten",
        "adapter_ready",
        "medium_for_cropped_lines",
        "Runtime dependencies are present. Use the existing eval_handwriting_models command for full local inference.",
        started,
    )


def _eval_htr_vt(*, run_local_models: bool) -> Dict[str, Any]:
    started = time.time()
    repo_candidates = [
        Path.cwd().parent / "experiments" / "htr_bakeoff" / "yutingli0606__htr-vt",
        Path.cwd() / "experiments" / "htr_bakeoff" / "yutingli0606__htr-vt",
        Path("/app/experiments/htr_bakeoff/yutingli0606__htr-vt"),
    ]
    repo = next((path for path in repo_candidates if path.exists()), None)
    if repo is None:
        return _row("htr_vt", "repo_missing", "research_only", "Repo/checkpoints are not deployed in the django-only image.", started)
    checkpoint_files = list(repo.glob("**/*.pth")) + list(repo.glob("**/*.ckpt")) + list(repo.glob("**/*.pt"))
    if not checkpoint_files:
        return _row("htr_vt", "no_checkpoint", "research_only", "Code is present, but no usable pretrained checkpoint is included.", started)
    if not run_local_models:
        return _row("htr_vt", "skipped_limited_vm", "research_benchmark_only", "Checkpoint exists but local model execution was disabled.", started)
    return _row("htr_vt", "needs_adapter", "research_benchmark_only", "Checkpoint exists; add a repo-specific adapter before production use.", started)


def _eval_cloud_vlm(reports: List[Path], probes_by_report: Dict[str, List[CropProbe]], *, run_cloud: bool) -> Dict[str, Any]:
    started = time.time()
    anchor_reports = sum(1 for probes in probes_by_report.values() if any(p.source_backend == "word_anchor" for p in probes))
    if not config.GROQ_API_KEY:
        return _row("full_page_cloud_vlm", "groq_key_missing", "high_for_current_vm", f"{anchor_reports}/{len(reports)} reports have medicine anchors ready.", started)
    if not run_cloud:
        return _row("full_page_cloud_vlm", "skipped_limited_vm", "high_for_current_vm", f"{anchor_reports}/{len(reports)} reports have medicine anchors; cloud calls disabled.", started)

    reference = get_medocr_reference_context()
    rows = []
    for report in reports:
        probes = probes_by_report.get(report.name) or []
        if not probes:
            continue
        probe = probes[0]
        rows.append(_read_probe_cloud(report, probe, reference))
    drug_hits = sum(row.get("drug_hits", 0) for row in rows)
    non_empty = sum(1 for row in rows if row.get("text_chars", 0) > 0)
    return _row("full_page_cloud_vlm", "ok", "best_current_production_fit", f"{non_empty}/{len(rows)} crops returned text; {drug_hits} drug hit(s).", started, extra={"cloud_rows": rows})


def _read_probe_cloud(report: Path, probe: CropProbe, reference: Dict[str, Any]) -> Dict[str, Any]:
    doc = fitz.open(str(report))
    try:
        page = doc[max(0, min(len(doc) - 1, probe.page_num - 1))]
        crop = _prepare_crop(_render_crop(page, probe.bbox))
        result = _groq_read_order_crop(crop, role=probe.role, page_num=probe.page_num, reference=reference)
        text = normalize_text(result.get("raw_text") or result.get("text") or "")
        return {
            "report": report.name,
            "page_num": probe.page_num,
            "source_backend": probe.source_backend,
            "text": text[:400],
            "text_chars": len(text),
            "drug_hits": len(find_drug_candidates(text, limit=8)),
            "confidence": result.get("confidence", 0.0),
        }
    finally:
        doc.close()


def _render_probe(report: Path, probe: CropProbe):
    doc = fitz.open(str(report))
    try:
        page = doc[max(0, min(len(doc) - 1, probe.page_num - 1))]
        return _prepare_crop(_render_crop(page, probe.bbox))
    finally:
        doc.close()


def _summarize(reports: List[Path], probes_by_report: Dict[str, List[CropProbe]], rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "report_count": len(reports),
        "total_crop_candidates": sum(len(items) for items in probes_by_report.values()),
        "anchor_hit_reports": sum(1 for probes in probes_by_report.values() if any(p.source_backend == "word_anchor" for p in probes)),
        "fallback_only_reports": [
            name for name, probes in probes_by_report.items()
            if probes and not any(p.source_backend == "word_anchor" for p in probes)
        ],
        "recommended_now": "full_page_cloud_vlm + deterministic medicine anchors",
        "recommended_next_install": "PaddleOCR/PP-OCRv5 for layout detection, then TrOCR only for clean line crops",
    }


def _row(
    approach: str,
    status: str,
    fit: str,
    notes: str,
    started: float | None = None,
    *,
    rank: int | None = None,
    extra: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    return {
        "rank": rank or _rank(approach),
        "approach": approach,
        "status": status,
        "fit_for_project": fit,
        "notes": notes,
        "seconds": round(time.time() - started, 2) if started else 0.0,
        **(extra or {}),
    }


def _rank(approach: str) -> int:
    return {
        "paddleocr_ppocrv5": 1,
        "trocr_large_handwritten": 2,
        "htr_vt": 3,
        "full_page_cloud_vlm": 4,
    }.get(approach, 99)
