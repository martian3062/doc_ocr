"""WSGI config for the doc-ocr project."""
import os
from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "doc_reader.settings")
application = get_wsgi_application()
