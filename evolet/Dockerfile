# doc-ocr Dockerfile.
# The stable runtime can stay lightweight, while the 20226_tech VM build can
# install CUDA PyTorch plus advanced SAHI/layout/HF model dependencies.

FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        git libglib2.0-0 libsm6 libxrender1 libxext6 libgl1 libgomp1 \
        poppler-utils ghostscript curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir --upgrade pip

WORKDIR /app
ARG DOC_READER_INSTALL_FULL_MODELS=1
ARG PYTORCH_CUDA_INDEX_URL=https://download.pytorch.org/whl/cu121
COPY requirements-django-only.txt requirements.txt requirements-advanced.txt ./

RUN if [ "$DOC_READER_INSTALL_FULL_MODELS" = "1" ]; then \
        pip install --no-cache-dir torch==2.5.1 torchvision==0.20.1 --index-url "$PYTORCH_CUDA_INDEX_URL" && \
        pip install --no-cache-dir -r requirements.txt && \
        pip install --no-cache-dir -r requirements-advanced.txt ; \
    else \
        pip install --no-cache-dir -r requirements-django-only.txt ; \
    fi

COPY . .

RUN python manage.py collectstatic --noinput

ENV DJANGO_SETTINGS_MODULE=doc_reader.settings \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DOC_READER_QUEUE_MODE=rq \
    DOC_READER_DOC_WORKERS=1 \
    DOC_READER_MAX_WORKERS=4 \
    DOC_READER_LLM_PROVIDER=groq \
    DOC_READER_ENABLE_LOCAL_HF_LLM=1 \
    DOC_READER_ENABLE_LOCAL_HF_VISION_MODELS=1 \
    DOC_READER_ENABLE_ADVANCED_PARSERS=1 \
    DOC_READER_PARSER_BACKENDS=sahi_prescription,yolo_layout \
    DOC_READER_ENABLE_SAHI_PRESCRIPTION_SEGMENTATION=1 \
    DOC_READER_SAHI_PRESCRIPTION_DEVICE=cuda:0 \
    DOC_READER_ENABLE_HANDWRITING_OCR=1 \
    DOC_READER_ENABLE_MEDICAL_HANDWRITING_OCR=1 \
    DOC_READER_ENABLE_GOT_VERIFICATION=0 \
    DOC_READER_SCHEMA_PROVIDER=local \
    DOC_READER_VALIDATION_BACKEND=heuristic \
    DOC_READER_ENABLE_TRANSFORMER_VALIDATION=0

EXPOSE 9000

COPY docker-entrypoint.sh /entrypoint.sh
RUN sed -i 's/\r$//' /entrypoint.sh && chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
