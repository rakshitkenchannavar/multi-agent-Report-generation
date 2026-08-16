"""
utils.py
--------
Logging setup and reusable helper functions.
Never log API keys or other secrets.
"""

from __future__ import annotations

import json
import logging
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Type, TypeVar, Union

from pydantic import BaseModel, ValidationError

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

_LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_RESERVED_SECRET_PATTERNS = (
    "api_key",
    "apikey",
    "api-key",
    "secret",
    "password",
    "token",
    "authorization",
    "bearer",
)


def setup_logging(level: str = "INFO") -> None:
    """
    Configure root logging once for the application.
    Safe to call multiple times; handlers are not duplicated.
    """
    root = logging.getLogger()
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    root.setLevel(numeric_level)

    if not root.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(numeric_level)
        handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT))
        root.addHandler(handler)
    else:
        for handler in root.handlers:
            handler.setLevel(numeric_level)


def get_logger(name: str) -> logging.Logger:
    """Return a named logger (call setup_logging() at app startup)."""
    return logging.getLogger(name)


logger = get_logger("ai_report_generator")


def _redact_secrets(text: str) -> str:
    """Best-effort redaction of secret-like key/value pairs in plain text."""
    redacted = text
    for key in _RESERVED_SECRET_PATTERNS:
        # JSON-style: "api_key": "value"
        redacted = re.sub(
            rf'("{key}"\s*:\s*)".*?"',
            rf'\1"***REDACTED***"',
            redacted,
            flags=re.IGNORECASE,
        )
        # env-style: API_KEY=value
        redacted = re.sub(
            rf"({key}\s*=\s*)\S+",
            rf"\1***REDACTED***",
            redacted,
            flags=re.IGNORECASE,
        )
    return redacted


def log_event(event: str, **kwargs: Any) -> None:
    """
    Structured-style info log for important workflow milestones.

    Example:
        log_event("planning_completed", title=plan.title, sections=len(plan.sections))
    """
    if kwargs:
        parts = []
        for key, value in kwargs.items():
            if any(s in key.lower() for s in _RESERVED_SECRET_PATTERNS):
                parts.append(f"{key}=***REDACTED***")
            else:
                parts.append(f"{key}={value!r}")
        message = f"{event} | " + " ".join(parts)
    else:
        message = event
    logger.info(_redact_secrets(message))


def log_error(event: str, error: Exception | str, **kwargs: Any) -> None:
    """Log an error without leaking secrets."""
    err_text = str(error)
    extra = " ".join(
        f"{k}=***REDACTED***" if any(s in k.lower() for s in _RESERVED_SECRET_PATTERNS) else f"{k}={v!r}"
        for k, v in kwargs.items()
    )
    msg = f"{event} | error={err_text}"
    if extra:
        msg = f"{msg} | {extra}"
    logger.error(_redact_secrets(msg))


# ---------------------------------------------------------------------------
# IDs / time / files
# ---------------------------------------------------------------------------


def new_request_id() -> str:
    """Short unique id for correlating a single report generation run."""
    return uuid.uuid4().hex[:12]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().isoformat()


def slugify(text: str, max_length: int = 60) -> str:
    """
    Convert text to a filesystem-safe slug.
    Example: "Impact of AI in Healthcare!" -> "impact_of_ai_in_healthcare"
    """
    text = text.strip().lower()
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    text = re.sub(r"[-\s]+", "_", text).strip("_")
    if not text:
        text = "report"
    return text[:max_length].rstrip("_")


def ensure_dir(path: Union[str, Path]) -> Path:
    """Create directory (and parents) if needed; return Path."""
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def build_output_basename(title: str, request_id: Optional[str] = None) -> str:
    """
    Build a unique-ish base filename (no extension).
    Example: 20260322_143015_impact_of_ai_a1b2c3d4e5f6
    """
    ts = utc_now().strftime("%Y%m%d_%H%M%S")
    slug = slugify(title)
    rid = request_id or new_request_id()
    return f"{ts}_{slug}_{rid}"


# ---------------------------------------------------------------------------
# LLM response parsing helpers
# ---------------------------------------------------------------------------

T = TypeVar("T", bound=BaseModel)

# Matches ```json ... ``` or ``` ... ```
_CODE_FENCE_RE = re.compile(
    r"```(?:json|JSON)?\s*([\s\S]*?)\s*```",
    re.MULTILINE,
)


def extract_json_text(raw: str) -> str:
    """
    Extract JSON text from an LLM reply.
    Handles:
      - pure JSON
      - markdown fenced blocks
      - leading/trailing prose around a JSON object/array
    """
    if raw is None:
        raise ValueError("Cannot parse JSON from empty LLM response.")

    text = raw.strip()
    if not text:
        raise ValueError("Cannot parse JSON from empty LLM response.")

    # 1) Fenced code block
    fence = _CODE_FENCE_RE.search(text)
    if fence:
        return fence.group(1).strip()

    # 2) First {...} or [...]
    obj_start = text.find("{")
    arr_start = text.find("[")

    if obj_start == -1 and arr_start == -1:
        raise ValueError("No JSON object or array found in LLM response.")

    if obj_start == -1:
        start = arr_start
        open_c, close_c = "[", "]"
    elif arr_start == -1:
        start = obj_start
        open_c, close_c = "{", "}"
    else:
        if obj_start < arr_start:
            start = obj_start
            open_c, close_c = "{", "}"
        else:
            start = arr_start
            open_c, close_c = "[", "]"

    # Scan for matching bracket (simple depth counter; good enough for LLM JSON)
    depth = 0
    in_string = False
    escape = False
    end = None
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch == open_c:
            depth += 1
        elif ch == close_c:
            depth -= 1
            if depth == 0:
                end = i + 1
                break

    if end is None:
        raise ValueError("Unbalanced JSON brackets in LLM response.")

    return text[start:end].strip()


def parse_json_to_model(raw: str, model_type: Type[T]) -> T:
    """
    Parse LLM text into a Pydantic model.
    Raises ValueError with a clear message on failure.
    """
    try:
        json_text = extract_json_text(raw)
        data = json.loads(json_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON from LLM: {exc}") from exc
    except ValueError:
        raise

    try:
        return model_type.model_validate(data)
    except ValidationError as exc:
        raise ValueError(
            f"JSON did not match schema {model_type.__name__}: {exc}"
        ) from exc


def safe_json_loads(raw: str, default: Any = None) -> Any:
    """Parse JSON from LLM text; return default on failure instead of raising."""
    try:
        return json.loads(extract_json_text(raw))
    except Exception:
        return default


def model_to_pretty_json(model: BaseModel) -> str:
    """Serialize a Pydantic model to indented JSON (for prompts / debugging)."""
    return model.model_dump_json(indent=2)


def truncate_text(text: str, max_chars: int = 2000, suffix: str = "…") -> str:
    """Truncate long text for logs or compact prompt sections."""
    if text is None:
        return ""
    text = str(text)
    if len(text) <= max_chars:
        return text
    if max_chars <= len(suffix):
        return text[:max_chars]
    return text[: max_chars - len(suffix)] + suffix


def message_content_to_str(content: Any) -> str:
    """
    Normalize chat-message content to a plain string.
    Handles str, list of blocks (OpenAI/MAF style), and other simple types.
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                # e.g. {"type": "text", "text": "..."}
                if "text" in block:
                    parts.append(str(block["text"]))
                elif "content" in block:
                    parts.append(str(block["content"]))
                else:
                    parts.append(str(block))
            else:
                # objects with .text attribute
                text_attr = getattr(block, "text", None)
                if text_attr is not None:
                    parts.append(str(text_attr))
                else:
                    parts.append(str(block))
        return "\n".join(parts)
    return str(content)