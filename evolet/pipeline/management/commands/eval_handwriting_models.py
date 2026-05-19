"""Compare Hugging Face handwriting recognizers on stored handwriting crops."""

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List

import fitz
import torch
from torch import nn
from django.core.management.base import BaseCommand, CommandError

from pipeline.models import DocumentArtifact, PDFDocument, PipelineRun
from pipeline.services import config
from pipeline.services.gpu_utils import detect_compute_dtype, release_cuda
from pipeline.services.handwriting_order_extractor import _fallback_zones, _prepare_crop, _relative_to_page_bbox, _render_crop
from pipeline.services.medical_short_forms import find_drug_candidates
from pipeline.services.pdf_extractor import normalize_text


@dataclass
class CropCase:
    artifact_id: str
    patient_code: str
    document_name: str
    pdf_path: str
    page_num: int
    bbox: List[float]
    reference_text: str


class Command(BaseCommand):
    help = "Evaluate configured handwriting-recognition candidates on the same stored handwriting crops."

    def add_arguments(self, parser):
        parser.add_argument("--model", action="append", dest="models", help="Model ID to test. Can be repeated.")
        parser.add_argument("--limit-crops", type=int, default=3, help="Maximum handwriting crops to test.")
        parser.add_argument("--run-id", default="", help="Optional PipelineRun id to source crops from.")
        parser.add_argument("--json", action="store_true", help="Print JSON instead of a text table.")
        parser.add_argument("--trust-remote-code", action="store_true", help="Allow trust_remote_code for candidate loading.")

    def handle(self, *args, **options):
        models = _normalise_models(options["models"] or config.MEDICAL_HANDWRITING_CANDIDATE_MODEL_IDS)
        cases = _load_cases(run_id=options["run_id"], limit=options["limit_crops"])
        if not cases:
            self.stderr.write("No stored handwriting artifacts found; rendering fallback handwriting zones from source PDFs.")
            cases = _load_fallback_cases(limit=options["limit_crops"])
        if not cases:
            raise CommandError("No handwriting crops could be prepared from artifacts or source PDFs.")

        rows = []
        for model_id in models:
            rows.extend(_evaluate_model(model_id, cases, trust_remote_code=options["trust_remote_code"]))
            release_cuda()

        rows.sort(key=lambda item: (item["score"], item["drug_hits"], item["text_chars"]), reverse=True)
        if options["json"]:
            self.stdout.write(json.dumps({"cases": [case.__dict__ for case in cases], "results": rows}, indent=2))
            return

        self.stdout.write(f"Tested {len(models)} model(s) on {len(cases)} crop(s)")
        self.stdout.write("score\tdrugs\tchars\tstatus\tmodel\ttext")
        for row in rows:
            text = row["text"].replace("\n", " ")[:160]
            self.stdout.write(
                f"{row['score']:.2f}\t{row['drug_hits']}\t{row['text_chars']}\t{row['status']}\t{row['model_id']}\t{text}"
            )


def _normalise_models(values: Iterable[str]) -> List[str]:
    out: List[str] = []
    for value in values:
        for item in str(value).split(","):
            item = item.strip()
            if item and item not in out:
                out.append(item)
    return out


def _load_cases(*, run_id: str, limit: int) -> List[CropCase]:
    qs = DocumentArtifact.objects.filter(
        artifact_type=DocumentArtifact.ArtifactType.HANDWRITING,
        bbox__isnull=False,
    ).select_related("patient", "document", "run")
    if run_id:
        qs = qs.filter(run_id=run_id)
    elif PipelineRun.objects.exists():
        latest = PipelineRun.objects.order_by("-started_at", "-created_at").first()
        qs = qs.filter(run=latest)

    cases: List[CropCase] = []
    for artifact in qs.order_by("document__original_filename", "page_num", "reading_order")[: max(1, limit * 4)]:
        document = artifact.document
        pdf_path = document.folder_path or (document.file.path if document.file else "")
        bbox = artifact.bbox if isinstance(artifact.bbox, list) else []
        if not pdf_path or not Path(pdf_path).exists() or len(bbox) != 4:
            continue
        cases.append(
            CropCase(
                artifact_id=str(artifact.id),
                patient_code=artifact.patient.code,
                document_name=document.original_filename,
                pdf_path=pdf_path,
                page_num=int(artifact.page_num or 1),
                bbox=[float(v) for v in bbox],
                reference_text=normalize_text(artifact.normalized_text or artifact.text),
            )
        )
        if len(cases) >= limit:
            break
    return cases


def _load_fallback_cases(*, limit: int) -> List[CropCase]:
    cases: List[CropCase] = []
    for document in PDFDocument.objects.select_related("patient").order_by("-created_at")[: max(1, limit * 3)]:
        pdf_path = document.folder_path or (document.file.path if document.file else "")
        if not pdf_path or not Path(pdf_path).exists():
            continue
        try:
            doc = fitz.open(pdf_path)
        except Exception:
            continue
        try:
            if not doc:
                continue
            page = doc[0]
            width = float(page.rect.width)
            height = float(page.rect.height)
            for index, (_role, rel_box) in enumerate(_fallback_zones(), start=1):
                bbox = _relative_to_page_bbox(rel_box, width, height)
                cases.append(
                    CropCase(
                        artifact_id=f"fallback:{document.id}:{index}",
                        patient_code=document.patient.code,
                        document_name=document.original_filename,
                        pdf_path=pdf_path,
                        page_num=1,
                        bbox=bbox,
                        reference_text="",
                    )
                )
                if len(cases) >= limit:
                    return cases
        finally:
            doc.close()
    return cases


def _evaluate_model(model_id: str, cases: List[CropCase], *, trust_remote_code: bool) -> List[Dict[str, Any]]:
    started = time.time()
    try:
        recognizer = _load_recognizer(model_id, trust_remote_code=trust_remote_code)
    except Exception as exc:
        return [{
            "model_id": model_id,
            "status": "load_failed",
            "error": str(exc)[:500],
            "case": "",
            "text": "",
            "text_chars": 0,
            "drug_hits": 0,
            "overlap": 0.0,
            "score": 0.0,
            "seconds": round(time.time() - started, 2),
        }]

    rows = []
    try:
        for case in cases:
            text = ""
            error = ""
            status = "ok"
            t0 = time.time()
            try:
                image = _render_case(case)
                text = normalize_text(recognizer(image))
            except Exception as exc:
                status = "inference_failed"
                error = str(exc)[:500]
            rows.append(_score_row(model_id, case, text, status, error, time.time() - t0))
    finally:
        del recognizer
        release_cuda()
    return rows


def _load_recognizer(model_id: str, *, trust_remote_code: bool):
    from transformers import AutoModelForImageTextToText, AutoProcessor, TrOCRProcessor, VisionEncoderDecoderModel, pipeline

    common_kwargs = {
        "local_files_only": config.LOCAL_FILES_ONLY,
        "trust_remote_code": trust_remote_code or config.MEDICAL_HANDWRITING_TRUST_REMOTE_CODE,
    }
    if config.HF_TOKEN:
        common_kwargs["token"] = config.HF_TOKEN

    model_kwargs = dict(common_kwargs)
    if torch.cuda.is_available():
        model_kwargs["torch_dtype"] = detect_compute_dtype() or torch.float16
        model_kwargs["device_map"] = "auto"

    load_errors = []
    if model_id == "ismatsamadov/handwriting-recognition-iam":
        return _load_ismatsamadov_crnn(model_id)
    if model_id == "Teklia/pylaia-iam":
        raise RuntimeError("Teklia/pylaia-iam requires a PyLaia decoder runner; direct Transformers inference is not available")

    if "trocr" in model_id.lower():
        try:
            processor = TrOCRProcessor.from_pretrained(model_id, **common_kwargs)
            model = VisionEncoderDecoderModel.from_pretrained(model_id, **model_kwargs)
            model.eval()

            def _trocr_run(image, processor=processor, model=model):
                device = next(model.parameters()).device
                pixel_values = processor(image, return_tensors="pt").pixel_values.to(device)
                with torch.inference_mode():
                    ids = model.generate(pixel_values, do_sample=False, max_new_tokens=128)
                return processor.batch_decode(ids, skip_special_tokens=True)[0].strip()

            return _trocr_run
        except Exception as exc:
            load_errors.append(f"TrOCRProcessor: {str(exc)[:180]}")
            release_cuda()

    for model_cls in (VisionEncoderDecoderModel, AutoModelForImageTextToText):
        try:
            processor = AutoProcessor.from_pretrained(model_id, **common_kwargs)
            model = model_cls.from_pretrained(model_id, **model_kwargs)
            model.eval()

            if model_cls is VisionEncoderDecoderModel:
                def _run(image, processor=processor, model=model):
                    device = next(model.parameters()).device
                    try:
                        inputs = processor(image, return_tensors="pt").to(device)
                    except Exception:
                        image_processor = getattr(processor, "image_processor", None) or getattr(processor, "feature_extractor", None)
                        if image_processor is None:
                            raise
                        inputs = image_processor(images=image, return_tensors="pt").to(device)
                    with torch.inference_mode():
                        ids = model.generate(**inputs, do_sample=False, max_new_tokens=128)
                    return processor.batch_decode(ids, skip_special_tokens=True)[0].strip()

                return _run

            def _run(image, processor=processor, model=model):
                device = next(model.parameters()).device
                prompt = "Read this handwriting crop. Return only the visible text."
                try:
                    inputs = processor(images=image, text=prompt, return_tensors="pt").to(device)
                except TypeError:
                    inputs = processor(images=image, return_tensors="pt").to(device)
                with torch.inference_mode():
                    ids = model.generate(**inputs, do_sample=False, max_new_tokens=128)
                text = processor.batch_decode(ids, skip_special_tokens=True)[0]
                return text.replace(prompt, "").strip()

            return _run
        except Exception as exc:
            load_errors.append(f"{model_cls.__name__}: {str(exc)[:180]}")
            release_cuda()

    try:
        device = 0 if torch.cuda.is_available() else -1
        pipe = pipeline("image-to-text", model=model_id, device=device, **common_kwargs)

        def _pipe_run(image, pipe=pipe):
            result = pipe(image)
            first = result[0] if isinstance(result, list) and result else result
            return str((first or {}).get("generated_text") if isinstance(first, dict) else first)

        return _pipe_run
    except Exception as exc:
        load_errors.append(f"pipeline: {str(exc)[:180]}")
    raise RuntimeError("; ".join(load_errors))


class CharacterMapper:
    def __init__(self):
        self.chars: List[str] = []
        self.char2idx: Dict[str, int] = {}
        self.idx2char: Dict[int, str] = {0: ""}
        self.num_classes = 1

    def decode(self, indices: Iterable[int]) -> str:
        chars = []
        prev = None
        for idx in indices:
            if idx != 0 and idx != prev and idx in self.idx2char:
                chars.append(self.idx2char[idx])
            prev = idx
        return "".join(chars)


class IsmatCRNN(nn.Module):
    def __init__(self, num_chars: int, hidden_size: int = 256, num_layers: int = 2):
        super().__init__()
        self.cnn = nn.Sequential(
            nn.Conv2d(1, 64, 3, padding=1), nn.BatchNorm2d(64), nn.ReLU(), nn.MaxPool2d(2, 2),
            nn.Conv2d(64, 128, 3, padding=1), nn.BatchNorm2d(128), nn.ReLU(), nn.MaxPool2d(2, 2),
            nn.Conv2d(128, 256, 3, padding=1), nn.BatchNorm2d(256), nn.ReLU(),
            nn.Conv2d(256, 256, 3, padding=1), nn.BatchNorm2d(256), nn.ReLU(), nn.MaxPool2d((2, 1)),
            nn.Conv2d(256, 512, 3, padding=1), nn.BatchNorm2d(512), nn.ReLU(),
            nn.Conv2d(512, 512, 3, padding=1), nn.BatchNorm2d(512), nn.ReLU(), nn.MaxPool2d((2, 1)),
            nn.Conv2d(512, 512, 2), nn.BatchNorm2d(512), nn.ReLU(),
        )
        self.map2seq = nn.Linear(512 * 7, hidden_size)
        self.rnn = nn.LSTM(
            hidden_size,
            hidden_size,
            num_layers,
            bidirectional=True,
            dropout=0.3 if num_layers > 1 else 0,
            batch_first=True,
        )
        self.fc = nn.Linear(hidden_size * 2, num_chars + 1)

    def forward(self, x):
        conv = self.cnn(x)
        b, c, h, w = conv.size()
        conv = conv.permute(0, 3, 1, 2).reshape(b, w, c * h)
        seq = self.map2seq(conv)
        rnn_out, _ = self.rnn(seq)
        output = self.fc(rnn_out)
        return torch.nn.functional.log_softmax(output, dim=2)


def _load_ismatsamadov_crnn(model_id: str):
    from huggingface_hub import hf_hub_download

    # The checkpoint pickles CharacterMapper as __main__.CharacterMapper.
    setattr(sys.modules["__main__"], "CharacterMapper", CharacterMapper)
    ckpt_path = hf_hub_download(repo_id=model_id, filename="best_model.pth", token=config.HF_TOKEN)
    checkpoint = torch.load(ckpt_path, map_location="cpu", weights_only=False)
    char_mapper = checkpoint["char_mapper"]
    model = IsmatCRNN(num_chars=len(char_mapper.chars))
    model.load_state_dict(checkpoint["model_state_dict"])
    device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
    model.to(device)
    model.eval()

    def _run(image, model=model, mapper=char_mapper, device=device):
        tensor = _preprocess_ismat_image(image).to(device)
        with torch.inference_mode():
            output = model(tensor)
        pred_indices = output.argmax(dim=2).squeeze(0).tolist()
        return mapper.decode(pred_indices)

    return _run


def _preprocess_ismat_image(image):
    gray = image.convert("L").resize((512, 128))
    import numpy as np

    arr = np.array(gray, dtype=np.float32) / 255.0
    arr = (arr - 0.5) / 0.5
    return torch.from_numpy(arr).unsqueeze(0).unsqueeze(0)


def _render_case(case: CropCase):
    doc = fitz.open(case.pdf_path)
    try:
        page = doc[max(0, min(len(doc) - 1, case.page_num - 1))]
        return _prepare_crop(_render_crop(page, case.bbox))
    finally:
        doc.close()


def _score_row(model_id: str, case: CropCase, text: str, status: str, error: str, seconds: float) -> Dict[str, Any]:
    drug_hits = len(find_drug_candidates(text, limit=8))
    overlap = _token_overlap(text, case.reference_text)
    text_chars = len(text)
    score = (drug_hits * 20.0) + min(text_chars, 180) / 18.0 + overlap * 25.0
    if status != "ok":
        score = 0.0
    return {
        "model_id": model_id,
        "status": status,
        "error": error,
        "case": case.artifact_id,
        "patient_code": case.patient_code,
        "document_name": case.document_name,
        "text": text,
        "text_chars": text_chars,
        "drug_hits": drug_hits,
        "overlap": round(overlap, 3),
        "score": round(score, 2),
        "seconds": round(seconds, 2),
    }


def _token_overlap(text: str, reference: str) -> float:
    left = {tok for tok in normalize_text(text).lower().split() if len(tok) >= 3}
    right = {tok for tok in normalize_text(reference).lower().split() if len(tok) >= 3}
    if not left or not right:
        return 0.0
    return len(left & right) / max(1, len(left | right))
