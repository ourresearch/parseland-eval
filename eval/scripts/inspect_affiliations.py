"""TUI for affiliation gap inspection.

Shows, per failed DOI, the gold-vs-predicted affiliations side-by-side with
character-level highlighting:

    GREEN background = present in gold but MISSING from AI (the gap)
    RED   background = present in AI but NOT in gold (hallucination / wrong pick)
    plain            = matching content

Auto-classifies each failure into one of four buckets:

    1 — empty            AI returned empty rases for an author who has gold
    3 — dropped detail   AI returned a substring of gold (or near-substring)
    4 — hallucinated     AI added content not in gold OR grabbed a job title
    2 — punctuation      pure normalization drift (rare)

Built for Casey's directive (2026-04-30):
    "look at examples outside of AI and see what is happening,
     and be able to summarize that so we can think it through."

Usage:
    eval/.venv/bin/python eval/scripts/inspect_affiliations.py
    eval/.venv/bin/python eval/scripts/inspect_affiliations.py --all
    eval/.venv/bin/python eval/scripts/inspect_affiliations.py \
        --human eval/goldie/holdout-50.csv \
        --ai runs/holdout-v1.5-taxicab/ai-goldie-1.csv
"""
from __future__ import annotations

import argparse
import difflib
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

# Reuse loaders/matchers from diff_goldie so bucket logic matches what we ship.
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
from diff_goldie import (  # noqa: E402
    _author_rases,
    _load_ai,
    _load_human,
    _name_to_author,
)

from rich import box  # noqa: E402
from rich.console import Console, Group  # noqa: E402
from rich.panel import Panel  # noqa: E402
from rich.text import Text  # noqa: E402
from rich.columns import Columns  # noqa: E402
from rich.rule import Rule  # noqa: E402

REPO_ROOT = SCRIPT_DIR.parent.parent
DEFAULT_HUMAN = REPO_ROOT / "eval" / "goldie" / "holdout-50.csv"
DEFAULT_AI = REPO_ROOT / "runs" / "holdout-v1.5-taxicab" / "ai-goldie-1.csv"

JOB_TITLE_TOKENS = {
    "professor", "prof", "associate", "assistant", "chemist", "engineer",
    "scientist", "researcher", "director", "chairman", "fellow", "lecturer",
    "instructor", "head of", "dean", "referee",
}

BUCKET_LABEL = {
    1: "Bucket 1 — AI returned empty",
    3: "Bucket 3 — Dropped postal/address/secondary detail",
    4: "Bucket 4 — Hallucinated or job-title content",
    2: "Bucket 2 — Pure punctuation/whitespace drift",
}
BUCKET_COLOR = {1: "red", 3: "yellow", 4: "magenta", 2: "blue"}


# ---- bucket classifier -----------------------------------------------------

import re

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _norm_for_compare(s: str) -> str:
    return " ".join((s or "").split()).lower()


def _tokens(s: str) -> set[str]:
    return set(_TOKEN_RE.findall((s or "").lower()))


def classify(gold: str, pred: str) -> int | None:
    """Return bucket id, or None if rases match (not a failure).

    Bucket logic (in order):
      1 — pred empty, gold non-empty
      2 — token-sort ratio >= 95 (pure punctuation/whitespace drift)
      3 — pred tokens are an (almost) subset of gold tokens AND pred is
          shorter (AI dropped postal codes / address detail / secondary aff)
      4 — pred has tokens not in gold (hallucinated / wrong pick / job title)
    """
    g_norm = _norm_for_compare(gold)
    p_norm = _norm_for_compare(pred)
    if g_norm == p_norm:
        return None
    if not p_norm and g_norm:
        return 1

    # Bucket 2: TRULY punctuation/whitespace-only differences.
    # Strip everything except letters/digits and compare. If equal, it's pure
    # punctuation drift (no content lost or added).
    g_alnum = re.sub(r"[^a-z0-9]+", "", g_norm)
    p_alnum = re.sub(r"[^a-z0-9]+", "", p_norm)
    if g_alnum == p_alnum and g_alnum:
        return 2

    g_tok = _tokens(gold)
    p_tok = _tokens(pred)
    if not g_tok or not p_tok:
        return 4

    # Bucket 3: AI's tokens are (mostly) contained in gold AND pred is shorter
    # — AI dropped detail (postal codes, secondary affiliations).
    extras = p_tok - g_tok
    coverage = len(p_tok & g_tok) / max(len(p_tok), 1)
    pred_shorter = len(pred) < len(gold)
    # Strong signal: pred is fully contained in gold (no extras)
    if not extras and pred_shorter:
        return 3
    # Looser: high coverage + clearly shorter
    if coverage >= 0.9 and len(extras) <= 1 and len(pred) < 0.85 * len(gold):
        return 3

    # Bucket 4: hallucinated content, wrong pick, or job-title prefix
    return 4


# ---- diff highlighting -----------------------------------------------------

def diff_text(gold: str, pred: str) -> tuple[Text, Text]:
    """Return (gold_with_green_gaps, pred_with_red_extras) Rich Texts.

    GREEN bg on gold = chars only in gold (the gap)
    RED   bg on pred = chars only in pred (hallucinated / wrong)
    """
    sm = difflib.SequenceMatcher(a=gold, b=pred, autojunk=False)
    g_text = Text()
    p_text = Text()
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        g_chunk = gold[i1:i2]
        p_chunk = pred[j1:j2]
        if tag == "equal":
            g_text.append(g_chunk)
            p_text.append(p_chunk)
        elif tag == "delete":
            g_text.append(g_chunk, style="black on green")
        elif tag == "insert":
            p_text.append(p_chunk, style="white on red")
        elif tag == "replace":
            g_text.append(g_chunk, style="black on green")
            p_text.append(p_chunk, style="white on red")
    return g_text, p_text


# ---- failure rows ----------------------------------------------------------

@dataclass(frozen=True)
class AuthorFailure:
    doi: str
    author: str
    gold: str
    pred: str
    bucket: int


def collect_failures(human: dict, ai: dict) -> list[AuthorFailure]:
    """Per-author failures across all shared DOIs."""
    out: list[AuthorFailure] = []
    for doi in sorted(human.keys() & ai.keys()):
        h_authors = human[doi]["authors"]
        a_authors = ai[doi]["authors"]
        h_map = _name_to_author(h_authors)
        a_map = _name_to_author(a_authors)
        for name in sorted(h_map.keys() & a_map.keys()):
            gold = (_author_rases(h_map[name]) or "").strip()
            pred = (_author_rases(a_map[name]) or "").strip()
            bucket = classify(gold, pred)
            if bucket is None:
                continue
            out.append(
                AuthorFailure(
                    doi=doi,
                    author=h_map[name].get("name") or name,
                    gold=gold,
                    pred=pred,
                    bucket=bucket,
                )
            )
    return out


def collect_doi_failures(failures: list[AuthorFailure]) -> list[tuple[str, list[AuthorFailure]]]:
    """Group per-author failures by DOI, preserving order."""
    grouped: dict[str, list[AuthorFailure]] = {}
    for f in failures:
        grouped.setdefault(f.doi, []).append(f)
    return list(grouped.items())


# ---- rendering -------------------------------------------------------------

def render_doi(idx: int, total: int, doi: str, fails: list[AuthorFailure]) -> Group:
    # Headline bucket = worst bucket on this DOI (most common, with tie -> lowest id)
    bucket_counts: dict[int, int] = {}
    for f in fails:
        bucket_counts[f.bucket] = bucket_counts.get(f.bucket, 0) + 1
    head_bucket = sorted(bucket_counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
    color = BUCKET_COLOR[head_bucket]
    label = BUCKET_LABEL[head_bucket]

    header = Text()
    header.append(f"[{idx + 1}/{total}]  ", style="bold")
    header.append(doi, style="bold cyan")
    header.append(f"   {len(fails)} failed author(s)   ", style="dim")
    header.append(label, style=f"bold {color}")

    blocks: list = [Panel(header, box=box.ROUNDED, border_style=color, padding=(0, 1))]

    for f in fails:
        gold_t, pred_t = diff_text(f.gold or "(empty)", f.pred or "(empty)")
        author_line = Text(f"author: {f.author}", style="bold")
        bucket_line = Text(BUCKET_LABEL[f.bucket], style=f"italic {BUCKET_COLOR[f.bucket]}")
        gold_panel = Panel(
            gold_t if f.gold else Text("(empty)", style="dim italic"),
            title="GOLD (verbatim)",
            border_style="green",
            box=box.ROUNDED,
            padding=(0, 1),
        )
        pred_panel = Panel(
            pred_t if f.pred else Text("(empty)", style="dim italic"),
            title="AI PRED",
            border_style="red" if f.bucket in (1, 4) else "yellow",
            box=box.ROUNDED,
            padding=(0, 1),
        )
        blocks.append(author_line)
        blocks.append(bucket_line)
        blocks.append(gold_panel)
        blocks.append(pred_panel)
        blocks.append(Rule(style="dim"))

    return Group(*blocks)


def bucket_summary(failures: list[AuthorFailure]) -> str:
    counts: dict[int, int] = {b: 0 for b in (1, 2, 3, 4)}
    for f in failures:
        counts[f.bucket] += 1
    total = sum(counts.values()) or 1
    parts = [
        f"Bucket 1 (empty): {counts[1]} ({100*counts[1]//total}%)",
        f"Bucket 3 (dropped detail): {counts[3]} ({100*counts[3]//total}%)",
        f"Bucket 4 (hallucinated/job-title): {counts[4]} ({100*counts[4]//total}%)",
        f"Bucket 2 (punctuation): {counts[2]} ({100*counts[2]//total}%)",
    ]
    return "  |  ".join(parts) + f"  |  total per-author failures: {total}"


# ---- CLI -------------------------------------------------------------------

def interactive(console: Console, doi_groups: list[tuple[str, list[AuthorFailure]]],
                summary_line: str) -> None:
    if not doi_groups:
        console.print("[bold green]No affiliation failures. Nothing to inspect.[/bold green]")
        return
    idx = 0
    while True:
        console.clear()
        console.rule("[bold]Affiliation gap inspector — Casey directive 2026-04-30[/bold]")
        console.print(summary_line, style="dim")
        console.print()
        doi, fails = doi_groups[idx]
        console.print(render_doi(idx, len(doi_groups), doi, fails))
        console.print()
        console.print(
            "[bold][n][/bold] next   [bold][p][/bold] prev   "
            "[bold][b1/b2/b3/b4][/bold] jump to bucket   "
            "[bold][q][/bold] quit",
            style="dim",
        )
        try:
            cmd = input("> ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            console.print("\nbye.")
            return
        if cmd in ("q", "quit", "exit"):
            return
        if cmd in ("n", "", "next"):
            idx = (idx + 1) % len(doi_groups)
        elif cmd in ("p", "prev"):
            idx = (idx - 1) % len(doi_groups)
        elif cmd.startswith("b") and cmd[1:].isdigit():
            target = int(cmd[1:])
            for offset in range(1, len(doi_groups) + 1):
                cand = (idx + offset) % len(doi_groups)
                if any(f.bucket == target for f in doi_groups[cand][1]):
                    idx = cand
                    break


def dump_all(console: Console, doi_groups: list[tuple[str, list[AuthorFailure]]],
             summary_line: str) -> None:
    console.rule("[bold]Affiliation gap inspector — full dump[/bold]")
    console.print(summary_line, style="dim")
    console.print()
    for i, (doi, fails) in enumerate(doi_groups):
        console.print(render_doi(i, len(doi_groups), doi, fails))
        console.print()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Interactive TUI for affiliation extraction failures.",
    )
    parser.add_argument("--human", type=Path, default=DEFAULT_HUMAN,
                        help=f"Human gold CSV (default: {DEFAULT_HUMAN.relative_to(REPO_ROOT)})")
    parser.add_argument("--ai", type=Path, default=DEFAULT_AI,
                        help=f"AI output CSV/JSON (default: {DEFAULT_AI.relative_to(REPO_ROOT)})")
    parser.add_argument("--all", action="store_true",
                        help="Non-interactive: dump every failure to stdout.")
    args = parser.parse_args(argv)

    if not args.human.exists():
        print(f"ERROR: human CSV not found: {args.human}", file=sys.stderr)
        return 2
    if not args.ai.exists():
        print(f"ERROR: AI output not found: {args.ai}", file=sys.stderr)
        return 2

    human = _load_human(args.human)
    ai = _load_ai(args.ai)
    failures = collect_failures(human, ai)
    doi_groups = collect_doi_failures(failures)
    summary_line = (
        f"human: {args.human.relative_to(REPO_ROOT)}  |  "
        f"ai: {args.ai.relative_to(REPO_ROOT)}  |  "
        f"failed DOIs: {len(doi_groups)}  |  {bucket_summary(failures)}"
    )

    console = Console()
    if args.all:
        dump_all(console, doi_groups, summary_line)
    else:
        interactive(console, doi_groups, summary_line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
