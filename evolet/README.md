# Evolet TMH OCR Project - Django Full-Stack

## 🚀 Quick Start

### On your VM (SSH to L4 GPU):

```bash
ssh -i C:\acer\4basecare\evolet_rsa pardeep@34.31.236.150
```

### Setup & Run:

```bash
# Upload the project (from Windows)
scp -i C:\acer\4basecare\evolet_rsa -r evolet/ pardeep@34.31.236.150:~/evolet/

# On the VM
cd ~/evolet
chmod +x setup_and_run.sh
./setup_and_run.sh
```

### Or step by step:

```bash
cd ~/evolet
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py import_folder /home/pardeep/data/TMH_Patient_Reports
python manage.py runserver 0.0.0.0:8000
```

### Access from local browser:

```bash
# Set up SSH tunnel from your Windows machine
ssh -L 8000:localhost:8000 -i C:\acer\4basecare\evolet_rsa pardeep@34.31.236.150
```

Then open: http://localhost:8000

## 📁 Project Structure

```
evolet/
├── manage.py                    # Django entry point
├── requirements.txt             # Python dependencies
├── setup_and_run.sh             # Linux setup script
├── setup_and_run.bat            # Windows setup script
├── evolet/                      # Django settings
│   ├── settings.py
│   ├── urls.py
│   ├── jinja2.py
│   ├── wsgi.py
│   └── asgi.py
├── pipeline/                    # Main app
│   ├── models.py                # DB models (Patient, PDF, Mention, etc.)
│   ├── views.py                 # HTMX-powered views
│   ├── urls.py                  # URL routing
│   ├── admin.py                 # Django admin
│   ├── forms.py                 # Upload forms
│   ├── orchestrator.py          # Full pipeline orchestrator
│   ├── services/                # Core pipeline services
│   │   ├── config.py            # Runtime configuration
│   │   ├── pdf_extractor.py     # Native + OCR extraction
│   │   ├── note_segmenter.py    # Note splitting + triage
│   │   ├── regex_extractor.py   # Deterministic extraction
│   │   ├── llm_engine.py        # Qwen model inference
│   │   ├── merger.py            # Mention merge
│   │   ├── qc.py                # Quality control
│   │   ├── photo_extractor.py   # Profile image extraction
│   │   ├── json_utils.py        # JSON parsing helpers
│   │   └── gpu_utils.py         # GPU management
│   └── management/commands/
│       ├── import_folder.py     # Import PDFs from folder
│       └── run_pipeline.py      # Run pipeline from CLI
├── templates/                   # Jinja2 templates (HTMX + Alpine.js)
├── static/                      # Static files
├── media/                       # Uploaded files + outputs
└── data/TMH_Patient_Reports/    # Place PDFs here
```

## 🧪 CLI Usage

```bash
# Import PDFs
python manage.py import_folder /path/to/pdfs

# Run pipeline on all PDFs
python manage.py run_pipeline

# Run on first 10 PDFs
python manage.py run_pipeline --limit 10

# Run on specific patient
python manage.py run_pipeline --patient "John"
```

## 🔧 Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `EVOLET_MODEL_ID` | `Qwen/Qwen2.5-1.5B-Instruct` | HuggingFace model |
| `EVOLET_USE_4BIT` | `1` | Enable 4-bit quantization |
| `EVOLET_MAX_WORKERS` | `4` | Parallel processing workers |
| `HF_TOKEN` | _(none)_ | HuggingFace API token |
| `DJANGO_DEBUG` | `1` | Django debug mode |

## 🧠 Pipeline Architecture

1. **PDF Extraction** → Native text + OCR fallback (doctr)
2. **Note Segmentation** → Split pages into clinical notes
3. **Triage** → Regex handles easy notes, LLM gets hard ones
4. **LLM Rescue** → Qwen 2.5 1.5B with 4-bit quantization
5. **Merge** → Deduplicate and merge mentions per patient
6. **QC** → Quality flags and review metrics
