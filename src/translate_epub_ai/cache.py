"""Persistent translation cache."""

import json
from pathlib import Path
from typing import Dict, Optional, Tuple

from .utils import stable_text_hash


class ProgressCache:
    def __init__(self, path: Path):
        self.path = path
        self.data: Dict[str, Dict[str, str]] = {"translations": {}, "meta": {}}
        if self.path.exists():
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                loaded = {}
            self.data["translations"].update(loaded.get("translations", {}))
            self.data["meta"].update(loaded.get("meta", {}))

    def get(self, text: str) -> Optional[str]:
        return self.data["translations"].get(stable_text_hash(text))

    def set(self, text: str, translated: str) -> None:
        self.data["translations"][stable_text_hash(text)] = translated

    def set_meta(self, key: str, value: str) -> None:
        self.data["meta"][key] = value

    def save(self) -> None:
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp_path.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp_path.replace(self.path)

    def stats(self) -> Tuple[int, int]:
        return len(self.data["translations"]), len(json.dumps(self.data, ensure_ascii=False))
