import pytest

from app.chunking.splitter import chunk_pages, clean_text
from app.parsers.files import Page


def test_chunks_keep_page_numbers_and_size_limit():
    pages = [Page(1, "alpha sentence. " * 200), Page(3, "beta sentence. " * 200)]
    chunks = chunk_pages(pages, chunk_size=500, overlap=50)
    assert len(chunks) > 2
    assert all(len(c.text) <= 500 for c in chunks)
    assert {c.page for c in chunks} == {1, 3}  # pages never mixed in one chunk
    assert [c.index for c in chunks] == list(range(len(chunks)))


def test_chunks_overlap():
    text = " ".join(f"word{i}" for i in range(400))
    chunks = chunk_pages([Page(1, text)], chunk_size=300, overlap=60)
    assert len(chunks) > 1
    tail = chunks[0].text.split()[-1]
    assert tail in chunks[1].text  # overlap carries context across the boundary


def test_empty_and_whitespace_pages_produce_no_chunks():
    assert chunk_pages([Page(1, "   \n\n  ")], 500, 50) == []


def test_overlap_must_be_smaller_than_size():
    with pytest.raises(ValueError):
        chunk_pages([Page(1, "x")], chunk_size=100, overlap=100)


def test_clean_text_collapses_whitespace():
    assert clean_text("a   b\t\tc\r\n\n\n\n\nd") == "a b c\n\nd"
