import pymupdf
import pytest

from pdfdiff import compare
from pdfdiff.layout import SATELLITE_LINE_BASE, reading_order

QUESTIONS = ["Do you smoke?", "Do you drink?", "Do you exercise?", "Any allergies?", "Need parking?"]
BOX_X = (300, 380)  # Yes, No


def make_form(path, checks, overlay):
    """A questionnaire with Yes/No checkboxes; ``checks[i]`` is 0 (Yes), 1 (No) or None.

    With ``overlay`` the X marks are drawn after all other text, as in a filled-in form.
    """
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    marks = []
    for i, q in enumerate(QUESTIONS):
        y = 100 + i * 30
        page.insert_text((72, y), q, fontsize=11, fontname="helv")
        for j, label in enumerate(("Yes", "No")):
            x = BOX_X[j]
            page.draw_rect(pymupdf.Rect(x, y - 10, x + 12, y + 2))
            page.insert_text((x + 18, y), label, fontsize=11, fontname="helv")
            if checks[i] == j:
                if overlay:
                    marks.append((x + 2.5, y))
                else:
                    page.insert_text((x + 2.5, y), "X", fontsize=11, fontname="helv")
    for p in marks:
        page.insert_text(p, "X", fontsize=11, fontname="helv")
    doc.save(path)
    return path


def w(x0, y0, x1, y1, text, block, line):
    return (x0, y0, x1, y1, text, block, line)


def texts(words):
    return [t[4] for t in words]


def test_satellite_moves_into_its_row_in_x_order():
    words = [
        w(72, 88, 148, 103, "Question", 0, 0),
        w(318, 88, 337, 103, "Yes", 0, 1),
        w(398, 88, 412, 103, "No", 0, 2),
        w(72, 118, 148, 133, "Next", 1, 0),
        w(382, 88, 390, 103, "X", 2, 0),  # drawn last, belongs to the first row
    ]
    out = reading_order(words)
    assert texts(out) == ["Question", "Yes", "X", "No", "Next"]
    x = out[2]
    assert x[5] == 0 and x[6] >= SATELLITE_LINE_BASE  # joins the host block, own line


def test_lines_of_a_row_are_sorted_left_to_right():
    words = [w(318, 88, 337, 103, "Yes", 0, 0), w(302, 88, 310, 103, "X", 0, 1), w(398, 88, 412, 103, "No", 0, 2)]
    assert texts(reading_order(words)) == ["X", "Yes", "No"]


def test_long_blocks_and_separate_rows_keep_content_order():
    words = [
        w(72, 88, 290, 103, "Left", 0, 0),
        w(72, 103, 290, 118, "column", 0, 1),
        w(320, 88, 540, 103, "Right", 1, 0),  # second column, same row: not a satellite
        w(320, 88, 540, 103, "heading", 1, 0),
        w(72, 300, 80, 315, "7", 2, 0),  # short, but alone on its row
    ]
    assert texts(reading_order(words)) == texts(words)


def test_right_to_left_rows_are_not_reordered():
    words = [w(300, 88, 340, 103, "שלום", 0, 0), w(200, 88, 240, 103, "עולם", 0, 1)]
    assert texts(reading_order(words)) == ["שלום", "עולם"]


@pytest.mark.parametrize("old_overlay,new_overlay", [(False, False), (True, True), (False, True), (True, False)])
def test_checkbox_changes_are_located_on_the_right_box(tmp_path, old_overlay, new_overlay):
    old = make_form(tmp_path / "old.pdf", [0, 1, 1, 0, None], old_overlay)
    new = make_form(tmp_path / "new.pdf", [1, 1, 0, 0, 0], new_overlay)
    r = compare(old, new)

    def box(word):  # (question, answer) of an X
        x0, y0 = word.bbox[0], word.bbox[1]
        return (round((y0 - 88) / 30), BOX_X.index(min(BOX_X, key=lambda b: abs(b - x0))))

    deleted = sorted(box(wd) for c in r.changes for wd in c.old_words)
    inserted = sorted(box(wd) for c in r.changes for wd in c.new_words)
    assert deleted == [(0, 0), (2, 1)]
    assert inserted == [(0, 1), (2, 0), (4, 0)]
    assert all(t.text == "X" for c in r.changes for t in c.old_tokens + c.new_tokens)
    # The caret on the side without the mark points at the (empty) box.
    for c in r.changes:
        if c.old_words and not c.new_words:
            assert c.new_caret_x == c.old_words[0].bbox[0]
        if c.new_words and not c.old_words:
            assert c.old_caret_x == c.new_words[0].bbox[0]


def test_unchanged_form_has_no_differences(tmp_path):
    a = make_form(tmp_path / "a.pdf", [0, 1, 1, 0, 1], False)
    b = make_form(tmp_path / "b.pdf", [0, 1, 1, 0, 1], True)
    assert compare(a, b).changes == []


def test_json_anchor_has_caret_x_for_form_marks(tmp_path):
    from pdfdiff.render.jsonfmt import to_dict

    old = make_form(tmp_path / "old.pdf", [0, None, 1, 0, 1], True)
    new = make_form(tmp_path / "new.pdf", [0, 1, 1, 0, 1], True)
    (change,) = to_dict(compare(old, new))["changes"]
    assert change["kind"] == "insert"
    assert change["old"]["anchor"]["caret_x"] == change["new"]["rects"][0]["bbox"][0]
