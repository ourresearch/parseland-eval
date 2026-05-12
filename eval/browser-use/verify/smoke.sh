#!/usr/bin/env bash
# 4-gate smoke test on BUX. Each gate ssh's into BUX and runs a 1-DOI version
# of the actual pipeline. Exit non-zero on any failure with stderr pointing to
# the runbook section that explains how to fix.
#
# Usage:
#   bash eval/browser-use/verify/smoke.sh            # actually run on BUX
#   bash eval/browser-use/verify/smoke.sh --dry-run  # print all commands without executing

set -euo pipefail

DRY_RUN=0
if [[ "${1:-}" == "--dry-run" ]]; then
    DRY_RUN=1
fi

BUX_REPO="/home/bux/parseland-eval"
BUX_TMP="/tmp/bux-smoke"
PROMPT="eval/prompts/ai-goldie-v1.9.1.md"

# Locate fixtures relative to this script's location
FIXTURES_DIR="eval/browser-use/verify/fixtures"

run_gate() {
    local name="$1"
    local cmd="$2"
    local runbook_section="$3"
    echo
    echo "═══ Gate: $name ═══"
    if [[ $DRY_RUN -eq 1 ]]; then
        echo "[dry-run] $cmd"
        return 0
    fi
    if eval "$cmd"; then
        echo "✓ PASS: $name"
    else
        echo "✗ FAIL: $name" >&2
        echo "  See: ../docs/runbook.md#$runbook_section" >&2
        exit 1
    fi
}

# Pre-flight: confirm BUX is reachable
if [[ $DRY_RUN -eq 0 ]]; then
    if ! ssh -o BatchMode=yes -o ConnectTimeout=10 bux echo connected >/dev/null 2>&1; then
        echo "ERROR: ssh bux fails. Check ~/.ssh/config and BUX status." >&2
        echo "See setup/01-provision.md step 2." >&2
        exit 1
    fi
    ssh bux "mkdir -p $BUX_TMP"
fi

# Gate 3 (per acceptance-gates.md) — 1-DOI Tier 1
run_gate "Tier 1 smoke (Routledge known-good)" \
    "ssh bux 'cd $BUX_REPO && eval/.venv/bin/python eval/scripts/extract_via_taxicab.py --source $FIXTURES_DIR/tier1-1doi.csv --output-dir $BUX_TMP/tier1 --prompt $PROMPT --concurrency 1' && \
     ssh bux 'test -s $BUX_TMP/tier1/ai-goldie-1.csv' && \
     ssh bux 'eval/.venv/bin/python -c \"import csv,sys; r=next(csv.DictReader(open(\\\"$BUX_TMP/tier1/ai-goldie-1.csv\\\"))); sys.exit(0 if r[\\\"Authors\\\"].strip() else 1)\"'" \
    "tier-1-returns-0-successes"

# Gate 4 — 1-DOI Tier 1.5 (re-harvest + re-extract)
run_gate "Tier 1.5 smoke (Taxicab re-harvest)" \
    "ssh bux 'cd $BUX_REPO && eval/.venv/bin/python eval/browser-use/runtime/taxicab_reharvest.py --dois 10.5840/zfs19354333 --output $BUX_TMP/reharvest.jsonl --timeout 90' && \
     ssh bux 'cd $BUX_REPO && eval/.venv/bin/python eval/scripts/extract_via_taxicab.py --source $FIXTURES_DIR/tier15-1doi.csv --output-dir $BUX_TMP/post-reharvest --prompt $PROMPT --concurrency 1' && \
     ssh bux 'grep -q \"refreshed\\|completed\" $BUX_TMP/reharvest.jsonl'" \
    "tier-1-5-hangs-on-post"

# Gate 5 — 1-DOI Tier 2 (Browser Harness CDP)
run_gate "Tier 2 smoke (Browser Harness CDP)" \
    "ssh bux 'cd $BUX_REPO && eval/.venv/bin/python eval/scripts/live_fetch_empty.py --targets $FIXTURES_DIR/tier2-1doi.json --prompt $PROMPT --output $BUX_TMP/livefetch-delta.csv --concurrency 1' && \
     ssh bux 'test -s $BUX_TMP/livefetch-delta.csv'" \
    "tier-2-hangs"

# Gate 6 — Telegram /ping
run_gate "Telegram smoke (/ping echoes back)" \
    "ssh bux 'cd $BUX_REPO && eval/.venv/bin/python eval/browser-use/runtime/telegram_ping.py --test'" \
    "telegram-go-not-received"

cat <<EOF

═══════════════════════════════════════════════════════════════════════════════
All 4 smoke gates passed.

Next: bash eval/browser-use/verify/parity_50.sh
═══════════════════════════════════════════════════════════════════════════════
EOF
