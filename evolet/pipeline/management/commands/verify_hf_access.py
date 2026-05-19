"""Verify Hugging Face model access for doc-reader."""

from django.core.management.base import BaseCommand, CommandError

from pipeline.services import config


class Command(BaseCommand):
    help = "Verify Hugging Face access for configured OCR/VLM models."

    def add_arguments(self, parser):
        parser.add_argument(
            "--model",
            action="append",
            dest="models",
            help="Model ID to verify. Can be passed multiple times.",
        )

    def handle(self, *args, **options):
        models = options["models"] or [
            config.TROCR_MODEL_ID,
            config.MEDICAL_HANDWRITING_MODEL_ID,
            config.GOT_OCR_MODEL_ID,
            config.VALIDATION_MODEL_ID,
        ]
        models = [model for model in models if model]

        try:
            from huggingface_hub import HfApi
        except Exception:
            HfApi = None

        api = HfApi(token=config.HF_TOKEN) if HfApi else None
        failed = []
        for model_id in models:
            try:
                if api is not None:
                    info = api.model_info(model_id)
                    resolved_id = info.modelId
                else:
                    resolved_id = _verify_with_http(model_id)
                self.stdout.write(self.style.SUCCESS(f"{resolved_id}: ok"))
            except Exception as exc:
                failed.append((model_id, str(exc)))
                self.stderr.write(self.style.ERROR(f"{model_id}: failed - {exc}"))

        if failed:
            raise CommandError(f"{len(failed)} model access check(s) failed")


def _verify_with_http(model_id: str) -> str:
    """Small stdlib fallback when huggingface_hub is not installed locally."""
    from urllib.error import HTTPError
    from urllib.request import Request, urlopen

    url = f"https://huggingface.co/api/models/{model_id}"
    headers = {}
    if config.HF_TOKEN:
        headers["Authorization"] = f"Bearer {config.HF_TOKEN}"
    request = Request(url, headers=headers)
    try:
        with urlopen(request, timeout=20) as response:
            if response.status != 200:
                raise RuntimeError(f"HTTP {response.status}")
    except HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code}") from exc
    return model_id
