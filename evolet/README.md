# TMH OCR Pipeline — Evolet

> **Extracts structured clinical data from scanned & digital medical PDF reports.**
> A multi-stage pipeline combining native PDF text extraction, DocTR OCR, embedded-image OCR (EasyOCR), regex NER, and a 4-bit quantised Qwen 2.5 LLM — served through a modern Django + HTMX + Alpine.js web interface.

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10%2B-blue?logo=python" />
  <img src="https://img.shields.io/badge/Django-5.x-green?logo=django" />
  <img src="https://img.shields.io/badge/CUDA-12.4-76B900?logo=nvidia" />
  <img src="https://img.shields.io/badge/Docker-GPU%20ready-2496ED?logo=docker" />
  <img src="https://img.shields.io/badge/LLM-Qwen%202.5%201.5B-orange" />
</p>

---

## Table of Contents

1. [Pipeline Architecture](#pipeline-architecture)
2. [Tech Stack](#tech-stack)
3. [Quick Start — Docker (recommended)](#quick-start--docker-recommended)
4. [Quick Start — Local Dev](#quick-start--local-dev)
5. [Web UI](#web-ui)
6. [Configuration](#configuration)
7. [Output Format](#output-format)
8. [Project Structure](#project-structure)
9. [Database Schema](#database-schema)
10. [Performance & Hardware](#performance--hardware)
11. [Requirements](#requirements)

---

## Pipeline Architecture

Each uploaded PDF passes through nine sequential phases. Phases 1 and 3 run in parallel across documents via a thread pool.

```
PDF Upload
    │
    ├─ Phase 1 · Extraction  ──────────────────────────────────────────────┐
    │   ├─ 1a. Native text    (PyMuPDF fast path — digital PDFs)           │
    │   ├─ 1b. DocTR OCR      (GPU-accelerated — scanned / weak pages)     │  parallel
    │   └─ 1c. Embedded imgs  (EasyOCR — text inside figures / stamps)     │  per-doc
    │                                                                       │
    ├─ Phase 2 · Clean & Segment ──────────────────────────────────────────┘
    │   ├─ 2a. Text cleaner   (remove OCR noise, headers, duplicates)
    │   ├─ 2b. Spell corrector (SymSpell fast-pass)
    │   └─ 2c. Note segmenter  (split pages → clinical notes)
    │
    ├─ Phase 3 · Extract mentions ─────────────────────────────────────────┐
    │   ├─ 3a. Regex NER      (deterministic; covers 60-80 % of mentions)  │  parallel
    │   ├─ 3b. Skip-if-done   (SKIP_EXISTING — re-run safe)                │  per-note
    │   └─ 3c. LLM queue      (notes not fully resolved by regex)          │
    │                                                                       │
    ├─ Phase 4 · LLM Inference ────────────────────────────────────────────┘
    │   └─ Qwen 2.5 1.5B (4-bit, FP16 fallback) — adaptive batch sizes
    │       SHORT notes → batch 6 · MEDIUM → batch 4 · LONG → batch 2
    │
    └─ Phase 5 · Merge & Output ───────────────────────────────────────────┐
        ├─ Deduplicate regex + LLM mentions                                │  parallel
        ├─ Build grouped_record (by category)                              │  per-patient
        └─ Persist FinalRecord to DB + make downloadable JSON              │
```

**Design rationale:** Regex is fast and deterministic — it resolves the majority of mentions with zero GPU cost. The LLM handles only what regex misses, keeping VRAM usage low and per-PDF runtime under 30 s on an L4 GPU.

---

## Tech Stack

| Layer | Technology |
|---|---|
| **Web framework** | Django 5.x + Jinja2 templates |
| **Async UI** | HTMX 2.x (server-driven partials) + Alpine.js 3.x |
| **Styling** | Tailwind CSS CDN + Bulma + custom aurora CSS |
| **OCR (pages)** | python-doctr (FP16 GPU singleton) |
| **OCR (embedded images)** | EasyOCR (GPU, lazy-loaded) |
| **PDF parsing** | PyMuPDF (native text + image extraction) |
| **LLM** | Qwen/Qwen2.5-1.5B-Instruct via HuggingFace Transformers |
| **Quantisation** | bitsandbytes 4-bit (saves ~1.5 GB VRAM) |
| **Parallelism** | `concurrent.futures.ThreadPoolExecutor` (doc + merge phases) |
| **DB** | SQLite3 (WAL mode, 30 s timeout) via Django ORM |
| **Static files** | WhiteNoise (compressed + manifest) |
| **Container** | Docker — CUDA 12.4 + cuDNN 9 on Ubuntu 22.04 |
| **Deployment** | GCP VM (L4 24 GB GPU) · `docker compose --profile gpu up` |

---

## Quick Start — Docker (recommended)

### Prerequisites

- Docker + Docker Compose v2
- **GPU:** [nvidia-container-toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html) installed on the host
- **CPU-only:** no extra setup needed

### 1 · Clone

```bash
git clone https://github.com/martian3062/TMH-OcR.git
cd TMH-OcR
```

### 2 · Build

```bash
docker build -t evolet-pipeline .
```

> **Disk space note:** the build needs ~15 GB free (PyTorch CUDA wheels + all deps).
> Run `docker system prune -f` first if space is tight.

### 3 · Run

```bash
# GPU server (L4 / T4 / A100)
docker compose --profile gpu up -d

# CPU-only machine
docker compose --profile cpu up -d
```

The app starts at **http://localhost:9000** (or your server's public IP).

### 4 · Useful commands

```bash
# Follow live logs
docker compose logs -f

# Rebuild after code changes
docker compose --profile gpu up -d --build

# Stop
docker compose --profile gpu down

# Shell into running container
docker exec -it evolet_gpu bash
```

### Persistent volumes

| Volume | Container path | Contains |
|---|---|---|
| `evolet_media` | `/app/media` | Uploaded PDF files |
| `evolet_db` | `/app/data` | SQLite database |
| `hf_cache` | `/root/.cache/huggingface` | Downloaded LLM + OCR model weights |

Model weights (~3 GB) are downloaded on first pipeline run and cached in the `hf_cache` volume — subsequent runs start immediately.

---

## Quick Start — Local Dev

```bash
# 1. Create virtual environment
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

# 2. Install PyTorch (CUDA) first, then remaining deps
pip install torch==2.6.0+cu124 torchvision==0.21.0+cu124 \
    --index-url https://download.pytorch.org/whl/cu124
pip install -r requirements.txt

# 3. Initialise database
python manage.py migrate

# 4. Collect static files
python manage.py collectstatic --noinput

# 5. Start server
python manage.py runserver 0.0.0.0:9000
```

Open **http://localhost:9000**.

---

## Web UI

### Pages

| Page | Description |
|---|---|
| **Dashboard** | Live stats (patients · PDFs · mentions · GPU), hardware/ETA panel, recent activity |
| **Upload** | Drag-and-drop multi-PDF upload with HTMX progress and per-file previews |
| **Patients** | Searchable patient list with HTMX delete confirmation |
| **Patient Detail** | Category accordion (Diagnosis · Medication · Imaging · Notes), raw JSON viewer, **3D knowledge graph** |
| **Runs** | Start, monitor, and cancel pipeline runs; step-wise live progress tracker |
| **Run Detail** | Per-stage status badges, collapsible execution log (INFO / WARN / ERR) |
| **QC Summary** | Coverage bars, missing-field badges, flagged-only filter |

### UI highlights

- **Aurora mesh background** — 5 animated gradient orbs with grain overlay
- **Dark / light mode** — persisted in `localStorage`, system-preference aware
- **3D knowledge graph** — Patient → Category → Finding → Date radial tree, Phong-shaded spheres, bloom glow, flowing link particles (Three.js + 3d-force-graph)
- **Hardware / ETA panel** — detects GPU model, VRAM, CPU, RAM; shows per-PDF time estimate and tier badge
- **HTMX live updates** — progress polling every 3 s, no full-page reloads
- **Cancel run** — stops pipeline after current step; safe for re-run
- **Delete patient / document** — cascade-deletes all related data with HTMX confirmation
- **`⌘K` spotlight** — keyboard quick-navigation
- **Toast notifications** — async feedback for uploads, errors, completions

---

## Configuration

All tuneable parameters are environment variables — no code changes needed.

### Docker Compose overrides

Edit the `environment:` block in `docker-compose.yml`:

```yaml
environment:
  DJANGO_SECRET_KEY: "your-strong-secret-here"
  DJANGO_DEBUG: "0"
  EVOLET_MODEL_ID: "Qwen/Qwen2.5-1.5B-Instruct"
  EVOLET_USE_4BIT: "1"
  EVOLET_DOC_WORKERS: "4"
  HF_TOKEN: "hf_xxx"           # only for gated HuggingFace models
```

### Full variable reference

| Variable | Default | Description |
|---|---|---|
| `DJANGO_SECRET_KEY` | (dev key) | **Change in production.** Django secret key |
| `DJANGO_DEBUG` | `1` | Set to `0` in production |
| `EVOLET_MODEL_ID` | `Qwen/Qwen2.5-1.5B-Instruct` | HuggingFace model ID for LLM phase |
| `EVOLET_USE_4BIT` | `1` | 4-bit quantisation — saves ~1.5 GB VRAM |
| `EVOLET_DOC_WORKERS` | `4` | Thread pool size for Phase 1 (extraction) |
| `EVOLET_MAX_WORKERS` | `4` | Thread pool size for other parallel stages |
| `HF_TOKEN` | _(empty)_ | HuggingFace auth token for gated models |

---

## Output Format

Each patient record stored in the database and available as downloadable JSON:

```json
{
  "patient_code": "TMH_2023_001",
  "mentions": [
    {
      "category": "diagnosis",
      "label": "diagnosis",
      "value": "Renal Cell Carcinoma, Stage III",
      "date_text": "15.03.2023",
      "certainty": "confirmed",
      "evidence_quote": "Final Diagnosis: RCC Stage III",
      "source_pages": [2],
      "origin": "regex"
    },
    {
      "category": "medication",
      "label": "drug",
      "value": "Tab Sunitinib 50mg OD",
      "date_text": "20.03.2023",
      "certainty": "confirmed",
      "evidence_quote": "Tab Sunitinib 50mg OD for 4 weeks",
      "source_pages": [4],
      "origin": "llm"
    }
  ],
  "grouped_record": {
    "diagnosis":  ["Renal Cell Carcinoma, Stage III"],
    "medication": ["Tab Sunitinib 50mg OD"],
    "imaging":    ["CT Abdomen — March 2023"],
    "follow_up":  ["Review after 4 weeks"]
  },
  "stats": {
    "page_count": 12,
    "note_count": 8,
    "mentions_after_merge": 14
  }
}
```

**Extracted categories:** `diagnosis` · `medication` · `imaging` · `pathology` · `symptom` · `lab` · `genomics` · `surgery` · `radiotherapy` · `plan` · `follow_up` · `performance_status` · `procedure` · `status` · `other`

---

## Project Structure

```
evolet/
├── Dockerfile                    # CUDA 12.4 + cuDNN 9, Ubuntu 22.04
├── docker-compose.yml            # GPU + CPU profiles, 3 named volumes
├── docker-entrypoint.sh          # migrate → collectstatic → runserver
├── requirements.txt              # Python deps (torch installed separately)
├── manage.py
│
├── evolet/                       # Django project config
│   ├── settings.py               # Env-var driven, SQLite, WhiteNoise
│   ├── urls.py
│   ├── wsgi.py
│   └── jinja2.py                 # Jinja2 environment (url, static, tojson)
│
├── pipeline/                     # Core Django app
│   ├── models.py                 # Patient → PDFDocument → Page/Note → Mention → FinalRecord
│   ├── views.py                  # Dashboard, upload, run, patient, QC, cancel, delete, system API
│   ├── urls.py                   # All URL patterns
│   ├── orchestrator.py           # Phase coordinator (ThreadPoolExecutor, F() atomics)
│   │
│   └── services/                 # One file per concern
│       ├── config.py             # All tuneable knobs (env-var overridable)
│       ├── pdf_extractor.py      # Native text + DocTR OCR + EasyOCR (3 phases, thread-safe singletons)
│       ├── text_cleaner.py       # OCR artifact removal, deduplication
│       ├── text_corrector.py     # SymSpell spell correction
│       ├── note_segmenter.py     # Page → clinical note splitting + regex triage
│       ├── regex_extractor.py    # Deterministic mention extraction (15 categories)
│       ├── llm_engine.py         # Qwen inference + adaptive batching (SHORT/MEDIUM/LONG)
│       ├── merger.py             # Deduplication + final record assembly
│       ├── schema_builder.py     # Output schema construction
│       ├── qc.py                 # Quality metrics + coverage scoring
│       ├── photo_extractor.py    # Patient photo from PDF page 1
│       ├── json_utils.py         # Robust LLM JSON parser (4-stage fallback)
│       └── gpu_utils.py          # CUDA/CPU detect, VRAM, system_info() for ETA
│
├── templates/
│   ├── base.html                 # Nav, dark mode, aurora background, toasts, spotlight
│   ├── dashboard.html            # Stats + hardware/ETA panel
│   └── pipeline/
│       ├── upload.html
│       ├── patient_list.html
│       ├── patient_detail.html   # Accordion + 3D knowledge graph + JSON viewer
│       ├── run_list.html
│       ├── run_detail.html       # Live log viewer + cancel button
│       ├── qc_summary.html
│       ├── components/           # Reusable Jinja2 partials (card, status_badge, progress_bar)
│       └── partials/             # HTMX response fragments (patient_rows, run_progress, gpu_status …)
│
└── static/
    ├── css/main.css              # Aurora mesh, Bulma dark-mode patches, stagger animations
    └── js/app.js                 # Alpine stores: toasts, spotlight, jsonViewer, etaPanel, uploadHandler
```

---

## Database Schema

```
Patient  ──<  PDFDocument  ──<  PageLedger    (one row per PDF page)
                            └─<  NoteLedger    (one row per clinical note)

Mention         >──  Patient / PDFDocument / PipelineRun / NoteLedger
FinalRecord     >──  Patient                  (one merged JSON record per patient)
PipelineRun     >──<  PDFDocument             (many-to-many via M2M field)
ProcessingLog   >──  PipelineRun              (timestamped INFO / WARN / ERR entries)
```

---

## Performance & Hardware

### Benchmarks (GCP L4 — 24 GB VRAM)

| Stage | Time per PDF |
|---|---|
| Native text extraction | < 1 s |
| DocTR OCR (scanned pages) | 3–8 s |
| EasyOCR (embedded images) | 1–4 s |
| Regex NER | < 0.5 s |
| LLM (Qwen 2.5 1.5B, 4-bit) | 5–15 s |
| Merge & persist | < 1 s |
| **Total (typical)** | **~25–30 s** |

### Parallelism

- **Phase 1** (PDF extraction): `DOC_WORKERS` threads process documents concurrently
- **Phase 3** (regex): parallel per-note extraction within each document
- **Phase 5** (merge): parallel per-patient merge
- **Thread safety**: GPU singletons guarded by `_gpu_lock` + double-checked locking; DB counter updates via `F()` expressions

### Compute tiers

| Tier | Hardware | ETA |
|---|---|---|
| `gpu_fast` | GPU ≥ 20 GB VRAM | ~20 s/PDF |
| `gpu_std` | GPU < 20 GB VRAM | ~40 s/PDF |
| `cpu` | CPU only | ~180 s/PDF |

---

## Requirements

| Requirement | Minimum |
|---|---|
| Python | 3.10+ |
| GPU VRAM | 6 GB (24 GB recommended for full parallel batch runs) |
| CUDA | 12.4 (container provides this) |
| Disk (build) | 15 GB free for Docker build |
| Disk (runtime) | ~3 GB for model weights + PDF storage |
| RAM | 8 GB+ |

> **CPU-only mode** works via `docker compose --profile cpu up -d` but the LLM phase takes ~3 min per note instead of ~5 s.

---

## License

Proprietary — 4BaseCare / TMH Project. All rights reserved.
