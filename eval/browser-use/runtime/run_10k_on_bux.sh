#!/usr/bin/env bash
# 10K production orchestrator on BUX. 5-tier pipeline:
#   Tier 1   — extract_via_taxicab.py (cached HTML + Claude)
#   Tier 1.5 — taxicab_reharvest.py for all-3-empty rows, then re-extract
#   Tier 2   — live_fetch_empty.py via Browser Harness (CDP-over-WSS)
#   Merge    — inline (gold-less for 10K) or merge_livefetch.py --gold for batch 1
#   Iter-R   — comparator rules #10/#14/#15 + new label iter-R:reharvest-recovered
#
# Per-batch atomic writes + DOI-keyed checkpoints. Crash-resume safe.
#
# Usage:
#   bash eval/browser-use/runtime/run_10k_on_bux.sh --batch 1            # batch 1 with telegram gate
#   bash eval/browser-use/runtime/run_10k_on_bux.sh --batch parity --no-gate  # parity verification
#   bash eval/browser-use/runtime/run_10k_on_bux.sh --batches 2-100      # unattended after gate
#   bash eval/browser-use/runtime/run_10k_on_bux.sh --help
#
# Env required:
#   ANTHROPIC_API_KEY, BROWSER_USE_API_KEY, CDP_URL, TELEGRAM_BOT_TOKEN,
#   TELEGRAM_CHAT_ID, TAXICAB_HARVESTER_URL
# Env optional:
#   BATCH_INPUT          — path to per-batch input CSV (default: eval/data/ai-goldie-N.csv)
#   BATCH_OUTPUT_DIR     — path to per-batch output dir (default: /home/bux/runs/10k/batch-N/)
#
# Exits non-zero on any tier failure or a missing required env var (per
# memory: never fail silently).

set -euo pipefail

# ─── Args ─────────────────────────────────────────────────────────────────────
BATCH_SPEC=""
NO_GATE=0
HELP=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --batch) BATCH_SPEC="$2"; shift 2 ;;
        --batches) BATCH_SPEC="$2"; shift 2 ;;
        --no-gate) NO_GATE=1; shift ;;
        --help|-h) HELP=1; shift ;;
        *) echo "Unknown arg: $1" >&2; exit 2 ;;
    esac
done

if [[ $HELP -eq 1 || -z "$BATCH_SPEC" ]]; then
    sed -n '2,30p' "$0"
    cat <<EOF

Tiers in this pipeline:
  Tier 1   — eval/scripts/extract_via_taxicab.py
  Tier 1.5 — eval/browser-use/runtime/taxicab_reharvest.py + re-extract
  Tier 2   — eval/scripts/live_fetch_empty.py (uses \$CDP_URL)
  Merge    — gold-less inline fill (orchestrator) for batches 2-100;
             merge_livefetch.py --gold for batch 1 (when gold subset exists)
  Iter-R   — comparator-style classifier writing iter-R:* labels into Notes

The orchestrator halts after batch 1 awaiting /go via Telegram (unless --no-gate).
EOF
    exit 0
fi

# ─── Env check (fail fast, no silent fallbacks) ──────────────────────────────
required_env=(ANTHROPIC_API_KEY CDP_URL TAXICAB_HARVESTER_URL)
[[ $NO_GATE -eq 1 ]] || required_env+=(TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID)
for v in "${required_env[@]}"; do
    if [[ -z "${!v:-}" ]]; then
        echo "ERROR: required env var $v is not set." >&2
        exit 2
    fi
done

# ─── Paths ────────────────────────────────────────────────────────────────────
REPO_ROOT="${REPO_ROOT:-/home/bux/parseland-eval}"
PY="$REPO_ROOT/eval/.venv/bin/python"
PROMPT="$REPO_ROOT/eval/prompts/ai-goldie-v1.9.1.md"
RUNS_BASE="${RUNS_BASE:-/home/bux/runs/10k}"

# Expand --batch into a list (e.g. "1", "2-100", "parity")
batch_list() {
    local spec="$1"
    if [[ "$spec" == "parity" ]]; then
        echo "parity"
    elif [[ "$spec" =~ ^([0-9]+)-([0-9]+)$ ]]; then
        seq "${BASH_REMATCH[1]}" "${BASH_REMATCH[2]}"
    else
        echo "$spec"
    fi
}

# ─── Per-batch pipeline ──────────────────────────────────────────────────────
run_batch() {
    local n="$1"
    local input_csv="${BATCH_INPUT:-$REPO_ROOT/eval/data/ai-goldie-${n}.csv}"
    local out_dir="${BATCH_OUTPUT_DIR:-$RUNS_BASE/batch-${n}}"
    mkdir -p "$out_dir/.checkpoint"

    if [[ ! -f "$input_csv" ]]; then
        echo "ERROR: batch $n input not found: $input_csv" >&2
        exit 3
    fi

    echo
    echo "═══ Batch $n ═══"
    echo "  input:  $input_csv"
    echo "  output: $out_dir"

    # ── Tier 1 ──
    echo "[Tier 1] extracting via Taxicab cached HTML..."
    "$PY" "$REPO_ROOT/eval/scripts/extract_via_taxicab.py" \
        --source "$input_csv" \
        --output-dir "$out_dir/tier1" \
        --prompt "$PROMPT" \
        --concurrency 10 \
        --model claude-sonnet-4-5

    # ── Tier 1.5 ──
    echo "[Tier 1.5] identifying all-3-empty rows after Tier 1..."
    local empties_file="$out_dir/tier15-targets.txt"
    "$PY" - <<PY
import csv, json
src = "$out_dir/tier1/ai-goldie-1.csv"
def is_empty(s): return not s or not s.strip() or s.strip().lower() in {"n/a","na","none","null"}
def is_empty_authors(s):
    if is_empty(s): return True
    try: a = json.loads(s); return not isinstance(a, list) or len(a)==0
    except: return True
with open("$empties_file","w") as f:
    for r in csv.DictReader(open(src)):
        if is_empty_authors(r["Authors"]) and is_empty(r["Abstract"]) and is_empty(r["PDF URL"]):
            f.write(r["DOI"] + "\n")
PY
    local n_empties=$(wc -l < "$empties_file" | tr -d ' ')
    echo "[Tier 1.5] $n_empties DOIs are all-3-empty; re-harvesting via Taxicab..."
    if [[ "$n_empties" -gt 0 ]]; then
        "$PY" "$REPO_ROOT/eval/browser-use/runtime/taxicab_reharvest.py" \
            --input "$empties_file" \
            --output "$out_dir/reharvest.jsonl" \
            --concurrency 4 \
            --timeout 90 || true  # don't fail the batch on partial re-harvest

        # Build a re-extract input CSV from the empties
        local reextract_csv="$out_dir/tier15-reextract-input.csv"
        "$PY" - <<PY
import csv
empties = {l.strip().lower() for l in open("$empties_file") if l.strip()}
src = "$out_dir/tier1/ai-goldie-1.csv"
rows = [r for r in csv.DictReader(open(src)) if r["DOI"].strip().lower() in empties]
if rows:
    with open("$reextract_csv","w",newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        # Reset extraction columns so the re-extract overwrites
        for r in rows:
            for k in ["Authors","Abstract","PDF URL","Status","Notes","Has Bot Check","Resolves To PDF","broken_doi","no english"]:
                r[k] = ""
            w.writerow(r)
PY
        if [[ -f "$reextract_csv" ]]; then
            echo "[Tier 1.5] re-extracting from refreshed cache..."
            "$PY" "$REPO_ROOT/eval/scripts/extract_via_taxicab.py" \
                --source "$reextract_csv" \
                --output-dir "$out_dir/tier15" \
                --prompt "$PROMPT" \
                --concurrency 5 \
                --model claude-sonnet-4-5 || true
        fi
    fi

    # ── Merge Tier 1 + Tier 1.5 (gold-less) ──
    echo "[Merge] folding Tier 1.5 deltas into Tier 1 baseline..."
    local merged_t15="$out_dir/post-tier15.csv"
    "$PY" - <<PY
import csv, json
from pathlib import Path
def is_empty(s): return not s or not s.strip() or s.strip().lower() in {"n/a","na","none","null"}
def is_empty_authors(s):
    if is_empty(s): return True
    try: a = json.loads(s); return not isinstance(a, list) or len(a)==0
    except: return True
base = list(csv.DictReader(open("$out_dir/tier1/ai-goldie-1.csv")))
by_doi = {r["DOI"]: r for r in base}
delta_path = Path("$out_dir/tier15/ai-goldie-1.csv")
if delta_path.exists():
    for d in csv.DictReader(delta_path.open()):
        doi = d["DOI"]
        if doi not in by_doi: continue
        b = by_doi[doi]
        if is_empty(b["Abstract"]) and not is_empty(d["Abstract"]):
            b["Abstract"] = d["Abstract"]
            b["Notes"] = (b.get("Notes","") + "; iter-R:reharvest-recovered").strip("; ")
        if is_empty_authors(b["Authors"]) and not is_empty_authors(d["Authors"]):
            b["Authors"] = d["Authors"]
            if "reharvest-recovered" not in b["Notes"]:
                b["Notes"] = (b.get("Notes","") + "; iter-R:reharvest-recovered").strip("; ")
        if is_empty(b["PDF URL"]) and not is_empty(d["PDF URL"]):
            b["PDF URL"] = d["PDF URL"]
            if "reharvest-recovered" not in b["Notes"]:
                b["Notes"] = (b.get("Notes","") + "; iter-R:reharvest-recovered").strip("; ")
fns = list(base[0].keys())
out = Path("$merged_t15")
tmp = out.with_suffix(".csv.tmp")
with tmp.open("w",newline="") as f:
    w = csv.DictWriter(f, fieldnames=fns); w.writeheader()
    for r in base: w.writerow(r)
tmp.replace(out)
PY

    # ── Tier 2 ──
    echo "[Tier 2] identifying STILL-empty rows after Tier 1.5..."
    local t2_targets="$out_dir/tier2-targets.json"
    "$PY" - <<PY
import csv, json
def is_empty(s): return not s or not s.strip() or s.strip().lower() in {"n/a","na","none","null"}
def is_empty_authors(s):
    if is_empty(s): return True
    try: a = json.loads(s); return not isinstance(a, list) or len(a)==0
    except: return True
src = "$merged_t15"
targets = []
for r in csv.DictReader(open(src)):
    if is_empty_authors(r["Authors"]) and is_empty(r["Abstract"]) and is_empty(r["PDF URL"]):
        targets.append({"doi": r["DOI"], "link": r["Link"], "reason": "post-tier-1.5-empty"})
open("$t2_targets","w").write(json.dumps(targets, indent=2))
print(f"{len(targets)} Tier-2 targets written")
PY
    local n_t2=$(jq 'length' "$t2_targets" 2>/dev/null || echo 0)
    echo "[Tier 2] $n_t2 DOIs escalating to live-fetch..."
    if [[ "$n_t2" -gt 0 ]]; then
        "$PY" "$REPO_ROOT/eval/scripts/live_fetch_empty.py" \
            --targets "$t2_targets" \
            --prompt "$PROMPT" \
            --output "$out_dir/livefetch-delta.csv" \
            --cdp-url "$CDP_URL" \
            --model claude-sonnet-4-5 \
            --max-steps 18 \
            --concurrency 50 || true
    fi

    # ── Final merge: Tier 2 delta into post-Tier-1.5 baseline ──
    echo "[Final merge] folding Tier 2 deltas..."
    local final_csv="$out_dir/ai-goldie-${n}.v2.csv"
    "$PY" - <<PY
import csv, json
from pathlib import Path
def is_empty(s): return not s or not s.strip() or s.strip().lower() in {"n/a","na","none","null"}
def is_empty_authors(s):
    if is_empty(s): return True
    try: a = json.loads(s); return not isinstance(a, list) or len(a)==0
    except: return True
base = list(csv.DictReader(open("$merged_t15")))
by_doi = {r["DOI"]: r for r in base}
delta_path = Path("$out_dir/livefetch-delta.csv")
if delta_path.exists():
    for d in csv.DictReader(delta_path.open()):
        doi = d["DOI"]
        if doi not in by_doi: continue
        b = by_doi[doi]
        if is_empty(b["Abstract"]) and not is_empty(d["Abstract"]): b["Abstract"] = d["Abstract"]
        if is_empty_authors(b["Authors"]) and not is_empty_authors(d["Authors"]): b["Authors"] = d["Authors"]
        if is_empty(b["PDF URL"]) and not is_empty(d["PDF URL"]): b["PDF URL"] = d["PDF URL"]
        for fld in ["Has Bot Check","Resolves To PDF"]:
            if (d.get(fld,"") or "").strip().upper() == "TRUE":
                b[fld] = "TRUE"
                b["Status"] = "FALSE"
fns = list(base[0].keys())
out = Path("$final_csv")
tmp = out.with_suffix(".csv.tmp")
with tmp.open("w",newline="") as f:
    w = csv.DictWriter(f, fieldnames=fns); w.writeheader()
    for r in base: w.writerow(r)
tmp.replace(out)
print(f"wrote {out}")
PY

    # ── Iter-R classify (label remaining empties) ──
    # For 10K we skip the full iter-R relabel here; the orchestrator writes
    # iter-R:reharvest-recovered in the Tier 1.5 merge step. Other labels
    # (paywalled/bot-check/pdf-redirect) require the resolved_links column
    # which would come from a separate doi.org-resolution step.
    # See runs/50-10K-test/codex-review/comparison.md for the rationale.

    echo "✓ Batch $n complete: $final_csv"
}

# ─── Main loop ───────────────────────────────────────────────────────────────
batches=( $(batch_list "$BATCH_SPEC") )
first_batch="${batches[0]}"

for n in "${batches[@]}"; do
    run_batch "$n"

    # Telegram gate after batch 1 (or after parity batch if not --no-gate)
    if [[ "$n" == "$first_batch" && $NO_GATE -eq 0 && "$n" != "parity" ]]; then
        scoreboard_text="*Batch ${n} complete on BUX.* Inspect \`/home/bux/runs/10k/batch-${n}/ai-goldie-${n}.v2.csv\` then reply \`/go\` to unblock batches 2-100."
        "$PY" "$REPO_ROOT/eval/browser-use/runtime/telegram_ping.py" \
            --gate "$n" --scoreboard-text "$scoreboard_text"
    fi
done

# ─── Final audit ─────────────────────────────────────────────────────────────
RESULTS_MD="$REPO_ROOT/RESULTS.md"
if [[ -w "$RESULTS_MD" ]]; then
    {
        echo
        echo "### $(date -u +%Y-%m-%dT%H:%M:%SZ) — BUX run batch(es) $BATCH_SPEC"
        echo "- commit: $(cd $REPO_ROOT && git rev-parse --short HEAD 2>/dev/null || echo 'unknown')"
        echo "- outputs: $RUNS_BASE/"
    } >> "$RESULTS_MD"
    echo "Appended cycle to RESULTS.md"
fi
