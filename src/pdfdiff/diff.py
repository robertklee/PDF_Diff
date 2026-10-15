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

from .model import Change, Token

Opcode = tuple[str, int, int, int, int]

# Above this n*m product, split on patience anchors before using SequenceMatcher.
_DIRECT_LIMIT = 250_000
_CONTEXT_TOKENS = 6


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

    An equal run is absorbed when it is no longer than the larger side of the
    edit on each side of it.
    """
    out: list[Opcode] = []
    for op in _merge(ops):
        out.append(op)
        while (
            len(out) >= 3
            and out[-1][0] != "equal"
            and out[-2][0] == "equal"
            and out[-3][0] != "equal"
            and out[-2][2] - out[-2][1] <= min(_size(out[-3]), _size(out[-1]))
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
            for wt, wi1, wi2, wj1, wj2 in diff_sequences(onorm[i1:i2], nnorm[j1:j2]):
                ops.append((wt, wi1 + i1, wi2 + i1, wj1 + j1, wj2 + j1))
    return cleanup_semantic(ops)


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

        for n, (otoks, ntoks) in enumerate(pieces):
            changes.append(
                Change(
                    id=len(changes) + 1,
                    kind=_kind(len(otoks), len(ntoks)),  # type: ignore[arg-type]
                    old_tokens=otoks,
                    new_tokens=ntoks,
                    old_anchor=None if otoks else _anchor(old, i1, i2, skip_old_pages),
                    new_anchor=None if ntoks else _anchor(new, j1, j2, skip_new_pages),
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


def _anchor(tokens: list[Token], lo: int, hi: int, skip_pages: frozenset[int] = frozenset()):
    """Word next to a change point: the nearest preceding word, else the following one."""
    for i in range(lo - 1, -1, -1):
        if tokens[i].page not in skip_pages:
            return tokens[i].words[-1]
    for i in range(hi, len(tokens)):
        if tokens[i].page not in skip_pages:
            return tokens[i].words[0]
    return None
