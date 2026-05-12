# Tier 1.5 — Taxicab Re-Harvest Endpoint

## What this is

A POST endpoint on the Taxicab harvester that triggers a **fresh scrape** of the underlying DOI's landing page. After the POST returns, the refreshed HTML is readable via the standard `GET /taxicab/doi/<DOI>` endpoint.

This was discovered 2026-05-12 and **supersedes memory entry `6918`** (2026-05-08) which audited the public-facing API and concluded "Taxicab API Is Read-Only Cache." That audit only walked code references in this repo — the harvester load balancer's POST endpoint isn't documented in `eval/parseland_eval/api.py` or `eval/scripts/extract_via_taxicab.py`, both of which know only about the GET path. The endpoint exists; the audit just didn't find it.

## The endpoint

```
POST {TAXICAB_HARVESTER_URL}/taxicab
Content-Type: application/json

{
  "native_id": "<doi>",
  "native_id_namespace": "doi",
  "url": "https://doi.org/<doi>"
}
```

The production URL is the AWS ELB:

```
http://harvester-load-balancer-366186003.us-east-1.elb.amazonaws.com
```

Set as `$TAXICAB_HARVESTER_URL` in BUX's `.env`.

After the POST returns 200, the refreshed page lands at:

```
GET {TAXICAB_HARVESTER_URL}/taxicab/doi/<doi>
```

## Manual smoke test

```bash
DOI="10.1016/0021-9673(93)80418-8"

# 1. Trigger re-harvest
curl -X POST $TAXICAB_HARVESTER_URL/taxicab \
  -H "Content-Type: application/json" \
  -d "{\"native_id\":\"$DOI\",\"native_id_namespace\":\"doi\",\"url\":\"https://doi.org/$DOI\"}"

# 2. Wait a few seconds, then read refreshed cache
sleep 5
curl $TAXICAB_HARVESTER_URL/taxicab/doi/$DOI | head -50
```

## When to invoke

The orchestrator (`runtime/run_10k_on_bux.sh`) invokes Tier 1.5 automatically when:
- A row finished Tier 1 (cached HTML + Claude) AND
- That row has `Authors`, `Abstract`, AND `PDF URL` all empty

For these rows, the most plausible cause is a stale or thin cached HTML capture. A fresh harvest may surface the missing content.

**Tier 1.5 does NOT bypass bot walls.** If the page is behind Cloudflare / login, the harvester gets the same blocked response. Bot-walled rows still need Tier 2 (live-fetch via real Chrome) — but the iter-R comparator will correctly label them as `iter-R:bot-check` so they don't count as failures.

## What happens after re-harvest

The orchestrator builds a new input CSV containing only the re-harvested DOIs (with their extraction columns reset to blank), then re-runs `extract_via_taxicab.py` on it. The resulting fills are merged into the post-Tier-1 baseline with a `iter-R:reharvest-recovered` label appended to the row's `Notes`.

Cost: $0 per POST (HTTP only) + ~$0.05–0.15 per re-extracted DOI (the Claude call on the refreshed HTML).

## Rate limits and caution

**Empirical only.** The harvester team hasn't published a rate-limit. The orchestrator defaults to concurrency 4 for Tier 1.5 re-harvests — conservative. If the harvester returns 429, the script logs `status: rate-limited` and the orchestrator continues without that row (it'll fall through to Tier 2 instead).

If you see widespread 429s, lower concurrency further or add a backoff. There's currently no exponential backoff implemented — that's a v2 improvement if needed.

## Implementation

See `runtime/taxicab_reharvest.py`. ~190 lines, thread-pool concurrent, JSONL output with per-DOI status:

- `refreshed` — POST succeeded AND the GET returned content with a new fingerprint
- `unchanged` — POST succeeded but the GET returned the same content (cache already current)
- `timeout` — POST succeeded but the GET kept returning the pre-POST fingerprint until timeout
- `rate-limited` — POST returned 429
- `harvester-5xx` — POST returned a server error
- `post-error` — network exception on the POST
- `dry-run` — `--dry-run` flag was set; nothing actually happened

## Self-test before deploying to BUX

```bash
# From local Mac (works without BUX since the harvester is publicly reachable)
TAXICAB_HARVESTER_URL=http://harvester-load-balancer-366186003.us-east-1.elb.amazonaws.com \
    eval/.venv/bin/python eval/browser-use/runtime/taxicab_reharvest.py \
    --dois 10.5840/zfs19354333 \
    --output /tmp/reharvest-smoke.jsonl \
    --timeout 30
```

Expected: `status: refreshed` or `status: unchanged` (either is fine — the endpoint is alive).

## Related memory entries

- `project_taxicab_reharvest_endpoint.md` — the endpoint discovery + supersession note
- `6918` (legacy, superseded) — the original "read-only" audit; preserved for history but no longer authoritative

## Source

User-provided 2026-05-12. Endpoint + payload shape verified manually with the `10.1016/0021-9673(93)80418-8` example.
