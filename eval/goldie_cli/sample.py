"""Random DOI sampling from Crossref (sampling ONLY — never metadata evidence).

Builds a fresh corpus CSV (``No,DOI,Link``) where Link is the DOI.org resolver URL
(the only resolver). Article-like types only; dedups against existing gold. The actual
Crossref call is injectable so the logic tests offline.
"""
from __future__ import annotations

import csv
import json
import logging
import time
from pathlib import Path
from typing import Callable

from .schema import GOLD_COLUMNS

CROSSREF_URL = "https://api.crossref.org/works"
SAMPLE_BATCH = 100
POLITE_EMAIL = "reach2shubhankar@gmail.com"
LOG = logging.getLogger(__name__)
CROSSREF_TIMEOUT_S = 90
CROSSREF_FETCH_RETRIES = 50

# Crossref `type` values to drop — target journal-article-like content.
DROP_TYPES = {
    "book", "book-set", "book-series", "book-track", "dataset", "journal", "journal-issue",
    "journal-volume", "report-component", "component", "peer-review", "grant", "other",
    "proceedings", "proceedings-series", "standard", "report-series",
}


def keep_item(item: dict) -> bool:
    t = (item.get("type") or "").strip().lower()
    return bool(item.get("DOI")) and t not in DROP_TYPES


def _default_fetch_sample() -> list[dict]:
    import requests
    headers = {"User-Agent": f"goldie/sample (mailto:{POLITE_EMAIL})"}
    resp = requests.get(CROSSREF_URL, params={"sample": SAMPLE_BATCH, "mailto": POLITE_EMAIL},
                        headers=headers, timeout=CROSSREF_TIMEOUT_S)
    resp.raise_for_status()
    return resp.json().get("message", {}).get("items", [])


def sample_dois(
    target: int,
    *,
    fetch_sample: Callable[[], list[dict]] = _default_fetch_sample,
    exclude: frozenset[str] = frozenset(),
    accepted: list[str] | None = None,
    max_batches: int = 10_000,
    on_accept: Callable[[str], None] | None = None,
    max_fetch_retries: int = CROSSREF_FETCH_RETRIES,
    retry_sleep_s: float = 1.0,
) -> list[str]:
    """Return up to ``target`` unique article DOIs not in ``exclude``."""
    out: list[str] = list(accepted or [])
    seen = set(out) | set(exclude)
    batches = 0
    while len(out) < target and batches < max_batches:
        batches += 1
        attempts = 0
        while True:
            try:
                items = fetch_sample()
                break
            except Exception as exc:
                attempts += 1
                if attempts > max_fetch_retries:
                    raise
                LOG.warning(
                    "Crossref sample fetch failed (%d/%d): %s; retrying",
                    attempts,
                    max_fetch_retries,
                    exc,
                )
                if retry_sleep_s > 0:
                    time.sleep(min(retry_sleep_s * attempts, 10.0))
        for item in items:
            if not keep_item(item):
                continue
            doi = (item.get("DOI") or "").strip().lower()
            if doi and doi not in seen:
                seen.add(doi)
                out.append(doi)
                if on_accept:
                    on_accept(doi)
                if len(out) >= target:
                    break
    return out[:target]


def write_corpus_csv(path: Path, dois: list[str]) -> None:
    """Write a full-schema corpus CSV with DOI.org resolver links.

    Operators can pass this output directly to ``goldie run``. Extraction columns
    are intentionally blank because Crossref is used for DOI sampling only, never
    as metadata evidence.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=GOLD_COLUMNS)
        w.writeheader()
        for i, doi in enumerate(dois, 1):
            row = {k: "" for k in GOLD_COLUMNS}
            row.update({"No": str(i), "DOI": doi, "Link": f"https://doi.org/{doi}"})
            w.writerow(row)
    tmp.replace(path)


def load_gold_dois(gold_csv: Path) -> set[str]:
    if not gold_csv.exists():
        return set()
    with gold_csv.open(encoding="utf-8") as f:
        return {(r.get("DOI") or "").strip().lower() for r in csv.DictReader(f) if r.get("DOI")}


def load_partial(partial_path: Path) -> list[str]:
    """Load accepted DOI sample state from ``<out>.partial.jsonl``."""
    if not partial_path.exists():
        return []
    accepted: list[str] = []
    with partial_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            doi = (obj.get("doi") or "").strip().lower()
            if doi:
                accepted.append(doi)
    return accepted


def append_partial(partial_path: Path, doi: str) -> None:
    """Append one accepted DOI to the sampling resume file."""
    partial_path.parent.mkdir(parents=True, exist_ok=True)
    with partial_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"doi": doi}, ensure_ascii=False) + "\n")
