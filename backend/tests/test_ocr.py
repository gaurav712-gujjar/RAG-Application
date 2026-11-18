import pytest

from app.parsers.files import ParseError, parse_file
from tests.fakes import make_pdf_pages

LONG = "This page has plenty of real extractable text about shipping and delivery times."


def _pdf(tmp_path, texts):
    p = tmp_path / "doc.pdf"
    p.write_bytes(make_pdf_pages(texts))
    return str(p)


def test_ocr_only_runs_on_low_text_pages_and_keeps_page_numbers(tmp_path):
    calls = []

    def fake_ocr(page_number):
        calls.append(page_number)
        return "Scanned invoice total is 500 rupees and payment is due in thirty days"

    pages = parse_file(_pdf(tmp_path, [LONG, "", LONG]), "pdf", ocr=fake_ocr)
    assert calls == [2]  # text pages are never OCR'd
    assert [p.number for p in pages] == [1, 2, 3]
    assert "invoice" in pages[1].text


def test_no_ocr_available_gives_clear_error_for_scanned_pdf(tmp_path):
    with pytest.raises(ParseError, match="scanned"):
        parse_file(_pdf(tmp_path, ["", ""]), "pdf", ocr=None)


def test_ocr_failure_keeps_text_pages(tmp_path):
    def broken_ocr(page_number):
        raise RuntimeError("tesseract is not installed")

    pages = parse_file(_pdf(tmp_path, [LONG, ""]), "pdf", ocr=broken_ocr)
    assert [p.number for p in pages] == [1]


def test_ocr_failure_on_fully_scanned_pdf_reports_reason(tmp_path):
    def broken_ocr(page_number):
        raise RuntimeError("tesseract is not installed")

    with pytest.raises(ParseError, match="OCR failed: tesseract is not installed"):
        parse_file(_pdf(tmp_path, ["", ""]), "pdf", ocr=broken_ocr)


def test_ocr_with_empty_result_is_reported(tmp_path):
    with pytest.raises(ParseError, match="even with OCR"):
        parse_file(_pdf(tmp_path, [""]), "pdf", ocr=lambda n: "   ")


def test_ocr_page_limit_is_respected(tmp_path):
    calls = []

    def fake_ocr(n):
        calls.append(n)
        return "recognised text that is definitely longer than fifty characters in total"

    pages = parse_file(_pdf(tmp_path, ["", "", "", ""]), "pdf", ocr=fake_ocr, ocr_max_pages=2)
    assert calls == [1, 2]
    assert [p.number for p in pages] == [1, 2]


def test_short_ocr_result_does_not_replace_better_extracted_text(tmp_path):
    short_real = "Short title page text"  # < 50 chars so OCR is tried
    pages = parse_file(_pdf(tmp_path, [short_real]), "pdf", ocr=lambda n: "x")
    assert pages[0].text == short_real
