import pymupdf
import pytest

from pdfdiff import compare
from pdfdiff.cli import main
from pdfdiff.render import pdf as render_pdf

from .conftest import LOREM, para


class A:
    """Plain copy of an annotation (PyMuPDF annots die with their page object)."""

    def __init__(self, a):
        self.rect = pymupdf.Rect(a.rect)
        self.info = dict(a.info)
        self.colors = dict(a.colors)


def annots(doc, pno):
    return [(a.type[1], a.info.get("subject", ""), A(a)) for a in doc[pno].annots()]


def word_rect(page, text):
    for w in page.get_text("words"):
        if w[4] == text:
            return pymupdf.Rect(w[:4])
    raise AssertionError(f"word {text!r} not on page")


def covers(annot_list, rect):
    center = (rect.tl + rect.br) / 2
    return any(a.rect.contains(center) for _, _, a in annot_list)


@pytest.fixture
def sample(pdf, tmp_path):
    old = pdf([LOREM])
    new = pdf(
        [[LOREM[0].replace("brown", "red"), LOREM[1].replace("five dozen", "five extra dozen"), LOREM[3]]]
    )
    r = compare(old, new)
    kinds = sorted(c.kind for c in r.changes)
    assert kinds == ["delete", "insert", "replace"], kinds
    return r, tmp_path


def test_annotated_copies(sample):
    r, tmp = sample
    render_pdf.write_annotated(r, str(tmp / "o.pdf"), str(tmp / "n.pdf"))
    old, new = pymupdf.open(tmp / "o.pdf"), pymupdf.open(tmp / "n.pdf")

    old_a = annots(old, 0)
    new_a = annots(new, 0)
    # old: highlight "brown" (replace), highlight deleted sentence, caret for insertion
    assert sorted(t for t, _, _ in old_a) == ["Caret", "Highlight", "Highlight"]
    assert sorted(t for t, _, _ in new_a) == ["Caret", "Highlight", "Highlight"]

    assert covers(old_a, word_rect(old[0], "brown"))
    assert covers(new_a, word_rect(new[0], "red"))
    assert covers(old_a, word_rect(old[0], "Sphinx"))
    assert covers(new_a, word_rect(new[0], "extra"))

    replace = next(a for t, s, a in old_a if s.startswith("Replaced"))
    assert replace.colors["stroke"] == pytest.approx(render_pdf.COLORS["replace"], abs=0.01)
    assert "Old (p.1): brown" in replace.info["content"]
    assert "New (p.1): red" in replace.info["content"]
    assert replace.info["title"] == "pdfdiff"

    caret_new = next(a for t, s, a in new_a if t == "Caret")
    assert caret_new.info["subject"].startswith("Deleted")
    # The insertion caret sits right after "five" in the old document.
    caret_old = next(a for t, s, a in old_a if t == "Caret")
    five = word_rect(old[0], "five")
    assert abs(caret_old.rect.x0 + caret_old.rect.width / 2 - five.x1) < 2


def test_leading_mark_caret_stays_on_its_row(leading_mark_diff, tmp_path):
    result = leading_mark_diff
    [change] = result.changes
    word = (change.old_words or change.new_words)[0]
    old_out, new_out = tmp_path / "o.pdf", tmp_path / "n.pdf"
    render_pdf.write_annotated(result, str(old_out), str(new_out))
    empty_out = new_out if change.kind == "delete" else old_out
    with pymupdf.open(empty_out) as doc:
        [(kind, _, caret)] = annots(doc, word.page)
        assert kind == "Caret"
        x0, y0, _, y1 = word.bbox
        center = (caret.rect.tl + caret.rect.br) / 2
        assert center.x == pytest.approx(x0, abs=0.01)
        assert center.y == pytest.approx(y1 - 0.25 * (y1 - y0), abs=0.01)


def test_page_banners_and_pointers(pdf, tmp_path):
    a = pdf([[para(1)], [para(2)], [para(3)]])
    b = pdf([[para(1)], [para(9)], [para(3)]])
    b2 = pdf([[para(1)], [para(9)], [para(2)], [para(3)]])
    r = compare(a, b2)
    assert [pc.kind for pc in r.page_changes] == ["page_inserted"]
    render_pdf.write_annotated(r, str(tmp_path / "o.pdf"), str(tmp_path / "n.pdf"))
    new = pymupdf.open(tmp_path / "n.pdf")
    types = sorted(t for t, _, _ in annots(new, 1))
    assert types == ["FreeText", "Square"]
    free = next(a for t, _, a in annots(new, 1) if t == "FreeText")
    assert "INSERTED PAGE" in free.info["content"]
    old = pymupdf.open(tmp_path / "o.pdf")
    [(t, _, note)] = annots(old, 0)
    assert t == "Text" and "New page 2 was inserted after this page." in note.info["content"]

    r = compare(a, pdf([[para(1)], [para(3)]]))
    render_pdf.write_annotated(r, str(tmp_path / "o2.pdf"), str(tmp_path / "n2.pdf"))
    old = pymupdf.open(tmp_path / "o2.pdf")
    free = next(a for t, _, a in annots(old, 1) if t == "FreeText")
    assert "DELETED PAGE" in free.info["content"]
    _ = b  # unused variant kept for readability


def test_encryption_preserved(pdf, tmp_path):
    a = pdf([LOREM], password="pw")
    b = pdf([[LOREM[0].replace("brown", "red")] + LOREM[1:]])
    r = compare(a, b, old_password="pw")
    render_pdf.write_annotated(r, str(tmp_path / "o.pdf"), str(tmp_path / "n.pdf"))
    render_pdf.write_combined(r, str(tmp_path / "c.pdf"))
    o = pymupdf.open(tmp_path / "o.pdf")
    assert o.needs_pass and o.authenticate("pw")
    assert not pymupdf.open(tmp_path / "n.pdf").needs_pass
    c = pymupdf.open(tmp_path / "c.pdf")
    assert c.needs_pass and c.authenticate("pw")


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_combined_marks_line_up_on_rotated_pages(pdf, tmp_path, rotation):
    old = pdf([LOREM])
    new = pdf([[LOREM[0].replace("brown", "red")] + LOREM[1:]])
    for path in (old, new):
        d = pymupdf.open(path)
        d[0].set_rotation(rotation)
        d.saveIncr()
    r = compare(old, new)
    out = tmp_path / "c.pdf"
    render_pdf.write_combined(r, str(out))
    c = pymupdf.open(out)
    assert c.page_count == 2  # cover + one row
    row = c[1]
    marks = annots(c, 1)
    assert len(marks) == 2
    assert covers(marks, word_rect(row, "brown"))
    assert covers(marks, word_rect(row, "red"))
    # Cover links to the row
    [link] = c[0].get_links()
    assert link["page"] == 1


def test_combined_with_page_changes_and_blank_pages(pdf, tmp_path):
    a = pdf([[para(1)], [], [para(2)], [para(3)]])
    b = pdf([[para(1)], [], [para(9)], [para(2)]])
    r = compare(a, b)
    assert sorted(pc.kind for pc in r.page_changes) == ["page_deleted", "page_inserted"]
    out = tmp_path / "c.pdf"
    render_pdf.write_combined(r, str(out))
    c = pymupdf.open(out)
    assert c.page_count == 1 + len(r.page_map)
    text = "\n".join(p.get_text() for p in c)
    assert "INSERTED PAGE" in text and "DELETED PAGE" in text
    assert "No page: inserted in new" in text and "No page: deleted from old" in text


def test_cover_handles_many_changes(pdf, tmp_path):
    words = [f"w{i}" for i in range(300)]
    a = pdf([[" ".join(words)]])
    b = pdf([[" ".join(w if i % 3 else "X" for i, w in enumerate(words))]])
    r = compare(a, b)
    assert len(r.changes) == 100
    out = tmp_path / "c.pdf"
    render_pdf.write_combined(r, str(out))
    c = pymupdf.open(out)
    n_cover = c.page_count - len(r.page_map)
    assert n_cover == 2
    assert sum(len(c[i].get_links()) for i in range(n_cover)) == 100


def test_cli_writes_pdfs(pdf, tmp_path):
    a = pdf([LOREM])
    b = pdf([[LOREM[0].replace("brown", "red")] + LOREM[1:]])
    out = tmp_path / "out"
    assert main([str(a), str(b), "-f", "pdf,combined", "-o", str(out)]) == 1
    stem = f"{a.stem}_vs_{b.stem}.diff"
    for suffix in ("old.pdf", "new.pdf", "combined.pdf"):
        assert pymupdf.open(out / f"{stem}.{suffix}").page_count >= 1
