"""Command-line interface.

Exit codes follow diff(1): 0 = no differences, 1 = differences, 2 = error.
"""

from __future__ import annotations

import argparse
import os
import sys

from . import __version__
from .compare import compare
from .extract import PDFError, quiet_mupdf
from .model import CompareOptions
from .render import html, jsonfmt, pdf, term

FORMATS = ("term", "json", "pdf", "combined", "html")


def _formats(value: str) -> list[str]:
    fmts = [f.strip().lower() for f in value.split(",") if f.strip()]
    bad = [f for f in fmts if f not in FORMATS]
    if bad or not fmts:
        raise argparse.ArgumentTypeError(
            f"invalid format(s): {', '.join(bad) or value!r}; choose from {', '.join(FORMATS)}"
        )
    return fmts


def _fraction(value: str) -> float:
    try:
        f = float(value)
    except ValueError:
        f = -1.0
    if not 0.0 <= f < 1.0:
        raise argparse.ArgumentTypeError(f"must be a number in [0, 1): {value!r}")
    return f


def _dpi(value: str) -> int:
    try:
        d = int(value)
    except ValueError:
        d = 0
    if not 36 <= d <= 600:
        raise argparse.ArgumentTypeError(f"must be an integer from 36 to 600: {value!r}")
    return d


def _margins(value: str) -> tuple[float, float]:
    try:
        top, bottom = (float(v) for v in value.split(","))
    except ValueError:
        top = bottom = -1.0
    if top < 0 or bottom < 0:
        raise argparse.ArgumentTypeError(f"expected TOP,BOTTOM in points, e.g. 50,40: {value!r}")
    return (top, bottom)


def _jobs(value: str) -> int:
    try:
        n = int(value)
    except ValueError:
        n = 0
    if n < 1:
        raise argparse.ArgumentTypeError(f"must be a positive integer: {value!r}")
    return n


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pdfdiff",
        description="Compare the text of two PDF files (fully offline).",
    )
    p.add_argument("old", help="original PDF")
    p.add_argument("new", help="revised PDF")
    p.add_argument(
        "-f", "--format", type=_formats, default=["term"],
        help="comma-separated output formats (default: term): term = terminal report, "
        "json = change list, pdf = annotated copies of both PDFs, "
        "combined = one side-by-side PDF with a linked summary, "
        "html = self-contained side-by-side report",
    )
    p.add_argument(
        "-o", "--output-dir", default=".",
        help="directory for file outputs (default: current directory)",
    )
    p.add_argument("--ignore-case", action="store_true", help="ignore letter case")
    p.add_argument("--ignore-punctuation", action="store_true", help="ignore punctuation")
    p.add_argument(
        "--no-dehyphenate", action="store_true",
        help="do not join words hyphenated across line breaks",
    )
    p.add_argument(
        "--ignore-header-footer", action="store_true",
        help="ignore running headers, footers and page numbers (detected automatically)",
    )
    p.add_argument(
        "--margins", type=_margins, metavar="TOP,BOTTOM",
        help="ignore all text within TOP and BOTTOM points of the page edges "
        "(instead of detecting headers and footers)",
    )
    p.add_argument(
        "--page-threshold", type=_fraction, default=CompareOptions.page_threshold,
        metavar="FRACTION",
        help="report a page as inserted/deleted when at most this fraction of its words "
        "match the other document (default: %(default)s)",
    )
    p.add_argument(
        "--dpi", type=_dpi, default=html.DEFAULT_DPI,
        help="resolution of page images in the HTML report (default: %(default)s)",
    )
    p.add_argument(
        "-j", "--jobs", type=_jobs, default=None, metavar="N",
        help="worker processes for text extraction and page rendering (default: automatic; 1 = none)",
    )
    p.add_argument("--password-old", help="password for the old PDF")
    p.add_argument("--password-new", help="password for the new PDF")
    p.add_argument("--no-color", action="store_true", help="disable colored terminal output")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def _output_name(old: str, new: str, ext: str) -> str:
    a = os.path.splitext(os.path.basename(old))[0]
    b = os.path.splitext(os.path.basename(new))[0]
    return f"{a}_vs_{b}.diff.{ext}"


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return _run(args)
    except KeyboardInterrupt:
        print("pdfdiff: interrupted", file=sys.stderr)
        return 130
    except Exception as e:
        if os.environ.get("PDFDIFF_DEBUG"):
            raise
        print(
            f"pdfdiff: internal error: {type(e).__name__}: {e} (set PDFDIFF_DEBUG=1 for details)",
            file=sys.stderr,
        )
        return 2


def _run(args: argparse.Namespace) -> int:
    quiet_mupdf()
    header_footer = args.margins or ("auto" if args.ignore_header_footer else None)
    options = CompareOptions(
        ignore_case=args.ignore_case,
        ignore_punctuation=args.ignore_punctuation,
        dehyphenate=not args.no_dehyphenate,
        page_threshold=args.page_threshold,
        header_footer=header_footer,
    )
    try:
        result = compare(args.old, args.new, options, args.password_old, args.password_new, jobs=args.jobs)
    except PDFError as e:
        print(f"pdfdiff: error: {e}", file=sys.stderr)
        return 2

    def out(suffix: str) -> str:
        return os.path.join(args.output_dir, _output_name(args.old, args.new, suffix))

    written: list[str] = []
    try:
        if any(f != "term" for f in args.format):
            os.makedirs(args.output_dir, exist_ok=True)
        if "json" in args.format:
            jsonfmt.write(result, out("json"))
            written.append(out("json"))
        if "pdf" in args.format:
            pdf.write_annotated(result, out("old.pdf"), out("new.pdf"))
            written += [out("old.pdf"), out("new.pdf")]
        if "combined" in args.format:
            pdf.write_combined(result, out("combined.pdf"))
            written.append(out("combined.pdf"))
        if "html" in args.format:
            html.write(result, out("html"), dpi=args.dpi, jobs=args.jobs)
            written.append(out("html"))
    except (OSError, RuntimeError, ValueError) as e:
        print(f"pdfdiff: error: cannot write output: {e}", file=sys.stderr)
        return 2
    for path in written:
        print(f"wrote {path}", file=sys.stderr)

    if "term" in args.format:
        term.print_report(result, sys.stdout, color=False if args.no_color else None)
    else:
        for w in result.warnings:
            print(f"pdfdiff: warning: {w}", file=sys.stderr)
        print(term.summary_line(result), file=sys.stderr)

    return 0 if result.identical else 1


if __name__ == "__main__":
    sys.exit(main())
