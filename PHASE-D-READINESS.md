# Phase D 10K launch readiness check — 2026-04-30

**Verdict: 1 small blocker. Locked Taxicab+Claude pipeline is ready otherwise.**

| # | Item | Status | Notes |
|---|---|---|---|
| 1 | `eval/scripts/sample_10k_dois.py` | ✅ exists | Crossref random sampler, resumable, polite-pool rate-limited. |
| 2 | Dedup vs human gold | ⚠️ **BLOCKER** | Script expects `eval/gold-standard.csv` (line 32), but repo only has `gold-standard.json`. Without dedup, the 10K sample WILL include some of the already-audited 100 DOIs. |
| 3 | `eval/scripts/extract_via_taxicab.py` | ✅ exists + locked | Two-tier: free citation_* meta tags → Claude Sonnet fallback. `--source` accepts any CSV → trivially supports batch-1 slicing. |
| 4 | Browser-use Cloud credits | ✅ N/A | Not used by the LOCKED pipeline. Only matters for the rejected `extract_batch_cloud.py` path. |
| 5 | `eval/data/` dir | ✅ auto-created | Created on first sample_10k run. |
| 6 | Batch-1 gate mechanism | ✅ trivial | After sampling, `head -101 eval/data/ai-goldie-source-10k.csv > eval/data/ai-goldie-batch-1.csv` (100 rows + header) and pass that as `--source` for first run. Review, then run remaining 9,900. |
| 7 | Cost estimate | ✅ ~$200–500 LLM | Tier log on holdout-50: $0.045–$0.075/DOI on Claude tier; many DOIs short-circuit on free citation_* meta tags (no LLM cost). 10K worst-case ≈ $750. Well under Jason's $5K cap. |
| 8 | Wall time | ✅ ~5–8 hours @ concurrency 10 | Tier log shows ~6–8s/DOI on Claude tier. At concurrency 10, 10K DOIs ≈ 10000 * 7s / 10 = ~2 hours net; allow overhead for retries + Crossref sample fetches. |
| 9 | v1.8 prompt acceptance | ⏳ pending | Cannot launch 10K until v1.8 clears holdout-50 (rases ≥ 66%, authors ≥ 85% no regression, CA ≥ 75%). |

## To unblock #2

Two options. **Option A is faster and lower-risk.**

**Option A — one-time conversion (5 minutes)**:
```bash
eval/.venv/bin/python -c "
import csv, json
from pathlib import Path
data = json.load(open('eval/gold-standard.json'))
cols = list(data[0].keys())
with open('eval/gold-standard.csv', 'w', newline='') as f:
    w = csv.DictWriter(f, fieldnames=cols)
    w.writeheader()
    for r in data:
        # Authors stays as JSON-encoded string in CSV per existing convention
        if isinstance(r.get('Authors'), list):
            r['Authors'] = json.dumps(r['Authors'], ensure_ascii=False)
        w.writerow(r)
print(f'wrote eval/gold-standard.csv: {len(data)} rows')
"
```

**Option B — patch `load_gold_dois` to accept .json**: more invasive; touches the script. Skip unless we want CSV gone permanently.

## DO NOT do until v1.8 clears

- Do not run `sample_10k_dois.py` against the live Crossref API. The 10K Crossref sample alone takes 100+ API calls (~3 min minimum), is somewhat costly to redo, and burns goodwill on the polite pool. Wait for v1.8 acceptance.
- Do not kick off `extract_via_taxicab.py` on 10K. Run the 100-row batch-1 first, review with `inspect_affiliations.py`, gate before parallel batches.

## Launch sequence (when ready)

```bash
# 1. Convert gold to CSV (Option A above)
# 2. Sample 10K
eval/.venv/bin/python eval/scripts/sample_10k_dois.py --target 10000

# 3. Slice batch 1 (first 100 rows + header)
head -101 eval/data/ai-goldie-source-10k.csv > eval/data/ai-goldie-batch-1.csv

# 4. Run batch 1 with v1.8 prompt
eval/.venv/bin/python eval/scripts/extract_via_taxicab.py \
    --source eval/data/ai-goldie-batch-1.csv \
    --output-dir runs/ai-goldie-batch-1 \
    --prompt eval/prompts/ai-goldie-v1.8.md \
    --concurrency 10

# 5. REVIEW gates here — user inspects ai-goldie-batch-1/ai-goldie-1.csv
#    against the auditor's expectations before running batches 2-100.
```
