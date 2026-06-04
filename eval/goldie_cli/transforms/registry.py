"""Ordered post-LLM transform registry.

The order MIRRORS ``extract_via_taxicab.run_doi`` lines 1462-1576 exactly — it is
load-bearing (e.g. JSON-LD abstract backfill must precede the title-as-abstract drop;
MDPI runs after the generic JSON-LD rases fill). ``apply_transforms`` runs each in
order over the capitalized extraction dict and returns the names that fired.

Corresponding-author policy (per plan): the lifted functions use strict on-page
signals only (class="corresp" wrappers, explicit "Correspondence to" labels, mailto
local-parts), support multiple CAs, and never infer CA from author order — and
``drop_all_ca`` removes order/mailto-proxy over-flagging when no marker exists.
"""
from __future__ import annotations

from typing import Any

from . import _source as src
from .base import Transform, TransformContext


# ---- transform wrappers (each: (ext, ctx) -> changed?) ---------------------

def _t_meta_backfill(ext: dict[str, Any], ctx: TransformContext) -> bool:
    """Backfill empty per-author rases + empty PDF URL from page meta tags
    (citation_author_institution / citation_pdf_url). run_doi:1462-1487."""
    if ctx.skip_meta_tags:
        return False
    meta = src.extract_via_meta_tags(ctx.html, ctx.doi, ctx.link)
    if not meta:
        return False
    changed = False
    sec_by_name = {
        (sa.get("name") or "").strip().lower(): sa
        for sa in (meta.get("Authors") or [])
    }
    for pa in (ext.get("Authors") or []):
        if (pa.get("rasses") or "").strip():
            continue
        sa = sec_by_name.get((pa.get("name") or "").strip().lower())
        if sa and (sa.get("rasses") or "").strip():
            pa["rasses"] = sa["rasses"].strip()
            changed = True
    if not (ext.get("PDF URL") or "").strip():
        meta_pdf = (meta.get("PDF URL") or "").strip()
        if meta_pdf:
            ext["PDF URL"] = meta_pdf
            changed = True
    return changed


def _t_jsonld_abstract(ext: dict[str, Any], ctx: TransformContext) -> bool:
    cur = (ext.get("Abstract") or "").strip()
    if not src._looks_truncated(cur):
        return False
    ld = src._jsonld_abstract(ctx.html)
    if ld and len(ld) > max(len(cur) * 3 // 2, 200):
        ext["Abstract"] = ld
        return True
    return False


def _t_drop_title_as_abstract(ext: dict[str, Any], ctx: TransformContext) -> bool:
    cur = (ext.get("Abstract") or "").strip()
    if src._is_title_as_abstract(cur, ctx.html):
        ext["Abstract"] = ""
        return True
    return False


def _t_latin_abstract(ext: dict[str, Any], ctx: TransformContext) -> bool:
    cur = (ext.get("Abstract") or "").strip()
    if cur and src._is_mostly_non_latin(cur):
        latin = src._latin_abstract_from_label(ctx.html)
        if latin and len(latin) >= 120:
            ext["Abstract"] = latin
            return True
    return False


def _authors(ext: dict[str, Any]) -> list[dict]:
    a = ext.get("Authors")
    return a if isinstance(a, list) else []


def _t_drop_all_ca(ext, ctx):            return src._maybe_drop_all_ca(_authors(ext), ctx.html)
def _t_ca_from_class(ext, ctx):          return src._maybe_backfill_ca_from_class(_authors(ext), ctx.html)
def _t_rases_overlay(ext, ctx):          return src._maybe_backfill_rases_from_overlay(_authors(ext), ctx.html)
def _t_rases_elsevier_iso(ext, ctx):     return src._maybe_backfill_rases_from_elsevier_iso(_authors(ext), ctx.html)
def _t_rases_jsonld(ext, ctx):           return src._maybe_backfill_rases_from_jsonld(_authors(ext), ctx.html)
def _t_abstract_emerald(ext, ctx):       return src._maybe_backfill_abstract_from_emerald(ext, ctx.html, ctx.doi)
def _t_ca_oup_email(ext, ctx):           return src._maybe_backfill_ca_from_oup_email(_authors(ext), ctx.html)
def _t_mdpi(ext, ctx):                   return src._maybe_backfill_rases_and_ca_from_mdpi(_authors(ext), ctx.html, ctx.doi)
def _t_pdf_local(ext, ctx):              return src._maybe_replace_doi_org_pdf_with_local(ext, ctx.html, ctx.doi, ctx.resolved_url)


# ---- ordered registry (mirrors run_doi 1462-1576) --------------------------

TRANSFORMS: list[Transform] = [
    Transform("meta_backfill",        10, _t_meta_backfill),
    Transform("jsonld_abstract",      20, _t_jsonld_abstract),
    Transform("drop_title_as_abstract", 25, _t_drop_title_as_abstract),
    Transform("latin_abstract",       30, _t_latin_abstract),
    Transform("drop_all_ca",          35, _t_drop_all_ca),
    Transform("ca_from_class",        40, _t_ca_from_class),
    Transform("rases_overlay",        45, _t_rases_overlay),
    Transform("rases_elsevier_iso",   50, _t_rases_elsevier_iso),
    Transform("rases_jsonld",         55, _t_rases_jsonld),
    Transform("abstract_emerald",     60, _t_abstract_emerald),
    Transform("ca_oup_email",         65, _t_ca_oup_email),
    Transform("mdpi_rases_ca",        70, _t_mdpi),
    Transform("pdf_local_replace",    75, _t_pdf_local),
]


def apply_transforms(ext: dict[str, Any], ctx: TransformContext) -> list[str]:
    """Run every transform in pinned order; return the names that changed ``ext``."""
    fired: list[str] = []
    for t in sorted(TRANSFORMS, key=lambda x: x.order):
        try:
            if t.apply(ext, ctx):
                fired.append(t.name)
        except Exception:  # a transform must never sink the whole row
            continue
    return fired
