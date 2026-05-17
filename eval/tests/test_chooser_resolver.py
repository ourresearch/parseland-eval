"""Unit tests for eval/scripts/chooser_resolver.py."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "eval" / "scripts"))

from chooser_resolver import (  # noqa: E402
    _looks_like_publisher_link,
    is_chooser_url,
    resolve_chooser,
)

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"


@pytest.mark.unit
class TestIsChooserUrl:
    @pytest.mark.parametrize("url", [
        "https://chooser.crossref.org/?doi=10.1515%2F9781773850016-065",
        "https://chooser.crossref.org/",
        "http://chooser.crossref.org/?doi=foo",
        # redirect chain leaves the substring in a query param
        "https://example.com/?next=https://chooser.crossref.org/?doi=x",
    ])
    def test_true(self, url: str) -> None:
        assert is_chooser_url(url) is True

    @pytest.mark.parametrize("url", [
        None,
        "",
        "https://doi.org/10.1515/9781773850016-065",
        "https://www.degruyterbrill.com/document/doi/10.1515/9781773850016-065/html",
        "https://chooser.example.com/foo",  # different host
        "https://crossref.org/",
    ])
    def test_false(self, url: str | None) -> None:
        assert is_chooser_url(url) is False


@pytest.mark.unit
class TestLooksLikePublisherLink:
    @pytest.mark.parametrize("href", [
        "https://www.degruyterbrill.com/document/doi/10.1515/9781773850016-065/html",
        "http://link.springer.com/article/10.1007/foo",
        "https://onlinelibrary.wiley.com/doi/10.1002/x.y",
    ])
    def test_accepts_publisher(self, href: str) -> None:
        assert _looks_like_publisher_link(href) is True

    @pytest.mark.parametrize("href", [
        None,
        "",
        "   ",
        "#",
        "#section-1",
        "mailto:support@crossref.org",
        "javascript:void(0)",
        "https://chooser.crossref.org/?doi=x",
        "https://www.crossref.org/contact",
        "https://doi.org/10.1515/x.y",
        "https://twitter.com/CrossrefOrg",
        "ftp://example.com/file.pdf",
        "https://example.com",  # no path
        "https://example.com/",  # bare slash
    ])
    def test_rejects(self, href: str | None) -> None:
        assert _looks_like_publisher_link(href) is False


@pytest.mark.unit
class TestResolveChooser:
    def _mock_get(self, *, status: int = 200, body: str = "") -> MagicMock:
        m = MagicMock()
        m.status_code = status
        m.text = body
        return m

    def test_returns_first_publisher_link(self) -> None:
        html = (FIXTURE_DIR / "chooser.html").read_text()
        session = MagicMock()
        session.get.return_value = self._mock_get(body=html)
        result = resolve_chooser(
            "https://chooser.crossref.org/?doi=10.1515%2F9781773850016-065",
            session=session,
        )
        assert result == (
            "https://www.degruyterbrill.com/document/doi/"
            "10.1515/9781773850016-065/html"
        )

    def test_returns_none_when_no_publisher_links(self) -> None:
        html = (FIXTURE_DIR / "chooser_empty.html").read_text()
        session = MagicMock()
        session.get.return_value = self._mock_get(body=html)
        result = resolve_chooser(
            "https://chooser.crossref.org/?doi=10.9999%2Fmissing",
            session=session,
        )
        assert result is None

    def test_returns_none_on_non_200(self) -> None:
        session = MagicMock()
        session.get.return_value = self._mock_get(status=503, body="boom")
        assert resolve_chooser(
            "https://chooser.crossref.org/?doi=x", session=session
        ) is None

    def test_returns_none_on_network_failure(self) -> None:
        session = MagicMock()
        session.get.side_effect = requests.ConnectionError("dns down")
        assert resolve_chooser(
            "https://chooser.crossref.org/?doi=x", session=session
        ) is None

    def test_returns_none_on_non_chooser_url(self) -> None:
        # Should not even attempt the GET — guard short-circuits.
        session = MagicMock()
        result = resolve_chooser(
            "https://doi.org/10.1515/9781773850016-065", session=session
        )
        assert result is None
        session.get.assert_not_called()

    def test_uses_module_requests_when_no_session(self) -> None:
        html = (FIXTURE_DIR / "chooser.html").read_text()
        with patch.object(requests, "get") as mock_get:
            mock_get.return_value = self._mock_get(body=html)
            result = resolve_chooser(
                "https://chooser.crossref.org/?doi=10.1515%2F9781773850016-065"
            )
        assert result is not None
        assert "degruyterbrill.com" in result
