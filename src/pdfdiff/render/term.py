"""Human-readable terminal summary with an inline (wdiff-style) view of each change."""

from __future__ import annotations

import os
import sys
from typing import TextIO

from ..model import Change, DiffResult, PageChange

_RED = "\033[31m"
_GREEN = "\033[32m"
_DIM = "\033[2m"
_BOLD = "\033[1m"
_YELLOW = "\033[33m"
_RESET = "\033[0m"

MAX_TEXT = 300


def use_color(stream: TextIO) -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    return hasattr(stream, "isatty") and stream.isatty()


def render(result: DiffResult, color: bool = False) -> str:
    def c(code: str, s: str) -> str:
        return f"{code}{s}{_RESET}" if color else s

    lines: list[str] = []
    lines.append(
        c(_BOLD, f"--- {result.old.path} ({result.old.page_count} pages)")
    )
    lines.append(
        c(_BOLD, f"+++ {result.new.path} ({result.new.page_count} pages)")
    )
    for w in result.warnings:
        lines.append(c(_YELLOW, f"warning: {w}"))
    lines.append("")

    for pc in result.page_changes:
        lines.append(c(_BOLD, f"[P{pc.id}] {_page_desc(pc)}"))
        if pc.preview:
            lines.append("    " + c(_GREEN if pc.kind == "page_inserted" else _RED, pc.preview))
        lines.append("")

    for ch in result.changes:
        lines.append(c(_BOLD, f"[{ch.id}] {ch.kind}  {_loc(ch)}"))
        lines.append("    " + _inline(ch, c))
        lines.append("")

    lines.append(summary_line(result))
    return "\n".join(lines) + "\n"


def summary_line(result: DiffResult) -> str:
    if result.identical:
        return "No differences found."
    counts = result.counts()
    parts = []
    for key, one, many in (
        ("page_inserted", "page inserted", "pages inserted"),
        ("page_deleted", "page deleted", "pages deleted"),
        ("replace", "replacement", "replacements"),
        ("insert", "insertion", "insertions"),
        ("delete", "deletion", "deletions"),
    ):
        n = counts[key]
        if n:
            parts.append(f"{n} {one if n == 1 else many}")
    n = len(result.changes) + len(result.page_changes)
    return f"{n} change{'s' if n != 1 else ''}: " + ", ".join(parts)


def print_report(result: DiffResult, stream: TextIO = sys.stdout, color: bool | None = None) -> None:
    if color is None:
        color = use_color(stream)
    stream.write(render(result, color=color))


def _page_desc(pc: PageChange) -> str:
    if pc.kind == "page_inserted":
        where = f"after old p.{pc.after + 1}" if pc.after >= 0 else "at start"
        desc = f"page inserted  new p.{pc.page + 1} ({where})"
    else:
        where = f"after new p.{pc.after + 1}" if pc.after >= 0 else "at start"
        desc = f"page deleted  old p.{pc.page + 1} ({where})"
    if not pc.word_count:
        desc += "  [blank]"
    return desc


def _loc(ch: Change) -> str:
    return f"old p.{_pages(ch.old_pages)} -> new p.{_pages(ch.new_pages)}"


def _pages(pages: list[int]) -> str:
    if not pages:
        return "-"
    if len(pages) == 1:
        return str(pages[0] + 1)
    return f"{pages[0] + 1}-{pages[-1] + 1}"


def _trim(s: str) -> str:
    return s if len(s) <= MAX_TEXT else s[: MAX_TEXT - 1] + "…"


def _inline(ch: Change, c) -> str:
    parts: list[str] = []
    if ch.context_before:
        parts.append(c(_DIM, "…" + ch.context_before))
    if ch.old_tokens:
        parts.append(c(_RED, f"[-{_trim(ch.old_text)}-]"))
    if ch.new_tokens:
        parts.append(c(_GREEN, f"{{+{_trim(ch.new_text)}+}}"))
    if ch.context_after:
        parts.append(c(_DIM, ch.context_after + "…"))
    return " ".join(parts)
