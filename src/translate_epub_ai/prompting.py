"""Prompt template loading and rendering."""

import json
import re
from importlib import resources
from pathlib import Path
from typing import Dict, List, Optional

# Errors a careful human translator would not make, but small local models
# often do. Kept concrete on purpose: models follow examples better than
# abstract advice.
SPANISH_STYLE_RULES = """
Spanish style rules (follow them strictly):
- Quotation marks: use « » for quotations and quoted phrases, never ‘ ’ or " ". Use “ ” only for a quote inside another quote.
- English passives usually become pronominal ("se") passives or active voice in Spanish: "it cannot be explained by chance" -> "no puede explicarse por azar" (not "no puede explicar por azar").
- Keep the preposition that English leaves at the end: "what is it made of?" -> "¿de qué está hecho?"; "who did they rely on?" -> "¿en quién confiaban?".
- Never drop qualifiers or intensifiers (best, only, even, all, merely, rather, at least): "only the best theories survive" -> "solo sobreviven las mejores teorías".
- Avoid calques: "to be curious about" -> "sentir curiosidad por"; "make sense" -> "tener sentido"; "in terms of" -> "en función de", not "en términos de" by default.
- Use the established Spanish spelling of terms (falibilismo, justificacionismo, empirismo, inductivismo, multiverso), not anglicised forms.
- Emphasis: when a marker wraps an emphasised English word, wrap the Spanish word that carries the same emphasis, even if it is a different part of speech. Do not put emphasis on a word that is not stressed in Spanish.
- Keep the source punctuation that structures the text: a paragraph ending in a colon must still end in a colon.
- Write the result as a native editor from Spain would: varied sentence openings, no "Por lo tanto" in every sentence, natural use of "lo que", "de modo que", "es decir".
"""


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
    payload_ids: Optional[List[str]] = None,
    current_translations: Optional[List[Optional[str]]] = None,
    context_hints: Optional[List[str]] = None,
    repair_mode: bool = False,
    review_mode: bool = False,
    glossary: Optional[Dict[str, str]] = None,
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
    prompt_items = []
    for index, text in enumerate(payload_texts):
        item = {
            "id": payload_ids[index] if payload_ids else f"item-{index + 1}",
            "text": text,
        }
        if context_hints and index < len(context_hints) and context_hints[index]:
            item["context"] = context_hints[index]
        prompt_items.append(item)

    prompt = template.format(
        source_language_clause=f" from {source_lang}" if source_lang else "",
        target_language_name=locale_instruction,
        style_instruction=style_instruction,
        quote_instruction=f"- {quote_instruction}" if quote_instruction else "",
        item_count=len(payload_texts),
        payload_json=json.dumps(prompt_items, ensure_ascii=False),
    )
    if target_lang.lower() == "es":
        prompt += "\n" + SPANISH_STYLE_RULES

    relevant_terms = select_glossary_terms(glossary, payload_texts)
    if relevant_terms:
        prompt += "\nMandatory terminology (use these renderings, inflected as needed, whenever the term is used in this sense):\n"
        for source_term, target_term in relevant_terms.items():
            prompt += f"- {source_term} -> {target_term}\n"

    prompt += (
        "\n\nQuality control:\n"
        "- Before finalizing each item, silently check that it is coherent, natural, idiomatic, and consistent with the likely surrounding document.\n"
        "- Fix broken phrasing, unnatural calques, punctuation issues, register mismatches, and formatting glitches before returning the final JSON.\n"
        "- If a literal translation sounds wrong in the target language, rewrite it so it sounds native while preserving meaning and tone.\n"
        "- Keep each item aligned with the surrounding narrative or argument, even when the local sentence is ambiguous.\n"
        "- Never merge one item's content into another item, and never import text from neighboring sections unless it is already present in the source item.\n"
        "- Treat each item's id as binding: return exactly one translation for that same id, with no extra ids and no missing ids.\n"
    )

    if review_mode:
        prompt += (
            "\nReview mode:\n"
            "- You are reviewing an existing translation against the source text.\n"
            "- For each item, keep the current translation if it is already strong, natural, and accurate.\n"
            "- Revise it only when needed to improve fluency, precision, coherence, formatting, tone, or idiomatic quality.\n"
            "- Return the final polished translation array only, not comments or explanations.\n"
            "- Act as a demanding Spanish copy editor. For every item, compare against the source and fix:\n"
            "  omitted or added words, wrong prepositions, literal passives, calques, wrong terminology,\n"
            "  misplaced emphasis markers, non-Spanish quotation marks, and changed punctuation.\n"
        )
        if current_translations:
            prompt += "\nCurrent translations to review:\n"
            for index, current in enumerate(current_translations, start=1):
                if current:
                    item_id = payload_ids[index - 1] if payload_ids else f"item-{index}"
                    prompt += f"- {item_id}: {json.dumps(current, ensure_ascii=False)}\n"

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
                    item_id = payload_ids[index - 1] if payload_ids else f"item-{index}"
                    prompt += f"- {item_id}: {json.dumps(current, ensure_ascii=False)}\n"
        if context_hints:
            visible_hints = [hint for hint in context_hints if hint]
            if visible_hints:
                prompt += "\nOptional context hints:\n"
                for index, hint in enumerate(context_hints, start=1):
                    if hint:
                        item_id = payload_ids[index - 1] if payload_ids else f"item-{index}"
                        prompt += f"- {item_id}: {json.dumps(hint, ensure_ascii=False)}\n"

    return prompt


def load_glossary(path: Optional[Path]) -> Dict[str, str]:
    """Read a glossary file with one "source term = translation" per line.

    Blank lines and lines starting with # are ignored.
    """
    if path is None:
        return {}
    glossary: Dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        source_term, target_term = (part.strip() for part in line.split("=", 1))
        if source_term and target_term:
            glossary[source_term] = target_term
    return glossary


def select_glossary_terms(glossary: Optional[Dict[str, str]], texts: List[str]) -> Dict[str, str]:
    """Only send the terms that appear in this request, to keep prompts short."""
    if not glossary:
        return {}
    haystack = " ".join(texts).lower()
    return {
        source_term: target_term
        for source_term, target_term in glossary.items()
        if re.search(r"\b" + re.escape(source_term.lower()) + r"\b", haystack)
    }
