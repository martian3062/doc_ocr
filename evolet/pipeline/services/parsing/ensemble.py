"""Parser ensemble routing for evidence-native document understanding."""

from __future__ import annotations

import logging
from typing import Dict, List

from .. import config
from .base import ParserResult
from .docling_parser import DoclingParser
from .paddle_parser import PaddleStructureParser
from .surya_parser import SuryaParser
from .yolo_layout_parser import YOLOLayoutParser

logger = logging.getLogger("pipeline")

PARSERS = {
    "docling": DoclingParser,
    "surya": SuryaParser,
    "paddle_structure": PaddleStructureParser,
    "yolo_layout": YOLOLayoutParser,
}


def parse_document(pdf_path: str) -> Dict[str, object]:
    """
    Run enabled parser adapters and return normalized artifact dictionaries.

    Parser artifacts augment the existing PyMuPDF block extraction. They never
    block the pipeline; failures are recorded in metadata and the stable native
    extraction path continues.
    """
    enabled = [name.strip() for name in config.DOC_READER_PARSER_BACKENDS.split(",") if name.strip()]
    artifacts: List[dict] = []
    parser_results: List[dict] = []

    if not config.DOC_READER_ENABLE_ADVANCED_PARSERS:
        return {"artifacts": artifacts, "parsers": parser_results}

    for name in enabled:
        parser_cls = PARSERS.get(name)
        if parser_cls is None:
            parser_results.append({"backend": name, "available": False, "error": "unknown_parser"})
            continue

        parser = parser_cls()
        result: ParserResult = parser.parse(pdf_path)
        parser_results.append({
            "backend": result.backend,
            "available": result.available,
            "artifact_count": len(result.artifacts),
            "metadata": result.metadata,
            "error": result.error,
        })
        artifacts.extend(item.as_dict() for item in result.artifacts)

    logger.info("Parser ensemble generated %d artifacts via %s", len(artifacts), enabled)
    return {"artifacts": artifacts, "parsers": parser_results}
