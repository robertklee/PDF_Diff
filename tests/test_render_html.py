import base64
import json
import re

import pymupdf
import pytest

from pdfdiff import compare
from pdfdiff.cli import main
from pdfdiff.render import html

from .conftest import LOREM, para

MARK = re.compile(r'<a(?: id="(c\d+)")? class="mark (hl|caret) (\w+)" href="#item-(\d+)" data-id="\d+" title="[^"]*" style="([^"]+)"')


def cells(report: str):
    """(side, page number, image index, [marks]) for each rendered page cell."""
    out = []
    for m in re.finditer(r'<div class="cell (old|new)"><div class="hdr">\w+ page (\d+)(.*?)</div></div>', report, re.S):
        img = re.search(r'data-img="(\d+)"', m.group(3))
        out.append((m.group(1), int(m.group(2)), int(img.group(1)), MARK.findall(m.group(3))))
    return out


def images(report: str) -> list[str]:
    return json.loads(re.search(r'<script type="application/json" id="images">(.*?)</script>', report, re.S).group(1))


def box(style: str) -> tuple[float, ...]:
    v = dict(re.findall(r"(\w+):([\d.]+)%", style))
    return tuple(float(v[k]) / 100 for k in ("left", "top", "width", "height"))


def pixmap(uri: str) -> pymupdf.Pixmap:
    return pymupdf.Pixmap(base64.b64decode(uri.split(",", 1)[1]))


def ink(pix: pymupdf.Pixmap, left, top, width, height) -> float:
    """Fraction of dark pixels in a region given in fractions of the image size."""
    x0, y0 = int(left * pix.width), int(top * pix.height)
    x1, y1 = max(x0 + 1, int((left + width) * pix.width)), max(y0 + 1, int((top + height) * pix.height))
    dark = total = 0
    for y in range(y0, min(y1, pix.height)):
        for x in range(x0, min(x1, pix.width)):
            total += 1
            dark += min(pix.pixel(x, y)) < 128
    return dark / max(total, 1)


@pytest.fixture
def edited(pdf):
    old = pdf([LOREM])
    new = pdf([[LOREM[0].replace("brown", "red"), LOREM[1].replace("five dozen", "five extra dozen"), LOREM[3]]])
    return old, new


def test_report_is_self_contained(edited):
    report = html.render(compare(*edited))
    assert "Content-Security-Policy" in report and "default-src 'none'" in report
    assert not re.search(r'(src|href)="(?!#|data:)', report)
    assert "@import" not in report and "url(" not in report


def test_sidebar_and_marks(edited):
    r = compare(*edited)
    report = html.render(r)
    items = re.findall(r'<li class="item (\w+)" id="item-(\d+)" data-id="\d+" data-target="(c\d+)"', report)
    assert [(k, int(i)) for k, i, _ in items] == [(c.kind, c.id) for c in r.changes]
    for _, _, target in items:
        assert f'id="{target}"' in report
    [(_, _, _, old_marks), (_, _, _, new_marks)] = sorted(cells(report))[::-1]  # old first
    kinds = lambda ms: sorted((t, k) for _, t, k, _, _ in ms)  # noqa: E731
    assert kinds(old_marks) == [("caret", "insert"), ("hl", "delete"), ("hl", "replace")]
    assert kinds(new_marks) == [("caret", "delete"), ("hl", "insert"), ("hl", "replace")]
    assert "<del>brown</del> <ins>red</ins>" in report
    assert 'Replaced (1)' in report and 'Inserted (1)' in report and 'Deleted (1)' in report


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_highlights_cover_the_rendered_text(pdf, rotation):
    old = pdf([LOREM])
    new = pdf([[LOREM[0].replace("The quick brown fox", "The quick red fox")] + LOREM[1:]])
    for path in (old, new):
        d = pymupdf.open(path)
        d[0].set_rotation(rotation)
        d.saveIncr()
    report = html.render(compare(old, new))
    imgs = images(report)
    for side, _, img, marks in cells(report):
        [mark] = marks
        pix = pixmap(imgs[img])
        b = box(mark[4])
        assert ink(pix, *b) > 0.05, side
        # The same box moved diagonally into the opposite quadrant is blank.
        moved = (b[0] + (0.4 if b[0] < 0.5 else -0.4), b[1] + (0.4 if b[1] < 0.5 else -0.4))
        assert ink(pix, *moved, b[2], b[3]) == 0, side


def test_identical_pages_share_one_image_and_gray_pages_are_gray(pdf):
    a = pdf([[para(1)], [para(2)]])
    b = pdf([[para(1)], [para(2) + " extra"]])
    d = pymupdf.open(b)
    d[1].draw_rect(pymupdf.Rect(400, 700, 450, 750), fill=(1, 0, 0))
    d.saveIncr()
    report = html.render(compare(a, b))
    c = {(side, pno): img for side, pno, img, _ in cells(report)}
    assert c[("old", 1)] == c[("new", 1)]
    assert c[("old", 2)] != c[("new", 2)]
    imgs = images(report)
    assert len(imgs) == 3
    assert pixmap(imgs[c[("old", 1)]]).n == 1
    assert pixmap(imgs[c[("new", 2)]]).n == 3


def test_page_changes_and_placeholders(pdf):
    a = pdf([[para(1)], [para(2)], [para(3)]])
    b = pdf([[para(1)], [para(9)], [para(2)]])
    r = compare(a, b)
    report = html.render(r)
    assert report.count('class="row"') == len(r.page_map)
    assert "No page: inserted in new" in report and "No page: deleted from old" in report
    assert '<span class="badge page_inserted">INSERTED PAGE</span>' in report
    assert '<span class="badge page_deleted">DELETED PAGE</span>' in report
    for pc in r.page_changes:
        assert f'id="item-P{pc.id}"' in report and f'id="p{pc.id}"' in report


def test_text_is_escaped(pdf):
    a = pdf([["Plain text here."]])
    b = pdf([['Plain <script>alert("x")</script> & text here.']])
    report = html.render(compare(a, b))
    assert "<script>alert" not in report
    assert "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;" in report


def test_identical_documents(pdf):
    a = pdf([LOREM])
    report = html.render(compare(a, pdf([LOREM])))
    assert "No differences found." in report


def test_cli_html(edited, tmp_path):
    old, new = edited
    assert main([str(old), str(new), "-f", "html", "-o", str(tmp_path), "--dpi", "50"]) == 1
    path = tmp_path / f"{old.stem}_vs_{new.stem}.diff.html"
    pix = pixmap(images(path.read_text())[0])
    assert pix.width == round(612 * 50 / 72)


@pytest.mark.parametrize("dpi", ["10", "abc", "1000"])
def test_cli_rejects_bad_dpi(edited, dpi, capsys):
    with pytest.raises(SystemExit) as e:
        main([str(edited[0]), str(edited[1]), "--dpi", dpi])
    assert e.value.code == 2
