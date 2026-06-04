"""Gold conventions — "don't chase phantoms" labels.

Derived from the parseland gold-issue registry (oxjobs .../parseland-gold-issues
G-001..G-013, #329 R-/H- codes), NOT lifted from any parser. These do not change
extraction; they let the report label a low score as a known gold/harvest blocker
rather than a parser/extractor miss, and document where an extractor must NOT
hallucinate a field that gold leaves empty by convention.
"""
from __future__ import annotations

from dataclasses import dataclass

# DOI registrant prefix → human note. Affiliations (rases) often legitimately
# empty in gold for these publishers (verified on-page); an extractor should not
# invent them, and a low rases score here is gold-owned, not an extraction miss.
EMPTY_RASES_CONVENTION: dict[str, str] = {
    "10.1177": "SAGE — ~71% of gold authors have empty rases (G-001)",
    "10.1109": "IEEE — ~77% of gold authors have empty rases (G-005)",
    "10.1097": "Wolters Kluwer/LWW — ~96% of rows have empty gold rases (G-006)",
}

# Abstract gold truncation (~200-char cap) — do not regress toward truncation.
ABSTRACT_TRUNCATION_CONVENTION: dict[str, str] = {
    "10.1080": "Taylor & Francis — ~33% of gold abstracts truncated ~200ch (#329 G-001)",
    "10.1177": "SAGE — ~23% of gold abstracts truncated ~200ch (G-004)",
}

# Corresponding-author gold is thin/under-recorded — high precision achievable,
# recall gaps are usually gold-owned (no on-page marker), not extractor misses.
CORRESP_GOLD_THIN: dict[str, str] = {
    "10.1097": "Wolters Kluwer/LWW — 0% of gold rows mark a CA (G-007)",
    "10.1017": "Cambridge UP — only ~14/143 gold rows mark a CA (G-010)",
}

# Harvest/router blockers — fields can be empty because the work never reached
# the parser/page, not because extraction failed.
ROUTER_HARVEST_BLOCKED: dict[str, str] = {
    "10.1093": "Oxford UP — ~60% of works router-blocked, never reach the page (#329 R-001)",
}

# Authorless content (news / front-matter) where empty authors is correct.
AUTHORLESS_HINTS: tuple[str, ...] = (
    "cen.acs.org",   # ACS C&EN magazine news items
)


@dataclass(frozen=True)
class ConventionLabels:
    rases_empty_ok: str | None = None
    abstract_truncation: str | None = None
    corresp_gold_thin: str | None = None
    router_harvest_blocked: str | None = None

    def any(self) -> bool:
        return any((self.rases_empty_ok, self.abstract_truncation,
                    self.corresp_gold_thin, self.router_harvest_blocked))


def _prefix(doi: str) -> str:
    return (doi or "").split("/", 1)[0].strip()


def convention_labels(doi: str) -> ConventionLabels:
    """Per-field gold-convention labels for a DOI (by registrant prefix)."""
    p = _prefix(doi)
    return ConventionLabels(
        rases_empty_ok=EMPTY_RASES_CONVENTION.get(p),
        abstract_truncation=ABSTRACT_TRUNCATION_CONVENTION.get(p),
        corresp_gold_thin=CORRESP_GOLD_THIN.get(p),
        router_harvest_blocked=ROUTER_HARVEST_BLOCKED.get(p),
    )
