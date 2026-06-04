"""Central configuration, credential handling, and hard-constraint guards.

One place loads ``eval/.env`` with ``override=True`` (the documented dotenv gotcha:
a stale shell-exported key must not shadow the file value). Credentials are
validated *per requested command/tier* and never logged by value. The hard
"gold independence" constraints are encoded here as an evidence allowlist that
backends/tiers consult and tests assert against.
"""
from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("goldie")

EVAL_DIR = Path(__file__).resolve().parents[1]          # .../parseland-eval/eval
REPO_ROOT = EVAL_DIR.parent                             # .../parseland-eval (git root)
DATA_DIR = EVAL_DIR / "data"
# Goldie run dirs live at the REPO-ROOT runs/ (matches the existing goldie/10K
# artifacts: runs/10k, runs/holdout-*, ...). eval/runs/ is a DIFFERENT subsystem —
# the parseland-vs-gold scorer/dashboard (parseland_eval/paths.py). Do not conflate.
RUNS_DIR = REPO_ROOT / "runs"
PROMPTS_DIR = EVAL_DIR / "prompts"
DEFAULT_ENV_FILE = EVAL_DIR / ".env"
DEFAULT_SOURCE = DATA_DIR / "ai-goldie-source-10k.csv"
DEFAULT_CDP_URL = "http://localhost:9222"

# Anthropic-API model id form (hyphenated). The cloud backend translates to the
# dotted form browser-use Cloud expects. Sonnet is the locked best (RESULTS.md).
DEFAULT_MODEL = "claude-sonnet-4-6"
BATCH_SIZE = 100

# ---- credential keys (names only; values live in gitignored eval/.env) -------
KEY_ANTHROPIC = "ANTHROPIC_API_KEY"
KEY_BROWSER_USE = "BROWSER_USE_API_KEY"
KEY_BROWSERBASE = "BROWSERBASE_API_KEY"
KEY_CDP_URL = "CDP_URL"  # optional, not a secret

# Which env keys each (command, tier) needs. Missing → fail loudly, exit 2.
TIER_CREDENTIALS: dict[str, list[str]] = {
    "cached": [KEY_ANTHROPIC],
    "local_cdp": [KEY_ANTHROPIC],
    "cloud": [KEY_BROWSER_USE],
}
SPIKE_CREDENTIALS: dict[str, list[str]] = {
    "browserbase-fetch": [KEY_BROWSERBASE],
}

# ---- hard-constraint evidence allowlist (gold independence) ------------------
# Backends/tiers may set a gold value ONLY from these evidence kinds.
ALLOWED_EVIDENCE_KINDS: frozenset[str] = frozenset({
    "doi_org_landing",   # DOI.org-resolved publisher page
    "taxicab_cache",     # Taxicab/cache HTML
    "browserbase_fetch", # raw HTML during the spike only
    "rendered_dom",      # rendered browser DOM
    "browser_session",   # screenshots / network traces
})
# Hosts/APIs that must NEVER be read to set a gold value. (Crossref is allowed
# for DOI *sampling* only — never as metadata evidence.)
FORBIDDEN_EVIDENCE_HOSTS: tuple[str, ...] = (
    "api.openalex.org", "openalex.org",
    "api.unpaywall.org", "unpaywall.org",
    "europepmc.org", "ebi.ac.uk",          # Europe PMC / JATS
    "api.crossref.org",                      # Crossref metadata (sampling-only allowed)
    "api.semanticscholar.org",
    "browserbase.com/search", "/v1/search",  # Browserbase Search (Fetch is fine)
)


class ConfigError(SystemExit):
    """Raised for config/precondition failures → process exit code 2."""

    def __init__(self, message: str) -> None:
        log.error("goldie: %s", message)
        super().__init__(2)


def load_env(env_file: Path | None = None) -> Path:
    """Load ``eval/.env`` with ``override=True``. Returns the path used."""
    path = env_file or DEFAULT_ENV_FILE
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover - dotenv is a declared dep
        log.warning("python-dotenv not installed; relying on process environment")
        return path
    load_dotenv(path, override=True)
    return path


def key_present(name: str) -> bool:
    """True iff the env key is set and non-empty. Never returns the value."""
    return bool((os.environ.get(name) or "").strip())


def require_keys(names: list[str]) -> None:
    """Fail loudly (exit 2) if any required key is absent. Logs names, never values."""
    missing = [n for n in names if not key_present(n)]
    if missing:
        raise ConfigError(
            "missing required credential(s): "
            + ", ".join(missing)
            + f" — add them to {DEFAULT_ENV_FILE} (gitignored)."
        )


def validate_credentials(*, tier: str | None = None, spike: str | None = None) -> None:
    """Validate only the credentials the requested command/tier needs."""
    if tier is not None:
        if tier not in TIER_CREDENTIALS:
            raise ConfigError(f"unknown tier {tier!r}; expected one of {sorted(TIER_CREDENTIALS)}")
        require_keys(TIER_CREDENTIALS[tier])
    if spike is not None:
        if spike not in SPIKE_CREDENTIALS:
            raise ConfigError(f"unknown spike {spike!r}; expected one of {sorted(SPIKE_CREDENTIALS)}")
        require_keys(SPIKE_CREDENTIALS[spike])


def credential_presence() -> dict[str, bool]:
    """Present/absent map for logging — values are never included."""
    return {k: key_present(k) for k in (KEY_ANTHROPIC, KEY_BROWSER_USE, KEY_BROWSERBASE)}


def assert_allowed_evidence_host(host_or_url: str) -> None:
    """Guard: raise if a gold-setting code path reads a forbidden metadata host."""
    lowered = host_or_url.lower()
    for bad in FORBIDDEN_EVIDENCE_HOSTS:
        if bad in lowered:
            raise ConfigError(
                f"forbidden evidence source for a gold value: {host_or_url!r} "
                f"(matched {bad!r}). Gold evidence must be page/Taxicab/Browserbase-Fetch only."
            )


@dataclass(frozen=True)
class GoldieConfig:
    """Immutable run configuration. Built once in ``cli`` after ``load_env``."""

    eval_dir: Path = EVAL_DIR
    data_dir: Path = DATA_DIR
    runs_dir: Path = RUNS_DIR
    prompts_dir: Path = PROMPTS_DIR
    source: Path = DEFAULT_SOURCE
    model: str = DEFAULT_MODEL
    batch_size: int = BATCH_SIZE
    concurrency: int = 200          # global cap on in-flight cloud DOI extractions
    batch_concurrency: int = 4      # how many batch-pipelines run at once
    livefetch_concurrency: int = 2  # small cap for local CDP / live-browser fallback
    retry_cap: int = 3
    poll_interval: float = 5.0
    task_timeout_sec: float = 30 * 60
    max_cost_usd: float | None = None
    proxy_country_code: str | None = "default"  # sentinel = leave unset (US default)
    cdp_url: str = field(default_factory=lambda: os.environ.get(KEY_CDP_URL) or DEFAULT_CDP_URL)
