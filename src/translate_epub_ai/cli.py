"""CLI entry point for the EPUB translator."""

import argparse
import json
import os
import tempfile
from pathlib import Path

from .cache import ProgressCache
from .epub import apply_translations, collect_pending_nodes, extract_epub, rebuild_epub
from .models import TranslationConfig
from .openai_batch import (
    OpenAIClient,
    build_batch_files,
    build_grouped_requests,
    parse_grouped_output,
    wait_for_batch,
)
from .utils import log, sanitized_model_name, stable_text_hash


def make_output_name(input_path: Path, target_lang: str) -> Path:
    return input_path.with_name(f"{input_path.stem}_{target_lang.upper()}.epub")


def make_cache_name(input_path: Path, target_lang: str, model: str) -> Path:
    return input_path.with_name(
        f"{input_path.stem}_{target_lang.upper()}_batch_{sanitized_model_name(model)}.progress.json"
    )


def make_jsonl_name(input_path: Path, target_lang: str, model: str) -> Path:
    return input_path.with_name(
        f"{input_path.stem}_{target_lang.upper()}_{sanitized_model_name(model)}.batch.jsonl"
    )


def make_manifest_name(input_path: Path, target_lang: str, model: str) -> Path:
    return input_path.with_name(
        f"{input_path.stem}_{target_lang.upper()}_{sanitized_model_name(model)}.manifest.json"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Translate an EPUB using the OpenAI Batch API while preserving ebook structure."
    )
    parser.add_argument("input", type=Path, help="Input EPUB file")
    parser.add_argument("--to", default="es", help="Target language, for example: es")
    parser.add_argument("--from-lang", default=None, help="Optional source language, for example: en")
    parser.add_argument("--model", default="gpt-4.1-mini", help="OpenAI model to use for batch translation")
    parser.add_argument("--output", type=Path, default=None, help="Output EPUB path")
    parser.add_argument("--cache-file", type=Path, default=None, help="Progress cache JSON path")
    parser.add_argument("--jsonl-file", type=Path, default=None, help="Prepared batch JSONL path")
    parser.add_argument("--manifest-file", type=Path, default=None, help="Prepared batch manifest path")
    parser.add_argument("--prompt-file", type=Path, default=None, help="Custom prompt template path")
    parser.add_argument("--literal", action="store_true", help="Prefer a more literal translation style")
    parser.add_argument("--natural", action="store_true", help="Prefer a more literary translation style (default)")
    parser.add_argument("--completion-window", default="24h", choices=["24h"])
    parser.add_argument("--poll-seconds", type=int, default=60, help="Seconds between batch status checks")
    parser.add_argument("--max-items-per-request", type=int, default=12)
    parser.add_argument("--max-chars-per-request", type=int, default=5500)
    parser.add_argument("--prepare-only", action="store_true", help="Only create batch artifacts locally")
    parser.add_argument("--resume-batch-id", default=None, help="Resume an already created OpenAI batch")
    return parser.parse_args()


def build_config(args: argparse.Namespace) -> TranslationConfig:
    natural = not args.literal or args.natural
    return TranslationConfig(
        input_epub=args.input,
        target_lang=args.to,
        model=args.model,
        output_epub=args.output or make_output_name(args.input, args.to),
        cache_file=args.cache_file or make_cache_name(args.input, args.to, args.model),
        jsonl_file=args.jsonl_file or make_jsonl_name(args.input, args.to, args.model),
        manifest_file=args.manifest_file or make_manifest_name(args.input, args.to, args.model),
        source_lang=args.from_lang,
        natural=natural,
        prompt_file=args.prompt_file,
        completion_window=args.completion_window,
        poll_seconds=args.poll_seconds,
        max_items_per_request=args.max_items_per_request,
        max_chars_per_request=args.max_chars_per_request,
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


def run(config: TranslationConfig) -> int:
    validate_config(config)

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key and not config.prepare_only:
        raise RuntimeError("OPENAI_API_KEY is not set.")

    cache = ProgressCache(config.cache_file)
    client = OpenAIClient(api_key) if api_key else None

    with tempfile.TemporaryDirectory(prefix="epub_translate_batch_") as tmp_dir:
        workdir = Path(tmp_dir)
        log(f"Using progress cache: {config.cache_file}")
        log(f"Extracting EPUB: {config.input_epub}")
        extract_epub(config.input_epub, workdir)

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

        groups = build_grouped_requests(
            pending,
            max_items_per_request=config.max_items_per_request,
            max_chars_per_request=config.max_chars_per_request,
        )
        build_batch_files(
            jsonl_path=config.jsonl_file,
            manifest_path=config.manifest_file,
            groups=groups,
            target_lang=config.target_lang,
            model=config.model,
            source_lang=config.source_lang,
            natural=config.natural,
            prompt_file=config.prompt_file,
        )
        log(f"Optimized batch JSONL created: {config.jsonl_file}")
        log(f"Manifest created: {config.manifest_file}")
        log(f"Grouped requests: {len(groups)}")
        log(f"Compression ratio: {len(pending) / len(groups):.1f} text nodes per batch request")

        if config.prepare_only:
            log("Prepare-only mode enabled. No upload or batch creation performed.")
            return 0

        assert client is not None
        if config.resume_batch_id:
            batch_id = config.resume_batch_id
            log(f"Resuming existing batch: {batch_id}")
        else:
            input_file_id = client.upload_file(config.jsonl_file, purpose="batch")
            log(f"Uploaded JSONL file. file_id={input_file_id}")
            batch_id = client.create_batch(
                input_file_id=input_file_id,
                metadata={
                    "source_epub": config.input_epub.name,
                    "target_lang": config.target_lang,
                    "model": config.model,
                    "prompt_style": "natural" if config.natural else "literal",
                },
                completion_window=config.completion_window,
            )
            log(f"Created batch. batch_id={batch_id}")
            cache.set_meta("last_batch_id", batch_id)
            cache.save()

        batch = wait_for_batch(client, batch_id, config.poll_seconds)
        if batch.get("status") != "completed":
            log("Batch did not complete successfully.")
            log(json.dumps(batch, ensure_ascii=False, indent=2))
            return 2

        output_file_id = batch.get("output_file_id")
        if not output_file_id:
            raise RuntimeError("Batch completed but output_file_id is missing.")

        output_bytes = client.download_file_content(output_file_id)
        output_jsonl = config.jsonl_file.with_suffix(".output.jsonl")
        output_jsonl.write_bytes(output_bytes)
        log(f"Saved batch output JSONL: {output_jsonl}")

        parsed = parse_grouped_output(output_bytes, config.manifest_file)
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

        translated_nodes = apply_translations(workdir, cache)
        rebuild_epub(workdir, config.output_epub)

        entries, approx_bytes = cache.stats()
        log(f"Done. Output: {config.output_epub}")
        log(f"Translated nodes applied into EPUB: {translated_nodes}")
        log(f"Cache entries: {entries} (~{approx_bytes} bytes)")
        log(f"Last batch id: {batch_id}")
        return 0


def main() -> int:
    try:
        return run(build_config(parse_args()))
    except (FileNotFoundError, ValueError) as error:
        log(str(error))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
