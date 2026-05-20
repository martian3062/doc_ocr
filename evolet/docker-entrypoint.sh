#!/usr/bin/env bash
set -e

echo "==> doc-reader starting..."
echo "    Python: $(python --version)"
echo "    Runtime: Django-only CPU/cloud-safe"

echo "==> Running database migrations..."
python manage.py migrate --noinput

echo "==> Collecting static files..."
python manage.py collectstatic --noinput --clear -v 0

if [ "$#" -gt 0 ]; then
    echo "==> Running command: $*"
    exec "$@"
fi

PORT="${PORT:-9000}"
echo "==> Starting Django server on 0.0.0.0:${PORT}..."
exec python manage.py runserver "0.0.0.0:${PORT}"
