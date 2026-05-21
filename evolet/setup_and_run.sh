#!/usr/bin/env bash
# doc-ocr - Setup & Run (Linux/VM)
set -e

echo "============================================"
echo "  doc-ocr - Setup & Run"
echo "============================================"
echo ""

# Create venv if needed
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

# Activate
source venv/bin/activate

# Install deps
echo "Installing dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

# Migrations
echo "Running database migrations..."
python manage.py migrate

# Collect static
echo "Collecting static files..."
python manage.py collectstatic --noinput 2>/dev/null || true

# Import PDFs if folder exists
if [ -d "data/doc-ocr-documents" ]; then
    echo "Importing PDFs from data/doc-ocr-documents..."
    python manage.py import_folder "data/doc-ocr-documents"
fi

# Also check common VM paths
if [ -d "/home/pardeep/data/doc-ocr-documents" ]; then
    echo "Importing PDFs from /home/pardeep/data/doc-ocr-documents..."
    python manage.py import_folder "/home/pardeep/data/doc-ocr-documents"
fi

echo ""
echo "============================================"
echo "  Setup complete! Starting server..."
echo "  Open http://0.0.0.0:9000 in your browser"
echo "============================================"
echo ""

python manage.py runserver 0.0.0.0:9000
