"""Per-author affiliation triangulation: gold vs AI vs citation_author_institution meta tag.

Answers Casey's 2026-04-30 meeting questions:
  1. Is the AI using citation_author_institution meta tags as its primary
     affiliation source? (signal: AI rases == meta-tag rases)
  2. Where AI matches the meta tag but NOT gold, would updating gold to the
     meta-tag value close the failure? (signal: count by publisher)
  3. Does this affect publishers beyond Oxford?

Output:
  - Overall counts of the four states
  - Per-publisher breakdown
  - Concrete "if-we-update-gold" yield estimate

No new LLM calls — reads existing v1.8 run + fetches cached HTML once per DOI
to extract meta tags. Designed to be re-runnable in <2 min.
"""
from __future__ import annotations

import csv
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
from extract_via_taxicab import (  # noqa: E402
    _all_meta,
    _fix_encoding,
    fetch_html,
)

REPO_ROOT = SCRIPT_DIR.parent.parent
HOLDOUT_CSV = REPO_ROOT / "eval" / "goldie" / "holdout-50.csv"
V18_CSV = REPO_ROOT / "runs" / "holdout-v1.8" / "ai-goldie-1.csv"

# Subset of dashboard/src/lib/publishers.ts — DOI prefix → publisher.
PREFIX_TO_PUBLISHER: dict[str, str] = {
    "10.1016": "Elsevier", "10.1006": "Elsevier", "10.1053": "Elsevier",
    "10.1067": "Elsevier", "10.1078": "Elsevier",
    "10.1007": "Springer Nature", "10.1038": "Springer Nature",
    "10.1186": "Springer Nature", "10.1057": "Springer Nature",
    "10.1140": "Springer Nature", "10.1361": "Springer Nature",
    "10.1080": "Taylor & Francis", "10.4324": "Taylor & Francis",
    "10.1081": "Taylor & Francis", "10.1201": "Taylor & Francis",
    "10.1002": "Wiley", "10.1111": "Wiley", "10.1046": "Wiley",
    "10.1113": "Wiley", "10.1034": "Wiley",
    "10.1177": "SAGE", "10.4135": "SAGE", "10.1191": "SAGE",
    "10.1097": "Wolters Kluwer", "10.1213": "Wolters Kluwer",
    "10.1212": "Wolters Kluwer",
    "10.1093": "Oxford University Press", "10.1215": "Oxford University Press",
    "10.1017": "Cambridge University Press", "10.1079": "Cambridge University Press",
    "10.1109": "IEEE",
    "10.1021": "American Chemical Society",
    "10.1103": "American Physical Society",
    "10.1063": "AIP Publishing", "10.1121": "Acoustical Society of America",
    "10.1039": "Royal Society of Chemistry", "10.1136": "BMJ",
    "10.1055": "Thieme", "10.1088": "IOP Publishing",
    "10.1042": "Portland Press",
    "10.1371": "PLOS", "10.3390": "MDPI", "10.3389": "Frontiers",
    "10.1101": "Cold Spring Harbor",
    "10.1086": "University of Chicago Press", "10.1108": "Emerald",
    "10.1163": "Brill", "10.1515": "De Gruyter", "10.2307": "JSTOR",
    "10.36838": "IDEAS", "10.3791": "MyJoVE", "10.7256": "PeerJ",
    "10.1161": "American Heart Association",
    "10.1124": "ASPET", "10.1128": "American Society for Microbiology",
    "10.1158": "AACR",
    "10.7717": "PeerJ", "10.1105": "ASPB",
}


def publisher_of(doi: str) -> str:
    if "/" not in doi:
        return "Other / Unknown"
    prefix = doi.split("/", 1)[0]
    return PREFIX_TO_PUBLISHER.get(prefix, "Other / Unknown")


_PUNCT_RE = re.compile(r"[.,'\"\-]")
_WS_RE = re.compile(r"\s+")


def normspaces(s: str) -> str:
    return _WS_RE.sub(" ", (s or "")).strip().lower()


def name_tokens(s: str) -> frozenset[str]:
    s = (s or "").lower()
    s = _PUNCT_RE.sub(" ", s)
    return frozenset(s.split())


def safe_load(s: str | None) -> list[dict]:
    if not s:
        return []
    try:
        return json.loads(s) or []
    except Exception:
        return []


def extract_meta_pairs(html: str) -> list[tuple[str, str]]:
    """Return [(author_name, affiliation_string)] from citation_* meta tags.
    Uses the same pairing logic as extract_via_meta_tags (1:1 by index, with
    the multi-aff distribution heuristic when counts mismatch)."""
    authors_raw = [_fix_encoding(a) for a in _all_meta(html, "citation_author")]
    affs_raw = [_fix_encoding(a) for a in _all_meta(html, "citation_author_institution")]
    if not authors_raw:
        return []
    if len(affs_raw) == len(authors_raw):
        return list(zip(authors_raw, affs_raw))
    if len(affs_raw) > len(authors_raw):
        per = max(1, len(affs_raw) // len(authors_raw))
        out: list[tuple[str, str]] = []
        idx = 0
        for i, name in enumerate(authors_raw):
            if i == len(authors_raw) - 1:
                chunk = affs_raw[idx:]
            else:
                chunk = affs_raw[idx:idx + per]
                idx += per
            out.append((name, "; ".join(chunk)))
        return out
    out = []
    for i, name in enumerate(authors_raw):
        out.append((name, affs_raw[i] if i < len(affs_raw) else ""))
    return out


def classify_pair(gold: str, ai: str, meta: str) -> str:
    """Classify the (gold, ai, meta) state for one author."""
    g, a, m = normspaces(gold), normspaces(ai), normspaces(meta)
    has_g, has_a, has_m = bool(g), bool(a), bool(m)

    if not has_g and not has_a and not has_m:
        return "all_empty"

    ai_eq_meta = has_a and has_m and a == m
    ai_eq_gold = has_a and has_g and a == g
    gold_eq_meta = has_g and has_m and g == m

    # Substring relations (the meaningful "AI/gold from same source" signal).
    ai_sub_meta = has_a and has_m and (a in m or m in a)
    gold_sub_meta = has_g and has_m and (g in m or m in g)

    if ai_eq_gold:
        return "AI_matches_gold"  # success
    if ai_eq_meta and not gold_eq_meta:
        return "AI_matches_meta_not_gold"  # the smoking gun for Casey's hypothesis
    if ai_sub_meta and not gold_sub_meta:
        return "AI_substr_meta_not_gold"
    if gold_eq_meta and not ai_eq_gold:
        return "gold_matches_meta_AI_misses"  # AI failed but meta=gold (extractor problem, not gold)
    if has_a and not has_g:
        return "AI_filled_gold_empty"
    if has_g and not has_a:
        return "AI_empty_gold_filled"
    return "AI_and_gold_differ_meta_unrelated"


def main(argv: list[str] | None = None) -> int:
    # Load gold + AI extraction
    gold = {r["DOI"]: safe_load(r.get("Authors")) for r in csv.DictReader(open(HOLDOUT_CSV))}
    ai = {r["DOI"]: safe_load(r.get("Authors")) for r in csv.DictReader(open(V18_CSV))}

    overall_states: Counter = Counter()
    by_pub_states: dict[str, Counter] = defaultdict(Counter)
    pub_doi_count: Counter = Counter()
    pub_meta_present: Counter = Counter()
    pub_meta_absent: Counter = Counter()

    # Per-DOI yield: how many DOIs would CHANGE FROM FAIL → PASS if gold
    # accepted meta-tag value where AI matches meta. Track per-DOI bool.
    n_dois_currently_pass = 0
    n_dois_would_pass_if_updated = 0
    n_dois_would_pass_if_gold_updated = 0

    print(f"Auditing {len(gold)} DOIs (fetching cached HTML — this takes ~1 min)...\n")

    for doi in sorted(gold.keys() & ai.keys()):
        pub = publisher_of(doi)
        pub_doi_count[pub] += 1

        html, _resolved, fetch_err = fetch_html(doi)
        if fetch_err or not html:
            overall_states["fetch_failed"] += 1
            by_pub_states[pub]["fetch_failed"] += 1
            continue

        meta_pairs = extract_meta_pairs(html)
        meta_by_norm = {normspaces(n): aff for n, aff in meta_pairs}
        meta_by_tokens = {name_tokens(n): aff for n, aff in meta_pairs}
        if meta_pairs:
            pub_meta_present[pub] += 1
        else:
            pub_meta_absent[pub] += 1

        gold_by_tokens = {name_tokens(a.get("name", "")): (a.get("rasses") or "") for a in gold[doi] if a.get("name")}
        ai_by_tokens = {name_tokens(a.get("name", "")): (a.get("rasses") or "") for a in ai[doi] if a.get("name")}

        # Per-author classification on the intersection of gold & AI authors.
        shared = gold_by_tokens.keys() & ai_by_tokens.keys()
        if not shared:
            overall_states["no_shared_authors"] += 1
            by_pub_states[pub]["no_shared_authors"] += 1
            continue

        doi_currently_passes = True
        doi_would_pass_if_updated = True
        for toks in shared:
            g = gold_by_tokens[toks]
            a = ai_by_tokens[toks]
            m = meta_by_tokens.get(toks, meta_by_norm.get(" ".join(sorted(toks)), ""))
            state = classify_pair(g, a, m)
            overall_states[state] += 1
            by_pub_states[pub][state] += 1

            # Currently passing? (relaxed: substring or equal)
            ng, na = normspaces(g), normspaces(a)
            curr_pass = (not ng and not na) or (ng == na) or (ng and na and (ng in na or na in ng))
            if not curr_pass:
                doi_currently_passes = False

            # Would pass if gold updated to meta-tag value (only when AI matches meta)?
            nm = normspaces(m)
            ai_matches_meta = nm and na and (na == nm or na in nm or nm in na)
            updated_pass = curr_pass or ai_matches_meta
            if not updated_pass:
                doi_would_pass_if_updated = False

        if doi_currently_passes:
            n_dois_currently_pass += 1
        if doi_would_pass_if_updated:
            n_dois_would_pass_if_updated += 1

    print("=" * 70)
    print("Overall per-author state counts")
    print("=" * 70)
    for state, count in sorted(overall_states.items(), key=lambda x: -x[1]):
        print(f"  {state:<40} {count}")
    print()

    print("=" * 70)
    print("DOI-level yield estimate")
    print("=" * 70)
    n_total = len(gold.keys() & ai.keys())
    print(f"  DOIs evaluated:               {n_total}")
    print(f"  DOIs currently passing rases: {n_dois_currently_pass}  ({100*n_dois_currently_pass/n_total:.1f}%)")
    print(f"  DOIs that WOULD pass if gold accepted meta-tag rases when AI matches meta: "
          f"{n_dois_would_pass_if_updated}  ({100*n_dois_would_pass_if_updated/n_total:.1f}%)")
    print(f"  Net additional DOIs:          {n_dois_would_pass_if_updated - n_dois_currently_pass}")
    print()

    print("=" * 70)
    print("Per-publisher meta-tag presence (citation_author_institution)")
    print("=" * 70)
    print(f"  {'Publisher':<35} {'DOIs':>6} {'Meta present':>14} {'Meta absent':>14}")
    for pub in sorted(pub_doi_count, key=lambda p: -pub_doi_count[p]):
        print(f"  {pub:<35} {pub_doi_count[pub]:>6} {pub_meta_present[pub]:>14} {pub_meta_absent[pub]:>14}")
    print()

    print("=" * 70)
    print("Per-publisher state breakdown (top patterns only)")
    print("=" * 70)
    for pub in sorted(pub_doi_count, key=lambda p: -pub_doi_count[p]):
        states = by_pub_states[pub]
        if sum(states.values()) == 0:
            continue
        print(f"  {pub} ({pub_doi_count[pub]} DOIs)")
        for state in ("AI_matches_gold", "AI_matches_meta_not_gold", "AI_substr_meta_not_gold",
                      "gold_matches_meta_AI_misses", "AI_empty_gold_filled",
                      "AI_and_gold_differ_meta_unrelated", "fetch_failed", "no_shared_authors"):
            if states.get(state, 0):
                print(f"     {state:<40} {states[state]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
