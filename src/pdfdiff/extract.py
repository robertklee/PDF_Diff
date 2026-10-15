"""Text extraction from PDF files using PyMuPDF."""

from __future__ import annotations

import os

import pymupdf

from .model import Document, Page, Word


class PDFError(Exception):
    """Raised when a PDF cannot be opened or read."""


def load_document(path: str | os.PathLike, password: str | None = None) -> Document:
    path = os.fspath(path)
    if not os.path.isfile(path):
        raise PDFError(f"{path}: file not found")
    try:
        doc = pymupdf.open(path, filetype="pdf")
    except Exception as e:
        raise PDFError(f"{path}: cannot open PDF ({e})") from e

    with doc:
        if doc.needs_pass:
            if not password:
                raise PDFError(f"{path}: PDF is encrypted; a password is required")
            if not doc.authenticate(password):
                raise PDFError(f"{path}: incorrect password")

        pages: list[Page] = []
        for page in doc:
            # sort=False keeps MuPDF's content order, which preserves reading
            # order in multi-column layouts (sort=True interleaves columns).
            raw = page.get_text("words", sort=False)
            words = [
                Word(
                    text=w[4],
                    page=page.number,
                    bbox=(w[0], w[1], w[2], w[3]),
                    block=w[5],
                    line=w[6],
                )
                for w in raw
                if w[4].strip()
            ]
            pages.append(
                Page(
                    index=page.number,
                    width=page.rect.width,
                    height=page.rect.height,
                    words=words,
                )
            )
    return Document(path=path, pages=pages, password=password)
