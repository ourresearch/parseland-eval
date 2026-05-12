"""Minimal checkpoint helper for the 10K orchestrator.

Two responsibilities:
  1. DOI-keyed `.partial.jsonl` checkpoint so a crashed/restarted batch resumes
     from where it left off (BUX cycles browser sessions every ~240 min and
     may reboot during long runs).
  2. Atomic `.tmp + rename` CSV writes so we never produce a half-written
     output CSV that a downstream reader might pick up.

Pattern ported from `eval/scripts/extract_batch_cloud.py` (lines 178, 264).
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Iterable


def load_partial(partial_path: Path) -> set[str]:
    """Read a `.partial.jsonl` and return the set of completed DOIs (lower-cased)."""
    if not partial_path.exists():
        return set()
    done: set[str] = set()
    with partial_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                doi = (obj.get("doi") or "").strip().lower()
                if doi:
                    done.add(doi)
            except json.JSONDecodeError:
                continue
    return done


def append_partial(partial_path: Path, doi: str, result: dict | None = None) -> None:
    """Append a single `{doi, ...}` record to the checkpoint file."""
    partial_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"doi": doi}
    if result:
        payload.update(result)
    with partial_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload) + "\n")


def atomic_write_csv(rows: Iterable[dict], fieldnames: list[str], path: Path) -> None:
    """Write rows to `path` via a .tmp file then rename, so partial writes
    never leave behind a half-finished CSV."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    tmp.replace(path)


if __name__ == "__main__":
    # Trivial self-test
    import tempfile, sys
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "partial.jsonl"
        append_partial(p, "10.x/abc")
        append_partial(p, "10.x/def", {"tier": "1.5"})
        assert load_partial(p) == {"10.x/abc", "10.x/def"}
        out = Path(td) / "out.csv"
        atomic_write_csv(
            [{"a": "1", "b": "x"}, {"a": "2", "b": "y"}],
            ["a", "b"],
            out,
        )
        assert out.read_text().startswith("a,b\n1,x\n2,y\n")
        print("checkpoint.py self-test: OK", file=sys.stderr)
