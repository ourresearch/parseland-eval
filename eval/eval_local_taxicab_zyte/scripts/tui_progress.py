"""Live TUI for the eval_local_taxicab_zyte prod cascades.

Watches both `runs/authors-local-<label>-*/results.tier-log.jsonl` and
`runs/rases-zyte-<label>-*/results.tier-log.jsonl`. Refreshes every 2s
by default.

Per cascade shows:
  - Rows landed / total expected, % complete with a progress bar
  - Rolling ETA from the rate of new rows in the last 5 minutes
  - closed_at breakdown (tier_a / tier_b / tier_c / terminal)
  - Top publishers seen, ranked by row count
  - Recent failures (last 5 terminal rows + their tier-chain)

Footer shows combined wall time, combined cost, and DONE status when a
run's `cost-ledger.json` lands.

Usage:
    python eval/eval_local_taxicab_zyte/scripts/tui_progress.py
    python eval/eval_local_taxicab_zyte/scripts/tui_progress.py --label prod --refresh 3
    python eval/eval_local_taxicab_zyte/scripts/tui_progress.py --once   # single render
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter, deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

_SCRIPTS_DIR = Path(__file__).resolve().parent
WORKSPACE_DIR = _SCRIPTS_DIR.parent
RUNS_DIR = WORKSPACE_DIR / "runs"

# Expected row counts per cascade — used for % complete and ETA.
EXPECTED_ROWS = {
    "authors": 1619,
    "rases": 3826,
}

# Rolling window for ETA (minutes of recent activity to average).
ETA_WINDOW_MIN = 5
ETA_MIN_SAMPLES = 5  # don't compute ETA with fewer than this many recent rows

# Publisher prefix → label, lifted from merge_results.py.
_PREFIX_LABELS = {
    "10.1016": "Elsevier",
    "10.1109": "IEEE",
    "10.1007": "Springer",
    "10.1002": "Wiley",
    "10.1080": "T&F",
    "10.1177": "SAGE",
    "10.1021": "ACS",
    "10.1093": "OUP",
    "10.1017": "Cambridge",
    "10.1515": "DeGruyter",
    "10.1201": "T&F Books",
    "10.3390": "MDPI",
    "10.1186": "BMC",
    "10.1371": "PLOS",
    "10.1145": "ACM",
    "10.1042": "Portland",
    "10.2307": "JSTOR",
    "10.1121": "AIP",
    "10.1103": "APS",
    "10.1097": "LWW",
    "10.1101": "bioRxiv",
}


@dataclass
class CascadeSnapshot:
    label: str                       # "authors" or "rases"
    run_dir: Path | None
    rows: list[dict[str, Any]] = field(default_factory=list)
    started_at: float | None = None
    ended_at: float | None = None     # mtime of cost-ledger.json if present
    expected_rows: int = 0

    @property
    def n_landed(self) -> int:
        return len(self.rows)

    @property
    def pct(self) -> float:
        if not self.expected_rows:
            return 0.0
        return 100.0 * self.n_landed / self.expected_rows

    @property
    def is_done(self) -> bool:
        return self.ended_at is not None

    @property
    def closed_breakdown(self) -> Counter[str]:
        return Counter(r["closed_at"] for r in self.rows)

    @property
    def extraction_rate_pct(self) -> float:
        if not self.rows:
            return 0.0
        n = sum(1 for r in self.rows if r.get("extraction_present"))
        return 100.0 * n / len(self.rows)

    @property
    def total_cost(self) -> float:
        return sum(float(r.get("total_cost_usd") or 0.0) for r in self.rows)

    def eta_seconds(self) -> int | None:
        """Project remaining seconds from row arrival rate in the last ETA_WINDOW_MIN minutes."""
        if self.is_done or not self.expected_rows:
            return None
        remaining = self.expected_rows - self.n_landed
        if remaining <= 0:
            return 0
        # Use mtime of the tier-log JSONL as a proxy for arrival timestamps.
        # The JSONL is append-only so the file's mtime is the last write.
        # For per-row times we'd need a timestamp field, which we don't
        # write today — so derive rate from total wall time / total rows.
        if not self.started_at or len(self.rows) < ETA_MIN_SAMPLES:
            return None
        wall = time.time() - self.started_at
        if wall <= 0:
            return None
        rate = self.n_landed / wall  # rows per second over full run
        if rate <= 0:
            return None
        return int(remaining / rate)

    def top_publishers(self, n: int = 6) -> list[tuple[str, int, str]]:
        c = Counter(r.get("doi_prefix") or "" for r in self.rows)
        return [
            (prefix, count, _PREFIX_LABELS.get(prefix, "—"))
            for prefix, count in c.most_common(n)
            if prefix
        ]

    def recent_failures(self, n: int = 5) -> list[dict[str, Any]]:
        return [r for r in self.rows if r["closed_at"] == "terminal"][-n:]


def _latest_run_dir(prefix: str, label: str) -> Path | None:
    candidates = sorted(
        RUNS_DIR.glob(f"{prefix}-{label}-*"),
        key=lambda p: p.stat().st_mtime if p.exists() else 0,
    )
    return candidates[-1] if candidates else None


def _load_snapshot(name: str, prefix: str, label: str, expected: int) -> CascadeSnapshot:
    run_dir = _latest_run_dir(prefix, label)
    snap = CascadeSnapshot(label=name, run_dir=run_dir, expected_rows=expected)
    if not run_dir:
        return snap
    tier_log = run_dir / "results.tier-log.jsonl"
    if tier_log.exists():
        # Started timestamp = creation time of the run dir.
        try:
            snap.started_at = run_dir.stat().st_birthtime  # type: ignore[attr-defined]
        except AttributeError:
            snap.started_at = run_dir.stat().st_ctime
        rows: list[dict[str, Any]] = []
        # Read line-by-line so a partial last line doesn't blow up.
        with tier_log.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        snap.rows = rows
    ledger = run_dir / "cost-ledger.json"
    if ledger.exists():
        snap.ended_at = ledger.stat().st_mtime
    return snap


def _format_eta(seconds: int | None) -> str:
    if seconds is None:
        return "calc..."
    if seconds <= 0:
        return "now"
    h, rem = divmod(seconds, 3600)
    m, _ = divmod(rem, 60)
    if h:
        return f"~{h}h{m:02d}m"
    return f"~{m}m"


def _format_wall(start: float | None, end: float | None) -> str:
    if start is None:
        return "—"
    elapsed = (end or time.time()) - start
    h, rem = divmod(int(elapsed), 3600)
    m, _ = divmod(rem, 60)
    if h:
        return f"{h}h{m:02d}m"
    return f"{m}m"


# --- rich rendering ---------------------------------------------------------

def render(snaps: dict[str, CascadeSnapshot]) -> "Group":
    from rich.console import Group
    from rich.panel import Panel
    from rich.progress_bar import ProgressBar
    from rich.table import Table
    from rich.text import Text
    from rich.columns import Columns

    panels = []
    for name in ("authors", "rases"):
        snap = snaps[name]
        panels.append(_render_cascade_panel(name, snap, Panel, ProgressBar, Table, Text))
    body = Columns(panels, equal=True, expand=True)

    header = _render_header(snaps, Table, Text)
    footer = _render_footer(snaps, Text)

    return Group(header, body, footer)


def _render_header(snaps: dict[str, CascadeSnapshot], Table, Text) -> "Panel":
    from rich.panel import Panel

    t = Table.grid(expand=True, padding=(0, 2))
    t.add_column(ratio=1)
    t.add_column(ratio=1)
    t.add_column(ratio=1)

    now = datetime.now().strftime("%H:%M:%S")
    t.add_row(
        Text(f"[parseland-eval] local-taxicab-zyte prod cascade", style="bold cyan"),
        Text(f"refresh @ {now}", style="dim", justify="center"),
        Text(f"workspace: eval/eval_local_taxicab_zyte/", style="dim", justify="right"),
    )
    return Panel(t, border_style="cyan", padding=(0, 1))


def _render_cascade_panel(name: str, snap: CascadeSnapshot, Panel, ProgressBar, Table, Text) -> "Panel":
    from rich.console import Group as RGroup

    if snap.run_dir is None:
        body = Text("(no run found — has the cascade started?)", style="dim italic")
        return Panel(body, title=f"[bold]{name}[/]", border_style="dim", padding=(1, 2))

    # Progress bar
    pb = ProgressBar(total=snap.expected_rows, completed=snap.n_landed, width=None)
    eta_txt = "DONE" if snap.is_done else _format_eta(snap.eta_seconds())

    # Headline row
    head = Table.grid(expand=True, padding=(0, 1))
    head.add_column(ratio=2)
    head.add_column(ratio=1, justify="right")
    head.add_row(
        Text(f"{snap.n_landed} / {snap.expected_rows}  ({snap.pct:.1f}%)", style="bold"),
        Text(f"close-rate {snap.extraction_rate_pct:.1f}%",
             style="bold green" if snap.extraction_rate_pct >= 95 else "bold yellow"),
    )
    head.add_row(
        Text(f"wall {_format_wall(snap.started_at, snap.ended_at)}", style="dim"),
        Text(f"ETA {eta_txt}", style="dim" if not snap.is_done else "bold green", justify="right"),
    )

    # closed_at breakdown
    closed = snap.closed_breakdown
    closed_table = Table(box=None, expand=True, padding=(0, 1))
    closed_table.add_column("tier", style="bold")
    closed_table.add_column("rows", justify="right")
    closed_table.add_column("share", justify="right")
    total_closed = sum(closed.values()) or 1
    for tier in ("tier_a_reharvest", "tier_b_local_chrome", "tier_b_zyte",
                 "tier_c_zyte", "tier_c_local_chrome", "terminal"):
        n = closed.get(tier, 0)
        if n == 0:
            continue
        style = "red" if tier == "terminal" else (
            "green" if tier.startswith("tier_a") else "yellow"
        )
        closed_table.add_row(
            Text(tier.replace("tier_", "").replace("_", " "), style=style),
            str(n),
            f"{100*n/total_closed:.1f}%",
        )

    # top publishers
    pub_table = Table(box=None, expand=True, padding=(0, 1))
    pub_table.add_column("prefix", style="bold")
    pub_table.add_column("publisher", style="dim")
    pub_table.add_column("rows", justify="right")
    for prefix, count, label in snap.top_publishers(6):
        pub_table.add_row(prefix, label, str(count))

    # recent failures
    failures = snap.recent_failures(5)
    fail_lines: list[Text] = []
    if failures:
        fail_lines.append(Text("recent terminal:", style="bold red"))
        for f in failures:
            doi = (f.get("doi") or "")[:50]
            chain = " → ".join(
                a.get("status", "?") for a in (f.get("attempts") or [])
            )
            fail_lines.append(Text(f"  {doi}  [{chain}]", style="red"))

    body_parts = [
        head,
        pb,
        Text(""),
        Text("closed by tier:", style="bold"),
        closed_table,
        Text(""),
        Text("top publishers:", style="bold"),
        pub_table,
    ]
    if fail_lines:
        body_parts.append(Text(""))
        for fl in fail_lines:
            body_parts.append(fl)

    title_style = "bold green" if snap.is_done else "bold cyan"
    title = f"[{title_style}]{name}[/]  cost ${snap.total_cost:.2f}"
    border = "green" if snap.is_done else "cyan"
    return Panel(RGroup(*body_parts), title=title, border_style=border, padding=(1, 1))


def _render_footer(snaps: dict[str, CascadeSnapshot], Text) -> "Panel":
    from rich.panel import Panel
    from rich.table import Table

    combined_done = sum(s.n_landed for s in snaps.values())
    combined_expected = sum(s.expected_rows for s in snaps.values())
    combined_cost = sum(s.total_cost for s in snaps.values())
    combined_close_n = sum(
        sum(1 for r in s.rows if r.get("extraction_present")) for s in snaps.values()
    )
    combined_close_pct = (
        100.0 * combined_close_n / combined_done if combined_done else 0.0
    )
    all_done = all(s.is_done for s in snaps.values())

    t = Table.grid(expand=True, padding=(0, 2))
    t.add_column(ratio=1, justify="left")
    t.add_column(ratio=1, justify="center")
    t.add_column(ratio=1, justify="right")
    state = (
        Text("BOTH RUNS COMPLETE — ready to merge", style="bold green")
        if all_done
        else Text("running", style="bold yellow")
    )
    t.add_row(
        Text(f"combined: {combined_done} / {combined_expected}  ({100*combined_done/combined_expected if combined_expected else 0:.1f}%)"),
        state,
        Text(f"combined cost ${combined_cost:.2f}   close-rate {combined_close_pct:.1f}%"),
    )
    return Panel(t, border_style="green" if all_done else "yellow", padding=(0, 1))


# --- driver -----------------------------------------------------------------

def _gather(label: str) -> dict[str, CascadeSnapshot]:
    return {
        "authors": _load_snapshot("authors", "authors-local", label, EXPECTED_ROWS["authors"]),
        "rases":   _load_snapshot("rases", "rases-zyte", label, EXPECTED_ROWS["rases"]),
    }


def _render_once(label: str) -> None:
    from rich.console import Console
    console = Console()
    snaps = _gather(label)
    console.print(render(snaps))


def _render_live(label: str, refresh_s: float) -> None:
    from rich.console import Console
    from rich.live import Live

    console = Console()
    snaps = _gather(label)
    with Live(render(snaps), console=console, refresh_per_second=4, screen=False) as live:
        try:
            while True:
                snaps = _gather(label)
                live.update(render(snaps))
                if all(s.is_done for s in snaps.values()):
                    # One final render at "done" then exit.
                    time.sleep(refresh_s)
                    snaps = _gather(label)
                    live.update(render(snaps))
                    break
                time.sleep(refresh_s)
        except KeyboardInterrupt:
            pass


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--label", default="prod",
                    help="Run label to watch (matches {prefix}-{label}-*). Default: prod")
    ap.add_argument("--refresh", type=float, default=2.0,
                    help="Refresh interval in seconds (default: 2)")
    ap.add_argument("--once", action="store_true",
                    help="Render once and exit (no Live loop)")
    args = ap.parse_args(argv)

    if args.once:
        _render_once(args.label)
    else:
        _render_live(args.label, args.refresh)
    return 0


if __name__ == "__main__":
    sys.exit(main())
