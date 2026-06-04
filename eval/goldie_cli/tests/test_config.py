from __future__ import annotations

import pytest

from goldie_cli import config


def test_key_present(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-xxx")
    monkeypatch.delenv("BROWSER_USE_API_KEY", raising=False)
    assert config.key_present("ANTHROPIC_API_KEY") is True
    assert config.key_present("BROWSER_USE_API_KEY") is False


def test_require_keys_exits_2_when_missing(monkeypatch):
    monkeypatch.delenv("BROWSERBASE_API_KEY", raising=False)
    with pytest.raises(SystemExit) as ei:
        config.require_keys(["BROWSERBASE_API_KEY"])
    assert ei.value.code == 2


def test_validate_credentials_per_tier(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.delenv("BROWSER_USE_API_KEY", raising=False)
    config.validate_credentials(tier="cached")  # ok
    with pytest.raises(SystemExit) as ei:
        config.validate_credentials(tier="cloud")  # needs BROWSER_USE_API_KEY
    assert ei.value.code == 2


def test_credential_presence_returns_bools_only(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "secret-value")
    pres = config.credential_presence()
    assert pres["ANTHROPIC_API_KEY"] is True
    assert all(isinstance(v, bool) for v in pres.values())
    assert "secret-value" not in str(pres)


@pytest.mark.parametrize("bad", [
    "https://api.openalex.org/works/W1",
    "https://api.unpaywall.org/v2/10.1/x",
    "https://www.ebi.ac.uk/europepmc/webservices/rest/x",
    "https://api.crossref.org/works/10.1/x",
])
def test_forbidden_evidence_hosts_rejected(bad):
    with pytest.raises(SystemExit) as ei:
        config.assert_allowed_evidence_host(bad)
    assert ei.value.code == 2


def test_allowed_publisher_host_passes():
    config.assert_allowed_evidence_host("https://www.sciencedirect.com/science/article/pii/X")
    config.assert_allowed_evidence_host("https://doi.org/10.1/x")
