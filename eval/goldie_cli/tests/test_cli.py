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


def test_spike_subcommand_parsing():
    p = build_parser()
    ns = p.parse_args(["spike", "browserbase-fetch", "--sample-size", "50"])
    assert ns.command == "spike"
    assert ns.spike_kind == "browserbase-fetch"
    assert ns.sample_size == 50


def test_missing_command_errors():
    p = build_parser()
    with pytest.raises(SystemExit) as ei:
        p.parse_args([])
    assert ei.value.code != 0
