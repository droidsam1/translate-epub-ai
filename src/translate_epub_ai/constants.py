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

# Elements that start a new paragraph-like unit. Everything else is treated as
# inline markup and kept inside the surrounding sentence as a marker.
BLOCK_TAGS = {
    "html", "body", "main", "section", "article", "aside", "nav", "header", "footer",
    "div", "p", "blockquote", "address", "figure", "figcaption", "details", "summary",
    "h1", "h2", "h3", "h4", "h5", "h6", "hr",
    "ul", "ol", "li", "dl", "dt", "dd",
    "table", "thead", "tbody", "tfoot", "tr", "td", "th", "caption",
    "pre",
}

HTML_EXTS = {".xhtml", ".html", ".htm"}
PACKAGE_EXTS = {".opf", ".ncx"}

FILES_URL = "https://api.openai.com/v1/files"
BATCHES_URL = "https://api.openai.com/v1/batches"
RESPONSES_ENDPOINT = "/v1/responses"
