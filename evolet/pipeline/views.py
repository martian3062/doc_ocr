"""
doc-reader Pipeline — Django Views
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
    Patient, PipelineRun,
)
from .services.gpu_utils import gpu_info, system_info
from .services.qc import compute_run_summary
from .services.queue import enqueue_pipeline_run

logger = logging.getLogger("pipeline")

# Status values that indicate a run is still in progress
_ACTIVE_STATUSES = [
    "pending", "extracting", "triaging",
    "llm_processing", "merging", "qc",
]


# ─────────────────────────────────────────────────────────────────────────────
# Dashboard
# ─────────────────────────────────────────────────────────────────────────────

def dashboard(request):
    """
    Main dashboard — system overview with stat cards and active run status.

    Counts are pulled directly from the database; no caching needed at
    typical dataset sizes (hundreds of patients).
    """
    all_doc_ids = list(PDFDocument.objects.values_list("id", flat=True))
    return render(request, "dashboard.html", {
        "total_patients": Patient.objects.count(),
        "total_pdfs":     PDFDocument.objects.count(),
        "total_mentions": Mention.objects.count(),
        "recent_runs":    _recent_runs_queryset(),
        "active_run":     PipelineRun.objects.filter(
            status__in=_ACTIVE_STATUSES
        ).first(),
        "gpu":        gpu_info(),
        "all_doc_ids": all_doc_ids,
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
        Path("/data/doc-reader-chemotherapy"),
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


def _recent_runs_queryset(limit: int | None = None):
    queryset = (
        PipelineRun.objects.annotate(
            document_count=Count("documents", distinct=True),
            patient_count=Count("documents__patient", distinct=True),
            result_count=Count("finalrecord", distinct=True),
            artifact_count=Count("artifacts", distinct=True),
        )
        .order_by("-created_at")
    )
    return queryset[:limit] if limit else queryset


def recent_runs_partial(request):
    """HTMX fragment for the dashboard recent-run rail."""
    response = render(request, "pipeline/partials/recent_runs.html", {
        "recent_runs": _recent_runs_queryset(),
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
    documents = patient.documents.all()
    final_record = getattr(patient, "final_record", None)
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
        "mentions":         cleaned_mentions,
        "grouped_mentions": grouped,
        "timeline_mentions":timeline_mentions,
        "final_record":     final_record,
        "notes":            notes,
        "mention_count":    len(cleaned_mentions),
        "category_count":   len(grouped),
    })


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline Runs
# ─────────────────────────────────────────────────────────────────────────────

def run_list(request):
    """List all pipeline runs, most recent first."""
    runs = PipelineRun.objects.annotate(
        document_count=Count("documents", distinct=True),
        patient_count=Count("documents__patient", distinct=True),
        result_count=Count("finalrecord", distinct=True),
        artifact_count=Count("artifacts", distinct=True),
    ).order_by("-created_at")
    all_doc_ids = list(PDFDocument.objects.values_list("id", flat=True))
    active_count = PipelineRun.objects.filter(status__in=_ACTIVE_STATUSES).count()
    completed_count = PipelineRun.objects.filter(status=PipelineRun.Status.COMPLETED).count()
    return render(request, "pipeline/run_list.html", {
        "runs": runs,
        "available_documents_count": len(all_doc_ids),
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
    documents = list(
        run.documents.select_related("patient").annotate(
            page_rows=Count("pages", distinct=True),
            note_rows=Count("notes", distinct=True),
            mention_rows=Count("mentions", filter=Q(mentions__run=run), distinct=True),
            artifact_rows=Count("artifacts", filter=Q(artifacts__run=run), distinct=True),
        ).order_by("original_filename")
    )
    patients = list(
        Patient.objects.filter(documents__runs=run).distinct().order_by("code")
    )
    final_records = {
        item.patient_id: item
        for item in FinalRecord.objects.filter(patient__in=patients).select_related("patient")
    }
    patient_rows = []
    for patient in patients:
        run_docs = [doc for doc in documents if doc.patient_id == patient.id]
        run_mentions = Mention.objects.filter(patient=patient, run=run).count()
        run_artifacts = DocumentArtifact.objects.filter(patient=patient, run=run).count()
        final_record = final_records.get(patient.id)
        patient_rows.append({
            "patient": patient,
            "documents": run_docs,
            "mention_count": run_mentions,
            "artifact_count": run_artifacts,
            "final_record": final_record,
            "has_result": bool(final_record),
        })

    totals = {
        "documents": len(documents),
        "patients": len(patients),
        "mentions": Mention.objects.filter(run=run).count(),
        "artifacts": DocumentArtifact.objects.filter(run=run).count(),
        "results": sum(1 for item in patient_rows if item["has_result"]),
        "pages": sum(int(getattr(doc, "page_rows", 0) or doc.page_count or 0) for doc in documents),
        "notes": sum(int(getattr(doc, "note_rows", 0) or 0) for doc in documents),
    }
    return render(request, "pipeline/run_detail.html", {
        "run": run,
        "logs": logs,
        "documents": documents,
        "patient_rows": patient_rows,
        "totals": totals,
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

    response = HttpResponse(
        json.dumps(
            {"patient_code": patient.code, "mentions": mentions},
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
            mentions = list(patient.mentions.filter(run=run).values(
                "category", "label", "value", "normalized_value",
                "date_text", "certainty", "evidence_quote", "origin",
            ))
            data = {"patient_code": patient.code, "mentions": mentions}
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
