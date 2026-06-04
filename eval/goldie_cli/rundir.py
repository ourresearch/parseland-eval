"""The single run-directory scheme: ``runs/<corpus>-<UTC>/``.

Lives at the REPO-ROOT ``runs/`` (see config.RUNS_DIR) alongside the existing goldie
artifacts (runs/10k, runs/holdout-*). Owns the layout so no other module hardcodes paths.

    runs/<corpus>-<UTC>/
      manifest.json          run metadata + status
      report.json            holdout-scored report (Phase 6)
      merged.csv             corpus-level concat of batches/*/ai-goldie.csv
      batches/batch-NNN/ai-goldie.csv
      checkpoints/batch-NNN.partial.jsonl
      failures/batch-NNN.failures.jsonl
      logs/
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .config import RUNS_DIR


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


@dataclass(frozen=True)
class RunDir:
    root: Path

    @classmethod
    def create(cls, corpus: str, *, runs_dir: Path | None = None, stamp: str | None = None) -> "RunDir":
        root = (runs_dir or RUNS_DIR) / f"{corpus}-{stamp or utc_stamp()}"
        rd = cls(root)
        for d in (rd.batches_dir, rd.checkpoints_dir, rd.failures_dir, rd.logs_dir):
            d.mkdir(parents=True, exist_ok=True)
        return rd

    @classmethod
    def open(cls, root: Path) -> "RunDir":
        return cls(Path(root))

    @property
    def batches_dir(self) -> Path: return self.root / "batches"
    @property
    def checkpoints_dir(self) -> Path: return self.root / "checkpoints"
    @property
    def failures_dir(self) -> Path: return self.root / "failures"
    @property
    def logs_dir(self) -> Path: return self.root / "logs"
    @property
    def manifest_path(self) -> Path: return self.root / "manifest.json"
    @property
    def report_path(self) -> Path: return self.root / "report.json"
    @property
    def merged_csv(self) -> Path: return self.root / "merged.csv"

    def batch_csv(self, n: int) -> Path:
        return self.batches_dir / f"batch-{n:03d}" / "ai-goldie.csv"

    def checkpoint(self, n: int) -> Path:
        return self.checkpoints_dir / f"batch-{n:03d}.partial.jsonl"

    def failures(self, n: int) -> Path:
        return self.failures_dir / f"batch-{n:03d}.failures.jsonl"

    def write_manifest(self, data: dict) -> None:
        tmp = self.manifest_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.manifest_path)

    def read_manifest(self) -> dict:
        if not self.manifest_path.exists():
            return {}
        return json.loads(self.manifest_path.read_text(encoding="utf-8"))
