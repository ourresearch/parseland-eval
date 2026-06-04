"""`clean`, `migrate`, and a read-only run snapshot (monitor) — Phase 8 utilities.

clean: archive/remove known clutter behind a HARD allowlist guard that refuses to touch
gold/protected files. migrate: audit ai-goldie-{N}.csv consumers. snapshot: summarize a
run dir for the monitor without any shared state.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from .config import DATA_DIR

# Files that must NEVER be removed/archived (the gold + protected inputs).
PROTECTED = {"merged-FINAL.csv", "human-goldie.csv", "gold-standard.json",
             "gold-standard.seed.json", "gold-standard.holdout.json"}

# Known clutter (relative to eval/data) the clean command targets.
CLUTTER = [
    "ai-goldie-1-judged-10k", "ai-goldie-1-judged-10k 2",
    "ai-goldie-10k", "ai-goldie-10k.zip", "ai-goldie-10k 2.zip",
    "50_10K.csv.partial.jsonl",
]


def _is_protected(p: Path) -> bool:
    return p.name in PROTECTED


def find_clutter(data_dir: Path = DATA_DIR) -> list[Path]:
    found = []
    for name in CLUTTER:
        p = data_dir / name
        if p.exists() and not _is_protected(p):
            found.append(p)
    return found


def clean(*, mode: str = "dry-run", data_dir: Path = DATA_DIR, archive_root: Path | None = None) -> dict:
    """mode: 'dry-run' | 'archive' | 'remove'. Returns {'targets':[...], 'action':mode}."""
    import shutil
    targets = find_clutter(data_dir)
    for t in targets:
        if _is_protected(t):
            raise RuntimeError(f"refusing to touch protected file: {t}")
    if mode == "remove":
        for t in targets:
            shutil.rmtree(t) if t.is_dir() else t.unlink()
    elif mode == "archive":
        dest = archive_root or (data_dir.parent / "runs" / "_archive")
        dest.mkdir(parents=True, exist_ok=True)
        for t in targets:
            shutil.move(str(t), str(dest / t.name))
    return {"action": mode, "targets": [str(t) for t in targets]}


def migrate_check(repo_root: Path) -> dict:
    """Report files that reference ai-goldie-{N}.csv (consumers whose paths must keep resolving)."""
    hits: list[str] = []
    for p in repo_root.rglob("*.py"):
        if ".venv" in p.parts or "goldie_cli" in p.parts:
            continue
        try:
            txt = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if "ai-goldie-" in txt:
            hits.append(str(p.relative_to(repo_root)))
    return {"consumers": sorted(hits), "count": len(hits)}


@dataclass(frozen=True)
class RunSnapshot:
    corpus: str
    status: str
    rows: int
    landed: int
    failed: int
    cost_usd: float
    batch_csvs: int

    @classmethod
    def read(cls, run_dir) -> "RunSnapshot":
        """Summarize a run dir from its manifest + batch CSVs (read-only, no shared state)."""
        from .rundir import RunDir
        rd = run_dir if isinstance(run_dir, RunDir) else RunDir.open(Path(run_dir))
        m = rd.read_manifest()
        landed_csvs = list(rd.batches_dir.glob("batch-*/ai-goldie.csv")) if rd.batches_dir.exists() else []
        return cls(
            corpus=m.get("corpus", rd.root.name),
            status=m.get("status", "unknown"),
            rows=int(m.get("rows", 0)),
            landed=int(m.get("landed", 0)),
            failed=int(m.get("failed", 0)),
            cost_usd=float(m.get("cost_usd", 0.0)),
            batch_csvs=len(landed_csvs),
        )
