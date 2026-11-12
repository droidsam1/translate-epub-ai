import tempfile
import unittest
from pathlib import Path

from translate_epub_ai.prompting import build_translation_prompt, load_glossary, select_glossary_terms
from translate_epub_ai.utils import normalize_spanish_quotes

GLOSSARY = Path(__file__).resolve().parents[1] / "glossaries" / "the-beginning-of-infinity.es.txt"


class SpanishQualityTests(unittest.TestCase):
    def test_spanish_prompt_contains_concrete_style_rules(self) -> None:
        prompt = build_translation_prompt(["Policies cannot be derived."], "es", "en", True, payload_ids=["p1"])
        self.assertIn("no puede explicarse por azar", prompt)
        self.assertIn("¿de qué está hecho?", prompt)
        self.assertIn("use « »", prompt)

    def test_style_rules_only_for_spanish(self) -> None:
        prompt = build_translation_prompt(["Hello"], "fr", "en", True, payload_ids=["p1"])
        self.assertNotIn("Spanish style rules", prompt)

    def test_only_relevant_glossary_terms_are_sent(self) -> None:
        glossary = load_glossary(GLOSSARY)
        self.assertEqual("falibilismo", glossary["fallibilism"])
        selected = select_glossary_terms(glossary, ["Thus fallibilism alone rather understates it."])
        self.assertEqual({"fallibilism": "falibilismo"}, selected)

        prompt = build_translation_prompt(
            ["Thus fallibilism alone."], "es", "en", True, payload_ids=["p1"], glossary=glossary
        )
        self.assertIn("- fallibilism -> falibilismo", prompt)
        self.assertNotIn("multiverse ->", prompt)

    def test_glossary_ignores_comments_and_blank_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "g.txt"
            path.write_text("# comment\n\nreach = alcance\nbroken line\n", encoding="utf-8")
            self.assertEqual({"reach": "alcance"}, load_glossary(path))

    def test_quotes_are_normalized(self) -> None:
        self.assertEqual(
            "el lema «la biosfera es <2>incapaz</2> de sostener la vida»",
            normalize_spanish_quotes("el lema ‘la biosfera es <2>incapaz</2> de sostener la vida’"),
        )
        self.assertEqual("dijo «hola»", normalize_spanish_quotes("dijo “hola”"))
        self.assertEqual("dijo «hola»", normalize_spanish_quotes('dijo "hola"'))
        self.assertEqual("«ya está»", normalize_spanish_quotes("«ya está»"))


if __name__ == "__main__":
    unittest.main()
