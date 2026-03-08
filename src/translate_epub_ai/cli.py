"""CLI entry point for the EPUB translator."""

import argparse
import json
import os
import tempfile
from pathlib import Path

from .batch_providers import build_grouped_requests, create_provider
from .cache import ProgressCache
from .epub import apply_translations, collect_pending_nodes, extract_epub, rebuild_epub
from .models import PendingNode, TranslationConfig
from .utils import log
from .workflow import (
    build_round_config,
    execute_batch_round,
    find_auto_repair_candidates,
    load_repair_items,
    make_cache_name,
    make_jsonl_name,
    make_manifest_name,
    make_output_name,
    make_round_artifact_path,
    required_api_key_env,
)


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
    parser.add_argument(
        "--repair-file",
        type=Path,
        default=None,
        help="JSON file with specific source fragments to retranslate and repair selectively",
    )
    parser.add_argument(
        "--review-passes",
        type=int,
        default=1,
        help="Editorial review passes over translated blocks before the final EPUB is built",
    )
    parser.add_argument(
        "--auto-repair-rounds",
        type=int,
        default=1,
        help="Automatic selective repair passes after the main translation batch",
    )
    parser.add_argument("--literal", action="store_true", help="Prefer a more literal translation style")
    parser.add_argument("--natural", action="store_true", help="Prefer a more literary translation style (default)")
    parser.add_argument("--completion-window", default="24h", choices=["24h"])
    parser.add_argument("--poll-seconds", type=int, default=60, help="Seconds between batch status checks")
    parser.add_argument("--max-items-per-request", type=int, default=12)
    parser.add_argument("--max-chars-per-request", type=int, default=5500)
    parser.add_argument(
        "--max-output-tokens",
        type=int,
        default=4096,
        help="Max output tokens for providers that require it, such as Anthropic",
    )
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


def load_pending_nodes(workdir: Path, config: TranslationConfig, cache: ProgressCache) -> list[PendingNode]:
    if config.repair_file is not None:
        pending = load_repair_items(config.repair_file, cache)
        cache_hits = sum(1 for item in pending if item.current_translation)
        log(f"Selective repair mode enabled: {config.repair_file}")
        log(f"Repair fragments queued: {len(pending)}")
        log(f"Existing cached translations available for review: {cache_hits}")
        return pending

    pending, cache_hits, skipped = collect_pending_nodes(workdir, cache)
    log(f"Pending untranslated text nodes: {len(pending)}")
    log(f"Immediate cache hits: {cache_hits}")
    log(f"Skipped navigation/package files: {len(skipped)}")
    for rel_path in skipped[:10]:
        log(f"  skipped: {rel_path}")
    return pending


def build_review_candidates(pending: list[PendingNode], cache: ProgressCache) -> list[PendingNode]:
    return [
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


def run_follow_up_rounds(
    *,
    pending: list[PendingNode],
    base_config: TranslationConfig,
    cache: ProgressCache,
    provider,
    artifact_provider,
) -> tuple[int, int]:
    review_applied = 0
    auto_repair_applied = 0

    if base_config.repair_file is None and base_config.review_passes > 0:
        review_candidates = build_review_candidates(pending, cache)
        for round_number in range(1, base_config.review_passes + 1):
            if not review_candidates:
                break
            log(f"Review pass {round_number}: reviewing {len(review_candidates)} translated fragments.")
            review_request_path = make_round_artifact_path(base_config.jsonl_file, f"review-{round_number}")
            review_manifest_path = make_round_artifact_path(base_config.manifest_file, f"review-{round_number}")
            review_config = build_round_config(
                base_config=base_config,
                request_path=review_request_path,
                manifest_path=review_manifest_path,
                prompt_mode="review",
                repair_file=None,
            )
            review_groups = build_grouped_requests(
                review_candidates,
                base_config.max_items_per_request,
                base_config.max_chars_per_request,
            )
            review_batch, review_stored = execute_batch_round(
                pending=review_candidates,
                config=review_config,
                cache=cache,
                provider=provider,
                artifact_provider=artifact_provider,
                request_path=review_request_path,
                manifest_path=review_manifest_path,
                groups=review_groups,
                resume_batch_id=None,
                mode_label="review",
            )
            review_applied += review_stored
            if review_stored != len(review_candidates):
                log("Review pass could not polish every fragment, but successful revisions were cached.")
                log(json.dumps(review_batch, ensure_ascii=False, indent=2))
                break

    if base_config.repair_file is None and base_config.auto_repair_rounds > 0:
        cached_translations = {stable_text_hash(item.core_text): cache.get(item.core_text) for item in pending}
        repair_candidates = find_auto_repair_candidates(pending, cached_translations, base_config.target_lang)
        for round_number in range(1, base_config.auto_repair_rounds + 1):
            if not repair_candidates:
                break
            log(f"Auto-repair round {round_number}: {len(repair_candidates)} suspicious fragments detected.")
            repair_request_path = make_round_artifact_path(base_config.jsonl_file, f"repair-{round_number}")
            repair_manifest_path = make_round_artifact_path(base_config.manifest_file, f"repair-{round_number}")
            repair_config = build_round_config(
                base_config=base_config,
                request_path=repair_request_path,
                manifest_path=repair_manifest_path,
                prompt_mode="repair",
                repair_file=Path(f"auto-repair-round-{round_number}.json"),
            )
            repair_groups = build_grouped_requests(
                repair_candidates,
                base_config.max_items_per_request,
                base_config.max_chars_per_request,
            )
            repair_batch, repair_stored = execute_batch_round(
                pending=repair_candidates,
                config=repair_config,
                cache=cache,
                provider=provider,
                artifact_provider=artifact_provider,
                request_path=repair_request_path,
                manifest_path=repair_manifest_path,
                groups=repair_groups,
                resume_batch_id=None,
                mode_label="repair",
            )
            auto_repair_applied += repair_stored
            if repair_stored != len(repair_candidates):
                log("Auto-repair could not fully repair every suspicious fragment, but successful fixes were cached.")
                log(json.dumps(repair_batch, ensure_ascii=False, indent=2))
                break
            cached_translations = {stable_text_hash(item.core_text): cache.get(item.core_text) for item in pending}
            repair_candidates = find_auto_repair_candidates(pending, cached_translations, base_config.target_lang)

    return review_applied, auto_repair_applied


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

        pending = load_pending_nodes(workdir, config, cache)
        if not pending:
            translated_nodes = apply_translations(workdir, cache)
            rebuild_epub(workdir, config.output_epub)
            log(f"Done. Output: {config.output_epub}")
            log(f"Translated nodes applied from cache: {translated_nodes}")
            return 0

        initial_groups = build_grouped_requests(
            pending,
            config.max_items_per_request,
            config.max_chars_per_request,
        )
        batch, stored = execute_batch_round(
            pending=pending,
            config=config,
            cache=cache,
            provider=provider,
            artifact_provider=artifact_provider,
            request_path=config.jsonl_file,
            manifest_path=config.manifest_file,
            groups=initial_groups,
            resume_batch_id=config.resume_batch_id,
            mode_label=config.prompt_mode,
        )

        if config.prepare_only:
            log("Prepare-only mode enabled. No upload or batch creation performed.")
            return 0

        assert provider is not None
        if stored != len(pending):
            log("Batch results were only partially usable. Cached successful translations so they will not be requested again.")
            log(json.dumps(batch, ensure_ascii=False, indent=2))
            return 2
        if not provider.is_success_status(batch):
            log("Batch did not complete fully successfully, but parsed results were cached.")
            log(json.dumps(batch, ensure_ascii=False, indent=2))
            return 2

        review_applied, auto_repair_applied = run_follow_up_rounds(
            pending=pending,
            base_config=config,
            cache=cache,
            provider=provider,
            artifact_provider=artifact_provider,
        )

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
