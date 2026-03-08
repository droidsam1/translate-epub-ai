import unittest

from translate_epub_ai.prompting import build_translation_prompt


DIFFICULT_PASSAGE = (
    "I mean it literally when I say that it was the system of numerals that performed arithmetic."
)


class PromptQualityTests(unittest.TestCase):
    def test_prompt_guides_translation_of_difficult_conceptual_passage(self) -> None:
        prompt = build_translation_prompt(
            payload_texts=[DIFFICULT_PASSAGE],
            payload_ids=["d1"],
            context_hints=["kind=paragraph"],
            target_lang="es",
            source_lang="en",
            natural=True,
        )

        self.assertIn(DIFFICULT_PASSAGE, prompt)
        self.assertIn('"id": "d1"', prompt)
        self.assertIn("fluid, idiomatic phrasing", prompt)
        self.assertIn("Preserve the author's voice and register", prompt)
        self.assertIn("technical or conceptually dense", prompt)
        self.assertIn("terminological consistency", prompt)
        self.assertIn("Do not simplify, summarize, embellish", prompt)
        self.assertIn("natural sentence construction in the target language", prompt)
        self.assertIn("Never merge one item's content into another item", prompt)


if __name__ == "__main__":
    unittest.main()
