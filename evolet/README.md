# Evolet

Evolet is a medical PDF intelligence system that extracts structured oncology information from scanned and digital hospital documents, stores the results in Django, and serves them through a modern Next.js frontend.

It combines:

- OCR for scanned pages
- native PDF text extraction for digital documents
- regex-based entity extraction for speed and determinism
- LLM-based extraction for deeper clinical understanding
- a Django API and orchestration layer
- a Next.js dashboard and patient explorer UI

This README explains what is in the project, why each major piece exists, and how the full system works from upload to patient profile.

## 1. What This Project Does

The platform reads hospital PDFs and turns them into structured patient data.

Typical outputs include:

- patient identity and document grouping
- diagnoses and disease mentions
- medications and treatment references
- symptoms and follow-up notes
- timelines and evidence quotes
- merged patient summaries
- patient-level knowledge-map data for visualization

At a high level:

1. PDFs are imported into the system.
2. Text is extracted by a fast native path or OCR path.
3. Text is cleaned and split into smaller note-like sections.
4. Regex extractors capture deterministic entities.
5. An LLM fills in harder or less structured mentions.
6. Mentions are merged into patient-level records.
7. Django stores the data and exposes API endpoints.
8. Next.js renders dashboard, patients, runs, documents, infra, and map pages.

## 2. Current Architecture

The project is now a hybrid system:

- Backend: Django application in the repo root
- Frontend: Next.js application in `frontend/`
- Runtime: Docker Compose with separate backend and frontend containers
- Database: SQLite persisted via Docker volume
- Model cache: Hugging Face cache volume

### Runtime ports

- `3000`: Next.js frontend
- `9000`: Django backend API

### Request flow

```text
Browser
  -> Next.js frontend (port 3000)
  -> /api/v1/* rewrite
  -> Django backend (port 9000)
  -> SQLite / media / model cache
```

### Processing flow

```text
PDF
  -> PyMuPDF / OCR
  -> text cleaning
  -> note segmentation
  -> regex extraction
  -> LLM extraction
  -> merge + schema build
  -> DB persistence
  -> API response
  -> frontend visualization
```

## 3. Why These Technologies Were Used

This stack is built around a practical goal: get accurate structured data from messy clinical PDFs without making the system too slow or too expensive.

### Backend and orchestration

- `Django`
  Used because it gives a strong ORM, admin, routing, forms, management commands, and a stable server-side foundation for data-heavy applications.

- `SQLite`
  Used because it keeps deployment simple and portable. For a single-VM pipeline product, SQLite is fast enough and easy to back up. The project uses a persisted Docker volume so the DB survives container rebuilds.

- `Django ORM`
  Used to model patients, documents, mentions, runs, logs, and final records cleanly, while keeping the code readable and maintainable.

- `Management commands`
  Used so the pipeline can be run manually, from scripts, or inside Docker without building a separate job runner.

### PDF and OCR

- `PyMuPDF`
  Used for fast native text extraction from digital PDFs. This is much cheaper and faster than OCR when the PDF already contains selectable text.

- `python-doctr`
  Used for page OCR on scanned PDFs. It performs well on document-style OCR and works well with GPU acceleration.

- `EasyOCR`
  Used for text inside embedded images, stamps, screenshots, or page regions where native extraction is not enough.

- `Pillow` and `OpenCV`
  Used for image manipulation and preprocessing during OCR-related stages.

### Information extraction

- `Regex extraction`
  Used first because it is deterministic, fast, explainable, and cheap. It handles many structured clinical mentions without needing a GPU or model inference.

- `Qwen 2.5 Instruct via Transformers`
  Used for harder extraction cases where clinical language is unstructured and regex rules are not sufficient.

- `bitsandbytes`
  Used for 4-bit quantization so the LLM uses less VRAM and is more practical on a single GPU VM.

- `accelerate`
  Used to help model loading and inference runtime behavior.

- `json-repair` and `orjson`
  Used because LLM JSON can be messy. These utilities help recover malformed responses and serialize data efficiently.

- `rapidfuzz`
  Used in merge and normalization workflows where fuzzy matching is helpful.

- `symspellpy`
  Used as a fast spell-correction pass for OCR-noisy text.

### Frontend

- `Next.js`
  Used for a structured React app with file-based routing, production builds, and easy deployment in Docker.

- `React`
  Used to build interactive dashboards, patient pages, and rich client-side visualizations.

- `Axios`
  Used for API requests because it keeps the frontend API layer simple and explicit.

- `Framer Motion`
  Used for page transitions and motion polish across the UI.

- `D3`
  Used for the patient knowledge graph layout.

- `Three.js`, `@react-three/fiber`, `@react-three/drei`
  Used for immersive visual background and 3D-style interface effects.

- `Lucide React`
  Used for consistent iconography.

- `clsx` and `tailwind-merge`
  Used to manage dynamic class composition cleanly.

- `GSAP`, `Nivo`, `Recharts`, `react-dropzone`
  Installed to support rich animation, charts, and document upload interactions. Some are used lightly today, but they support the intended UI direction.

### Deployment

- `Docker`
  Used so the backend and frontend run in reproducible containers.

- `Docker Compose`
  Used to manage the multi-container setup and persistent volumes.

- `NVIDIA container runtime`
  Used so OCR and LLM inference can access the VM GPU.

## 4. Main Project Areas

### 4.1 Root backend app

The root of the repo contains the Django project and OCR pipeline.

- `manage.py`
  Django entry point for migrations, server start, and management commands.

- `Dockerfile`
  Builds the backend container.

- `docker-compose.yml`
  Defines the live multi-container deployment.

- `db.sqlite3`
  Local database file. In Docker production, the real database lives in the `evolet_db` volume and is mounted into `/app/data/db.sqlite3`.

- `requirements.txt`
  Python dependencies.

### 4.2 Django project config

Path: `evolet/settings.py`

This folder contains core Django configuration:

- `settings.py`
  App config, middleware, DB configuration, static/media setup, CORS, and env-based settings.

- `urls.py`
  Root URL routing.

- `jinja2.py`
  Jinja support for the older server-rendered UI.

- `wsgi.py` and `asgi.py`
  Standard Django deployment entry points.

### 4.3 Pipeline app

Path: `pipeline/`

This is the main business logic area.

- `models.py`
  Defines core entities such as patient, document, note, mention, final record, and pipeline run.

- `views.py`
  Older Django-rendered pages and actions.

- `api_views.py`
  JSON endpoints used by the Next.js frontend.

- `urls.py`
  Route definitions for dashboard, documents, patients, runs, QC, downloads, and `/api/v1/*`.

- `orchestrator.py`
  Coordinates the pipeline stages and run flow.

### Services folder

Path: `pipeline/services/`

This folder keeps each concern separate:

- `config.py`
  Central place for pipeline config values and environment-driven tuning.

- `pdf_extractor.py`
  Native text extraction, OCR selection, and image extraction.

- `text_cleaner.py`
  Removes OCR noise and repeated junk.

- `text_corrector.py`
  Corrects common OCR misspellings and noisy words.

- `note_segmenter.py`
  Breaks long extracted text into smaller note-like chunks for extraction.

- `regex_extractor.py`
  Fast deterministic clinical mention extraction.

- `llm_engine.py`
  Loads and runs the model for advanced extraction.

- `merger.py`
  Deduplicates and merges extracted mentions.

- `schema_builder.py`
  Builds the structured patient-level output format.

- `json_utils.py`
  Repairs and validates model output.

- `gpu_utils.py`
  Detects GPU/CPU info and exposes infra metrics.

- `photo_extractor.py`
  Pulls patient profile image data when possible from documents.

- `qc.py`
  Quality-control summaries and coverage scoring.

### 4.4 Frontend app

Path: `frontend/`

This is the user-facing app currently served on port `3000`.

- `next.config.ts`
  Rewrites `/api/v1/*` calls to the Django backend.

- `Dockerfile`
  Builds the production Next.js container.

- `package.json`
  Frontend dependencies and scripts.

- `src/app`
  App Router pages.

- `src/components`
  Shared UI and visualization components.

- `src/lib/api.ts`
  Frontend API client.

### Frontend routes

Path: `frontend/src/app/`

- `/`
  Dashboard/home page

- `/patients`
  Patient list

- `/patients/[id]`
  Patient detail profile

- `/patients/[id]/knowledge-map`
  Patient-specific knowledge graph page

- `/documents`
  Document list page

- `/runs`
  Pipeline runs page

- `/infra`
  Infrastructure/system information page

- `/settings`
  Settings placeholder page

### Key frontend components

- `frontend/src/components/KnowledgeGraph.tsx`
  D3-based patient graph visualization.

- `frontend/src/components/Sidebar.tsx`
  App navigation.

- `frontend/src/components/ThreeBackground.tsx`
  Visual background and atmosphere layer.

### 4.5 Templates and legacy UI

Path: `templates/`

The repo still contains the earlier Django/Jinja server-rendered UI. That part is useful for internal actions and as a fallback reference, but the main user-facing app is now the Next.js frontend.

## 5. Full Working Process

This is the most important section if you want to understand how the system behaves end to end.

### Step 1: Import PDFs

Documents enter the system through upload or import flows. Each PDF is stored as a `PDFDocument` linked to a `Patient` where possible.

Why this step exists:

- keeps original files available for traceability
- allows reruns without re-uploading
- gives each run a concrete source set

### Step 2: Extract text

The extraction layer chooses the best path per page:

- native text from `PyMuPDF` when the PDF is digital
- OCR from `DocTR` when the page is scanned
- image OCR from `EasyOCR` for embedded image text

Why this mixed strategy is used:

- native extraction is fastest and most accurate for digital PDFs
- OCR is necessary for scans
- embedded images often contain clinically important text that normal PDF extraction misses

### Step 3: Clean the text

The system removes:

- repeated OCR junk
- broken spacing
- duplicate headers/footers
- obvious garbage introduced by OCR

Why this matters:

- raw OCR text is noisy
- regex extraction gets much better after cleanup
- LLM prompts become shorter and more reliable

### Step 4: Correct obvious OCR mistakes

SymSpell-based correction is applied where helpful.

Why this matters:

- medical text often has OCR distortions
- small spelling repairs improve downstream extraction accuracy

### Step 5: Split into note-sized chunks

Large page text is segmented into smaller note-like units.

Why this matters:

- regex works better on smaller sections
- LLM prompts stay manageable
- traceability back to source sections becomes easier

### Step 6: Regex extraction

The system first runs rule-based extraction to detect mentions such as:

- diagnosis
- medication
- symptom
- procedure
- pathology
- follow-up
- plan

Why regex is used first:

- fast
- cheap
- deterministic
- explainable
- reduces model workload

### Step 7: LLM extraction

The LLM handles harder sections that are not well covered by rules.

Typical jobs:

- interpreting semi-structured summaries
- extracting mentions from free-form text
- producing richer structured outputs

Why an LLM is used:

- hospital notes are inconsistent
- clinical statements often do not follow strict templates
- rules alone miss too many relationships and fields

### Step 8: Merge and normalize

Regex and LLM mentions are merged into patient-level structured data.

This stage handles:

- deduplication
- grouping
- value normalization
- final output schema assembly

Why this matters:

- one patient may have many PDFs and many overlapping mentions
- the frontend needs one coherent patient record, not hundreds of near-duplicates

### Step 9: Persist to database

The merged result is stored in Django models, including final record JSON and evidence-linked mention rows.

Why this matters:

- data becomes queryable
- dashboard stats are fast
- patient detail pages can be generated quickly
- reruns and audits become possible

### Step 10: Expose APIs

The backend exposes REST-like endpoints used by the frontend, including:

- `/api/v1/dashboard`
- `/api/v1/documents`
- `/api/v1/patients`
- `/api/v1/patients/<id>`
- `/api/v1/patients/<id>/knowledge-map`
- `/api/v1/runs`
- `/api/v1/runs/<id>`

Both slash and no-slash versions are supported to avoid frontend routing issues.

### Step 11: Render in Next.js

The frontend fetches backend data and renders:

- dashboard metrics
- patient lists
- patient summaries
- document inventory
- runs and status
- infrastructure details
- graph-based knowledge views

Why the frontend is separate:

- cleaner UI iteration
- better component reuse
- easier visual polish and rich interactions
- clearer separation from backend extraction logic

## 6. What the Knowledge Map Is Doing

The current knowledge map is a patient-level mention visualization.

It is based on:

- extracted `Mention` rows
- grouped mention categories
- patient-specific API output

Current behavior:

- creates category nodes
- creates mention nodes
- connects each mention to its category

This means the graph is currently a structured evidence map, not a full clinical reasoning graph.

It does not yet infer relationships like:

- drug treats diagnosis
- symptom caused by condition
- progression from one date to another

That can be added later through relation extraction or graph modeling.

## 7. Current API and UI Contract

The frontend talks to the backend through same-origin `/api/v1/*` calls.

That works because `frontend/next.config.ts` rewrites them to the backend container.

This design was used to avoid:

- browser CORS issues
- direct public exposure of backend URLs in client code
- environment mismatch between local/dev/VM setups

## 8. Data Model Summary

The main backend entities are:

- `Patient`
  One logical patient profile.

- `PDFDocument`
  One uploaded/imported PDF linked to a patient.

- `PageLedger`
  Per-page extracted data.

- `NoteLedger`
  Smaller text chunks/notes derived from pages.

- `Mention`
  Structured extracted evidence item with label, value, category, confidence, and source.

- `FinalRecord`
  Final merged patient summary JSON.

- `PipelineRun`
  One processing run over one or more documents.

- `ProcessingLog`
  Timestamped run log entries.

## 9. Detailed Folder Structure

```text
evolet/
├── Dockerfile
├── docker-compose.yml
├── docker-entrypoint.sh
├── manage.py
├── requirements.txt
├── README.md
├── db.sqlite3
├── docs/
├── data/
├── media/
├── static/
├── templates/
├── evolet/
│   ├── __init__.py
│   ├── asgi.py
│   ├── jinja2.py
│   ├── settings.py
│   ├── urls.py
│   └── wsgi.py
├── pipeline/
│   ├── __init__.py
│   ├── admin.py
│   ├── api_views.py
│   ├── apps.py
│   ├── forms.py
│   ├── models.py
│   ├── orchestrator.py
│   ├── urls.py
│   ├── views.py
│   ├── management/
│   │   └── commands/
│   │       ├── import_folder.py
│   │       ├── import_json_records.py
│   │       └── run_pipeline.py
│   └── services/
│       ├── config.py
│       ├── gpu_utils.py
│       ├── json_utils.py
│       ├── llm_engine.py
│       ├── merger.py
│       ├── note_segmenter.py
│       ├── pdf_extractor.py
│       ├── photo_extractor.py
│       ├── qc.py
│       ├── regex_extractor.py
│       ├── schema_builder.py
│       ├── text_cleaner.py
│       └── text_corrector.py
└── frontend/
    ├── Dockerfile
    ├── next.config.ts
    ├── package.json
    ├── public/
    └── src/
        ├── app/
        │   ├── documents/page.tsx
        │   ├── infra/page.tsx
        │   ├── map/page.tsx
        │   ├── patients/page.tsx
        │   ├── patients/[id]/page.tsx
        │   ├── patients/[id]/knowledge-map/page.tsx
        │   ├── runs/page.tsx
        │   ├── settings/page.tsx
        │   ├── globals.css
        │   ├── layout.tsx
        │   └── page.tsx
        ├── components/
        │   ├── KnowledgeGraph.tsx
        │   ├── Sidebar.tsx
        │   └── ThreeBackground.tsx
        └── lib/
            ├── api.ts
            └── utils.ts
```

## 10. Docker and Deployment

The main production deployment uses Docker Compose.

### Containers

- `evolet_backend`
  Django backend and OCR/LLM runtime

- `evolet_frontend`
  Next.js frontend

### Named volumes

- `evolet_media`
  Stores uploaded/generated media

- `evolet_db`
  Stores the persisted SQLite database

- `hf_cache`
  Stores downloaded model weights and caches

### Important deployment decision

The backend now reads the database path from `EVOLET_DB_PATH`.

This was added so Docker uses the persisted DB volume path:

```text
/app/data/db.sqlite3
```

instead of accidentally using an internal throwaway container file.

### Frontend API strategy

The frontend uses:

- `BACKEND_INTERNAL_URL=http://backend:9000`

inside Docker, and rewrites `/api/v1/*` to that internal backend URL.

This keeps:

- browser requests simple
- Docker networking internal
- client code environment-agnostic

## 11. Local Development

### Backend

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver 0.0.0.0:9000
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Then open:

- frontend: `http://localhost:3000`
- backend API: `http://localhost:9000`

## 12. Docker Run

From the repo root:

```bash
docker compose build
docker compose up -d
```

Useful commands:

```bash
docker compose logs -f frontend
docker compose logs -f backend
docker compose ps
docker compose restart frontend
docker compose restart backend
```

## 13. Common Commands

### Run the pipeline

```bash
python manage.py run_pipeline
```

### Import a folder of PDFs

```bash
python manage.py import_folder "C:\path\to\pdfs"
```

### Import JSON records

```bash
python manage.py import_json_records "C:\path\to\records"
```

### Django admin/dev commands

```bash
python manage.py makemigrations
python manage.py migrate
python manage.py collectstatic --noinput
```

## 14. Troubleshooting

### Patient page loads but crashes in browser

Likely causes:

- stale frontend bundle
- frontend/backend route mismatch
- API returning a schema shape the page does not expect

What helped in this project:

- support both slash and no-slash API routes
- use same-origin frontend API rewrites
- force a clean frontend rebuild with `--no-cache`

### `/settings` or another route returns 404 in frontend

Likely cause:

- stale Next.js image was still being served

Fix:

```bash
docker rm -f evolet_frontend
docker rmi evolet-frontend:latest
docker compose build --no-cache frontend
docker compose up -d frontend
```

### Backend shows empty or wrong data

Likely cause:

- Django is reading the wrong SQLite path

Fix:

- confirm `EVOLET_DB_PATH=/app/data/db.sqlite3`
- confirm the volume-mounted DB contains the real data

### Docker disk space issues

Useful cleanup:

```bash
docker container prune -f
docker image prune -f
docker builder prune -af
```

## 15. What Is Live Today

The app currently has working routes for:

- dashboard
- patients
- patient detail
- patient knowledge map
- documents
- runs
- infra
- settings

The current patient profile rendering is designed to handle deeper `final_record.grouped` structures, not just simple arrays. That matters because some patient records use a nested schema version and would otherwise crash the UI.

## 16. What Can Be Improved Next

Strong next improvements would be:

- move from SQLite to Postgres if multi-user write load grows
- add formal relation extraction for the knowledge map
- add auth and role-based access control
- move long pipeline jobs to a dedicated queue worker
- add automated smoke tests for critical frontend routes
- add health checks and monitoring for the VM deployment

## 17. In One Sentence

Evolet is a hybrid OCR + clinical extraction platform where Django handles ingestion, extraction, storage, and APIs, while Next.js handles the user experience and visualization layer on top of those structured patient records.
