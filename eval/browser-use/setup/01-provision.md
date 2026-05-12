# 01 — Provision BUX

Manual click-through. Should take ~5 minutes end-to-end (~60 seconds for the actual provision; the rest is configuration).

## 1. Provision the managed BUX instance

1. Open <https://cloud.browser-use.com/bux> in a browser.
2. Click **Provision**. A new BUX VM spins up in ~60 seconds with these services preinstalled as systemd units:
   - `claude-code` — persistent Claude Code session ready to drive the orchestrator
   - `browser-harness` — Chromium with CDP-over-WSS exposed for `live_fetch_empty.py`
   - `telegram-bot` — handles `/ping`, `/go`, and scoreboard posts

3. Once the BUX dashboard shows "Ready", copy the SSH hostname displayed.
4. Upload your SSH public key via the dashboard (or use the web terminal to append it to `/home/bux/.ssh/authorized_keys`).

Verify all three services are green:

```
ssh bux 'systemctl status claude-code browser-harness telegram-bot --no-pager'
```

## 2. Add a `Host bux` entry to your local SSH config

Append to `~/.ssh/config`:

```
Host bux
    HostName <your-bux-hostname>.cloud.browser-use.com
    User bux
    IdentityFile ~/.ssh/id_ed25519
    ServerAliveInterval 60
    ServerAliveCountMax 30
```

Test:

```
ssh bux echo connected
```

Should print `connected`. If not, fix SSH config before proceeding.

## 3. Upgrade Browser Use Cloud to Starter tier

The 10K production run uses Tier 2 live-fetch via Browser Harness, which routes through Browser Use Cloud sessions. Free tier is 3 concurrent (Tier 2 wall ≈ 13 h — rejected). **Starter tier gives 50 concurrent (Tier 2 wall ≈ 48 min across all 10K — accepted).**

1. Open <https://cloud.browser-use.com/billing>
2. Upgrade to Starter (50 concurrent sessions)
3. Copy the API key from the dashboard — format `bu_...`
4. Set it as `BROWSER_USE_API_KEY` in your local `.env` (will be scp'd to BUX in step 5)

## 4. Wire the Telegram bot

1. Open Telegram, message `@BotFather`
2. Send `/newbot`, follow prompts, copy the bot token
3. Create a private channel for scoreboard posts
4. Add the bot as an admin to that channel
5. Get the chat ID:
   - Send any message in the channel
   - `curl "https://api.telegram.org/bot<TOKEN>/getUpdates"`
   - Find the chat ID in the response
6. Save `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in `.env`

## 5. Set the Taxicab harvester URL

Add to `.env`:

```
TAXICAB_HARVESTER_URL=http://harvester-load-balancer-366186003.us-east-1.elb.amazonaws.com
```

This drives Tier 1.5 (re-harvest of stale cache rows). See `../docs/taxicab-reharvest.md`.

## 6. (Optional) AI-agent self-registration

If you don't have a `BROWSER_USE_API_KEY` and want an AI agent to self-register a Browser Use Cloud account (free tier), use the challenge-response flow:

```
# Step 1 — request a challenge
curl -X POST https://api.browser-use.com/cloud/signup \
  -H "Content-Type: application/json" \
  -d '{}'
# → returns {"challenge_id": "...", "challenge_text": "<obfuscated math problem>"}

# Step 2 — solve the math problem USING AN LLM (deterministic code is explicitly disallowed)
# Answer format: string with 2 decimal places, e.g. "144.00"

# Step 3 — verify
curl -X POST https://api.browser-use.com/cloud/signup/verify \
  -H "Content-Type: application/json" \
  -d '{"challenge_id": "<from step 1>", "answer": "144.00"}'
# → returns {"api_key": "bu_..."}

# Step 4 — use it
# Header on all api.browser-use.com requests: X-Browser-Use-API-Key: bu_...
```

**Security caveat (from official docs):** never send your API key to any domain other than `api.browser-use.com` or `cloud.browser-use.com`.

To attach the agent-registered account to a human later:

```
curl -X POST https://api.browser-use.com/cloud/signup/claim
# → returns {"claim_url": "..."}  # valid for 1 hour
```

`02-deploy.sh` does NOT automate this flow — it assumes `BROWSER_USE_API_KEY` is already set in your local `.env`. Self-registration is an option for cold-start automation, not the default operator path.

Source: <https://docs.browser-use.com/cloud/quickstart>

## Once steps 1-5 are done

Proceed to `02-deploy.sh`.
