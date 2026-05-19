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

    def handle(self, *args, **options):
        models = (
            FinalRecord,
            MentionRelation,
            Mention,
            ProcessingLog,
            DocumentArtifact,
            PageLedger,
            NoteLedger,
            PipelineRun,
        )
        before = {model.__name__: model.objects.count() for model in models}
        for model in models:
            model.objects.all().delete()
        after = {model.__name__: model.objects.count() for model in models}
        self.stdout.write(self.style.SUCCESS(f"Cleared extracted results: before={before} after={after}"))
