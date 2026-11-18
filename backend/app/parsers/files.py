"""File validation (magic bytes, type) and text extraction.

Phase 2 adds an OCR fallback inside `_parse_pdf` for pages with no extractable text.
"""
import io
import logging
import os
import re
import zipfile
from collections.abc import Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)


class UnsupportedFile(Exception):
    """Wrong extension, or the content does not match the extension."""


class ParseError(Exception):
    """File is valid-looking but could not be read / has no text."""


@dataclass
class Page:
    number: int
    text: str


ALLOWED_EXTENSIONS = {".pdf": "pdf", ".docx": "docx", ".txt": "txt", ".md": "md"}


def sanitize_filename(name: str | None) -> str:
    """Display-name only. Files on disk are stored under the doc id, never this name."""
    base = os.path.basename((name or "").replace("\\", "/"))
    base = re.sub(r"[^\w.\- ऀ-ॿ]", "_", base).strip(" .")
    return base[:120] or "document"


def detect_file_type(filename: str, data: bytes) -> str:
    ext = os.path.splitext(filename)[1].lower()
    ftype = ALLOWED_EXTENSIONS.get(ext)
    if not ftype:
        raise UnsupportedFile("Unsupported file type. Allowed: PDF, DOCX, TXT, MD")

    if ftype == "pdf":
        if b"%PDF-" not in data[:1024]:
            raise UnsupportedFile("File content is not a valid PDF")
    elif ftype == "docx":
        if not data.startswith(b"PK\x03\x04"):
            raise UnsupportedFile("File content is not a valid DOCX")
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                if "word/document.xml" not in z.namelist():
                    raise UnsupportedFile("File content is not a valid DOCX")
        except zipfile.BadZipFile:
            raise UnsupportedFile("File content is not a valid DOCX")
    else:  # txt / md
        if b"\x00" in data[:8192]:
            raise UnsupportedFile("File looks binary, not text")
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            raise UnsupportedFile("Text files must be UTF-8 encoded")
    return ftype


def parse_file(
    path: str,
    ftype: str,
    ocr: Callable[[int], str] | None = None,
    ocr_min_chars: int = 50,
    ocr_max_pages: int = 100,
) -> list[Page]:
    if ftype == "pdf":
        pages = _parse_pdf(path, ocr, ocr_min_chars, ocr_max_pages)
    elif ftype == "docx":
        pages = _parse_docx(path)
    elif ftype in ("txt", "md"):
        pages = _parse_text(path)
    else:
        raise UnsupportedFile(f"Unsupported type: {ftype}")
    if not pages:
        raise ParseError("File contains no text")
    return pages


def _parse_pdf(
    path: str,
    ocr: Callable[[int], str] | None,
    min_chars: int,
    max_ocr_pages: int,
) -> list[Page]:
    """Extract text per page. Pages with fewer than `min_chars` characters are sent to OCR
    (if available); the OCR text is only used when it is longer than what was extracted.
    Page numbers are always the real PDF page numbers, so citations stay correct."""
    from pypdf import PdfReader

    pages: list[Page] = []
    ocr_used = 0
    ocr_error: str | None = None
    try:
        reader = PdfReader(path)
        if reader.is_encrypted and not reader.decrypt(""):
            raise ParseError("PDF is password-protected")
        for i, page in enumerate(reader.pages, start=1):
            text = (page.extract_text() or "").strip()
            if len(text) < min_chars and ocr is not None and ocr_used < max_ocr_pages:
                ocr_used += 1
                try:
                    ocr_text = (ocr(i) or "").strip()
                except Exception as e:  # tesseract missing, bad language pack, render failure...
                    ocr_error = str(e) or type(e).__name__
                    logger.warning("OCR failed on page %d: %s", i, ocr_error)
                else:
                    if len(ocr_text) > len(text):
                        text = ocr_text
            if text:
                pages.append(Page(i, text))
    except ParseError:
        raise
    except Exception as e:  # corrupt / truncated / unsupported encryption
        raise ParseError(f"Could not read PDF: {e}") from e

    if not pages:
        if ocr_error:
            raise ParseError(f"Scanned PDF and OCR failed: {ocr_error}")
        if ocr is None:
            raise ParseError("No extractable text found (scanned PDF? enable OCR)")
        raise ParseError("No text found, even with OCR")
    return pages


def _parse_docx(path: str) -> list[Page]:
    import docx

    try:
        document = docx.Document(path)
    except Exception as e:
        raise ParseError(f"Could not read DOCX: {e}") from e
    parts = [p.text for p in document.paragraphs if p.text.strip()]
    for table in document.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                parts.append(" | ".join(cells))
    text = "\n".join(parts).strip()
    # DOCX has no fixed pages; everything is "page 1"
    return [Page(1, text)] if text else []


def _parse_text(path: str) -> list[Page]:
    with open(path, "rb") as f:
        text = f.read().decode("utf-8-sig", errors="replace").strip()
    return [Page(1, text)] if text else []
