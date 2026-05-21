"""ASGI config for the doc-ocr project."""
import os
from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "doc_reader.settings")
application = get_asgi_application()
