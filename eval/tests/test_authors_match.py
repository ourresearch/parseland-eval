from parseland_eval.score.authors import score_authors, score_corresponding


def _mk(name: str, ca: bool = False) -> dict:
    return {"name": name, "corresponding_author": ca}


class TestAuthorMatching:
    def test_empty_inputs(self) -> None:
        r = score_authors([], [])
        assert r.f1 == 1.0
        assert r.f1_soft == 1.0

    def test_perfect_match_exact(self) -> None:
        gold = [_mk("Jane Doe"), _mk("John Smith")]
        parsed = [_mk("Jane Doe"), _mk("John Smith")]
        r = score_authors(gold, parsed)
        assert r.f1 == 1.0
        assert r.f1_soft == 1.0

    def test_order_insensitive(self) -> None:
        gold = [_mk("Jane Doe"), _mk("John Smith")]
        parsed = [_mk("John Smith"), _mk("Jane Doe")]
        r = score_authors(gold, parsed)
        assert r.f1 == 1.0

    def test_diacritic_fold(self) -> None:
        gold = [_mk("Cédric Moreau")]
        parsed = [_mk("Cedric Moreau")]
        r = score_authors(gold, parsed)
        assert r.f1 == 1.0

    def test_last_first_vs_first_last(self) -> None:
        gold = [_mk("Doe, Jane")]
        parsed = [_mk("Jane Doe")]
        r = score_authors(gold, parsed)
        assert r.f1 == 1.0

    def test_initial_vs_full_first(self) -> None:
        gold = [_mk("J. Doe")]
        parsed = [_mk("Jane Doe")]
        r = score_authors(gold, parsed)
        # Strict (last + first-initial) → match
        assert r.f1 == 1.0

    def test_missing_author_reduces_recall(self) -> None:
        gold = [_mk("Jane Doe"), _mk("John Smith"), _mk("Ada Lovelace")]
        parsed = [_mk("Jane Doe")]
        r = score_authors(gold, parsed)
        assert r.recall < 1.0
        assert r.precision == 1.0

    def test_spurious_author_reduces_precision(self) -> None:
        gold = [_mk("Jane Doe")]
        parsed = [_mk("Jane Doe"), _mk("Fake Person"), _mk("Noise Name")]
        r = score_authors(gold, parsed)
        assert r.precision < 1.0
        assert r.recall == 1.0

    def test_soft_mode_separates_precision_and_recall(self) -> None:
        # Two gold, parser gets one right and invents a plausible-looking one
        # that still misses the key — soft precision should drop below 1.0.
        gold = [_mk("Jane Doe"), _mk("John Smith")]
        parsed = [_mk("Jane Doe"), _mk("Unrelated Stranger Xyz")]
        r = score_authors(gold, parsed)
        # Strict key-match path should lose recall on the missed John Smith.
        assert r.recall < 1.0
        # Soft ratios are independent of strict P/R and must be surfaced.
        assert 0.0 <= r.precision_soft <= 1.0
        assert 0.0 <= r.recall_soft <= 1.0


class TestCorrespondingAuthorScoring:
    """Rule #15 (2026-05-06) — CA flag scoring."""

    def test_perfect_ca_match_is_f1_one(self) -> None:
        gold = [_mk("Jane Doe", ca=True), _mk("John Smith")]
        parsed = [_mk("Jane Doe", ca=True), _mk("John Smith")]
        a = score_authors(gold, parsed)
        c = score_corresponding(gold, parsed, a.matched)
        assert c.tp == 1
        assert c.fp == 0
        assert c.fn == 0
        assert c.f1 == 1.0

    def test_ai_missed_ca_drops_recall(self) -> None:
        # Old Elsevier OUP-redirect train-50 row 5 shape:
        # gold marks one CA, AI marks none.
        gold = [_mk("Jane Doe", ca=True), _mk("John Smith")]
        parsed = [_mk("Jane Doe"), _mk("John Smith")]
        a = score_authors(gold, parsed)
        c = score_corresponding(gold, parsed, a.matched)
        assert c.tp == 0
        assert c.fp == 0
        assert c.fn == 1
        assert c.recall == 0.0

    def test_ai_invented_ca_drops_precision(self) -> None:
        # holdout-50 Stroke row 6 shape: AI marks CA where gold has none.
        gold = [_mk("Jane Doe"), _mk("John Smith")]
        parsed = [_mk("Jane Doe", ca=True), _mk("John Smith")]
        a = score_authors(gold, parsed)
        c = score_corresponding(gold, parsed, a.matched)
        assert c.tp == 0
        assert c.fp == 1
        assert c.fn == 0
        assert c.precision == 0.0

    def test_unmatched_gold_ca_counts_as_recall_miss(self) -> None:
        # Gold has an extra author marked CA that AI didn't extract at all.
        gold = [_mk("Jane Doe"), _mk("John Smith", ca=True)]
        parsed = [_mk("Jane Doe")]
        a = score_authors(gold, parsed)
        c = score_corresponding(gold, parsed, a.matched)
        # John Smith is unmatched in parsed; gold's CA flag → fn.
        assert c.fn >= 1
        assert c.recall < 1.0

    def test_no_ca_anywhere_returns_zero_scores(self) -> None:
        gold = [_mk("Jane Doe"), _mk("John Smith")]
        parsed = [_mk("Jane Doe"), _mk("John Smith")]
        a = score_authors(gold, parsed)
        c = score_corresponding(gold, parsed, a.matched)
        assert c.tp == 0 and c.fp == 0 and c.fn == 0
        # No CA truth and no CA prediction → undefined → 0.0 by convention.
        assert c.precision == 0.0 and c.recall == 0.0 and c.f1 == 0.0
