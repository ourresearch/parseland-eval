"""Shared helpers for the two rerun scripts.

Single source of truth for path resolution, prompt loading, CSV I/O, and
the per-row tier-log writer — keeps `rerun_authors_local.py` and
`rerun_rases_zyte.py` thin and mostly identical in shape.
"""
from __future__ import annotations

import csv
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

# --- repo path resolution ---------------------------------------------------

_THIS_DIR = Path(__file__).resolve().parent
WORKSPACE_DIR = _THIS_DIR.parent                              # eval/eval_local_taxicab_zyte/
EVAL_DIR = WORKSPACE_DIR.parent                               # eval/
EVAL_SCRIPTS_DIR = EVAL_DIR / "scripts"
EVAL_PROMPTS_DIR = EVAL_DIR / "prompts"
REPO_ROOT = EVAL_DIR.parent                                   # parseland-eval/

# Allow read-only imports from eval/scripts/ — these modules are NOT
# modified by this workspace; we only depend on their public functions.
for _p in (EVAL_SCRIPTS_DIR, EVAL_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


# --- env loading ------------------------------------------------------------

def load_env() -> None:
    """Load `.env` from this workspace first, then fall back to eval/.env.
    Honor `override=True` per the CLAUDE.md dotenv-gotcha rule.
    """
    try:
        from dotenv import load_dotenv  # type: ignore
    except ImportError:
        return
    for candidate in (WORKSPACE_DIR / ".env", EVAL_DIR / ".env"):
        if candidate.exists():
            load_dotenv(candidate, override=True)


# --- prompt loading ---------------------------------------------------------

DEFAULT_PROMPT_NAME = "ai-goldie-v1.9.2.md"


def resolve_prompt_path(prompt: str | Path | None) -> Path:
    """Resolve user-supplied --prompt to an absolute path."""
    if not prompt:
        return EVAL_PROMPTS_DIR / DEFAULT_PROMPT_NAME
    p = Path(prompt)
    if p.is_absolute():
        return p
    # Try as-is from cwd, then as a name under eval/prompts/.
    if p.exists():
        return p.resolve()
    return EVAL_PROMPTS_DIR / p.name


def load_extraction_prompt(prompt_path: Path) -> tuple[str, str]:
    """Reuse extract_batch_cloud.load_prompt — returns (version, system_prompt)."""
    from extract_batch_cloud import load_prompt  # type: ignore
    return load_prompt(prompt_path)


# --- CSV I/O ----------------------------------------------------------------

# Input triage CSV schema (per eval/scripts/triage_10k.py).
INPUT_COLUMNS = (
    "No", "DOI", "Link", "Authors", "Abstract", "PDF URL",
    "Status", "Notes", "Has Bot Check", "Resolves To PDF",
    "broken_doi", "no english", "doi_prefix",
)

# Output schema mirrors the upstream gold shape (per extract_with_judge.to_gold_row_dict).
OUTPUT_COLUMNS = [
    "No", "DOI", "Link", "Authors", "Abstract", "PDF URL",
    "Status", "Notes", "Has Bot Check", "Resolves To PDF",
    "broken_doi", "no english",
]


def read_input_rows(path: Path, *, limit: int = 0) -> list[dict[str, str]]:
    """Read a triage CSV. Honors --limit (0 = all)."""
    rows: list[dict[str, str]] = []
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        missing = [c for c in INPUT_COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(
                f"{path}: missing required columns {missing} "
                f"(found {reader.fieldnames})"
            )
        for row in reader:
            doi = (row.get("DOI") or "").strip()
            if not doi:
                continue
            rows.append(row)
            if limit and len(rows) >= limit:
                break
    return rows


# --- output dir + per-row writers -------------------------------------------

def make_run_dir(parent: Path, prefix: str, label: str) -> Path:
    """`runs/<prefix>-<label>-<ts>/` — timestamped to never collide."""
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = parent / f"{prefix}-{label}-{ts}"
    out.mkdir(parents=True, exist_ok=True)
    return out


def append_tier_log(out_dir: Path, entry: dict[str, Any]) -> None:
    """One JSON line per processed row. Writes are append-only and flushed
    so concurrent workers can stream into the same file safely (single
    writer process, multiple coroutines — flush avoids interleaving)."""
    p = out_dir / "results.tier-log.jsonl"
    line = json.dumps(entry, ensure_ascii=False, default=str) + "\n"
    with p.open("a", encoding="utf-8") as f:
        f.write(line)


def append_failure(out_dir: Path, entry: dict[str, Any]) -> None:
    p = out_dir / "failures.jsonl"
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")


def write_results_csv(out_dir: Path, rows: Iterable[dict[str, Any]]) -> Path:
    """Atomic CSV write — `.tmp` + os.replace."""
    p = out_dir / "results.csv"
    tmp = p.with_suffix(".csv.tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=OUTPUT_COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in OUTPUT_COLUMNS})
    os.replace(tmp, p)
    return p


def write_cost_ledger(out_dir: Path, ledger: dict[str, Any]) -> Path:
    p = out_dir / "cost-ledger.json"
    p.write_text(json.dumps(ledger, indent=2, default=str), encoding="utf-8")
    return p


# --- tier-log entry shape ---------------------------------------------------

@dataclass
class TierAttempt:
    tier: str                         # e.g. "tier_a_reharvest", "tier_b_local_chrome", "tier_b_zyte", "tier_c_local_chrome"
    status: str                       # "approved", "needs_live_fetch", "auth_wall_confirmed", "fetch_failed", "extractor_failed"
    error: str | None = None
    duration_s: float = 0.0
    cost_usd: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tier": self.tier,
            "status": self.status,
            "error": self.error,
            "duration_s": round(self.duration_s, 2),
            "cost_usd": round(self.cost_usd, 4),
            **({"extra": self.extra} if self.extra else {}),
        }


def make_tier_log_entry(
    *,
    doi: str,
    no: int,
    link: str,
    doi_prefix: str,
    attempts: list[TierAttempt],
    closed_at: str,
    final_extraction: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "doi": doi,
        "no": no,
        "link": link,
        "doi_prefix": doi_prefix,
        "closed_at": closed_at,
        "attempts": [a.to_dict() for a in attempts],
        "total_cost_usd": round(sum(a.cost_usd for a in attempts), 4),
        "total_duration_s": round(sum(a.duration_s for a in attempts), 2),
        "extraction_present": bool(final_extraction),
    }


# --- logging ----------------------------------------------------------------

def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%H:%M:%S",
    )
    # Silence the chatty httpx loggers — Zyte calls otherwise spam INFO.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


# --- gold-row builder -------------------------------------------------------

def build_gold_row(
    *,
    no: int,
    doi: str,
    link: str,
    extraction: dict[str, Any] | None,
    notes: str,
) -> dict[str, Any]:
    """Mirror extract_with_judge.to_gold_row_dict, but defensive about
    missing extraction (terminal-tier rows still produce a row so the
    final merge is row-aligned)."""
    e = extraction or {}
    authors = e.get("Authors") or []
    return {
        "No": no,
        "DOI": doi,
        "Link": link,
        "Authors": json.dumps(authors, ensure_ascii=False),
        "Abstract": e.get("Abstract") or "",
        "PDF URL": e.get("PDF URL") or "",
        "Status": "TRUE" if e else "FALSE",
        "Notes": notes,
        "Has Bot Check": "TRUE" if e.get("has_bot_check") else "FALSE",
        "Resolves To PDF": "TRUE" if e.get("resolves_to_pdf") else "FALSE",
        "broken_doi": "TRUE" if e.get("broken_doi") else "FALSE",
        "no english": "TRUE" if e.get("no_english") else "FALSE",
    }


__all__ = [
    "WORKSPACE_DIR", "EVAL_DIR", "EVAL_SCRIPTS_DIR", "EVAL_PROMPTS_DIR", "REPO_ROOT",
    "DEFAULT_PROMPT_NAME",
    "INPUT_COLUMNS", "OUTPUT_COLUMNS",
    "TierAttempt",
    "load_env", "resolve_prompt_path", "load_extraction_prompt",
    "read_input_rows", "make_run_dir",
    "append_tier_log", "append_failure",
    "write_results_csv", "write_cost_ledger",
    "make_tier_log_entry", "setup_logging", "build_gold_row",
]
