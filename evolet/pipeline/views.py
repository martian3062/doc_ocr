"""
doc-ocr Pipeline — Django Views
================================
HTMX-powered views for the medical report OCR pipeline web UI.

URL namespace: "pipeline"

View groups
-----------
  Dashboard           dashboard()
  Upload & Import     upload(), folder_browser(), import_folder()
  Patients            patient_list(), patient_detail()
  Pipeline Runs       run_list(), run_detail(), start_run(), start_run_one()
  Progress / API      run_progress(), api_gpu_status()
  QC                  qc_summary()
  Downloads           download_patient_json(), download_run_zip()

HTMX pattern used throughout
-----------------------------
For requests with the "HX-Request" header, views return lightweight HTML
fragments (partials/) that HTMX swaps into the DOM.
For normal (non-HTMX) requests, views redirect or return full pages.
"""

import json
import shutil
import tempfile
import logging
from pathlib import Path
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.db.models import Count, Q
from django.db.models.functions import Coalesce
from django.http import FileResponse, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .forms import FolderImportForm, PDFUploadForm
from .models import (
    DocumentArtifact, FinalRecord, Mention, NoteLedger, PDFDocument,
    PageLedger, Patient, PipelineRun,
)
from .services.gpu_utils import gpu_info, system_info
from .services.qc import compute_run_summary
from .services.queue import enqueue_pipeline_run
from .services.source_folders import document_source_payload, documents_source_summary

logger = logging.getLogger("pipeline")

# Status values that indicate a run is still in progress
_ACTIVE_STATUSES = [
    "pending", "extracting", "triaging",
    "llm_processing", "merging", "qc",
]

RECENT_RUN_LIMIT = 8
RUN_LIST_LIMIT = 50
DASHBOARD_DOC_ID_LIMIT = 200

COHORT_LABELS = {
    "chemotherapy": "Chemotherapy",
    "radiation": "Radiation",
    "other": "Other",
}


def _cohort_key_from_text(text: str) -> str:
    value = (text or "").lower()
    if "chemo" in value or "chemotherapy" in value:
        return "chemotherapy"
    if "radio" in value or "radiation" in value:
        return "radiation"
    return "other"


def _cohort_key_for_document(doc) -> str:
    source = getattr(doc, "source_folder", {}) or {}
    folder_name = str(source.get("folder_name", "") if isinstance(source, dict) else "")
    folder_path = str(source.get("folder_path", "") if isinstance(source, dict) else "")

    # Prefer the actual child folder over parent staging names such as
    # "chemo_radio_40...", otherwise radiation files get misclassified.
    folder_key = _cohort_key_from_text(folder_name)
    if folder_key != "other":
        return folder_key

    path_segments = [
        segment.lower()
        for segment in folder_path.replace("\\", "/").split("/")
        if segment
    ]
    if any(segment in {"radiation", "radio"} or segment.startswith("radiation") for segment in path_segments):
        return "radiation"
    if any(segment in {"chemotherapy", "chemo"} or segment.startswith("chemotherapy") for segment in path_segments):
        return "chemotherapy"

    return _cohort_key_from_text(
        " ".join([getattr(doc, "original_filename", ""), getattr(doc.patient, "code", "")])
    )


def _cohort_label(key: str) -> str:
    return COHORT_LABELS.get(key, COHORT_LABELS["other"])


# ─────────────────────────────────────────────────────────────────────────────
# Dashboard
# ─────────────────────────────────────────────────────────────────────────────

def dashboard(request):
    """
    Main dashboard — system overview with stat cards and active run status.

    Counts are pulled directly from the database; no caching needed at
    typical dataset sizes (hundreds of patients).
    """
    total_pdfs = PDFDocument.objects.count()
    all_doc_ids = list(
        PDFDocument.objects.order_by("-created_at")
        .values_list("id", flat=True)[:DASHBOARD_DOC_ID_LIMIT]
    )
    return render(request, "dashboard.html", {
        "total_patients": Patient.objects.count(),
        "total_pdfs":     total_pdfs,
        "total_mentions": Mention.objects.count(),
        "recent_runs":    _recent_runs_queryset(RECENT_RUN_LIMIT),
        "active_run":     PipelineRun.objects.filter(
            status__in=_ACTIVE_STATUSES
        ).first(),
        "gpu":        gpu_info(),
        "all_doc_ids": all_doc_ids,
        "all_doc_count": total_pdfs,
    })


# ─────────────────────────────────────────────────────────────────────────────
# Upload & Import — shared helpers
# ─────────────────────────────────────────────────────────────────────────────

def _import_json_file(file_content: str, filename: str):
    """
    Import a previously exported *_final.json record.

    Creates (or updates) the Patient, PDFDocument, Mention rows, and
    FinalRecord from the JSON payload.  This lets users re-seed the
    database from saved extraction outputs without re-running the pipeline.

    Returns (patient, None) on success, (None, error_string) on failure.
    """
    try:
        data = json.loads(file_content)
    except Exception as exc:
        return None, f"Invalid JSON: {exc}"

    # Patient code: prefer explicit field, fall back to filename stem
    patient_code = data.get(
        "patient_code",
        Path(filename).stem.replace("_final", ""),
    )

    patient, _ = Patient.objects.get_or_create(
        code=patient_code,
        defaults={"display_name": patient_code},
    )

    source_pdf = data.get("source_pdf", f"{patient_code}.pdf")
    doc, _ = PDFDocument.objects.get_or_create(
        patient=patient,
        original_filename=source_pdf,
        defaults={
            "source_type": PDFDocument.SourceType.FOLDER,
            "page_count":  data.get("page_count", 0),
            "folder_path": filename,
        },
    )

    # Rebuild Mention rows from the JSON mention list
    mentions_data = data.get("mentions", [])
    mention_objects = []
    for m in mentions_data:
        if not isinstance(m, dict):
            continue
        # Handle both "origin" (single string) and "origins" (list) formats
        origins = m.get("origins")
        origin  = (
            origins[0] if isinstance(origins, list) and origins
            else m.get("origin", "regex")
        )
        mention_objects.append(Mention(
            patient          = patient,
            document         = doc,
            category         = m.get("category", "unknown"),
            label            = m.get("label", ""),
            value            = m.get("value", ""),
            normalized_value = m.get("normalized_value", ""),
            date_text        = m.get("date_text", ""),
            certainty        = m.get("certainty", "unknown"),
            attributes       = m.get("attributes", {}),
            evidence_quote   = m.get("evidence_quote", ""),
            source_pages     = m.get("source_pages", []),
            evidence_ids     = m.get("evidence_ids", []),
            origin           = origin,
        ))
    if mention_objects:
        Mention.objects.bulk_create(mention_objects)

    # Upsert the FinalRecord
    grouped = data.get("grouped_record", {})
    FinalRecord.objects.update_or_create(
        patient  = patient,
        defaults = {
            "page_count":     data.get("page_count", 0),
            "note_count":     data.get("note_count", 0),
            "mention_count":  len(mentions_data),
            "category_count": len(grouped),
            "grouped_record": grouped,
            "review_flags":   data.get("review_flags", []),
            "traceability":   data.get("document_traceability", {}),
            "stats":          data.get("stats", {}),
        },
    )

    return patient, None


# ─────────────────────────────────────────────────────────────────────────────
# Upload
# ─────────────────────────────────────────────────────────────────────────────

def upload(request):
    """
    Handle multi-file uploads: PDF, JSON (_final.json), or plain text.

    GET  → render upload form
    POST → process files, return HTMX partial or redirect

    File handling per type
    ----------------------
    .json  → parsed as previously exported FinalRecord
    .pdf   → stored as a new PDFDocument for the pipeline
    .txt   → stored as a PDFDocument (text-only path)
    other  → recorded as an error
    """
    if request.method != "POST":
        return render(request, "pipeline/upload.html", {"form": PDFUploadForm()})

    files = request.FILES.getlist("files")
    if not files:
        return render(request, "pipeline/upload.html", {
            "form":  PDFUploadForm(),
            "error": "No files selected — please choose at least one file.",
        })

    imported   = []
    errors     = []
    json_count = pdf_count = txt_count = 0

    for f in files:
        ext = Path(f.name).suffix.lower()

        if ext == ".json":
            content       = f.read().decode("utf-8", errors="replace")
            patient, err  = _import_json_file(content, f.name)
            if err:
                errors.append(f"{f.name}: {err}")
            else:
                json_count += 1
                imported.append({"name": f.name, "type": "json", "patient": patient})

        elif ext == ".pdf":
            patient_code = Path(f.name).stem
            patient, _ = Patient.objects.get_or_create(
                code=patient_code,
                defaults={"display_name": patient_code},
            )
            doc = PDFDocument.objects.create(
                patient          = patient,
                original_filename = f.name,
                file             = f,
                source_type      = PDFDocument.SourceType.UPLOAD,
                file_size_bytes  = f.size,
            )
            pdf_count += 1
            imported.append({"name": f.name, "type": "pdf", "patient": patient, "doc_id": str(doc.id)})

        elif ext in (".txt", ".text"):
            patient_code = Path(f.name).stem
            patient, _ = Patient.objects.get_or_create(
                code=patient_code,
                defaults={"display_name": patient_code},
            )
            doc = PDFDocument.objects.create(
                patient          = patient,
                original_filename = f.name,
                file             = f,
                source_type      = PDFDocument.SourceType.UPLOAD,
                file_size_bytes  = f.size,
            )
            txt_count += 1
            imported.append({"name": f.name, "type": "txt", "patient": patient, "doc_id": str(doc.id)})

        else:
            errors.append(f"{f.name}: Unsupported file type ({ext})")

    # HTMX requests get a lightweight partial; full-page requests redirect
    # Collect IDs of newly created PDF/TXT documents so the Run Pipeline
    # button only processes those files, not the entire database.
    new_doc_ids = [item["doc_id"] for item in imported if "doc_id" in item]

    if request.headers.get("HX-Request"):
        return render(request, "pipeline/partials/upload_result.html", {
            "imported":    imported,
            "count":       len(imported),
            "json_count":  json_count,
            "pdf_count":   pdf_count,
            "txt_count":   txt_count,
            "errors":      errors,
            "new_doc_ids": new_doc_ids,
        })
    return redirect("pipeline:patient_list")


# ─────────────────────────────────────────────────────────────────────────────
# Folder Browser & Import
# ─────────────────────────────────────────────────────────────────────────────

def folder_browser(request):
    """
    Browse a server-side directory for importable files (.pdf, .json, .txt).

    Path is taken from the "path" query parameter; defaults to
    settings.DOC_READER_DATA_DIR. Shows import status for each file (whether
    a PDFDocument row already exists with that folder_path).
    """
    folder_path = request.GET.get("path", str(settings.DOC_READER_DATA_DIR))
    error = None
    files = []
    quick_paths = []
    for candidate in [
        Path(settings.DOC_READER_DATA_DIR),
        Path("/data/django_only_10pdf_smoke"),
        Path("/data/doc-ocr-chemotherapy"),
        Path("/data/drive-download-chenmotherapy data"),
        Path("/data"),
    ]:
        if candidate.exists() and candidate.is_dir() and str(candidate) not in [item["path"] for item in quick_paths]:
            candidate_path = str(candidate)
            quick_paths.append({
                "path": candidate_path,
                "href": f"{request.path}?{urlencode({'path': candidate_path})}",
            })

    try:
        p = Path(folder_path)
        if p.exists() and p.is_dir():
            for f in sorted(p.iterdir()):
                if f.is_file() and f.suffix.lower() in (".pdf", ".json", ".txt"):
                    files.append({
                        "name":     f.name,
                        "path":     str(f),
                        "size_mb":  round(f.stat().st_size / (1024 * 1024), 2),
                        "imported": PDFDocument.objects.filter(folder_path=str(f)).exists(),
                        "type":     f.suffix.lower()[1:],
                    })
        else:
            error = f"Directory not found: {folder_path}"
    except Exception as exc:
        error = str(exc)

    return render(request, "pipeline/folder_browser.html", {
        "folder_path": folder_path,
        "files":       files,
        "file_count":  len(files),
        "error":       error,
        "quick_paths": quick_paths,
        "form":        FolderImportForm(initial={"folder_path": folder_path}),
    })


def _recent_runs_queryset(limit: int = RECENT_RUN_LIMIT):
    """
    Recent run cards are polled frequently by HTMX. Keep this intentionally
    cheap: avoid distinct joins across DocumentArtifact/Mention while a run is
    actively writing thousands of rows.
    """
    limit = max(int(limit or RECENT_RUN_LIMIT), 1)
    runs = list(
        PipelineRun.objects.order_by("-created_at")[:limit]
    )
    if not runs:
        return []

    run_ids = [run.id for run in runs]
    result_counts = {
        item["run_id"]: item["count"]
        for item in FinalRecord.objects.filter(run_id__in=run_ids)
        .values("run_id")
        .annotate(count=Count("id"))
    }
    artifact_counts = {
        item["run_id"]: item["count"]
        for item in DocumentArtifact.objects.filter(run_id__in=run_ids)
        .values("run_id")
        .annotate(count=Count("id"))
    }

    for run in runs:
        result_count = result_counts.get(run.id, 0)
        run.document_count = run.total_pdfs or 0
        run.patient_count = result_count or run.document_count
        run.result_count = result_count
        run.artifact_count = artifact_counts.get(run.id, 0)
    return runs


def recent_runs_partial(request):
    """HTMX fragment for the dashboard recent-run rail."""
    response = render(request, "pipeline/partials/recent_runs.html", {
        "recent_runs": _recent_runs_queryset(RECENT_RUN_LIMIT),
    })
    response["Cache-Control"] = "no-store, max-age=0"
    return response


@require_POST
def import_folder(request):
    """
    Import all PDFs and *_final.json records from a server-side directory.

    Skips files that already have a PDFDocument row (idempotent).
    Returns a lightweight HTMX partial on success, or a JSON error on failure.
    """
    folder_path = request.POST.get("folder_path", "")
    p = Path(folder_path)

    if not p.exists() or not p.is_dir():
        return JsonResponse({"error": f"Directory not found: {folder_path}"}, status=400)

    imported     = 0
    json_imported = 0

    # Import PDFs — patient code derived from the filename stem
    for f in sorted(p.glob("*.pdf")):
        if PDFDocument.objects.filter(folder_path=str(f)).exists():
            continue   # already imported
        patient, _ = Patient.objects.get_or_create(
            code=f.stem,
            defaults={"display_name": f.stem},
        )
        PDFDocument.objects.create(
            patient          = patient,
            original_filename = f.name,
            folder_path      = str(f),
            source_type      = PDFDocument.SourceType.FOLDER,
            file_size_bytes  = f.stat().st_size,
        )
        imported += 1

    # Import pre-existing JSON extraction records
    for f in sorted(p.glob("*_final.json")):
        content      = f.read_text(encoding="utf-8")
        patient, err = _import_json_file(content, str(f))
        if not err:
            json_imported += 1

    total = imported + json_imported

    if request.headers.get("HX-Request"):
        return render(request, "pipeline/partials/upload_result.html", {
            "count":      total,
            "folder":     folder_path,
            "pdf_count":  imported,
            "json_count": json_imported,
        })
    return redirect("pipeline:folder_browser")


# ─────────────────────────────────────────────────────────────────────────────
# Patient Management
# ─────────────────────────────────────────────────────────────────────────────

def patient_list(request):
    """
    List all patients with live search (HTMX partial for "partial=1" requests).

    Annotates each patient with mention_count and doc_count to avoid
    N+1 queries in the template.  Search is case-insensitive on code or name.
    """
    q = request.GET.get("q", "").strip()

    patients = Patient.objects.select_related("final_record").annotate(
        raw_mention_count = Count("mentions", distinct=True),
        mention_count     = Coalesce("final_record__mention_count", Count("mentions", distinct=True)),
        doc_count         = Count("documents", distinct=True),
    )

    if q:
        patients = patients.filter(
            Q(code__icontains=q) | Q(display_name__icontains=q)
        )

    patients = patients.order_by("code")

    # HTMX live-search: return only the table rows partial
    if request.headers.get("HX-Request") and request.GET.get("partial"):
        return render(request, "pipeline/partials/patient_rows.html", {
            "patients": patients,
        })

    return render(request, "pipeline/patient_list.html", {
        "patients": patients,
        "query":    q,
        "total":    patients.count(),
    })


def patient_detail(request, patient_id):
    """
    Full patient record page — mentions grouped by category + timeline view.

    Mentions are fetched once, then split into two views:
    - grouped: {category: [mentions]} for the category accordion
    - timeline: all mentions sorted by (has_date, date, category) for
      the chronological view (empty dates appear at the end)
    """
    patient   = get_object_or_404(Patient, id=patient_id)
    documents = list(patient.documents.all())
    for doc in documents:
        doc.source_folder = document_source_payload(doc)
    final_record = getattr(patient, "final_record", None)
    source_folder = documents_source_summary(documents)
    schema_payload = final_record.grouped_record if final_record else {}
    cleaned_mentions = []
    grouped: dict = {}

    if isinstance(schema_payload, dict) and schema_payload.get("mentions_flat"):
        cleaned_mentions = list(schema_payload.get("mentions_flat") or [])
        grouped = {
            str(category): list(items or [])
            for category, items in (schema_payload.get("grouped_record") or {}).items()
        }
    else:
        raw_mentions = patient.mentions.all().order_by("category", "date_text")
        cleaned_mentions = list(raw_mentions)
        for m in cleaned_mentions:
            grouped.setdefault(m.category, []).append(m)

    timeline_mentions = sorted(
        cleaned_mentions,
        key=lambda x: (
            (x.get("date_text", "") if isinstance(x, dict) else x.date_text) == "",
            x.get("date_text", "") if isinstance(x, dict) else x.date_text,
            x.get("category", "") if isinstance(x, dict) else x.category,
        ),
    )

    notes = NoteLedger.objects.filter(
        document__patient=patient
    ).order_by("page_num", "note_ix")

    return render(request, "pipeline/patient_detail.html", {
        "patient":          patient,
        "documents":        documents,
        "document_count":   len(documents),
        "first_document":   documents[0] if documents else None,
        "mentions":         cleaned_mentions,
        "grouped_mentions": grouped,
        "timeline_mentions":timeline_mentions,
        "final_record":     final_record,
        "source_folder":    source_folder,
        "notes":            notes,
        "mention_count":    len(cleaned_mentions),
        "category_count":   len(grouped),
    })


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline Runs
# ─────────────────────────────────────────────────────────────────────────────

def run_list(request):
    """List all pipeline runs, most recent first."""
    runs = _recent_runs_queryset(RUN_LIST_LIMIT)
    all_doc_ids = list(
        PDFDocument.objects.order_by("-created_at")
        .values_list("id", flat=True)[:DASHBOARD_DOC_ID_LIMIT]
    )
    available_documents_count = PDFDocument.objects.count()
    active_count = PipelineRun.objects.filter(status__in=_ACTIVE_STATUSES).count()
    completed_count = PipelineRun.objects.filter(status=PipelineRun.Status.COMPLETED).count()
    return render(request, "pipeline/run_list.html", {
        "runs": runs,
        "available_documents_count": available_documents_count,
        "all_doc_ids": all_doc_ids,
        "active_count": active_count,
        "completed_count": completed_count,
    })


def run_detail(request, run_id):
    """
    Pipeline run detail page — shows status, progress, and log entries.
    Logs are capped at 50 most recent to keep the page responsive.
    """
    run = get_object_or_404(PipelineRun, id=run_id)
    logs = run.logs.all()[:80]
    documents = list(run.documents.select_related("patient").order_by("original_filename"))
    document_ids = [doc.id for doc in documents]

    page_counts = {
        item["document_id"]: item["count"]
        for item in PageLedger.objects.filter(document_id__in=document_ids)
        .values("document_id")
        .annotate(count=Count("id"))
    }
    note_counts = {
        item["document_id"]: item["count"]
        for item in NoteLedger.objects.filter(document_id__in=document_ids)
        .values("document_id")
        .annotate(count=Count("id"))
    }
    mention_counts_by_doc = {
        item["document_id"]: item["count"]
        for item in Mention.objects.filter(run=run, document_id__in=document_ids)
        .values("document_id")
        .annotate(count=Count("id"))
    }
    artifact_counts_by_doc = {
        item["document_id"]: item["count"]
        for item in DocumentArtifact.objects.filter(run=run, document_id__in=document_ids)
        .values("document_id")
        .annotate(count=Count("id"))
    }

    for doc in documents:
        doc.source_folder = document_source_payload(doc)
        doc.cohort_key = _cohort_key_for_document(doc)
        doc.cohort_label = _cohort_label(doc.cohort_key)
        doc.page_rows = page_counts.get(doc.id, doc.page_count or 0)
        doc.note_rows = note_counts.get(doc.id, 0)
        doc.mention_rows = mention_counts_by_doc.get(doc.id, 0)
        doc.artifact_rows = artifact_counts_by_doc.get(doc.id, 0)
    patients = list(
        Patient.objects.filter(documents__runs=run).distinct().order_by("code")
    )
    patient_ids = [patient.id for patient in patients]
    mention_counts_by_patient = {
        item["patient_id"]: item["count"]
        for item in Mention.objects.filter(run=run, patient_id__in=patient_ids)
        .values("patient_id")
        .annotate(count=Count("id"))
    }
    artifact_counts_by_patient = {
        item["patient_id"]: item["count"]
        for item in DocumentArtifact.objects.filter(run=run, patient_id__in=patient_ids)
        .values("patient_id")
        .annotate(count=Count("id"))
    }
    final_records = {
        item.patient_id: item
        for item in FinalRecord.objects.filter(patient__in=patients).select_related("patient")
    }
    patient_rows = []
    for patient in patients:
        run_docs = [doc for doc in documents if doc.patient_id == patient.id]
        run_mentions = mention_counts_by_patient.get(patient.id, 0)
        run_artifacts = artifact_counts_by_patient.get(patient.id, 0)
        final_record = final_records.get(patient.id)
        cohort_key = "other"
        if run_docs:
            cohort_counts = {}
            for doc in run_docs:
                cohort_counts[doc.cohort_key] = cohort_counts.get(doc.cohort_key, 0) + 1
            cohort_key = max(cohort_counts.items(), key=lambda item: item[1])[0]
        patient_rows.append({
            "patient": patient,
            "documents": run_docs,
            "source_folder": documents_source_summary(run_docs),
            "cohort_key": cohort_key,
            "cohort_label": _cohort_label(cohort_key),
            "mention_count": run_mentions,
            "artifact_count": run_artifacts,
            "final_record": final_record,
            "has_result": bool(final_record),
        })

    cohort_tabs = []
    for key in ("chemotherapy", "radiation", "other"):
        cohort_docs = [doc for doc in documents if doc.cohort_key == key]
        cohort_rows = [row for row in patient_rows if row["cohort_key"] == key]
        if not cohort_docs and not cohort_rows and key == "other":
            continue
        cohort_tabs.append({
            "key": key,
            "label": _cohort_label(key),
            "documents": cohort_docs,
            "patient_rows": cohort_rows,
            "document_count": len(cohort_docs),
            "patient_count": len(cohort_rows),
            "mention_count": sum(row["mention_count"] for row in cohort_rows),
            "artifact_count": sum(row["artifact_count"] for row in cohort_rows),
            "result_count": sum(1 for row in cohort_rows if row["has_result"]),
        })

    totals = {
        "documents": len(documents),
        "patients": len(patients),
        "mentions": sum(mention_counts_by_patient.values()),
        "artifacts": sum(artifact_counts_by_patient.values()),
        "results": sum(1 for item in patient_rows if item["has_result"]),
        "pages": sum(int(getattr(doc, "page_rows", 0) or doc.page_count or 0) for doc in documents),
        "notes": sum(int(getattr(doc, "note_rows", 0) or 0) for doc in documents),
        "cohorts": cohort_tabs,
    }
    return render(request, "pipeline/run_detail.html", {
        "run": run,
        "logs": logs,
        "documents": documents,
        "patient_rows": patient_rows,
        "cohort_tabs": cohort_tabs,
        "totals": totals,
    })


def _comparison_rows(parent):
    related_runs = list(
        PipelineRun.objects.filter(Q(id=parent.id) | Q(parent_run=parent))
        .annotate(
            document_count=Count("documents", distinct=True),
            patient_count=Count("documents__patient", distinct=True),
            result_count=Count("finalrecord", distinct=True),
            artifact_count=Count("artifacts", distinct=True),
        )
        .order_by("created_at")
    )

    rows = []
    best_score = None
    for run in related_runs:
        snapshot = run.comparison_snapshot or {}
        totals = snapshot.get("totals") or {}
        score = int(snapshot.get("score") or 0)
        if best_score is None or score > best_score:
            best_score = score
        rows.append({
            "run": run,
            "snapshot": snapshot,
            "totals": totals,
            "score": score,
            "category_counts": snapshot.get("category_counts") or {},
            "artifact_counts": snapshot.get("artifact_counts") or {},
            "patients": snapshot.get("patients") or [],
        })

    for row in rows:
        row["is_best"] = row["score"] == best_score and best_score is not None
    return rows


def run_compare(request, run_id):
    """Compare a main run against its non-destructive experimental runs."""
    selected = get_object_or_404(PipelineRun, id=run_id)
    parent = selected.parent_run or selected
    rows = _comparison_rows(parent)

    return render(request, "pipeline/run_compare.html", {
        "parent": parent,
        "selected": selected,
        "rows": rows,
    })


def parallel_approaches(request):
    """Show stored main/experimental extraction results grouped by comparison batch."""
    parents = list(
        PipelineRun.objects.filter(parent_run__isnull=True)
        .exclude(comparison_snapshot={})
        .order_by("-created_at")[:20]
    )
    groups = []
    for parent in parents:
        rows = _comparison_rows(parent)
        if len(rows) < 2:
            continue
        groups.append({
            "parent": parent,
            "rows": rows,
            "best": next((row for row in rows if row["is_best"]), None),
        })

    return render(request, "pipeline/parallel_approaches.html", {
        "groups": groups,
        "group_count": len(groups),
    })


@require_POST
def start_run(request):
    """
    Create a new PipelineRun and launch it in a background thread.

    "document_ids" MUST be provided — we never fall back to processing all
    PDFs automatically, which would silently reprocess the whole database.
    Use the dashboard "Run All" button (which explicitly passes all IDs) for
    a full batch run.
    """
    selected_ids = request.POST.getlist("document_ids")
    if not selected_ids:
        messages.error(request, "No documents selected. Upload a PDF first or use Run All.")
        return redirect("pipeline:upload")

    documents = PDFDocument.objects.filter(id__in=selected_ids)
    if not documents.exists():
        return JsonResponse({"error": "No documents to process"}, status=400)

    run = PipelineRun.objects.create(
        name       = request.POST.get("name", f"Run {timezone.now().strftime('%Y-%m-%d %H:%M')}"),
        model_id   = request.POST.get("model_id", "Qwen/Qwen2.5-1.5B-Instruct"),
        use_4bit   = request.POST.get("use_4bit", "on") == "on",
        total_pdfs = documents.count(),
    )
    run.documents.set(documents)

    enqueue_pipeline_run(run)

    return redirect("pipeline:run_detail", run_id=run.id)


@require_POST
def start_run_one(request):
    """
    Quick test: run the pipeline on only the earliest imported PDF.
    Useful for verifying extraction quality on a single document before
    running the full batch.
    """
    doc = PDFDocument.objects.order_by("created_at").first()
    if not doc:
        messages.error(
            request,
            "No PDF documents found. Import a folder or upload a PDF first.",
        )
        if request.headers.get("HX-Request"):
            return JsonResponse({"error": "No documents to process"}, status=400)
        return redirect("pipeline:dashboard")

    run = PipelineRun.objects.create(
        name     = request.POST.get(
            "name",
            f"Single-PDF run {timezone.now().strftime('%Y-%m-%d %H:%M')}",
        ),
        model_id  = request.POST.get("model_id", "Qwen/Qwen2.5-1.5B-Instruct"),
        use_4bit  = request.POST.get("use_4bit", "on") == "on",
        total_pdfs = 1,
    )
    run.documents.set([doc])

    enqueue_pipeline_run(run)

    return redirect("pipeline:run_detail", run_id=run.id)


# ─────────────────────────────────────────────────────────────────────────────
# Progress & API endpoints
# ─────────────────────────────────────────────────────────────────────────────

def run_progress(request, run_id):
    """
    HTMX polling endpoint — returns the run progress partial.
    Called every few seconds by the run_detail template while a run is active.
    """
    run = get_object_or_404(PipelineRun, id=run_id)
    return render(request, "pipeline/partials/run_progress.html", {"run": run})


def api_gpu_status(request):
    """
    HTMX polling endpoint — returns the GPU memory status partial.
    Polled every 10 s by the base template navigation bar.
    """
    return render(request, "pipeline/partials/gpu_status.html", {"gpu": gpu_info()})


def api_system_info(request):
    """
    JSON endpoint — full hardware snapshot + per-PDF ETA estimate.
    Called by the ETA panel on upload and dashboard pages.
    Optionally accepts ?n=<int> to compute ETA for n PDFs.
    """
    info = system_info()
    n = int(request.GET.get("n", 1))
    info["n_pdfs"]       = n
    info["eta_total_s"]  = info["eta_per_pdf_s"] * n
    info["eta_minutes"]  = round(info["eta_total_s"] / 60, 1)
    return JsonResponse(info)


# ─────────────────────────────────────────────────────────────────────────────
# QC Summary
# ─────────────────────────────────────────────────────────────────────────────

def qc_summary(request):
    """
    Quality-control summary across all processed patients.

    Reads from FinalRecord (pre-merged, denormalised) rather than
    re-aggregating from individual Mention rows — fast for large datasets.
    """
    final_records = FinalRecord.objects.select_related("patient").all()

    qc_rows = [
        {
            "patient_id":    fr.patient_id,
            "patient_code":  fr.patient.code,
            "mention_count": fr.mention_count,
            "category_count":fr.category_count,
            "review_flags":  len(fr.review_flags),
            "has_diagnosis": "diagnosis"  in fr.grouped_record,
            "has_medication":"medication" in fr.grouped_record,
            "has_imaging":   "imaging"    in fr.grouped_record,
            "has_pathology": "pathology"  in fr.grouped_record,
            "page_count":    fr.page_count,
            "note_count":    fr.note_count,
        }
        for fr in final_records
    ]

    return render(request, "pipeline/qc_summary.html", {
        "qc_rows": qc_rows,
        "summary": compute_run_summary(qc_rows) if qc_rows else {},
    })


# ─────────────────────────────────────────────────────────────────────────────
# Downloads & Export
# ─────────────────────────────────────────────────────────────────────────────

def download_patient_json(request, patient_id):
    """
    Download a single patient's mentions as a JSON file.

    Returns only the fields useful for external tools; internal DB IDs
    are excluded.  The json.dumps(default=str) handles UUID serialisation.
    """
    patient  = get_object_or_404(Patient, id=patient_id)
    mentions = list(patient.mentions.values(
        "category", "label", "value", "normalized_value",
        "date_text", "certainty", "evidence_quote", "origin",
    ))
    documents = list(patient.documents.all())
    final_record = getattr(patient, "final_record", None)
    source_folder = documents_source_summary(documents)

    response = HttpResponse(
        json.dumps(
            {
                "patient_code": patient.code,
                "source_folder": source_folder,
                "final_record_source_folder": (final_record.stats or {}).get("source_folder") if final_record else {},
                "mentions": mentions,
            },
            indent=2,
            default=str,
        ),
        content_type="application/json",
    )
    response["Content-Disposition"] = (
        f'attachment; filename="{patient.code}_record.json"'
    )
    return response


def download_run_zip(request, run_id):
    """
    Download all patient records from a run as a ZIP archive.

    Each patient gets its own <patient_code>_record.json file inside the ZIP.
    Files are written to a temp directory and the directory is zipped with
    shutil.make_archive; the resulting file is streamed back as a download.
    """
    run      = get_object_or_404(PipelineRun, id=run_id)
    patients = Patient.objects.filter(documents__runs=run).distinct()

    with tempfile.TemporaryDirectory() as tmp_dir:
        zip_dir = Path(tmp_dir) / f"doc_reader_run_{run.id.hex[:8]}"
        zip_dir.mkdir()

        for patient in patients:
            documents = list(patient.documents.filter(runs=run))
            final_record = getattr(patient, "final_record", None)
            mentions = list(patient.mentions.filter(run=run).values(
                "category", "label", "value", "normalized_value",
                "date_text", "certainty", "evidence_quote", "origin",
            ))
            data = {
                "patient_code": patient.code,
                "run_id": str(run.id),
                "source_folder": documents_source_summary(documents),
                "final_record_source_folder": (final_record.stats or {}).get("source_folder") if final_record else {},
                "mentions": mentions,
            }
            (zip_dir / f"{patient.code}_record.json").write_text(
                json.dumps(data, indent=2, default=str),
                encoding="utf-8",
            )

        zip_path = shutil.make_archive(str(zip_dir), "zip", str(zip_dir))
        return FileResponse(
            open(zip_path, "rb"),
            as_attachment=True,
            filename=f"doc_reader_run_{run.id.hex[:8]}.zip",
        )


# ─────────────────────────────────────────────────────────────────────────────
# Cancel run
# ─────────────────────────────────────────────────────────────────────────────

@require_POST
def cancel_run(request, run_id):
    """
    Mark an active run as failed/cancelled immediately.
    The background thread will keep running until its current step finishes,
    but no new steps will be started (the orchestrator checks status between phases).
    """
    run = get_object_or_404(PipelineRun, id=run_id)
    if run.status not in ("completed", "failed"):
        run.status        = PipelineRun.Status.FAILED
        run.error_message = "Cancelled by user"
        run.completed_at  = timezone.now()
        run.save(update_fields=["status", "error_message", "completed_at"])
    if request.headers.get("HX-Request"):
        return HttpResponse("")
    return redirect("pipeline:run_detail", run_id=run.id)


# ─────────────────────────────────────────────────────────────────────────────
# Delete
# ─────────────────────────────────────────────────────────────────────────────

@require_POST
def delete_document(request, doc_id):
    """
    Delete a single PDFDocument and all its extracted data (pages, notes, mentions).
    If the parent patient has no remaining documents after deletion, the patient
    record is also deleted.

    HTMX: returns an empty 200 response so the caller can swap the element out.
    """
    doc     = get_object_or_404(PDFDocument, id=doc_id)
    patient = doc.patient

    # Remove uploaded file from disk (folder-imported files are not deleted
    # from the source directory — only the database record is removed)
    if doc.file:
        try:
            Path(doc.file.path).unlink(missing_ok=True)
        except Exception:
            pass

    doc.delete()   # cascades: PageLedger, NoteLedger, Mention (document FK)

    # Cascade-delete the patient if no documents remain
    patient_deleted = False
    if not patient.documents.exists():
        patient.delete()
        patient_deleted = True

    if request.headers.get("HX-Request"):
        return HttpResponse("")   # HTMX removes the target element
    if patient_deleted:
        return redirect("pipeline:patient_list")
    return redirect("pipeline:patient_detail", patient_id=patient.id)


@require_POST
def delete_patient(request, patient_id):
    """
    Delete a patient record and ALL associated data: documents, pages, notes,
    mentions, and the final record.  Uploaded PDF files are removed from disk.

    HTMX: returns an empty 200 response so the caller can remove the table row.
    """
    patient = get_object_or_404(Patient, id=patient_id)

    # Remove uploaded PDF files from disk
    for doc in patient.documents.filter(source_type=PDFDocument.SourceType.UPLOAD):
        if doc.file:
            try:
                Path(doc.file.path).unlink(missing_ok=True)
            except Exception:
                pass

    patient.delete()   # cascades: PDFDocument → PageLedger, NoteLedger, Mention, FinalRecord

    if request.headers.get("HX-Request"):
        return HttpResponse("")
    return redirect("pipeline:patient_list")
