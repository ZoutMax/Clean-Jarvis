"""In-process event bus (kernel Phase 1, docs/KERNEL_PLAN.md).

A tiny synchronous publish/subscribe hub for :class:`JarvisEvent`s. It is
deliberately **pure** — no Home Assistant import, no I/O, no threads — so it is
trivially unit-testable and safe to call from anywhere. Persistence lives in a
separate subscriber (``kernel.ledger.EventLedger``), which keeps the bus itself
free of blocking work.

Phase 1 runs the bus in **shadow mode**: the observer, camera, proactive-audio
and voice paths publish events alongside their existing logic, and the ledger
records them. Nothing consumes events to drive behaviour yet — the payoff is a
queryable, correlated trail that later phases build on.

Delivery contract: ``publish`` never raises. A subscriber that throws is logged
and skipped so one bad handler can't break a publisher's authoritative path or
starve the other subscribers.
"""
from __future__ import annotations

import logging
from typing import Callable, Dict, List

from .event import JarvisEvent

_LOGGER = logging.getLogger(__name__)

Handler = Callable[[JarvisEvent], None]


class JarvisEventBus:
    """Synchronous in-process pub/sub for JarvisEvents.

    Handlers registered for a specific ``event.type`` and handlers registered for
    all events are both invoked on publish, in registration order, each guarded so
    a failure is contained.
    """

    def __init__(self) -> None:
        self._by_type: Dict[str, List[Handler]] = {}
        self._all: List[Handler] = []

    def subscribe(self, event_type: str, handler: Handler) -> Callable[[], None]:
        """Subscribe to one event type. Returns an unsubscribe callable."""
        handlers = self._by_type.setdefault(event_type, [])
        handlers.append(handler)

        def _unsub() -> None:
            try:
                handlers.remove(handler)
            except ValueError:
                pass

        return _unsub

    def subscribe_all(self, handler: Handler) -> Callable[[], None]:
        """Subscribe to every event (e.g. the ledger). Returns an unsubscribe."""
        self._all.append(handler)

        def _unsub() -> None:
            try:
                self._all.remove(handler)
            except ValueError:
                pass

        return _unsub

    def publish(self, event: JarvisEvent) -> None:
        """Deliver ``event`` to all matching subscribers. Never raises."""
        for handler in list(self._by_type.get(event.type, ())):
            self._safe_call(handler, event)
        for handler in list(self._all):
            self._safe_call(handler, event)

    @staticmethod
    def _safe_call(handler: Handler, event: JarvisEvent) -> None:
        try:
            handler(event)
        except Exception as exc:  # a bad subscriber must not break the publisher
            _LOGGER.debug("event bus: subscriber %r failed on %s: %s",
                          getattr(handler, "__name__", handler), event.type, exc)

    def subscriber_count(self) -> int:
        """Total registered handlers (diagnostics)."""
        return sum(len(v) for v in self._by_type.values()) + len(self._all)
