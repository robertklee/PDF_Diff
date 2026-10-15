from pdfdiff.model import CompareOptions, Word
from pdfdiff.normalize import normalize_text, tokenize

OPTS = CompareOptions()


def w(text, line=0, block=0, page=0):
    return Word(text=text, page=page, bbox=(0, 0, 1, 1), block=block, line=line)


def test_ligatures_quotes_dashes_are_normalized():
    assert normalize_text("\ufb01ne", OPTS) == "fine"
    assert normalize_text("\u201cquoted\u201d", OPTS) == '"quoted"'
    assert normalize_text("it\u2019s", OPTS) == "it's"
    assert normalize_text("a\u2014b", OPTS) == "a-b"
    assert normalize_text("soft\u00adhyphen", OPTS) == "softhyphen"


def test_ignore_case_and_punctuation():
    opts = CompareOptions(ignore_case=True, ignore_punctuation=True)
    assert normalize_text("Hello,", opts) == "hello"
    assert normalize_text("...", opts) == ""


def test_punctuation_only_tokens_dropped_when_ignored():
    opts = CompareOptions(ignore_punctuation=True)
    toks = tokenize([w("a"), w("-"), w("b")], opts)
    assert [t.norm for t in toks] == ["a", "b"]


def test_dehyphenates_across_line_break():
    toks = tokenize([w("infor-", line=0), w("mation", line=1), w("age", line=1)], OPTS)
    assert [t.norm for t in toks] == ["information", "age"]
    assert toks[0].text == "information"
    assert len(toks[0].words) == 2


def test_no_dehyphenation_on_same_line_or_capitalized_or_disabled():
    assert [t.norm for t in tokenize([w("well-"), w("known")], OPTS)] == ["well-", "known"]
    assert [t.norm for t in tokenize([w("Anglo-"), w("Saxon", line=1)], OPTS)] == [
        "Anglo-",
        "Saxon",
    ]
    opts = CompareOptions(dehyphenate=False)
    assert [t.norm for t in tokenize([w("infor-"), w("mation", line=1)], opts)] == [
        "infor-",
        "mation",
    ]
