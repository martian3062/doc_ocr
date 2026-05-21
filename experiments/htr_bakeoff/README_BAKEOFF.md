# HTR / Prescription OCR Bake-off Notes

Date: 2026-05-20
Scope: CPU-safe doc-ocr integration for handwritten medicine/order crops.

## Cloneability

| Repo | Status | License signal | Runtime signal |
| --- | --- | --- | --- |
| rsommerfeld/trocr | cloned | MIT | Hugging Face TrOCR wrapper, CPU env available |
| githubharald/SimpleHTR | cloned | MIT | TensorFlow 2.4, pretrained model download required |
| yutingli0606/htr-vt | cloned | no LICENSE file in repo | research code, checkpoints external, GPU-oriented |
| awslabs/handwritten-text-recognition-for-apache-mxnet | cloned | Apache-2.0 | old MXNet full-page pipeline, heavy setup |
| jc639/pytorch-handwritingCTC | cloned | no LICENSE file in repo | training/demo code, no packaged inference model |
| dmitrijsk/AttentionHTR | cloned | Apache-2.0 | pretrained models external Google Drive, word-level |
| DocumentRecognitionModels/HTR-JAND | not cloned | unavailable | GitHub returned repository not found |
| JonSnow1807/Medical-Prescription-OCR | cloned | MIT | closest prescription model, HF Donut model |
| Infi-09/Doctor-Prescripton-Handwritten-Recoginition | cloned | no LICENSE file in repo | medicine-name/classification style, old PyTorch |
| shubhm-gupta/Keywords-Identification-from-Handwritten-Doctor-Prescription | cloned | no LICENSE file in repo | architecture/writeup, no runnable model package |

## Best fit for this app

1. JonSnow1807/Medical-Prescription-OCR
   - Best open integration candidate for medicine/order crops.
   - MIT, prescription-focused, Hugging Face model already maps to the app's existing Donut backend setting.
   - Use only on deterministic medicine-table crops, not whole PDFs.

2. rsommerfeld/trocr
   - Easiest generic TrOCR wrapper, MIT, CPU env exists.
   - Better as a line/crop baseline than as final doctor-prescription OCR.
   - In production, direct `transformers` TrOCR is simpler than importing the whole repo.

3. githubharald/SimpleHTR
   - Good classic baseline and teaching reference.
   - Not ideal for production because TensorFlow 2.4 and Dropbox model download are stale.

4. dmitrijsk/AttentionHTR
   - Stronger word-level research baseline than SimpleHTR.
   - Requires external pretrained weights and old Torch stack, so integration cost is higher.

5. yutingli0606/htr-vt
   - Interesting 2025 research option, but not easy for this app today.
   - No license file found, external checkpoints, GPU-oriented environment.

Do not prioritize AWS MXNet, jc639 CTC, Infi-09, or shubhm-gupta for production integration unless the goal is research/reference only.

## 5-report crop-target smoke

Test was run inside the live django-only container against `/data/django_only_10pdf_smoke/*.pdf` using the deterministic crop-targeting logic, with no GPU-local model calls.

| Report | Target pages | First crop source |
| --- | --- | --- |
| 2 Rupali Katyal - UHID 35955.pdf | 3, 2 | page 3 word_anchor, page 2 fallback |
| 2 files Umesh kumar - UHID 35363.pdf | 3, 5 | word_anchor on both pages |
| 2 files kaushal devi - 34530.pdf | 3, 5 | word_anchor on both pages |
| 3 files Kusum Jain - 34295.pdf | 1, 2 | fallback only |
| Abdul Aleem - UHID 32553.pdf | 3, 1 | page 3 word_anchor, page 1 fallback |

Result: 4/5 reports have deterministic medicine/order chart anchors before model OCR. Kusum needs either more page text signal or a second visual/table detector pass.

## 5-report runtime estimate

Current VM-safe settings:

- `DOC_READER_ENABLE_LOCAL_HF_LLM=0`
- `DOC_READER_ENABLE_LOCAL_HF_VISION_MODELS=0`
- `DOC_READER_MULTIMODAL_MEDICINE_BACKENDS=dictionary`
- `DOC_READER_PAGE_VISION_MAX_PAGES_PER_DOCUMENT=2`
- `DOC_READER_PAGE_VISION_MAX_CROPS_PER_PAGE=4`
- `DOC_READER_HANDWRITING_ORDER_MAX_PAGES_PER_DOCUMENT=2`
- `DOC_READER_HANDWRITING_ORDER_MAX_CROPS_PER_PAGE=4`

For 5 reports, upper bound is about 80 Groq vision crop calls: 5 reports x 2 pages x 4 page-vision crops + 5 reports x 2 pages x 4 order crops. Blank/low-signal crops may reduce this.

Expected elapsed time on CPU/cloud-safe path: roughly 4-10 minutes for 5 multi-page reports, depending mostly on Groq latency and page count.

Recommended next implementation path:

1. Keep deterministic crop targeting as the router.
2. Add JonSnow/Donut as an optional crop backend only when GPU is idle or CPU time is acceptable.
3. Add TrOCR as a generic crop baseline for line-level handwriting only.
4. Keep Groq vision as the production fallback because it handles messy chart context better than IAM-trained line recognizers.

## Top-4 VM limited smoke

Date: 2026-05-20
VM mode: django-only, CPU/cloud-safe, local HF/Paddle disabled because another training job was active.

Command:

```bash
docker compose exec -T web sh scripts/run_top4_ocr_vm_limited.sh
```

Settings:

- 5 reports
- 2 target pages per report
- 4 crops per page
- local HF vision off
- Paddle execution off
- dictionary medicine backend
- one Groq handwriting/order crop read per report

Result:

| Rank | Approach | VM status | Fit result |
| --- | --- | --- | --- |
| 1 | PaddleOCR / PP-OCRv5 | not installed | Best next install for layout/table detection, but not added to the limited django-only image. |
| 2 | TrOCR-large-handwritten | not installed | Good crop-line recognizer later; 40 crop candidates were ready for it. |
| 3 | HTR-VT | repo/checkpoint missing in VM image | Research benchmark only for now; do not deploy without a checkpoint and license/runtime review. |
| 4 | full-page/crop cloud VLM | ok | Best current production fit: 5/5 selected crops returned text and 13 medicine dictionary hits. |

Crop targeting:

- 40 total crop candidates across 5 reports.
- 4/5 reports had deterministic word-anchor crops.
- `3 files Kusum Jain - 34295.pdf` remained fallback-only.

Cloud VLM crop reads:

| Report | Page | Drug hits | Summary |
| --- | ---: | ---: | --- |
| 2 Rupali Katyal - UHID 35955.pdf | 3 | 2 | Read NS, PALN/Pan-like premeds, paclitaxel, carboplatin-style orders. |
| 2 files Umesh kumar - UHID 35363.pdf | 3 | 2 | Read NS/Pantodac/NAB-paclitaxel/carboplatin-style chemo orders. |
| 2 files kaushal devi - 34530.pdf | 3 | 5 | Read Emend, Pan/Pantodac, Trastuzumab, Docetaxel, Carboplatin, Peg-Filg-style order text. |
| 3 files Kusum Jain - 34295.pdf | 1 | 0 | Fallback crop hit admission/header content, not medicine orders. |
| Abdul Aleem - UHID 32553.pdf | 3 | 4 | Read Palonosetron/Pan/Paclitaxel/Carboplatin-style order text. |

Decision from the smoke: keep production on deterministic medicine anchors plus Groq/crop VLM now. Install PaddleOCR next only when disk/CPU headroom is better; install TrOCR later in a separate local-HF image or sidecar, not in the current django-only image.
