# translate-epub-ai

Translate EPUB books with OpenAI or Anthropic in a simple, copy-paste friendly way.

[Leer en español](README.es.md)

![How it works](docs/assets/how-it-works.svg)

## The shortest version

If you only want the fast path:

1. Install the project.
2. Set your API key.
3. Run one command.

```bash
pip install -e .
python -m translate_epub_ai "book.epub" --to es
```

Output:

```text
book_ES.epub
```

## What this tool does

You give it an EPUB file.

It will:

- open the EPUB safely
- find the human-readable text
- send translation jobs through a batch API
- keep the original EPUB structure
- generate a translated EPUB
- save progress so you can resume later

## What you need

- Python 3.10 or newer
- an OpenAI API key or an Anthropic API key

## Install

Open a terminal in the project folder and run:

```bash
pip install -e .
```

## Set your API key

OpenAI in PowerShell:

```powershell
$env:OPENAI_API_KEY="your_api_key_here"
```

Anthropic in PowerShell:

```powershell
$env:ANTHROPIC_API_KEY="your_api_key_here"
```

OpenAI in Command Prompt (`cmd`):

```cmd
set OPENAI_API_KEY=your_api_key_here
```

Anthropic in Command Prompt (`cmd`):

```cmd
set ANTHROPIC_API_KEY=your_api_key_here
```

macOS / Linux:

```bash
export OPENAI_API_KEY="your_api_key_here"
```

Anthropic on macOS / Linux:

```bash
export ANTHROPIC_API_KEY="your_api_key_here"
```

## Translate your first book

Basic example:

```bash
python -m translate_epub_ai "book.epub" --to es
```

Example with a real model:

```bash
python -m translate_epub_ai "book.epub" --to es --model gpt-4.1-mini
```

Anthropic example:

```bash
python -m translate_epub_ai "book.epub" --provider anthropic --model claude-sonnet-4-20250514 --to es
```

## Useful commands

Prepare everything but do not send the batch yet:

```bash
python -m translate_epub_ai "book.epub" --to es --prepare-only
```

Resume a batch you already created:

```bash
python -m translate_epub_ai "book.epub" --resume-batch-id batch_123
```

Important:

- if you do not set `--provider`, it still uses `openai`
- `python -m translate_epub_ai` is the recommended direct command
- cache and resume still work so translated segments are not requested again

Use a custom prompt file:

```bash
python -m translate_epub_ai "book.epub" --to es --prompt-file my_prompt.txt
```

Automatic repair is enabled by default:

- after the main translation batch, the tool runs an editorial review pass over translated blocks
- after the main translation batch
- the tool looks for suspicious fragments
- it automatically re-translates only those fragments
- then it builds the final EPUB

So in normal use, you do not need to manually review the whole book just to trigger repairs.

Repair only specific bad phrases or paragraphs:

```bash
python -m translate_epub_ai "book.epub" --to es --repair-file repair.json
```

Example `repair.json`:

```json
[
  "The original English sentence that needs a better translation.",
  {
    "source_text": "A difficult paragraph from the source book.",
    "context_hint": "This section is technical and should sound precise but natural."
  }
]
```

## Want better translation style?

The default prompt lives here:

```text
src/translate_epub_ai/prompts/default_prompt.txt
```

You can:

- edit that file directly
- pass your own file with `--prompt-file`

This means you can improve tone, style, and fluency without changing Python code.

If some translated lines still look broken, awkward, or unnatural, the tool now tries to repair suspicious fragments automatically in the same translation run.

`--repair-file` is still available as an advanced option when you want to force selective retries for exact source fragments.

You can also control the built-in review stage:

```bash
python -m translate_epub_ai "book.epub" --to es --review-passes 1
```

## Translate locally with Ollama (free, no API key)

1. Install Ollama: <https://ollama.com/download> (Windows: `winget install Ollama.Ollama`).
2. Download a model, for example:

```bash
ollama pull gemma4:26b-a4b-it-qat
```

3. Translate:

```bash
python -m translate_epub_ai "book.epub" --provider ollama --glossary glossaries/the-beginning-of-infinity.es.txt --to es
```

Notes:

- Requests run one by one on your machine, so a full book can take hours. Progress and an ETA are printed as it goes.
- If the run stops, launch the same command again: finished paragraphs are in the progress cache and are not translated twice.
- `--review-passes 0` skips the second editorial pass and halves the time.
- `--glossary glossaries/the-beginning-of-infinity.es.txt` forces consistent terminology. Only terms present in each request are sent. Write your own glossary for other books (one `source = translation` per line).
- Recommended model for a 16 GB GPU: `gemma4:26b-a4b-it-qat` (mixture-of-experts, ~4B active parameters, so it is fast). On smaller GPUs use `gemma4:12b`. Thinking is disabled automatically for speed.
- For Spanish, the prompt adds concrete style rules (« » quotes, pronominal passives, no dropped qualifiers, no calques) and quotes are normalised to « » automatically.
- Optional environment variables: `OLLAMA_HOST` (default `http://localhost:11434`), `OLLAMA_NUM_CTX` (default `16384`), `OLLAMA_TEMPERATURE` (default `0.3`).

## How text is sent to the model

Each paragraph or heading is translated as one unit. Inline formatting is replaced by numbered markers so the model sees the whole sentence:

```text
<p>The <em>principle</em> was key.<sup><a href="#n1">12</a></sup></p>
-> "The <1>principle</1> was key.<2/>"
```

The model may move markers to follow Spanish word order; they are mapped back onto the original tags afterwards. Translations whose markers do not match the source are rejected and retried.

## Run tests

```bash
python -m unittest discover -s tests -v
```

## Project layout

```text
src/translate_epub_ai/cli.py
src/translate_epub_ai/epub.py
src/translate_epub_ai/segments.py
src/translate_epub_ai/batch_providers.py
src/translate_epub_ai/prompting.py
tests/
```

## What each file does

- `cli.py`: command-line entry point
- `epub.py`: extract and rebuild EPUB files
- `batch_providers.py`: provider logic for OpenAI and Anthropic (batch APIs) and Ollama (local)
- `segments.py`: split chapters into paragraphs and map inline markup to markers and back
- `prompting.py`: generate the translation prompt
- `tests/`: keep refactors safer

## Included tests

This repo currently checks:

- prompt generation
- batch grouping logic
- prompt quality using a difficult passage from *The Beginning of Infinity*

## Troubleshooting

If you see `OPENAI_API_KEY is not set`, set the environment variable first.

If you are using Anthropic, set `ANTHROPIC_API_KEY` instead and add:

```bash
--provider anthropic
```

If you want to inspect the generated batch files before sending them, use:

```bash
python -m translate_epub_ai "book.epub" --to es --prepare-only
```

If you want to change the translation style, start by editing:

```text
src/translate_epub_ai/prompts/default_prompt.txt
```
