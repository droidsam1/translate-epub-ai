"""Lightweight models used across the package."""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class PendingNode:
    rel_path: str
    node_index: int
    core_text: str
    current_translation: Optional[str] = None
    context_hint: str = ""


@dataclass(frozen=True)
class TranslationConfig:
    input_epub: Path
    provider: str
    target_lang: str
    model: str
    output_epub: Path
    cache_file: Path
    jsonl_file: Path
    manifest_file: Path
    source_lang: Optional[str]
    natural: bool
    prompt_mode: str
    prompt_file: Optional[Path]
    repair_file: Optional[Path]
    auto_repair_rounds: int
    review_passes: int
    completion_window: str
    poll_seconds: int
    max_items_per_request: int
    max_chars_per_request: int
    max_output_tokens: int
    prepare_only: bool
    resume_batch_id: Optional[str]
