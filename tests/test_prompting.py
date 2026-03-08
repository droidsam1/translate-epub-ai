import unittest

from translate_epub_ai.prompting import build_translation_prompt


class PromptingTests(unittest.TestCase):
    def test_default_prompt_includes_expected_values(self) -> None:
        prompt = build_translation_prompt(
            payload_texts=["Hello", "World"],
            target_lang="es",
            source_lang="en",
            natural=True,
        )
        self.assertIn("from en", prompt)
        self.assertIn("Spanish as used in Spain", prompt)
        self.assertIn('["Hello", "World"]', prompt)
        self.assertIn("exactly 2 translated strings", prompt)
        self.assertIn("Preserve the author's voice and register", prompt)
        self.assertIn("prioritize precision, clarity, and terminological consistency", prompt)


if __name__ == "__main__":
    unittest.main()
