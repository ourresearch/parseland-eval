from parseland_eval.score.abstract import score_abstract
from parseland_eval.score.affiliations import score_affiliations
from parseland_eval.score.authors import score_authors
from parseland_eval.score.normalize import is_empty_value
from parseland_eval.score.pdf_url import score_pdf_url


def _author(affiliations: list[str]) -> dict:
    return {"name": "Jane Doe", "affiliations": affiliations}


def test_empty_value_normalization() -> None:
    for value in (None, "", "   ", "NA", "N/A", "null", "[]", "{}", [], {}):
        assert is_empty_value(value)


def test_empty_empty_scores_as_correct_across_fields() -> None:
    authors = score_authors([], [])
    assert authors.f1 == 1.0
    assert authors.f1_soft == 1.0

    abstract = score_abstract(None, "")
    assert abstract.strict_match is True
    assert abstract.fuzzy_ratio == 1.0

    pdf_url = score_pdf_url(None, {})
    assert pdf_url.strict_match is True

    affiliations = score_affiliations(_author([]), _author([]))
    assert affiliations.fuzzy_f1 == 1.0


def test_gold_present_parser_empty_is_miss_across_fields() -> None:
    authors = score_authors([{"name": "Jane Doe"}], [])
    assert authors.f1 == 0.0
    assert authors.f1_soft == 0.0

    abstract = score_abstract("Expected abstract.", "")
    assert abstract.strict_match is False
    assert abstract.fuzzy_ratio == 0.0

    pdf_url = score_pdf_url("https://example.org/article.pdf", {})
    assert pdf_url.strict_match is False

    affiliations = score_affiliations(_author(["MIT"]), _author([]))
    assert affiliations.fuzzy_f1 == 0.0


def test_gold_empty_parser_present_is_not_a_match_across_direct_scorers() -> None:
    authors = score_authors([], [{"name": "Jane Doe"}])
    assert authors.f1 == 0.0

    abstract = score_abstract("", "Parser found text.")
    assert abstract.strict_match is False

    pdf_url = score_pdf_url(None, {"pdf_url": "https://example.org/article.pdf"})
    assert pdf_url.strict_match is False

    affiliations = score_affiliations(_author([]), _author(["MIT"]))
    assert affiliations.fuzzy_f1 == 0.0
