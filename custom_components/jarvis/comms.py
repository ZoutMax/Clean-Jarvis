"""
JARVIS Communication Agent (v6.51.0).

The blueprint's "Communication Agent": proactively surface calendar conflicts
and upcoming commitments. This module handles calendars only; read-only email
access lives in mail.py (v6.81.0) — fetched on request via IMAP and sanitized,
superseding the earlier stance that the inbox should not be read in-process.

What this does, using the `calendar.*` entities HA already provides (Google
Calendar, CalDAV, Local Calendar, etc.):
  - Reads every calendar entity's current + next event.
  - Detects OVERLAPS across calendars (two things booked at once).
  - Detects TIGHT transitions (back-to-back with < a configurable gap).
  - Produces a plain-language agenda + conflict list the agent speaks.

Dependency-light, registry-guarded, never raises. Times are compared as naive
ISO strings from HA's calendar attributes; we parse defensively and skip
anything unparseable rather than guessing.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

_LOGGER = logging.getLogger(__name__)

_DEFAULT_TIGHT_GAP_MIN = 15   # back-to-back closer than this = "tight"


def _cfg(key: str, default):
    try:
        from . import jarvis_config
        val = jarvis_config.get(key, default)
        return val if val is not None else default
    except Exception:
        return default


def _parse(ts: Optional[str]) -> Optional[datetime]:
    """Parse an HA calendar timestamp (date or datetime, with/without TZ) to a
    naive datetime for comparison. None if unparseable."""
    if not ts:
        return None
    s = str(ts).strip()
    try:
        # datetime forms
        if "T" in s or " " in s and ":" in s:
            s2 = s.replace(" ", "T")
            if s2.endswith("Z"):
                s2 = s2[:-1]
            # drop timezone offset if present (compare wall-clock)
            for sep in ("+",):
                if sep in s2[11:]:
                    s2 = s2[:11] + s2[11:].split(sep)[0]
            return datetime.fromisoformat(s2[:19])
        # date-only → midnight
        return datetime.fromisoformat(s[:10])
    except Exception:
        return None


# Jarvis Home patch (2026-11-15): integrations whose calendars say whether a day is a working day, not appointments
_JH_HELPER_CALENDARS = frozenset({"workday"})


def gather_events(hass) -> list[dict]:
    """All calendar entities' current+next events as
    {calendar, title, start(dt), end(dt), all_day}. Skips unparseable."""
    out: list[dict] = []
    if hass is None:
        return out
    try:
        states = hass.states.async_all("calendar")
    except Exception:
        return out
    _jh_skip = set()
    try:   # Jarvis Home patch (2026-11-15): jh-calendar - working-day helpers are no appointments
        from homeassistant.helpers import entity_registry as _jh_er
        _jh_reg = _jh_er.async_get(hass)
        for st in states:
            entry = _jh_reg.async_get(st.entity_id)
            if getattr(entry, "platform", None) in _JH_HELPER_CALENDARS:
                _jh_skip.add(st.entity_id)
    except Exception:
        pass
    for st in states:
        if st.entity_id in _jh_skip:
            continue
        a = st.attributes or {}
        title = a.get("message") or a.get("friendly_name") or st.entity_id
        start = _parse(a.get("start_time"))
        end = _parse(a.get("end_time"))
        if start is None:
            continue
        out.append({
            "calendar": st.entity_id,
            "title": str(title),
            "start": start,
            "end": end or (start + timedelta(hours=1)),
            "all_day": bool(a.get("all_day")),
            "location": a.get("location"),
            "active": st.state == "on",
        })
    return out


def _jh_naive_local(v, end=False):
    """jh-r21-cal: a CalendarEvent start/end (aware datetime, naive datetime or date) as naive local wall-clock time, as the
    state-based events are."""
    try:
        from homeassistant.util import dt as _jh_dtu
    except Exception:  # noqa: BLE001
        _jh_dtu = None
    if isinstance(v, datetime):
        if v.tzinfo is not None and _jh_dtu is not None:
            v = _jh_dtu.as_local(v)
        return v.replace(tzinfo=None)
    if v is not None and hasattr(v, "year") and hasattr(v, "day"):
        return datetime(v.year, v.month, v.day)
    return _parse(v)


async def gather_events_window(hass, start, end, only=None):
    """jh-r21-cal (step 163, review round 21 #3): EVERY event of every calendar between start and end (naive local times), read
    from the calendar entity itself (async_get_events), as jh_work_info does. Returns (events, partial): partial lists the
    calendars that could not be read that way; for those only the one event in their state is known (gather_events), and the
    agenda says so. Working-day helpers are skipped as in gather_events; only: the calendars this login may read."""
    out, partial = [], []
    if hass is None:
        return out, partial
    try:
        states = hass.states.async_all("calendar")
    except Exception:  # noqa: BLE001
        return out, partial
    try:
        from homeassistant.util import dt as _jh_dtu
        a, b = _jh_dtu.as_utc(_jh_dtu.as_local(start)) if start.tzinfo is None else start, \
            _jh_dtu.as_utc(_jh_dtu.as_local(end)) if end.tzinfo is None else end
    except Exception:  # noqa: BLE001
        a, b = start, end
    state_events = {e["calendar"]: e for e in gather_events(hass)}
    skip = set()
    try:
        from homeassistant.helpers import entity_registry as _jh_er
        _jh_reg = _jh_er.async_get(hass)
        for st in states:
            if getattr(_jh_reg.async_get(st.entity_id), "platform", None) in _JH_HELPER_CALENDARS:
                skip.add(st.entity_id)
    except Exception:  # noqa: BLE001
        pass
    comp = None
    try:
        comp = hass.data.get("calendar")
    except Exception:  # noqa: BLE001
        comp = None
    for st in states:
        eid = st.entity_id
        if eid in skip or (only is not None and eid not in only):
            continue
        try:
            ent = comp.get_entity(eid) if comp is not None else None
            evs = await ent.async_get_events(hass, a, b) if ent is not None else None
        except Exception:  # noqa: BLE001
            evs = None
        if evs is None:
            partial.append(eid)
            if eid in state_events:
                out.append(state_events[eid])
            continue
        for ev in evs:
            s = _jh_naive_local(getattr(ev, "start", None))
            if s is None:
                continue
            e_ = _jh_naive_local(getattr(ev, "end", None)) or (s + timedelta(hours=1))
            all_day = bool(getattr(ev, "all_day", False)) or not isinstance(getattr(ev, "start", None), datetime)
            out.append({"calendar": eid, "title": str(getattr(ev, "summary", None) or "(no title)"), "start": s, "end": e_,
                        "all_day": all_day, "location": getattr(ev, "location", None), "active": False})
    return out, partial


def find_conflicts(events: list[dict], tight_gap_min: int) -> dict:
    """Pure conflict analysis over event dicts. Returns
    {overlaps: [...], tight: [...]}. Timed events only — all-day events don't
    'conflict' with timed ones."""
    timed = sorted(
        [e for e in events if not e["all_day"]],
        key=lambda e: e["start"],
    )
    overlaps, tight = [], []
    gap = timedelta(minutes=max(0, int(tight_gap_min)))
    for i in range(len(timed)):
        a = timed[i]
        for j in range(i + 1, len(timed)):
            b = timed[j]
            if b["start"] >= a["end"]:
                # b starts after a ends → check tightness, then stop (sorted)
                if b["start"] - a["end"] < gap and b["start"] >= a["end"]:
                    tight.append((a, b))
                break
            # b starts before a ends → overlap
            overlaps.append((a, b))
    return {"overlaps": overlaps, "tight": tight}


def _fmt_time(dt: datetime) -> str:
    return dt.strftime("%-I:%M %p") if hasattr(dt, "strftime") else str(dt)


def agenda(hass, horizon_hours: int = 24, only=None, events=None, partial=None) -> dict:
    """Human-facing agenda + conflicts for the next `horizon_hours`.
    Returns {events: [...str], conflicts: [...str], count}. Never raises.
    only: the calendars this login may read (jh-r10-calendar, review round 10); None = all."""
    try:
        gap = int(_cfg("calendar_tight_gap_min", _DEFAULT_TIGHT_GAP_MIN))
        try:   # jh-r4-agenda-tz (review round 4): Home Assistant's time zone, as naive wall-clock time like the events
            from homeassistant.util import dt as _jh_dtu
            now = _jh_dtu.now().replace(tzinfo=None)
            if not isinstance(now, datetime):
                now = datetime.now()
        except Exception:
            now = datetime.now()
        horizon = now + timedelta(hours=max(1, int(horizon_hours)))
        _jh_src = gather_events(hass) if events is None else events   # jh-r21-cal (step 163): every event, when read
        evts = [e for e in _jh_src
                if e["start"] <= horizon and e["end"] >= now and (only is None or e["calendar"] in only)]
        evts.sort(key=lambda e: e["start"])

        lines = []
        for e in evts:   # Jarvis Home patch (2026-11-15): jh-calendar - every event says its own date
            when = "all day" if e["all_day"] else _fmt_time(e["start"])
            if e["start"].date() < now.date():
                # jh-r23-cal (step 165, review round 23 #7): began on an earlier day and still running: since when, until when
                _jh_last = e["end"] - timedelta(days=1) if e["all_day"] else e["end"]
                _jh_since = e["start"].strftime("%A %d %B") + ("" if e["all_day"] else ", " + _fmt_time(e["start"]))
                _jh_until = (_jh_last.strftime("%A %d %B") if e["all_day"] else
                             ("today" if e["end"].date() == now.date() else e["end"].strftime("%A %d %B"))
                             + ", " + _fmt_time(e["end"]))
                lines.append(f"{e['title']} — ongoing since {_jh_since}, until {_jh_until}")
                continue
            day = "today" if e["start"].date() == now.date() else e["start"].strftime("%A %d %B")   # jh-r23-cal
            lines.append(f"{e['title']} — {day}, {when}")

        conf = find_conflicts(evts, gap)
        cflines = []
        for a, b in conf["overlaps"]:
            cflines.append(
                f"conflict: “{a['title']}” and “{b['title']}” overlap "
                f"({_fmt_time(a['start'])} / {_fmt_time(b['start'])})")
        for a, b in conf["tight"]:
            mins = int((b["start"] - a["end"]).total_seconds() // 60)
            cflines.append(
                f"tight: only {mins} min between “{a['title']}” and "
                f"“{b['title']}”")

        # jh-r22-cal (step 164, review round 22 #5): at most 60 event lines and 20 conflict lines go to the model
        _jh_more = max(0, len(lines) - 60)
        _jh_after = evts[59]["start"].strftime("%A %d %B") if _jh_more else ""
        lines = lines[:60]
        _jh_cmore = max(0, len(cflines) - 20)
        cflines = cflines[:20]
        # jh-r21-cal (step 163, review round 21 #3): the completeness claim only for what was really read
        if events is None:
            _jh_note = ("Only the current or next event of each calendar is known here; later events in this window may exist, "
                        "so never say these are all. Each says its own day. Never present a later day as today.")
        elif partial:
            _jh_note = ("Every event in this window is listed, except for %s, of which only the next event could be read "
                        "(later ones may exist; say so if it matters). Each says its own day. Never present a later day as "
                        "today." % ", ".join(sorted(partial)))
        else:
            _jh_note = ("Every event of the readable calendars in this window is listed; each says its own day. Never present "
                        "a later day as today.")
        if _jh_more:   # jh-r22-cal: cut, so no completeness claim
            _jh_note = ("Only the first 60 events in this window are listed; %d more events in this window are not listed "
                        "(after %s, until %s). Never say these are all; for later events ask about a shorter window or a "
                        "day. Each says its own day. Never present a later day as today."
                        % (_jh_more, _jh_after, horizon.strftime("%A %d %B")))
        if _jh_cmore:
            _jh_note += " %d more conflict lines are not listed." % _jh_cmore
        # jh-r23-cal (step 165, review round 23 #7): never an earlier day as today either
        _jh_note += " An event that began on an earlier day says 'ongoing since'; never say it starts today."
        return {"today": now.strftime("%A %d %B %Y"), "events": lines, "conflicts": cflines, "count": len(evts),
                "listed": len(lines), "note": _jh_note}
    except Exception as exc:
        _LOGGER.debug("comms.agenda failed: %s", exc)
        return {"events": [], "conflicts": [], "count": 0, "error": str(exc)}
