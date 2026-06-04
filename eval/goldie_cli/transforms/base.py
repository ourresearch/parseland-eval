"""Transform primitives.

A ``Transform`` is one post-LLM, page-evidence-only step that mutates the extraction
dict in place and reports whether it changed anything. The extraction dict here uses
the **capitalized** gold shape the proven functions expect:

    {"Authors": [{"name","rasses","corresponding_author"}, ...],
     "Abstract": str, "PDF URL": str}

The tier layer (Phase 4) converts the backend's lowercase ``ExtractionOut`` to/from this
shape at the boundary, so the lifted logic stays byte-identical.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class TransformContext:
    """Everything a transform may read. All evidence is page-derived."""

    html: str
    doi: str
    link: str
    resolved_url: str | None = None
    skip_meta_tags: bool = False


@dataclass(frozen=True)
class Transform:
    """One ordered post-LLM transform. ``order`` pins the load-bearing run_doi sequence."""

    name: str
    order: int
    apply: Callable[[dict[str, Any], TransformContext], bool]
