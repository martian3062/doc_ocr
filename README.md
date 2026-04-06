# TMH OCR — Evolet

**Live:** http://34.31.236.150:9000

<p align="left">
  <img src="https://img.shields.io/badge/Python-3.10%2B-blue?logo=python" />
  <img src="https://img.shields.io/badge/Django-5.x-green?logo=django" />
  <img src="https://img.shields.io/badge/Next.js-15-black?logo=next.js" />
  <img src="https://img.shields.io/badge/CUDA-12.4-76B900?logo=nvidia" />
  <img src="https://img.shields.io/badge/Docker-GPU%20ready-2496ED?logo=docker" />
  <img src="https://img.shields.io/badge/LLM-Qwen%202.5%201.5B-orange" />
</p>

Evolet is a medical PDF intelligence system that extracts structured oncology data from scanned and digital hospital documents, stores the results in Django, and serves them through a modern Next.js frontend.

It combines:

- OCR for scanned pages
- native PDF text extraction for digital documents
- regex-based entity extraction for speed and determinism
- LLM-based extraction for deeper clinical understanding
- a Django REST API and orchestration layer
- a Next.js dashboard and patient explorer UI

---

## Table of Contents

1. [What This Project Does](#1-what-this-project-does)
2. [Architecture](#2-architecture)
3. [Why These Technologies](#3-why-these-technologies)
4. [Quick Start — Docker](#4-quick-start--docker)
5. [Quick Start — Local Dev](#5-quick-start--local-dev)
6. [Full Processing Flow](#6-full-processing-flow)
7. [Frontend Pages](#7-frontend-pages)
8. [REST API Endpoints](#8-rest-api-endpoints)
9. [Configuration](#9-configuration)
10. [Output Format](#10-output-format)
11. [Data Model](#11-data-model)
12. [Project Structure](#12-project-structure)
13. [Performance & Hardware](#13-performance--hardware)
14. [Requirements](#14-requirements)

---

## 1. What This Project Does

The platform reads hospital PDFs and turns them into structured patient data.

Typical outputs include:

- patient identity and document grouping
- diagnoses and disease mentions
- medications and treatment references
- symptoms and follow-up notes
- timelines and evidence quotes
- merged patient summaries
- patient-level knowledge-map data for visualisation

At a high level:

1. PDFs are imported into the system.
2. Text is extracted by a fast native path or OCR path.
3. Text is cleaned and split into smaller note-like sections.
4. Regex extractors capture deterministic entities.
5. An LLM fills in harder or less structured mentions.
6. Mentions are merged into patient-level records.
7. Django stores the data and exposes REST API endpoints.
8. Next.js renders dashboard, patients, runs, documents, infra, and map pages.

---

## 2. Architecture

### System overview

```
Browser
  -> Next.js frontend  (port 3000)
  -> /api/v1/* rewrite
  -> Django backend    (port 9000)
  -> SQLite / media / model cache
```

### Runtime ports

| Port | Service |
|---|---|
| `9000` | Django backend API + legacy server-rendered UI |
| `3000` | Next.js frontend |

### Processing flow

```
PDF Upload
  -> PyMuPDF native text  (digital pages)
  -> DocTR OCR            (scanned pages)
  -> EasyOCR              (embedded images)
  -> text cleaning + spell correction
  -> note segmentation
  -> regex extraction     (fast, deterministic — 60-80 % coverage)
  -> LLM extraction       (Qwen 2.5 1.5B 4-bit — remaining mentions)
  -> merge + deduplication
  -> schema build + DB persist
  -> API response
  -> Next.js visualisation
```

### Pipeline phases in detail

```
Phase 1 · Extraction  ────────────────────────────────── parallel per-doc
  1a. Native text    (PyMuPDF fast path)
  1b. DocTR OCR      (GPU-accelerated, scanned pages)
  1c. EasyOCR        (embedded image text)

Phase 2 · Clean & Segment
  2a. Text cleaner   (OCR noise, headers, duplicates)
  2b. Spell corrector (SymSpell)
  2c. Note segmenter  (page → clinical notes)

Phase 3 · Extract ────────────────────────────────────── parallel per-note
  3a. Regex NER      (deterministic, 15 categories)
  3b. SKIP_EXISTING  (safe reruns)
  3c. LLM queue      (notes not resolved by regex)

Phase 4 · LLM Inference
  Qwen 2.5 1.5B (4-bit) — adaptive batching
  SHORT → batch 6 · MEDIUM → batch 4 · LONG → batch 2

Phase 5 · Merge & Output ─────────────────────────────── parallel per-patient
  Deduplicate · group · normalise · persist FinalRecord
```

---

## 3. Why These Technologies

### Backend and orchestration

- **Django** — strong ORM, admin, routing, management commands, stable foundation for data-heavy applications
- **SQLite** — simple, portable, fast enough for a single-VM pipeline; persisted via Docker volume
- **ThreadPoolExecutor** — parallel document extraction and patient merge without a separate job runner
- **django.db.models.F()** — atomic counter increments across threads without race conditions

### PDF and OCR

- **PyMuPDF** — fastest path for digital PDFs with selectable text
- **python-doctr** — GPU-accelerated document OCR for scanned pages
- **EasyOCR** — text inside embedded images, stamps, screenshots
- **Pillow / OpenCV** — image preprocessing during OCR stages

### Information extraction

- **Regex extraction** — fast, deterministic, explainable; handles the majority without GPU cost
- **Qwen 2.5 Instruct** — handles harder cases where clinical language is unstructured
- **bitsandbytes** — 4-bit quantisation so the LLM uses less VRAM
- **accelerate** — model loading and inference runtime management
- **json-repair + orjson** — recover malformed LLM JSON responses
- **rapidfuzz** — fuzzy matching in merge and normalisation
- **symspellpy** — fast spell correction for OCR-noisy text

### Frontend

- **Next.js 15** — file-based routing, production builds, easy Docker deployment
- **React** — interactive dashboards, patient pages, rich visualisations
- **Axios** — clean API client layer targeting the Django backend
- **Framer Motion** — page transitions and motion polish
- **D3** — patient knowledge graph layout
- **Three.js / @react-three/fiber / @react-three/drei** — 3D visual background effects
- **Lucide React** — consistent iconography
- **Tailwind CSS + clsx + tailwind-merge** — dynamic class composition
- **GSAP, Nivo, Recharts, react-dropzone** — animation, charts, file upload interactions

### Deployment

- **Docker + Docker Compose** — reproducible multi-container setup
- **NVIDIA container runtime** — GPU access for OCR and LLM inference inside containers

---

## 4. Quick Start — Docker

### Prerequisites

- Docker + Docker Compose v2
- GPU: [nvidia-container-toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html) installed on host
- CPU-only: no extra setup needed

### Clone

```bash
git clone https://bitbucket.org/tmh_ocr/tmh.git
cd tmh/evolet
```

### Build and run

```bash
# GPU server (L4 / T4 / A100)
docker compose --profile gpu up -d

# CPU-only machine
docker compose --profile cpu up -d
```

| URL | Service |
|---|---|
| http://localhost:9000 | Django backend / legacy UI |
| http://localhost:3000 | Next.js frontend |

### Useful commands

```bash
docker compose logs -f                          # live logs
docker compose --profile gpu up -d --build      # rebuild after code changes
docker compose --profile gpu down               # stop
docker exec -it evolet_gpu bash                 # shell into backend container
```

> **Disk space note:** the Docker build needs ~15 GB free (PyTorch CUDA wheels).
> Run `docker system prune -f` first if space is tight.

### Persistent volumes

| Volume | Path inside container | Contents |
|---|---|---|
| `evolet_media` | `/app/media` | Uploaded PDF files |
| `evolet_db` | `/app/data` | SQLite database |
| `hf_cache` | `/root/.cache/huggingface` | LLM and OCR model weights |

Model weights (~3 GB) download on first pipeline run and persist across restarts.

---

## 5. Quick Start — Local Dev

```bash
# 1. Virtual environment
python3 -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate

# 2. PyTorch CUDA first (prevents version conflicts)
pip install torch==2.6.0+cu124 torchvision==0.21.0+cu124 \
    --index-url https://download.pytorch.org/whl/cu124

# 3. Remaining deps
pip install -r requirements.txt

# 4. Initialise DB
python manage.py migrate
python manage.py collectstatic --noinput

# 5. Start backend
python manage.py runserver 0.0.0.0:9000
```

Frontend (separate terminal):

```bash
cd frontend
npm install
npm run dev       # starts on http://localhost:3000
```

---

## 6. Full Processing Flow

### Step 1 — Import PDFs

Documents enter through upload or import flows. Each PDF is stored as a `PDFDocument` linked to a `Patient`.

### Step 2 — Extract text

The extraction layer picks the best path per page:

- native text via PyMuPDF for digital PDFs
- DocTR OCR for scanned pages
- EasyOCR for embedded image text

### Step 3 — Clean

Removes OCR noise, duplicate headers/footers, broken spacing, and garbage characters.

### Step 4 — Spell correct

SymSpell fast-pass repairs common OCR distortions in medical text.

### Step 5 — Segment

Large page text is split into smaller note-like chunks for extraction.

### Step 6 — Regex extraction

Rule-based extraction detects: diagnosis · medication · symptom · procedure · pathology · follow-up · plan · imaging · lab · genomics · surgery · radiotherapy · performance status · status · other.

### Step 7 — LLM extraction

Qwen 2.5 1.5B handles harder sections: semi-structured summaries, free-form clinical text, richer relationship fields.

### Step 8 — Merge and normalise

Regex and LLM mentions are deduplicated, grouped, and normalised into a patient-level record.

### Step 9 — Persist

Merged results are stored in Django models. FinalRecord JSON and evidence-linked Mention rows are both saved.

### Step 10 — API

Django exposes REST-like endpoints at `/api/v1/*` consumed by the Next.js frontend.

### Step 11 — Render

Next.js fetches data and renders dashboard metrics, patient lists and detail pages, document inventory, run status, infrastructure info, and graph-based knowledge views.

---

## 7. Frontend Pages

| Route | Description |
|---|---|
| `/` | Dashboard — stats, recent activity, hardware/ETA panel |
| `/patients` | Patient list with search |
| `/patients/[id]` | Patient detail — accordion (Dx · Rx · Imaging · Notes), JSON viewer |
| `/patients/[id]/knowledge-map` | D3 knowledge graph — categories and mention nodes |
| `/documents` | Document inventory |
| `/runs` | Pipeline run list and status |
| `/infra` | Infrastructure info — GPU, CPU, RAM, compute tier |
| `/settings` | Settings |

---

## 8. REST API Endpoints

All endpoints are under `/api/v1/`. The Next.js frontend rewrites same-origin `/api/v1/*` calls to the Django backend.

| Method | Path | Description |
|---|---|---|
| GET | `/api/v1/dashboard` | Summary stats |
| GET | `/api/v1/documents` | Document list |
| GET | `/api/v1/patients` | Patient list |
| GET | `/api/v1/patients/<id>` | Patient detail + mentions |
| GET | `/api/v1/patients/<id>/knowledge-map` | Graph nodes and edges |
| GET | `/api/v1/runs` | Run list |
| GET | `/api/v1/runs/<id>` | Run detail + logs |
| GET | `/api/v1/system` | Hardware info + ETA |
| POST | `/api/v1/runs/<id>/cancel` | Cancel active run |

---

## 9. Configuration

All tuneable parameters are environment variables — no code changes needed.

Set them in the `environment:` block in `docker-compose.yml`:

```yaml
environment:
  DJANGO_SECRET_KEY: "change-this-in-production"
  DJANGO_DEBUG: "0"
  EVOLET_MODEL_ID: "Qwen/Qwen2.5-1.5B-Instruct"
  EVOLET_USE_4BIT: "1"
  EVOLET_DOC_WORKERS: "4"
  HF_TOKEN: "hf_xxx"
```

| Variable | Default | Description |
|---|---|---|
| `DJANGO_SECRET_KEY` | dev key | Change in production |
| `DJANGO_DEBUG` | `1` | Set to `0` in production |
| `EVOLET_MODEL_ID` | `Qwen/Qwen2.5-1.5B-Instruct` | HuggingFace model ID |
| `EVOLET_USE_4BIT` | `1` | 4-bit quantisation (saves ~1.5 GB VRAM) |
| `EVOLET_DOC_WORKERS` | `4` | Parallel document extraction threads |
| `EVOLET_MAX_WORKERS` | `4` | Parallel threads for other stages |
| `HF_TOKEN` | _(empty)_ | HuggingFace auth token for gated models |

---

## 10. Output Format

Each patient produces a JSON record stored in the database and downloadable from the UI:

```json
{
  "patient_code": "TMH_2023_001",
  "mentions": [
    {
      "category": "diagnosis",
      "value": "Renal Cell Carcinoma, Stage III",
      "date_text": "15.03.2023",
      "certainty": "confirmed",
      "evidence_quote": "Final Diagnosis: RCC Stage III",
      "source_pages": [2],
      "origin": "regex"
    },
    {
      "category": "medication",
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

## 11. Data Model

```
Patient  ──<  PDFDocument  ──<  PageLedger    (one row per PDF page)
                            └─<  NoteLedger    (one row per clinical note)

Mention        >──  Patient / PDFDocument / PipelineRun / NoteLedger
FinalRecord    >──  Patient                   (one merged JSON per patient)
PipelineRun    >──<  PDFDocument              (many-to-many)
ProcessingLog  >──  PipelineRun               (timestamped INFO / WARN / ERR entries)
```

---

## 12. Project Structure

```
tmh/
└── evolet/                          # repo root is the Django project
    ├── Dockerfile                   # backend container (CUDA 12.4 + cuDNN 9)
    ├── docker-compose.yml           # gpu + cpu profiles, 3 named volumes
    ├── docker-entrypoint.sh         # migrate → collectstatic → runserver
    ├── manage.py
    ├── requirements.txt
    │
    ├── evolet/                      # Django project config
    │   ├── settings.py              # env-var driven, SQLite, WhiteNoise, CORS
    │   ├── urls.py
    │   ├── jinja2.py                # Jinja2 environment for legacy templates
    │   └── wsgi.py
    │
    ├── pipeline/                    # core Django app
    │   ├── models.py                # Patient → PDF → Page → Note → Mention → FinalRecord
    │   ├── views.py                 # legacy server-rendered UI actions
    │   ├── api_views.py             # JSON endpoints for Next.js frontend
    │   ├── urls.py                  # all URL patterns incl. /api/v1/*
    │   ├── orchestrator.py          # phase coordinator (ThreadPoolExecutor, F() atomics)
    │   └── services/
    │       ├── config.py            # all tuneable knobs
    │       ├── pdf_extractor.py     # native + DocTR OCR + EasyOCR (thread-safe singletons)
    │       ├── text_cleaner.py      # OCR noise removal
    │       ├── text_corrector.py    # SymSpell correction
    │       ├── note_segmenter.py    # page → note splitting + triage
    │       ├── regex_extractor.py   # deterministic NER (15 categories)
    │       ├── llm_engine.py        # Qwen inference + adaptive batching
    │       ├── merger.py            # deduplication + final record assembly
    │       ├── schema_builder.py    # output schema construction
    │       ├── qc.py                # quality metrics + coverage scoring
    │       ├── json_utils.py        # LLM JSON repair (4-stage fallback)
    │       └── gpu_utils.py         # CUDA/CPU detect, VRAM, ETA estimation
    │
    ├── templates/                   # legacy Jinja2 server-rendered UI (fallback)
    │   ├── base.html
    │   ├── dashboard.html
    │   └── pipeline/
    │       ├── patient_detail.html  # 3D knowledge graph + JSON viewer
    │       ├── run_detail.html      # live log viewer + cancel button
    │       ├── components/          # reusable partials
    │       └── partials/            # HTMX response fragments
    │
    ├── static/
    │   ├── css/main.css             # aurora mesh background, dark mode, animations
    │   └── js/app.js                # Alpine stores: toasts, spotlight, etaPanel
    │
    └── frontend/                    # Next.js 15 app (primary UI)
        ├── Dockerfile               # frontend container
        ├── next.config.ts           # /api/v1/* rewrite → Django backend
        ├── package.json
        └── src/
            ├── app/
            │   ├── page.tsx                         # dashboard
            │   ├── patients/page.tsx                # patient list
            │   ├── patients/[id]/page.tsx           # patient detail
            │   ├── patients/[id]/knowledge-map/     # D3 knowledge graph
            │   ├── documents/page.tsx
            │   ├── runs/page.tsx
            │   ├── infra/page.tsx
            │   └── settings/page.tsx
            ├── components/
            │   ├── KnowledgeGraph.tsx               # D3 graph visualisation
            │   ├── Sidebar.tsx                      # navigation
            │   └── ThreeBackground.tsx              # Three.js background
            └── lib/
                ├── api.ts                           # Axios API client
                └── utils.ts
```

---

## 13. Performance & Hardware

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

### Compute tiers

| Tier | Hardware | ETA |
|---|---|---|
| `gpu_fast` | GPU >= 20 GB VRAM | ~20 s/PDF |
| `gpu_std` | GPU < 20 GB VRAM | ~40 s/PDF |
| `cpu` | CPU only | ~180 s/PDF |

---

## 14. Requirements

| Requirement | Minimum |
|---|---|
| Python | 3.10+ |
| Node.js | 18+ (frontend) |
| GPU VRAM | 6 GB (24 GB recommended) |
| CUDA | 12.4 (provided by container) |
| Disk — build | 15 GB free |
| Disk — runtime | ~3 GB model weights + PDF storage |
| RAM | 8 GB+ |

> CPU-only mode works via `docker compose --profile cpu up -d` but LLM phase takes ~3 min per note instead of ~5 s.

---

## License

Proprietary — 4BaseCare / TMH Project. All rights reserved.
