"""Shared utility helpers."""

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Tuple


def log(message: str) -> None:
    print(message, flush=True)


def is_probably_text(value: str) -> bool:
    if not value:
        return False
    stripped = value.strip()
    if not stripped:
        return False
    if stripped.startswith("http://") or stripped.startswith("https://"):
        return False
    return sum(char.isalnum() for char in stripped) > 0


def leading_trailing_ws(text: str) -> Tuple[str, str, str]:
    leading_match = re.match(r"^\s*", text)
    trailing_match = re.search(r"\s*$", text)
    leading = leading_match.group(0) if leading_match else ""
    trailing = trailing_match.group(0) if trailing_match else ""
    core = text[len(leading) : len(text) - len(trailing) if trailing else len(text)]
    return leading, core, trailing


def stable_text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def clean_json_text(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

    first_arr = text.find("[")
    first_obj = text.find("{")
    candidates = [index for index in (first_arr, first_obj) if index != -1]
    if candidates:
        text = text[min(candidates) :]

    if text.startswith("["):
        end = text.rfind("]")
        if end != -1:
            text = text[: end + 1]
    elif text.startswith("{"):
        end = text.rfind("}")
        if end != -1:
            text = text[: end + 1]

    return text.strip()


def sanitized_model_name(model: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", model)


def dump_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
