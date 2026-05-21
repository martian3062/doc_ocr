# doc-ocr Django-only Dockerfile.
# This branch intentionally avoids Node, CUDA, torch, and local HF model
# installs. The UI is served by Django templates/static files, while AI
# extraction uses Groq/cloud-safe paths when configured.

FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libglib2.0-0 libsm6 libxrender1 libxext6 libgl1 \
        poppler-utils ghostscript curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir --upgrade pip

WORKDIR /app
COPY requirements-django-only.txt .

RUN pip install --no-cache-dir -r requirements-django-only.txt

COPY . .

RUN python manage.py collectstatic --noinput

ENV DJANGO_SETTINGS_MODULE=doc_reader.settings \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DOC_READER_QUEUE_MODE=rq \
    DOC_READER_DOC_WORKERS=1 \
    DOC_READER_MAX_WORKERS=4 \
    DOC_READER_LLM_PROVIDER=groq \
    DOC_READER_ENABLE_LOCAL_HF_LLM=0 \
    DOC_READER_ENABLE_LOCAL_HF_VISION_MODELS=0 \
    DOC_READER_ENABLE_ADVANCED_PARSERS=0 \
    DOC_READER_ENABLE_HANDWRITING_OCR=0 \
    DOC_READER_ENABLE_MEDICAL_HANDWRITING_OCR=0 \
    DOC_READER_ENABLE_GOT_VERIFICATION=0 \
    DOC_READER_VALIDATION_BACKEND=heuristic \
    DOC_READER_ENABLE_TRANSFORMER_VALIDATION=0

EXPOSE 9000

COPY docker-entrypoint.sh /entrypoint.sh
RUN sed -i 's/\r$//' /entrypoint.sh && chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
