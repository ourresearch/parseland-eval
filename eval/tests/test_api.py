"""Unit tests for the Taxicab + Parseland HTTP client.

The client is mocked by monkeypatching the module-level ``_SESSION.get`` so
tests never hit the network.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from parseland_eval import api


@dataclass
class _FakeResponse:
    status_code: int
    body: Any

    def json(self) -> Any:
        if isinstance(self.body, BaseException):
            raise self.body
        return self.body

    @property
    def text(self) -> str:
        return str(self.body)[:200]


class _Router:
    """Map URL substring → (status_code, body). Raise RequestException if body is one."""

    def __init__(self, routes: dict[str, tuple[int, Any]]) -> None:
        self.routes = routes
        self.calls: list[str] = []

    def __call__(self, url: str, timeout: float) -> _FakeResponse:  # matches requests API
        self.calls.append(url)
        for key, (status, body) in self.routes.items():
            if key in url:
                return _FakeResponse(status_code=status, body=body)
        return _FakeResponse(status_code=404, body={})


@pytest.fixture
def router(monkeypatch: pytest.MonkeyPatch):
    def _install(routes: dict[str, tuple[int, Any]]) -> _Router:
        r = _Router(routes)
        monkeypatch.setattr(api._SESSION, "get", r)
        return r

    return _install


class TestResolveHarvestUuid:
    def test_happy_path(self, router) -> None:
        r = router(
            {
                "/taxicab/doi/10.1/abc": (
                    200,
                    {"html": [{"id": "abc-uuid", "resolved_url": "x"}], "pdf": [], "grobid": []},
                )
            }
        )
        uuid, call = api.resolve_harvest_uuid("10.1/abc")
        assert uuid == "abc-uuid"
        assert call.status_code == 200
        assert call.error is None
        assert len(r.calls) == 1

    def test_empty_html_returns_none(self, router) -> None:
        router({"/taxicab/doi/": (200, {"html": [], "pdf": [], "grobid": []})})
        uuid, call = api.resolve_harvest_uuid("10.1/xyz")
        assert uuid is None
        assert call.error is None  # request succeeded, just no harvest

    def test_non_200_returns_none(self, router) -> None:
        router({"/taxicab/doi/": (503, "gateway down")})
        uuid, call = api.resolve_harvest_uuid("10.1/xyz")
        assert uuid is None
        assert call.status_code == 503
        assert call.error == "status_503"


class TestFetchParsed:
    def test_happy_path(self, router) -> None:
        body = {
            "authors": [{"name": "A", "affiliations": [{"name": "Lab"}], "is_corresponding": True}],
            "urls": [{"url": "x", "content_type": "pdf"}],
            "abstract": "hello",
            "license": None,
            "version": "publishedVersion",
        }
        router({"/parseland/uuid-1": (200, body)})
        out, call = api.fetch_parsed("uuid-1")
        assert out == body
        assert call.status_code == 200

    def test_404_returns_none(self, router) -> None:
        router({"/parseland/missing": (404, {"error": "not found"})})
        out, call = api.fetch_parsed("missing")
        assert out is None
        assert call.status_code == 404

    def test_non_dict_response_rejected(self, router) -> None:
        router({"/parseland/weird": (200, ["unexpected", "list"])})
        out, call = api.fetch_parsed("weird")
        assert out is None
        assert call.error == "non_object_response"


class TestAdapter:
    def test_preserves_author_affiliation_shape(self) -> None:
        api_json = {
            "authors": [
                {
                    "name": "Jane Doe",
                    "affiliations": [{"name": "MIT"}, {"name": "Harvard"}],
                    "is_corresponding": False,
                }
            ],
            "urls": [{"url": "https://example.com/paper.pdf", "content_type": "pdf"}],
            "abstract": "Short abstract.",
            "license": "cc-by",
            "version": "publishedVersion",
        }
        adapted = api.parsed_api_to_parseland_shape(api_json)
        assert adapted["authors"][0]["name"] == "Jane Doe"
        assert adapted["authors"][0]["affiliations"] == [{"name": "MIT"}, {"name": "Harvard"}]
        assert adapted["urls"][0]["content_type"] == "pdf"
        assert adapted["abstract"] == "Short abstract."
        assert adapted["license"] == "cc-by"
        assert adapted["version"] == "publishedVersion"

    def test_missing_keys_default_safely(self) -> None:
        adapted = api.parsed_api_to_parseland_shape({})
        assert adapted["authors"] == []
        assert adapted["urls"] == []
        assert adapted["abstract"] is None
