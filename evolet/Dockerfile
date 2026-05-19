# doc-reader Dockerfile
# Base: CUDA 12.4 + cuDNN runtime on Ubuntu 22.04 for the L4 GPU VM.
# Build: docker build -t doc-reader-pipeline .
# Run: docker compose --profile gpu up

FROM nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3.10 python3.10-dev python3.10-distutils python3-pip \
        libglib2.0-0 libsm6 libxrender1 libxext6 libgl1-mesa-glx \
        libgomp1 \
        poppler-utils ghostscript curl \
    && rm -rf /var/lib/apt/lists/*

RUN update-alternatives --install /usr/bin/python python /usr/bin/python3.10 1 \
 && update-alternatives --install /usr/bin/pip pip /usr/bin/pip3 1 \
 && pip install --no-cache-dir --upgrade pip

WORKDIR /app
COPY requirements.txt .

RUN pip install --no-cache-dir \
    torch==2.6.0+cu124 torchvision==0.21.0+cu124 \
    --index-url https://download.pytorch.org/whl/cu124

RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN python manage.py collectstatic --noinput

ENV DJANGO_SETTINGS_MODULE=doc_reader.settings \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DOC_READER_DOC_WORKERS=4 \
    DOC_READER_ENABLE_ADVANCED_PARSERS=1

EXPOSE 9000

COPY docker-entrypoint.sh /entrypoint.sh
RUN sed -i 's/\r$//' /entrypoint.sh && chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
