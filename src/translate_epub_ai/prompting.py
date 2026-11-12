"""Prompt template loading and rendering."""

import json
import re
from dataclasses import dataclass
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
- Use Peninsular Spanish, not Latin American usage: "lo buenas que sean" (not "qué tan buenas sean"), "ordenador" (not "computadora"), "vosotros" forms in dialogue between friends, "coger"/"aparcar"/"móvil" where relevant.
- Choose "ser" or "estar" carefully: states and results take "estar" ("está sujeta a error", not "es sujeta a error").
- Write the result as a native editor from Spain would: varied sentence openings, no "Por lo tanto" in every sentence, natural use of "lo que", "de modo que", "es decir".
"""


CITATION_RULES = """
Cited works and references:
- Never translate titles of cited works (books, articles, papers, journals, films) or people's names. Keep them exactly as in the source, in running text and in notes.
- Items whose context says kind=index are entries of the book's alphabetical index: translate the terms concisely as they would appear in a translated index, keep people's names and titles unchanged, and keep the numbers, commas and other punctuation exactly as they are.
- Items whose context says kind=reference are bibliography or reference-list entries: keep authors, titles, journal names, publishers, places, volume/page numbers and dates exactly as in the source. Translate only generic words such as "ed.", "trans.", "Chapter", "and", "in" and any annotation written in prose.
"""

QUALITY_CONTROL = """
Quality control:
- Before finalizing each item, silently check that it is coherent, natural, idiomatic, and consistent with the likely surrounding document.
- Fix broken phrasing, unnatural calques, punctuation issues, register mismatches, and formatting glitches before returning the final JSON.
- If a literal translation sounds wrong in the target language, rewrite it so it sounds native while preserving meaning and tone.
- Keep each item aligned with the surrounding narrative or argument, even when the local sentence is ambiguous.
- Never merge one item's content into another item, and never import text from neighboring sections unless it is already present in the source item.
- Treat each item's id as binding: return exactly one translation for that same id, with no extra ids and no missing ids.
- If you are genuinely unsure about an item (ambiguous term, wordplay, idiom, a sentence you could not render naturally), add a short "doubt" field to that object explaining why. Leave it out otherwise.
"""

REVIEW_MODE = """
Review mode:
- You are reviewing existing translations against the source text, as a demanding copy editor of the target language.
- Each item has "current_translation". Keep it if it is already accurate and natural; otherwise return an improved version.
- Compare against the source and fix: omitted or added words, wrong prepositions, literal passives, calques, wrong terminology, misplaced emphasis markers, wrong quotation marks and changed punctuation.
- "previous_translation", when present, is the already-approved translation of the paragraph right before this one. Make the item read as a natural continuation of it: pronouns, connectors, tense, terminology and tone must agree.
- "review_notes", when present, explains why the item was flagged. Check those points first, but the flag can be a false alarm: do not change a good translation just because it was flagged.
- Return the final translation array only, without comments.
"""

REPAIR_MODE = """
Repair mode:
- You are revising suspicious or low-quality translated items, not retranslating a whole chapter.
- Each item may have "current_translation". Correct what needs improvement and return the full corrected array in order.
- Prioritize fixing broken formatting or markers, lost meaning, untranslated text, awkward wording and inconsistencies with the rest of the document.
"""


@dataclass(frozen=True)
class PromptParts:
    """A prompt split into a stable prefix and the per-request part.

    `instructions` is identical for every request of a run (same mode and
    languages), so providers can cache it (Anthropic cache_control, OpenAI
    automatic prefix caching, Ollama KV-cache reuse). `request` holds the
    items and anything that changes per request.
    """

    instructions: str
    request: str

    @property
    def full(self) -> str:
        return f"{self.instructions.rstrip()}\n\n{self.request.lstrip()}"


def load_prompt_template(prompt_file: Optional[Path]) -> str:
    if prompt_file is not None:
        return prompt_file.read_text(encoding="utf-8")
    return resources.files("translate_epub_ai.prompts").joinpath("default_prompt.txt").read_text(encoding="utf-8")


def split_template(template: str) -> tuple[str, str]:
    """Split at the first line that depends on the request (item count or payload)."""
    lines = template.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if "{item_count}" in line or "{payload_json}" in line:
            return "".join(lines[:index]), "".join(lines[index:])
    return template, "{payload_json}"


def build_prompt_parts(
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
    previous_translations: Optional[List[Optional[str]]] = None,
    review_notes: Optional[List[str]] = None,
) -> PromptParts:
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

    def optional(values: Optional[List], index: int):
        if values and index < len(values):
            return values[index]
        return None

    prompt_items = []
    for index, text in enumerate(payload_texts):
        item = {
            "id": payload_ids[index] if payload_ids else f"item-{index + 1}",
            "text": text,
        }
        if optional(context_hints, index):
            item["context"] = context_hints[index]
        if (review_mode or repair_mode) and optional(current_translations, index):
            item["current_translation"] = current_translations[index]
        if review_mode and optional(previous_translations, index):
            item["previous_translation"] = previous_translations[index]
        if (review_mode or repair_mode) and optional(review_notes, index):
            item["review_notes"] = review_notes[index]
        prompt_items.append(item)

    fields = {
        "source_language_clause": f" from {source_lang}" if source_lang else "",
        "target_language_name": locale_instruction,
        "style_instruction": style_instruction,
        "quote_instruction": f"- {quote_instruction}" if quote_instruction else "",
        "item_count": len(payload_texts),
        "payload_json": json.dumps(prompt_items, ensure_ascii=False),
    }
    static_template, request_template = split_template(load_prompt_template(prompt_file))

    instructions = static_template.format(**fields).rstrip() + "\n" + CITATION_RULES
    if target_lang.lower() == "es":
        instructions += SPANISH_STYLE_RULES
    instructions += QUALITY_CONTROL
    if review_mode:
        instructions += REVIEW_MODE
    if repair_mode:
        instructions += REPAIR_MODE

    request = request_template.format(**fields)
    relevant_terms = select_glossary_terms(glossary, payload_texts)
    if relevant_terms:
        request += (
            "\n\nMandatory terminology (use these renderings, inflected as needed, "
            "whenever the term is used in this sense):\n"
        )
        for source_term, target_term in relevant_terms.items():
            request += f"- {source_term} -> {target_term}\n"

    return PromptParts(instructions=instructions, request=request)


def build_translation_prompt(*args, **kwargs) -> str:
    """Single-string prompt (instructions followed by the request)."""
    return build_prompt_parts(*args, **kwargs).full


def load_glossary(path: Optional[Path]) -> Dict[str, str]:
    """Read a glossary file.

    One entry per line: ``source term = translation`` or, for words that
    also have an everyday meaning, ``source term = translation | when it
    means ...``. The note is shown to the model so it only applies the
    rendering in that sense. Blank lines and lines starting with # are
    ignored.
    """
    if path is None:
        return {}
    glossary: Dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        source_term, target_part = (part.strip() for part in line.split("=", 1))
        target_term, _, note = (part.strip() for part in target_part.partition("|"))
        if source_term and target_term:
            glossary[source_term] = f"{target_term} [only when: {note}]" if note else target_term
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
