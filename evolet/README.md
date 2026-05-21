# doc-ocr Django-only runtime

This branch serves the complete `doc-ocr` interface from Django templates,
static CSS, and browser JavaScript. There is no Next.js frontend in this branch.

## Runtime Shape

- Django project: `doc_reader`
- Django app: `pipeline`
- UI: `templates/` + `static/`
- Queue: Redis/RQ
- Database: Postgres in Docker, SQLite for simple local runs
- AI extraction: Groq/cloud path when `DOC_READER_GROQ_API_KEY` is set
- Validation: heuristic by default
- Local HF/GPU models: disabled by default

## OCR / Extraction Stack

This branch is CPU/cloud-safe, but it is still a layered OCR system. Regex is
the final deterministic field extractor; the other layers prepare evidence,
read difficult crops, normalize medical text, build schema, and validate the
record.

| Layer | Tech stack | What it does |
| --- | --- | --- |
| PDF reader | PyMuPDF / `fitz` | Extracts native text, page geometry, text blocks, and embedded image metadata. |
| Page evidence | Django ORM + Postgres `PageLedger` | Stores per-page text, selected source, counts, dimensions, and layout metadata. |
| Page/crop vision | Groq vision, capped by page/crop env limits | Reads selected visual regions when medicine/order pages need OCR beyond native text. |
| Handwriting order extraction | Custom crop router + Groq | Targets anchors such as `Staff Nurse ... medicines and injections` and extracts medicine/vital order crops. |
| Artifact builder | `layout_segmenter.py` | Converts native blocks and crop outputs into evidence artifacts with page, bbox, role, backend, and text. |
| Note grouping | Layout-aware note segmenter | Groups page artifacts into clinical note chunks. |
| Field extraction | Regex + medical rules + artifact extraction | Creates `Mention` rows for diagnosis, medication, dates, vitals, investigations, and other schema fields. |
| Medicine cleanup | Dictionary, fuzzy matching, medical short forms | Normalizes drug names, doses, routes, and common OCR/handwriting variants. |
| Text correction | SymSpell + medical whitelist | Cleans OCR spelling noise after extraction while preserving evidence. |
| Schema builder | Deep schema Python builder | Groups mentions into diagnosis, medication, imaging, pathology, timeline, treatment, and quality sections. |
| Optional enrichment | Groq text schema model | Adds richer schema summaries when quota allows; deterministic schema remains the fallback. |
| Validation | Heuristic validator | Flags missing categories, low evidence, low mention count, and review risks. |
| UI/runtime | Django templates, HTMX, Alpine, Redis/RQ, Postgres | Shows upload/import, runs, QC, patient detail, PDF viewer, schema tree, and live recent-run updates. |

Latest VM smoke verification for this branch:

- 10 PDFs processed
- 42 note chunks
- 296 mentions
- 1062 artifacts
- Kaushal report: 5 notes, 44 mentions, 9 categories, 9 schema sections

The VM image intentionally does not install `torch`, PaddleOCR, TrOCR, HTR-VT,
or local HF models. Those should be added later as a separate sidecar/full OCR
image when the GPU is idle. The production-safe path today is:

```text
PyMuPDF native text
  -> deterministic medicine/order crop targeting
  -> Groq crop vision for selected handwriting/table regions
  -> regex + medical rules for auditable mentions
  -> normalization, schema building, validation, and Django review UI
```

## Why This Branch Exists

The GPU VM may be busy with other model training. This branch avoids competing
for VRAM by removing the CUDA image, Node build, local HF model loading, and
heavy OCR packages from the default container.

The extraction may take longer, but it should not OOM the training job.

## Docker Deploy

```bash
cd evolet
DOC_READER_DJANGO_PORT=7000 docker compose up -d --build
```

Use any free host port from `7000-7300`:

```bash
DOC_READER_DJANGO_PORT=7010 docker compose up -d --build
```

Services:

- `doc_reader_django_only_web`
- `doc_reader_django_only_worker`
- `doc_reader_django_only_postgres`
- `doc_reader_django_only_redis`

The web service listens inside the container on port `9000` and is exposed on
the selected host port.

## Safe Defaults

```env
DOC_READER_LLM_PROVIDER=groq
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

## Local Django

```bash
python manage.py migrate
python manage.py check
python manage.py runserver 0.0.0.0:9000
```

## Main Files

- `Dockerfile`
- `docker-compose.yml`
- `requirements-django-only.txt`
- `doc_reader/settings.py`
- `pipeline/views.py`
- `pipeline/api_views.py`
- `templates/`
- `static/`
