"""OpenAI Batch API integration and batch file processing."""

import json
import time
from pathlib import Path
from typing import Dict, List, Optional

import requests

from .constants import BATCHES_URL, FILES_URL, RESPONSES_ENDPOINT
from .models import PendingNode
from .prompting import build_translation_prompt
from .utils import clean_json_text, dump_json, log, stable_text_hash


class OpenAIClient:
    def __init__(self, api_key: str):
        self.api_key = api_key

    @property
    def headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}"}

    def upload_file(self, path: Path, purpose: str = "batch") -> str:
        with path.open("rb") as file_handle:
            response = requests.post(
                FILES_URL,
                headers=self.headers,
                files={"file": (path.name, file_handle, "application/jsonl")},
                data={"purpose": purpose},
                timeout=300,
            )
        response.raise_for_status()
        return response.json()["id"]

    def create_batch(
        self,
        input_file_id: str,
        metadata: Optional[dict] = None,
        completion_window: str = "24h",
    ) -> str:
        body = {
            "input_file_id": input_file_id,
            "endpoint": RESPONSES_ENDPOINT,
            "completion_window": completion_window,
        }
        if metadata:
            body["metadata"] = metadata

        response = requests.post(
            BATCHES_URL,
            headers={**self.headers, "Content-Type": "application/json"},
            json=body,
            timeout=120,
        )
        response.raise_for_status()
        return response.json()["id"]

    def get_batch(self, batch_id: str) -> dict:
        response = requests.get(f"{BATCHES_URL}/{batch_id}", headers=self.headers, timeout=120)
        response.raise_for_status()
        return response.json()

    def download_file_content(self, file_id: str) -> bytes:
        response = requests.get(f"{FILES_URL}/{file_id}/content", headers=self.headers, timeout=300)
        response.raise_for_status()
        return response.content


def build_grouped_requests(
    pending: List[PendingNode],
    max_items_per_request: int,
    max_chars_per_request: int,
) -> List[List[PendingNode]]:
    groups: List[List[PendingNode]] = []
    current_group: List[PendingNode] = []
    current_chars = 2
    current_file: Optional[str] = None

    for item in pending:
        item_chars = len(item.core_text) + 48
        split_for_file_change = (
            bool(current_group)
            and current_file is not None
            and item.rel_path != current_file
            and len(current_group) >= max(4, max_items_per_request // 2)
        )
        split_for_items = len(current_group) >= max_items_per_request
        split_for_chars = bool(current_group) and (current_chars + item_chars > max_chars_per_request)

        if split_for_file_change or split_for_items or split_for_chars:
            groups.append(current_group)
            current_group = []
            current_chars = 2
            current_file = None

        current_group.append(item)
        current_chars += item_chars
        current_file = item.rel_path

    if current_group:
        groups.append(current_group)

    return groups


def build_batch_files(
    jsonl_path: Path,
    manifest_path: Path,
    groups: List[List[PendingNode]],
    target_lang: str,
    model: str,
    source_lang: Optional[str],
    natural: bool,
    prompt_file: Optional[Path],
) -> None:
    manifest: Dict[str, List[dict]] = {}

    with jsonl_path.open("w", encoding="utf-8") as handle:
        for group_index, group in enumerate(groups, start=1):
            custom_id = f"group_{group_index:06d}"
            payload_texts = [item.core_text for item in group]
            prompt = build_translation_prompt(
                payload_texts=payload_texts,
                target_lang=target_lang,
                source_lang=source_lang,
                natural=natural,
                prompt_file=prompt_file,
            )

            record = {
                "custom_id": custom_id,
                "method": "POST",
                "url": RESPONSES_ENDPOINT,
                "body": {"model": model, "input": prompt},
            }
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

            manifest[custom_id] = [
                {
                    "rel_path": item.rel_path,
                    "node_index": item.node_index,
                    "hash": stable_text_hash(item.core_text),
                    "core_text": item.core_text,
                }
                for item in group
            ]

    dump_json(manifest_path, manifest)


def extract_output_text(body: dict) -> Optional[str]:
    if body.get("output_text"):
        return body["output_text"]

    parts: List[str] = []
    for item in body.get("output", []):
        for content in item.get("content", []):
            if content.get("type") == "output_text":
                parts.append(content.get("text", ""))

    joined = "".join(parts).strip()
    return joined or None


def parse_grouped_output(output_bytes: bytes, manifest_path: Path) -> Dict[str, str]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    translations_by_hash: Dict[str, str] = {}
    output_text = output_bytes.decode("utf-8", errors="replace")

    for line in output_text.splitlines():
        if not line.strip():
            continue

        record = json.loads(line)
        custom_id = record.get("custom_id")
        if not custom_id or custom_id not in manifest:
            continue

        raw_output = extract_output_text((record.get("response") or {}).get("body") or {})
        if not raw_output:
            continue

        try:
            translated_items = json.loads(clean_json_text(raw_output))
        except json.JSONDecodeError:
            continue

        expected = manifest[custom_id]
        if not isinstance(translated_items, list) or len(translated_items) != len(expected):
            continue

        for item_meta, translated in zip(expected, translated_items):
            translations_by_hash[item_meta["hash"]] = str(translated)

    return translations_by_hash


def wait_for_batch(client: OpenAIClient, batch_id: str, poll_seconds: int) -> dict:
    while True:
        batch = client.get_batch(batch_id)
        status = batch.get("status")
        counts = batch.get("request_counts", {})
        log(
            f"Batch status: {status} | "
            f"completed={counts.get('completed', 0)} "
            f"failed={counts.get('failed', 0)} "
            f"total={counts.get('total', 0)}"
        )
        if status in {"completed", "failed", "cancelled", "expired"}:
            return batch
        time.sleep(poll_seconds)
