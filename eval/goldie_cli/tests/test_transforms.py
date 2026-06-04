from __future__ import annotations

from goldie_cli.transforms import TRANSFORMS, apply_transforms, convention_labels
from goldie_cli.transforms.base import TransformContext


def _ctx(html, doi="10.1/x", link="https://doi.org/10.1/x", resolved_url=None, skip_meta_tags=True):
    return TransformContext(html=html, doi=doi, link=link, resolved_url=resolved_url,
                            skip_meta_tags=skip_meta_tags)


def test_registry_order_is_load_bearing():
    names = [t.name for t in sorted(TRANSFORMS, key=lambda t: t.order)]
    assert names == [
        "meta_backfill", "jsonld_abstract", "drop_title_as_abstract", "latin_abstract",
        "drop_all_ca", "ca_from_class", "rases_overlay", "rases_elsevier_iso",
        "rases_jsonld", "abstract_emerald", "ca_oup_email", "mdpi_rases_ca",
        "pdf_local_replace",
    ]
    # orders strictly increasing
    orders = [t.order for t in sorted(TRANSFORMS, key=lambda t: t.order)]
    assert orders == sorted(orders) and len(set(orders)) == len(orders)


def test_ca_from_class_flags_named_author():
    html = ('<span class="contribDegrees corresponding ">'
            '<a class="author">Matthew Leggatt</a></span>')
    ext = {"Authors": [
        {"name": "Matthew Leggatt", "rasses": "", "corresponding_author": False},
        {"name": "Other Person", "rasses": "", "corresponding_author": False},
    ], "Abstract": "", "PDF URL": ""}
    fired = apply_transforms(ext, _ctx(html))
    assert "ca_from_class" in fired
    flags = {a["name"]: a["corresponding_author"] for a in ext["Authors"]}
    assert flags["Matthew Leggatt"] is True
    assert flags["Other Person"] is False


def test_drop_all_ca_when_no_marker():
    html = "<html><body><p>no markers here</p></body></html>"
    ext = {"Authors": [
        {"name": "A One", "rasses": "", "corresponding_author": True},
        {"name": "B Two", "rasses": "", "corresponding_author": True},
    ], "Abstract": "", "PDF URL": ""}
    fired = apply_transforms(ext, _ctx(html))
    assert "drop_all_ca" in fired
    assert all(a["corresponding_author"] is False for a in ext["Authors"])


def test_drop_title_as_abstract():
    html = '<meta name="citation_title" content="A Study Of Things">'
    ext = {"Authors": [], "Abstract": "A Study Of Things", "PDF URL": ""}
    fired = apply_transforms(ext, _ctx(html))
    assert "drop_title_as_abstract" in fired
    assert ext["Abstract"] == ""


def test_no_op_on_clean_extraction():
    html = "<html><body>nothing actionable</body></html>"
    ext = {"Authors": [{"name": "Solo", "rasses": "MIT", "corresponding_author": False}],
           "Abstract": "A real, sentence-terminated abstract that is plainly long enough "
                       "to not look truncated and ends properly.", "PDF URL": "https://x/y.pdf"}
    fired = apply_transforms(ext, _ctx(html))
    assert fired == []


def test_convention_labels():
    sage = convention_labels("10.1177/abc")
    assert sage.rases_empty_ok is not None
    assert sage.abstract_truncation is not None
    assert sage.any() is True
    elsevier = convention_labels("10.1016/j.x.2020.01.001")
    assert elsevier.any() is False
    oup = convention_labels("10.1093/x")
    assert oup.router_harvest_blocked is not None
