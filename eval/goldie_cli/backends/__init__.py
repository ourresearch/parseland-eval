"""Extraction backends behind a single ``Backend`` seam.

The default is browser-use Cloud; ``cached_html`` and ``local_cdp`` cover the
Taxicab/judge and real-Chrome tiers. A future Stagehand/Skyvern/Codex/Firecrawl
backend only implements ``Backend.extract`` and registers in ``get_backend``.
"""
from __future__ import annotations

from .base import Backend, ExtractionResult, RetryPolicy, extract_with_retries

__all__ = ["Backend", "ExtractionResult", "RetryPolicy", "extract_with_retries", "get_backend"]


def get_backend(name: str, **kwargs):
    """Factory: resolve a backend by name. Imports lazily so a missing optional
    dependency (e.g. browser-use) only errors when that backend is requested."""
    if name in ("cloud", "browser_use_cloud"):
        from .browser_use_cloud import BrowserUseCloudBackend
        return BrowserUseCloudBackend(**kwargs)
    if name in ("cached", "cached_html"):
        from .cached_html import CachedHtmlBackend
        return CachedHtmlBackend(**kwargs)
    if name in ("local_cdp", "local"):
        from .local_cdp import LocalCdpBackend
        return LocalCdpBackend(**kwargs)
    if name in ("stub", "fake"):
        from .stub import StubBackend
        return StubBackend(**kwargs)
    raise ValueError(f"unknown backend {name!r}")
