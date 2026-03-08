# translate-epub-ai

Simple tool to translate EPUB books with OpenAI while keeping the EPUB structure intact.

Spanish version: [README.es.md](/C:/Users/Sam/OneDrive/Documentos/dev/translate_script/README.es.md)

## What this does

You give the tool an `.epub` file.

The tool:

- extracts the book
- finds the readable text
- sends translation jobs through the OpenAI Batch API
- keeps the original EPUB structure
- builds a translated `.epub` file
- saves progress in a cache so you can resume work

## What you need

- Python 3.10 or newer
- an OpenAI API key

## 1. Install

Open a terminal in the project folder and run:

```bash
pip install -e .
```

## 2. Set your OpenAI API key

PowerShell:

```powershell
$env:OPENAI_API_KEY="your_api_key_here"
```

Command Prompt (`cmd`):

```cmd
set OPENAI_API_KEY=your_api_key_here
```

## 3. Translate a book

Example:

```bash
python translate_epub_batch_v3.py "book.epub" --to es
```

This will create a translated file like:

```text
book_ES.epub
```

## Most useful commands

Create the batch files only, without sending anything yet:

```bash
python translate_epub_batch_v3.py "book.epub" --to es --prepare-only
```

Use a different model:

```bash
python translate_epub_batch_v3.py "book.epub" --to es --model gpt-4.1-mini
```

Resume an existing batch:

```bash
python translate_epub_batch_v3.py "book.epub" --resume-batch-id batch_123
```

Use your own translation prompt:

```bash
python translate_epub_batch_v3.py "book.epub" --to es --prompt-file my_prompt.txt
```

## Change the prompt

Default prompt file:

```text
src/translate_epub_ai/prompts/default_prompt.txt
```

If you want to change translation style, tone, or wording, edit that file or pass your own file with `--prompt-file`.

You do not need to change Python code just to tune the prompt.

## Run tests

```bash
python -m unittest discover -s tests -v
```

## Project structure

```text
src/translate_epub_ai/cli.py
src/translate_epub_ai/epub.py
src/translate_epub_ai/openai_batch.py
src/translate_epub_ai/prompting.py
tests/
```

## In plain words

- `cli.py`: runs the tool
- `epub.py`: opens and rebuilds EPUB files
- `openai_batch.py`: talks to OpenAI Batch API
- `prompting.py`: builds the translation prompt
- `tests/`: basic checks so changes are safer

## Current tests

The repository includes tests for:

- prompt generation
- batch grouping logic
- prompt quality checks using a difficult passage from *The Beginning of Infinity*
