"""EPUB extraction, parsing, and rebuild helpers."""

import zipfile
from pathlib import Path
from typing import List, Optional, Tuple

from bs4 import BeautifulSoup, Comment
from lxml import etree

from .cache import ProgressCache
from .constants import HTML_EXTS, PACKAGE_EXTS, SKIP_TAGS
from .models import PendingNode
from .utils import is_probably_text, leading_trailing_ws


def extract_epub(epub_path: Path, workdir: Path) -> None:
    with zipfile.ZipFile(epub_path, "r") as archive:
        archive.extractall(workdir)


def rebuild_epub(src_dir: Path, output_epub: Path) -> None:
    if output_epub.exists():
        output_epub.unlink()

    mimetype_path = src_dir / "mimetype"
    if not mimetype_path.exists():
        raise RuntimeError("Invalid EPUB: missing mimetype file at archive root.")

    with zipfile.ZipFile(output_epub, "w") as archive:
        archive.write(mimetype_path, "mimetype", compress_type=zipfile.ZIP_STORED)
        for file_path in sorted(src_dir.rglob("*")):
            if file_path.is_dir():
                continue
            rel_path = file_path.relative_to(src_dir).as_posix()
            if rel_path == "mimetype":
                continue
            archive.write(file_path, rel_path, compress_type=zipfile.ZIP_DEFLATED)


def find_content_files(workdir: Path) -> List[Path]:
    return sorted(path for path in workdir.rglob("*") if path.is_file() and path.suffix.lower() in HTML_EXTS)


def is_navigation_or_package(path: Path) -> bool:
    name = path.name.lower()
    suffix = path.suffix.lower()
    if suffix in PACKAGE_EXTS:
        return True
    if name in {"nav.xhtml", "nav.html", "toc.ncx", "content.opf"}:
        return True
    as_posix = path.as_posix().lower()
    return as_posix.endswith("/nav.xhtml") or as_posix.endswith("/nav.html")


def local_name(tag: object) -> str:
    if isinstance(tag, str) and tag.startswith("{"):
        return etree.QName(tag).localname.lower()
    return str(tag).lower()


def collect_text_slots_xhtml(path: Path) -> List[str]:
    parser = etree.XMLParser(recover=False, remove_blank_text=False)
    root = etree.parse(str(path), parser).getroot()
    slots: List[str] = []

    def walk(elem: etree._Element) -> None:
        tag_name = local_name(elem.tag) if isinstance(elem.tag, str) else ""
        if tag_name not in SKIP_TAGS and elem.text and is_probably_text(elem.text):
            slots.append(elem.text)
        for child in elem:
            walk(child)
            if tag_name not in SKIP_TAGS and child.tail and is_probably_text(child.tail):
                slots.append(child.tail)

    walk(root)
    return slots


def collect_text_slots_fallback(path: Path) -> List[str]:
    original = path.read_text(encoding="utf-8", errors="ignore")
    parser = "lxml-xml" if path.suffix.lower() == ".xhtml" else "lxml"
    soup = BeautifulSoup(original, parser)
    slots: List[str] = []
    for node in soup.find_all(string=True):
        if isinstance(node, Comment):
            continue
        parent = node.parent
        if not parent:
            continue
        if parent.name and parent.name.lower() in SKIP_TAGS:
            continue
        text = str(node)
        if is_probably_text(text):
            slots.append(text)
    return slots


def collect_pending_nodes(workdir: Path, cache: ProgressCache) -> Tuple[List[PendingNode], int, List[str]]:
    pending: List[PendingNode] = []
    cache_hits = 0
    skipped: List[str] = []

    for file_path in find_content_files(workdir):
        rel_path = file_path.relative_to(workdir).as_posix()
        if is_navigation_or_package(file_path):
            skipped.append(rel_path)
            continue

        try:
            slots = collect_text_slots_xhtml(file_path)
        except Exception:
            slots = collect_text_slots_fallback(file_path)

        for node_index, raw_text in enumerate(slots):
            _, core, _ = leading_trailing_ws(raw_text)
            if not is_probably_text(core):
                continue
            if cache.get(core) is None:
                pending.append(PendingNode(rel_path=rel_path, node_index=node_index, core_text=core))
            else:
                cache_hits += 1

    return pending, cache_hits, skipped


def apply_cache_xhtml(path: Path, cache: ProgressCache) -> int:
    parser = etree.XMLParser(recover=False, remove_blank_text=False)
    tree = etree.parse(str(path), parser)
    root = tree.getroot()
    translated_nodes = 0

    def translate_piece(value: str) -> Optional[str]:
        leading, core, trailing = leading_trailing_ws(value)
        if not is_probably_text(core):
            return None
        cached = cache.get(core)
        if cached is None:
            return None
        return leading + cached + trailing

    def walk(elem: etree._Element) -> None:
        nonlocal translated_nodes
        tag_name = local_name(elem.tag) if isinstance(elem.tag, str) else ""
        if tag_name not in SKIP_TAGS and elem.text:
            new_text = translate_piece(elem.text)
            if new_text is not None:
                elem.text = new_text
                translated_nodes += 1
        for child in elem:
            walk(child)
            if tag_name not in SKIP_TAGS and child.tail:
                new_tail = translate_piece(child.tail)
                if new_tail is not None:
                    child.tail = new_tail
                    translated_nodes += 1

    walk(root)
    tree.write(str(path), encoding="utf-8", xml_declaration=True)
    return translated_nodes


def apply_cache_fallback(path: Path, cache: ProgressCache) -> int:
    original = path.read_text(encoding="utf-8", errors="ignore")
    parser = "lxml-xml" if path.suffix.lower() == ".xhtml" else "lxml"
    soup = BeautifulSoup(original, parser)
    changed = False
    translated_nodes = 0

    for node in list(soup.find_all(string=True)):
        if isinstance(node, Comment):
            continue
        parent = node.parent
        if not parent:
            continue
        if parent.name and parent.name.lower() in SKIP_TAGS:
            continue

        old = str(node)
        leading, core, trailing = leading_trailing_ws(old)
        if not is_probably_text(core):
            continue

        cached = cache.get(core)
        if cached is None:
            continue

        node.replace_with(leading + cached + trailing)
        translated_nodes += 1
        changed = True

    if changed:
        path.write_text(str(soup), encoding="utf-8")

    return translated_nodes


def apply_translations(workdir: Path, cache: ProgressCache) -> int:
    translated_nodes = 0
    for file_path in find_content_files(workdir):
        if is_navigation_or_package(file_path):
            continue
        try:
            translated_nodes += apply_cache_xhtml(file_path, cache)
        except Exception:
            translated_nodes += apply_cache_fallback(file_path, cache)
    return translated_nodes
