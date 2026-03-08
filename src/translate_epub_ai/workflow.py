"""Workflow helpers for translation rounds, repair loading, and quality checks."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .cache import ProgressCache
from .models import PendingNode, TranslationConfig
from .utils import log, sanitized_model_name, stable_text_hash


def make_output_name(input_path: Path, target_lang: str) -> Path:
    return input_path.with_name(f"{input_path.stem}_{target_lang.upper()}.epub")


def make_cache_name(input_path: Path, provider: str, target_lang: str, model: str) -> Path:
    suffix = f"{target_lang.upper()}_{sanitized_model_name(model)}"
    if provider == "openai":
        return input_path.with_name(f"{input_path.stem}_batch_{suffix}.progress.json")
    return input_path.with_name(f"{input_path.stem}_{provider}_{suffix}.progress.json")


def make_jsonl_name(input_path: Path, provider: str, target_lang: str, model: str) -> Path:
    suffix = f"{target_lang.upper()}_{sanitized_model_name(model)}"
    if provider == "openai":
        return input_path.with_name(f"{input_path.stem}_{suffix}.batch.jsonl")
    return input_path.with_name(f"{input_path.stem}_{provider}_{suffix}.batch.jsonl")


def make_manifest_name(input_path: Path, provider: str, target_lang: str, model: str) -> Path:
    suffix = f"{target_lang.upper()}_{sanitized_model_name(model)}"
    if provider == "openai":
        return input_path.with_name(f"{input_path.stem}_{suffix}.manifest.json")
    return input_path.with_name(f"{input_path.stem}_{provider}_{suffix}.manifest.json")


def required_api_key_env(provider: str) -> str:
    return "ANTHROPIC_API_KEY" if provider == "anthropic" else "OPENAI_API_KEY"


def load_repair_items(repair_file: Path, cache: ProgressCache) -> list[PendingNode]:
    raw_items = json.loads(repair_file.read_text(encoding="utf-8"))
    if not isinstance(raw_items, list) or not raw_items:
        raise ValueError("Repair file must be a non-empty JSON array.")

    items: list[PendingNode] = []
    for index, raw in enumerate(raw_items):
        if isinstance(raw, str):
            source_text = raw
            current_translation = cache.get(source_text)
            context_hint = ""
        elif isinstance(raw, dict):
            source_text = raw.get("source_text")
            if not source_text:
                raise ValueError(f"Repair item {index + 1} is missing 'source_text'.")
            current_translation = raw.get("current_translation") or cache.get(source_text)
            context_hint = str(raw.get("context_hint", ""))
        else:
            raise ValueError("Repair file entries must be strings or objects.")

        items.append(
            PendingNode(
                rel_path=repair_file.name,
                node_index=index,
                core_text=str(source_text),
                current_translation=current_translation,
                context_hint=context_hint,
            )
        )
    return items


def make_round_artifact_path(base_path: Path, suffix: str) -> Path:
    return base_path.with_name(f"{base_path.stem}.{suffix}{base_path.suffix}")


def count_words(text: str) -> int:
    return len(re.findall(r"[A-Za-zÀ-ÿ0-9]+", text))


def has_broken_encoding_artifacts(text: str) -> bool:
    return any(token in text for token in ("\u00c2", "\u00c3", "\ufffd"))


def has_suspicious_repetition(text: str) -> bool:
    return re.search(r"([^\s])\1{4,}", text) is not None


def looks_unbalanced(text: str) -> bool:
    pairs = [("(", ")"), ("[", "]"), ("{", "}"), ("\u201c", "\u201d"), ('"', '"')]
    for left, right in pairs:
        if left == right:
            if text.count(left) % 2 != 0:
                return True
        elif text.count(left) != text.count(right):
            return True
    return False


def should_auto_repair(source_text: str, translated_text: str, target_lang: str) -> bool:
    source = source_text.strip()
    translated = translated_text.strip()
    if not translated:
        return True
    if has_broken_encoding_artifacts(translated) or has_suspicious_repetition(translated):
        return True
    if looks_unbalanced(translated) and count_words(source) >= 6:
        return True

    source_words = count_words(source)
    translated_words = count_words(translated)
    if source_words >= 8:
        if translated_words <= max(2, int(source_words * 0.35)):
            return True
        if translated_words >= int(source_words * 3.2):
            return True

    if target_lang.lower() == "es" and source_words >= 5:
        if translated.lower() == source.lower():
            return True
        english_markers = {
            "the",
            "and",
            "that",
            "with",
            "this",
            "from",
            "which",
            "there",
            "would",
            "could",
            "because",
        }
        translated_tokens = {token.lower() for token in re.findall(r"[A-Za-z]+", translated)}
        if len(english_markers.intersection(translated_tokens)) >= 3:
            return True

    return False


def find_auto_repair_candidates(
    pending: list[PendingNode],
    cached_translations: dict[str, str | None],
    target_lang: str,
) -> list[PendingNode]:
    candidates: list[PendingNode] = []
    for item in pending:
        translated = cached_translations.get(stable_text_hash(item.core_text))
        if translated and should_auto_repair(item.core_text, translated, target_lang):
            candidates.append(
                PendingNode(
                    rel_path=item.rel_path,
                    node_index=item.node_index,
                    core_text=item.core_text,
                    current_translation=translated,
                    context_hint=f"Nearby file context: {item.rel_path}",
                )
            )
    return candidates


def build_round_config(
    *,
    base_config: TranslationConfig,
    request_path: Path,
    manifest_path: Path,
    prompt_mode: str,
    repair_file: Path | None,
) -> TranslationConfig:
    return TranslationConfig(
        input_epub=base_config.input_epub,
        provider=base_config.provider,
        target_lang=base_config.target_lang,
        model=base_config.model,
        output_epub=base_config.output_epub,
        cache_file=base_config.cache_file,
        jsonl_file=request_path,
        manifest_file=manifest_path,
        source_lang=base_config.source_lang,
        natural=base_config.natural,
        prompt_mode=prompt_mode,
        prompt_file=base_config.prompt_file,
        repair_file=repair_file,
        auto_repair_rounds=0,
        review_passes=0,
        completion_window=base_config.completion_window,
        poll_seconds=base_config.poll_seconds,
        max_items_per_request=base_config.max_items_per_request,
        max_chars_per_request=base_config.max_chars_per_request,
        max_output_tokens=base_config.max_output_tokens,
        prepare_only=False,
        resume_batch_id=None,
    )


def execute_batch_round(
    *,
    pending: list[PendingNode],
    config: TranslationConfig,
    cache: ProgressCache,
    provider: Any,
    artifact_provider: Any,
    request_path: Path,
    manifest_path: Path,
    groups: list[list[PendingNode]],
    resume_batch_id: str | None,
    mode_label: str,
) -> tuple[dict, int]:
    artifact_provider.build_request_artifact(
        request_path=request_path,
        manifest_path=manifest_path,
        groups=groups,
        config=config,
    )
    log(f"Prepared {mode_label} request artifact: {request_path}")
    log(f"Manifest created: {manifest_path}")
    log(f"Grouped requests: {len(groups)}")
    log(f"Compression ratio: {len(pending) / len(groups):.1f} text nodes per batch request")

    if config.prepare_only:
        return {}, 0

    if resume_batch_id:
        batch_id = resume_batch_id
        log(f"Resuming existing batch: {batch_id}")
    else:
        batch_id = provider.create_batch(
            request_path=request_path,
            metadata={
                "provider": config.provider,
                "source_epub": config.input_epub.name,
                "target_lang": config.target_lang,
                "model": config.model,
                "prompt_style": "natural" if config.natural else "literal",
                "mode": mode_label,
            },
            completion_window=config.completion_window,
        )
        log(f"Created {mode_label} batch. batch_id={batch_id}")
        cache.set_meta("last_provider", config.provider)
        cache.set_meta("last_batch_id", batch_id)
        cache.save()

    batch = provider.wait_for_batch(batch_id, config.poll_seconds)
    output_bytes = provider.get_result_bytes(batch, batch_id)
    if output_bytes is None:
        log("Batch finished but no results payload is available yet.")
        log(json.dumps(batch, ensure_ascii=False, indent=2))
        return batch, 0

    output_jsonl = request_path.with_suffix(".output.jsonl")
    output_jsonl.write_bytes(output_bytes)
    log(f"Saved batch output JSONL: {output_jsonl}")

    parsed = provider.parse_grouped_output(output_bytes, manifest_path)
    if not parsed:
        raise RuntimeError("No grouped translations could be parsed from batch output.")

    stored = 0
    for item in pending:
        translated = parsed.get(stable_text_hash(item.core_text))
        if translated is not None:
            cache.set(item.core_text, translated)
            stored += 1

    cache.save()
    log(f"Stored translations in cache: {stored}")
    return batch, stored
