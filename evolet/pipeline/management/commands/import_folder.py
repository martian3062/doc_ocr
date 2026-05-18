"""
Management command to import PDFs from a local folder.

Usage:
    python manage.py import_folder /path/to/pdfs
    python manage.py import_folder   # uses default DOC_READER_DATA_DIR
"""
from pathlib import Path
from django.core.management.base import BaseCommand
from django.conf import settings
from pipeline.models import Patient, PDFDocument


class Command(BaseCommand):
    help = "Import PDF files from a local folder into the database"

    def add_arguments(self, parser):
        parser.add_argument(
            "folder",
            nargs="?",
            default=str(settings.DOC_READER_DATA_DIR),
            help="Path to folder containing PDF files",
        )

    def handle(self, *args, **options):
        folder = Path(options["folder"])
        if not folder.exists():
            self.stderr.write(self.style.ERROR(f"Folder not found: {folder}"))
            return

        pdf_files = sorted(folder.glob("*.pdf"))
        self.stdout.write(f"Found {len(pdf_files)} PDF files in {folder}")

        imported = 0
        skipped = 0
        for f in pdf_files:
            if PDFDocument.objects.filter(folder_path=str(f)).exists():
                skipped += 1
                continue

            patient_code = f.stem
            patient, created = Patient.objects.get_or_create(
                code=patient_code,
                defaults={"display_name": patient_code},
            )
            PDFDocument.objects.create(
                patient=patient,
                original_filename=f.name,
                folder_path=str(f),
                source_type=PDFDocument.SourceType.FOLDER,
                file_size_bytes=f.stat().st_size,
            )
            imported += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Imported {imported} PDFs, skipped {skipped} already imported"
            )
        )
