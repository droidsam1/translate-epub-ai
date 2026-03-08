# translate-epub-ai

Traduce libros EPUB con OpenAI o Anthropic de forma sencilla y con comandos fáciles de copiar y pegar.

[Read in English](README.md)

![Cómo funciona](docs/assets/how-it-works.svg)

## La versión corta

Si solo quieres la ruta rápida:

1. Instala el proyecto.
2. Configura tu API key.
3. Ejecuta un comando.

```bash
pip install -e .
python translate_epub_batch_v3.py "book.epub" --to es
```

Salida:

```text
book_ES.epub
```

## Qué hace esta herramienta

Le das un archivo EPUB.

La herramienta:

- abre el EPUB de forma segura
- encuentra el texto legible
- envía los trabajos de traducción mediante una API batch
- mantiene la estructura original del EPUB
- genera un EPUB traducido
- guarda progreso para poder reanudar más tarde

## Qué necesitas

- Python 3.10 o superior
- una API key de OpenAI o de Anthropic

## Instalar

Abre una terminal en la carpeta del proyecto y ejecuta:

```bash
pip install -e .
```

## Configurar tu API key

OpenAI en PowerShell:

```powershell
$env:OPENAI_API_KEY="tu_api_key_aqui"
```

Anthropic en PowerShell:

```powershell
$env:ANTHROPIC_API_KEY="tu_api_key_aqui"
```

OpenAI en Command Prompt (`cmd`):

```cmd
set OPENAI_API_KEY=tu_api_key_aqui
```

Anthropic en Command Prompt (`cmd`):

```cmd
set ANTHROPIC_API_KEY=tu_api_key_aqui
```

macOS / Linux:

```bash
export OPENAI_API_KEY="tu_api_key_aqui"
```

Anthropic en macOS / Linux:

```bash
export ANTHROPIC_API_KEY="tu_api_key_aqui"
```

## Traducir tu primer libro

Ejemplo básico:

```bash
python translate_epub_batch_v3.py "book.epub" --to es
```

Ejemplo indicando modelo:

```bash
python translate_epub_batch_v3.py "book.epub" --to es --model gpt-4.1-mini
```

Ejemplo con Anthropic:

```bash
python translate_epub_batch_v3.py "book.epub" --provider anthropic --model claude-sonnet-4-20250514 --to es
```

## Comandos útiles

Preparar todo pero sin enviar todavía el batch:

```bash
python translate_epub_batch_v3.py "book.epub" --to es --prepare-only
```

Reanudar un batch que ya existe:

```bash
python translate_epub_batch_v3.py "book.epub" --resume-batch-id batch_123
```

Importante:

- si no indicas `--provider`, seguirá usando `openai`
- esto mantiene compatibilidad con el uso anterior
- la caché y la reanudación siguen evitando llamadas repetidas y gasto extra de tokens

Usar tu propio archivo de prompt:

```bash
python translate_epub_batch_v3.py "book.epub" --to es --prompt-file my_prompt.txt
```

## ¿Quieres mejorar el estilo de traducción?

El prompt por defecto está aquí:

```text
src/translate_epub_ai/prompts/default_prompt.txt
```

Puedes:

- editar ese archivo directamente
- pasar tu propio archivo con `--prompt-file`

Así puedes mejorar tono, estilo y fluidez sin tocar el código Python.

## Ejecutar tests

```bash
python -m unittest discover -s tests -v
```

## Estructura del proyecto

```text
src/translate_epub_ai/cli.py
src/translate_epub_ai/epub.py
src/translate_epub_ai/batch_providers.py
src/translate_epub_ai/prompting.py
tests/
```

## Qué hace cada archivo

- `cli.py`: punto de entrada por línea de comandos
- `epub.py`: extrae y reconstruye archivos EPUB
- `batch_providers.py`: lógica específica de OpenAI y Anthropic para batches
- `prompting.py`: genera el prompt de traducción
- `tests/`: ayuda a que los cambios sean más seguros

## Tests incluidos

Este repositorio comprueba ahora mismo:

- generación del prompt
- lógica de agrupación para batches
- calidad del prompt usando un pasaje difícil de *The Beginning of Infinity*

## Solución de problemas

Si ves `OPENAI_API_KEY is not set`, primero configura la variable de entorno.

Si usas Anthropic, configura `ANTHROPIC_API_KEY` y añade:

```bash
--provider anthropic
```

Si quieres inspeccionar los archivos del batch antes de enviarlos, usa:

```bash
python translate_epub_batch_v3.py "book.epub" --to es --prepare-only
```

Si quieres cambiar el estilo de traducción, empieza por editar:

```text
src/translate_epub_ai/prompts/default_prompt.txt
```
