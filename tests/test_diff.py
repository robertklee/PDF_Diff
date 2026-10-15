import random

import pytest

from pdfdiff import diff as diffmod
from pdfdiff.diff import diff_sequences, diff_tokens
from pdfdiff.model import Token, Word


def apply(a, b, ops):
    """Rebuild b from a using opcodes; also checks opcodes cover both inputs contiguously."""
    out = []
    ai = bj = 0
    for tag, i1, i2, j1, j2 in ops:
        assert (i1, j1) == (ai, bj)
        if tag == "equal":
            assert a[i1:i2] == b[j1:j2]
            out.extend(a[i1:i2])
        else:
            assert tag == {(True, True): "replace", (True, False): "delete", (False, True): "insert"}[
                (i2 > i1, j2 > j1)
            ]
            out.extend(b[j1:j2])
        ai, bj = i2, j2
    assert (ai, bj) == (len(a), len(b))
    return out


def test_simple_ops():
    a = "the quick brown fox".split()
    b = "the slow brown fox jumps".split()
    ops = diff_sequences(a, b)
    assert apply(a, b, ops) == b
    assert [o[0] for o in ops] == ["equal", "replace", "equal", "insert"]


def test_empty_inputs():
    assert diff_sequences([], []) == []
    assert diff_sequences([], ["x"]) == [("insert", 0, 0, 0, 1)]
    assert diff_sequences(["x"], []) == [("delete", 0, 1, 0, 0)]


def test_adjacent_changes_are_merged():
    ops = [("delete", 0, 1, 0, 0), ("insert", 1, 1, 0, 2), ("equal", 1, 2, 2, 3)]
    assert diffmod._merge(ops) == [("replace", 0, 1, 0, 2), ("equal", 1, 2, 2, 3)]


@pytest.mark.parametrize("seed", range(40))
def test_random_roundtrip(seed, monkeypatch):
    rng = random.Random(seed)
    # Exercise the patience-anchor path too, using a tiny direct limit.
    if seed % 2:
        monkeypatch.setattr(diffmod, "_DIRECT_LIMIT", 16)
    vocab = [f"w{i}" for i in range(rng.randint(3, 60))]
    a = [rng.choice(vocab) for _ in range(rng.randint(0, 200))]
    b = list(a)
    for _ in range(rng.randint(0, 15)):
        op = rng.random()
        pos = rng.randint(0, len(b))
        if op < 0.33:
            b[pos:pos] = [rng.choice(vocab) for _ in range(rng.randint(1, 5))]
        elif op < 0.66:
            del b[pos : pos + rng.randint(1, 5)]
        elif b:
            b[min(pos, len(b) - 1)] = rng.choice(vocab)
    assert apply(a, b, diff_sequences(a, b)) == b
    assert apply(a, b, diffmod.cleanup_semantic(diff_sequences(a, b))) == b


def test_cleanup_absorbs_short_coincidental_match():
    a = "x deleted para with the many words y".split()
    b = "x added at the end y".split()
    ops = diffmod.cleanup_semantic(diff_sequences(a, b))
    assert [o[0] for o in ops] == ["equal", "replace", "equal"]
    assert apply(a, b, ops) == b


def test_cleanup_keeps_unchanged_sentence_next_to_big_edit():
    sentence = "how vexingly quick daft zebras jump".split()
    a = "x".split() + "old words here now gone".split() + sentence
    b = "x".split() + "new".split() + sentence + [f"n{i}" for i in range(40)]
    ops = diffmod.cleanup_semantic(diff_sequences(a, b))
    assert ("equal", 6, 12, 2, 8) in ops


def test_cleanup_keeps_meaningful_matches():
    a = "the quick brown fox jumps over the lazy dog".split()
    b = "the slow brown fox jumps over the sleepy dog".split()
    ops = diffmod.cleanup_semantic(diff_sequences(a, b))
    assert [o[0] for o in ops] == ["equal", "replace", "equal", "replace", "equal"]


def test_large_input_uses_anchors_and_finds_minimal_change():
    a = [f"w{i}" for i in range(20000)]
    b = list(a)
    b[10000] = "CHANGED"
    ops = diff_sequences(a, b)
    assert [o for o in ops if o[0] != "equal"] == [("replace", 10000, 10001, 10000, 10001)]


def _toks(text, page=0, block=0, x=0, y=0):
    return [
        Token(norm=t, text=t, words=(Word(t, page, (x + i, y, x + i + 1, y + 1), block, 0),))
        for i, t in enumerate(text.split())
    ]


def test_diff_tokens_context_and_anchor():
    old = _toks("one two three four five")
    new = _toks("one two NEW three four five")
    [ch] = diff_tokens(old, new)
    assert ch.kind == "insert"
    assert ch.new_text == "NEW"
    assert ch.context_before == "one two"
    assert ch.context_after == "three four five"
    assert ch.old_anchor.text == "two"


def test_diff_tokens_reflow_across_blocks_is_identical():
    old = _toks("a b c d", block=0) + _toks("e f g h", block=1)
    new = _toks("a b c", block=0) + _toks("d e f g h", block=1)
    assert diff_tokens(old, new) == []


@pytest.mark.parametrize("page", [0, 1])
@pytest.mark.parametrize("shift_y", [0, 30])
def test_leading_deletion_follows_label_across_rows_and_pages(page, shift_y):
    previous = _toks("previous", y=10)
    old = previous + _toks("X", page=page, x=74.5, y=40) + _toks("label", page=page, x=90, y=40)
    new = previous + _toks("label", page=page, x=90, y=40 + shift_y)
    [change] = diff_tokens(old, new)
    assert change.old_text == "X"
    assert change.new_anchor == new[-1].words[0]
    assert not change.new_anchor_after
    assert change.new_caret_x == 74.5


def test_leading_deletion_does_not_copy_source_x_when_text_reflows():
    previous = _toks("previous", y=10)
    old = previous + _toks("X", x=72, y=40) + _toks("label", x=150, y=40)
    new = previous + _toks("label", x=72, y=40)
    [change] = diff_tokens(old, new)
    assert change.new_anchor == new[-1].words[0]
    assert not change.new_anchor_after
    assert change.new_caret_x is None


def test_leading_sentence_keeps_existing_anchor():
    previous = _toks("previous", y=10)
    old = previous + _toks("label", x=90, y=40)
    new = previous + _toks("Brand new sentence.", x=72, y=40) + _toks("label", x=90, y=40)
    [change] = diff_tokens(old, new)
    assert change.old_anchor == old[0].words[0]
    assert change.old_anchor_after
    assert change.old_caret_x is None


def test_trailing_deletion_keeps_preceding_row_anchor():
    previous = _toks("label", x=90, y=10)
    following = _toks("following", x=90, y=40)
    old = previous + _toks("X", x=110, y=10) + following
    new = previous + following
    [change] = diff_tokens(old, new)
    assert change.new_anchor == new[0].words[0]
    assert change.new_anchor_after
    assert change.new_caret_x is None


def test_leading_deletion_does_not_anchor_to_skipped_page():
    previous = _toks("previous", y=10)
    old = previous + _toks("X", x=74.5, y=40) + _toks("label", x=90, y=40)
    new = previous + _toks("label", page=1, x=90, y=40)
    [change] = diff_tokens(old, new, skip_new_pages=frozenset({1}))
    assert change.new_anchor == new[0].words[0]
    assert change.new_anchor_after
