"""Telegram bot wrapper for BUX.

Three modes:
  --test                     post "BUX smoke test from <host> at <iso>"
  --scoreboard <path>        post a free-form scoreboard (reads the file content)
  --gate <batch_no>          post batch-N scoreboard, then poll Telegram getUpdates
                             for /go from $TELEGRAM_CHAT_ID, then touch
                             /home/bux/runs/10k/.gate-released and exit

Reads `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` from env. Both required (except
in --test mode where a missing chat-id fails loudly).
"""
from __future__ import annotations

import argparse
import os
import platform
import socket
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

API_BASE = "https://api.telegram.org"
GATE_SENTINEL = Path("/home/bux/runs/10k/.gate-released")


def _conf() -> tuple[str, str]:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token:
        print("ERROR: TELEGRAM_BOT_TOKEN not set in env.", file=sys.stderr)
        sys.exit(2)
    if not chat:
        print("ERROR: TELEGRAM_CHAT_ID not set in env.", file=sys.stderr)
        sys.exit(2)
    return token, chat


def send(text: str, parse_mode: str = "Markdown") -> None:
    token, chat = _conf()
    url = f"{API_BASE}/bot{token}/sendMessage"
    r = requests.post(url, json={"chat_id": chat, "text": text, "parse_mode": parse_mode}, timeout=15)
    if r.status_code != 200:
        print(f"ERROR: Telegram sendMessage returned {r.status_code}: {r.text[:200]}", file=sys.stderr)
        sys.exit(3)


def poll_for_go(timeout_s: int = 86400, poll_interval_s: int = 5) -> bool:
    """Poll getUpdates until a `/go` message arrives in TELEGRAM_CHAT_ID, or timeout."""
    token, chat = _conf()
    url = f"{API_BASE}/bot{token}/getUpdates"
    offset = None
    deadline = time.time() + timeout_s
    print(f"[telegram-ping] waiting for /go in chat {chat} (timeout {timeout_s}s)...", file=sys.stderr)
    while time.time() < deadline:
        params = {"timeout": 30}
        if offset is not None:
            params["offset"] = offset
        try:
            r = requests.get(url, params=params, timeout=40)
            data = r.json()
        except (requests.RequestException, ValueError) as e:
            print(f"[telegram-ping] getUpdates error: {e}", file=sys.stderr)
            time.sleep(poll_interval_s)
            continue
        for upd in data.get("result", []):
            offset = upd["update_id"] + 1
            msg = upd.get("message") or upd.get("channel_post") or {}
            text = (msg.get("text") or "").strip().lower()
            msg_chat = str(msg.get("chat", {}).get("id", ""))
            if msg_chat == str(chat) and text in {"/go", "go"}:
                print(f"[telegram-ping] received /go from chat {msg_chat}", file=sys.stderr)
                return True
        time.sleep(poll_interval_s)
    print("[telegram-ping] timeout waiting for /go", file=sys.stderr)
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--test", action="store_true", help="Send a smoke-test message")
    mode.add_argument("--scoreboard", type=Path, help="Send a free-form scoreboard (file content)")
    mode.add_argument("--gate", type=str, help="Post batch-N scoreboard + wait for /go")
    ap.add_argument("--scoreboard-text", help="Inline scoreboard text (overrides --scoreboard file)")
    ap.add_argument("--gate-timeout", type=int, default=86400, help="Seconds to wait for /go (default 24h)")
    args = ap.parse_args()

    if args.test:
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        send(f"*BUX smoke test* from `{socket.gethostname()}` at `{ts}` ({platform.platform()})")
        print("[telegram-ping] sent test message", file=sys.stderr)
        return 0

    if args.scoreboard or args.scoreboard_text:
        text = args.scoreboard_text or args.scoreboard.read_text()
        send(text)
        print("[telegram-ping] sent scoreboard", file=sys.stderr)
        return 0

    if args.gate:
        batch_no = args.gate
        text = args.scoreboard_text or (args.scoreboard.read_text() if args.scoreboard else
                                         f"*Batch {batch_no} complete.* Reply `/go` to authorize next batches.")
        send(text)
        print(f"[telegram-ping] gate scoreboard posted for batch {batch_no}", file=sys.stderr)
        ok = poll_for_go(timeout_s=args.gate_timeout)
        if not ok:
            print("[telegram-ping] gate timed out waiting for /go", file=sys.stderr)
            return 4
        GATE_SENTINEL.parent.mkdir(parents=True, exist_ok=True)
        GATE_SENTINEL.touch()
        print(f"[telegram-ping] released gate via {GATE_SENTINEL}", file=sys.stderr)
        send(f"Gate released for batch {batch_no} — proceeding.")
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
