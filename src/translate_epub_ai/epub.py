"""EPUB extraction, parsing, and rebuild helpers."""

import zipfile
from pathlib import Path
from typing import List, Optional, Tuple

from .cache import ProgressCache
from .constants import HTML_EXTS, PACKAGE_EXTS
from .models import PendingNode
from .segments import build_context_hint, decode_into, iter_segments, parse_document
from .utils import log


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


def collect_pending_nodes(workdir: Path, cache: ProgressCache) -> Tuple[List[PendingNode], int, List[str]]:
    """Collect one pending item per paragraph-like segment (see segments.py)."""
    pending: List[PendingNode] = []
    cache_hits = 0
    skipped: List[str] = []
    queued: set[str] = set()

    for file_path in find_content_files(workdir):
        rel_path = file_path.relative_to(workdir).as_posix()
        if is_navigation_or_package(file_path):
            skipped.append(rel_path)
            continue

        tree, _ = parse_document(file_path)
        segments = iter_segments(tree.getroot())
        for index, segment in enumerate(segments):
            if cache.get(segment.encoded) is not None:
                cache_hits += 1
                continue
            if segment.encoded in queued:
                # Same paragraph appears twice (e.g. repeated epigraph):
                # translate it once, apply it everywhere.
                continue
            queued.add(segment.encoded)
            pending.append(
                PendingNode(
                    rel_path=rel_path,
                    node_index=index,
                    core_text=segment.encoded,
                    context_hint=build_context_hint(segments, index),
                )
            )

    return pending, cache_hits, skipped


def apply_translations(workdir: Path, cache: ProgressCache) -> int:
    translated_segments = 0
    for file_path in find_content_files(workdir):
        if is_navigation_or_package(file_path):
            continue
        tree, save = parse_document(file_path)
        changed = False
        for segment in iter_segments(tree.getroot()):
            cached = cache.get(segment.encoded)
            if cached is None:
                continue
            if decode_into(segment, cached):
                translated_segments += 1
                changed = True
            else:
                log(
                    f"Kept original text in {file_path.name}: cached translation has mismatched inline markers"
                )
        if changed:
            save()
    return translated_segments
