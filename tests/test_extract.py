import pymupdf
import pytest

from pdfdiff import compare, extract, parallel
from pdfdiff.cli import main
from pdfdiff.extract import load_documents

from .conftest import LOREM, para


@pytest.fixture
def small_chunks(monkeypatch):
    monkeypatch.setattr(extract, "PARALLEL_MIN_PAGES", 1)
    monkeypatch.setattr(extract, "_MIN_CHUNK", 2)


def test_parallel_matches_serial(pdf, small_chunks, monkeypatch):
    used = []
    real = extract._extract_parallel
    monkeypatch.setattr(extract, "_extract_parallel", lambda *a: used.append(real(*a)) or used[-1])
    a = pdf([[para(i)] for i in range(7)])
    b = pdf([[para(i + 50)] for i in range(5)], password="pw")
    specs = [(a, None), (b, "pw")]
    serial = load_documents(specs, jobs=1)
    parallel = load_documents(specs, jobs=3)
    assert used and used[0] is not None  # really ran in worker processes
    assert parallel == serial
    assert [p.index for p in parallel[0].pages] == list(range(7))
    assert parallel[1].password == "pw"


def test_falls_back_to_serial_when_workers_fail(pdf, small_chunks, monkeypatch):
    class Broken:
        def __init__(self, *a, **k):
            raise OSError("no processes allowed")

    monkeypatch.setattr(parallel, "ProcessPoolExecutor", Broken)
    a = pdf([[para(i)] for i in range(4)])
    [doc] = load_documents([(a, None)], jobs=4)
    assert len(doc.pages) == 4 and all(p.words for p in doc.pages)


def test_rotation_matrix_is_recorded(pdf):
    path = pdf([LOREM])
    d = pymupdf.open(path)
    d[0].set_rotation(90)
    d.saveIncr()
    [doc] = load_documents([(path, None)], jobs=1)
    page = doc.pages[0]
    assert (page.width, page.height) == (792, 612)
    w = page.words[0]  # "The", near the top-left of the unrotated page
    x0, y0, x1, y1 = page.displayed(w.bbox)
    assert x0 > 612 / 2 and y0 < 792 / 2  # displayed near the top-right


def test_damaged_page_is_a_warning(pdf, capsys):
    path = pdf([[para(1)], [para(2)], [para(3)]])
    d = pymupdf.open(path)
    xref = d[1].get_contents()[0]
    d.update_stream(xref, b"BT /F1 11 Tf 72 72 Td (Readable) Tj ET q /Nope Do Q BT ((( garbage")
    d.saveIncr()
    r = compare(path, path)
    assert any(w.startswith("old document page 2: damaged content") for w in r.warnings)
    assert any(w.startswith("new document page 2: damaged content") for w in r.warnings)
    assert "MuPDF" not in capsys.readouterr().err


def test_cli_warnings_go_to_stderr_without_term(pdf, tmp_path, capsys):
    a = pdf([[para(1)], []])
    assert main([str(a), str(a), "-f", "json", "-o", str(tmp_path)]) == 0
    err = capsys.readouterr().err
    assert "pdfdiff: warning: old document pages without extractable text: 2" in err


def test_cli_header_footer_options(pdf, capsys):
    old = pdf([[para(1)], [para(2)]], header="Draft", footer="Page {n}")
    new = pdf([[para(1)], [para(2)]], header="Final", footer="Page {n}")
    assert main([str(old), str(new)]) == 1
    assert main([str(old), str(new), "--ignore-header-footer"]) == 0
    assert main([str(old), str(new), "--margins", "50,30"]) == 0
    assert main([str(old), str(new), "--margins", "0,0", "--jobs", "1"]) == 1


@pytest.mark.parametrize("args", [["--margins", "50"], ["--margins", "a,b"], ["--margins", "-1,3"], ["--jobs", "0"]])
def test_cli_rejects_bad_values(pdf, args):
    a = pdf([LOREM])
    with pytest.raises(SystemExit) as e:
        main([str(a), str(a), *args])
    assert e.value.code == 2


def test_cli_interrupt_and_internal_error(pdf, monkeypatch, capsys):
    a = pdf([LOREM])

    def boom(exc):
        def f(*args, **kwargs):
            raise exc

        return f

    monkeypatch.setattr("pdfdiff.cli.compare", boom(KeyboardInterrupt()))
    assert main([str(a), str(a)]) == 130
    monkeypatch.setattr("pdfdiff.cli.compare", boom(ZeroDivisionError("oops")))
    assert main([str(a), str(a)]) == 2
    assert "internal error: ZeroDivisionError: oops" in capsys.readouterr().err
    monkeypatch.setenv("PDFDIFF_DEBUG", "1")
    with pytest.raises(ZeroDivisionError):
        main([str(a), str(a)])
