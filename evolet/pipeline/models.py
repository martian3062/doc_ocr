"""
doc-reader Pipeline — Database Models
===================================
Eight Django models that mirror the pipeline's artifact hierarchy:

  Patient
    └── PDFDocument (one or more PDFs per patient)
          ├── PageLedger   (one row per PDF page — extraction metadata)
          └── NoteLedger   (one row per segmented note — triage metadata)
                └── Mention (one row per extracted clinical mention)

  PipelineRun  ←→  PDFDocument (M2M)
  PipelineRun  →   ProcessingLog (audit trail)
  Patient      →   FinalRecord   (merged output, one per patient)

Design notes
------------
- UUIDs are used as PKs for PipelineRun, PDFDocument, and Mention to
  make external references (download URLs, JSON exports) opaque and
  safe to share.
- JSONField columns (attributes, source_pages, evidence_ids, grouped_record,
  review_flags, traceability, stats) hold structured data that doesn't need
  to be queried as relational columns, avoiding premature schema normalisation.
- auto_now_add / auto_now timestamps are used throughout for audit trails
  without requiring explicit timestamp management in application code.
"""

import uuid
from django.db import models
from django.utils import timezone


class Patient(models.Model):
    """
    One patient record, identified by a unique code derived from the PDF filename stem.

    The code is the stable external identifier (e.g. "TMH_2023_001").
    display_name can be enriched later from structured data in the PDF.
    profile_image is populated by the photo_extractor phase.
    """
    code          = models.CharField(max_length=300, unique=True, db_index=True)
    display_name  = models.CharField(max_length=300, blank=True)
    profile_image = models.ImageField(
        upload_to="profile_images/", blank=True, null=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return self.display_name or self.code


class PDFDocument(models.Model):
    """
    One source PDF file — either imported from a folder or uploaded via the UI.

    folder_path stores the absolute filesystem path for folder-imported files
    (the file field is empty).  For uploaded files, the file field holds the
    Django storage path and folder_path is empty.
    """
    class SourceType(models.TextChoices):
        FOLDER = "folder", "Imported from folder"
        UPLOAD = "upload", "Uploaded by user"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    patient = models.ForeignKey(
        Patient, on_delete=models.CASCADE, related_name="documents"
    )
    original_filename = models.CharField(max_length=500)
    file              = models.FileField(upload_to="pdfs/", blank=True)
    folder_path       = models.CharField(
        max_length=1000, blank=True,
        help_text="Absolute filesystem path for folder-imported PDFs",
    )
    source_type = models.CharField(
        max_length=10, choices=SourceType.choices, default=SourceType.FOLDER
    )
    page_count      = models.IntegerField(default=0)
    file_size_bytes = models.BigIntegerField(default=0)
    created_at      = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.original_filename


class PipelineRun(models.Model):
    """
    One pipeline execution session — covers one or more PDFDocuments.

    Status lifecycle:
      pending → extracting → (triaging) → llm_processing → merging → qc → completed
                                                                           ↘ failed

    Progress fields (total_pdfs, processed_pdfs, …) are updated after each
    document and polled by the HTMX progress endpoint.
    """
    class Status(models.TextChoices):
        PENDING        = "pending",        "Pending"
        EXTRACTING     = "extracting",     "Extracting text"
        TRIAGING       = "triaging",       "Triaging notes"
        LLM_PROCESSING = "llm_processing", "LLM processing"
        MERGING        = "merging",        "Merging mentions"
        QC             = "qc",             "Quality check"
        COMPLETED      = "completed",      "Completed"
        FAILED         = "failed",         "Failed"

    class RunKind(models.TextChoices):
        MAIN       = "main",       "Main pipeline"
        EXPERIMENT = "experiment", "Experimental pipeline"

    id   = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200, blank=True)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )
    model_id = models.CharField(max_length=200, default="Qwen/Qwen2.5-1.5B-Instruct")
    use_4bit = models.BooleanField(default=True)
    run_kind = models.CharField(
        max_length=20, choices=RunKind.choices, default=RunKind.MAIN
    )
    approach_key = models.CharField(max_length=80, blank=True)
    approach_config = models.JSONField(default=dict, blank=True)
    comparison_snapshot = models.JSONField(default=dict, blank=True)
    parent_run = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="experiment_runs",
    )

    # ── Progress counters (updated after each document) ──────────────────
    total_pdfs       = models.IntegerField(default=0)
    processed_pdfs   = models.IntegerField(default=0)
    total_notes      = models.IntegerField(default=0)
    resolved_notes   = models.IntegerField(default=0)
    unresolved_notes = models.IntegerField(default=0)
    total_mentions   = models.IntegerField(default=0)

    # ── Timing ───────────────────────────────────────────────────────────
    started_at    = models.DateTimeField(null=True, blank=True)
    completed_at  = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    # Many-to-many link to the documents processed in this run
    documents = models.ManyToManyField(PDFDocument, related_name="runs", blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Run {self.id.hex[:8]} — {self.status}"

    @property
    def progress_percent(self) -> int:
        """Completion percentage based on processed / total PDFs."""
        if self.total_pdfs == 0:
            return 0
        return int((self.processed_pdfs / self.total_pdfs) * 100)

    @property
    def duration_seconds(self) -> float:
        """Elapsed seconds from run start to completion (or now if still running)."""
        if not self.started_at:
            return 0.0
        end = self.completed_at or timezone.now()
        return (end - self.started_at).total_seconds()


class PageLedger(models.Model):
    """
    Per-page extraction result — one row per page per document.

    selected_source records whether native text or OCR was used.
    Quality metrics (char_count, word_count, alpha_ratio) are stored for
    debugging extraction quality and tuning OCR thresholds.
    """
    document = models.ForeignKey(
        PDFDocument, on_delete=models.CASCADE, related_name="pages"
    )
    page_num        = models.IntegerField()
    text            = models.TextField(blank=True)
    selected_source = models.CharField(max_length=10, default="native")  # "native" | "ocr"
    char_count      = models.IntegerField(default=0)
    word_count      = models.IntegerField(default=0)
    alpha_ratio     = models.FloatField(default=0.0)
    need_ocr        = models.BooleanField(default=False)
    page_width      = models.FloatField(default=0.0)
    page_height     = models.FloatField(default=0.0)
    layout_blocks   = models.JSONField(default=list, blank=True)

    class Meta:
        unique_together = ("document", "page_num")
        ordering        = ["document", "page_num"]

    def __str__(self):
        return f"Page {self.page_num} of {self.document}"


class NoteLedger(models.Model):
    """
    Per-note segmentation result — one row per clinical note unit.

    note_id is a human-readable stable ID like "p0001_n002".
    text_hash enables deduplication across pages within the same PDF.
    signal_score drives triage priority; notes below MIN_SIGNAL_FOR_LLM
    are not sent to the LLM.
    """
    document     = models.ForeignKey(
        PDFDocument, on_delete=models.CASCADE, related_name="notes"
    )
    note_id      = models.CharField(max_length=20)   # e.g. "p0001_n002"
    page_num     = models.IntegerField()
    note_ix      = models.IntegerField()             # 1-based index within the page
    text         = models.TextField()
    text_hash    = models.CharField(max_length=32)   # MD5 for exact dedup
    signal_score = models.IntegerField(default=0)
    is_low_value = models.BooleanField(default=False)
    needs_llm    = models.BooleanField(default=False)
    is_resolved  = models.BooleanField(default=False)
    layout_hint  = models.JSONField(default=dict, blank=True)
    artifact_ids = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["document", "page_num", "note_ix"]

    def __str__(self):
        return f"Note {self.note_id} of {self.document}"


class Mention(models.Model):
    """
    One extracted clinical mention — from regex or LLM.

    Every mention is linked to:
    - patient   : for patient-level aggregation
    - document  : for source traceability
    - run       : for run-level isolation (SET_NULL so mentions survive run deletion)
    - note      : for note-level traceability (optional, SET_NULL)

    source_pages and evidence_ids are JSON arrays that may be merged
    across duplicate mentions during the merger phase.
    """
    class Origin(models.TextChoices):
        REGEX  = "regex",  "Deterministic regex"
        LLM    = "llm",    "LLM extraction"
        MERGED = "merged", "Merged"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    patient  = models.ForeignKey(Patient,     on_delete=models.CASCADE,  related_name="mentions")
    document = models.ForeignKey(PDFDocument, on_delete=models.CASCADE,  related_name="mentions", null=True, blank=True)
    run      = models.ForeignKey(PipelineRun, on_delete=models.SET_NULL, related_name="mentions", null=True, blank=True)
    note     = models.ForeignKey(NoteLedger,  on_delete=models.SET_NULL, related_name="mentions", null=True, blank=True)

    # ── Extracted content fields ──────────────────────────────────────────
    category         = models.CharField(max_length=50, db_index=True)
    label            = models.CharField(max_length=200)
    value            = models.TextField()
    normalized_value = models.TextField(blank=True)
    date_text        = models.CharField(max_length=100, blank=True)
    certainty        = models.CharField(max_length=20, default="unknown")
    attributes       = models.JSONField(default=dict, blank=True)
    evidence_quote   = models.TextField(blank=True)
    source_pages     = models.JSONField(default=list, blank=True)
    evidence_ids     = models.JSONField(default=list, blank=True)
    evidence_artifact_ids = models.JSONField(default=list, blank=True)
    origin           = models.CharField(
        max_length=10, choices=Origin.choices, default=Origin.REGEX
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["category", "date_text", "label"]

    def __str__(self):
        return f"[{self.category}] {self.label}: {self.value[:60]}"


class FinalRecord(models.Model):
    """
    Merged, deduplicated patient-level record produced after the full pipeline.

    One record per patient (OneToOneField).  Stores the complete grouped
    output in grouped_record (a JSONField dict keyed by category) alongside
    traceability, review flags, and aggregate stats.

    This is the primary source for the patient_detail view and JSON downloads.
    """
    patient = models.OneToOneField(
        Patient, on_delete=models.CASCADE, related_name="final_record"
    )
    run = models.ForeignKey(
        PipelineRun, on_delete=models.SET_NULL, null=True, blank=True
    )

    # ── Denormalised counts (cached from stats dict for fast template rendering) ─
    page_count     = models.IntegerField(default=0)
    note_count     = models.IntegerField(default=0)
    mention_count  = models.IntegerField(default=0)
    category_count = models.IntegerField(default=0)

    # ── Full structured output ────────────────────────────────────────────
    grouped_record = models.JSONField(default=dict)   # {category: [mentions]}
    review_flags   = models.JSONField(default=list)   # ["no_mentions_after_merge", …]
    traceability   = models.JSONField(default=dict)   # {source_pages, evidence_ids}
    stats          = models.JSONField(default=dict)   # raw/merged counts, category count
    relation_graph = models.JSONField(default=dict, blank=True)
    timeline_events = models.JSONField(default=list, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["patient__code"]

    def __str__(self):
        return f"Final record for {self.patient}"


class ProcessingLog(models.Model):
    """
    Append-only audit trail for pipeline execution events.

    Written by the orchestrator at key milestones and error conditions.
    Displayed in the run_detail view for debugging and monitoring.
    """
    class Level(models.TextChoices):
        INFO    = "info",    "Info"
        WARNING = "warning", "Warning"
        ERROR   = "error",   "Error"

    run   = models.ForeignKey(
        PipelineRun, on_delete=models.CASCADE, related_name="logs",
        null=True, blank=True
    )
    level   = models.CharField(max_length=10, choices=Level.choices, default=Level.INFO)
    stage   = models.CharField(max_length=50, blank=True)
    message = models.TextField()
    details = models.JSONField(default=dict, blank=True)  # e.g. {"traceback": "…"}

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"[{self.level}] {self.stage}: {self.message[:80]}"


class DocumentArtifact(models.Model):
    """
    Layout-aware document artifact produced during page understanding.

    Artifacts capture the evidence-native layer of the advanced pipeline:
    text blocks, tables, stamps, signatures, figures, handwritten regions,
    and OCR line crops with coordinates and backend provenance.
    """

    class ArtifactType(models.TextChoices):
        TEXT_BLOCK = "text_block", "Text block"
        TEXT_LINE = "text_line", "Text line"
        TABLE = "table", "Table"
        HEADER = "header", "Header"
        FOOTER = "footer", "Footer"
        STAMP = "stamp", "Stamp"
        SIGNATURE = "signature", "Signature"
        HANDWRITING = "handwriting", "Handwriting"
        FIGURE = "figure", "Figure"
        IMAGE = "image", "Image"
        PAGE_REGION = "page_region", "Page region"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="artifacts")
    document = models.ForeignKey(PDFDocument, on_delete=models.CASCADE, related_name="artifacts")
    page = models.ForeignKey(
        PageLedger, on_delete=models.SET_NULL, related_name="artifacts",
        null=True, blank=True,
    )
    note = models.ForeignKey(
        NoteLedger, on_delete=models.SET_NULL, related_name="artifacts",
        null=True, blank=True,
    )
    run = models.ForeignKey(
        PipelineRun, on_delete=models.SET_NULL, related_name="artifacts",
        null=True, blank=True,
    )
    artifact_type = models.CharField(max_length=30, choices=ArtifactType.choices)
    role = models.CharField(max_length=50, blank=True)
    backend = models.CharField(max_length=50, blank=True)
    text = models.TextField(blank=True)
    normalized_text = models.TextField(blank=True)
    confidence = models.FloatField(default=0.0)
    bbox = models.JSONField(default=list, blank=True)
    polygon = models.JSONField(default=list, blank=True)
    page_num = models.IntegerField(default=0)
    reading_order = models.IntegerField(default=0)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["document", "page_num", "reading_order", "artifact_type"]

    def __str__(self):
        return f"{self.artifact_type} p{self.page_num} {self.document}"


class MentionRelation(models.Model):
    """
    Patient-level relation edge built from extracted mentions and evidence.

    This allows the knowledge view to move beyond category grouping into
    timeline events, treatment episodes, and cross-mention evidence links.
    """

    class RelationType(models.TextChoices):
        HAS_DIAGNOSIS = "has_diagnosis", "Has diagnosis"
        TREATED_WITH = "treated_with", "Treated with"
        EVIDENCED_BY = "evidenced_by", "Evidenced by"
        TEST_RESULT = "test_result", "Test result"
        FOLLOWED_BY = "followed_by", "Followed by"
        RELATED_TO = "related_to", "Related to"
        OCCURRED_ON = "occurred_on", "Occurred on"
        PROGRESSION = "progression", "Progression"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="relations")
    run = models.ForeignKey(
        PipelineRun, on_delete=models.SET_NULL, related_name="relations",
        null=True, blank=True,
    )
    source_mention = models.ForeignKey(
        Mention, on_delete=models.SET_NULL, related_name="outgoing_relations",
        null=True, blank=True,
    )
    target_mention = models.ForeignKey(
        Mention, on_delete=models.SET_NULL, related_name="incoming_relations",
        null=True, blank=True,
    )
    relation_type = models.CharField(max_length=40, choices=RelationType.choices)
    confidence = models.FloatField(default=0.0)
    evidence_artifact_ids = models.JSONField(default=list, blank=True)
    evidence_pages = models.JSONField(default=list, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["patient__code", "relation_type", "created_at"]

    def __str__(self):
        return f"{self.patient.code} {self.relation_type}"
