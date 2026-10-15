"""Self-contained side-by-side HTML report.

Every page is rendered to a PNG and embedded, so the report is a single file that
works offline. A Content-Security-Policy forbids loading anything else. Identical
page images (unchanged pages usually render identically in both documents) are
stored once and loaded lazily by a small inline script. Old and new pages that correspond (per the page map) share a row, so the two
columns always scroll together. Change marks are absolutely positioned overlays in
percent of the displayed page, so they stay aligned at any zoom level.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from collections import defaultdict
from html import escape

import pymupdf

from .. import parallel
from ..extract import quiet_mupdf
from ..model import Change, DiffResult, Document, PageChange
from .pdf import COLORS, LABELS, Mark, _open, _pages, collect_marks
from .term import summary_line

DEFAULT_DPI = 96
MAX_PIXELS = 2400  # cap on the longer side of a rendered page image
MAX_TEXT = 400
_MIN_CHUNK = 10  # pages per rendering task
PARALLEL_MIN_PAGES = 40


def write(result: DiffResult, out: str, dpi: int = DEFAULT_DPI, jobs: int | None = None) -> None:
    """Write the report. ``jobs`` limits the worker processes used to render page
    images (None = automatic, 1 = none)."""
    report = render(result, dpi, jobs)
    with open(out, "w", encoding="utf-8") as f:
        f.write(report)


def render(result: DiffResult, dpi: int = DEFAULT_DPI, jobs: int | None = None) -> str:
    pngs = _render_all([result.old, result.new], dpi, jobs)
    pngs = {"old": pngs[0], "new": pngs[1]}
    marks = {"old": collect_marks(result, "old"), "new": collect_marks(result, "new")}
    gone = {
        "old": {pc.page: pc for pc in result.page_changes if pc.kind == "page_deleted"},
        "new": {pc.page: pc for pc in result.page_changes if pc.kind == "page_inserted"},
    }
    # The first mark of each change gets an element id so the sidebar can link to it.
    first_mark: dict[int, str] = {}
    images = _Images()

    rows: list[str] = []
    with _open(result.old) as old_doc, _open(result.new) as new_doc:
        docs = {"old": old_doc, "new": new_doc}
        for r, (o, n) in enumerate(result.page_map):
            pnos = {"old": o, "new": n}
            sizes = {s: docs[s][p].rect for s, p in pnos.items() if p is not None}
            max_w = max(rect.width for rect in sizes.values())
            cells = []
            for side in ("new", "old"):  # new first so links prefer the new side
                pno = pnos[side]
                if pno is None:
                    other = "old" if side == "new" else "new"
                    cells.append((side, _placeholder(side, pnos[other], gone[other], sizes[other], max_w)))
                    continue
                page = docs[side][pno]
                img = images.add(pngs[side][pno])
                cells.append(
                    (side, _page_cell(side, page, img, marks[side].get(pno, []), gone[side].get(pno), max_w, first_mark))
                )
            body = dict(cells)
            rows.append(f'<section class="row" id="row-{r}">{body["old"]}{body["new"]}</section>')

    items = _sidebar_items(result, first_mark)
    return _PAGE.format(
        title=escape(f"{os.path.basename(result.old.path)} vs {os.path.basename(result.new.path)}"),
        old_name=escape(result.old.path),
        new_name=escape(result.new.path),
        old_pages=result.old.page_count,
        new_pages=result.new.page_count,
        summary=escape(summary_line(result)),
        warnings="".join(f'<p class="warn">{escape(w)}</p>' for w in result.warnings),
        filters=_filters(result),
        items="\n".join(items) or '<li class="none">No differences found.</li>',
        rows="\n".join(rows),
        images=images.json(),
        css=_CSS % _color_vars(),
        js=_JS,
    )


# ----------------------------------------------------------------------- page cells


class _Images:
    """Deduplicated page images, referenced by index."""

    def __init__(self) -> None:
        self._index: dict[bytes, int] = {}
        self._uris: list[str] = []

    def add(self, png: bytes) -> int:
        key = hashlib.sha256(png).digest()
        if key not in self._index:
            self._index[key] = len(self._uris)
            self._uris.append("data:image/png;base64," + base64.b64encode(png).decode("ascii"))
        return self._index[key]

    def __len__(self) -> int:
        return len(self._uris)

    def json(self) -> str:
        # Base64 data URIs contain no "<", so this is safe inside a <script> element.
        return json.dumps(self._uris)


def _render_all(docs: list[Document], dpi: int, jobs: int | None) -> list[list[bytes]]:
    """PNG images of every page of each document, rendered in parallel when worthwhile."""
    total = sum(d.page_count for d in docs)
    workers = parallel.worker_count(jobs, total, _MIN_CHUNK)
    if workers > 1 and total >= PARALLEL_MIN_PAGES:
        tasks = [
            (i, (d.path, d.password, start, stop, dpi))
            for i, d in enumerate(docs)
            for start, stop in parallel.chunks(d.page_count, workers, _MIN_CHUNK)
        ]
        parts = parallel.run(_render_range, [t for _, t in tasks], workers)
        if parts is not None:
            out: list[list[bytes]] = [[] for _ in docs]
            for (i, _), part in zip(tasks, parts):
                out[i].extend(part)
            return out
    return [_render_range(d.path, d.password, 0, d.page_count, dpi) for d in docs]


def _render_range(path: str, password: str | None, start: int, stop: int, dpi: int) -> list[bytes]:
    quiet_mupdf()
    with pymupdf.open(path, filetype="pdf") as doc:
        if doc.needs_pass:
            doc.authenticate(password or "")
        return [_render_png(doc[pno], dpi) for pno in range(start, stop)]


def _render_png(page: pymupdf.Page, dpi: int) -> bytes:
    rect = page.rect  # displayed (rotated) size
    zoom = min(dpi / 72, MAX_PIXELS / max(rect.width, rect.height, 1))
    try:
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False, annots=False)
    except Exception:  # damaged page: show it blank rather than fail the report
        size = (rect * pymupdf.Matrix(zoom, zoom)).irect
        pix = pymupdf.Pixmap(pymupdf.csGRAY, pymupdf.IRect(0, 0, max(size.width, 1), max(size.height, 1)), False)
        pix.clear_with(255)
        return pix.tobytes("png")
    s = pix.samples
    if s[0::3] == s[1::3] == s[2::3]:  # no color on the page: a gray PNG is much smaller
        pix = pymupdf.Pixmap(pymupdf.csGRAY, pix)
    return pix.tobytes("png")


def _page_cell(side, page: pymupdf.Page, img: int, marks: list[Mark], pc: PageChange | None, max_w, first_mark) -> str:
    rect = page.rect  # displayed (rotated) size

    rot = page.rotation_matrix
    overlays = []
    for mk in marks:
        if mk.rects:
            boxes = [pymupdf.Rect(b) * rot for b in mk.rects]
            cls = "hl"
        else:
            x, top, bottom = mk.caret
            boxes = [pymupdf.Rect(x - 1, top, x + 1, bottom) * rot]
            cls = "caret"
        for b in boxes:
            el_id = ""
            key = mk.change_id
            if key not in first_mark:
                first_mark[key] = f"c{key}"
                el_id = f' id="c{key}"'
            overlays.append(
                f'<a{el_id} class="mark {cls} {mk.kind}" href="#item-{key}" data-id="{key}" '
                f'title="{escape(mk.note)}" style="{_pos(b, rect)}"></a>'
            )

    label = "OLD" if side == "old" else "NEW"
    banner = ""
    cls = ""
    if pc is not None:
        cls = " gone " + pc.kind
        banner = f'<span class="badge {pc.kind}">{"INSERTED PAGE" if pc.kind == "page_inserted" else "DELETED PAGE"}</span>'
    pid = f' id="p{pc.id}"' if pc is not None else ""
    width = 100 * rect.width / max_w
    return (
        f'<div class="cell {side}">'
        f'<div class="hdr">{label} page {page.number + 1}{banner}</div>'
        f'<div class="page{cls}"{pid} style="width:{width:.2f}%;aspect-ratio:{rect.width:.2f}/{rect.height:.2f}">'
        f'<img alt="{label} page {page.number + 1}" data-img="{img}">'
        + "".join(overlays)
        + "</div></div>"
    )


def _placeholder(side, other_pno, other_gone, other_rect, max_w) -> str:
    label = "OLD" if side == "old" else "NEW"
    if other_pno in other_gone:
        msg = "No page: deleted from old" if side == "new" else "No page: inserted in new"
    else:
        msg = "No matching page (content reflowed)"
    width = 100 * other_rect.width / max_w
    return (
        f'<div class="cell {side}"><div class="hdr">{label} &mdash;</div>'
        f'<div class="page missing" style="width:{width:.2f}%;aspect-ratio:{other_rect.width:.2f}/{other_rect.height:.2f}">'
        f"<span>{msg}</span></div></div>"
    )


def _pos(b: pymupdf.Rect, page_rect: pymupdf.Rect) -> str:
    w, h = page_rect.width or 1, page_rect.height or 1
    b = pymupdf.Rect(b).normalize()
    return (
        f"left:{100 * b.x0 / w:.3f}%;top:{100 * b.y0 / h:.3f}%;"
        f"width:{100 * b.width / w:.3f}%;height:{100 * b.height / h:.3f}%"
    )


# -------------------------------------------------------------------------- sidebar


def _sidebar_items(result: DiffResult, first_mark: dict[int, str]) -> list[str]:
    row_of = defaultdict(dict)
    for r, (o, n) in enumerate(result.page_map):
        if o is not None:
            row_of["old"][o] = r
        if n is not None:
            row_of["new"][n] = r

    entries: list[tuple[tuple[int, int, int], str]] = []
    for pc in result.page_changes:
        side = "new" if pc.kind == "page_inserted" else "old"
        r = row_of[side][pc.page]
        where = f"{'new' if side == 'new' else 'old'} p.{pc.page + 1}"
        title = "Page inserted" if pc.kind == "page_inserted" else "Page deleted"
        body = escape(_trim(pc.preview)) if pc.preview else "<em>blank page</em>"
        entries.append(
            (
                (r, 0, pc.id),
                f'<li class="item {pc.kind}" id="item-P{pc.id}" data-target="p{pc.id}">'
                f'<a href="#p{pc.id}"><b>P{pc.id}</b> {title} <span class="loc">{where} &middot; {pc.word_count} words</span>'
                f'<span class="txt">{body}</span></a></li>',
            )
        )
    for ch in result.changes:
        if ch.new_pages and ch.new_pages[0] in row_of["new"]:
            r = row_of["new"][ch.new_pages[0]]
        else:
            r = row_of["old"].get(ch.old_pages[0], 0) if ch.old_pages else 0
        target = first_mark.get(ch.id, "")
        entries.append(
            (
                (r, 1, ch.id),
                f'<li class="item {ch.kind}" id="item-{ch.id}" data-id="{ch.id}" data-target="{target}">'
                f'<a href="#{target}"><b>#{ch.id}</b> {LABELS[ch.kind]} '
                f'<span class="loc">old p.{_pages(ch.old_pages)} &middot; new p.{_pages(ch.new_pages)}</span>'
                f'<span class="txt">{_inline(ch)}</span></a></li>',
            )
        )
    entries.sort(key=lambda e: e[0])
    return [html for _, html in entries]


def _inline(ch: Change) -> str:
    parts = []
    if ch.context_before:
        parts.append(f'<span class="ctx">&hellip;{escape(ch.context_before)}</span>')
    if ch.old_tokens:
        parts.append(f"<del>{escape(_trim(ch.old_text))}</del>")
    if ch.new_tokens:
        parts.append(f"<ins>{escape(_trim(ch.new_text))}</ins>")
    if ch.context_after:
        parts.append(f'<span class="ctx">{escape(ch.context_after)}&hellip;</span>')
    return " ".join(parts)


def _filters(result: DiffResult) -> str:
    counts = result.counts()
    out = []
    for key, label in (
        ("replace", "Replaced"),
        ("insert", "Inserted"),
        ("delete", "Deleted"),
        ("page_inserted", "Pages inserted"),
        ("page_deleted", "Pages deleted"),
    ):
        if counts[key]:
            out.append(
                f'<label class="{key}"><input type="checkbox" data-kind="{key}" checked> '
                f"{label} ({counts[key]})</label>"
            )
    return "".join(out)


def _trim(s: str) -> str:
    return s if len(s) <= MAX_TEXT else s[: MAX_TEXT - 1] + "…"


def _color_vars() -> str:
    def rgb(c):
        return ",".join(str(round(v * 255)) for v in c)

    return (
        f"--ins:{rgb(COLORS['insert'])};--del:{rgb(COLORS['delete'])};--rep:{rgb(COLORS['replace'])};"
    )


# ------------------------------------------------------------------------ templates

_CSS = """
:root{%s--side:340px;--zoom:1}
*{box-sizing:border-box}
html,body{margin:0;height:100%%;font:13px/1.4 system-ui,-apple-system,"Segoe UI",sans-serif;color:#222;background:#e9eaed}
header{position:fixed;top:0;left:0;right:0;height:64px;padding:8px 14px;background:#fff;border-bottom:1px solid #ccc;z-index:3;display:flex;gap:18px;align-items:center}
header h1{font-size:15px;margin:0}
header .files{font-size:11px;color:#555;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;max-width:45vw}
header .files div{overflow:hidden;text-overflow:ellipsis}
header .sum{font-weight:600}
header .tools{margin-left:auto;display:flex;gap:6px;align-items:center}
button{font:inherit;padding:3px 9px;border:1px solid #bbb;background:#f7f7f7;border-radius:4px;cursor:pointer}
button:hover{background:#eee}
aside{position:fixed;top:64px;bottom:0;left:0;width:var(--side);background:#fafafa;border-right:1px solid #ccc;display:flex;flex-direction:column;z-index:2}
aside .filters{padding:8px 10px;border-bottom:1px solid #ddd;display:flex;flex-wrap:wrap;gap:4px 12px;font-size:12px}
aside .warn{margin:0;padding:6px 10px;background:#fff4d6;font-size:12px;border-bottom:1px solid #e8d9a8}
aside ol{list-style:none;margin:0;padding:0;overflow:auto;flex:1}
.item a{display:block;padding:7px 10px 7px 12px;border-bottom:1px solid #e4e4e4;border-left:4px solid transparent;color:inherit;text-decoration:none}
.item a:hover{background:#f0f4ff}
.item.current a{background:#e3ecff}
.item.insert a,.item.page_inserted a{border-left-color:rgb(var(--ins))}
.item.delete a,.item.page_deleted a{border-left-color:rgb(var(--del))}
.item.replace a{border-left-color:rgb(var(--rep))}
.item .loc{color:#777;font-size:11px;margin-left:4px}
.item .txt{display:block;margin-top:2px;word-wrap:break-word;font-size:12px}
.none{padding:12px;color:#555}
del{background:rgba(var(--del),.18);color:#8a1010}
ins{background:rgba(var(--ins),.18);color:#0a5a28;text-decoration:none}
.ctx{color:#888}
main{position:absolute;top:64px;left:var(--side);right:0;bottom:0;overflow:auto;padding:14px}
.rows{width:calc(100%% * var(--zoom));min-width:400px;margin:0 auto}
.row{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:18px}
.cell{min-width:0}
.hdr{font-size:11px;color:#555;margin:0 0 3px 2px;height:16px}
.badge{margin-left:8px;padding:1px 6px;border-radius:3px;color:#fff;font-weight:600;font-size:10px}
.badge.page_inserted{background:rgb(var(--ins))}
.badge.page_deleted{background:rgb(var(--del))}
.page{position:relative;background:#fff;box-shadow:0 1px 3px rgba(0,0,0,.25)}
.page img{display:block;width:100%%;height:100%%}
.page img:not([src]){visibility:hidden}
main>.warn{margin:0 0 10px;padding:6px 10px;background:#fff4d6}
.page.gone.page_inserted{outline:4px solid rgb(var(--ins))}
.page.gone.page_deleted{outline:4px solid rgb(var(--del))}
.page.missing{background:repeating-linear-gradient(45deg,#f2f2f2 0 10px,#e8e8e8 10px 20px);box-shadow:none;border:1px dashed #999;display:flex;align-items:flex-start;justify-content:center}
.page.missing span{margin-top:15%%;color:#666;font-size:14px}
.mark{position:absolute;display:block}
.mark.hl{mix-blend-mode:multiply;border-radius:2px}
.mark.hl.insert{background:rgba(var(--ins),.35)}
.mark.hl.delete{background:rgba(var(--del),.35)}
.mark.hl.replace{background:rgba(var(--rep),.35)}
.mark.caret{min-width:2px;min-height:2px}
.mark.caret.insert{background:rgb(var(--ins))}
.mark.caret.delete{background:rgb(var(--del))}
.mark.caret.replace{background:rgb(var(--rep))}
.mark.caret::after{content:"";position:absolute;left:50%%;bottom:-5px;width:8px;height:8px;margin-left:-4px;border-radius:50%%;background:inherit}
.mark.focus{outline:2px solid #ffb000;outline-offset:1px;animation:pulse 1s 2}
@keyframes pulse{50%%{outline-color:transparent}}
body.hide-insert .item.insert,body.hide-insert .mark.insert,
body.hide-delete .item.delete,body.hide-delete .mark.delete,
body.hide-replace .item.replace,body.hide-replace .mark.replace,
body.hide-page_inserted .item.page_inserted,body.hide-page_deleted .item.page_deleted{display:none}
@media print{header,aside{display:none}main{position:static;overflow:visible}.row{break-inside:avoid}}
"""

_JS = """
(function(){
  var imgs=JSON.parse(document.getElementById('images').textContent);
  function load(img){if(!img.src)img.src=imgs[+img.getAttribute('data-img')];}
  var main=document.querySelector('main'),rows=[].slice.call(document.querySelectorAll('.row'));
  // Load images of the rows within ~2 screens of the viewport. Rows are in document
  // order, so binary-search the first one that ends below the top margin.
  function refresh(){
    var h=main.clientHeight,top=-2*h,bottom=3*h,lo=0,hi=rows.length;
    while(lo<hi){var mid=(lo+hi)>>1;if(rows[mid].getBoundingClientRect().bottom<top)lo=mid+1;else hi=mid;}
    for(var i=lo;i<rows.length;i++){
      if(rows[i].getBoundingClientRect().top>bottom)break;
      rows[i].querySelectorAll('img[data-img]').forEach(load);
    }
  }
  var queued=false;
  function schedule(){if(!queued){queued=true;requestAnimationFrame(function(){queued=false;refresh();});}}
  main.addEventListener('scroll',schedule);
  window.addEventListener('resize',schedule);
  window.addEventListener('hashchange',schedule);
  window.addEventListener('load',refresh);
  window.addEventListener('beforeprint',function(){document.querySelectorAll('img[data-img]').forEach(load);});
  refresh();
  var items=[].slice.call(document.querySelectorAll('aside .item'));
  var cur=-1;
  function visible(){return items.filter(function(li){return li.offsetParent!==null;});}
  function select(li,scroll){
    items.forEach(function(x){x.classList.remove('current');});
    document.querySelectorAll('.mark.focus').forEach(function(m){m.classList.remove('focus');});
    if(!li)return;
    li.classList.add('current');
    cur=items.indexOf(li);
    li.scrollIntoView({block:'nearest'});
    var id=li.getAttribute('data-id');
    if(id){document.querySelectorAll('.mark[data-id="'+id+'"]').forEach(function(m){void m.offsetWidth;m.classList.add('focus');});}
    var t=document.getElementById(li.getAttribute('data-target'));
    if(t&&scroll)t.scrollIntoView({block:'center',behavior:'smooth'});
  }
  function step(d){
    var v=visible();if(!v.length)return;
    var i=v.indexOf(items[cur]);
    i=i<0?(d>0?0:v.length-1):Math.min(v.length-1,Math.max(0,i+d));
    select(v[i],true);
  }
  items.forEach(function(li){li.querySelector('a').addEventListener('click',function(e){e.preventDefault();select(li,true);});});
  document.querySelectorAll('.mark').forEach(function(m){m.addEventListener('click',function(e){
    e.preventDefault();select(document.getElementById('item-'+m.getAttribute('data-id')),false);});});
  document.getElementById('next').onclick=function(){step(1);};
  document.getElementById('prev').onclick=function(){step(-1);};
  var zoom=1,root=document.documentElement;
  function setZoom(z){zoom=Math.min(4,Math.max(.4,z));root.style.setProperty('--zoom',zoom);schedule();document.getElementById('zv').textContent=Math.round(zoom*100)+'%';}
  document.getElementById('zin').onclick=function(){setZoom(zoom*1.25);};
  document.getElementById('zout').onclick=function(){setZoom(zoom/1.25);};
  document.getElementById('zfit').onclick=function(){setZoom(1);};
  document.querySelectorAll('.filters input').forEach(function(cb){cb.addEventListener('change',function(){
    document.body.classList.toggle('hide-'+cb.getAttribute('data-kind'),!cb.checked);});});
  document.addEventListener('keydown',function(e){
    if(e.target.tagName==='INPUT'||e.metaKey||e.ctrlKey||e.altKey)return;
    if(e.key==='n'||e.key==='j'){step(1);e.preventDefault();}
    else if(e.key==='p'||e.key==='k'){step(-1);e.preventDefault();}
  });
})();
"""

_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:; style-src 'unsafe-inline'; script-src 'unsafe-inline'">
<meta name="referrer" content="no-referrer">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="generator" content="pdfdiff">
<title>{title}</title>
<style>{css}</style>
</head>
<body>
<header>
  <h1>PDF comparison</h1>
  <div class="files"><div title="{old_name}"><b>Old:</b> {old_name} ({old_pages} pages)</div><div title="{new_name}"><b>New:</b> {new_name} ({new_pages} pages)</div></div>
  <div class="sum">{summary}</div>
  <div class="tools">
    <button id="prev" title="Previous change (p / k)">&uarr; Prev</button>
    <button id="next" title="Next change (n / j)">&darr; Next</button>
    <button id="zout" title="Zoom out">&minus;</button><span id="zv">100%</span><button id="zin" title="Zoom in">+</button>
    <button id="zfit" title="Fit width">Fit</button>
  </div>
</header>
<aside>
  {warnings}
  <div class="filters">{filters}</div>
  <ol>
{items}
  </ol>
</aside>
<main><noscript><p class="warn">Enable JavaScript to see the page images.</p></noscript><div class="rows">
{rows}
</div></main>
<script type="application/json" id="images">{images}</script>
<script>{js}</script>
</body>
</html>
"""
