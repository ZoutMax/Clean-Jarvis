"""
JARVIS — Presence awareness.

Reads HA's person.* entities, zone states, and (when available) mmWave room
sensors to know who is home and where. Provides a concise summary string for
the conversation agent's context.
"""
from __future__ import annotations

import logging
from collections import deque
from typing import Optional

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

# SHADOW (#237, Epistemic Fabric — Conflict): get_presence_summary() also
# adjudicates each person's own backing device_trackers through kernel.conflict —
# one kernel.provenance.Provenance per tracker (value = its home/away/zone state,
# source = the tracker entity, reliability by source_type, observed-at from the
# state's last_updated) — and logs the resolved winner, whether it is CONTESTED
# (trackers disagree within the margin) and whether it AGREES with HA's own
# person.state. This is the primitive's canonical case: the phone says home, the
# watch says away. Observe-only: HA's own resolution still stands and the summary
# is unchanged; a consumer reads the adjudicated value authoritatively only at
# enforce (owner-gated). Flip CONFLICT_SHADOW to False to silence it.
CONFLICT_SHADOW = True

# PARITY (Epistemic Fabric — Conflict): over a rolling window of the per-person
# adjudications the shadow computes, log how often kernel.conflict's
# reliability-weighted winner AGREES with HA's native person.state and how often
# it would DEFER (CONTESTED). That is the quantified bar the owner-gated enforce
# rung needs — a consumer should read the adjudicated value only once it tracks
# HA closely and rarely defers. Rides inside the shadow emission (so it shares
# the same real reads); observe-only, drives nothing. Flip CONFLICT_PARITY off
# to silence it.
CONFLICT_PARITY = True
_CONFLICT_PARITY_WINDOW = 200
_conflict_verdicts: deque = deque(maxlen=_CONFLICT_PARITY_WINDOW)

# SHADOW (Epistemic Fabric — Conflict, multi-source fusion): get_presence_summary()
# also logs the world-model fusion of each person's whereabouts across independent
# source *types* — HA's own person.state vs a recent camera recognition — via
# WorldModel.fuse_presence (kernel.conflict over kernel.provenance records). This
# broadens the conflict adjudication from the per-device_tracker shadow above to
# whole source types, and is the multi-source verdict the Conflict enforce rung
# will read. Observe-only: HA's resolution still stands. Flip off to silence it.
PRESENCE_FUSION_SHADOW = True

# Trust prior by device_tracker source_type: GPS is strongest, a router/ping
# weaker, a BLE/beacon weaker still. Unknown sources get a neutral prior. These
# feed kernel.conflict's reliability term; the tracker's own assertion is taken
# at confidence 1.0 (it definitively reports a state), so trust lives here.
_TRACKER_RELIABILITY = {
    "gps": 0.9, "router": 0.7, "bluetooth": 0.6, "bluetooth_le": 0.55,
}
_DEFAULT_TRACKER_RELIABILITY = 0.5


def _norm_presence(state) -> Optional[str]:
    """Canonicalize a person/tracker state for conflict grouping: 'home', 'away'
    (from not_home/away), or a lower-cased zone name; None for unknown/
    unavailable so it contributes no vote."""
    s = str(state or "").strip().lower()
    if s in ("", "unknown", "unavailable", "none"):
        return None
    if s == "not_home":
        return "away"
    return s


def _emit_presence_conflict_shadow(hass: HomeAssistant) -> None:
    """For each person with ≥2 backing device_trackers, adjudicate the trackers
    through kernel.conflict and log the winner / contested / agreement with HA's
    own person.state (shadow). Best-effort, never raises — the presence summary
    is unaffected either way."""
    try:
        import time as _t
        from .kernel import conflict as _conflict
        from .kernel import provenance as _prov
        now = _t.time()
        for pstate in hass.states.async_all("person"):
            try:
                trackers = pstate.attributes.get("device_trackers") or []
                if len(trackers) < 2:
                    continue  # no possible conflict with fewer than two sources
                records = []
                reliabilities = {}
                for tid in trackers:
                    tstate = hass.states.get(tid)
                    if not tstate:
                        continue
                    val = _norm_presence(tstate.state)
                    if val is None:
                        continue
                    stype = str(tstate.attributes.get("source_type", "")).lower()
                    reliabilities[tid] = _TRACKER_RELIABILITY.get(
                        stype, _DEFAULT_TRACKER_RELIABILITY)
                    try:
                        observed = tstate.last_updated.timestamp()
                    except Exception:
                        observed = now
                    records.append(_prov.record(
                        val, source=tid, confidence=1.0, now=(lambda o=observed: o)))
                if len(records) < 2:
                    continue
                res = _conflict.resolve(records, reliabilities=reliabilities, now=now)
                ha_state = _norm_presence(pstate.state)
                verdict = ("CONTESTED" if res.contested
                           else "AGREEMENT" if (res.resolved and res.value == ha_state)
                           else "DIVERGENCE" if res.resolved
                           else "—")
                _LOGGER.debug(
                    "presence_conflict(shadow): %s kernel=%r ha=%r resolved=%s %s",
                    pstate.entity_id, res.value, ha_state, res.resolved, verdict)
                # PARITY: accumulate this real adjudication's verdict.
                if CONFLICT_PARITY and verdict != "—":
                    _conflict_verdicts.append(verdict)
            except Exception:
                continue
        _emit_presence_conflict_parity()
    except Exception:   # pragma: no cover - defensive
        pass


def _emit_presence_fusion_shadow(hass: HomeAssistant) -> None:
    """Log the world-model multi-source presence fusion per person — HA's own
    person.state vs a recent camera recognition, adjudicated through
    WorldModel.fuse_presence (kernel.conflict). Observe-only, best-effort, never
    raises — the presence summary is unaffected. Only a genuine ≥2-source case
    (a resolved or contested fusion) is logged."""
    if not PRESENCE_FUSION_SHADOW:
        return
    try:
        from .kernel.world_model import WorldModel
        wm = WorldModel(hass)
        for pstate in hass.states.async_all("person"):
            try:
                name = (pstate.attributes.get("friendly_name")
                        or pstate.entity_id.split(".", 1)[-1])
                res = wm.fuse_presence(name)
                if res.resolved or res.contested:
                    verdict = "CONTESTED" if res.contested else "resolved"
                    _LOGGER.debug(
                        "presence_fusion(shadow): %s kernel=%r %s ranked=%s",
                        pstate.entity_id, res.value, verdict,
                        [[v, round(s, 3)] for v, s in res.ranked])
            except Exception:
                continue
    except Exception:   # pragma: no cover - defensive
        pass


def _emit_presence_conflict_parity() -> None:
    """PARITY (Epistemic Fabric — Conflict): log the accumulated agreement and
    contested rates of kernel.conflict's winner vs HA's native person.state over
    the rolling window. Best-effort, never raises; reads the window, drives
    nothing."""
    if not CONFLICT_PARITY:
        return
    try:
        n = len(_conflict_verdicts)
        if n == 0:
            return
        agree = sum(1 for v in _conflict_verdicts if v == "AGREEMENT")
        diverge = sum(1 for v in _conflict_verdicts if v == "DIVERGENCE")
        contested = sum(1 for v in _conflict_verdicts if v == "CONTESTED")
        decided = agree + diverge            # non-contested resolutions
        agree_rate = (agree / decided) if decided else 0.0
        contested_rate = contested / n
        _LOGGER.debug(
            "presence_conflict(parity): agree_rate=%.2f (%d/%d) "
            "contested_rate=%.2f n=%d", agree_rate, agree, decided,
            contested_rate, n)
    except Exception:   # pragma: no cover - defensive
        pass


def _person_state(hass: HomeAssistant, entity_id: str) -> dict:
    """Extract useful data from a person.* entity."""
    state = hass.states.get(entity_id)
    if not state:
        return {}
    return {
        "name":  state.attributes.get("friendly_name", entity_id.split(".", 1)[-1]),
        "state": state.state,  # 'home', 'not_home', or a zone name
        "latitude":  state.attributes.get("latitude"),
        "longitude": state.attributes.get("longitude"),
    }


def everyone_confidently_away(hass) -> bool:
    """True only when there are tracked people and EVERY one is explicitly away
    (not_home/away). Unknown or unavailable presence returns False — fail open,
    so actions like scheduled briefings are only suppressed when we are sure the
    house is empty, not merely because presence is uncertain (v6.95.0)."""
    try:
        summ = get_presence_summary(hass)
        people = summ.get("people", [])
        if not people or summ.get("home_count", 0) > 0:
            return False
        return all(str(p.get("state", "")).lower() in ("not_home", "away")
                   for p in people)
    except Exception:
        return False


# Device classes that mean "a human is (still) in this area". mmWave/radar
# presence sensors (Shelly Presence Gen4, Aqara FP2, Everything Presence, ESPHome
# LD2410/LD2420, …) report one of these. `motion` is deliberately excluded from
# the awareness summary — it's transient and would make the "who's where" view
# flap — but audio_routing still uses it for immediate room routing.
_PRESENCE_CLASSES = ("occupancy", "presence")

# A radar occupant/target count sensor has no standard device_class, so it's
# recognised by name shape: a numeric "...count" naming a person/target/object.
_COUNT_NAME_TOKENS = ("target", "person", "people", "occupan", "object", "presence")


def _to_number(value) -> Optional[float]:
    """Parse a state string to a float, or None (unknown/unavailable/non-numeric)."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if f == f else None  # drop NaN


def _area_room_name(hass, area_id: Optional[str]) -> Optional[str]:
    """The HA area's human name (lowercased) for an area_id, or None."""
    if not area_id:
        return None
    try:
        from homeassistant.helpers import area_registry as ar
        area = ar.async_get(hass).async_get_area(area_id)
        nm = getattr(area, "name", None) if area else None
        return nm.lower() if nm else None
    except Exception:
        return None


def get_presence_summary(hass: HomeAssistant) -> dict:
    """
    Return a summary of who's home and where.

    Shape:
      {
        "total_people":  int,
        "home_count":    int,
        "away_count":    int,
        "people":        [{"name": "Sam", "state": "home", ...}, ...],
        "rooms":         {"kitchen": [], "living room": []},   # occupied areas
        "room_details":  {"living room": {"occupants": 2, "lux": 40.0}},
        "anyone_home":   bool,
      }

    ``rooms`` keys are occupied areas (mmWave/radar presence sensors, mapped to
    their HA area). ``room_details`` adds occupant count + illuminance per room
    where a radar count / illuminance sensor exists in that area.
    """
    people = []
    for state in hass.states.async_all("person"):
        info = _person_state(hass, state.entity_id)
        if info:
            people.append(info)

    home_count = sum(1 for p in people if p.get("state") == "home")
    away_count = len(people) - home_count

    # Room detection via mmWave/radar presence sensors (Shelly Presence Gen4,
    # Aqara FP2, Everything Presence, ESPHome LD2410, …). Recognise the whole
    # presence family (`occupancy` + `presence`), and map each sensor to the HA
    # AREA its device is assigned to — far more reliable than parsing the sensor's
    # own name, which turns e.g. "Presence Gen4 Occupancy" into a bogus room.
    rooms: dict[str, list[str]] = {}
    room_details: dict[str, dict] = {}
    _area_to_room: dict[str, str] = {}   # area_id -> room, for occupied areas only
    try:
        from .entity_filter import is_excluded as _excl
    except Exception:
        _excl = lambda _h, _e: False
    try:
        from .entity_filter import is_nonperson_object_sensor as _nonperson
    except Exception:
        _nonperson = lambda _e, _n=None: False
    try:
        from .audio_routing import entity_area as _entity_area
    except Exception:
        _entity_area = lambda _h, _e: None

    for state in hass.states.async_all("binary_sensor"):
        if state.state != "on":
            continue
        if state.attributes.get("device_class") not in _PRESENCE_CLASSES:
            continue
        if _excl(hass, state.entity_id):
            continue  # user excluded this sensor (e.g. a virtual occupancy sensor)
        name = state.attributes.get("friendly_name", state.entity_id)
        if _nonperson(state.entity_id, name):
            continue  # #254: a car/animal/package detector is not a human being present

        # Prefer the HA area the device is in; fall back to parsing the name.
        area_id = _entity_area(hass, state.entity_id)
        room = _area_room_name(hass, area_id)
        if room:
            if area_id:
                _area_to_room[area_id] = room
        else:
            room = name.lower().replace("presence", "").replace("occupancy", "").strip()
        if room:
            rooms.setdefault(room, [])

    # Occupant COUNT and ILLUMINANCE from radar/light sensors, attributed to an
    # occupied room by HA area — so JARVIS knows "2 people in the living room at
    # 40 lux", not just that the room is occupied. Only for rooms already detected
    # as occupied, so idle sensors elsewhere add no noise.
    if _area_to_room:
        for state in hass.states.async_all("sensor"):
            area_id = _entity_area(hass, state.entity_id)
            room = _area_to_room.get(area_id) if area_id else None
            if not room or _excl(hass, state.entity_id):
                continue
            val = _to_number(state.state)
            if val is None:
                continue
            dc = state.attributes.get("device_class")
            nm = str(state.attributes.get("friendly_name", state.entity_id)).lower()
            det = room_details.setdefault(room, {})
            if dc == "illuminance":
                if det.get("lux") is None or val > det["lux"]:
                    det["lux"] = val
            elif "count" in nm and any(tok in nm for tok in _COUNT_NAME_TOKENS):
                iv = int(val)
                if det.get("occupants") is None or iv > det["occupants"]:
                    det["occupants"] = iv

    if CONFLICT_SHADOW:
        _emit_presence_conflict_shadow(hass)   # #237: observe-only
    if PRESENCE_FUSION_SHADOW:
        _emit_presence_fusion_shadow(hass)     # multi-source fusion: observe-only

    return {
        "total_people": len(people),
        "home_count":   home_count,
        "away_count":   away_count,
        "people":       people,
        "rooms":        rooms,
        "room_details": room_details,   # {room: {"occupants": int, "lux": float}}
        "anyone_home":  home_count > 0,
    }


async def jh_hidden_people(hass, user_id) -> dict:
    """jh-r4-hidden-people (review round 4): {person entity id: lower-case names (full and first)} of the people this login
    may not know the whereabouts of (the where-is rule); every person for voice with no login."""
    out = {}
    for pst in hass.states.async_all("person"):
        try:
            if user_id and await _jh_may_see(hass, user_id, pst):
                continue
        except Exception:
            pass
        name = str(pst.attributes.get("friendly_name") or pst.entity_id.split(".", 1)[1]).strip().lower()
        out[pst.entity_id] = {name, name.split()[0]} if name else set()
    return out


def presence_context_string(hass: HomeAssistant, hidden=None) -> str:
    """
    One-line summary suitable for injecting into the LLM system prompt.
    Example: "Sam is home. Alex is away. 3 presence sensors active: kitchen, office."
    """
    data = get_presence_summary(hass)
    if not data["people"]:
        return "No person entities configured in Home Assistant."

    bits: list[str] = []
    _jh_hid = {n for names in (hidden or {}).values() for n in names}
    for p in data["people"]:
        state = p["state"]
        if hidden is not None and str(p.get("name") or "").strip().lower() in _jh_hid:   # review round 4: the where-is rule
            bits.append(f"{p['name']}: location private")
            continue
        if state == "home":
            bits.append(f"{p['name']} is home")
        elif state == "not_home":
            bits.append(f"{p['name']} is away")
        else:
            # A zone name like 'Work' or 'School'
            bits.append(f"{p['name']} is at {state.replace('_', ' ')}")

    summary = ". ".join(bits) + "."
    if data["rooms"]:
        details = data.get("room_details", {})
        parts: list[str] = []
        for room in data["rooms"].keys():
            d = details.get(room) or {}
            extra: list[str] = []
            occ = d.get("occupants")
            if occ:
                extra.append(f"{occ} {'person' if occ == 1 else 'people'}")
            lux = d.get("lux")
            if lux is not None:
                extra.append(f"{round(lux)} lux")
            parts.append(f"{room} ({', '.join(extra)})" if extra else room)
        summary += f" Occupied areas: {', '.join(parts)}."
    try:   # Jarvis Home patch (2026-11-14): jh-person-where - pronouns, so the AI never guesses
        _jh_pl = jh_pronoun_line(hass)
        if _jh_pl:
            summary += " " + _jh_pl
    except Exception:
        pass
    return summary


def find_person(hass: HomeAssistant, name: str) -> Optional[dict]:
    """Find a person entity by friendly name (case-insensitive, partial match OK)."""
    needle = name.lower().strip()
    for state in hass.states.async_all("person"):
        friendly = state.attributes.get("friendly_name", "").lower()
        if needle in friendly or needle in state.entity_id.lower():
            return _person_state(hass, state.entity_id)
    return None


# Jarvis Home patch (2026-11-14): jh-person-where - "where is <person>?" answered from Home Assistant's own data, and
# pronouns per person from the person_pronouns setting (default: the name or they; JARVIS never guesses a gender).
import json as _jh_json
import math as _jh_math
import re as _jh_re
import time as _jh_time

JH_PRONOUNS = {"she": ("she", "her", "her"), "he": ("he", "him", "his"), "they": ("they", "them", "their")}
JH_STALE_S = 6 * 3600          # older than this, a location is "last known", not current
_JH_DEAD = ("", "unknown", "unavailable", "none", "null")
# jh-r23-lang (step 165, review round 23 #6): country names for a German reply (the where-is sentences JARVIS words itself are
# German or English); a code not listed takes the phone's own Latin name, else the English name without "the"
_JH_COUNTRIES_DE = {
    "DE": "Deutschland", "NL": "Niederlande", "BE": "Belgien", "LU": "Luxemburg", "FR": "Frankreich", "AT": "Österreich",
    "CH": "Schweiz", "IT": "Italien", "ES": "Spanien", "PT": "Portugal", "GB": "Großbritannien", "IE": "Irland",
    "US": "Vereinigte Staaten", "CA": "Kanada", "DK": "Dänemark", "SE": "Schweden", "NO": "Norwegen", "FI": "Finnland",
    "IS": "Island", "PL": "Polen", "CZ": "Tschechien", "SK": "Slowakei", "HU": "Ungarn", "HR": "Kroatien", "SI": "Slowenien",
    "RO": "Rumänien", "BG": "Bulgarien", "RS": "Serbien", "GR": "Griechenland", "CY": "Zypern", "MT": "Malta", "EE": "Estland",
    "LV": "Lettland", "LT": "Litauen", "TR": "Türkei", "UA": "Ukraine", "RU": "Russland", "MA": "Marokko",
    "EG": "Ägypten", "ZA": "Südafrika", "AE": "Vereinigte Arabische Emirate", "IL": "Israel", "IN": "Indien",
    "TH": "Thailand", "VN": "Vietnam", "MY": "Malaysia", "SG": "Singapur", "ID": "Indonesien", "PH": "Philippinen",
    "CN": "China", "JP": "Japan", "KR": "Südkorea", "AU": "Australien", "NZ": "Neuseeland", "MX": "Mexiko", "BR": "Brasilien",
    "AR": "Argentinien", "CL": "Chile", "CO": "Kolumbien", "PE": "Peru",
}
_JH_COUNTRIES = {
    "AD": "Andorra", "AE": "the United Arab Emirates", "AR": "Argentina", "AT": "Austria", "AU": "Australia",
    "BE": "Belgium", "BG": "Bulgaria", "BR": "Brazil", "CA": "Canada", "CH": "Switzerland", "CL": "Chile", "CN": "China",
    "CO": "Colombia", "CY": "Cyprus", "CZ": "Czechia", "DE": "Germany", "DK": "Denmark", "EE": "Estonia", "EG": "Egypt",
    "ES": "Spain", "FI": "Finland", "FR": "France", "GB": "the United Kingdom", "GR": "Greece", "HK": "Hong Kong",
    "HR": "Croatia", "HU": "Hungary", "ID": "Indonesia", "IE": "Ireland", "IL": "Israel", "IN": "India", "IS": "Iceland",
    "IT": "Italy", "JP": "Japan", "KH": "Cambodia", "KR": "South Korea", "LA": "Laos", "LK": "Sri Lanka",
    "LT": "Lithuania", "LU": "Luxembourg", "LV": "Latvia", "MA": "Morocco", "MM": "Myanmar", "MT": "Malta",
    "MX": "Mexico", "MY": "Malaysia", "NL": "the Netherlands", "NO": "Norway", "NZ": "New Zealand", "PH": "the Philippines",
    "PL": "Poland", "PT": "Portugal", "RO": "Romania", "RS": "Serbia", "SA": "Saudi Arabia", "SE": "Sweden",
    "SG": "Singapore", "SI": "Slovenia", "SK": "Slovakia", "TH": "Thailand", "TR": "Turkey", "TW": "Taiwan",
    "UA": "Ukraine", "US": "the United States", "VN": "Vietnam", "ZA": "South Africa",
    "RU": "Russia", "BY": "Belarus", "KZ": "Kazakhstan", "GE": "Georgia", "AM": "Armenia", "AZ": "Azerbaijan", "MD": "Moldova",
    "IR": "Iran", "IQ": "Iraq", "JO": "Jordan", "LB": "Lebanon", "SY": "Syria", "KW": "Kuwait", "QA": "Qatar", "BH": "Bahrain",
    "OM": "Oman", "YE": "Yemen", "MA": "Morocco", "DZ": "Algeria", "TN": "Tunisia", "LY": "Libya", "SD": "Sudan",
    "PK": "Pakistan", "BD": "Bangladesh", "NP": "Nepal", "MM": "Myanmar", "MN": "Mongolia", "BT": "Bhutan", "ET": "Ethiopia",
    "MK": "North Macedonia", "BA": "Bosnia and Herzegovina", "ME": "Montenegro", "AL": "Albania", "MO": "Macao",
}


def jh_clean_pronouns(value) -> dict:
    """Check the person_pronouns setting: {person entity: she | he | they}. Returns the clean dict without the default
    (they); a ValueError carries a message for the panel."""
    if value is None or value == "":
        return {}
    if isinstance(value, str):
        try:
            value = _jh_json.loads(value)
        except ValueError:
            raise ValueError('Pronouns must be a JSON object like {"person.sam": "she"}') from None
    if not isinstance(value, dict):
        raise ValueError("Pronouns must be an object: person entity -> she, he or they")
    if len(value) > 200:
        raise ValueError("At most 200 people")
    out = {}
    for key, val in value.items():
        k = key.strip() if isinstance(key, str) else ""
        if not _jh_re.fullmatch(r"person\.[a-z0-9_]+", k):
            raise ValueError(f"{key!r} is not a person entity (person.<name>)")
        p = val.strip().lower() if isinstance(val, str) else ""
        if p not in JH_PRONOUNS:
            raise ValueError(f"{k}: the pronoun must be she, he or they")
        if p != "they":
            out[k] = p
    return out


def _jh_entry(hass):
    try:
        from .const import DOMAIN
        for entry in hass.config_entries.async_entries(DOMAIN):
            return entry
    except Exception:
        pass
    return None


def jh_pronouns(hass) -> dict:
    """Every person in Home Assistant now -> she, he or they (the default). A saved value for a person who no longer
    exists is ignored; a broken setting counts as not set."""
    try:
        from . import jarvis_config
        saved = jh_clean_pronouns(jarvis_config.runtime_get(hass, _jh_entry(hass), "person_pronouns", None))
    except Exception:
        saved = {}
    try:
        return {st.entity_id: saved.get(st.entity_id, "they") for st in hass.states.async_all("person")}
    except Exception:
        return {}


def jh_pronoun(hass, person_eid: str) -> tuple:
    """(subject, object, possessive) for a person: she/her/her, he/him/his or they/them/their."""
    return JH_PRONOUNS[jh_pronouns(hass).get(person_eid, "they")]


def _jh_person_name(st) -> str:
    return str(st.attributes.get("friendly_name") or st.entity_id.split(".", 1)[-1].replace("_", " ").title())


def jh_pronoun_line(hass) -> str:
    """One line for the AI: who uses which pronoun, and never guess."""
    try:
        people = list(hass.states.async_all("person"))
    except Exception:
        return ""
    if not people:
        return ""
    pr = jh_pronouns(hass)
    named = [f"{_jh_person_name(st)}: {'/'.join(JH_PRONOUNS[pr[st.entity_id]][:2])}"
             for st in people if pr.get(st.entity_id, "they") != "they"]
    return ("Pronouns: " + ("; ".join(named) + ". " if named else "")
            + "Anyone else: use their name or they/them. Never guess anyone's gender from a name.")


def _jh_ts(st):
    """Unix time of a state's last update (last_reported when newer), or None."""
    best = None
    for attr in ("last_updated", "last_reported"):
        v = getattr(st, attr, None)
        if v is None:
            continue
        try:
            t = v.timestamp() if hasattr(v, "timestamp") else float(v)
        except (TypeError, ValueError):
            continue
        best = t if best is None else max(best, t)
    return best


def _jh_attr(st, key) -> str:
    v = st.attributes.get(key)
    v = str(v).strip() if v is not None else ""
    return "" if v.lower() in _JH_DEAD else v


def _jh_latin(s: str) -> bool:
    return all(ord(c) < 0x250 for c in s if c.isalpha())


def _jh_person_devices(hass, pst) -> list:
    """Entity ids per device for a person: the devices of the person's device_trackers, and the companion apps
    (mobile_app) signed in with the person's login - the same link the People page uses. A tracker with no device
    stands alone."""
    groups, seen, dev_ids = [], set(), []
    try:
        from homeassistant.helpers import device_registry as dr, entity_registry as er
        ents, devs = er.async_get(hass), dr.async_get(hass)
    except Exception:
        er = dr = ents = devs = None
    for tracker in pst.attributes.get("device_trackers") or []:
        ent = ents.async_get(tracker) if ents is not None else None
        did = getattr(ent, "device_id", None) if ent is not None else None
        if isinstance(did, str) and did:
            dev_ids.append(did)
        else:
            groups.append([tracker])
    uid = pst.attributes.get("user_id")
    if uid and devs is not None:
        try:
            for entry in hass.config_entries.async_entries("mobile_app"):
                if (entry.data or {}).get("user_id") != uid or getattr(entry, "disabled_by", None):
                    continue
                for dev in dr.async_entries_for_config_entry(devs, entry.entry_id):
                    dev_ids.append(dev.id)
        except Exception:
            pass
    for did in dev_ids:
        if did in seen:
            continue
        seen.add(did)
        try:
            groups.append([e.entity_id for e in er.async_entries_for_device(ents, did)])
        except Exception:
            pass
    return groups


def _jh_is_geocoded(hass, eid: str) -> bool:
    if not eid.startswith("sensor."):
        return False
    if eid.endswith("_geocoded_location"):
        return True
    try:
        from homeassistant.helpers import entity_registry as er
        ent = er.async_get(hass).async_get(eid)
        uid = getattr(ent, "unique_id", None)
        return isinstance(uid, str) and uid.endswith("geocoded_location")
    except Exception:
        return False


def _jh_best_geocoded(hass, pst):
    """The person's geocoded-location sensor that updated most recently (or None)."""
    best, best_t = None, None
    for group in _jh_person_devices(hass, pst):
        cands = [e for e in group if _jh_is_geocoded(hass, e)]
        for e in group:   # a tracker with no registry entry: its companion sensor by name
            if e.startswith("device_tracker."):
                cands.append("sensor." + e.split(".", 1)[1] + "_geocoded_location")
        for eid in cands:
            st = hass.states.get(eid)
            if st is None or str(st.state).strip().lower() in _JH_DEAD:
                continue
            t = _jh_ts(st)
            if best is None or (t or 0) > (best_t or 0):
                best, best_t = st, t
    return best


import unicodedata as _jh_ud130

_JH_LANG_SCRIPTS = {"th": {"THAI"}, "zh": {"CJK"}, "ja": {"CJK", "HIRAGANA", "KATAKANA"}, "ko": {"HANGUL", "CJK"},
                    "ru": {"CYRILLIC"}, "uk": {"CYRILLIC"}, "bg": {"CYRILLIC"}, "sr": {"CYRILLIC"}, "mk": {"CYRILLIC"},
                    "be": {"CYRILLIC"}, "kk": {"CYRILLIC"}, "mn": {"CYRILLIC"}, "el": {"GREEK"}, "he": {"HEBREW"},
                    "ar": {"ARABIC"}, "fa": {"ARABIC"}, "ur": {"ARABIC"}, "hi": {"DEVANAGARI"}, "bn": {"BENGALI"},
                    "ta": {"TAMIL"}, "te": {"TELUGU"}, "my": {"MYANMAR"}, "km": {"KHMER"}, "lo": {"LAO"}, "ka": {"GEORGIAN"},
                    "hy": {"ARMENIAN"}, "am": {"ETHIOPIC"}}
_JH_TRANSLIT = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюяєіїґ",
                        ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r", "s", "t", "u",
                         "f", "kh", "ts", "ch", "sh", "shch", "", "y", "", "e", "yu", "ya", "ye", "i", "yi", "g"]))
_JH_TRANSLIT.update(zip("αβγδεζηθικλμνξοπρσςτυφχψωάέήίόύώϊϋΐΰ",
                        ["a", "v", "g", "d", "e", "z", "i", "th", "i", "k", "l", "m", "n", "x", "o", "p", "r", "s", "s", "t", "y",
                         "f", "ch", "ps", "o", "a", "e", "i", "i", "o", "y", "o", "i", "y", "i", "y"]))


def _jh_scripts(text) -> set:
    """The scripts of the letters in text: LATIN, THAI, CYRILLIC, ARABIC, CJK, HANGUL, ..."""
    out = set()
    for ch in str(text or ""):
        if ch.isalpha():
            name = _jh_ud130.name(ch, "")
            out.add(name.split(" ")[0] if name else "?")
    return out


def _jh_fits(text, lang) -> bool:
    """True when text is written in the reply language's script (Latin is fine in every reply)."""
    return _jh_scripts(text) <= (_JH_LANG_SCRIPTS.get(lang, set()) | {"LATIN"})


def _jh_translit(text):
    """Cyrillic or Greek to Latin letters ("Москва" -> "Moskva"); None for any other script (no clean offline way)."""
    if not text or not _jh_scripts(text) <= {"CYRILLIC", "GREEK", "LATIN"}:
        return None
    out = []
    for ch in str(text):
        low = ch.lower()
        if low in _JH_TRANSLIT:
            t = _JH_TRANSLIT[low]
            out.append(t.capitalize() if ch != low and t else t)
        else:
            out.append(ch)
    s = "".join(out).strip()
    return s or None


def _jh_reply_code(hass) -> str:
    """The reply language as a primary code ("en", "th", ...), as the where-is answers use it."""
    try:
        from .language import configured_language
        return str(configured_language(hass, None) or "en").split("-")[0].lower()
    except Exception:
        return "en"


def _jh_place(geo, spoken=False, lang="en") -> str:
    """Town, region, country from a geocoded sensor, in the reply's script: names in another script are transliterated
    (Cyrillic, Greek, for a Latin-script reply) or left out, and then the country comes from its ISO code. A reply in a
    non-Latin language keeps names in its own script (a Thai reply keeps Thai)."""
    town = _jh_attr(geo, "locality") or _jh_attr(geo, "sub_administrative_area")
    region = _jh_attr(geo, "administrative_area")
    iso = _jh_attr(geo, "iso_country_code").upper()
    native = _jh_attr(geo, "country")
    if lang in _JH_LANG_SCRIPTS and native and _jh_fits(native, lang) and not _jh_latin(native):
        country = native                                   # the reply's own script, e.g. Thai
    elif lang == "de":   # jh-r23-lang (step 165, review round 23 #6): a German where-is sentence names the country in German
        country = (_JH_COUNTRIES_DE.get(iso) or (native if native and _jh_latin(native) else "")
                   or (_JH_COUNTRIES.get(iso) or "").removeprefix("the ") or iso)
    else:
        country = _JH_COUNTRIES.get(iso) or (native if native and _jh_latin(native) else "") or iso
    latin_reply = lang not in _JH_LANG_SCRIPTS
    parts = []
    for p in (town, region):
        if not p:
            continue
        if not _jh_fits(p, lang):
            p = _jh_translit(p) if latin_reply else None
            if not p:
                continue
        if p.casefold() != country.casefold() and p.casefold() not in [x.casefold() for x in parts]:
            parts.append(p)
    return ", ".join(parts + ([country] if country else []))


def _jh_answer_lang(hass) -> str:
    """The language of the where-is sentences JARVIS words itself: German or English (other languages go to the AI, step 127),
    so their place names are always in Latin script."""
    try:
        return "de" if _jh_reply_lang(hass) == "de" else "en"
    except Exception:
        return "en"


def _jh_address_for_reply(address, lang) -> str:
    """The full address only when it is in the reply's script; else "" (the answer then uses town and country)."""
    address = str(address or "").strip()
    return address if address and _jh_fits(address, lang) else ""


def _jh_place_v122(geo, spoken=False) -> str:
    """Town, region, country from a geocoded sensor. Names in another script (Thai, ...) follow in brackets in text and
    are left out when spoken (the voice cannot read them)."""
    town = _jh_attr(geo, "locality") or _jh_attr(geo, "sub_administrative_area")
    region = _jh_attr(geo, "administrative_area")
    iso = _jh_attr(geo, "iso_country_code").upper()
    country = _JH_COUNTRIES.get(iso) or _jh_attr(geo, "country") or iso
    if spoken and country and not _jh_latin(country):
        country = iso
    parts = []
    for p in (town, region):
        if p and p.casefold() != country.casefold() and p.casefold() not in [x.casefold() for x in parts]:
            parts.append(p)
    latin = [p for p in parts if _jh_latin(p)]
    other = [p for p in parts if not _jh_latin(p)]
    text = ", ".join(latin + ([country] if country else []))
    if other and not spoken:
        text = f"{text} ({', '.join(other)})" if text else ", ".join(other)
    return text


def _jh_km(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = _jh_math.radians(lat1), _jh_math.radians(lat2)
    a = (_jh_math.sin((p2 - p1) / 2) ** 2
         + _jh_math.cos(p1) * _jh_math.cos(p2) * _jh_math.sin(_jh_math.radians(lon2 - lon1) / 2) ** 2)
    return 6371.0 * 2 * _jh_math.asin(min(1.0, _jh_math.sqrt(a)))


def _jh_coords(st):
    try:
        lat, lon = st.attributes.get("latitude"), st.attributes.get("longitude")
        if lat is None or lon is None:
            loc = st.attributes.get("location")
            lat, lon = loc[0], loc[1]
        return float(lat), float(lon)
    except Exception:
        return None


def jh_ago(seconds) -> str:
    s = max(0, int(seconds))
    if s < 90:
        return "just now"
    if s < 3600:
        return f"{round(s / 60)} minutes ago"
    if s < 48 * 3600:
        h = round(s / 3600)
        return "1 hour ago" if h == 1 else f"{h} hours ago"
    return f"{round(s / 86400)} days ago"


def jh_person_location(hass, person_eid: str, now=None, spoken=False, lang=None):
    """What Home Assistant knows about where a person is. None when there is no such person."""
    pst = hass.states.get(person_eid)
    if pst is None:
        return None
    now = _jh_time.time() if now is None else now
    state = str(pst.state or "").strip()
    zone = None
    if state == "home":
        zone = "home"
    elif state.lower() not in _JH_DEAD + ("not_home", "away"):
        zone = state                      # a person's state is the zone's name
    geo = _jh_best_geocoded(hass, pst)
    geo_t = _jh_ts(geo) if geo is not None else None
    coords = _jh_coords(pst) or (_jh_coords(geo) if geo is not None else None)
    dist = None
    home = hass.states.get("zone.home")
    hc = _jh_coords(home) if home is not None else None
    if coords and hc:
        dist = _jh_km(coords[0], coords[1], hc[0], hc[1])
    pt = _jh_ts(pst)
    return {
        "entity_id": person_eid, "name": _jh_person_name(pst), "state": state, "zone": zone,
        # jh-place-script: with lang (an answer JARVIS words itself) only names in that language's script; without it
        # (facts for the AI) the place as before, with names in other scripts in brackets so the AI can use them
        "place": (_jh_place(geo, spoken, lang) if lang else _jh_place_v122(geo, spoken)) if geo is not None else "",
        "address": (_jh_address_for_reply(geo.state, lang) if lang else str(geo.state).strip()) if geo is not None else "",
        "geo_age": (now - geo_t) if geo_t is not None else None,
        "age": (now - pt) if pt is not None else None,
        "has_coords": bool(coords), "distance_km": dist,
    }


def _jh_dist_text(d) -> str:
    if d is None or d < 1:
        return ""
    return f"about {d:.1f} km from home" if d < 10 else f"about {round(d):,} km from home"


def jh_where_text(hass, info, exact=False, you=False, home_question=False) -> str:
    """The answer for one person, worded with their name (or 'you') and their set pronoun, never a guessed one."""
    if you:
        subj, be, obj, poss, owner = "You", "are", "you", "your", "Your"
    else:
        _s, obj, poss = jh_pronoun(hass, info["entity_id"])
        subj, be, owner = info["name"], "is", info["name"] + "'s"
    age = f" Last update: {jh_ago(info['age'])}." if info.get("age") is not None else ""
    if info["zone"] == "home":
        return f"{'Yes, ' + (subj if not you else 'you') if home_question else subj} {be} at home.{age}"
    lead = f"No, {subj if not you else 'you'} {be} not at home. " if home_question else ""
    if info["zone"]:
        return f"{lead}{subj} {be} at {info['zone']}.{age}"
    place = info["address"] if exact and info["address"] else info["place"]
    dist = _jh_dist_text(info.get("distance_km"))
    gage = info.get("geo_age")
    if place and gage is not None and gage <= JH_STALE_S:
        return (f"{lead}{subj} {be} {'at' if exact and info['address'] else 'in'} {place}{', ' + dist if dist else ''}. "
                f"Last update: {jh_ago(gage)}.")
    if place:
        when = f", {jh_ago(gage)}" if gage is not None else ""
        return f"{lead}{owner} last known location was {place}{when}. Nothing newer has come in from {poss} phone."
    if info["has_coords"] and dist:
        return f"{lead}{subj} {be} away, {dist}.{age} No place name has come in from {poss} phone."
    if info["state"].lower() in ("not_home", "away"):
        return f"{lead}{subj} {be} away from home, but no phone linked to {obj} has reported a location."
    return f"{lead}I have no location for {obj if you else info['name']}: no phone linked to {obj} has reported one."


def _jh_names(hass) -> dict:
    """Lower-case name -> person entity: the full name, the first name when no one else has it, the entity's own name."""
    out, first = {}, {}
    try:
        people = list(hass.states.async_all("person"))
    except Exception:
        people = []
    for st in people:
        full = _jh_person_name(st).strip().lower()
        if full:
            out.setdefault(full, st.entity_id)
        out.setdefault(st.entity_id.split(".", 1)[1].replace("_", " "), st.entity_id)
        if " " in full:
            first.setdefault(full.split()[0], set()).add(st.entity_id)
    for word, eids in first.items():
        if len(eids) == 1:
            out.setdefault(word, next(iter(eids)))
    return out


_JH_TAIL = r"(?: (?:located|now|right now|at the moment|currently|exactly|at|on the map|these days|today))*"
_JH_LOC = r"(?:(?:current|exact|last known|full|street|precise) )*(?:location|whereabouts|address|position|gps position)"


def jh_where_match(hass, text):
    """(person entity or 'self', exact, home_question) for a clear where-is question, else None."""
    t = (text or "").lower().replace("’", "'").replace("‘", "'")
    t = _jh_re.sub(r"[?!.,;:\"]+", " ", t)
    t = _jh_re.sub(r"\s+", " ", t).strip()
    t = _jh_re.sub(r"^(?:(?:hey|hi|ok|okay) )?jarvis ", "", t)
    t = _jh_re.sub(r" jarvis$", "", t)
    t = _jh_re.sub(r"^(?:(?:can|could) you )?(?:please )?(?:tell me|do you know|show me|give me) ", "", t)
    t = _jh_re.sub(r" please$", "", t).strip()
    exact = bool(_jh_re.search(r"\b(?:exact|address|street|precise)\b", t))
    # self forms, also the statement word order a typed question often has ("where i am located", 2026-11-15)
    if _jh_re.fullmatch(r"where (?:am i|i am|i'm|i m)" + _JH_TAIL + r"|what(?: is|'s| s) my " + _JH_LOC + r"|my " + _JH_LOC, t):
        return ("self", exact, False)
    if _jh_re.fullmatch(r"am i (?:at )?home(?: now| right now)?", t):
        return ("self", exact, True)
    names = _jh_names(hass)
    if not names:
        return None
    alt = "|".join(_jh_re.escape(n) for n in sorted(names, key=len, reverse=True))
    n = rf"(?P<n>{alt})"
    pats = [
        (rf"where(?: is|'s| s|s| are) {n}" + _JH_TAIL, False),
        (rf"where {n} is" + _JH_TAIL, False),
        (rf"(?:what(?: is|'s| s) )?{n}(?:'s|s'|') " + _JH_LOC + _JH_TAIL, False),
        (rf"(?:what(?: is|'s| s) )?the {_JH_LOC} of {n}", False),
        (rf"is {n} (?:at )?home(?: now| right now| yet| already)?", True),
    ]
    for pat, home_q in pats:
        m = _jh_re.fullmatch(pat, t)
        if m:
            return (names[m.group("n")], exact, home_q)
    return None


async def _jh_may_see(hass, user_id, pst) -> bool:
    """Someone's location goes to that person and to administrators. Voice with no login is nobody in particular."""
    if not user_id:
        return False
    if pst.attributes.get("user_id") == user_id:
        return True
    # Jarvis Home patch (2026-11-15): jh-location-sharing - in household mode every login linked to a person may see
    if jh_location_sharing(hass) == "household" and _jh_self(hass, user_id) is not None:
        return True
    try:
        user = await hass.auth.async_get_user(user_id)
    except Exception:
        user = None
    return bool(user is not None and getattr(user, "is_admin", False))


def _jh_self(hass, user_id):
    if not user_id:
        return None
    try:
        for st in hass.states.async_all("person"):
            if st.attributes.get("user_id") == user_id:
                return st
    except Exception:
        pass
    return None


# Jarvis Home patch (2026-11-15): jh-live-answers. Where-is answers: the asker's form of address, and Latin script when the
# reply language uses it (a Thai place name in an English reply cannot be read by the asker or by the voice).
_JH_NON_LATIN = {"th", "zh", "ja", "ko", "ru", "uk", "bg", "el", "he", "ar", "fa", "hi", "ka", "hy", "sr", "mk", "be", "kk",
                 "mn", "ur", "bn", "ta", "te", "my", "km", "lo", "am"}


def _jh_latin_reply(hass) -> bool:
    try:
        from .language import configured_language
        lang = str(configured_language(hass, None) or "en").split("-")[0].lower()
    except Exception:
        lang = "en"
    return lang not in _JH_NON_LATIN


def _jh_with_h(text, h):
    """The form of address at the end of the first sentence: 'You are at home, sir. Last update: ...'."""
    if not text or not h:
        return text
    i = text.find(". ")
    if i < 0:
        return text[:-1] + f", {h}." if text.endswith(".") else text
    return text[:i] + f", {h}" + text[i:]


# Jarvis Home patch (2026-11-15): jh-conv-lang. Where-is answers in German when that is the reply language (English otherwise).
import contextvars as _jh_cv133
_JH_ASK_LANG = _jh_cv133.ContextVar("jarvis_ask_lang", default=None)   # review 2026-11-15: the request's own language


def _jh_reply_lang(hass) -> str:
    if _JH_ASK_LANG.get():
        return _JH_ASK_LANG.get()
    try:
        from .language import configured_language
        return str(configured_language(hass, None) or "en").split("-")[0].lower()
    except Exception:
        return "en"


def _jh_ago_de(seconds) -> str:
    s = max(0, int(seconds))
    if s < 90:
        return "gerade eben"
    if s < 3600:
        return f"vor {round(s / 60)} Minuten"
    if s < 48 * 3600:
        hrs = round(s / 3600)
        return "vor 1 Stunde" if hrs == 1 else f"vor {hrs} Stunden"
    return f"vor {round(s / 86400)} Tagen"


def jh_where_text_de(hass, info, exact=False, you=False, home_question=False) -> str:
    subj, be = ("Sie", "sind") if you else (info["name"], "ist")
    age = f" Letzte Aktualisierung: {_jh_ago_de(info['age'])}." if info.get("age") is not None else ""
    if info["zone"] == "home":
        return f"{'Ja, ' if home_question else ''}{subj} {be} zu Hause.{age}"
    lead = f"Nein, {subj} {be} nicht zu Hause. " if home_question else ""
    if info["zone"]:
        return f"{lead}{subj} {be} bei {info['zone']}.{age}"
    place = info["address"] if exact and info["address"] else info["place"]
    d = info.get("distance_km")
    dist = "" if d is None or d < 1 else (f"etwa {d:.1f} km von zu Hause entfernt" if d < 10 else f"etwa {round(d):,} km von zu Hause entfernt".replace(",", "."))
    gage = info.get("geo_age")
    if place and gage is not None and gage <= JH_STALE_S:
        return f"{lead}{subj} {be} {'bei' if exact else 'in'} {place}{', ' + dist if dist else ''}. Letzte Aktualisierung: {_jh_ago_de(gage)}."
    if place:
        when = f", {_jh_ago_de(gage)}" if gage is not None else ""
        owner = "Ihr" if you else f"Der von {info['name']}"
        return f"{lead}{owner} letzter bekannter Ort war {place}{when}. Seitdem kam nichts Neueres vom Telefon."
    if info["has_coords"] and dist:
        return f"{lead}{subj} {be} unterwegs, {dist}.{age} Vom Telefon kam kein Ortsname."
    if info["state"].lower() in ("not_home", "away"):
        return f"{lead}{subj} {be} nicht zu Hause, aber kein verknüpftes Telefon hat einen Ort gemeldet."
    return f"{lead}Ich habe keinen Ort für {'Sie' if you else info['name']}: kein verknüpftes Telefon hat einen gemeldet."


async def jh_where_answer(hass, text, user_id="", honorific="sir", spoken=False, now=None):
    """The deterministic answer to a clear 'where is <person>' question, or None (not such a question)."""
    m = jh_where_match(hass, text)
    if not m:
        return None
    target, exact, home_q = m
    spoken = spoken or _jh_latin_reply(hass)   # jh-live-answers
    if target == "self":
        pst = _jh_self(hass, user_id)
        if pst is None:
            return ("I can't tell who is asking without a login linked to a person, so I have no location for you."
                    if not user_id else "Your login is not linked to a person in Home Assistant, so I have no location for you.")
        info = jh_person_location(hass, pst.entity_id, now=now, spoken=spoken, lang=_jh_answer_lang(hass))   # jh-place-script
        _jh_fn = jh_where_text_de if _jh_reply_lang(hass) == "de" else jh_where_text   # jh-conv-lang
        return _jh_with_h(_jh_fn(hass, info, exact=exact, you=True, home_question=home_q), honorific)
    pst = hass.states.get(target)
    if pst is None:
        return None
    if not await _jh_may_see(hass, user_id, pst):
        # no honorific: the asker is not the owner, and "sir" would guess their gender too
        return _jh_refusal(hass)   # Jarvis Home patch (2026-11-15): jh-location-sharing
    you = pst.attributes.get("user_id") == user_id and bool(user_id)
    info = jh_person_location(hass, target, now=now, spoken=spoken, lang=_jh_answer_lang(hass))   # jh-place-script
    _jh_fn = jh_where_text_de if _jh_reply_lang(hass) == "de" else jh_where_text   # jh-conv-lang
    return _jh_with_h(_jh_fn(hass, info, exact=exact, you=you, home_question=home_q), honorific)


_JH_BATT = _jh_re.compile(r"\b(?:battery|batteries|charged|charging|charge|batterij|opgeladen|akku|aufgeladen|geladen"
                          r"|batterie|batería|bateria|batteria|chargé|chargée|cargado|cargada)\b|แบต", _jh_re.I)   # jh-r13-app-sensors
_JH_SELF_WORDS = _jh_re.compile(r"\b(?:my|mine|i|me|mijn|mein|meine|meinem|meines)\b", _jh_re.I)
_JH_PRONOUN_WORDS = _jh_re.compile(r"\b(?:her|his|their|haar|zijn|ihr|ihre|ihrem|sein|seine|seinem)\b", _jh_re.I)
_JH_PARTS = _jh_re.compile(r"(?<=[?.!])\s+|\s*[,;]?\s+(?:and|en|und)\s+(?=(?:is|are|how|what|does|has|hoe|wat|ist|wie|heeft|hat)\b)", _jh_re.I)
_JH_BSTATE = {"charging": ("charging", "lädt"), "discharging": ("not charging", "lädt nicht"),
              "not_charging": ("not charging", "lädt nicht"), "full": ("full", "voll")}


def _jh_named(hass, text):
    """The person entity named in text (the longest name that fits), or None."""
    low = (text or "").lower()
    best = None
    for name, eid in _jh_names(hass).items():
        if name and _jh_re.search(rf"(?:^|\W){_jh_re.escape(name)}(?:'s|s)?(?:\W|$)", low) and (best is None or len(name) > len(best[0])):
            best = (name, eid)
    return best[1] if best else None


def jh_battery_report(hass, user_id):
    """[(device name, level text or None, state key or None)] for every app signed in with this login that has a battery
    sensor. Read live from the mobile_app config entries, the device and entity registries and the states."""
    try:
        from .optional_pages import apps_by_user
        apps = apps_by_user(hass).get(user_id or "", [])
    except Exception:
        apps = []
    out = []
    for d in apps:
        ents = d.get("entities") or []
        keys = d.get("keys") or {}   # jh-r7-sensor-keys (review round 7): the app's own key, the same in every language
        lvl = next((e for e in ents if keys.get(e) == "battery_level"), None) or next((e for e in ents if e.endswith("_battery_level")), None)
        stt = next((e for e in ents if keys.get(e) == "battery_state"), None) or next((e for e in ents if e.endswith("_battery_state")), None)
        if not lvl and not stt:
            continue
        ls = hass.states.get(lvl) if lvl else None
        ss = hass.states.get(stt) if stt else None
        level = ls.state if ls is not None and ls.state not in ("unknown", "unavailable", "") else None
        state = ss.state if ss is not None and ss.state not in ("unknown", "unavailable", "") else None
        out.append((d.get("name") or "?", level, state))
    return out


def _jh_battery_text(owner, devices, de=False):
    def one(name, level, state):
        st = _JH_BSTATE.get(str(state or "").lower())
        if level is None:
            return (f"{name} meldet keinen Akkustand" if de else f"{name} reports no battery level")
        lv = f"{level}%" if str(level).replace(".", "", 1).isdigit() else str(level)
        if de:
            return f"{name} hat {lv}" + (f" und {st[1]}" if st else "")
        return f"{name} is at {lv}" + (f" and {st[0]}" if st else "")
    parts = [one(*d) for d in devices]
    lead = ("" if owner is None else (f"{owner}: " if de else f"{owner}'s "))
    if de:
        return (f"{lead}" if owner else "Dein ") + "; ".join(parts) + "."
    return (lead if owner else "Your ") + ("; ".join(parts) if len(parts) == 1 else "devices: " + "; ".join(parts)) + "."


async def jh_battery_answer(hass, text, user_id="", honorific="sir", hint=None):
    """(answer or None, person entity it was about). None: not a battery question JARVIS can answer at once (the AI then)."""
    if not _JH_BATT.search(text or ""):
        return None, hint
    de = _jh_reply_lang(hass) == "de"
    target = _jh_named(hass, text)
    if target is None and hint and _JH_PRONOUN_WORDS.search(text):
        target = hint
    if target is None and _JH_SELF_WORDS.search(text):
        if not user_id:
            return ("Ich weiß ohne Anmeldung nicht, wer fragt." if de else
                    "I can't tell who is asking without a login, so I don't know which phone is yours."), None
        devs = jh_battery_report(hass, user_id)
        if not devs:
            return _jh_with_h(("Bei Home Assistant ist kein Telefon oder Tablet mit Ihrer Anmeldung angemeldet." if de else
                               "No phone or tablet is signed in to Home Assistant with your login."), honorific), None
        return _jh_with_h(_jh_battery_text(None, devs, de), honorific), None
    if target is None:
        return None, hint
    pst = hass.states.get(target)
    if pst is None:
        return None, hint
    if not await _jh_may_see(hass, user_id, pst):
        return (("Es tut mir leid, über das Telefon einer Person spreche ich nur mit ihr selbst oder einem Administrator." if de else
                 "I'm sorry, I only tell that person or an administrator about their phone.")), target
    name = _jh_person_name(pst).split()[0] if _jh_person_name(pst).split() else _jh_person_name(pst)
    mine = bool(user_id) and pst.attributes.get("user_id") == user_id
    devs = jh_battery_report(hass, pst.attributes.get("user_id") or "")
    if not devs:
        return _jh_with_h((f"{name} hat kein Telefon oder Tablet mit Akkuanzeige bei Home Assistant angemeldet." if de else
                           f"{name} has no phone or tablet that reports its battery to Home Assistant."), honorific), target
    return _jh_with_h(_jh_battery_text(None if mine else name, devs, de), honorific), target


_JH_HIST_Q = _jh_re.compile(r"^(?:(?:hey|ok)\s+)?(?:jarvis[\s,]+)?where\s+(?:was|were)\s+(.+?)\s+"
                            r"(yesterday|today|this morning|last night|(?:yesterday\s+)?at\s+\d{1,2}(?::\d{2})?(?:\s*[ap]\.?m\.?)?)\s*\??$", _jh_re.I)


def _jh_hist_window(when, now):
    """(start, end, at) in local time for 'yesterday', 'today', 'this morning', 'last night', '[yesterday] at 15:00'."""
    import datetime as _d
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    w = when.lower()
    m = _jh_re.search(r"at\s+(\d{1,2})(?::(\d{2}))?\s*([ap])?", w)
    if m:
        hh, mm = int(m.group(1)), int(m.group(2) or 0)
        if m.group(3) == "p" and hh < 12:
            hh += 12
        elif m.group(3) == "a" and hh == 12:   # review 2026-11-15: 12am is midnight
            hh = 0
        base = day - _d.timedelta(days=1) if w.startswith("yesterday") else day
        at = base.replace(hour=hh % 24, minute=mm)
        if at > now:
            at -= _d.timedelta(days=1)
        return None, None, at
    if w == "yesterday":
        return day - _d.timedelta(days=1), day, None
    if w == "this morning":
        start = day.replace(hour=5)
        return (start if now > start else day), min(now, day.replace(hour=12)), None
    if w == "last night":
        return day - _d.timedelta(hours=4), day.replace(hour=7), None
    return day, now, None


def _jh_hist_states(hass, eid, start, end):
    from homeassistant.components.recorder import history
    out = history.state_changes_during_period(hass, start, end, eid, no_attributes=False, include_start_time_state=True)
    return out.get(eid, []) if isinstance(out, dict) else []


async def jh_history_answer(hass, text, user_id="", honorific="sir", now=None):
    """'Where was Robin yesterday / at 15:00?' from the recorder: zones with times, and the place names of the phone's
    geocoded location while away. None: not such a question (or no recorder)."""
    m = _JH_HIST_Q.match((text or "").strip())
    if not m:
        return None
    who, when = m.group(1).strip(), m.group(2)
    if who.lower() in ("i", "me"):
        pst = _jh_self(hass, user_id)
        if pst is None:
            return None
    else:
        eid = _jh_named(hass, who)
        pst = hass.states.get(eid) if eid else None
        if pst is None:
            return None
        if not await _jh_may_see(hass, user_id, pst):
            return _jh_refusal(hass)
    from homeassistant.util import dt as dt_util
    try:
        from homeassistant.components.recorder import get_instance
        inst = get_instance(hass)
    except Exception:
        return None
    now = now or dt_util.now()
    start, end, at = _jh_hist_window(when, now)
    you = bool(user_id) and pst.attributes.get("user_id") == user_id
    name = "You" if you else (_jh_person_name(pst).split()[0] if _jh_person_name(pst).split() else _jh_person_name(pst))
    geo = _jh_best_geocoded(hass, pst)
    lang = _jh_answer_lang(hass)
    hm = lambda d: dt_util.as_local(d).strftime("%H:%M")

    def zone(st):
        s = str(st.state or "")
        if s.strip().lower() in _JH_DEAD:   # jh-r17-hist (step 159, review round 17 #6): no location, never "at unknown"
            return ""
        return "home" if s == "home" else ("away" if s in ("not_home", "away") else s)

    def place_during(rows, a, b):
        # jh-r19-hist (step 161, review round 19 #4): the place in effect when the span starts is the LAST geocoded row at or
        # before its start (not the window's first row), then every place reported inside the span
        names = []
        for g in [g for g in rows if g.last_changed <= a][-1:] + [g for g in rows if a < g.last_changed <= b]:
            p = _jh_place(g, False, lang)
            if p and p not in names:
                names.append(p)
        return names[-3:]

    if at is not None:
        a0 = at - __import__("datetime").timedelta(hours=12)
        rows = await inst.async_add_executor_job(_jh_hist_states, hass, pst.entity_id, a0, at)
        if not rows:
            return _jh_with_h(f"I have no recorded location for {'you' if you else name} at {hm(at)}.", honorific)
        z = zone(rows[-1])
        if not z:   # jh-r17-hist: the state then was unknown or unavailable
            return _jh_with_h(f"I have no recorded location for {'you' if you else name} at {hm(at)}.", honorific)
        grows = await inst.async_add_executor_job(_jh_hist_states, hass, geo.entity_id, a0, at) if geo is not None else []
        _jh_at = place_during(grows, at, at)   # jh-r19-hist: the place at the asked time, the last geocoded row at or before it
        where = "at home" if z == "home" else (f"away ({', '.join(_jh_at)})" if z == "away" and _jh_at
                                               else ("away from home" if z == "away" else f"at {z}"))
        return _jh_with_h(f"At {hm(at)} {'you were' if you else name + ' was'} {where}.", honorific)
    rows = await inst.async_add_executor_job(_jh_hist_states, hass, pst.entity_id, start, end)
    if not rows:
        return _jh_with_h(f"I have no recorded location for {'you' if you else name} {when}.", honorific)
    grows = await inst.async_add_executor_job(_jh_hist_states, hass, geo.entity_id, start, end) if geo is not None else []
    segs = []
    for i, st in enumerate(rows):
        a = max(st.last_changed, start)
        b = rows[i + 1].last_changed if i + 1 < len(rows) else end
        z = zone(st)
        if segs and segs[-1][2] == z:
            segs[-1] = (segs[-1][0], b, z)
        else:
            segs.append((a, b, z))
    if not any(z for _a, _b, z in segs):   # jh-r17-hist: nothing but unknown / unavailable in that window
        return _jh_with_h(f"I have no recorded location for {'you' if you else name} {when}.", honorific)
    bits = []
    # jh-r19-hist (step 161, review round 19 #6): a busy day is never cut off silently: the first seven changes, then how many
    # are not listed and between which times, then the last one (where the day ended)
    _jh_cut = len(segs) - 8 if len(segs) > 8 else 0
    for _jh_seg in (segs[:7] + [None] + segs[-1:]) if _jh_cut else segs:
        if _jh_seg is None:
            bits.append(f"{_jh_cut} more change{'s' if _jh_cut != 1 else ''} between {hm(segs[7][0])} and {hm(segs[-1][0])}"
                        " (not listed)")
            continue
        a, b, z = _jh_seg
        span = f"{hm(a)} to {hm(b)}" if b < end else f"from {hm(a)}"
        if z == "home":
            bits.append(f"home {span}")
        elif z == "away":
            places = place_during(grows, a, b)
            bits.append(f"away {span}" + (f" ({', '.join(places)})" if places else ""))
        elif not z:   # jh-r17-hist: merged neighbouring unknown / unavailable rows
            bits.append(f"location unknown {span}")
        else:
            bits.append(f"at {z} {span}")
    lead = when[0].upper() + when[1:]
    subj = "you were" if you else f"{name} was"
    return _jh_with_h(f"{lead} {subj}: " + "; ".join(bits) + ".", honorific)


async def jh_where_and_battery(hass, text, user_id="", honorific="sir", spoken=False, now=None):
    """The where-is answer, the phone battery answer, or both for "Where is Robin? Is her phone charged?". None when any
    part cannot be answered at once (then the AI answers the whole message)."""
    try:   # review 2026-11-15: answered in the language the request is written in (en, de)
        from .local_engine import jh_text_lang_sure as _jh_tls
        _JH_ASK_LANG.set(_jh_tls(text))
    except Exception:
        pass
    try:   # jh-where-history (step 132): where was X yesterday / at 15:00, from the recorder
        _jh_h = await jh_history_answer(hass, text, user_id, honorific)
    except Exception:
        _jh_h = None
    if _jh_h:
        return _jh_h
    if not _JH_BATT.search(text or ""):
        return await jh_where_answer(hass, text, user_id, honorific, spoken=spoken, now=now)
    parts = [p.strip() for p in _JH_PARTS.split(text or "") if p and p.strip()]
    out, hint = [], None
    for part in parts:
        if _JH_BATT.search(part):
            ans, hint = await jh_battery_answer(hass, part, user_id, honorific, hint)
        else:
            ans = await jh_where_answer(hass, part, user_id, honorific, spoken=spoken, now=now)
            hint = _jh_named(hass, part) or hint
        if not ans:
            return None
        out.append(ans)
    return " ".join(out) if out else None


_JH_LOC_WORDS = _jh_re.compile(r"\b(?:where|whereabouts|location|located|address|home|away|far|distance|map|travel\w*|trip|"
                               r"country|city|abroad|back|arriv\w*|left|leave|gone)\b", _jh_re.I)


async def jh_people_facts(hass, text, user_id="", now=None) -> str:
    """Live facts about people for the AI on this message: the pronoun of each person, and, when the message is about
    a person or about places, where each person is (only the ones this login may see; no street address)."""
    try:
        people = list(hass.states.async_all("person"))
    except Exception:
        return ""
    if not people:
        return ""
    low = (text or "").lower()
    names = _jh_names(hass)
    about = bool(_JH_LOC_WORDS.search(low)) or any(_jh_re.search(rf"\b{_jh_re.escape(n)}\b", low) for n in names)
    pr = jh_pronouns(hass)
    lines = []
    for st in people:
        name = _jh_person_name(st)
        p = "/".join(JH_PRONOUNS[pr.get(st.entity_id, "they")][:2])
        p = p if pr.get(st.entity_id, "they") != "they" else "no pronoun set: use the name or they/them"
        me = " (the person asking)" if user_id and st.attributes.get("user_id") == user_id else ""
        line = f"- {name}{me} ({p})"
        if about:
            if await _jh_may_see(hass, user_id, st):
                info = jh_person_location(hass, st.entity_id, now=now)
                line += ": " + jh_where_text(hass, info)
            else:
                line += (": where this person is is shared only with logged-in members of this household"   # jh-location-sharing
                         if jh_location_sharing(hass) == "household"
                         else ": where this person is is shared only with that person or an administrator")
        lines.append(line)
    rules = "Refer to each person by name or the pronoun given here; never guess anyone's gender."
    if about:
        rules += (" These locations come from the person's phone and are current as stated; name the town, region and "
                  "country, never a street address unless the exact address is asked for, and never put anyone's "
                  "location in an announcement or broadcast.")
    return "## People (live, for this message)\n" + "\n".join(lines) + "\n" + rules



# Jarvis Home patch (2026-11-15): jh-location-sharing - who may hear where someone is. household (default): any login
# linked to a person in Home Assistant; private: only administrators and the person themselves. Voice with no login and
# a login linked to no person never get anyone else's location.
JH_SHARING = ("household", "private")


def jh_clean_sharing(value) -> str:
    """The location_sharing setting: private (also when not set) or household. A ValueError carries a message for the panel.
    jh-private-by-default (2026-11-15): a fresh install, or one that never set it, shares a location only with that person
    and administrators; an administrator can pick household in Settings."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return "private"
    v = value.strip().lower() if isinstance(value, str) else None
    if v not in JH_SHARING:
        raise ValueError("Location sharing must be household or private")
    return v


def jh_location_sharing(hass) -> str:
    """The saved location_sharing setting; a broken value counts as private (the stricter one)."""
    try:
        from . import jarvis_config
        return jh_clean_sharing(jarvis_config.runtime_get(hass, _jh_entry(hass), "location_sharing", None))
    except ValueError:
        return "private"
    except Exception:   # unreadable: the stricter one
        return "private"


def _jh_refusal(hass) -> str:
    if _jh_reply_lang(hass) == "de":   # jh-conv-lang
        return ("Es tut mir leid, ich sage nur angemeldeten Personen dieses Haushalts, wo jemand ist."
                if jh_location_sharing(hass) == "household" else
                "Es tut mir leid, ich sage nur dieser Person selbst oder einem Administrator, wo jemand ist.")
    if jh_location_sharing(hass) == "household":
        return "I'm sorry, I only share where someone is with people of this household who are logged in."
    return "I'm sorry, I only share where someone is with that person or an administrator."



# Jarvis Home patch (2026-11-14): jh-person-address - how JARVIS addresses each person (sir, madam, their name, nothing)
JH_ADDRESS = ("sir", "madam", "name", "none")


def jh_clean_address(value) -> dict:
    """Check the person_address setting: {person entity: sir | madam | name | none}. Returns the clean dict without the
    default (name); a ValueError carries a message for the panel."""
    if value is None or value == "":
        return {}
    if isinstance(value, str):
        try:
            value = _jh_json.loads(value)
        except ValueError:
            raise ValueError('Forms of address must be a JSON object like {"person.sam": "sir"}') from None
    if not isinstance(value, dict):
        raise ValueError("Forms of address must be an object: person entity -> sir, madam, name or none")
    if len(value) > 200:
        raise ValueError("At most 200 people")
    out = {}
    for key, val in value.items():
        k = key.strip() if isinstance(key, str) else ""
        if not _jh_re.fullmatch(r"person\.[a-z0-9_]+", k):
            raise ValueError(f"{key!r} is not a person entity (person.<name>)")
        a = val.strip().lower() if isinstance(val, str) else ""
        if a not in JH_ADDRESS:
            raise ValueError(f"{k}: the form of address must be sir, madam, name or none")
        if a != "name":
            out[k] = a
    return out


def jh_address_setting(hass) -> dict:
    """Every person in Home Assistant now -> sir, madam, name (the default) or none."""
    try:
        from . import jarvis_config
        saved = jh_clean_address(jarvis_config.runtime_get(hass, _jh_entry(hass), "person_address", None))
    except Exception:
        saved = {}
    try:
        return {st.entity_id: saved.get(st.entity_id, "name") for st in hass.states.async_all("person")}
    except Exception:
        return {}


def jh_address_for(hass, user_id, fallback):
    """The words JARVIS addresses the asker with: "sir", "madam", the first name or "" (none). A login linked to no
    person, and voice with no login, keep the household's own Form of address (fallback)."""
    pst = _jh_self(hass, user_id)
    if pst is None:
        return fallback
    a = jh_address_setting(hass).get(pst.entity_id, "name")
    if a == "none":
        return ""
    if a == "name":
        return _jh_person_name(pst).split()[0] if _jh_person_name(pst).split() else fallback
    return a


def jh_persona_address(text: str, address: str) -> str:
    """Put the asker's form of address into the system prompt; with none, tell the model to use none."""
    if address:
        return text.replace("{honorific}", address)
    text = _jh_re.sub(r"Address the (?:owner|user|person)[^\n.]*\{honorific\}\.?", "Do not use a form of address (no sir, madam or name).", text)
    return _jh_re.sub(r"[ ,]*\{honorific\}", "", text)


def jh_swap_address(text: str, address: str, others=("sir",)) -> str:
    """A reply addresses the asker with their own form of address: ", sir" / "Sir, " (or the household's global form,
    when it is no longer this person's) becomes theirs, or goes when they want none. Other uses of the word stay."""
    if not text or not isinstance(text, str):
        return text
    words = [w for w in dict.fromkeys(o.strip() for o in others if o and o.strip()) if w.lower() != (address or "").lower()]
    for w in words:
        e = _jh_re.escape(w)
        if address:
            text = _jh_re.sub(rf",(\s*){e}\b", lambda m: "," + m.group(1) + address, text, flags=_jh_re.I)
            text = _jh_re.sub(rf"^(\W*){e}(\s*[,.!])", lambda m: m.group(1) + address[:1].upper() + address[1:] + m.group(2),
                              text, flags=_jh_re.I | _jh_re.M)
        else:
            text = _jh_re.sub(rf",\s*{e}\b", "", text, flags=_jh_re.I)          # "Done, sir." -> "Done."
            text = _jh_re.sub(rf"^(\W*){e}\s*,\s*(\w)", lambda m: m.group(1) + m.group(2).upper(), text,
                              flags=_jh_re.I | _jh_re.M)                          # "Sir, the lamp" -> "The lamp"
    return text
