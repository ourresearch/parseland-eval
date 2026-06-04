"""Pre-emptive cost cap.

asyncio is single-threaded, so a plain float accumulator is race-free between awaits.
``would_exceed`` is checked BEFORE acquiring an extraction slot (stricter than the old
between-batch-only check, which could overshoot by a whole batch)."""
from __future__ import annotations


class CostTracker:
    def __init__(self, max_cost_usd: float | None = None) -> None:
        self._max = max_cost_usd
        self._spent = 0.0

    def add(self, cost: float) -> None:
        if cost:
            self._spent += cost

    def would_exceed(self) -> bool:
        return self._max is not None and self._spent >= self._max

    @property
    def spent(self) -> float:
        return round(self._spent, 4)

    @property
    def remaining(self) -> float | None:
        return None if self._max is None else round(max(0.0, self._max - self._spent), 4)
