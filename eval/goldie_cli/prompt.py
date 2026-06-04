"""Prompt loader — extracts the version + the ``## System prompt`` fenced body.

Lifted verbatim from extract_batch_cloud.py:122-141 (the regex is the de-facto
contract for the ``eval/prompts/ai-goldie-v*.md`` files). Returns a ``ValueError``
rather than ``SystemExit`` so callers decide the exit code (cli maps it to 2).
"""
from __future__ import annotations

import re
from pathlib import Path

_FRONTMATTER_RE = re.compile(r"^---\n.*?\n---\n", re.DOTALL)
_SYSTEM_PROMPT_BLOCK_RE = re.compile(
    r"##\s*System prompt\s*\n+```[a-z]*\n(?P<body>.*?)\n```",
    re.DOTALL | re.IGNORECASE,
)


def load_prompt(path: Path) -> tuple[str, str]:
    """Return ``(version, system_prompt_body)`` for a prompt .md file."""
    raw = path.read_text(encoding="utf-8")
    version = "unknown"
    fm = _FRONTMATTER_RE.match(raw)
    if fm:
        for line in fm.group(0).splitlines():
            if line.strip().startswith("version:"):
                version = line.split(":", 1)[1].strip()
                break
    m = _SYSTEM_PROMPT_BLOCK_RE.search(raw)
    if not m:
        raise ValueError(f"could not find '## System prompt' fenced block in {path}")
    return version, m.group("body").strip()
