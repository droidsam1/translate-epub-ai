"""Build a starting glossary for any book.

1. Find candidate terms locally (no model calls):
   - words the author emphasises (italics/bold), typical for defined concepts;
   - capitalised multi-word names of concepts ("Principle of Mediocrity");
   - content-word phrases that recur throughout the book.
2. Ask the model which candidates are real terms that need a consistent
   translation, and how to translate them (with a short "sense" note when the
   word also has an everyday meaning).
3. Write an editable text file in the same format as hand-made glossaries.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional

from .models import PendingNode
from .segments import MARKER_RE
from .utils import clean_json_text, log
from .workflow import extract_context_kind

EMPHASIS_RE = re.compile(r"<(\d+)>([^<]{3,60})</\1>")
CAPITALISED_TERM_RE = re.compile(
    r"(?<![.!?:;]\s)(?<!^)\b([A-Z][a-z]+(?:\s+(?:of|the|and|to|in)\s+[A-Z][a-z]+|\s+[A-Z][a-z]+){1,3})\b"
)
WORD_RE = re.compile(r"[A-Za-z][A-Za-z'’-]*")
STOPWORDS = set(
    """a an the and or but if then than so of to in on at by for with from as into onto about over under
    between through during before after above below up down out off again further once here there when where
    why how all any both each few more most other some such no nor not only own same too very can will just
    should now is are was were be been being have has had having do does did doing it its itself this that
    these those i me my we our you your he him his she her they them their what which who whom would could
    may might must shall also one two even much many like well because while however thus hence yet still
    i'm it's that's there's don't can't isn't""".split()
)

GLOSSARY_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "term": {"type": "string"},
            "translation": {"type": "string"},
            "note": {"type": "string"},
        },
        "required": ["term", "translation"],
    },
}


def plain(text: str) -> str:
    return " ".join(MARKER_RE.sub(" ", text).split())


def extract_candidates(nodes: List[PendingNode], limit: int = 120) -> List[Dict[str, object]]:
    texts = [
        node.core_text
        for node in nodes
        if extract_context_kind(node.context_hint) == "paragraph"
    ]
    plain_texts = [plain(text) for text in texts]
    book_lower = "\n".join(plain_texts).lower()

    emphasis_counts: Counter = Counter()
    for text in texts:
        for _, phrase in EMPHASIS_RE.findall(text):
            phrase = phrase.strip(" ,.;:’'‘\"").lower()
            words = phrase.split()
            if not 1 <= len(words) <= 4 or not all(WORD_RE.fullmatch(word) for word in words):
                continue
            if words[0] in STOPWORDS or words[-1] in STOPWORDS:
                continue
            emphasis_counts[phrase] += 1

    scores: Counter = Counter()
    for phrase, emphasis in emphasis_counts.items():
        if " " not in phrase and emphasis < 2:
            continue  # a single word stressed once is emphasis, not a term
        occurrences = len(re.findall(r"\b" + re.escape(phrase) + r"\b", book_lower))
        if occurrences >= 2:
            scores[phrase] += min(occurrences, 100) + 10 * emphasis

    for text in plain_texts:
        for match in CAPITALISED_TERM_RE.finditer(text):
            scores[match.group(1)] += 3

    gram_counts: Counter = Counter()
    for text in plain_texts:
        words = [word.lower() for word in WORD_RE.findall(text)]
        for size in (2, 3):
            for index in range(len(words) - size + 1):
                gram = words[index : index + size]
                if gram[0] in STOPWORDS or gram[-1] in STOPWORDS:
                    continue
                if sum(word in STOPWORDS for word in gram) > 1:
                    continue
                gram_counts[" ".join(gram)] += 1
    for gram, count in gram_counts.items():
        if count >= 5:
            scores[gram] += min(count, 100)

    # Merge variants that differ only in case, keeping the capitalised form.
    counts: Counter = Counter()
    display: Dict[str, str] = {}
    for term, score in scores.items():
        key = term.lower()
        counts[key] += score
        if key not in display or term != key:
            display[key] = term
    counts = Counter({display[key]: score for key, score in counts.items()})

    candidates = []
    for term, count in counts.most_common():
        if count < 6:
            continue
        example = next((text for text in plain_texts if term.lower() in text.lower()), "")
        position = example.lower().find(term.lower())
        snippet = example[max(0, position - 80) : position + len(term) + 80].strip()
        candidates.append({"term": term, "occurrences": count, "example": snippet})
        if len(candidates) >= limit:
            break
    return candidates


def glossary_instructions(source_lang: Optional[str], target_lang: str) -> str:
    source = source_lang or "the source language"
    return (
        f"You are preparing a terminology glossary for translating a book from {source} into {target_lang}.\n"
        "You receive candidate expressions found in the book, each with an example sentence.\n"
        "Keep ONLY real terms that a professional translator would fix in a glossary so they are translated "
        "consistently: concepts the author defines or relies on, technical terms, coined expressions, named "
        "principles or theories. Skip ordinary phrases, people's names, places and titles of works.\n"
        "For each kept term give its established translation in the target language (use the standard term "
        "of the field when one exists). Add a short \"note\" in English ONLY when the word also has an "
        "everyday meaning, describing the specialised sense in which the translation applies.\n"
        "Return ONLY a JSON array of objects with \"term\", \"translation\" and optional \"note\"."
    )


def build_glossary(
    provider,
    nodes: List[PendingNode],
    model: str,
    source_lang: Optional[str],
    target_lang: str,
    output_path: Path,
    max_output_tokens: int = 4096,
    chunk_size: int = 40,
) -> Path:
    candidates = extract_candidates(nodes)
    log(f"Glossary: {len(candidates)} candidate terms found; asking {model} to choose and translate them.")
    instructions = glossary_instructions(source_lang, target_lang)
    entries: Dict[str, Dict[str, str]] = {}
    for start in range(0, len(candidates), chunk_size):
        chunk = candidates[start : start + chunk_size]
        request = json.dumps(chunk, ensure_ascii=False)
        try:
            raw = provider.complete(instructions, request, model, max_output_tokens, GLOSSARY_SCHEMA)
            items = json.loads(clean_json_text(raw))
        except Exception as error:  # one bad chunk should not lose the rest
            log(f"Glossary: chunk {start // chunk_size + 1} failed ({error}); skipping it.")
            continue
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue
            term = str(item.get("term", "")).strip()
            translation = str(item.get("translation", "")).strip()
            if term and translation and "=" not in term and "|" not in translation:
                entries[term] = {"translation": translation, "note": str(item.get("note") or "").strip()}

    lines = [
        "# Glossary generated automatically by translate-epub-ai (--auto-glossary).",
        "# Review it before translating: fix renderings, delete wrong entries, add missing terms.",
        "# Format: source term = translation | optional note on the sense in which it applies",
        "",
    ]
    for term in sorted(entries, key=str.lower):
        entry = entries[term]
        note = f" | {entry['note']}" if entry["note"] else ""
        lines.append(f"{term} = {entry['translation']}{note}")
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log(f"Glossary: wrote {len(entries)} terms to {output_path}")
    return output_path


def default_glossary_path(input_epub: Path, target_lang: str) -> Path:
    return input_epub.with_name(f"{input_epub.stem}.glossary.{target_lang.lower()}.txt")
