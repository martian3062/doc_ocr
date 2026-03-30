"""
JSON Utilities — Robust parsing of LLM output.
================================================
LLMs sometimes wrap their JSON responses in markdown code fences, add
commentary, or produce slightly malformed JSON (trailing commas, single
quotes, unquoted keys).  This module applies a multi-stage parsing
strategy to recover valid Python objects from such output:

  Stage 1 — Strip wrappers (```json…```, <json>…</json>, "assistant: …")
  Stage 2 — Try standard json.loads + orjson.loads on the cleaned string
            AND on every balanced {…} / […] substring found inside it
  Stage 3 — Try json_repair (if installed) which fixes common JSON errors
  Stage 4 — Python ast.literal_eval after converting JS literals
            (true/false/null → True/False/None)

Raises ValueError only if all four stages fail.
"""

import re
import ast
import json
import logging
from typing import Any, List

import orjson

logger = logging.getLogger("pipeline")

# Optional dependency — graceful fallback if not installed
try:
    from json_repair import repair_json
except ImportError:
    repair_json = None

# ── Compiled regexes (module-level for speed) ────────────────────────────────
_FENCE_START = re.compile(r"^```json\s*", re.I)
_FENCE_ANY   = re.compile(r"^```\s*")
_FENCE_END   = re.compile(r"\s*```$")
_XML_TAG_S   = re.compile(r"^<json>\s*",  re.I)
_XML_TAG_E   = re.compile(r"\s*</json>$", re.I)
_ASST_PREFIX = re.compile(r"^\s*assistant\s*[:\-]\s*", re.I)
_TRAIL_COMMA = re.compile(r",\s*([}\]])")   # trailing commas before } or ]
_JS_TRUE     = re.compile(r"\btrue\b",  re.I)
_JS_FALSE    = re.compile(r"\bfalse\b", re.I)
_JS_NULL     = re.compile(r"\bnull\b",  re.I)


# ─────────────────────────────────────────────────────────────────────────────
# Private helpers
# ─────────────────────────────────────────────────────────────────────────────

def _strip_response_wrappers(text: str) -> str:
    """
    Remove common response decorators that LLMs add around JSON:
    - Markdown code fences (```json ... ``` or ``` ... ```)
    - XML-style tags (<json> ... </json>)
    - "assistant:" prefix sometimes injected by chat templates
    """
    text = (text or "").strip()
    text = _FENCE_START.sub("", text)
    text = _FENCE_ANY.sub("", text)
    text = _FENCE_END.sub("", text)
    text = _XML_TAG_S.sub("", text)
    text = _XML_TAG_E.sub("", text)
    text = _ASST_PREFIX.sub("", text)
    return text.strip()


def _extract_balanced_json_candidates(text: str) -> List[str]:
    """
    Find every balanced {…} and […] substring in *text*.

    Some LLMs emit preamble text before the JSON (e.g., "Here is the
    extraction: {…}"). This function locates all candidate JSON blobs by
    tracking brace/bracket depth, so we can try to parse each one.

    Returns a deduplicated list of candidate strings (longest first is NOT
    guaranteed — we use insertion order which tends to prefer outer objects).
    """
    candidates = []

    for open_ch, close_ch in [("{", "}"), ("[", "]")]:
        for start in (m.start() for m in re.finditer(re.escape(open_ch), text)):
            depth      = 0
            in_string  = False
            escape     = False

            for idx in range(start, len(text)):
                ch = text[idx]

                if escape:
                    escape = False
                    continue
                if ch == "\\":
                    escape = True
                    continue
                if ch == '"':
                    in_string = not in_string
                    continue
                if in_string:
                    continue

                if ch == open_ch:
                    depth += 1
                elif ch == close_ch:
                    depth -= 1
                    if depth == 0:
                        candidates.append(text[start : idx + 1])
                        break  # found the closing bracket for this start

    # Deduplicate while preserving order
    seen: set = set()
    unique: List[str] = []
    for c in candidates:
        c = c.strip()
        if c and c not in seen:
            seen.add(c)
            unique.append(c)
    return unique


def _normalize_jsonish(text: str) -> str:
    """
    Minimal JSON repairs before attempting parse:
    - Remove trailing commas before } or ] (common LLM mistake)
    """
    return _TRAIL_COMMA.sub(r"\1", text.strip())


def _try_parse(cand: str) -> Any:
    """
    Try json.loads then orjson.loads on *cand*.
    Returns the parsed object, or raises the last exception on failure.
    """
    for loader in (json.loads, orjson.loads):
        try:
            return loader(cand)
        except Exception:
            pass
    raise ValueError("All standard parsers failed")


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def parse_json_loose(text: str) -> Any:
    """
    Aggressively parse JSON from LLM output — four-stage cascade:

    Stage 1 — Direct parse:
      Strip response wrappers → try json / orjson on the full string and
      on each balanced sub-object found within it.

    Stage 2 — json_repair:
      Pass every candidate through the `json_repair` library which handles
      missing quotes, truncated objects, etc.

    Stage 3 — Python literal_eval:
      Translate JavaScript boolean/null literals to Python, then use
      ast.literal_eval as a last resort.

    Raises ValueError if nothing works (caller should log and skip the note).
    """
    cleaned    = _strip_response_wrappers(text)
    candidates = [cleaned] + _extract_balanced_json_candidates(cleaned)

    # Stage 1 — standard parsers
    for cand in candidates:
        cand_norm = _normalize_jsonish(cand)
        for loader in (json.loads, orjson.loads):
            try:
                return loader(cand_norm)
            except Exception:
                pass

    # Stage 2 — json_repair (optional dependency)
    if repair_json is not None:
        for cand in candidates:
            try:
                repaired = repair_json(cand, return_objects=True)
                if isinstance(repaired, (dict, list)):
                    return repaired
            except Exception:
                pass

    # Stage 3 — Python literal_eval after JS→Python literal translation
    for cand in candidates:
        try:
            pyish = _JS_TRUE.sub("True",   cand)
            pyish = _JS_FALSE.sub("False", pyish)
            pyish = _JS_NULL.sub("None",   pyish)
            parsed = ast.literal_eval(pyish)
            if isinstance(parsed, (dict, list)):
                return parsed
        except Exception:
            pass

    # All stages failed — surface a useful error preview for debugging
    preview = cleaned[:500].replace("\n", "\\n")
    raise ValueError(f"Could not parse JSON. Preview: {preview}")


def safe_json_dumps(obj: Any) -> bytes:
    """
    Serialise *obj* to JSON bytes using orjson (faster than stdlib json).
    Output is pretty-printed with 2-space indentation.
    """
    return orjson.dumps(obj, option=orjson.OPT_INDENT_2)
