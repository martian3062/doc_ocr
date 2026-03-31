# ─────────────────────────────────────────────────────────────────────────────
# Evolet OCR Pipeline — Dockerfile
# Base: CUDA 12.4 + cuDNN 9 runtime on Ubuntu 22.04 (matches VM L4 runtime)
# Build: docker build -t evolet-pipeline .
# Run:   docker compose --profile gpu up
# ─────────────────────────────────────────────────────────────────────────────
FROM nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04

# ── System deps ───────────────────────────────────────────────────────────────
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3.10 python3.10-dev python3.10-distutils python3-pip \
        libglib2.0-0 libsm6 libxrender1 libxext6 libgl1-mesa-glx \
        poppler-utils ghostscript curl \
    && rm -rf /var/lib/apt/lists/*

RUN update-alternatives --install /usr/bin/python python /usr/bin/python3.10 1 \
 && update-alternatives --install /usr/bin/pip    pip    /usr/bin/pip3       1 \
 && pip install --no-cache-dir --upgrade pip

# ── Python deps (PyTorch first from cu124 index) ─────────────────────────────
WORKDIR /app
COPY requirements.txt .

RUN pip install --no-cache-dir \
    torch==2.6.0+cu124 torchvision==0.21.0+cu124 \
    --index-url https://download.pytorch.org/whl/cu124

RUN pip install --no-cache-dir -r requirements.txt

# ── App source ────────────────────────────────────────────────────────────────
COPY . .

# ── Collect static ────────────────────────────────────────────────────────────
RUN python manage.py collectstatic --noinput

# ── Runtime defaults ──────────────────────────────────────────────────────────
ENV DJANGO_SETTINGS_MODULE=evolet.settings \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    EVOLET_DOC_WORKERS=4

EXPOSE 9000

COPY docker-entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
