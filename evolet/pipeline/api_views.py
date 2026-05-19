"""
doc-reader Pipeline — REST API Views
================================
JSON endpoints for the decoupled Next.js frontend.
"""

import json
import math
import os
from django.db.models import Count, Q
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from .models import (
    DocumentArtifact, FinalRecord, Mention, MentionRelation, NoteLedger, PDFDocument,
    Patient, PipelineRun, ProcessingLog
)
from .services.gpu_utils import gpu_info, system_info
from .services import config

def _serialize_patient(p):
    return {
        "id": str(p.id) if hasattr(p, 'id') else p.pk,
        "code": p.code,
        "display_name": p.display_name or p.code,
        "profile_image": p.profile_image.url if p.profile_image else None,
        "created_at": p.created_at.isoformat(),
        "mention_count": getattr(p, "mention_count", 0),
        "doc_count": getattr(p, "doc_count", 0),
    }

def _serialize_run(r):
    elapsed_seconds = int(r.duration_seconds or 0)
    remaining_pdfs = max((r.total_pdfs or 0) - (r.processed_pdfs or 0), 0)
    is_active = r.status in {
        PipelineRun.Status.PENDING,
        PipelineRun.Status.EXTRACTING,
        PipelineRun.Status.TRIAGING,
        PipelineRun.Status.LLM_PROCESSING,
        PipelineRun.Status.MERGING,
        PipelineRun.Status.QC,
    }

    docs_per_minute = 0.0
    eta_seconds = None
    if r.processed_pdfs and elapsed_seconds > 0:
        docs_per_minute = round((r.processed_pdfs / elapsed_seconds) * 60, 2)
        if is_active and remaining_pdfs > 0 and docs_per_minute > 0:
            eta_seconds = int(math.ceil((remaining_pdfs / docs_per_minute) * 60))

    return {
        "id": str(r.id),
        "name": r.name,
        "status": r.status,
        "progress": r.progress_percent,
        "started_at": r.started_at.isoformat() if r.started_at else None,
        "completed_at": r.completed_at.isoformat() if r.completed_at else None,
        "total_pdfs": r.total_pdfs,
        "processed_pdfs": r.processed_pdfs,
        "remaining_pdfs": remaining_pdfs,
        "total_mentions": r.total_mentions,
        "elapsed_seconds": elapsed_seconds,
        "docs_per_minute": docs_per_minute,
        "eta_seconds": eta_seconds,
        "eta_text": _format_duration(eta_seconds) if eta_seconds is not None else None,
        "is_active": is_active,
    }


def _serialize_document(doc):
    return {
        "id": str(doc.id),
        "filename": doc.original_filename,
        "pdf_url": f"/api/v1/documents/{doc.id}/pdf/",
        "patient_id": doc.patient_id,
        "patient_code": doc.patient.code,
        "patient_name": doc.patient.display_name or doc.patient.code,
        "source_type": doc.source_type,
        "page_count": doc.page_count,
        "file_size_bytes": doc.file_size_bytes,
        "created_at": doc.created_at.isoformat(),
    }


def _format_duration(seconds):
    if seconds is None:
        return None

    seconds = max(int(seconds), 0)
    if seconds < 60:
        return f"{seconds}s"

    minutes, rem_seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {rem_seconds}s" if rem_seconds else f"{minutes}m"

    hours, rem_minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {rem_minutes}m" if rem_minutes else f"{hours}h"

    days, rem_hours = divmod(hours, 24)
    return f"{days}d {rem_hours}h" if rem_hours else f"{days}d"

# ── Endpoints ──

def api_dashboard(request):
    """System overview with stats and active runs."""
    active_run = PipelineRun.objects.filter(
        status__in=["pending", "extracting", "triaging", "llm_processing", "merging", "qc"]
    ).first()
    
    # Category Distribution
    categories = Mention.objects.values('category').annotate(count=Count('id')).order_by('-count')
    
    data = {
        "stats": {
            "total_patients": Patient.objects.count(),
            "total_pdfs": PDFDocument.objects.count(),
            "total_mentions": Mention.objects.count(),
            "total_artifacts": DocumentArtifact.objects.count(),
            "total_relations": MentionRelation.objects.count(),
        },
        "category_distribution": list(categories),
        "recent_runs": [_serialize_run(r) for r in PipelineRun.objects.all()[:5]],
        "active_run": _serialize_run(active_run) if active_run else None,
        "gpu": gpu_info(),
        "system": system_info(),
        "pipeline_stack": {
            "parsers_enabled": config.DOC_READER_ENABLE_ADVANCED_PARSERS,
            "parser_backends": [
                name.strip()
                for name in config.DOC_READER_PARSER_BACKENDS.split(",")
                if name.strip()
            ],
            "handwriting_model": config.TROCR_MODEL_ID,
            "medical_handwriting_model": config.MEDICAL_HANDWRITING_MODEL_ID,
            "medocr_reference_dataset": config.MEDOCR_VISION_DATASET_ID,
            "verification_model": config.GOT_OCR_MODEL_ID,
            "handwriting_ocr_enabled": config.ENABLE_HANDWRITING_OCR,
            "medical_handwriting_ocr_enabled": config.ENABLE_MEDICAL_HANDWRITING_OCR,
            "page_vision_sweep_enabled": config.ENABLE_PAGE_VISION_SWEEP,
            "groq_vision_ocr_enabled": config.ENABLE_GROQ_VISION_OCR,
            "handwriting_order_extractor_enabled": config.ENABLE_HANDWRITING_ORDER_EXTRACTOR,
            "medocr_reference_layer_enabled": config.ENABLE_MEDOCR_REFERENCE_LAYER,
            "verification_enabled": config.ENABLE_GOT_VERIFICATION,
            "auto_schema_enabled": config.ENABLE_AUTO_SCHEMA,
            "schema_provider": config.SCHEMA_PROVIDER,
            "schema_model": config.SCHEMA_MODEL,
            "transformer_validation_enabled": config.ENABLE_TRANSFORMER_VALIDATION,
            "medical_validation_required": config.REQUIRE_MEDICAL_VALIDATION,
        },
    }
    return JsonResponse(data)

def api_patient_list(request):
    """List of patients with filtering."""
    q = request.GET.get("q", "").strip()
    run_id = request.GET.get("run", "").strip()
    has_results = request.GET.get("has_results", "").strip().lower() in {"1", "true", "yes"}
    patients = Patient.objects.annotate(
        mention_count=Count("mentions"),
        doc_count=Count("documents"),
    )
    if run_id:
        patients = patients.filter(documents__runs__id=run_id).distinct()
    if has_results:
        patients = patients.filter(final_record__isnull=False)
    if q:
        patients = patients.filter(Q(code__icontains=q) | Q(display_name__icontains=q))
    
    data = {
        "patients": [_serialize_patient(p) for p in patients.order_by("code")],
        "total": patients.count(),
    }
    return JsonResponse(data)

def api_patient_detail(request, patient_id):
    """Detail view including final record and mentions."""
    patient = get_object_or_404(Patient, pk=patient_id)
    mentions = patient.mentions.all().order_by("category", "date_text")
    final_record = getattr(patient, "final_record", None)
    artifacts = patient.artifacts.all().order_by("page_num", "reading_order")[:200]
    relations = patient.relations.all().order_by("relation_type", "created_at")
    
    mentions_data = []
    for m in mentions:
        mentions_data.append({
            "id": str(m.id),
            "category": m.category,
            "label": m.label,
            "value": m.value,
            "normalized_value": m.normalized_value,
            "date_text": m.date_text,
            "certainty": m.certainty,
            "origin": m.origin,
            "evidence_quote": m.evidence_quote,
            "source_pages": m.source_pages,
            "evidence_ids": m.evidence_ids,
            "evidence_artifact_ids": m.evidence_artifact_ids,
        })
        
    data = {
        "patient": _serialize_patient(patient),
        "documents": [_serialize_document(d) for d in patient.documents.select_related("patient").all()],
        "mentions": mentions_data,
        "artifacts": [
            {
                "id": str(a.id),
                "artifact_type": a.artifact_type,
                "role": a.role,
                "backend": a.backend,
                "text": a.text,
                "confidence": a.confidence,
                "bbox": a.bbox,
                "page_num": a.page_num,
                "reading_order": a.reading_order,
                "metadata": a.metadata,
            }
            for a in artifacts
        ],
        "relations": [
            {
                "id": str(r.id),
                "relation_type": r.relation_type,
                "confidence": r.confidence,
                "source_mention_id": str(r.source_mention_id) if r.source_mention_id else None,
                "target_mention_id": str(r.target_mention_id) if r.target_mention_id else None,
                "evidence_artifact_ids": r.evidence_artifact_ids,
                "evidence_pages": r.evidence_pages,
                "metadata": r.metadata,
            }
            for r in relations
        ],
        "final_record": {
            "grouped": final_record.grouped_record if final_record else {},
            "stats": final_record.stats if final_record else {},
            "relation_graph": final_record.relation_graph if final_record else {},
            "timeline_events": final_record.timeline_events if final_record else [],
        } if final_record else None,
    }
    return JsonResponse(data)

def api_document_list(request):
    """List source documents with patient context."""
    q = request.GET.get("q", "").strip()
    run_id = request.GET.get("run", "").strip()
    documents = PDFDocument.objects.select_related("patient").all().order_by("-created_at")

    if run_id:
        documents = documents.filter(runs__id=run_id).distinct()
    if q:
        documents = documents.filter(
            Q(original_filename__icontains=q)
            | Q(patient__code__icontains=q)
            | Q(patient__display_name__icontains=q)
        )

    data = {
        "documents": [_serialize_document(doc) for doc in documents],
        "total": documents.count(),
    }
    return JsonResponse(data)


def api_document_pdf(request, doc_id):
    """Stream a source PDF by document id for side-by-side review."""
    doc = get_object_or_404(PDFDocument, id=doc_id)
    path = doc.folder_path or (doc.file.path if doc.file else "")

    if not path or not os.path.exists(path) or not os.path.isfile(path):
        raise Http404("Source PDF is not available on this server")

    response = FileResponse(
        open(path, "rb"),
        content_type="application/pdf",
        as_attachment=False,
        filename=doc.original_filename,
    )
    response["X-Frame-Options"] = "SAMEORIGIN"
    return response

def api_run_list(request):
    """Full history of pipeline runs."""
    runs = PipelineRun.objects.all().order_by("-created_at")
    return JsonResponse({"runs": [_serialize_run(r) for r in runs]})

def api_run_detail(request, run_id):
    """Run progress, configuration, and recent logs."""
    run = get_object_or_404(PipelineRun, id=run_id)
    logs = run.logs.all().order_by("-created_at")[:100]
    latest_log = logs[0] if logs else None
    documents = run.documents.select_related("patient").all().order_by("original_filename")
    patients = Patient.objects.filter(documents__runs=run).annotate(
        mention_count=Count("mentions", filter=Q(mentions__run=run)),
        doc_count=Count("documents", filter=Q(documents__runs=run)),
    ).distinct().order_by("code")
    
    data = {
        "run": _serialize_run(run),
        "config": {
            "model_id": run.model_id,
            "use_4bit": run.use_4bit,
        },
        "latest_log_at": latest_log.created_at.isoformat() if latest_log else None,
        "documents": [_serialize_document(doc) for doc in documents],
        "patients": [_serialize_patient(patient) for patient in patients],
        "logs": [
            {
                "id": l.id,
                "level": l.level,
                "stage": l.stage,
                "message": l.message,
                "details": l.details,
                "created_at": l.created_at.isoformat(),
            } for l in logs
        ]
    }
    return JsonResponse(data)

def api_knowledge_map(request, patient_id):
    """
    Data structure for Knowledge Map (Node-Link Diagram).
    Nodes: Categories and Mentions.
    Links: Relation within the same document/page or clinical context.
    """
    patient = get_object_or_404(Patient, pk=patient_id)
    mentions = patient.mentions.all()
    relations = patient.relations.all()
    artifacts = patient.artifacts.all()
    
    nodes = []
    links = []
    
    # ── Add Category Nodes ──
    categories = sorted(list(set(m.category for m in mentions)))
    category_map = {}
    for i, cat in enumerate(categories):
        node_id = f"cat_{cat}"
        category_map[cat] = node_id
        nodes.append({
            "id": node_id,
            "label": cat.capitalize(),
            "type": "category",
            "val": 10
        })

    # ── Add Mention Nodes & Links to Categories ──
    for m in mentions:
        mention_node_id = f"men_{m.id}"
        nodes.append({
            "id": mention_node_id,
            "label": m.label,
            "value": m.value,
            "type": "mention",
            "category": m.category,
            "val": 5
        })
        # Link mention to its category
        links.append({
            "source": category_map[m.category],
            "target": mention_node_id,
            "type": "belongs_to"
        })
        
    artifact_lookup = {}
    for artifact in artifacts[:120]:
        artifact_node_id = f"art_{artifact.id}"
        artifact_lookup[str(artifact.id)] = artifact_node_id
        nodes.append({
            "id": artifact_node_id,
            "label": artifact.role or artifact.artifact_type.replace("_", " "),
            "value": artifact.text[:80],
            "type": "artifact",
            "category": artifact.role or artifact.artifact_type,
            "val": 4,
            "page_num": artifact.page_num,
            "backend": artifact.backend,
        })

    for m in mentions:
        mention_node_id = f"men_{m.id}"
        for artifact_id in m.evidence_artifact_ids[:6]:
            artifact_node_id = artifact_lookup.get(str(artifact_id))
            if not artifact_node_id:
                continue
            links.append({
                "source": mention_node_id,
                "target": artifact_node_id,
                "type": "evidence"
            })

    for relation in relations:
        if relation.source_mention_id and relation.target_mention_id:
            links.append({
                "source": f"men_{relation.source_mention_id}",
                "target": f"men_{relation.target_mention_id}",
                "type": relation.relation_type,
            })
    
    return JsonResponse({"nodes": nodes, "links": links})
