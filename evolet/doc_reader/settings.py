"""
doc-ocr — Django settings
"""
import importlib.util
import os
from pathlib import Path

try:
    import dj_database_url
except ImportError:
    dj_database_url = None

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    "doc-ocr-dev-key-change-in-production-8f3k2j5m9x",
)

DEBUG = os.environ.get("DJANGO_DEBUG", "1").strip() == "1"

ALLOWED_HOSTS = ["*"]

# ── Applications ──
AVAILABLE_OPTIONAL_APPS = {
    optional_app
    for optional_app in ("corsheaders", "storages", "django_rq")
    if importlib.util.find_spec(optional_app)
}

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    # project
    "pipeline",
] + [app for app in ("corsheaders", "storages", "django_rq") if app in AVAILABLE_OPTIONAL_APPS]

# ── Middleware ──
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

if "corsheaders" in AVAILABLE_OPTIONAL_APPS:
    MIDDLEWARE.insert(2, "corsheaders.middleware.CorsMiddleware")

CORS_ALLOW_ALL_ORIGINS = True

ROOT_URLCONF = "doc_reader.urls"

# ── Templates (Jinja2 + Django) ──
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.jinja2.Jinja2",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": False,
        "OPTIONS": {
            "environment": "doc_reader.jinja2.environment",
            "context_processors": [
                "django.template.context_processors.request",
                "django.template.context_processors.csrf",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "doc_reader.wsgi.application"

# ── Database (SQLite3) ──
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
if DATABASE_URL and dj_database_url is not None:
    DATABASES = {
        "default": dj_database_url.parse(
            DATABASE_URL,
            conn_max_age=600,
            ssl_require=False,
        )
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": os.environ.get(
                "DOC_READER_DB_PATH",
                os.environ.get("EVOLET_DB_PATH", str(BASE_DIR / "db.sqlite3")),
            ),
            "OPTIONS": {
                "timeout": 30,
            },
        }
    }

# ── Static & Media ──
STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
USE_S3_STORAGE = os.environ.get(
    "DOC_READER_USE_S3_STORAGE",
    os.environ.get("EVOLET_USE_S3_STORAGE", "0"),
).strip() == "1"
if USE_S3_STORAGE:
    AWS_STORAGE_BUCKET_NAME = os.environ.get("AWS_STORAGE_BUCKET_NAME", "doc-ocr-media")
    AWS_S3_REGION_NAME = os.environ.get("AWS_S3_REGION_NAME", "us-east-1")
    AWS_S3_ENDPOINT_URL = os.environ.get("AWS_S3_ENDPOINT_URL", "")
    AWS_ACCESS_KEY_ID = os.environ.get("AWS_ACCESS_KEY_ID", "")
    AWS_SECRET_ACCESS_KEY = os.environ.get("AWS_SECRET_ACCESS_KEY", "")
    AWS_S3_ADDRESSING_STYLE = "path"
    AWS_DEFAULT_ACL = None
    AWS_QUERYSTRING_AUTH = False
    STORAGES = {
        "default": {
            "BACKEND": "storages.backends.s3.S3Storage",
            "OPTIONS": {
                "bucket_name": AWS_STORAGE_BUCKET_NAME,
                "region_name": AWS_S3_REGION_NAME,
                "endpoint_url": AWS_S3_ENDPOINT_URL or None,
                "access_key": AWS_ACCESS_KEY_ID,
                "secret_key": AWS_SECRET_ACCESS_KEY,
            },
        },
        "staticfiles": {
            "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
        },
    }
else:
    STORAGES = {
        "default": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
        },
        "staticfiles": {
            "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
        },
    }

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

# ── File uploads ──
FILE_UPLOAD_MAX_MEMORY_SIZE = 100 * 1024 * 1024  # 100 MB
DATA_UPLOAD_MAX_MEMORY_SIZE = 100 * 1024 * 1024

# ── Defaults ──
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ── doc-ocr Pipeline Config ──
_data_dir_env = os.environ.get("DOC_READER_DATA_DIR", "").strip()
if _data_dir_env:
    DOC_READER_DATA_DIR = Path(_data_dir_env)
elif Path("/data").exists():
    DOC_READER_DATA_DIR = Path("/data")
else:
    DOC_READER_DATA_DIR = BASE_DIR / "data" / os.environ.get("DOC_READER_DATA_SUBDIR", "documents")
DOC_READER_MODEL_ID = os.environ.get(
    "DOC_READER_MODEL_ID",
    os.environ.get("EVOLET_MODEL_ID", "Qwen/Qwen2.5-1.5B-Instruct"),
)
DOC_READER_USE_4BIT = os.environ.get(
    "DOC_READER_USE_4BIT",
    os.environ.get("EVOLET_USE_4BIT", "1"),
).strip() == "1"
DOC_READER_MAX_WORKERS = int(os.environ.get("DOC_READER_MAX_WORKERS", os.environ.get("EVOLET_MAX_WORKERS", "4")))
DOC_READER_QUEUE_MODE = os.environ.get("DOC_READER_QUEUE_MODE", os.environ.get("EVOLET_QUEUE_MODE", "rq"))
DOC_READER_TROCR_MODEL_ID = os.environ.get(
    "DOC_READER_TROCR_MODEL_ID",
    os.environ.get("EVOLET_TROCR_MODEL_ID", "microsoft/trocr-large-handwritten"),
)
DOC_READER_GOT_OCR_MODEL_ID = os.environ.get(
    "DOC_READER_GOT_OCR_MODEL_ID",
    os.environ.get("EVOLET_GOT_OCR_MODEL_ID", "stepfun-ai/GOT-OCR-2.0-hf"),
)
DOC_READER_MEDICAL_HANDWRITING_MODEL_ID = os.environ.get(
    "DOC_READER_MEDICAL_HANDWRITING_MODEL_ID",
    os.environ.get("EVOLET_MEDICAL_HANDWRITING_MODEL_ID", ""),
)
DOC_READER_MEDOCR_VISION_DATASET_ID = os.environ.get(
    "DOC_READER_MEDOCR_VISION_DATASET_ID",
    os.environ.get("EVOLET_MEDOCR_VISION_DATASET_ID", "naazimsnh02/medocr-vision-dataset"),
)
DOC_READER_ENABLE_ADVANCED_PARSERS = os.environ.get(
    "DOC_READER_ENABLE_ADVANCED_PARSERS",
    os.environ.get("EVOLET_ENABLE_ADVANCED_PARSERS", "1"),
).strip() == "1"
DOC_READER_PARSER_BACKENDS = os.environ.get(
    "DOC_READER_PARSER_BACKENDS",
    os.environ.get("EVOLET_PARSER_BACKENDS", "docling,surya,paddle_structure"),
)

# Backward-compatible aliases used by existing pipeline imports.
EVOLET_DATA_DIR = DOC_READER_DATA_DIR
EVOLET_MODEL_ID = DOC_READER_MODEL_ID
EVOLET_USE_4BIT = DOC_READER_USE_4BIT
EVOLET_MAX_WORKERS = DOC_READER_MAX_WORKERS
EVOLET_QUEUE_MODE = DOC_READER_QUEUE_MODE
EVOLET_TROCR_MODEL_ID = DOC_READER_TROCR_MODEL_ID
EVOLET_GOT_OCR_MODEL_ID = DOC_READER_GOT_OCR_MODEL_ID
EVOLET_MEDICAL_HANDWRITING_MODEL_ID = DOC_READER_MEDICAL_HANDWRITING_MODEL_ID
EVOLET_MEDOCR_VISION_DATASET_ID = DOC_READER_MEDOCR_VISION_DATASET_ID
EVOLET_ENABLE_ADVANCED_PARSERS = DOC_READER_ENABLE_ADVANCED_PARSERS
EVOLET_PARSER_BACKENDS = DOC_READER_PARSER_BACKENDS

RQ_QUEUES = {
    "default": {
        "HOST": os.environ.get("REDIS_HOST", "127.0.0.1"),
        "PORT": int(os.environ.get("REDIS_PORT", "6379")),
        "DB": int(os.environ.get("REDIS_DB", "0")),
        "DEFAULT_TIMEOUT": 60 * 60 * 8,
    }
}

# ── Logging ──
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
        "file": {
            "class": "logging.FileHandler",
            "filename": BASE_DIR / "doc_reader_pipeline.log",
            "formatter": "verbose",
        },
    },
    "loggers": {
        "pipeline": {
            "handlers": ["console", "file"],
            "level": "INFO",
            "propagate": False,
        },
        "django": {
            "handlers": ["console"],
            "level": "WARNING",
        },
    },
}
