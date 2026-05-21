#!/usr/bin/env bash
set -e

echo "==> doc-ocr starting..."
echo "    Python: $(python --version)"
echo "    Runtime: Django-only CPU/cloud-safe"

if [ "${RUN_MIGRATIONS:-1}" = "1" ]; then
    echo "==> Running database migrations..."
    python manage.py migrate --noinput
else
    echo "==> Skipping database migrations in this process"
fi

if [ "${COLLECT_STATIC:-1}" = "1" ]; then
    echo "==> Collecting static files..."
    python manage.py collectstatic --noinput --clear -v 0
fi

if [ "$#" -gt 0 ]; then
    echo "==> Running command: $*"
    exec "$@"
fi

PORT="${PORT:-9000}"
echo "==> Starting Django server on 0.0.0.0:${PORT}..."
exec python manage.py runserver "0.0.0.0:${PORT}"
