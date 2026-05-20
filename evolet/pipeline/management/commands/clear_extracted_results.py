from django.core.management.base import BaseCommand

from pipeline.models import (
    DocumentArtifact,
    FinalRecord,
    Mention,
    MentionRelation,
    NoteLedger,
    PageLedger,
    PipelineRun,
    ProcessingLog,
)


class Command(BaseCommand):
    help = "Clear regenerated extraction outputs while preserving patients, PDF documents, and source files."

    def add_arguments(self, parser):
        parser.add_argument(
            "--include-runs",
            action="store_true",
            help="Also delete PipelineRun rows and their logs. By default run history is preserved.",
        )

    def handle(self, *args, **options):
        models = [
            FinalRecord,
            MentionRelation,
            Mention,
            DocumentArtifact,
            PageLedger,
            NoteLedger,
        ]
        if options["include_runs"]:
            models.extend([ProcessingLog, PipelineRun])
        before = {model.__name__: model.objects.count() for model in models}
        for model in models:
            model.objects.all().delete()
        after = {model.__name__: model.objects.count() for model in models}
        self.stdout.write(self.style.SUCCESS(f"Cleared extracted results: before={before} after={after}"))
