# Evolet — Medical Report OCR Pipeline

> Extracts structured clinical data from scanned/digital PDF reports using a
> **lossless-first → regex → LLM** approach. Built with Django + HTMX for a
> clean web interface and Qwen 2.5 1.5B for LLM-powered extraction.

---

## How It Works (in plain English)

Each PDF goes through these stages automatically:

```
PDF
 │
 ├─ 1. Extract text  →  Try native PDF text first.
 │                       Weak/scanned pages? Fall back to DocTR OCR.
 │
 ├─ 2. Clean text    →  Remove OCR noise, headers, duplicate lines.
 │
 ├─ 3. Segment       →  Split each page into individual clinical notes.
 │
 ├─ 4. Triage        →  Regex covered it well?  → Mark resolved, skip LLM.
 │                       Still missing data?     → Queue for LLM.
 │
 ├─ 5. LLM           →  Qwen 2.5 1.5B (4-bit, GPU) extracts remaining mentions.
 │                       Model loaded once for all PDFs, then freed.
 │
 ├─ 6. Merge         →  Deduplicate regex + LLM mentions per patient.
 │
 └─ 7. Output        →  Structured JSON per patient stored in DB + downloadable.
```

**Why this design?**
Regex is fast and free — it covers 60–80% of mentions with zero GPU cost.
The LLM only handles what regex misses, keeping runtime and VRAM usage low.

---

## Quick Start

### Option A — Linux/VM (GPU server)

```bash
# 1. Copy project to VM
scp -i ~/.ssh/YOUR_KEY.rsa -r evolet/ USER@YOUR_VM_IP:~/evolet/

# 2. SSH in and run setup script
ssh -i ~/.ssh/YOUR_KEY.rsa USER@YOUR_VM_IP
cd ~/evolet
chmod +x setup_and_run.sh
./setup_and_run.sh

# 3. SSH tunnel so your browser can reach it
ssh -L 9000:localhost:9000 -i ~/.ssh/YOUR_KEY.rsa USER@YOUR_VM_IP
```

Then open **http://localhost:9000** in your browser.

### Option B — Manual setup (any OS)

```bash
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate

pip install -r requirements.txt
python manage.py migrate
python manage.py collectstatic --noinput

# Optional: pre-load a folder of PDFs
python manage.py import_folder /path/to/TMH_Patient_Reports

python manage.py runserver 0.0.0.0:9000
```

### Option C — Windows one-click

```
setup_and_run.bat
```

---

## Web UI — What You Can Do

| Section | What it does |
|---|---|
| **Dashboard** | See total patients, PDFs, mentions, and GPU status at a glance |
| **Upload** | Drag-and-drop PDFs, JSON exports, or text files |
| **Folder Browser** | Point to a server directory and bulk-import all PDFs |
| **Patients** | Search, browse, and download per-patient JSON records |
| **Runs** | Start a pipeline run, watch live progress, view logs |
| **QC Summary** | See which patients have missing diagnosis/medications/etc. |

---

## CLI Commands

```bash
# Import all PDFs from a folder into the database
python manage.py import_folder /path/to/pdfs

# Run the full pipeline on all imported PDFs
python manage.py run_pipeline

# Quick test — run on first N PDFs only
python manage.py run_pipeline --limit 5

# Run on a specific patient
python manage.py run_pipeline --patient "TMH_2023_001"
```

---

## Output Format

Each patient produces a JSON record like this:

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
    "diagnosis":   [ ... ],
    "medication":  [ ... ],
    "imaging":     [ ... ],
    "follow_up":   [ ... ]
  },
  "stats": {
    "page_count": 12,
    "note_count": 8,
    "mentions_after_merge": 14
  }
}
```

**Extracted categories:** `diagnosis` · `medication` · `imaging` · `pathology` ·
`symptom` · `lab` · `genomics` · `surgery` · `radiotherapy` · `plan` ·
`follow_up` · `performance_status` · `procedure` · `status` · `other`

---

## Configuration

Set these environment variables to override defaults (no code changes needed):

| Variable | Default | What it controls |
|---|---|---|
| `EVOLET_MODEL_ID` | `Qwen/Qwen2.5-1.5B-Instruct` | LLM model to load |
| `EVOLET_USE_4BIT` | `1` | 4-bit quantisation (`1` = on, saves ~1.5 GB VRAM) |
| `EVOLET_MAX_WORKERS` | `4` | Worker threads for IO-bound stages |
| `EVOLET_LOCAL_ONLY` | `0` | `1` = use cached model weights only (offline/air-gapped) |
| `HF_TOKEN` | _(none)_ | HuggingFace token (needed for gated models) |
| `DJANGO_DEBUG` | `1` | Django debug mode (`0` for production) |

---

## Project Structure

```
evolet/
├── manage.py
├── requirements.txt
├── setup_and_run.sh / .bat
│
├── evolet/                      # Django project config
│   ├── settings.py
│   ├── urls.py
│   └── jinja2.py                # Jinja2 + HTMX/Alpine.js environment
│
├── pipeline/                    # Main app
│   ├── models.py                # 8 DB models (Patient → PDF → Page → Note → Mention)
│   ├── views.py                 # HTMX views
│   ├── orchestrator.py          # Runs all 5 pipeline phases
│   ├── forms.py
│   │
│   └── services/                # Core logic (one file per concern)
│       ├── config.py            # All tuneable knobs (env-var overridable)
│       ├── pdf_extractor.py     # Native text + DocTR OCR fallback
│       ├── text_cleaner.py      # OCR artifact removal
│       ├── note_segmenter.py    # Page → note splitting + triage
│       ├── regex_extractor.py   # Deterministic mention extraction
│       ├── llm_engine.py        # Qwen inference + adaptive batching
│       ├── merger.py            # Deduplication + final record assembly
│       ├── qc.py                # Quality metrics
│       ├── photo_extractor.py   # Patient photo from PDF page 1
│       ├── json_utils.py        # Robust LLM JSON parser (4-stage fallback)
│       └── gpu_utils.py         # CUDA setup + memory management
│
├── templates/                   # Jinja2 + HTMX + Alpine.js templates
├── static/                      # CSS, JS
└── media/                       # Uploaded files + extracted photos
```

---

## Requirements

| Requirement | Minimum |
|---|---|
| Python | 3.10+ |
| GPU VRAM | 6 GB (16 GB recommended for full batch runs) |
| CUDA | 11.8+ |
| Disk space | ~3 GB (model weights) + PDF storage |

> **CPU-only mode** works but is very slow — the LLM phase takes ~5 min per note instead of ~3 s.

---

## Database Models (quick reference)

```
Patient  ──<  PDFDocument  ──<  PageLedger   (one row per PDF page)
                            └─<  NoteLedger   (one row per clinical note)

Mention  >──  Patient / PDFDocument / PipelineRun / NoteLedger
FinalRecord  >──  Patient   (one merged record per patient)
PipelineRun  >──<  PDFDocument  (many-to-many)
ProcessingLog  >──  PipelineRun  (audit trail)
```

---

## License

Proprietary — 4BaseCare / TMH Project
