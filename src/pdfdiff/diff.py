"""Sequence diffing.

``diff_sequences`` produces SequenceMatcher-style opcodes. Small problems go
straight to ``difflib.SequenceMatcher``. Large ones are first split on
patience-diff anchors (elements unique in both inputs), which keeps it fast
even on big documents.

``diff_tokens`` diffs two token streams in two passes: first block by block,
then word by word inside the blocks that differ.
"""

from __future__ import annotations

from bisect import bisect_left
from collections import Counter
from difflib import SequenceMatcher
from typing import Hashable, Sequence

from .model import BBox, Change, Token, Word

Opcode = tuple[str, int, int, int, int]

# Above this n*m product, split on patience anchors before using SequenceMatcher.
_DIRECT_LIMIT = 250_000
_CONTEXT_TOKENS = 6
# Matched tokens this short are checked for a plausible position (see _misplaced).
_SHORT_TOKEN = 3
_GEOMETRY_PASSES = 3
_MAX_SINGLE_TRIES = 8


def diff_sequences(a: Sequence[Hashable], b: Sequence[Hashable]) -> list[Opcode]:
    out: list[Opcode] = []
    _diff(a, b, 0, len(a), 0, len(b), out)
    return _merge(out)


def _diff(a, b, alo, ahi, blo, bhi, out: list[Opcode]) -> None:
    p = 0
    while alo + p < ahi and blo + p < bhi and a[alo + p] == b[blo + p]:
        p += 1
    if p:
        out.append(("equal", alo, alo + p, blo, blo + p))
        alo += p
        blo += p
    s = 0
    while alo < ahi - s and blo < bhi - s and a[ahi - 1 - s] == b[bhi - 1 - s]:
        s += 1
    _diff_middle(a, b, alo, ahi - s, blo, bhi - s, out)
    if s:
        out.append(("equal", ahi - s, ahi, bhi - s, bhi))


def _diff_middle(a, b, alo, ahi, blo, bhi, out: list[Opcode]) -> None:
    n, m = ahi - alo, bhi - blo
    if n == 0 and m == 0:
        return
    if n == 0:
        out.append(("insert", alo, alo, blo, bhi))
        return
    if m == 0:
        out.append(("delete", alo, ahi, blo, blo))
        return
    if n * m > _DIRECT_LIMIT:
        anchors = _patience_anchors(a, alo, ahi, b, blo, bhi)
        if anchors:
            pa, pb = alo, blo
            for i, j in anchors:
                _diff(a, b, pa, i, pb, j, out)
                out.append(("equal", i, i + 1, j, j + 1))
                pa, pb = i + 1, j + 1
            _diff(a, b, pa, ahi, pb, bhi, out)
            return
    sm = SequenceMatcher(None, a[alo:ahi], b[blo:bhi], autojunk=n * m > _DIRECT_LIMIT)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        out.append((tag, i1 + alo, i2 + alo, j1 + blo, j2 + blo))


def _patience_anchors(a, alo, ahi, b, blo, bhi) -> list[tuple[int, int]]:
    """Longest increasing run of elements that occur exactly once in both ranges."""
    ca = Counter(a[alo:ahi])
    cb = Counter(b[blo:bhi])
    b_pos = {b[j]: j for j in range(blo, bhi) if cb[b[j]] == 1 and ca[b[j]] == 1}
    pairs = [(i, b_pos[a[i]]) for i in range(alo, ahi) if a[i] in b_pos]
    if not pairs:
        return []
    # Patience sort / LIS on the b positions.
    tails: list[int] = []
    tail_idx: list[int] = []
    prev = [-1] * len(pairs)
    for k, (_, j) in enumerate(pairs):
        pos = bisect_left(tails, j)
        if pos == len(tails):
            tails.append(j)
            tail_idx.append(k)
        else:
            tails[pos] = j
            tail_idx[pos] = k
        prev[k] = tail_idx[pos - 1] if pos else -1
    result = []
    k = tail_idx[-1]
    while k != -1:
        result.append(pairs[k])
        k = prev[k]
    result.reverse()
    return result


def _merge(ops: list[Opcode]) -> list[Opcode]:
    """Join adjacent equal ops and adjacent non-equal ops."""
    merged: list[Opcode] = []
    for op in ops:
        tag, i1, i2, j1, j2 = op
        if i1 == i2 and j1 == j2:
            continue
        if merged:
            ptag, pi1, pi2, pj1, pj2 = merged[-1]
            if (ptag == "equal") == (tag == "equal"):
                ni1, ni2, nj1, nj2 = pi1, i2, pj1, j2
                if tag == "equal":
                    ntag = "equal"
                else:
                    ntag = _kind(ni2 - ni1, nj2 - nj1)
                merged[-1] = (ntag, ni1, ni2, nj1, nj2)
                continue
        if tag != "equal":
            tag = _kind(i2 - i1, j2 - j1)
        merged.append((tag, i1, i2, j1, j2))
    return merged


def cleanup_semantic(ops: list[Opcode]) -> list[Opcode]:
    """Absorb short coincidental matches (e.g. a lone "the") between two edits.

    An equal run is absorbed when it is at most half as long as the larger side of
    the edits on each side of it. So a stray word between two rewritten phrases is
    absorbed, but an unchanged sentence next to a big edit is kept.
    """
    out: list[Opcode] = []
    for op in _merge(ops):
        out.append(op)
        while (
            len(out) >= 3
            and out[-1][0] != "equal"
            and out[-2][0] == "equal"
            and out[-3][0] != "equal"
            and 2 * (out[-2][2] - out[-2][1]) <= min(_size(out[-3]), _size(out[-1]))
        ):
            prev, _, nxt = out[-3:]
            ni1, ni2, nj1, nj2 = prev[1], nxt[2], prev[3], nxt[4]
            out[-3:] = [(_kind(ni2 - ni1, nj2 - nj1), ni1, ni2, nj1, nj2)]
    return out


def _size(op: Opcode) -> int:
    return max(op[2] - op[1], op[4] - op[3])


def _kind(n_old: int, n_new: int) -> str:
    if n_old and n_new:
        return "replace"
    return "delete" if n_old else "insert"


def _block_ranges(tokens: list[Token]) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    start = 0
    for i in range(1, len(tokens) + 1):
        if i == len(tokens) or tokens[i].block_key != tokens[start].block_key:
            ranges.append((start, i))
            start = i
    return ranges if tokens else []


def token_opcodes(old: list[Token], new: list[Token]) -> list[Opcode]:
    """Two-pass diff: block level first, then word level inside changed regions."""
    ob = _block_ranges(old)
    nb = _block_ranges(new)
    okeys = ["\x00".join(t.norm for t in old[s:e]) for s, e in ob]
    nkeys = ["\x00".join(t.norm for t in new[s:e]) for s, e in nb]
    onorm = [t.norm for t in old]
    nnorm = [t.norm for t in new]

    ops: list[Opcode] = []
    for tag, b1, b2, c1, c2 in diff_sequences(okeys, nkeys):
        i1 = ob[b1][0] if b1 < len(ob) else len(old)
        i2 = ob[b2 - 1][1] if b2 > b1 else i1
        j1 = nb[c1][0] if c1 < len(nb) else len(new)
        j2 = nb[c2 - 1][1] if c2 > c1 else j1
        if tag == "equal":
            ops.append(("equal", i1, i2, j1, j2))
        else:
            for wt, wi1, wi2, wj1, wj2 in _word_diff(old, new, onorm, nnorm, i1, i2, j1, j2):
                ops.append((wt, wi1 + i1, wi2 + i1, wj1 + j1, wj2 + j1))
    return cleanup_semantic(ops)


def _word_diff(old, new, onorm, nnorm, i1, i2, j1, j2) -> list[Opcode]:
    """Word-level diff of one changed region, using positions to break ties.

    Equal-cost alignments are common with short repeated text: old "X Yes No" vs new
    "Yes X No" can keep either the X or the "Yes". When a short token is matched to a
    copy at a position that disagrees with its neighbours' matches (such as a checkbox
    mark that moved to another box), the match is forbidden and the region re-diffed.
    The new alignment is kept only if it is no more costly, so text that merely
    re-wrapped is unaffected.
    """
    a, b = onorm[i1:i2], nnorm[j1:j2]
    wops = diff_sequences(a, b)
    if len(a) * len(b) > _DIRECT_LIMIT:  # large rewrites: not worth re-diffing
        return wops
    for _ in range(_GEOMETRY_PASSES):
        bad = _misplaced(old, new, wops, i1, j1)
        if not bad:
            break
        cost = _cost(wops)
        tries = [bad] + ([[p] for p in bad] if 1 < len(bad) <= _MAX_SINGLE_TRIES else [])
        improved = False
        for pairs in tries:
            ta, tb = list(a), list(b)
            for i, j in pairs:
                ta[i - i1] = f"\x01{i}"  # unique keys can't match anything
                tb[j - j1] = f"\x02{j}"
            tops = diff_sequences(ta, tb)
            if _cost(tops) <= cost:
                a, b, wops, improved = ta, tb, tops, True
                break
        if not improved:
            break
    return wops


def _cost(ops: list[Opcode]) -> int:
    return sum(i2 - i1 + j2 - j1 for tag, i1, i2, j1, j2 in ops if tag != "equal")


def _misplaced(old, new, wops: list[Opcode], i1: int, j1: int) -> list[tuple[int, int]]:
    """Matched pairs of short tokens whose displacement (new position minus old) agrees
    with none of the reference matches on the same pages: the neighbouring matches and
    the nearest matches of longer tokens on either side."""
    pairs = [(i + i1, j + j1) for tag, a1, a2, b1, b2 in wops if tag == "equal" for i, j in zip(range(a1, a2), range(b1, b2))]
    if len(pairs) < 2:
        return []
    is_long = [len(old[i].norm) > _SHORT_TOKEN for i, _ in pairs]
    prev_long, next_long = [-1] * len(pairs), [-1] * len(pairs)
    last = -1
    for k in range(len(pairs)):
        prev_long[k] = last
        if is_long[k]:
            last = k
    last = -1
    for k in range(len(pairs) - 1, -1, -1):
        next_long[k] = last
        if is_long[k]:
            last = k

    def shift(k):
        i, j = pairs[k]
        wo, wn = old[i].words[0], new[j].words[0]
        return (wo.page, wn.page), wn.bbox[0] - wo.bbox[0], wn.bbox[1] - wo.bbox[1], wo.bbox[3] - wo.bbox[1]

    bad = []
    for k, (i, j) in enumerate(pairs):
        if is_long[k]:
            continue
        pages, dx, dy, h = shift(k)
        tol = max(h, 6.0)
        seen = False
        for nk in {k - 1, k + 1, prev_long[k], next_long[k]} - {-1}:
            if 0 <= nk < len(pairs):
                npages, ndx, ndy, _ = shift(nk)
                if npages != pages:
                    continue
                seen = True
                if abs(dx - ndx) <= tol and abs(dy - ndy) <= tol:
                    break
        else:
            if seen:
                bad.append((i, j))
    return bad


def diff_tokens(
    old: list[Token],
    new: list[Token],
    ops: list[Opcode] | None = None,
    skip_old_pages: frozenset[int] = frozenset(),
    skip_new_pages: frozenset[int] = frozenset(),
) -> list[Change]:
    """Build text changes from token opcodes.

    Tokens on ``skip_*_pages`` (whole pages reported as inserted/deleted) are
    left out so they are not reported twice.
    """
    if ops is None:
        ops = token_opcodes(old, new)
    changes: list[Change] = []
    for k, (tag, i1, i2, j1, j2) in enumerate(ops):
        if tag == "equal":
            continue
        before = ""
        if k > 0 and ops[k - 1][0] == "equal":
            _, _, _, pj1, pj2 = ops[k - 1]
            before = " ".join(t.text for t in new[max(pj1, pj2 - _CONTEXT_TOKENS):pj2])
        after = ""
        if k + 1 < len(ops) and ops[k + 1][0] == "equal":
            _, _, _, nj1, nj2 = ops[k + 1]
            after = " ".join(t.text for t in new[nj1:min(nj2, nj1 + _CONTEXT_TOKENS)])

        old_runs = _kept_runs(old, i1, i2, skip_old_pages)
        new_runs = _kept_runs(new, j1, j2, skip_new_pages)
        if old_runs and new_runs:
            pieces = [
                (
                    [t for s, e in old_runs for t in old[s:e]],
                    [t for s, e in new_runs for t in new[s:e]],
                )
            ]
        elif old_runs:
            pieces = [(old[s:e], []) for s, e in old_runs]
        elif new_runs:
            pieces = [([], new[s:e]) for s, e in new_runs]
        else:
            continue

        old_next = old[i2].words[0] if i2 < len(old) else None
        new_next = new[j2].words[0] if j2 < len(new) else None
        for n, (otoks, ntoks) in enumerate(pieces):
            old_anchor, old_after, old_x = (
                (None, True, None) if otoks
                else _change_point(old, i1, i2, skip_old_pages, ntoks, new_next)
            )
            new_anchor, new_after, new_x = (
                (None, True, None) if ntoks
                else _change_point(new, j1, j2, skip_new_pages, otoks, old_next)
            )
            changes.append(
                Change(
                    id=len(changes) + 1,
                    kind=_kind(len(otoks), len(ntoks)),  # type: ignore[arg-type]
                    old_tokens=otoks,
                    new_tokens=ntoks,
                    old_anchor=old_anchor,
                    new_anchor=new_anchor,
                    old_anchor_after=old_after,
                    new_anchor_after=new_after,
                    old_caret_x=old_x,
                    new_caret_x=new_x,
                    context_before=before if n == 0 else "",
                    context_after=after if n == len(pieces) - 1 else "",
                )
            )
    return changes


def _kept_runs(
    tokens: list[Token], lo: int, hi: int, skip_pages: frozenset[int]
) -> list[tuple[int, int]]:
    """Contiguous index runs in [lo, hi) whose tokens are not on skipped pages."""
    if not skip_pages:
        return [(lo, hi)] if hi > lo else []
    runs: list[tuple[int, int]] = []
    start = None
    for i in range(lo, hi):
        if tokens[i].page in skip_pages:
            if start is not None:
                runs.append((start, i))
                start = None
        elif start is None:
            start = i
    if start is not None:
        runs.append((start, hi))
    return runs


def _same_row(a: BBox, b: BBox) -> bool:
    ha, hb = a[3] - a[1], b[3] - b[1]
    return ha > 0 and hb > 0 and min(a[3], b[3]) - max(a[1], b[1]) >= 0.5 * min(ha, hb)


def _change_point(
    tokens: list[Token], lo: int, hi: int, skip_pages: frozenset[int],
    other: list[Token], following: Word | None,
) -> tuple[Word | None, bool, float | None]:
    """Keep short leading fields on their label's row without re-anchoring prose."""
    if (
        len(other) == 1 and len(other[0].norm) <= _SHORT_TOKEN
        and following is not None and hi < len(tokens)
    ):
        first = other[0].words[0]
        nxt = tokens[hi].words[0]
        prev = tokens[lo - 1].words[-1] if lo else None
        if (
            nxt.page not in skip_pages
            and first.page == following.page
            and _same_row(first.bbox, following.bbox)
            and first.bbox[2] <= following.bbox[0] + 1.0
            and (prev is None or prev.page != nxt.page or not _same_row(prev.bbox, nxt.bbox))
        ):
            # Keep the source x only when the following word did not shift horizontally.
            x = first.bbox[0] if abs(nxt.bbox[0] - following.bbox[0]) <= 1.0 else None
            return nxt, False, x
    anchor, after = _anchor(tokens, lo, hi, skip_pages)
    return anchor, after, _caret_x(tokens, lo, hi, skip_pages, other)


def _caret_x(
    tokens: list[Token], lo: int, hi: int, skip_pages: frozenset[int], other: list[Token]
) -> float | None:
    """x position for a caret between tokens[lo-1] and tokens[hi] when the text on the
    other side sits in the gap between them (same row, same layout), e.g. a checkbox
    mark or a form field value. None means: use the anchor word's edge."""
    if not other or lo == 0 or hi >= len(tokens):
        return None
    prev, nxt = tokens[lo - 1].words[-1], tokens[hi].words[0]
    if prev.page != nxt.page or prev.page in skip_pages or not _same_row(prev.bbox, nxt.bbox):
        return None
    w = other[0].words[0]
    tol = 1.0
    if _same_row(w.bbox, prev.bbox) and prev.bbox[2] - tol <= w.bbox[0] and w.bbox[2] <= nxt.bbox[0] + tol:
        return w.bbox[0]
    return None


def _anchor(
    tokens: list[Token], lo: int, hi: int, skip_pages: frozenset[int] = frozenset()
) -> tuple[Word | None, bool]:
    """Word next to a change point: (preceding word, True), else (following word, False)."""
    for i in range(lo - 1, -1, -1):
        if tokens[i].page not in skip_pages:
            return tokens[i].words[-1], True
    for i in range(hi, len(tokens)):
        if tokens[i].page not in skip_pages:
            return tokens[i].words[0], False
    return None, True
