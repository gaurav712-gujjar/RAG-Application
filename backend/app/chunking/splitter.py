import re
from dataclasses import dataclass

from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.parsers.files import Page


@dataclass
class Chunk:
    index: int
    page: int
    text: str


_SPACES = re.compile(r"[ \t]+")
_BLANKS = re.compile(r"\n{3,}")


def clean_text(text: str) -> str:
    """Strip junk whitespace so we don't pay tokens for it."""
    text = text.replace("\r", "")
    text = _SPACES.sub(" ", text)
    text = _BLANKS.sub("\n\n", text)
    return text.strip()


def chunk_pages(pages: list[Page], chunk_size: int, overlap: int) -> list[Chunk]:
    """Split page by page so every chunk keeps an accurate page number for citations."""
    if overlap >= chunk_size:
        raise ValueError("overlap must be smaller than chunk_size")
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=overlap,
        separators=["\n\n", "\n", ". ", " ", ""],
    )
    chunks: list[Chunk] = []
    for page in pages:
        for piece in splitter.split_text(clean_text(page.text)):
            piece = piece.strip()
            if piece:
                chunks.append(Chunk(index=len(chunks), page=page.number, text=piece))
    return chunks
