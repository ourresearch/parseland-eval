# Report field order

`goldie report` (and `report.py`) presents the five fields in this order:

```
rases > pdf_url > ca > abstract > authors
```

## Why this differs from the approved plan wording

The approved plan (`~/.claude/plans/polished-seeking-hedgehog.md`) wrote the order as
`rases > corresponding > abstract > pdf_url > authors`. The CLI uses a **different** order:
`rases > pdf_url > ca > abstract > authors`.

This is deliberate. The `openalex-goldie-extractor` skill — Casey's codified operating
principles, the current project source of truth for reporting — specifies:

> rases (affiliations) → most important · pdf_url · ca (corresponding) · abstract · authors → least important

When the plan wording and the skill disagree, the skill wins for reporting, because it
reflects how Casey actually prioritizes the fields (rases is both the most important and
the biggest gap; pdf_url is the next-hardest structural field). The divergence is recorded
here and in the `report.py` module docstring so the choice is auditable.

## Bar and splits

- Per-field bar: **85%** (gap-to-bar reported as `accuracy_all − 0.85`).
- Accuracy is reported on **all matched rows** AND on **fetch-OK rows** separately — never
  conflated (fetch success ≠ extraction accuracy).
- Misses are bucketed: **empty / punctuation-only / hallucination / dropped-detail**.
- Scoring uses the locked relaxed comparator (`eval/scripts/diff_goldie.py`), not a
  reimplementation.
