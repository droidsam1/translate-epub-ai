import json
import sys
import tempfile
import unittest
from importlib import import_module
from pathlib import Path

from translate_epub_ai.batch_providers import AnthropicBatchProvider, OpenAIBatchProvider, build_manifest
from translate_epub_ai.cache import ProgressCache
from translate_epub_ai.cli import build_config, parse_args
from translate_epub_ai.models import PendingNode, TranslationConfig
from translate_epub_ai.workflow import (
    cleanup_artifacts,
    contains_section_leakage,
    execute_batch_round,
    load_repair_items,
    required_api_key_env,
    should_auto_repair,
)


class ProviderTests(unittest.TestCase):
    def test_importing_package_does_not_preload_cli_module(self) -> None:
        sys.modules.pop("translate_epub_ai", None)
        sys.modules.pop("translate_epub_ai.cli", None)

        import_module("translate_epub_ai")

        self.assertNotIn("translate_epub_ai.cli", sys.modules)

    def test_openai_remains_default_provider(self) -> None:
        namespace = parse_args_for_test(["book.epub"])
        config = build_config(namespace)
        self.assertEqual("openai", config.provider)
        self.assertEqual("translate", config.prompt_mode)
        self.assertEqual(1, config.review_passes)
        self.assertEqual("OPENAI_API_KEY", required_api_key_env(config.provider))

    def test_anthropic_uses_own_api_key_env(self) -> None:
        self.assertEqual("ANTHROPIC_API_KEY", required_api_key_env("anthropic"))

    def test_anthropic_output_is_parsed_into_manifest_hashes(self) -> None:
        provider = AnthropicBatchProvider("test-key")
        with tempfile.TemporaryDirectory() as tmp_dir:
            manifest_path = Path(tmp_dir) / "manifest.json"
            manifest = {
                "group_000001": [
                    {
                        "rel_path": "chapter.xhtml",
                        "node_index": 0,
                        "hash": "abc123",
                        "core_text": "Hello world",
                    }
                ]
            }
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            output_line = {
                "custom_id": "group_000001",
                "result": {
                    "type": "succeeded",
                    "message": {
                        "content": [{"type": "text", "text": '["Hola mundo"]'}]
                    },
                },
            }
            parsed = provider.parse_grouped_output(
                output_bytes=(json.dumps(output_line) + "\n").encode("utf-8"),
                manifest_path=manifest_path,
            )
            self.assertEqual({"abc123": "Hola mundo"}, parsed.translations_by_hash)
            self.assertEqual({}, parsed.malformed_groups)

    def test_openai_parser_reports_malformed_group_sizes(self) -> None:
        provider = OpenAIBatchProvider("test-key")
        with tempfile.TemporaryDirectory() as tmp_dir:
            manifest_path = Path(tmp_dir) / "manifest.json"
            manifest = {
                "group_000001": [
                    {"rel_path": "chapter.xhtml", "node_index": 0, "hash": "abc123", "core_text": "One"},
                    {"rel_path": "chapter.xhtml", "node_index": 1, "hash": "def456", "core_text": "Two"},
                ]
            }
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            output_line = {
                "custom_id": "group_000001",
                "response": {"body": {"output_text": '["Uno"]'}},
            }
            parsed = provider.parse_grouped_output(
                output_bytes=(json.dumps(output_line) + "\n").encode("utf-8"),
                manifest_path=manifest_path,
            )

            self.assertEqual({}, parsed.translations_by_hash)
            self.assertEqual({"group_000001": "expected 2 items, got 1"}, parsed.malformed_groups)

    def test_openai_parser_accepts_object_output_with_ids(self) -> None:
        provider = OpenAIBatchProvider("test-key")
        with tempfile.TemporaryDirectory() as tmp_dir:
            manifest_path = Path(tmp_dir) / "manifest.json"
            manifest = {
                "group_000001": [
                    {
                        "item_id": "group_000001_item_001",
                        "rel_path": "chapter.xhtml",
                        "node_index": 0,
                        "hash": "abc123",
                        "core_text": "One",
                    }
                ]
            }
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            output_line = {
                "custom_id": "group_000001",
                "response": {
                    "body": {
                        "output_text": '[{"id":"group_000001_item_001","translation":"Uno"}]'
                    }
                },
            }
            parsed = provider.parse_grouped_output(
                output_bytes=(json.dumps(output_line) + "\n").encode("utf-8"),
                manifest_path=manifest_path,
            )

            self.assertEqual({"abc123": "Uno"}, parsed.translations_by_hash)
            self.assertEqual({}, parsed.malformed_groups)

    def test_load_repair_items_uses_cache_when_current_translation_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            cache = ProgressCache(tmp_path / "cache.json")
            cache.set("Source text", "Cached translation")
            repair_file = tmp_path / "repair.json"
            repair_file.write_text(json.dumps([{"source_text": "Source text"}]), encoding="utf-8")

            items = load_repair_items(repair_file, cache)

            self.assertEqual(1, len(items))
            self.assertEqual("Source text", items[0].core_text)
            self.assertEqual("Cached translation", items[0].current_translation)

    def test_auto_repair_detects_broken_encoding_artifacts(self) -> None:
        self.assertTrue(
            should_auto_repair(
                "A thoughtful sentence about knowledge and explanation.",
                'Una frase extrana con "\u00c2\u00ab comillas \u00c2\u00bb" rotas.',
                "es",
            )
        )

    def test_auto_repair_detects_unchanged_english_output_for_spanish(self) -> None:
        self.assertTrue(
            should_auto_repair(
                "This theory explains the structure of reality in a precise way.",
                "This theory explains the structure of reality in a precise way.",
                "es",
            )
        )

    def test_auto_repair_does_not_flag_normal_spanish_sentence(self) -> None:
        self.assertFalse(
            should_auto_repair(
                "This theory explains the structure of reality in a precise way.",
                "Esta teoria explica la estructura de la realidad de una manera precisa.",
                "es",
            )
        )

    def test_section_leakage_detection_flags_heading_inside_paragraph(self) -> None:
        self.assertTrue(
            contains_section_leakage(
                "The growth of knowledge depends on correcting mistakes through criticism and explanation.",
                "El crecimiento del conocimiento depende de corregir errores mediante crítica y explicación. Agradecimientos",
                "es",
            )
        )

    def test_cleanup_artifacts_removes_only_listed_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            request_file = tmp_path / "batch.jsonl"
            manifest_file = tmp_path / "batch.manifest.json"
            cache_file = tmp_path / "batch.progress.json"
            request_file.write_text("{}", encoding="utf-8")
            manifest_file.write_text("{}", encoding="utf-8")
            cache_file.write_text("{}", encoding="utf-8")

            cleanup_artifacts([request_file, manifest_file, request_file, tmp_path / "missing.json"])

            self.assertFalse(request_file.exists())
            self.assertFalse(manifest_file.exists())
            self.assertTrue(cache_file.exists())

    def test_execute_batch_round_retries_only_malformed_groups(self) -> None:
        pending = [
            PendingNode(rel_path="chapter.xhtml", node_index=0, core_text="First fragment", context_hint="kind=paragraph"),
            PendingNode(rel_path="chapter.xhtml", node_index=1, core_text="Second fragment", context_hint="kind=paragraph"),
        ]

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            config = TranslationConfig(
                input_epub=tmp_path / "book.epub",
                provider="openai",
                target_lang="es",
                model="gpt-4.1-mini",
                output_epub=tmp_path / "book_ES.epub",
                cache_file=tmp_path / "cache.json",
                jsonl_file=tmp_path / "batch.jsonl",
                manifest_file=tmp_path / "manifest.json",
                source_lang="en",
                natural=True,
                prompt_mode="translate",
                prompt_file=None,
                repair_file=None,
                auto_repair_rounds=0,
                review_passes=0,
                completion_window="24h",
                poll_seconds=0,
                max_items_per_request=12,
                max_chars_per_request=5000,
                max_output_tokens=4096,
                prepare_only=False,
                resume_batch_id=None,
            )
            cache = ProgressCache(config.cache_file)
            groups = [pending]
            provider = FakeOpenAIProvider(
                outputs=[
                    {
                        "group_000001": '[{"id":"group_000001_item_001","translation":"Primero"}]',
                    },
                    {
                        "group_000001": '[{"id":"group_000001_item_001","translation":"Primero"}]',
                        "group_000002": '[{"id":"group_000002_item_001","translation":"Segundo"}]',
                    },
                ]
            )

            _, stored, _ = execute_batch_round(
                pending=pending,
                config=config,
                cache=cache,
                provider=provider,
                artifact_provider=provider,
                request_path=config.jsonl_file,
                manifest_path=config.manifest_file,
                groups=groups,
                resume_batch_id=None,
                mode_label="translate",
            )

            self.assertEqual(2, stored)
            self.assertEqual("Primero", cache.get("First fragment"))
            self.assertEqual("Segundo", cache.get("Second fragment"))
            self.assertEqual([2, 1, 1], provider.group_sizes_seen)
            self.assertFalse((tmp_path / "batch.format-retry-1.jsonl").exists())
            self.assertFalse((tmp_path / "manifest.format-retry-1.json").exists())
            self.assertFalse((tmp_path / "batch.format-retry-1.output.jsonl").exists())

    def test_execute_batch_round_retries_suspicious_section_leakage(self) -> None:
        pending = [
            PendingNode(
                rel_path="chapter.xhtml",
                node_index=0,
                core_text="Knowledge grows through conjecture and criticism over long stretches of history.",
                context_hint='kind=paragraph; prev="Earlier paragraph"; next="Later paragraph"',
            )
        ]

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            config = TranslationConfig(
                input_epub=tmp_path / "book.epub",
                provider="openai",
                target_lang="es",
                model="gpt-4.1-mini",
                output_epub=tmp_path / "book_ES.epub",
                cache_file=tmp_path / "cache.json",
                jsonl_file=tmp_path / "batch.jsonl",
                manifest_file=tmp_path / "manifest.json",
                source_lang="en",
                natural=True,
                prompt_mode="translate",
                prompt_file=None,
                repair_file=None,
                auto_repair_rounds=0,
                review_passes=0,
                completion_window="24h",
                poll_seconds=0,
                max_items_per_request=12,
                max_chars_per_request=5000,
                max_output_tokens=4096,
                prepare_only=False,
                resume_batch_id=None,
            )
            cache = ProgressCache(config.cache_file)
            provider = FakeOpenAIProvider(
                outputs=[
                    {
                        "group_000001": '[{"id":"group_000001_item_001","translation":"El conocimiento crece mediante conjeturas y crítica. Agradecimientos"}]',
                    },
                    {
                        "group_000001": '[{"id":"group_000001_item_001","translation":"El conocimiento crece mediante conjeturas y crítica a lo largo de la historia."}]',
                    },
                ]
            )

            _, stored, _ = execute_batch_round(
                pending=pending,
                config=config,
                cache=cache,
                provider=provider,
                artifact_provider=provider,
                request_path=config.jsonl_file,
                manifest_path=config.manifest_file,
                groups=[pending],
                resume_batch_id=None,
                mode_label="translate",
            )

            self.assertEqual(1, stored)
            self.assertEqual(
                "El conocimiento crece mediante conjeturas y crítica a lo largo de la historia.",
                cache.get(pending[0].core_text),
            )
            self.assertEqual([1, 1], provider.group_sizes_seen)


def parse_args_for_test(argv: list[str]):
    import sys

    old_argv = sys.argv
    try:
        sys.argv = ["translate_epub_ai", *argv]
        return parse_args()
    finally:
        sys.argv = old_argv


class FakeOpenAIProvider:
    def __init__(self, outputs: list[dict[str, str]]):
        self.outputs = outputs
        self.output_index = 0
        self.group_sizes_seen: list[int] = []

    def build_request_artifact(self, request_path: Path, manifest_path: Path, groups, config) -> None:
        manifest = build_manifest(groups)
        self.group_sizes_seen.extend(len(group) for group in groups)
        request_path.write_text("{}", encoding="utf-8")
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    def create_batch(self, request_path: Path, metadata: dict | None, completion_window: str) -> str:
        return f"fake-batch-{self.output_index}"

    def wait_for_batch(self, batch_id: str, poll_seconds: int) -> dict:
        return {"status": "completed", "output_file_id": "fake-file"}

    def get_result_bytes(self, batch: dict, batch_id: str) -> bytes:
        records = []
        for custom_id, output_text in self.outputs[self.output_index].items():
            records.append(
                {
                    "custom_id": custom_id,
                    "response": {"body": {"output_text": output_text}},
                }
            )
        self.output_index += 1
        return ("\n".join(json.dumps(record) for record in records) + "\n").encode("utf-8")

    def parse_grouped_output(self, output_bytes: bytes, manifest_path: Path):
        return OpenAIBatchProvider("test-key").parse_grouped_output(output_bytes, manifest_path)


if __name__ == "__main__":
    unittest.main()
