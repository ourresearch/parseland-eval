"""``goldie`` command-line entry point.

Subcommands: sample · split · extract · run · report · monitor · clean · migrate · spike.
Phase 1 wires the parser, env/credential handling, and dispatch; individual commands
are filled in by later phases. Credentials are validated per command/tier only.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from . import __version__
from .config import (
    DEFAULT_ENV_FILE,
    DEFAULT_MODEL,
    GoldieConfig,
    credential_presence,
    load_env,
    validate_credentials,
)

log = logging.getLogger("goldie")

DEFAULT_PROMPT_NAME = "ai-goldie-v1.9.2.md"  # locked production prompt


def _not_yet(name: str) -> int:
    log.error("goldie %s: not yet implemented in this build phase", name)
    return 1


def _resolve_prompt(cfg: GoldieConfig, prompt_arg: str | None) -> tuple[str, str]:
    from .prompt import load_prompt
    path = Path(prompt_arg) if prompt_arg else cfg.prompts_dir / DEFAULT_PROMPT_NAME
    if not path.exists():
        from .config import ConfigError
        raise ConfigError(f"prompt file not found: {path}")
    return load_prompt(path)


def cmd_split(args, cfg: GoldieConfig) -> int:
    from .io import chunk_batches, read_source_rows
    rows = read_source_rows(Path(args.source))
    batches = chunk_batches(rows, args.batch_size)
    log.info("split %d rows → %d batches of %d", len(rows), len(batches), args.batch_size)
    return 0


def _run_pipeline(args, cfg: GoldieConfig, *, single_batch: int | None) -> int:
    """Shared body for `run` (full corpus) and `extract` (one tier / optional batch)."""
    from .backends import get_backend
    from .io import chunk_batches, read_source_rows
    from .orchestrator import run_corpus
    from .rundir import RunDir
    from .signals import install_handlers

    version, body = _resolve_prompt(cfg, args.prompt)
    model = getattr(args, "model", None) or DEFAULT_MODEL
    tier = args.tier
    try:
        backend = get_backend(tier, model=model)
    except Exception as e:  # missing dep / key already validated; surface clearly
        from .config import ConfigError
        raise ConfigError(f"could not init backend {tier!r}: {e}")

    rows = read_source_rows(Path(args.source))
    batches = chunk_batches(rows, cfg.batch_size)
    if single_batch is not None:
        batches = [b for b in batches if b[0] == single_batch] or batches[:1]

    corpus = getattr(args, "corpus", None) or Path(args.source).stem
    run_dir = RunDir.create(corpus)
    log.info("run: corpus=%s tier=%s model=%s prompt=%s rows=%d batches=%d → %s",
             corpus, tier, model, version, len(rows), len(batches), run_dir.root)

    no_fallback = bool(getattr(args, "no_fallback", False))
    holdout = getattr(args, "holdout", None)

    async def _go() -> dict:
        from .io import read_source_rows, write_csv_atomic
        from .schema import GOLD_COLUMNS
        from .tiers import run_with_fallback
        ev = install_handlers()
        try:
            manifest = await run_corpus(
                backend, batches, run_dir, prompt=body, corpus=corpus, model=model,
                concurrency=getattr(args, "concurrency", None) or cfg.concurrency,
                batch_concurrency=getattr(args, "batch_concurrency", None) or cfg.batch_concurrency,
                max_cost_usd=getattr(args, "max_cost_usd", None),
                skip_meta_tags=False, shutdown_event=ev,
            )
            # Finalize cascade: tier-2 live fallback (unless --no-fallback) → merge/classify/cleanup.
            fb = None if no_fallback else _make_fallback(cfg, body, model)
            merged_rows = read_source_rows(run_dir.merged_csv)
            finalized, fstats = await run_with_fallback(merged_rows, fallback_extract=fb, do_cleanup=True)
            write_csv_atomic(run_dir.merged_csv, [{k: r.get(k, "") for k in GOLD_COLUMNS} for r in finalized])
            manifest["fallback"] = {"enabled": not no_fallback, **fstats}
            run_dir.write_manifest(manifest)
            _write_run_report(run_dir, finalized, manifest, holdout)
            return manifest
        finally:
            await backend.aclose()

    manifest = asyncio.run(_go())
    fb = manifest.get("fallback", {})
    print(f"corpus={manifest['corpus']} status={manifest['status']} "
          f"landed={manifest['landed']}/{manifest['rows']} failed={manifest['failed']} "
          f"cost=${manifest['cost_usd']:.2f} fallback_used={fb.get('fallback_used', 0)} "
          f"→ {run_dir.merged_csv} | report {run_dir.report_path.name}")
    if manifest["status"] == "interrupted":
        return 130
    return 0 if manifest["failed"] == 0 else 1


def _make_fallback(cfg: GoldieConfig, prompt_body: str, model: str):
    """Build an async (doi, link) -> gold_row fallback extractor on the local_cdp (live)
    backend. Returns None (loudly) if the live backend can't init — tier-1 result stands."""
    import asyncio as _aio

    from .backends import get_backend
    from .backends.base import RetryPolicy
    from .pipeline import extract_one
    try:
        fb_backend = get_backend("local_cdp", model=model)
    except Exception as e:  # missing key / browser-use absent
        log.warning("tier-2 fallback unavailable (%s) — proceeding tier-1 only", e)
        return None
    sem = _aio.Semaphore(cfg.livefetch_concurrency)
    policy = RetryPolicy()

    async def _extract(doi: str, link: str):
        row, _cost, err = await extract_one(
            fb_backend, sem, policy, no=0, doi=doi, link=link, prompt=prompt_body,
            skip_meta_tags=False,
        )
        return None if (row is None or err) else row

    return _extract


def _write_run_report(run_dir, finalized, manifest, holdout) -> None:
    from .io import read_source_rows
    from .report import compute_report, summary_report, write_report
    if holdout and Path(holdout).exists():
        rep = compute_report(read_source_rows(Path(holdout)), finalized)
    else:
        rep = summary_report(finalized, manifest)
    write_report(run_dir.report_path, rep)


def cmd_run(args, cfg: GoldieConfig) -> int:
    return _run_pipeline(args, cfg, single_batch=None)


def cmd_extract(args, cfg: GoldieConfig) -> int:
    return _run_pipeline(args, cfg, single_batch=getattr(args, "batch", None))


def cmd_report(args, cfg: GoldieConfig) -> int:
    from .config import ConfigError
    from .io import read_source_rows
    from .report import compute_report, write_report
    from .rundir import RunDir

    rd = RunDir.open(Path(args.run))
    if not rd.merged_csv.exists():
        raise ConfigError(f"no merged.csv in {rd.root} — run the corpus first")
    produced = read_source_rows(rd.merged_csv)
    if not args.holdout:
        raise ConfigError("--holdout <gold.csv> is required to score a report")
    gold = read_source_rows(Path(args.holdout))
    rep = compute_report(gold, produced)
    write_report(rd.report_path, rep)
    print(f"report: {rep['matched_rows']} matched rows, {rep['fetch_ok_rows']} fetch-OK "
          f"(bar {rep['bar']:.0%})  → {rd.report_path}")
    for f in rep["field_order"]:
        fd = rep["fields"][f]
        mark = {"above": "OK ", "close": "~  ", "far": "XX "}[fd["status"]]
        print(f"  {mark}{f:9} all={fd['accuracy_all']:.1%}  fetch_ok={fd['accuracy_fetch_ok']:.1%}  "
              f"gap={fd['gap_to_bar']:+.2f}  buckets={fd['failure_buckets'] or '-'}")
    return 0


def cmd_sample(args, cfg: GoldieConfig) -> int:
    from .sample import load_gold_dois, sample_dois, write_corpus_csv
    exclude = load_gold_dois(Path(args.gold)) if args.gold else set()
    log.info("sampling %d DOIs from Crossref (excluding %d gold)", args.target, len(exclude))
    dois = sample_dois(args.target, exclude=frozenset(exclude))
    out = Path(args.out)
    hs = max(0, int(getattr(args, "holdout_size", 0) or 0))
    holdout, corpus = (dois[:hs], dois[hs:]) if hs else ([], dois)
    write_corpus_csv(out, corpus)
    print(f"sampled {len(corpus)} corpus DOIs → {out}")
    if holdout:
        hp = out.with_suffix(".holdout.csv")
        write_corpus_csv(hp, holdout)
        print(f"sealed {len(holdout)} holdout DOIs → {hp}")
    return 0


def cmd_clean(args, cfg: GoldieConfig) -> int:
    from .maintenance import clean
    mode = "remove" if args.remove else ("archive" if args.archive else "dry-run")
    res = clean(mode=mode)
    print(f"clean ({mode}): {len(res['targets'])} clutter target(s)")
    for t in res["targets"]:
        print("  ", t)
    return 0


def cmd_migrate(args, cfg: GoldieConfig) -> int:
    from .config import REPO_ROOT
    from .maintenance import migrate_check
    res = migrate_check(REPO_ROOT)
    print(f"ai-goldie-* consumers (paths must keep resolving): {res['count']}")
    for c in res["consumers"]:
        print("  ", c)
    return 0


def cmd_monitor(args, cfg: GoldieConfig) -> int:
    from .config import RUNS_DIR
    from .maintenance import RunSnapshot
    run = args.run
    if not run:
        cands = sorted(RUNS_DIR.glob("*/manifest.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not cands:
            log.error("no runs found under %s", RUNS_DIR)
            return 1
        run = cands[0].parent
    s = RunSnapshot.read(run)
    print(f"{s.corpus}: status={s.status} landed={s.landed}/{s.rows} failed={s.failed} "
          f"cost=${s.cost_usd:.2f} batch_csvs={s.batch_csvs}")
    return 0


def cmd_spike(args, cfg: GoldieConfig) -> int:
    from .spike.browserbase_fetch import run_spike
    from .transforms._source import src as tx
    from .rundir import RunDir, utc_stamp
    import json

    if args.spike_kind != "browserbase-fetch":
        return _not_yet(f"spike {args.spike_kind}")

    dois: list[str] = []
    if getattr(args, "source", None):
        from .io import read_source_rows
        dois = [(r.get("DOI") or "").strip() for r in read_source_rows(Path(args.source))][: args.sample_size]
    if not dois:
        raise __import__("goldie_cli.config", fromlist=["ConfigError"]).ConfigError(
            "spike needs --source <corpus.csv> to pick DOI.org-resolved URLs")

    def taxicab_fetch(doi: str):
        html, _resolved, _err = tx.fetch_html(doi)
        return html

    def browserbase_fetch(doi: str):
        # Raw HTML only — no json/structured extraction. Verify SDK call vs live docs.
        from browserbase import Browserbase  # lazy; needs BROWSERBASE_API_KEY
        import os
        bb = Browserbase(api_key=os.environ["BROWSERBASE_API_KEY"])
        return bb.fetch(url=f"https://doi.org/{doi}", format="html").content  # report-only

    report = run_spike(dois, taxicab_fetch=taxicab_fetch, browserbase_fetch=browserbase_fetch)
    rd = RunDir.create(f"spike-browserbase-{utc_stamp()}")
    out = rd.root / "spike-report.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"spike: {report['summary']}  → {out}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="goldie",
        description="Reusable gold-standard scholarly-metadata extraction CLI. "
                    "Evidence = page/Taxicab/browser only; never external metadata APIs.",
    )
    p.add_argument("--version", action="version", version=f"goldie {__version__}")
    p.add_argument("--env-file", default=str(DEFAULT_ENV_FILE),
                   help=f"dotenv file with credentials (default: {DEFAULT_ENV_FILE})")
    p.add_argument("--log-level", default="INFO",
                   choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    sub = p.add_subparsers(dest="command", required=True, metavar="<command>")

    sp = sub.add_parser("sample", help="Sample random DOIs via Crossref (sampling only)")
    sp.add_argument("--target", type=int, required=True, help="number of DOIs to sample")
    sp.add_argument("--out", required=True, help="output corpus CSV path")
    sp.add_argument("--gold", default=None, help="existing gold CSV to dedup against")
    sp.add_argument("--holdout-size", type=int, default=100, help="sealed holdout carve-out size")

    sp = sub.add_parser("split", help="Split a corpus CSV into fixed-size batches")
    sp.add_argument("--source", required=True)
    sp.add_argument("--batch-size", type=int, default=100)

    sp = sub.add_parser("extract", help="Run a single extraction tier over a corpus/batch")
    sp.add_argument("--source", required=True)
    sp.add_argument("--tier", required=True, choices=["cached", "cloud", "local_cdp"])
    sp.add_argument("--batch", type=int, default=None)
    sp.add_argument("--prompt", default=None)
    sp.add_argument("--model", default=None)

    sp = sub.add_parser("run", help="Full tiered pipeline over a corpus")
    sp.add_argument("--source", required=True)
    sp.add_argument("--corpus", required=True)
    sp.add_argument("--tier", default="cached", choices=["cached", "cloud", "local_cdp"],
                    help="entry tier requiring credentials (default: cached)")
    sp.add_argument("--concurrency", type=int, default=None)
    sp.add_argument("--batch-concurrency", type=int, default=None)
    sp.add_argument("--max-cost-usd", type=float, default=None)
    sp.add_argument("--prompt", default=None)
    sp.add_argument("--holdout", default=None)
    sp.add_argument("--resume", default=None)
    sp.add_argument("--monitor", action="store_true")
    sp.add_argument("--no-fallback", action="store_true",
                    help="disable tier-2 local_cdp live-fetch fallback (default: full cascade)")

    sp = sub.add_parser("report", help="(Re)build a run report, optionally scored vs holdout")
    sp.add_argument("--run", required=True)
    sp.add_argument("--holdout", default=None)

    sp = sub.add_parser("monitor", help="Read-only TUI over a run directory")
    sp.add_argument("--run", default=None)

    sp = sub.add_parser("clean", help="Archive/remove run-dir + known clutter (guarded)")
    grp = sp.add_mutually_exclusive_group()
    grp.add_argument("--dry-run", action="store_true")
    grp.add_argument("--archive", action="store_true")
    grp.add_argument("--remove", action="store_true")

    sp = sub.add_parser("migrate", help="Audit ai-goldie-{N}.csv consumers")
    sp.add_argument("--check", action="store_true")

    sp = sub.add_parser("spike", help="Scoped experiments (report-only)")
    spsub = sp.add_subparsers(dest="spike_kind", required=True, metavar="<kind>")
    bb = spsub.add_parser("browserbase-fetch",
                          help="Compare Browserbase raw Fetch vs Taxicab (report-only)")
    bb.add_argument("--sample-size", type=int, default=100)
    bb.add_argument("--source", default=None)

    return p


def _dispatch(args: argparse.Namespace, cfg: GoldieConfig) -> int:
    cmd = args.command
    if cmd == "extract":
        validate_credentials(tier=args.tier)
        return cmd_extract(args, cfg)
    if cmd == "run":
        validate_credentials(tier=args.tier)
        return cmd_run(args, cfg)
    if cmd == "split":
        return cmd_split(args, cfg)
    if cmd == "report":
        return cmd_report(args, cfg)
    if cmd == "sample":
        return cmd_sample(args, cfg)
    if cmd == "clean":
        return cmd_clean(args, cfg)
    if cmd == "migrate":
        return cmd_migrate(args, cfg)
    if cmd == "monitor":
        return cmd_monitor(args, cfg)
    if cmd == "spike":
        validate_credentials(spike=args.spike_kind)
        return cmd_spike(args, cfg)
    return _not_yet(cmd)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    from pathlib import Path
    load_env(Path(args.env_file))
    log.debug("credential presence: %s", credential_presence())
    cfg = GoldieConfig()
    return _dispatch(args, cfg)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
