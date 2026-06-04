"""goldie — reusable gold-standard scholarly-metadata extraction CLI.

Gold evidence comes only from the page (DOI.org landing / Taxicab cache / rendered
DOM / browser session / Browserbase raw Fetch during the spike). External
scholarly-metadata APIs (OpenAlex, Unpaywall, Europe PMC/JATS, Crossref metadata)
are never evidence; Crossref is used only to sample DOIs. ``parseland-lib`` parser
*recipes* may inform transforms, but parser *output* never sets a gold value.
"""
from __future__ import annotations

__version__ = "0.1.0"

from .schema import GOLD_COLUMNS, AuthorOut, ExtractionOut

__all__ = ["__version__", "GOLD_COLUMNS", "AuthorOut", "ExtractionOut"]
