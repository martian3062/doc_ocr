"""
Management command to import final JSON records from the notebook pipeline.

Usage:
    python manage.py import_json_records /path/to/json/folder
    python manage.py import_json_records   # uses default DOC_READER_DATA_DIR
"""
import json
from pathlib import Path
from django.core.management.base import BaseCommand
from django.conf import settings
from pipeline.models import Patient, PDFDocument, Mention, FinalRecord


class Command(BaseCommand):
    help = "Import _final.json records from the notebook pipeline into the database"

    def add_arguments(self, parser):
        parser.add_argument(
            "folder",
            nargs="?",
            default=str(settings.DOC_READER_DATA_DIR),
            help="Path to folder containing *_final.json files",
        )

    def handle(self, *args, **options):
        folder = Path(options["folder"])
        if not folder.exists():
            self.stderr.write(self.style.ERROR(f"Folder not found: {folder}"))
            return

        json_files = sorted(folder.glob("*_final.json"))
        self.stdout.write(f"Found {len(json_files)} _final.json files in {folder}")

        imported = 0
        skipped = 0
        total_mentions = 0

        for f in json_files:
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except Exception as e:
                self.stderr.write(self.style.WARNING(f"  Skip {f.name}: {e}"))
                skipped += 1
                continue

            patient_code = data.get("patient_code", f.stem.replace("_final", ""))

            # Check if already imported
            if FinalRecord.objects.filter(patient__code=patient_code).exists():
                skipped += 1
                continue

            # Create patient
            patient, _ = Patient.objects.get_or_create(
                code=patient_code,
                defaults={"display_name": patient_code},
            )

            # Create placeholder document
            source_pdf = data.get("source_pdf", f"{patient_code}.pdf")
            doc, _ = PDFDocument.objects.get_or_create(
                patient=patient,
                original_filename=source_pdf,
                defaults={
                    "source_type": PDFDocument.SourceType.FOLDER,
                    "page_count": data.get("page_count", 0),
                    "folder_path": str(f),
                },
            )

            # Import mentions
            mentions_data = data.get("mentions", [])
            mention_objects = []
            for m in mentions_data:
                if not isinstance(m, dict):
                    continue
                mention_objects.append(
                    Mention(
                        patient=patient,
                        document=doc,
                        category=m.get("category", "unknown"),
                        label=m.get("label", ""),
                        value=m.get("value", ""),
                        normalized_value=m.get("normalized_value", ""),
                        date_text=m.get("date_text", ""),
                        certainty=m.get("certainty", "unknown"),
                        attributes=m.get("attributes", {}),
                        evidence_quote=m.get("evidence_quote", ""),
                        source_pages=m.get("source_pages", []),
                        evidence_ids=m.get("evidence_ids", []),
                        origin=(m.get("origins", ["regex"])[0]
                                if isinstance(m.get("origins"), list) and m.get("origins")
                                else m.get("origin", "regex")),
                    )
                )

            if mention_objects:
                Mention.objects.bulk_create(mention_objects)
                total_mentions += len(mention_objects)

            # Create final record
            grouped = data.get("grouped_record", {})
            stats = data.get("stats", {})
            FinalRecord.objects.update_or_create(
                patient=patient,
                defaults={
                    "page_count": data.get("page_count", 0),
                    "note_count": data.get("note_count", 0),
                    "mention_count": len(mentions_data),
                    "category_count": len(grouped),
                    "grouped_record": grouped,
                    "review_flags": data.get("review_flags", []),
                    "traceability": data.get("document_traceability", {}),
                    "stats": stats,
                },
            )

            imported += 1
            if imported % 20 == 0:
                self.stdout.write(f"  ...imported {imported} patients")

        self.stdout.write(
            self.style.SUCCESS(
                f"\nDone! Imported {imported} patients ({total_mentions} mentions), "
                f"skipped {skipped}"
            )
        )
