"""Batch-provider abstraction with OpenAI and Anthropic implementations."""

from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import requests

from .models import PendingNode, TranslationConfig
from .prompting import build_translation_prompt
from .utils import clean_json_text, dump_json, log, stable_text_hash

OPENAI_FILES_URL = "https://api.openai.com/v1/files"
OPENAI_BATCHES_URL = "https://api.openai.com/v1/batches"
OPENAI_RESPONSES_ENDPOINT = "/v1/responses"

ANTHROPIC_BATCHES_URL = "https://api.anthropic.com/v1/messages/batches"
ANTHROPIC_VERSION = "2023-06-01"


@dataclass(frozen=True)
class ParsedBatchOutput:
    translations_by_hash: Dict[str, str]
    malformed_groups: Dict[str, str]


class BatchProvider(ABC):
    provider_name: str

    def __init__(self, api_key: str):
        self.api_key = api_key

    @abstractmethod
    def build_request_artifact(
        self,
        request_path: Path,
        manifest_path: Path,
        groups: List[List[PendingNode]],
        config: TranslationConfig,
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    def create_batch(self, request_path: Path, metadata: Optional[dict], completion_window: str) -> str:
        raise NotImplementedError

    @abstractmethod
    def get_batch(self, batch_id: str) -> dict:
        raise NotImplementedError

    @abstractmethod
    def wait_terminal_statuses(self) -> set[str]:
        raise NotImplementedError

    @abstractmethod
    def is_success_status(self, batch: dict) -> bool:
        raise NotImplementedError

    @abstractmethod
    def get_result_bytes(self, batch: dict, batch_id: str) -> Optional[bytes]:
        raise NotImplementedError

    @abstractmethod
    def parse_grouped_output(self, output_bytes: bytes, manifest_path: Path) -> ParsedBatchOutput:
        raise NotImplementedError

    def wait_for_batch(self, batch_id: str, poll_seconds: int) -> dict:
        while True:
            batch = self.get_batch(batch_id)
            log(self.describe_status(batch))
            if self.get_status(batch) in self.wait_terminal_statuses():
                return batch
            time.sleep(poll_seconds)

    @abstractmethod
    def get_status(self, batch: dict) -> str:
        raise NotImplementedError

    @abstractmethod
    def describe_status(self, batch: dict) -> str:
        raise NotImplementedError


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


def build_manifest(groups: List[List[PendingNode]]) -> Dict[str, List[dict]]:
    manifest: Dict[str, List[dict]] = {}
    for group_index, group in enumerate(groups, start=1):
        custom_id = f"group_{group_index:06d}"
        manifest[custom_id] = [
            {
                "item_id": f"{custom_id}_item_{item_index:03d}",
                "rel_path": item.rel_path,
                "node_index": item.node_index,
                "hash": stable_text_hash(item.core_text),
                "core_text": item.core_text,
                "current_translation": item.current_translation,
                "context_hint": item.context_hint,
            }
            for item_index, item in enumerate(group, start=1)
        ]
    return manifest


def parse_translated_array(raw_output: Optional[str], expected: List[dict]) -> tuple[Dict[str, str], Optional[str]]:
    if not raw_output:
        return {}, "empty output"

    try:
        translated_items = json.loads(clean_json_text(raw_output))
    except json.JSONDecodeError as error:
        return {}, f"invalid JSON: {error.msg}"

    if not isinstance(translated_items, list):
        return {}, f"expected a JSON array, got {type(translated_items).__name__}"
    if len(translated_items) != len(expected):
        return {}, f"expected {len(expected)} items, got {len(translated_items)}"

    if all(isinstance(item, str) for item in translated_items):
        return (
            {
                item_meta["hash"]: str(translated)
                for item_meta, translated in zip(expected, translated_items)
            },
            None,
        )

    if not all(isinstance(item, dict) for item in translated_items):
        return {}, "expected either translated strings or {id, translation} objects"

    translations_by_hash: Dict[str, str] = {}
    expected_by_id = {item["item_id"]: item for item in expected}
    seen_ids: set[str] = set()

    for raw_item in translated_items:
        item_id = raw_item.get("id")
        translation = raw_item.get("translation")
        if not isinstance(item_id, str) or item_id not in expected_by_id:
            return {}, f"unexpected item id: {item_id!r}"
        if item_id in seen_ids:
            return {}, f"duplicate item id: {item_id}"
        if not isinstance(translation, str):
            return {}, f"missing translation text for item id: {item_id}"
        seen_ids.add(item_id)
        translations_by_hash[expected_by_id[item_id]["hash"]] = translation

    if len(seen_ids) != len(expected):
        return {}, f"expected {len(expected)} unique item ids, got {len(seen_ids)}"

    return translations_by_hash, None


class OpenAIBatchProvider(BatchProvider):
    provider_name = "openai"

    @property
    def headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}"}

    def upload_file(self, path: Path, purpose: str = "batch") -> str:
        with path.open("rb") as file_handle:
            response = requests.post(
                OPENAI_FILES_URL,
                headers=self.headers,
                files={"file": (path.name, file_handle, "application/jsonl")},
                data={"purpose": purpose},
                timeout=300,
            )
        response.raise_for_status()
        return response.json()["id"]

    def build_request_artifact(
        self,
        request_path: Path,
        manifest_path: Path,
        groups: List[List[PendingNode]],
        config: TranslationConfig,
    ) -> None:
        manifest = build_manifest(groups)

        with request_path.open("w", encoding="utf-8") as handle:
            for custom_id, items in manifest.items():
                payload_texts = [item["core_text"] for item in items]
                prompt = build_translation_prompt(
                    payload_texts=payload_texts,
                    payload_ids=[item["item_id"] for item in items],
                    target_lang=config.target_lang,
                    source_lang=config.source_lang,
                    natural=config.natural,
                    prompt_file=config.prompt_file,
                    current_translations=[item.get("current_translation") for item in items],
                    context_hints=[item.get("context_hint", "") for item in items],
                    repair_mode=config.prompt_mode == "repair",
                    review_mode=config.prompt_mode == "review",
                )
                record = {
                    "custom_id": custom_id,
                    "method": "POST",
                    "url": OPENAI_RESPONSES_ENDPOINT,
                    "body": {"model": config.model, "input": prompt},
                }
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")

        dump_json(manifest_path, manifest)

    def create_batch(self, request_path: Path, metadata: Optional[dict], completion_window: str) -> str:
        input_file_id = self.upload_file(request_path, purpose="batch")
        log(f"Uploaded JSONL file. file_id={input_file_id}")
        body = {
            "input_file_id": input_file_id,
            "endpoint": OPENAI_RESPONSES_ENDPOINT,
            "completion_window": completion_window,
        }
        if metadata:
            body["metadata"] = metadata

        response = requests.post(
            OPENAI_BATCHES_URL,
            headers={**self.headers, "Content-Type": "application/json"},
            json=body,
            timeout=120,
        )
        response.raise_for_status()
        return response.json()["id"]

    def get_batch(self, batch_id: str) -> dict:
        response = requests.get(f"{OPENAI_BATCHES_URL}/{batch_id}", headers=self.headers, timeout=120)
        response.raise_for_status()
        return response.json()

    def get_status(self, batch: dict) -> str:
        return str(batch.get("status"))

    def describe_status(self, batch: dict) -> str:
        counts = batch.get("request_counts", {})
        return (
            f"Batch status: {self.get_status(batch)} | "
            f"completed={counts.get('completed', 0)} "
            f"failed={counts.get('failed', 0)} "
            f"total={counts.get('total', 0)}"
        )

    def wait_terminal_statuses(self) -> set[str]:
        return {"completed", "failed", "cancelled", "expired"}

    def is_success_status(self, batch: dict) -> bool:
        return self.get_status(batch) == "completed"

    def download_file_content(self, file_id: str) -> bytes:
        response = requests.get(f"{OPENAI_FILES_URL}/{file_id}/content", headers=self.headers, timeout=300)
        response.raise_for_status()
        return response.content

    def get_result_bytes(self, batch: dict, batch_id: str) -> Optional[bytes]:
        output_file_id = batch.get("output_file_id")
        if not output_file_id:
            return None
        return self.download_file_content(output_file_id)

    def parse_grouped_output(self, output_bytes: bytes, manifest_path: Path) -> ParsedBatchOutput:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        translations_by_hash: Dict[str, str] = {}
        malformed_groups: Dict[str, str] = {}
        seen_groups: set[str] = set()
        output_text = output_bytes.decode("utf-8", errors="replace")

        for line in output_text.splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            custom_id = record.get("custom_id")
            if not custom_id or custom_id not in manifest:
                continue
            seen_groups.add(custom_id)
            raw_output = extract_openai_output_text((record.get("response") or {}).get("body") or {})
            parsed_group, issue = parse_translated_array(raw_output, manifest[custom_id])
            if issue is not None:
                malformed_groups[custom_id] = issue
                continue
            translations_by_hash.update(parsed_group)

        for custom_id in manifest:
            if custom_id not in seen_groups:
                malformed_groups.setdefault(custom_id, "missing output record")

        return ParsedBatchOutput(
            translations_by_hash=translations_by_hash,
            malformed_groups=malformed_groups,
        )


class AnthropicBatchProvider(BatchProvider):
    provider_name = "anthropic"

    @property
    def headers(self) -> dict:
        return {
            "x-api-key": self.api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        }

    def build_request_artifact(
        self,
        request_path: Path,
        manifest_path: Path,
        groups: List[List[PendingNode]],
        config: TranslationConfig,
    ) -> None:
        manifest = build_manifest(groups)

        with request_path.open("w", encoding="utf-8") as handle:
            for custom_id, items in manifest.items():
                payload_texts = [item["core_text"] for item in items]
                prompt = build_translation_prompt(
                    payload_texts=payload_texts,
                    payload_ids=[item["item_id"] for item in items],
                    target_lang=config.target_lang,
                    source_lang=config.source_lang,
                    natural=config.natural,
                    prompt_file=config.prompt_file,
                    current_translations=[item.get("current_translation") for item in items],
                    context_hints=[item.get("context_hint", "") for item in items],
                    repair_mode=config.prompt_mode == "repair",
                    review_mode=config.prompt_mode == "review",
                )
                record = {
                    "custom_id": custom_id,
                    "params": {
                        "model": config.model,
                        "max_tokens": config.max_output_tokens,
                        "messages": [{"role": "user", "content": prompt}],
                    },
                }
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")

        dump_json(manifest_path, manifest)

    def create_batch(self, request_path: Path, metadata: Optional[dict], completion_window: str) -> str:
        requests_payload = [
            json.loads(line)
            for line in request_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        body = {"requests": requests_payload}
        response = requests.post(ANTHROPIC_BATCHES_URL, headers=self.headers, json=body, timeout=120)
        response.raise_for_status()
        return response.json()["id"]

    def get_batch(self, batch_id: str) -> dict:
        response = requests.get(f"{ANTHROPIC_BATCHES_URL}/{batch_id}", headers=self.headers, timeout=120)
        response.raise_for_status()
        return response.json()

    def get_status(self, batch: dict) -> str:
        return str(batch.get("processing_status"))

    def describe_status(self, batch: dict) -> str:
        counts = batch.get("request_counts", {})
        return (
            f"Batch status: {self.get_status(batch)} | "
            f"succeeded={counts.get('succeeded', 0)} "
            f"processing={counts.get('processing', 0)} "
            f"errored={counts.get('errored', 0)} "
            f"canceled={counts.get('canceled', 0)} "
            f"expired={counts.get('expired', 0)}"
        )

    def wait_terminal_statuses(self) -> set[str]:
        return {"ended"}

    def is_success_status(self, batch: dict) -> bool:
        return self.get_status(batch) == "ended"

    def get_result_bytes(self, batch: dict, batch_id: str) -> Optional[bytes]:
        response = requests.get(f"{ANTHROPIC_BATCHES_URL}/{batch_id}/results", headers=self.headers, timeout=300)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.content

    def parse_grouped_output(self, output_bytes: bytes, manifest_path: Path) -> ParsedBatchOutput:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        translations_by_hash: Dict[str, str] = {}
        malformed_groups: Dict[str, str] = {}
        seen_groups: set[str] = set()
        output_text = output_bytes.decode("utf-8", errors="replace")

        for line in output_text.splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            custom_id = record.get("custom_id")
            if not custom_id or custom_id not in manifest:
                continue
            seen_groups.add(custom_id)
            result = record.get("result") or {}
            if result.get("type") != "succeeded":
                continue
            message = result.get("message") or {}
            raw_output = extract_anthropic_output_text(message)
            parsed_group, issue = parse_translated_array(raw_output, manifest[custom_id])
            if issue is not None:
                malformed_groups[custom_id] = issue
                continue
            translations_by_hash.update(parsed_group)

        for custom_id in manifest:
            if custom_id not in seen_groups:
                malformed_groups.setdefault(custom_id, "missing output record")

        return ParsedBatchOutput(
            translations_by_hash=translations_by_hash,
            malformed_groups=malformed_groups,
        )


def extract_openai_output_text(body: dict) -> Optional[str]:
    if body.get("output_text"):
        return body["output_text"]

    parts: List[str] = []
    for item in body.get("output", []):
        for content in item.get("content", []):
            if content.get("type") == "output_text":
                parts.append(content.get("text", ""))

    joined = "".join(parts).strip()
    return joined or None


def extract_anthropic_output_text(message: dict) -> Optional[str]:
    parts: List[str] = []
    for block in message.get("content", []):
        if block.get("type") == "text":
            parts.append(block.get("text", ""))
    joined = "".join(parts).strip()
    return joined or None


def create_provider(provider_name: str, api_key: str) -> BatchProvider:
    normalized = provider_name.lower()
    if normalized == "openai":
        return OpenAIBatchProvider(api_key)
    if normalized == "anthropic":
        return AnthropicBatchProvider(api_key)
    raise ValueError(f"Unsupported provider: {provider_name}")
