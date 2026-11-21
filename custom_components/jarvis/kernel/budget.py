"""Agency budget — limits JARVIS places on *itself* (MCU audit A3).

The external audit's point 21: "a correct system can still produce runaway
behavior." Once causal learning and autonomous planning are wired, JARVIS needs
hard ceilings on its own agency — so a bug, a feedback loop, or an over-eager
plan can't flood the house with actions, burn the LLM budget, or delegate without
bound.

This is the pure primitive that holds those ceilings and answers *"am I allowed
to do one more of this right now?"*:

    * **rate caps** — autonomous actions / hour, LLM calls / hour (sliding window),
    * **retry cap** — attempts allowed per action,
    * **delegation-depth cap** — how deep a chain of delegated agents may go,
    * **concurrency cap** — how many of a kind may be in flight at once.

Pure: no Home Assistant import, no I/O, no wall clock — ``now`` is passed in, so
it is deterministic and unit-testable. It only *reports*; the caller decides what
to do when a budget is exhausted (defer, drop, alert). Like the loop detector and
authority engine, it is additive and advisory until a caller consults it.
"""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Deque, Dict, Optional, Tuple

# well-known rate "kinds" (callers may use any string, these are the defaults)
ACTION = "action"      # an autonomous actuation
LLM = "llm"            # an LLM / model call

# verdict reasons
OK = "ok"
RATE_EXCEEDED = "rate_exceeded"
RETRIES_EXCEEDED = "retries_exceeded"
DEPTH_EXCEEDED = "delegation_depth_exceeded"
CONCURRENCY_EXCEEDED = "concurrency_exceeded"


@dataclass(frozen=True)
class BudgetLimits:
    """The ceilings. ``0`` (or negative) on any field means *unlimited*."""

    window_s: float = 3600.0        # the rolling window for the per-hour rates
    rates: Dict[str, int] = field(default_factory=lambda: {ACTION: 60, LLM: 120})
    retries_per_action: int = 3
    max_delegation_depth: int = 3
    max_concurrency: Dict[str, int] = field(default_factory=lambda: {ACTION: 4})

    def rate_for(self, kind: str) -> int:
        return int(self.rates.get(kind, 0) or 0)

    def concurrency_for(self, kind: str) -> int:
        return int(self.max_concurrency.get(kind, 0) or 0)


@dataclass(frozen=True)
class BudgetVerdict:
    allowed: bool
    reason: str = OK
    kind: str = ""
    remaining: Optional[int] = None   # for a rate kind: slots left in the window

    def __bool__(self) -> bool:
        return self.allowed


@dataclass
class AgencyBudget:
    """Tracks usage against :class:`BudgetLimits` with sliding-window rates."""

    limits: BudgetLimits = field(default_factory=BudgetLimits)
    # kind -> timestamps of recent events within the window
    _events: Dict[str, Deque[float]] = field(
        default_factory=lambda: defaultdict(deque))

    def _prune(self, kind: str, now: float) -> None:
        dq = self._events[kind]
        cutoff = now - self.limits.window_s
        while dq and dq[0] < cutoff:
            dq.popleft()

    def count(self, kind: str, now: float) -> int:
        """Events of ``kind`` currently inside the window."""
        self._prune(kind, now)
        return len(self._events[kind])

    def remaining(self, kind: str, now: float) -> Optional[int]:
        cap = self.limits.rate_for(kind)
        if cap <= 0:
            return None                       # unlimited
        return max(0, cap - self.count(kind, now))

    # ── rate ──────────────────────────────────────────────────────────────
    def allow(self, kind: str, *, now: float) -> BudgetVerdict:
        """May one more ``kind`` happen now, by its rate cap? Does not record."""
        cap = self.limits.rate_for(kind)
        if cap <= 0:
            return BudgetVerdict(True, OK, kind, None)
        used = self.count(kind, now)
        if used >= cap:
            return BudgetVerdict(False, RATE_EXCEEDED, kind, 0)
        return BudgetVerdict(True, OK, kind, cap - used)

    def record(self, kind: str, *, now: float) -> None:
        """Record that one ``kind`` happened (counts against its rate)."""
        self._prune(kind, now)
        self._events[kind].append(now)

    def check_and_record(self, kind: str, *, now: float) -> BudgetVerdict:
        """Atomic allow→record: records only when allowed."""
        v = self.allow(kind, now=now)
        if v.allowed:
            self.record(kind, now=now)
            # remaining reflects the slot we just consumed
            rem = self.remaining(kind, now)
            return BudgetVerdict(True, OK, kind, rem)
        return v

    # ── retries / depth / concurrency (stateless caps) ──────────────────────
    def retries_ok(self, attempt: int) -> BudgetVerdict:
        """``attempt`` is 1-based. True while within ``retries_per_action``
        (an action's first try plus that many retries)."""
        cap = self.limits.retries_per_action
        if cap < 0:
            return BudgetVerdict(True, OK, "retry")
        ok = attempt <= cap + 1
        return BudgetVerdict(ok, OK if ok else RETRIES_EXCEEDED, "retry")

    def depth_ok(self, depth: int) -> BudgetVerdict:
        """Delegation depth (0 = the owner, 1 = first delegate, …)."""
        cap = self.limits.max_delegation_depth
        if cap <= 0:
            return BudgetVerdict(True, OK, "delegation")
        ok = depth <= cap
        return BudgetVerdict(ok, OK if ok else DEPTH_EXCEEDED, "delegation")

    def concurrency_ok(self, kind: str, in_flight: int) -> BudgetVerdict:
        """May another ``kind`` start, given ``in_flight`` already running?"""
        cap = self.limits.concurrency_for(kind)
        if cap <= 0:
            return BudgetVerdict(True, OK, kind)
        ok = in_flight < cap
        return BudgetVerdict(ok, OK if ok else CONCURRENCY_EXCEEDED, kind)

    def reset(self, kind: Optional[str] = None) -> None:
        if kind is None:
            self._events.clear()
        else:
            self._events.pop(kind, None)
