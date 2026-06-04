from __future__ import annotations

from goldie_cli.schema import GOLD_COLUMNS, AuthorOut, ExtractionOut, extraction_json_schema


def test_gold_columns_order_is_frozen():
    assert GOLD_COLUMNS == [
        "No", "DOI", "Link", "Authors", "Abstract", "PDF URL",
        "Status", "Notes", "Has Bot Check", "Resolves To PDF",
        "broken_doi", "no english",
    ]
    assert len(GOLD_COLUMNS) == 12


def test_author_defaults():
    a = AuthorOut(name="Jane Roe")
    assert a.rasses == ""
    assert a.corresponding_author is False
    assert a.affiliations == []


def test_extraction_roundtrip():
    e = ExtractionOut(authors=[AuthorOut(name="A B", corresponding_author=True)])
    assert e.abstract is None
    assert e.has_bot_check is False
    assert e.authors[0].corresponding_author is True


def test_json_schema_has_fields():
    schema = extraction_json_schema()
    assert "authors" in schema["properties"]
    assert "abstract" in schema["properties"]
    assert "pdf_url" in schema["properties"]
