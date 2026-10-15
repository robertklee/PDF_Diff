"""Turn extracted words into normalized comparison tokens."""

from __future__ import annotations

import re
import unicodedata

from .model import CompareOptions, Document, Token, Word

_CHAR_MAP = str.maketrans(
    {
        "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'", "\u2032": "'",
        "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u201f": '"', "\u2033": '"',
        "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-", "\u2014": "-",
        "\u2015": "-", "\u2212": "-",
        "\u00ad": None,  # soft hyphen
        "\u200b": None, "\u200c": None, "\u200d": None, "\ufeff": None,
    }
)
_WS = re.compile(r"\s+")


def normalize_text(text: str, options: CompareOptions) -> str:
    s = unicodedata.normalize("NFKC", text).translate(_CHAR_MAP)
    s = _WS.sub("", s)
    if options.ignore_punctuation:
        s = "".join(ch for ch in s if not unicodedata.category(ch).startswith("P"))
    if options.ignore_case:
        s = s.casefold()
    return s


def _ends_line_with_hyphen(word: Word, nxt: Word) -> bool:
    if (word.page, word.block, word.line) == (nxt.page, nxt.block, nxt.line):
        return False
    t = unicodedata.normalize("NFKC", word.text).translate(_CHAR_MAP)
    return len(t) > 1 and t.endswith("-") and t[-2].isalpha() and nxt.text[:1].islower()


def tokenize(words: list[Word], options: CompareOptions) -> list[Token]:
    tokens: list[Token] = []
    i = 0
    n = len(words)
    while i < n:
        w = words[i]
        if options.dehyphenate and i + 1 < n and _ends_line_with_hyphen(w, words[i + 1]):
            nxt = words[i + 1]
            head = unicodedata.normalize("NFKC", w.text).translate(_CHAR_MAP)[:-1]
            text = head + nxt.text
            norm = normalize_text(text, options)
            if norm:
                tokens.append(Token(norm=norm, text=text, words=(w, nxt)))
            i += 2
            continue
        norm = normalize_text(w.text, options)
        if norm:
            tokens.append(Token(norm=norm, text=w.text, words=(w,)))
        i += 1
    return tokens


def tokenize_document(doc: Document, options: CompareOptions) -> list[Token]:
    return tokenize([w for p in doc.pages for w in p.words], options)
