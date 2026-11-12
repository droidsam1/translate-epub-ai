import tempfile
import unittest
from pathlib import Path

from lxml import etree

from translate_epub_ai.cache import ProgressCache
from translate_epub_ai.epub import apply_translations, collect_pending_nodes
from translate_epub_ai.segments import iter_segments, validate_markers

XHTML = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>T</title></head><body>
<h1>Chapter <em>One</em></h1>
<p>The <em>principle of testability</em> was key.<sup><a href="#n1">12</a></sup> But this misses the point.</p>
<div>Loose text <p>Inner <b>bold <i>nested</i></b>.</p> tail text</div>
</body></html>
"""


def write_chapter(directory: Path) -> Path:
    path = directory / "ch.xhtml"
    path.write_text(XHTML, encoding="utf-8")
    return path


class SegmentTests(unittest.TestCase):
    def test_paragraph_with_inline_markup_is_one_segment(self) -> None:
        root = etree.fromstring(XHTML.encode("utf-8"))
        encoded = [segment.encoded for segment in iter_segments(root)]
        self.assertIn(
            "The <1>principle of testability</1> was key.<2/> But this misses the point.",
            encoded,
        )
        self.assertIn("Chapter <1>One</1>", encoded)
        self.assertIn("Inner <1>bold <2>nested</2></1>.", encoded)
        self.assertIn("Loose text", encoded)
        self.assertIn("tail text", encoded)

    def test_validate_markers(self) -> None:
        source = "a <1>b <2>c</2></1> d<3/>"
        self.assertIsNone(validate_markers(source, "x<3/> <1>y <2>z</2></1>"))
        self.assertIsNotNone(validate_markers(source, "x <1>y z</1><3/>"))
        self.assertIsNotNone(validate_markers(source, "<2>x <1>y</2></1><3/>"))
        self.assertIsNotNone(validate_markers(source, "x <1>y <2>z</2></1> <3/><4/>"))

    def test_round_trip_moves_formatting_with_reordered_words(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp)
            chapter = write_chapter(workdir)
            cache = ProgressCache(workdir / "cache.json")
            pending, _, _ = collect_pending_nodes(workdir, cache)
            self.assertEqual(5, len(pending))
            self.assertIn('prev="Chapter One"', pending[1].context_hint)

            cache.set(
                "The <1>principle of testability</1> was key.<2/> But this misses the point.",
                "El <1>principio de falsabilidad</1> era clave.<2/> Pero esto no da en el clavo.",
            )
            cache.set("Chapter <1>One</1>", "Capítulo <1>uno</1>")
            cache.set("Inner <1>bold <2>nested</2></1>.", "Interior <1>negrita <2>anidada</2></1>.")
            cache.set("tail text", "texto final")
            cache.set("Loose text", "<1>roto</1>")  # invalid: must be ignored

            applied = apply_translations(workdir, cache)
            self.assertEqual(4, applied)
            output = chapter.read_text(encoding="utf-8")
            self.assertIn("<h1>Capítulo <em>uno</em></h1>", output)
            self.assertIn(
                '<p>El <em>principio de falsabilidad</em> era clave.<sup><a href="#n1">12</a></sup> '
                "Pero esto no da en el clavo.</p>",
                output,
            )
            self.assertIn("Interior <b>negrita <i>anidada</i></b>.", output)
            self.assertIn("Loose text", output)
            self.assertIn("texto final", output)

    def test_salvage_markers_always_returns_valid_markers(self) -> None:
        from translate_epub_ai.segments import salvage_markers

        cases = [
            ("Spartan <1/>, <2/>–<3/>, <4/>–<5/>", "espartano <1>, <2>–<3>, <4>–<5/>"),
            ("<1>By the way, this is never correct.</1>", "<1>Por cierto, esto nunca es correcto.<1>"),
            (
                "Next, philosophers moved away from <1/>trying to <2>understand</2> anything.",
                "Después, los filósofos dejaron de <1>intentar <2>comprender <2>nada</2>.",
            ),
        ]
        for source, broken in cases:
            fixed = salvage_markers(source, broken)
            self.assertIsNone(validate_markers(source, fixed), fixed)
        self.assertTrue(salvage_markers(*cases[1]).startswith("<1>Por cierto"))
        self.assertTrue(salvage_markers(*cases[1]).endswith("correcto.</1>"))


if __name__ == "__main__":
    unittest.main()
