# translate-epub-ai

Traduce libros EPUB con OpenAI de forma sencilla y con comandos fáciles de copiar y pegar.

[Read in English](README.md)

![Cómo funciona](docs/assets/how-it-works.svg)

## La versión corta

Si solo quieres la ruta rápida:

1. Instala el proyecto.
2. Configura `OPENAI_API_KEY`.
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
- envía los trabajos de traducción mediante OpenAI Batch API
- mantiene la estructura original del EPUB
- genera un EPUB traducido
- guarda progreso para poder reanudar más tarde

## Qué necesitas

- Python 3.10 o superior
- una API key de OpenAI

## Instalar

Abre una terminal en la carpeta del proyecto y ejecuta:

```bash
pip install -e .
```

## Configurar tu API key de OpenAI

PowerShell:

```powershell
$env:OPENAI_API_KEY="tu_api_key_aqui"
```

Command Prompt (`cmd`):

```cmd
set OPENAI_API_KEY=tu_api_key_aqui
```

macOS / Linux:

```bash
export OPENAI_API_KEY="tu_api_key_aqui"
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

## Comandos útiles

Preparar todo pero sin enviar todavía el batch:

```bash
python translate_epub_batch_v3.py "book.epub" --to es --prepare-only
```

Reanudar un batch que ya existe:

```bash
python translate_epub_batch_v3.py "book.epub" --resume-batch-id batch_123
```

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
src/translate_epub_ai/openai_batch.py
src/translate_epub_ai/prompting.py
tests/
```

## Qué hace cada archivo

- `cli.py`: punto de entrada por línea de comandos
- `epub.py`: extrae y reconstruye archivos EPUB
- `openai_batch.py`: construye batches y lee sus resultados
- `prompting.py`: genera el prompt de traducción
- `tests/`: ayuda a que los cambios sean más seguros

## Tests incluidos

Este repositorio comprueba ahora mismo:

- generación del prompt
- lógica de agrupación para batches
- calidad del prompt usando un pasaje difícil de *The Beginning of Infinity*

## Solución de problemas

Si ves `OPENAI_API_KEY is not set`, primero configura la variable de entorno.

Si quieres inspeccionar los archivos del batch antes de enviarlos, usa:

```bash
python translate_epub_batch_v3.py "book.epub" --to es --prepare-only
```

Si quieres cambiar el estilo de traducción, empieza por editar:

```text
src/translate_epub_ai/prompts/default_prompt.txt
```
