"""Bridge to the proven post-LLM transform functions.

The deterministic publisher transforms were developed and battle-tested in
``eval/scripts/extract_via_taxicab.py`` (the ``_maybe_backfill_*`` family + abstract
helpers). Rather than transcribe ~1,400 regex-dense lines of the *quality core*
(and risk drift), goldie runs the **same functions**. This module imports them.

Independence note: these are EXTRACTION recipes that read the page HTML only — they
never consult an external metadata API, so importing them does not violate the gold
-independence constraints. They are distinct from ``parseland-lib`` parser *output*.

Phase-8 caveat: ``extract_via_taxicab.py`` must stay importable (keep it as a library
module, not a 5-line shim) until these functions are internalised into ``transforms/``
with fixtures. The registry below pins the exact call order, so internalising later is
a mechanical, test-guarded move.
"""
from __future__ import annotations

import sys

from ..config import EVAL_DIR

_SCRIPTS_DIR = EVAL_DIR / "scripts"


def _load():
    if str(_SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(_SCRIPTS_DIR))
    try:
        import extract_via_taxicab as src  # noqa: E402
    except Exception as e:  # pragma: no cover - clear failure beats a silent skip
        raise RuntimeError(
            f"could not import transform source extract_via_taxicab from {_SCRIPTS_DIR}: {e}"
        ) from e
    return src


src = _load()

# The proven callables the registry pins (verified present by test_transforms).
extract_via_meta_tags = src.extract_via_meta_tags
_jsonld_abstract = src._jsonld_abstract
_looks_truncated = src._looks_truncated
_is_title_as_abstract = src._is_title_as_abstract
_is_mostly_non_latin = src._is_mostly_non_latin
_latin_abstract_from_label = src._latin_abstract_from_label
_maybe_drop_all_ca = src._maybe_drop_all_ca
_maybe_backfill_ca_from_class = src._maybe_backfill_ca_from_class
_maybe_backfill_rases_from_overlay = src._maybe_backfill_rases_from_overlay
_maybe_backfill_rases_from_elsevier_iso = src._maybe_backfill_rases_from_elsevier_iso
_maybe_backfill_rases_from_jsonld = src._maybe_backfill_rases_from_jsonld
_maybe_backfill_abstract_from_emerald = src._maybe_backfill_abstract_from_emerald
_maybe_backfill_ca_from_oup_email = src._maybe_backfill_ca_from_oup_email
_maybe_backfill_rases_and_ca_from_mdpi = src._maybe_backfill_rases_and_ca_from_mdpi
_maybe_replace_doi_org_pdf_with_local = src._maybe_replace_doi_org_pdf_with_local
