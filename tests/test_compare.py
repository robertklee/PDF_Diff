import pymupdf
import pytest

from pdfdiff import CompareOptions, compare
from pdfdiff.extract import PDFError

from .conftest import LOREM


def test_identical(pdf):
    a = pdf([LOREM[:2], LOREM[2:]])
    b = pdf([LOREM[:2], LOREM[2:]])
    r = compare(a, b)
    assert r.identical
    assert r.warnings == []


def test_word_replaced(pdf):
    a = pdf([LOREM])
    b = pdf([[LOREM[0].replace("brown", "red")] + LOREM[1:]])
    [ch] = compare(a, b).changes
    assert (ch.kind, ch.old_text, ch.new_text) == ("replace", "brown", "red")
    assert ch.old_pages == ch.new_pages == [0]
    assert ch.old_words[0].bbox[0] > 72


def test_sentence_inserted(pdf):
    a = pdf([LOREM])
    b = pdf([[LOREM[0] + " A brand new sentence."] + LOREM[1:]])
    [ch] = compare(a, b).changes
    assert ch.kind == "insert"
    assert ch.new_text == "A brand new sentence."
    assert ch.old_anchor is not None and ch.old_anchor.text == "bank."


def test_paragraph_deleted(pdf):
    a = pdf([LOREM])
    b = pdf([[LOREM[0], LOREM[2], LOREM[3]]])
    [ch] = compare(a, b).changes
    assert ch.kind == "delete"
    assert ch.old_text == LOREM[1]


def test_reflow_across_page_break_is_not_a_change(pdf):
    a = pdf([LOREM[:2], LOREM[2:]])
    b = pdf([LOREM[:3], LOREM[3:]])
    assert compare(a, b).identical


def test_change_on_later_page_reports_page(pdf):
    a = pdf([LOREM[:2], LOREM[2:]])
    b = pdf([LOREM[:2], [LOREM[2], LOREM[3].replace("zebras", "giraffes")]])
    [ch] = compare(a, b).changes
    assert ch.old_pages == ch.new_pages == [1]


def test_two_column_reading_order(tmp_path, pdf):
    path = tmp_path / "cols.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_textbox(pymupdf.Rect(36, 72, 296, 700), " ".join(LOREM[:2]), fontsize=11)
    page.insert_textbox(pymupdf.Rect(316, 72, 576, 700), " ".join(LOREM[2:]), fontsize=11)
    doc.save(path)
    single = pdf([[" ".join(LOREM)]])
    assert compare(path, single).identical


def test_hyphenated_line_break_matches_whole_word(tmp_path, pdf):
    path = tmp_path / "hyph.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "The infor-", fontsize=11)
    page.insert_text((72, 114), "mation age", fontsize=11)
    doc.save(path)
    assert compare(path, pdf([["The information age"]])).identical


def test_ignore_case(pdf):
    a = pdf([["Hello World"]])
    b = pdf([["hello world"]])
    assert len(compare(a, b).changes) == 1
    assert compare(a, b, CompareOptions(ignore_case=True)).identical


def test_page_without_text_warns(tmp_path, pdf):
    a = pdf([LOREM, []])
    r = compare(a, a)
    assert r.identical
    assert any("pages without extractable text: 2" in w for w in r.warnings)


def test_encrypted_pdf(pdf):
    a = pdf([LOREM], password="s3cret")
    b = pdf([LOREM])
    with pytest.raises(PDFError, match="password is required"):
        compare(a, b)
    with pytest.raises(PDFError, match="incorrect password"):
        compare(a, b, old_password="wrong")
    assert compare(a, b, old_password="s3cret").identical


def test_missing_and_invalid_files(tmp_path):
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"not a pdf")
    with pytest.raises(PDFError, match="file not found"):
        compare(tmp_path / "nope.pdf", bad)
    with pytest.raises(PDFError, match="cannot open PDF"):
        compare(bad, bad)
