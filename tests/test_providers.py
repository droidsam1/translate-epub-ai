import json
import tempfile
import unittest
from pathlib import Path

from translate_epub_ai.batch_providers import AnthropicBatchProvider, build_grouped_requests
from translate_epub_ai.cli import build_config, parse_args, required_api_key_env
from translate_epub_ai.models import PendingNode


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
            groups = build_grouped_requests(
                [PendingNode(rel_path="chapter.xhtml", node_index=0, core_text="Hello world")],
                max_items_per_request=12,
                max_chars_per_request=5000,
            )
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
