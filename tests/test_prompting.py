import unittest

from translate_epub_ai.prompting import build_translation_prompt


class PromptingTests(unittest.TestCase):
    def test_default_prompt_includes_expected_values(self) -> None:
        prompt = build_translation_prompt(
            payload_texts=["Hello", "World"],
            payload_ids=["a1", "a2"],
            context_hints=["kind=paragraph; next=\"World\"", "kind=paragraph; prev=\"Hello\""],
            target_lang="es",
            source_lang="en",
            natural=True,
        )
        self.assertIn("from en", prompt)
        self.assertIn("Spanish as used in Spain", prompt)
        self.assertIn('"id": "a1"', prompt)
        self.assertIn('"text": "Hello"', prompt)
        self.assertIn('"context": "kind=paragraph; next=\\"World\\""', prompt)
        self.assertIn("exactly 2 objects", prompt)
        self.assertIn('"translation": the translated text for that item only', prompt)
        self.assertIn("Preserve the author's voice and register", prompt)
        self.assertIn("prioritize precision, clarity, and terminological consistency", prompt)
        self.assertIn("silently check that it is coherent, natural, idiomatic", prompt)
        self.assertIn("Treat each item's id as binding", prompt)

    def test_repair_mode_includes_revision_guidance(self) -> None:
        prompt = build_translation_prompt(
            payload_texts=["Original sentence."],
            payload_ids=["p1"],
            target_lang="es",
            source_lang="en",
            natural=True,
            repair_mode=True,
            current_translations=["Traducción rara y rota."],
            context_hints=["This paragraph explains a technical argument."],
        )
        self.assertIn("Repair mode:", prompt)
        self.assertIn('"current_translation": "Traducción rara y rota."', prompt)
        self.assertIn('"context": "This paragraph explains a technical argument."', prompt)

    def test_review_mode_includes_editorial_revision_guidance(self) -> None:
        prompt = build_translation_prompt(
            payload_texts=["Original sentence."],
            payload_ids=["r1"],
            target_lang="es",
            source_lang="en",
            natural=True,
            review_mode=True,
            current_translations=["Una traducción aceptable pero mejorable."],
        )
        self.assertIn("Review mode:", prompt)
        self.assertIn("Keep it if it is already accurate and natural", prompt)
        self.assertIn('"current_translation": "Una traducción aceptable pero mejorable."', prompt)

    def test_prompt_parts_keep_stable_prefix_across_requests(self) -> None:
        from translate_epub_ai.prompting import build_prompt_parts

        first = build_prompt_parts(["One."], "es", "en", True, payload_ids=["a"])
        second = build_prompt_parts(["Two.", "Three."], "es", "en", True, payload_ids=["b", "c"])
        self.assertEqual(first.instructions, second.instructions)
        self.assertNotIn("One.", first.instructions)
        self.assertIn('"text": "One."', first.request)
        self.assertIn("exactly 2 objects", second.request)
        self.assertIn("Never translate titles of cited works", first.instructions)

    def test_review_items_carry_previous_translation_and_notes(self) -> None:
        prompt = build_translation_prompt(
            payload_texts=["But this misses the point."],
            payload_ids=["r1"],
            target_lang="es",
            source_lang="en",
            natural=True,
            review_mode=True,
            current_translations=["Pero esto no da en el clavo."],
            previous_translations=["Popper lo llamó el problema de la demarcación."],
            review_notes=["starts with a reference to the previous paragraph"],
        )
        self.assertIn('"previous_translation": "Popper lo llamó el problema de la demarcación."', prompt)
        self.assertIn('"review_notes": "starts with a reference to the previous paragraph"', prompt)


if __name__ == "__main__":
    unittest.main()
