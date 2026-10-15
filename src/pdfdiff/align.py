"""Page-level analysis: inserted/deleted page detection and the old<->new page map.

This runs *after* the whole-document token diff, so text that reflows across
page boundaries never causes a false page insertion:

* A non-blank page is a candidate for insertion/deletion when at most
  ``page_threshold`` of its tokens are matched (``equal``) in the other document.
* The page map is the monotone page pairing that maximizes the number of shared
  matched tokens (heaviest common subsequence over a sparse weight matrix).
* Between two mapped pages, leftover candidate and blank pages are paired in order
  as rewritten pages, and their text is diffed. Only the surplus on one side is
  reported as inserted/deleted.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from .diff import Opcode
from .model import Document, PageChange, PageMap, Token

_PREVIEW_WORDS = 12


@dataclass
class PageAnalysis:
    page_changes: list[PageChange]
    page_map: PageMap
    skip_old: frozenset[int]
    skip_new: frozenset[int]


def analyze_pages(
    old_doc: Document,
    new_doc: Document,
    old: list[Token],
    new: list[Token],
    ops: list[Opcode],
    threshold: float,
) -> PageAnalysis:
    n_old, n_new = old_doc.page_count, new_doc.page_count
    old_total = Counter(t.page for t in old)
    new_total = Counter(t.page for t in new)
    old_matched: Counter[int] = Counter()
    new_matched: Counter[int] = Counter()
    shared: Counter[tuple[int, int]] = Counter()
    for tag, i1, i2, j1, j2 in ops:
        if tag != "equal":
            continue
        for a, b in zip(old[i1:i2], new[j1:j2]):
            old_matched[a.page] += 1
            new_matched[b.page] += 1
            shared[(a.page, b.page)] += 1

    deleted = {
        p for p in range(n_old) if old_total[p] and old_matched[p] <= threshold * old_total[p]
    }
    inserted = {
        p for p in range(n_new) if new_total[p] and new_matched[p] <= threshold * new_total[p]
    }

    weights = {k: w for k, w in shared.items() if k[0] not in deleted and k[1] not in inserted}
    anchors = heaviest_chain(weights, n_new)

    old_blank = {p for p in range(n_old) if not old_total[p]}
    new_blank = {p for p in range(n_new) if not new_total[p]}
    page_map: PageMap = []
    po, pn = -1, -1
    for o, n in anchors + [(n_old, n_new)]:
        gap_old = list(range(po + 1, o))
        gap_new = list(range(pn + 1, n))
        # Pages with no real counterpart (blank or mostly unmatched) between two
        # mapped pages are paired in order, as rewritten pages whose text is diffed.
        # Only the surplus on one side counts as inserted/deleted.
        uo = [p for p in gap_old if p in deleted or p in old_blank]
        un = [p for p in gap_new if p in inserted or p in new_blank]
        pairs = list(zip(uo, un))
        for a, b in pairs:
            deleted.discard(a)
            inserted.discard(b)
        deleted.update(uo[len(pairs):])
        inserted.update(un[len(pairs):])
        _emit_gap(page_map, gap_old, gap_new, pairs)
        if o < n_old and n < n_new:
            page_map.append((o, n))
        po, pn = o, n

    page_changes: list[PageChange] = []
    last_old, last_new = -1, -1
    for o, n in page_map:
        if n is None and o in deleted:
            page_changes.append(_page_change(len(page_changes) + 1, "page_deleted", old_doc, o, last_new))
        elif o is None and n in inserted:
            page_changes.append(_page_change(len(page_changes) + 1, "page_inserted", new_doc, n, last_old))
        if o is not None:
            last_old = o
        if n is not None:
            last_new = n

    return PageAnalysis(
        page_changes=page_changes,
        page_map=page_map,
        skip_old=frozenset(deleted),
        skip_new=frozenset(inserted),
    )


def _emit_gap(out: PageMap, gap_old: list[int], gap_new: list[int], pairs) -> None:
    """Emit unmatched pages of a gap in order, interleaved with blank-page pairs."""
    oi = ni = 0
    for bo, bn in pairs + [(None, None)]:
        while oi < len(gap_old) and (bo is None or gap_old[oi] < bo):
            out.append((gap_old[oi], None))
            oi += 1
        while ni < len(gap_new) and (bn is None or gap_new[ni] < bn):
            out.append((None, gap_new[ni]))
            ni += 1
        if bo is not None:
            out.append((bo, bn))
            oi += 1
            ni += 1


def _page_change(id_: int, kind, doc: Document, page: int, after: int) -> PageChange:
    words = doc.pages[page].words
    preview = " ".join(w.text for w in words[:_PREVIEW_WORDS])
    if len(words) > _PREVIEW_WORDS:
        preview += " …"
    return PageChange(
        id=id_, kind=kind, page=page, after=after, word_count=len(words), preview=preview
    )


def heaviest_chain(weights: dict[tuple[int, int], int], n_cols: int) -> list[tuple[int, int]]:
    """Max-weight chain of (row, col) pairs strictly increasing in both coordinates.

    Sparse dynamic programming with a Fenwick tree for prefix maxima:
    O(K log n_cols) for K pairs.
    """
    if not weights:
        return []
    items = sorted(weights.items(), key=lambda kv: (kv[0][0], -kv[0][1]))
    tree_val = [0] * (n_cols + 1)
    tree_idx = [-1] * (n_cols + 1)
    best: list[int] = []
    prev: list[int] = []
    for k, ((_, col), w) in enumerate(items):
        # Prefix max over columns < col. Pairs in the same row are processed in
        # descending column order, so they never chain onto each other.
        v, p, j = 0, -1, col
        while j > 0:
            if tree_val[j] > v:
                v, p = tree_val[j], tree_idx[j]
            j -= j & -j
        best.append(v + w)
        prev.append(p)
        j = col + 1
        while j <= n_cols:
            if v + w > tree_val[j]:
                tree_val[j], tree_idx[j] = v + w, k
            j += j & -j
    # On ties, prefer the chain ending at the earliest page (keeps maps of reflowed
    # documents anchored at the start).
    k = max(range(len(items)), key=lambda i: (best[i], -items[i][0][1], -items[i][0][0]))
    chain = []
    while k != -1:
        chain.append(items[k][0])
        k = prev[k]
    chain.reverse()
    return chain
