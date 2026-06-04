from __future__ import annotations

import pytest

from goldie_cli.prompt import load_prompt

_PROMPT = """---
version: v1.9.2
date: 2026-06-04
---

# notes

## System prompt

```
You are an extractor. Read the page only.
Emit structured JSON.
```
"""


def test_load_prompt_parses_version_and_body(tmp_path):
    p = tmp_path / "ai-goldie-v1.9.2.md"
    p.write_text(_PROMPT, encoding="utf-8")
    version, body = load_prompt(p)
    assert version == "v1.9.2"
    assert body.startswith("You are an extractor.")
    assert "Emit structured JSON." in body


def test_load_prompt_missing_block_raises(tmp_path):
    p = tmp_path / "bad.md"
    p.write_text("---\nversion: x\n---\nno fenced block here\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_prompt(p)
