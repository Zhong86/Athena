"""Uploaded bytes -> plain text.

Extraction happens here rather than being handed to Hermes. For PDFs that is
forced: the api_server accepts inline images but rejects uploaded files and
non-image `data:` URLs outright, so there is no way to give it the document.
For images it is a choice -- tesseract fails visibly (garbage characters) where
a vision model fails invisibly (a plausible but wrong formula), and material
that gets embedded, tagged and quizzed against has to be trustworthy.

Third-party imports are function-local so a missing optional dependency breaks
only the format that needs it. A backend that can still ingest pasted text with
no tesseract installed is more useful than one that refuses to boot.
"""

import re
from io import BytesIO

UPLOAD_TYPES = ("text", "pdf", "image")

# Below this, a PDF's text layer is a handful of stray glyphs rather than
# content -- the file is scanned images and pypdf cannot help.
MIN_PDF_TEXT = 100

_EXTENSIONS = {
    "pdf": "pdf",
    "txt": "text",
    "md": "text",
    "markdown": "text",
    "png": "image",
    "jpg": "image",
    "jpeg": "image",
    "webp": "image",
    "gif": "image",
    "bmp": "image",
    "tiff": "image",
    "tif": "image",
}

_TRAILING_SPACE = re.compile(r"[ \t]+$", re.MULTILINE)
_EXCESS_BLANK_LINES = re.compile(r"\n{3,}")


class ExtractError(RuntimeError):
    """Anything that stops bytes becoming text. The message reaches the user as
    `source_files.ingest_error`, so it has to read as an explanation."""


def infer_upload_type(filename: str, content_type: str | None = None) -> str | None:
    """Map an upload onto the `source_files.upload_type` CHECK constraint.

    Extension wins over content-type: browsers send application/octet-stream
    for plenty of legitimate files, but a .pdf is a .pdf.
    """
    _, _, ext = filename.rpartition(".")
    if ext and (mapped := _EXTENSIONS.get(ext.lower())):
        return mapped

    if content_type:
        mime = content_type.split(";")[0].strip().lower()
        if mime == "application/pdf":
            return "pdf"
        if mime.startswith("image/"):
            return "image"
        if mime.startswith("text/"):
            return "text"
    return None


def _normalize(text: str) -> str:
    """PDF extraction leaves ragged trailing spaces and runs of blank lines.
    The chunker splits on blank lines, so tidying here directly decides whether
    paragraph detection works downstream."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _TRAILING_SPACE.sub("", text)
    return _EXCESS_BLANK_LINES.sub("\n\n", text).strip()


def _extract_pdf(data: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - deployment error
        raise ExtractError("PDF support unavailable: pypdf is not installed") from exc

    try:
        reader = PdfReader(BytesIO(data))
        if reader.is_encrypted:
            raise ExtractError("this PDF is password-protected")
        pages = [page.extract_text() or "" for page in reader.pages]
    except ExtractError:
        raise
    except Exception as exc:
        raise ExtractError(f"could not read this PDF: {exc}") from exc

    text = _normalize("\n\n".join(pages))
    if len(text) < MIN_PDF_TEXT:
        raise ExtractError(
            "no extractable text found -- this looks like a scanned PDF, "
            "which is not supported yet"
        )
    return text


def _extract_image(data: bytes) -> str:
    try:
        import pytesseract
        from PIL import Image
    except ImportError as exc:  # pragma: no cover - deployment error
        raise ExtractError(
            "image support unavailable: pytesseract and pillow are not installed"
        ) from exc

    try:
        image = Image.open(BytesIO(data))
    except Exception as exc:
        raise ExtractError(f"could not open this image: {exc}") from exc

    try:
        raw = pytesseract.image_to_string(image)
    except pytesseract.TesseractNotFoundError as exc:
        raise ExtractError(
            "OCR unavailable: the tesseract binary is not installed on this host "
            "(apt-get install tesseract-ocr)"
        ) from exc
    except Exception as exc:
        raise ExtractError(f"OCR failed: {exc}") from exc

    text = _normalize(raw)
    if not text:
        raise ExtractError("OCR produced no text from this image")
    return text


def extract(data: bytes, upload_type: str) -> str:
    if not data:
        raise ExtractError("the uploaded file is empty")

    if upload_type == "text":
        # errors="replace" rather than failing: a stray byte in an otherwise
        # fine set of lecture notes should not cost the whole upload.
        text = _normalize(data.decode("utf-8", errors="replace"))
        if not text:
            raise ExtractError("the uploaded file contains no text")
        return text
    if upload_type == "pdf":
        return _extract_pdf(data)
    if upload_type == "image":
        return _extract_image(data)

    raise ExtractError(f"unsupported upload type: {upload_type!r}")
