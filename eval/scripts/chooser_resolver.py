"""Crossref chooser-page resolver.

Some DOIs resolve to https://chooser.crossref.org/?doi=<urlencoded-doi> — a
disambiguation interstitial that lists candidate publisher landing pages and
asks the user to pick one. Our extraction pipeline reads cached HTML expecting
a real publisher page, so a chooser page comes back author-empty.

This module provides two small utilities used by the targeted-rerun cascade:

  is_chooser_url(url)  -> bool
  resolve_chooser(url) -> str | None     # first plausible publisher link, or None

Both are pure-Python with a single requests dependency; no async, no Selenium.
On any failure (network, parse, zero candidates) resolve_chooser returns None
and the caller is responsible for surfacing this loudly per the project's
no-silent-failure rule.
"""
from __future__ import annotations

import logging
from typing import Final
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

log = logging.getLogger("chooser-resolver")

_CHOOSER_HOST: Final[str] = "chooser.crossref.org"
_DEFAULT_TIMEOUT_S: Final[int] = 15
_USER_AGENT: Final[str] = (
    "parseland-eval-chooser-resolver/0.1 "
    "(mailto:reach2shubhankar@gmail.com)"
)

# Hosts whose links on a chooser page are never the answer (the chooser
# itself, doi.org redirects, social/share buttons, mailto, anchors).
_REJECT_HOSTS: Final[frozenset[str]] = frozenset({
    "chooser.crossref.org",
    "www.crossref.org",
    "crossref.org",
    "doi.org",
    "dx.doi.org",
    "twitter.com",
    "x.com",
    "facebook.com",
    "linkedin.com",
})


def is_chooser_url(url: str | None) -> bool:
    """True iff `url` is a Crossref chooser interstitial.

    Substring match is sufficient — the chooser host is unique and we want
    to catch both `https://chooser.crossref.org/?doi=...` and any redirect
    chain that lands there.
    """
    if not url:
        return False
    return _CHOOSER_HOST in url


def _looks_like_publisher_link(href: str) -> bool:
    """Heuristic: an `<a href>` from a chooser page is a publisher link if
    it's an absolute http(s) URL whose host is not a rejected interstitial
    and whose path is non-empty (i.e., it points at an actual document)."""
    if not href or not href.strip():
        return False
    href = href.strip()
    if href.startswith("#") or href.startswith("mailto:") or href.startswith("javascript:"):
        return False
    try:
        p = urlparse(href)
    except ValueError:
        return False
    if p.scheme not in {"http", "https"}:
        return False
    host = (p.netloc or "").lower()
    if not host or host in _REJECT_HOSTS:
        return False
    if not p.path or p.path == "/":
        return False
    return True


def resolve_chooser(
    url: str,
    *,
    timeout_s: int = _DEFAULT_TIMEOUT_S,
    session: requests.Session | None = None,
) -> str | None:
    """Fetch a chooser page and return the first plausible publisher link.

    Returns None on any of:
      - network failure
      - non-200 response
      - HTML with zero plausible publisher anchors

    The caller (typically rerun_targeted) should treat a None return as
    "use the original DOI Link as-is, the chooser is intact in the cache
    pipeline" and log it loudly.
    """
    if not is_chooser_url(url):
        return None

    sess = session or requests
    try:
        resp = sess.get(
            url,
            timeout=timeout_s,
            headers={"User-Agent": _USER_AGENT, "Accept": "text/html,*/*"},
            allow_redirects=True,
        )
    except requests.RequestException as e:
        log.warning("chooser GET failed for %s: %s", url, e)
        return None

    if resp.status_code != 200:
        log.warning("chooser GET returned %s for %s", resp.status_code, url)
        return None

    soup = BeautifulSoup(resp.text, "html.parser")
    for anchor in soup.find_all("a"):
        href = anchor.get("href")
        if _looks_like_publisher_link(href):
            log.info("chooser resolved %s -> %s", url, href)
            return href

    log.warning("chooser page had no plausible publisher links: %s", url)
    return None
