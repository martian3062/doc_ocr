"""
doc-reader Pipeline Orchestrator
=============================
Coordinates the complete medical-report processing pipeline across six phases:

  Phase 1 — Extract + Triage  (CPU / IO-bound, per document)
    1a. PDF Extraction  : native text + DocTR OCR fallback  → PageLedger rows
    1b. Segmentation    : split pages into note-sized units  → NoteLedger rows
    1c. Regex Extraction: deterministic mention extraction   → Mention rows (origin=regex)
    1d. Triage          : classify notes as resolved vs. LLM-needed

  Phase 2 — LLM Processing    (GPU-bound, batched across all unresolved notes)
    Load Qwen 2.5 1.5B once, process all unresolved notes, unload.  → Mention rows (origin=llm)

  Phase 3 — Merge             (CPU, per patient)
    Deduplicate regex + LLM mentions per patient → FinalRecord rows

  Phase 3b — Text Correction  (CPU, per patient)  ← NEW
    Spell-check and grammar-clean all extracted mention values using
    SymSpell + medical whitelist + rule-based fixes.

  Phase 3c — Deep Schema      (CPU, per patient)  ← NEW
    Build nested clinical sub-schemas (diagnosis staging, medication
    dose/frequency, imaging modality, etc.) from the corrected flat mentions.
    Stored in FinalRecord.grouped_record as schema_version="2.0".

  Phase 4 — Profile Photos    (CPU / IO-bound, per document)
    Extract passport photo from page 1 of each PDF

  Phase 5 — QC                (CPU, logging only)
    Log processing summary to ProcessingLog

Design decisions
----------------
- Phases 1 runs serially (not threaded) because DocTR holds the GPU during OCR.
  True parallelism would require multiple GPUs or CPU-only OCR.
- The LLM is loaded ONCE after all documents are extracted, batched across every
  patient, then unloaded immediately — minimising total GPU memory hold time.
- DB writes use bulk_create() wherever possible to reduce round-trips.
- Progress is persisted to the database after each document so the UI polling
  endpoint always reflects current state.
"""

import concurrent.futures
import logging
import threading
import traceback
from typing import Dict, List

from django.db.models import F
from django.utils import timezone

from .models import (
    Patient, PDFDocument, PipelineRun, PageLedger, NoteLedger,
    Mention, FinalRecord, ProcessingLog, DocumentArtifact, MentionRelation,
)
from .services import config
from .services.pdf_extractor    import extract_pdf_pages, normalize_text, word_count
from .services.note_segmenter   import triage_notes
from .services.llm_engine       import load_model, process_unresolved_notes, unload_model
from .services.merger           import build_final_record
from .services.text_corrector   import correct_all_mentions, correction_report
from .services.schema_builder   import build_deep_schema
from .services.auto_schema      import enrich_with_auto_schema
from .services.qc               import compute_qc_metrics
from .services.photo_extractor  import extract_profile_photo
from .services.gpu_utils        import setup_gpu, release_cuda
from .services.layout_segmenter import extract_document_artifacts, segment_layout_aware_notes
from .services.regex_extractor  import extract_note_mentions
from .services.relation_extractor import build_relation_payload
from .services.validation import validate_final_record
from .services.artifact_extractor import extract_artifact_mentions

logger = logging.getLogger("pipeline")


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _log(run: PipelineRun, level: str, stage: str, message: str, details: dict = None) -> None:
    """Persist a ProcessingLog entry for *run*."""
    ProcessingLog.objects.create(
        run=run, level=level, stage=stage,
        message=message, details=details or {},
    )


def _update_run(run: PipelineRun, **kwargs) -> None:
    """
    Atomically update specific fields on *run* and persist to the database.
    Uses update_fields= so only the changed columns are written.
    """
    for k, v in kwargs.items():
        setattr(run, k, v)
    run.save(update_fields=list(kwargs.keys()))


def _inc_run(run_id, **kwargs) -> None:
    """Atomically increment counter fields using DB-level F() expressions.
    Safe to call from multiple threads simultaneously."""
    PipelineRun.objects.filter(id=run_id).update(
        **{k: F(k) + v for k, v in kwargs.items()}
    )


# ─────────────────────────────────────────────────────────────────────────────
# Phase 1 — Document extraction, segmentation, regex, triage
# ─────────────────────────────────────────────────────────────────────────────

def process_single_document(doc: PDFDocument, run: PipelineRun) -> dict:
    """
    Run Phases 1a–1d on a single PDF document.

    Returns a result dict used by the orchestrator to:
    - Update run-level progress counters
    - Collect unresolved notes for the LLM phase

    The LLM phase is intentionally decoupled from this function so that
    the model is loaded only once across all documents, not once per PDF.

    Result keys
    -----------
    patient_code        : str
    status              : "ok" | "error"
    page_count          : int
    note_count          : int
    resolved            : int  — notes fully covered by regex
    unresolved          : int  — notes queued for LLM
    regex_mentions      : int  — total regex-extracted mentions
    unresolved_notes_data: list — note dicts for LLM phase
    error               : str  (only when status="error")
    """
    patient  = doc.patient
    pdf_path = doc.folder_path or doc.file.path

    result = {
        "patient_code":          patient.code,
        "status":                "ok",
        "page_count":            0,
        "note_count":            0,
        "resolved":              0,
        "unresolved":            0,
        "regex_mentions":        0,
        "unresolved_notes_data": [],
    }

    try:
        # ── Skip if already processed ─────────────────────────────────────
        # When SKIP_EXISTING is True and a FinalRecord already exists for
        # this patient, return cached counts from the DB rather than
        # re-extracting everything.  This makes re-runs near-instant for
        # documents that were fully processed in a previous run.
        if config.SKIP_EXISTING and FinalRecord.objects.filter(patient=patient).exists():
            page_ct = doc.page_count
            note_ct = NoteLedger.objects.filter(document=doc).count()
            res_ct  = NoteLedger.objects.filter(document=doc, is_resolved=True).count()
            men_ct  = Mention.objects.filter(patient=patient, run__isnull=False, origin="regex").count()
            result.update({
                "page_count":   page_ct,
                "note_count":   note_ct,
                "resolved":     res_ct,
                "unresolved":   0,
                "regex_mentions": men_ct,
                "status":       "skipped",
            })
            _log(run, "info", "extraction",
                 f"{patient.code}: skipped (FinalRecord already exists)")
            return result

        # ── 1a: PDF Extraction ────────────────────────────────────────────
        # Two-phase: native text first, then DocTR OCR for weak pages.
        page_rows = extract_pdf_pages(pdf_path)
        result["page_count"] = len(page_rows)

        doc.page_count = len(page_rows)
        doc.save(update_fields=["page_count"])

        # Persist per-page extraction results to the PageLedger table.
        # Delete any stale rows first (re-run scenario).
        PageLedger.objects.filter(document=doc).delete()
        PageLedger.objects.bulk_create([
            PageLedger(
                document        = doc,
                page_num        = row["page_num"],
                text            = row["text"],
                selected_source = row["selected_source"],
                char_count      = row["char_count"],
                word_count      = row["word_count"],
                alpha_ratio     = row["alpha_ratio"],
                need_ocr        = row["need_ocr"],
                page_width      = row.get("page_width", 0.0),
                page_height     = row.get("page_height", 0.0),
                layout_blocks   = row.get("layout_blocks", []),
            )
            for row in page_rows
        ])

        artifacts = extract_document_artifacts(pdf_path, page_rows)
        page_map = {
            page.page_num: page
            for page in PageLedger.objects.filter(document=doc)
        }
        DocumentArtifact.objects.filter(document=doc).delete()
        artifact_objects = [
            DocumentArtifact(
                patient=patient,
                document=doc,
                page=page_map.get(item["page_num"]),
                run=run,
                artifact_type=item["artifact_type"],
                role=item.get("role", ""),
                backend=item.get("backend", ""),
                text=item.get("text", ""),
                normalized_text=item.get("normalized_text", ""),
                confidence=item.get("confidence", 0.0),
                bbox=item.get("bbox", []),
                polygon=item.get("polygon", []),
                page_num=item.get("page_num", 0),
                reading_order=item.get("reading_order", 0),
                metadata=item.get("metadata", {}),
            )
            for item in artifacts
        ]
        if artifact_objects:
            DocumentArtifact.objects.bulk_create(artifact_objects)
            artifacts = list(
                DocumentArtifact.objects.filter(document=doc).order_by("page_num", "reading_order").values(
                    "id", "artifact_type", "role", "backend", "text", "normalized_text",
                    "confidence", "bbox", "polygon", "page_num", "reading_order", "metadata",
                )
            )

        # ── 1b: Note Segmentation ─────────────────────────────────────────
        # Split each page into clinical-note-sized chunks using layout-aware artifacts.
        notes = segment_layout_aware_notes(page_rows, artifacts)
        result["note_count"] = len(notes)

        # ── 1c: Regex Extraction ──────────────────────────────────────────
        # Run deterministic extraction on every note.
        # Parallelised with threads: regex is I/O-safe (pure Python) and each
        # note is fully independent, so ThreadPoolExecutor gives real overlap
        # on multi-core machines.
        if config.LLM_EXTRACT_ALL_NOTES:
            llm_notes = [
                note for note in notes
                if int(note.get("signal") or 0) >= config.MIN_SIGNAL_FOR_LLM
                and (not note.get("low_value") or config.SEND_SHORT_NOTES_TO_LLM)
            ][: config.MAX_NOTES_PER_PATIENT_FOR_LLM]
            triaged = {"resolved": [], "unresolved": llm_notes}
            result["regex_mentions"] = 0
        else:
            _workers = min(config.MAX_WORKERS, max(1, len(notes)))
            with concurrent.futures.ThreadPoolExecutor(max_workers=_workers) as _ex:
                _results = list(_ex.map(extract_note_mentions, notes))
            regex_results = {notes[i]["note_id"]: _results[i] for i in range(len(notes))}
            result["regex_mentions"] = sum(len(v) for v in regex_results.values())
            triaged = triage_notes(notes, regex_results)

        # ── 1d: Triage ────────────────────────────────────────────────────
        # Classify notes: resolved (regex enough) vs. unresolved (needs LLM).
        result["resolved"] = len(triaged["resolved"])
        result["unresolved"] = len(triaged["unresolved"])

        # Build a set of note_ids that need LLM for O(1) lookup below
        unresolved_ids = {n["note_id"] for n in triaged["unresolved"]}

        # Persist note ledger
        NoteLedger.objects.filter(document=doc).delete()
        NoteLedger.objects.bulk_create([
            NoteLedger(
                document     = doc,
                note_id      = note["note_id"],
                page_num     = note["page_num"],
                note_ix      = note["note_ix"],
                text         = note["text"],
                text_hash    = note["hash"],
                signal_score = note["signal"],
                is_low_value = note["low_value"],
                needs_llm    = note["note_id"] in unresolved_ids,
                is_resolved  = False if config.LLM_EXTRACT_ALL_NOTES else note["note_id"] not in unresolved_ids,
                layout_hint  = note.get("layout_hint", {}),
                artifact_ids = note.get("artifact_ids", []),
            )
            for note in notes
        ])
        note_map = {
            note.note_id: note
            for note in NoteLedger.objects.filter(document=doc)
        }

        artifact_mentions = extract_artifact_mentions(artifacts, page_rows)

        # Persist regex-extracted mentions (only from resolved notes)
        Mention.objects.filter(patient=patient, run=run, origin="regex").delete()
        regex_mention_objects = []
        for m in artifact_mentions:
            regex_mention_objects.append(Mention(
                patient          = patient,
                document         = doc,
                run              = run,
                note             = None,
                category         = m["category"],
                label            = m["label"],
                value            = m["value"],
                normalized_value = m["normalized_value"],
                date_text        = m.get("date_text", ""),
                certainty        = m.get("certainty", "unknown"),
                attributes       = m.get("attributes", {}),
                evidence_quote   = m.get("evidence_quote", ""),
                source_pages     = m.get("source_pages", []),
                evidence_ids     = m.get("evidence_ids", []),
                evidence_artifact_ids = m.get("evidence_artifact_ids", []),
                origin           = "regex",
            ))
        if not config.LLM_EXTRACT_ALL_NOTES:
            for resolved_note in triaged["resolved"]:
                for m in resolved_note.get("mentions", []):
                    note_obj = note_map.get(resolved_note["note_id"])
                    regex_mention_objects.append(Mention(
                        patient          = patient,
                        document         = doc,
                        run              = run,
                        note             = note_obj,
                        category         = m["category"],
                        label            = m["label"],
                        value            = m["value"],
                        normalized_value = m["normalized_value"],
                        date_text        = m.get("date_text", ""),
                        certainty        = m.get("certainty", "unknown"),
                        attributes       = m.get("attributes", {}),
                        evidence_quote   = m.get("evidence_quote", ""),
                        source_pages     = m.get("source_pages", []),
                        evidence_ids     = m.get("evidence_ids", []),
                        evidence_artifact_ids = list(note_obj.artifact_ids) if note_obj else [],
                        origin           = "regex",
                    ))
        if regex_mention_objects:
            Mention.objects.bulk_create(regex_mention_objects)
        result["regex_mentions"] += len(artifact_mentions)

        # Pass unresolved notes up to the orchestrator for batched LLM processing
        result["unresolved_notes_data"] = triaged["unresolved"]

        _log(run, "info", "extraction",
             f"{patient.code}: {len(page_rows)} pages, {len(notes)} grouped notes, "
             f"{result['unresolved']} queued for LLM-first extraction")

    except Exception as exc:
        result["status"] = "error"
        result["error"]  = str(exc)
        _log(run, "error", "extraction",
             f"{patient.code}: {exc}",
             {"traceback": traceback.format_exc()})

    return result


# ─────────────────────────────────────────────────────────────────────────────
# Phase 2 — LLM processing
# ─────────────────────────────────────────────────────────────────────────────

def run_llm_phase(run: PipelineRun, doc_results: List[dict]) -> None:
    """
    Process all unresolved notes from all documents through the LLM.

    The model is loaded once here, batches are processed across all patients,
    then the model is unloaded immediately.  This minimises total GPU hold
    time and avoids OOM from holding the model between documents.

    Parameters
    ----------
    run         : current PipelineRun
    doc_results : list of result dicts from process_single_document()
    """
    _update_run(run, status=PipelineRun.Status.LLM_PROCESSING)

    # Collect all unresolved notes and build a note_id → patient_code map
    all_unresolved = []
    patient_map: Dict[str, str] = {}   # note_id → patient_code

    for res in doc_results:
        if res["status"] != "ok":
            continue
        for note in res.get("unresolved_notes_data", []):
            all_unresolved.append(note)
            patient_map[note["note_id"]] = res["patient_code"]

    if not all_unresolved:
        logger.info("No unresolved notes — skipping LLM phase")
        return

    logger.info("LLM phase: %d unresolved notes across %d documents",
                len(all_unresolved), len(doc_results))

    # Load model (singleton — already loaded if called twice)
    if config.LLM_PROVIDER == "local":
        if not config.ENABLE_LOCAL_HF_LLM:
            _log(run, "warning", "llm", "Local HF LLM is disabled; skipping unresolved-note LLM phase")
            return
        try:
            load_model(model_id=run.model_id, use_4bit=run.use_4bit)
        except Exception as exc:
            _log(run, "error", "llm", f"Model load failed: {exc}")
            return
    elif config.LLM_PROVIDER == "groq":
        if not config.GROQ_API_KEY:
            _log(run, "warning", "llm", "Groq key missing; skipping unresolved-note LLM phase")
            return
    else:
        _log(run, "warning", "llm", f"Unsupported LLM provider: {config.LLM_PROVIDER}")
        return

    def _progress(done: int, total: int) -> None:
        _log(run, "info", "llm", f"LLM batch {done}/{total} complete")

    try:
        llm_mentions = process_unresolved_notes(
            all_unresolved, progress_callback=_progress
        )

        def _short(value, max_length: int, default: str = "") -> str:
            text = str(value or default).strip()
            return text[:max_length]

        # Map LLM mentions back to their patient / document
        mention_objects = []
        for m in llm_mentions:
            # evidence_ids[0] holds the note_id, which maps back to patient_code
            note_id      = (m.get("evidence_ids") or [""])[0]
            patient_code = patient_map.get(note_id, "")
            if not patient_code:
                continue

            try:
                patient = Patient.objects.get(code=patient_code)
            except Patient.DoesNotExist:
                continue

            # Use the first associated document (patients in this pipeline are 1:1 with PDFs)
            doc = patient.documents.first()
            note_obj = NoteLedger.objects.filter(document=doc, note_id=note_id).first() if doc else None

            mention_objects.append(Mention(
                patient          = patient,
                document         = doc,
                run              = run,
                note             = note_obj,
                category         = _short(m.get("category"), 50, "other"),
                label            = _short(m.get("label"), 200, "LLM mention"),
                value            = m["value"],
                normalized_value = m["normalized_value"],
                date_text        = _short(m.get("date_text"), 100),
                certainty        = _short(m.get("certainty"), 20, "unknown"),
                attributes       = m.get("attributes", {}),
                evidence_quote   = m.get("evidence_quote", ""),
                source_pages     = m.get("source_pages", []),
                evidence_ids     = m.get("evidence_ids", []),
                evidence_artifact_ids = list(note_obj.artifact_ids) if note_obj else [],
                origin           = "llm",
            ))

        if mention_objects:
            Mention.objects.bulk_create(mention_objects)

        run.refresh_from_db(fields=["total_mentions"])
        _update_run(run, total_mentions=run.total_mentions + len(llm_mentions))
        _log(run, "info", "llm", f"LLM extracted {len(llm_mentions)} mentions")

    except Exception as exc:
        _log(run, "error", "llm", f"LLM phase failed: {exc}",
             {"traceback": traceback.format_exc()})

    finally:
        # Always unload the model after this phase to free GPU for other work
        if config.LLM_PROVIDER == "local":
            unload_model()


# ─────────────────────────────────────────────────────────────────────────────
# Phase 3 — Merge
# ─────────────────────────────────────────────────────────────────────────────

def run_merge_phase(run: PipelineRun) -> None:
    """
    Merge and deduplicate all mentions per patient into FinalRecord rows.

    Fetches all mentions (regex + LLM) for each patient involved in this
    run, runs the merger, and upserts the FinalRecord.

    Uses update_or_create so that re-running a pipeline on the same patient
    updates the record rather than creating a duplicate.
    """
    _update_run(run, status=PipelineRun.Status.MERGING)

    patients = Patient.objects.filter(documents__runs=run).distinct()

    def _group_mentions_for_schema(mentions: list[dict]) -> dict[str, list[dict]]:
        grouped: dict[str, list[dict]] = {}
        for mention in mentions:
            category = str(mention.get("category") or "uncategorized")
            grouped.setdefault(category, []).append(mention)
        return grouped

    def _merge_one(patient: Patient) -> None:
        try:
            mentions = list(
                Mention.objects.filter(patient=patient, run=run).values(
                    "category", "label", "value", "normalized_value",
                    "date_text", "certainty", "attributes",
                    "evidence_quote", "source_pages", "evidence_ids",
                    "evidence_artifact_ids", "origin",
                )
            )
            run_docs = list(patient.documents.filter(runs=run).order_by("original_filename"))
            doc = run_docs[0] if run_docs else patient.documents.first()
            source_pdf = ", ".join(d.original_filename for d in run_docs) if run_docs else (doc.original_filename if doc else "")
            page_qs = PageLedger.objects.filter(document__in=run_docs) if run_docs else PageLedger.objects.filter(document=doc)
            note_qs = NoteLedger.objects.filter(document__in=run_docs) if run_docs else NoteLedger.objects.filter(document=doc)
            page_count = page_qs.count() if doc else 0
            note_count = note_qs.count() if doc else 0
            source_text = "\n\n".join(
                page_qs.order_by("document__original_filename", "page_num").values_list("text", flat=True)
            )
            source_pages = [
                {
                    "document": page.document.original_filename if page.document else "",
                    "page_num": page.page_num,
                    "selected_source": page.selected_source,
                    "text": page.text,
                }
                for page in page_qs.select_related("document").order_by("document__original_filename", "page_num")
            ]

            final_data = build_final_record(
                patient_code = patient.code,
                source_pdf   = source_pdf,
                page_count   = page_count,
                note_count   = note_count,
                all_mentions = mentions,
            )
            corrected_mentions = correct_all_mentions(final_data["mentions"])
            spell_check_payload = correction_report(final_data["mentions"], corrected_mentions)
            corrected_grouped_record = _group_mentions_for_schema(corrected_mentions)
            deep_schema = build_deep_schema(
                patient_code   = patient.code,
                source_pdf     = source_pdf,
                mentions_flat  = corrected_mentions,
                grouped_record = corrected_grouped_record,
                traceability   = final_data["traceability"],
                stats          = final_data["stats"],
                review_flags   = final_data["review_flags"],
                page_count     = final_data["page_count"],
                note_count     = final_data["note_count"],
            )
            deep_schema = enrich_with_auto_schema(
                base_schema=deep_schema,
                patient_code=patient.code,
                source_pdf=source_pdf,
                source_text=source_text,
                mentions=corrected_mentions,
                spell_check=spell_check_payload,
                source_pages=source_pages,
            )
            relation_payload = build_relation_payload(corrected_mentions)
            validation_payload = validate_final_record(
                patient_code=patient.code,
                source_pdf=source_pdf,
                mentions=corrected_mentions,
                grouped_record=deep_schema,
                stats=final_data["stats"],
                review_flags=final_data["review_flags"],
            )
            merged_review_flags = sorted(
                set(final_data["review_flags"] + validation_payload.get("flags", []))
            )
            merged_stats = {
                **final_data["stats"],
                "validation": validation_payload,
                "auto_schema": deep_schema.get("quality_checks", {}).get("auto_schema", {}),
                "text_corrections_applied": spell_check_payload.get("corrected_mentions", 0),
            }
            deep_schema["validation"] = validation_payload
            deep_schema.setdefault("quality_checks", {})["validation"] = validation_payload
            FinalRecord.objects.update_or_create(
                patient  = patient,
                defaults = {
                    "run":            run,
                    "page_count":     final_data["page_count"],
                    "note_count":     final_data["note_count"],
                    "mention_count":  final_data["stats"]["mentions_after_merge"],
                    "category_count": final_data["stats"]["categories_after_merge"],
                    "grouped_record": deep_schema,
                    "review_flags":   merged_review_flags,
                    "traceability":   final_data["traceability"],
                    "stats":          merged_stats,
                    "relation_graph": {"edges": relation_payload["edges"]},
                    "timeline_events": relation_payload["timeline_events"],
                },
            )

            MentionRelation.objects.filter(patient=patient, run=run).delete()
            mention_index = {
                "::".join([
                    str(m.category).lower(),
                    str(m.label).lower(),
                    str(m.normalized_value or m.value).lower(),
                    str(m.date_text).lower(),
                ]): m
                for m in Mention.objects.filter(patient=patient, run=run)
            }
            relation_objects = []
            for edge in relation_payload["edges"]:
                relation_objects.append(MentionRelation(
                    patient=patient,
                    run=run,
                    source_mention=mention_index.get(edge["source_key"]),
                    target_mention=mention_index.get(edge["target_key"]),
                    relation_type=edge["relation_type"],
                    confidence=edge.get("confidence", 0.0),
                    evidence_artifact_ids=edge.get("source_artifact_ids", []) + edge.get("target_artifact_ids", []),
                    evidence_pages=edge.get("evidence_pages", []),
                    metadata={
                        "source_label": edge.get("source_label", ""),
                        "target_label": edge.get("target_label", ""),
                        "source_value": edge.get("source_value", ""),
                        "target_value": edge.get("target_value", ""),
                    },
                ))
            if relation_objects:
                MentionRelation.objects.bulk_create(relation_objects)
        except Exception as exc:
            _log(run, "error", "merge", f"Merge failed for {patient.code}: {exc}")

    # Medical transformer validation can load a multi-GB model. Keep the merge
    # stage serial when it is enabled so a small run cannot start one validator
    # per patient and overwhelm the VM.
    merge_workers = 1 if config.ENABLE_TRANSFORMER_VALIDATION else min(config.MAX_WORKERS, max(1, patients.count()))
    with concurrent.futures.ThreadPoolExecutor(max_workers=merge_workers) as executor:
        list(executor.map(_merge_one, patients))


# ─────────────────────────────────────────────────────────────────────────────
# Full pipeline entry point
# ─────────────────────────────────────────────────────────────────────────────

def run_full_pipeline(run: PipelineRun) -> None:
    """
    Execute the complete pipeline for all documents in *run*.

    Called from a background thread (spawned by views.start_run / start_run_one).
    Updates run.status and progress fields after each phase so the HTMX
    polling endpoint always shows current progress.

    Phases
    ------
    1. Extract + Triage  (per document, serial)
    2. LLM processing   (all unresolved notes batched)
    3. Merge            (per patient, serial)
    4. Profile photos   (per document, serial, best-effort)
    5. Mark complete
    """
    setup_gpu()
    _update_run(run, started_at=timezone.now(), status=PipelineRun.Status.EXTRACTING)

    documents = list(run.documents.all())
    _update_run(run, total_pdfs=len(documents))

    # ── Phase 1: Extraction + Triage (parallel across documents) ─────────
    # CPU phases (native text extraction, page rendering, segmentation,
    # regex) run in parallel threads. GPU phases (DocTR, EasyOCR) are
    # serialised by a lock inside pdf_extractor.py so only one GPU kernel
    # runs at a time, preventing OOM on single-GPU servers.
    doc_results = []

    def _process_doc_safe(doc: PDFDocument) -> dict:
        try:
            return process_single_document(doc, run)
        except Exception as exc:
            _log(run, "error", "extraction",
                 f"Document {doc.original_filename}: {exc}")
            return {
                "patient_code": doc.patient.code,
                "status": "error",
                "error": str(exc),
                "note_count": 0, "resolved": 0, "unresolved": 0, "regex_mentions": 0,
            }

    with concurrent.futures.ThreadPoolExecutor(max_workers=config.DOC_WORKERS) as executor:
        future_to_doc = {executor.submit(_process_doc_safe, doc): doc for doc in documents}
        for future in concurrent.futures.as_completed(future_to_doc):
            result = future.result()
            doc_results.append(result)
            # Atomic DB-level increment — safe across concurrent completions
            _inc_run(
                run.id,
                processed_pdfs   = 1,
                total_notes      = result.get("note_count",    0),
                resolved_notes   = result.get("resolved",      0),
                unresolved_notes = result.get("unresolved",    0),
                total_mentions   = result.get("regex_mentions", 0),
            )

    # Release any GPU memory held by DocTR before loading the LLM
    release_cuda()

    # ── Phase 2: LLM processing ───────────────────────────────────────────
    run_llm_phase(run, doc_results)
    release_cuda()

    # ── Phase 3: Merge mentions ───────────────────────────────────────────
    run_merge_phase(run)

    # ── Phase 4: Extract profile photos (best-effort, never fails run) ───
    for doc in documents:
        try:
            pdf_path    = doc.folder_path or doc.file.path
            output_path = f"media/profile_images/{doc.patient.code}.png"
            meta        = extract_profile_photo(pdf_path, output_path)

            if meta["photo_found"]:
                doc.patient.profile_image = f"profile_images/{doc.patient.code}.png"
                doc.patient.save(update_fields=["profile_image"])
        except Exception:
            pass   # photo extraction is non-critical; never abort the run

    # ── Phase 5: Mark complete ────────────────────────────────────────────
    _update_run(
        run,
        status       = PipelineRun.Status.COMPLETED,
        completed_at = timezone.now(),
    )
    run.refresh_from_db(fields=["total_mentions"])
    _log(run, "info", "complete",
         f"Pipeline complete: {len(documents)} PDFs processed, "
         f"{run.total_mentions} total mentions")
