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
    current_translations: Optional[List[Optional[str]]] = None,
    context_hints: Optional[List[str]] = None,
    repair_mode: bool = False,
    review_mode: bool = False,
) -> str:
    if target_lang.lower() == "es":
        locale_instruction = "natural European Spanish (Spanish as used in Spain)"
        quote_instruction = 'Use proper Spanish book-style quotation marks ("\u00ab \u00bb") when appropriate.'
    else:
        locale_instruction = target_lang
        quote_instruction = ""

    style_instruction = (
        "The translation must read like a professionally published book, with natural rhythm, strong readability, and faithful preservation of the author's voice."
        if natural
        else "The translation should remain precise and faithful, but still read as fluent, idiomatic prose in the target language."
    )

    template = load_prompt_template(prompt_file)
    prompt = template.format(
        source_language_clause=f" from {source_lang}" if source_lang else "",
        target_language_name=locale_instruction,
        style_instruction=style_instruction,
        quote_instruction=f"- {quote_instruction}" if quote_instruction else "",
        item_count=len(payload_texts),
        payload_json=json.dumps(payload_texts, ensure_ascii=False),
    )
    prompt += (
        "\n\nQuality control:\n"
        "- Before finalizing each item, silently check that it is coherent, natural, idiomatic, and consistent with the likely surrounding document.\n"
        "- Fix broken phrasing, unnatural calques, punctuation issues, register mismatches, and formatting glitches before returning the final JSON.\n"
        "- If a literal translation sounds wrong in the target language, rewrite it so it sounds native while preserving meaning and tone.\n"
        "- Keep each item aligned with the surrounding narrative or argument, even when the local sentence is ambiguous.\n"
    )

    if review_mode:
        prompt += (
            "\nReview mode:\n"
            "- You are reviewing an existing translation against the source text.\n"
            "- For each item, keep the current translation if it is already strong, natural, and accurate.\n"
            "- Revise it only when needed to improve fluency, precision, coherence, formatting, tone, or idiomatic quality.\n"
            "- Return the final polished translation array only, not comments or explanations.\n"
        )
        if current_translations:
            prompt += "\nCurrent translations to review:\n"
            for index, current in enumerate(current_translations, start=1):
                if current:
                    prompt += f"- Item {index}: {json.dumps(current, ensure_ascii=False)}\n"

    if repair_mode:
        prompt += (
            "\nRepair mode:\n"
            "- You are revising only suspicious or low-quality translated items, not retranslating a whole chapter.\n"
            "- Correct only the items that need improvement, but return the full final corrected array in order.\n"
            "- Prioritize fixing broken formatting, awkward wording, lost meaning, unnatural phrasing, and inconsistencies with the rest of the document.\n"
        )
        if current_translations:
            prompt += "\nCurrent translations to review:\n"
            for index, current in enumerate(current_translations, start=1):
                if current:
                    prompt += f"- Item {index}: {json.dumps(current, ensure_ascii=False)}\n"
        if context_hints:
            visible_hints = [hint for hint in context_hints if hint]
            if visible_hints:
                prompt += "\nOptional context hints:\n"
                for index, hint in enumerate(context_hints, start=1):
                    if hint:
                        prompt += f"- Item {index}: {json.dumps(hint, ensure_ascii=False)}\n"

    return prompt
