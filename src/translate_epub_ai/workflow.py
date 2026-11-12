"""Workflow helpers for translation rounds, repair loading, and quality checks."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .batch_providers import build_grouped_requests
from .cache import ProgressCache
from .models import PendingNode, TranslationConfig
from .quality import ReviewItem, book_length_ratio, detect_weak_points
from .segments import MARKER_RE, salvage_markers, validate_markers
from .utils import log, normalize_spanish_quotes, sanitized_model_name, stable_text_hash

FORMAT_RETRY_PASSES = 1
SECTION_MARKERS = {
    "es": {
        "agradecimientos",
        "bibliografía",
        "contenido",
        "índice",
        "prefacio",
        "prólogo",
        "apéndice",
        "notas",
        "referencias",
    },
    "en": {
        "acknowledgements",
        "bibliography",
        "contents",
        "index",
        "preface",
        "prologue",
        "appendix",
        "notes",
        "references",
    },
}


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


def required_api_key_env(provider: str) -> str | None:
    if provider == "ollama":
        return None
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


def round_artifact_paths(request_path: Path, manifest_path: Path) -> list[Path]:
    return [request_path, manifest_path, request_path.with_suffix(".output.jsonl")]


def cleanup_artifacts(paths: list[Path]) -> None:
    for path in dict.fromkeys(paths):
        if path.exists():
            path.unlink()
            log(f"Removed intermediate artifact: {path}")


def count_words(text: str) -> int:
    return len(re.findall(r"[A-Za-zÀ-ÿ0-9]+", MARKER_RE.sub(" ", text)))


def normalize_text(text: str) -> str:
    return " ".join(text.split())


def extract_context_kind(context_hint: str) -> str:
    match = re.search(r"\bkind=([a-z]+)\b", context_hint)
    return match.group(1) if match else "paragraph"


def looks_like_heading(text: str) -> bool:
    normalized = normalize_text(text)
    if not normalized:
        return False
    words = re.findall(r"[A-Za-zÀ-ÿ0-9]+", normalized)
    if len(words) > 10 or len(normalized) > 90:
        return False
    if normalized.endswith(":"):
        return True
    if normalized == normalized.title():
        return True
    return len(words) <= 4 and normalized.isupper()


def contains_section_leakage(source_text: str, translated_text: str, target_lang: str) -> bool:
    """Detect a neighbouring section title glued into the translation.

    Only whole lines count: words like "contenido" or "notas" are ordinary
    vocabulary inside a sentence and must not trigger this check.
    """
    if count_words(source_text) < 8:
        return False
    markers = SECTION_MARKERS.get(target_lang.lower(), set())
    source = normalize_text(source_text).lower()
    plain_translation = normalize_text(MARKER_RE.sub("", translated_text))
    for marker in markers:
        capitalised = re.escape(marker.capitalize())
        glued_at_end = re.search(r"[.!?»…]\s+" + capitalised + r"\s*$", plain_translation)
        glued_at_start = re.match(capitalised + r"\s*(?:\n|:|$)", plain_translation)
        if (glued_at_end or glued_at_start) and marker not in source:
            return True
    lines = [normalize_text(line) for line in translated_text.splitlines() if normalize_text(line)]
    if len(lines) <= len(normalize_text(source_text).splitlines() or [""]):
        return False
    for line in lines:
        bare = line.lower().strip(" .:")
        if bare in markers:
            return True
        if looks_like_heading(line) and line.lower() not in source:
            return True
    return False


def has_broken_encoding_artifacts(text: str) -> bool:
    return any(token in text for token in ("\u00c2", "\u00c3", "\ufffd"))


def has_suspicious_repetition(text: str, source: str = "") -> bool:
    """A character repeated 5+ times that the source does not have (e.g. "aaaaa")."""
    return any(
        match.group(0) not in source for match in re.finditer(r"([^\s])\1{4,}", text)
    )


CITATION_RE = re.compile(r"\((?:[^()]*\b)?(?:1[5-9]\d\d|20\d\d)\)|\bISBN\b")


def looks_like_citation(text: str) -> bool:
    """Source lines such as 'Richard Dawkins, Climbing Mount Improbable (1996)'."""
    plain = MARKER_RE.sub("", text)
    return bool(CITATION_RE.search(plain)) and len(plain) < 300


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
    if has_broken_encoding_artifacts(translated) or has_suspicious_repetition(translated, source):
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


def translation_issue(item: PendingNode, translated_text: str, target_lang: str) -> str | None:
    source_words = count_words(item.core_text)
    translated_words = count_words(translated_text)
    context_kind = extract_context_kind(item.context_hint)

    marker_issue = validate_markers(item.core_text, translated_text)
    if marker_issue is not None:
        return marker_issue
    if context_kind in {"reference", "index"} or (
        looks_like_citation(item.core_text) and translated_text.strip() == item.core_text.strip()
    ):
        # Bibliography and index entries are often legitimately identical to
        # the source (names, titles, numbers): only structural checks apply.
        return "empty translation" if not translated_text.strip() else None
    if should_auto_repair(item.core_text, translated_text, target_lang):
        return "failed quality heuristics"
    if contains_section_leakage(item.core_text, translated_text, target_lang):
        return "possible section leakage from a neighboring fragment"
    if context_kind == "heading" and translated_words >= max(10, source_words * 3):
        return "heading expanded into paragraph-like output"
    if (
        context_kind == "paragraph"
        and source_words >= 8
        and not plain_source_ends_with_colon(item.core_text)
        and looks_like_heading(translated_text)
    ):
        return "paragraph collapsed into heading-like output"
    if translated_text.count("\n\n") > item.core_text.count("\n\n") + 1:
        return "unexpected extra paragraph breaks"
    return None


def plain_source_ends_with_colon(text: str) -> bool:
    return MARKER_RE.sub("", text).rstrip().endswith(":")


def build_review_candidates(
    nodes: list[PendingNode],
    cache: ProgressCache,
    scope: str,
    glossary: dict[str, str] | None,
    source_lang: str | None,
) -> list[PendingNode]:
    """Pick the paragraphs to review and tell the reviewer why.

    `nodes` must be in reading order: each candidate carries the translation
    of the paragraph before it, so seams between requests can be checked.
    """
    translated = [(node, cache.get(node.core_text)) for node in nodes]
    typical_ratio = book_length_ratio(
        ReviewItem(node.core_text, translation) for node, translation in translated if translation
    )
    candidates: list[PendingNode] = []
    seen: set[str] = set()
    previous: tuple[str, str | None] = ("", None)
    for node, translation in translated:
        previous_translation = previous[1] if previous[0] == node.rel_path else None
        previous = (node.rel_path, translation)
        if not translation or node.core_text in seen:
            continue
        seen.add(node.core_text)
        item = ReviewItem(
            source=node.core_text,
            translation=translation,
            kind=extract_context_kind(node.context_hint),
            doubt=cache.get_note(node.core_text),
        )
        notes = detect_weak_points(
            item,
            typical_ratio=typical_ratio,
            glossary=glossary,
            source_lang=source_lang,
            include_back_references=previous_translation is not None,
        )
        if scope != "all" and not notes:
            continue
        candidates.append(
            PendingNode(
                rel_path=node.rel_path,
                node_index=node.node_index,
                core_text=node.core_text,
                current_translation=translation,
                context_hint=node.context_hint,
                previous_translation=previous_translation,
                review_notes="; ".join(notes),
            )
        )
    return candidates


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
        glossary_file=base_config.glossary_file,
        reasoning_effort=base_config.reasoning_effort,
        review_scope=base_config.review_scope,
        skip_index=base_config.skip_index,
        review_existing=False,
        auto_glossary=False,
        glossary_only=False,
    )


def malformed_group_items(groups: list[list[PendingNode]], group_ids: list[str]) -> list[PendingNode]:
    selected: list[PendingNode] = []
    wanted = set(group_ids)
    for group_index, group in enumerate(groups, start=1):
        custom_id = f"group_{group_index:06d}"
        if custom_id in wanted:
            selected.extend(group)
    return selected


def dedupe_pending_nodes(items: list[PendingNode]) -> list[PendingNode]:
    deduped: list[PendingNode] = []
    seen_hashes: set[str] = set()
    for item in items:
        item_hash = stable_text_hash(item.core_text)
        if item_hash in seen_hashes:
            continue
        seen_hashes.add(item_hash)
        deduped.append(item)
    return deduped


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
    format_retry_attempts_left: int = FORMAT_RETRY_PASSES,
) -> tuple[dict, int, list[Path]]:
    artifacts = round_artifact_paths(request_path, manifest_path)
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
        return {}, 0, artifacts

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
        return batch, 0, artifacts

    output_jsonl = request_path.with_suffix(".output.jsonl")
    output_jsonl.write_bytes(output_bytes)
    log(f"Saved batch output JSONL: {output_jsonl}")

    parsed = provider.parse_grouped_output(output_bytes, manifest_path)
    if not parsed.translations_by_hash and not parsed.malformed_groups:
        raise RuntimeError("No grouped translations could be parsed from batch output.")

    stored = 0
    suspicious_items: list[PendingNode] = []
    for item in pending:
        translated = parsed.translations_by_hash.get(stable_text_hash(item.core_text))
        if translated is not None and config.target_lang.lower() == "es":
            translated = normalize_spanish_quotes(translated)
        if translated is not None:
            issue = translation_issue(item, translated, config.target_lang)
            salvage_note = None
            if issue is not None and issue.startswith("inline markers") and format_retry_attempts_left == 0:
                salvaged = salvage_markers(item.core_text, translated)
                if translation_issue(item, salvaged, config.target_lang) is None:
                    translated, issue = salvaged, None
                    salvage_note = "formatting markers were repositioned automatically; check italics/links"
                    log(f"Repositioned inline markers automatically in {item.rel_path}#{item.node_index}")
            if issue is not None:
                suspicious_items.append(
                    PendingNode(
                        rel_path=item.rel_path,
                        node_index=item.node_index,
                        core_text=item.core_text,
                        current_translation=translated,
                        context_hint=item.context_hint,
                    )
                )
                log(
                    f"Rejected suspicious {mode_label} fragment {item.rel_path}#{item.node_index}: {issue}"
                )
                continue
            cache.set(item.core_text, translated)
            cache.set_note(
                item.core_text,
                salvage_note or parsed.doubts_by_hash.get(stable_text_hash(item.core_text)),
            )
            stored += 1

    cache.save()
    log(f"Stored translations in cache: {stored}")

    if parsed.malformed_groups:
        malformed_ids = sorted(parsed.malformed_groups)
        sample_details = ", ".join(
            f"{group_id} ({parsed.malformed_groups[group_id]})" for group_id in malformed_ids[:3]
        )
        if len(malformed_ids) > 3:
            sample_details += ", ..."
        log(
            f"Detected {len(malformed_ids)} malformed {mode_label} group(s): {sample_details}"
        )

    retry_pending = malformed_group_items(groups, sorted(parsed.malformed_groups))
    if suspicious_items:
        log(f"Detected {len(suspicious_items)} suspicious {mode_label} fragment(s) that will be retried.")
        retry_pending.extend(suspicious_items)

    retry_pending = dedupe_pending_nodes(retry_pending)
    if retry_pending:
        if format_retry_attempts_left > 0:
            log(
                f"Retrying {len(retry_pending)} fragment(s) from malformed or suspicious {mode_label} output "
                f"as single-item requests."
            )
            retry_request_path = make_round_artifact_path(
                request_path,
                f"format-retry-{FORMAT_RETRY_PASSES - format_retry_attempts_left + 1}",
            )
            retry_manifest_path = make_round_artifact_path(
                manifest_path,
                f"format-retry-{FORMAT_RETRY_PASSES - format_retry_attempts_left + 1}",
            )
            retry_prompt_mode = "repair" if any(item.current_translation for item in retry_pending) else config.prompt_mode
            retry_config = build_round_config(
                base_config=config,
                request_path=retry_request_path,
                manifest_path=retry_manifest_path,
                prompt_mode=retry_prompt_mode,
                repair_file=config.repair_file if retry_prompt_mode == "repair" else None,
            )
            retry_groups = build_grouped_requests(
                retry_pending,
                max_items_per_request=1,
                max_chars_per_request=max(1, config.max_chars_per_request),
            )
            retry_batch, retry_stored, retry_artifacts = execute_batch_round(
                pending=retry_pending,
                config=retry_config,
                cache=cache,
                provider=provider,
                artifact_provider=artifact_provider,
                request_path=retry_request_path,
                manifest_path=retry_manifest_path,
                groups=retry_groups,
                resume_batch_id=None,
                mode_label=f"{mode_label} retry",
                format_retry_attempts_left=format_retry_attempts_left - 1,
            )
            cleanup_artifacts(retry_artifacts)
            stored += retry_stored
            batch = retry_batch if retry_stored == len(retry_pending) else batch
        else:
            log(f"Automatic malformed-output retries exhausted for {mode_label}.")

    return batch, stored, artifacts
