"""CLI: ``python -m parseland_eval run``."""
from __future__ import annotations

import argparse
import logging
import sys

from parseland_eval.gold import load_gold
from parseland_eval.report import write_run
from parseland_eval.runner import run_all
from parseland_eval.score.aggregate import score_row, summarize


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def cmd_run(args: argparse.Namespace) -> int:
    rows = load_gold()
    if args.limit is not None:
        rows = rows[: args.limit]
    logging.info("loaded %d gold rows", len(rows))

    runs = run_all(rows)
    scores = [score_row(g, r) for g, r in zip(rows, runs)]
    summary = summarize(scores)
    out = write_run(rows, runs, scores, summary, label=args.label, source="api")
    logging.info("wrote run to %s", out)

    o = summary["overall"]
    print(
        f"\n─── Parseland Eval — {len(rows)} rows ───\n"
        f"  Authors      F1 soft  : {o['authors_f1_soft']:.3f}   "
        f"P: {o.get('authors_precision_soft', 0):.3f}   R: {o.get('authors_recall_soft', 0):.3f}\n"
        f"  Affiliations F1 fuzzy : {o['affiliations_f1_fuzzy']:.3f}   "
        f"P: {o.get('affiliations_precision_fuzzy', 0):.3f}   R: {o.get('affiliations_recall_fuzzy', 0):.3f}\n"
        f"  Abstract     match    : {o.get('abstract_match_rate', 0):.3f} @ {o.get('abstract_match_threshold', 0):.2f}   "
        f"ratio: {o['abstract_ratio_fuzzy']:.3f}\n"
        f"  PDF URL      accuracy : {o['pdf_url_accuracy']:.3f}   "
        f"P: {o.get('pdf_url_precision', 0):.3f}   R: {o.get('pdf_url_recall', 0):.3f}\n"
        f"  Errors              : {o['errors']}\n"
        f"  Mean duration (ms)  : {o['duration_ms_mean']:.1f}\n"
        f"\n  run file: {out}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="parseland-eval", description="Parseland live-API eval")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="Run deployed Parseland service against gold rows + score")
    r.add_argument("--label", help="Optional label for the run file (e.g. 'baseline')")
    r.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only score the first N gold rows (useful for smoke tests)",
    )
    r.set_defaults(func=cmd_run)

    args = parser.parse_args(argv)
    _configure_logging(args.verbose)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
