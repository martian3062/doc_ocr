# doc-reader

`doc-reader` is an evidence-native clinical document reader for hospital PDFs, scanned reports, mixed-layout records, and difficult handwritten notes. It is built to turn source documents into traceable structured medical data, not just loose OCR text.

The current default system is LLM-oriented: native PDF text is grouped into clinical notes, every useful note goes through the extraction LLM, and a MedGemma/Gemma-style validation LLM audits the merged patient record. Heavy OCR/layout engines such as TrOCR, GOT-OCR, Docling, Surya, PaddleOCR, and YOLO are optional comparison backends, not the default path.

## What It Does

`doc-reader` reads clinical documents through a hybrid document-understanding pipeline:

1. native PDF text extraction when the PDF already contains reliable text
2. page and region artifact generation with coordinates and reading order
3. layout-aware note segmentation using page structure, not only regex markers
4. LLM extraction across the grouped clinical notes
5. relation and timeline construction
6. validation LLM review of the merged medical record
7. API and frontend review with evidence provenance and source PDF comparison

The goal is to preserve source evidence all the way through the pipeline. A final extracted field should be traceable back to page, region, backend, confidence, and supporting text.

## Current Architecture

```text
PDF input
  -> native text extraction
  -> page layout and artifact capture
  -> layout-aware note grouping
  -> LLM extraction over all useful notes
  -> merge and normalization
  -> validation LLM audit
  -> relation graph
  -> timeline events
  -> Django API + Next.js review UI
```

## Name And Runtime Identity

The project-facing name is now `doc-reader`.

Active runtime names:

- Django settings package: `doc_reader`
- Docker containers: `doc_reader_backend`, `doc_reader_worker`, `doc_reader_frontend`, `doc_reader_postgres`, `doc_reader_redis`
- default Postgres database/user: `doc_reader`
- preferred environment prefix: `DOC_READER_*`

Legacy `EVOLET_*` variables are still accepted as compatibility fallbacks so older VM environments can keep running while the deployment is migrated.

## OCR Stack

## Parser Stack

The parser layer is deliberately separate from the LLM. It is responsible for page structure, coordinates, reading order, tables, and evidence artifacts.

Implemented parser adapters:

- `docling`: full document conversion and structured artifact extraction when `docling` is installed
- `surya`: line/layout/table recognition hook for deployments with `surya-ocr`
- `paddle_structure`: PaddleOCR/PP-Structure hook for deployments with `paddleocr`

These adapters are optional. If a parser package is missing, the pipeline records that in metadata and continues with the stable PyMuPDF/native extraction path.

### Primary handwriting recognizer

`microsoft/trocr-large-handwritten`

This is the main handwriting OCR path. It is intended for cropped handwritten lines or compact regions, not entire noisy pages.

### Verification and page OCR

`stepfun-ai/GOT-OCR-2.0-hf`

This is used for full-page OCR support, patch OCR, and verification of difficult or ambiguous regions.

### Why the hybrid matters

TrOCR is better as a focused handwriting recognizer. GOT-OCR is better as a broader OCR/VLM verifier for messy page structure. `doc-reader` uses both so the pipeline can handle handwritten crops and page-level uncertainty without forcing one model to do every job.

## Data Model

The upgraded backend stores both extracted meaning and source evidence:

- `Patient`
- `PDFDocument`
- `PageLedger`
- `NoteLedger`
- `DocumentArtifact`
- `Mention`
- `MentionRelation`
- `FinalRecord`
- `PipelineRun`
- `ProcessingLog`

Artifacts carry information such as page number, bounding box, role, backend, confidence, text, and metadata. Relations connect mentions into diagnosis, treatment, test, evidence, and timeline-style structures.

## Important Files

Backend:

- `evolet/manage.py`
- `evolet/doc_reader/settings.py`
- `evolet/pipeline/orchestrator.py`
- `evolet/pipeline/models.py`
- `evolet/pipeline/api_views.py`
- `evolet/pipeline/views.py`
- `evolet/pipeline/services/layout_segmenter.py`
- `evolet/pipeline/services/ocr_backends/`
- `evolet/pipeline/services/relation_extractor.py`
- `evolet/pipeline/services/queue.py`

Frontend:

- `evolet/frontend/src/app/layout.tsx`
- `evolet/frontend/src/app/page.tsx`
- `evolet/frontend/src/app/patients/[id]/page.tsx`
- `evolet/frontend/src/app/runs/page.tsx`
- `evolet/frontend/src/app/runs/[id]/page.tsx`
- `evolet/frontend/src/components/KnowledgeGraph.tsx`
- `evolet/frontend/src/lib/api.ts`

Historical prototype files:

- `evolet_TMH_OCRd.ipynb`
- `evolet_tmh_ocrd.py`

## Environment

Preferred runtime variables:

```env
DJANGO_SETTINGS_MODULE=doc_reader.settings
DATABASE_URL=postgresql://doc_reader:doc_reader@postgres:5432/doc_reader
REDIS_HOST=redis
REDIS_PORT=6379
REDIS_DB=0
HF_TOKEN=...
HUGGING_FACE_HUB_TOKEN=...
DOC_READER_MODEL_ID=Qwen/Qwen2.5-1.5B-Instruct
DOC_READER_TROCR_MODEL_ID=microsoft/trocr-large-handwritten
DOC_READER_GOT_OCR_MODEL_ID=stepfun-ai/GOT-OCR-2.0-hf
DOC_READER_ENABLE_HANDWRITING_OCR=1
DOC_READER_ENABLE_GOT_VERIFICATION=1
DOC_READER_ENABLE_ADVANCED_PARSERS=1
DOC_READER_PARSER_BACKENDS=docling,surya,paddle_structure
DOC_READER_USE_4BIT=1
DOC_READER_QUEUE_MODE=rq
DOC_READER_DOC_WORKERS=4
```

Compatibility variables:

```env
EVOLET_MODEL_ID=...
EVOLET_TROCR_MODEL_ID=...
EVOLET_GOT_OCR_MODEL_ID=...
EVOLET_ENABLE_HANDWRITING_OCR=...
EVOLET_ENABLE_GOT_VERIFICATION=...
```

## Local Development

Backend:

```bash
cd evolet
python manage.py migrate
python manage.py check
python manage.py verify_hf_access --model microsoft/trocr-large-handwritten
python manage.py runserver 0.0.0.0:9000
```

Frontend:

```bash
cd evolet/frontend
npm install
npm run dev
```

## Docker Deployment

```bash
cd evolet
docker compose --profile gpu up -d --build
```

Default services:

- frontend: `http://localhost:3000`
- backend API: `http://localhost:9000/api/v1/dashboard/`
- Postgres: `doc_reader_postgres`
- Redis: `doc_reader_redis`

## Live VM

The last known deployed VM target is:

- SSH: `ssh -i D:\data\evolet_rsa pardeep@34.126.112.227`
- frontend: `http://34.126.112.227:3000`
- backend: `http://34.126.112.227:9000/api/v1/dashboard/`

The VM has an L4 GPU and is the right target for the TrOCR/GOT-OCR workload.

### Live runner

The frontend now has a live run monitor:

- runs list: `http://34.126.112.227:3000/runs`
- latest validation run: `http://34.126.112.227:3000/runs/f5429551-e07c-4745-93cf-319f8a9acf45`
- YOLO validation run: `http://34.126.112.227:3000/runs/62ba872e-de75-411c-bc2e-2c4abeb952dd`
- run detail API: `http://34.126.112.227:9000/api/v1/runs/f5429551-e07c-4745-93cf-319f8a9acf45/`

The run detail page polls the API and shows:

- live status, progress, processed PDF count, processing rate, and ETA while active
- stream-style processing logs from `ProcessingLog`
- only the documents and patients attached to the selected run
- links from each processed result into the patient detail page

The patient list is result-focused by default. It filters to patients with completed `FinalRecord` rows, so an imported folder with many PDFs does not appear as extracted output until those documents have actually been processed.

Patient detail now includes a `Compare` tab. It embeds the source PDF through:

```text
/api/v1/documents/<document_id>/pdf/
```

The PDF is streamed inline by Django and proxied through the Next.js app, so extracted fields, evidence quotes, validation flags, and the original source file can be reviewed side by side.

### Chemotherapy dataset validation

The chemotherapy folder was uploaded to the VM from:

```text
E:\doc_ocr\drive-download-chenmotherapy data
```

VM paths:

- uploaded archive: `/home/pardeep/chemotherapy_pdfs.tar.gz`
- extracted data: `/home/pardeep/data/doc-reader-chemotherapy`
- container mount: `/data/doc-reader-chemotherapy`

Import result:

- PDFs found locally: 175
- PDFs imported on VM: 175
- skipped on import: 0

Per request, the all-PDF run was stopped and superseded by a first-10-PDF validation run. The clean validation run is:

- run ID: `f5429551-e07c-4745-93cf-319f8a9acf45`
- name: `Chemotherapy 10 PDF validation run 2026-05-18 forced clean`
- status: completed
- processed PDFs: 10 / 10
- final records shown: 10
- run-linked mentions: 90
- LLM fallback: completed with 2 / 2 LLM batches and 7 LLM mentions
- 4-bit quantization: disabled for this validation run to avoid the bitsandbytes import path

Runtime fixes applied during validation:

- mounted `/home/pardeep/data` into the containers as `/data:ro`
- fixed the worker entrypoint so `rqworker` starts correctly
- switched the worker path to `rq.SimpleWorker` to avoid CUDA fork issues
- tightened handwriting OCR routing so TrOCR/GOT do not run on every small native text block
- added run telemetry fields in the API: elapsed seconds, processing rate, remaining PDFs, ETA seconds/text, and latest log timestamp
- added run detail polling and live logs in the Next.js frontend
- made `DOC_READER_SKIP_EXISTING=0` available for forced validation reruns while keeping skip-existing behavior on by default
- wired `--no-4bit` into the actual LLM model loader
- clamped generated LLM short fields before database insert so model prose cannot overflow fixed-length columns

Additional detection/validation run:

- run ID: `62ba872e-de75-411c-bc2e-2c4abeb952dd`
- name: `Chemotherapy 10 PDF YOLO validation run 2026-05-18`
- status: completed
- processed PDFs: 10 / 10
- total mentions: 99
- run-linked mentions: 93
- YOLO layout artifacts: 183
- final records shown: 10
- LLM fallback: completed with 2 / 2 batches and 9 LLM mentions

This run uses the optional `yolo_layout` parser backend with `Armaggheddon/yolo11-document-layout` and `yolo11n_doc_layout.pt` for DocLayNet-style page-region detection. It also writes a validation payload into `FinalRecord.stats.validation` and `FinalRecord.grouped_record.validation`. The default validation backend is heuristic for reliability; set `DOC_READER_VALIDATION_BACKEND=model` to attempt the configured MedGemma/Gemma validation model (`DOC_READER_VALIDATION_MODEL_ID`, default `google/medgemma-1.5-4b-it`) when model access and VRAM are available.

## Verified Status

The advanced reader work added:

- TrOCR backend
- GOT-OCR backend
- OCR backend interface
- layout-aware artifact generation
- relation extraction
- queue launch helper
- expanded database migration
- evidence-aware frontend updates
- Docker and VM runtime wiring
- live run ETA and log monitoring
- run-isolated result browsing for validation batches

Known remaining hardening work:

- switch backend serving from Django runserver to Gunicorn
- install and benchmark optional `requirements-advanced.txt` parser packages on the GPU VM
- add artifact crop previews and page overlays in the frontend
- add API tests for patient detail, evidence artifacts, and relation graph responses
