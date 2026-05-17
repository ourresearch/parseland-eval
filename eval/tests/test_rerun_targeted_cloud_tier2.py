"""Unit tests for the cloud_sessions Tier 2 backend in rerun_targeted.py.

No pytest-asyncio dependency — wraps coroutines in `asyncio.run()`.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "eval" / "scripts"))
sys.path.insert(0, str(REPO_ROOT / "eval" / "browser-use" / "runtime"))

from rerun_targeted import (  # noqa: E402
    _preflight_cloud_api,
    _tier2_cloud_sessions,
)


def _make_mock_client(
    *,
    output: dict | None = None,
    cost_usd: float | None = 0.07,
    raises: Exception | None = None,
):
    """Build a MagicMock that quacks like CloudClient."""
    client = MagicMock()
    if raises is not None:
        client.create_session = AsyncMock(side_effect=raises)
        client.wait_for_session = AsyncMock(side_effect=raises)
    else:
        client.create_session = AsyncMock(return_value="sess_test_id")
        payload: dict = {"status": "stopped"}
        if output is not None:
            payload["output"] = output
        if cost_usd is not None:
            payload["totalCostUsd"] = str(cost_usd)
        client.wait_for_session = AsyncMock(return_value=payload)
    return client


def _run_tier2(
    *,
    doi: str = "10.1016/test",
    tier2_link: str = "https://example.com/x",
    cloud_client=None,
    tier2_prompt_body: str = "prompt body",
):
    return asyncio.run(_tier2_cloud_sessions(
        doi=doi,
        tier2_link=tier2_link,
        cloud_client=cloud_client,
        tier2_prompt_body=tier2_prompt_body,
    ))


@pytest.mark.unit
class TestTier2CloudSessionsHappy:
    def test_returns_tier2_cloud_when_authors_present(self) -> None:
        client = _make_mock_client(output={
            "authors": [
                {"name": "Alice Smith", "rasses": "MIT", "corresponding_author": True},
                {"name": "Bob Jones",   "rasses": "Stanford"},
            ],
            "abstract": "An abstract.",
            "pdf_url": "https://example.com/x.pdf",
            "has_bot_check": False,
        }, cost_usd=0.12)
        final_tier, extraction, status, cost, n_authors, has_bot, err = _run_tier2(
            doi="10.1016/test",
            tier2_link="https://www.sciencedirect.com/x",
            cloud_client=client,
        )
        assert final_tier == "tier2_cloud"
        assert n_authors == 2
        assert status == "ok"
        assert cost == pytest.approx(0.12)
        assert has_bot is False
        assert err is None
        assert extraction is not None
        assert extraction["authors"][0]["name"] == "Alice Smith"


@pytest.mark.unit
class TestTier2CloudSessionsBotCheck:
    def test_has_bot_check_with_empty_authors_tags_cloudflare(self) -> None:
        client = _make_mock_client(output={
            "authors": [],
            "has_bot_check": True,
        }, cost_usd=0.04)
        final_tier, extraction, status, cost, n_authors, has_bot, err = _run_tier2(
            doi="10.1016/cloudflare-blocked",
            tier2_link="https://www.sciencedirect.com/y",
            cloud_client=client,
        )
        assert final_tier == "none"
        assert status == "cloud_bot_check"
        assert has_bot is True
        assert err == "iter-R:cloudflare_blocked"
        assert n_authors == 0
        assert cost == pytest.approx(0.04)


@pytest.mark.unit
class TestTier2CloudSessionsNoAuthors:
    def test_extraction_with_no_authors_and_no_bot_check(self) -> None:
        client = _make_mock_client(output={
            "authors": [],
            "has_bot_check": False,
            "abstract": "something",
        }, cost_usd=0.05)
        final_tier, extraction, status, cost, n_authors, has_bot, err = _run_tier2(
            cloud_client=client,
        )
        assert final_tier == "none"
        assert status == "cloud_no_authors"
        assert has_bot is False
        assert err == "tier2_cloud_no_authors"

    def test_empty_output_returns_no_authors(self) -> None:
        client = _make_mock_client(output=None, cost_usd=0.01)
        final_tier, extraction, status, cost, n_authors, has_bot, err = _run_tier2(
            cloud_client=client,
        )
        assert final_tier == "none"
        assert status == "cloud_no_authors"


@pytest.mark.unit
class TestTier2CloudSessionsErrors:
    def test_runtime_error_from_non_2xx(self) -> None:
        client = _make_mock_client(raises=RuntimeError("create_session http 500: server boom"))
        final_tier, extraction, status, cost, n_authors, has_bot, err = _run_tier2(
            cloud_client=client,
        )
        assert final_tier == "none"
        assert status.startswith("cloud_error:")
        assert err is not None and "500" in err

    def test_timeout_error(self) -> None:
        client = _make_mock_client(raises=TimeoutError("session exceeded 1800s"))
        final_tier, extraction, status, cost, n_authors, has_bot, err = _run_tier2(
            cloud_client=client,
        )
        assert final_tier == "none"
        assert status == "cloud_timeout"
        assert err is not None and "1800" in err

    def test_generic_exception_returns_cloud_exception(self) -> None:
        class CustomBoom(Exception):
            pass
        client = _make_mock_client(raises=CustomBoom("weird"))
        final_tier, extraction, status, cost, n_authors, has_bot, err = _run_tier2(
            cloud_client=client,
        )
        assert final_tier == "none"
        assert status == "cloud_exception:CustomBoom"

    def test_none_client_returns_no_client(self) -> None:
        final_tier, extraction, status, cost, n_authors, has_bot, err = _run_tier2(
            cloud_client=None,
        )
        assert final_tier == "none"
        assert status == "no_client"
        assert err == "cloud_client_not_initialized"


@pytest.mark.unit
class TestPreflightCloudApi:
    def test_no_key_returns_false(self) -> None:
        ok, reason = _preflight_cloud_api(None)
        assert ok is False
        assert "BROWSER_USE_API_KEY" in reason

    def test_empty_key_returns_false(self) -> None:
        ok, reason = _preflight_cloud_api("")
        assert ok is False

    def test_auth_failure_returns_false(self) -> None:
        with patch("rerun_targeted.requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 401
            mock_resp.text = "Invalid API key"
            mock_get.return_value = mock_resp
            ok, reason = _preflight_cloud_api("bu_bad_key")
        assert ok is False
        assert "401" in reason

    def test_200_returns_ok(self) -> None:
        with patch("rerun_targeted.requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.text = "[]"
            mock_get.return_value = mock_resp
            ok, reason = _preflight_cloud_api("bu_good_key")
        assert ok is True
        assert reason == "ok"

    def test_404_405_pass_as_endpoint_responding(self) -> None:
        for status in (404, 405):
            with patch("rerun_targeted.requests.get") as mock_get:
                mock_resp = MagicMock()
                mock_resp.status_code = status
                mock_resp.text = ""
                mock_get.return_value = mock_resp
                ok, reason = _preflight_cloud_api("bu_good_key")
            assert ok is True, f"status={status} should pass"

    def test_network_error_returns_false(self) -> None:
        import requests as _requests
        with patch("rerun_targeted.requests.get") as mock_get:
            mock_get.side_effect = _requests.ConnectionError("dns down")
            ok, reason = _preflight_cloud_api("bu_key")
        assert ok is False
        assert "unreachable" in reason.lower()
