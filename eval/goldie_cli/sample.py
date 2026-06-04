"""Random DOI sampling from Crossref (sampling ONLY — never metadata evidence).

Builds a fresh corpus CSV (``No,DOI,Link``) where Link is the DOI.org resolver URL
(the only resolver). Article-like types only; dedups against existing gold. The actual
Crossref call is injectable so the logic tests offline.
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Callable

CROSSREF_URL = "https://api.crossref.org/works"
SAMPLE_BATCH = 100
POLITE_EMAIL = "reach2shubhankar@gmail.com"

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
                        headers=headers, timeout=30)
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
) -> list[str]:
    """Return up to ``target`` unique article DOIs not in ``exclude``."""
    out: list[str] = list(accepted or [])
    seen = set(out) | set(exclude)
    batches = 0
    while len(out) < target and batches < max_batches:
        batches += 1
        for item in fetch_sample():
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
    """Write a corpus CSV with DOI.org resolver links (the only resolver)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["No", "DOI", "Link"])
        for i, doi in enumerate(dois, 1):
            w.writerow([i, doi, f"https://doi.org/{doi}"])
    tmp.replace(path)


def load_gold_dois(gold_csv: Path) -> set[str]:
    if not gold_csv.exists():
        return set()
    with gold_csv.open(encoding="utf-8") as f:
        return {(r.get("DOI") or "").strip().lower() for r in csv.DictReader(f) if r.get("DOI")}
