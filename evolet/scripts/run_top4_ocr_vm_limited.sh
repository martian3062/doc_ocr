#!/usr/bin/env sh
set -eu

PDF_DIR="${PDF_DIR:-/data/django_only_10pdf_smoke}"
LIMIT_REPORTS="${LIMIT_REPORTS:-5}"
MAX_PAGES="${MAX_PAGES:-2}"
MAX_CROPS="${MAX_CROPS:-4}"
OUT="${OUT:-/tmp/doc_reader_top4_ocr_results.json}"

export DOC_READER_DOC_WORKERS="${DOC_READER_DOC_WORKERS:-1}"
export DOC_READER_MAX_WORKERS="${DOC_READER_MAX_WORKERS:-2}"
export DOC_READER_ENABLE_LOCAL_HF_LLM=0
export DOC_READER_ENABLE_LOCAL_HF_VISION_MODELS=0
export DOC_READER_ENABLE_HANDWRITING_OCR=0
export DOC_READER_ENABLE_MEDICAL_HANDWRITING_OCR=0
export DOC_READER_ENABLE_GOT_VERIFICATION=0
export DOC_READER_ENABLE_ADVANCED_PARSERS=0
export DOC_READER_PARSER_BACKENDS=""
export DOC_READER_ENABLE_PAGE_VISION_SWEEP=1
export DOC_READER_ENABLE_GROQ_VISION_OCR=1
export DOC_READER_PAGE_VISION_MAX_PAGES_PER_DOCUMENT="$MAX_PAGES"
export DOC_READER_PAGE_VISION_MAX_CROPS_PER_PAGE="$MAX_CROPS"
export DOC_READER_ENABLE_HANDWRITING_ORDER_EXTRACTOR=1
export DOC_READER_HANDWRITING_ORDER_MAX_PAGES_PER_DOCUMENT="$MAX_PAGES"
export DOC_READER_HANDWRITING_ORDER_MAX_CROPS_PER_PAGE="$MAX_CROPS"
export DOC_READER_MULTIMODAL_MEDICINE_BACKENDS=dictionary
export DOC_READER_ENABLE_TRANSFORMER_VALIDATION=0
export DOC_READER_VALIDATION_BACKEND=heuristic

python manage.py eval_top4_ocr_approaches \
  --pdf-dir "$PDF_DIR" \
  --limit-reports "$LIMIT_REPORTS" \
  --max-pages "$MAX_PAGES" \
  --max-crops "$MAX_CROPS" \
  --run-cloud \
  --json > "$OUT"

python - "$OUT" <<'PY'
import json
import sys
payload = json.load(open(sys.argv[1], encoding="utf-8"))
summary = payload["summary"]
print(f"results={sys.argv[1]}")
print(f"reports={summary['report_count']} anchors={summary['anchor_hit_reports']} crops={summary['total_crop_candidates']}")
for row in sorted(payload["approach_results"], key=lambda item: item["rank"]):
    print(f"{row['rank']}. {row['approach']} status={row['status']} fit={row['fit_for_project']} notes={row['notes']}")
PY
