# doc-reader application

This directory contains the live Django + Next.js application for `doc-reader`.

## Runtime Shape

- Django settings package: `doc_reader`
- Django pipeline app: `pipeline`
- Next.js frontend: `frontend`
- Redis queue path: RQ when available
- database: SQLite locally or Postgres in Docker/VM
- default extraction: native PDF text grouped into clinical notes, then extracted by the LLM
- validation: MedGemma/Gemma-style validation LLM after the merged patient record is built
- optional advanced OCR/parser ensemble: TrOCR, GOT-OCR, Docling, Surya, PaddleOCR/PP-Structure, and YOLO adapters

## Main Backend Files

- `manage.py`
- `doc_reader/settings.py`
- `doc_reader/urls.py`
- `pipeline/orchestrator.py`
- `pipeline/models.py`
- `pipeline/api_views.py`
- `pipeline/views.py`
- `pipeline/services/config.py`
- `pipeline/services/layout_segmenter.py`
- `pipeline/services/parsing/`
- `pipeline/services/ocr_backends/`
- `pipeline/services/relation_extractor.py`
- `pipeline/services/queue.py`

## Main Frontend Files

- `frontend/src/app/layout.tsx`
- `frontend/src/app/page.tsx`
- `frontend/src/app/patients/[id]/page.tsx`
- `frontend/src/components/KnowledgeGraph.tsx`
- `frontend/src/lib/api.ts`

## Local Backend

```bash
python manage.py migrate
python manage.py check
python manage.py runserver 0.0.0.0:9000
```

## Local Frontend

```bash
cd frontend
npm install
npm run dev
```

## Docker

```bash
docker compose --profile gpu up -d --build
```

Services:

- `doc_reader_frontend` on port `3000`
- `doc_reader_backend` on port `9000`
- `doc_reader_worker`
- `doc_reader_postgres`
- `doc_reader_redis`

## Environment

```env
DJANGO_SETTINGS_MODULE=doc_reader.settings
DATABASE_URL=postgresql://doc_reader:doc_reader@postgres:5432/doc_reader
REDIS_HOST=redis
REDIS_PORT=6379
HF_TOKEN=...
HUGGING_FACE_HUB_TOKEN=...
DOC_READER_LLM_EXTRACT_ALL_NOTES=1
DOC_READER_ENABLE_MEDICAL_VALIDATION=1
DOC_READER_VALIDATION_BACKEND=model
DOC_READER_VALIDATION_MODEL_ID=google/medgemma-1.5-4b-it
DOC_READER_VALIDATION_FALLBACK_MODEL_ID=Qwen/Qwen2.5-1.5B-Instruct
DOC_READER_ENABLE_HANDWRITING_OCR=0
DOC_READER_ENABLE_GOT_VERIFICATION=0
DOC_READER_ENABLE_ADVANCED_PARSERS=0
DOC_READER_PARSER_BACKENDS=
DOC_READER_QUEUE_MODE=rq
DOC_READER_DOC_WORKERS=4
```

The older `EVOLET_*` environment names are still accepted as fallbacks, but new setup should use `DOC_READER_*`.

The default install is intentionally LLM-oriented and avoids heavy Python OCR/layout packages. Optional advanced parser packages live in `requirements-advanced.txt`. Install that file and enable the matching env switches only for a CV/OCR comparison run.
