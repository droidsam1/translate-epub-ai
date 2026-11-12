import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from translate_epub_ai.batch_providers import OllamaProvider
from translate_epub_ai.cli import build_config
from translate_epub_ai.models import PendingNode
from translate_epub_ai.workflow import required_api_key_env

from test_providers import parse_args_for_test


class FakeOllama(BaseHTTPRequestHandler):
    requests_seen: list = []

    def log_message(self, *args) -> None:  # silence
        pass

    def _send(self, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        self._send({"models": [{"name": "test-model:latest"}]})

    def do_POST(self) -> None:
        length = int(self.headers["Content-Length"])
        request = json.loads(self.rfile.read(length))
        FakeOllama.requests_seen.append(request)
        prompt = request["messages"][0]["content"]
        ids = sorted(set(part.split('"')[0] for part in prompt.split('"id": "')[1:]))
        answer = [{"id": item_id, "translation": f"ES {item_id}"} for item_id in ids]
        self._send({"message": {"content": json.dumps(answer)}})


class OllamaProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeOllama.requests_seen = []
        self.server = HTTPServer(("127.0.0.1", 0), FakeOllama)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.host = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()

    def test_no_api_key_needed_and_local_defaults(self) -> None:
        self.assertIsNone(required_api_key_env("ollama"))
        config = build_config(parse_args_for_test(["book.epub", "--provider", "ollama"]))
        self.assertEqual(6, config.max_items_per_request)
        self.assertTrue(config.model)

    def test_runs_requests_locally_and_parses_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config = build_config(
                parse_args_for_test(["book.epub", "--provider", "ollama", "--model", "test-model"])
            )
            provider = OllamaProvider(host=self.host)
            groups = [[PendingNode("a.xhtml", 0, "Hello"), PendingNode("a.xhtml", 1, "World")]]
            request_path = tmp_path / "book.batch.jsonl"
            manifest_path = tmp_path / "book.manifest.json"
            provider.build_request_artifact(request_path, manifest_path, groups, config)

            batch_id = provider.create_batch(request_path, None, "24h")
            batch = provider.wait_for_batch(batch_id, 0)
            self.assertTrue(provider.is_success_status(batch))
            parsed = provider.parse_grouped_output(provider.get_result_bytes(batch, batch_id), manifest_path)

            self.assertEqual(2, len(parsed.translations_by_hash))
            self.assertEqual({}, parsed.malformed_groups)
            self.assertEqual(1, len(FakeOllama.requests_seen))
            sent = FakeOllama.requests_seen[0]
            self.assertFalse(sent["stream"])
            self.assertEqual("array", sent["format"]["type"])

            # Resuming does not resend finished requests.
            provider.wait_for_batch(batch_id, 0)
            self.assertEqual(1, len(FakeOllama.requests_seen))


if __name__ == "__main__":
    unittest.main()
