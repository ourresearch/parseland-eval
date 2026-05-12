#!/usr/bin/env bash
# Deploy parseland-eval to BUX.
#
# Idempotent: re-running on an already-deployed BUX is safe (git pull + venv reuse).
# Assumes:
#   - `ssh bux` works (see ../docs/01-provision.md step 2)
#   - eval/.env exists locally with the required keys (see env.example)
#   - all three BUX systemd services are green
#
# Usage:
#   bash eval/browser-use/setup/02-deploy.sh           # actually deploy
#   bash eval/browser-use/setup/02-deploy.sh --dry-run # print all commands without executing

set -euo pipefail

DRY_RUN=0
if [[ "${1:-}" == "--dry-run" ]]; then
    DRY_RUN=1
fi

run() {
    if [[ $DRY_RUN -eq 1 ]]; then
        echo "[dry-run] $*"
    else
        echo "→ $*"
        eval "$@"
    fi
}

# 0. Sanity: are we in the right place?
if [[ ! -f eval/.env ]]; then
    echo "ERROR: eval/.env not found. Run from the parseland-eval repo root." >&2
    echo "Required keys in eval/.env: ANTHROPIC_API_KEY, BROWSER_USE_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID" >&2
    exit 1
fi

REPO_URL="${REPO_URL:-https://github.com/ourresearch/parseland-eval}"
BUX_REPO_DIR="/home/bux/parseland-eval"
BUX_RUNS_DIR="/home/bux/runs/10k"

# 1. Clone or pull the repo on BUX
run "ssh bux 'if [ -d $BUX_REPO_DIR/.git ]; then cd $BUX_REPO_DIR && git pull --rebase; else git clone $REPO_URL $BUX_REPO_DIR; fi'"

# 2. scp the local .env to BUX
run "scp eval/.env bux:$BUX_REPO_DIR/eval/.env"

# 3. Create/update the venv and install the package
run "ssh bux 'cd $BUX_REPO_DIR/eval && python3 -m venv .venv && .venv/bin/pip install -q -U pip && .venv/bin/pip install -q -e .'"

# 4. Create the runs directory with checkpoint subdir
run "ssh bux 'mkdir -p $BUX_RUNS_DIR/.checkpoint'"

# 5. Echo the env-vars the operator must ensure are exported on BUX
cat <<'EOF'

═══════════════════════════════════════════════════════════════════════════════
Deploy complete (or dry-run finished).

Before running smoke.sh / parity_50.sh / run_10k_on_bux.sh, ssh into BUX and
ensure these are exported in /home/bux/parseland-eval/eval/.env (or in BUX's
shell rc file):

    ANTHROPIC_API_KEY=sk-ant-...
    BROWSER_USE_API_KEY=bu_...
    CDP_URL=wss://<browser-harness-host>:<port>          # from BUX dashboard
    TELEGRAM_BOT_TOKEN=...                                # from @BotFather
    TELEGRAM_CHAT_ID=...                                  # private channel id
    TAXICAB_HARVESTER_URL=http://harvester-load-balancer-366186003.us-east-1.elb.amazonaws.com

Verify:
    ssh bux 'source $BUX_REPO_DIR/eval/.env && env | grep -E "ANTHROPIC|BROWSER_USE|CDP_URL|TELEGRAM|TAXICAB"'

Next step:
    bash eval/browser-use/verify/smoke.sh
═══════════════════════════════════════════════════════════════════════════════
EOF
