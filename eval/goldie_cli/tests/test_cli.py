from __future__ import annotations

import pytest

from goldie_cli.cli import build_parser


def test_parser_builds_and_lists_commands():
    p = build_parser()
    # --help exits 0
    with pytest.raises(SystemExit) as ei:
        p.parse_args(["--help"])
    assert ei.value.code == 0


def test_version_flag():
    p = build_parser()
    with pytest.raises(SystemExit) as ei:
        p.parse_args(["--version"])
    assert ei.value.code == 0


def test_subcommand_parsing():
    p = build_parser()
    ns = p.parse_args(["sample", "--target", "100", "--out", "c.csv"])
    assert ns.command == "sample"
    assert ns.target == 100
    assert ns.holdout_size == 0
    assert ns.force is False


def test_sample_force_parsing():
    p = build_parser()
    ns = p.parse_args(["sample", "--target", "100", "--out", "c.csv", "--force"])
    assert ns.force is True


def test_run_resume_parsing():
    p = build_parser()
    ns = p.parse_args([
        "run",
        "--source", "source.csv",
        "--corpus", "goldie",
        "--resume", "runs/goldie-existing",
    ])
    assert ns.resume == "runs/goldie-existing"


def test_spike_subcommand_parsing():
    p = build_parser()
    ns = p.parse_args(["spike", "browserbase-fetch", "--sample-size", "50"])
    assert ns.command == "spike"
    assert ns.spike_kind == "browserbase-fetch"
    assert ns.sample_size == 50


def test_monitor_watch_parsing():
    p = build_parser()
    ns = p.parse_args(["monitor", "--run", "runs/x", "--watch", "--interval", "0.5"])
    assert ns.command == "monitor"
    assert ns.watch is True
    assert ns.interval == 0.5


def test_bestof_parsing():
    p = build_parser()
    ns = p.parse_args([
        "bestof",
        "--run", "runs/a",
        "--run", "runs/b",
        "--out", "runs/best.csv",
        "--report", "runs/best.json",
        "--probe-empty-live",
        "--probe-ca-live",
        "--refresh-page-transforms",
    ])
    assert ns.command == "bestof"
    assert ns.run == ["runs/a", "runs/b"]
    assert ns.out == "runs/best.csv"
    assert ns.report == "runs/best.json"
    assert ns.probe_empty_live is True
    assert ns.probe_ca_live is True
    assert ns.refresh_page_transforms is True


def test_missing_command_errors():
    p = build_parser()
    with pytest.raises(SystemExit) as ei:
        p.parse_args([])
    assert ei.value.code != 0
