"""
Management command to run the pipeline from CLI.

Usage:
    python manage.py run_pipeline                    # all documents
    python manage.py run_pipeline --limit 10         # first 10
    python manage.py run_pipeline --patient "John"   # specific patient
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
from pipeline.models import Patient, PDFDocument, PipelineRun
from pipeline.orchestrator import run_full_pipeline


class Command(BaseCommand):
    help = "Run the doc-ocr extraction pipeline"

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit", type=int, default=None,
            help="Limit number of PDFs to process",
        )
        parser.add_argument(
            "--patient", type=str, default=None,
            help="Process only this patient code",
        )
        parser.add_argument(
            "--model", type=str, default="Qwen/Qwen2.5-1.5B-Instruct",
            help="Model ID for LLM inference",
        )
        parser.add_argument(
            "--no-4bit", action="store_true",
            help="Disable 4-bit quantization",
        )
        parser.add_argument(
            "--name", type=str, default=None,
            help="Name for this pipeline run",
        )

    def handle(self, *args, **options):
        documents = PDFDocument.objects.all()

        if options["patient"]:
            documents = documents.filter(patient__code__icontains=options["patient"])
        if options["limit"]:
            documents = documents[: options["limit"]]

        if not documents.exists():
            self.stderr.write(self.style.ERROR("No documents found to process"))
            return

        run_name = options["name"] or f"CLI Run {timezone.now().strftime('%Y-%m-%d %H:%M')}"

        run = PipelineRun.objects.create(
            name=run_name,
            model_id=options["model"],
            use_4bit=not options["no_4bit"],
            total_pdfs=documents.count(),
        )
        run.documents.set(documents)

        self.stdout.write(
            self.style.SUCCESS(
                f"Starting pipeline run: {run.name} ({documents.count()} PDFs)"
            )
        )

        try:
            run_full_pipeline(run)
            self.stdout.write(
                self.style.SUCCESS(
                    f"Pipeline completed: {run.total_mentions} mentions extracted"
                )
            )
        except Exception as e:
            self.stderr.write(self.style.ERROR(f"Pipeline failed: {e}"))
            raise
