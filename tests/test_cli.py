import json

from pdfdiff.cli import main

from .conftest import LOREM


def test_identical_exit_0(pdf, capsys):
    a = pdf([LOREM])
    assert main([str(a), str(a), "--no-color"]) == 0
    assert "No differences found." in capsys.readouterr().out


def test_differences_exit_1_and_term_output(pdf, capsys):
    a = pdf([LOREM])
    b = pdf([[LOREM[0].replace("brown", "red")] + LOREM[1:]])
    assert main([str(a), str(b), "--no-color"]) == 1
    out = capsys.readouterr().out
    assert "[1] replace  old p.1 -> new p.1" in out
    assert "The quick [-brown-] {+red+} fox jumps" in out
    assert "1 change: 1 replacement" in out
    assert "\033[" not in out


def test_json_output(pdf, tmp_path, capsys):
    a = pdf([LOREM])
    b = pdf([[LOREM[0].replace("lazy dog", "sleepy cat")] + LOREM[1:] + ["Extra para."]])
    outdir = tmp_path / "out"
    assert main([str(a), str(b), "-f", "json", "-o", str(outdir)]) == 1
    data = json.loads((outdir / f"{a.stem}_vs_{b.stem}.diff.json").read_text())
    assert data["schema"] == 1
    assert data["summary"] == {
        "identical": False,
        "changes": 2,
        "insertions": 1,
        "deletions": 0,
        "replacements": 1,
        "pages_inserted": 0,
        "pages_deleted": 0,
    }
    assert data["page_changes"] == []
    assert data["page_map"] == [[1, 1]]
    rep, ins = data["changes"]
    assert (rep["old"]["text"], rep["new"]["text"]) == ("lazy dog", "sleepy cat")
    assert rep["old"]["rects"][0]["page"] == 1
    assert len(rep["old"]["rects"][0]["bbox"]) == 4
    assert ins["kind"] == "insert" and ins["new"]["text"] == "Extra para."
    assert ins["old"]["anchor"]["page"] == 1
    # JSON-only run prints the summary on stderr, nothing on stdout
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "2 changes" in captured.err


def test_errors_exit_2(tmp_path, capsys):
    assert main([str(tmp_path / "x.pdf"), str(tmp_path / "y.pdf")]) == 2
    assert "file not found" in capsys.readouterr().err


def test_invalid_format_exit_2(pdf):
    a = pdf([LOREM])
    try:
        main([str(a), str(a), "-f", "xml"])
    except SystemExit as e:
        assert e.code == 2
    else:
        raise AssertionError("expected SystemExit")


def test_page_changes_in_outputs(pdf, tmp_path, capsys):
    from .conftest import para

    a = pdf([[para(1)], [para(2)]])
    b = pdf([[para(1)], [para(99)], [para(2)]])
    assert main([str(a), str(b), "-f", "term,json", "-o", str(tmp_path), "--no-color"]) == 1
    out = capsys.readouterr().out
    assert "[P1] page inserted  new p.2 (after old p.1)" in out
    assert "1 change: 1 page inserted" in out
    data = json.loads((tmp_path / f"{a.stem}_vs_{b.stem}.diff.json").read_text())
    assert data["page_changes"] == [
        {
            "id": 1,
            "kind": "page_inserted",
            "new_page": 2,
            "after_old_page": 1,
            "word_count": 60,
            "preview": " ".join(para(99).split()[:12]) + " …",
        }
    ]
    assert data["page_map"] == [[1, 1], [None, 2], [2, 3]]
    assert data["changes"] == []


def test_invalid_page_threshold_exit_2(pdf):
    a = pdf([LOREM])
    try:
        main([str(a), str(a), "--page-threshold", "1.5"])
    except SystemExit as e:
        assert e.code == 2
    else:
        raise AssertionError("expected SystemExit")
