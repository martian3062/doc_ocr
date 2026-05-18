#!/usr/bin/env bash
set -e

echo "==> doc-reader starting..."
echo "    Python: $(python --version)"
echo "    GPU:    $(python -c 'import torch; print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else \"CPU only\")' 2>/dev/null || echo 'torch not ready')"

echo "==> Running database migrations..."
python manage.py migrate --noinput

echo "==> Collecting static files..."
python manage.py collectstatic --noinput --clear -v 0

if [ "$#" -gt 0 ]; then
    echo "==> Running command: $*"
    exec "$@"
fi

echo "==> Starting server on 0.0.0.0:9000..."
exec python manage.py runserver 0.0.0.0:9000
