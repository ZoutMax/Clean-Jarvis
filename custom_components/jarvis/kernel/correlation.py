"""Ambient correlation id (kernel Phase 1, docs/KERNEL_PLAN.md).

The decision record is written from ~9 call sites scattered across the reasoning
code. Rather than thread a ``correlation_id`` argument through every one of them,
a publisher sets the *current* correlation id for the duration of handling an
event, and ``decision_record.record`` reads it as the default. That links every
event → decision → outcome into one chain with almost no call-site churn.

Backed by a :class:`contextvars.ContextVar`, so the value is isolated per async
task / thread and never leaks between concurrent chains. Pure stdlib; no Home
Assistant import.

Typical use (in an event handler)::

    with correlation.scope(event.correlation_id):
        ...                      # any decision_record.record() here inherits it
"""
from __future__ import annotations

import contextlib
from contextvars import ContextVar
from typing import Iterator, Optional

_CURRENT: ContextVar[Optional[str]] = ContextVar("jarvis_correlation_id", default=None)


def current() -> Optional[str]:
    """The correlation id in effect for the current context, or None."""
    return _CURRENT.get()


def set_current(correlation_id: Optional[str]):
    """Set the current correlation id. Returns the contextvars Token for reset.

    Prefer :func:`scope` where the extent is a block; use this directly only when
    the set and reset can't be bracketed.
    """
    return _CURRENT.set(correlation_id)


def reset(token) -> None:
    """Restore the correlation id to what it was before :func:`set_current`."""
    try:
        _CURRENT.reset(token)
    except Exception:
        pass


@contextlib.contextmanager
def scope(correlation_id: Optional[str]) -> Iterator[Optional[str]]:
    """Context manager that sets the correlation id for the enclosed block.

    A falsy ``correlation_id`` is a no-op passthrough (keeps whatever is current),
    so callers can wrap unconditionally without clobbering an outer chain.
    """
    if not correlation_id:
        yield current()
        return
    token = _CURRENT.set(correlation_id)
    try:
        yield correlation_id
    finally:
        try:
            _CURRENT.reset(token)
        except Exception:
            pass
