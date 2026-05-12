"""Live TUI for Phase E.2 progress.

Reads two sources:
  1. The orchestrator's stdout log (run_phase_e2.sh output) — for current
     batch / step / cost-from-judge / cost-from-livefetch lines.
  2. Per-batch v2 CSVs (runs/10k/batch-N-judge/ai-goldie-1.v2.csv) — for
     completed-batch scoreboards (fully-filled, explained, iter-R distribution).

Renders a live table refreshing every 2s with rich.live. Ctrl-C to exit; the
underlying Phase E.2 orchestrator keeps running.

Run:
    eval/.venv/bin/python eval/scripts/tui_progress.py \\
        --log /private/tmp/claude-501/.../tasks/b2l7dbool.output

If --log is omitted it tries runs/10k/phase-e2-batches-*.log.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.layout import Layout
from rich.text import Text
from rich.progress_bar import ProgressBar

BATCH_START_RE = re.compile(r"=== Batch (\d+) starting at (\d{2}:\d{2}:\d{2}) ===")
STEP_RE = re.compile(r"\[(\d)/5\] (.+)")
JUDGE_DONE_RE = re.compile(r"DONE — extracted: (\d+) / failed: (\d+) / cost: \$([\d.]+) \(extractor \$([\d.]+) \+ judge \$([\d.]+)\)")
T2_DONE_RE = re.compile(r"\s+done ([\d.]+)s steps=\d+\s+(ok|fail)")
BATCH_DONE_RE = re.compile(r"✓ batch (\d+) done at (\d{2}:\d{2}:\d{2})")
SUMMARY_RE = re.compile(r"summary: (\d+) fully filled \+ (\d+) explained = (\d+)/(\d+) filled-or-explained")
PHASE_DONE_RE = re.compile(r"Phase E\.2 batches .* DONE at (\d{2}:\d{2}:\d{2})")


def _is_empty(s: str) -> bool:
    if not s: return True
    s = s.strip()
    return not s or s.lower() in {"n/a", "na", "none", "null", "[]"}


def batch_stats(v2_csv: Path) -> dict[str, Any]:
    """Read a finished v2 CSV and compute the per-batch scoreboard."""
    fully = 0
    auth = abst = pdf = 0
    labels: Counter = Counter()
    rows = 0
    try:
        with v2_csv.open() as f:
            for r in csv.DictReader(f):
                rows += 1
                a = not _is_empty(r.get("Authors", ""))
                ab = not _is_empty(r.get("Abstract", ""))
                p = not _is_empty(r.get("PDF URL", ""))
                if a: auth += 1
                if ab: abst += 1
                if p: pdf += 1
                if a and ab and p: fully += 1
                note = (r.get("Notes") or "").strip()
                if note.startswith("iter-R:"):
                    labels[note[len("iter-R:"):].split("=")[0]] += 1
    except Exception:
        return {}
    return {
        "rows": rows,
        "fully": fully,
        "authors": auth,
        "abstract": abst,
        "pdf": pdf,
        "labels": labels,
    }


def parse_log(log_path: Path) -> dict[str, Any]:
    """Parse the orchestrator log into structured state."""
    state: dict[str, Any] = {
        "current_batch": None,
        "current_step": None,
        "current_step_name": None,
        "batches_seen": set(),
        "batches_done": {},      # batch_no -> done_time
        "batch_start": {},       # batch_no -> start_time
        "judge_cost": {},        # batch_no -> total cost
        "judge_extracted": {},   # batch_no -> n extracted
        "phase_done_at": None,
    }
    if not log_path.exists():
        return state

    current = None
    for line in log_path.read_text().splitlines():
        m = BATCH_START_RE.search(line)
        if m:
            current = int(m.group(1))
            state["current_batch"] = current
            state["batches_seen"].add(current)
            state["batch_start"][current] = m.group(2)
            state["current_step"] = None
            continue
        m = STEP_RE.search(line)
        if m and current is not None:
            state["current_step"] = int(m.group(1))
            state["current_step_name"] = m.group(2).strip().rstrip(".")
            continue
        m = JUDGE_DONE_RE.search(line)
        if m and current is not None:
            state["judge_extracted"][current] = int(m.group(1))
            state["judge_cost"][current] = float(m.group(3))
            continue
        m = BATCH_DONE_RE.search(line)
        if m:
            n = int(m.group(1))
            state["batches_done"][n] = m.group(2)
            continue
        m = PHASE_DONE_RE.search(line)
        if m:
            state["phase_done_at"] = m.group(1)
    return state


def find_default_log() -> Path | None:
    candidates = sorted(Path("runs/10k").glob("phase-e2-batches-*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None


def discover_completed_batches() -> list[int]:
    """Return sorted list of batch numbers that have a final v2 CSV on disk.
    This is the source of truth for completed batches across all logs/runs."""
    completed = []
    for d in Path("runs/10k").glob("batch-*-judge"):
        m = re.match(r"batch-(\d+)-judge", d.name)
        if not m: continue
        if (d / "ai-goldie-1.v2.csv").exists():
            completed.append(int(m.group(1)))
    return sorted(completed)


def batch_cost(batch_no: int) -> float:
    """Sum cost_usd from a batch's tier-log.jsonl. Falls back to 0 if missing."""
    log_path = Path(f"runs/10k/batch-{batch_no}-judge/ai-goldie-1.tier-log.jsonl")
    if not log_path.exists():
        return 0.0
    total = 0.0
    try:
        for line in log_path.read_text().splitlines():
            try:
                e = json.loads(line)
                c = e.get("cost_usd")
                if isinstance(c, (int, float)):
                    total += c
            except Exception:
                continue
    except Exception:
        return 0.0
    return total


def batch_done_at(batch_no: int) -> str:
    """Best-effort mtime of the v2 CSV as the 'done at' timestamp."""
    p = Path(f"runs/10k/batch-{batch_no}-judge/ai-goldie-1.v2.csv")
    if not p.exists():
        return "—"
    return datetime.fromtimestamp(p.stat().st_mtime).strftime("%H:%M:%S")


TOTAL_BATCHES = 100
TOTAL_DOIS = 10_000


def render(state: dict[str, Any], log_path: Path) -> Layout:
    layout = Layout()
    layout.split_column(
        Layout(name="header", size=7),
        Layout(name="body"),
        Layout(name="footer", size=3),
    )

    # Compute progress numbers up front (used by both header and footer)
    completed_batches = discover_completed_batches()
    n_done = len(completed_batches)
    n_dois_done = n_done * 100  # batches are uniformly 100 DOIs
    pct = (100.0 * n_done / TOTAL_BATCHES) if TOTAL_BATCHES else 0.0

    # Avg wall / batch + ETA
    avg_min_per_batch = None
    eta_str = "—"
    if n_done >= 2:
        try:
            first_v2 = Path(f"runs/10k/batch-{completed_batches[0]}-judge/ai-goldie-1.v2.csv")
            last_v2 = Path(f"runs/10k/batch-{completed_batches[-1]}-judge/ai-goldie-1.v2.csv")
            t0 = first_v2.stat().st_mtime
            t1 = last_v2.stat().st_mtime
            elapsed = (t1 - t0)
            avg_sec = elapsed / max(1, n_done - 1)
            avg_min_per_batch = avg_sec / 60.0
            remaining = TOTAL_BATCHES - n_done
            eta_sec = remaining * avg_sec
            if eta_sec > 3600:
                eta_str = f"{eta_sec/3600:.1f}h"
            else:
                eta_str = f"{eta_sec/60:.0f}m"
        except Exception:
            pass

    # Header — text status + progress bar group
    now = datetime.now().strftime("%H:%M:%S")
    cb = state.get("current_batch")
    cstep = state.get("current_step")
    cstep_name = state.get("current_step_name") or ""
    if state.get("phase_done_at"):
        header_text = Text.from_markup(f"[bold green]Phase E.2 DONE at {state['phase_done_at']}[/]")
    elif cb is not None:
        header_text = Text.from_markup(
            f"[bold]Now: batch {cb} — step {cstep}/5 — {cstep_name}[/]   [dim]({now})[/]"
        )
    else:
        header_text = Text.from_markup(f"[bold]Waiting for first batch …[/]   [dim]({now})[/]")

    batch_bar = ProgressBar(total=TOTAL_BATCHES, completed=n_done, width=None, complete_style="green", finished_style="green")
    progress_summary = Text.from_markup(
        f"[bold]{n_done}/{TOTAL_BATCHES}[/] batches  "
        f"([cyan]{pct:.1f}%[/])  •  "
        f"[bold]{n_dois_done:,}/{TOTAL_DOIS:,}[/] DOIs  •  "
        f"avg: [yellow]{(f'{avg_min_per_batch:.1f} min/batch' if avg_min_per_batch else '—')}[/]  •  "
        f"ETA: [magenta]{eta_str}[/]"
    )

    header_group = Group(header_text, Text(""), progress_summary, batch_bar)
    layout["header"].update(Panel(header_group, title="Phase E.2 — local M4 Max"))

    # Body: completed-batches table
    table = Table(title="Completed batches", expand=True)
    table.add_column("Batch", justify="right")
    table.add_column("Fully", justify="right")
    table.add_column("Authors", justify="right")
    table.add_column("Abstract", justify="right")
    table.add_column("PDF", justify="right")
    table.add_column("Labels", overflow="fold")
    table.add_column("Judge $", justify="right")
    table.add_column("Done at")

    total_fully = total_explained = total_rows = 0
    total_judge_cost = 0.0
    # Source of truth for completed batches is the filesystem (v2 CSVs),
    # not the orchestrator log — picks up batches from any prior run.
    completed_batches = discover_completed_batches()
    for n in completed_batches:
        v2 = Path(f"runs/10k/batch-{n}-judge/ai-goldie-1.v2.csv")
        s = batch_stats(v2)
        if not s:
            continue
        total_fully += s["fully"]
        total_rows += s["rows"]
        explained = sum(s["labels"].values())
        total_explained += explained
        # Prefer cost from current log; fall back to per-batch tier-log.jsonl
        cost = state["judge_cost"].get(n) or batch_cost(n)
        total_judge_cost += cost
        labels_str = " ".join(f"{k}:{v}" for k, v in s["labels"].most_common(5))
        done_at = state["batches_done"].get(n) or batch_done_at(n)
        table.add_row(
            str(n),
            f"{s['fully']}/{s['rows']}",
            str(s["authors"]),
            str(s["abstract"]),
            str(s["pdf"]),
            labels_str,
            f"${cost:.2f}" if cost else "—",
            done_at,
        )

    if completed_batches:
        # Summary row
        table.add_row(
            "[bold]TOTAL[/]",
            f"[bold]{total_fully}/{total_rows}[/]",
            "", "", "",
            f"[bold]filled-or-explained={total_fully+total_explained}/{total_rows}[/]",
            f"[bold]${total_judge_cost:.2f}[/]",
            "",
        )
    layout["body"].update(table)

    # Footer: ETA estimate
    footer_lines = []
    footer_lines.append(f"Log: [dim]{log_path}[/]")
    if completed_batches:
        # avg wall per batch (rough — from start of first to done of latest)
        first_start = state["batch_start"].get(completed_batches[0])
        last_done = state["batches_done"][completed_batches[-1]]
        if first_start and last_done:
            try:
                fmt = "%H:%M:%S"
                t0 = datetime.strptime(first_start, fmt)
                t1 = datetime.strptime(last_done, fmt)
                elapsed = (t1 - t0).total_seconds()
                avg = elapsed / len(completed_batches)
                footer_lines.append(f"Completed: {len(completed_batches)} batches | avg wall: {avg/60:.1f} min/batch | avg cost: ${total_judge_cost/len(completed_batches):.2f}")
            except Exception:
                pass
    layout["footer"].update(Panel("\n".join(footer_lines)))
    return layout


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--log", type=Path, default=None)
    ap.add_argument("--refresh", type=float, default=2.0)
    ap.add_argument("--once", action="store_true", help="render once and exit")
    ap.add_argument("--screen", action="store_true", help="use alt-screen buffer (default: in-place)")
    args = ap.parse_args()

    log_path = args.log or find_default_log()
    if not log_path or not log_path.exists():
        print(f"could not find a log file (tried: {log_path})", file=sys.stderr)
        return 1

    console = Console()

    if args.once:
        try:
            console.print(render(parse_log(log_path), log_path))
        except Exception as exc:
            console.print(f"[red]render error:[/red] {exc!r}")
            import traceback
            traceback.print_exc()
            return 2
        return 0

    try:
        with Live(
            render(parse_log(log_path), log_path),
            refresh_per_second=4,
            console=console,
            screen=args.screen,
            transient=False,
        ) as live:
            while True:
                time.sleep(args.refresh)
                try:
                    state = parse_log(log_path)
                    live.update(render(state, log_path))
                except Exception as exc:
                    live.update(Panel(f"[red]render error:[/red] {exc!r}\n[dim]TUI continues; orchestrator unaffected.[/]"))
                    continue
                if state.get("phase_done_at"):
                    # keep showing the final state for one more cycle then exit
                    time.sleep(args.refresh)
                    break
    except KeyboardInterrupt:
        console.print("\n[dim]TUI stopped. The Phase E.2 orchestrator is still running.[/]")
    except Exception as exc:
        console.print(f"[red]TUI crashed:[/red] {exc!r}")
        import traceback
        traceback.print_exc()
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
