"""Resumable, DOI-keyed checkpoints + transparent failure log.

Append-only JSONL is crash-consistent: every landed DOI (success or failure) is one
line, last-write-wins on resume. A separate ``*.failures.jsonl`` is the source of
truth for blank CSV rows. Lifted from extract_batch_cloud.py:171-218.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_partial(path: Path) -> dict[str, dict[str, Any]]:
    """DOI → record dict, from an append-only JSONL checkpoint (last write wins)."""
    if not path.exists():
        return {}
    out: dict[str, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            doi = (obj.get("DOI") or "").strip()
            if doi:
                out[doi] = obj  # last write wins (resume after retry)
    return out


def append_partial(path: Path, record: dict[str, Any]) -> None:
    """Append one landed-DOI record to the checkpoint JSONL."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def append_failure(path: Path, payload: dict[str, Any]) -> None:
    """Append one failure record (DOI, No, error, retries) to the failures JSONL."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def count_lines(path: Path) -> int:
    """Number of non-blank JSONL records (used for the resume invariant assertion)."""
    if not path.exists():
        return 0
    n = 0
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                n += 1
    return n
