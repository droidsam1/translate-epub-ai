import json
import tempfile
import unittest
from pathlib import Path

from lxml import etree

from translate_epub_ai.cache import ProgressCache
from translate_epub_ai.glossary_builder import build_glossary, extract_candidates
from translate_epub_ai.models import PendingNode
from translate_epub_ai.prompting import load_glossary
from translate_epub_ai.quality import ReviewItem, detect_weak_points
from translate_epub_ai.segments import iter_segments, section_kinds
from translate_epub_ai.workflow import build_review_candidates


class WeakPointTests(unittest.TestCase):
    def test_good_translation_has_no_flags(self) -> None:
        item = ReviewItem(
            "The <1>principle</1> was key in 1959. But it misses the point.",
            "El <1>principio</1> fue clave en 1959. Pero no da en el clavo.",
        )
        self.assertEqual([], detect_weak_points(item, include_back_references=False))

    def test_flags_marker_number_and_untranslated_problems(self) -> None:
        notes = detect_weak_points(
            ReviewItem("It happened in 1959, as <1>Popper</1> said.", "Ocurrió en 1950, como dijo Popper.")
        )
        joined = " ".join(notes)
        self.assertIn("inline markers", joined)
        self.assertIn("1959", joined)

        untranslated = detect_weak_points(
            ReviewItem(
                "Explanations are essential for arriving at a rule of thumb in the first place.",
                "Las explanations are essential for arriving at a rule of thumb in the first place.",
            )
        )
        self.assertTrue(any("untranslated" in note for note in untranslated))

    def test_flags_dropped_sentences_and_glossary_misses(self) -> None:
        source = "One idea. Two ideas. Three ideas. Four ideas. Five ideas about fallibilism."
        notes = detect_weak_points(
            ReviewItem(source, "Una idea sobre el fallibilismo."),
            glossary={"fallibilism": "falibilismo"},
        )
        joined = " ".join(notes)
        self.assertIn("sentences", joined)
        self.assertIn('"fallibilism" -> "falibilismo"', joined)

    def test_translated_title_in_bibliography_is_flagged(self) -> None:
        notes = detect_weak_points(
            ReviewItem(
                "Jacob Bronowski, <1>The Ascent of Man</1> (BBC, 1973)",
                "Jacob Bronowski, <1>El ascenso del hombre</1> (BBC, 1973)",
                kind="reference",
            )
        )
        self.assertTrue(any('"The Ascent of Man"' in note for note in notes))
        self.assertEqual(
            [],
            detect_weak_points(
                ReviewItem("Jacob Bronowski, <1>The Ascent of Man</1> (BBC, 1973)",
                           "Jacob Bronowski, <1>The Ascent of Man</1> (BBC, 1973)", kind="reference")
            ),
        )

    def test_glossary_sense_note_is_parsed_and_ignored_for_matching(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "g.txt"
            path.write_text("reach = alcance | of an explanation\n", encoding="utf-8")
            glossary = load_glossary(path)
        self.assertEqual("alcance [only when: of an explanation]", glossary["reach"])
        notes = detect_weak_points(
            ReviewItem("Its reach is infinite here and now.", "Su alcance es infinito aquí y ahora."),
            glossary=glossary,
            include_back_references=False,
        )
        self.assertEqual([], notes)

    def test_review_candidates_carry_previous_translation_and_skip_clean_ones(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = ProgressCache(Path(tmp) / "c.json")
            nodes = [
                PendingNode("a.xhtml", 0, "Popper called it the demarcation problem.", context_hint="kind=paragraph"),
                PendingNode("a.xhtml", 1, "But this misses the point.", context_hint="kind=paragraph"),
                PendingNode("a.xhtml", 2, "Science is about explanations.", context_hint="kind=paragraph"),
            ]
            cache.set(nodes[0].core_text, "Popper lo llamó el problema de la demarcación.")
            cache.set(nodes[1].core_text, "Pero esto no da en el clavo.")
            cache.set(nodes[2].core_text, "La ciencia trata de explicaciones.")
            cache.set_note(nodes[2].core_text, "unsure about 'about'")

            flagged = build_review_candidates(nodes, cache, "flagged", None, "en")
            self.assertEqual([1, 2], [node.node_index for node in flagged])
            self.assertEqual("Popper lo llamó el problema de la demarcación.", flagged[0].previous_translation)
            self.assertIn("previous paragraph", flagged[0].review_notes)
            self.assertIn("unsure", flagged[1].review_notes)

            everything = build_review_candidates(nodes, cache, "all", None, "en")
            self.assertEqual(3, len(everything))


class SectionTests(unittest.TestCase):
    def test_bibliography_and_index_sections_are_detected(self) -> None:
        html = b"""<html xmlns="http://www.w3.org/1999/xhtml"><body>
        <h1>Chapter</h1><p>Normal text here.</p>
        <h2>Further reading</h2><p>Karl Popper, <i>The Open Society</i> (Routledge, 1945)</p>
        <h2>Index</h2><p>light, 12, 45</p></body></html>"""
        segments = iter_segments(etree.fromstring(html))
        self.assertEqual(["", "", "", "reference", "", "index"], section_kinds("ch.xhtml", segments))
        self.assertEqual("index", section_kinds("index1.html", segments)[1])
        self.assertEqual("reference", section_kinds("bibliography.html", segments)[1])


class FakeProvider:
    def __init__(self) -> None:
        self.calls = []

    def complete(self, instructions, request, model, max_tokens, schema=None):
        self.calls.append(request)
        terms = [item["term"] for item in json.loads(request)]
        return json.dumps(
            [{"term": term, "translation": f"ES {term}", "note": "sense" if term == "reach" else ""} for term in terms]
        )


class GlossaryBuilderTests(unittest.TestCase):
    def test_candidates_and_generated_file(self) -> None:
        nodes = [
            PendingNode("c.xhtml", i, text, context_hint="kind=paragraph")
            for i, text in enumerate(
                ["Good explanations are <1>hard to vary</1>. The <2>reach</2> of ideas matters."] * 4
                + ["The Principle of Mediocrity is wrong, says the <1>reach</1> argument."] * 3
            )
        ]
        terms = [candidate["term"] for candidate in extract_candidates(nodes)]
        self.assertIn("hard to vary", terms)
        self.assertIn("reach", terms)

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "book.glossary.es.txt"
            provider = FakeProvider()
            build_glossary(provider, nodes, "m", "en", "es", path)
            glossary = load_glossary(path)
        self.assertEqual("ES hard to vary", glossary["hard to vary"])
        self.assertEqual("ES reach [only when: sense]", glossary["reach"])


if __name__ == "__main__":
    unittest.main()
