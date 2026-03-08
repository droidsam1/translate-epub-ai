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
        self.assertIn("silently check that it is coherent, natural, idiomatic", prompt)

    def test_repair_mode_includes_revision_guidance(self) -> None:
        prompt = build_translation_prompt(
            payload_texts=["Original sentence."],
            target_lang="es",
            source_lang="en",
            natural=True,
            repair_mode=True,
            current_translations=["Traducción rara y rota."],
            context_hints=["This paragraph explains a technical argument."],
        )
        self.assertIn("Repair mode:", prompt)
        self.assertIn("Current translations to review:", prompt)
        self.assertIn("Optional context hints:", prompt)

    def test_review_mode_includes_editorial_revision_guidance(self) -> None:
        prompt = build_translation_prompt(
            payload_texts=["Original sentence."],
            target_lang="es",
            source_lang="en",
            natural=True,
            review_mode=True,
            current_translations=["Una traducción aceptable pero mejorable."],
        )
        self.assertIn("Review mode:", prompt)
        self.assertIn("keep the current translation if it is already strong", prompt)


if __name__ == "__main__":
    unittest.main()
