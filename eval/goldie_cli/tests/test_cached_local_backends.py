from __future__ import annotations

import asyncio

import pytest

from goldie_cli.backends import get_backend
from goldie_cli.backends.cached_html import CachedHtmlBackend, _caps_to_lower
from goldie_cli.backends.local_cdp import LocalCdpBackend, _build_task
from goldie_cli.transforms._source import src as tx


def _run(coro):
    return asyncio.run(coro)


# ---- cached_html (offline, bridged functions monkeypatched) ----------------

def test_caps_to_lower():
    caps = {"Authors": [{"name": "A", "rasses": "MIT", "corresponding_author": True}],
            "Abstract": "abs", "PDF URL": "http://x/y.pdf"}
    low = _caps_to_lower(caps)
    assert low == {"authors": [{"name": "A", "rasses": "MIT", "corresponding_author": True}],
                   "abstract": "abs", "pdf_url": "http://x/y.pdf", "has_bot_check": False}


def test_cached_missing_key_raises(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        CachedHtmlBackend()


def test_cached_extract_success(monkeypatch):
    monkeypatch.setattr(tx, "fetch_html", lambda doi: ("<html>cache</html>", "https://resolved", None))
    monkeypatch.setattr(tx, "extract_via_claude", lambda *a, **k: (
        {"Authors": [{"name": "Jane Roe", "rasses": "MIT", "corresponding_author": False}],
         "Abstract": "an abstract", "PDF URL": "https://pub/x.pdf"},
        {"input_tokens": 10, "output_tokens": 5},
    ))
    monkeypatch.setattr(tx, "_approx_cost", lambda usage, model: 0.0123)
    be = CachedHtmlBackend(api_key="x")
    res = _run(be.extract("10.1/a", "https://doi.org/10.1/a", html=None, schema={}, prompt="p"))
    assert res.extraction["abstract"] == "an abstract"
    assert res.extraction["authors"][0]["rasses"] == "MIT"
    assert res.extraction["has_bot_check"] is False
    assert res.raw_html == "<html>cache</html>"   # page evidence preserved for transforms
    assert res.cost_usd == 0.0123
    assert res.meta["resolved_url"] == "https://resolved"


def test_cached_fetch_failure(monkeypatch):
    monkeypatch.setattr(tx, "fetch_html", lambda doi: (None, None, "taxicab: no html"))
    be = CachedHtmlBackend(api_key="x")
    res = _run(be.extract("10.1/a", "L", html=None, schema={}, prompt="p"))
    assert res.extraction is None
    assert "no html" in res.error


def test_cached_claude_failure_keeps_html(monkeypatch):
    monkeypatch.setattr(tx, "fetch_html", lambda doi: ("<html/>", "u", None))
    monkeypatch.setattr(tx, "extract_via_claude", lambda *a, **k: (None, {"error": "claude boom"}))
    be = CachedHtmlBackend(api_key="x")
    res = _run(be.extract("10.1/a", "L", html=None, schema={}, prompt="p"))
    assert res.extraction is None
    assert res.error == "claude boom"
    assert res.raw_html == "<html/>"


# ---- local_cdp (construction only; live path needs a CDP Chrome) -----------

def test_local_cdp_missing_key_raises(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        LocalCdpBackend()


def test_local_cdp_constructs_and_factory(monkeypatch):
    be = LocalCdpBackend(api_key="x", cdp_url="http://localhost:9222")
    assert be.name == "local_cdp"
    assert isinstance(get_backend("local_cdp", api_key="x"), LocalCdpBackend)


def test_local_cdp_build_task_embeds_doi_url():
    t = _build_task("RULES", "10.1/a", "https://doi.org/10.1/a")
    assert "RULES" in t and "10.1/a" in t and "https://doi.org/10.1/a" in t
