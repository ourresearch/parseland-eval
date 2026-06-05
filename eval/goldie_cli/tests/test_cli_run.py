from __future__ import annotations

import argparse
import csv
import json

import goldie_cli.backends as backends_mod
import goldie_cli.rundir as rundir_mod
import goldie_cli.sample as sample_mod
import goldie_cli.tiers as tiers_mod
from goldie_cli import cli
from goldie_cli.backends.base import ExtractionResult
from goldie_cli.backends.stub import StubBackend
from goldie_cli.config import GoldieConfig
from goldie_cli.io import chunk_batches


def test_chunk_batches():
    rows = [{"No": str(i)} for i in range(1, 6)]
    b = chunk_batches(rows, 2)
    assert [no for no, _ in b] == [1, 2, 3]
    assert [len(r) for _, r in b] == [2, 2, 1]


def _corpus(tmp_path, n=2):
    src = tmp_path / "c.csv"
    lines = ["No,DOI,Link"] + [f"{i},10.1/{i},https://doi.org/10.1/{i}" for i in range(1, n + 1)]
    src.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return src


def _prompt(tmp_path):
    p = tmp_path / "p.md"
    p.write_text("---\nversion: vT\n---\n## System prompt\n```\nrules\n```\n", encoding="utf-8")
    return p


def test_cmd_run_offline_with_stub(tmp_path, monkeypatch):
    monkeypatch.setattr(rundir_mod, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(backends_mod, "get_backend", lambda name, **k: StubBackend())
    args = argparse.Namespace(
        source=str(_corpus(tmp_path, 3)), corpus="smoke", tier="stub", model=None,
        prompt=str(_prompt(tmp_path)), concurrency=2, batch_concurrency=2,
        max_cost_usd=None, batch=None,
    )
    rc = cli.cmd_run(args, GoldieConfig())
    assert rc == 0
    merged = list((tmp_path / "runs").glob("smoke-*/merged.csv"))
    assert merged, "merged.csv not produced"
    with merged[0].open() as f:
        got = list(csv.DictReader(f))
    assert [int(r["No"]) for r in got] == [1, 2, 3]
    assert all(r["Status"] == "TRUE" for r in got)


def test_cmd_split_returns_zero(tmp_path):
    args = argparse.Namespace(source=str(_corpus(tmp_path, 4)), batch_size=2)
    assert cli.cmd_split(args, GoldieConfig()) == 0


def test_cmd_sample_writes_full_schema_and_partial(tmp_path, monkeypatch):
    def fake_sample_dois(target, *, exclude=frozenset(), accepted=None, on_accept=None, **kwargs):
        out = list(accepted or [])
        for doi in ["10.1/a", "10.1/b", "10.1/c"]:
            if len(out) >= target:
                break
            if doi in out or doi in exclude:
                continue
            out.append(doi)
            if on_accept:
                on_accept(doi)
        return out[:target]

    monkeypatch.setattr(sample_mod, "sample_dois", fake_sample_dois)
    out = tmp_path / "source.csv"
    args = argparse.Namespace(
        target=2, out=str(out), gold=None, holdout_size=0, force=False,
    )

    assert cli.cmd_sample(args, GoldieConfig()) == 0
    rows = list(csv.DictReader(out.open()))
    assert len(rows) == 2
    assert rows[0]["DOI"] == "10.1/a"
    assert rows[0]["Authors"] == ""
    assert (tmp_path / "source.csv.partial.jsonl").exists()


def test_cmd_sample_rejects_holdout_that_consumes_target(tmp_path):
    args = argparse.Namespace(
        target=100, out=str(tmp_path / "source.csv"), gold=None, holdout_size=100, force=False,
    )

    try:
        cli.cmd_sample(args, GoldieConfig())
    except SystemExit as e:
        assert e.code == 2
    else:
        raise AssertionError("expected holdout-size guard")


def test_cmd_run_cascade_no_fallback_writes_all_artifacts(tmp_path, monkeypatch):
    monkeypatch.setattr(rundir_mod, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(backends_mod, "get_backend", lambda name, **k: StubBackend())
    args = argparse.Namespace(
        source=str(_corpus(tmp_path, 3)), corpus="cas", tier="stub", model=None,
        prompt=str(_prompt(tmp_path)), concurrency=2, batch_concurrency=2,
        max_cost_usd=None, batch=None, no_fallback=True, holdout=None,
    )
    assert cli.cmd_run(args, GoldieConfig()) == 0
    run_root = next((tmp_path / "runs").glob("cas-*"))
    # All required artifacts present.
    assert (run_root / "merged.csv").exists()
    assert (run_root / "report.json").exists()
    assert (run_root / "manifest.json").exists()
    assert (run_root / "checkpoints").is_dir()
    assert (run_root / "failures").is_dir()
    rep = json.loads((run_root / "report.json").read_text())
    assert rep["type"] == "summary" and "cost_usd" in rep
    man = json.loads((run_root / "manifest.json").read_text())
    assert man["fallback"]["enabled"] is False
    assert "cost_usd" in man


def test_cmd_run_resume_reuses_existing_run_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(backends_mod, "get_backend", lambda name, **k: StubBackend())
    run_root = tmp_path / "existing-run"
    run_root.mkdir()
    args = argparse.Namespace(
        source=str(_corpus(tmp_path, 2)), corpus="resume", tier="stub", model=None,
        prompt=str(_prompt(tmp_path)), concurrency=2, batch_concurrency=1,
        max_cost_usd=None, batch=None, no_fallback=True, fallback_tier=None,
        holdout=None, resume=str(run_root),
    )

    assert cli.cmd_run(args, GoldieConfig()) == 0
    assert (run_root / "merged.csv").exists()
    assert (run_root / "manifest.json").exists()
    assert list(tmp_path.glob("resume-*")) == []


def test_cmd_run_cascade_with_fallback_stub(tmp_path, monkeypatch):
    # Both primary + fallback resolve to a stub (offline). Primary fills rows, so the
    # fallback path is wired but no-ops; the run still completes with all artifacts.
    monkeypatch.setattr(rundir_mod, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(backends_mod, "get_backend", lambda name, **k: StubBackend())
    args = argparse.Namespace(
        source=str(_corpus(tmp_path, 2)), corpus="casfb", tier="stub", model=None,
        prompt=str(_prompt(tmp_path)), concurrency=2, batch_concurrency=2,
        max_cost_usd=None, batch=None, no_fallback=False, holdout=None,
    )
    assert cli.cmd_run(args, GoldieConfig()) == 0
    run_root = next((tmp_path / "runs").glob("casfb-*"))
    man = json.loads((run_root / "manifest.json").read_text())
    assert man["fallback"]["enabled"] is True


def test_cmd_run_manifest_distinguishes_tier1_failure_from_final_status(tmp_path, monkeypatch):
    monkeypatch.setattr(rundir_mod, "RUNS_DIR", tmp_path / "runs")

    def get_backend(name, **kwargs):
        if name == "stub":
            return StubBackend(
                responder=lambda doi, link: ExtractionResult(
                    extraction=None, error="tier1 miss", meta={"no_retry": True},
                )
            )
        if name == "cloud":
            return StubBackend(default_extraction={
                "authors": [{"name": "Live Author", "rasses": "Live University"}],
                "abstract": "live abstract",
                "pdf_url": "",
            })
        raise AssertionError(name)

    monkeypatch.setattr(backends_mod, "get_backend", get_backend)
    args = argparse.Namespace(
        source=str(_corpus(tmp_path, 1)), corpus="finalstatus", tier="stub", model=None,
        prompt=str(_prompt(tmp_path)), concurrency=1, batch_concurrency=1,
        max_cost_usd=None, batch=None, no_fallback=False, fallback_tier="cloud",
        holdout=None,
    )

    assert cli.cmd_run(args, GoldieConfig()) == 0
    run_root = next((tmp_path / "runs").glob("finalstatus-*"))
    man = json.loads((run_root / "manifest.json").read_text())
    rows = list(csv.DictReader((run_root / "merged.csv").open()))

    assert man["tier1_failed"] == 1
    assert man["failed"] == 0
    assert man["final_status"] == {"true": 1, "false": 0, "other": 0}
    assert rows[0]["Status"] == "TRUE"
    assert "Live Author" in rows[0]["Authors"]


def test_cmd_run_manifest_marks_fallback_error_if_tier2_crashes(tmp_path, monkeypatch):
    monkeypatch.setattr(rundir_mod, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(backends_mod, "get_backend", lambda name, **kwargs: StubBackend())

    async def boom(*args, **kwargs):
        raise RuntimeError("tier2 crashed")

    monkeypatch.setattr(tiers_mod, "run_with_fallback", boom)
    args = argparse.Namespace(
        source=str(_corpus(tmp_path, 1)), corpus="fallbackcrash", tier="stub", model=None,
        prompt=str(_prompt(tmp_path)), concurrency=1, batch_concurrency=1,
        max_cost_usd=None, batch=None, no_fallback=False, fallback_tier="cloud",
        holdout=None,
    )

    try:
        cli.cmd_run(args, GoldieConfig())
    except RuntimeError as e:
        assert str(e) == "tier2 crashed"
    else:
        raise AssertionError("expected fallback crash")

    run_root = next((tmp_path / "runs").glob("fallbackcrash-*"))
    man = json.loads((run_root / "manifest.json").read_text())
    assert man["status"] == "fallback_error"
    assert man["fallback"]["status"] == "fallback_error"
    assert "tier2 crashed" in man["fallback"]["error"]
    assert man["tier1_landed"] == 1


def test_cmd_run_manifest_marks_report_error_if_report_write_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(rundir_mod, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(backends_mod, "get_backend", lambda name, **kwargs: StubBackend())

    def boom(*args, **kwargs):
        raise RuntimeError("report crashed")

    monkeypatch.setattr(cli, "_write_run_report", boom)
    args = argparse.Namespace(
        source=str(_corpus(tmp_path, 1)), corpus="reportcrash", tier="stub", model=None,
        prompt=str(_prompt(tmp_path)), concurrency=1, batch_concurrency=1,
        max_cost_usd=None, batch=None, no_fallback=False, fallback_tier="cloud",
        holdout=None,
    )

    try:
        cli.cmd_run(args, GoldieConfig())
    except RuntimeError as e:
        assert str(e) == "report crashed"
    else:
        raise AssertionError("expected report crash")

    run_root = next((tmp_path / "runs").glob("reportcrash-*"))
    man = json.loads((run_root / "manifest.json").read_text())
    assert man["status"] == "report_error"
    assert "report crashed" in man["report_error"]
    assert man["fallback"]["status"] == "complete"


def test_cmd_report_without_holdout_rebuilds_summary(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(rundir_mod, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(backends_mod, "get_backend", lambda name, **k: StubBackend())
    args = argparse.Namespace(
        source=str(_corpus(tmp_path, 2)), corpus="rep", tier="stub", model=None,
        prompt=str(_prompt(tmp_path)), concurrency=2, batch_concurrency=1,
        max_cost_usd=None, batch=None, no_fallback=True, fallback_tier=None, holdout=None,
    )
    assert cli.cmd_run(args, GoldieConfig()) == 0
    run_root = next((tmp_path / "runs").glob("rep-*"))

    rc = cli.cmd_report(argparse.Namespace(run=str(run_root), holdout=None), GoldieConfig())

    assert rc == 0
    out = capsys.readouterr().out
    assert "summary:" in out and "authors" in out
    rep = json.loads((run_root / "report.json").read_text())
    assert rep["type"] == "summary"
    assert "field_presence" in rep
