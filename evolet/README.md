# doc-ocr Django-only runtime

This directory contains the active deployable app for `doc-ocr`. It is a
Django-only runtime: Django templates, static assets, Postgres, Redis/RQ, and
the OCR/schema pipeline run without the old Next.js frontend.

## Live VM Alignment

Verified VM:

```text
ssh -i D:\data\evolet_rsa pardeep@34.126.112.227
```

Live URL:

```text
http://34.126.112.227:7000/
```

Current services:

```text
doc_reader_django_only_web       host 7000 -> container 9000
doc_reader_django_only_worker
doc_reader_django_only_postgres
doc_reader_django_only_redis
```

Current resource policy:

```text
GPU: full NVIDIA L4 exposed to web and worker containers with gpus: all
CPU workers: DOC_READER_MAX_WORKERS fixed at 4
Document workers: DOC_READER_DOC_WORKERS defaults to 1
```

Current health after the VM reboot/GPU fix:

```text
Django check: no issues
Public app: HTTP 200
GPU: NVIDIA L4, driver 580.159.03, 23034 MiB VRAM
Docker GPU: works with --gpus all
Disk: 484G total, 281G used, 204G free
RAM: about 29Gi available after reboot
```

## Runtime Shape

- Django project: `doc_reader`
- Django app: `pipeline`
- UI: `templates/` + `static/`
- Queue: Redis/RQ
- Database: Postgres in Docker, SQLite for simple local runs
- AI extraction: Groq/cloud path when `DOC_READER_GROQ_API_KEY` is set
- Validation: heuristic by default
- Local HF/GPU models: disabled by default

This branch exists because the VM is shared and may also run pathology training
or GPU experiments. The default image stays CPU/cloud-safe. GPU use is possible
now that the driver is fixed, but it should be turned on deliberately.

## OCR And Schema Stack

Regex is the deterministic field-capture layer, not the whole system. The
pipeline keeps evidence first and builds schema after extraction.

| Layer | Tech stack | What it does |
| --- | --- | --- |
| PDF reader | PyMuPDF / `fitz` | Extracts native text, page geometry, blocks, and image metadata. |
| Page ledger | Django ORM + Postgres | Stores per-page text, dimensions, counts, source type, and layout metadata. |
| Crop/page vision | Groq vision, capped by env limits | Reads selected visual regions when native text is weak. |
| Handwriting order extraction | Custom crop router + Groq | Targets treatment/order-chart anchors and extracts medicine/vital crops. |
| Artifact builder | `layout_segmenter.py` | Builds evidence artifacts with page, bbox, role, backend, and text. |
| Note grouping | Layout-aware grouping | Groups artifacts into clinical note chunks. |
| Field extraction | Regex + medical rules + `artifact_extractor.py` | Creates auditable `Mention` rows for diagnosis, medication, dates, vitals, labs, plans, and other fields. |
| Medicine cleanup | Dictionary, fuzzy matching, short-form expansion | Normalizes drug names, doses, routes, fluids, and common OCR/handwriting variants. |
| Hybrid schema cleaner | `schema_cleaner.py` | Cleans final mentions and enriches medicine parts after merge. |
| Schema builder | `schema_builder.py` | Builds `4.0-auto-schema`, including treatment and medication tree sections. |
| Optional auto schema | Groq text model | Adds richer summaries when quota allows; deterministic schema remains fallback. |
| Validation | Heuristic validator | Flags missing categories, weak evidence, and review risks. |
| UI/runtime | Django templates, HTMX, Alpine, Redis/RQ, Postgres | Shows upload/import, runs, QC, patient detail, PDF viewer, schema tree, and recent run updates. |

Latest verified smoke state:

```text
10 PDFs processed
42 note chunks
296 mentions
1062 artifacts
Kaushal report: 5 notes, 44 mentions, 9 categories, 9 schema sections
```

## Medication And Terminology

The current schema stores medication data under:

```text
grouped_record.treatment.medication_tree
```

The tree contains medicine names, order rows, dosage, route, frequency,
duration, source pages, and evidence quotes.

VM terminology check:

```text
scispacy: installed
medspacy: installed
requests: installed
RxNav API: working
local rxnorm package: not installed
local snomed/umls packages: not installed
```

RxNorm examples verified from inside `doc_reader_django_only_web`:

```text
dexamethasone -> RxCUI 3264
paclitaxel    -> RxCUI 56946
carboplatin   -> RxCUI 40048
ondansetron   -> RxCUI 26225
```

The next clean medication upgrade is a lightweight RxNav resolver that appends
`coding.rxnorm` to medication tree entries and orders. SNOMED/UMLS should come
after that, mostly for clinical crosswalk and non-medication concepts.

## Advanced Technique Lane

These techniques are implemented on the `20226_tech` branch as an experimental
GPU lane. The stable VM pipeline remains available, but this branch can run
CUDA PyTorch, SAHI, Ultralytics, local HF vision/LLM models, and SPARK-style
schema verification.

### SAHI-BAR

SAHI-BAR is a 2026 instance-segmentation approach for medical prescriptions. It
is relevant to this app because prescription/order pages contain small dense
regions where medicine names, doses, fluids, and route instructions are hard to
isolate with whole-page OCR.

Implemented app use:

- optional prescription/order-region segmenter before crop OCR;
- SAHI sliced inference for dense prescription/order regions;
- artifact output with `backend=sahi_prescription`, page, bbox, role, model
  metadata, and confidence;
- supplement for the existing `handwriting_order_extractor.py` and
  `page_vision_sweep.py` lanes.

Implemented files:

```text
pipeline/services/parsing/sahi_prescription_parser.py
pipeline/services/parsing/ensemble.py
pipeline/services/layout_segmenter.py
```

Reference: https://www.etasr.com/index.php/ETASR/article/view/16418

### SPARK

SPARK is a 2026 Nature Medicine agentic framework for cancer pathology research.
It is not an OCR model. It is a research workflow that generates, refines,
codes, and verifies interpretable pathology parameters from structured WSI and
cell/tissue data.

Implemented app use:

- post-merge agentic schema verifier;
- concepts for evidence coverage, medication completeness, oncology/pathology
  readiness, and SAHI segmentation yield;
- stored in `FinalRecord.grouped_record.spark_agentic_analysis`;
- never overwrites extracted medical facts.

Implemented files:

```text
pipeline/services/spark_agentic_schema.py
pipeline/orchestrator.py
```

Reference: https://www.nature.com/articles/s41591-026-04357-y

Latest 5-PDF VM test:

```text
run: 20226 tech full GPU SAHI SPARK 5 PDF 2026-05-27
status: completed
duration: 763.28 seconds
PDFs: 5/5
mentions: 75
sahi_prescription artifacts: 103
yolo_layout artifacts: 62
SPARK final records: 5/5 completed
```

## Docker Deploy

```bash
DOC_READER_DJANGO_PORT=7000 docker compose up -d --build
```

Use another free port if needed:

```bash
DOC_READER_DJANGO_PORT=7010 docker compose up -d --build
```

The web service listens inside the container on `9000` and is exposed on the
selected host port.

## Safe Defaults

```env
DOC_READER_LLM_PROVIDER=groq
DOC_READER_SCHEMA_PROVIDER=groq
DOC_READER_ENABLE_GROQ_VISION_OCR=1
DOC_READER_ENABLE_PAGE_VISION_SWEEP=1
DOC_READER_ENABLE_HANDWRITING_ORDER_EXTRACTOR=1

DOC_READER_ENABLE_LOCAL_HF_LLM=0
DOC_READER_ENABLE_LOCAL_HF_VISION_MODELS=0
DOC_READER_ENABLE_HANDWRITING_OCR=0
DOC_READER_ENABLE_MEDICAL_HANDWRITING_OCR=0
DOC_READER_ENABLE_GOT_VERIFICATION=0
DOC_READER_ENABLE_ADVANCED_PARSERS=0
DOC_READER_VALIDATION_BACKEND=heuristic
DOC_READER_ENABLE_TRANSFORMER_VALIDATION=0
DOC_READER_DOC_WORKERS=1
DOC_READER_MAX_WORKERS=4
DOC_READER_MULTIMODAL_MEDICINE_BACKENDS=dictionary
```

Set `DOC_READER_GROQ_API_KEY` only in the VM/container environment. Do not
commit secrets.

## Operational Checks

```bash
sudo -n docker ps --format '{{.Names}} {{.Status}} {{.Ports}}' \
  | grep doc_reader_django_only

sudo -n docker exec doc_reader_django_only_web python manage.py check

sudo -n docker stats --no-stream \
  doc_reader_django_only_web \
  doc_reader_django_only_worker \
  doc_reader_django_only_postgres \
  doc_reader_django_only_redis

nvidia-smi

sudo -n docker run --rm --gpus all \
  nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
```

## Local Django

```bash
python manage.py migrate
python manage.py check
python manage.py runserver 0.0.0.0:9000
```

## Main Files

```text
Dockerfile
docker-compose.yml
requirements-django-only.txt
doc_reader/settings.py
pipeline/orchestrator.py
pipeline/models.py
pipeline/views.py
pipeline/api_views.py
pipeline/services/layout_segmenter.py
pipeline/services/page_vision_sweep.py
pipeline/services/handwriting_order_extractor.py
pipeline/services/artifact_extractor.py
pipeline/services/schema_cleaner.py
pipeline/services/schema_builder.py
pipeline/services/auto_schema.py
pipeline/services/validation.py
templates/
static/
```
