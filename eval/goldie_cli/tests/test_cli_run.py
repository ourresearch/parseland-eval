from __future__ import annotations

import argparse
import csv
import json

import goldie_cli.backends as backends_mod
import goldie_cli.rundir as rundir_mod
from goldie_cli import cli
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
