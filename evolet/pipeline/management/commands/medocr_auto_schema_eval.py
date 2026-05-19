"""Evaluate auto-schema generation on Hugging Face MedOCR samples."""

import json
from urllib.parse import urlencode
from urllib.request import urlopen

from django.core.management.base import BaseCommand

from pipeline.services import config
from pipeline.services.auto_schema import enrich_with_auto_schema


BASE_URL = "https://datasets-server.huggingface.co"


class Command(BaseCommand):
    help = "Run a small auto-schema smoke eval using MedOCR vision dataset text samples."

    def add_arguments(self, parser):
        parser.add_argument("--split", default="validation")
        parser.add_argument("--limit", type=int, default=5)
        parser.add_argument("--offset", type=int, default=0)

    def handle(self, *args, **options):
        rows = self._fetch_rows(options["split"], options["offset"], options["limit"])
        ok = 0
        for row in rows:
            idx = row["row_idx"]
            text = row["row"].get("text", "")
            schema = enrich_with_auto_schema(
                base_schema=self._base_schema(idx),
                patient_code=f"medocr-{idx}",
                source_pdf=f"medocr-row-{idx}.jpg",
                source_text=text,
                mentions=[],
                spell_check={"status": "not_applicable", "libraries": []},
            )
            valid = all(key in schema for key in ("major_info", "document_profile", "sections", "quality_checks"))
            ok += int(valid)
            self.stdout.write(json.dumps({
                "row_idx": idx,
                "valid": valid,
                "document_type": schema.get("document_profile", {}).get("document_type"),
                "section_count": len(schema.get("sections", {})),
                "auto_schema_status": schema.get("quality_checks", {}).get("auto_schema", {}).get("status"),
            }))

        self.stdout.write(self.style.SUCCESS(f"MedOCR auto-schema eval: {ok}/{len(rows)} valid"))

    def _fetch_rows(self, split: str, offset: int, limit: int):
        query = urlencode({
            "dataset": config.MEDOCR_VISION_DATASET_ID,
            "config": "default",
            "split": split,
            "offset": offset,
            "length": limit,
        })
        with urlopen(f"{BASE_URL}/rows?{query}", timeout=60) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        return payload.get("rows", [])

    def _base_schema(self, idx: int):
        return {
            "identity": {
                "patient_code": f"medocr-{idx}",
                "source_pdf": f"medocr-row-{idx}.jpg",
                "page_count": 1,
                "note_count": 1,
            },
            "document_summary": {"category_count": 0, "mention_count": 0, "categories": [], "category_counts": {}},
            "sections": {},
            "mentions_flat": [],
            "grouped_record": {},
            "traceability": {},
            "stats": {},
            "review_flags": [],
            "schema_version": "4.0-auto-schema",
        }
