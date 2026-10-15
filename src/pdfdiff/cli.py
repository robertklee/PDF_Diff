"""Command-line interface.

Exit codes follow diff(1): 0 = no differences, 1 = differences, 2 = error.
"""

from __future__ import annotations

import argparse
import os
import sys

from . import __version__
from .compare import compare
from .extract import PDFError
from .model import CompareOptions
from .render import jsonfmt, pdf, term

FORMATS = ("term", "json", "pdf", "combined")


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
        "combined = one side-by-side PDF with a linked summary",
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
        "--page-threshold", type=_fraction, default=CompareOptions.page_threshold,
        metavar="FRACTION",
        help="report a page as inserted/deleted when at most this fraction of its words "
        "match the other document (default: %(default)s)",
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
    options = CompareOptions(
        ignore_case=args.ignore_case,
        ignore_punctuation=args.ignore_punctuation,
        dehyphenate=not args.no_dehyphenate,
        page_threshold=args.page_threshold,
    )
    try:
        result = compare(args.old, args.new, options, args.password_old, args.password_new)
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
    except (OSError, RuntimeError, ValueError) as e:
        print(f"pdfdiff: error: cannot write output: {e}", file=sys.stderr)
        return 2
    for path in written:
        print(f"wrote {path}", file=sys.stderr)

    if "term" in args.format:
        term.print_report(result, sys.stdout, color=False if args.no_color else None)
    else:
        print(term.summary_line(result), file=sys.stderr)

    return 0 if result.identical else 1


if __name__ == "__main__":
    sys.exit(main())
