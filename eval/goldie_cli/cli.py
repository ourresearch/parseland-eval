"""``goldie`` command-line entry point.

Subcommands: sample · prepare · random · split · extract · run · resume · report · monitor
· clean · migrate · spike.
Phase 1 wires the parser, env/credential handling, and dispatch; individual commands
are filled in by later phases. Credentials are validated per command/tier only.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

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
DEFAULT_GOLD_CSV = "eval/human-goldie.csv"

# Tier-2 fallback choices for `goldie run`. "cloud" is browser-use Cloud v3 — the live,
# JS-rendering quality tier; "local_cdp" is a local CDP-attached Chrome; "none" disables.
FALLBACK_TIERS = ("cloud", "local_cdp", "none")
DEFAULT_FALLBACK_TIER = "cloud"  # quality-first default for `run` (rendered-browser tier)


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


def _resolve_fallback_tier(args, *, default: str) -> str:
    """Resolve the effective tier-2 fallback from ``--fallback-tier`` / ``--no-fallback``.

    ``--no-fallback`` is the backward-compatible alias for ``--fallback-tier none``. Passing
    a real ``--fallback-tier`` together with ``--no-fallback`` is contradictory and fails
    loudly rather than silently picking one. Absent both, the command's ``default`` applies.
    """
    explicit = getattr(args, "fallback_tier", None)
    if getattr(args, "no_fallback", False):
        if explicit is not None and explicit != "none":
            from .config import ConfigError
            raise ConfigError(
                f"--no-fallback conflicts with --fallback-tier {explicit!r}; "
                "--no-fallback is the alias for --fallback-tier none."
            )
        return "none"
    return explicit or default


@dataclass
class _Fallback:
    """A built tier-2 fallback: the extractor closure, its backend (for lifecycle), the
    tier name, and a live cost accumulator (telemetry only — fallback is never cost-capped)."""

    tier: str
    extract: Callable[[str, str], Awaitable[dict[str, Any] | None]] | None = None
    backend: Any | None = None
    cost_usd: float = 0.0
    event_writer: Any | None = None

    @property
    def enabled(self) -> bool:
        return self.tier != "none"


def _status_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    true = 0
    false = 0
    other = 0
    for row in rows:
        status = str(row.get("Status") or "").strip().upper()
        if status == "TRUE":
            true += 1
        elif status == "FALSE":
            false += 1
        else:
            other += 1
    return {"true": true, "false": false, "other": other}


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _safe_corpus_name(name: str) -> str:
    cleaned = (name or "").strip()
    from .config import ConfigError
    if not cleaned:
        raise ConfigError("--name must be non-empty")
    if not re.fullmatch(r"[A-Za-z0-9._-]+", cleaned):
        raise ConfigError("--name may contain only letters, numbers, '.', '_' and '-'")
    return cleaned


def _operator_source_path(args, cfg: GoldieConfig) -> tuple[Path, str]:
    """Resolve the sample-only source path for `prepare` / `random`.

    The extraction corpus includes the sample timestamp so `goldie run` still creates the
    familiar `runs/<corpus>-<run-stamp>/` directory while the source lives beside it.
    """
    from .rundir import utc_stamp

    if getattr(args, "out", None):
        out = Path(args.out)
        corpus = out.parent.name if out.name == "source.csv" else out.stem
    else:
        corpus = f"{_safe_corpus_name(args.name)}-{utc_stamp()}"
        out = cfg.runs_dir / corpus / "source.csv"
    return out, corpus


def _default_gold_path(args) -> str | None:
    return getattr(args, "gold", None) or DEFAULT_GOLD_CSV


def _run_dir_from_corpus(corpus: str, cfg: GoldieConfig) -> Path | None:
    cands = sorted(
        cfg.runs_dir.glob(f"{corpus}-*/manifest.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return cands[0].parent if cands else None


def _make_fallback(
    cfg: GoldieConfig,
    prompt_body: str,
    model: str,
    fallback_tier: str,
    *,
    concurrency: int | None = None,
) -> _Fallback:
    """Build the tier-2 fallback extractor on ``fallback_tier`` (``cloud`` → browser-use
    Cloud, ``local_cdp`` → local CDP Chrome, ``none`` → disabled).

    Quality-first contract: if the chosen fallback backend cannot initialize, FAIL LOUDLY
    (``ConfigError`` → exit 2). It must never silently downgrade to a tier-1-only run —
    a green run would otherwise hide a misconfigured live quality fallback. The only way to
    run without tier-2 is to ask for it explicitly (``--no-fallback`` / ``--fallback-tier none``).
    Returns a ``_Fallback`` so the caller owns the backend's lifecycle and reads its cost.
    """
    fb = _Fallback(tier=fallback_tier)
    if fallback_tier == "none":
        return fb

    from .backends import get_backend
    from .backends.base import RetryPolicy
    from .config import ConfigError
    from .pipeline import extract_one

    try:
        fb.backend = get_backend(fallback_tier, model=model)
    except Exception as e:  # missing key / SDK / client init — surface, never swallow
        raise ConfigError(
            f"--fallback-tier {fallback_tier!r} requested but its backend could not "
            f"initialize: {e}. Fix the precondition or pass --no-fallback to opt out."
        )

    # Cloud can run wide; local CDP is bounded to a couple of live browsers.
    cap = cfg.livefetch_concurrency if fallback_tier == "local_cdp" else (concurrency or cfg.concurrency)
    sem = asyncio.Semaphore(cap)
    policy = RetryPolicy()

    async def _extract(doi: str, link: str):
        row, cost, err = await extract_one(
            fb.backend, sem, policy, no=0, doi=doi, link=link, prompt=prompt_body,
            skip_meta_tags=False, event_writer=fb.event_writer,
        )
        if cost:
            fb.cost_usd += cost
        return None if (row is None or err) else row

    fb.extract = _extract
    return fb


def _run_pipeline(args, cfg: GoldieConfig, *, single_batch: int | None,
                  default_fallback_tier: str) -> int:
    """Shared body for `run` (full corpus + tier-2 cascade) and `extract` (one tier)."""
    from .backends import get_backend
    from .config import ConfigError
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
        raise ConfigError(f"could not init backend {tier!r}: {e}")

    # Resolve + BUILD the tier-2 fallback up front so a misconfigured quality fallback
    # fails fast & loud, *before* the (paid) tier-1 corpus run — never a silent downgrade.
    fallback_tier = _resolve_fallback_tier(args, default=default_fallback_tier)
    effective_concurrency = getattr(args, "concurrency", None) or cfg.concurrency
    fb = _make_fallback(cfg, body, model, fallback_tier, concurrency=effective_concurrency)

    rows = read_source_rows(Path(args.source))
    batches = chunk_batches(rows, cfg.batch_size)
    if single_batch is not None:
        batches = [b for b in batches if b[0] == single_batch] or batches[:1]

    corpus = getattr(args, "corpus", None) or Path(args.source).stem
    resume = getattr(args, "resume", None)
    started_at_utc = _utc_iso()
    if resume:
        resume_root = Path(resume)
        if not resume_root.exists():
            raise ConfigError(f"--resume run dir does not exist: {resume_root}")
        run_dir = RunDir.open(resume_root)
        for d in (run_dir.batches_dir, run_dir.checkpoints_dir, run_dir.failures_dir, run_dir.logs_dir):
            d.mkdir(parents=True, exist_ok=True)
    else:
        run_dir = RunDir.create(corpus)
    from .events import RunEventWriter
    event_writer = RunEventWriter(run_dir)
    fb.event_writer = event_writer
    log.info("run: corpus=%s tier=%s fallback=%s model=%s prompt=%s rows=%d batches=%d → %s",
             corpus, tier, fallback_tier, model, version, len(rows), len(batches), run_dir.root)

    holdout = getattr(args, "holdout", None)

    async def _go() -> dict:
        from .io import read_source_rows, write_csv_atomic
        from .schema import GOLD_COLUMNS
        from .tiers import run_with_fallback
        ev = install_handlers()
        try:
            manifest = await run_corpus(
                backend, batches, run_dir, prompt=body, corpus=corpus, model=model,
                concurrency=effective_concurrency,
                batch_concurrency=getattr(args, "batch_concurrency", None) or cfg.batch_concurrency,
                max_cost_usd=getattr(args, "max_cost_usd", None),
                skip_meta_tags=False, shutdown_event=ev, event_writer=event_writer,
            )
            manifest.update({
                "source_csv": str(Path(args.source)),
                "source_csv_abs": str(Path(args.source).resolve()),
                "corpus": corpus,
                "tier": tier,
                "fallback_tier": fallback_tier,
                "prompt_version": version,
                "started_at_utc": started_at_utc,
                "report_json": str(run_dir.report_path),
                "command_argv": getattr(args, "command_argv", None),
            })
            if resume:
                manifest["resume_run_dir"] = str(Path(resume))
            # Finalize cascade: tier-2 fallback over empty-field rows → merge/classify/cleanup.
            # fb.extract is None only when the tier is "none".
            from .events import load_resolved_urls
            merged_rows = read_source_rows(run_dir.merged_csv)
            resolved_urls = load_resolved_urls(run_dir.events_path)
            manifest["tier1_landed"] = manifest.get("landed", 0)
            manifest["tier1_failed"] = manifest.get("failed", 0)
            manifest["status"] = "fallback_in_progress" if fb.enabled else "finalizing"
            manifest["fallback"] = {
                "tier": fb.tier,
                "enabled": fb.enabled,
                "status": "in_progress" if fb.enabled else "disabled",
            }
            run_dir.write_manifest(manifest)
            event_writer.write("fallback_phase_start", tier=fb.tier, enabled=fb.enabled)
            try:
                finalized, fstats = await run_with_fallback(
                    merged_rows, fallback_extract=fb.extract, do_cleanup=True,
                    event_writer=event_writer, fallback_tier=fb.tier,
                    resolved_urls=resolved_urls)
            except BaseException as e:
                status = "fallback_interrupted" if fb.enabled else "interrupted"
                if not isinstance(e, (KeyboardInterrupt, asyncio.CancelledError)):
                    status = "fallback_error" if fb.enabled else "finalize_error"
                manifest["status"] = status
                manifest["fallback"] = {
                    **manifest.get("fallback", {}),
                    "status": status,
                    "error": f"{type(e).__name__}: {e}",
                }
                run_dir.write_manifest(manifest)
                event_writer.write("fallback_phase_error", tier=fb.tier, enabled=fb.enabled,
                                   status=status, error=manifest["fallback"]["error"])
                raise
            write_csv_atomic(run_dir.merged_csv,
                             [{k: r.get(k, "") for k in GOLD_COLUMNS} for r in finalized])
            tier1_cost = manifest.get("cost_usd") or 0.0
            final_status = _status_counts(finalized)
            manifest["landed"] = len(finalized)
            manifest["failed"] = final_status["false"] + final_status["other"]
            manifest["final_status"] = final_status
            manifest["status"] = "complete"
            manifest["fallback"] = {
                "tier": fb.tier, "enabled": fb.enabled,
                "status": "complete" if fb.enabled else "disabled",
                "cost_usd": round(fb.cost_usd, 4), **fstats,
            }
            manifest["total_cost_usd"] = round(tier1_cost + fb.cost_usd, 4)
            manifest["completed_at_utc"] = _utc_iso()
            event_writer.write("fallback_phase_complete", **manifest["fallback"])
            try:
                _write_run_report(run_dir, finalized, manifest, holdout)
            except BaseException as e:
                status = "report_interrupted"
                if not isinstance(e, (KeyboardInterrupt, asyncio.CancelledError)):
                    status = "report_error"
                manifest["status"] = status
                manifest["report_error"] = f"{type(e).__name__}: {e}"
                run_dir.write_manifest(manifest)
                event_writer.write("report_error", status=status, error=manifest["report_error"])
                raise
            run_dir.write_manifest(manifest)
            event_writer.write("report_written", report=str(run_dir.report_path),
                               live_html=str(run_dir.live_html_path))
            return manifest
        finally:
            await backend.aclose()
            if fb.backend is not None:
                await fb.backend.aclose()

    manifest = asyncio.run(_go())
    fbm = manifest.get("fallback", {})
    print(f"corpus={manifest['corpus']} status={manifest['status']} "
          f"landed={manifest['landed']}/{manifest['rows']} failed={manifest['failed']} "
          f"tier1_cost=${manifest['cost_usd']:.2f} fallback={fbm.get('tier')} "
          f"fallback_used={fbm.get('fallback_used', 0)} fallback_cost=${fbm.get('cost_usd', 0.0):.2f} "
          f"total_cost=${manifest.get('total_cost_usd', manifest['cost_usd']):.2f} "
          f"→ {run_dir.merged_csv} | report {run_dir.report_path.name} | live {run_dir.live_html_path}")
    if manifest["status"] == "interrupted":
        return 130
    return 0 if manifest["failed"] == 0 else 1


def _write_run_report(run_dir, finalized, manifest, holdout) -> None:
    from .io import read_source_rows
    from .report import compute_report, summary_report, write_report
    if holdout and Path(holdout).exists():
        rep = compute_report(read_source_rows(Path(holdout)), finalized)
    else:
        rep = summary_report(finalized, manifest)
    write_report(run_dir.report_path, rep)


def _resume_run_args(args) -> argparse.Namespace:
    from .config import ConfigError
    from .rundir import RunDir

    rd = RunDir.open(Path(args.run))
    manifest = rd.read_manifest()
    source = manifest.get("source_csv") or manifest.get("source")
    corpus = manifest.get("corpus") or rd.root.name.rsplit("-", 1)[0]
    tiers = manifest.get("tiers") or []
    tier = getattr(args, "tier", None) or manifest.get("tier") or (tiers[0] if tiers else "cached")
    fallback = (
        getattr(args, "fallback_tier", None)
        or manifest.get("fallback_tier")
        or (manifest.get("fallback") or {}).get("tier")
        or DEFAULT_FALLBACK_TIER
    )
    if not source:
        raise ConfigError(
            f"cannot infer source CSV from {rd.manifest_path}. Re-run with the primitive form:\n"
            f"uv run --project eval goldie run --source <source.csv> --corpus {corpus} "
            f"--tier {tier} --fallback-tier {fallback} --resume {rd.root}"
        )
    if not Path(source).exists():
        raise ConfigError(
            f"source CSV recorded in manifest is missing: {source}. Re-run with:\n"
            f"uv run --project eval goldie run --source <source.csv> --corpus {corpus} "
            f"--tier {tier} --fallback-tier {fallback} --resume {rd.root}"
        )
    return argparse.Namespace(
        source=source,
        corpus=corpus,
        tier=tier,
        model=getattr(args, "model", None),
        prompt=getattr(args, "prompt", None),
        concurrency=getattr(args, "concurrency", None),
        batch_concurrency=getattr(args, "batch_concurrency", None),
        max_cost_usd=getattr(args, "max_cost_usd", None),
        batch=None,
        holdout=getattr(args, "holdout", None),
        resume=str(rd.root),
        fallback_tier=fallback,
        no_fallback=fallback == "none",
        command_argv=getattr(args, "command_argv", None),
    )


def cmd_run(args, cfg: GoldieConfig) -> int:
    # `run` is the full cascade: tier-2 fallback defaults to cloud (quality-first).
    return _run_pipeline(args, cfg, single_batch=None,
                         default_fallback_tier=DEFAULT_FALLBACK_TIER)


def cmd_resume(args, cfg: GoldieConfig) -> int:
    return cmd_run(_resume_run_args(args), cfg)


def cmd_prepare(args, cfg: GoldieConfig) -> int:
    out, corpus = _operator_source_path(args, cfg)
    sample_args = argparse.Namespace(
        target=args.count,
        out=str(out),
        gold=_default_gold_path(args),
        holdout_size=getattr(args, "holdout_size", 0),
        force=getattr(args, "force", False),
    )
    rc = cmd_sample(sample_args, cfg)
    print(f"corpus={corpus}")
    print(f"source={out}")
    print("run command:")
    print(
        "  uv run --project eval goldie run "
        f"--source {out} --corpus {corpus} --tier cached --fallback-tier cloud"
    )
    return rc


def cmd_random(args, cfg: GoldieConfig) -> int:
    out, corpus = _operator_source_path(args, cfg)
    sample_args = argparse.Namespace(
        target=args.count,
        out=str(out),
        gold=_default_gold_path(args),
        holdout_size=getattr(args, "holdout_size", 0),
        force=getattr(args, "force", False),
    )
    sample_rc = cmd_sample(sample_args, cfg)
    if sample_rc != 0:
        return sample_rc

    run_args = argparse.Namespace(
        source=str(out),
        corpus=corpus,
        tier=getattr(args, "tier", "cached"),
        model=getattr(args, "model", None),
        prompt=getattr(args, "prompt", None),
        concurrency=getattr(args, "concurrency", None),
        batch_concurrency=getattr(args, "batch_concurrency", None),
        max_cost_usd=getattr(args, "max_cost_usd", None),
        batch=None,
        holdout=getattr(args, "holdout", None),
        resume=None,
        fallback_tier=getattr(args, "fallback_tier", None),
        no_fallback=getattr(args, "no_fallback", False),
        command_argv=getattr(args, "command_argv", None),
    )
    run_rc = cmd_run(run_args, cfg)
    run_dir = _run_dir_from_corpus(corpus, cfg)
    if run_dir:
        out_arg = getattr(args, "report_out", None) or str(run_dir / "OPERATOR_REPORT.md")
        report_args = argparse.Namespace(
            run=str(run_dir),
            holdout=getattr(args, "holdout", None),
            operator=True,
            out=out_arg,
        )
        cmd_report(report_args, cfg)
        print(f"operator_report={out_arg}")
    return run_rc


def cmd_extract(args, cfg: GoldieConfig) -> int:
    # `extract` is explicitly a single tier — no tier-2 fallback unless `run` is used.
    return _run_pipeline(args, cfg, single_batch=getattr(args, "batch", None),
                         default_fallback_tier="none")


def cmd_report(args, cfg: GoldieConfig) -> int:
    from .config import ConfigError
    from .io import read_source_rows
    from .report import (
        FIELD_ORDER,
        compute_report,
        operator_report_markdown,
        summary_report,
        write_operator_report,
        write_report,
    )
    from .rundir import RunDir

    rd = RunDir.open(Path(args.run))
    if not rd.merged_csv.exists():
        raise ConfigError(f"no merged.csv in {rd.root} — run the corpus first")
    produced = read_source_rows(rd.merged_csv)
    manifest = rd.read_manifest()
    if args.holdout:
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
    else:
        rep = summary_report(produced, manifest)
        write_report(rd.report_path, rep)
        print(f"summary: rows={rep['rows']} fetch_ok={rep['fetch_ok_rows']}/{rep['rows']} "
              f"extraction_miss={rep['extraction_miss']} → {rd.report_path}")
        for f in FIELD_ORDER:
            fp = rep["field_presence"][f]
            fm = rep.get("field_missing", {}).get(f, {})
            print(f"  {f:9} present={fp['present']}/{fp['rows']} "
                  f"missing={fm.get('missing', fp['rows'] - fp['present'])}/{fp['rows']}")
        if rep.get("missing_field_combinations"):
            combos = ", ".join(f"{combo}:{n}" for combo, n in rep["missing_field_combinations"][:5])
            print(f"  missing_combos={combos}")
        qf = rep.get("quality_focus") or {}
        if qf.get("counts"):
            counts = qf["counts"]
            print("  quality_focus="
                  f"all_core_empty:{counts.get('all_core_empty', 0)} "
                  f"unresolved_empty:{counts.get('unresolved_all_core_empty', 0)} "
                  f"terminal_empty:{counts.get('terminal_flagged_empty', 0)} "
                  f"bot_empty:{counts.get('bot_check_empty', 0)} "
                  f"ca_only:{counts.get('ca_only_missing', 0)} "
                  f"ca_convention:{counts.get('ca_convention_candidates', 0)} "
                  f"ca_audit:{counts.get('ca_needs_evidence_audit', 0)} "
                  f"extraction_miss:{counts.get('extraction_miss', 0)} "
                  f"multi_missing:{counts.get('multi_priority_missing', 0)}")
            sample = [r.get("doi") for r in qf.get("all_core_empty", [])[:5] if r.get("doi")]
            if sample:
                print(f"  all_core_empty_sample={', '.join(sample)}")
        if rep.get("taxicab_reharvest"):
            print(f"  taxicab_reharvest={rep['taxicab_reharvest']}")
        if rep.get("fallback"):
            fb = rep["fallback"]
            returned = fb.get("fallback_returned")
            returned_s = f" returned={returned}" if returned is not None else ""
            legacy_s = " legacy_used_semantics=returned" if fb.get("legacy_fallback_used_semantics") else ""
            filled = fb.get("fallback_filled_fields") or {}
            filled_s = f" filled={filled}" if filled else ""
            print(f"  fallback={fb.get('tier')} attempted={fb.get('fallback_attempted', 0)}"
                  f"{returned_s} used={fb.get('fallback_used', 0)}{filled_s}{legacy_s}")
    if getattr(args, "operator", False):
        out = Path(getattr(args, "out", None) or (rd.root / "OPERATOR_REPORT.md"))
        markdown = operator_report_markdown(rd.root, rep, manifest)
        write_operator_report(out, markdown)
        manifest["report_json"] = str(rd.report_path)
        manifest["operator_report"] = str(out)
        rd.write_manifest(manifest)
        print(f"operator report → {out}")
    return 0


def cmd_sample(args, cfg: GoldieConfig) -> int:
    from .config import ConfigError
    from .sample import append_partial, load_gold_dois, load_partial, sample_dois, write_corpus_csv
    if args.target <= 0:
        raise ConfigError("--target must be positive")
    hs = max(0, int(getattr(args, "holdout_size", 0) or 0))
    if hs >= args.target:
        raise ConfigError("--holdout-size must be smaller than --target")
    out = Path(args.out)
    partial = out.with_suffix(out.suffix + ".partial.jsonl")
    if getattr(args, "force", False):
        partial.unlink(missing_ok=True)
        out.unlink(missing_ok=True)
        out.with_suffix(".holdout.csv").unlink(missing_ok=True)
    exclude = load_gold_dois(Path(args.gold)) if args.gold else set()
    accepted = load_partial(partial)
    log.info("sampling %d DOIs from Crossref (excluding %d gold, resuming %d accepted)",
             args.target, len(exclude), len(accepted))
    dois = sample_dois(
        args.target,
        exclude=frozenset(exclude),
        accepted=accepted,
        on_accept=lambda doi: append_partial(partial, doi),
    )
    holdout, corpus = (dois[:hs], dois[hs:]) if hs else ([], dois)
    write_corpus_csv(out, corpus)
    print(f"sampled {len(corpus)} corpus DOIs → {out}")
    print(f"resume file: {partial}")
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
    if getattr(args, "watch", False):
        import time
        from rich.live import Live
        from rich.table import Table

        def _table():
            snap = RunSnapshot.read(run)
            t = Table(title=f"goldie monitor: {snap.corpus}")
            t.add_column("status")
            t.add_column("landed")
            t.add_column("failed")
            t.add_column("fallback")
            t.add_column("taxicab reharvest")
            t.add_column("events")
            t.add_row(
                snap.status,
                f"{snap.landed}/{snap.rows}",
                str(snap.failed),
                str(snap.fallback_used),
                str(snap.taxicab_reharvest),
                str(snap.events),
            )
            return t

        with Live(_table(), refresh_per_second=1) as live:
            while True:
                live.update(_table())
                time.sleep(max(0.5, float(getattr(args, "interval", 2.0))))
        return 0

    print(f"{s.corpus}: status={s.status} landed={s.landed}/{s.rows} failed={s.failed} "
          f"cost=${s.cost_usd:.2f} fallback_used={s.fallback_used} "
          f"taxicab_reharvest={s.taxicab_reharvest} events={s.events} "
          f"batch_csvs={s.batch_csvs} live_html={s.live_html}")
    return 0


def cmd_bestof(args, cfg: GoldieConfig) -> int:
    from .maintenance import best_of_runs

    out_csv = Path(args.out) if args.out else None
    out_json = Path(args.report) if args.report else None
    rep = best_of_runs(
        [Path(r) for r in args.run],
        out_csv=out_csv,
        out_json=out_json,
        probe_empty_live=bool(getattr(args, "probe_empty_live", False)),
        probe_ca_live=bool(getattr(args, "probe_ca_live", False)),
        refresh_page_transforms=bool(getattr(args, "refresh_page_transforms", False)),
    )
    status = rep.get("final_status") or {}
    print(
        f"bestof: rows={rep['rows']} true={status.get('true', 0)} "
        f"false={status.get('false', 0)} changed_from_latest={rep['changed_from_latest_count']}"
    )
    if out_csv:
        print(f"  csv={out_csv}")
    if out_json:
        print(f"  report={out_json}")
    return 0


def cmd_spike(args, cfg: GoldieConfig) -> int:
    import json
    import os

    from .config import ConfigError
    from .rundir import RunDir, utc_stamp
    from .schema import extraction_json_schema
    from .spike.browserbase_fetch import run_spike
    from .transforms._source import src as tx

    if args.spike_kind != "browserbase-fetch":
        return _not_yet(f"spike {args.spike_kind}")

    dois: list[str] = []
    if getattr(args, "source", None):
        from .io import read_source_rows
        dois = [(r.get("DOI") or "").strip() for r in read_source_rows(Path(args.source))][: args.sample_size]
    if not dois:
        raise ConfigError("spike needs --source <corpus.csv> to pick DOI.org-resolved URLs")

    mode = getattr(args, "mode", "both")
    evidence_format = "markdown" if getattr(args, "evidence_format", "html") == "markdown" else "html"

    def taxicab_fetch(doi: str):
        html, _resolved, _err = tx.fetch_html(doi)
        return html

    def browserbase_fetch(doi: str):
        # Mode 1 — raw/markdown evidence (NO JS). Verify the SDK call vs live docs.
        from browserbase import Browserbase  # lazy; needs BROWSERBASE_API_KEY
        bb = Browserbase(api_key=os.environ["BROWSERBASE_API_KEY"])
        fmt = "markdown" if evidence_format == "markdown" else "raw"
        return bb.fetch_api.create(
            url=f"https://doi.org/{doi}", format=fmt, proxies=True, allow_redirects=True,
        ).content

    def browserbase_extract(doi: str):
        # Mode 2 — structured JSON extraction with the Goldie ExtractionOut schema (NO JS;
        # static-HTML fields only). Verify exact kwarg names against the live SDK before relying
        # on this — the spike is report-only and is NOT mixed into the production pilot.
        from browserbase import Browserbase  # lazy; needs BROWSERBASE_API_KEY
        bb = Browserbase(api_key=os.environ["BROWSERBASE_API_KEY"])
        resp = bb.fetch_api.create(
            url=f"https://doi.org/{doi}", format="json", schema=extraction_json_schema(),
            proxies=True, allow_redirects=True,
        )
        data = getattr(resp, "content", None)
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except json.JSONDecodeError:
                data = None
        return data if isinstance(data, dict) else None

    raw_fn = browserbase_fetch if mode in ("raw", "both") else None
    extract_fn = browserbase_extract if mode in ("json", "both") else None
    report = run_spike(dois, taxicab_fetch=taxicab_fetch, browserbase_fetch=raw_fn,
                       browserbase_extract=extract_fn, evidence_format=evidence_format)

    rd = RunDir.create(f"spike-browserbase-{utc_stamp()}")
    out = rd.root / "spike-report.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    ev = report.get("summary", {}).get("recommendation", "—") if "summary" in report else "—"
    sx = report.get("structured_summary", {}).get("recommendation", "—") if "structured_summary" in report else "—"
    print(f"spike (mode={mode}, format={evidence_format}, n={len(dois)}, js_execution=false)\n"
          f"  evidence:   {ev}\n  structured: {sx}\n  → {out}")
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
    sp.add_argument("--holdout-size", type=int, default=0, help="optional sealed holdout carve-out size")
    sp.add_argument("--force", action="store_true", help="discard existing output + partial sample state")

    sp = sub.add_parser("prepare", help="Sample random DOI source only for a named operator run")
    sp.add_argument("--count", type=int, required=True, help="number of random DOIs to sample")
    sp.add_argument("--name", required=True, help="safe run name prefix, e.g. goldie-10k")
    sp.add_argument("--out", default=None, help="optional explicit source.csv output path")
    sp.add_argument("--gold", default=DEFAULT_GOLD_CSV, help="existing gold CSV to dedup against")
    sp.add_argument("--holdout-size", type=int, default=0, help="optional sealed holdout carve-out size")
    sp.add_argument("--force", action="store_true", help="discard existing output + partial sample state")

    sp = sub.add_parser("random", help="Sample random DOIs, run cached+fallback extraction, and write report")
    sp.add_argument("--count", type=int, required=True, help="number of random DOIs to sample")
    sp.add_argument("--name", required=True, help="safe run name prefix, e.g. goldie-random-100")
    sp.add_argument("--out", default=None, help="optional explicit source.csv output path")
    sp.add_argument("--gold", default=DEFAULT_GOLD_CSV, help="existing gold CSV to dedup against")
    sp.add_argument("--holdout-size", type=int, default=0, help="optional sealed holdout carve-out size")
    sp.add_argument("--force", action="store_true", help="discard existing output + partial sample state")
    sp.add_argument("--tier", default="cached", choices=["cached", "cloud", "local_cdp"],
                    help="entry tier (default: cached)")
    sp.add_argument("--fallback-tier", default=None, choices=list(FALLBACK_TIERS),
                    help="tier-2 fallback (default: cloud)")
    sp.add_argument("--no-fallback", action="store_true",
                    help="backward-compatible alias for --fallback-tier none")
    sp.add_argument("--concurrency", type=int, default=None)
    sp.add_argument("--batch-concurrency", type=int, default=None)
    sp.add_argument("--max-cost-usd", type=float, default=None,
                    help="optional safety stop; omit for quality-first runs")
    sp.add_argument("--prompt", default=None)
    sp.add_argument("--model", default=None)
    sp.add_argument("--holdout", default=None)
    sp.add_argument("--report-out", default=None, help="operator Markdown output path")

    sp = sub.add_parser("split", help="Split a corpus CSV into fixed-size batches")
    sp.add_argument("--source", required=True)
    sp.add_argument("--batch-size", type=int, default=100)

    sp = sub.add_parser("extract", help="Run a single extraction tier over a corpus/batch")
    sp.add_argument("--source", required=True)
    sp.add_argument("--tier", required=True, choices=["cached", "cloud", "local_cdp"],
                    help="cached = Taxicab cached HTML + Taxicab live reharvest; "
                         "cloud/local_cdp = rendered-browser backends")
    sp.add_argument("--batch", type=int, default=None)
    sp.add_argument("--prompt", default=None)
    sp.add_argument("--model", default=None)

    sp = sub.add_parser("run", help="Full tiered pipeline over a corpus")
    sp.add_argument("--source", required=True)
    sp.add_argument("--corpus", required=True)
    sp.add_argument("--tier", default="cached", choices=["cached", "cloud", "local_cdp"],
                    help="entry tier (default: cached = Taxicab cached HTML + Taxicab "
                         "live reharvest before rendered-browser fallback)")
    sp.add_argument("--concurrency", type=int, default=None)
    sp.add_argument("--batch-concurrency", type=int, default=None)
    sp.add_argument("--max-cost-usd", type=float, default=None,
                    help="optional safety stop; omit for quality-first runs")
    sp.add_argument("--prompt", default=None)
    sp.add_argument("--holdout", default=None)
    sp.add_argument("--resume", default=None,
                    help="existing run dir to reopen; per-batch checkpoints skip landed DOIs")
    sp.add_argument("--monitor", action="store_true")
    sp.add_argument("--fallback-tier", default=None, choices=list(FALLBACK_TIERS),
                    help="tier-2 fallback over empty-field rows (default: cloud — the live "
                         "browser-use Cloud rendered-browser quality tier). 'none' disables tier-2.")
    sp.add_argument("--no-fallback", action="store_true",
                    help="backward-compatible alias for --fallback-tier none")

    sp = sub.add_parser("resume", help="Resume an existing run directory from its manifest/checkpoints")
    sp.add_argument("--run", required=True, help="existing run directory")
    sp.add_argument("--tier", default=None, choices=["cached", "cloud", "local_cdp"],
                    help="override manifest tier")
    sp.add_argument("--fallback-tier", default=None, choices=list(FALLBACK_TIERS),
                    help="override manifest fallback tier")
    sp.add_argument("--concurrency", type=int, default=None)
    sp.add_argument("--batch-concurrency", type=int, default=None)
    sp.add_argument("--max-cost-usd", type=float, default=None,
                    help="optional safety stop; omit for quality-first runs")
    sp.add_argument("--prompt", default=None)
    sp.add_argument("--model", default=None)
    sp.add_argument("--holdout", default=None)

    sp = sub.add_parser("report", help="(Re)build a run report; scored when --holdout is supplied")
    sp.add_argument("--run", required=True)
    sp.add_argument("--holdout", default=None)
    sp.add_argument("--operator", action="store_true",
                    help="also write a GitHub-rendered Markdown operator report")
    sp.add_argument("--out", default=None, help="operator Markdown output path")

    sp = sub.add_parser("monitor", help="Read-only TUI over a run directory")
    sp.add_argument("--run", default=None)
    sp.add_argument("--watch", action="store_true", help="live terminal TUI until interrupted")
    sp.add_argument("--interval", type=float, default=2.0, help="watch refresh interval seconds")

    sp = sub.add_parser("bestof", help="Merge strongest evidence across completed run dirs")
    sp.add_argument("--run", action="append", required=True,
                    help="completed run dir or merged CSV; pass at least twice")
    sp.add_argument("--out", required=True, help="derived best-of CSV output path")
    sp.add_argument("--report", default=None, help="optional best-of report JSON output path")
    sp.add_argument("--probe-empty-live", action="store_true",
                    help="resolve unresolved empty rows through DOI.org/publisher pages and "
                         "record live structural labels such as bot-check or router-only")
    sp.add_argument("--probe-ca-live", action="store_true",
                    help="resolve CA-only rows through DOI.org/publisher pages and record "
                         "whether live evidence has a corresponding-author marker candidate")
    sp.add_argument("--refresh-page-transforms", action="store_true",
                    help="run additive deterministic page transforms over Taxicab HTML for "
                         "rows still missing authors/rases/CA/abstract/PDF")

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
                          help="Browserbase Fetch spike vs Taxicab — raw evidence and/or "
                               "structured JSON extraction (report-only; NO JS execution)")
    bb.add_argument("--sample-size", type=int, default=100)
    bb.add_argument("--source", default=None)
    bb.add_argument("--mode", choices=["raw", "json", "both"], default="both",
                    help="raw = HTML/markdown evidence vs Taxicab; json = structured Fetch "
                         "Extract (format=json + Goldie schema); both (default)")
    bb.add_argument("--evidence-format", choices=["html", "markdown"], default="html",
                    help="raw-evidence fetch format (default: html)")

    return p


def _dispatch(args: argparse.Namespace, cfg: GoldieConfig) -> int:
    cmd = args.command
    if cmd == "extract":
        validate_credentials(tier=args.tier)
        return cmd_extract(args, cfg)
    if cmd == "run":
        validate_credentials(tier=args.tier)
        # The tier-2 fallback needs its own credentials: e.g. --tier cached --fallback-tier
        # cloud requires BOTH ANTHROPIC_API_KEY (cached) and BROWSER_USE_API_KEY (cloud).
        fb_tier = _resolve_fallback_tier(args, default=DEFAULT_FALLBACK_TIER)
        if fb_tier != "none":
            validate_credentials(tier=fb_tier)
        return cmd_run(args, cfg)
    if cmd == "random":
        validate_credentials(tier=args.tier)
        fb_tier = _resolve_fallback_tier(args, default=DEFAULT_FALLBACK_TIER)
        if fb_tier != "none":
            validate_credentials(tier=fb_tier)
        return cmd_random(args, cfg)
    if cmd == "resume":
        run_args = _resume_run_args(args)
        validate_credentials(tier=run_args.tier)
        fb_tier = _resolve_fallback_tier(run_args, default=DEFAULT_FALLBACK_TIER)
        if fb_tier != "none":
            validate_credentials(tier=fb_tier)
        return cmd_run(run_args, cfg)
    if cmd == "split":
        return cmd_split(args, cfg)
    if cmd == "report":
        return cmd_report(args, cfg)
    if cmd == "sample":
        return cmd_sample(args, cfg)
    if cmd == "prepare":
        return cmd_prepare(args, cfg)
    if cmd == "clean":
        return cmd_clean(args, cfg)
    if cmd == "migrate":
        return cmd_migrate(args, cfg)
    if cmd == "monitor":
        return cmd_monitor(args, cfg)
    if cmd == "bestof":
        return cmd_bestof(args, cfg)
    if cmd == "spike":
        validate_credentials(spike=args.spike_kind)
        return cmd_spike(args, cfg)
    return _not_yet(cmd)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.command_argv = ["goldie", *(argv if argv is not None else sys.argv[1:])]
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
