# Maintenance Guide

## Development workflow

1. Keep prompt edits in template files whenever possible.
2. Keep EPUB parsing changes in `src/translate_epub_ai/epub.py`.
3. Keep OpenAI API and batch lifecycle changes in `src/translate_epub_ai/openai_batch.py`.
4. Add tests before changing parsing or output-mapping behavior.

## Prompt strategy

Prompt behavior is intentionally decoupled from the translation pipeline:

- Default template: `src/translate_epub_ai/prompts/default_prompt.txt`
- Runtime override: `--prompt-file path/to/template.txt`

Template placeholders are rendered with Python `str.format`, so any new prompt file should preserve the existing placeholder names unless you also update `prompting.py`.

## Safe extension points

- Add more CLI flags in `src/translate_epub_ai/cli.py` and map them into `TranslationConfig`.
- Add locale-specific prompt helpers in `src/translate_epub_ai/prompting.py`.
- Add extra batch metadata or parsing rules in `src/translate_epub_ai/openai_batch.py`.
- Add provider abstractions later if you want non-OpenAI backends again.
