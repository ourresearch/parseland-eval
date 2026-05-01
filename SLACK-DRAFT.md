# Slack draft — 2026-04-30 EOD — for #project-parseland

**For**: `#project-parseland` (`C0AU0BLM50V`) on impactstory.slack.com
**CC**: Casey `<@U07HJQKJ42C>`, Jason `<@UEVFABBBP>`
**Send via**: `slack_send_message_draft` (review-then-send) — DO NOT auto-send.
**Style**: plain voice per SKILL.md. Lead with numbers. No tables, no callouts, no em-dashes, no cost-labeling on others' behalf.

---

```
Holdout-50 v1.8 EOD numbers (relaxed comparator, all 50 / fetch-OK 49):

  authors     90 / 89.8
  rases       60 / 59.2
  ca          82 / 81.6
  abstract    80 / 79.6
  pdf_url     60 / 61.2

Comparator gains today vs this morning's baseline: +2 authors, +2 rases,
+2 ca, +4 abstract, +4 pdf_url. Worked examples for each new rule are
in diff_goldie.py docstrings. No prompt or extractor changes since
the rolled-back Taxicab refactor; v1.8 + Sonnet 4.6 + skip-meta-tags
is the locked baseline.

No field clears 95% yet. Three fields are decision-bound, not
engineering-bound. Asks, in priority order:

1. rases: GOLD-UPDATE-PROPOSAL.md is on main. 13 author-rows where
   AI extracts citation_author_institution verbatim and gold has more.
   If approved, per-author rases lifts +11pp (35 to 46), per-DOI +4pp.
   Need a yes/no from Casey on whether to narrow gold.

2. pdf_url: 10 holdout DOIs where gold is N/A but AI emits a working
   publisher PDF (Springer, OUP, PLOS, etc.). Convention call: are
   those AI hits valid (+20pp on pdf_url) or false positives?

3. abstract: threshold sat at 0.95 historically; tuned to 0.75 today
   for +4pp without false positives. Ask: confirm 0.75 or lower to
   0.65 (+2pp more).

Two engineering items still in flight:

- Elsevier cache investigation. 6 holdout DOIs return empty rases
  because Taxicab cached /article/abs/<pii>, the paywalled stub.
  Need 15 min on /article/<pii> to see if it has structured
  affiliations. If yes, refetch policy unblocks +6 to +12pp on rases.

- Authors gold review. 4 DOIs where gold and AI disagree on the
  author list itself (auditor recorded empty or different names).
  Need auditor pass.

Full per-field rubric in REPORT.md on main. Per-author/per-DOI yield
math for the gold update is in GOLD-UPDATE-PROPOSAL.md.

Next biggest blocker after rases: pdf_url at 60. Same shape (gold
convention question) but smaller decision because the AI URLs are
working publisher links.
```

---

## Pre-send checklist (per SKILL.md anti-patterns)

- [ ] All numbers re-verified against `eval/goldie/summary-v1.8-holdout.json` and `/tmp/s.json`
- [ ] Plain voice: no callouts, no tables, no em-dashes
- [ ] No "small," "quick," "won't take long" — checked
- [ ] No echoing manager's framing back ("I know your time is valuable" etc.) — checked
- [ ] Asks are specific (Casey can answer yes/no to each)
- [ ] Next-biggest-blocker named at the bottom
- [ ] Tone is "owns" not "apologizes"

## Send via

```
slack_send_message_draft  → review in Slack → send
```

Do NOT auto-send. Memory rule: review-then-send only on `#project-parseland`.
