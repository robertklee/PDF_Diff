import itertools
import random

import pytest

from pdfdiff import CompareOptions, compare
from pdfdiff.align import heaviest_chain

from .conftest import para


def kinds(r):
    return [(pc.kind, pc.page, pc.after) for pc in r.page_changes]


def test_page_inserted_in_middle(pdf):
    a = pdf([[para(1)], [para(2)], [para(3)]])
    b = pdf([[para(1)], [para(9)], [para(2)], [para(3)]])
    r = compare(a, b)
    assert kinds(r) == [("page_inserted", 1, 0)]
    assert r.changes == []
    assert r.page_map == [(0, 0), (None, 1), (1, 2), (2, 3)]
    assert not r.identical


def test_pages_deleted_at_start_and_end(pdf):
    a = pdf([[para(1)], [para(2)], [para(3)], [para(4)]])
    b = pdf([[para(2)], [para(3)]])
    r = compare(a, b)
    assert kinds(r) == [("page_deleted", 0, -1), ("page_deleted", 3, 1)]
    assert r.changes == []
    assert r.page_map == [(0, None), (1, 0), (2, 1), (3, None)]


def test_multiple_contiguous_pages_inserted(pdf):
    a = pdf([[para(1)], [para(2)]])
    b = pdf([[para(1)], [para(7)], [para(8)], [para(2)]])
    assert kinds(compare(a, b)) == [("page_inserted", 1, 0), ("page_inserted", 2, 0)]


def test_blank_page_inserted_and_blank_pages_matched(pdf):
    a = pdf([[para(1)], [], [para(2)]])
    b = pdf([[para(1)], [], [], [para(2)]])
    r = compare(a, b)
    assert kinds(r) == [("page_inserted", 2, 1)]
    assert r.page_changes[0].word_count == 0
    assert compare(a, a).identical


def test_reflow_to_more_pages_is_identical(pdf):
    a = pdf([[para(1), para(2), para(3)]])
    b = pdf([[para(1)], [para(2)], [para(3)]])
    r = compare(a, b)
    assert r.identical
    assert r.page_map == [(0, 0), (None, 1), (None, 2)]


def test_rewritten_page_is_text_change_not_page_change(pdf):
    a = pdf([[para(1)], [para(2)], [para(3)]])
    b = pdf([[para(1)], [para(5)], [para(3)]])
    r = compare(a, b)
    assert r.page_changes == []
    [ch] = r.changes
    assert ch.kind == "replace" and ch.old_pages == [1] and ch.new_pages == [1]
    assert r.page_map == [(0, 0), (1, 1), (2, 2)]


def test_inserted_page_plus_text_edit_nearby(pdf):
    a = pdf([[para(1)], [para(2)]])
    b = pdf([[para(1)], [para(9)], ["Brand new opening sentence. " + para(2)]])
    r = compare(a, b)
    assert kinds(r) == [("page_inserted", 1, 0)]
    [ch] = r.changes
    assert ch.kind == "insert"
    assert ch.new_text == "Brand new opening sentence."
    assert ch.new_pages == [2]
    assert ch.old_anchor is not None and ch.old_anchor.page == 0


def test_threshold_controls_partially_new_page(pdf):
    # New page 2 is half new text, half text that moved from old page 2.
    half = " ".join(para(2).split()[:30])
    rest = " ".join(para(2).split()[30:])
    a = pdf([[para(1)], [half], [rest]])
    b = pdf([[para(1)], [para(9, 30) + " " + half], [rest]])
    assert compare(a, b).page_changes == []
    r = compare(a, b, CompareOptions(page_threshold=0.6))
    assert [(k, p) for k, p, _ in kinds(r)] == [("page_inserted", 1)]
    # The matched half is still matched; no text is lost or duplicated.
    assert r.changes == []


def test_completely_different_documents_of_different_length(pdf):
    a = pdf([[para(1)]])
    b = pdf([[para(2)], [para(3)]])
    r = compare(a, b)
    assert kinds(r) == [("page_inserted", 1, 0)]
    [ch] = r.changes
    assert ch.kind == "replace" and ch.old_pages == [0] and ch.new_pages == [0]


def _brute(weights, n_rows, n_cols):
    best = 0
    keys = sorted(weights)
    for r in range(len(keys) + 1):
        for combo in itertools.combinations(keys, r):
            if all(a[0] < b[0] and a[1] < b[1] for a, b in zip(combo, combo[1:])):
                best = max(best, sum(weights[k] for k in combo))
    return best


@pytest.mark.parametrize("seed", range(30))
def test_heaviest_chain_matches_brute_force(seed):
    rng = random.Random(seed)
    n_rows, n_cols = rng.randint(1, 6), rng.randint(1, 6)
    cells = [(i, j) for i in range(n_rows) for j in range(n_cols)]
    weights = {c: rng.randint(1, 20) for c in rng.sample(cells, rng.randint(0, min(10, len(cells))))}
    chain = heaviest_chain(weights, n_cols)
    assert all(a[0] < b[0] and a[1] < b[1] for a, b in zip(chain, chain[1:]))
    assert sum(weights[c] for c in chain) == _brute(weights, n_rows, n_cols)
