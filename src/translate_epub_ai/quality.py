"""Find weak spots in a finished translation, so the review pass can focus on them.

Every check is cheap and local (no model calls). Most are language-agnostic:
they compare the translation with its own source, or with the rest of the
book, instead of relying on fixed per-language thresholds. Each check returns
a short note that is passed to the reviewing model, so it knows what to look
at. A flag is a reason to look, not proof of an error.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional

from .segments import MARKER_RE, validate_markers

WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)
NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)*")
SENTENCE_END_RE = re.compile(r"[.!?…](?:[»”’\"')\]]*)(?=\s|$)")

# Paragraph openings that refer back to the previous paragraph. If the
# previous paragraph was translated in another request, these are the
# places where pronouns, connectors or terminology can drift.
BACK_REFERENCE_OPENINGS = {
    "en": {
        "this", "that", "these", "those", "it", "its", "he", "she", "they", "them", "such",
        "but", "so", "thus", "hence", "however", "nevertheless", "nonetheless", "yet",
        "and", "or", "nor", "then", "therefore", "moreover", "furthermore", "indeed",
        "instead", "similarly", "likewise", "also", "again", "still", "consequently",
        "accordingly", "otherwise", "meanwhile", "conversely", "rather",
    },
}


@dataclass(frozen=True)
class ReviewItem:
    source: str
    translation: str
    kind: str = "paragraph"
    doubt: Optional[str] = None


def plain(text: str) -> str:
    return " ".join(MARKER_RE.sub(" ", text).split())


def words(text: str) -> List[str]:
    return WORD_RE.findall(plain(text))


def length_ratio(item: ReviewItem) -> Optional[float]:
    source = plain(item.source)
    if len(source) < 80:
        return None
    return len(plain(item.translation)) / len(source)


def book_length_ratio(items: Iterable[ReviewItem]) -> Optional[float]:
    ratios = [ratio for ratio in (length_ratio(item) for item in items) if ratio]
    return statistics.median(ratios) if len(ratios) >= 20 else None


def sentence_count(text: str) -> int:
    return len(SENTENCE_END_RE.findall(plain(text)))


def check_length(item: ReviewItem, typical_ratio: Optional[float]) -> Optional[str]:
    ratio = length_ratio(item)
    if ratio is None or typical_ratio is None:
        return None
    relative = ratio / typical_ratio
    if relative < 0.7:
        return f"much shorter than usual for this book ({relative:.0%} of the typical length): possible omission"
    if relative > 1.45:
        return f"much longer than usual for this book ({relative:.0%} of the typical length): possible addition or paraphrase"
    return None


def check_untranslated(item: ReviewItem) -> Optional[str]:
    source_words = {word.lower() for word in words(item.source) if len(word) >= 4}
    target_words = [word.lower() for word in words(item.translation) if len(word) >= 4]
    if len(source_words) < 6 or len(target_words) < 6:
        return None
    shared = sum(1 for word in target_words if word in source_words)
    if shared / len(target_words) > 0.35:
        return "many words identical to the source: parts may be left untranslated"
    return None


def check_numbers(item: ReviewItem) -> Optional[str]:
    def numbers(text: str) -> List[str]:
        return [re.sub(r"[.,]", "", number) for number in NUMBER_RE.findall(plain(text))]

    target = numbers(item.translation)
    missing = [number for number in numbers(item.source) if number not in target]
    if missing:
        return f"numbers missing or changed: {', '.join(sorted(set(missing))[:5])}"
    return None


def check_sentences(item: ReviewItem) -> Optional[str]:
    source_count = sentence_count(item.source)
    target_count = sentence_count(item.translation)
    if source_count >= 3 and abs(source_count - target_count) >= max(2, round(source_count * 0.4)):
        return (
            f"{source_count} sentences in the source but {target_count} in the translation: "
            "check for dropped, merged or split content"
        )
    return None


def check_repetition(item: ReviewItem) -> Optional[str]:
    match = re.search(r"\b(\w{3,})\s+\1\b", plain(item.translation), flags=re.IGNORECASE)
    if match:
        return f'repeated word "{match.group(1)}"'
    return None


def check_balance(item: ReviewItem) -> Optional[str]:
    text = plain(item.translation)
    for left, right in (("«", "»"), ("(", ")"), ("“", "”"), ("¿", "?"), ("¡", "!")):
        if left in ("¿", "¡"):
            if text.count(left) > text.count(right):
                return f"unbalanced {left}{right}"
        elif text.count(left) != text.count(right):
            return f"unbalanced {left}{right}"
    return None


def glossary_stems(translation: str) -> List[str]:
    """Stems of the glossary rendering, tolerant to gender/number inflection."""
    rendering = re.sub(r"\[.*?\]", "", translation)
    return [word.lower()[: max(4, len(word) - 2)] for word in WORD_RE.findall(rendering) if len(word) >= 4]


def check_glossary(item: ReviewItem, glossary: Optional[Dict[str, str]]) -> Optional[str]:
    if not glossary:
        return None
    source = plain(item.source).lower()
    target = plain(item.translation).lower()
    misses = []
    for term, rendering in glossary.items():
        if not re.search(r"\b" + re.escape(term.lower()) + r"\b", source):
            continue
        stems = glossary_stems(rendering)
        if stems and not all(stem in target for stem in stems):
            misses.append(f'"{term}" -> "{re.sub(r"\[.*?\]", "", rendering).strip()}"')
    if misses:
        return "glossary terms not used: " + ", ".join(misses[:4])
    return None


def check_back_reference(item: ReviewItem, source_lang: Optional[str]) -> Optional[str]:
    openings = BACK_REFERENCE_OPENINGS.get((source_lang or "en").lower())
    first_words = words(item.source)[:1]
    if openings and first_words and first_words[0].lower() in openings:
        return "opens by referring back to the previous paragraph: check it reads as a continuation"
    return None


def detect_weak_points(
    item: ReviewItem,
    typical_ratio: Optional[float] = None,
    glossary: Optional[Dict[str, str]] = None,
    source_lang: Optional[str] = "en",
    include_back_references: bool = True,
) -> List[str]:
    notes: List[str] = []
    marker_issue = validate_markers(item.source, item.translation)
    if marker_issue:
        notes.append(marker_issue)
    if item.doubt:
        notes.append(f"the translator was unsure: {item.doubt}")
    if item.kind in {"reference", "index"}:
        return notes

    checks = [
        check_length(item, typical_ratio),
        check_untranslated(item),
        check_numbers(item),
        check_sentences(item),
        check_repetition(item),
        check_balance(item),
        check_glossary(item, glossary),
    ]
    notes.extend(note for note in checks if note)
    if include_back_references and item.kind == "paragraph":
        note = check_back_reference(item, source_lang)
        if note:
            notes.append(note)
    return notes
