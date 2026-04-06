"""
Evolet Pipeline — REST API Views
================================
JSON endpoints for the decoupled Next.js frontend.
"""

import json
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from .models import (
    FinalRecord, Mention, NoteLedger, PDFDocument,
    Patient, PipelineRun, ProcessingLog
)
from .services.gpu_utils import gpu_info, system_info

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
    return {
        "id": str(r.id),
        "name": r.name,
        "status": r.status,
        "progress": r.progress_percent,
        "started_at": r.started_at.isoformat() if r.started_at else None,
        "completed_at": r.completed_at.isoformat() if r.completed_at else None,
        "total_pdfs": r.total_pdfs,
        "processed_pdfs": r.processed_pdfs,
        "total_mentions": r.total_mentions,
    }

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
        },
        "category_distribution": list(categories),
        "recent_runs": [_serialize_run(r) for r in PipelineRun.objects.all()[:5]],
        "active_run": _serialize_run(active_run) if active_run else None,
        "gpu": gpu_info(),
        "system": system_info(),
    }
    return JsonResponse(data)

def api_patient_list(request):
    """List of patients with filtering."""
    q = request.GET.get("q", "").strip()
    patients = Patient.objects.annotate(
        mention_count=Count("mentions"),
        doc_count=Count("documents"),
    )
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
        })
        
    data = {
        "patient": _serialize_patient(patient),
        "documents": [{"id": str(d.id), "filename": d.original_filename} for d in patient.documents.all()],
        "mentions": mentions_data,
        "final_record": {
            "grouped": final_record.grouped_record if final_record else {},
            "stats": final_record.stats if final_record else {},
        } if final_record else None,
    }
    return JsonResponse(data)

def api_document_list(request):
    """List source documents with patient context."""
    q = request.GET.get("q", "").strip()
    documents = PDFDocument.objects.select_related("patient").all().order_by("-created_at")

    if q:
        documents = documents.filter(
            Q(original_filename__icontains=q)
            | Q(patient__code__icontains=q)
            | Q(patient__display_name__icontains=q)
        )

    data = {
        "documents": [
            {
                "id": str(doc.id),
                "filename": doc.original_filename,
                "patient_id": doc.patient_id,
                "patient_code": doc.patient.code,
                "patient_name": doc.patient.display_name or doc.patient.code,
                "source_type": doc.source_type,
                "page_count": doc.page_count,
                "file_size_bytes": doc.file_size_bytes,
                "created_at": doc.created_at.isoformat(),
            }
            for doc in documents
        ],
        "total": documents.count(),
    }
    return JsonResponse(data)

def api_run_list(request):
    """Full history of pipeline runs."""
    runs = PipelineRun.objects.all().order_by("-created_at")
    return JsonResponse({"runs": [_serialize_run(r) for r in runs]})

def api_run_detail(request, run_id):
    """Run progress, configuration, and recent logs."""
    run = get_object_or_404(PipelineRun, id=run_id)
    logs = run.logs.all().order_by("-created_at")[:100]
    
    data = {
        "run": _serialize_run(run),
        "config": {
            "model_id": run.model_id,
            "use_4bit": run.use_4bit,
        },
        "logs": [
            {
                "level": l.level,
                "stage": l.stage,
                "message": l.message,
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
        
    # ── Add Evidence Links (Mentions appearing in the same PageLedger) ──
    # Note: For now, we cluster by category. 
    # Advanced logic: link nodes if their labels are similar or related.
    
    return JsonResponse({"nodes": nodes, "links": links})
