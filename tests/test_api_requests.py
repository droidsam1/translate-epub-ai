import json
import tempfile
import unittest
from pathlib import Path

from translate_epub_ai.batch_providers import (
    AnthropicBatchProvider,
    OpenAIBatchProvider,
    build_manifest,
    extract_doubts,
)
from translate_epub_ai.cli import build_config
from translate_epub_ai.models import PendingNode

from test_providers import parse_args_for_test

GROUPS = [[PendingNode("a.xhtml", 0, "Hello world."), PendingNode("a.xhtml", 1, "Second paragraph.")]]


def build_records(provider, argv):
    config = build_config(parse_args_for_test(["book.epub", *argv]))
    with tempfile.TemporaryDirectory() as tmp:
        request_path = Path(tmp) / "r.jsonl"
        provider.build_request_artifact(request_path, Path(tmp) / "m.json", GROUPS, config)
        return [json.loads(line) for line in request_path.read_text(encoding="utf-8").splitlines()], config


class ApiRequestTests(unittest.TestCase):
    def test_defaults_are_current_models(self) -> None:
        self.assertEqual("gpt-5.6-terra", build_config(parse_args_for_test(["b.epub"])).model)
        self.assertEqual(
            "claude-sonnet-5-5", build_config(parse_args_for_test(["b.epub", "--provider", "anthropic"])).model
        )

    def test_openai_request_uses_cacheable_instructions_and_no_reasoning(self) -> None:
        records, _ = build_records(OpenAIBatchProvider("k"), [])
        body = records[0]["body"]
        self.assertIn("Quality control", body["instructions"])
        self.assertNotIn("Hello world.", body["instructions"])
        self.assertIn("Hello world.", body["input"])
        self.assertTrue(body["prompt_cache_key"].startswith("translate-epub-ai-"))
        self.assertEqual({"effort": "none"}, body["reasoning"])

        records, _ = build_records(OpenAIBatchProvider("k"), ["--model", "gpt-4.1-mini"])
        self.assertNotIn("reasoning", records[0]["body"])

    def test_anthropic_request_marks_instructions_for_caching(self) -> None:
        records, _ = build_records(AnthropicBatchProvider("k"), ["--provider", "anthropic"])
        params = records[0]["params"]
        self.assertEqual({"type": "ephemeral"}, params["system"][0]["cache_control"])
        self.assertIn("Quality control", params["system"][0]["text"])
        self.assertIn("Hello world.", params["messages"][0]["content"])

    def test_doubts_are_collected_by_item(self) -> None:
        expected = build_manifest(GROUPS)["group_000001"]
        raw = json.dumps(
            [
                {"id": expected[0]["item_id"], "translation": "Hola mundo."},
                {"id": expected[1]["item_id"], "translation": "Segundo.", "doubt": "pun on 'second'"},
            ]
        )
        self.assertEqual({expected[1]["hash"]: "pun on 'second'"}, extract_doubts(raw, expected))


if __name__ == "__main__":
    unittest.main()
