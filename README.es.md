# translate-epub-ai

Herramienta sencilla para traducir libros EPUB con OpenAI manteniendo la estructura del archivo EPUB.

English version: [README.md](/C:/Users/Sam/OneDrive/Documentos/dev/translate_script/README.md)

## Qué hace

Le pasas al programa un archivo `.epub`.

La herramienta:

- extrae el libro
- encuentra el texto legible
- envía los trabajos de traducción mediante OpenAI Batch API
- mantiene la estructura original del EPUB
- genera un nuevo `.epub` traducido
- guarda progreso en una caché para poder reanudar

## Qué necesitas

- Python 3.10 o superior
- una API key de OpenAI

## 1. Instalar

Abre una terminal en la carpeta del proyecto y ejecuta:

```bash
pip install -e .
```

## 2. Configurar tu API key de OpenAI

PowerShell:

```powershell
$env:OPENAI_API_KEY="tu_api_key_aqui"
```

Command Prompt (`cmd`):

```cmd
set OPENAI_API_KEY=tu_api_key_aqui
```

## 3. Traducir un libro

Ejemplo:

```bash
python translate_epub_batch_v3.py "book.epub" --to es
```

Esto creará un archivo traducido como:

```text
book_ES.epub
```

## Comandos más útiles

Crear solo los archivos del batch, sin enviarlos todavía:

```bash
python translate_epub_batch_v3.py "book.epub" --to es --prepare-only
```

Usar otro modelo:

```bash
python translate_epub_batch_v3.py "book.epub" --to es --model gpt-4.1-mini
```

Reanudar un batch existente:

```bash
python translate_epub_batch_v3.py "book.epub" --resume-batch-id batch_123
```

Usar tu propio prompt de traducción:

```bash
python translate_epub_batch_v3.py "book.epub" --to es --prompt-file my_prompt.txt
```

## Cambiar el prompt

Archivo de prompt por defecto:

```text
src/translate_epub_ai/prompts/default_prompt.txt
```

Si quieres cambiar el estilo de traducción, el tono o el wording, edita ese archivo o pasa otro con `--prompt-file`.

No necesitas tocar código Python solo para ajustar el prompt.

## Ejecutar tests

```bash
python -m unittest discover -s tests -v
```

## Estructura del proyecto

```text
src/translate_epub_ai/cli.py
src/translate_epub_ai/epub.py
src/translate_epub_ai/openai_batch.py
src/translate_epub_ai/prompting.py
tests/
```

## En palabras simples

- `cli.py`: ejecuta la herramienta
- `epub.py`: abre y reconstruye archivos EPUB
- `openai_batch.py`: se comunica con OpenAI Batch API
- `prompting.py`: construye el prompt de traducción
- `tests/`: comprobaciones básicas para cambiar cosas con más seguridad

## Tests actuales

El repositorio incluye tests para:

- generación del prompt
- lógica de agrupación para batches
- comprobación de calidad del prompt usando un pasaje difícil de *The Beginning of Infinity*
