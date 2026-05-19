# doc-reader

`doc-reader` is a clinical PDF understanding system for hospital records, scanned reports, chemotherapy sheets, and doctor handwriting. It is designed to produce evidence-linked structured medical data, not just OCR text.

The current architecture is intentionally layered:

1. collect all available text and page evidence
2. detect layout, tables, page regions, and likely handwritten order zones
3. crop difficult regions and read them with vision OCR
4. normalize medical short forms and drug-name variants
5. build adaptive schema records with evidence, confidence, page, and bounding-box provenance
6. validate the final medical record

## Current Status

The latest VM deployment uses a safe hybrid stack:

- PyMuPDF/native PDF extraction for embedded text and page rendering
- optional PaddleOCR PP-Structure parser, capped by page count and GPU memory limits
- Groq vision OCR for page/crop reading
- dedicated handwriting order extraction for doctor-written medicine/vital regions
- medical short-form normalization before schema extraction
- MedOCR reference layer using `naazimsnh02/medocr-vision-dataset` as prompt/evaluation context
- Groq text/schema models for adaptive extraction when quota is available
- heuristic validation by default, with local HF validation disabled unless explicitly enabled

Recent verified VM runs:

- `3 PDF handwriting order extractor clean run 2026-05-19`: completed, 97 mentions, extracted handwritten chemo orders including Palonosetron, Paclitaxel 150mg, Trastuzumab 264mg, and Docetaxel 95mg from a page crop.
- `3 PDF handwriting order extractor final clean run 2026-05-19`: completed extraction, 89 mentions, but Groq text schema cleanup hit the daily token limit during final auto-schema calls.
- `1 PDF handwriting chart crop extraction-only 2026-05-19`: completed, 43 mentions, chart-page crop detection enabled.

Operational note: Groq API keys are runtime secrets. Set them in the VM/container environment; do not commit them.

## Pipeline

```text
PDF/image input
  -> native PDF text extraction
  -> page rendering and quality checks
  -> optional PaddleOCR/PP-Structure layout parsing
  -> page vision sweep over clinical regions
  -> handwriting_order_extractor for medicine/vital chart crops
  -> medical short-form and drug normalization
  -> artifact and mention extraction
  -> adaptive schema construction
  -> validation
  -> Django API + Next.js review UI
```

Every extracted value should keep source evidence:

- document and page number
- artifact backend
- bounding box where available
- evidence quote
- confidence
- normalized value
- extraction method

## Handwriting Order Layer

Doctor handwriting is handled as a separate crop-level layer, not as normal whole-page OCR.

Main files:

- `evolet/pipeline/services/handwriting_order_extractor.py`
- `evolet/pipeline/services/medical_short_forms.py`
- `evolet/pipeline/services/medocr_reference.py`
- `evolet/pipeline/services/artifact_extractor.py`

What it does:

- takes page/layout artifacts from native text, page vision, and optional Paddle layout
- detects probable medicine/injection chart pages
- creates focused crops for vitals, orders, and staff-nurse medicine charts
- sends each crop to Groq vision with a strict medicine/vitals JSON prompt
- normalizes short forms such as `Inj`, `IV`, `BD`, `TDS`, `STAT`, `NS`, `DNS`, and `RL`
- normalizes common doctor-writing variants such as `PAN` to pantoprazole, `TRASTU` to trastuzumab, `DOCET` to docetaxel, and `PALONONAIL` to palonosetron
- stores the result as handwriting artifacts before schema extraction

The MedOCR dataset layer uses:

```text
naazimsnh02/medocr-vision-dataset
```

It is a reference/evaluation layer for examples and prompting. It is not treated as a runnable inference model.

## Model And Provider Strategy

Default safe VM mode:

- text/schema provider: Groq
- vision/crop OCR: Groq vision
- local HF LLMs: disabled by default
- local HF vision models: disabled by default
- validation: heuristic by default
- PaddleOCR: optional advanced parser, capped

Optional local/HF models remain supported:

- `Qwen/Qwen2.5-1.5B-Instruct`
- `google/medgemma-1.5-4b-it`
- `microsoft/trocr-large-handwritten`
- `stepfun-ai/GOT-OCR-2.0-hf`
- `Armaggheddon/yolo11-document-layout`

Use local models only when GPU memory and access are confirmed.

## Key Environment Variables

```env
DJANGO_SETTINGS_MODULE=doc_reader.settings
DATABASE_URL=postgresql://doc_reader:doc_reader@postgres:5432/doc_reader
REDIS_HOST=redis
REDIS_PORT=6379
REDIS_DB=0

DOC_READER_GROQ_API_KEY=...
DOC_READER_LLM_PROVIDER=groq
DOC_READER_GROQ_EXTRACTION_MODEL=llama-3.3-70b-versatile
DOC_READER_SCHEMA_PROVIDER=groq
DOC_READER_SCHEMA_MODEL=llama-3.3-70b-versatile

DOC_READER_ENABLE_GROQ_VISION_OCR=1
DOC_READER_GROQ_VISION_MODEL=meta-llama/llama-4-scout-17b-16e-instruct
DOC_READER_ENABLE_PAGE_VISION_SWEEP=1
DOC_READER_PAGE_VISION_MAX_PAGES_PER_DOCUMENT=5
DOC_READER_PAGE_VISION_MAX_CROPS_PER_PAGE=4

DOC_READER_ENABLE_HANDWRITING_ORDER_EXTRACTOR=1
DOC_READER_HANDWRITING_ORDER_MAX_PAGES_PER_DOCUMENT=5
DOC_READER_HANDWRITING_ORDER_MAX_CROPS_PER_PAGE=8
DOC_READER_HANDWRITING_ORDER_RENDER_DPI=220

DOC_READER_ENABLE_MEDOCR_REFERENCE_LAYER=1
DOC_READER_MEDOCR_VISION_DATASET_ID=naazimsnh02/medocr-vision-dataset

DOC_READER_ENABLE_ADVANCED_PARSERS=1
DOC_READER_PARSER_BACKENDS=paddle_structure
DOC_READER_PADDLE_STRUCTURE_DEVICE=gpu:0
DOC_READER_PADDLE_STRUCTURE_GPU_MEMORY_FRACTION=0.55
DOC_READER_PADDLE_STRUCTURE_GPU_STOP_FRACTION=0.80
DOC_READER_PADDLE_STRUCTURE_MAX_PAGES_PER_DOCUMENT=5
DOC_READER_PADDLE_STRUCTURE_CPU_THREADS=1

DOC_READER_ENABLE_LOCAL_HF_LLM=0
DOC_READER_ENABLE_LOCAL_HF_VISION_MODELS=0
DOC_READER_VALIDATION_BACKEND=heuristic
DOC_READER_ENABLE_TRANSFORMER_VALIDATION=0
DOC_READER_DOC_WORKERS=1
DOC_READER_MAX_WORKERS=4
```

Legacy `EVOLET_*` variables are still accepted as compatibility fallbacks through `evolet/pipeline/services/config.py`.

## Local Development

Backend:

```bash
cd evolet
python manage.py migrate
python manage.py check
python manage.py runserver 0.0.0.0:9000
```

Frontend:

```bash
cd evolet/frontend
npm install
npm run dev
```

Production frontend build:

```bash
cd evolet/frontend
npm run build
```

## Docker Deployment

```bash
cd evolet
docker compose up -d --build
```

Default services:

- frontend: `http://localhost:3000`
- backend API: `http://localhost:9000/api/v1/dashboard/`
- Postgres: `doc_reader_postgres`
- Redis: `doc_reader_redis`

## Live VM

Last deployed target:

```text
ssh -i D:\data\evolet_rsa pardeep@34.126.112.227
```

Live URLs:

- frontend: `http://34.126.112.227:3000`
- backend dashboard API: `http://34.126.112.227:9000/api/v1/dashboard/`
- runs page: `http://34.126.112.227:3000/runs`

The VM has an NVIDIA L4 GPU. Keep GPU-heavy paths capped:

- one document worker for GPU parser/vision runs
- max four general workers
- Paddle GPU memory fraction below 0.8
- page and crop caps on all vision routes

## Useful Commands

Run a bounded handwriting extraction test:

```bash
cd evolet
docker compose exec -T \
  -e DOC_READER_SKIP_EXISTING=0 \
  -e DOC_READER_ENABLE_HANDWRITING_ORDER_EXTRACTOR=1 \
  -e DOC_READER_ENABLE_MEDOCR_REFERENCE_LAYER=1 \
  -e DOC_READER_ENABLE_GROQ_VISION_OCR=1 \
  -e DOC_READER_ENABLE_AUTO_SCHEMA=0 \
  backend python manage.py run_pipeline \
  --limit 1 \
  --no-4bit \
  --name "1 PDF handwriting extraction smoke"
```

Run a capped 3-PDF validation:

```bash
cd evolet
docker compose exec -T \
  -e DOC_READER_SKIP_EXISTING=0 \
  -e DOC_READER_DOC_WORKERS=1 \
  -e DOC_READER_MAX_WORKERS=4 \
  -e DOC_READER_HANDWRITING_ORDER_MAX_PAGES_PER_DOCUMENT=5 \
  -e DOC_READER_HANDWRITING_ORDER_MAX_CROPS_PER_PAGE=8 \
  backend python manage.py run_pipeline \
  --limit 3 \
  --no-4bit \
  --name "3 PDF handwriting validation"
```

Check backend health:

```bash
cd evolet
docker compose exec -T backend python manage.py check
docker stats --no-stream doc_reader_backend doc_reader_worker
nvidia-smi
```

## Important Backend Files

- `evolet/pipeline/orchestrator.py`
- `evolet/pipeline/services/layout_segmenter.py`
- `evolet/pipeline/services/page_vision_sweep.py`
- `evolet/pipeline/services/handwriting_order_extractor.py`
- `evolet/pipeline/services/artifact_extractor.py`
- `evolet/pipeline/services/medical_short_forms.py`
- `evolet/pipeline/services/medocr_reference.py`
- `evolet/pipeline/services/parsing/paddle_parser.py`
- `evolet/pipeline/services/auto_schema.py`
- `evolet/pipeline/services/validation.py`
- `evolet/pipeline/api_views.py`

## Known Limits

- Doctor handwriting is still probabilistic. The best current path is crop selection plus strict vision prompting plus medical normalization.
- PaddleOCR PP-Structure may return zero artifacts for some PDFs; the pipeline still falls back to native text, page vision, and chart-region crops.
- Groq quota can block final schema cleanup even when extraction has completed. The extracted artifacts and mentions are still saved.
- The Paddle GPU packages were live-installed on the VM after rebuild during testing. For permanent deployment, bake the chosen Paddle GPU version into the Docker image.
- `pkg_resources` emits a deprecation warning from SymSpell, but the dictionary loads and correction runs with `setuptools<81`.

## Repository

GitHub:

```text
https://github.com/martian3062/doc_ocr.git
```
