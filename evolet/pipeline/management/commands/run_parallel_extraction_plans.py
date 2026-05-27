"""Run one canonical pipeline plus non-destructive experimental extraction plans."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Dict, Iterable, List

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count
from django.utils import timezone

from pipeline.models import (
    DocumentArtifact,
    Mention,
    NoteLedger,
    PDFDocument,
    PipelineRun,
    ProcessingLog,
)
from pipeline.orchestrator import process_single_document, run_full_pipeline
from pipeline.services import config
from pipeline.services.gpu_utils import release_cuda, setup_gpu


PLAN_MATRIX: List[Dict[str, Any]] = [
    {
        "key": "main_best",
        "kind": PipelineRun.RunKind.MAIN,
        "title": "Main best pipeline",
        "description": "Canonical output: native text, medicine/order crops, Groq vision when configured, cleaner merge, final schema.",
        "full_pipeline": True,
        "overrides": {
            "SKIP_EXISTING": False,
            "ENABLE_LOCAL_HF_LLM": False,
            "ENABLE_LOCAL_HF_VISION_MODELS": False,
            "ENABLE_TRANSFORMER_VALIDATION": False,
        },
    },
    {
        "key": "native_rules",
        "kind": PipelineRun.RunKind.EXPERIMENT,
        "title": "EXP native OCR + rules",
        "description": "Baseline lane: PyMuPDF native text and deterministic regex/medicine rules only.",
        "full_pipeline": False,
        "overrides": {
            "SKIP_EXISTING": False,
            "ENABLE_PAGE_VISION_SWEEP": False,
            "ENABLE_GROQ_VISION_OCR": False,
            "ENABLE_HANDWRITING_ORDER_EXTRACTOR": False,
            "ENABLE_AUTO_SCHEMA": False,
            "ENABLE_LOCAL_HF_LLM": False,
            "ENABLE_LOCAL_HF_VISION_MODELS": False,
        },
    },
    {
        "key": "vlm_crops",
        "kind": PipelineRun.RunKind.EXPERIMENT,
        "title": "EXP OCR + VLM crops",
        "description": "Vision lane: targeted page/crop VLM artifacts plus deterministic extraction.",
        "full_pipeline": False,
        "overrides": {
            "SKIP_EXISTING": False,
            "ENABLE_PAGE_VISION_SWEEP": True,
            "ENABLE_GROQ_VISION_OCR": True,
            "ENABLE_HANDWRITING_ORDER_EXTRACTOR": False,
            "ENABLE_AUTO_SCHEMA": False,
            "ENABLE_LOCAL_HF_LLM": False,
            "ENABLE_LOCAL_HF_VISION_MODELS": False,
        },
    },
    {
        "key": "order_focused",
        "kind": PipelineRun.RunKind.EXPERIMENT,
        "title": "EXP medicine/order focused",
        "description": "Clinical chart lane: deterministic medicine-table anchors and Groq order crops.",
        "full_pipeline": False,
        "overrides": {
            "SKIP_EXISTING": False,
            "ENABLE_PAGE_VISION_SWEEP": False,
            "ENABLE_GROQ_VISION_OCR": True,
            "ENABLE_HANDWRITING_ORDER_EXTRACTOR": True,
            "ENABLE_AUTO_SCHEMA": False,
            "ENABLE_LOCAL_HF_LLM": False,
            "ENABLE_LOCAL_HF_VISION_MODELS": False,
        },
    },
    {
        "key": "sahi_spark_full",
        "kind": PipelineRun.RunKind.EXPERIMENT,
        "title": "EXP SAHI prescription + SPARK schema",
        "description": "20226_tech lane: SAHI-style sliced prescription regions, YOLO layout, local HF vision hooks and SPARK-style schema verification.",
        "full_pipeline": True,
        "overrides": {
            "SKIP_EXISTING": False,
            "DOC_READER_ENABLE_ADVANCED_PARSERS": True,
            "DOC_READER_PARSER_BACKENDS": "sahi_prescription,yolo_layout",
            "ENABLE_SAHI_PRESCRIPTION_SEGMENTATION": True,
            "ENABLE_LOCAL_HF_VISION_MODELS": True,
            "ENABLE_HANDWRITING_OCR": True,
            "ENABLE_MEDICAL_HANDWRITING_OCR": True,
            "ENABLE_SPARK_AGENTIC_SCHEMA": True,
            "SCHEMA_PROVIDER": "local",
            "ENABLE_LOCAL_HF_LLM": True,
        },
    },
]


class Command(BaseCommand):
    help = "Run a canonical pipeline plus experimental extraction plans and store comparison snapshots."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=5)
        parser.add_argument("--patient", default="")
        parser.add_argument("--pdf-dir-prefix", default="")
        parser.add_argument("--skip-main", action="store_true")
        parser.add_argument("--name", default="")

    def handle(self, *args, **options):
        documents = _select_documents(
            limit=max(1, int(options["limit"])),
            patient=str(options["patient"] or "").strip(),
            folder_prefix=str(options["pdf_dir_prefix"] or "").strip(),
        )
        if not documents:
            raise CommandError("No documents selected for extraction-plan comparison.")

        label = options["name"] or timezone.now().strftime("%Y-%m-%d %H:%M")
        self.stdout.write(f"Selected {len(documents)} document(s)")
        for doc in documents:
            self.stdout.write(f" - {doc.original_filename}")

        parent_run = None
        runs = []
        for plan in PLAN_MATRIX:
            if plan["key"] == "main_best" and options["skip_main"]:
                continue
            run = _create_run(plan, documents, label, parent_run)
            if plan["kind"] == PipelineRun.RunKind.MAIN:
                parent_run = run
            elif parent_run:
                run.parent_run = parent_run
                run.save(update_fields=["parent_run"])

            self.stdout.write(self.style.NOTICE(f"Running {plan['title']} -> {run.id}"))
            with _patched_config(plan["overrides"]):
                if plan["full_pipeline"]:
                    run_full_pipeline(run)
                else:
                    _run_experimental_phase1(run, documents)
            run.refresh_from_db()
            snapshot = _build_snapshot(run, plan)
            run.comparison_snapshot = snapshot
            run.save(update_fields=["comparison_snapshot"])
            runs.append(run)
            self.stdout.write(
                self.style.SUCCESS(
                    f"{plan['key']}: mentions={snapshot['totals']['mentions']} "
                    f"artifacts={snapshot['totals']['artifacts']} score={snapshot['score']}"
                )
            )

        if parent_run:
            parent_run.comparison_snapshot = {
                **(parent_run.comparison_snapshot or {}),
                "experiment_run_ids": [str(run.id) for run in runs if run.id != parent_run.id],
            }
            parent_run.save(update_fields=["comparison_snapshot"])


def _select_documents(*, limit: int, patient: str, folder_prefix: str) -> List[PDFDocument]:
    qs = PDFDocument.objects.select_related("patient").order_by("original_filename")
    if patient:
        qs = qs.filter(patient__code__icontains=patient)
    if folder_prefix:
        qs = qs.filter(folder_path__startswith=folder_prefix)
    return list(qs[:limit])


def _create_run(plan: Dict[str, Any], documents: List[PDFDocument], label: str, parent_run) -> PipelineRun:
    run = PipelineRun.objects.create(
        name=f"{plan['title']} {label}",
        model_id="django-only-comparison",
        use_4bit=False,
        total_pdfs=len(documents),
        run_kind=plan["kind"],
        approach_key=plan["key"],
        approach_config={
            "description": plan["description"],
            "overrides": plan["overrides"],
            "full_pipeline": plan["full_pipeline"],
        },
        parent_run=parent_run if plan["kind"] == PipelineRun.RunKind.EXPERIMENT else None,
    )
    run.documents.set(documents)
    return run


def _run_experimental_phase1(run: PipelineRun, documents: List[PDFDocument]) -> None:
    setup_gpu()
    run.started_at = timezone.now()
    run.status = PipelineRun.Status.EXTRACTING
    run.total_pdfs = len(documents)
    run.save(update_fields=["started_at", "status", "total_pdfs"])
    try:
        for doc in documents:
            result = process_single_document(doc, run)
            run.processed_pdfs += 1
            run.total_notes += int(result.get("note_count") or 0)
            run.resolved_notes += int(result.get("resolved") or 0)
            run.unresolved_notes += int(result.get("unresolved") or 0)
            run.total_mentions += int(result.get("regex_mentions") or 0)
            run.save(update_fields=[
                "processed_pdfs",
                "total_notes",
                "resolved_notes",
                "unresolved_notes",
                "total_mentions",
            ])
        run.status = PipelineRun.Status.COMPLETED
        run.completed_at = timezone.now()
        run.save(update_fields=["status", "completed_at"])
        ProcessingLog.objects.create(
            run=run,
            level="info",
            stage="experiment",
            message="Experimental extraction plan completed without updating FinalRecord.",
        )
    except Exception as exc:
        run.status = PipelineRun.Status.FAILED
        run.error_message = str(exc)
        run.completed_at = timezone.now()
        run.save(update_fields=["status", "error_message", "completed_at"])
        raise
    finally:
        release_cuda()


def _build_snapshot(run: PipelineRun, plan: Dict[str, Any]) -> Dict[str, Any]:
    mentions = Mention.objects.filter(run=run)
    artifacts = DocumentArtifact.objects.filter(run=run)
    notes = NoteLedger.objects.filter(document__runs=run)
    category_counts = dict(
        mentions.values("category").annotate(count=Count("id")).values_list("category", "count")
    )
    artifact_counts = dict(
        artifacts.values("artifact_type").annotate(count=Count("id")).values_list("artifact_type", "count")
    )
    patient_rows = []
    for row in (
        mentions.values("patient__code")
        .annotate(mentions=Count("id"), categories=Count("category", distinct=True))
        .order_by("-mentions", "patient__code")
    ):
        patient_rows.append({
            "patient": row["patient__code"],
            "mentions": row["mentions"],
            "categories": row["categories"],
        })
    totals = {
        "documents": run.documents.count(),
        "patients": len(patient_rows),
        "mentions": mentions.count(),
        "artifacts": artifacts.count(),
        "notes": notes.count(),
        "categories": len(category_counts),
    }
    score = (
        totals["mentions"]
        + totals["categories"] * 8
        + artifact_counts.get("handwriting_order", 0) * 6
        + artifact_counts.get("page_region", 0) * 2
    )
    return {
        "approach_key": plan["key"],
        "title": plan["title"],
        "description": plan["description"],
        "totals": totals,
        "category_counts": category_counts,
        "artifact_counts": artifact_counts,
        "patients": patient_rows,
        "score": score,
        "generated_at": timezone.now().isoformat(),
    }


@contextmanager
def _patched_config(overrides: Dict[str, Any]):
    original = {name: getattr(config, name) for name in overrides if hasattr(config, name)}
    try:
        for name, value in overrides.items():
            if hasattr(config, name):
                setattr(config, name, value)
        yield
    finally:
        for name, value in original.items():
            setattr(config, name, value)
