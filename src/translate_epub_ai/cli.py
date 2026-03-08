"""CLI entry point for the EPUB translator."""

import argparse
import json
import os
import re
import tempfile
from pathlib import Path

from .batch_providers import build_grouped_requests, create_provider
from .cache import ProgressCache
from .epub import apply_translations, collect_pending_nodes, extract_epub, rebuild_epub
from .models import PendingNode, TranslationConfig
from .utils import log, sanitized_model_name, stable_text_hash


def make_output_name(input_path: Path, target_lang: str) -> Path:
    return input_path.with_name(f"{input_path.stem}_{target_lang.upper()}.epub")


def make_cache_name(input_path: Path, provider: str, target_lang: str, model: str) -> Path:
    if provider == "openai":
        return input_path.with_name(
            f"{input_path.stem}_{target_lang.upper()}_batch_{sanitized_model_name(model)}.progress.json"
        )
    return input_path.with_name(
        f"{input_path.stem}_{target_lang.upper()}_{provider}_{sanitized_model_name(model)}.progress.json"
    )


def make_jsonl_name(input_path: Path, provider: str, target_lang: str, model: str) -> Path:
    if provider == "openai":
        return input_path.with_name(
            f"{input_path.stem}_{target_lang.upper()}_{sanitized_model_name(model)}.batch.jsonl"
        )
    return input_path.with_name(
        f"{input_path.stem}_{target_lang.upper()}_{provider}_{sanitized_model_name(model)}.batch.jsonl"
    )


def make_manifest_name(input_path: Path, provider: str, target_lang: str, model: str) -> Path:
    if provider == "openai":
        return input_path.with_name(
            f"{input_path.stem}_{target_lang.upper()}_{sanitized_model_name(model)}.manifest.json"
        )
    return input_path.with_name(
        f"{input_path.stem}_{target_lang.upper()}_{provider}_{sanitized_model_name(model)}.manifest.json"
    )


def required_api_key_env(provider: str) -> str:
    if provider == "anthropic":
        return "ANTHROPIC_API_KEY"
    return "OPENAI_API_KEY"


def load_repair_items(repair_file: Path, cache: ProgressCache) -> list:
    raw_items = json.loads(repair_file.read_text(encoding="utf-8"))
    if not isinstance(raw_items, list) or not raw_items:
        raise ValueError("Repair file must be a non-empty JSON array.")

    items = []
    for index, raw in enumerate(raw_items):
        if isinstance(raw, str):
            source_text = raw
            current_translation = cache.get(source_text)
            context_hint = ""
        elif isinstance(raw, dict):
            source_text = raw.get("source_text")
            if not source_text:
                raise ValueError(f"Repair item {index + 1} is missing 'source_text'.")
            current_translation = raw.get("current_translation")
            if current_translation is None:
                current_translation = cache.get(source_text)
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
    return any(token in text for token in ("Â", "Ã", "�"))


def has_suspicious_repetition(text: str) -> bool:
    return re.search(r"([^\s])\1{4,}", text) is not None


def looks_unbalanced(text: str) -> bool:
    pairs = [("(", ")"), ("[", "]"), ("{", "}"), ("“", "”"), ('"', '"')]
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


def find_auto_repair_candidates(pending: list[PendingNode], parsed: dict[str, str], target_lang: str) -> list[PendingNode]:
    candidates = []
    for item in pending:
        translated = parsed.get(stable_text_hash(item.core_text))
        if translated is None:
            continue
        if should_auto_repair(item.core_text, translated, target_lang):
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


def execute_batch_round(
    *,
    pending: list[PendingNode],
    config: TranslationConfig,
    cache: ProgressCache,
    provider,
    artifact_provider,
    request_path: Path,
    manifest_path: Path,
    resume_batch_id: str | None,
    mode_label: str,
) -> tuple[dict, int, Path]:
    groups = build_grouped_requests(
        pending,
        max_items_per_request=config.max_items_per_request,
        max_chars_per_request=config.max_chars_per_request,
    )
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
        return {}, 0, request_path

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
        return batch, 0, request_path

    output_jsonl = request_path.with_suffix(".output.jsonl")
    output_jsonl.write_bytes(output_bytes)
    log(f"Saved batch output JSONL: {output_jsonl}")

    parsed = provider.parse_grouped_output(output_bytes, manifest_path)
    if not parsed:
        raise RuntimeError("No grouped translations could be parsed from batch output.")

    stored = 0
    for item in pending:
        translated = parsed.get(stable_text_hash(item.core_text))
        if translated is None:
            continue
        cache.set(item.core_text, translated)
        stored += 1

    cache.save()
    log(f"Stored translations in cache: {stored}")
    return batch, stored, request_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Translate an EPUB using a batch API while preserving ebook structure."
    )
    parser.add_argument("input", type=Path, help="Input EPUB file")
    parser.add_argument("--provider", choices=["openai", "anthropic"], default="openai")
    parser.add_argument("--to", default="es", help="Target language, for example: es")
    parser.add_argument("--from-lang", default=None, help="Optional source language, for example: en")
    parser.add_argument("--model", default="gpt-4.1-mini", help="Model to use for batch translation")
    parser.add_argument("--output", type=Path, default=None, help="Output EPUB path")
    parser.add_argument("--cache-file", type=Path, default=None, help="Progress cache JSON path")
    parser.add_argument("--jsonl-file", type=Path, default=None, help="Prepared batch JSONL path")
    parser.add_argument("--manifest-file", type=Path, default=None, help="Prepared batch manifest path")
    parser.add_argument("--prompt-file", type=Path, default=None, help="Custom prompt template path")
    parser.add_argument("--repair-file", type=Path, default=None, help="JSON file with specific source fragments to retranslate and repair selectively")
    parser.add_argument("--review-passes", type=int, default=1, help="Editorial review passes over translated blocks before the final EPUB is built")
    parser.add_argument("--auto-repair-rounds", type=int, default=1, help="Automatic selective repair passes after the main translation batch")
    parser.add_argument("--literal", action="store_true", help="Prefer a more literal translation style")
    parser.add_argument("--natural", action="store_true", help="Prefer a more literary translation style (default)")
    parser.add_argument("--completion-window", default="24h", choices=["24h"])
    parser.add_argument("--poll-seconds", type=int, default=60, help="Seconds between batch status checks")
    parser.add_argument("--max-items-per-request", type=int, default=12)
    parser.add_argument("--max-chars-per-request", type=int, default=5500)
    parser.add_argument("--max-output-tokens", type=int, default=4096, help="Max output tokens for providers that require it, such as Anthropic")
    parser.add_argument("--prepare-only", action="store_true", help="Only create batch artifacts locally")
    parser.add_argument("--resume-batch-id", default=None, help="Resume an already created batch")
    return parser.parse_args()


def build_config(args: argparse.Namespace) -> TranslationConfig:
    natural = not args.literal or args.natural
    return TranslationConfig(
        input_epub=args.input,
        provider=args.provider,
        target_lang=args.to,
        model=args.model,
        output_epub=args.output or make_output_name(args.input, args.to),
        cache_file=args.cache_file or make_cache_name(args.input, args.provider, args.to, args.model),
        jsonl_file=args.jsonl_file or make_jsonl_name(args.input, args.provider, args.to, args.model),
        manifest_file=args.manifest_file or make_manifest_name(args.input, args.provider, args.to, args.model),
        source_lang=args.from_lang,
        natural=natural,
        prompt_mode="repair" if args.repair_file else "translate",
        prompt_file=args.prompt_file,
        repair_file=args.repair_file,
        auto_repair_rounds=max(0, args.auto_repair_rounds),
        review_passes=max(0, args.review_passes),
        completion_window=args.completion_window,
        poll_seconds=args.poll_seconds,
        max_items_per_request=args.max_items_per_request,
        max_chars_per_request=args.max_chars_per_request,
        max_output_tokens=args.max_output_tokens,
        prepare_only=args.prepare_only,
        resume_batch_id=args.resume_batch_id,
    )


def validate_config(config: TranslationConfig) -> None:
    if not config.input_epub.exists():
        raise FileNotFoundError(f"Input file not found: {config.input_epub}")
    if config.input_epub.suffix.lower() != ".epub":
        raise ValueError("Input file must be an .epub")
    if config.prompt_file is not None and not config.prompt_file.exists():
        raise FileNotFoundError(f"Prompt template not found: {config.prompt_file}")
    if config.repair_file is not None and not config.repair_file.exists():
        raise FileNotFoundError(f"Repair file not found: {config.repair_file}")


def run(config: TranslationConfig) -> int:
    validate_config(config)

    api_key_env = required_api_key_env(config.provider)
    api_key = os.getenv(api_key_env)
    if not api_key and not config.prepare_only:
        raise RuntimeError(f"{api_key_env} is not set.")

    cache = ProgressCache(config.cache_file)
    provider = create_provider(config.provider, api_key) if api_key else None
    artifact_provider = provider or create_provider(config.provider, "prepare-only")

    with tempfile.TemporaryDirectory(prefix="epub_translate_batch_") as tmp_dir:
        workdir = Path(tmp_dir)
        log(f"Using progress cache: {config.cache_file}")
        log(f"Extracting EPUB: {config.input_epub}")
        extract_epub(config.input_epub, workdir)

        if config.repair_file is not None:
            pending = load_repair_items(config.repair_file, cache)
            cache_hits = sum(1 for item in pending if item.current_translation)
            skipped = []
            log(f"Selective repair mode enabled: {config.repair_file}")
            log(f"Repair fragments queued: {len(pending)}")
            log(f"Existing cached translations available for review: {cache_hits}")
        else:
            pending, cache_hits, skipped = collect_pending_nodes(workdir, cache)
            log(f"Pending untranslated text nodes: {len(pending)}")
            log(f"Immediate cache hits: {cache_hits}")
            log(f"Skipped navigation/package files: {len(skipped)}")
            for rel_path in skipped[:10]:
                log(f"  skipped: {rel_path}")

        if not pending:
            translated_nodes = apply_translations(workdir, cache)
            rebuild_epub(workdir, config.output_epub)
            log(f"Done. Output: {config.output_epub}")
            log(f"Translated nodes applied from cache: {translated_nodes}")
            return 0

        batch, stored, request_path = execute_batch_round(
            pending=pending,
            config=config,
            cache=cache,
            provider=provider,
            artifact_provider=artifact_provider,
            request_path=config.jsonl_file,
            manifest_path=config.manifest_file,
            resume_batch_id=config.resume_batch_id,
            mode_label="repair" if config.repair_file else "translate",
        )

        if config.prepare_only:
            log("Prepare-only mode enabled. No upload or batch creation performed.")
            return 0

        assert provider is not None
        if stored != len(pending):
            log(
                "Batch results were only partially usable. Cached successful translations so they will not be requested again."
            )
            log(json.dumps(batch, ensure_ascii=False, indent=2))
            return 2

        auto_repair_applied = 0
        if config.repair_file is None and config.auto_repair_rounds > 0:
            parsed_from_cache = {stable_text_hash(item.core_text): cache.get(item.core_text) for item in pending}
            repair_candidates = find_auto_repair_candidates(pending, parsed_from_cache, config.target_lang)
            for round_number in range(1, config.auto_repair_rounds + 1):
                if not repair_candidates:
                    break
                log(f"Auto-repair round {round_number}: {len(repair_candidates)} suspicious fragments detected.")
                repair_request_path = make_round_artifact_path(config.jsonl_file, f"repair-{round_number}")
                repair_manifest_path = make_round_artifact_path(config.manifest_file, f"repair-{round_number}")
                repair_config = TranslationConfig(
                    input_epub=config.input_epub,
                    provider=config.provider,
                    target_lang=config.target_lang,
                    model=config.model,
                    output_epub=config.output_epub,
                    cache_file=config.cache_file,
                    jsonl_file=repair_request_path,
                    manifest_file=repair_manifest_path,
                    source_lang=config.source_lang,
                    natural=config.natural,
                    prompt_mode="repair",
                    prompt_file=config.prompt_file,
                    repair_file=Path(f"auto-repair-round-{round_number}.json"),
                    auto_repair_rounds=0,
                    review_passes=0,
                    completion_window=config.completion_window,
                    poll_seconds=config.poll_seconds,
                    max_items_per_request=config.max_items_per_request,
                    max_chars_per_request=config.max_chars_per_request,
                    max_output_tokens=config.max_output_tokens,
                    prepare_only=False,
                    resume_batch_id=None,
                )
                repair_batch, repair_stored, _ = execute_batch_round(
                    pending=repair_candidates,
                    config=repair_config,
                    cache=cache,
                    provider=provider,
                    artifact_provider=artifact_provider,
                    request_path=repair_request_path,
                    manifest_path=repair_manifest_path,
                    resume_batch_id=None,
                    mode_label="repair",
                )
                auto_repair_applied += repair_stored
                if repair_stored != len(repair_candidates):
                    log("Auto-repair could not fully repair every suspicious fragment, but successful fixes were cached.")
                    log(json.dumps(repair_batch, ensure_ascii=False, indent=2))
                    break
                refreshed = []
                for item in repair_candidates:
                    updated = cache.get(item.core_text)
                    if updated and should_auto_repair(item.core_text, updated, config.target_lang):
                        refreshed.append(
                            PendingNode(
                                rel_path=item.rel_path,
                                node_index=item.node_index,
                                core_text=item.core_text,
                                current_translation=updated,
                                context_hint=item.context_hint,
                            )
                        )
                repair_candidates = refreshed

        review_applied = 0
        if config.repair_file is None and config.review_passes > 0:
            review_candidates = [
                PendingNode(
                    rel_path=item.rel_path,
                    node_index=item.node_index,
                    core_text=item.core_text,
                    current_translation=cache.get(item.core_text),
                    context_hint=f"Nearby file context: {item.rel_path}",
                )
                for item in pending
                if cache.get(item.core_text)
            ]
            for round_number in range(1, config.review_passes + 1):
                if not review_candidates:
                    break
                log(f"Review pass {round_number}: reviewing {len(review_candidates)} translated fragments.")
                review_request_path = make_round_artifact_path(config.jsonl_file, f"review-{round_number}")
                review_manifest_path = make_round_artifact_path(config.manifest_file, f"review-{round_number}")
                review_config = TranslationConfig(
                    input_epub=config.input_epub,
                    provider=config.provider,
                    target_lang=config.target_lang,
                    model=config.model,
                    output_epub=config.output_epub,
                    cache_file=config.cache_file,
                    jsonl_file=review_request_path,
                    manifest_file=review_manifest_path,
                    source_lang=config.source_lang,
                    natural=config.natural,
                    prompt_mode="review",
                    prompt_file=config.prompt_file,
                    repair_file=None,
                    auto_repair_rounds=0,
                    review_passes=0,
                    completion_window=config.completion_window,
                    poll_seconds=config.poll_seconds,
                    max_items_per_request=config.max_items_per_request,
                    max_chars_per_request=config.max_chars_per_request,
                    max_output_tokens=config.max_output_tokens,
                    prepare_only=False,
                    resume_batch_id=None,
                )
                review_batch, review_stored, _ = execute_batch_round(
                    pending=review_candidates,
                    config=review_config,
                    cache=cache,
                    provider=provider,
                    artifact_provider=artifact_provider,
                    request_path=review_request_path,
                    manifest_path=review_manifest_path,
                    resume_batch_id=None,
                    mode_label="review",
                )
                review_applied += review_stored
                if review_stored != len(review_candidates):
                    log("Review pass could not polish every fragment, but successful revisions were cached.")
                    log(json.dumps(review_batch, ensure_ascii=False, indent=2))
                    break

        if not provider.is_success_status(batch):
            log("Batch did not complete fully successfully, but parsed results were cached.")
            log(json.dumps(batch, ensure_ascii=False, indent=2))
            return 2

        translated_nodes = apply_translations(workdir, cache)
        rebuild_epub(workdir, config.output_epub)

        entries, approx_bytes = cache.stats()
        log(f"Done. Output: {config.output_epub}")
        if config.repair_file:
            log(f"Repaired fragments applied into EPUB: {translated_nodes}")
        else:
            log(f"Translated nodes applied into EPUB: {translated_nodes}")
            if review_applied:
                log(f"Review-polished fragments: {review_applied}")
            if auto_repair_applied:
                log(f"Auto-repaired fragments: {auto_repair_applied}")
        log(f"Cache entries: {entries} (~{approx_bytes} bytes)")
        last_batch_id = cache.data.get("meta", {}).get("last_batch_id")
        if last_batch_id:
            log(f"Last batch id: {last_batch_id}")
        return 0


def main() -> int:
    try:
        return run(build_config(parse_args()))
    except (FileNotFoundError, ValueError) as error:
        log(str(error))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
