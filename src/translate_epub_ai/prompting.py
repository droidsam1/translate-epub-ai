"""Prompt template loading and rendering."""

import json
from importlib import resources
from pathlib import Path
from typing import List, Optional


def load_prompt_template(prompt_file: Optional[Path]) -> str:
    if prompt_file is not None:
        return prompt_file.read_text(encoding="utf-8")
    return resources.files("translate_epub_ai.prompts").joinpath("default_prompt.txt").read_text(encoding="utf-8")


def build_translation_prompt(
    payload_texts: List[str],
    target_lang: str,
    source_lang: Optional[str],
    natural: bool,
    prompt_file: Optional[Path] = None,
) -> str:
    if target_lang.lower() == "es":
        locale_instruction = "natural European Spanish (Spanish as used in Spain)"
        quote_instruction = 'Use proper Spanish book-style quotation marks ("« »") when appropriate.'
    else:
        locale_instruction = target_lang
        quote_instruction = ""

    style_instruction = (
        "The translation must read like a professionally published book, with natural rhythm, strong readability, and faithful preservation of the author's voice."
        if natural
        else "The translation should remain precise and faithful, but still read as fluent, idiomatic prose in the target language."
    )

    template = load_prompt_template(prompt_file)
    return template.format(
        source_language_clause=f" from {source_lang}" if source_lang else "",
        target_language_name=locale_instruction,
        style_instruction=style_instruction,
        quote_instruction=f"- {quote_instruction}" if quote_instruction else "",
        item_count=len(payload_texts),
        payload_json=json.dumps(payload_texts, ensure_ascii=False),
    )
