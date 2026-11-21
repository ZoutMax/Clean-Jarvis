"""
JARVIS — situational snapshot (v6.87.0).

Composites the live signals JARVIS already tracks — time, who's home, weather,
the next calendar events, current power draw, and recent activity — into one
concise picture for the agent's system prompt, so judgments are grounded in the
current situation rather than a static device inventory alone.

Each signal is gathered independently and guarded: a missing or failing source
is omitted, never an error. Dependency-light and synchronous (cheap state
reads); the agent calls it in the executor alongside the home-context build.
"""
from __future__ import annotations

import logging
from datetime import datetime

_LOGGER = logging.getLogger(__name__)


def _time(hass) -> str:
    try:
        # Home Assistant's configured local time when available, so the date and
        # clock match the household's timezone rather than the server process's.
        try:
            from homeassistant.util import dt as dt_util
            dt = dt_util.now()
        except Exception:
            dt = datetime.now()
        # Include the full date (weekday, month, day, YEAR), not just the weekday
        # and clock: without the year the model falls back to its training-cutoff
        # date and mis-handles time-sensitive questions (e.g. web searches for
        # current events land on stale results). See discussion #55.
        clock = dt.strftime("%I:%M %p").lstrip("0")
        return f"Time: {dt.strftime('%A, %B')} {dt.day}, {dt.year} · {clock}"
    except Exception:
        return ""


def _presence(hass, hidden=None) -> str:
    try:
        from . import presence
        s = ((presence.presence_context_string(hass, hidden=hidden) if hidden is not None   # jh-r5-hidden: the where-is rule
              else presence.presence_context_string(hass)) or "").strip()
        if not s or "No person entities" in s:
            return ""
        return "Presence: " + s
    except Exception:
        return ""


def _weather(hass) -> str:
    try:
        states = list(hass.states.async_all("weather"))
        if not states:
            return ""
        st = states[0]
        cond = str(st.state or "").replace("_", " ").strip()
        temp = st.attributes.get("temperature")
        unit = st.attributes.get("temperature_unit", "")
        if cond and temp is not None:
            return "Weather: %s, %s%s" % (cond, temp, unit)
        return ("Weather: " + cond) if cond else ""
    except Exception:
        return ""


def _calendar(hass, only=None) -> str:
    try:
        from . import comms
        # jh-r10-calendar (review round 10): only the calendars this asker may read (the plain call when it may read all)
        ag = (comms.agenda(hass, 12) if only is None else comms.agenda(hass, 12, only)) or {}
        evs = ag.get("events") or []
        if not evs:
            return ""
        line = "Next up: " + "; ".join(evs[:2])
        confs = ag.get("conflicts") or []
        if confs:
            line += " (" + confs[0] + ")"
        return line
    except Exception:
        return ""


def _energy(hass) -> str:
    try:
        from . import energy
        ps = energy.power_status(hass) or {}
        kw = ps.get("kw")
        if kw is None:
            return ""
        return "Power: %.1f kW%s" % (kw, " — over peak" if ps.get("over_peak") else "")
    except Exception:
        return ""


def _activity(hass, hidden=None) -> str:   # jh-r6-activity (review round 6): no camera line about a hidden person
    try:
        from . import observer
        s = (observer.get_recent_context(600) or "").strip()
        if not s or s.startswith("quiet"):
            return ""
        lines = [ln.strip() for ln in s.splitlines() if ln.strip()]
        if hidden is not None:
            import re as _re6
            names = {n for ns in hidden.values() for n in ns if n}
            lines = [ln for ln in lines if not any(_re6.search(r"(?<!\w)" + _re6.escape(n) + r"(?!\w)", ln, _re6.I) for n in names)]
        lines = lines[-3:]
        if not lines:
            return ""
        return "Recent activity: " + "; ".join(lines)
    except Exception:
        return ""


def snapshot(hass, hidden=None, calendars=None) -> str:
    """One concise situational block composited from the live signals. Empty
    string when nothing meaningful is available. Never raises."""
    parts = []
    for fn in (_time, _presence, _weather, _calendar, _energy, _activity):
        try:
            line = (fn(hass, hidden) if fn in (_presence, _activity) else fn(hass, calendars) if fn is _calendar
                    else fn(hass))   # review round 5, 6: people this asker may not see; round 10: its calendars
        except Exception:
            line = ""
        if line:
            parts.append(line)
    return "\n".join(parts)
