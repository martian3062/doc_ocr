from django.core.management.base import BaseCommand

from pipeline.models import FinalRecord, Mention, Patient, PDFDocument, PipelineRun


class Command(BaseCommand):
    help = "Print a compact health summary for the latest pipeline run."

    def handle(self, *args, **options):
        run = PipelineRun.objects.latest("started_at")
        self.stdout.write(
            f"run id={run.id} status={run.status} processed={run.processed_pdfs}/{run.total_pdfs}"
        )
        self.stdout.write(
            f"db patients={Patient.objects.count()} documents={PDFDocument.objects.count()} "
            f"final_records={FinalRecord.objects.count()} mentions={Mention.objects.count()}"
        )
        for record in FinalRecord.objects.select_related("patient").order_by("created_at"):
            grouped = record.grouped_record or {}
            quality = grouped.get("quality_checks") or {}
            auto_schema = quality.get("auto_schema") or {}
            profile = grouped.get("document_profile") or {}
            major_info = grouped.get("major_info") or {}
            sections = grouped.get("sections") or {}
            spell = quality.get("spell_check") or {}
            self.stdout.write(
                "record patient={patient} schema={schema} doc_type={doc_type} "
                "auto_schema={status} sections={sections} primary={primary} spell_changes={spell_changes}".format(
                    patient=record.patient.display_name,
                    schema=grouped.get("schema_version"),
                    doc_type=profile.get("document_type"),
                    status=auto_schema.get("status"),
                    sections=len(sections),
                    primary=bool(major_info.get("primary_finding")),
                    spell_changes=spell.get("corrected_mentions"),
                )
            )
