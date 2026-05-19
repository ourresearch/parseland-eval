"""Snapshot OpenAlex /works responses for the 10K goldie as a frozen baseline.

Fetches `https://api.openalex.org/works/doi:{DOI}` for every DOI in
`merged-FINAL.csv` (10,000 unique) and stores the responses as sharded
gzipped NDJSON under `eval/data/openalex-baseline/`.

Why: before any future change to the OpenAlex parser or the API schema,
we need a point-in-time snapshot to diff against. Without it, a future
regression on any specific row is unfalsifiable.

Behavior:
  * Polite-pool: `mailto=` query param + descriptive User-Agent.
  * Throughput cap ~10 req/s (Semaphore + per-task post-response sleep).
  * Exponential backoff (10s, 60s, 300s) for 429 / 5xx / network errors.
  * 404 is terminal (no retry, recorded in fetch-log only).
  * Resumable: DOI-keyed `.checkpoint/openalex-baseline/done.partial.jsonl`.
    Re-runs skip completed DOIs, so shard append-mode never duplicates.
  * One shard file per registrant prefix with >= MIN_PREFIX_FOR_SHARD
    DOIs in the source; the rest fall through to `_other.ndjson.gz`.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import gzip
import hashlib
import json
import signal
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import IO

import httpx

# Make `eval.browser-use.runtime.checkpoint` importable. The directory has a
# hyphen so we can't do a normal package import; add it to sys.path.
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "eval" / "browser-use"))
from runtime.checkpoint import append_partial, load_partial  # noqa: E402


OPENALEX_BASE = "https://api.openalex.org/works/doi:"
RETRY_BACKOFF_SEC: tuple[float, ...] = (10.0, 60.0, 300.0)
MIN_PREFIX_FOR_SHARD = 10  # prefixes with fewer DOIs collapse into _other
USER_AGENT_TEMPLATE = "parseland-eval-baseline/1.0 (mailto:{mailto})"


@dataclass(frozen=True)
class FetchResult:
    doi: str
    http_status: int | None  # None on network error after retries
    duration_ms: int
    attempts: int
    shard: str | None  # only set for 200
    error: str | None  # only set for terminal failure


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def load_doi_list(source_csv: Path) -> list[str]:
    """Return unique lowercased DOIs in first-seen order from a CSV with a
    `DOI` column."""
    seen: set[str] = set()
    ordered: list[str] = []
    with source_csv.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if "DOI" not in (reader.fieldnames or []):
            raise SystemExit(
                f"source CSV {source_csv} has no DOI column; "
                f"fieldnames={reader.fieldnames}"
            )
        for row in reader:
            doi = (row.get("DOI") or "").strip().lower()
            if not doi or doi in seen:
                continue
            seen.add(doi)
            ordered.append(doi)
    return ordered


def compute_shard_routing(dois: list[str]) -> dict[str, str]:
    """For each DOI, return the shard filename it should land in.

    Prefixes appearing >= MIN_PREFIX_FOR_SHARD times get a named shard
    (e.g. 10.1016.ndjson.gz); everything else routes to _other.ndjson.gz.
    """
    counts: Counter[str] = Counter()
    for d in dois:
        counts[d.split("/", 1)[0]] += 1
    routing: dict[str, str] = {}
    for d in dois:
        prefix = d.split("/", 1)[0]
        if counts[prefix] >= MIN_PREFIX_FOR_SHARD:
            routing[d] = f"{prefix}.ndjson.gz"
        else:
            routing[d] = "_other.ndjson.gz"
    return routing


class ShardWriters:
    """Lazily-opened gzip append writers keyed by shard filename.

    Append-mode is safe because resume skips completed DOIs upstream; no
    DOI is ever written twice.
    """

    def __init__(self, shard_dir: Path) -> None:
        self._dir = shard_dir
        self._handles: dict[str, IO[str]] = {}
        self._lock = asyncio.Lock()
        self._dir.mkdir(parents=True, exist_ok=True)

    async def write(self, shard: str, line: str) -> None:
        async with self._lock:
            handle = self._handles.get(shard)
            if handle is None:
                handle = gzip.open(
                    self._dir / shard, "at", encoding="utf-8"
                )
                self._handles[shard] = handle
            handle.write(line)
            if not line.endswith("\n"):
                handle.write("\n")

    def close_all(self) -> None:
        for handle in self._handles.values():
            try:
                handle.flush()
                handle.close()
            except Exception:
                pass
        self._handles.clear()


class FetchLog:
    """Append-only JSONL writer for per-DOI fetch outcomes."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path = path
        self._lock = asyncio.Lock()

    async def write(self, entry: dict[str, object]) -> None:
        line = json.dumps(entry, ensure_ascii=False, default=str)
        async with self._lock:
            with self._path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")


async def fetch_one(
    client: httpx.AsyncClient,
    doi: str,
    shard_filename: str,
    mailto: str,
    sem: asyncio.Semaphore,
    shards: ShardWriters,
    fetch_log: FetchLog,
    partial_path: Path,
    rate_sleep_sec: float,
) -> FetchResult:
    url = f"{OPENALEX_BASE}{doi}"
    params = {"mailto": mailto}
    started = time.monotonic()
    attempt = 0
    last_status: int | None = None
    last_error: str | None = None

    async with sem:
        while True:
            attempt += 1
            try:
                resp = await client.get(url, params=params)
                last_status = resp.status_code

                if resp.status_code == 200:
                    payload = resp.json()
                    record = {
                        "doi": doi,
                        "fetched_at": utc_now_iso(),
                        "http_status": 200,
                        "openalex_response": payload,
                    }
                    await shards.write(
                        shard_filename,
                        json.dumps(record, ensure_ascii=False, default=str),
                    )
                    duration_ms = int((time.monotonic() - started) * 1000)
                    await fetch_log.write(
                        {
                            "doi": doi,
                            "status": "ok",
                            "http_code": 200,
                            "duration_ms": duration_ms,
                            "attempts": attempt,
                            "shard": shard_filename,
                        }
                    )
                    append_partial(
                        partial_path,
                        doi,
                        {"http_status": 200, "shard": shard_filename},
                    )
                    # Polite-pool throttle.
                    await asyncio.sleep(rate_sleep_sec)
                    return FetchResult(
                        doi=doi,
                        http_status=200,
                        duration_ms=duration_ms,
                        attempts=attempt,
                        shard=shard_filename,
                        error=None,
                    )

                if resp.status_code == 404:
                    duration_ms = int((time.monotonic() - started) * 1000)
                    await fetch_log.write(
                        {
                            "doi": doi,
                            "status": "not_in_openalex",
                            "http_code": 404,
                            "duration_ms": duration_ms,
                            "attempts": attempt,
                        }
                    )
                    append_partial(
                        partial_path, doi, {"http_status": 404}
                    )
                    await asyncio.sleep(rate_sleep_sec)
                    return FetchResult(
                        doi=doi,
                        http_status=404,
                        duration_ms=duration_ms,
                        attempts=attempt,
                        shard=None,
                        error=None,
                    )

                # Retryable: 429 or 5xx.
                if resp.status_code == 429 or 500 <= resp.status_code < 600:
                    last_error = f"http {resp.status_code}"
                    if attempt - 1 >= len(RETRY_BACKOFF_SEC):
                        break
                    await asyncio.sleep(RETRY_BACKOFF_SEC[attempt - 1])
                    continue

                # Other 4xx: terminal failure, no retry.
                last_error = f"http {resp.status_code}: {resp.text[:200]}"
                break

            except (httpx.RequestError, asyncio.TimeoutError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                if attempt - 1 >= len(RETRY_BACKOFF_SEC):
                    break
                await asyncio.sleep(RETRY_BACKOFF_SEC[attempt - 1])

    duration_ms = int((time.monotonic() - started) * 1000)
    await fetch_log.write(
        {
            "doi": doi,
            "status": "error",
            "http_code": last_status,
            "duration_ms": duration_ms,
            "attempts": attempt,
            "error": last_error,
        }
    )
    # Deliberately NOT writing to partial — so resume will retry.
    return FetchResult(
        doi=doi,
        http_status=last_status,
        duration_ms=duration_ms,
        attempts=attempt,
        shard=None,
        error=last_error,
    )


async def run_snapshot(
    source_csv: Path,
    out_dir: Path,
    concurrency: int,
    mailto: str,
    limit: int | None,
    rate_sleep_sec: float,
) -> dict[str, object]:
    started_at = utc_now_iso()
    started_mono = time.monotonic()

    source_csv = source_csv.resolve()
    all_dois = load_doi_list(source_csv)
    if limit is not None:
        all_dois = all_dois[:limit]
    routing = compute_shard_routing(all_dois)

    partial_path = (
        REPO_ROOT / ".checkpoint" / "openalex-baseline" / "done.partial.jsonl"
    )
    done = load_partial(partial_path)
    todo = [d for d in all_dois if d not in done]

    print(
        f"[snapshot] source={source_csv.name} unique_dois={len(all_dois)} "
        f"already_done={len(done & set(all_dois))} todo={len(todo)} "
        f"concurrency={concurrency} mailto={mailto}",
        file=sys.stderr,
    )

    shard_dir = out_dir / "shards"
    shards = ShardWriters(shard_dir)
    fetch_log = FetchLog(out_dir / "fetch-log.jsonl")

    sem = asyncio.Semaphore(concurrency)
    timeout = httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=None)
    headers = {"User-Agent": USER_AGENT_TEMPLATE.format(mailto=mailto)}

    counts: Counter[str] = Counter()  # ok / not_in_openalex / error
    progress_lock = asyncio.Lock()
    progress = {"done": 0}

    async def progress_logger() -> None:
        last_count = -1
        while True:
            await asyncio.sleep(30)
            async with progress_lock:
                d = progress["done"]
            if d == last_count:
                continue
            last_count = d
            elapsed = time.monotonic() - started_mono
            rate = d / elapsed if elapsed > 0 else 0
            eta = (len(todo) - d) / rate if rate > 0 else float("inf")
            print(
                f"[snapshot] progress {d}/{len(todo)} "
                f"({d / max(1, len(todo)) * 100:.1f}%) "
                f"rate={rate:.1f}/s eta={eta / 60:.1f}min "
                f"counts={dict(counts)}",
                file=sys.stderr,
            )

    async def bounded_fetch(doi: str) -> FetchResult:
        result = await fetch_one(
            client=client,
            doi=doi,
            shard_filename=routing[doi],
            mailto=mailto,
            sem=sem,
            shards=shards,
            fetch_log=fetch_log,
            partial_path=partial_path,
            rate_sleep_sec=rate_sleep_sec,
        )
        async with progress_lock:
            progress["done"] += 1
            if result.http_status == 200:
                counts["ok"] += 1
            elif result.http_status == 404:
                counts["not_in_openalex"] += 1
            else:
                counts["error"] += 1
        return result

    async with httpx.AsyncClient(
        timeout=timeout,
        headers=headers,
        http2=False,
        limits=httpx.Limits(
            max_connections=concurrency * 2,
            max_keepalive_connections=concurrency,
        ),
    ) as client:
        progress_task = asyncio.create_task(progress_logger())
        try:
            await asyncio.gather(*[bounded_fetch(d) for d in todo])
        finally:
            progress_task.cancel()
            try:
                await progress_task
            except asyncio.CancelledError:
                pass
            shards.close_all()

    finished_at = utc_now_iso()

    # Per-shard line counts (decompressed) for the manifest.
    shard_line_counts: dict[str, int] = {}
    for shard_file in sorted(shard_dir.glob("*.ndjson.gz")):
        with gzip.open(shard_file, "rt", encoding="utf-8") as f:
            shard_line_counts[shard_file.name] = sum(1 for _ in f)

    # Replay fetch-log to derive cumulative counts (survives resume runs).
    # Latest entry per DOI wins.
    final_status: dict[str, str] = {}
    log_path = out_dir / "fetch-log.jsonl"
    if log_path.exists():
        with log_path.open("r", encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                d = (entry.get("doi") or "").lower()
                if not d:
                    continue
                final_status[d] = entry.get("status", "")
    cumulative = Counter(final_status.values())

    try:
        source_csv_display = str(source_csv.relative_to(REPO_ROOT))
    except ValueError:
        source_csv_display = str(source_csv)
    manifest = {
        "schema_version": 1,
        "source_csv": source_csv_display,
        "source_csv_sha256": sha256_file(source_csv),
        "source_csv_unique_dois": len(all_dois),
        "generation_started_at": started_at,
        "generation_finished_at": finished_at,
        "polite_pool_email": mailto,
        "openalex_endpoint": OPENALEX_BASE + "{DOI}",
        "concurrency": concurrency,
        "rate_sleep_sec": rate_sleep_sec,
        "min_prefix_for_shard": MIN_PREFIX_FOR_SHARD,
        "counts_this_run": {
            "http_200": counts["ok"],
            "http_404": counts["not_in_openalex"],
            "error": counts["error"],
        },
        "counts_cumulative": {
            "http_200": cumulative.get("ok", 0),
            "http_404": cumulative.get("not_in_openalex", 0),
            "error": cumulative.get("error", 0),
            "total_dois_in_log": len(final_status),
        },
        "shard_line_counts": shard_line_counts,
    }
    manifest_path = out_dir / "manifest.json"
    tmp = manifest_path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    tmp.replace(manifest_path)

    print(
        f"[snapshot] done in {(time.monotonic() - started_mono) / 60:.1f}min "
        f"counts={dict(counts)} shards={len(shard_line_counts)}",
        file=sys.stderr,
    )
    return manifest


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Snapshot OpenAlex /works responses for a DOI list."
    )
    p.add_argument(
        "--source",
        type=Path,
        default=Path(
            "eval/eval_local_taxicab_zyte/runs/merged/"
            "merged-prod-20260517-152047/merged-FINAL.csv"
        ),
    )
    p.add_argument(
        "--out", type=Path, default=Path("eval/data/openalex-baseline")
    )
    p.add_argument("--concurrency", type=int, default=10)
    p.add_argument(
        "--mailto", default="reach2shubhankar@gmail.com",
    )
    p.add_argument("--limit", type=int, default=None)
    p.add_argument(
        "--rate-sleep-sec",
        type=float,
        default=0.1,
        help="Per-task post-response sleep (caps effective rate ~10/s with "
        "concurrency=10).",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()

    # Graceful SIGINT: shut down cleanly so partial checkpoint persists.
    def _on_sigint(_sig: int, _frame: object) -> None:
        print("\n[snapshot] SIGINT received; shutting down", file=sys.stderr)
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, _on_sigint)

    try:
        asyncio.run(
            run_snapshot(
                source_csv=args.source,
                out_dir=args.out,
                concurrency=args.concurrency,
                mailto=args.mailto,
                limit=args.limit,
                rate_sleep_sec=args.rate_sleep_sec,
            )
        )
    except KeyboardInterrupt:
        print("[snapshot] interrupted; resume by re-running same command", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
