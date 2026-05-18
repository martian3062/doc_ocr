#!/usr/bin/env bash
set -e
APP_DIR=${APP_DIR:-~/doc-reader}
LOG="$APP_DIR/setup.log"
exec > >(tee -a "$LOG") 2>&1

echo ""
echo "============================================"
echo "  doc-reader VM Setup  $(date)"
echo "  Python 3.10 + CUDA 12.4 + L4 GPU"
echo "============================================"

cd "$APP_DIR"

# Use pyenv Python 3.10 for ML lib compatibility
PY=~/.pyenv/versions/3.10.20/bin/python3

echo "--- Creating venv with Python 3.10..."
$PY -m venv venv
source venv/bin/activate

echo "--- Upgrading pip..."
pip install --upgrade pip --quiet

echo "--- Installing PyTorch with CUDA 12.4..."
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu124 --quiet

echo "--- Verifying CUDA in torch..."
python -c "
import torch
print('torch', torch.__version__)
print('CUDA available:', torch.cuda.is_available())
if torch.cuda.is_available():
    print('GPU:', torch.cuda.get_device_name(0))
    print('VRAM:', round(torch.cuda.get_device_properties(0).total_memory / 1024**3, 1), 'GB')
"

echo "--- Installing project requirements (excl torch)..."
pip install -r requirements.txt --quiet

echo "--- Running migrations..."
python manage.py migrate

echo "--- Collecting static files..."
python manage.py collectstatic --noinput 2>/dev/null || true

echo ""
echo "--- Importing sample patient reports if available..."
if [ -d ~/data/doc-reader-documents ]; then
    python manage.py import_folder ~/data/doc-reader-documents && echo "Import done"
elif [ -d ~/data/TMH_Patient_Reports ]; then
    python manage.py import_folder ~/data/TMH_Patient_Reports && echo "Import done"
else
    echo "Skipping: no document folder found"
fi

echo ""
echo "============================================"
echo "  Setup complete!  $(date)"
echo "  Starting server on 0.0.0.0:9000..."
echo "============================================"

nohup python manage.py runserver 0.0.0.0:9000 >> "$APP_DIR/server.log" 2>&1 &
echo $! > "$APP_DIR/server.pid"
echo "Server PID: $(cat "$APP_DIR/server.pid")"
echo "Tail server logs:  tail -f $APP_DIR/server.log"
echo "Setup log:         $APP_DIR/setup.log"
