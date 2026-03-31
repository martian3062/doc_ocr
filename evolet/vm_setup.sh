#!/usr/bin/env bash
set -e
LOG=~/evolet/setup.log
exec > >(tee -a "$LOG") 2>&1

echo ""
echo "============================================"
echo "  Evolet VM Setup  $(date)"
echo "  Python 3.10 + CUDA 12.4 + L4 GPU"
echo "============================================"

cd ~/evolet

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
echo "--- Importing TMH patient reports..."
if [ -d ~/data/TMH_Patient_Reports ]; then
    python manage.py import_folder ~/data/TMH_Patient_Reports && echo "Import done"
else
    echo "Skipping: ~/data/TMH_Patient_Reports not found"
fi

echo ""
echo "============================================"
echo "  Setup complete!  $(date)"
echo "  Starting server on 0.0.0.0:9000..."
echo "============================================"

nohup python manage.py runserver 0.0.0.0:9000 >> ~/evolet/server.log 2>&1 &
echo $! > ~/evolet/server.pid
echo "Server PID: $(cat ~/evolet/server.pid)"
echo "Tail server logs:  tail -f ~/evolet/server.log"
echo "Setup log:         ~/evolet/setup.log"
