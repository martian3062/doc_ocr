# doc-ocr

`doc-ocr` is a Django-based clinical PDF understanding system for scanned
hospital records, chemotherapy sheets, order charts, and doctor handwriting.
The goal is not plain OCR text; the goal is evidence-linked structured medical
data that can be reviewed back against the source PDF.

The active branch and live VM deployment are the **Django-only runtime**. The
old Next.js frontend is not part of this branch. Django serves the dashboard,
upload/import screens, runs, patient detail pages, PDF viewer, QC, schema tree,
and API routes from one containerized app.

## Current Live VM

Verified live target:

```text
ssh -i D:\data\evolet_rsa pardeep@34.126.112.227
```

Live app:

```text
http://34.126.112.227:7000/
```

Current Docker services:

```text
doc_reader_django_only_web       Django web app, host 7000 -> container 9000
doc_reader_django_only_worker    RQ worker
doc_reader_django_only_postgres  Postgres
doc_reader_django_only_redis     Redis
```

Current resource policy:

```text
GPU: full NVIDIA L4 exposed to web and worker containers with gpus: all
CPU workers: DOC_READER_MAX_WORKERS fixed at 4
Document workers: DOC_READER_DOC_WORKERS defaults to 1
```

Latest VM health check after the GPU fix:

```text
GPU: NVIDIA L4
Driver: 580.159.03
CUDA shown by nvidia-smi: 13.0
VRAM: 23034 MiB total, 0 MiB used at check time
Disk: 484G total, 281G used, 204G free, 59%
RAM: 31Gi total, about 29Gi available after reboot
Django check: no issues
Public app: HTTP 200 on port 7000
Docker GPU: works with --gpus all
```

The earlier GPU failure was caused by a stale loaded NVIDIA kernel module
`580.126.09` with userspace/NVML `580.159.03`. A VM reboot aligned the driver
stack, and both host `nvidia-smi` and Docker GPU checks now work.

## Runtime Shape

This branch is intentionally safe for shared VM use:

- Python/Django runtime, no Node/Next build.
- Server-rendered templates with HTMX/Alpine-style browser interactions.
- Postgres + Redis/RQ through Docker Compose.
- CPU-safe default image.
- Groq/cloud OCR and schema calls when the API key is configured.
- Local HF, Paddle, TrOCR, and other heavy GPU paths disabled by default.
- GPU is available on the VM, but should be enabled only for explicit advanced
  runs or sidecar experiments.

## Pipeline

```text
PDF input
  -> PyMuPDF native text, page geometry, blocks, and image evidence
  -> page ledger with page text, dimensions, counts, and provenance
  -> layout/artifact construction with page, bbox, backend, role, and text
  -> medicine/order crop targeting for handwriting and treatment charts
  -> optional Groq crop vision for hard visual regions
  -> deterministic field extraction into Mention rows
  -> medicine normalization, short-form expansion, and fuzzy cleanup
  -> deep schema builder with treatment, medication tree, diagnosis, imaging,
     pathology, timeline, validation, and quality sections
  -> Django patient review UI with PDF viewer and schema tree
```

Regex is only the final auditable mention extractor. The upstream layers decide
what evidence is available; the downstream schema and validation layers make the
record reviewable.

## Medication And Terminology Layer

The current medication subschema builds a `treatment.medication_tree` with
medicine names, orders, dosage, route, frequency, duration, source pages, and
evidence quotes.

Live checks on the VM showed:

```text
scispacy: installed
medspacy: installed
requests: installed
RxNav API access: working
local rxnorm package: not installed
local snomed/umls packages: not installed
```

RxNorm/RxNav lookups work from inside the live web container:

```text
dexamethasone -> RxCUI 3264
paclitaxel    -> RxCUI 56946
carboplatin   -> RxCUI 40048
ondansetron   -> RxCUI 26225
```

For the active patient record `Anand  Kumar Jain - UHID 32685`, the VM record
exists and its medication tree contains `dexamethasone`. It does not yet persist
`coding.rxnorm` or `coding.snomed_ct` fields. The recommended next enrichment is
to add a lightweight RxNav resolver after medication parsing:

```json
{
  "medicine_name": "dexamethasone",
  "coding": {
    "rxnorm": {
      "rxcui": "3264",
      "name": "dexamethasone",
      "tty": "IN",
      "source": "rxnav",
      "match_type": "exact"
    }
  }
}
```

Use RxNorm as the primary medication coding system. Use SNOMED CT later for
diagnosis, procedure, route, clinical concept, or crosswalk enrichment after the
medication layer is stable.

## 2026 Advanced Technique Lane

Branch `20226_tech` now has a GPU-enabled experimental lane for the two latest
techniques below. The stable extraction path still exists, but the VM image can
also run CUDA PyTorch, SAHI, Ultralytics, local HF vision/LLM models, and the
SPARK-style schema verifier.

### SAHI-BAR For Prescription Instance Segmentation

SAHI-BAR is a 2026 prescription instance-segmentation paper: "SAHI-BAR: An
Instance Segmentation Model for Medical Prescriptions", Engineering,
Technology & Applied Science Research, Vol. 16 No. 2, April 2026. The method is
relevant because this project already struggles with small, dense handwritten
medicine/order regions inside high-resolution prescription and chemotherapy
chart images.

Potential fit for `doc-ocr`:

- replace or augment the current heuristic order-chart crop router;
- segment handwritten prescription/order components before OCR;
- improve line/field isolation for medicine name, dose, route, frequency, and
  instruction extraction;
- use slicing-aided inference ideas for small text/object regions without
  sending the whole page to a heavy model;
- keep the output as evidence artifacts with page, bbox, backend, and role.

Implemented integration:

```text
pipeline/services/parsing/sahi_prescription_parser.py
pipeline/services/parsing/ensemble.py
pipeline/services/layout_segmenter.py
```

Runtime switches:

```text
DOC_READER_ENABLE_ADVANCED_PARSERS=1
DOC_READER_PARSER_BACKENDS=sahi_prescription,yolo_layout
DOC_READER_ENABLE_SAHI_PRESCRIPTION_SEGMENTATION=1
DOC_READER_SAHI_PRESCRIPTION_DEVICE=cuda:0
```

The adapter uses SAHI sliced inference with a configurable Ultralytics model.
Public SAHI-BAR prescription weights were not found as a drop-in package, so
the default model is the configured document-layout detector until
prescription-specific weights are supplied.

Source: https://www.etasr.com/index.php/ETASR/article/view/16418

### SPARK For Agentic Cancer Pathology Research

SPARK, "System of Pathology Agents for Research and Knowledge", is a 2026
Nature Medicine framework for agentic AI in cancer pathology. It uses agentic
modules for idea generation, idea refinement, idea/parameter coding, and
parameter verification. The Nature Medicine paper describes SPARK as a
pathology reasoning workflow that operates on H&E whole-slide images, tissue
segmentation, and single-cell detection outputs to generate interpretable
biological parameters across cancer cohorts.

Potential fit for `doc-ocr`:

- not a direct OCR replacement;
- useful as a future research layer over pathology report fields, WSI-derived
  features, and structured schema outputs;
- can inspire an agentic "schema hypothesis" or "pathology biomarker concept"
  layer that proposes, codes, and verifies interpretable features;
- should consume verified artifacts and schema outputs, not raw OCR text;
- should remain separated from clinical extraction until validation and review
  controls are strong.

Implemented integration:

```text
pipeline/services/spark_agentic_schema.py
pipeline/orchestrator.py
```

The deployed implementation runs after merge/auto-schema and stores
`spark_agentic_analysis` in each `FinalRecord.grouped_record`. It generates
concepts, codes measurable parameters, and verifies evidence coverage,
medication completeness, oncology/pathology readiness, and SAHI region yield.
It does not overwrite deterministic medical fields.

Source: https://www.nature.com/articles/s41591-026-04357-y

Latest 5-PDF full-model VM test:

```text
run: 20226 tech full GPU SAHI SPARK 5 PDF 2026-05-27
status: completed
duration: 763.28 seconds
PDFs: 5/5
notes: 15 total, 15 resolved, 0 unresolved
mentions: 75
artifact backends:
  sahi_prescription: 103
  yolo_layout: 62
  page_vision_sweep: 44
  handwriting_ensemble: 125
  native: 352
  embedded_image: 23
SPARK: completed for all 5 final records
```

## Latest Verified OCR State

The Django-only VM smoke path has already processed:

```text
10 PDFs
42 note chunks
296 mentions
1062 artifacts
```

The Kaushal report produced:

```text
5 notes
44 mentions
9 categories
9 schema sections
```

The active schema version in the database is:

```text
4.0-auto-schema
```

## Deploy On VM

From the repo on the VM:

```bash
cd ~/TMH-OcR/evolet
DOC_READER_DJANGO_PORT=7000 docker compose up -d --build
```

Use a different free port if needed:

```bash
DOC_READER_DJANGO_PORT=7010 docker compose up -d --build
```

Check services:

```bash
sudo -n docker ps --format '{{.Names}} {{.Status}} {{.Ports}}' \
  | grep doc_reader_django_only
sudo -n docker stats --no-stream \
  doc_reader_django_only_web \
  doc_reader_django_only_worker \
  doc_reader_django_only_redis \
  doc_reader_django_only_postgres
```

Check Django and public app:

```bash
sudo -n docker exec doc_reader_django_only_web python manage.py check
curl -I http://127.0.0.1:7000/
```

Check GPU:

```bash
nvidia-smi
sudo -n docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
```

## Local Development

```bash
cd evolet
python manage.py migrate
python manage.py check
python manage.py runserver 0.0.0.0:9000
```

Docker locally:

```bash
cd evolet
DOC_READER_DJANGO_PORT=7000 docker compose up -d --build
```

## Important Environment Variables

Safe default profile:

```env
DOC_READER_LLM_PROVIDER=groq
DOC_READER_SCHEMA_PROVIDER=groq
DOC_READER_GROQ_API_KEY=...

DOC_READER_ENABLE_GROQ_VISION_OCR=1
DOC_READER_ENABLE_PAGE_VISION_SWEEP=1
DOC_READER_ENABLE_HANDWRITING_ORDER_EXTRACTOR=1

DOC_READER_ENABLE_LOCAL_HF_LLM=0
DOC_READER_ENABLE_LOCAL_HF_VISION_MODELS=0
DOC_READER_ENABLE_ADVANCED_PARSERS=0
DOC_READER_VALIDATION_BACKEND=heuristic
DOC_READER_ENABLE_TRANSFORMER_VALIDATION=0

DOC_READER_DOC_WORKERS=1
DOC_READER_MAX_WORKERS=4
DOC_READER_MULTIMODAL_MEDICINE_BACKENDS=dictionary
```

Optional advanced settings, only when GPU/runtime is explicitly verified:

```env
DOC_READER_ENABLE_ADVANCED_PARSERS=1
DOC_READER_PARSER_BACKENDS=paddle_structure
DOC_READER_PADDLE_STRUCTURE_DEVICE=gpu:0

DOC_READER_ENABLE_LOCAL_HF_LLM=1
DOC_READER_MODEL_ID=Qwen/Qwen2.5-7B-Instruct
DOC_READER_USE_4BIT=0

DOC_READER_ENABLE_LOCAL_HF_VISION_MODELS=1
DOC_READER_MULTIMODAL_MEDICINE_BACKENDS=keracare,donut,phi3,dictionary
```

Legacy `EVOLET_*` variables are still accepted as compatibility fallbacks in
`evolet/pipeline/services/config.py`.

## Main Files

```text
evolet/docker-compose.yml
evolet/Dockerfile
evolet/requirements-django-only.txt
evolet/doc_reader/settings.py
evolet/pipeline/orchestrator.py
evolet/pipeline/models.py
evolet/pipeline/views.py
evolet/pipeline/api_views.py
evolet/pipeline/services/layout_segmenter.py
evolet/pipeline/services/page_vision_sweep.py
evolet/pipeline/services/handwriting_order_extractor.py
evolet/pipeline/services/artifact_extractor.py
evolet/pipeline/services/schema_cleaner.py
evolet/pipeline/services/schema_builder.py
evolet/pipeline/services/auto_schema.py
evolet/pipeline/services/validation.py
evolet/templates/
evolet/static/
```

## Known Limits

- Doctor handwriting remains probabilistic. The best current path is tight crop
  targeting, Groq vision, dictionary medicine normalization, and schema review.
- Local HF/Paddle/TrOCR-style models are not part of the default Docker image.
  Add them as a sidecar or advanced image when GPU use is intentional.
- Groq quota can block optional auto-schema enrichment. Deterministic mentions,
  artifacts, and schema records still persist.
- RxNorm coding is verified as possible through RxNav but is not yet persisted
  in saved medication tree records.
- SNOMED/UMLS local packages are not installed in the live container.

## Repository

```text
https://github.com/martian3062/doc_ocr.git
```
