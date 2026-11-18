"""OCR fallback for scanned PDF pages (Tesseract via pdf2image + pytesseract).

Requires the system packages `tesseract-ocr` (+ language packs, e.g. `tesseract-ocr-hin`)
and `poppler-utils`. The Dockerfile installs them.
"""
from collections.abc import Callable

from app.core import metrics
from app.core.config import Settings


def preprocess(image):
    """Basic cleanup that helps Tesseract on noisy scans: grayscale, stretch contrast, denoise."""
    from PIL import ImageFilter, ImageOps

    gray = ImageOps.grayscale(image)
    gray = ImageOps.autocontrast(gray)
    return gray.filter(ImageFilter.MedianFilter(3))


def ocr_pdf_page(path: str, page_number: int, *, lang: str, dpi: int) -> str:
    """Render ONE page (1-based) to an image and OCR it. One page at a time keeps memory flat."""
    import pytesseract
    from pdf2image import convert_from_path

    images = convert_from_path(path, dpi=dpi, first_page=page_number, last_page=page_number)
    if not images:
        return ""
    return pytesseract.image_to_string(preprocess(images[0]), lang=lang)


def make_ocr(path: str, settings: Settings) -> Callable[[int], str]:
    def _ocr(page_number: int) -> str:
        metrics.OCR_PAGES.inc()
        return ocr_pdf_page(path, page_number, lang=settings.ocr_lang, dpi=settings.ocr_dpi)

    return _ocr
