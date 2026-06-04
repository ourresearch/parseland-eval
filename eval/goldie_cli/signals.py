"""Signal-safe shutdown: SIGINT/SIGTERM set an asyncio.Event the orchestrator polls.

Append-only checkpoints are already crash-consistent, so a clean stop just means no new
slots are scheduled; in-flight tasks finish and flush. The CLI maps a triggered shutdown
to exit code 130.
"""
from __future__ import annotations

import asyncio
import logging
import signal

log = logging.getLogger("goldie")


def install_handlers(loop: asyncio.AbstractEventLoop | None = None) -> asyncio.Event:
    """Register SIGINT/SIGTERM → set the returned Event. Falls back to ``signal.signal``
    on platforms without ``add_signal_handler``."""
    loop = loop or asyncio.get_event_loop()
    event = asyncio.Event()

    def _trigger() -> None:
        if not event.is_set():
            log.warning("shutdown signal received — finishing in-flight work, then stopping")
        event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _trigger)
        except (NotImplementedError, RuntimeError):  # pragma: no cover - non-Unix / no running loop
            signal.signal(sig, lambda *_a: _trigger())
    return event
