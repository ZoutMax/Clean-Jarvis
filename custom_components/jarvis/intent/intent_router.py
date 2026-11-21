"""Local intent routing for JARVIS.

``LocalIntentRouter`` matches a spoken/typed phrase against a small table of
local command patterns (no cloud round-trip), resolves pronoun context ("turn it
off") to the active entity in a room, executes the change, and supports a short
interactive feedback window so an actionable announcement can be confirmed by
voice without a fresh wake word.

Module-level code is stdlib-only so the pure matching helpers
(``match_intent`` / ``is_affirmative``) load and test without Home Assistant.
Anything touching hass (entity-area lookup, service calls, the feedback timer)
is imported lazily inside the methods that need it.
"""
from __future__ import annotations

import logging
import re

_LOGGER = logging.getLogger(__name__)

# Fired when an actionable announcement opens a confirmation window; the voice
# satellite layer listens for this to start a short, wake-word-free capture.
EVENT_FEEDBACK_WINDOW = "jarvis_feedback_window"
FEEDBACK_TIMEOUT_S = 10.0

# Intent table — ordered most-specific first so "turn off the lights" matches
# the light intent rather than the pronoun ("it") intent.
_INTENT_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("secure_area", (
        r"\bsecure\b",
        r"\bclose\b.*\bgarage\b",
        r"\block\s+(up|down|the|it)\b",
        r"\barm\b.*\b(garage|house|home|alarm)\b",
    )),
    ("lights_off", (
        r"\b(turn|switch|shut)\s+off\b.*\blight",
        r"\blights?\s+off\b",
        r"\bkill\s+the\s+lights?\b",
    )),
    ("lights_on", (
        r"\b(turn|switch)\s+on\b.*\blight",
        r"\blights?\s+on\b",
    )),
    ("context_off", (
        r"\bturn\s+it\s+off\b",
        r"\bswitch\s+it\s+off\b",
        r"\bshut\s+it\s+off\b",
        r"\bturn\s+that\s+off\b",
        r"\bpause\s+it\b",
    )),
    ("context_close", (
        r"\bclose\s+it\b",
        r"\bshut\s+it\b",
    )),
)

_AFFIRMATIVE_PATTERNS: tuple[str, ...] = (
    r"\b(yes|yeah|yep|yup|sure|ok|okay|affirmative|confirm(ed)?|proceed|go ahead|do it)\b",
    r"\b(close|shut|secure)\s+it\b",
)

# jh-r21-gate (step 163, review round 21 #1): a standalone load (upstream's unit tests, no package) cannot reach the kit's shared
# rule in local_engine; these are the same words and negations (a kit test keeps them equal). In Home Assistant the shared
# jh_is_yes / jh_free_text_gate are used.
_JH_YES_STANDALONE = frozenset(("yes", "yes sir", "yes do", "yeah", "yep", "sure", "ok", "okay", "do it", "go ahead", "please do",
                                "ja", "ja hoor", "jawel", "doe maar", "yes please", "affirmative", "please", "confirm", "confirmed",
                                "proceed",
                                # jh-r23-yes (step 165, review round 23 #14): the same words as local_engine.JH_YES_WORDS
                                "yup", "yes go ahead", "yeah go ahead", "yep go ahead", "ok go ahead", "okay go ahead",
                                "sure go ahead", "go ahead please", "yes do it", "yeah do it", "yep do it", "yup do it", "ok do it",
                                "okay do it", "sure do it", "do it please", "sure thing", "yes proceed", "absolutely", "of course",
                                "certainly", "ja bitte", "ja graag"))
_JH_NEG_STANDALONE = re.compile(r"(?<![\w'])(?:no|not|nope|never|nah|cancel|stop|wait|abort|negative|don'?t|do not|doesn'?t"
                                r"|didn'?t|isn'?t|won'?t|can'?t|cannot|shouldn'?t|wouldn'?t|nein|nicht|nie|kein\w*|nee|niet|nooit"
                                r"|geen|non|pas|jamais|nunca|não|nao|nada|nessuno|mai)(?![\w'])|n't(?![\w'])|ไม่|อย่า|ห้าม")


def _jh_gate(hass, phrase) -> str:
    """jh-r21-gate: the kit's shared free-text gate; fails closed when it cannot be reached."""
    try:
        from ..local_engine import jh_free_text_gate
    except Exception:  # noqa: BLE001
        return "gate unavailable"
    try:
        return jh_free_text_gate(phrase, hass, verbs=r"(?:kill|secure)\b")
    except Exception:  # noqa: BLE001
        return "error"


# jh-r22-router (step 164, review round 22 #1): the router never acts on PART of a sentence. Once the gate let a phrase through,
# it is acted on only when the WHOLE order is one of these exact phrases (for the room the request came from, nothing left
# over), or else the whole phrase goes to local_engine.try_local, which matches whole orders only (fullmatch) with its own
# guards and acts only on the device or room it names. match_intent's search patterns above no longer act (upstream's unit
# tests still read them). "and", "but", "except", "keep", "leave", "apart from" and the like: nothing is switched.
# jh-r23-router (step 165, review round 23 #1, #15): a plural or "all" only ("turn off the light" is one light: it goes whole to
# try_local, which takes the one light of the request's room or asks); "lights out" and "shut the lights off" are lights off
_JH_ROUTER_LIGHTS = r"(?:all\s+(?:of\s+)?(?:the\s+)?lights?|(?:the\s+)?lights)"
_JH_WHOLE_INTENTS = (
    ("lights_off", r"(?:(?:turn|switch|shut)\s+(?:off|out)\s+" + _JH_ROUTER_LIGHTS
                   + r"|(?:turn|switch|shut)\s+" + _JH_ROUTER_LIGHTS + r"\s+(?:off|out)|" + _JH_ROUTER_LIGHTS + r"\s+(?:off|out)"
                   r"|kill\s+" + _JH_ROUTER_LIGHTS + r")"),
    ("lights_on", r"(?:(?:turn|switch)\s+on\s+" + _JH_ROUTER_LIGHTS + r"|(?:turn|switch)\s+" + _JH_ROUTER_LIGHTS + r"\s+on"
                  r"|" + _JH_ROUTER_LIGHTS + r"\s+on)"),
    ("secure_area", r"(?:secure|lock\s+up|lock\s+it\s+up|lock\s+everything\s+up)"
                    r"(?:\s+(?:the\s+)?(?:house|home|area|room|place|everything|here))?"),
    ("context_off", r"(?:turn|switch|shut)\s+(?:it|that)\s+off"),
    ("context_pause", r"pause\s+(?:it|that)"),
    ("context_close", r"(?:close|shut)\s+(?:it|that)"),
)
_JH_LEFTOVER = re.compile(r"\b(?:and|but|except|excluding|keep|keeping|leave|leaving|apart|besides|other\s+than|without|then"
                          r"|also|plus|or|unless|instead|only|too)\b|[,;:&+]")


def _jh_leftover(text) -> bool:
    """jh-r22-router: a second part, an exception or a 'keep / leave' in the order. Used only to REFUSE, never to pick an action."""
    return bool(_JH_LEFTOVER.search(str(text or "").lower()))


def _jh_whole_intent(cmd):
    """jh-r22-router: the router's own intent when the WHOLE order is one of its exact phrases (re.fullmatch), else None."""
    for name, pat in _JH_WHOLE_INTENTS:
        if re.fullmatch(pat, str(cmd or "")):
            return name
    return None


# jh-r23-router (step 165, review round 23 #3, #13): a leading wake word or courtesy ("Jarvis,", "Hey Jarvis,", "Okay,", "Please,")
# and a trailing ", please" / ", thanks" / ", now" / ", sir" / ", Jarvis" are no second part of the order. Only those are taken
# off (repeatedly); a comma inside the order still makes it more than one order.
_JH_COURTESY_LEAD = re.compile(r"^\s*(?:(?:(?:hey|hi|hello|ok|okay)\s+)?jarvis\b|please\b|ok(?:ay)?\b|alright\b|all\s+right\b)"
                               r"\s*[,!.:;]?\s*", re.I)
_JH_COURTESY_TAIL = re.compile(r"\s*,\s*(?:please|thanks|thank\s+you|now|sir|ma'?am|jarvis)\s*[.!]*\s*$", re.I)


def _jh_courtesy_strip(phrase) -> str:
    """jh-r23-router: the order without a leading wake word / courtesy and a trailing courtesy after a comma."""
    t, prev = str(phrase or "").strip(), None
    while t != prev:
        prev = t
        t = _JH_COURTESY_TAIL.sub("", _JH_COURTESY_LEAD.sub("", t, count=1)).strip()
    return t


_JH_PRONOUN = re.compile(r"\b(?:it|that|this|them|those|these)\b", re.I)


def _jh_fresh_order(hass, phrase) -> bool:
    """jh-r23-window (step 165, review round 23 #14): an answer in the confirmation window that is a whole new order of its own
    (no "it" / "that", no negation, the gate lets it through): it is not the announced action, so the window is cancelled and
    the caller routes it as a fresh order (upstream did the same). Fails closed (False: the window just cancels)."""
    try:
        core = _jh_courtesy_strip(phrase)
        low = core.lower()
        if not core or _JH_PRONOUN.search(low) or _JH_NEG_STANDALONE.search(low):
            return False
        return _jh_gate(hass, core) == ""
    except Exception:  # noqa: BLE001
        return False


_ACTIVE_MEDIA_STATES = {"playing"}
_ACTIVE_LIGHT_STATES = {"on"}
_ACTIVE_GENERIC_STATES = {"on", "open"}


def match_intent(phrase: str) -> dict | None:
    """Return {"intent": name, "raw": phrase} for the first matching pattern, or
    None if the phrase doesn't map to a known local command. Pure function."""
    if not phrase:
        return None
    text = phrase.lower().strip()
    for name, patterns in _INTENT_PATTERNS:
        if any(re.search(pat, text) for pat in patterns):
            return {"intent": name, "raw": phrase}
    return None


def is_affirmative(phrase: str) -> bool:
    """True if the phrase reads as a yes/confirmation. Pure function.
    jh-r21-gate (step 163, review round 21 #1): the WHOLE phrase must be one of the shared yes words, never with a negation or
    a tail ("no, don't do it", "I'm not sure", "not okay", "cancel, do not proceed" are no yes; a substring is no answer)."""
    if not phrase:
        return False
    try:
        from ..local_engine import jh_is_yes
    except Exception:  # noqa: BLE001 - a standalone load: the same words, kept equal by a kit test
        text = " ".join(re.sub(r"[^\w\s']+", " ", str(phrase).lower().replace("\u2019", "'")).split())
        return (bool(text) and "?" not in str(phrase) and not _JH_NEG_STANDALONE.search(text)   # jh-r25-yes: "yes?" is no yes
                and text in _JH_YES_STANDALONE)
    return jh_is_yes(phrase)


class LocalIntentRouter:
    """Route phrases to local actions, with room-context pronoun resolution and
    a short voice-confirmation window."""

    def __init__(self, hass, *, ledger=None, mutex=None) -> None:
        self.hass = hass
        self.ledger = ledger  # optional StateLedger (duck-typed); write-ahead for high-stakes
        self.mutex = mutex    # optional EntityLockRegistry (duck-typed); concurrency control
        self._pending_feedback: dict | None = None
        self._feedback_cancel = None

    # ── Area helper (lazy import keeps the module HA-free at import time) ──
    def _area_of(self, entity_id: str):
        from .. import audio_routing  # lazy
        return audio_routing.entity_area(self.hass, entity_id)

    # ── Context resolution ────────────────────────────────────────────────
    def resolve_active_entity(
        self, area_id: str,
        domains: tuple[str, ...] = ("media_player", "light"),
        *, area_of=None,
    ) -> tuple[str | None, str | None]:
        """Find the entity a pronoun ("it") most likely refers to in an area:
        a playing media_player first, then an 'on' light, then any other 'on'/
        'open' entity in the requested domains. Returns (entity_id, domain)."""
        resolve_area = area_of or self._area_of
        for domain in domains:
            _jh_hits = []   # jh-r23-router (step 165, review round 23 #2): every active one; two of a kind are no answer
            for st in self.hass.states.async_all(domain):
                try:
                    if resolve_area(st.entity_id) != area_id:
                        continue
                except Exception:  # noqa: BLE001
                    continue
                state = str(st.state).lower()
                if domain == "media_player" and state in _ACTIVE_MEDIA_STATES:
                    _jh_hits.append((st.entity_id, domain))
                elif domain == "light" and state in _ACTIVE_LIGHT_STATES:
                    _jh_hits.append((st.entity_id, domain))
                elif domain not in ("media_player", "light") and state in _ACTIVE_GENERIC_STATES:
                    _jh_hits.append((st.entity_id, domain))
            if len(_jh_hits) == 1:
                return _jh_hits[0]
            if _jh_hits:
                return None, None
        return None, None

    # ── Execution ─────────────────────────────────────────────────────────
    async def _call_domain_in_area(
        self, domain: str, service: str, area_id: str,
        *, high_stakes: bool = False, desired_state: str | None = None,
        priority: int | None = None,
    ) -> list[str]:
        """Call a service on the entities of `domain` in `area_id`. Acquires a
        per-entity concurrency lock first (skipping entities held at equal-or-
        higher priority), writes a recovery-ledger intent before high-stakes
        actions, and releases the locks after. Returns the entity_ids acted on."""
        candidates: list[str] = []
        for st in self.hass.states.async_all(domain):
            try:
                if self._area_of(st.entity_id) != area_id:
                    continue
            except Exception:  # noqa: BLE001
                continue
            candidates.append(st.entity_id)
        # jh-r25-group (step 167, review round 25): THE group function decides which of the room's devices a group order takes
        # (no setting, the hidden-twin rule, no manual heater, never a barrier or security device, every lock in a lock
        # order); the ones it left out are kept for the result. Fails closed.
        self._jh_left = []
        self._jh_unc = {}   # jh-r26-bulk (step 168): the targets sent that did not confirm
        try:
            from .. import local_engine as _jh_le25
            _jh_g25 = _jh_le25.jh_group_targets(self.hass, domain, {"close_cover": "close", "open_cover": "open"}.get(
                service, service), None, None, "", candidates=candidates)
            if _jh_g25.error:
                return []
            self._jh_left = [n for _e, n, _w in _jh_g25.left_out]
            candidates = list(_jh_g25.ids)
        except Exception:  # noqa: BLE001
            return []
        if not candidates:
            return []

        # Concurrency control: only act on entities we can lock at this priority.
        tokens: dict = {}
        if self.mutex is not None and priority is not None:
            targets: list[str] = []
            for eid in candidates:
                token = self.mutex.try_acquire(eid, priority)
                if token is not None:
                    tokens[eid] = token
                    targets.append(eid)
                else:
                    _LOGGER.info("intent: %s busy (higher-priority lock) — skipping", eid)
            if not targets:
                return []
        else:
            targets = candidates

        # Write-ahead: durably record intent BEFORE issuing a high-stakes call,
        # keyed per entity so each target's recovery-ledger entry is marked
        # complete only when that entity actually actuated.
        txn_by_eid: dict[str, str] = {}
        if high_stakes and self.ledger is not None and desired_state is not None:
            for eid in targets:
                try:
                    from functools import partial as _jh_partial   # Jarvis Home patch (2026-11-15): jh-loop-io
                    txn_by_eid[eid] = await self.hass.async_add_executor_job(_jh_partial(
                        self.ledger.record_intent, eid, desired_state, action=f"{domain}.{service}"
                    ))
                except Exception:  # noqa: BLE001
                    _LOGGER.exception("ledger record_intent failed for %s", eid)

        # MCU Phase H (H10): route each target through the universal actuator
        # seam (one canonical per-entity request) instead of a single direct
        # multi-entity hass.services call — so intent-router actuations are
        # planned, event-published and journaled like every other actuation.
        # Same net effect on the home; now observable, and the recovery ledger is
        # marked complete per entity that genuinely actuated.
        acted: list[str] = []
        # jh-r26-bulk (step 168, review round 26 #2): the group's targets go through THE group send of local_engine
        # (jh_group_act: each target through the universal actuator seam, then all read back together), never a loop of its
        # own. A target that was sent but does not show the end state is reported in self._jh_unc (the result names it).
        self._jh_unc = {}
        try:
            _jh_c26, _jh_u26, _jh_f26 = await _jh_le25.jh_group_act(
                self.hass, _jh_g25, {"close_cover": "close", "open_cover": "open"}.get(service, service), targets)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("intent: %s.%s failed for %s", domain, service, targets)
            _jh_c26, _jh_u26, _jh_f26 = [], {}, {}
        self._jh_unc = {e: s for e, s in dict(_jh_u26).items()}
        for eid in targets:
            if eid in _jh_c26 or eid in _jh_u26:
                acted.append(eid)
                txn = txn_by_eid.get(eid)
                if txn is not None:
                    try:
                        await self.hass.async_add_executor_job(self.ledger.mark_complete, txn)   # jh-loop-io
                    except Exception:  # noqa: BLE001
                        pass

        self._release_all(tokens)
        return acted

    def _release_all(self, tokens: dict) -> None:
        if self.mutex is None:
            return
        for token in tokens.values():
            try:
                self.mutex.release(token)
            except Exception:  # noqa: BLE001
                pass

    async def execute(self, decision: dict, area_id: str, *, user_id: str | None = None) -> dict:
        """Carry out a matched intent in the given area."""
        intent = decision["intent"]

        priority = None
        if self.mutex is not None:
            from ..automation.mutex import Priority  # lazy — keeps module HA-free
            priority = Priority.INTENT

        if intent == "lights_off":
            acted = await self._call_domain_in_area(
                "light", "turn_off", area_id, priority=priority
            )
            return {"executed": bool(acted), "intent": intent, "entities": acted,
                    "unconfirmed": dict(getattr(self, "_jh_unc", None) or {})}   # jh-r26-bulk

        if intent == "lights_on":
            acted = await self._call_domain_in_area(
                "light", "turn_on", area_id, priority=priority
            )
            return {"executed": bool(acted), "intent": intent, "entities": acted,
                    "unconfirmed": dict(getattr(self, "_jh_unc", None) or {})}   # jh-r26-bulk

        if intent == "secure_area":
            covers = await self._call_domain_in_area(
                "cover", "close_cover", area_id,
                high_stakes=True, desired_state="closed", priority=priority,
            )
            _jh_left25 = list(getattr(self, "_jh_left", None) or [])   # jh-r25-group: the covers left out (a garage door, gate)
            _jh_unc26 = dict(getattr(self, "_jh_unc", None) or {})   # jh-r26-bulk: the covers sent that did not confirm
            locks = await self._call_domain_in_area(
                "lock", "lock", area_id,
                high_stakes=True, desired_state="locked", priority=priority,
            )
            return {
                "executed": bool(covers or locks),
                "intent": intent,
                "entities": covers + locks,
                "left_out": _jh_left25 + list(getattr(self, "_jh_left", None) or []),
                "unconfirmed": {**_jh_unc26, **dict(getattr(self, "_jh_unc", None) or {})},   # jh-r26-bulk
            }

        if intent in ("context_off", "context_close"):
            entity_id, domain = self.resolve_active_entity(area_id)
            if entity_id is None:
                return {"executed": False, "intent": intent,
                        "reason": "nothing active to act on in this area"}
            service = "close_cover" if domain == "cover" else "turn_off"
            token = None
            if self.mutex is not None and priority is not None:
                token = self.mutex.try_acquire(entity_id, priority)
                if token is None:
                    return {"executed": False, "intent": intent, "entity_id": entity_id,
                            "reason": "entity busy (higher-priority lock)"}
            try:
                from .. import actuation
                areq = actuation.request(f"{domain}.{service}", entity_id,
                                         params={"entity_id": entity_id}, action=service)
                ok, _detail = await actuation.execute_actuator(
                    self.hass, capability=f"{domain}.{service}", entity_id=entity_id,
                    domain=domain, service=service, data={"entity_id": entity_id},
                    action=service, areq=areq, verify=None, blocking=True)
                if not ok:
                    raise RuntimeError("actuation did not complete")
            except Exception:  # noqa: BLE001
                _LOGGER.exception("intent: context action failed for %s", entity_id)
                if token is not None:
                    self.mutex.release(token)
                return {"executed": False, "intent": intent, "entity_id": entity_id}
            if token is not None:
                self.mutex.release(token)
            return {"executed": True, "intent": intent, "entity_id": entity_id}

        return {"executed": False, "intent": intent, "reason": "unhandled intent"}

    async def route(self, phrase: str, target_area: str, *, user_id: str | None = None) -> dict:
        """Match a phrase and execute it. Returns a result dict; never raises."""
        phrase = _jh_courtesy_strip(phrase)   # jh-r23-router (step 165, review round 23 #3, #13): "Jarvis, ...", "..., please"
        _jh_why = _jh_gate(self.hass, phrase)   # jh-r21-gate (step 163, review round 21 #1): before any match or action
        if _jh_why:
            _LOGGER.info("intent: %r is no order to carry out now (%s) - nothing switched", str(phrase or "")[:80], _jh_why)
            return {"matched": False, "intent": None, "executed": False, "refused": _jh_why}
        return await self._jh_whole_order(phrase, target_area, user_id=user_id)   # jh-r22-router (step 164): the whole order only

    # ── jh-r22-router (step 164, review round 22 #1): the whole order only ──
    def _jh_area_name(self, area_id) -> str:
        """The real name of the area the request came from (area registry), '' when it is no known area."""
        try:
            from homeassistant.helpers import area_registry as _jh_ar
            for a in _jh_ar.async_get(self.hass).async_list_areas():
                if getattr(a, "id", None) == area_id:
                    return str(getattr(a, "name", "") or "")
        except Exception:  # noqa: BLE001
            pass
        return ""

    async def _jh_whole_order(self, phrase, target_area, *, user_id=None) -> dict:
        """jh-r22-router: act on the WHOLE order or not at all. The router's own exact phrases act in the request's room only
        (lights through try_local with that room named, secure_area on that room's covers and locks, "it" on the one active
        device of that kind there); every other order goes whole through try_local. Something left over: nothing switched."""
        def no(why):
            _LOGGER.info("intent: %r is not one whole order to carry out (%s) - nothing switched", str(phrase or "")[:80], why)
            return {"matched": False, "intent": None, "executed": False, "refused": why}
        try:
            from .. import local_engine as _jh_le
            cmd = _jh_le._jh_command_text(_jh_le._normalize(str(phrase or "")))
        except Exception:  # noqa: BLE001
            return no("gate unavailable")
        if not cmd or _jh_leftover(phrase) or _jh_leftover(cmd):
            return no("more than one order, or an exception in it")
        intent = _jh_whole_intent(cmd)
        name = self._jh_area_name(target_area)
        named = re.fullmatch(r"secure\s+(?:the\s+)?(.+)", cmd)
        if intent is None and named and name and named.group(1).strip() == name.strip().lower():
            intent = "secure_area"   # "secure the <this room>"; any other room or device is not this room
        if intent in ("lights_off", "lights_on"):
            if not name:
                return no("no known room")
            try:
                if str(_jh_le._jh_room_named(self.hass, name, None, "light") or "").lower() != name.lower():
                    return no("the room's name is also a device's")
            except Exception:  # noqa: BLE001
                return no("error")
            return await self._jh_local("turn %s the lights in the %s" % ("off" if intent == "lights_off" else "on", name),
                                        intent)
        if intent == "secure_area":
            result = await self.execute({"intent": "secure_area"}, target_area, user_id=user_id)
            return {"matched": True, **result}
        if intent in ("context_off", "context_pause", "context_close"):
            return await self._jh_context(intent, target_area)
        return await self._jh_local(phrase, None, target_area)   # jh-r23-router: the request's room for a name several devices share

    async def _jh_local(self, text, intent, area=None) -> dict:
        """jh-r22-router: the whole phrase through the instant path (local_engine.try_local: whole orders only, the named device
        or room, manual heaters, the tail, the which-one rule). A follow-up answer is never taken from here: a which-one or
        good-night question it asks is dropped at once. None from try_local: nothing switched."""
        import uuid
        key = "jh-intent-" + uuid.uuid4().hex
        res, _jh_le = None, None
        try:
            from .. import local_engine as _jh_le
            # jh-r23-router (step 165, review round 23 #2): several devices that fit the words: the one in the request's room when
            # exactly one is there, else nothing (try_local asks, and the question is dropped below)
            _jh_tok = _jh_le._JH_REQ_AREA.set(str(area or ""))
            try:
                res = await _jh_le.try_local(self.hass, text, pending_key=key)
            finally:
                _jh_le._JH_REQ_AREA.reset(_jh_tok)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("intent: the instant path failed for %r", str(text or "")[:80])
            res = None
        finally:
            for _jh_d in (getattr(_jh_le, "_JH_PENDING", None), getattr(_jh_le, "_JH_GN_ASKED", None)):
                try:
                    _jh_d.pop(key, None)
                except Exception:  # noqa: BLE001
                    pass
        if res is None:
            return {"matched": False, "intent": intent, "executed": False, "refused": "no whole order the instant path knows"}
        return {"matched": True, "intent": intent or "instant", "executed": bool(getattr(res, "success", False)),
                "reply": str(getattr(res, "text", "") or "")}

    async def _jh_context(self, intent, area_id) -> dict:
        """jh-r22-router: "turn it off" / "pause it" / "close it". "it" is the ONE active device of that kind in this room (a
        playing media player, else an 'on' light; for "pause it" only a playing media player, paused with media_pause; for
        "close it" only an open cover). Two candidates: nothing (never a guess). Manual heaters and settings are never "it"."""
        if intent == "context_pause":
            # jh-r26-media (step 168, review round 26 #9): a TV that shows only 'on' while it plays (webOS, Samsung, Android TV)
            plan = (("media_player", {"playing", "buffering", "on"}, "media_pause"),)
        elif intent == "context_close":
            plan = (("cover", {"open", "opening"}, "close_cover"),)
        else:
            plan = (("media_player", {"playing"}, "turn_off"), ("light", {"on"}, "turn_off"))
        try:
            from .. import local_engine as _jh_le
        except Exception:  # noqa: BLE001
            return {"matched": False, "intent": intent, "executed": False, "refused": "gate unavailable"}
        for domain, active, service in plan:
            found = []
            for st in self.hass.states.async_all(domain):
                try:
                    if self._area_of(st.entity_id) != area_id or str(st.state).lower() not in active:
                        continue
                    if _jh_le._jh_is_manual(self.hass, st.entity_id) or _jh_le._jh_is_setting(self.hass, st.entity_id):
                        continue
                except Exception:  # noqa: BLE001
                    continue
                found.append(st.entity_id)
            if len(found) > 1:
                return {"matched": True, "intent": intent, "executed": False, "refused": "'it' could be %d devices" % len(found)}
            if found:
                ok = await self._jh_act_one(domain, service, found[0])
                return {"matched": True, "intent": intent, "executed": ok, "entity_id": found[0],
                        "service": "%s.%s" % (domain, service)}
        return {"matched": True, "intent": intent, "executed": False, "reason": "nothing active to act on in this area"}

    async def _jh_act_one(self, domain, service, eid) -> bool:
        """jh-r22-router: one entity through the actuator seam, under the router's concurrency lock."""
        token = None
        if self.mutex is not None:
            try:
                from ..automation.mutex import Priority
                token = self.mutex.try_acquire(eid, Priority.INTENT)
            except Exception:  # noqa: BLE001
                token = None
            if token is None:
                return False
        try:
            from .. import actuation
            areq = actuation.request(f"{domain}.{service}", eid, params={"entity_id": eid}, action=service)
            ok, _detail = await actuation.execute_actuator(
                self.hass, capability=f"{domain}.{service}", entity_id=eid, domain=domain, service=service,
                data={"entity_id": eid}, action=service, areq=areq, verify=None, blocking=True)
            return bool(ok)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("intent: %s.%s failed for %s", domain, service, eid)
            return False
        finally:
            if token is not None:
                try:
                    self.mutex.release(token)
                except Exception:  # noqa: BLE001
                    pass

    # ── Interactive feedback window ───────────────────────────────────────
    async def open_feedback_window(
        self, pending_action: dict, *, timeout: float = FEEDBACK_TIMEOUT_S
    ) -> None:
        """Arm a short confirmation window. Fires EVENT_FEEDBACK_WINDOW for the
        voice satellite layer and auto-disarms after `timeout` seconds.

        `pending_action` should contain at least {"intent": ..., "area": ...}.
        """
        from homeassistant.helpers.event import async_call_later  # lazy

        self._cancel_feedback()
        self._pending_feedback = pending_action
        self.hass.bus.async_fire(
            EVENT_FEEDBACK_WINDOW, {"timeout": timeout, "action": pending_action}
        )

        def _expire(_now):
            self._pending_feedback = None
            self._feedback_cancel = None

        self._feedback_cancel = async_call_later(self.hass, timeout, _expire)

    async def handle_voice_response(self, phrase: str) -> dict:
        """Process a phrase captured during an open feedback window. If the
        window is open and the phrase is affirmative, execute the pending
        action."""
        if self._pending_feedback is None:
            return {"handled": False, "reason": "no open window"}
        if not is_affirmative(phrase):
            # jh-r22-window (step 164, review round 22 #9): while a window is open only an exact yes counts. Anything else ("close
            # it", "shut it", "no", a new order) cancels the announced action and switches nothing: "it" is the announced action,
            # never whatever happens to be active in the room, and the answer is not sent on as a fresh order.
            self._cancel_feedback()
            if _jh_fresh_order(self.hass, phrase):
                # jh-r23-window (step 165, review round 23 #14): a whole new order ("turn off the lights") is no answer: the window
                # is cancelled and the caller routes it as a fresh order (with all of route()'s guards), as upstream did
                _LOGGER.info("intent: the confirmation window got a new order %r - window cancelled, routed as an order",
                             str(phrase or "")[:80])
                return {"handled": False, "affirmative": False, "cancelled": True, "executed": False}
            _LOGGER.info("intent: the confirmation window got %r, no exact yes - cancelled, nothing switched", str(phrase or "")[:80])
            return {"handled": True, "affirmative": False, "cancelled": True, "executed": False}
        pending = self._pending_feedback
        self._cancel_feedback()
        result = await self.execute({"intent": pending["intent"]}, pending["area"])
        return {"handled": True, **result}

    def _cancel_feedback(self) -> None:
        if self._feedback_cancel is not None:
            try:
                self._feedback_cancel()
            except Exception:  # noqa: BLE001
                pass
        self._feedback_cancel = None
        self._pending_feedback = None
