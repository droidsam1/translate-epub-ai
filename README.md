# translate-epub-ai

`translate-epub-ai` translates EPUB files with the OpenAI Batch API while preserving the original ebook structure. The project is organized for long-term maintenance: the CLI is thin, EPUB handling is isolated, OpenAI batch logic is isolated, and the translation prompt is configurable without touching Python code.

## Features

- Preserves EPUB packaging and rewrites the archive in a reader-friendly way.
- Avoids retranslating completed segments through a persistent progress cache.
- Groups nearby text nodes from the same document for better narrative continuity.
- Skips navigation and package files that should not be translated.
- Lets you swap the prompt with `--prompt-file` so prompt iteration does not require code changes.
- Supports a prepare-only workflow for reviewing the generated JSONL before submitting a batch.

## Project layout

```text
.
|-- src/translate_epub_ai/
|   |-- cli.py
|   |-- cache.py
|   |-- epub.py
|   |-- openai_batch.py
|   |-- prompting.py
|   `-- prompts/default_prompt.txt
|-- tests/
|-- translate_epub_batch_v3.py
|-- pyproject.toml
`-- README.md
```

## Requirements

- Python 3.10+
- `OPENAI_API_KEY` environment variable

Install locally:

```bash
pip install -e .
```

## Usage

Prepare batch files only:

```bash
python translate_epub_batch_v3.py "book.epub" --to es --model gpt-4.1-mini --prepare-only
```

Create and wait for a batch:

```bash
python translate_epub_batch_v3.py "book.epub" --to es --model gpt-4.1-mini
```

Resume an existing batch:

```bash
python translate_epub_batch_v3.py "book.epub" --resume-batch-id batch_123
```

Use a custom prompt template:

```bash
python translate_epub_batch_v3.py "book.epub" --to es --prompt-file prompts/my_prompt.txt --prepare-only
```

## Prompt customization

The default prompt lives in `src/translate_epub_ai/prompts/default_prompt.txt`. You can pass your own template with `--prompt-file`.

Supported placeholders:

- `{source_language_clause}`
- `{target_language_name}`
- `{style_instruction}`
- `{quote_instruction}`
- `{item_count}`
- `{payload_json}`

This keeps prompt experimentation separate from the application code. If you want multiple translation styles later, add more prompt templates and switch them from the CLI rather than editing logic in `cli.py`.

## Engineering notes

- `cli.py` owns argument parsing and workflow orchestration.
- `epub.py` owns archive extraction, content discovery, and translation application.
- `openai_batch.py` owns request grouping, JSONL generation, polling, and output parsing.
- `cache.py` owns persistent translation state.
- `prompting.py` owns prompt loading and rendering.

That separation makes it easier to test, replace the translation backend later, or tune batching independently from EPUB parsing.

## Recommended next improvements

- Add automated integration tests with a tiny fixture EPUB.
- Add structured logging instead of plain `print`.
- Add richer output validation for partial or malformed batch results.
- Add configuration profiles for different target locales and editorial styles.
