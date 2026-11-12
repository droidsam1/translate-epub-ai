"""Paragraph-level segmentation with inline-markup placeholders.

Instead of translating every XHTML text node on its own (which splits a
sentence at every <em>, <a>, <sup>…), each block element whose children are
all inline is turned into ONE segment. Inline children are replaced by
numbered markers so the model sees the full sentence:

    <p>The <em>principle</em> was key.<sup><a href="#n1">12</a></sup></p>
    ->  "The <1>principle</1> was key.<2/>"

Wrapping markers (<N>…</N>) hold translatable text; self-closing markers
(<N/>) stand for elements copied verbatim (footnote refs, images, code…).
After translation the markers are mapped back onto copies of the original
elements, so formatting survives even when the word order changes.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from lxml import etree

from .constants import BLOCK_TAGS, SKIP_TAGS
from .utils import is_probably_text, leading_trailing_ws

MARKER_RE = re.compile(r"<(/?)(\d+)(/?)>")


def local_name(tag: object) -> str:
    if isinstance(tag, str):
        if tag.startswith("{"):
            return etree.QName(tag).localname.lower()
        return tag.lower()
    return ""


def is_element(node: object) -> bool:
    return isinstance(node, etree._Element) and isinstance(node.tag, str)


def is_block(elem: etree._Element) -> bool:
    return local_name(elem.tag) in BLOCK_TAGS


def has_block_child(elem: etree._Element) -> bool:
    return any(is_element(child) and is_block(child) for child in elem)


def plain_text(elem: etree._Element) -> str:
    return "".join(elem.itertext())


def is_atomic(elem: etree._Element) -> bool:
    """Inline elements copied verbatim instead of translated."""
    if local_name(elem.tag) in SKIP_TAGS or local_name(elem.tag) == "br":
        return True
    text = plain_text(elem)
    # Footnote references, page anchors, empty spans: nothing to translate.
    return not any(char.isalpha() for char in text)


@dataclass
class Segment:
    element: etree._Element
    encoded: str
    markers: Dict[int, etree._Element] = field(default_factory=dict)
    atomic_ids: List[int] = field(default_factory=list)
    leading_ws: str = ""
    trailing_ws: str = ""

    @property
    def kind(self) -> str:
        name = local_name(self.element.tag)
        if re.fullmatch(r"h[1-6]", name):
            return "heading"
        return "paragraph"

    @property
    def readable(self) -> str:
        return strip_markers(self.encoded)


def strip_markers(text: str) -> str:
    return " ".join(MARKER_RE.sub("", text).split())


def encode_segment(elem: etree._Element) -> Optional[Segment]:
    markers: Dict[int, etree._Element] = {}
    atomic_ids: List[int] = []
    counter = [0]

    def encode_children(parent: etree._Element) -> str:
        parts: List[str] = [parent.text or ""]
        for child in parent:
            if is_element(child):
                counter[0] += 1
                marker_id = counter[0]
                markers[marker_id] = child
                if is_atomic(child):
                    atomic_ids.append(marker_id)
                    parts.append(f"<{marker_id}/>")
                else:
                    parts.append(f"<{marker_id}>{encode_children(child)}</{marker_id}>")
            # Comments / processing instructions are dropped from the text
            # but their tails still count.
            parts.append(child.tail or "")
        return "".join(parts)

    raw = encode_children(elem)
    if not is_probably_text(strip_markers(raw)):
        return None

    leading, core, trailing = leading_trailing_ws(raw)
    # Collapse the internal whitespace/newlines that come from XHTML pretty
    # printing; the model sees one clean paragraph.
    core = re.sub(r"\s+", " ", core)
    return Segment(
        element=elem,
        encoded=core,
        markers=markers,
        atomic_ids=atomic_ids,
        leading_ws=leading,
        trailing_ws=trailing,
    )


def iter_segments(root: etree._Element) -> List[Segment]:
    """Return translatable segments in document order.

    Blocks with inline-only content become one segment. Containers that mix
    block children with loose text get one segment per loose text run, so
    nothing is lost.
    """
    segments: List[Segment] = []

    def walk(elem: etree._Element) -> None:
        if not is_element(elem) or local_name(elem.tag) in SKIP_TAGS:
            return
        if not has_block_child(elem):
            segment = encode_segment(elem)
            if segment is not None:
                segments.append(segment)
            return

        # Mixed container: loose text before/after block children.
        if elem.text and is_probably_text(elem.text):
            segments.append(_loose_text_segment(elem, None))
        for child in elem:
            if is_element(child):
                if is_block(child):
                    walk(child)
                elif local_name(child.tag) not in SKIP_TAGS and is_probably_text(plain_text(child)):
                    # Inline element sitting directly in a container.
                    wrapper_segment = encode_inline_in_container(child)
                    if wrapper_segment is not None:
                        segments.append(wrapper_segment)
            if child.tail and is_probably_text(child.tail):
                segments.append(_loose_text_segment(elem, child))

    walk(root)
    return segments


@dataclass
class LooseTextSegment(Segment):
    """A bare text run (elem.text or child.tail) inside a mixed container."""

    owner: Optional[etree._Element] = None
    tail_of: Optional[etree._Element] = None


def _loose_text_segment(container: etree._Element, tail_of: Optional[etree._Element]) -> Segment:
    raw = tail_of.tail if tail_of is not None else container.text
    leading, core, trailing = leading_trailing_ws(raw or "")
    return LooseTextSegment(
        element=container,
        encoded=re.sub(r"\s+", " ", core),
        leading_ws=leading,
        trailing_ws=trailing,
        owner=container,
        tail_of=tail_of,
    )


def encode_inline_in_container(elem: etree._Element) -> Optional[Segment]:
    # Treat the inline element itself as a tiny block (its own text + children).
    return encode_segment(elem)


def validate_markers(source_encoded: str, translated: str) -> Optional[str]:
    """Return a description of the problem, or None if markers line up."""
    source_tokens = sorted(m.group(0) for m in MARKER_RE.finditer(source_encoded))
    translated_tokens = sorted(m.group(0) for m in MARKER_RE.finditer(translated))
    if source_tokens != translated_tokens:
        missing = sorted(set(source_tokens) - set(translated_tokens))
        extra = sorted(set(translated_tokens) - set(source_tokens))
        details = []
        if missing:
            details.append(f"missing {' '.join(missing)}")
        if extra:
            details.append(f"unexpected {' '.join(extra)}")
        if not details:
            details.append("marker count mismatch")
        return "inline markers do not match source (" + ", ".join(details) + ")"

    stack: List[str] = []
    for match in MARKER_RE.finditer(translated):
        closing, marker_id, self_closing = match.groups()
        if self_closing:
            continue
        if closing:
            if not stack or stack[-1] != marker_id:
                return f"inline marker </{marker_id}> is not properly nested"
            stack.pop()
        else:
            stack.append(marker_id)
    if stack:
        return "inline markers left unclosed"
    return None


def decode_into(segment: Segment, translated: str) -> bool:
    """Write a translated segment back into the tree. Returns False if the
    markers are invalid (the original content is then left untouched)."""
    if isinstance(segment, LooseTextSegment):
        if MARKER_RE.search(translated):
            return False
        value = segment.leading_ws + translated + segment.trailing_ws
        if segment.tail_of is not None:
            segment.tail_of.tail = value
        else:
            segment.owner.text = value
        return True

    if validate_markers(segment.encoded, translated) is not None:
        return False

    elem = segment.element
    for child in list(elem):
        elem.remove(child)
    elem.text = segment.leading_ws or None

    stack: List[etree._Element] = [elem]
    tokens = _tokenize(translated)
    cursor: Tuple[etree._Element, str] = (elem, "text")

    def append_text(value: str) -> None:
        node, slot = cursor
        current = getattr(node, slot) or ""
        setattr(node, slot, current + value)

    for kind, value in tokens:
        if kind == "text":
            append_text(value)
            continue
        marker_id = int(value)
        original = segment.markers[marker_id]
        if kind == "self":
            clone = copy.deepcopy(original)
            clone.tail = None
            stack[-1].append(clone)
            cursor = (clone, "tail")
        elif kind == "open":
            clone = _shallow_clone(original)
            stack[-1].append(clone)
            stack.append(clone)
            cursor = (clone, "text")
        else:  # close
            closed = stack.pop()
            cursor = (closed, "tail")
    if segment.trailing_ws:
        append_text(segment.trailing_ws)
    return True


def _shallow_clone(original: etree._Element) -> etree._Element:
    clone = copy.deepcopy(original)
    for child in list(clone):
        clone.remove(child)
    clone.text = None
    clone.tail = None
    return clone


def _tokenize(text: str) -> List[Tuple[str, str]]:
    tokens: List[Tuple[str, str]] = []
    position = 0
    for match in MARKER_RE.finditer(text):
        if match.start() > position:
            tokens.append(("text", text[position : match.start()]))
        closing, marker_id, self_closing = match.groups()
        if self_closing:
            tokens.append(("self", marker_id))
        elif closing:
            tokens.append(("close", marker_id))
        else:
            tokens.append(("open", marker_id))
        position = match.end()
    if position < len(text):
        tokens.append(("text", text[position:]))
    return tokens


REFERENCE_FILE_RE = re.compile(
    r"(biblio|references?|further[-_ ]?reading|works[-_ ]?cited|sources|citations?)", re.IGNORECASE
)
INDEX_FILE_RE = re.compile(r"(^|[/_-])index\d*\.x?html?$", re.IGNORECASE)
REFERENCE_HEADINGS = {
    "bibliography", "selected bibliography", "references", "further reading", "works cited",
    "sources", "suggested reading", "recommended reading",
    "bibliografía", "referencias", "lecturas recomendadas", "obras citadas", "fuentes",
    "bibliographie", "références", "literatur", "literaturverzeichnis", "bibliografia",
}
INDEX_HEADINGS = {"index", "índice analítico", "índice alfabético", "register", "indice"}


def section_kinds(rel_path: str, segments: List[Segment]) -> List[str]:
    """Label each segment as "reference", "index" or "" (normal text).

    Uses the file name and the section headings, so it works for any book:
    a "Bibliography" heading switches to reference mode until the next
    heading that is not a reference heading.
    """
    file_name = rel_path.rsplit("/", 1)[-1]
    file_kind = ""
    if INDEX_FILE_RE.search(file_name):
        file_kind = "index"
    elif REFERENCE_FILE_RE.search(file_name):
        file_kind = "reference"

    kinds: List[str] = []
    current = file_kind
    for segment in segments:
        if segment.kind == "heading":
            heading = segment.readable.strip().rstrip(":").lower()
            if heading in REFERENCE_HEADINGS:
                current = "reference"
            elif heading in INDEX_HEADINGS:
                current = "index"
            elif not file_kind:
                current = ""
            kinds.append("")  # headings are always translated normally
            continue
        kinds.append(current)
    return kinds


def build_context_hint(
    segments: List[Segment], index: int, limit: int = 240, section_kind: str = ""
) -> str:
    def snippet(text: str) -> str:
        return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"

    pieces = [f"kind={section_kind or segments[index].kind}"]
    if index > 0:
        pieces.append(f'prev="{snippet(segments[index - 1].readable)}"')
    if index + 1 < len(segments):
        pieces.append(f'next="{snippet(segments[index + 1].readable)}"')
    return "; ".join(pieces)


def parse_document(path, write: bool = False) -> Tuple[etree._ElementTree, Callable[[], None]]:
    """Parse XHTML strictly, falling back to the lenient HTML parser."""
    try:
        parser = etree.XMLParser(recover=False, remove_blank_text=False, resolve_entities=False)
        tree = etree.parse(str(path), parser)

        def save() -> None:
            tree.write(str(path), encoding="utf-8", xml_declaration=True)

    except etree.XMLSyntaxError:
        parser = etree.HTMLParser(encoding="utf-8")
        tree = etree.parse(str(path), parser)

        def save() -> None:
            tree.write(str(path), encoding="utf-8", method="html")

    return tree, save
