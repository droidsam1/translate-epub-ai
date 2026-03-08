"""Project-wide constants."""

SKIP_TAGS = {
    "script",
    "style",
    "code",
    "pre",
    "kbd",
    "samp",
    "math",
    "svg",
    "head",
    "title",
    "meta",
    "link",
    "img",
    "audio",
    "video",
    "source",
}

HTML_EXTS = {".xhtml", ".html", ".htm"}
PACKAGE_EXTS = {".opf", ".ncx"}

FILES_URL = "https://api.openai.com/v1/files"
BATCHES_URL = "https://api.openai.com/v1/batches"
RESPONSES_ENDPOINT = "/v1/responses"
