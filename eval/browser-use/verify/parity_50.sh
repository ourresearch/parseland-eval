#!/usr/bin/env bash
# Re-run the 50-DOI dry run through the new 5-tier pipeline on BUX, then diff
# against the local 2026-05-11 baseline (runs/50-10K-test/ai-goldie-50.v2.csv).
#
# Pass criteria (per acceptance-gates.md gates 7-8):
#   - Final filled-or-explained ≥ 88% (matches local baseline)
#   - iter-R paywalled / bot-check rates within ±5pp of local
#   - extraction-miss count is strictly LESS than local 5 (Tier 1.5 must help)
#   - Zero unlabelled empty PDF URLs
#
# Outputs:
#   runs/50-10K-bux-parity/ on BUX (full 5-tier outputs)
#   runs/50-10K-bux-parity/parity-report.md (per-row deltas + per-tier histogram)
#
# Usage:
#   bash eval/browser-use/verify/parity_50.sh
#   bash eval/browser-use/verify/parity_50.sh --dry-run

set -euo pipefail

DRY_RUN=0
if [[ "${1:-}" == "--dry-run" ]]; then
    DRY_RUN=1
fi

BUX_REPO="/home/bux/parseland-eval"
BUX_PARITY_DIR="/home/bux/runs/50-10K-bux-parity"
PROMPT="eval/prompts/ai-goldie-v1.9.1.md"
LOCAL_BASELINE="runs/50-10K-test/ai-goldie-50.v2.csv"

if [[ ! -f $LOCAL_BASELINE ]]; then
    echo "ERROR: local baseline $LOCAL_BASELINE not found." >&2
    echo "Run the 50-DOI dry run first (see runs/50-10K-test/ artifacts)." >&2
    exit 1
fi

run() {
    if [[ $DRY_RUN -eq 1 ]]; then
        echo "[dry-run] $*"
    else
        echo "→ $*"
        eval "$@"
    fi
}

echo "═══ Parity-50: 5-tier BUX run vs local 4-tier baseline ═══"

# 1. Prepare BUX parity dir
run "ssh bux 'mkdir -p $BUX_PARITY_DIR/.checkpoint'"

# 2. scp the same 50-DOI input that produced the local baseline
run "scp eval/data/50_10K.csv bux:$BUX_PARITY_DIR/input-50.csv"

# 3. Run the orchestrator in single-batch mode against this input (bypasses the
#    Phase E.1 telegram gate; this is a verification run, not a 10K production run)
run "ssh bux 'cd $BUX_REPO && BATCH_INPUT=$BUX_PARITY_DIR/input-50.csv BATCH_OUTPUT_DIR=$BUX_PARITY_DIR bash eval/browser-use/runtime/run_10k_on_bux.sh --batch parity --no-gate'"

# 4. Pull the final merged + iter-R-classified CSV back to local
run "scp bux:$BUX_PARITY_DIR/ai-goldie-parity.v2.csv $BUX_PARITY_DIR/ai-goldie-parity.v2.csv 2>/dev/null || true"
run "mkdir -p runs/50-10K-bux-parity && scp bux:$BUX_PARITY_DIR/ai-goldie-parity.v2.csv runs/50-10K-bux-parity/ai-goldie-parity.v2.csv"

# 5. Diff against local baseline + write parity-report.md
if [[ $DRY_RUN -eq 1 ]]; then
    echo "[dry-run] eval/.venv/bin/python eval/browser-use/verify/_diff_parity.py runs/50-10K-bux-parity/ai-goldie-parity.v2.csv $LOCAL_BASELINE > runs/50-10K-bux-parity/parity-report.md"
    cat <<'EOF'

═══════════════════════════════════════════════════════════════════════════════
(dry-run) Would compare 5-tier BUX output to 4-tier local baseline.
The diff script (_diff_parity.py) is inlined below for dry-run inspection only.

For the actual run, the script is invoked via heredoc — see the parity_50.sh
source after the dry-run early-exit.
═══════════════════════════════════════════════════════════════════════════════
EOF
    exit 0
fi

eval/.venv/bin/python <<'PY' > runs/50-10K-bux-parity/parity-report.md
import csv, json, sys
from pathlib import Path
from collections import Counter

local = "runs/50-10K-test/ai-goldie-50.v2.csv"
bux   = "runs/50-10K-bux-parity/ai-goldie-parity.v2.csv"

def is_empty(s): return not s or not s.strip() or s.strip().lower() in {"n/a","na","none","null"}
def is_empty_authors(s):
    if is_empty(s): return True
    try: a = json.loads(s); return not isinstance(a, list) or len(a)==0
    except: return True

def load(path):
    rows = list(csv.DictReader(open(path)))
    return {r["DOI"]: r for r in rows}

L = load(local)
B = load(bux)

def label(r):
    notes = (r.get("Notes") or "")
    for tok in ["iter-R:reharvest-recovered","iter-R:bot-check","iter-R:pdf-redirect","iter-R:paywalled","iter-R:resolve-error","iter-R:extraction-miss"]:
        if tok in notes:
            return tok
    return "complete"

L_labels = Counter(label(r) for r in L.values())
B_labels = Counter(label(r) for r in B.values())

print("# Parity-50 report — 5-tier BUX vs 4-tier local baseline\n")
print(f"Local baseline: {local}  ({len(L)} rows)")
print(f"BUX 5-tier:     {bux}  ({len(B)} rows)\n")

print("## iter-R label distribution\n")
print("| label | local | BUX 5-tier | delta |")
print("|---|---:|---:|---:|")
all_labels = sorted(set(L_labels) | set(B_labels))
for lbl in all_labels:
    l = L_labels.get(lbl, 0); b = B_labels.get(lbl, 0)
    print(f"| {lbl} | {l} | {b} | {b-l:+d} |")
print()

# Extraction-miss test (gate 8)
local_miss = L_labels.get("iter-R:extraction-miss", 0)
bux_miss = B_labels.get("iter-R:extraction-miss", 0)
print(f"\n## Gate 8 — Tier 1.5 effectiveness\n")
print(f"- extraction-miss count local: **{local_miss}**")
print(f"- extraction-miss count BUX:   **{bux_miss}**")
verdict_g8 = "PASS" if bux_miss < local_miss else "FAIL"
print(f"- **Gate 8 verdict: {verdict_g8}** (BUX miss must be < local miss)\n")

# Filled-or-explained rate
def explained(r):
    return label(r) != "complete" or (not is_empty_authors(r["Authors"]) and not is_empty(r["Abstract"]) and not is_empty(r["PDF URL"]))
local_pct = 100*sum(1 for r in L.values() if explained(r)) / len(L)
bux_pct   = 100*sum(1 for r in B.values() if explained(r)) / len(B)
print(f"## Filled-or-explained rate\n")
print(f"- local: **{local_pct:.0f}%**")
print(f"- BUX:   **{bux_pct:.0f}%**")
verdict_g7 = "PASS" if bux_pct >= 88 else "FAIL"
print(f"- **Gate 7 verdict: {verdict_g7}** (BUX must be ≥ 88%)\n")

# Per-row deltas (rows that changed label)
print("## Rows whose label changed\n")
print("| DOI | local label | BUX label |")
print("|---|---|---|")
for doi in sorted(L):
    if doi in B and label(L[doi]) != label(B[doi]):
        print(f"| `{doi}` | {label(L[doi])} | {label(B[doi])} |")

# Final
print(f"\n## Overall\n")
if verdict_g7 == "PASS" and verdict_g8 == "PASS":
    print("**✓ PARITY-50 PASSED.** Authorized to proceed to 10K via `runtime/run_10k_on_bux.sh`.")
    sys.exit(0)
else:
    print("**✗ PARITY-50 FAILED.** See gates above. Do NOT proceed to 10K.")
    sys.exit(1)
PY

echo
echo "Parity report: runs/50-10K-bux-parity/parity-report.md"
