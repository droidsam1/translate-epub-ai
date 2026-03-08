import json
import tempfile
import unittest
from pathlib import Path

from translate_epub_ai.batch_providers import AnthropicBatchProvider
from translate_epub_ai.cache import ProgressCache
from translate_epub_ai.cli import (
    build_config,
    load_repair_items,
    parse_args,
    required_api_key_env,
    should_auto_repair,
)


class ProviderTests(unittest.TestCase):
    def test_openai_remains_default_provider(self) -> None:
        namespace = parse_args_for_test(["book.epub"])
        config = build_config(namespace)
        self.assertEqual("openai", config.provider)
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
                        "content": [
                            {
                                "type": "text",
                                "text": '["Hola mundo"]',
                            }
                        ]
                    },
                },
            }
            parsed = provider.parse_grouped_output(
                output_bytes=(json.dumps(output_line) + "\n").encode("utf-8"),
                manifest_path=manifest_path,
            )
            self.assertEqual({"abc123": "Hola mundo"}, parsed)

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
                'Una frase extraña con "Â« comillas Â»" rotas.',
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
                "Esta teoría explica la estructura de la realidad de una manera precisa.",
                "es",
            )
        )


def parse_args_for_test(argv: list[str]):
    import sys

    old_argv = sys.argv
    try:
        sys.argv = ["translate_epub_batch_v3.py", *argv]
        return parse_args()
    finally:
        sys.argv = old_argv


if __name__ == "__main__":
    unittest.main()
