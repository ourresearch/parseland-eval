from __future__ import annotations

import asyncio

import pytest

from goldie_cli.backends import extract_with_retries, get_backend
from goldie_cli.backends.base import ExtractionResult, RetryPolicy
from goldie_cli.backends.browser_use_cloud import to_cloud_model
from goldie_cli.backends.stub import StubBackend


async def _noop_sleep(_):
    return None


def _run(coro):
    return asyncio.run(coro)


@pytest.mark.parametrize("api_id,cloud", [
    ("claude-sonnet-4-6", "claude-sonnet-4.6"),
    ("claude-opus-4-7", "claude-opus-4.7"),
    ("claude-haiku-4-5", "claude-haiku-4.5"),
    ("claude-sonnet-4.6", "claude-sonnet-4.6"),  # already dotted
    ("gpt-5.4-mini", "gpt-5.4-mini"),            # non-claude untouched
])
def test_to_cloud_model(api_id, cloud):
    assert to_cloud_model(api_id) == cloud


def test_get_backend_stub():
    assert isinstance(get_backend("stub"), StubBackend)
    with pytest.raises(ValueError):
        get_backend("nope")


def test_retry_success_first_try():
    backend = StubBackend()
    sem = asyncio.Semaphore(1)
    res = _run(extract_with_retries(
        backend, doi="10.1/a", link="L", html=None, schema={}, prompt="p",
        policy=RetryPolicy(retry_cap=3), sem=sem, sleep=_noop_sleep,
    ))
    assert res.extraction is not None
    assert res.meta["retries"] == 0
    assert backend.calls == 1


def test_bot_check_retries_then_gives_up():
    def responder(doi, link):
        return ExtractionResult(extraction={"authors": [], "has_bot_check": True}, cost_usd=0.1)
    backend = StubBackend(responder=responder)
    sem = asyncio.Semaphore(1)
    res = _run(extract_with_retries(
        backend, doi="d", link="L", html=None, schema={}, prompt="p",
        policy=RetryPolicy(retry_cap=2), sem=sem, sleep=_noop_sleep,
    ))
    # bot-check on every attempt → 3 calls (initial + 2 retries), returns the row.
    assert backend.calls == 3
    assert res.extraction.get("has_bot_check") is True


def test_exception_retries_then_fails():
    state = {"n": 0}

    def responder(doi, link):
        state["n"] += 1
        raise RuntimeError("boom")

    backend = StubBackend(responder=responder)
    sem = asyncio.Semaphore(1)
    res = _run(extract_with_retries(
        backend, doi="d", link="L", html=None, schema={}, prompt="p",
        policy=RetryPolicy(retry_cap=2), sem=sem, sleep=_noop_sleep,
    ))
    assert res.extraction is None
    assert "boom" in res.error
    assert state["n"] == 3  # initial + 2 retries


def test_recovers_after_transient_error():
    state = {"n": 0}

    def responder(doi, link):
        state["n"] += 1
        if state["n"] == 1:
            raise RuntimeError("transient")
        return ExtractionResult(extraction={"authors": [{"name": "A"}]}, cost_usd=0.2)

    backend = StubBackend(responder=responder)
    sem = asyncio.Semaphore(1)
    res = _run(extract_with_retries(
        backend, doi="d", link="L", html=None, schema={}, prompt="p",
        policy=RetryPolicy(retry_cap=3), sem=sem, sleep=_noop_sleep,
    ))
    assert res.extraction["authors"][0]["name"] == "A"
    assert res.cost_usd == 0.2
    assert res.meta["retries"] == 1
