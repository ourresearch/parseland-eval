#!/bin/bash
# Phase E.2 orchestrator — runs batches START..END through the full pipeline.
#
# Per batch:
#   1. Tier 1 + Sonnet+Opus judge       extract_with_judge.py     ~10 min, ~$30
#   2. Build Tier 2 targets             (empties from baseline)
#   3. Tier 2 live-fetch via CDP        live_fetch_empty.py       ~10-15 min, ~$10
#   4. Merge (gold-aware, --gold)       merge_livefetch.py         <1 min
#   5. iter-R classify                  iter_r_classify.py         ~1 min
#   6. Mojibake post-pass               fix_mojibake.py            <1 min
#
# Note: extract_with_judge.py and extract_via_taxicab.py both hardcode the
# output filename to "ai-goldie-1.csv". Within each batch directory we use
# that canonical name. Each batch lives in runs/10k/batch-${N}-judge/.
#
# Resumable per-step: each step checks if its output exists and skips if so,
# so a partial batch (e.g., judge done, merge missing) recovers without
# re-burning the $30 judge cost.
#
# Halt on any per-step failure (set -e) per the no-silent-failures rule.
#
# Usage:
#   bash eval/scripts/run_phase_e2.sh 2 5     # batches 2-5
#   bash eval/scripts/run_phase_e2.sh 6 100   # batches 6-100

set -e
set -a; source eval/.env; set +a

START=${1:-2}
END=${2:-5}
ROOT=$(pwd)
PROMPT=eval/prompts/ai-goldie-v1.9.2.md
GOLD=eval/human-goldie.csv

echo "=== Phase E.2 launching batches $START..$END at $(date +%H:%M:%S) ==="

for N in $(seq $START $END); do
    BATCH_DIR=runs/10k/batch-${N}-judge
    SOURCE=eval/data/ai-goldie-${N}.csv
    BASELINE=${BATCH_DIR}/ai-goldie-1.csv          # judge hardcodes "1"
    MERGED=${BATCH_DIR}/ai-goldie-1.merged.csv
    V2=${BATCH_DIR}/ai-goldie-1.v2.csv
    TARGETS=${BATCH_DIR}/livefetch-targets.json
    DELTA=${BATCH_DIR}/livefetch-delta.csv

    echo ""
    echo "=== Batch $N starting at $(date +%H:%M:%S) ==="

    if [ -f "$V2" ]; then
        echo "  V2 already present, skipping batch"
        continue
    fi

    mkdir -p $BATCH_DIR

    # Step 1: Tier 1 + judge — skip if baseline already exists
    if [ -f "$BASELINE" ]; then
        echo "  [1/5] judge baseline already present, skipping"
    else
        echo "  [1/5] Sonnet+Opus judge tier..."
        eval/.venv/bin/python eval/scripts/extract_with_judge.py \
            --source $SOURCE \
            --output-dir ${ROOT}/${BATCH_DIR} \
            --prompt $PROMPT \
            --concurrency 5 2>&1 | tail -2

        if [ ! -f "$BASELINE" ]; then
            echo "  FAIL: $BASELINE not produced" >&2
            exit 2
        fi
    fi

    # Step 2: Build Tier 2 targets (fully-empty rows only)
    if [ -f "$TARGETS" ]; then
        echo "  [2/5] targets already present"
    else
        echo "  [2/5] Building Tier 2 targets..."
        eval/.venv/bin/python - <<PYEOF
import csv, json
from pathlib import Path
targets = []
with open("${BASELINE}") as f:
    for r in csv.DictReader(f):
        a = bool(r.get('Authors','').strip()) and r['Authors'].strip() != '[]'
        ab = bool(r.get('Abstract','').strip())
        p = bool(r.get('PDF URL','').strip())
        if not (a or ab or p):
            targets.append({'doi': r['DOI'].strip(), 'link': r.get('Link','').strip(), 'reason': 'empty'})
Path("${TARGETS}").write_text(json.dumps(targets, indent=2))
print(f"  {len(targets)} empty targets")
PYEOF
    fi

    # Step 3: Tier 2 — skip if delta exists
    if [ -f "$DELTA" ]; then
        echo "  [3/5] Tier 2 delta already present, skipping"
    else
        N_TARGETS=$(eval/.venv/bin/python -c "import json; print(len(json.load(open('${TARGETS}'))))")
        if [ "$N_TARGETS" -gt 0 ]; then
            echo "  [3/5] Tier 2 live-fetch on $N_TARGETS targets..."
            eval/.venv/bin/python eval/scripts/live_fetch_empty.py \
                --targets $TARGETS \
                --prompt $PROMPT \
                --output $DELTA \
                --concurrency 8 2>&1 | tail -2
        else
            echo "  [3/5] No Tier 2 targets; writing empty delta"
            echo "No,DOI,Link,Authors,Abstract,PDF URL,Status,Notes,Has Bot Check,Resolves To PDF,broken_doi,no english" > $DELTA
        fi
    fi

    # Step 4: Merge
    if [ -f "$MERGED" ]; then
        echo "  [4/5] merged CSV already present, skipping"
    else
        echo "  [4/5] Merge (gold-aware)..."
        eval/.venv/bin/python eval/scripts/merge_livefetch.py \
            --baseline $BASELINE \
            --deltas $DELTA \
            --output $MERGED \
            --gold $GOLD 2>&1 | tail -2
    fi

    # Step 5: iter-R classify + mojibake fix
    echo "  [5/5] iter-R classify + mojibake fix..."
    eval/.venv/bin/python eval/scripts/iter_r_classify.py \
        --input $MERGED \
        --output $V2 2>&1 | tail -3

    eval/.venv/bin/python eval/scripts/fix_mojibake.py \
        --input $V2 \
        --output $V2 2>&1 | tail -1

    if [ ! -f "$V2" ]; then
        echo "  FAIL: $V2 not produced" >&2
        exit 3
    fi

    echo "  ✓ batch $N done at $(date +%H:%M:%S)"
done

echo ""
echo "=== Phase E.2 batches $START..$END DONE at $(date +%H:%M:%S) ==="
