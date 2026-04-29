# Holdout-50 v1.4 — disagreement triage and v1.5 patch surface

Source: `goldie/disagreements-v1.4-holdout.md` (42 disagreements out of 50 holdout DOIs).
Run: `runs/holdout-v1.4/ai-goldie-1.csv` (Opus 4.7, browser-use Cloud v3, 2026-04-29).

Field disagreement counts:

| Field | Disagreements | % of holdout |
|---|---|---|
| rases | 30 | 60% |
| pdf_url | 25 | 50% |
| corresponding | 20 | 40% |
| abstract | 18 | 36% |
| authors | 16 | 32% |

8 / 50 rows match perfectly across all fields.

## Class A — bot-checked / inaccessible / non-article (NOT prompt-fixable)

11 rows came back fully empty because the landing page rejected the agent or
isn't an article. This caps achievable per-field at 78% on the v1.4 holdout
unless we change runtime, not prompt.

| No (DOI prefix) | Class | Notes from v1.4 run |
|---|---|---|
| 10.1016/0963-8695(91)90937-x | ScienceDirect bot-check | 1991 paper; bot-check on Linkinghub redirect. 3 retries, no metadata. |
| 10.1016/j.surfcoat.2023.129748 | ScienceDirect bot-check | Linkinghub → ScienceDirect, bot-protected. |
| 10.1016/0021-9673(93)80418-8 | ScienceDirect empty document | Linkinghub returned 0-char document in headless browser. |
| 10.1103/physrevb.44.3757 | APS HTTP 403 | Bot-check on journals.aps.org. |
| 10.1093/jaoac/60.2.289 | Oxford Academic HTTP 403 | Bot-check at doi.org redirect. |
| 10.1093/jee/97.2.646 | Oxford Academic HTTP 403 | Same. |
| 10.7202/1091893ar | Érudit Anubis BotStopper | Proof-of-work CAPTCHA. |
| 10.47405/mjssh.v6i11.1167 | Malaysian OJS reCAPTCHA | OJS site gated by reCAPTCHA. |
| 10.1097/00000441-183517330-00010 | Ovid host-root redirect | Article URL migrated; lands on oce.ovid.com root. |
| 10.1093/oed/4932880791 | OED dictionary entry | Not a scholarly article. |
| 10.1093/oed/5131921241 | OED dictionary entry | Not a scholarly article. |

**Action**: do not attempt to fix in v1.5. These need either (a) per-publisher
country-specific residential proxy rotation (`proxy_country_code` override),
(b) parent-DOI substitution like the No 81 fix, or (c) accept that the gold
standard for these rows should be `N/A` / empty across the board.

If the user audits human-goldie.csv to mark these as expected-empty, several
of them will flip from disagreement to match without any prompt change.

## Class B — abstract threshold borderline (re-tuning candidate, NOT prompt)

Sample seen on `10.1007/978-3-662-68645-4_10`: AI and human both start with the
exact same German text — "Bei Intoxikationen ist die Anamnese/Fremdanamnese
durch den Notarzt wichtig…" — but the text length and tail differ enough that
`difflib.SequenceMatcher.ratio()` falls below the 0.95 threshold.

There is already tooling for this: `parseland-eval/eval/scripts/tune_abstract_threshold.py`
(per CLAUDE.md non-negotiable #2). Re-running it against this holdout would
likely recover several borderline-abstract rows without changing the prompt.

**Action**: separate task, not a v1.5 prompt change. Estimate: 1-2pp lift on
abstract field.

## Class C — affiliation exact-string mismatch (PARTIAL prompt-fixable)

`rases` is the largest disagreement bucket (30/50). The comparator is exact
string match per shared author after `.strip()`. Common micro-disagreement
patterns from the v1.4 run:

- Punctuation: human has trailing periods, AI doesn't (or vice versa).
- Whitespace inside multi-affiliation strings (";", "; ", " ; ").
- Postal-code or street-address fragments included by AI but not human.
- Affiliation language: AI normalizes to English where human kept native.
- Order of multiple affiliations within one author's `rasses` field.

Some of these are the comparator being too strict; some are real prompt
divergence from v1.1. v1.4 trim removed v1.1's explicit examples of how
to format multi-affiliation strings.

**Action for v1.5**: re-introduce v1.1's affiliation-formatting examples
(separator, casing, language preservation). Not a comparator change.

## Class D — author-list miss (PARTIAL prompt-fixable)

16 rows have `authors` mismatch. Of those, 7 are AI-empty (Class A bot-check).
The remaining 9 are name-set differences — usually:

- AI dropped one or two co-authors in long lists.
- AI included an editor or affiliation contact as an author.
- AI used initials where human spelled full first names.

The 11KB→8KB v1.4 trim removed v1.1's "include all authors regardless of
position" explicit instruction. Restoring it should recover most of these.

**Action for v1.5**: restore v1.1's full author-list extraction depth.

## Class E — pdf_url canonicalization (NOT prompt-fixable, comparator constant)

25 disagreements but only 4 are AI-empty (extraction missed it). The other 21
are both-have-URL-but-canonicalized-differently:

- ScienceDirect signed S3 URLs (`X-Amz-Signature`, `X-Amz-Date`) — these
  expire and get re-signed. Human gold has stale signed URL; AI has fresh
  signed URL. The canonicalizer drops query params, so this *should* match,
  unless the path also rotates (it does, via `tid=spdf-...` segments
  embedded in the host). Worth verifying in `score/pdf_url.py`.
- MDPI vs publisher-direct: human has the `/pdf?version=...` form, AI returns
  the publisher landing PDF link. Both legit.

**Action**: not a v1.5 issue. Audit-side fix or canonicalizer relaxation.

## v1.5 patch list

To be implemented as `eval/prompts/ai-goldie-v1.5.md` if the user authorizes
another iteration:

1. **Restore v1.1's "extract all authors" explicit instruction** (Class D fix).
2. **Restore v1.1's affiliation-formatting examples**: per-author multi-affil
   separator, casing, language preservation (Class C fix).
3. **Keep v1.4's "no URL construction from DOI patterns" rule** (the +8pp
   pdf_url win).
4. **Keep v1.4's prompt size discipline** — don't bloat back to 11KB and
   re-trigger the POST `/sessions` timeout (already mitigated by 60→180s
   bump in `extract_batch_cloud.py`, but cleanliness still matters at scale).

Target: v1.5 should hit v1.1's per-field on authors/rases/CA/abstract while
keeping v1.4's pdf_url. That gets us to roughly authors 78 / rases 48 /
CA 70 / abstract 68 / pdf_url 50 / overall ~18, still well below 95% but
the best Pareto-improvement available via prompt-only changes.

## What 95% would actually require

A holdout 95% gate is unreachable on this holdout-50 distribution given
~22% bot-checked / non-article rows. Either:
1. **Resample holdout** to exclude pre-2000 ScienceDirect, OED dictionary
   entries, and Anubis-protected sites; OR
2. **Mark Class A rows as expected-empty in human-goldie** so they score
   as match, not miss; OR
3. **Lower the gate** for Phase D (e.g., 80% per-field on authors/abstract,
   60% on rases/pdf_url) and document the rationale.

Each is a user-side decision, not an autonomous one.
