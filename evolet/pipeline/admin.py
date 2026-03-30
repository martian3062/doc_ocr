"""Pipeline admin configuration."""
from django.contrib import admin
from .models import (
    Patient, PDFDocument, PipelineRun, PageLedger,
    NoteLedger, Mention, FinalRecord, ProcessingLog,
)


@admin.register(Patient)
class PatientAdmin(admin.ModelAdmin):
    list_display = ["code", "display_name", "created_at"]
    search_fields = ["code", "display_name"]


@admin.register(PDFDocument)
class PDFDocumentAdmin(admin.ModelAdmin):
    list_display = ["original_filename", "patient", "source_type", "page_count", "created_at"]
    list_filter = ["source_type"]
    search_fields = ["original_filename", "patient__code"]


@admin.register(PipelineRun)
class PipelineRunAdmin(admin.ModelAdmin):
    list_display = ["id", "name", "status", "total_pdfs", "processed_pdfs", "total_mentions", "created_at"]
    list_filter = ["status"]


@admin.register(Mention)
class MentionAdmin(admin.ModelAdmin):
    list_display = ["patient", "category", "label", "value", "origin", "created_at"]
    list_filter = ["category", "origin", "certainty"]
    search_fields = ["patient__code", "value", "label"]


@admin.register(FinalRecord)
class FinalRecordAdmin(admin.ModelAdmin):
    list_display = ["patient", "mention_count", "category_count", "created_at"]


@admin.register(ProcessingLog)
class ProcessingLogAdmin(admin.ModelAdmin):
    list_display = ["run", "level", "stage", "message", "created_at"]
    list_filter = ["level", "stage"]
