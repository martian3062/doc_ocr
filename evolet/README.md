# doc-reader Django-only runtime

This branch serves the complete `doc-reader` interface from Django templates,
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
