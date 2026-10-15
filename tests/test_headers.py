import pymupdf
import pytest

from pdfdiff import compare
from pdfdiff.extract import load_document
from pdfdiff.headers import strip_headers_footers
from pdfdiff.model import CompareOptions

from .conftest import LOREM, para

AUTO = CompareOptions(header_footer="auto")


def texts(doc):
    return [" ".join(w.text for w in p.words) for p in doc.pages]


def test_page_numbers_and_headers_are_ignored(pdf):
    body = [[para(i)] for i in range(5)]
    old = pdf(body, header="ACME Annual Report - DRAFT", footer="Page {n} of {total}")
    new = pdf([[para(99)]] + body, header="ACME Annual Report - FINAL", footer="Page {n} of {total}")

    plain = compare(old, new)
    assert plain.changes  # the footers and headers differ

    r = compare(old, new, AUTO)
    assert r.changes == []
    assert [(pc.kind, pc.page) for pc in r.page_changes] == [("page_inserted", 0)]


def test_only_margin_lines_are_removed(pdf):
    old = pdf([[para(i), "Notes:"] for i in range(4)], footer="{n}")
    doc, removed = strip_headers_footers(load_document(old), "auto")
    assert removed == 4
    for i, text in enumerate(texts(doc)):
        assert text.startswith(para(i).split()[0]) and text.endswith("Notes:")
        assert not text.endswith(f"Notes: {i + 1}")


def test_roman_numeral_page_numbers(pdf):
    romans = ["i", "ii", "iii", "iv"]
    path = pdf([[para(i)] for i in range(4)])
    d = pymupdf.open(path)
    for page, numeral in zip(d, romans):
        page.insert_text((300, 770), numeral, fontsize=9)
    d.saveIncr()
    doc, removed = strip_headers_footers(load_document(path), "auto")
    assert removed == 4
    assert all(t.split()[-1] not in romans for t in texts(doc))


def test_single_page_is_not_guessed(pdf):
    path = pdf([LOREM], header="Report", footer="Page 1")
    _, removed = strip_headers_footers(load_document(path), "auto")
    assert removed == 0


def test_lines_at_different_heights_are_kept(pdf):
    path = pdf([[para(i)] for i in range(4)])
    d = pymupdf.open(path)
    for i, page in enumerate(d):
        page.insert_text((72, 20 + 15 * i), "Section", fontsize=9)
    d.saveIncr()
    _, removed = strip_headers_footers(load_document(path), "auto")
    assert removed == 0


def test_unrepeated_margin_text_is_still_compared(pdf):
    def doc(pct):
        path = pdf([[para(1)], [para(2)], [para(3)]], footer="Page {n}")
        d = pymupdf.open(path)
        d[0].insert_text((72, 40), f"Revenue grew {pct}% this year", fontsize=9)
        d.saveIncr()
        return path

    r = compare(doc(5), doc(7), AUTO)
    assert [(c.old_text, c.new_text) for c in r.changes] == [("5%", "7%")]


def test_explicit_margins(pdf):
    old = pdf([LOREM], header="Report v1", footer="Page 1")
    new = pdf([LOREM], header="Report v2", footer="Page one")
    assert compare(old, new, CompareOptions(header_footer=(0, 0))).changes
    assert compare(old, new, CompareOptions(header_footer=(50, 30))).changes == []
    # The body starts at y=72: a bigger top margin removes the first line too.
    doc, _ = strip_headers_footers(load_document(old), (90, 0))
    assert "brown" not in texts(doc)[0] and "Pack" in texts(doc)[0]


def test_margins_use_displayed_orientation(pdf):
    path = pdf([LOREM], header="Report")
    d = pymupdf.open(path)
    d[0].set_rotation(90)  # the header is now along the right edge
    d.saveIncr()
    doc, _ = strip_headers_footers(load_document(path), (50, 50))
    assert "Report" in texts(doc)[0]
    doc, _ = strip_headers_footers(load_document(path), (0, 0))
    assert "Report" in texts(doc)[0]


@pytest.mark.parametrize("spec", ["auto", (40, 40)])
def test_header_only_page_becomes_blank(pdf, spec):
    path = pdf([[para(1)], [], [para(2)]], header="ACME", footer="Page {n}")
    doc, _ = strip_headers_footers(load_document(path), spec)
    assert [p.has_text for p in doc.pages] == [True, False, True]
