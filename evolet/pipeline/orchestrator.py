"""
Evolet Pipeline Orchestrator
=============================
Coordinates the complete medical-report processing pipeline across five phases:

  Phase 1 — Extract + Triage  (CPU / IO-bound, per document)
    1a. PDF Extraction  : native text + DocTR OCR fallback  → PageLedger rows
    1b. Segmentation    : split pages into note-sized units  → NoteLedger rows
    1c. Regex Extraction: deterministic mention extraction   → Mention rows (origin=regex)
    1d. Triage          : classify notes as resolved vs. LLM-needed

  Phase 2 — LLM Processing    (GPU-bound, batched across all unresolved notes)
    Load Qwen 2.5 1.5B once, process all unresolved notes, unload.  → Mention rows (origin=llm)

  Phase 3 — Merge             (CPU, per patient)
    Deduplicate regex + LLM mentions per patient → FinalRecord rows

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

import logging
import traceback
from typing import Dict, List

from django.utils import timezone

from .models import (
    Patient, PDFDocument, PipelineRun, PageLedger, NoteLedger,
    Mention, FinalRecord, ProcessingLog,
)
from .services import config
from .services.pdf_extractor    import extract_pdf_pages, normalize_text, word_count
from .services.note_segmenter   import segment_pages_to_notes, triage_notes
from .services.regex_extractor  import extract_all_notes
from .services.llm_engine       import load_model, process_unresolved_notes, unload_model
from .services.merger           import build_final_record
from .services.qc               import compute_qc_metrics
from .services.photo_extractor  import extract_profile_photo
from .services.gpu_utils        import setup_gpu, release_cuda

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
            )
            for row in page_rows
        ])

        # ── 1b: Note Segmentation ─────────────────────────────────────────
        # Split each page into clinical-note-sized chunks; dedup by MD5.
        notes = segment_pages_to_notes(page_rows)
        result["note_count"] = len(notes)

        # ── 1c: Regex Extraction ──────────────────────────────────────────
        # Run deterministic extraction on every note.
        # Returns {note_id: [mention_dicts]}.
        regex_results         = extract_all_notes(notes)
        result["regex_mentions"] = sum(len(v) for v in regex_results.values())

        # ── 1d: Triage ────────────────────────────────────────────────────
        # Classify notes: resolved (regex enough) vs. unresolved (needs LLM).
        triaged = triage_notes(notes, regex_results)
        result["resolved"]   = len(triaged["resolved"])
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
                is_resolved  = note["note_id"] not in unresolved_ids,
            )
            for note in notes
        ])

        # Persist regex-extracted mentions (only from resolved notes)
        Mention.objects.filter(patient=patient, run=run, origin="regex").delete()
        regex_mention_objects = []
        for resolved_note in triaged["resolved"]:
            for m in resolved_note.get("mentions", []):
                regex_mention_objects.append(Mention(
                    patient          = patient,
                    document         = doc,
                    run              = run,
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
                    origin           = "regex",
                ))
        if regex_mention_objects:
            Mention.objects.bulk_create(regex_mention_objects)

        # Pass unresolved notes up to the orchestrator for batched LLM processing
        result["unresolved_notes_data"] = triaged["unresolved"]

        _log(run, "info", "extraction",
             f"{patient.code}: {len(page_rows)} pages, {len(notes)} notes, "
             f"{result['resolved']} resolved, {result['unresolved']} queued for LLM")

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
    try:
        load_model()
    except Exception as exc:
        _log(run, "error", "llm", f"Model load failed: {exc}")
        return

    def _progress(done: int, total: int) -> None:
        _log(run, "info", "llm", f"LLM batch {done}/{total} complete")

    try:
        llm_mentions = process_unresolved_notes(
            all_unresolved, progress_callback=_progress
        )

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

            mention_objects.append(Mention(
                patient          = patient,
                document         = doc,
                run              = run,
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
                origin           = "llm",
            ))

        if mention_objects:
            Mention.objects.bulk_create(mention_objects)

        _update_run(run, total_mentions=run.total_mentions + len(llm_mentions))
        _log(run, "info", "llm", f"LLM extracted {len(llm_mentions)} mentions")

    except Exception as exc:
        _log(run, "error", "llm", f"LLM phase failed: {exc}",
             {"traceback": traceback.format_exc()})

    finally:
        # Always unload the model after this phase to free GPU for other work
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

    # Find all patients that have documents in this run
    patients = Patient.objects.filter(documents__runs=run).distinct()

    for patient in patients:
        try:
            # Fetch all mentions for this patient + run as plain dicts
            # (avoids model instantiation overhead for large mention sets)
            mentions = list(
                Mention.objects.filter(patient=patient, run=run).values(
                    "category", "label", "value", "normalized_value",
                    "date_text", "certainty", "attributes",
                    "evidence_quote", "source_pages", "evidence_ids", "origin",
                )
            )

            # Gather page / note counts for stats
            doc        = patient.documents.first()
            page_count = PageLedger.objects.filter(document=doc).count() if doc else 0
            note_count = NoteLedger.objects.filter(document=doc).count() if doc else 0

            final_data = build_final_record(
                patient_code = patient.code,
                source_pdf   = doc.original_filename if doc else "",
                page_count   = page_count,
                note_count   = note_count,
                all_mentions = mentions,
            )

            FinalRecord.objects.update_or_create(
                patient  = patient,
                defaults = {
                    "run":            run,
                    "page_count":     final_data["page_count"],
                    "note_count":     final_data["note_count"],
                    "mention_count":  final_data["stats"]["mentions_after_merge"],
                    "category_count": final_data["stats"]["categories_after_merge"],
                    "grouped_record": final_data["grouped_record"],
                    "review_flags":   final_data["review_flags"],
                    "traceability":   final_data["traceability"],
                    "stats":          final_data["stats"],
                },
            )

        except Exception as exc:
            _log(run, "error", "merge", f"Merge failed for {patient.code}: {exc}")


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

    # ── Phase 1: Extraction + Triage ─────────────────────────────────────
    doc_results = []
    for i, doc in enumerate(documents):
        try:
            result = process_single_document(doc, run)
            doc_results.append(result)

            # Increment run-level counters after each document
            _update_run(
                run,
                processed_pdfs   = i + 1,
                total_notes      = run.total_notes      + result.get("note_count",   0),
                resolved_notes   = run.resolved_notes   + result.get("resolved",     0),
                unresolved_notes = run.unresolved_notes + result.get("unresolved",   0),
                total_mentions   = run.total_mentions   + result.get("regex_mentions", 0),
            )
        except Exception as exc:
            _log(run, "error", "extraction",
                 f"Document {doc.original_filename}: {exc}")
            doc_results.append({
                "patient_code": doc.patient.code,
                "status": "error",
                "error": str(exc),
            })

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
    _log(run, "info", "complete",
         f"Pipeline complete: {len(documents)} PDFs processed, "
         f"{run.total_mentions} total mentions")
