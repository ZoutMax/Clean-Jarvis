"""
JARVIS Local Intent Engine (v5.7.00).

PRIMARY handler for all conversations and event reasoning. Zero API calls.
Handles 95%+ of user requests locally with pattern matching, HA entity
resolution, contextual state queries, multi-entity commands, scene/script
activation, media control, and conversational responses.

Only genuinely complex or ambiguous requests fall through to the configured
cloud LLM provider.

Architecture:
  1. Regex intent matching (commands, queries, greetings)
  2. Contextual multi-entity resolution (area-based, group, "all X")
  3. HA state introspection (who's home, what's open, etc.)
  4. Scene/script/automation triggers
  5. Media player control
  6. Complexity scoring (decides local vs LLM escalation)
  7. Follow-up context tracking
"""
from __future__ import annotations

import logging
import random
import re
import time
from dataclasses import dataclass
from typing import Optional

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

# Rolling memory of the last few acknowledgment lead-ins used, so JARVIS does
# not repeat the same opener twice in a row — variety without randomness that
# feels chaotic. MCU JARVIS rarely says the same confirmation back-to-back.
_recent_acks: list[str] = []


def _pick(options: list[str]) -> str:
    """
    Choose a phrasing that wasn't just used. Keeps confirmations from sounding
    canned while staying deterministic-enough (no external state, no cost).
    """
    if not options:
        return ""
    fresh = [o for o in options if o not in _recent_acks]
    choice = random.choice(fresh) if fresh else random.choice(options)
    _recent_acks.append(choice)
    if len(_recent_acks) > 4:
        _recent_acks.pop(0)
    return choice


# ── Result types ────────────────────────────────────────────────────────────

@dataclass
class LocalResult:
    """Result of local intent execution."""
    text: str
    success: bool
    handled: bool = True   # False = couldn't handle, fall through to LLM


# ── Follow-up context ──────────────────────────────────────────────────────

class _ConvCtx:
    last_entity: str = ""
    last_area: str = ""
    last_domain: str = ""
    last_action: str = ""
    last_ts: float = 0.0

_CTX = _ConvCtx()

def _update_ctx(**kw):
    for k, v in kw.items():
        if v:
            setattr(_CTX, f"last_{k}", v)
    _CTX.last_ts = time.time()

def _ctx_fresh():
    return (time.time() - _CTX.last_ts) < 120


# ── Normalization ───────────────────────────────────────────────────────────

# Jarvis Home patch (2026-11-06): punctuation is how speech-to-text writes a pause, so it never changes what a command does.
# Digits keep what numbers and times need (21.5, 1,5, 7:30, -5, 50%), and words keep their apostrophes and hyphens.
_JH_MARKS_RE = re.compile(r"(?<!\d)[,:]|[,:](?!\d)|[!?;…¡¿\"“”„«»()\[\]{}–—。、，！？；：]|(?<!\w)['`]|['`](?!\w)|(?<!\S)-+(?!\S)")


def _jh_clean(text: str) -> str:
    text = str(text or "").replace("’", "'").replace("‘", "'")
    text = re.sub(r"(?<!\d)\.|\.(?!\d)", "", text)   # gone without a space, so "a.m." stays one word
    return " ".join(_JH_MARKS_RE.sub(" ", text).split())


def _normalize(text: str) -> str:
    text = _jh_clean(text.lower())
    text = re.sub(r"\s+", " ", text)
    return text


# ── Intent patterns ─────────────────────────────────────────────────────────

_INTENT_PATTERNS = [
    # Lights
    (r"turn\s+on\s+(?:the\s+)?(.+?)(?:\s+light(?:s)?)?$",       "turn_on",  "light"),
    (r"turn\s+off\s+(?:the\s+)?(.+?)(?:\s+light(?:s)?)?$",      "turn_off", "light"),
    (r"switch\s+on\s+(?:the\s+)?(.+?)(?:\s+light(?:s)?)?$",     "turn_on",  "light"),
    (r"switch\s+off\s+(?:the\s+)?(.+?)(?:\s+light(?:s)?)?$",    "turn_off", "light"),
    (r"(?:switch|turn)\s+(?:the\s+)?(.+?)\s+on$",  "turn_on",  "light"),
    (r"(?:switch|turn)\s+(?:the\s+)?(.+?)\s+off$", "turn_off", "light"),
    (r"(?:switch|turn)\s+(?:the\s+)?(.+?)\s+on\s+please$",  "turn_on",  "light"),
    (r"(?:switch|turn)\s+(?:the\s+)?(.+?)\s+off\s+please$", "turn_off", "light"),
    (r"lights?\s+on\s+(?:in\s+)?(?:the\s+)?(.+)$",              "turn_on",  "light"),
    (r"lights?\s+off\s+(?:in\s+)?(?:the\s+)?(.+)$",             "turn_off", "light"),
    (r"dim\s+(?:the\s+)?(.+?)(?:\s+light(?:s)?)?\s+to\s+(\d+)", "dim",      "light"),
    (r"set\s+(?:the\s+)?(.+?)(?:\s+light(?:s)?)?\s+(?:brightness\s+)?to\s+(\d+)",
     "dim", "light"),
    (r"brighten\s+(?:the\s+)?(.+)", "brighten", "light"),
    # Switches
    (r"turn\s+on\s+(?:the\s+)?(.+?)(?:\s+switch)?$",   "turn_on",  "switch"),
    (r"turn\s+off\s+(?:the\s+)?(.+?)(?:\s+switch)?$",  "turn_off", "switch"),
    # German on/off verbs (issue #511). German puts the separable particle at the
    # END — "schalte das Licht ein/aus", "mach die Lampe an/aus" — or uses the
    # infinitive "<Gerät> einschalten/ausschalten". Entity resolution already
    # worked in German; only the action verb was unrecognised, so these sentences
    # fell through to a bogus state query ("… is currently off") instead of
    # actuating. domain_hint=None lets the resolver match a light, switch, etc.
    (r"(?:schalte?|mache?)\s+(?:(?:das|die|der|den|dem)\s+)?(.+?)\s+(?:ein|an)$",
     "turn_on", None),
    (r"(?:schalte?|mache?)\s+(?:(?:das|die|der|den|dem)\s+)?(.+?)\s+aus$",
     "turn_off", None),
    (r"(.+?)\s+einschalten$", "turn_on", None),
    (r"(.+?)\s+ausschalten$", "turn_off", None),
    # Locks (unlock before lock; lock's (?<!un) stops it matching inside "unlock")
    (r"unlock\s+(?:the\s+)?(.+?)(?:\s+door)?$", "unlock", "lock"),
    (r"(?<!un)lock\s+(?:the\s+)?(.+?)(?:\s+door)?$",   "lock",   "lock"),
    # Covers / Garage
    (r"open\s+(?:the\s+)?(.+?)(?:\s+(?:door|gate|cover|garage))?$",  "open",  "cover"),
    (r"close\s+(?:the\s+)?(.+?)(?:\s+(?:door|gate|cover|garage))?$", "close", "cover"),
    # Climate
    (r"set\s+(?:the\s+)?(?:temperature|thermostat|temp)\s+(?:to|at)\s+(\d+)",
     "set_temp", "climate"),
    (r"set\s+(?:the\s+)?(.+?)\s+(?:temperature|thermostat|temp)\s+(?:to|at)\s+(\d+)",
     "set_temp_named", "climate"),
    (r"(?:make\s+it|set\s+it)\s+(?:to\s+)?(\d+)\s*(?:degrees|°)?", "set_temp", "climate"),
    # Fan
    (r"turn\s+on\s+(?:the\s+)?(.+?)(?:\s+fan)?$",   "turn_on",  "fan"),
    (r"turn\s+off\s+(?:the\s+)?(.+?)(?:\s+fan)?$",  "turn_off", "fan"),
    # Media
    (r"(?:pause|stop)\s+(?:the\s+)?(?:music|media|tv|playback)(?:\s+(?:in|on)\s+(?:the\s+)?(.+))?$",
     "media_pause", "media_player"),
    (r"(?:resume|play|unpause)\s+(?:the\s+)?(?:music|media|tv|playback)(?:\s+(?:in|on)\s+(?:the\s+)?(.+))?$",
     "media_play", "media_player"),
    (r"(?:volume\s+up|turn\s+(?:it|the\s+volume)\s+up)(?:\s+(?:in|on)\s+(?:the\s+)?(.+))?$",
     "volume_up", "media_player"),
    (r"(?:volume\s+down|turn\s+(?:it|the\s+volume)\s+down)(?:\s+(?:in|on)\s+(?:the\s+)?(.+))?$",
     "volume_down", "media_player"),
    (r"(?:set\s+)?volume\s+(?:to\s+)?(\d+)(?:\s+(?:percent|%?))?(?:\s+(?:in|on)\s+(?:the\s+)?(.+))?$",
     "volume_set", "media_player"),
    (r"mute(?:\s+(?:the\s+)?(?:tv|speaker|media))?(?:\s+(?:in|on)\s+(?:the\s+)?(.+))?$",
     "mute", "media_player"),
    (r"unmute(?:\s+(?:the\s+)?(?:tv|speaker|media))?(?:\s+(?:in|on)\s+(?:the\s+)?(.+))?$",
     "unmute", "media_player"),
    (r"(?:skip|next)\s+(?:track|song)(?:\s+(?:in|on)\s+(?:the\s+)?(.+))?$",
     "media_next", "media_player"),
    # Scenes/scripts
    (r"(?:activate|run|trigger|start|execute)\s+(?:the\s+)?(.+?)(?:\s+scene)?$",
     "scene", None),
    # State queries
    (r"(?:what(?:'s| is)\s+the\s+)?temperature\s+(?:in\s+)?(?:the\s+)?(.+)",
     "query_temp", None),
    (r"(?:what(?:'s| is)\s+the\s+)?(.+?)(?:\s+temperature)", "query_temp", None),
    (r"(?:is|are)\s+(?:the\s+)?(.+?)\s+(?:on|off|open|closed|locked|unlocked)",
     "query_state", None),
    (r"(?:what(?:'s| is)\s+the\s+)?status\s+of\s+(?:the\s+)?(.+)",
     "query_state", None),
    # Time/Date
    (r"what\s+time\s+is\s+it",      "query_time", None),
    (r"what(?:'s| is)\s+the\s+time", "query_time", None),
    (r"what(?:'s| is)\s+today(?:'s)?\s+date", "query_date", None),
    (r"what\s+(?:day|date)\s+is\s+(?:it|today)(?:\s+today)?$", "query_date", None),   # Jarvis Home patch (2026-11-15)
    # Greetings
    (r"^(?:hey|hi|hello|good\s+(?:morning|afternoon|evening))(?:\s+jarvis)?[.!]?$",
     "greeting", None),
    (r"^(?:thanks|thank\s+you|cheers)(?:\s+.*)?$", "thanks", None),
    (r"^(?:you'?re\s+welcome|no\s+problem|no\s+worries)", "thanks", None),
    # Home status
    (r"(?:home|house)\s+status",     "status", None),
    (r"how(?:'s| is)\s+the\s+house", "status", None),
    (r"system\s+status",             "status", None),
    (r"run\s+(?:a\s+)?diagnostic",   "status", None),
]

# ── Multi-entity / bulk patterns ────────────────────────────────────────────

# jh-r23-bulk (step 165, review round 23 #1): a group order ONLY for an explicit plural or "all" ("turn off the lights", "lock
# all the doors", "close the blinds"). A singular noun ("unlock the door", "open the blind", "turn off the fan", "turn off the
# light in the kitchen") is no group order: it goes to the single-device path, which asks which one when several devices fit.
# Unlocking is never done in bulk (refused with the reason), and a group order never reaches a security barrier (a garage door,
# gate or door cover, a lock opened, an alarm, siren or security switch turned off) or a manual heater: jh_bulk_barrier.
_JH_BULK_ALL = r"all\s+(?:of\s+)?(?:the\s+)?"
_JH_BULK_LIGHTS = r"(?:" + _JH_BULK_ALL + r"lights?|(?:the\s+)?lights)"
# jh-r26-lockup (step 168, review round 26 #10): "lock up", "lock everything", "lock the house" are lock orders of the whole
# home (the lock row: THE group function, every lock, the garage / gate locks said, every lock still not locked named);
# "unlock everything" is refused like "unlock all the doors"
_JH_BULK_LOCKUP = (r"(?:up|(?:it\s+)?(?:all|everything)(?:\s+up)?|(?:up\s+)?(?:the\s+)?(?:whole\s+)?(?:house|home|place|flat"
                   r"|apartment)(?:\s+up)?)")
_JH_BULK_DOORS = r"(?:" + _JH_BULK_ALL + r"(?:doors?|locks?)|(?:the\s+)?(?:doors|locks)|" + _JH_BULK_LOCKUP + r")"
_JH_BULK_COVERS = (r"(?:" + _JH_BULK_ALL + r"(?:covers?|blinds?|shades?|curtains?|shutters?)"
                   r"|(?:the\s+)?(?:covers|blinds|shades|curtains|shutters))")
_JH_BULK_FANS = r"(?:" + _JH_BULK_ALL + r"fans?|(?:the\s+)?fans)"
_BULK_PATTERNS = [
    (r"(?:turn|switch|shut)\s+off\s+" + _JH_BULK_LIGHTS + r"$", "turn_off", "light", None),
    (r"(?:turn|switch)\s+on\s+" + _JH_BULK_LIGHTS + r"$", "turn_on", "light", None),
    (r"(?:turn|switch|shut)\s+off\s+" + _JH_BULK_LIGHTS + r"\s+(?:in|on)\s+(?:the\s+)?(.+)$", "turn_off", "light", "area"),
    (r"(?:turn|switch)\s+on\s+" + _JH_BULK_LIGHTS + r"\s+(?:in|on)\s+(?:the\s+)?(.+)$", "turn_on", "light", "area"),
    (r"(?:turn|switch|shut)\s+" + _JH_BULK_LIGHTS + r"\s+off\s+(?:in|on)\s+(?:the\s+)?(.+)$", "turn_off", "light", "area"),
    (r"(?:turn|switch)\s+" + _JH_BULK_LIGHTS + r"\s+on\s+(?:in|on)\s+(?:the\s+)?(.+)$", "turn_on", "light", "area"),
    (r"unlock\s+" + _JH_BULK_DOORS + r"$", "unlock", "lock", None),
    (r"(?<!un)lock\s+" + _JH_BULK_DOORS + r"$", "lock", "lock", None),
    (r"close\s+" + _JH_BULK_COVERS + r"$", "close", "cover", None),
    (r"open\s+" + _JH_BULK_COVERS + r"$", "open", "cover", None),
    (r"(?:turn|switch|shut)\s+off\s+" + _JH_BULK_FANS + r"$", "turn_off", "fan", None),
    (r"(?:turn|switch)\s+on\s+" + _JH_BULK_FANS + r"$", "turn_on", "fan", None),
    (r"turn\s+(?:off|out)\s+everything$", "turn_off", "all", None),
]

# ── Contextual queries ──────────────────────────────────────────────────────

# These are scanned BEFORE the command patterns (see try_local), so a query
# pattern that can match a bare command word would hijack the command — e.g.
# `open` in "open the garage door" matching a `what_open` query. The
# interrogative prefix on the vulnerable patterns is therefore REQUIRED (not
# optional): a real question like "what's open" still matches, but a command that
# merely *contains* the word (open / home / weather / outside) no longer does.
_QUERY_PATTERNS = [
    (r"(?:who(?:'s| is)\s+)home\b",                     "who_home"),
    (r"(?:is\s+)?(?:anyone|anybody)\s+home",              "who_home"),
    (r"(?:what(?:'s| is)\s+)(?:open|unlocked)",          "what_open"),
    (r"(?:are\s+)?(?:any|which)\s+(?:doors?|windows?)\s+open", "what_open"),
    (r"(?:which|what)\s+lights?\s+(?:are\s+)?(?:still\s+)?on\b", "lights_on"),   # Jarvis Home patch (2026-11-15)
    (r"(?:are\s+)?(?:any|which)\s+(?:lights?)\s+on",     "lights_on"),
    (r"(?:how\s+many)\s+lights?\s+(?:are\s+)?on",        "lights_on"),
    # Jarvis Home patch (2026-11-09): a question about a period goes to JARVIS (energy_usage), not to the power-now answer
    (r"(?:what(?:'s| is)\s+(?:the\s+)?)?(?:energy|power)\s+(?:usage|consumption)(?!.*\b(?:today|yesterday|week|month|year|far|since|last|this|used|until now|till now|up to now)\b)", "energy"),   # jh-live-answers: right now stays
    (r"(?:what(?:'s| is)\s+(?:the\s+)?)weather(?!.*\b(?:tomorrow|tonight|week|weekend|forecast|later|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b)", "weather"),
    (r"(?:what(?:'s| is)\s+(?:it\s+)?)(?:like\s+)?outside", "weather"),
    (r"(?:how\s+(?:warm|cold|hot))\s+is\s+it",           "weather"),
    (r"(?:what\s+)?(?:devices?|entities?)\s+(?:are\s+)?(?:in|at)\s+(?:the\s+)?(.+)", "area_devices"),
]


# ── Complexity scoring ──────────────────────────────────────────────────────

def score_complexity(text: str) -> int:
    """Score 0-100. Higher = needs LLM. <70 handled locally."""
    t = _jh_clean(text).lower()
    score = 20
    # Strong LLM signals
    for w in ("explain", "why", "how does", "what do you think",
              "write", "compose", "draft", "create a", "help me",
              "analyze", "compare", "recommend", "suggest",
              "tell me about", "what happened", "story",
              "code", "script", "program", "debug", "fix this",
              "plan", "schedule", "strategy", "brainstorm",
              "summarize", "translate", "calculate"):
        if w in t:
            score += 40
            break
    if len(text.split()) > 20:
        score += 15
    if "?" in text and len(text.split()) > 10:
        score += 10
    for w in ("because", "however", "although"):
        if w in t:
            score += 10
            break
    # Simple signals (reduce)
    for w in ("turn on", "turn off", "lock", "unlock", "open", "close",
              "dim", "brighten", "set temp", "volume", "pause", "play"):
        if w in t:
            score -= 20
            break
    for w in ("what time", "who's home", "how many lights",
              "what's open", "temperature", "status",
              "good morning", "good night", "hello", "thanks"):
        if w in t:
            score -= 15
            break
    return max(0, min(100, score))


# ── Entity resolution ────────────────────────────────────────────────────────

def _fuzzy_score(a: str, b: str) -> float:
    """
    Simple fuzzy similarity score (0-100) without external deps.
    Combines character overlap, word overlap, and edit distance approximation.
    """
    if a == b:
        return 100.0
    if not a or not b:
        return 0.0

    # Character bigram overlap (Dice coefficient)
    def bigrams(s):
        return set(s[i:i+2] for i in range(len(s)-1)) if len(s) > 1 else {s}

    bg_a, bg_b = bigrams(a), bigrams(b)
    if bg_a and bg_b:
        overlap = len(bg_a & bg_b)
        dice = (2.0 * overlap) / (len(bg_a) + len(bg_b)) * 100
    else:
        dice = 0.0

    # Word overlap
    words_a, words_b = set(a.split()), set(b.split())
    if words_a and words_b:
        common = len(words_a & words_b)
        word_score = common / max(len(words_a), len(words_b)) * 100
    else:
        word_score = 0.0

    # Containment bonus
    contain = 0.0
    if _jh_word_in(a, b):   # jh-r18-word: containment counts at a word start only
        contain = len(a) / len(b) * 80
    elif _jh_word_in(b, a):
        contain = len(b) / len(a) * 80

    return max(dice, word_score, contain)


# Common STT mishearing patterns: what STT outputs → what user likely meant
_STT_CORRECTIONS = {
    "lamp": "light", "lamps": "lights",
    "lite": "light", "lites": "lights",
    "nite": "night", "nitestand": "nightstand",
    "night stand": "nightstand",
    "bed side": "bedside", "bed lamp": "bedside light",
    "chase": "chase", "chaise": "chase",
    "tv": "television", "teevee": "tv",
    "a.c.": "ac", "air con": "air conditioner",
    "thermos": "thermostat", "thermo": "thermostat",
    "bed room": "bedroom", "living room": "living_room",
    "bath room": "bathroom", "dining room": "dining_room",
    "down stairs": "downstairs", "up stairs": "upstairs",
    "front porch": "front_porch", "back yard": "backyard",
}


def _load_learned_aliases(hass) -> dict:
    try:
        import json as _json
        from .paths import config_path_str
        learn_file = config_path_str(".jarvis_learned.json", hass=hass)
        with open(learn_file) as f:
            learned = _json.load(f)
        aliases = learned.get("alias", {})
        return aliases if isinstance(aliases, dict) else {}
    except Exception:
        return {}


def _jh_is_setting(hass, eid: str) -> bool:
    """Jarvis Home patch: config and diagnostic entities are never spoken command targets."""
    try:
        from homeassistant.helpers import entity_registry as _er
        entry = _er.async_get(hass).async_get(eid)
        if entry is not None and entry.entity_category is not None:
            return True
    except Exception:
        pass
    return False   # Jarvis Home patch (2026-11-07): Home Assistant's entity category decides, never words in an id


# jh-r18-word (step 160, review round 18 #1): THE one rule for "these words are in that name". Every fragment-in-name test of
# the instant path (entity, scene, lock, area and sound matching, the vague-word check) goes through _jh_word_in; a bare
# substring test is banned by a kit test. Three review rounds found a safety bug in a substring test ("heater" in "Home
# Theater", "room" in "Bathroom"), so the rule lives in one place: the fragment must START a word of the text (an underscore,
# a dot, a digit or a space before it counts as a word start, a letter does not), the same rule as _jh_vague and the heater
# words. whole=True also needs a word END after it ("tv" names "Living Room TV", not "TVs").
_JH_WORD_START = r"(?<![^\W\d_])"
_JH_WORD_END = r"(?![^\W\d_])"


def _jh_word_in(frag, text, whole=False) -> bool:
    """True when frag stands in text at the start of a word (and ends one with whole=True). The caller lower-cases both."""
    frag, text = str(frag or ""), str(text or "")
    if not frag.strip():
        return False
    return re.search(_JH_WORD_START + re.escape(frag) + (_JH_WORD_END if whole else ""), text) is not None


# jh-r18-class (step 160, review round 18 #1): a generic device word ("the heater", "the fan", "the tv") names a KIND of device.
# _find_entity may only answer it with a device of that kind: one whose name or id holds a word of the kind, or whose domain or
# device class is that kind (heaters: JARVIS's own heater list, a heater label, the manual_heater label, a water heater). When
# no such device exists the answer is None (online the AI answers, offline the honest "couldn't find"), never an unrelated
# device that merely scored best ("Home Theater" for "heater", a lamp called Heather by sound).
_JH_CLASS_WORDS = {
    "light": ("light", "lights", "lamp", "lamps", "licht", "lichter", "lampe", "lampen"),
    "fan": ("fan", "fans", "ventilator", "ventilatoren"),
    "tv": ("tv", "tvs", "television", "televisions", "telly", "fernseher", "televisie"),
    "speaker": ("speaker", "speakers", "lautsprecher"),
    "climate": ("thermostat", "thermostats"),
    "lock": ("lock", "locks", "schloss"),
    "cover": ("blind", "blinds", "curtain", "curtains", "shutter", "shutters", "cover", "covers", "rollladen"),
    "plug": ("plug", "plugs", "socket", "sockets", "outlet", "outlets", "steckdose", "stopcontact"),
}
_JH_CLASS_DOMAINS = {"light": ("light",), "fan": ("fan",), "climate": ("climate",), "lock": ("lock",), "cover": ("cover",),
                     "plug": ("switch",), "tv": (), "speaker": ()}
_JH_CLASS_MEDIA = {"tv": "tv", "speaker": "speaker"}   # a media player of that device class

# jh-r24-kind (step 166, review round 24 #1, #2): ONE strict word -> kind map for the whole engine: the bulk rows,
# jh_bulk_barrier, _jh_could_be, _jh_class_of / _jh_class_fits, _jh_one_of, the single-device path (the router acts through
# it) and the AI's bulk_control. A kind word reaches ONLY devices of that kind. Covers and media players are known by their
# device class; one with no device class by the LAST kind word in its own name (else its id), so "Pool Cover" is a cover,
# "Window Blind" a blind and "Shed Roller" nothing in particular. Every other kind is known by its domain. Blind, shade,
# curtain and shutter never mean a window, skylight, awning, damper, pool cover, gate, garage or door (those are reached by
# their own name or kind word, or by the generic "covers"); "lights" never a plain switch (only a light, or a switch that
# switch_as_x shows as a light); "fans" and "lights" never a heater; "speakers" never a tv and "tv" never a speaker. A device
# is still reached by its own whole name. When nothing of the named kind exists, nothing is switched.
JH_KIND_WORDS = {}
for _jh_k24, _jh_w24 in (
        ("light", "light lights lamp lamps licht lichter lampe lampen"),
        ("fan", "fan fans ventilator ventilatoren"),
        ("tv", "tv tvs television televisions telly fernseher televisie"),
        ("speaker", "speaker speakers lautsprecher"),
        ("climate", "thermostat thermostats"),
        ("lock", "lock locks schloss"),
        ("plug", "plug plugs socket sockets outlet outlets steckdose stopcontact"),
        ("blind", "blind blinds shade shades curtain curtains shutter shutters rollladen jalousie jalousien rolluik rolluiken "
                  "gordijn gordijnen"),
        ("window", "window windows skylight skylights fenster raam ramen"),
        ("awning", "awning awnings markise markisen zonnescherm zonneschermen"),
        ("damper", "damper dampers"),
        ("garage", "garage garages garagedeur garagentor"),
        ("gate", "gate gates"),
        ("door", "door doors deur tür tuer"),
        ("cover", "cover covers")):
    for _jh_x24 in _jh_w24.split():
        JH_KIND_WORDS[_jh_x24] = _jh_k24
_JH_KIND_CLASSES = {"blind": frozenset(("blind", "shade", "curtain", "shutter")), "window": frozenset(("window",)),
                    "awning": frozenset(("awning",)), "damper": frozenset(("damper",)), "garage": frozenset(("garage",)),
                    "gate": frozenset(("gate",)), "door": frozenset(("door",)), "tv": frozenset(("tv",)),
                    "speaker": frozenset(("speaker",))}
_JH_KIND_DOMS = {"light": ("light",), "fan": ("fan",), "climate": ("climate",), "lock": ("lock",), "plug": ("switch",),
                 "cover": ("cover",), "blind": ("cover",), "window": ("cover",), "awning": ("cover",), "damper": ("cover",),
                 "garage": ("cover",), "gate": ("cover",), "door": ("lock", "cover"), "tv": ("media_player",),
                 "speaker": ("media_player",)}
# garage, gate and door are also names and rooms ("the garage", "the front door"): _jh_class_of leaves them to the name rules,
# where the kind still decides for a device of another kind whose own name does not hold the words (_jh_class_fits)
_JH_NAME_KINDS = frozenset(("garage", "gate", "door"))
class _JhWordRule:
    """jh-r26-barrier (step 168, review round 26 #1, #3): a stand-in for the old barrier / security regexes. .search(text) is
    true when THE word rule (jh_barrier_words, compound-aware) finds one of these categories in the text, so every check that
    still reads one of the old names reads the one rule, and no copy can drift."""
    __slots__ = ("cats",)

    def __init__(self, cats):
        self.cats = frozenset(cats)

    def search(self, text):
        return bool(self.cats & jh_barrier_words(text))


_JH_NOT_DOOR = _JhWordRule(("garage", "gate"))   # jh-r26-barrier: a lock named as a garage or gate lock ("Tuinpoort", "Hoftor")
_JH_KIND_LEAD = frozenset(("any", "all", "of", "every", "each", "my", "our", "the", "a", "an", "de", "het", "die", "der", "das"))
# the default kind of a group order when its words name none (the bulk rows, the AI's bulk_control)
_JH_KIND_DEFAULT = {"light": "light", "fan": "fan", "lock": "lock", "cover": "cover"}


def _jh_kind_words(fragment) -> list:
    """jh-r24-kind: the words of a name as said, without leading articles and quantifiers ("all of the blinds" -> ["blinds"])."""
    words = re.findall(r"[^\W_]+", str(fragment or "").casefold())
    while words and words[0] in _JH_KIND_LEAD:
        words = words[1:]
    return words


def jh_kind_of(fragment):
    """jh-r24-kind: the kind a fragment names when it is nothing but a kind word ("blinds", "the speaker", "all lights",
    "garage door"), else None. Heater words are the kind "heat"."""
    words = _jh_kind_words(fragment)
    if not words:
        return None
    t = " ".join(words)
    if t in _JH_HEAT_WORDS:
        return "heat"
    if t in ("garage door", "garage doors"):
        return "garage"
    # jh-r25-kindarg (step 167, review round 25 #14): the same kinds in the other reply languages (fr, es, it, pt, th)
    return (JH_KIND_WORDS.get(t) or JH_KIND_WORDS_MORE.get(t)
            or JH_KIND_WORDS_MORE.get(str(fragment or "").strip().casefold()))


def _jh_name_kind(eid, name, dom) -> str:
    """jh-r24-kind: the kind a cover or media player with no device class has by its own name: the LAST kind word of that
    domain in its name (else in its id); '' when there is none."""
    for text in (str(name or ""), str(eid or "").split(".", 1)[-1].replace("_", " ")):
        words = re.findall(r"[^\W_]+", text.casefold())
        for w in reversed(words):
            k = JH_KIND_WORDS.get(w)
            if k and dom in _JH_KIND_DOMS.get(k, ()):
                return k
    return ""


def _jh_switch_shown_as(hass, eid, cache) -> str:
    """jh-r24-kind: the domain a switch_as_x wrapper shows this switch as ('light', 'fan', ...), '' when none (read once per
    resolution through the caller's cache)."""
    if "sax" not in cache:
        found = {}
        try:
            from homeassistant.helpers import entity_registry as er
            for entry in list(er.async_get(hass).entities.values()):
                if getattr(entry, "platform", None) == "switch_as_x":
                    src = _jh_wrapped_source(hass, entry)
                    if src:
                        found[str(src)] = str(entry.entity_id).split(".", 1)[0]
        except Exception:  # noqa: BLE001
            pass
        cache["sax"] = found
    return cache["sax"].get(str(eid), "")


def _jh_heaterish(hass, eid, name, cache) -> bool:
    """jh-r24-kind: a heater, whatever its domain (a heater plug shown as a fan or a light): the manual_heater label, JARVIS's
    heater list, heater words in its name or id, a heater label. Fails closed (True: never a light or a fan)."""
    try:
        if _jh_is_manual(hass, eid):
            return True
        if _jh_heater_text(str(name or "").casefold()) or _jh_heater_text(str(eid).casefold()):
            return True
        if "heat" not in cache:
            switches, climates = _jh_heaters(hass)
            cache["heat"] = {s.entity_id for s in switches + climates}
        if eid in cache["heat"]:
            return True
        if "labels" not in cache:
            cache["labels"] = _JhHeaterLabels(hass)
        return bool(cache["labels"].on(eid))
    except Exception:  # noqa: BLE001
        return True


def _jh_heat_fits(hass, eid, name, cache) -> bool:
    """jh-r18-class: a device "the heater" may mean: JARVIS's own heater list (labels, exposure), a manual heater (refused
    later, never switched), or an exposed, shown water heater or device with heater words or a heater label."""
    dom, low_id, name = eid.split(".")[0], eid.casefold(), str(name or "").casefold()
    if "heat" not in cache:
        switches, climates = _jh_heaters(hass)
        cache["heat"] = {s.entity_id for s in switches + climates}
    if eid in cache["heat"] or _jh_is_manual(hass, eid):
        return True
    if not (_jh_shown(hass, eid) and _jh_exposed(hass, eid)):
        return False
    if dom == "water_heater" or _jh_heater_text(name) or _jh_heater_text(low_id):
        return True
    if "labels" not in cache:
        cache["labels"] = _JhHeaterLabels(hass)
    return bool(cache["labels"].on(eid))


def jh_kind_fits(hass, kind, eid, name=None, cache=None, hidden_ok=False, barrier_ok=False) -> bool:
    """jh-r24-kind (step 166, review round 24 #1, #2): THE kind rule. True when the device is of that kind: a cover or media
    player by its device class (with none, by the last kind word of its own name), any other device by its domain; lights and
    fans are never heaters; a switch is a light or a fan only when switch_as_x shows it as one; a lock is a door unless it is
    named as a gate or garage. No kind: True. Fails closed (False)."""
    if not kind:
        return True
    try:
        eid = str(eid)
        dom = eid.split(".", 1)[0]
        cache = cache if cache is not None else {}
        attrs = getattr(hass.states.get(eid), "attributes", None) or {}
        name = str(attrs.get("friendly_name") or "") if name is None else str(name)
        if kind == "heat":
            return _jh_heat_fits(hass, eid, name, cache)
        if not hidden_ok and _jh_hidden(hass, eid):
            return False   # a hidden entity (the plug under a switch_as_x light) is no device of any kind of its own
        # jh-r25-group (step 167, review round 25 #1): a garage / gate / door relay or an alarm / siren switch is no "plug"
        # (jh_group_targets asks with barrier_ok=True, so it can name such a device as left out)
        if (kind == "plug" and dom in ("switch", "input_boolean") and not barrier_ok
                and (_jh_barrier_switch(eid, name, attrs.get("device_class") or "")
                     or (dom == "switch" and jh_sax_kind(hass, eid, cache)))):   # jh-r28-group (step 171): a wrapped siren / lock
            return False
        if kind == "cover":
            return dom == "cover"
        if kind in _JH_KIND_CLASSES:
            if dom not in _JH_KIND_DOMS[kind]:
                return False
            if dom == "lock":   # "the door" of a lock: every lock not named as a gate or garage
                return not _JH_NOT_DOOR.search("%s %s" % (eid, name))
            dc = str(attrs.get("device_class") or "").strip().lower()
            if dc:
                return dc in _JH_KIND_CLASSES[kind]
            return _jh_name_kind(eid, name, dom) == kind
        if dom not in _JH_KIND_DOMS.get(kind, ()):
            if kind in ("light", "fan") and dom == "switch":
                return _jh_switch_shown_as(hass, eid, cache) == kind and not _jh_heaterish(hass, eid, name, cache)
            return False
        if kind in ("light", "fan"):
            return not _jh_heaterish(hass, eid, name, cache)
        return True
    except Exception:  # noqa: BLE001
        return False


def jh_bulk_kind(domain, text):
    """jh-r24-kind: (kind, word as said) of a group order: the last kind word of the order that a device of that domain can
    have ("close the blinds" -> blind, "lock all the doors" -> door, "turn off the lights in the garage" -> light), else the
    domain's own kind."""
    for w in reversed(re.findall(r"[^\W_]+", str(text or "").casefold())):
        k = JH_KIND_WORDS.get(w)
        if k and domain in _JH_KIND_DOMS.get(k, ()):
            return k, w
    return _JH_KIND_DEFAULT.get(domain), {"light": "lights", "fan": "fans", "lock": "locks", "cover": "covers"}.get(domain, domain)


def _jh_class_of(fragment):
    """The device kind a fragment names when it is nothing but a generic device word ("the heater", "heaters", "fan"), else None."""
    frag = re.sub(r"^(?:any|all|all of|all the|all of the|every|each|my|the|a|an)\s+", "", str(fragment or "").strip().casefold()).strip()
    if frag in _JH_HEAT_WORDS:
        return "heat"
    kind = jh_kind_of(frag)   # jh-r24-kind (step 166): the one strict map; garage, gate and door are left to the name rules
    return None if _JH_NAME_KINDS.issuperset((kind,)) else kind


def _jh_class_fits(hass, fragment, match, _cache=None) -> bool:
    """False only when the fragment is a generic device word and the match is no device of that kind (jh-r18-class). _cache: a
    dict kept by the caller for one resolution, so the heater list and the labels are read once, not per entity."""
    # jh-r24-kind (step 166, review round 24 #1, #2): the kind is decided by the one strict map (jh_kind_fits): a cover or media
    # player by its device class, another device by its domain, never by a kind word that merely stands in its name ("the
    # light" is no plain switch called "Garden Light", "the blind" no skylight). A device whose whole name is the words is
    # always its own. For more words ("the bedroom blind") the last word names the kind, and a device of another kind fits
    # only when its own name (or id) holds every word as said ("the garden light" is the switch "Garden Light").
    if not match:
        return True
    kind = _jh_class_of(fragment)
    eid, name = str(match[0]), str(match[1] or "")
    words = _jh_kind_words(fragment)
    try:
        if words and _jh_norm_name(name) == " ".join(words):
            return True
        if kind is not None:
            return jh_kind_fits(hass, kind, eid, name, _cache)
        head = jh_kind_of(words[-1]) if words else None
        if head is None or jh_kind_fits(hass, head, eid, name, _cache):
            return True
        low, low_id = name.casefold(), eid.casefold().replace("_", " ")
        return all(_jh_word_in(w, low, whole=True) or _jh_word_in(w, low_id, whole=True) for w in words)
    except Exception:
        return False   # fail closed: an unreadable check never lets an unrelated device through


def _jh_word_sim(a, b) -> float:
    """jh-r19-fuzzy: how alike two single words are (1.0 the same or the same sound; a word with a digit only as itself)."""
    if a == b or _jh_sound(a) == _jh_sound(b):
        return 1.0
    if any(c.isdigit() for c in a + b):
        return 0.0
    import difflib
    return difflib.SequenceMatcher(None, a, b).ratio()


def _jh_fuzzy_words_fit(fragment, name) -> bool:
    """jh-r19-fuzzy (step 161, review round 19 #1 a): a fuzzy name match is a misheard word, never an added one. True only when
    the said words, one by one, each match their own word of the name (exactly or by sound for words of up to 3 letters, else
    at least 0.7 alike), so "bedroom heater at 7", "living room heater later" or "schlafzimmer heizung nicht" never pass for a
    name. Words run together ("bed room heater") count as the name."""
    fw, nw = str(fragment or "").split(), str(name or "").split()
    if not fw or not nw:
        return False
    if "".join(fw) == "".join(nw) or _jh_sound("".join(fw)) == _jh_sound("".join(nw)):
        return True
    if len(fw) > len(nw):
        return False
    left = list(nw)
    for w in fw:
        best = max(left, key=lambda n: _jh_word_sim(w, n))
        sim = _jh_word_sim(w, best)
        if sim < (1.0 if len(w) <= 3 else 0.7):
            return False
        left.remove(best)
    return True


def _jh_frag(name_fragment):
    """Jarvis Home patch (2026-11-06): one name clean-up shared by the resolver and the vague check."""
    fragment = name_fragment.lower().strip().replace("colour", "color")
    changed = True
    while changed:
        changed = False
        for suffix in ("light", "lights", "lamp", "lamps", "switch", "switches",
                       "fan", "fans", "lock", "locks", "door", "doors",
                       "cover", "covers", "thermostat", "sensor",
                       "please", "now", "for me", "jarvis", "again"):
            shorter = re.sub(rf"\s+{suffix}$", "", fragment)
            if shorter != fragment:
                fragment, changed = shorter, True
    return re.sub(r"^(?:my|the|light|lamp|plug|switch)\s+(?=\S)", "", fragment.strip()).strip()  # Jarvis Home patch: filler words


def _jh_sound(text):
    """Jarvis Home patch (2026-11-06): how a name sounds, for comparing only. k, ck and q become c, ph becomes f, z becomes s and
    doubled letters count once. Digits stay as they are, so Lamp 11 is never Lamp 1."""
    t = _jh_clean(text).lower().replace("colour", "color").replace("ph", "f").replace("ck", "c")
    t = re.sub(r"[kq]", "c", t).replace("z", "s")
    return re.sub(r"([^\W\d_])\1+", r"\1", t)


def _jh_sound_hits(hass, fragment, domains):
    """The real names that sound like these words, read live: the whole name when one sounds alike, else names that hold every
    word. Whole words only, and three letters at least, so a sound never stretches to an unrelated name."""
    want = _jh_sound(fragment)
    if len(want.replace(" ", "")) < 3:
        return []
    words = set(want.split())
    whole, same, part = [], [], []
    for domain in domains:
        for st in hass.states.async_all(domain):
            name = st.attributes.get("friendly_name") or ""
            if not name or _jh_is_setting(hass, st.entity_id):
                continue
            sound = _jh_sound(name)
            heard = set(sound.split())
            box = whole if sound == want else same if heard == words else part if words <= heard else None
            if box is not None and name not in [x[1] for x in box]:
                box.append((st.entity_id, name))
            elif box is not None and st.state != "unavailable":
                # Jarvis Home patch (2026-11-07, round 2): one name, two devices: the live one is kept, not the one that is gone
                box[:] = [(st.entity_id, name) if x[1] == name and getattr(hass.states.get(x[0]), "state", "unavailable") == "unavailable"
                          else x for x in box]
    return whole or same or part


_JH_SOUND_DOMAINS = ("light", "switch", "lock", "cover", "climate", "fan", "media_player", "sensor", "binary_sensor")
_JH_SOUND_CONTROL = ("light", "switch", "lock", "cover", "climate", "fan", "media_player")


def _jh_sound_many(hass, name_fragment, resolved, domains=None):
    """Two or more devices that sound like these words, when normal matching did not find the name. Never guess between them."""
    frag = _jh_frag(name_fragment)
    if not frag or (resolved and _jh_word_in(frag, str(resolved[1]).lower().replace("colour", "color"))):   # jh-r18-word
        return []
    hits = _jh_sound_hits(hass, frag, domains or _JH_SOUND_DOMAINS)
    return hits if len(hits) > 1 else []


# Jarvis Home patch (2026-11-14): jh-heaters-exposed. Home Assistant's own "Expose to Assist" and hidden/disabled flags decide.
_JH_EXPOSE_NOTED = []


def _jh_exposed(hass, eid) -> bool:
    """Whether Home Assistant exposes this entity to Assist (conversation). When that cannot be read, every entity counts as
    exposed, as before this check existed, and that is logged once."""
    try:
        from homeassistant.components.homeassistant.exposed_entities import async_should_expose
        return bool(async_should_expose(hass, "conversation", eid))
    except Exception as exc:
        if not _JH_EXPOSE_NOTED:
            _JH_EXPOSE_NOTED.append(True)
            _LOGGER.debug("Expose to Assist could not be read, heaters are counted without it: %s", exc)
        return True


def _jh_shown(hass, eid) -> bool:
    """False for an entity whose registry entry is hidden or disabled. No registry entry: shown."""
    try:
        from homeassistant.helpers import entity_registry as er
        entry = er.async_get(hass).async_get(eid)
    except Exception:
        return True
    return entry is None or (getattr(entry, "hidden_by", None) is None and getattr(entry, "disabled_by", None) is None)


_JH_HEAT_WORDS = frozenset("heater heaters radiator radiators heating heat".split())
# Review round 3: heater words in the languages JARVIS answers in, for names and label names (the manual_heater label and the
# climate domain need no words at all).
_JH_HEATER_STEMS = ("heater", "radiator", "kachel", "verwarming", "heizung", "heizkörper", "heizlüfter", "chauffage",
                    "radiateur", "calefactor", "calefacción", "estufa", "radiador", "stufa", "termosifone", "riscaldamento",
                    "aquecedor", "ฮีตเตอร์", "เครื่องทำความร้อน")
_JH_HEAT_WORDS = _JH_HEAT_WORDS | frozenset(("kachel", "kachels", "verwarming", "heizung", "heizungen", "heizkörper", "chauffage",
                                             "radiateur", "radiateurs", "calefactor", "calefacción", "estufa", "radiador",
                                             "stufa", "ฮีตเตอร์"))


# jh-r17-word (step 159, review round 17 #4): a heater word counts only where a word starts, so "Home Theater" (home_theater) is
# no heater while "Hall Heater", "heaters" and "bedroom_heater" are. German and Dutch write compounds (Elektroheizung,
# vloerverwarming) and Thai has no spaces, so those stems may stand anywhere in a word.
_JH_HEATER_ANYWHERE = ("verwarming", "heizung", "heizkörper", "heizlüfter", "ฮีตเตอร์", "เครื่องทำความร้อน")
_JH_HEATER_RE = re.compile(
    r"(?<![^\W\d_])(?:" + "|".join(re.escape(w) for w in _JH_HEATER_STEMS if w not in _JH_HEATER_ANYWHERE) + r")"
    + r"|(?:" + "|".join(re.escape(w) for w in _JH_HEATER_ANYWHERE) + r")")


def _jh_heater_text(s) -> bool:
    """A heater word in an id, a name or a label (whole word or word start; compounds for German and Dutch)."""
    return bool(_JH_HEATER_RE.search(str(s or "").casefold()))


def _jh_heater_word(s) -> bool:
    s = str(s or "").casefold()
    return s in ("heating", "heaters") or _jh_heater_text(s)


def _jh_heat_state(state) -> str:
    """on, off or unavailable as a person means it: a climate device is on in every mode but off."""
    if state is None or state.state in ("unavailable", "unknown"):
        return "unavailable"
    if state.entity_id.startswith("climate."):
        return "off" if state.state == "off" else "on"
    return "on" if state.state == "on" else "off"


def _jh_generic_heater(hass, name_fragment, resolved):
    """The real heaters as (entity_id, name), when "the heater" was said as a generic word and matched something that is no
    heater (a plug called Heater that is not exposed to Assist, a hidden switch) or nothing. [] means: go on as before."""
    frag = re.sub(r"^(?:any|all|all of|all the|all of the|every|each|my|the)\s+", "", _jh_frag(name_fragment))
    if frag not in _JH_HEAT_WORDS:
        return []
    switches, climates = _jh_heaters(hass)
    heat = [(s.entity_id, s.attributes.get("friendly_name") or s.entity_id) for s in switches + climates]
    if not heat or (resolved and resolved[0] in {x[0] for x in heat}):
        return []
    return sorted(heat, key=lambda x: x[1].lower())


def _jh_heaters_answer(hass, heat, h="sir") -> str:
    """ "Hall Heater and Study Heater are on; Office Heater is off, sir." """
    groups = {"on": [], "heating": [], "standing by": [], "off": [], "unavailable": [], "on (no power reading)": []}
    for eid, name in heat:
        if eid.split(".")[0] in ("switch", "fan"):   # jh-manual-heater / jh-live-answers: heating or standing by from power
            key = _jh_manual_status(hass, eid)[0]
            if key == "no reading":
                key = "on (no power reading)" if _jh_is_manual(hass, eid) else "on"
            groups[key].append(name)
            continue
        groups[_jh_heat_state(hass.states.get(eid))].append(name)
    parts = []
    for key in ("heating", "on", "standing by", "on (no power reading)", "off", "unavailable"):
        names = groups[key]
        if names:
            said = names[0] if len(names) == 1 else _jh_t("jh_list", items=", ".join(names[:-1]), last=names[-1])
            _jh_word = _jh_t("jh_heat_" + re.sub(r"\W+", "_", key).strip("_")) or key
            parts.append(_jh_t("jh_hstate_" + ("one" if len(names) == 1 else "many"), names=said, state=_jh_word))
    text = "; ".join(parts)
    return f"{text[:1].upper()}{text[1:]}, {h}."


# Jarvis Home patch (2026-11-15): jh-manual-heater. A plug labelled manual_heater feeds a heater that starts with its own
# button: JARVIS never switches it and reads heating / standing by from the plug's power.
_JH_MANUAL_LABEL = "manual_heater"
_JH_MANUAL_HEAT_W = 50.0


def _jh_manual_label_ids(hass) -> set:
    """Label ids whose id or name is manual_heater (spaces and dashes count as underscores). The plain id when the label
    registry cannot be read."""
    try:
        from homeassistant.helpers import label_registry as lr
        found = set()
        for lab in list(lr.async_get(hass).async_list_labels()):
            lid = str(getattr(lab, "label_id", "") or "")
            name = re.sub(r"[\s-]+", "_", str(getattr(lab, "name", "") or "").strip().casefold())
            if _JH_MANUAL_LABEL in (lid.casefold(), name):
                found.add(lid)
        return found or {_JH_MANUAL_LABEL}
    except Exception:
        return {_JH_MANUAL_LABEL}


# jh-residents (step 158, the live audit C2): a person labelled not_resident (a guest login, JARVIS's own person) is no
# resident: the briefings, the day summary and the home status leave them out, as a residents-home template sensor would.
_JH_NOT_RESIDENT = "not_resident"


def _jh_not_resident_ids(hass) -> set:
    """Label ids whose id or name is not_resident (spaces and dashes count as underscores); the plain id when the label registry
    cannot be read."""
    try:
        from homeassistant.helpers import label_registry as lr
        found = set()
        for lab in list(lr.async_get(hass).async_list_labels()):
            lid = str(getattr(lab, "label_id", "") or "")
            name = re.sub(r"[\s-]+", "_", str(getattr(lab, "name", "") or "").strip().casefold())
            if _JH_NOT_RESIDENT in (lid.casefold(), name):
                found.add(lid)
        return found | {_JH_NOT_RESIDENT}
    except Exception:
        return {_JH_NOT_RESIDENT}


def jh_residents(hass, persons) -> list:
    """The person states that count as residents: every person whose entity does not carry the not_resident label. Read live;
    when the registry cannot be read, everyone (as before)."""
    persons = list(persons or [])
    try:
        from homeassistant.helpers import entity_registry as er
        reg, ids = er.async_get(hass), _jh_not_resident_ids(hass)
        out = []
        for p in persons:
            entry = reg.async_get(p.entity_id)
            if entry is not None and set(getattr(entry, "labels", None) or ()) & ids:
                continue
            out.append(p)
        return out
    except Exception:
        return persons


def _jh_wrapped_source(hass, entry):
    """The entity a switch_as_x wrapper (a plug shown as a fan, light, ...) switches, or None."""
    if entry is None or getattr(entry, "platform", None) != "switch_as_x":
        return None
    try:
        from homeassistant.helpers import entity_registry as er
        reg = er.async_get(hass)
        ce = hass.config_entries.async_get_entry(getattr(entry, "config_entry_id", None)) if getattr(entry, "config_entry_id", None) else None
        src = (getattr(ce, "options", None) or {}).get("entity_id") if ce is not None else None
        if src:
            try:
                return er.async_resolve_entity_id(reg, src) or src
            except Exception:
                return src
        if getattr(entry, "device_id", None):   # else the hidden switch on the same device
            for other in er.async_entries_for_device(reg, entry.device_id, include_disabled_entities=True):
                if other.entity_id != entry.entity_id and other.entity_id.startswith("switch.") and getattr(other, "hidden_by", None):
                    return other.entity_id
    except Exception:
        return None
    return None


def _jh_idents(dev) -> set:
    """jh-r17-twin (step 159): a device's identifiers as (integration, value) pairs whose value has at least 8 characters (a
    short value such as "12" is no proof that two devices are one)."""
    out = set()
    for ident in (getattr(dev, "identifiers", None) or ()):
        try:
            dom, val = str(tuple(ident)[0]), str(tuple(ident)[1])
        except Exception:
            continue
        if len(val) >= 8:
            out.add((dom, val))
    return out


def _jh_device_twins(hass, dreg=None, devices=None) -> dict:
    """jh-r17-twin (step 159, review round 17 #1): the ONE twin rule, used by the heater label (_JhHeaterLabels) and by the
    manual_heater check (_jh_is_manual) alike. Device id -> the ids of its twins: devices of ANOTHER integration that share an
    identifier value of at least 8 characters (the Tuya Local device and the Tuya cloud device of one plug share the Tuya device
    id, and the owner may put a label on either). Devices without a twin are left out; {} when the registry cannot be read."""
    try:
        if devices is None:
            if dreg is None:
                from homeassistant.helpers import device_registry as dr
                dreg = dr.async_get(hass)
            devices = dict(getattr(dreg, "devices", None) or {})
        by_val = {}
        for did, dev in devices.items():
            for dom, val in _jh_idents(dev):
                by_val.setdefault(val, set()).add((dom, did))
        out = {}
        for owners in by_val.values():
            for dom, did in owners:
                twins = {o for d, o in owners if d != dom and o != did}
                if twins:
                    out.setdefault(did, set()).update(twins)
        return out
    except Exception:
        return {}


def _jh_is_manual(hass, eid, _ids=None, _depth=0, _twins=None) -> bool:
    """True for a switch or fan whose entity or device carries the manual_heater label, and for a switch_as_x wrapper of any
    domain (fan, light, ...) whose source switch carries it (review 2026-11-15: the owner labels the plug, HA hides it and
    voice, the AI and Assist use the wrapper). A plain light on a labelled device (a backlight) is no heater."""
    if not isinstance(eid, str):
        return False
    try:
        from homeassistant.helpers import device_registry as dr, entity_registry as er
        entry = er.async_get(hass).async_get(eid)
        if entry is None:
            return False
        ids = _ids if _ids is not None else _jh_manual_label_ids(hass)
        # review round 3 (jh-r3-label-on-wrapper): the label on a switch_as_x wrapper of any domain counts too (the owner labels
        # the entity HA shows, the light, while the plug is hidden); a device label alone still makes no plain light a heater
        # review round 6: the domain from the registry entry (a device action may name the entity by its registry id)
        _jh_dom = str(getattr(entry, "entity_id", "") or eid).split(".")[0]
        if _jh_dom in ("switch", "fan") or getattr(entry, "platform", None) == "switch_as_x":
            found = set(getattr(entry, "labels", None) or ())
            if getattr(entry, "device_id", None):
                _jh_dreg = dr.async_get(hass)
                device = _jh_dreg.async_get(entry.device_id)
                found |= set(getattr(device, "labels", None) or ())
                # jh-r17-twin (step 159, review round 17 #1): the label on the device's twin counts too (the owner labels the Tuya
                # cloud device, Home Assistant shows the Tuya Local plug): the same twin rule as the heater label
                twins = _twins if _twins is not None else _jh_device_twins(hass, _jh_dreg)
                for twin in twins.get(entry.device_id, ()):
                    found |= set(getattr(_jh_dreg.async_get(twin), "labels", None) or ())
            if found & ids:
                return True
        src = _jh_wrapped_source(hass, entry)
        if src and src != eid and _depth < 2:
            return _jh_is_manual(hass, src, ids, _depth + 1, _twins)
        return False
    except Exception:
        return False


_JH_WRAP_DOMAINS = ("switch", "fan", "light", "cover", "lock", "siren", "valve", "climate", "input_boolean")


def _jh_manual_states(hass) -> list:
    """The manual heaters, read live (hidden or disabled ones left out): labelled switches and fans, and switch_as_x wrappers of
    any domain whose plug is labelled (review round 2)."""
    ids, twins = _jh_manual_label_ids(hass), _jh_device_twins(hass)   # jh-r17-twin: one twin map for the whole sweep
    out = []
    for dom in _JH_WRAP_DOMAINS:
        out += [s for s in hass.states.async_all(dom) if _jh_is_manual(hass, s.entity_id, ids, _twins=twins) and _jh_shown(hass, s.entity_id)]
    return out


def _jh_plug_watts(hass, eid):
    """Watts from the plug's power sensor (same device, or named after the plug), or None."""
    for st in _jh_linked_sensors(hass, eid, "power"):   # jh-live-answers
        unit = str(st.attributes.get("unit_of_measurement") or "").lower()
        try:
            watts = float(st.state)
        except (TypeError, ValueError):
            continue
        if unit in ("w", "kw"):
            return watts * 1000 if unit == "kw" else watts
    return None
    try:
        from homeassistant.helpers import entity_registry as er
        reg = er.async_get(hass)
        entry = reg.async_get(eid)
        device_id = getattr(entry, "device_id", None)
        if not device_id:
            return None
        for other in list(reg.entities.values()):
            if getattr(other, "device_id", None) != device_id or getattr(other, "disabled_by", None) is not None:
                continue
            st = hass.states.get(getattr(other, "entity_id", ""))
            unit = str((st.attributes.get("unit_of_measurement") if st is not None else "") or "").lower()
            if st is None or st.attributes.get("device_class") != "power" or unit not in ("w", "kw"):
                continue
            try:
                watts = float(st.state)
            except (TypeError, ValueError):
                continue
            return watts * 1000 if unit == "kw" else watts
    except Exception:
        return None
    return None


def _jh_manual_status(hass, eid):
    """(heating | standing by | off | unavailable | no reading, watts or None) for a manual heater plug."""
    st = hass.states.get(eid)
    if st is None or st.state in ("unavailable", "unknown"):
        return "unavailable", None
    if st.state == "off":
        return "off", None
    watts = _jh_plug_watts(hass, eid)
    if watts is None:
        return "no reading", None
    return ("heating" if watts > _JH_MANUAL_HEAT_W else "standing by"), watts


def _jh_heating(hass, s) -> bool:
    """Is this heater switch or fan producing heat: a manual heater by its plug's power, any other by its on state."""
    if s.entity_id.split(".")[0] in ("switch", "fan"):   # jh-live-answers: any heater plug with a power reading
        key = _jh_manual_status(hass, s.entity_id)[0]
        if key != "no reading":
            return key == "heating"
    return s.state == "on"


def _jh_heater_said(hass, s) -> str:
    """A heater switch's state for the AI's facts: manual heaters by their plug's power."""
    manual = _jh_is_manual(hass, s.entity_id)
    if not manual and s.entity_id.split(".")[0] not in ("switch", "fan"):
        return s.state
    key, watts = _jh_manual_status(hass, s.entity_id)
    if not manual:   # jh-live-answers: a heater plug heats only above 50 W
        return {"heating": f"heating ({watts or 0:.0f} W)", "standing by": f"on, standing by ({watts or 0:.0f} W)"}.get(key, s.state)
    return {"heating": f"heating ({watts or 0:.0f} W, manual heater)", "standing by": f"standing by ({watts or 0:.0f} W, manual heater)",
            "off": "plug off, so it cannot heat (manual heater)", "unavailable": "unavailable (manual heater)",
            "no reading": "plug on, no power reading (manual heater)"}[key]


def _jh_manual_note(hass, eid, step=False) -> str:
    """What the AI tools and the seam say when asked to switch a manual heater. step: one step of a plan (review round 3: the
    other steps may have run, so it never says that nothing was changed)."""
    if step:
        st = hass.states.get(eid)
        name = st.attributes.get("friendly_name", eid) if st is not None else eid
        return (f"This step was not run: {name} is a manual heater, which JARVIS never switches on or off (it starts with its "
                f"own button). The other steps are not affected by this.")
    st = hass.states.get(eid)
    name = st.attributes.get("friendly_name", eid) if st is not None else eid
    return (f"Not switched: {name} is a manual heater. It starts with its own button and its plug stays on, so JARVIS never "
            f"switches it on or off. Right now it is {_jh_heater_said(hass, st) if st is not None else 'unavailable'}. "
            f"Nothing was changed. Tell the user so plainly.")


def _jh_manual_said(hass, eid, action, h="sir", lang=None) -> str:
    """The instant answer for a manual heater: its state for a question, the reason nothing is switched for a command."""
    from . import local_i18n
    lang = lang or _JH_LANG.get()   # jh-local-lang
    st = hass.states.get(eid)
    name = st.attributes.get("friendly_name", eid) if st is not None else eid
    key, watts = _jh_manual_status(hass, eid)
    slug = {"heating": "heating", "standing by": "standby", "off": "off", "unavailable": "unavailable",
            "no reading": "noreading"}[key]
    w = f"{watts:.0f}" if watts is not None else ""
    if action == "query_state":
        return local_i18n.t("jh_manual_q_" + slug, lang, name=name, w=w, h=h)
    return (local_i18n.t("jh_manual_cmd", lang, name=name, h=h) + " "
            + local_i18n.t("jh_manual_now_" + slug, lang, w=w))


def _jh_area_of(hass, eid) -> str:
    """The area name of an entity, or of its device."""
    try:
        from homeassistant.helpers import area_registry as ar, device_registry as dr, entity_registry as er
        entry = er.async_get(hass).async_get(eid)
        area_id = getattr(entry, "area_id", None)
        if not area_id and getattr(entry, "device_id", None):
            area_id = getattr(dr.async_get(hass).async_get(entry.device_id), "area_id", None)
        area = ar.async_get(hass).async_get_area(area_id) if area_id else None
        return str(getattr(area, "name", "") or "")
    except Exception:
        return ""


# Review round 3: Home Assistant's Assist tools that never switch anything; every other Hass* tool is checked. The domains a tool
# can act on, where its name says so (HassLightSet only reaches lights); an unknown acting tool is checked against every heater.
_JH_HASS_READ_ONLY = frozenset(("hassgetstate", "hassgetweather", "hassgetcurrentdate", "hassgetcurrenttime", "hassnevermind",
                                "hassrespond", "hassbroadcast", "hasslistadditem", "hasslistcompleteitem", "hassshoppinglistadditem",
                                "hassshoppinglistcompleteitem", "hassstarttimer", "hasscanceltimer", "hasscancelalltimers",
                                "hassincreasetimer", "hassdecreasetimer", "hasspausetimer", "hassunpausetimer", "hasstimerstatus",
                                "hassgettodoitems", "hassclimategettemperature"))
_JH_HASS_TOOL_DOMAINS = {"hasslightset": ("light",), "hasssetposition": ("cover", "valve"), "hassfansetspeed": ("fan",),
                         "hassclimatesettemperature": ("climate",), "hassvacuumstart": ("vacuum",),
                         "hassvacuumreturntobase": ("vacuum",), "hassmediapause": ("media_player",),
                         "hassmediaunpause": ("media_player",), "hassmedianext": ("media_player",),
                         "hassmediaprevious": ("media_player",), "hasssetvolume": ("media_player",),
                         "hasssetvolumerelative": ("media_player",), "hassmediasearchandplay": ("media_player",),
                         "hassmediaplayermute": ("media_player",), "hassmediaplayerunmute": ("media_player",),
                         "hasslawnmowerstartmowing": ("lawn_mower",), "hasslawnmowerdock": ("lawn_mower",)}


def _jh_norm_name(s) -> str:
    """A name as Home Assistant compares it: case, punctuation and extra spaces do not count."""
    return " ".join(re.sub(r"[^\w\s]", " ", str(s or "").casefold()).replace("_", " ").split())


def _jh_names_of(hass, s) -> set:
    """Every name Assist resolves to this entity: its friendly name, id, registry name and aliases, and its device's names."""
    names = {s.attributes.get("friendly_name"), s.entity_id, s.entity_id.split(".", 1)[1]}
    try:
        from homeassistant.helpers import device_registry as dr, entity_registry as er
        entry = er.async_get(hass).async_get(s.entity_id)
        if entry is not None:
            names |= {getattr(entry, "name", None), getattr(entry, "original_name", None)} | set(getattr(entry, "aliases", None) or ())
            dev = dr.async_get(hass).async_get(entry.device_id) if getattr(entry, "device_id", None) else None
            if dev is not None:
                names |= {getattr(dev, "name", None), getattr(dev, "name_by_user", None)}
    except Exception:
        pass
    return {_jh_norm_name(n) for n in names if n}


def _jh_area_names(hass, eid) -> set:
    """The names Assist resolves to this entity's area: the area's name and aliases, and its floor's name and aliases."""
    out = {_jh_norm_name(_jh_area_of(hass, eid))}
    try:
        from homeassistant.helpers import area_registry as ar, device_registry as dr, entity_registry as er
        entry = er.async_get(hass).async_get(eid)
        area_id = getattr(entry, "area_id", None)
        if not area_id and getattr(entry, "device_id", None):
            area_id = getattr(dr.async_get(hass).async_get(entry.device_id), "area_id", None)
        area = ar.async_get(hass).async_get_area(area_id) if area_id else None
        if area is not None:
            out |= {_jh_norm_name(a) for a in (getattr(area, "aliases", None) or ())}
            floor_id = getattr(area, "floor_id", None)
            if floor_id:
                from homeassistant.helpers import floor_registry as fr
                fl = fr.async_get(hass).async_get_floor(floor_id)
                if fl is not None:
                    out |= {_jh_norm_name(getattr(fl, "name", ""))} | {_jh_norm_name(a) for a in (getattr(fl, "aliases", None) or ())}
    except Exception:
        pass
    return {n for n in out if n}


def jh_manual_hass_tool(hass, tool_name, args):
    """A Home Assistant Assist tool that would switch a manual heater, by its name or alias, its room, floor or a whole domain:
    the reason as a JSON error, else None. Every acting Hass* tool is checked (HassTurnOn/Off, HassToggle, HassLightSet,
    HassSetPosition, ...), only the read-only ones are not (review round 3). An error here must block the tool (the caller fails
    closed)."""
    low = str(tool_name or "").lower()
    if not low.startswith("hass"):   # review round 7: Home Assistant offers each exposed script as a tool of its own
        _jh_sid = f"script.{tool_name}"
        if hass.states.get(_jh_sid) is not None and jh_call_manual(hass, "script", "turn_on", [_jh_sid]):
            import json as _json7
            return _json7.dumps({"error": "Not run: that script would switch a manual heater, which JARVIS never switches. "
                                          "Nothing was changed. Tell the user so plainly."})
        return None
    if low in _JH_HASS_READ_ONLY:
        return None
    manual = _jh_manual_states(hass)
    if manual or _jh_manual_all(hass, "homeassistant"):   # review round 6, 7: a scene, script or group that reaches one
        for dom in ("scene", "script", "group", "light", "switch", "fan", "cover", "lock", "valve"):
            manual += [g for g in hass.states.async_all(dom) if g not in manual and (dom in ("scene", "script") or isinstance(
                       g.attributes.get("entity_id"), (list, tuple))) and jh_call_manual(hass, dom, "turn_on", [g.entity_id])]
    if not manual:
        return None
    args = args if isinstance(args, dict) else {}
    said = _jh_norm_name(args.get("name"))
    dom = args.get("domain")
    doms = {dom} if isinstance(dom, str) else set(dom or ())
    implied = _JH_HASS_TOOL_DOMAINS.get(low)
    if implied:
        doms = (doms & set(implied)) if doms else set(implied)
        if not doms:
            return None
    if doms:
        manual = [s for s in manual if s.entity_id.split(".")[0] in doms]
    if said:
        hit = [s for s in manual if said in _jh_names_of(hass, s)]
    else:
        hit = list(manual)
    for key in ("area", "floor"):
        want = _jh_norm_name(args.get(key))
        if want:
            hit = [s for s in hit if want in _jh_area_names(hass, s.entity_id)]
    if not hit:
        return None
    import json as _json
    return _json.dumps({"error": " ".join(_jh_manual_note(hass, s.entity_id) for s in hit)
                        + " To switch other devices in that room, name them."})


# jh-r28-hass (step 171, review round 28 #6): every Assist tool of Home Assistant, classified. 'read': switches nothing;
# 'group': its group form (no name, or an area / floor / domain / device class) goes through bulk_control, a named target is
# checked; 'named': a group form cannot open, unlock or heat anything (media, vacuum, mower, thermostat, humidifier). A tool that
# is not in the table and not read-only counts as 'group' (fails closed).
JH_HASS_TOOL_CLASS = {
    "hassturnon": "group", "hassturnoff": "group", "hasstoggle": "group", "hasssetposition": "group", "hasslightset": "group",
    "hassopencover": "group", "hassclosecover": "group", "hassfansetspeed": "group",
    "hassmediapause": "named", "hassmediaunpause": "named", "hassmedianext": "named", "hassmediaprevious": "named",
    "hasssetvolume": "named", "hasssetvolumerelative": "named", "hassmediasearchandplay": "named",
    "hassmediaplayermute": "named", "hassmediaplayerunmute": "named", "hassvacuumstart": "named",
    "hassvacuumreturntobase": "named", "hasslawnmowerstartmowing": "named", "hasslawnmowerdock": "named",
    "hassclimatesettemperature": "named", "hasshumidifiersetpoint": "named", "hasshumidifiermode": "named",
    "hassshoppinglistlastitems": "read",
}
_JH_HASS_GROUP_KEYS = ("area", "floor", "domain", "device_class")


def jh_hass_tool_class(tool_name) -> str:
    """jh-r28-hass (step 171): 'script' (an exposed script or another tool that is no Hass* intent), 'read', 'group' or
    'named' (JH_HASS_TOOL_CLASS); an unknown acting Hass* tool is 'group'."""
    low = str(tool_name or "").lower()
    if not low.startswith("hass"):
        return "script"
    if low in _JH_HASS_READ_ONLY:
        return "read"
    return JH_HASS_TOOL_CLASS.get(low, "group")


def _jh_hass_named(hass, tool_name, args, said) -> list:
    """jh-r28-hass: the entities an Assist tool's name reaches, as Home Assistant matches it (the name or an alias of the entity
    or its device; the domain, device class, area and floor narrow it; exposed to Assist only)."""
    low = str(tool_name or "").lower()
    dom = args.get("domain")
    doms = {dom} if isinstance(dom, str) else set(dom or ())
    implied = _JH_HASS_TOOL_DOMAINS.get(low)
    if implied:
        doms = (doms & set(implied)) if doms else set(implied)
    dcs = args.get("device_class")
    dcs = {dcs} if isinstance(dcs, str) else set(dcs or ())
    out = []
    for s in hass.states.async_all():
        eid = s.entity_id
        if doms and eid.split(".", 1)[0] not in doms:
            continue
        if dcs and str(s.attributes.get("device_class") or "") not in dcs:
            continue
        if said not in _jh_names_of(hass, s) or not _jh_exposed(hass, eid):
            continue
        wants = {_jh_norm_name(args.get(k)) for k in ("area", "floor")} - {""}
        if wants and not wants <= _jh_area_names(hass, eid):   # the room and floor as said: whole names (a set)
            continue
        out.append(eid)
    return out


def jh_hass_tool_guard(hass, tool_name, args):
    """jh-r28-hass (step 171, review round 28 #6): THE check of a Home Assistant Assist tool before it runs: the reason as a JSON
    error, else None. The manual-heater check first (jh_manual_hass_tool); then for an acting Hass* tool: a group form goes
    through bulk_control (refused here), a named target must be exactly one device, no group entity, and no door, gate, lock,
    alarm or security device. An error here must block the tool (the caller fails closed)."""
    import json as _json28
    blocked = jh_manual_hass_tool(hass, tool_name, args)
    cls = jh_hass_tool_class(tool_name)
    if blocked or cls in ("script", "read", "named"):
        return blocked
    args = args if isinstance(args, dict) else {}
    said = _jh_norm_name(args.get("name"))
    if not said:
        return _json28.dumps({"error": "Not run: a group order through Home Assistant's own %s (a room, floor, domain or device "
                                       "class) is not used by JARVIS. Use bulk_control: it leaves out doors, gates, locks, "
                                       "alarms, heaters and devices of unknown kind and names them. Nothing was changed."
                                       % tool_name, "use": "bulk_control"})
    hits = _jh_hass_named(hass, tool_name, args, said)
    if len(hits) != 1:
        return _json28.dumps({"error": "Not run: '%s' %s. Use control_device with the one entity id, or bulk_control for a group. "
                                       "Nothing was changed." % (args.get("name"), "names no single device JARVIS can tell"
                                                                  if not hits else "fits more than one device"),
                              "use": "control_device"})
    eid = hits[0]
    st = hass.states.get(eid)
    if eid.split(".", 1)[0] == "group" or isinstance(getattr(st, "attributes", {}).get("entity_id"), (list, tuple)):
        return _json28.dumps({"error": "Not run: '%s' is a group of devices. Use bulk_control: it switches the members by the "
                                       "group rules and names what it leaves out. Nothing was changed." % args.get("name"),
                              "use": "bulk_control"})
    action = {"hassturnoff": "turn_off", "hasstoggle": "toggle", "hasssetposition": "open", "hassopencover": "open",
              "hassclosecover": "close"}.get(str(tool_name).lower(), "turn_on")
    why = jh_bulk_barrier(hass, eid, action, lock_up=False)
    if why in ("lock", "unlock", "barrier", "security", "alarm", "error"):
        return _json28.dumps({"error": "Not run through Home Assistant's own tool: '%s' is a door, gate, lock, alarm or security "
                                       "device. Use control_device (it asks the confirmation). Nothing was changed."
                                       % args.get("name"), "use": "control_device"})
    return None


def _jh_manual_all(hass, domain) -> set:
    """Review round 3: every manual heater a call on this domain can reach, hidden ones included (switch_as_x hides the labelled
    plug, but entity_id 'all', a label, a device or an area still reach it). 'homeassistant' reaches every domain."""
    ids, twins = _jh_manual_label_ids(hass), _jh_device_twins(hass)   # jh-r17-twin
    doms = _JH_WRAP_DOMAINS if domain == "homeassistant" else (domain,)
    out = set()
    for dom in doms:
        out |= {s.entity_id for s in hass.states.async_all(dom) if _jh_is_manual(hass, s.entity_id, ids, _twins=twins)}
    return out


def jh_scene_members(hass, ids, _depth=0) -> list:   # jh-r6-members
    """The entities a call on these ids reaches, so one that would switch a manual heater is refused like the heater itself:
    a scene's members (review round 2), a group's members (group.* or a light/switch/fan/... group helper, review round 6), a
    script's and an automation's entities (Home Assistant's own entities_in_script / entities_in_automation), each followed
    a few levels deep."""
    out = []
    for e in ids or []:
        if not isinstance(e, str):
            continue
        dom = e.split(".", 1)[0]
        members = []
        st = hass.states.get(e)
        if dom == "scene" or dom == "group" or (st is not None and isinstance(st.attributes.get("entity_id"), (list, tuple))
                                                and _jh_platform(hass, e) == "group"):
            members = (st.attributes.get("entity_id") if st is not None else None) or []
            members = [members] if isinstance(members, str) else list(members)
        elif dom in ("script", "automation"):
            try:
                if dom == "script":
                    from homeassistant.components.script import entities_in_script as _jh_eis
                else:
                    from homeassistant.components.automation import entities_in_automation as _jh_eis
                members = list(_jh_eis(hass, e) or [])
            except Exception:
                members = []
        members = [m for m in members if isinstance(m, str) and m != e]
        out += members
        if members and _depth < 3:
            out += jh_scene_members(hass, members, _depth + 1)
    return out


def _jh_platform(hass, eid):
    try:
        from homeassistant.helpers import entity_registry as er
        return getattr(er.async_get(hass).async_get(eid), "platform", None)
    except Exception:
        return None


def jh_reaches_manual(hass, ids) -> bool:
    """Review round 6: a group, script or automation (or scene) that would switch a manual heater."""
    return any(_jh_is_manual(hass, m) for m in jh_scene_members(hass, jh_norm_ids(ids)))


# Review round 4 (jh-r4-switching): every service on a device counts as switching, except these read-only ones; and entity ids
# are compared as Home Assistant reads them (comma-separated text split, spaces stripped, lower case).
_JH_READ_ONLY_SERVICES = frozenset(("reload", "update_entity", "get_forecasts", "get_forecast", "get_events", "get_items",
                                    "list_items", "snapshot", "camera_view", "play_stream", "record", "get_statistics"))


def jh_switching_service(domain, service) -> bool:
    return bool(service) and service not in _JH_READ_ONLY_SERVICES


def jh_norm_ids(value) -> list:
    """Entity ids as Home Assistant's service schema reads them: a list or comma-separated text, stripped, lower case."""
    vals = value.split(",") if isinstance(value, str) else list(value or [])
    return [v.strip().lower() for v in vals if isinstance(v, str) and v.strip()]


import contextvars as _jh_cv7
_JH_REACH_DEPTH = _jh_cv7.ContextVar("jarvis_reach_depth", default=0)


def _jh_attr_members(hass, eid) -> list:
    st = hass.states.get(eid)
    m = (st.attributes.get("entity_id") if st is not None else None) or []
    return [x for x in ([m] if isinstance(m, str) else list(m)) if isinstance(x, str) and x != eid]


def _jh_entity_config(hass, domain, eid):
    """A script's or automation's own configuration (raw_config), or None."""
    try:
        comp = hass.data.get(domain)
        ent = comp.get_entity(eid) if comp is not None and hasattr(comp, "get_entity") else None
        raw = getattr(ent, "raw_config", None)
        return raw if isinstance(raw, dict) else None
    except Exception:
        return None


def _jh_steps_manual(hass, domain, eid):
    """The manual heater a script's or automation's ACTION steps would switch (never its triggers or conditions)."""
    raw = _jh_entity_config(hass, domain, eid)
    steps = None
    if raw is not None:
        steps = raw.get("sequence") if domain == "script" else (raw.get("actions") or raw.get("action"))
    if steps is None:   # its configuration cannot be read: every entity it names counts (the safe side)
        try:
            if domain == "script":
                from homeassistant.components.script import entities_in_script as _jh_eis
            else:
                from homeassistant.components.automation import entities_in_automation as _jh_eis
            return next((m for m in (_jh_eis(hass, eid) or []) if _jh_is_manual(hass, m)), None)
        except Exception:
            return None
    tok = _JH_REACH_DEPTH.set(_JH_REACH_DEPTH.get() + 1)
    try:
        return eid if jh_manual_in_actions(hass, steps) else None
    finally:
        _JH_REACH_DEPTH.reset(tok)


def _jh_expands(hass, e) -> bool:
    """Review round 8 (jh-r8-authority): an id whose call can reach other entities (a scene, script, automation or group)."""
    if e.split(".", 1)[0] in ("scene", "script", "automation", "group"):
        return True
    return bool(_jh_attr_members(hass, e)) or _jh_platform(hass, e) == "group"


def jh_call_manual(hass, domain, service, ids, _depth=0):
    """Review round 7: the ONE check of whether a service call reaches a manual heater, used by every path (strip, seam,
    automation check, Assist tools). The manual heater's entity id, or None. It follows what really runs: the ids themselves;
    a scene's members when the scene is turned on (scene.turn_on/apply, homeassistant.turn_on/toggle); a group's members for
    a switching service on the group; a script's ACTION steps when it is run (script.turn_on/toggle, script.<name>,
    homeassistant.turn_on/toggle) and an automation's when it is triggered (automation.trigger). Stopping, disabling,
    reloading or reading never counts.
    Review round 8 (jh-r8-authority): the ONLY decision point; every other path asks it. entity_id 'all' is followed like the
    list it stands for (every entity of the domain; for homeassistant every manual heater), and when the nesting is too deep
    to follow while any manual heater exists, the id that could not be followed is the hit (fail closed)."""
    ids = list(jh_norm_ids(ids))
    run_script = False
    if domain == "script" and service not in ("turn_on", "turn_off", "toggle", "reload"):
        ids.append(f"script.{service}")
        run_script = True
    for e in ids:
        if e != "all" and _jh_is_manual(hass, e):
            return e
    if not jh_switching_service(domain, service):
        return None
    if "all" in ids:   # review round 8: 'all' reaches every entity of the domain, scenes, scripts and groups included
        ids = [e for e in ids if e != "all"]
        if domain == "homeassistant":
            _jh_every = sorted(_jh_manual_all(hass, "homeassistant"))
            if _jh_every:
                return _jh_every[0]
        else:
            _jh_dom_all = [st.entity_id for st in hass.states.async_all(domain)]
            _jh_direct = next((e for e in _jh_dom_all if _jh_is_manual(hass, e)), None)
            if _jh_direct:
                return _jh_direct
            ids += [e for e in _jh_dom_all if e not in ids]
    if _depth > 3 or _JH_REACH_DEPTH.get() > 3:   # review round 8: too deep to follow is never a pass
        if _jh_manual_all(hass, "homeassistant"):
            return next((e for e in ids if _jh_expands(hass, e)), None)
        return None
    starts = service in ("turn_on", "toggle", "apply")
    for e in ids:
        d = e.split(".", 1)[0]
        hit = None
        if d == "scene" and starts and domain in ("scene", "homeassistant"):
            for m in _jh_attr_members(hass, e):
                hit = hit or jh_call_manual(hass, m.split(".", 1)[0], "turn_on", [m], _depth + 1)
        elif (d == "group" or _jh_platform(hass, e) == "group" or (d not in ("scene", "script", "automation")
                                                                  and _jh_attr_members(hass, e))) and service != "reload":   # review round 8
            for m in _jh_attr_members(hass, e):
                hit = hit or jh_call_manual(hass, m.split(".", 1)[0], service, [m], _depth + 1)
        elif d == "script" and (run_script or (starts and domain in ("script", "homeassistant"))):
            hit = _jh_steps_manual(hass, "script", e)
        elif d == "automation" and domain == "automation" and service == "trigger":
            hit = _jh_steps_manual(hass, "automation", e)
        if hit:
            return hit
    return None


_JH_TARGET_KEYS = ("entity_id", "device_id", "area_id", "floor_id", "label_id")   # review round 8: what picks the devices


def jh_manual_in_actions(hass, actions):
    """Review round 5: the reason when automation actions (any nesting: choose, if/then/else, sequence, parallel, repeat)
    would switch a manual heater, else None. Each service step goes through jh_strip_manual; a device action or a scene step
    is checked by its entity."""
    seen = []

    def walk(node):
        if isinstance(node, list):
            for x in node:
                r = walk(x)
                if r:
                    return r
            return None
        if not isinstance(node, dict):
            return None
        svc = node.get("action") or node.get("service")
        if (svc is not None or node.get("device_id") or node.get("scene") is not None) and _jh_manual_all(hass, "homeassistant"):   # review round 7: fail closed
            _jh_rd, _jh_rt = node.get("data") or node.get("service_data") or {}, node.get("target") or {}
            if not isinstance(_jh_rd, dict) or not isinstance(_jh_rt, dict):
                return "its data or target could not be checked for a manual heater"
            # review round 8 (jh-r8-templates): only a template that picks WHAT is switched counts (the action, a target or
            # entity field); a templated message, title or brightness never does
            _jh_all = " ".join(str(x) for x in [svc, node.get("scene")] + [h.get(k) for h in (node, _jh_rd, _jh_rt)
                                                                          for k in _JH_TARGET_KEYS])
            if "{{" in _jh_all or "{%" in _jh_all:
                return "a template could switch a manual heater; name the devices instead"
        if isinstance(svc, str) and "." in svc:
            domain, service = svc.split(".", 1)
            _jh_rd8 = node.get("data") or node.get("service_data") or {}
            data = dict(_jh_rd8) if isinstance(_jh_rd8, dict) else {}
            target = dict(node.get("target")) if isinstance(node.get("target"), dict) else {}
            if node.get("entity_id") and "entity_id" not in target:
                target["entity_id"] = node["entity_id"]
            want = jh_norm_ids(data.get("entity_id")) + jh_norm_ids(target.get("entity_id"))
            _d, _t, skip = jh_strip_manual(hass, domain, service, data, target)
            kept = jh_norm_ids((_d or {}).get("entity_id")) + jh_norm_ids((_t or {}).get("entity_id") if isinstance(_t, dict) else None)
            if skip:
                return skip
            if want and "all" not in want and len(kept) < len(want):
                return "it would switch a manual heater"
            if "all" in want and _jh_manual_all(hass, domain):   # review round 6: the stored action keeps "all"
                return "entity_id all would switch a manual heater; name the devices instead"
        if isinstance(node.get("scene"), str) and jh_call_manual(hass, "scene", "turn_on", [node["scene"]]):   # review round 8: the shared check
            return "that scene would switch a manual heater"
        # review round 6: only a device ACTION switches (not a condition or trigger; is_on / is_off only read). Its entity may
        # be the registry id, which _jh_is_manual resolves.
        if (node.get("device_id") and node.get("entity_id") and "condition" not in node and "platform" not in node
                and "trigger" not in node and not str(node.get("type") or "").startswith(("is_", "changed_states"))
                and jh_call_manual(hass, "homeassistant", "turn_on", ([node["entity_id"]] if isinstance(node["entity_id"], str) and "." not in node["entity_id"]
                                                        else jh_norm_ids(node.get("entity_id"))))):   # review round 8: the shared check
            return "it would switch a manual heater"
        for k, v in node.items():
            if isinstance(v, (list, dict)) and k not in ("data", "target", "service_data"):
                r = walk(v)
                if r:
                    return r
        return None
    return walk(actions)


def _jh_scene_manual_reply(hass, eid, fname, h, lang):
    """Review round 5: a scene that would switch a manual heater is not run, and the reply says why (no other action in its
    place)."""
    _jh_hit8 = jh_call_manual(hass, eid.split(".", 1)[0], "turn_on", [eid])   # review round 8: the shared check decides
    if not _jh_hit8:
        return None
    # only the names come from the members (a scene's, or a script's entities); the decision above is the shared check's
    heaters = [_jh_hit8] if _jh_is_manual(hass, _jh_hit8) else [m for m in jh_scene_members(hass, [eid]) if _jh_is_manual(hass, m)]
    names = ", ".join(str(getattr(hass.states.get(m), "attributes", {}).get("friendly_name") or m) for m in heaters) or (
        "Manuelle Heizung" if str(lang or "").startswith("de") else "a manual heater")
    if str(lang or "").startswith("de"):
        return f"{fname} wurde nicht ausgeführt: die Szene würde {names} schalten, eine manuelle Heizung, die JARVIS nie schaltet."
    return (f"{fname} was not run, {h}: it would switch {names}, a manual heater that JARVIS never switches. Remove it from the "
            f"scene, or switch the other devices by name." if h else
            f"{fname} was not run: it would switch {names}, a manual heater that JARVIS never switches.")


def jh_strip_manual(hass, domain, service, data, target):
    """For a switching service call from a JARVIS routine: (data, target, skipped) with the manual heaters taken out of an
    explicit entity list or 'all'. skipped is a reason when the call can only be made by switching a manual heater (it then
    must not run); None otherwise."""
    if not jh_switching_service(domain, service):   # review round 4: open_cover, unlock, set_valve_position, ... switch too
        return data, target, None
    manual = _jh_manual_states(hass)   # (review round 2: no early return; a scene or a hidden plug named directly still counts)
    data, target = dict(data or {}), (dict(target) if isinstance(target, dict) else target)
    for _jh_hd in (data, target if isinstance(target, dict) else {}):   # review round 4: checked as Home Assistant reads them
        if _jh_hd.get("entity_id") not in (None, "", "all") and not (isinstance(_jh_hd.get("entity_id"), str) and _jh_hd["entity_id"].strip().lower() == "all"):
            _jh_hd["entity_id"] = jh_norm_ids(_jh_hd["entity_id"])
        elif isinstance(_jh_hd.get("entity_id"), str) and _jh_hd["entity_id"].strip().lower() == "all":
            _jh_hd["entity_id"] = "all"
    if domain == "scene" and service in ("apply", "create"):   # review round 4: the entities a scene.apply / create sets
        _jh_set = [str(k).strip().lower() for k in ((data.get("entities") or {}) if isinstance(data.get("entities"), dict) else {})]
        _jh_set += jh_norm_ids(data.get("snapshot_entities"))
        if jh_call_manual(hass, "homeassistant", "turn_on", _jh_set):   # review round 8: the shared check
            return data, target, "that scene would switch a manual heater"

    def ids(v):
        return [v] if isinstance(v, str) else list(v or [])
    man = _jh_manual_all(hass, domain)   # review round 3: hidden plugs too (entity_id all, a label, a device, an area)
    # review round 2 (a scene that sets a manual heater) is now part of the shared check below
    _jh_reach = jh_norm_ids(data.get("entity_id")) + jh_norm_ids((target or {}).get("entity_id") if isinstance(target, dict) else None)
    if domain == "script" and service not in ("turn_on", "turn_off", "toggle", "reload"):
        _jh_reach.append(f"script.{service}")
    # review round 7: the one shared check. Review round 8 (jh-r8-strip): it runs on what would really be called after a
    # heater named directly is taken out ('all' expanded the same way); if any of that still reaches a manual heater (a
    # scene, group, script or automation, or a nesting too deep to follow), the whole call is refused, never run in part.
    _jh_direct8 = {e for e in _jh_reach if e != "all" and _jh_is_manual(hass, e)} | set(man)
    _jh_rest8 = [e for e in _jh_reach if e != "all" and e not in _jh_direct8]
    if "all" in _jh_reach:
        if domain == "homeassistant":
            _jh_rest8.append("all")
        else:
            _jh_rest8 += [st.entity_id for st in hass.states.async_all(domain)
                          if st.entity_id not in _jh_direct8 and not _jh_is_manual(hass, st.entity_id)]
    _jh_hit7 = jh_call_manual(hass, domain, service, _jh_rest8)
    if _jh_hit7:
        return data, target, ("a homeassistant call on all entities would switch the manual heaters" if "all" in _jh_rest8 else
                              "it would switch a manual heater (through a scene, group, script or automation); nothing of it was run")
    for holder in (data, target if isinstance(target, dict) else {}):   # a hidden plug named directly counts too
        man |= {e for e in ([holder.get("entity_id")] if isinstance(holder.get("entity_id"), str) else list(holder.get("entity_id") or []))
                if isinstance(e, str) and e != "all" and _jh_is_manual(hass, e)}
    if not man:
        return data, target, None
    for holder in (data, target if isinstance(target, dict) else {}):
        if "entity_id" not in holder:
            continue
        wanted = ids(holder["entity_id"])
        if "all" in wanted:
            if domain == "homeassistant":
                return data, target, "a homeassistant call on all entities would switch the manual heaters"
            wanted = [s.entity_id for s in hass.states.async_all(domain)]
        keep = [e for e in wanted if e not in man]
        if not keep:
            return data, target, "it only targets manual heaters"
        holder["entity_id"] = keep
    if any(isinstance(_jh_hd, dict) and any(_jh_hd.get(k) for k in ("area_id", "device_id", "label_id", "floor_id"))
           for _jh_hd in (data, target)):   # review round 3: in the call data as well as in its target
        return data, target, "a room, device or label target could switch a manual heater; name the devices instead"
    return data, target, None


# Jarvis Home patch (2026-11-15): jh-local-lang. The reply language of this request, for the kit's own instant replies.
import contextvars as _jh_contextvars
_JH_LANG = _jh_contextvars.ContextVar("jarvis_local_lang", default="en")


def _jh_t(key, **kw) -> str:
    """A kit reply in the language of this request, from the local_i18n catalogue ('' for an unknown key)."""
    from . import local_i18n
    return local_i18n.t(key, _JH_LANG.get(), **kw)


# Jarvis Home patch (2026-11-15): jh-live-answers. Instant answers found missing in a live chat test.
def _jh_linked_sensors(hass, eid, device_class):
    """Sensors of one device class that belong to an entity: on its device (not disabled), else the sensors whose name is
    the entity's name plus more words (a plug that the integration shows as two devices)."""
    out = []
    try:
        from homeassistant.helpers import entity_registry as er
        reg = er.async_get(hass)
        entry = reg.async_get(eid)
        device_id = getattr(entry, "device_id", None)
        if device_id:
            for other in list(reg.entities.values()):
                if getattr(other, "device_id", None) == device_id and getattr(other, "disabled_by", None) is None:
                    st = hass.states.get(getattr(other, "entity_id", ""))
                    if st is not None and st.attributes.get("device_class") == device_class:
                        out.append(st)
    except Exception:
        pass
    if out:
        return out
    st = hass.states.get(eid)
    name = str((st.attributes.get("friendly_name") if st is not None else "") or "").strip().casefold()
    if not name:
        return []
    return [s for s in hass.states.async_all("sensor") if s.attributes.get("device_class") == device_class
            and str(s.attributes.get("friendly_name") or "").casefold().startswith(name + " ")]


def jh_is_daily_meter(hass, s) -> bool:
    """Review round 8 (jh-r8-daily): a meter that counts from midnight, found by what it is in any language: a utility meter
    with a daily cycle (its meter_period, or its helper's cycle option), or a total that was last reset today."""
    a = s.attributes
    if str(a.get("meter_period") or "").strip().lower() == "daily":
        return True
    try:
        from homeassistant.helpers import entity_registry as er
        entry = er.async_get(hass).async_get(s.entity_id)
        ce = hass.config_entries.async_get_entry(entry.config_entry_id) if getattr(entry, "config_entry_id", None) else None
        if ce is not None and str((getattr(ce, "options", None) or {}).get("cycle") or "").strip().lower() == "daily":
            return True
    except Exception:
        pass
    if a.get("last_reset") and a.get("state_class") == "total":
        try:
            from homeassistant.util import dt as dt_util
            when = dt_util.parse_datetime(str(a.get("last_reset")))
            # review round 13 (jh-r13-midnight): only a reset at local midnight counts from midnight; Home Assistant's own Energy
            # cost sensor resets at every restart (15:00 after an update), which is no "today"
            loc = dt_util.as_local(when) if when is not None else None
            if loc is not None and loc.date() == dt_util.now().date() and loc.hour == 0 and loc.minute < 10:
                return True
        except Exception:
            pass
    return False


def _jh_daily_words(label) -> bool:
    """The English name words, only the last fallback for a device's own daily sensor that shows no daily reset at all."""
    return "today" in label or "daily" in label


def _jh_energy_unit(s) -> bool:
    """An energy meter by its device class or its unit (Wh, kWh, MWh)."""
    return (s.attributes.get("device_class") == "energy"
            or str(s.attributes.get("unit_of_measurement") or "").strip().lower() in ("wh", "kwh", "mwh"))


def _jh_today_kwh(hass, eid):
    """Energy used today by a device, in kWh: its daily energy sensor, or None."""
    for st in _jh_linked_sensors(hass, eid, "energy"):
        label = f"{st.entity_id} {st.attributes.get('friendly_name') or ''}".casefold()
        if not (jh_is_daily_meter(hass, st) or _jh_daily_words(label)):   # review round 8: any language
            continue
        unit = str(st.attributes.get("unit_of_measurement") or "").casefold()
        try:
            value = float(st.state)
        except (TypeError, ValueError):
            continue
        return value / 1000 if unit == "wh" else value * 1000 if unit == "mwh" else value
    return None


def _jh_heater_energy_today(hass, h="sir"):
    switches, climates = _jh_heaters(hass)
    rows, missing = [], []
    for s in switches + climates:
        name = s.attributes.get("friendly_name") or s.entity_id
        kwh = _jh_today_kwh(hass, s.entity_id)
        (rows.append((name, kwh)) if kwh is not None else missing.append(name))
    if not rows:
        return None
    rows.sort(key=lambda r: -r[1])
    text = _jh_t("jh_heat_energy_today", total=f"{sum(r[1] for r in rows):.2f}", h=h,
                 items=", ".join(f"{n} {k:.2f} kWh" for n, k in rows))
    if missing:
        text += " " + _jh_t("jh_heat_energy_missing", names=", ".join(missing))
    return text


def _jh_named_entity(hass, frag):
    """The one entity called this (a trailing device word such as plug or lamp may be left off), unavailable ones included."""
    frag = re.sub(r"^(?:the|my|our)\s+", "", (frag or "").strip().casefold())
    m = re.fullmatch(r"(.+?)\s+(plug|socket|outlet|switch|lamp|light|camera|sensor|speaker|tv)", frag)
    base = m.group(1) if m else frag
    dom = {"plug": "switch", "socket": "switch", "outlet": "switch", "switch": "switch", "lamp": "light", "light": "light",
           "camera": "camera", "sensor": "sensor", "speaker": "media_player", "tv": "media_player"}.get(m.group(2)) if m else None
    found = [st for st in hass.states.async_all()
             if str(st.attributes.get("friendly_name") or "").casefold() in (frag, base)
             or st.entity_id.split(".", 1)[1].replace("_", " ") in (frag, base)]
    if dom:
        found = [s for s in found if s.entity_id.startswith(dom + ".")] or found
    found = [s for s in found if not _jh_is_setting(hass, s.entity_id)]
    shown = [s for s in found if _jh_exposed(hass, s.entity_id)] or found
    return shown[0] if len(shown) == 1 else None


def _jh_since(st) -> str:
    from . import local_i18n
    from homeassistant.util import dt as dt_util
    when = getattr(st, "last_changed", None)
    if when is None:
        return ""
    lang = _JH_LANG.get()
    try:
        local, now = dt_util.as_local(when), dt_util.now()
        if local.date() == now.date():
            return local_i18n.format_time(local, lang)
        return _jh_t("jh_when_day", date=local_i18n.format_date(local, lang), time=local_i18n.format_time(local, lang))
    except Exception:
        return ""   # no time to give: the answer just says offline


def _jh_online_text(hass, st, h="sir"):
    name = st.attributes.get("friendly_name") or st.entity_id
    if st.state in ("unavailable", "unknown"):
        since = _jh_since(st)
        return _jh_t("jh_offline_since", name=name, when=since, h=h) if since else _jh_t("jh_offline_now", name=name, h=h)
    return _jh_t("jh_online", name=name, h=h)


async def _jh_weather_today(hass) -> str:
    """ ' Today: high 6°, low 1°, 2.4 mm rain (80% chance).' from the first weather entity's daily forecast, or ''."""
    try:
        import asyncio as _jh_asyncio
        from homeassistant.util import dt as dt_util
        ws = [s.entity_id for s in hass.states.async_all("weather")]
        if not ws:
            return ""
        res = await _jh_asyncio.wait_for(hass.services.async_call(
            "weather", "get_forecasts", {"entity_id": ws[0], "type": "daily"}, blocking=True, return_response=True), 5)
        fc = ((res or {}).get(ws[0]) or {}).get("forecast") or []
        if not fc:
            return ""
        d = fc[0]
        day = dt_util.parse_datetime(str(d.get("datetime") or ""))
        if day is not None and dt_util.as_local(day).date() != dt_util.now().date():
            return ""
        def n(x):
            return f"{float(x):g}"
        parts = []
        if d.get("temperature") is not None and d.get("templow") is not None:
            parts.append(_jh_t("jh_wx_hilo", hi=n(d["temperature"]), lo=n(d["templow"])))
        elif d.get("temperature") is not None:
            parts.append(_jh_t("jh_wx_hi", hi=n(d["temperature"])))
        if d.get("precipitation") is not None:
            parts.append(_jh_t("jh_wx_rain", mm=n(d["precipitation"])))
        if d.get("precipitation_probability") is not None:
            parts.append(_jh_t("jh_wx_pct", pct=n(d["precipitation_probability"])))
        return (" " + _jh_t("jh_wx_today", parts=", ".join(parts))) if parts else ""
    except Exception:
        return ""


# Jarvis Home patch (2026-11-15): jh-round2. More instant answers from chat test round 2.
_JH_MONTHS = {m: i for i, m in enumerate(("january february march april may june july august september october november "
                                           "december").split(), 1)}
_JH_NAMED_DAYS = {"christmas": (12, 25), "christmas day": (12, 25), "christmas eve": (12, 24), "new year": (1, 1),
                  "new year's day": (1, 1), "new years day": (1, 1), "new year's eve": (12, 31), "new years eve": (12, 31),
                  "halloween": (10, 31), "valentine's day": (2, 14), "valentines day": (2, 14)}


def _jh_target_day(phrase, today):
    """The next date a phrase names ('christmas', 'december 25', '25 december', 'the 3rd'), or None."""
    import datetime as _jh_dt
    p = re.sub(r"^(?:the\s+)?", "", (phrase or "").strip().casefold())
    md = _JH_NAMED_DAYS.get(p)
    if md is None:
        m = re.fullmatch(r"([a-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?", p) or None
        if m and m.group(1) in _JH_MONTHS:
            md = (_JH_MONTHS[m.group(1)], int(m.group(2)))
        m = re.fullmatch(r"(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?([a-z]+)", p)
        if m and m.group(2) in _JH_MONTHS:
            md = (_JH_MONTHS[m.group(2)], int(m.group(1)))
    if md is None:
        return None
    try:
        day = _jh_dt.date(today.year, md[0], md[1])
        if day < today:
            day = _jh_dt.date(today.year + 1, md[0], md[1])
        return day
    except ValueError:
        return None


def _jh_today():
    import datetime as _jh_dt
    try:
        from homeassistant.util import dt as dt_util
        d = dt_util.now().date()
        if isinstance(d, _jh_dt.date):
            return d
    except Exception:
        pass
    return _jh_dt.date.today()


_JH_ABILITY = (("light", "jh_can_light"), ("climate", "jh_can_heat"), ("fan", "jh_can_fan"), ("switch", "jh_can_switch"),
               ("cover", "jh_can_cover"), ("lock", "jh_can_lock"), ("media_player", "jh_can_media"),
               ("scene", "jh_can_scene"), ("camera", "jh_can_camera"), ("weather", "jh_can_weather"),
               ("calendar", "jh_can_calendar"), ("person", "jh_can_person"))


def _jh_what_i_can(hass, h="sir"):
    """'What can you do?' from what Home Assistant exposes to Assist here, in plain speech."""
    doms = set()
    for st in hass.states.async_all():
        d = st.entity_id.split(".")[0]
        if d not in doms and not _jh_is_setting(hass, st.entity_id) and _jh_exposed(hass, st.entity_id):
            doms.add(d)
    for d in ("weather", "person", "calendar", "camera"):   # read without Assist exposure
        if hass.states.async_all(d):
            doms.add(d)
    parts = [_jh_t(key) for d, key in _JH_ABILITY if d in doms]
    if any(s.attributes.get("device_class") in ("power", "energy") for s in hass.states.async_all("sensor")):
        parts.append(_jh_t("jh_can_energy"))
    parts.append(_jh_t("jh_can_talk"))
    said = parts[0] if len(parts) == 1 else _jh_t("jh_list", items=", ".join(parts[:-1]), last=parts[-1])
    return _jh_t("jh_can_intro", h=h, items=said)


# Jarvis Home patch (2026-11-15): jh-open-items. Own actions today, how long something has been on, cost today.
def _jh_hhmm(ts) -> str:
    try:
        from homeassistant.util import dt as dt_util
        out = dt_util.as_local(dt_util.utc_from_timestamp(ts)).strftime("%H:%M")
        if isinstance(out, str):
            return out
    except Exception:
        pass
    return time.strftime("%H:%M", time.localtime(ts))


def _jh_day_start() -> float:
    """jh-r21-day (step 163, review round 21 #4): midnight in Home Assistant's time zone (the container's clock may be UTC)."""
    try:
        from homeassistant.util import dt as _jh_dtu
        v = _jh_dtu.start_of_local_day().timestamp()
        if isinstance(v, (int, float)):
            return float(v)
    except Exception:
        pass
    lt = time.localtime()
    return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))


def _jh_journal_text(hass, act, h="sir"):
    from . import actuation
    start = _jh_day_start()
    rows = []
    for ts, cap, eid in list(getattr(actuation, "_JH_JOURNAL", [])):
        service = cap.split(".", 1)[1] if "." in cap else cap
        if ts < start or (act and service != "turn_" + act):
            continue
        st = hass.states.get(eid)
        name = st.attributes.get("friendly_name", eid) if st is not None else eid
        word = {"turn_on": "on", "turn_off": "off"}.get(service, service.replace("_", " "))
        rows.append(f"{name} {_jh_t('jh_journal_' + word) or word} {_jh_hhmm(ts)}" if not act else f"{name} {_jh_hhmm(ts)}")
    since = getattr(actuation, "_JH_JOURNAL_SINCE", 0)
    note = (" " + _jh_t("jh_journal_since", time=_jh_hhmm(since))) if since > start else ""
    if not rows:
        return _jh_t("jh_journal_none", h=h) + note
    key = "jh_journal_list_" + (act or "any")
    return _jh_t(key, items=", ".join(rows[:12]) + (" ..." if len(rows) > 12 else ""), h=h) + note


def _jh_duration(seconds) -> str:
    s = max(0, int(seconds))
    hours, mins = s // 3600, (s % 3600) // 60
    if hours >= 48:
        return _jh_t("jh_dur_days", n=round(s / 86400))
    if hours:
        return _jh_t("jh_dur_hm", h=hours, m=mins) if mins else _jh_t("jh_dur_h", h=hours)
    return _jh_t("jh_dur_m", m=max(1, mins))


def _jh_how_long_text(hass, st, want, h="sir"):
    from homeassistant.util import dt as dt_util
    when = getattr(st, "last_changed", None)
    if when is None:
        return None
    try:
        secs = (dt_util.utcnow() - when).total_seconds()
        if not isinstance(secs, (int, float)):
            return None
    except Exception:
        return None
    name = st.attributes.get("friendly_name") or st.entity_id
    is_on = st.state not in ("off", "closed", "locked", "unavailable", "unknown", "idle", "standby", "not_home")
    on_words = ("on", "open", "heating", "running", "home")
    now_word = st.state if st.state not in ("on", "off") else (_jh_t("jh_heat_" + st.state) or st.state)
    if (want in on_words) == is_on:
        return _jh_t("jh_how_long", name=name, state=now_word, dur=_jh_duration(secs), since=_jh_hhmm(time.time() - secs), h=h)
    return _jh_t("jh_how_long_not", name=name, state=now_word, since=_jh_hhmm(time.time() - secs), h=h)


def _jh_cost_sensors(hass, only=None):
    out = []
    pool = []
    if only is not None:
        for eid in only:
            pool += _jh_linked_sensors(hass, eid, "monetary")
    else:
        pool = [s for s in hass.states.async_all("sensor") if s.attributes.get("device_class") == "monetary"]
    for s in pool:
        label = f"{s.entity_id} {s.attributes.get('friendly_name') or ''}".casefold()
        # review round 8 (jh-r8-daily): a daily money meter in any language; the English words only as the fallback. Money that
        # comes back (export compensation) is not a cost.
        if jh_is_daily_meter(hass, s):
            if _JH_PRODUCED.search(label) or str(_jh_meter_source(hass, s) or "").endswith("_compensation"):
                continue
        elif "cost" not in label or not _jh_daily_words(label):
            continue
        try:
            out.append((s, float(s.state)))
        except (TypeError, ValueError):
            continue
    return out


async def _jh_cost_text(hass, t, h="sir"):
    only = None
    if re.search(r"\bheat(?:er|ers|ing)\b", t):
        switches, climates = _jh_heaters(hass)
        only = [s.entity_id for s in switches + climates]
    elif re.search(r"\b(?:it|that|this)\b", t) and _ctx_fresh() and getattr(_CTX, "last_entity", None):
        only = [_CTX.last_entity]
    rows = _jh_cost_sensors(hass, only)
    if not rows:
        return None
    units = {}   # review round 3: one currency (the most used), and the whole-house cost is the total, never added to its parts
    for s, _v in rows:
        u = str(s.attributes.get("unit_of_measurement") or "")
        units[u] = units.get(u, 0) + 1
    unit = max(units, key=units.get)
    rows = [(s, v) for s, v in rows if str(s.attributes.get("unit_of_measurement") or "") == unit]
    total = sum(v for _s, v in rows)
    if only is None:
        _jh_c = jh_today_cost(hass, await jh_energy_grid_stats(hass), await jh_energy_grid_costs(hass))   # review round 7
        if _jh_c.get("total") is not None and _jh_c.get("unit") == unit:
            total = _jh_c["total"]
    items = ", ".join(f"{s.attributes.get('friendly_name') or s.entity_id} {v:.2f}" for s, v in sorted(rows, key=lambda r: -r[1])[:8])
    return _jh_t("jh_cost_today", total=f"{total:.2f}", unit=unit, items=items, h=h)


async def _jh_round2_answer(hass, t, h="sir"):
    """Days until a date, a lock that does not exist, what JARVIS can do. t: lower case, no punctuation."""
    m = re.fullmatch(r"how many (?:more )?(?:days|sleeps) (?:are there |is it |left )?(?:until|till|til|to|before) (.+?)"
                     r"(?: from (?:now|today))?", t)
    if m:
        today = _jh_today()
        day = _jh_target_day(m.group(1), today)
        if day is not None:
            n = (day - today).days
            return LocalResult(text=_jh_t("jh_days_until_" + ("one" if n == 1 else "many"), n=n, what=m.group(1).strip(),
                                          date=day.isoformat(), h=h), success=True)
    m = re.fullmatch(r"(?:please )?(un)?lock (?:the )?(.+?)(?: please| now)?", t)
    if m:
        frag = m.group(2).strip()
        _jh_cut = _jh_tail_at(frag, hass)   # jh-r19-tail (step 161, review round 19 #2): "unlock the back door soon"
        if _jh_cut >= 0:
            frag = frag[:_jh_cut].strip()   # "no lock" only when the words WITHOUT the tail name no lock; else the guard
        locks = list(hass.states.async_all("lock"))
        words = [w for w in frag.split() if w not in ("door", "the", "lock")]
        fit = [s for s in locks if all(_jh_word_in(w, str(s.attributes.get("friendly_name") or s.entity_id).casefold()) for w in words)]   # jh-r18-word
        plural = re.match(r"(?:all|every|both|each)\b", frag) or frag.endswith(("doors", "locks"))   # a bulk order: not here
        # jh-r26-lockup (step 168, review round 26 #10): "lock up", "lock the house", "lock everything" are lock orders (the lock
        # row takes them); "lock it" / "lock that" points back (the context or the AI): never "no lock on the up / it"
        if re.fullmatch(_JH_BULK_LOCKUP + r"|(?:it|that|this|them|those)(?:\s+up)?", frag):
            return None
        # jh-r20-tail (step 162, review round 20 #2): never "no lock" while a tail is present (the guard answers: online the AI,
        # offline the honest line) or when the words hold a real lock's name ("the front door for 30 minutes")
        if (not fit and not plural and _jh_cut < 0 and not _jh_lock_said(locks, m.group(2))
                and _jh_named_entity(hass, frag) is None):   # a same-named device keeps its own line
            return LocalResult(text=_jh_t("jh_no_lock", what=frag, h=h), success=False)
    m = re.fullmatch(r"(?:did|have) you (?:turn(?:ed)?|switch(?:ed)?) (?:anything|something|any devices?|any lights?) (on|off)(?: today)?"
                     r"|what (?:did|have) you (?:turn(?:ed)?|switch(?:ed)?) (on|off)(?: today)?|(?:did|have) you (?:do|done) anything today"
                     r"|what (?:did|have) you (?:do|done) today", t)
    if m:   # jh-open-items: from JARVIS's own journal
        return LocalResult(text=_jh_journal_text(hass, m.group(1) or m.group(2), h), success=True)
    m = re.fullmatch(r"how long (?:has|have|is|are) (?:the )?(.+?) (?:been )?(on|off|open|closed|heating|running|home|away)(?: for)?(?: now)?", t)
    if m:
        frag = m.group(1).strip()
        st = (hass.states.get(_CTX.last_entity) if frag in ("it", "that", "this") and _ctx_fresh() and getattr(_CTX, "last_entity", None)
              else _jh_named_entity(hass, frag) if frag not in ("it", "that", "this") else None)
        if st is not None and _jh_local_hidden(hass, st.entity_id):   # jh-r28-private (step 171): "how long has Robin been home"
            return LocalResult(text=_jh_private_reply(hass), success=False)
        if st is not None:
            out = _jh_how_long_text(hass, st, m.group(2), h)
            if out:
                return LocalResult(text=out, success=True)
    if re.search(r"\bcosts?\b", t) and re.search(r"\b(?:today|per day|a day|daily|so far)\b", t):
        out = await _jh_cost_text(hass, t, h)
        if out:
            return LocalResult(text=out, success=True)
    if re.fullmatch(r"(?:what (?:can|could) you do(?: for me)?|what are you able to do|what do you do|help|what can i ask you)", t):
        return LocalResult(text=_jh_what_i_can(hass, h), success=True)
    return None


async def _jh_live_answer(hass, text, h="sir"):
    """Heater state, online or offline, and heater energy today, straight from Home Assistant. None: not such a question."""
    t = re.sub(r"[?!.,;:]+", " ", (text or "").casefold())
    t = re.sub(r"\s+", " ", t).strip()
    t = re.sub(r"^(?:(?:hey|ok|okay) )?jarvis ", "", t)
    _jh_r2 = await _jh_round2_answer(hass, t, h)   # jh-round2
    if _jh_r2 is not None:
        return _jh_r2
    m = re.fullmatch(r"(?:is|are) (?:the )?(.+?) (?:actually |really |still )?(?:heating|warming up|warming)(?: (?:right )?now)?"
                     r"|what(?:'s| is) (?:the )?(.+?) doing(?: (?:right )?now)?", t)
    if m:
        frag = (m.group(1) or m.group(2) or "").strip()
        switches, climates = _jh_heaters(hass)
        heat = [(s.entity_id, s.attributes.get("friendly_name") or s.entity_id) for s in switches + climates]
        hit = [x for x in heat if x[1].casefold() in (frag, frag + " heater")
               or x[0].split(".", 1)[1].replace("_", " ") in (frag, frag + " heater")]
        if hit:
            return LocalResult(text=_jh_heaters_answer(hass, hit, h), success=True)
    m = re.fullmatch(r"(?:is|are) (?:the )?(.+?) (online|offline|available|unavailable|connected|disconnected|reachable)"
                     r"(?: (?:right )?now)?", t)
    if m:
        st = _jh_named_entity(hass, m.group(1))
        if st is not None:
            return LocalResult(text=_jh_online_text(hass, st, h), success=True)
    if (re.search(r"\bheat(?:er|ers|ing)\b", t) and re.search(r"\b(?:energy|kwh|electricity|power)\b", t)
            and re.search(r"\btoday\b", t) and re.search(r"\b(?:use|used|uses|consume|consumed|usage|consumption|draw|drew)\b", t)):
        out = _jh_heater_energy_today(hass, h)
        if out:
            return LocalResult(text=out, success=True)
    m = re.fullmatch(r"(?:what (?:do|can) you see|what(?:'s| is) (?:happening|going on)|look|show me what you see) "
                     r"(?:on|in|at|through) (?:the )?(.+?)(?: (?:right )?now)?", t)
    if m:
        cam = _jh_named_camera(hass, m.group(1))
        if cam is not None:   # the vision tool, through the same gates as when the AI calls it
            import json as _jh_json
            from .agent import _execute_tool
            try:
                out = _jh_json.loads(await _execute_tool(hass, "look_at_camera", {
                    "entity_id": cam.entity_id, "question": "What do you see? Describe it briefly."}, None, None))
            except Exception:
                out = {}
            if out.get("success") and out.get("answer"):
                return LocalResult(text=str(out["answer"]).strip(), success=True)
            return LocalResult(text=_jh_t("jh_cam_fail", name=cam.attributes.get("friendly_name") or cam.entity_id, h=h),
                               success=False)
    return None


def _jh_named_camera(hass, frag):
    """The one camera called this ('living room camera', 'the doorbell cam'), by its name or entity id."""
    frag = re.sub(r"^(?:the|my|our)\s+", "", (frag or "").strip().casefold())
    base = re.sub(r"\s+(?:camera|cam|webcam)$", "", frag)
    found = [st for st in hass.states.async_all("camera")
             if str(st.attributes.get("friendly_name") or "").casefold() in (frag, base, base + " camera", base + " cam")
             or st.entity_id.split(".", 1)[1].replace("_", " ") in (frag, base, base + " camera", base + " cam")]
    return found[0] if len(found) == 1 else None


class _JhHeaterLabels:
    """jh-heater-label (step 158, the live audit C1): which entities a Home Assistant heater label marks. A label means heater
    when its id or name holds a heater word in a reply language (Heater, Heating, Verwarming, ...); the manual_heater label is
    not one (those plugs always count, through _jh_is_manual). The label counts on the entity, on its device, on the source plug
    of a switch_as_x wrapper, and on the device's twins: a device of another integration with the same identifier (the Tuya
    Local device and the Tuya cloud device of one plug share the Tuya device id, at least 8 characters). in_use: some entity
    or device carries such a label; then the label decides and heater words in ids or names no longer count."""

    def __init__(self, hass):
        self.hass, self.ids, self.dev, self.in_use, self.ereg, self.dreg = hass, set(), {}, False, None, None
        try:
            from homeassistant.helpers import device_registry as dr, entity_registry as er
            manual = _jh_manual_label_ids(hass)
            try:
                from homeassistant.helpers import label_registry as lr
                for lab in list(lr.async_get(hass).async_list_labels()):
                    lid = str(getattr(lab, "label_id", "") or "")
                    if lid and lid not in manual and (_jh_heater_word(lid) or _jh_heater_word(str(getattr(lab, "name", "") or "").strip())):
                        self.ids.add(lid)
            except Exception:
                pass
            self.ereg, self.dreg = er.async_get(hass), dr.async_get(hass)
            devices = dict(getattr(self.dreg, "devices", None) or {})
            ents = list((getattr(self.ereg, "entities", None) or {}).values())
            seen = {x for o in list(devices.values()) + ents for x in (getattr(o, "labels", None) or ())}
            self.ids |= {x for x in seen if isinstance(x, str) and x not in manual and _jh_heater_word(x)}   # registry unreadable
            if not self.ids:
                return
            twins = _jh_device_twins(hass, devices=devices)   # jh-r17-twin: the ONE twin rule, shared with _jh_is_manual
            for did, dev in devices.items():
                labs = set(getattr(dev, "labels", None) or ())
                for twin in twins.get(did, ()):
                    labs |= set(getattr(devices.get(twin), "labels", None) or ())
                self.dev[did] = labs & self.ids
            # jh-r17-scope (step 159, review round 17 #10): the label is "in use" only when a switch or fan that can be a heater
            # carries it (on itself, its device, a twin or its wrapped plug); a "Heating" label on a thermostat, an automation or
            # a sensor no longer switches the name detection off for every plug
            plugs = [str(getattr(e, "entity_id", None) or k) for k, e in dict(getattr(self.ereg, "entities", None) or {}).items()]
            self.in_use = any(self.on(eid) for eid in plugs
                              if eid.split(".")[0] in ("switch", "fan") and not _jh_is_setting(hass, eid))
        except Exception:
            self.in_use = False

    def on(self, eid, _depth=0) -> bool:
        try:
            entry = self.ereg.async_get(eid) if self.ereg is not None else None
            if entry is None:
                return False
            if set(getattr(entry, "labels", None) or ()) & self.ids:
                return True
            did = getattr(entry, "device_id", None)
            if did:
                if did in self.dev:
                    if self.dev[did]:
                        return True
                else:
                    device = self.dreg.async_get(did) if self.dreg is not None else None
                    if set(getattr(device, "labels", None) or ()) & self.ids:
                        return True
            src = _jh_wrapped_source(self.hass, entry)
            return bool(src and src != eid and _depth < 2 and self.on(src, _depth + 1))
        except Exception:
            return False


def _jh_heaters(hass):
    """Jarvis Home patch (2026-11-06): heater switches and climate devices, read live. Setting entities such as a child lock or
    display never count. jh-heater-label (step 158, C1): a plug labelled manual_heater always counts (_jh_is_manual); when a
    heater label is in use in Home Assistant (_JhHeaterLabels), a switch or fan counts when that label is on it, its device, its
    wrapped plug or the device's Tuya Local / cloud twin; only when no heater label is in use do heater words in its id or name
    count (any reply language). Fans too (switch_as_x shows a heater plug as a fan), and only what Home Assistant exposes to
    Assist and does not hide or disable. Switches and fans come back together: both are simply on or off."""
    def counts(s):
        return _jh_shown(hass, s.entity_id) and _jh_exposed(hass, s.entity_id)
    labels, twins = _JhHeaterLabels(hass), _jh_device_twins(hass)   # jh-r17-word / jh-r17-twin: one twin map for the sweep
    switches = []
    for s in list(hass.states.async_all("switch")) + list(hass.states.async_all("fan")):
        if _jh_is_setting(hass, s.entity_id):
            continue
        if _jh_is_manual(hass, s.entity_id, _twins=twins) and _jh_shown(hass, s.entity_id):   # jh-manual-heater: the label says it
            switches.append(s)
            continue
        if labels.in_use:
            found = labels.on(s.entity_id)
        else:
            words = f"{s.entity_id} {s.attributes.get('friendly_name') or ''}".lower()
            found = _jh_heater_text(words)   # jh-r17-word: whole words, "theater" is no heater
        if found and counts(s):
            switches.append(s)
    return switches, [s for s in hass.states.async_all("climate") if counts(s)]


def _jh_same_kind(hass, a, b):
    """Jarvis Home patch (2026-11-07, round 2): two lights, or two heaters (a climate device, or a switch _jh_heaters counts).
    Only these may stand in for each other when a vague room word is said. The domain alone says nothing: a lamp plug, a heater
    plug and the amplifier plug are all switches."""
    if a.split(".")[0] == "light" and b.split(".")[0] == "light":
        return True
    switches, climates = _jh_heaters(hass)
    heat = {s.entity_id for s in switches} | {s.entity_id for s in climates}
    return a in heat and b in heat


def _find_entity(hass, name_fragment, domain_hint=None, learned_aliases=None):
    """
    Fuzzy-match a name fragment against HA entities. v5.7.08.

    Matching tiers (first match wins):
      1. Learned aliases (from agent's remember tool)
      2. Exact friendly_name match
      3. Substring: fragment appears inside friendly_name
      4. Word overlap: all words in fragment appear in friendly_name
      5. STT correction → retry with corrected form
      6. Fuzzy similarity scoring (handles phonetic/STT errors)
      7. Entity_id substring match
      8. Area-based fallback

    Returns (entity_id, friendly_name) or None.
    """
    fragment = _jh_frag(name_fragment)
    if not fragment:
        return None

    # ── Tier 0: Check learned aliases ───────────────────────────────
    aliases = learned_aliases or {}
    if fragment in aliases:
        resolved_id = aliases[fragment]
        state = hass.states.get(resolved_id)
        if state:
            _LOGGER.info("Entity resolve: alias '%s' → %s", fragment, resolved_id)
            return (resolved_id, state.attributes.get("friendly_name", resolved_id))

    domains = [domain_hint] if domain_hint else [
        "light", "switch", "lock", "cover", "climate",
        "fan", "media_player", "sensor", "binary_sensor"]

    frag_words = set(fragment.split())
    best_match = None
    best_score = 0
    _jh_kind, _jh_kc = _jh_class_of(fragment), {}   # jh-r18-class (step 160): "the heater", "the fan" name a kind of device
    _jh_exact = []   # jh-r23-res (step 165, review round 23 #2): every exact name, never the first one found

    for domain in domains:
        for state in hass.states.async_all(domain):
            eid = state.entity_id
            if _jh_is_setting(hass, eid):
                continue
            fname = (state.attributes.get("friendly_name") or "").lower().replace("colour", "color")
            fname_words = set(fname.split())

            # Tier 1: exact (jh-r23-dup: collected; one live device of that name is the answer, several are none)
            if fname == fragment:
                if not _jh_hidden(hass, eid):
                    _jh_exact.append((eid, state.attributes.get("friendly_name", eid)))
                continue

            if _jh_kind and not _jh_class_fits(hass, fragment, (eid, state.attributes.get("friendly_name", "")), _jh_kc):
                continue   # jh-r18-class: a generic device word only ever scores devices of that kind
            # Tier 2: substring at a word start (jh-r18-word, review round 18 #1: "heater" is not in "home theater")
            if _jh_word_in(fragment, fname):
                score = len(fragment) / max(len(fname), 1) * 100
                if score > best_score:
                    best_score = score
                    best_match = (eid, state.attributes.get("friendly_name", eid))
                continue

            # Tier 3: word overlap
            if frag_words and frag_words.issubset(fname_words):
                score = len(frag_words) / max(len(fname_words), 1) * 95
                if score > best_score:
                    best_score = score
                    best_match = (eid, state.attributes.get("friendly_name", eid))
                continue

            # Tier 4: fuzzy similarity (jh-r19-fuzzy: every said word is a word of the name, no extra words)
            fuzz = _fuzzy_score(fragment, fname)
            if fuzz > 85 and fuzz > best_score and _jh_fuzzy_words_fit(fragment, fname):
                best_score = fuzz
                best_match = (eid, state.attributes.get("friendly_name", eid))
                continue

            # Tier 5: entity_id substring
            frag_u = fragment.replace(" ", "_")
            if _jh_word_in(frag_u, eid):   # jh-r18-word: switch.home_theater does not hold "heater"
                score = len(frag_u) / max(len(eid), 1) * 80
                if score > best_score:
                    best_score = score
                    best_match = (eid, state.attributes.get("friendly_name", eid))

    if _jh_exact:   # jh-r23-dup: the exact name decides, as before, but only when it is one device
        _jh_xl = [x for x in _jh_exact if _jh_is_live(hass, x[0])] or (_jh_exact if len(_jh_exact) == 1 else [])
        _jh_x1, _jh_xs = _jh_one_of(hass, _jh_xl)
        if _jh_x1:
            _LOGGER.info("Entity resolve: exact '%s' → %s", fragment, _jh_x1[0])
            return _jh_x1
        _LOGGER.info("Entity resolve: '%s' is the name of %d devices - no guess", fragment, len(_jh_xs))
        return None

    # ── Tier 5b: STT correction retry ──────────────────────────────
    if (not best_match or best_score < 50):
        corrected = fragment
        for wrong, right in _STT_CORRECTIONS.items():   # jh-r18-word: whole words only ("lamp" is not in "lampshade")
            corrected = re.sub(_JH_WORD_START + re.escape(wrong) + _JH_WORD_END, right, corrected)
        if corrected != fragment:
            _LOGGER.info("Entity resolve: STT correction '%s' → '%s'", fragment, corrected)
            # Retry with corrected form (non-recursive, just one pass)
            for domain in domains:
                for state in hass.states.async_all(domain):
                    if _jh_is_setting(hass, state.entity_id):
                        continue
                    fname = (state.attributes.get("friendly_name") or "").lower()
                    if _jh_kind and not _jh_class_fits(hass, fragment, (state.entity_id, state.attributes.get("friendly_name", "")), _jh_kc):
                        continue   # jh-r18-class
                    if _jh_word_in(corrected, fname):   # jh-r18-word
                        score = len(corrected) / max(len(fname), 1) * 90
                        if score > best_score:
                            best_score = score
                            best_match = (state.entity_id,
                                          state.attributes.get("friendly_name", state.entity_id))
                    fuzz = _fuzzy_score(corrected, fname)
                    if fuzz > 85 and fuzz > best_score and _jh_fuzzy_words_fit(corrected, fname):   # jh-r19-fuzzy
                        best_score = fuzz
                        best_match = (state.entity_id,
                                      state.attributes.get("friendly_name", state.entity_id))

    # ── Tier 6: area-based fallback ────────────────────────────────
    if not best_match and domain_hint:
        try:
            from homeassistant.helpers import (
                area_registry as areg, entity_registry as er, device_registry as dr)
            area_reg = areg.async_get(hass)
            ent_reg = er.async_get(hass)
            dev_reg = dr.async_get(hass)
            _jh_t6 = []   # jh-r23-dup
            for area in area_reg.async_list_areas():
                if _jh_word_in(fragment, area.name.lower()):   # jh-r18-word: "room" is not in "Bathroom"
                    for entry in ent_reg.entities.values():
                        if entry.domain != domain_hint or _jh_is_setting(hass, entry.entity_id):
                            continue
                        in_area = entry.area_id == area.id
                        if not in_area and entry.device_id:
                            device = dev_reg.async_get(entry.device_id)
                            in_area = device and device.area_id == area.id
                        if in_area:
                            state = hass.states.get(entry.entity_id)
                            if state and _jh_class_fits(hass, fragment, (entry.entity_id, state.attributes.get("friendly_name", "")), _jh_kc):
                                _jh_t6.append((entry.entity_id, state.attributes.get("friendly_name", entry.entity_id)))
            _jh_t6one, _jh_t6all = _jh_one_of(hass, _jh_t6)   # jh-r23-dup: the one device of that room, never the first
            if _jh_t6one:
                return _jh_t6one
        except Exception:
            pass

    if best_match and best_score > 25:
        if not _jh_class_fits(hass, fragment, best_match, _jh_kc):
            # jh-r18-class (step 160, review round 18 #1): a generic device word ("the heater") and no device of that kind: never
            # an unrelated device that merely scored best
            _LOGGER.debug("Entity resolve: '%s' names a kind of device, %s is none", fragment, best_match[0])
            return None
        _LOGGER.info("Entity resolve: '%s' → %s (score=%.0f)", fragment, best_match[0], best_score)
        return best_match

    _jh_heard = _jh_sound_hits(hass, fragment, domains)
    if len(_jh_heard) == 1 and _jh_class_fits(hass, fragment, _jh_heard[0], _jh_kc):   # two sound-alikes are for the caller to ask about
        _LOGGER.info("Entity resolve: sounds like '%s' → %s", fragment, _jh_heard[0][0])
        return _jh_heard[0]

    # A failed resolution is normal, expected control flow: the phrase looked
    # vaguely command-like but matched no device, so the caller falls through
    # to the LLM (which has fuzzy search + aliases). DEBUG, not WARNING — this
    # is not an error condition and should not surface in the user's log.
    _LOGGER.debug("Entity resolve unmatched: '%s' (domain=%s, best=%.0f)", fragment, domain_hint, best_score)
    return None


def _find_entities_in_area(hass, area_name, domain, exact=False):
    results = []
    try:
        from homeassistant.helpers import (
            area_registry as areg, entity_registry as er, device_registry as dr)
        try:
            from .entity_filter import is_excluded
        except Exception:
            is_excluded = lambda _h, _e: False
        area_reg = areg.async_get(hass)
        ent_reg = er.async_get(hass)
        dev_reg = dr.async_get(hass)
        target = None
        for area in area_reg.async_list_areas():
            if (area_name.strip().lower() == area.name.strip().lower()) if exact else _jh_word_in(area_name.lower(), area.name.lower()):
                target = area   # jh-r14-room: exact=True is the whole area name only (review round 14)
                break
        if not target and not exact:
            _jh_rooms = [a for a in area_reg.async_list_areas() if len(_jh_sound(area_name)) >= 3 and _jh_sound(a.name) == _jh_sound(area_name)]
            target = _jh_rooms[0] if len(_jh_rooms) == 1 else None
        if not target:
            return []
        for entry in ent_reg.entities.values():
            if entry.domain != domain:
                continue
            in_area = entry.area_id == target.id
            if not in_area and entry.device_id:
                device = dev_reg.async_get(entry.device_id)
                in_area = device and device.area_id == target.id
            if in_area:
                # Excluded entities don't take part in area/group commands
                # ("turn on the living-room lights"); a by-name request still
                # resolves them through a different path.
                if is_excluded(hass, entry.entity_id) or _jh_is_setting(hass, entry.entity_id):
                    continue
                state = hass.states.get(entry.entity_id)
                if state:
                    results.append((entry.entity_id,
                                    state.attributes.get("friendly_name", entry.entity_id)))
    except Exception:
        pass
    return results


# Actions whose captured group is a VALUE (a number) and/or an optional AREA,
# never a device name — so the target entity is resolved from the area (if one
# was captured) or a sole/active default, not by searching for the number/None.
_MEDIA_ACTIONS = frozenset({"media_pause", "media_play", "media_next",
                            "volume_up", "volume_down", "mute", "unmute", "volume_set"})


def _default_target(hass, domain):
    """The unambiguous default entity for a domain when a command named none.

    For media_player: a single currently-active player (playing / paused /
    buffering) wins; otherwise the sole non-satellite media_player. For any other
    domain: the sole entity. Returns None when it is ambiguous (zero, or several
    with no single active one) so the caller falls through rather than guessing
    the wrong device. Never raises.
    """
    try:
        ents = [s.entity_id for s in hass.states.async_all(domain)
                if not s.entity_id.startswith("assist_satellite.")]
        if domain == "media_player":
            active = [e for e in ents
                      if (hass.states.get(e) and
                          hass.states.get(e).state in ("playing", "paused", "buffering"))]
            if len(active) == 1:
                return active[0]
        if len(ents) == 1:
            return ents[0]
    except Exception:   # pragma: no cover - defensive
        pass
    return None


# Jarvis Home patch (2026-11-06): one open "which one" question per conversation, never shared between people.
_JH_PENDING = {}
# Words that point back at something said before. Only the AI has that context, so they are never device names.
_JH_PRONOUNS = frozenset(("it", "that", "this", "them", "those", "these", "everything", "all"))


_JH_YES = r"yes|yes sir|yes do|yeah|yep|sure|ok|okay|do it|go ahead|please do|that one|ja|ja hoor|jawel|doe maar"


# jh-r23-one (step 165, review round 23 #1 and #2): "more than one device fits" is one rule for the whole instant path. A name
# that more than one live device has, or a singular word ("the door", "the blind", "the fan") that fits more than one, is a
# which-one question, never the first match. In the router path (a satellite in a known room, _JH_REQ_AREA) the one candidate
# in that room is meant only when exactly one is there; otherwise nothing is switched (the router drops the question).
_JH_REQ_AREA = _jh_contextvars.ContextVar("jarvis_request_area", default="")
_JH_BARRIER_CLASSES = frozenset(("garage", "gate", "door"))
_JH_BLIND_CLASSES = frozenset(("blind", "shade", "curtain", "shutter"))   # jh-r24-kindbar (step 166): window coverings only
# jh-r26-barrier (step 168, review round 26 #1, #3, #12): ONE barrier / security word rule for every check (jh_bulk_barrier,
# _jh_barrier_switch, jh_kind_fits' plug and lock rules, the group function, _jh_could_be, policy's security hint). Names are
# compound words in Dutch, German and Italian (Garagepoort, Tuinpoort, Garagedeuropener, Haustüröffner, Einfahrtstor,
# Inbraakalarm, Buitensirene, Apricancello) and Thai is written without spaces, so a stem also counts at the END of a word, and
# the long, unambiguous stems anywhere in it: the rule fails toward leaving a device out. Short, risky stems are whole words or
# explicit lists: German -tor only as Tor, Garagentor, Gartentor, Hoftor, Einfahrtstor, Schiebetor, Rolltor, Flügeltor,
# Falttor, ... (never Ventilator, Motor, Monitor, Aktor); "door" never Outdoor / Indoor; "porte" / "porta" never Transporte,
# Esporte, Suporte, Deporte; "lock" never Clock or Block; "gate" never Gateway.
import functools as _jh_ft26
_JH_COVERING_CLASSES = frozenset(("blind", "shade", "curtain", "shutter", "awning", "window", "damper"))
_JH_BW_BARRIER = frozenset(("garage", "gate", "door", "opener"))
# per category: (stems found anywhere in a word, stems a word may end with, whole words only). Written without accents and with
# ue / oe / ae / ss for ü / ö / ä / ß: each word of a name is folded the same way before the test.
_JH_BW = {
    "garage": (("garage", "garaje"), (), ()),
    "gate": (("poort", "slagboom", "slagbomen"),
             ("hek", "hekken", "schranke", "schranken", "portail", "portails", "porton", "portones", "portao", "portoes",
              "cancello", "cancelli", "cancelletto", "cancelo", "cancela", "cancelas", "portone", "portoni"),
             ("gate", "gates", "barrier", "barriers", "barriere", "tor", "tore", "garagentor", "garagentore", "gartentor",
              "gartentore", "hoftor", "hoftore", "einfahrtstor", "einfahrtstore", "schiebetor", "schiebetore", "rolltor",
              "rolltore", "fluegeltor", "fluegeltore", "falttor", "falttore", "sektionaltor", "hallentor", "zufahrtstor",
              "gittertor", "drehtor", "kipptor", "haustor", "werkstor", "torantrieb", "torsteuerung", "hekbediening", "hekmotor",
              "basculante")),
    "door": ((), ("door", "doors", "deur", "deuren", "tuer", "tuere", "tueren", "porte", "portes", "puerta", "puertas", "porta",
                  "portas"), ()),
    "opener": (("opener", "oeffner", "buzzer", "tuersummer", "ontgrendel", "entriegel", "portier", "deurautomaat"), (),
               ("gache",)),
    "security": (("alarm", "allarm", "siren", "camera", "kamera", "camara", "security", "securite", "seguridad", "sicurezza",
                  "seguranca", "beveiliging", "sicherheit", "surveillance", "ueberwachung", "inbraak", "einbruch", "rookmelder",
                  "rauchmelder", "brandmelder"),
                 ("slot", "schloss"),
                 ("secure", "lock", "locks", "verrou", "verrous", "cerradura", "cerraduras", "serratura", "serrature", "fechadura",
                  "fechaduras")),
}
# Thai has no spaces between words: these are found anywhere in the text
_JH_BW_THAI = (("garage", ("โรงรถ",)), ("gate", ("ประตูรั้ว", "ไม้กั้น")), ("door", ("ประตู",)), ("opener", ("เปิดประตู",)),
               ("security", ("กันขโมย", "ไซเรน", "กล้อง", "เตือนภัย", "สัญญาณเตือน", "ล็อค", "กุญแจ")))
# look-alike words that end in a stem but are none: never a barrier
_JH_BW_NOT = frozenset(("outdoor", "outdoors", "indoor", "indoors", "odeur", "odeurs", "transporte", "transportes", "esporte",
                        "esportes", "desporte", "deporte", "deportes", "suporte", "suportes", "soporte", "soportes", "reporte",
                        "reportes", "importe", "aporte", "importa", "comporta", "sporta", "apporte", "supporte"))


def _jh_bw_fold(word) -> str:
    """jh-r26-barrier: a Latin word without accents, ü / ö / ä / ß as ue / oe / ae / ss ("Haustüröffner" -> "haustueroeffner")."""
    import unicodedata
    w = word.replace("ß", "ss").replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")
    return "".join(c for c in unicodedata.normalize("NFKD", w) if not unicodedata.combining(c))


@_jh_ft26.lru_cache(maxsize=4096)
def jh_barrier_words(text) -> frozenset:
    """jh-r26-barrier (step 168): THE barrier / security word rule. The categories the words of a name or id say: 'garage',
    'gate', 'door', 'opener' (a barrier: a device that opens a way into the house) and 'security' (an alarm, siren, camera,
    security or lock switch). Compound-aware in every reply language (en, nl, de, fr, es, it, pt, th); fails toward a match."""
    low = str(text or "").casefold()
    out = set()
    for cat, stems in _JH_BW_THAI:
        if any(s in low for s in stems):
            out.add(cat)
    for tok in re.findall(r"[^\W\d_]+", low):
        if not tok.isascii():
            tok = _jh_bw_fold(tok)
        if tok in _JH_BW_NOT:
            continue
        for cat, (anywhere, ends, whole) in _JH_BW.items():
            if cat not in out and (tok in whole or any(s in tok for s in anywhere) or tok.endswith(ends)):
                out.add(cat)
        # jh-r27-barrier (step 170, review round 27 #1): more stems, a stem may also START a word (Türmotor), each with its
        # look-alikes (Doorbell, Clock, Aggregate) that are no barrier
        for cat, (anywhere, ends, starts, not_ends, not_starts) in _JH_BW27.items():
            if cat not in out and (any(s in tok for s in anywhere) or (tok.endswith(ends) and not tok.endswith(not_ends))
                                   or (tok.startswith(starts) and not tok.startswith(not_starts))):
                out.add(cat)
        # jh-r28-words (step 171, review round 28 #2): lock and security words the rule still missed (serrure, strike,
        # Haftmagnet, bewaking, vigilancia, sorveglianza, Scharfschaltung, arm / armed)
        for cat, (anywhere, whole, starts) in _JH_BW28.items():
            if cat not in out and (tok in whole or any(s in tok for s in anywhere) or tok.startswith(starts)):
                out.add(cat)
    return frozenset(out)


# jh-r27-barrier (step 170, review round 27 #1): per category (anywhere, a word may end with, a word may start with, look-alike
# endings that are none, look-alike beginnings that are none); folded like _JH_BW (ue / oe / ae / ss, no accents)
_JH_BW27 = {
    "gate": (("pforte", "portillon", "saracinesca", "barrera", "barreira"),
             ("gate", "gates", "verja", "verjas", "gatter", "poortje", "poortjes"),
             (),
             ("aggregate", "aggregates", "navigate", "navigates", "irrigate", "irrigates", "delegate", "delegates",
              "investigate", "propagate", "interrogate", "obligate", "segregate", "congregate", "surrogate", "legate", "legates",
              "frigate", "frigates", "mitigate", "litigate", "instigate", "fumigate", "castigate", "corrugate", "abrogate",
              "variegate", "subrogate", "promulgate", "negate", "relegate", "arrogate", "elongate", "conjugate", "profligate"),
             ()),
    "door": ((), ("deurtje", "deurtjes"), ("door", "deur", "tuer"), (),
             ("doorbell", "deurbel", "tuerklingel", "tuerkis", "doordat", "doorgaan", "doorheen", "doorlopend")),
    "opener": (("riegel", "grendel", "cerrojo"), (), (), (), ()),
    "security": (("warnmelder",), ("lock", "locks"), (), ("clock", "clocks", "block", "blocks"), ()),
}
# jh-r28-words (step 171, review round 28 #2): per category (anywhere in a word, whole words only, a word may start with); folded
# like _JH_BW. A maglock relay that is switched off unlocks its door, a surveillance or arming switch that is switched off disarms.
_JH_BW28 = {
    "security": (("serrure", "haftmagnet", "magneet", "magnetslot", "bewaking", "vigilancia", "sorveglianza"),
                 ("strike", "strikes", "arm", "armed", "arming"),
                 ("scharf",)),
}


def jh_barrier_word(text) -> str:
    """jh-r26-barrier: the first category of jh_barrier_words (garage, gate, door, opener, security), '' when none."""
    cats = jh_barrier_words(str(text or ""))
    return next((c for c in ("garage", "gate", "door", "opener", "security") if c in cats), "")


# the old names, now THE word rule (no regex copy is left that could drift)
_JH_BARRIER_WORDS = _JhWordRule(("garage", "gate", "door"))
_JH_SECURITY_SWITCH = _JhWordRule(("security", "garage", "gate", "door"))
_JH_BLIND_WORDS = frozenset(("blind", "blinds", "shade", "shades", "curtain", "curtains", "shutter", "shutters"))
_JH_CONTROL_DOMAINS = ("light", "switch", "lock", "cover", "climate", "fan", "media_player", "input_boolean", "water_heater",
                       "humidifier", "valve", "vacuum", "siren")


def jh_bulk_barrier(hass, eid, action, kind=None, lock_up=True, cache=None) -> str:   # jh-r28-group (step 171): cache
    """jh-r23-bulk (step 165, review round 23 #1), jh-r25-group (step 167, review round 25 #1, #12): why this device is never
    part of a GROUP order ('' when it may be): a lock outside a lock order (unlocked or opened: 'unlock'; any other group order
    than locking: 'lock'), a cover that is a garage door, gate or door (by device class, or a name / id word), a switch or
    input_boolean that opens a garage, gate or door ('barrier') or is an alarm / siren / security / lock / camera switch
    ('security') FOR EVERY ACTION, a siren, an alarm panel, a manual heater. Named alone, such a device is still switched by
    the single-device path with its own guards. jh_group_targets calls this for every group. Fails closed ("error")."""
    try:
        dom = str(eid).split(".", 1)[0]
        attrs = getattr(hass.states.get(eid), "attributes", None) or {}
        name = str(attrs.get("friendly_name") or "")
        hay = "%s %s" % (eid, name)
        dc = str(attrs.get("device_class") or "").lower()
        if dom == "lock":
            if action in ("unlock", "open"):
                return "unlock"
            if not (lock_up and action == "lock"):
                return "lock"
        if dom == "cover":
            # jh-r26-barrier (step 168, review round 26 #12): a window-covering device class decides (a "Garage Blind" or a
            # "Patio Door Curtain" of class blind / curtain is a blind, never a barrier); a garage, gate or door class is a
            # barrier; with no device class (or another one) the compound-aware words of its name or id decide
            if dc in _JH_BARRIER_CLASSES or (dc not in _JH_COVERING_CLASSES and jh_barrier_words(hay) & _JH_BW_BARRIER):
                return "barrier"
        # jh-r24-kindbar (step 166, review round 24 #1): a device of another kind than the one named, by the one strict map
        # (jh_kind_fits); the generic "covers" takes windows, awnings and dampers too, never a barrier
        if kind and not jh_kind_fits(hass, kind, eid):
            return "kind"
        if dom == "alarm_control_panel":
            return "alarm"
        if dom == "siren":
            return "security"
        if dom == "switch":
            # jh-r28-group (step 171, review round 28 #3): a switch that switch_as_x shows as a siren, lock, alarm panel, valve or
            # a cover that is no window covering is that device, for every action (the source of a siren is no plug)
            why = jh_sax_kind(hass, eid, cache if cache is not None else {})
            if why:
                return why
        if dom in ("switch", "input_boolean"):
            why = _jh_barrier_switch(eid, name, dc)   # jh-r25-group: for every action, turning on included
            if why:
                return why
        if _jh_is_manual(hass, eid):
            return "manual"
        # jh-r27-heat (step 170, the owner 2026-11-20): a heater is never part of a group order that turns devices ON (turn on
        # the plugs, turn on everything, a room, toggle), whatever its class: an outlet-class heater plug is a plug to the
        # allowlist. A group turn-off still takes it; by its own name it is still switched
        if action in _JH_HEAT_ON and dom in ("switch", "fan", "light", "input_boolean") and _jh_heaterish(hass, eid, name, {}):
            return "heater"
        return ""
    except Exception:  # noqa: BLE001
        return "error"


_JH_HEAT_ON = frozenset(("turn_on", "toggle", "open", "open_cover"))   # jh-r27-heat (step 170)


# jh-r25-group (step 167, review round 25 #1, #8, #9, #10, #12, #13): ONE function decides the targets of EVERY multi-target
# action (the instant bulk rows, "turn off everything", the room-lights order, good night, the router's secure_area, the room-
# lights popover and the AI's bulk_control). The duplicated copies are gone, and an AST test (test_review_r25.SingleGroupAudit)
# keeps it that way: jh_act_and_confirm is called only by jh_group_act, which acts only on a JhGroup's own targets, and every
# loop that switches devices sits in a function that asks jh_group_targets first.
# A device that opens a way into the house or raises an alarm is never part of a group, in either direction: a cover, switch or
# input_boolean whose name, id or device class says garage, gate, door or opener; a siren, alarm or lock-like switch; an alarm
# panel. A lock is never part of a group, except in an explicit lock order ("lock all the doors", "lock up", good night's
# lock-up, secure_area's locks): then EVERY lock is locked, and the ones named as a garage or gate lock are reported.
_JH_OPENER_WORDS = _JhWordRule(("opener",))   # jh-r26-barrier (step 168): THE word rule (jh_barrier_words), not a copy
_JH_GROUP_DOMAINS = {"all": ("light", "switch", "fan")}
# the end state of a group action: a device left out that is already there is not left out in effect (it is not named)
_JH_GROUP_END = {"turn_on": ("on",), "turn_off": ("off",), "lock": ("locked",), "close": ("closed",), "open": ("open",)}


def _jh_barrier_switch(eid, name, dc="") -> str:
    """jh-r25-group (review round 25 #1): 'barrier' for a switch / input_boolean that opens a garage, gate or door (its device
    class, or garage / gate / door / opener words in its name or id), 'security' for an alarm, siren, security, lock or camera
    switch, '' otherwise. For EVERY action: turning such a relay on is what opens the barrier or sounds the siren."""
    # jh-r26-barrier (step 168, review round 26 #1, #3): THE word rule, compound-aware ("Garagepoort", "Haustüröffner",
    # "Inbraakalarm", "Buitensirene", "ประตูโรงรถ"): it fails toward leaving the device out
    cats = jh_barrier_words("%s %s" % (eid, name or ""))
    if str(dc or "").lower() in _JH_BARRIER_CLASSES or cats & _JH_BW_BARRIER:
        return "barrier"
    if "security" in cats:
        return "security"
    return ""


# jh-r27-allow (step 170, review round 27 #1): the ALLOWLIST of group orders, see jh_safe_kind
_JH_SAFE_COVER = frozenset(("blind", "shade", "curtain", "shutter", "awning"))
_JH_GROUP_IN = frozenset(("jarvis_group",))
_JH_GROUP_OUT = frozenset(("jarvis_no_group", "jarvis_never_in_groups"))
_JH_PLUG_WORDS = frozenset(("plug", "plugs", "smartplug", "socket", "sockets", "outlet", "outlets", "steckdose", "steckdosen",
                            "zwischenstecker", "steckdosenleiste", "stopcontact", "stekker", "stekkerdoos", "prise", "enchufe",
                            "presa", "tomada", "powerstrip"))
_JH_PLUG_MODEL = re.compile(r"(?:ts011f|ts0121|hs10[03-9]|hs110|kp1\d\d|kp4\d\d|p1[01][05]|ep10|ep25|s26r?\d?|s31|s40|shplg\w*)")


def _jh_group_label_ids(hass, cache) -> tuple:
    """jh-r27-allow: (label ids that let a device into group orders, label ids that keep it out): a label whose id or name is
    jarvis_group / jarvis_no_group (or "JARVIS never in groups"); spaces and dashes count as underscores. Read once per group."""
    if "glab27" not in cache:
        ins, outs = set(_JH_GROUP_IN), set(_JH_GROUP_OUT)
        try:
            from homeassistant.helpers import label_registry as lr
            for lab in list(lr.async_get(hass).async_list_labels()):
                lid = str(getattr(lab, "label_id", "") or "")
                nm = re.sub(r"[\s-]+", "_", str(getattr(lab, "name", "") or "").strip().casefold())
                if lid.casefold() in _JH_GROUP_OUT or nm in _JH_GROUP_OUT:
                    outs.add(lid)
                elif lid.casefold() in _JH_GROUP_IN or nm in _JH_GROUP_IN:
                    ins.add(lid)
        except Exception:  # noqa: BLE001 - the plain ids still count
            pass
        cache["glab27"] = (ins, outs)
    return cache["glab27"]


def jh_group_label_of(hass, eid, cache=None) -> str:
    """jh-r27-allow (step 170): 'out' when the entity or its device carries the "JARVIS never in groups" label, 'in' for the
    "JARVIS group" label, '' for neither (out wins). Read live from the registries."""
    cache = cache if cache is not None else {}
    try:
        ins, outs = _jh_group_label_ids(hass, cache)
        from homeassistant.helpers import device_registry as dr, entity_registry as er
        entry = er.async_get(hass).async_get(str(eid))
        labs = set(getattr(entry, "labels", None) or ())
        did = getattr(entry, "device_id", None)
        if did:
            labs |= set(getattr(dr.async_get(hass).async_get(did), "labels", None) or ())
    except Exception:  # noqa: BLE001
        return ""
    if labs & outs:
        return "out"
    return "in" if labs & ins else ""


def _jh_plug_hint(hass, eid, attrs) -> bool:
    """jh-r27-allow: True only when the switch is POSITIVELY an outlet: device class outlet (its state or its registry entry),
    a registry translation key that says plug / socket / outlet, or a device whose model is a known smart plug (the model or
    model id says plug / socket / outlet / Steckdose / stopcontact / ..., or is a known plug model code such as TS011F, HS110,
    KP115, P110, S26, S31). The user-given names do not count. Decides conservatively: False when unsure."""
    try:
        if str(attrs.get("device_class") or "").strip().lower() == "outlet":
            return True
        from homeassistant.helpers import device_registry as dr, entity_registry as er
        entry = er.async_get(hass).async_get(str(eid))
        if entry is None:
            return False
        if any(str(getattr(entry, k, "") or "").strip().lower() == "outlet" for k in ("device_class", "original_device_class")):
            return True
        if set(re.findall(r"[a-z]+", str(getattr(entry, "translation_key", "") or "").casefold())) & _JH_PLUG_WORDS:
            return True
        did = getattr(entry, "device_id", None)
        dev = dr.async_get(hass).async_get(did) if did else None
        if dev is None:
            return False
        for k in ("model", "model_id"):
            toks = re.findall(r"[a-z0-9]+", str(getattr(dev, k, "") or "").casefold())
            if any(t in _JH_PLUG_WORDS or _JH_PLUG_MODEL.fullmatch(t) for t in toks):
                return True
    except Exception:  # noqa: BLE001 - unsure: no plug
        return False
    return False


def jh_safe_kind(hass, eid, cache=None, action=None) -> str:   # jh-r28-group (step 171): action
    """jh-r27-allow (step 170, review round 27 #1): the safe kind a device POSITIVELY is, for a group order that turns devices
    on, opens them or switches everything off: 'light' (the light domain, or a switch shown as a light), 'plug' (an outlet),
    'fan' (no heater), 'blind' (a cover of a window-covering class), 'media' (a media player), 'label' (the JARVIS group
    label); '' for an unknown kind: a switch or input_boolean with no class (a relay could drive an alarm, a pump or a door), a
    cover with no class or another class, any other domain. Fails closed ('')."""
    cache = cache if cache is not None else {}
    try:
        eid = str(eid)
        dom = eid.split(".", 1)[0]
        attrs = getattr(hass.states.get(eid), "attributes", None) or {}
        name = str(attrs.get("friendly_name") or "")
        dc = str(attrs.get("device_class") or "").strip().lower()
        k = ""
        # jh-r28-group (step 171, review round 28 #1): switching OFF, a device JARVIS positively knows is a heater (heater words,
        # a Heater label, JARVIS's heater list) is a safe kind, whatever its class; a manual heater was left out before. Turning
        # on never takes a heater (jh_bulk_barrier)
        if (action == "turn_off" and dom in ("switch", "fan", "light", "input_boolean")
                and not (dom == "switch" and jh_sax_kind(hass, eid, cache)) and _jh_heater_known(hass, eid, name, cache)):
            return "heater"
        if dom == "light":
            k = "light"
        elif dom == "media_player":
            k = "media"
        elif dom == "fan":
            k = "" if _jh_heaterish(hass, eid, name, cache) else "fan"
        elif dom == "cover":
            k = "blind" if dc in _JH_SAFE_COVER else ""
        elif dom == "switch":
            shown = _jh_switch_shown_as(hass, eid, cache)
            if shown in ("light", "fan") and not _jh_heaterish(hass, eid, name, cache):
                k = shown
            elif not shown and _jh_plug_hint(hass, eid, attrs):   # jh-r28-group (step 171): a wrapped switch is never a plug
                k = "plug"
        if not k and jh_group_label_of(hass, eid, cache) == "in":
            k = "label"
        return k
    except Exception:  # noqa: BLE001
        return ""


def jh_group_needs_safe(domain, action, kind, dom=None) -> bool:
    """jh-r27-allow: True for a group order that takes only devices of a safe kind: turning on, opening, unlocking or toggling
    (any domain), and switching off everything or the plugs. jh-r28-group (step 171, review round 28 #2): every group turn-off
    of switches and input_booleans too (dom: the device's own domain), so a relay that is not positively safe (an outlet, a
    switch shown as a light or fan, a known heater, the JARVIS group label) is left out of "turn off the switches" as well, and
    named. A close keeps the barrier and security rules only (a cover with no class is closed)."""
    if action in ("turn_on", "open", "open_cover", "unlock", "toggle"):
        return True
    return action == "turn_off" and (domain in ("all", "switch", "input_boolean") or kind == "plug"
                                     or dom in ("switch", "input_boolean"))


# jh-r28-group (step 171, review round 28 #3): the wrapper kinds that make a switch a security device or a barrier
_JH_SAX_SECURITY = frozenset(("siren", "lock", "alarm_control_panel"))


def jh_sax_kind(hass, eid, cache=None) -> str:
    """jh-r28-group (step 171, review round 28 #3): 'security' for a switch that switch_as_x shows as a siren, lock or alarm
    panel, 'barrier' for one shown as a valve or as a cover that is no window covering (garage, gate, door or no class), '' for
    none (or a light, fan or window covering). The source keeps the wrapper's kind for every action and is never a plug, also
    when the owner has unhidden it. Fails closed ('error')."""
    cache = cache if cache is not None else {}
    try:
        shown = _jh_switch_shown_as(hass, eid, cache)
        if not shown or shown in ("light", "fan", "switch"):
            return ""
        if shown in _JH_SAX_SECURITY:
            return "security"
        if shown == "cover":
            wrap = next((w for w, s in _jh_sax_pairs(hass, cache).items() if s == str(eid)), "")
            dc = str((getattr(hass.states.get(wrap), "attributes", None) or {}).get("device_class") or "").strip().lower()
            return "" if dc in _JH_SAFE_COVER else "barrier"
        return "barrier"
    except Exception:  # noqa: BLE001
        return "error"


def _jh_heater_known(hass, eid, name, cache) -> bool:
    """jh-r28-group (step 171, review round 28 #1): True only when JARVIS POSITIVELY knows the device is a heater: heater words
    in its name or id, JARVIS's heater list, a Heater label. Unlike _jh_heaterish (which fails toward 'heater' so a heater is
    never a light or fan to turn on), this one fails toward False: an unreadable device is no heater to switch off."""
    try:
        if _jh_heater_text(str(name or "").casefold()) or _jh_heater_text(str(eid).casefold()):
            return True
        if "heat" not in cache:
            switches, climates = _jh_heaters(hass)
            cache["heat"] = {s.entity_id for s in switches + climates}
        if eid in cache["heat"]:
            return True
        if "labels" not in cache:
            cache["labels"] = _JhHeaterLabels(hass)
        return bool(cache["labels"].on(eid))
    except Exception:  # noqa: BLE001
        return False


class JhGroup:
    """jh-r25-group: the result of jh_group_targets. targets: [(entity_id, name)] to switch; left_out: [(entity_id, name,
    reason)] that the reply names (barrier, security, alarm, lock, hidden, manual, unlock); skipped: [(entity_id, reason)] that
    are no member of the group as said (a setting, an excluded entity, another kind, a hidden twin switched through its twin,
    a device already in the end state); reported: names of garage / gate locks a lock order includes; area / area_id: the real
    room; error: '' | 'area' (no room of that exact name or alias: rooms holds the near ones) | 'error' (fails closed)."""

    def __init__(self):
        self.targets, self.left_out, self.skipped, self.reported = [], [], [], []
        self.area, self.area_id, self.error, self.rooms, self.kind = "", "", "", [], None

    @property
    def ids(self):
        return [e for e, _n in self.targets]


def jh_area_exact(hass, words):
    """jh-r25-group (review round 25 #8): THE exact room resolver of the group function: (area id, real name, []) for the ONE
    area whose whole name or alias is the words ("the bedroom" -> Bedroom, never Guest Bedroom); else ('', '', [the names of
    the areas that hold the words]) so the caller asks which room and switches nothing. No substring match, no first pick."""
    # jh-r26-group (step 168, review round 26 #4): the same leading words come off the words AND each area name or alias, so a
    # room whose own name starts with "The" ("The Den", "The Loft") is found as "The Den", "the den" or "den"; a name said
    # exactly as it is written wins first (a home with both "Den" and "The Den")
    raw = re.sub(r"\s+", " ", str(words or "").strip().casefold())

    def bare(t):
        return re.sub(r"^(?:all\s+(?:of\s+)?)?(?:the\s+)?", "", t).strip()
    want = bare(raw)
    if not want:
        return "", "", []
    from homeassistant.helpers import area_registry as _jh_ar25
    areas = list(_jh_ar25.async_get(hass).async_list_areas())

    def own(a):
        return ({re.sub(r"\s+", " ", str(a.name or "").strip().casefold())}
                | {re.sub(r"\s+", " ", str(x).strip().casefold()) for x in (getattr(a, "aliases", None) or [])})

    def names(a):
        return own(a) | {bare(n) for n in own(a)}
    exact = [a for a in areas if any(raw == n for n in own(a))]
    if len(exact) == 1:
        return str(exact[0].id), str(exact[0].name), []
    hits = [a for a in areas if any(want == n for n in names(a))]
    if len(hits) == 1:
        return str(hits[0].id), str(hits[0].name), []
    near = hits or [a for a in areas if any(_jh_word_in(want, n, whole=True) or _jh_word_in(n, want, whole=True)
                                            for n in names(a) if n)]
    return "", "", sorted({str(a.name) for a in near})


def _jh_area_members(hass, area_id, domains):
    """jh-r25-group (review round 25 #9): EVERY entity of these domains in the area (its own area, else its device's), no cap."""
    from homeassistant.helpers import device_registry as _jh_dr25, entity_registry as _jh_er25
    ents, devs, out = _jh_er25.async_get(hass), _jh_dr25.async_get(hass), []
    for entry in list(ents.entities.values()):
        eid = str(getattr(entry, "entity_id", "") or "")
        if eid.split(".", 1)[0] not in domains or hass.states.get(eid) is None:
            continue
        aid = getattr(entry, "area_id", None)
        if not aid and getattr(entry, "device_id", None):
            aid = getattr(devs.async_get(entry.device_id), "area_id", None)
        if str(aid or "") == str(area_id):
            out.append(eid)
    return out


def _jh_sax_pairs(hass, cache) -> dict:
    """jh-r25-group (review round 25 #13): {switch_as_x wrapper entity: its source entity} (read once per group)."""
    if "sax2" not in cache:
        pairs = {}
        try:
            from homeassistant.helpers import entity_registry as er
            for entry in list(er.async_get(hass).entities.values()):
                if getattr(entry, "platform", None) == "switch_as_x":
                    src = _jh_wrapped_source(hass, entry)
                    if src:
                        pairs[str(entry.entity_id)] = str(src)
        except Exception:  # noqa: BLE001
            pass
        cache["sax2"] = pairs
    return cache["sax2"]


def jh_group_targets(hass, domain, action, kind=None, area=None, words="", *, area_id=None, candidates=None, lock_up=None):
    """jh-r25-group (step 167): THE target rule of every multi-target action. domain: an entity domain, or 'all' (lights,
    switches, fans); kind: the kind named (None: from the words, else the domain's own kind; '' none); area: a room as said
    (resolved exactly by jh_area_exact) or area_id; candidates: a caller's own list of the room's entities (the router).
    1. the room by its exact name or alias, else nothing (error 'area', the near rooms named);
    2. EVERY entity of the domain in the room (no cap), else of the home;
    3. no setting (config / diagnostic) and no entity JARVIS excludes;
    4. a hidden entity: switched through its switch_as_x twin when that twin is targeted too (not named); kept only when it is
       the light half of a switch_as_x pair whose visible twin is not targeted; otherwise left out and named;
    5. the kind (jh_kind_fits); a lock order takes every lock;
    6. never a barrier or security device (any action), never a lock outside a lock order, never a manual heater: left out and
       named (one already in the end state is not named).
    Returns a JhGroup. Fails closed (error 'error', nothing to switch)."""
    grp = JhGroup()
    try:
        doms = _JH_GROUP_DOMAINS.get(domain, (domain,))
        if kind is None:
            kind = jh_bulk_kind(domain, words)[0] if (words and domain in _JH_KIND_DEFAULT) else _JH_KIND_DEFAULT.get(domain)
        grp.kind = kind or None
        lock_up = (domain == "lock" and action == "lock") if lock_up is None else bool(lock_up)
        if candidates is not None:
            ids = [str(e) for e in candidates if str(e).split(".", 1)[0] in doms and hass.states.get(str(e)) is not None]
        elif area_id or area:
            if not area_id:
                area_id, grp.area, grp.rooms = jh_area_exact(hass, area)
                if not area_id:
                    grp.error = "area"
                    return grp
            grp.area_id = str(area_id)
            if not grp.area:
                from homeassistant.helpers import area_registry as _jh_ar25
                grp.area = next((str(a.name) for a in _jh_ar25.async_get(hass).async_list_areas()
                                 if str(a.id) == str(area_id)), str(area_id))
            ids = _jh_area_members(hass, area_id, doms)
        else:
            ids = [s.entity_id for d in doms for s in hass.states.async_all(d)]
        ids = list(dict.fromkeys(ids))
        try:
            from .entity_filter import is_excluded as _jh_excl25
        except Exception:  # noqa: BLE001
            def _jh_excl25(_h, _e):
                return False
        cache = {}
        # jh-r26-group (step 168, review round 26 #8): a group entity (a light / switch / fan / cover / lock group) is switched
        # through its members, never besides them: each member takes every rule below, the group itself is skipped
        # ('group'), so nothing is switched twice and nothing a rule leaves out is switched through its group. A member of a
        # group (hidden by the group helper's "hide members", or by hand behind the group) is no device hidden on purpose.
        members_of = _jh_group_members(hass, cache)
        member_set = {m for ms in members_of.values() for m in ms}
        ids = _jh_expand_groups(hass, ids, members_of, doms, grp, _jh_excl25)
        idset = set(ids)
        pairs = _jh_sax_pairs(hass, cache)
        twins = {}
        for w, s in pairs.items():
            twins.setdefault(w, set()).add(s)
            twins.setdefault(s, set()).add(w)
        end = _JH_GROUP_END.get(action, ())

        def leave(eid, name, why):
            if str(getattr(hass.states.get(eid), "state", "")).lower() in end:
                grp.skipped.append((eid, "already"))
            else:
                grp.left_out.append((eid, name, why))
        for eid in ids:
            st = hass.states.get(eid)
            name = str(getattr(st, "attributes", {}).get("friendly_name") or eid)
            dom = eid.split(".", 1)[0]
            if _jh_is_setting(hass, eid):
                grp.skipped.append((eid, "setting"))   # Jarvis Home patch: settings stay out (step 62), for every group
                continue
            if _jh_excl25(hass, eid):
                grp.skipped.append((eid, "excluded"))
                continue
            # jh-r27-allow (step 170, review round 27 #1): the "JARVIS never in groups" label keeps a device out of every
            # group order (named)
            if jh_group_label_of(hass, eid, cache) == "out":
                leave(eid, name, "nogroup")
                continue
            hidden = _jh_hidden(hass, eid)
            if hidden and eid not in member_set:   # jh-r26-group: a group's member is hidden by its group, not on purpose
                if twins.get(eid, set()) & idset:
                    grp.skipped.append((eid, "twin"))   # switched through its visible twin, which is in this group
                    continue
                if not (eid in pairs and dom == "light"):
                    leave(eid, name, "hidden")
                    continue
            if dom == "lock" and lock_up:
                if jh_bulk_barrier(hass, eid, action, lock_up=True):
                    leave(eid, name, "lock")
                    continue
                if kind not in (None, "", "lock", "door") and not jh_kind_fits(hass, kind, eid, name, cache, hidden_ok=hidden):
                    grp.skipped.append((eid, "kind"))
                    continue
                if _JH_NOT_DOOR.search("%s %s" % (eid, name)):
                    grp.reported.append(name)   # review round 25 #12: a garage / gate lock is locked with the rest, and said
                grp.targets.append((eid, name))
                continue
            if kind and not jh_kind_fits(hass, kind, eid, name, cache, hidden_ok=hidden, barrier_ok=True):
                if _jh_is_manual(hass, eid):
                    leave(eid, name, "manual")
                else:
                    grp.skipped.append((eid, "kind"))
                continue
            why = jh_bulk_barrier(hass, eid, action, lock_up=lock_up, cache=cache)   # jh-r28-group (step 171)
            if why:
                leave(eid, name, why if why != "error" else "unreadable")
                continue
            # jh-r27-allow (step 170, review round 27 #1): turning on, opening, switching off everything or the plugs: only a
            # device that is POSITIVELY a safe kind (or carries the JARVIS group label); a relay of unknown kind is named
            # jh-r28-group (step 171, review round 28 #1, #2): every group turn-off of switches / input_booleans too; switching
            # off, a known heater is a safe kind
            if jh_group_needs_safe(domain, action, kind, dom) and not jh_safe_kind(hass, eid, cache, action=action):
                leave(eid, name, "unknown")
                continue
            grp.targets.append((eid, name))
        return grp
    except Exception as exc:  # noqa: BLE001 - fails closed: nothing in the group
        _LOGGER.warning("JARVIS: the group targets could not be decided (%s); nothing is switched", exc)
        bad = JhGroup()
        bad.error = "error"
        return bad


def _jh_group_members(hass, cache) -> dict:
    """jh-r26-group (step 168, review round 26 #8): {group entity: [its member entities]} for every light, switch, fan, cover,
    lock and media player group of the home (the 'entity_id' list Home Assistant's group helper and integration groups show).
    Read once per group order."""
    if "grp26" not in cache:
        out = {}
        for d in ("light", "switch", "fan", "cover", "lock", "media_player"):
            for st in hass.states.async_all(d):
                mem = (getattr(st, "attributes", None) or {}).get("entity_id")
                if isinstance(mem, (list, tuple)):
                    mem = [str(m) for m in mem if isinstance(m, str) and "." in m and m != st.entity_id]
                    if mem:
                        out[st.entity_id] = mem
        cache["grp26"] = out
    return cache["grp26"]


def _jh_expand_groups(hass, ids, members_of, doms, grp, excluded):
    """jh-r26-group (step 168, review round 26 #8): the ids of a group order with every group entity replaced by its members
    (nested groups too). Decision: a group is switched through its MEMBERS, never the group entity besides them: each member
    takes every rule (kind, manual heater, barrier, lock), so no member a rule leaves out is switched through its group and no
    bulb is switched twice; the group itself is skipped ('group', not named). A group that is a setting or that JARVIS
    excludes, or one with no member that exists, stays as it is (the rules decide on it)."""
    out, seen = [], set()

    def add(e, depth):
        if e in seen:
            return
        seen.add(e)
        mem = [m for m in members_of.get(e, ()) if m.split(".", 1)[0] in doms and hass.states.get(m) is not None]
        if mem and depth < 6 and not _jh_is_setting(hass, e) and not excluded(hass, e):
            grp.skipped.append((e, "group"))
            for m in mem:
                add(m, depth + 1)
        else:
            out.append(e)
    for e in ids:
        add(e, 0)
    return out


def jh_group_label(name, why) -> str:
    """How a device left out is named in a reply: 'Porch (hidden)', 'Bath Heater (manual heater)', else its name."""
    tag = {"hidden": _jh_t("jh_lbl_hidden"), "manual": _jh_t("jh_lbl_manual"), "unknown": _jh_t("jh_lbl_unknown"),
           "nogroup": _jh_t("jh_lbl_nogroup")}.get(why)   # jh-r27-allow (step 170)
    return "%s (%s)" % (name, tag) if tag else str(name)


def jh_group_note(hass, grp) -> str:
    """jh-r25-group: what a group reply adds: ' Left out ...: Garage Door, Porch (hidden).' and, for a lock order, the garage
    and gate locks it included. '' when there is nothing to say."""
    out = ""
    # jh-r27-allow (step 170, review round 27 #1): the devices of unknown kind and the ones labelled "never in groups" are said
    # in their own sentence, so the reason is clear
    rest = [x for x in grp.left_out if x[2] not in ("unknown", "nogroup")]
    if rest:
        out += " " + _jh_t("jh_bulk_left_out", names=", ".join(jh_group_label(n, w) for _e, n, w in rest[:8])
                           + (" (+%d)" % (len(rest) - 8) if len(rest) > 8 else ""))
    for why, key in (("unknown", "jh_group_unknown"), ("nogroup", "jh_group_nogroup")):
        some = [n for _e, n, w in grp.left_out if w == why]
        if some:
            out += " " + _jh_t(key, names=", ".join(some[:8]) + (" (+%d)" % (len(some) - 8) if len(some) > 8 else ""))
    if grp.reported:
        out += " " + _jh_t("jh_group_locks_incl", names=", ".join(grp.reported[:6]))
    return out


def jh_group_left_all(grp, h) -> str:
    """The reply when everything of the group was left out."""
    return _jh_t("jh_bulk_left_out_all", h=h, names=", ".join(jh_group_label(n, w) for _e, n, w in grp.left_out[:8]))


async def _jh_confirm_temp(hass, eid, want, wait=4.0):
    """jh-r25-media, step 167 (review round 25 #2): True once the thermostat shows the temperature it was set to (its target,
    or its low / high target), within about 4 s; else _JhUnconfirmed with what it shows, so the reply is "not confirmed"."""
    import asyncio
    try:
        want = float(want)
    except (TypeError, ValueError):
        return True
    shown = None
    for i in range(int(wait / 0.25) + 1):
        a = getattr(hass.states.get(eid), "attributes", None) or {}
        for k in ("temperature", "target_temp_low", "target_temp_high"):
            try:
                if a.get(k) is not None and abs(float(a.get(k)) - want) < 0.05:
                    return True
            except (TypeError, ValueError):
                continue
        shown = a.get("temperature")
        if i * 0.25 >= wait:
            break
        await asyncio.sleep(0.25)
    return _JhUnconfirmed(("set to %s°" % shown) if shown is not None else str(getattr(hass.states.get(eid), "state", "unknown")))


async def _jh_read_back(hass, target, action, vargs, success, sure, before):
    """jh-r26-media (step 168): the read-back of a single value order: a thermostat must show its new temperature (step 167),
    a pause / next track on a player that may not be playing must show it (review round 26 #9); else _JhUnconfirmed."""
    if not success or isinstance(success, _JhUnconfirmed):
        return success
    if action == "set_temp":
        return await _jh_confirm_temp(hass, target, vargs.get("temperature"))
    if action in ("media_pause", "media_next") and not sure:
        return await _jh_confirm_media(hass, target, action, before)
    return success


async def _jh_confirm_media(hass, eid, action, before, wait=4.0):
    """jh-r26-media (step 168, review round 26 #9): True once the player shows the order (a pause: paused, idle, off or
    standby; a next track: another title or content id than before), within about 4 s; else _JhUnconfirmed with its state, so
    the reply is "sent, but not confirmed" (a TV that keeps showing 'on' cannot confirm a pause)."""
    import asyncio
    before = tuple(before or (None, None))
    for i in range(int(wait / 0.25) + 1):
        st = hass.states.get(eid)
        s = str(getattr(st, "state", "unavailable")).lower()
        a = getattr(st, "attributes", None) or {}
        if action == "media_pause" and s in ("paused", "idle", "off", "standby"):
            return True
        if action == "media_next" and any(before) and (a.get("media_content_id"), a.get("media_title")) != before:
            return True
        if i * 0.25 >= wait:
            break
        await asyncio.sleep(0.25)
    return _JhUnconfirmed(str(getattr(hass.states.get(eid), "state", "unknown")))


async def jh_group_act(hass, grp, action, ids=None, args=None):
    """jh-r25-group: THE send of a group order: only the group's own targets (ids, when given, must be among them: anything
    else is dropped, fails closed), through the shared send-and-confirm step. The only caller of jh_act_and_confirm."""
    if not isinstance(grp, JhGroup):
        raise TypeError("a group order acts only on jh_group_targets' result")
    allowed = set(grp.ids)
    ids = [e for e in (grp.ids if ids is None else ids) if e in allowed]
    return await jh_act_and_confirm(hass, action, ids, args)


# jh-r25-kindarg (step 167, review round 25 #14): bulk_control's kind argument is lenient on the WORDS and strict on the targets:
# the shared kind words in every reply language and domain words are understood; a word that names a kind decides strictly
# (only that kind is switched, nothing of it: nothing); an unknown word is the domain's own kind (said so), except for covers,
# where the kind matters ("blinds" never open a window or awning) and an unknown word switches nothing.
JH_KIND_WORDS_MORE = {}
for _jh_k25, _jh_w25 in (
        ("light", "lumière lumières lumiere lumieres lampe lampes luz luces luce luci luzes lâmpada lâmpadas lampada lampadas "
                  "ไฟ หลอดไฟ โคมไฟ"),
        ("fan", "ventilateur ventilateurs ventilador ventiladores ventilatore ventilatori พัดลม"),
        ("tv", "télé tele télévision television televisor televisores televisione televisori televisão ทีวี โทรทัศน์"),
        ("speaker", "enceinte enceintes altavoz altavoces altoparlante altoparlanti alto-falante alto-falantes ลำโพง"),
        ("climate", "termostato termostatos termostati"),
        ("lock", "serrure serrures cerradura cerraduras serratura serrature fechadura fechaduras กุญแจ ล็อค"),
        ("plug", "prise prises enchufe enchufes presa prese tomada tomadas ปลั๊ก"),
        ("blind", "volet volets rideau rideaux persienne persiennes persiana persianas tapparella tapparelle "
                  "tenda tende cortina cortinas estore estores veneziana veneziane ม่าน มู่ลี่"),
        ("window", "fenêtre fenêtres fenetre fenetres ventana ventanas finestra finestre janela janelas lucernaire หน้าต่าง"),
        ("awning", "auvent auvents toldo toldos"),
        ("garage", "garaje garajes"),
        ("gate", "portail portails portón porton portones cancello cancelli portão portao portões"),
        ("door", "porte portes puerta puertas porta portas ประตู")):
    for _jh_x25 in _jh_w25.split():
        JH_KIND_WORDS_MORE.setdefault(_jh_x25, _jh_k25)
# jh-r26-group (step 168, review round 26 #5): inflected and other everyday cover words (de, nl, en, th): "Rollläden", "Rollos",
# "Vorhänge", "rolgordijnen", "screens", "zonwering"
for _jh_k26, _jh_w26 in (
        ("blind", "rollläden rolläden rollladen rolladen rollo rollos raffstore raffstores vorhang vorhänge vorhaenge plissee "
                  "plissees lamellen rolgordijn rolgordijnen vouwgordijn vouwgordijnen jaloezie jaloezieën jaloezieen zonwering "
                  "screens ม่านม้วน ม่านบังแดด"),
        ("window", "dachfenster dakraam dakramen"),
        ("awning", "markies markiezen")):
    for _jh_x26 in _jh_w26.split():
        JH_KIND_WORDS_MORE.setdefault(_jh_x26, _jh_k26)
# words that name a domain rather than a kind ("the switches", "the media players"): the domain's own kind
_JH_DOMAIN_WORDS = frozenset((
    "switch", "switches", "schalter", "schakelaar", "schakelaars", "interrupteur", "interrupteurs", "interruptor",
    "interruptores", "interruttore", "interruttori", "media player", "media players", "player", "players", "device",
    "devices", "geräte", "apparaten", "everything", "all", "input boolean", "input booleans", "toggle", "toggles"))


def jh_kind_arg(domain, word):
    """jh-r25-kindarg: (kind, error) for bulk_control's kind argument. error is a reason to switch nothing, else ''."""
    w = " ".join(_jh_kind_words(word))
    if not w:
        if domain == "cover":
            return None, ("Say which kind of cover the person named in 'kind' (blinds, curtains, windows, awnings, gates, ... "
                          "or 'covers' when they said covers). Nothing was switched.")
        return _JH_KIND_DEFAULT.get(domain), ""
    if w in _JH_DOMAIN_WORDS or w.rstrip("s") in _JH_DOMAIN_WORDS:
        return ("cover", "") if domain == "cover" else (_JH_KIND_DEFAULT.get(domain), "")
    k = jh_kind_of(word) or jh_kind_of(w)
    if k:
        return k, ""
    # jh-r26-group (step 168, review round 26 #5): a kind said in more words ("roller blinds", "window blinds", "bedroom
    # blinds"): the LAST word that names a kind decides, strictly, as jh_bulk_kind does for the instant path; a cover word
    # written as one compound ("Wohnzimmerrollläden", "slaapkamergordijnen"): the cover kind word it ends with
    for x in reversed(w.split()):
        kx = jh_kind_of(x)
        if kx:
            return kx, ""
    if domain == "cover":
        for x in reversed(w.split()):
            for kw, kx in _jh_cover_kind_words():
                if x.endswith(kw):
                    return kx, ""
        return None, ("'%s' is no kind of cover I know. Say one of: blinds, curtains, shades, shutters, windows, skylights, "
                      "awnings, dampers, garage doors, gates, doors (or covers when the person said covers). Nothing was "
                      "switched." % str(word)[:40])
    return _JH_KIND_DEFAULT.get(domain), ""


def _jh_cover_kind_words():
    """jh-r26-group: (word, kind) for every cover kind word of five letters or more, longest first (a compound's ending)."""
    out = [(x, k) for d in (JH_KIND_WORDS, JH_KIND_WORDS_MORE) for x, k in d.items()
           if len(x) >= 5 and " " not in x and "cover" in _JH_KIND_DOMS.get(k, ())]
    return sorted(out, key=lambda p: -len(p[0]))


def _jh_area_id_of(hass, eid) -> str:
    """jh-r23-one: the area id of an entity, or of its device ('' when none)."""
    try:
        from homeassistant.helpers import device_registry as dr, entity_registry as er
        entry = er.async_get(hass).async_get(eid)
        aid = getattr(entry, "area_id", None)
        if not aid and getattr(entry, "device_id", None):
            aid = getattr(dr.async_get(hass).async_get(entry.device_id), "area_id", None)
        return str(aid or "")
    except Exception:  # noqa: BLE001
        return ""


def _jh_is_live(hass, eid) -> bool:
    return str(getattr(hass.states.get(eid), "state", "unavailable")) not in ("unavailable", "unknown")


def _jh_hidden(hass, eid) -> bool:
    """jh-r23-dup: a hidden entity (the plug under a switch_as_x light) is never a second device of the same name."""
    try:
        from homeassistant.helpers import entity_registry as er
        return getattr(er.async_get(hass).async_get(eid), "hidden_by", None) is not None
    except Exception:  # noqa: BLE001
        return False


def _jh_one_of(hass, opts, kind=None):
    """jh-r23-one: THE uniqueness rule. (the one device, the candidates): one candidate is the device; several are a question,
    except in the router path, where the one candidate in the request's room is meant when exactly one is there.
    jh-r24-kindbar (step 166, review round 24 #2): with a kind, only candidates of that kind count (no candidate: (None, []))."""
    opts = list(dict.fromkeys(opts))
    if kind:
        _jh_kc = {}
        opts = [o for o in opts if str(o[0]) == "?" or jh_kind_fits(hass, kind, o[0], o[1], _jh_kc)]
    if len(opts) == 1:
        return opts[0], opts
    area = _JH_REQ_AREA.get()
    if area and opts:
        here = [o for o in opts if _jh_area_id_of(hass, o[0]) == area]
        if len(here) == 1:
            return here[0], opts
    return None, opts


def _jh_same_name(hass, *said, domains=None):
    """jh-r23-dup (step 165, review round 23 #2): every live, shown device (no setting) whose WHOLE name is the name as said
    (the names are tried in the order given: the words as spoken first, then the name without its kind word; the first that
    names any device decides). Home Assistant does not make names unique ("Lamp" in the kitchen and in the office)."""
    for s in said:
        want = {w for w in (_jh_norm_name(s), _jh_norm_name(_jh_said_name(s or ""))) if w}
        out = []
        for dom in (domains or _JH_CONTROL_DOMAINS):
            for st in hass.states.async_all(dom):
                if _jh_is_setting(hass, st.entity_id) or _jh_hidden(hass, st.entity_id) or not _jh_is_live(hass, st.entity_id):
                    continue
                if _jh_norm_name(st.attributes.get("friendly_name") or "") in want:
                    out.append((st.entity_id, st.attributes.get("friendly_name", st.entity_id)))
        if out:
            return out
    return []


def _jh_could_be(hass, name_fragment, spoken, resolved, action):
    """jh-r23-one (step 165, review round 23 #1): every live device a name that is no device's whole name could mean, in the
    resolved device's domain (or the action's): a generic word ("the door", "the blind", "the fan", "the lock") or a part of a
    name ("the front" for Front Door and Front Gate). [] when the words fit one device only. Blind, shade, curtain and shutter
    never mean a garage door, gate or door cover."""
    try:
        frag = _jh_norm_name(_jh_frag(name_fragment))
        if not frag or not resolved:
            return []
        if _jh_same_name(hass, name_fragment, spoken):
            return []   # a device's whole name: _jh_same_name decided (one device, or the which-one question)
        doms = _jh_action_domains(action) or (str(resolved[0]).split(".", 1)[0],)
        kind = _jh_class_of(frag)
        _jh_kc24 = {}   # jh-r24-kindbar (step 166): one heater / switch_as_x read for the whole sweep
        out = []
        for dom in doms:
            for st in hass.states.async_all(dom):
                eid, nm = st.entity_id, str(st.attributes.get("friendly_name") or "")
                if _jh_is_setting(hass, eid) or _jh_hidden(hass, eid) or not _jh_is_live(hass, eid):
                    continue
                if kind:
                    fits = _jh_class_fits(hass, frag, (eid, nm), _jh_kc24)
                else:   # jh-r24-kindbar (step 166, review round 24 #2): a part of a name, and of the kind its last word names
                    fits = ((_jh_word_in(frag, nm.casefold().replace("colour", "color")) or _jh_word_in(frag.replace(" ", "_"), eid))
                            and _jh_class_fits(hass, frag, (eid, nm), _jh_kc24))
                if fits and frag.split()[-1] in _JH_BLIND_WORDS and jh_bulk_barrier(hass, eid, "open") in ("barrier", "kind"):
                    fits = False
                if fits:
                    out.append((eid, st.attributes.get("friendly_name", eid)))
        if resolved not in out and _jh_is_live(hass, resolved[0]) and _jh_class_fits(hass, frag, tuple(resolved), _jh_kc24):
            out.append(tuple(resolved))   # jh-r24-kindbar: the device found counts only when it is of the kind named
        return out if len(out) > 1 else []
    except Exception:  # noqa: BLE001
        return [tuple(resolved), ("?", "?")] if resolved else []   # fail closed: a question, never a guess


def _jh_area_label(hass, eid) -> str:
    """jh-r23-dup: the name of an entity's area (or its device's), '' when none."""
    aid = _jh_area_id_of(hass, eid)
    if not aid:
        return ""
    try:
        from homeassistant.helpers import area_registry as ar
        return next((str(a.name) for a in ar.async_get(hass).async_list_areas() if getattr(a, "id", None) == aid), "")
    except Exception:  # noqa: BLE001
        return ""


def _jh_label_dups(hass, opts):
    """jh-r23-dup: options whose names are the same get their room ("Lamp (Kitchen)", "Lamp (Office)"), so the question can be
    answered; None when two of them still cannot be told apart (same name, same or no room)."""
    names = [str(n) for _e, n in opts]
    out = []
    for eid, nm in opts:
        if names.count(str(nm)) > 1:
            room = _jh_area_label(hass, eid)
            nm = "%s (%s)" % (nm, room) if room else nm
        out.append((eid, nm))
    labels = [n.casefold() for _e, n in out]
    return out if len(set(labels)) == len(labels) else None


def _jh_ask_which(hass, pending_key, action, opts, honorific, remember=True):
    """jh-r23-one: the which-one question for several candidates (kept per conversation, as the other which-one questions);
    the honest refusal when they cannot be told apart. Nothing is switched."""
    opts = None if any(e == "?" for e, _n in opts) else _jh_label_dups(hass, opts)
    if not opts:
        return LocalResult(text=_jh_t("jh_cant_tell", h=honorific), success=False)
    if remember:   # an order with a value (a volume, a temperature) is asked again whole, never run with the value lost
        _JH_PENDING[pending_key] = {"action": action, "options": opts, "at": time.time()}
    _jh_n = [x[1] for x in opts]
    return LocalResult(text=_jh_t("jh_which", h=honorific, opts=", ".join(_jh_n[:-1]), last=_jh_n[-1]), success=False)


def _jh_vague(hass, name_fragment, spoken=""):
    """Jarvis Home patch (2026-11-05): names of every device a vague word could mean, read live.
    "the bathroom" and "the bathroom light" reach this as the same fragment, so the spoken words decide (2026-11-06):
    only a name really said is exact, a bare room word is vague, and "light" or "lamp" leaves the other devices out."""
    frag = _jh_frag(name_fragment)
    if not frag:
        return []
    said = spoken.lower().replace("colour", "color")
    light_word = bool(re.search(rf"\b{re.escape(frag)}\s+(?:lights?|lamps?)\b", said))
    hits, exact, _jh_ex23 = [], [], []
    _jh_kind23 = _jh_class_of(frag)   # jh-r23-dup: "the light", "the lamp" is every light, not the names with that word
    _jh_kc24 = {}   # jh-r24-kindbulk (step 166): one heater / switch_as_x read for the whole sweep
    for dom in ("light", "switch", "climate"):
        for st in hass.states.async_all(dom):
            nm = st.attributes.get("friendly_name") or ""
            low = nm.lower().replace("colour", "color")
            if _jh_is_setting(hass, st.entity_id):
                continue
            if light_word and dom != "light" and not re.search(r"\b(?:lights?|lamps?)\b", low):
                continue
            if low == frag and st.state not in ("unavailable", "unknown") and not _jh_hidden(hass, st.entity_id):
                _jh_ex23.append((st.entity_id, nm))   # jh-r23-dup: one is the device, two or more are a question
                continue
            # jh-r17-vague (step 159, review round 17 #4): the word must start a word of the name ("heater" is not in "Home
            # Theater", "room" is not in "Bathroom"), so a vague word never reaches a device that merely contains its letters
            # jh-r23-dup (step 165, review round 23 #1, #2): a generic word ("the light") means every device of that kind (the
            # Desk Lamp too), and the list is kept by device, so two devices with the same name are both in it
            _jh_fit23 = _jh_class_fits(hass, frag, (st.entity_id, nm), _jh_kc24) if _jh_kind23 else _jh_word_in(frag, low)
            if _jh_fit23 and st.entity_id not in [h[0] for h in hits]:   # jh-r18-word: the one shared rule
                hits.append((st.entity_id, nm))
                if low != frag and _jh_frag(nm) == frag and re.search(rf"\b{re.escape(low)}\b", said):
                    exact.append(nm)
    if _jh_ex23:   # jh-r23-dup (step 165, review round 23 #2): never the first of two devices with the same name
        return _jh_ex23 if len(_jh_ex23) > 1 else []
    if len(exact) == 1:
        return []
    return hits if len(hits) > 1 else []


# Jarvis Home patch (2026-11-06, round 3): a word like fan or door is cut from a name to find "the bedroom" in "the bedroom lamp",
# but it can be the very word that tells two real devices apart.
_JH_KIND_DOMAINS = {"fan": ("fan",), "fans": ("fan",), "lock": ("lock",), "locks": ("lock",), "door": ("lock", "cover"), "doors": ("lock", "cover"),
                    "cover": ("cover",), "covers": ("cover",), "thermostat": ("climate",)}


_JH_KIND_DOMAINS.update({"light": ("light",), "lights": ("light",), "lamp": ("light",), "lamps": ("light",),   # review round 2
                         "heater": ("climate", "switch", "fan", "water_heater"), "heaters": ("climate", "switch", "fan", "water_heater"),
                         "radiator": ("climate", "switch", "fan", "water_heater")})


def _jh_split_change(hass, ents, action):
    """(to switch, already in that state, unavailable) for a list of (entity_id, name) (review round 2: no fake 'Done')."""
    want = {"turn_on": "on", "turn_off": "off"}.get(action)
    todo, same, gone = [], [], []
    for eid, name in ents:
        st = hass.states.get(eid)
        state = getattr(st, "state", "unavailable")
        if state in ("unavailable", "unknown"):
            gone.append(name)
        elif want and state == want:
            same.append(name)
        else:
            todo.append((eid, name))
    return todo, same, gone


def _jh_skip_why(same, gone, action):
    bits = []
    if same:
        bits.append(_jh_t("jh_skip_same", names=", ".join(same[:4]) + (_jh_t("jh_and_more", n=len(same) - 4) if len(same) > 4 else ""),
                          state=_jh_t("jh_journal_" + ("on" if action == "turn_on" else "off"))))
    if gone:
        bits.append(_jh_t("jh_skip_gone", names=", ".join(gone[:4]) + (_jh_t("jh_and_more", n=len(gone) - 4) if len(gone) > 4 else "")))
    return "; ".join(bits)


def _jh_said_name(spoken):
    name = re.sub(r"\s+(?:on|off)$", "", re.sub(r"\s+(?:please|now|for me|jarvis|again)$", "", spoken.lower().strip()))
    return re.sub(r"^(?:my|the)\s+", "", name).replace("colour", "color").strip()


def _jh_action_domains(action):
    """Jarvis Home patch (2026-11-07): the device domains an action can really work on, from the service it would call.
    None when the action works on the device's own domain (turn on, turn off, toggle) or calls nothing."""
    svc = _service_for(action, "x.x")
    return (svc[0],) if svc and action not in ("turn_on", "turn_off", "toggle") else None


def _jh_whole(hass, spoken, domains=None, prefer=None):
    """The one device whose name holds the whole name as said, suffix word included. Only devices the action can work on
    count (2026-11-07: "lock the front door" may never land on a lamp called Front Door), and an exact name in the
    preferred domain wins over an exact name in another domain."""
    name = _jh_said_name(spoken)
    words = name.split()
    if len(words) < 2 or words[-1] not in _JH_KIND_DOMAINS:
        return None
    bare, hits, exact = " ".join(words[:-1]), [], []
    for dom in ("light", "switch", "lock", "cover", "climate", "fan", "media_player"):
        if domains and dom not in domains:
            continue
        for st in hass.states.async_all(dom):
            if _jh_is_setting(hass, st.entity_id):
                continue
            nm = (st.attributes.get("friendly_name") or "").lower().replace("colour", "color")
            found = (st.entity_id, st.attributes.get("friendly_name", st.entity_id))
            if nm == name:
                exact.append(found)
            elif set(words) <= set(nm.split()) or (nm == bare and dom in _JH_KIND_DOMAINS[words[-1]]):
                hits.append(found)
    if exact:   # jh-r23-res (step 165, review round 23 #2): one device of that name (in the preferred domain), else none
        _jh_pref = [x for x in exact if x[0].split(".")[0] == prefer] or exact
        _jh_live23 = [x for x in _jh_pref if _jh_is_live(hass, x[0]) and not _jh_hidden(hass, x[0])] or _jh_pref
        return _jh_one_of(hass, _jh_live23)[0] if len(_jh_live23) > 1 else _jh_live23[0]
    return hits[0] if len(hits) == 1 else None


def _jh_wrong_kind(spoken, resolved):
    """True when the words asked for a fan, a lock or the like and the match is neither that kind nor named so."""
    words = _jh_said_name(spoken).split()
    if len(words) < 2 or words[-1] not in _JH_KIND_DOMAINS:
        return False
    return resolved[0].split(".")[0] not in _JH_KIND_DOMAINS[words[-1]] and words[-1] not in str(resolved[1]).lower().split()


# Is this turn a device command? The truth check in agent.py asks, so a wrong yes costs a good answer and a wrong no lets a claim
# through. It leans hard to no: only the opening word makes an order, so "the heating went off" and a bare "door open" are
# statements. A question is never a command, and chat that begins like one needs a device to be one.
_JH_FILLER_RE = re.compile(r"^(?:(?:ok(?:ay)?|hey|hi|hello|yo|sir|jarvis|please|kindly|just|now|then|and|also|so|go ahead and|graag"
                           r"|alsjeblieft|alstublieft|bitte|por favor)\b[\s,]*)+")
_JH_POLITE_RE = re.compile(r"^(?:could you|can you|would you|will you|i want you to|i need you to|i'd like you to|kun je|kan je|kunt u|wil je|wilt u)\b[\s,]*")
_JH_TAIL_RE = re.compile(r"[\s,]+(?:please|sir|jarvis|now|for me|thanks|thank you|alsjeblieft|alstublieft|graag|bitte|por favor)$")
_JH_ASK_RE = re.compile(r"^(?:tell me|check if|check whether|what|what's|whats|which|who|who's|whose|where|where's|when|why|how|is|are|was|were|am|do|does|did|has|have|had|should"
                        r"|would|could|can|will|shall|isn't|aren't|wat|welke|wie|waar|wanneer|waarom|hoe|staat|ben|zijn|heeft|hebben|kan|kun|moet"
                        r"|ist|sind|warum|wann|wo|wer|comment|pourquoi|quand|quel|quelle|qué|que|cómo|como|cuándo|dónde|cosa|quando|perché)\b")
# Words that make a sentence about something, not an order to it.
_JH_NO_ORDER_RE = re.compile(r"\b(?:don't|do not|never|not|niet|geen|nicht|if|when|als|wanneer|about|over|story|poem|joke|song|essay|letter|talking|praten"
                             r"|verhaal|gedicht|grap|tell|explain\w*|write|translate|remember|remind|what|how|why|wat|hoe|i|i'm|i've|we|ik|wij|you"
                             r"|he|she|they|is|are|was|were|ben|zijn|staat|ist|sind)\b")
_JH_ORDER_VERBS = frozenset(("arm disarm turn switch set dim brighten open close shut lock unlock kill stop start pause resume play mute unmute activate deactivate "
                             "enable disable raise lower toggle zet doe draai sluit schakel dimmen schalte schalt öffne schließe stell dimme "
                             "allume éteins eteins ouvre ferme baisse monte enciende apaga abre cierra accendi spegni apri chiudi "
                             # jh-r9-move-verbs (review round 9): a camera or a fan that is moved is ordered about too
                             "move pan tilt point rotate aim beweeg verplaats richt bewege drehe schwenke déplace tourne mueve gira "
                             "sposta ruota").split())
# Too common to be an order alone: these only open one together with an end word, as in "put the lamp on".
_JH_SOFT_VERBS = frozenset("put make maak mach".split())
_JH_ORDER_ENDS = frozenset("on off up down open shut closed brighter dimmer darker warmer cooler colder hotter higher lower aan uit dicht omhoog omlaag hoger lager kouder an aus auf zu".split())
# Dutch puts the verb last after a polite opening: "kun je de lamp aanzetten".
_JH_ORDER_LAST = frozenset("zetten doen aanzetten uitzetten aandoen uitdoen openen sluiten dimmen".split())
# Kinds of device, not devices: a claim about the blinds is false in a house without blinds too.
_JH_KIND_RE = re.compile(r"\b(?:lights?|lamps?|heat|heating|heaters?|volume|brightness|radiators?|thermostats?|aircon|air conditioning|tv|television|blinds?|curtains?|shutters?|shades?"
                         r"|doors?|gates?|garage|windows?|fans?|locks?|plugs?|sockets?|speakers?|music|vacuum|alarms?"
                         r"|licht|lichten|lampen|verwarming|kachels?|thermostaat|televisie|gordijnen|gordijn|rolluiken|rolluik|zonwering|deuren|deur|poort"
                         r"|ventilator|stekker|muziek|stofzuiger|lampe|lampen|heizung|fernseher|tür|lumi[eè]res?|chauffage|télé|porte|volets?"
                         r"|luz|luces|lámpara|calefacción|puerta|luce|luci|lampada|riscaldamento|porta)\b")
# Jarvis Home patch (2026-11-07, round 2): an alarm for a time is a wake-up alarm or a reminder, and an alarm clock is a clock.
# Neither is the alarm system, so they are taken out before the device words are read; "switch on the alarm" stays a command.
_JH_WAKE_ALARM_RE = re.compile(r"\balarms?\s+clocks?\b|\balarms?\s+(?:for|at|in|om|um|to)\s+(?:the\s+)?"
                               r"(?:\d|half|quarter|noon|midnight|tomorrow|morning|one|two|three|four|five|six|seven|eight|nine|ten"
                               r"|eleven|twelve|fifteen|twenty|thirty|forty|fifty|een|twee|drie|vier|vijf|zes|zeven|acht|negen|tien"
                               r"|elf|twaalf|kwart|morgen|ochtend|eins|zwei|sechs|sieben|neun|zehn|zwölf|halb|viertel|früh)")
# Jarvis Home patch (2026-11-07, round 3 + 4): a wake-up alarm is no device. An alarm clock and "my wake-up / weekday / weekend
# alarm" are always taken out, also after arm or activate ("deactivate my alarm clock"). An alarm with a time is a wake-up alarm
# ("set a 7 am alarm", "zet mijn alarm op 7 uur"), unless the words are about the alarm system: arm, disarm, activate or
# deactivate "the alarm", or a word only the alarm system has ("set the alarm to away mode at 11 pm", "turn off alarm in zone 2").
_JH_ALARM_ORDERS = frozenset("arm disarm activate deactivate".split())
_JH_ALARM_CLOCK_RE = re.compile(r"\balarms?\s+clocks?\b|\b(?:my|mijn)\s+(?:wake[- ]?up|weekday|weekend)\s+alarms?\b")
# Jarvis Home patch (2026-11-08, round 5): "the house / home / burglar / intruder / security alarm" is "the alarm" too, and those
# words are the alarm system's own: "activate the burglar alarm at 11 pm", "deactivate the home alarm at 7".
_JH_THE_ALARM_RE = re.compile(r"\b(?:the|het)\s+(?:(?:house|home|burglar|intruder|security)\s+)?alarms?\b")
# Words only the alarm system has. Away, home and night count as a mode ("to away", "op afwezig", "night mode"), not "at night".
_JH_ALARM_SYSTEM_RE = re.compile(r"\b(?:arm|arms|arming|armed|disarm|disarms|disarming|disarmed|mode|modus|zones?|system|systeem"
                                 r"|panel|security|beveiliging|burglar|intruder)\b|(?<!google\s)(?<!nest\s)\b(?:house|home)\s+alarms?\b"
                                 r"|\b(?:to|on|op|naar)\s+(?:the\s+|de\s+)?(?:away|home|night|afwezig|thuis|nacht)\b")
# "the alarm" switched on or off: "turn the alarm off at 7", "zet het alarm aan om 23 uur", "schakel het alarm in om 23 uur".
_JH_ALARM_SWITCH_RE = re.compile(r"^(?:turn|switch|put|zet|doe)\b.*\b(?:on|off|aan|uit)\b|^schakel\b.*\b(?:in|uit)\b"
                                 r"|\b(?:aan|uit)(?:zetten|doen)\b|\b(?:in|uit)schakelen\b")
# A time: "7 am", "7:30", "at 7", "om 7", "tomorrow", "in 20 minutes". A number that is not a time is a name or a zone: "alarm 1".
_JH_ALARM_WHEN_RE = re.compile(r"\d\s*(?:[:.h]\s*\d|h\b|am\b|pm\b|uur\b|u\b|o'?\s?clock\b|min|hour|hrs?\b|sec|uren\b)"
                               r"|\b(?:at|for|om|op|to|until|till|tot|by|in|around|rond|tegen|voor)\s+(?:about\s+|ongeveer\s+|half\s+)?\d"
                               r"|\b(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s*(?:am|pm)\b|\bo'?\s?clock\b"
                               r"|\b(?:uur|tomorrow|morgen|morgens|morning|mornings|ochtend|ochtends|minutes?|minuten|minuut|hours?)\b")


def _jh_no_wake_alarm(body):
    """The words without a wake-up alarm, so what is left is read for a device. "turn off the alarm" with no time is the alarm
    system. "turn off the alarm at 7" stays a wake-up alarm, as in rounds 1 to 3; "turn off the alarm in 10 minutes" is the alarm
    system: a delay is how the system is set, a wake-up alarm is set for a time."""
    body = " ".join(_JH_ALARM_CLOCK_RE.sub(" ", body).split())
    words = body.split()
    if not words or not re.search(r"\balarms?\b", body):
        return body
    the = _JH_THE_ALARM_RE.search(body)
    if (the and words[0] in _JH_ALARM_ORDERS) or _JH_ALARM_SYSTEM_RE.search(body):
        return body
    switch = bool(the and _JH_ALARM_SWITCH_RE.search(body))
    body = " ".join(_JH_WAKE_ALARM_RE.sub(lambda m: m.group(0) if switch and re.match(r"alarms?\s+in\b", m.group(0)) else " ",
                                          body).split())
    if not switch and re.search(r"\balarms?\b", body) and _JH_ALARM_WHEN_RE.search(body):
        body = " ".join(re.sub(r"\balarms?\b", " ", body).split())
    return body


_JH_WARMTH_RE = re.compile(r"(?:make|get) it (?:a (?:bit|little) |slightly )?(?:warmer|cooler|colder|hotter)|maak het (?:wat |een beetje )?(?:warmer|kouder)")
_JH_ARTICLE = r"\s(?:the|my|a|an|de|het|een)\s"
_JH_ACT_DOMAINS = ("light", "switch", "fan", "cover", "lock", "climate", "media_player", "vacuum", "humidifier", "water_heater", "siren", "scene",
                   "script", "input_boolean", "button", "input_button", "valve", "remote", "lawn_mower", "group", "alarm_control_panel")


def _jh_opening(text):
    """(the words without greetings and politeness, was it a polite request, did it end in a question mark)."""
    said = (text or "").strip().lower().replace("’", "'")
    body = _JH_FILLER_RE.sub("", _jh_clean(said))
    polite = _JH_POLITE_RE.match(body)
    if polite:
        body = _JH_FILLER_RE.sub("", body[polite.end():])
    return body, bool(polite), said.endswith("?")


def is_question(text):
    body, polite, mark = _jh_opening(text)
    return mark or polite or bool(_JH_ASK_RE.match(body))


_JH_CAMERA_KIND_RE = re.compile(r"\b(?:cameras?|cams?|camera's|kamera|kameras|caméras?|cámaras?|telecamera)\b")   # jh-r9-move-verbs


def _jh_names_device(hass, body):
    """A kind of device, or the name of a real device or room, read live."""
    # Jarvis Home patch (2026-11-07): "an alarm for 7" is a wake-up time. A reminder or timer set for it is not a device command,
    # so device_command takes those words out first (round 2). An "alarm" that is left is the alarm system, in every home.
    if _JH_KIND_RE.search(body) or _JH_CAMERA_KIND_RE.search(body):   # review round 9: a camera that is moved too
        return True
    names = []
    for dom in _JH_ACT_DOMAINS + ("camera",):
        names += [str(s.attributes.get("friendly_name") or "") for s in hass.states.async_all(dom) if not _jh_is_setting(hass, s.entity_id)]
    try:
        from homeassistant.helpers import area_registry as areg
        for area in areg.async_get(hass).async_list_areas():
            names += [area.name, *(getattr(area, "aliases", None) or ())]
    except Exception:
        pass
    # "set the mood in the bedroom" is about the room, not an order to it, and "sun's up" is no device called Sun
    hay = re.sub(_JH_ARTICLE, " ", body)
    ears = _jh_sound(hay)
    def said(name, words):
        return len(name) >= 3 and re.search(rf"(?<!\bin )(?<!\bat )(?<!\bto )(?<!\bof )(?<!\bfor )(?<!\bwith )(?<!\bfrom )\b{re.escape(name)}\b(?!['\u2019])", words)
    return any(said(n.lower(), hay) or said(_jh_sound(n), ears) for n in names)


def _jh_local_order(hass, body, aliases):
    """The local command patterns, whole sentence only, with a real target where they name one."""
    if _JH_WARMTH_RE.fullmatch(body) or any(re.fullmatch(p[0], body) for p in _BULK_PATTERNS):
        return True
    for pattern, action, hint in _INTENT_PATTERNS:
        match = re.fullmatch(pattern, body)
        if not match:
            continue
        if action in _MEDIA_ACTIONS or action == "set_temp":
            return True
        if action == "scene":
            if match.group(1) and _jh_scene_candidates(hass, match.group(1)):   # jh-r24-res: one or several
                return True
        elif action in ("turn_on", "turn_off", "dim", "brighten", "lock", "unlock", "open", "close", "set_temp_named"):
            name = match.group(1).strip()
            if name in _JH_PRONOUNS:
                return True
            found = _find_entity(hass, name, hint, aliases) or _find_entity(hass, name, None, aliases)
            if found and found[0].split(".")[0] in _JH_ACT_DOMAINS:
                return True
            if any(x[0].split(".")[0] in _JH_ACT_DOMAINS for x in _jh_sound_many(hass, name, None)):
                return True
    return False


def device_command(hass, text, aliases=None):
    """True when these words order a device about. By the local patterns, or, in any language, by an action word that opens the
    sentence (after please, the assistant's name or a polite opening) and a device or room name after it. Nothing else counts:
    a sentence that opens with its own subject is a statement, and missing a command costs less than replacing ordinary chat."""
    body, polite, _mark = _jh_opening(text)
    if not polite and _JH_ASK_RE.match(body):
        return False
    body = _normalize(body)
    while _JH_TAIL_RE.search(body):
        body = _JH_TAIL_RE.sub("", body)
    body = _jh_no_wake_alarm(body)   # Jarvis Home patch (2026-11-07, round 2 + 3): no wake-up alarm; arm and disarm are orders
    if not body:
        return False
    if _jh_local_order(hass, body, aliases):
        return True
    words = body.split()
    if words[0] in _JH_ORDER_VERBS or (words[0] in _JH_SOFT_VERBS and words[-1] in _JH_ORDER_ENDS):
        target = " ".join(words[1:])   # "lock in the plan": the verb itself is no device
    elif polite and words[-1] in _JH_ORDER_LAST:
        target = body
    else:
        return False
    return not _JH_NO_ORDER_RE.search(body) and _jh_names_device(hass, target)


def _jh_scene_cands_all(hass, name_fragment):
    """jh-r24-res (step 166, review round 24 #6): every scene or script the words could mean, from the first rule that finds
    any (the whole name, then the words in a name or id, then by sound): [] for none, one, or several (a scene and a script
    both called "Good Night"), so a caller can tell 'none' from 'several'. _find_scene_or_script gives the one only."""
    fragment = name_fragment.lower().strip()
    for suffix in ("scene", "script", "routine", "mode"):
        fragment = re.sub(rf"\s+{suffix}$", "", fragment)
    fragment = fragment.strip()
    # jh-r20-exact (step 162, review round 20 #1): a scene or script whose whole name is the words wins over one that only holds
    # them ("activate sunset" is the "Sunset" scene, not "Living Room Savanna sunset"): only the named target is changed
    # jh-r23-res (step 165, review round 23 #2): every match is collected; a scene or script runs only when it is the ONE that
    # fits (the whole name first, then the words in a name or id), never the first of two
    _jh_sx = [(state.entity_id, state.attributes.get("friendly_name", state.entity_id), dtype)
              for domain, dtype in [("scene", "scene"), ("script", "script")] for state in hass.states.async_all(domain)
              if fragment and _jh_norm_name(state.attributes.get("friendly_name") or "") == _jh_norm_name(fragment)]
    if _jh_sx:
        return _jh_sx
    _jh_sp = [(state.entity_id, state.attributes.get("friendly_name", state.entity_id), dtype)
              for domain, dtype in [("scene", "scene"), ("script", "script")] for state in hass.states.async_all(domain)
              if ((state.attributes.get("friendly_name") or "").lower() == fragment
                  or _jh_word_in(fragment, (state.attributes.get("friendly_name") or "").lower())   # jh-r18-word
                  or _jh_word_in(fragment.replace(" ", "_"), state.entity_id))]
    if _jh_sp:
        return _jh_sp
    _jh_heard = _jh_sound_hits(hass, fragment, ("scene", "script"))
    return [(*_jh_h, _jh_h[0].split(".")[0]) for _jh_h in _jh_heard]


def _jh_scene_candidates(hass, name_fragment, said_kind=None):
    """jh-r24-res: the candidates of _jh_scene_cands_all; when the words end in "scene" or "script" ("good night scene") and
    several fit, only those of that kind (all of them when none is of that kind)."""
    found = _jh_scene_cands_all(hass, name_fragment)
    said = re.search(r"\s(scene|script)$", " " + str(name_fragment or "").lower().strip())
    said = said_kind or (said.group(1) if said else None)   # jh-r25-scene (step 167): also when the pattern took it off
    if said and len(found) > 1:
        return [x for x in found if x[2] == said] or found
    return found


def _find_scene_or_script(hass, name_fragment):
    """The ONE scene or script the words mean, else None (none, or several: see _jh_scene_candidates)."""
    found = _jh_scene_candidates(hass, name_fragment)
    return found[0] if len(found) == 1 else None


def _jh_scene_which(hass, pending_key, cands, honorific):
    """jh-r24-res: the which-one question for several scenes / scripts of one name, each with its kind ("Good Night (scene)
    or Good Night (script)?"), so "the scene" answers it; the answer runs the one chosen. Nothing is run now."""
    return _jh_ask_which(hass, pending_key, "activate", [(eid, "%s (%s)" % (fname, dtype)) for eid, fname, dtype in cands],
                         honorific)


# ── Action execution ────────────────────────────────────────────────────────

def _service_for(action, entity_id):
    """Map a fast-path action to the (domain, service) it would call, or None if
    it isn't a direct actuation. Mirrors _execute_action so the confirmation
    pre-check matches exactly what would run."""
    dom = entity_id.split(".")[0] if entity_id else ""
    if action in ("turn_on", "turn_off", "toggle"):
        return (dom, action)
    fixed = {
        "lock": ("lock", "lock"), "unlock": ("lock", "unlock"),
        "open": ("cover", "open_cover"), "close": ("cover", "close_cover"),
        "dim": ("light", "turn_on"), "brighten": ("light", "turn_on"),
        "media_pause": ("media_player", "media_pause"),
        "media_play": ("media_player", "media_play"),
        "media_next": ("media_player", "media_next_track"),
        "volume_up": ("media_player", "volume_up"),
        "volume_down": ("media_player", "volume_down"),
        "mute": ("media_player", "volume_mute"),
        "unmute": ("media_player", "volume_mute"),
        "set_temp": ("climate", "set_temperature"),
        "set_temp_named": ("climate", "set_temperature"),
        "volume_set": ("media_player", "volume_set"),
    }
    return fixed.get(action)


def _needs_confirmation(hass, action, entity_id="") -> bool:
    """Whether this fast-path action would require confirmation. Protected
    actions (unlock, open a garage/cover, disarm, …) are deferred to the agent
    rather than actuated here, so the same authorization gate that guards the
    agent's tools also guards the fast-path — the fast-path never becomes a
    second, unguarded way to unlock a door."""
    svc = _service_for(action, entity_id)
    if not svc:
        return False
    try:
        from . import policy
        return bool(policy.requires_confirmation(hass, svc[0], svc[1], entity_id))
    except Exception:
        return False


async def _seam_execute(hass, domain: str, service: str, data: dict, *,
                        blocking: bool = True, action: str = "") -> bool:
    """Route a local-engine actuation through the **universal actuator seam**
    (MCU Phase H) — so the offline fast-path is planned, event-published and
    journaled like every other actuation instead of a bare ``hass.services``
    call. Behaviour-preserving: it performs the same service call (same
    blocking), just through the kernel planner, and returns True on success /
    False on failure exactly as the direct call's try/except did. Never raises."""
    if _JH_QUERY_ONLY.get():   # jh-no-act-on-questions: a question never switches anything
        raise _JHNoAct()
    _jh_act_guard()   # jh-r19-tail (step 161): no order with a time, condition or negation tail is acted on here
    entity_id = str(data.get("entity_id", "")) if isinstance(data, dict) else ""
    capability = f"{domain}.{service}"
    try:
        from . import actuation
        areq = actuation.request(capability, entity_id, params=data,
                                 action=(action or service))
        ok, _detail = await actuation.execute_actuator(
            hass, capability=capability, entity_id=entity_id, domain=domain,
            service=service, data=dict(data), action=(action or service),
            areq=areq, verify=None, blocking=blocking)
        return bool(ok)
    except Exception as exc:   # pragma: no cover - defensive
        _LOGGER.warning("Local seam execute failed for %s.%s (%s): %s",
                        domain, service, entity_id, exc)
        return False


# jh-r11-confirm (review round 11): an instant action with an end state is only done once the device shows it, as control_device
# checks on the AI path (up to about 4 s); otherwise the reply says it was sent but not confirmed, and it is never counted
_JH_EXPECT_STATE = {"turn_on": ("on",), "turn_off": ("off",), "lock": ("locked",), "unlock": ("unlocked",), "open": ("open",),
                    "close": ("closed",)}


class _JhUnconfirmed:
    """The call went out, but the device did not reach the state in time. Falsy, so it is never counted as done."""
    def __init__(self, state):
        self.state = state

    def __bool__(self):
        return False


async def _jh_confirm_all(hass, action, ids, wait=4.0):
    """(confirmed ids, {unconfirmed id: its state}) after a call, waiting up to `wait` seconds for all of them together."""
    want = _JH_EXPECT_STATE.get(action)
    ids = list(ids or [])
    if not want or not ids:
        return ids, {}
    import asyncio
    try:
        from .agent import _jh_reached
    except Exception:  # noqa: BLE001
        def _jh_reached(eid, expected, state):
            return str(state).lower() in expected
    left = list(ids)
    for i in range(int(wait / 0.25) + 1):
        left = [e for e in left if not ((st := hass.states.get(e)) is not None and _jh_reached(e, want, st.state))]
        if not left or i * 0.25 >= wait:
            break
        await asyncio.sleep(0.25)
    return [e for e in ids if e not in left], {e: str(getattr(hass.states.get(e), "state", "unknown")) for e in left}


def _jh_unconfirmed_note(hass, unc) -> str:
    """' Not confirmed: Hall Lamp (off), Desk (unavailable).' for a room or group order, or ''."""
    if not unc:
        return ""
    names = ", ".join(f"{_jh_name_of(hass.states.get(e)) if hass.states.get(e) is not None else e} ({s})" for e, s in unc.items())
    return " " + _jh_t("jh_not_confirmed", names=names)


async def jh_act_and_confirm(hass, action, ids, args=None):
    """Review round 12 (jh-r12-act): THE send-and-confirm step of every instant group order (room, domain, everything, good
    night): each device is sent the action, then all are checked together (about 4 s in total). (confirmed ids, {unconfirmed
    id: its state}, {failed or refused id: "manual" or its state}) (review round 13)."""
    _jh_act_guard()   # jh-r19-tail (step 161): before the first device of a group order
    sent, failed = [], {}
    for e in ids:   # review round 13 (jh-r13-failed): a send that failed or was refused (a manual heater) is kept and named
        if await _execute_action(hass, action, e, dict(args or {}), confirm=False):
            sent.append(e)
        else:
            failed[e] = "manual" if _jh_is_manual(hass, e) else str(getattr(hass.states.get(e), "state", "unknown"))
    conf, unc = await _jh_confirm_all(hass, action, sent)
    return conf, unc, failed


def jh_group_text(hass, done_text, confirmed, unc, h="sir", failed=None):
    """Review round 12 (jh-r12-act): the reply of a group order. The done line only when at least one device confirmed it, then
    the devices that did not; with none confirmed, only the honest line and those devices (never "Done"). Review round 13: the
    failed or refused sends are named too (a manual heater with its own note), and with nothing confirmed there is no done line
    at all, also when every send failed."""
    failed = failed or {}
    notes = _jh_unconfirmed_note(hass, unc) + _jh_failed_note(hass, failed)
    if not confirmed and (unc or failed):
        return _jh_t("jh_none_confirmed" if unc else "jh_none_switched", h=h) + notes
    return done_text + notes


def _jh_failed_note(hass, failed) -> str:
    """' Not switched: Orb (on). <the manual-heater note>' for the sends that failed or were refused, or ''."""
    if not failed:
        return ""
    def name(e):
        st = hass.states.get(e)
        return _jh_name_of(st) if st is not None else e
    plain = ", ".join(f"{name(e)} ({s})" for e, s in failed.items() if s != "manual")
    out = (" " + _jh_t("jh_not_switched", names=plain)) if plain else ""
    for e in [e for e, s in failed.items() if s == "manual"]:
        out += " " + _jh_manual_note(hass, e)
    return out


def jh_single_result(action, fname, result, args=None, h="sir", lang="en"):
    """Review round 12 (jh-r12-act): the reply of a single-device order. A device that did not show the state is "sent, but not
    confirmed (it is <state>)", never a false "I wasn't able to"."""
    if isinstance(result, _JhUnconfirmed):
        return LocalResult(text=_jh_t("jh_state_unconfirmed", name=fname, h=h, state=result.state), success=False)
    return LocalResult(text=_resp(action, fname, result, args or {}, h, lang), success=bool(result))


async def _execute_action(hass, action, entity_id, args, confirm=True):
    if _JH_QUERY_ONLY.get():   # jh-no-act-on-questions: also before the seam
        raise _JHNoAct()
    _jh_act_guard()   # jh-r19-tail (step 161)
    result = await _jh_execute_action(hass, action, entity_id, args)
    if result and confirm and action in _JH_EXPECT_STATE:   # jh-r11-confirm
        _ok, unc = await _jh_confirm_all(hass, action, [entity_id])
        if unc:
            return _JhUnconfirmed(unc[entity_id])
    return result


async def _jh_execute_action(hass, action, entity_id, args):
    try:
        domain = entity_id.split(".")[0]
        svc_map = {
            "turn_on": (domain, "turn_on"), "turn_off": (domain, "turn_off"),
            "toggle": (domain, "toggle"), "lock": ("lock", "lock"),
            "unlock": ("lock", "unlock"), "open": ("cover", "open_cover"),
            "close": ("cover", "close_cover"), "dim": ("light", "turn_on"),
            "brighten": ("light", "turn_on"),
            "media_pause": ("media_player", "media_pause"),
            "media_play": ("media_player", "media_play"),
            "media_next": ("media_player", "media_next_track"),
            "volume_up": ("media_player", "volume_up"),
            "volume_down": ("media_player", "volume_down"),
            "mute": ("media_player", "volume_mute"),
            "unmute": ("media_player", "volume_mute"),
        }

        if action in svc_map:
            svc_domain, svc_name = svc_map[action]
            svc_data = {"entity_id": entity_id}
            if action == "dim" and "brightness_pct" in args:
                svc_data["brightness_pct"] = args["brightness_pct"]
            elif action == "brighten":
                svc_data["brightness_pct"] = 100
            elif action == "mute":
                svc_data["is_volume_muted"] = True
            elif action == "unmute":
                svc_data["is_volume_muted"] = False
            return await _seam_execute(hass, svc_domain, svc_name, svc_data,
                                       blocking=True, action=action)
        elif action in ("set_temp", "set_temp_named"):
            temp = args.get("temperature")
            if temp is not None:   # a 0° setpoint is valid — don't drop it on falsy 0
                return await _seam_execute(
                    hass, "climate", "set_temperature",
                    {"entity_id": entity_id, "temperature": float(temp)},
                    blocking=True, action="set_temperature")
        elif action == "volume_set":
            level = args.get("volume_level", 50)
            return await _seam_execute(
                hass, "media_player", "volume_set",
                {"entity_id": entity_id, "volume_level": level / 100.0},
                blocking=True, action="volume_set")
        return False
    except Exception as exc:
        _LOGGER.warning("Local action failed for %s: %s", entity_id, exc)
        return False


# ── Response generation ──────────────────────────────────────────────────────

def _resp(action, fname, success, args=None, h="sir", lang="en"):
    from . import local_i18n
    if not success:
        # JARVIS reports failure calmly and precisely, no hand-wringing.
        return local_i18n.t("resp_fail", lang,
                            action=action.replace('_', ' '), name=fname, h=h)

    # Understated lead-ins, MCU style. Varied so confirmations never sound
    # canned. Each is something JARVIS would actually say.
    ack = _pick(local_i18n.leadins("ack", lang))
    sec = _pick(local_i18n.leadins("sec", lang))

    bp = (args or {}).get('brightness_pct', '?')
    temp = (args or {}).get('temperature', '?')
    vol = (args or {}).get('volume_level', '?')

    _KNOWN = {
        "turn_on", "turn_off", "toggle", "lock", "unlock", "open", "close",
        "dim", "brighten", "set_temp", "set_temp_named", "media_pause",
        "media_play", "media_next", "volume_up", "volume_down", "volume_set",
        "mute", "unmute",
    }
    key = action if action in _KNOWN else "resp_fallback"
    return local_i18n.t(key, lang, ack=ack, sec=sec, h=h, name=fname,
                        bp=bp, temp=temp, vol=vol, action=action)


def _query_resp(hass, action, entity_id, fname, h="sir", lang="en"):
    from . import local_i18n
    if action == "query_time":
        from homeassistant.util import dt as dt_util
        return local_i18n.t("query_time", lang,
                            time=local_i18n.format_time(dt_util.now(), lang), h=h)
    if action == "query_date":
        from homeassistant.util import dt as dt_util
        return local_i18n.t("query_date", lang,
                            date=local_i18n.format_date(dt_util.now(), lang), h=h)
    if action == "greeting":
        from homeassistant.util import dt as dt_util
        g = local_i18n.greet_word(dt_util.now().hour, lang)
        return _pick([local_i18n.t("greet_plain", lang, g=g, h=h),
                      local_i18n.t("greet_help", lang, g=g, h=h),
                      local_i18n.t("greet_disposal", lang, g=g, h=h)])
    if action == "thanks":
        return _pick([local_i18n.t("thanks_1", lang, h=h),
                      local_i18n.t("thanks_2", lang, h=h),
                      local_i18n.t("thanks_3", lang, h=h)])
    if action in ("query_temp", "query_state") and _jh_local_hidden(hass, entity_id):   # jh-r28-private (step 171)
        return _jh_private_reply(hass)
    if action == "query_temp":
        state = hass.states.get(entity_id)
        if state:
            # Jarvis Home patch (2026-11-08): a thermostat or heater reports its room temperature in current_temperature, not in
            # its state ("off", "heat"), and a value that is no number is never read out as a temperature ("off°")
            value = state.attributes.get("current_temperature") if entity_id.split(".")[0] in ("climate", "water_heater") else state.state
            try:
                float(value)
            except (TypeError, ValueError):
                return local_i18n.t("jh_temp_none", lang, name=fname, h=h)
            unit = state.attributes.get("unit_of_measurement", "°")
            return local_i18n.t("query_temp", lang, name=fname,
                                state=value, unit=unit, h=h)
        return local_i18n.t("query_temp_fail", lang, name=fname, h=h)
    if action == "query_state":
        state = hass.states.get(entity_id)
        if state:
            return local_i18n.t("query_state", lang, name=fname,
                                state=local_i18n.state_word(state.state, lang), h=h)
        return local_i18n.t("query_state_fail", lang, name=fname, h=h)
    if action == "status":
        return _home_status(hass, h, lang)
    return ""


def _jh_offline(hass):
    """Jarvis Home patch (2026-11-06): door, window and lock devices with no usable state. An all-clear must
    not rest on them."""
    found = [s for s in hass.states.async_all("binary_sensor")
             if s.attributes.get("device_class") in ("door", "window", "garage_door")]
    found += list(hass.states.async_all("cover")) + list(hass.states.async_all("lock"))
    return [s.attributes.get("friendly_name", s.entity_id) for s in found
            if s.state in ("unavailable", "unknown", "jammed")]


def _home_status(hass, h="sir", lang="en"):
    from . import local_i18n
    lights_on = sum(1 for s in hass.states.async_all("light") if s.state == "on")
    locks_ul = sum(1 for s in hass.states.async_all("lock") if s.state == "unlocked")
    doors_open = sum(1 for s in hass.states.async_all("binary_sensor")
                     if s.attributes.get("device_class") == "door" and s.state == "on")
    people = sum(1 for s in jh_residents(hass, hass.states.async_all("person")) if s.state == "home")   # jh-residents (C2)
    parts = [local_i18n.t("status_header", lang, h=h)]
    parts.append(local_i18n.t("status_lights", lang, n=lights_on,
                              noun=local_i18n.noun("light", lights_on, lang)))
    _jh_dark = _jh_offline(hass)
    _jh_locks = hass.states.async_all("lock")  # Jarvis Home patch: only report locks that exist
    if locks_ul:
        parts.append(local_i18n.t("status_locks_unlocked", lang, n=locks_ul,
                                  noun=local_i18n.noun("lock", locks_ul, lang)))
    elif _jh_locks and all(s.state == "locked" for s in _jh_locks):
        parts.append(local_i18n.t("status_locks_ok", lang))
    if doors_open:
        parts.append(local_i18n.t("status_doors", lang, n=doors_open,
                                  noun=local_i18n.noun("door", doors_open, lang)))
    if _jh_dark:
        parts.append(local_i18n.t("jh_not_reporting", lang, items=", ".join(_jh_dark)))
    parts.append(local_i18n.t("status_people", lang, n=people,
                              noun=local_i18n.noun("person", people, lang)))
    return " ".join(parts)


# ── Contextual queries ──────────────────────────────────────────────────────

async def _jh_who_home(hass, h="sir", lang="en"):
    """Review round 7: who is home, by the where-is rule for the asker: only the people this login may know about; voice
    with no login (or a login that may see nobody at home) gets the where-is refusal."""
    from . import local_i18n
    asker = _JH_ASKER.get()
    try:
        from .presence import jh_hidden_people, _jh_refusal
        hidden = await jh_hidden_people(hass, asker or "")
    except Exception:
        return None
    home = [s for s in hass.states.async_all("person") if s.state == "home"]
    seen = [s.attributes.get("friendly_name", s.entity_id) for s in home if s.entity_id not in hidden]
    if not asker or (home and not seen):
        return _jh_refusal(hass)
    if not seen:
        return local_i18n.t("who_none", lang, h=h)
    if len(seen) == 1:
        return local_i18n.t("who_one", lang, name=seen[0], h=h)
    return local_i18n.t("who_many", lang, names=", ".join(seen[:-1]), last=seen[-1], h=h)


def _ctx_query(hass, qtype, h="sir", area_match="", lang="en"):
    from . import local_i18n
    if qtype == "who_home":
        ppl = [s.attributes.get("friendly_name", s.entity_id)   # jh-r28-private (step 171): the where-is rule
               for s in hass.states.async_all("person") if s.state == "home" and not _jh_local_hidden(hass, s.entity_id)]
        if not ppl:
            return local_i18n.t("who_none", lang, h=h)
        if len(ppl) == 1:
            return local_i18n.t("who_one", lang, name=ppl[0], h=h)
        return local_i18n.t("who_many", lang,
                            names=", ".join(ppl[:-1]), last=ppl[-1], h=h)

    if qtype == "what_open":
        items = []
        for s in hass.states.async_all("binary_sensor"):
            dc = s.attributes.get("device_class", "")
            if dc in ("door", "window", "garage_door") and s.state == "on":
                items.append(s.attributes.get("friendly_name", s.entity_id))
        # Jarvis Home patch (2026-11-07): only a closed cover and a locked lock are secure. A cover on its way or a latch that is
        # open is named with its state, so the all-clear never rests on them. Offline ones stay under "not reporting".
        for s in hass.states.async_all("cover"):
            if s.state == "open":
                items.append(s.attributes.get("friendly_name", s.entity_id))
            elif s.state not in ("closed", "unavailable", "unknown"):
                items.append(f"{s.attributes.get('friendly_name', s.entity_id)} ({local_i18n.state_word(s.state, lang)})")
        for s in hass.states.async_all("lock"):
            if s.state == "unlocked":
                items.append(s.attributes.get("friendly_name", s.entity_id)
                             + local_i18n.t("open_unlocked_suffix", lang))
            elif s.state not in ("locked", "unavailable", "unknown", "jammed"):
                items.append(f"{s.attributes.get('friendly_name', s.entity_id)} ({local_i18n.state_word(s.state, lang)})")
        if not items:
            if not (hass.states.async_all("lock") or hass.states.async_all("cover") or any(
                    s.attributes.get("device_class") in ("door", "window", "garage_door")
                    for s in hass.states.async_all("binary_sensor"))):
                return local_i18n.t("jh_no_sensors", lang, h=h)
            if _jh_offline(hass):
                return local_i18n.t("jh_open_none_dark", lang, h=h, items=", ".join(_jh_offline(hass)))
            return local_i18n.t("open_none", lang, h=h)
        _jh_tail = (" " + local_i18n.t("jh_not_reporting", lang, items=", ".join(_jh_offline(hass)))) if _jh_offline(hass) else ""
        return local_i18n.t("open_list", lang, items=", ".join(items)) + _jh_tail

    if qtype == "lights_on":
        on = [s.attributes.get("friendly_name", s.entity_id)
              for s in hass.states.async_all("light") if s.state == "on"]
        if not on:
            return local_i18n.t("lights_none", lang, h=h)
        c = len(on)
        listing = ", ".join(on[:5])
        return local_i18n.t("lights_list", lang, c=c,
                            noun=local_i18n.noun("light", c, lang),
                            listing=listing, ellipsis=("..." if c > 5 else ""))

    if qtype == "energy":
        for s in hass.states.async_all("sensor"):
            if s.attributes.get("device_class") == "power" and "total" in s.entity_id.lower():
                unit = s.attributes.get("unit_of_measurement", "W")
                return local_i18n.t("energy", lang, state=s.state, unit=unit, h=h)
        return None   # Jarvis Home patch: no whole-house power sensor - JARVIS answers (energy_status adds up the devices)

    if qtype == "weather":
        for s in hass.states.async_all("weather"):
            temp = s.attributes.get("temperature", "—")
            humidity = s.attributes.get("humidity", "—")
            condition = s.state.replace("_", " ")
            return local_i18n.t("weather", lang, condition=condition,
                                temp=temp, humidity=humidity, h=h)
        for s in hass.states.async_all("sensor"):
            if ("outdoor" in s.entity_id.lower() or "outside" in s.entity_id.lower()):
                if s.attributes.get("device_class") == "temperature":
                    unit = s.attributes.get("unit_of_measurement", "°")
                    return local_i18n.t("weather_outdoor", lang,
                                        state=s.state, unit=unit, h=h)
        return local_i18n.t("weather_none", lang, h=h)

    if qtype == "area_devices" and area_match:
        try:
            from homeassistant.helpers import (
                area_registry as areg, entity_registry as er, device_registry as dr)
            area_reg = areg.async_get(hass)
            ent_reg = er.async_get(hass)
            dev_reg = dr.async_get(hass)
            target = None
            for area in area_reg.async_list_areas():
                if _jh_word_in(area_match.lower(), area.name.lower()):   # jh-r18-word
                    target = area
                    break
            if not target:
                return local_i18n.t("area_none", lang, area=area_match, h=h)
            devs = []
            for entry in ent_reg.entities.values():
                in_area = entry.area_id == target.id
                if not in_area and entry.device_id:
                    device = dev_reg.async_get(entry.device_id)
                    in_area = device and device.area_id == target.id
                if in_area:
                    state = hass.states.get(entry.entity_id)
                    if state and not _jh_local_hidden(hass, entry.entity_id):   # jh-r28-private (step 171)
                        devs.append(f"{state.attributes.get('friendly_name', entry.entity_id)} "
                                    f"({local_i18n.state_word(state.state, lang)})")
            if not devs:
                return local_i18n.t("area_empty", lang, name=target.name, h=h)
            listing = ", ".join(devs[:10])
            more = (local_i18n.t("area_more", lang, n=len(devs) - 10)
                    if len(devs) > 10 else "")
            return local_i18n.t("area_list", lang, name=target.name, n=len(devs),
                                noun=local_i18n.noun("device", len(devs), lang),
                                listing=listing, more=more)
        except Exception:
            pass
    return None


# ── Main entry point ─────────────────────────────────────────────────────────

def _output_lang(hass, lang=None):
    """The household output language as a primary subtag ('de'), 'en' default.
    Steers the zero-API local replies the same way the LLM path is steered by
    language.language_directive. Never raises.

    ``lang`` is the per-request pipeline language (``user_input.language``) when
    the caller has one — a command spoken to a German satellite. It is passed
    straight through to :func:`language.configured_language`, where it is
    priority #1, so a German satellite is confirmed in German even when JARVIS's
    ``output_language`` is Auto and the system language is English (#522). A
    None ``lang`` falls through to the output_language/ui_language/system order
    exactly as before."""
    try:
        from . import language
        return language.configured_language(hass, lang)
    except Exception:
        return "en"


_JH_COLOR_RE = re.compile(r"^(?:(?:hey\s+)?jarvis[,\s]+)?(?:please\s+|could you\s+|can you\s+|would you\s+)*(?:switch|set|change|make|turn|put)\s+(?:the\s+)?(.+?)\s+(?:to\s+|in\s+)?([a-z]+)(?:\s+colou?r)?$")


def _jh_shows(state, want) -> bool:
    """Jarvis Home patch (2026-11-06): the lamp is on and reports roughly the asked colour. Lamps round
    colours, so a wide margin only rejects a lamp that still shows something clearly different."""
    if state is None or state.state != "on":
        return False
    got = state.attributes.get("rgb_color")
    if not got:
        return True
    return sum((int(a) - int(b)) ** 2 for a, b in zip(got, want)) <= 130 ** 2


async def _jh_try_color(hass, normalized, honorific, pending_key=""):
    """Jarvis Home patch (2026-11-05): colour commands, read from the lamp's real abilities."""
    said = _jh_opening(normalized)[0]   # "Jarvis, switch the lamp green, please" is the same colour command
    while _JH_TAIL_RE.search(said):
        said = _JH_TAIL_RE.sub("", said)
    m = _JH_COLOR_RE.match(said)
    if not m or m.group(1).split()[0] in ("on", "off") or m.group(2) in ("please", "now") or m.group(1).strip() in _JH_PRONOUNS:
        return None
    from homeassistant.util.color import color_name_to_rgb
    try:
        want = color_name_to_rgb(m.group(2))
    except ValueError:
        return None
    found = _find_entity(hass, m.group(1), "light")
    if not found:
        _jh_ears = _jh_sound_many(hass, m.group(1), None, ["light"])
        if not _jh_ears:
            return None
        _JH_PENDING[pending_key] = {"action": "turn_on", "colour": m.group(2), "options": _jh_ears, "at": time.time()}
        _jh_n = [x[1] for x in _jh_ears]
        return LocalResult(text=_jh_t("jh_which", h=honorific, opts=", ".join(_jh_n[:-1]), last=_jh_n[-1]), success=False)
    entity_id, fname = found
    state = hass.states.get(entity_id)
    if state is None or state.state in ("unavailable", "unknown"):
        return LocalResult(text=_jh_t("jh_offline_nochange", name=fname, h=honorific), success=False)
    modes = set(state.attributes.get("supported_color_modes") or [])
    if not modes & {"hs", "rgb", "rgbw", "rgbww", "xy"}:
        return LocalResult(text=_jh_t("jh_no_colours", name=fname, h=honorific), success=False)
    if _needs_confirmation(hass, "turn_on", entity_id):
        return None
    colour = m.group(2)
    if not await _seam_execute(hass, "light", "turn_on", {"entity_id": entity_id, "color_name": colour},
                               blocking=True, action="turn_on"):
        return LocalResult(text=_jh_t("jh_colour_fail", name=fname, value=colour, h=honorific), success=False)
    _update_ctx(entity=entity_id, domain="light", action="turn_on")
    import asyncio
    for _ in range(8):
        if _jh_shows(hass.states.get(entity_id), want):
            return LocalResult(text=_jh_t("jh_colour_done", name=fname, value=colour, h=honorific), success=True)
        await asyncio.sleep(0.25)
    return LocalResult(text=_jh_t("jh_sent_unconfirmed", name=fname, value=colour, h=honorific), success=False)


_JH_EFFECT_TO_RE = re.compile(r"^(?:(?:hey\s+)?jarvis[,\s]+)?(?:please\s+|could you\s+|can you\s+|would you\s+)*"
                              r"(?:switch|set|change|make|turn|put|start)\s+(?:the\s+)?(.+?)\s+(?:to|in|on)\s+(?:the\s+)?(.+?)"
                              r"(?:\s+(?:scene|effect|mode))?$")
_JH_EFFECT_ON_RE = re.compile(r"^(?:(?:hey\s+)?jarvis[,\s]+)?(?:please\s+|could you\s+|can you\s+|would you\s+)*"
                              r"(?:start|play|show|put on)\s+(?:the\s+)?(.+?)(?:\s+(?:scene|effect|mode))?\s+on\s+(?:the\s+)?(.+?)$")


async def _jh_try_effect(hass, normalized, honorific):
    """Jarvis Home patch (2026-11-11): start a lamp's own effect or scene by its name, read from the lamp's effect_list."""
    said = _jh_opening(normalized)[0]
    while _JH_TAIL_RE.search(said):
        said = _JH_TAIL_RE.sub("", said)
    pairs = []
    m = _JH_EFFECT_TO_RE.match(said)
    if m:
        pairs.append((m.group(1), m.group(2)))
    m = _JH_EFFECT_ON_RE.match(said)
    if m:
        pairs.append((m.group(2), m.group(1)))
    for lamp, wanted in pairs:
        if lamp.strip() in _JH_PRONOUNS or not wanted.strip():
            continue
        found = _find_entity(hass, lamp, "light")
        if not found or not str(found[0]).startswith("light."):
            continue
        entity_id, fname = found
        state = hass.states.get(entity_id)
        if state is None:
            continue
        pick = next((e for e in state.attributes.get("effect_list") or []
                     if str(e).lower() not in ("off", "none") and str(e).lower() == wanted.strip().lower()), None)
        if pick is None:
            continue
        if state.state in ("unavailable", "unknown"):
            return LocalResult(text=_jh_t("jh_offline_nochange", name=fname, h=honorific), success=False)
        if _needs_confirmation(hass, "turn_on", entity_id):
            return None
        if not await _seam_execute(hass, "light", "turn_on", {"entity_id": entity_id, "effect": pick},
                                   blocking=True, action="turn_on"):
            return LocalResult(text=_jh_t("jh_effect_fail", name=fname, value=pick, h=honorific), success=False)
        _update_ctx(entity=entity_id, domain="light", action="turn_on")
        import asyncio
        for _ in range(8):
            now = hass.states.get(entity_id)
            if now is not None and now.state == "on" and now.attributes.get("effect") == pick:
                return LocalResult(text=_jh_t("jh_effect_done", name=fname, value=pick, h=honorific), success=True)
            await asyncio.sleep(0.25)
        return LocalResult(text=_jh_t("jh_sent_unconfirmed", name=fname, value=pick, h=honorific), success=False)
    return None


_JH_TYPE_WORDS = {"light": "a light", "switch": "a switch", "lock": "a lock", "cover": "a cover", "climate": "a climate device",
                  "fan": "a fan", "media_player": "a media player"}
_JH_DO_WORDS = {"open": "open it", "close": "close it", "lock": "lock it", "unlock": "unlock it", "dim": "dim it",
                "brighten": "brighten it", "set_temp_named": "set its temperature"}


def _jh_wrong_type_line(fname, domain, action, honorific):
    """Jarvis Home patch (2026-11-07, round 2): the honest offline line for a device that has exactly the name said but cannot do
    what was asked, a lock asked to open. It names that device instead of saying that no such device exists."""
    kind = _JH_TYPE_WORDS.get(domain) or f"a {domain.replace('_', ' ')}"
    do = _JH_DO_WORDS.get(action) or f"{action.replace('_', ' ')} it"
    kind = _jh_t("jh_kind_" + domain) or kind   # jh-local-lang
    do = _jh_t("jh_do_" + action) or do
    return _jh_t("jh_wrong_kind", name=fname, kind=kind, do=do, h=honorific)


async def _jh_power_now(hass, h):
    """Jarvis Home patch (2026-11-10): 'what is the power consumption' without a whole-house meter: what the monitored devices
    draw right now, read live, and that the house draws more. None when nothing can be read: then JARVIS answers."""
    from .energy import jh_devices_power_now
    now = await jh_devices_power_now(hass)
    if not now or not now["devices"]:
        return None

    def fmt(w):
        return f"{w / 1000:.1f} kW" if w >= 1000 else f"{w:.0f} W"

    count = len(now["devices"]) + len(now["unreadable"])
    line = _jh_t("jh_power_now", n=count, total=fmt(now["total_watts"]), h=h)
    big = [d for d in now["devices"] if d["watts"] >= 1][:3]
    if big:
        line += " " + _jh_t("jh_power_most", items=", ".join(f"{d['name']} {fmt(d['watts'])}" for d in big))
    if now["unreadable"]:
        line += " " + _jh_t("jh_power_unread", names=", ".join(now["unreadable"][:3]))
    return line + " " + _jh_t("jh_power_more")


# Jarvis Home patch (2026-11-15): jh-no-act-on-questions. A question or a hypothetical never switches anything.
_JH_QUERY_ONLY = _jh_contextvars.ContextVar("jarvis_query_only", default=False)
_JH_MULTI = _jh_contextvars.ContextVar("jarvis_multi_part", default=False)


# Jarvis Home patch (2026-11-15): jh-goodnight-yes - "good night" asked; a yes in the very next message runs it
_JH_GN_ASKED: dict = {}
_JH_GN_PLAN: dict = {}   # jh-r25-gn (step 167): what the good-night question promised
_JH_GN_YES = re.compile(r"(?:yes|yes please|yeah|yep|sure|do it|do so|do|go ahead|please do|ok|okay|ja|ja graag|doe maar|bitte)")


class _JHNoAct(BaseException):
    """Raised by the local actuation seam while a question is handled: nothing may be switched, the AI answers instead.
    A BaseException, so no 'except Exception' on the way can turn it into 'I was not able to'."""


# jh-r19-tail (step 161, review round 19 #1): an order with a time, a condition or a negation in it ("turn on the heater at 7 /
# later / in 5 / when I get home / not", "schalte die Heizung nicht ein / um 7 ein / später ein", "zet de verwarming om 7 aan")
# is never acted on by the instant path: it cannot schedule, wait for a condition or read a "not" the way the AI can. try_local
# sets _JH_TAIL for the whole request; _jh_act_guard() is called first by every actuation path and refuses while it is set. Online
# the AI gets the order; offline the reply says that nothing was switched. Questions and answers that switch nothing still work.
_JH_TAIL = _jh_contextvars.ContextVar("jarvis_order_tail", default="")


class _JHTailNoAct(_JHNoAct):
    """Raised by _jh_act_guard: the order has a time, condition or negation tail, so the instant path switches nothing."""


def _jh_act_guard() -> None:
    """THE one guard before any local actuation (jh-r19-tail): nothing is switched while the request has a time, condition or
    negation tail. Called by _seam_execute, _execute_action, jh_act_and_confirm and the order paths of _jh_try_local_inner;
    a kit test checks that every actuation path calls it."""
    if _JH_TAIL.get():
        raise _JHTailNoAct()


_JH_TAIL_NUM = (r"(?:(?:[01]?\d|2[0-4])(?:[:.h][0-5]\d)?|half|quarter|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen"
                r"|twenty|thirty|forty|fifty|sixty|ninety|eins|zwei|drei|vier|fünf|sechs|sieben|acht|neun|zehn|elf|zwölf|zwanzig"
                r"|dreißig|halb|viertel|een|twee|drie|vijf|zes|zeven|negen|tien|twaalf|twintig|dertig|kwart|une?|deux|trois|quatre"
                r"|cinq|sept|huit|neuf|dix|onze|douze|una|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez|doce|uno|due|tre|quattro"
                r"|sei|sette|otto|nove|dieci|undici|dodici|uma|duas|três)")
_JH_TAIL_UNIT = (r"(?:secs?|seconds?|mins?|minutes?|hrs?|hours?|moments?|bit|while|jiffy|tick|days?|weeks?|sekunden?|minuten?"
                 r"|stunden?|tagen?|seconden?|minuut|uur|uren|kwartier|dagen|secondes?|heures?|jours?|segundos?|minutos?|horas?"
                 r"|días?|dias|secondi|minuti|ora|ore|giorni)")
_JH_TAIL_AMOUNT = (r"(?:(?:a|an|a few|a couple of|a couple|half an?|another|several|some|einer?|einem|ein paar|een|een paar|enkele"
                   r"|une?|quelques|unos|unas|una|un|uma|um|alcuni|qualche|un'|meia)\s*)?(?:(?:\d+(?:[.,]\d+)?|" + _JH_TAIL_NUM
                   + r")\s*)?" + _JH_TAIL_UNIT)   # jh-r20-tail (step 162, review round 20 #2): any number of digits
_JH_TAIL_TIMEWORD = (r"(?:morning|afternoon|evening|night|noon|midnight|weekend|week|month|monday|tuesday|wednesday|thursday"
                     r"|friday|saturday|sunday|weekday|morgen|abend|nacht|mittag|nachmittag|vormittag|woche|wochenende|montag|dienstag"
                     r"|mittwoch|donnerstag|freitag|samstag|sonntag|ochtend|middag|avond|maandag|dinsdag|woensdag|donderdag|vrijdag"
                     r"|zaterdag|zondag|matin|soir|nuit|semaine|lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche|mañana|tarde"
                     r"|noche|semana|lunes|martes|miércoles|jueves|viernes|sábado|domingo|mattina|pomeriggio|sera|notte|settimana"
                     r"|lunedì|martedì|mercoledì|giovedì|venerdì|sabato|domenica|manhã|noite|segunda|terça|quarta|quinta|sexta)")
_JH_TAIL_WEEKDAY = (r"(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|montag|dienstag|mittwoch|donnerstag|freitag"
                    r"|samstag|sonntag|maandag|dinsdag|woensdag|donderdag|vrijdag|zaterdag|zondag)")
_JH_ORDER_TAIL_RE = re.compile(
    # a clock time: 7:30, 19:00, 7 am, 7pm, 7 o'clock, 7 uhr, 7 uur, 7h30
    r"\b\d{1,2}:\d{2}\b|\b\d{1,2}\s*(?:am|pm|o'?clock|oclock|uhr|uur)\b|\b\d{1,2}h\d{0,2}\b"
    # at / um / om / à ... + a time
    r"|\b(?:at|around|from|um|om|ab|gegen|vanaf|rond|à|a las?|alle|às|as)\s+(?:about\s+|ungefähr\s+|ongeveer\s+)?"
    + _JH_TAIL_NUM + r"\b"
    r"|\b(?:at|um|om|à|a|by)\s+(?:noon|midnight|night|dawn|dusk|sunset|sunrise|sundown|bedtime|lunch(?:time)?|dinner(?:time)?"
    r"|breakfast|mitternacht|mittag|middernacht|zonsopgang|zonsondergang|midi|minuit|mediodía|medianoche|mezzanotte"
    r"|mezzogiorno)\b"
    # in / within / for / over / dans / en / tra ... + an amount of time; in + a bare number ("in 5")
    r"|\b(?:in|within|for|für|binnen|over|voor|dans|pendant|en|durante|tra|fra|per|por|em|daqui a)\s+" + _JH_TAIL_AMOUNT + r"\b"
    r"|\b(?:in|within|binnen|dans|en|tra|fra|em)\s+\d+\b"
    # this / next / every ... + a time word; on + a weekday
    r"|\b(?:this|next|every|each|coming|tomorrow|tonight|jeden|jede|jedes|nächsten?|diesen?|heute|elke|iedere|volgende|deze"
    r"|chaque|ce|cette|prochain|cada|este|esta|próximo|próxima|ogni|questo|questa|prossimo|todo|toda|todos|todas)\s+"
    + _JH_TAIL_TIMEWORD + r"\b"
    r"|\b(?:on|by|until|till|am|op|le)\s+" + _JH_TAIL_WEEKDAY + r"\b"
    r"|\b(?:in the|during the|am|des|de la|por la|di|à la|la|à)\s+(?:morning|afternoon|evening|night|morgen|abend|nachmittag"
    r"|vormittag|matin|soir|nuit|mañana|tarde|noche|mattina|pomeriggio|sera|notte|manhã|noite)\b"
    r"|(?:^|\s)'s\s+(?:avonds|ochtends|nachts|middags|morgens)\b"
    # a time word, a condition or a negation anywhere (whole words)
    r"|\b(?:later|soon|tonight|tomorrow|shortly|eventually|afterwards|overnight|daily|nightly|weekly|hourly|before|after|until"
    r"|till|unless|whenever|when|if|while|as soon as|in case|once (?:i|we|you|he|she|they|it|the|my|everyone|someone|somebody|nobody)"
    r"|not|never|no|nor|don't|dont|doesn't|doesnt|didn't|didnt|shouldn't|shouldnt|won't|wont|wouldn't|wouldnt|mustn't|mustnt"
    r"|später|spaeter|gleich|bald|nachher|danach|demnächst|morgen|übermorgen|abends|morgens|nachts|mittags|nachmittags"
    r"|vormittags|täglich|sobald|wenn|falls|sofern|außer|ausser|bis|nicht|nie|niemals|kein|keine|keinen|keinem|keiner"
    r"|straks|zometeen|zo meteen|zo direct|binnenkort|overmorgen|vanavond|vannacht|vanmiddag|vanochtend|vanmorgen|dagelijks|als"
    r"|wanneer|zodra|tenzij|indien|niet|nooit|geen|tot"
    r"|plus tard|tout à l'heure|bientôt|bientot|ce soir|cette nuit|ce matin|demain|après|apres|avant|jusqu'?à|jusqu|quand"
    r"|lorsque|si|sauf|dès que|ne|n'|pas|jamais"
    r"|más tarde|mas tarde|luego|pronto|esta noche|mañana|manana|después|despues|antes|hasta|cuando|a menos que|en cuanto"
    r"|nunca|jamás|jamas"
    r"|più tardi|piu tardi|dopo|presto|stasera|stanotte|domani|prima di|fino a|se|a meno che|appena|non"
    r"|mais tarde|em breve|esta noite|hoje à noite|amanhã|amanha|depois|até|assim que|não|nao)\b")
# a time word that ends the order ("turn on the heater this morning" is above; "... heater evening" here). Inside a name it is
# no tail: "the Morning Light", "the Night Lamp" keep working.
_JH_TAIL_END = re.compile(r"(?<!good )(?<!gute )(?<!goede )(?<!bonne )(?<!buona )(?<!buenas )(?<!boa )"   # a greeting
                          r"\b(?:morning|afternoon|evening|night|noon|midnight|sunset|sunrise|dawn|dusk|bedtime|weekend|"
                          + _JH_TAIL_WEEKDAY[3:-1] + r"|abend|nacht|mittag|mitternacht|avond|middag|middernacht|matin|soir|nuit"
                          r"|noche|tarde|sera|notte|noite)$")
_JH_TAIL_TH = ("พรุ่งนี้", "คืนนี้", "เย็นนี้", "เช้านี้", "ทีหลัง", "ภายหลัง", "เดี๋ยว", "โมง", "ทุ่ม", "นาฬิกา", "นาที", "ชั่วโมง",
               "ตอนเช้า", "ตอนเย็น", "ตอนกลางคืน", "เมื่อ", "ถ้า", "หาก", "ไม่", "อย่า", "ห้าม", "จนถึง", "หลังจาก", "ก่อน", "ทุกวัน")
# a set point is no time: "set the temperature at 21", "volume to 30%", "at 50 percent", "21 degrees"
_JH_TAIL_SETPOINT = re.compile(
    r"\b(?:temperature|temp|thermostat|volume|brightness|level|temperatur|lautstärke|helligkeit|helderheid|temperatuur)\s+"
    r"(?:to|at|auf|op|naar|on)\s+-?\d+(?:[.,]\d+)?|-?\d+(?:[.,]\d+)?\s*(?:%|°\s*[cf]?|degrees?|grad|graden|percent|prozent"
    r"|procent|pour ?cent|por ?ciento|per ?cento|por ?cento)")


# jh-r20-tail (step 162, review round 20 #2): more time tails, in every reply language. A bare amount of time ("10 minutes
# from now", "30 Minuten", "dos horas"), by / tegen / bis um / avant / para las / entro le + a time ("unlock the front door by 7";
# not after up / down / dim ...: "turn the volume up by 10" is an amount), in / within / binnen / dans / en / tra / em + a spelled
# number that ends the order ("in ten"), and "from now" / "vanaf nu" / "a partir de ahora" ... ("10 minutes later": "later" above).
_JH_TAIL_SPELLED = (r"(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|twenty|thirty|forty|fifty|sixty"
                    r"|ninety|zwei|drei|vier|fünf|sechs|sieben|acht|neun|zehn|elf|zwölf|zwanzig|dreißig|twee|drie|vijf|zes|zeven"
                    r"|negen|tien|twaalf|twintig|dertig|deux|trois|quatre|cinq|sept|huit|neuf|dix|onze|douze|dos|tres|cuatro|cinco"
                    r"|seis|siete|ocho|nueve|diez|doce|due|tre|quattro|sei|sette|otto|nove|dieci|undici|dodici|duas|três"
                    r"|fünfzehn|vijftien|quinze|vingt|trente|quince|veinte|treinta|cinque|quindici|venti|trenta|quatro|sete"
                    r"|oito|dez|vinte|trinta)")
_JH_TAIL_TIME_UNIT = (r"(?:secs?|seconds?|mins?|minutes?|hrs?|hours?|days?|weeks?|sekunden?|minuten?|stunden?|tagen?|tage"
                      r"|seconden?|minuut|uur|uren|kwartier|dagen|secondes?|heures?|jours?|segundos?|minutos?|horas?|días?|dias"
                      r"|secondi|minuti|ora|ore|giorni)")
_JH_TAIL_MORE_RE = re.compile(
    r"\b(?:\d+(?:[.,]\d+)?\s*|(?:an?|a few|a couple of|several|ein|eine|einer|een|une?|una|un'|uma|um|" + _JH_TAIL_SPELLED
    + r")\s+)" + _JH_TAIL_TIME_UNIT + r"\b"
    r"|\b(?:in|within|binnen|over|dans|en|tra|fra|em|dentro de|daqui a)\s+" + _JH_TAIL_SPELLED
    + r"(?=[\s,.!?]*(?:$|please\b|jarvis\b|bitte\b|graag\b|alsjeblieft\b|svp\b|s'il te plaît|por favor\b|per favore\b|or so\b"
    r"|(?:on|off|ein|an|aus|aan|uit)[\s,.!?]*$))"
    r"|\b(?:from now|from today|hence|ab jetzt|von jetzt an|vanaf nu|à partir de maintenant|d'ici|a partir de ahora|desde ahora"
    r"|da adesso|da ora|a partir de agora)\b"
    r"|อีก\s*\d")
_JH_TAIL_BY = re.compile(r"\b(?:by|tegen|bis um|bis|avant|para las?|entro(?: le| la| l')?)\s+(?:about\s+|ungefähr\s+|ongeveer\s+)?"
                         r"(?:\d{1,2}(?:[:.h]\d{2})?|" + _JH_TAIL_SPELLED + r"|noon|midnight)\b")
_JH_TAIL_RELATIVE = re.compile(r"\b(?:up|down|dim|dimmer|brighten|brighter|raise|lower|increase|decrease|reduce|louder|quieter"
                               r"|softer|warmer|cooler|higher|hoch|runter|höher|niedriger|omhoog|omlaag|hoger|lager|verhoog"
                               r"|verlaag|plus|moins|más|menos|più|meno|mais)\b")


_JH_TAIL_REL_LEAD = re.compile(r"(?:(?:turn|switch|put|set|make|crank|schalte?|drehe?|mach|zet|draai)\s+(?:it\s+|them\s+|that\s+"
                               r"|es\s+|het\s+|ze\s+)?)?(?:up|down|dim|dimmer|brighten|raise|lower|increase|decrease|reduce|hoch"
                               r"|runter|omhoog|omlaag|verhoog|verlaag|erhöhe|senke|augmente|baisse|sube|baja|alza|abbassa|aumenta"
                               r"|diminuisci|aumente|diminua)\b")


def _jh_by_amount(t, m) -> bool:
    """jh-r21-by (step 163, review round 21 #8): this "by" gives an amount ("turn the volume up by 10", "lower the volume by 10",
    "turn up the heat by 2"), not a deadline: a relative word stands right before it, or the order itself is a relative one.
    A relative word elsewhere ("the lower floor heater by 7", "the lower door by 7") does not make it an amount."""
    before = t[:m.start()]
    words = re.findall(r"[\w']+", before)
    if words and _JH_TAIL_RELATIVE.fullmatch(words[-1]):
        return True
    lead = _JH_TAIL_LEAD.match(before)
    rest = before[lead.end():] if lead else before
    return bool(_JH_TAIL_REL_LEAD.match(rest.strip()))


def _jh_tail_spans(t, end) -> list:
    """jh-r20-tail: every tail in the tail text t (end: t without its closing politeness) as (start, end) spans."""
    spans = [m.span() for m in _JH_ORDER_TAIL_RE.finditer(t)]
    spans += [m.span() for m in _JH_TAIL_MORE_RE.finditer(t)]
    spans += [m.span() for m in _JH_TAIL_BY.finditer(t) if not _jh_by_amount(t, m)]   # jh-r21-by (step 163)
    spans += [(t.find(w), t.find(w) + len(w)) for w in _JH_TAIL_TH if w in t]
    m = _JH_TAIL_END.search(end)
    if m:
        spans.append(m.span())
    return spans


# jh-r20-name (step 162, review round 20 #1, #8): a time word that is part of a real name is no tail. The words that hold it
# must BE the name, read by the shared resolvers: the whole request (a "which one?" answer: "bedtime"), the order's object with
# the word kept ("activate movie night", "run bedtime", "turn on the sunrise", "activate savanna sunset"), a lamp's own effect
# after "to" ("set the hue lamp to sunset"), a room after "in" ("the light in the morning room"). "turn on the heater evening"
# keeps its tail: "heater evening" names nothing.
_JH_TAIL_LEAD = re.compile(r"(?:(?:hey|ok|okay|hi)\s+)?(?:jarvis[,\s]+)?(?:(?:please|kindly|could you|can you|would you|will you"
                           r"|kannst du|könntest du|bitte|kun je|wil je|peux-tu|pouvez-vous|puedes|podrías|puoi|potresti|pode"
                           r"|podes)\s+)*")
_JH_TAIL_VERB = re.compile(r"(?:turn on|turn off|switch on|switch off|turn|switch|activate|deactivate|run|start|trigger|execute"
                           r"|play|enable|disable|open|close|lock|unlock|set|put on|dim|brighten|schalte|schalt|mach|mache"
                           r"|aktiviere|starte|zet|doe|activeer|allume|allumez|éteins|eteins|active|activez|lance|enciende|apaga"
                           r"|activa|ejecuta|accendi|spegni|attiva|avvia|liga|desliga|ativa|executa)\s+(?:(?:on|off|an|aus|ein)\s+)?"
                           r"(?:(?:the|my|die|der|den|das|de|het|le|la|les|l'|el|los|las|il|lo|o|os|as)\s+)?(?P<o>.+?)"
                           r"(?:\s+(?:on|off|ein|an|aus|aan|uit))?$")
_JH_TAIL_IN_AREA = re.compile(r"(?P<p>\s(?:in the|in|im|in der|in den|in de|in het|dans la|dans le|dans|en la|en el|nella|nel"
                              r"|na|no)\s+)(?P<y>.+)$")
_JH_TAIL_TO_EFFECT = re.compile(r"(?P<x>.+?)\s+(?:to|into|auf|naar|zu|sur)\s+(?:the\s+)?(?P<y>.+?)(?:\s+(?:effect|scene|mode))?")


def _jh_tail_named(hass, frag) -> bool:
    """jh-r20-name: True when the words are the whole name (or an alias) of a real scene, script, device or area, or a lamp's
    own effect, as the shared resolvers read them (_find_scene_or_script, _jh_names_of, _jh_room_named), or the end of a scene's
    or script's name as Hue names them ("savanna sunset" of "Living Room Savanna sunset"). Never by sound or by part of a
    word."""
    n = re.sub(r"^(?:the|my|die|der|den|das|de|het|le|la|les|l|el|los|las|il|lo|o|os|as)\s+", "", _jh_norm_name(frag))
    if not n:
        return False
    for cand in {n, re.sub(r"\s+(?:scene|script|routine|mode|effect|szene|skript|scène|escena|scena|cena)$", "", n)}:
        if not cand:
            continue
        for found in _jh_scene_candidates(hass, cand):   # jh-r24-res: several scenes or scripts of that name are named too
            fn = _jh_norm_name(found[1])
            if fn == cand or fn.endswith(" " + cand):
                return True
        for dom in _JH_ACT_DOMAINS:
            for st in hass.states.async_all(dom):
                if _jh_is_setting(hass, st.entity_id):
                    continue
                names = _jh_names_of(hass, st)   # a set of whole names
                if cand in names:
                    return True
                if dom == "light" and any(_jh_norm_name(e) == cand for e in (st.attributes.get("effect_list") or [])):
                    return True
        if _jh_room_named(hass, cand):
            return True
    return False


def _jh_tail_unnamed(hass, t, end, spans) -> list:
    """jh-r20-name (step 162, review round 20 #1, #8): the tail spans that lie in no real name (see _jh_tail_named). A span is
    dropped only when it lies wholly inside words that are a name; any other tail stays. Fails closed: on any error every
    tail stays."""
    try:
        lead = _JH_TAIL_LEAD.match(end)
        a0 = lead.end() if lead else 0
        named = []
        if _jh_tail_named(hass, end[a0:]):
            named.append((a0, len(end)))
        m = _JH_TAIL_VERB.match(end, a0)
        if m:
            o0, o1 = m.span("o")
            for b in sorted({o1, len(end)}):
                if _jh_tail_named(hass, end[o0:b]):
                    named.append((o0, b))
            obj = end[o0:o1]
            mi = _JH_TAIL_IN_AREA.search(obj)
            if mi and _jh_room_named(hass, mi.group("y")):
                named.append((o0 + mi.start("p") + 1, o0 + mi.end("y")))
            me = _JH_TAIL_TO_EFFECT.fullmatch(obj)
            if me:
                lamp = _find_entity(hass, me.group("x"), "light")
                st = hass.states.get(lamp[0]) if lamp and str(lamp[0]).startswith("light.") else None
                want = _jh_norm_name(me.group("y"))
                if st is not None and any(_jh_norm_name(e) == want for e in (st.attributes.get("effect_list") or [])):
                    named.append((o0 + me.start("y"), o0 + me.end("y")))
        if not named:
            return spans
        return [(s, e) for s, e in spans if not any(a <= s and e <= b for a, b in named)]
    except Exception:
        return spans


def _jh_lock_said(locks, words) -> bool:
    """jh-r20-tail (review round 20 #2): True when the words hold a real lock's name (its words other than door / lock / the) as
    whole words, extra words aside ("the front door for 30 minutes" holds "Front Door"): the lock line never says that such a
    lock does not exist."""
    for s in locks:
        nw = [w for w in _jh_norm_name(s.attributes.get("friendly_name") or s.entity_id.split(".", 1)[-1]).split()
              if w not in ("door", "the", "lock")]
        if nw and all(_jh_word_in(w, str(words or "").casefold(), whole=True) for w in nw):
            return True
    return False


def _jh_tail_text(text) -> str:
    """The text the tail rules read: lower case, typographic apostrophes plain, set points blanked (same length, so a position
    found here is a position in the lower-cased text)."""
    t = str(text or "").lower().replace("’", "'").replace("‘", "'")
    return _JH_TAIL_SETPOINT.sub(lambda m: " " * len(m.group(0)), t)


def _jh_tail_at(text, hass=None) -> int:
    """Where the time, condition or negation tail of an order starts, or -1 (jh-r19-tail). Thai: where its first tail word is.
    jh-r20-name (step 162, review round 20 #1, #8): with hass, a time word that is part of a real name is no tail ("activate
    movie night", "run bedtime"); without hass every tail counts."""
    t = _jh_tail_text(text)
    end = re.sub(r"[\s,.!?]*(?:please|thanks|thank you|for me|jarvis|bitte|danke|alsjeblieft|graag|svp|s'il te plaît"
                 r"|por favor|per favore|ครับ|ค่ะ|คะ|นะ)?[\s,.!?]*$", "", t)
    spans = _jh_tail_spans(t, end)   # jh-r20-tail: every tail, not only the first
    if spans and hass is not None:
        spans = _jh_tail_unnamed(hass, t, end, spans)
    return min(s for s, _e in spans) if spans else -1


def jh_order_tail(text, hass=None) -> str:
    """The time, condition or negation tail of a request ("at 7", "later", "in 5 minutes", "nicht", "um 7", "om 7", "when I get
    home"), or "" when it has none (jh-r19-tail, every supported reply language: en, nl, de, fr, es, it, pt, th). jh-r20-name:
    with hass, a time word inside a real name is no tail."""
    at = _jh_tail_at(text, hass)
    if at < 0:
        return ""
    t = _jh_tail_text(text)
    return (t[at:at + 40].strip() or "tail")


# the start of an order in every reply language (after "can you", "please"; a leading negation is part of the order)
_JH_ORDER_START = re.compile(
    r"(?:turn|switch|set|dim|brighten|make|open|close|shut|lock|unlock|play|pause|resume|unpause|stop|mute|unmute|volume|skip"
    r"|next|activate|deactivate|run|trigger|start|execute|change|put|arm|disarm|enable|disable|lights?|power|heat|warm|cool"
    r"|schalte?|mache?|öffne|oeffne|offne|schließe|schliesse|schliess|verriegele|verriegle|entriegele|entriegle|starte"
    r"|aktiviere|spiele|pausiere|dimme?|stelle?|drehe?"
    r"|zet|doe|draai|sluit|vergrendel|ontgrendel|speel|pauzeer"
    r"|allume|allumez|éteins|eteins|éteignez|eteignez|ferme|fermez|ouvre|ouvrez|mets|mettez|lance|verrouille|déverrouille"
    r"|enciende|encienda|enciendas|apaga|apague|apagues|abre|abra|abras|cierra|cierre|cierres|pon|ponga|pongas|activa"
    r"|bloquea|desbloquea"
    r"|accendi|accenda|accendere|spegni|spenga|spegnere|apri|aprire|chiudi|chiudere|metti|mettere|attiva|attivare|blocca"
    r"|sblocca"
    r"|liga|ligue|ligues|desliga|desligue|desligues|feche|fecha|ativa|tranca|destranca)\b")


def _jh_order_shape(text) -> bool:
    """True when the request is an order to a device (any reply language): it starts with an order word, ends with a German or
    Dutch on/off particle, or is the whole of one of the engine's order patterns."""
    t = _jh_command_text(_normalize(text or ""))
    t = re.sub(r"^(?:(?:please\s+)?(?:do\s+not|don'?t|never|nicht|niet|no|ne|non|não|nunca)\s+|n'|อย่า|ห้าม|ไม่ต้อง)", "", t)
    if _JH_ORDER_START.match(t) or re.match(r"(?:ช่วย)?(?:เปิด|ปิด|ตั้ง|หรี่|ล็อก|ปลดล็อก)", t):
        return True
    if re.search(r"(?:einschalten|ausschalten|anmachen|ausmachen|aufmachen|zumachen|aanzetten|uitzetten|aandoen|uitdoen)\b"
                 r"|\s(?:ein|an|aus|aan|uit)$", t):
        return True
    return (any(re.fullmatch(p, t) for p, a, _d in _INTENT_PATTERNS
                if not a.startswith("query_") and a not in ("greeting", "thanks", "status"))
            or any(re.fullmatch(p, t) for p, *_r in _BULK_PATTERNS))


_JH_TAIL_REPLY = {   # offline: the honest line, in the language the order was written in (a form of address in en and de only)
    "en": "That order has a time, a condition or a 'not' in it{hp}, and I can't schedule or check that while I'm offline. "
          "Nothing was switched.",
    "de": "Dieser Befehl enthält eine Zeit, eine Bedingung oder ein „nicht“{hp}, und das kann ich offline nicht planen oder prüfen. "
          "Es wurde nichts geschaltet.",
    "nl": "Die opdracht bevat een tijd, een voorwaarde of een 'niet', en dat kan ik offline niet plannen of controleren. "
          "Er is niets geschakeld.",
    "fr": "Cet ordre contient une heure, une condition ou une négation, et hors ligne je ne peux ni le programmer ni le vérifier. "
          "Rien n'a été allumé ni éteint.",
    "es": "Esa orden tiene una hora, una condición o una negación, y sin conexión no puedo programarla ni comprobarla. "
          "No se ha encendido ni apagado nada.",
    "it": "Quel comando contiene un orario, una condizione o una negazione, e offline non posso programmarlo né verificarlo. "
          "Non è stato acceso né spento nulla.",
    "pt": "Essa ordem tem uma hora, uma condição ou uma negação, e offline não consigo agendá-la nem verificá-la. "
          "Nada foi ligado nem desligado.",
    "th": "คำสั่งนี้มีเวลา เงื่อนไข หรือคำปฏิเสธ ซึ่งตอนออฟไลน์ฉันตั้งเวลาหรือตรวจสอบไม่ได้ จึงไม่ได้เปิดหรือปิดอะไรเลย",
}


_JH_TAIL_LANG_HINTS = (   # order words of the reply languages the instant path does not speak
    ("nl", r"\b(?:zet|doe|draai|niet|aan|uit|verwarming|straks|graag)\b"),
    ("fr", r"\b(?:allume|éteins|eteins|chauffage|lumière|plus tard)\b|\bn'allume"),
    ("es", r"\b(?:enciende|enciendas|apaga|apagues|calefacción|calefaccion|luz|más tarde)\b"),
    ("it", r"\b(?:accendi|accendere|spegni|spegnere|riscaldamento|luce|più tardi)\b"),
    ("pt", r"\b(?:liga|ligue|desliga|desligue|aquecedor|mais tarde)\b"),
)


def _jh_tail_reply(honorific, text) -> str:
    """The offline answer to an order with a tail: nothing was switched, in the order's language (jh-r19-tail)."""
    try:
        code = jh_text_language(text)
    except Exception:
        code = "en"
    if code == "other":
        code = "th" if re.search(r"[฀-๿]", str(text or "")) else "en"
    if code in ("en", "de") and not jh_text_lang_sure(text):
        low = str(text or "").lower()   # a short order ("no enciendas la luz") is too short for the language check
        for c, rx in _JH_TAIL_LANG_HINTS:
            if re.search(rx, low):
                code = c
                break
    if code in ("en", "de") and _JH_LANG.get() in ("en", "de"):
        code = _JH_LANG.get()   # the instant path's own reply language for this request
    hp = f", {honorific}" if honorific and code in ("en", "de") else ""
    return _JH_TAIL_REPLY.get(code, _JH_TAIL_REPLY["en"]).format(hp=hp)


_JH_HYPOTHETICAL = re.compile(   # jh-r9-hypothetical (review round 9): the weak words only at the start of a clause
    r"\b(?:what if|what would|what happens|what will happen|would happen|will happen|happens? if|"
    r"if (?:i|you|we|someone) (?:ask|asked|said|say|told|tell|were|was|wanted)|wat als|was wäre|was passiert)\b"
    r"|(?:^|[,.;:!?]\s*)(?:@?(?:jarvis|ok|okay|so|but|and|now|well|hey|just)[\s,]+)*(?:let'?s\s+)?"
    r"(?:suppose|supposing|imagine|hypothetical(?:ly)?|in theory|pretend)\b"
    r"|\b(?:imagine|suppose|pretend) (?:if|that)\b")
_JH_POLITE_ORDER = re.compile(
    r"(?:please\s+)?(?:(?:can|could|would|will)\s+you|kun\s+je|kan\s+je|kunt\s+u|wil\s+je|wilt\s+u|kannst\s+du|"
    r"könntest\s+du|würdest\s+du|können\s+sie|könnten\s+sie)\s+(?:please\s+)?")
_JH_QUESTION_START = re.compile(
    r"(?:what|whats|what's|when|where|which|who|whom|whose|why|how|is|are|am|do|does|did|was|were|has|have|had|should|"
    r"shall|may|might|would|could|can|will|if|whether|wat|wanneer|waar|welke|waarom|hoe|zijn|staat|staan|"
    r"was|wann|wo|welche|welcher|wer|warum|wie|ist|sind)\b")


# jh-r22-gate (step 164, review round 22 #2): a question clause after a comma or "or" makes the whole request a question, also
# after a polite order ("can you lock up, or is someone still outside?", "can you turn off the lights or should i?", "would you
# turn on the lights, do you think?"). Checked before the polite-order exemption.
_JH_TAG_QUESTION = re.compile(r"(?:,\s*(?:or\s+)?|\bor\s+)(?:is|isn'?t|are|aren'?t|am|was|wasn'?t|were|should|shall|do|does"
                              r"|did|can|could|will|would|has|have|may|might|right|ok(?:ay)?|correct|yes|no)\b")


def _jh_lead_off(text: str) -> str:
    t = (text or "").strip().lower()
    t = re.sub(r"^(?:(?:hey|ok|okay|hi)\s+)?jarvis[\s,:!.]*", "", t)
    return re.sub(r"^please[\s,]+", "", t).strip()


def _jh_utterance_kind(text: str) -> str:
    """hypothetical, question or command."""
    raw = (text or "").strip().lower()
    if _JH_HYPOTHETICAL.search(raw):
        return "hypothetical"
    body = _jh_lead_off(raw)
    # jh-r22-gate (step 164, review round 22 #2): a question clause after a comma or "or", or a '?' with more words after it
    # ("turn the lights off? is that okay"), is a question, before the polite-order exemption
    if _JH_TAG_QUESTION.search(body) or re.search(r"\?\s*\w", raw):
        return "question"
    if _JH_POLITE_ORDER.match(body) and not re.search(r"\b(?:if|whether|what|which|how|when|why|should|is)\b", body):
        return "command"
    if _JH_QUESTION_START.match(body) or "?" in raw:   # jh-r22-gate: a '?' anywhere
        return "question"
    return "command"


# jh-r21-gate (step 163, review round 21 #1): THE gate for free text that may become an action outside try_local (the
# intent router of jarvis.process_intent, its confirmation window, jarvis.scene_by_intent, the voice confirmation, the offer's yes).
# A question, a hypothetical, a negation or an order with a time / condition tail never acts; a yes is the WHOLE answer and one of
# the shared yes words. The inventory test (test_inventory_actuation) checks that every free-text path to a device goes through it.
JH_YES_WORDS = (frozenset(w for w in _JH_YES.split("|") if w != "that one")
                | frozenset(("yes please", "affirmative", "please", "confirm", "confirmed", "proceed"))
                # jh-r23-yes (step 165, review round 23 #14): upstream's own plain yes answers, as WHOLE answers only (never a
                # substring, never with a negation or a tail). "close it", "shut it", "secure it" are no yes: they name an
                # action, and "it" may be another device than the announced one.
                | frozenset(("yup", "yes go ahead", "yeah go ahead", "yep go ahead", "ok go ahead", "okay go ahead",
                             "sure go ahead", "go ahead please", "yes do it", "yeah do it", "yep do it", "yup do it", "ok do it",
                             "okay do it", "sure do it", "do it please", "sure thing", "yes proceed", "absolutely", "of course",
                             "certainly", "ja bitte", "ja graag")))
_JH_NEGATION = re.compile(r"(?<![\w'])(?:no|not|nope|never|nah|cancel|stop|wait|abort|negative|don'?t|do not|doesn'?t|didn'?t"
                          r"|isn'?t|won'?t|can'?t|cannot|shouldn'?t|wouldn'?t|nein|nicht|nie|kein\w*|nee|niet|nooit|geen|non|pas"
                          r"|jamais|nunca|não|nao|nada|nessuno|mai)(?![\w'])|n't(?![\w'])|ไม่|อย่า|ห้าม")


def _jh_yes_norm(text) -> str:
    t = str(text or "").lower().replace("’", "'")
    t = re.sub(r"[^\w\s']+", " ", t)
    return " ".join(t.split())


def jh_is_yes(text, extra=()) -> bool:
    """jh-r21-gate (step 163, review round 21 #1): True only when the WHOLE answer is one of the shared yes words (JH_YES_WORDS,
    plus a caller's own exact phrases), with no negation and no time or condition tail anywhere. "no, don't do it", "I'm not
    sure", "not okay", "cancel, do not proceed", "yeah do it later" are no yes. Fails closed."""
    try:
        if "?" in str(text or "") or "\uff1f" in str(text or ""):
            return False   # jh-r25-yes (step 167, review round 25 #5): "yes?", "ok?", "sure?" are questions, never a yes
        t = _jh_yes_norm(text)
        if not t or _JH_NEGATION.search(t) or jh_order_tail(t):
            return False
        return t in JH_YES_WORDS or t in {_jh_yes_norm(x) for x in extra or ()}
    except Exception:
        return False


# jh-r22-mood (step 164, review round 22 #10): what a scene wish may say about its mood without being a timed order
_JH_MOOD_NOUN = (r"(?:bed\s*time|sunset|sunrise|sundown|dusk|dawn|tonight|dinner(?:\s*time)?|lunch(?:\s*time)?|breakfast|supper"
                 r"|tea\s*time|movie\s*night|game\s*night|date\s*night|night\s*time)")
_JH_MOOD_SUBJECT = r"(?:i|we|you|he|she|they|it|everyone|everybody|someone|somebody|nobody|that|this|then)"


def _jh_mood_strip(text) -> str:
    """jh-r22-mood (step 164, review round 22 #10): a scene wish without the words that only describe its mood: time-of-day
    words, a bare mood noun ("bedtime", "sunset", "tonight") unless "at / by / around / until" makes it a time, and a closing
    "after / before <one noun>" ("after work", "before bed", "after the movie"). A clock time, a number, an amount of time,
    "later", "tomorrow", a clause with a subject ("after I get home") and a negation stay, so the gate still refuses them."""
    s = re.sub(r"\b" + _JH_TAIL_TIMEWORD + r"s?\b", " ", str(text or ""), flags=re.I)

    def _act(m):
        w = m.group(1)
        if re.fullmatch(_JH_MOOD_SUBJECT + "|" + _JH_TAIL_NUM + "|" + _JH_TAIL_UNIT + r"|\d.*", w, re.I):
            return m.group(0)
        return " "
    s = re.sub(r"\b(?:after|before)\s+(?:(?:the|my|a|an|our|a\s+long)\s+)?([^\W\d_][\w']*)[\s.!]*$", _act, s, flags=re.I)
    s = re.sub(r"(\b(?:at|by|around|until|till|from|um|om|à|a)\s+)?\b" + _JH_MOOD_NOUN + r"\b",
               lambda m: m.group(0) if m.group(1) else " ", s, flags=re.I)
    return s


def jh_free_text_gate(text, hass=None, order=True, mood=False, verbs=None) -> str:
    """jh-r21-gate (step 163, review round 21 #1): '' when free text may be acted on now; else why not: "empty", "hypothetical",
    "question", "multi" (several parts: the AI answers it whole), "tail" (a time, condition or negation: jh_order_tail with the
    home's real names), "no order" (order=True and it is no order to a device in any reply language; verbs: a caller's own extra
    order words), "error" (fails closed). mood=True (a scene wish: "something cozy for movie night") lets a bare time-of-day
    word describe the mood, while a clock time, an amount of time, later, tomorrow or a negation still refuse."""
    try:
        t = str(text or "").strip()
        if not t:
            return "empty"
        kind = _jh_utterance_kind(t)
        if kind != "command":
            return kind
        if _jh_multi_part(t):
            return "multi"
        tail = jh_order_tail(t, hass)
        if tail and mood:
            tail = jh_order_tail(_jh_mood_strip(t), hass)   # jh-r22-mood (step 164, review round 22 #10)
        if tail:
            return "tail"
        if order and not (_jh_order_shape(t) or (verbs and re.match(verbs, _jh_command_text(_normalize(t))))):
            return "no order"
        return ""
    except Exception:
        return "error"


def _jh_multi_part(text: str) -> bool:
    """A long sentence with several parts or several questions: the AI answers it whole."""
    t = (text or "").lower()
    words = re.findall(r"[\w']+", t)
    q = len(re.findall(r"\b(?:who|what|which|when|where|how|is|are)\b", t))
    return len(words) > 14 or t.count(",") >= 2 or (t.count(",") >= 1 and len(words) > 9) or q >= 3


def _jh_command_text(normalized: str) -> str:
    """The order itself: no 'jarvis', 'please', 'can you' in front, no 'please' or 'now' at the end."""
    t = _jh_lead_off(normalized)
    t = _JH_POLITE_ORDER.sub("", t, count=1) if _JH_POLITE_ORDER.match(t) else t
    t = re.sub(r"^please\s+", "", t)
    t = re.sub(r"\s+(?:please|now|right now|for me|jarvis|thanks|thank you)$", "", t)
    t = re.sub(r"(\d)\s*(?:%|percent|procent|prozent)", r"\1", t)   # "to 30%" is "to 30"
    return t.strip()


def _jh_room_lights(hass, frag, exact=False):
    """'the bedroom lamps' -> (room, [(entity_id, name)]) for every light in that room; None when it is no room. exact=True
    (review round 14): only an area with exactly that name."""
    m = re.fullmatch(r"(?:all\s+(?:of\s+)?)?(?:the\s+)?(.+?)\s+(?:lamps|lights)", (frag or "").strip())
    if not m:
        return None
    ents = [x for x in _find_entities_in_area(hass, m.group(1), "light", exact) if not _jh_is_setting(hass, x[0])]
    return (m.group(1), ents) if ents else None


def _jh_area_named(hass, frag):
    """Review round 14 (jh-r14-room): the real name of the one area whose whole name (or alias) is the fragment, else ''."""
    want = re.sub(r"^(?:all\s+(?:of\s+)?)?(?:the\s+)?", "", (frag or "").strip().lower()).strip()
    if not want:
        return ""
    try:
        from homeassistant.helpers import area_registry as areg
        hits = [a for a in areg.async_get(hass).async_list_areas()
                if want == str(a.name).strip().lower() or want in {str(x).strip().lower() for x in (getattr(a, "aliases", None) or [])}]
    except Exception:
        return ""
    return str(hits[0].name) if len(hits) == 1 else ""


def _jh_device_named(hass, words, domain):
    """jh-r18-media (step 160, review round 18 #2): the one device of this domain the words name ("on the tv"): its exact name,
    else the only one whose name holds the words as whole words (_jh_word_in, whole=True). None when none or several do, so
    the order never guesses between two."""
    want = re.sub(r"^(?:the|my)\s+", "", str(words or "").strip().casefold()).strip()
    if not want:
        return None
    exact, whole = [], []
    for st in hass.states.async_all(domain):
        if _jh_is_setting(hass, st.entity_id):
            continue
        nm = str(st.attributes.get("friendly_name") or "").strip().casefold()
        if nm == want:
            exact.append(st.entity_id)
        elif nm and _jh_word_in(want, nm, whole=True):
            whole.append(st.entity_id)
    if exact:
        return exact[0] if len(exact) == 1 else None
    if whole:
        return whole[0] if len(whole) == 1 else None
    # jh-r19-media (step 161, review round 19 #11): "the tv" / "the speaker" / "the thermostat" with no such word in any name:
    # the one device of that kind (a media player's device class, a climate entity), as _jh_class_fits decides; never a
    # guess between two
    if _jh_class_of(want):
        fit = [st.entity_id for st in hass.states.async_all(domain) if not _jh_is_setting(hass, st.entity_id)
               and _jh_class_fits(hass, want, (st.entity_id, st.attributes.get("friendly_name") or ""))]
        return fit[0] if len(fit) == 1 else None
    return None


def _jh_room_named(hass, frag, domains=None, prefer=None, said=(), kinds=("lights", "lamps")):
    """Review round 16 (jh-r16-room): THE room rule for every path that takes a room from the words (the room order "turn on
    the X lights" in every word order, the bulk order "turn on the lights in X", the media and thermostat "... in X"). The real
    name of the one area whose whole name (or alias) is X, else ''. Never a room when the words as said name a device (X
    itself, "X lights" / "X lamps" for a plural order, "X light" / "X lamp" for a singular one, and the words as said), never a
    part of an area name ("room" is no room), never a sound-alike. Callers switch the area with
    _find_entities_in_area(..., exact=True) and name this real area in the reply."""
    raw = (frag or "").strip()
    bare = re.sub(r"\s+(?:lamps|lights|lamp|light)\s*$", "", raw).strip()
    if not bare:
        return ""
    for words in [raw] + [bare + " " + k for k in (kinds or ())] + [w for w in (said or ()) if w]:
        if _jh_whole(hass, words, domains, prefer):
            return ""
    return _jh_area_named(hass, bare)


import unicodedata as _jh_ud

# Words that only (or mainly) belong to one language; short shared words (a, in, is, die, so) are left out on purpose.
_JH_STOPWORDS = {
    "en": {"the", "what", "which", "who", "how", "turn", "switch", "on", "off", "please", "my", "is", "are", "of", "to", "and",
           "lights", "light", "lamp", "heater", "time", "it", "you", "can", "set", "where", "when", "today", "now", "all"},
    "de": {"der", "das", "ist", "und", "nicht", "ein", "eine", "wie", "spät", "schalte", "schalten", "licht", "lampe", "an", "aus",
           "bitte", "mach", "mache", "heizung", "wo", "wer", "wann", "heute", "alle", "ich", "du", "sie", "mein", "meine"},
    "nl": {"het", "een", "hoe", "wat", "waar", "wanneer", "welke", "waarom", "zijn", "staat", "staan", "aan", "uit", "doe", "zet",
           "de", "van", "ik", "jij", "mijn", "niet", "nog", "maar", "ook", "deze", "laat", "alle", "graag", "alsjeblieft",
           "vandaag", "morgen", "hoeveel", "kun", "kunt", "lampen", "verwarming", "licht", "lichten", "woonkamer", "keuken",
           "slaapkamer", "er", "dat", "dit", "wie", "weer", "je", "u", "thuis", "goedemorgen", "goedemiddag", "goedenavond",
           "welterusten", "dankjewel", "bedankt", "hoi", "doei", "hij", "zij", "nu", "en", "haar", "hem", "telefoon",
           "opgeladen", "batterij", "met", "voor", "naar", "om", "heb", "heeft", "hebben", "kan", "moet", "wil"},
    "fr": {"le", "la", "les", "est", "et", "quelle", "quel", "heure", "allume", "éteins", "lumière", "lumières", "du", "des",
           "une", "s'il", "plaît", "où", "qui", "comment", "aujourd'hui", "chauffage", "je", "tu", "vous", "mon", "ma"},
    "es": {"el", "los", "las", "qué", "hora", "es", "y", "enciende", "apaga", "luz", "luces", "del", "una", "por", "favor",
           "dónde", "quién", "cómo", "hoy", "calefacción", "yo", "mi", "está"},
    "it": {"il", "lo", "gli", "che", "ore", "sono", "accendi", "spegni", "luce", "luci", "della", "per", "favore", "dove",
           "chi", "come", "oggi", "riscaldamento", "io", "mio", "è"},
    "pt": {"o", "os", "as", "que", "horas", "são", "liga", "desliga", "luz", "luzes", "da", "do", "por", "favor", "onde",
           "quem", "como", "hoje", "aquecimento", "eu", "meu", "está"},
}


def jh_text_language(text: str) -> str:
    """Clean Jarvis (2026-11-15): the language a request is written in, as far as the instant path needs it: "other" for a
    non-Latin script (2 or more such letters), else the language with the most of its own words when that is clearly more
    than English and German have ("nl", "fr", "es", "it", "pt"), else "en" or "de". Device names in other words do not count
    on their own: a Latin-script language needs at least two of its words and more than English has (one is enough in a
    short greeting with no English or German word)."""
    t = re.sub(r"^\s*(?:(?:hey|ok|okay|hi)\s+)?jarvis[\s,:!.]*", "", (text or "").lower())
    foreign = sum(1 for ch in t if ch.isalpha() and not _jh_ud.name(ch, "").startswith("LATIN"))
    if foreign >= 2:
        return "other"
    words = re.findall(r"[a-zà-ÿ']+", t)
    if not words:
        return "en"
    score = {lang: sum(1 for w in words if w in sw) for lang, sw in _JH_STOPWORDS.items()}
    best = max((l for l in score if l not in ("en", "de")), key=lambda l: score[l])
    if score[best] >= 2 and score[best] > score["en"] and score[best] > score["de"]:
        return best
    if len(words) <= 3 and score[best] >= 1 and not score["en"] and not score["de"]:   # "goedemorgen", "dankjewel jarvis"
        return best
    return "de" if score["de"] > score["en"] else "en"


def jh_answer_elsewhere(text: str) -> bool:
    """True when the instant path must leave the request to the AI because it is written in a language the instant replies do
    not have (they speak English and German); the AI then answers in the user's own language."""
    return jh_text_language(text) not in ("en", "de")


_JH_LATER = re.compile(r"\b(?:afternoon|tonight|this evening|evening|tomorrow|later|morning|weekend|this week|next week|"
                       r"rain|raining|snow|snowing|storm|forecast|will it|going to|umbrella)\b", re.I)
_JH_WEATHERISH = re.compile(r"\b(?:weather|rain|raining|snow|snowing|storm|temperature|cold|warm|hot|sunny|forecast|umbrella|outside)\b", re.I)
_JH_OUTSIDE = re.compile(r"^(?:(?:what(?:'s| is)|how(?:'s| is))\s+)?(?:the\s+)?(?:temperature|temp)\s+(?:outside|outdoors|out there)\??$"
                         r"|^(?:what(?:'s| is)\s+the\s+)?(?:outside|outdoor)\s+(?:temperature|temp)\??$"
                         r"|^how\s+(?:cold|warm|hot)\s+is\s+it\s+(?:outside|outdoors|out)\??$", re.I)
_JH_PCT = re.compile(r"^(?:what(?:'s| is)\s+)?(\d+(?:[.,]\d+)?)\s*(?:%|percent)\s+of\s+(\d+(?:[.,]\d+)?)\s*\??$", re.I)
_JH_SUM = re.compile(r"^(?:what(?:'s| is)\s+|how much is\s+|calculate\s+)?(-?\d+(?:[.,]\d+)?)\s*(\+|-|plus|minus|times|x|\*|multiplied by|"
                     r"divided by|/|over)\s*(-?\d+(?:[.,]\d+)?)\s*\??$", re.I)
_JH_WHO_LIVES = re.compile(r"^who\s+(?:lives|live|is living|stays|are the people living)\s+(?:here|in this (?:house|home|apartment|flat))\??$"
                           r"|^who\s+(?:lives|live)\s+with\s+me\??$|^who\s+is\s+in\s+(?:this|my|the)\s+household\??$", re.I)
_JH_CANT = re.compile(r"^what\s+(?:can'?t|cannot|can not)\s+you\s+do\??$|^what\s+are\s+you\s+(?:not\s+able|unable)\s+to\s+do\??$", re.I)


def _jh_fmt(v: float) -> str:
    if abs(v - round(v)) < 1e-9:
        return f"{int(round(v)):,}"
    return f"{v:,.4f}".rstrip("0").rstrip(".")


def _jh_num(x: str, lang: str = "en") -> float:
    """A number as the user wrote it (review 2026-11-15): in English 1,000 groups thousands and . is the decimal mark; in
    German . groups thousands and , is the decimal mark ("1.000" or "2,5")."""
    x = x.strip()
    if lang in ("de", "nl"):   # jh-r19-math: Dutch writes numbers as German does
        if re.fullmatch(r"-?\d{1,3}(?:\.\d{3})+(?:,\d+)?", x):   # 1.000 or 1.000,5
            return float(x.replace(".", "").replace(",", "."))
        return float(x.replace(",", "."))   # 2.5 or 2,5 (review round 2: 2.5 was read as 25)
    if re.fullmatch(r"-?\d{1,3}(?:,\d{3})+(?:\.\d+)?", x):
        return float(x.replace(",", ""))
    return float(x.replace(",", "."))


def _jh_fmt_lang(v: float, lang: str = "en") -> str:
    s = _jh_fmt(v)
    return s.replace(",", "X").replace(".", ",").replace("X", ".") if lang in ("de", "nl") else s   # jh-r19-math


def _jh_round3(hass, text, honorific, lang):
    """Round-3 instant answers before the other handlers: ("answer", text), ("ai", None), ("rewrite", text) or (None, None)."""
    raw = (text or "").strip()
    body = _jh_lead_off(raw).strip(" ?!.,")
    de = lang == "de"
    h = honorific
    if raw and not re.search(r"[^\W_]", raw):                     # only emoji or punctuation
        return "answer", (f"Wie kann ich helfen, {h}?" if de else f"How can I help, {h}?") if h else (
            "Wie kann ich helfen?" if de else "How can I help?")
    if _JH_WEATHERISH.search(body) and _JH_LATER.search(body):
        return "ai", None                                         # a forecast question: the AI calls the forecast tool
    if _JH_OUTSIDE.match(body):
        return "rewrite", "what's it like outside"
    # jh-r18-math (step 160, review round 18 #7): the sum patterns are English ("what's", "plus", "of", "percent"); words in
    # them make it an English question, read with English number rules (1,000 is a thousand) and answered in English. Bare
    # digits and signs keep the home's language ("1,5 + 2" in German), and so does a number that only German writes that way
    # (review round 1: "1.000" with dot groups, or a decimal comma such as "2,5").
    m = _JH_PCT.match(body) or _JH_SUM.match(body)
    # jh-r19-math (step 161, review round 19 #5, #10): only words that are English and nothing else make it English; "plus",
    # "minus" and "x" are German and Dutch too ("drei plus vier" reaches us as "3 plus 4"), so a bare sum keeps the home's
    # language and number rules ("1,250 plus 1" is 2,25 in German). A Dutch home answers in Dutch, with Dutch number rules.
    if (m and re.search(r"(?<![^\W\d_])(?:what'?s|what is|how much is|calculate|times|multiplied by|divided by|over|of|percent)"
                        r"(?![^\W\d_])", m.group(0), re.I)
            and not any(re.fullmatch(r"-?\d{1,3}(?:\.\d{3})+(?:,\d+)?|-?\d+,(?:\d{1,2}|\d{4,})", g or "")
                        for g in m.groups())):
        lang, de = "en", False
    nl = lang == "nl"   # jh-r19-math
    hn = "" if str(h or "").strip().lower() in ("sir", "madam", "ma'am", "miss") else str(h or "").strip()   # no English address
    m = _JH_PCT.match(body)
    if m:
        v = _jh_num(m.group(1), lang) * _jh_num(m.group(2), lang) / 100
        if nl:
            return "answer", f"{m.group(1)} procent van {m.group(2)} is {_jh_fmt_lang(v, lang)}" + (f", {hn}." if hn else ".")
        return "answer", (f"{m.group(1)} Prozent von {m.group(2)} sind {_jh_fmt_lang(v, lang)}, {h}." if de else
                          f"{m.group(1)}% of {m.group(2)} is {_jh_fmt(v)}, {h}.")
    m = _JH_SUM.match(body)
    if m:
        a, op, b = _jh_num(m.group(1), lang), m.group(2).lower(), _jh_num(m.group(3), lang)
        if op in ("/", "divided by", "over") and b == 0 and nl:
            return "answer", "Door nul kun je niet delen" + (f", {hn}." if hn else ".")
        if nl:
            v = a + b if op in ("+", "plus") else a - b if op in ("-", "minus") else a / b if op in ("/", "divided by", "over") else a * b
            return "answer", f"Dat is {_jh_fmt_lang(v, lang)}" + (f", {hn}." if hn else ".")
        if op in ("/", "divided by", "over") and b == 0:
            return "answer", ("Durch null kann man nicht teilen, %s." % h) if de else ("That can't be divided by zero, %s." % h)
        v = a + b if op in ("+", "plus") else a - b if op in ("-", "minus") else a / b if op in ("/", "divided by", "over") else a * b
        return "answer", (f"Das sind {_jh_fmt_lang(v, lang)}, {h}." if de else f"That's {_jh_fmt(v)}, {h}.")
    if _JH_WHO_LIVES.match(body):
        # jh-r17-resident (step 159, review round 17 #7): a person labelled not_resident (a guest login, JARVIS's own person) does
        # not live here, as in the day summary and the home status
        _jh_all_p = list(hass.states.async_all("person"))
        names = sorted({str(s.attributes.get("friendly_name") or s.entity_id.split(".", 1)[1]) for s in jh_residents(hass, _jh_all_p)})
        if not names and _jh_all_p:
            return "answer", (f"In Home Assistant ist niemand als Bewohner eingetragen, {h}." if de else
                              f"No one in Home Assistant is marked as living here, {h}.")
        if not names:
            return "answer", (f"In Home Assistant ist niemand als Person eingetragen, {h}." if de else
                              f"No one is set up as a person in Home Assistant, {h}.")
        joined = names[0] if len(names) == 1 else ", ".join(names[:-1]) + (" und " if de else " and ") + names[-1]
        return "answer", (f"Hier wohnen {joined}, {h}." if de else f"{joined} {'lives' if len(names) == 1 else 'live'} here, {h}.")
    if _JH_CANT.match(body):
        return "answer", (
            f"Ich kann nichts steuern oder sehen, was nicht in Home Assistant ist oder mir nicht freigegeben wurde, ich zeige niemandem "
            f"außer Administratoren die privaten Daten anderer, und ich erinnere mich nur an das, was man mir gesagt hat. Datum und Uhrzeit "
            f"kenne ich, {h}." if de else
            f"I can't control or see anything that isn't in Home Assistant or isn't shared with me, I don't show anyone's private "
            f"data to others unless they are an administrator, and I only remember what I have been told. I do know the date and "
            f"the time, {h}.")
    return None, None


_JH_PREV_Q = re.compile(r"^(?:(?:hey|ok|okay)\s+)?(?:jarvis[\s,]+)?what\s+(?:did|was\s+it\s+(?:that\s+)?)\s*i\s+(?:ask|say|said|asked)"
                        r"(?:\s+you)?(?:\s+(?:(\d+|a few|five|ten|two|three)\s+minutes?\s+ago|before|earlier|last|just now|"
                        r"a (?:moment|minute) ago|first))?\s*\??$", re.I)
_JH_WORD_NUM = {"two": 2, "three": 3, "five": 5, "ten": 10, "a few": 3}


async def jh_previous_question(hass, text, user_id, device_id, honorific, getter):
    """'What did I ask you before / 5 minutes ago?' from this login's own stored turns (voice with no login: this device's
    turns without a login). None when it is not that question."""
    m = _JH_PREV_Q.match((text or "").strip())
    if not m:
        return None
    if str(device_id or "").startswith("ch:chat:g:"):   # jh-r3-group-prev (review round 3): in a group JARVIS cannot tell who asked
        return ("In the group I can't tell who asked what" + (f", {honorific}" if honorific else "")
                + ". Ask me in your private chat with me and I'll tell you what you asked there.")
    # with no login a satellite is shared: only its last few minutes (as the seeding window), never another person's day
    hours, limit = (24, 40) if user_id else (10 / 60, 6)
    rows = await hass.async_add_executor_job(lambda: getter(hours=hours, limit=limit, user_id=user_id or "",
                                                            device_id=device_id or ""))   # step 132: this chat channel
    asks = [r for r in rows if r.get("role") == "user"]
    if asks and asks[-1].get("content", "").strip() == (text or "").strip():
        asks = asks[:-1]                                          # the question being answered now
    h = honorific
    if not asks:
        return f"You haven't asked me anything else today, {h}." if h else "You haven't asked me anything else today."
    from homeassistant.util import dt as dt_util
    pick = asks[0] if re.search(r"\bfirst\b", text or "", re.I) else asks[-1]   # review round 2: 'first' is the oldest
    if m.group(1):
        n = _JH_WORD_NUM.get(m.group(1).lower()) or int(m.group(1))
        want = dt_util.utcnow().replace(tzinfo=None).timestamp() - n * 60
        def ts(r):
            try:
                return __import__("datetime").datetime.fromisoformat(r["timestamp"]).timestamp()
            except Exception:
                return 0
        pick = min(asks, key=lambda r: abs(ts(r) - want))
    try:
        when = dt_util.as_local(dt_util.as_utc(__import__("datetime").datetime.fromisoformat(pick["timestamp"]))).strftime("%H:%M")
        when = f" at {when}"
    except Exception:
        when = ""
    return f'You asked{when}: "{pick.get("content", "").strip()}", {h}.' if h else f'You asked{when}: "{pick.get("content", "").strip()}".'


import contextvars as _jh_cv_ask
_JH_ASKER = _jh_cv_ask.ContextVar("jarvis_asker", default="")   # review round 2: the login asking (set by conversation.py)
_JH_WORK_Q = re.compile(r"^(?:when|what time)\s+(?:do|should|must|will|shall)\s+i\s+(?:need to\s+|have to\s+)?(?:leave|go|head out|set off|get up|wake up)"
                        r"(?:\s+(?:for|to)\s+(?:work|the office|office))?(?:\s+tomorrow)?\??$"
                        r"|^is\s+tomorrow\s+(?:an?\s+)?(?:office|work|working)\s*day\??$"
                        r"|^(?:do|will)\s+i\s+(?:go|have to go|need to go)\s+to\s+(?:the\s+)?(?:office|work)\s+tomorrow\??$"
                        r"|^(?:when|what time)\s+is\s+my\s+(?:alarm|wake[- ]?up)\s+tomorrow\??$", re.I)
_JH_SUMMARY_Q = re.compile(r"^(?:please\s+)?(?:summari[sz]e|sum up|give me a summary of|recap)\s+(?:my|the|to)\s*day\??$"
                           r"|^how\s+(?:was|is)\s+my\s+day\s+(?:going|so far)\??$|^(?:my\s+)?daily\s+summary\??$", re.I)
# jh-r22-work (step 164, review round 22 #4): one word set per role in the languages the kit detects (en, nl, de, fr, es, it, pt)
_JH_WORD_OFFICE = re.compile(r"office|workday|work day|werkdag|kantoor|arbeitstag|büro|buero|werktag|oficina|ufficio|escritório"
                             r"|escritorio|jour de travail|día laborable|dia laborable|giorno lavorativo|dia útil"
                             r"|bureau|jour ouvré|jour ouvre", re.I)   # jh-r23-work (step 165, review round 23 #5): French too
# jh-r23-work (step 165, review round 23 #5): "tomorrow" in every language the kit detects, for the office sensors
_JH_WORD_TOMORROW = re.compile(r"\b(?:tomorrow|morgen|demain|mañana|manana|domani|amanhã|amanha)\b", re.I)
_JH_WORD_LEAVE = re.compile(r"leave|depart|vertrek|commute|weggaan|abfahrt|abreise|losfahren|départ|salida|partenza|partida|saída"
                            r"|saida|pendel", re.I)
_JH_WORD_WAKE = re.compile(r"wake|alarm|wekker|morning|ochtend|get up|opstaan|wecker|aufstehen|aufwachen|weckzeit|morgenroutine"
                           r"|réveil|reveil|despertador|despertar|sveglia|acordar", re.I)
_JH_WORD_AUTO = re.compile(r"\b(?:work(?:day)?|office|morning|wake(?:[- ]?up)?|werk(?:dag)?|kantoor|ochtend|wekker|opstaan"
                           r"|morgen(?:routine)?|wecker|aufstehen|arbeit(?:stag)?|büro|buero|réveil|reveil|matin|travail"
                           r"|despertador|mañana|trabajo|sveglia|mattina|lavoro|manhã|trabalho)\b", re.I)


def _jh_name_of(s):
    return str(s.attributes.get("friendly_name") or s.entity_id.split(".", 1)[1].replace("_", " "))


def _jh_hhmm_of(value):   # jh-r6-work-day (review round 6): also "2026-11-17 08:00:00"
    m = re.search(r"(?:^|[\sT])(\d{1,2}):(\d{2})", str(value or ""))
    return f"{int(m.group(1)):02d}:{m.group(2)}" if m else None


async def _jh_calendar_has_events(hass, eid, start, end):
    try:
        comp = hass.data.get("calendar")
        ent = comp.get_entity(eid) if comp is not None else None
        if ent is None:
            return None
        return bool(await ent.async_get_events(hass, start, end))
    except Exception:
        return None


async def _jh_asker(hass, user_id):
    """(first name of the asker's person or '', the other people's first names, the asker is an admin)."""
    mine, others, admin = "", set(), False
    for p in hass.states.async_all("person"):
        first = (_jh_name_of(p).split() or [""])[0].casefold()
        if user_id and p.attributes.get("user_id") == user_id:
            mine = first
        elif first:
            others.add(first)
    try:
        u = await hass.auth.async_get_user(user_id) if user_id else None
        admin = bool(u and u.is_admin)
    except Exception:
        admin = False
    return mine, others - {mine}, admin


async def jh_calendars_for(hass, user_id):
    """Review round 10 (jh-r10-calendar): the calendars this login may read. None = every calendar (an administrator, or no
    login at all, as the round-8 briefing rule). A login that is not an administrator (the house group login too) gets the
    calendars named for itself plus the ones named for nobody, never one named for another member; when the login cannot be
    read, only the ones named for nobody."""
    if not user_id:
        return None
    try:
        mine, others, admin = await _jh_asker(hass, user_id)
    except Exception:
        mine, others, admin = "", {(_jh_name_of(p).split() or [""])[0].casefold() for p in hass.states.async_all("person")}, False
    if admin:
        return None
    out = set()
    for s in hass.states.async_all("calendar"):
        words = _jh_owner_words(_jh_name_of(s) + " " + s.entity_id, others | {mine})
        if (mine and mine in words) or not (words & (others - {""})):
            out.add(s.entity_id)
    return out


_JH_CAL_WORDS = frozenset(("agenda", "agendas", "calendar", "calendars", "kalender", "kalenders", "calendrier", "calendario",
                           "planning", "schedule", "rooster", "termine"))


def _jh_owner_words(text, names=()) -> set:
    """Review round 11/12 (jh-r12-possessive): the words of a name, with a possessive read as the person it belongs to.
    "Alex's calendar" / "Ed's agenda" (an apostrophe, any name length) and "agenda van Ed" always name that person. Without an
    apostrophe ("Eds agenda", "calendar.eds_agenda") the stem counts only when it is a member's first name AND a calendar word
    follows, so a shared "Bills" calendar (person Bill) stays shared, while "Bills agenda" is read as Bill's (the private side)."""
    text = re.sub(r"\b[a-z_]+\.(?=[a-z0-9_])", " ", str(text or ""))   # an entity id's domain ("calendar.") is no calendar word
    names = {n for n in (names or ()) if n}
    words = set(_jh_label_words(re.sub("['\u2019\u2018`´]s\\b", " ", text)).split())   # "Ed's" -> "ed"
    seq = _jh_label_words(re.sub("['\u2019\u2018`´]", "", text)).split()
    words |= set(seq)
    for i, w in enumerate(seq):
        if len(w) >= 2 and w.endswith("s") and w[:-1] in names and i + 1 < len(seq) and seq[i + 1] in _JH_CAL_WORDS:
            words.add(w[:-1])
    return words


def _jh_for_asker(cands, mine, others, admin):
    """Review round 2: the work-day entities that belong to the asker - named for them first, else ones named for nobody (one,
    or the first for an admin); never one named for another member."""
    def words(s):   # review round 11, 12: possessives too ("Robins agenda"), never a shared "Bills" (person Bill)
        return _jh_owner_words(_jh_name_of(s) + " " + s.entity_id, others | {mine})
    own = [s for s in cands if mine and mine in words(s)]
    if own:
        return own
    free = [s for s in cands if not (words(s) & others)]
    if len(free) <= 1 or admin:
        return free
    return []


def _jh_workday_cal(hass, eid) -> bool:
    """The Workday integration's calendar: it marks every working day, not the days at the office (known by its platform)."""
    try:
        from homeassistant.helpers import entity_registry as er
        return getattr(er.async_get(hass).async_get(eid), "platform", None) == "workday"
    except Exception:
        return False


async def jh_work_info(hass, user_id="", day=1):
    """What Home Assistant knows about the asker's work day tomorrow: (office tomorrow True/False/None, from what),
    [(automation name, time)], (leave helper name, time) or None, (wake helper name, time) or None. Only the asker's own
    calendars, helpers and automations (named for them, or named for nobody), never another member's (review round 2)."""
    from homeassistant.util import dt as dt_util
    mine, others, admin = await _jh_asker(hass, user_id)
    office, office_src = None, ""
    start = dt_util.start_of_local_day(dt_util.now() + __import__("datetime").timedelta(days=day))   # review round 6: today or tomorrow
    end = start + __import__("datetime").timedelta(days=1)
    for s in _jh_for_asker([c for c in hass.states.async_all("calendar")   # review round 3: never the Workday integration's
                            if _JH_WORD_OFFICE.search(_jh_label_words(_jh_name_of(c) + " " + c.entity_id))
                            and not _jh_workday_cal(hass, c.entity_id)], mine, others, admin):
        if s.state != "unavailable":
            has = await _jh_calendar_has_events(hass, s.entity_id, start, end)
            if has is not None:
                office, office_src = (True if has or office else False), _jh_name_of(s)
                if has:
                    break
    if office is None:
        for s in _jh_for_asker([b for b in hass.states.async_all("binary_sensor")   # jh-r13-roles: the office sensors only
                                if _JH_WORD_OFFICE.search(_jh_label_words(_jh_name_of(b) + " " + b.entity_id))
                                and _JH_WORD_TOMORROW.search(_jh_label_words(_jh_name_of(b) + " " + b.entity_id))],   # jh-r23-work
                               mine, others, admin):
            n = _jh_label_words(_jh_name_of(s) + " " + s.entity_id)
            if day == 1 and _JH_WORD_OFFICE.search(n) and _JH_WORD_TOMORROW.search(n) and s.state in ("on", "off"):   # jh-r23-work
                office, office_src = s.state == "on", _jh_name_of(s)
                break
    leave = wake = None
    # review round 13 (jh-r13-roles): each role (leave, wake) is chosen among its own helpers, so an unrelated helper (a vacation
    # date) never makes a shared leave or wake helper ambiguous for a member
    _jh_dts = list(hass.states.async_all("input_datetime"))
    _jh_leaves = [d for d in _jh_dts if _JH_WORD_LEAVE.search(_jh_name_of(d) + " " + d.entity_id)
                  and not _JH_WORD_WAKE.search(_jh_name_of(d) + " " + d.entity_id)]
    _jh_wakes = [d for d in _jh_dts if _JH_WORD_WAKE.search(_jh_name_of(d) + " " + d.entity_id)]
    for s in _jh_for_asker(_jh_leaves, mine, others, admin) + _jh_for_asker(_jh_wakes, mine, others, admin):
        t = _jh_hhmm_of(s.state if s.attributes.get("has_time", True) else "")
        if not t:
            continue
        if s.attributes.get("has_date") and str(s.state)[:10] != start.date().isoformat():   # a dated helper: that day only
            continue
        n = _jh_name_of(s) + " " + s.entity_id
        if leave is None and _JH_WORD_LEAVE.search(n) and not _JH_WORD_WAKE.search(n):
            leave = (_jh_name_of(s), t)
        elif wake is None and _JH_WORD_WAKE.search(n):
            wake = (_jh_name_of(s), t)
    autos = []
    try:
        from .agent import _jh_automation_triggers
        trig = _jh_automation_triggers(hass)
    except Exception:
        trig = {}
    for s in _jh_for_asker([a for a in hass.states.async_all("automation")
                            if _JH_WORD_AUTO.search(_jh_label_words(_jh_name_of(a) + " " + a.entity_id))], mine, others, admin):
        if s.state != "on":
            continue
        t = trig.get(s.entity_id, "")
        m = re.match(r"^at ([0-9]{1,2}:[0-9]{2})", t)
        if m:
            autos.append((_jh_name_of(s), _jh_hhmm_of(m.group(1))))
    return (office, office_src), autos, leave, wake


async def jh_work_answer(hass, h="sir", user_id="", text=""):
    # review round 6: about today when asked before today's leave or routine time without "tomorrow", else tomorrow
    day = 1
    if not re.search(r"\btomorrow\b", text or "", re.I):
        info0 = await jh_work_info(hass, user_id, 0)
        (_o0, _s0), autos0, leave0, wake0 = info0
        ref = (leave0 or (autos0[0] if autos0 else None) or wake0 or (None, None))[1]
        try:
            from homeassistant.util import dt as _jh_dtu6
            now_hm = _jh_dtu6.now().strftime("%H:%M")
        except Exception:
            now_hm = None
        if ref and isinstance(now_hm, str) and now_hm < ref:
            day = 0
    (office, src), autos, leave, wake = await jh_work_info(hass, user_id, day) if day else info0
    when = "Today" if day == 0 else "Tomorrow"
    parts = []
    if office is True:
        parts.append(f"{when} is an office day")
    elif office is False:
        parts.append(f"{when} is not an office day")
    if autos and office is not False:   # review 2026-11-15: no claim that a work routine runs on a day off
        name, t = autos[0]
        parts.append(f"your {name} routine is set for {t}")
    elif wake:
        parts.append(f"{wake[0]} is set to {wake[1]}")
    if leave:
        parts.append(f"you usually leave at {leave[1]} ({leave[0]})")
    if not parts:
        # jh-r22-work (step 164, review round 22 #4): what was not found, never that Home Assistant has none
        return (f"I can't tell when you leave for work, {h}: I found no work or office calendar, helper or morning automation "
                f"with a name I recognise." if h else "I can't tell when you leave for work: I found no work or office "
                "calendar, helper or morning automation with a name I recognise.")
    text = "; ".join(parts)
    text = text[0].upper() + text[1:]
    if not leave and office is not False:
        text += ". I don't know when you leave; nothing in Home Assistant says so"
    return text + (f", {h}." if h else ".")


_JH_PRODUCED = re.compile(r"solar|production|produced|return|returned|export|feed|generat|teruglever|opbrengst|zonne", re.I)
_JH_WHOLE = re.compile(r"\b(?:grid|mains|whole house|home total|total consumption|house total|woning|huis|netz)\b", re.I)


async def jh_energy_grid_stats(hass) -> set:
    """Review round 3: the grid meters on Home Assistant's Energy dashboard (stat_energy_from of each grid source); empty when
    there is none or it cannot be read."""
    try:
        from homeassistant.components.energy.data import async_get_manager
        prefs = (await async_get_manager(hass)).data or {}
    except Exception:
        return set()
    out = set()
    for source in prefs.get("energy_sources") or []:
        if source.get("type") != "grid":
            continue
        for flow in source.get("flow_from") or []:
            if flow.get("stat_energy_from"):
                out.add(flow["stat_energy_from"])
        if source.get("stat_energy_from"):
            out.add(source["stat_energy_from"])
    return out


async def jh_energy_grid_costs(hass) -> set:
    """Review round 4: the cost entities of the Energy dashboard's grid meters (stat_cost), empty when none or unreadable."""
    try:
        from homeassistant.components.energy.data import async_get_manager
        prefs = (await async_get_manager(hass)).data or {}
    except Exception:
        return set()
    out = set()
    for source in prefs.get("energy_sources") or []:
        if source.get("type") == "grid":
            for flow in source.get("flow_from") or []:
                if flow.get("stat_cost"):
                    out.add(flow["stat_cost"])
    return out


async def jh_energy_flows(hass) -> dict:
    """Review round 8 (jh-r8-energy): every energy statistic on Home Assistant's Energy dashboard by its role: grid_in (bought),
    grid_out (sold), solar (produced), battery_in (charged), battery_out (discharged); empty sets when none or unreadable."""
    out = {"grid_in": set(), "grid_out": set(), "solar": set(), "battery_in": set(), "battery_out": set()}
    try:
        from homeassistant.components.energy.data import async_get_manager
        prefs = (await async_get_manager(hass)).data or {}
    except Exception:
        return out
    for source in prefs.get("energy_sources") or []:
        kind = source.get("type")
        if kind == "grid":
            for flow in source.get("flow_from") or []:
                if flow.get("stat_energy_from"):
                    out["grid_in"].add(flow["stat_energy_from"])
            for flow in source.get("flow_to") or []:
                if flow.get("stat_energy_to"):
                    out["grid_out"].add(flow["stat_energy_to"])
            if source.get("stat_energy_from"):
                out["grid_in"].add(source["stat_energy_from"])
            if source.get("stat_energy_to"):
                out["grid_out"].add(source["stat_energy_to"])
        elif kind == "solar" and source.get("stat_energy_from"):
            out["solar"].add(source["stat_energy_from"])
        elif kind == "battery":
            if source.get("stat_energy_from"):
                out["battery_out"].add(source["stat_energy_from"])
            if source.get("stat_energy_to"):
                out["battery_in"].add(source["stat_energy_to"])
    return out


def jh_house_use(grid_in, roles, have) -> tuple:
    """Review round 8: the house's own use as the Energy dashboard counts it, grid_in + solar - grid_out + battery_out -
    battery_in. roles: the configured role -> kWh; have: whether every configured statistic was read. (kwh, whole_house):
    without solar, export or a battery the grid import is the house; with them and a figure missing, only the grid import is
    known (whole_house False)."""
    extra = {r: v for r, v in roles.items() if r != "grid_in"}
    if not extra:
        return grid_in, True
    if not have:
        return grid_in, False
    total = grid_in + extra.get("solar", 0) - extra.get("grid_out", 0) + extra.get("battery_out", 0) - extra.get("battery_in", 0)
    return max(total, 0.0), True


def _jh_meter_source(hass, s):
    """The sensor a utility meter counts (its source attribute, or its helper's options), or None."""
    src = s.attributes.get("source")
    if isinstance(src, str) and src:
        return src
    try:
        from homeassistant.helpers import entity_registry as er
        entry = er.async_get(hass).async_get(s.entity_id)
        ce = hass.config_entries.async_get_entry(entry.config_entry_id) if getattr(entry, "config_entry_id", None) else None
        src = (getattr(ce, "options", None) or {}).get("source") if ce is not None else None
        return src if isinstance(src, str) and src else None
    except Exception:
        return None


def _jh_label_words(s) -> str:
    """An id and name as words ('sensor.home_office_pc' -> 'sensor home office pc'), for whole-word tests."""
    return re.sub(r"[._\-]+", " ", str(s or "")).casefold()


def jh_today_energy(hass, grid=None, flows=None):
    """The energy used today, read live, counted once (review 2026-11-15): daily energy sensors (device_class energy, 'today'
    or 'daily' in id or name) in kWh (Wh and MWh converted); production and returned energy left out; when a whole-house or
    grid daily sensor exists it is the total and the device sensors are parts of it, never added to it.
    {total_kwh, whole_house, parts: [(name, kwh)], house: [(name, kwh)]}
    Review round 8 (jh-r8-energy): a daily meter is found by what it is (jh_is_daily_meter, any language; the English words
    only as the fallback). flows (jh_energy_flows): with solar, export or a battery on the Energy dashboard the house's use is
    grid import + solar - export + battery out - battery in from their daily meters; when one of those has no daily meter the
    figure is only the grid import and says so (grid_only True, whole_house False)."""
    parts, house, rows = [], [], []
    flows = flows or {}
    grid = set(grid or ()) | set(flows.get("grid_in") or ())
    roles = {r: set(flows.get(r) or ()) for r in ("grid_out", "solar", "battery_in", "battery_out") if flows.get(r)}
    linked = set(grid).union(*roles.values()) if roles else set(grid)
    flow_kwh = {}
    for s in hass.states.async_all("sensor"):
        label = (s.entity_id + " " + str(s.attributes.get("friendly_name") or "")).casefold()
        if not _jh_energy_unit(s) or not (jh_is_daily_meter(hass, s) or _jh_daily_words(label)):
            continue
        try:
            v = float(s.state)
        except (TypeError, ValueError):
            continue
        unit = str(s.attributes.get("unit_of_measurement") or "kWh").strip().lower()
        v = v / 1000 if unit == "wh" else v * 1000 if unit == "mwh" else v
        name = str(s.attributes.get("friendly_name") or s.entity_id)
        src = s.entity_id if s.entity_id in linked else _jh_meter_source(hass, s)
        role = next((r for r, ids in roles.items() if src in ids), None)
        if role:   # a solar, export or battery daily meter: part of the house figure, never a device
            key = (src, str(s.attributes.get("tariff") or ""))
            if v >= flow_kwh.setdefault(role, {}).get(key, -1.0):
                flow_kwh[role][key] = v
            continue
        if src not in grid and _JH_PRODUCED.search(label):
            continue
        rows.append((s, name, round(v, 3)))
    # review round 3: the whole-house meter is the daily meter of a grid meter on the Energy dashboard (one per tariff); name
    # words only when the dashboard has no grid meter or no daily meter counts it
    by_src, gridded = {}, set()   # jh-r4-tariffs (review round 4): one daily meter per grid meter and tariff, all added up
    for s, name, v in rows:
        src = s.entity_id if s.entity_id in (grid or ()) else _jh_meter_source(hass, s)
        if grid and src in grid:
            gridded.add(name)
            key = (src, str(s.attributes.get("tariff") or ""))
            if v >= by_src.get(key, (None, -1.0))[1]:
                by_src[key] = (name, v)
    if by_src:
        house = list(by_src.values())
        parts = [(n, v) for _s, n, v in rows if n not in gridded]
        # review round 8: every configured solar / export / battery statistic needs its daily meter, or only the import is known
        have = all({k[0] for k in flow_kwh.get(r, {})} >= ids for r, ids in roles.items())
        kwh, whole = jh_house_use(sum(v for _n, v in house), {r: sum(flow_kwh.get(r, {}).values()) for r in roles}, have)
        return {"total_kwh": round(kwh, 2), "whole_house": whole, "grid_only": not whole, "parts": parts, "house": house}
    for s, name, v in rows:
        label = (s.entity_id + " " + name).casefold()
        (house if _JH_WHOLE.search(_jh_label_words(label)) else parts).append((name, v))
    if house:
        return {"total_kwh": round(max(v for _n, v in house), 2), "whole_house": True, "parts": parts, "house": house}
    if parts:
        return {"total_kwh": round(sum(v for _n, v in parts), 2), "whole_house": False, "parts": parts, "house": []}
    return {"total_kwh": None, "whole_house": False, "parts": [], "house": []}


def jh_today_cost(hass, grid=None, costs=None):
    """Today's cost, counted once (review round 2): the cost-today sensors in the most used currency only; a whole-house or
    grid cost sensor is the total and the device costs are parts of it. {total, unit, whole_house, parts}"""
    rows = _jh_cost_sensors(hass)
    if not rows:
        return {"total": None, "unit": "", "whole_house": False, "parts": []}
    units = {}
    for s, _v in rows:
        u = str(s.attributes.get("unit_of_measurement") or "")
        units[u] = units.get(u, 0) + 1
    unit = max(units, key=units.get)
    rows = [(s, v) for s, v in rows if str(s.attributes.get("unit_of_measurement") or "") == unit]
    # review round 4: the whole-house cost is the daily meter of the Energy dashboard's grid cost (its stat_cost, or the cost
    # sensor Home Assistant makes for the grid meter), summed per tariff; name words only when there is none
    gcost = set(costs or ()) | {str(g) + "_cost" for g in (grid or ())}
    by_key = {}
    for s, v in rows:
        src = s.entity_id if s.entity_id in gcost else _jh_meter_source(hass, s)
        if gcost and src in gcost:
            key = (src, str(s.attributes.get("tariff") or ""))
            if v >= by_key.get(key, (None, -1.0))[1]:
                by_key[key] = (s, v)
    if by_key:
        mine = {id(s) for s, _v in by_key.values()}
        grid_rows = {id(s) for s, _v in rows if (s.entity_id in gcost or _jh_meter_source(hass, s) in gcost)}
        parts = [(str(s.attributes.get("friendly_name") or s.entity_id), round(v, 2)) for s, v in rows if id(s) not in grid_rows]
        return {"total": round(sum(v for _s, v in by_key.values()), 2), "unit": unit, "whole_house": True, "parts": parts}
    house = [(s, v) for s, v in rows if _JH_WHOLE.search(_jh_label_words(s.entity_id + " " + str(s.attributes.get("friendly_name") or "")))]
    parts = [(str(s.attributes.get("friendly_name") or s.entity_id), round(v, 2)) for s, v in rows if (s, v) not in house]
    if house:
        return {"total": round(max(v for _s, v in house), 2), "unit": unit, "whole_house": True, "parts": parts}
    return {"total": round(sum(v for _s, v in rows), 2), "unit": unit, "whole_house": False, "parts": parts}


async def jh_day_summary(hass, h="sir", user_id=None):
    """Short spoken summary of the day, from live states only."""
    out = []
    en = jh_today_energy(hass, await jh_energy_grid_stats(hass), await jh_energy_flows(hass))   # review round 3, 8: the dashboard
    if en["total_kwh"] is not None:
        line = (f"so far today the house used {en['total_kwh']:.1f} kWh" if en["whole_house"] else
                f"so far today {en['total_kwh']:.1f} kWh came from the grid (solar and battery not counted)" if en.get("grid_only") else
                f"so far today the measured devices used {en['total_kwh']:.1f} kWh")
        cost = jh_today_cost(hass, await jh_energy_grid_stats(hass), await jh_energy_grid_costs(hass))   # review round 4
        if cost["total"] is not None:   # review round 15 (jh-r15-cost): the cost is named for what it covers
            _jh_c = f"{cost['total']:.2f} {cost['unit']}".rstrip()
            if bool(cost.get("whole_house")) == bool(en["whole_house"] or en.get("grid_only")):
                line += f", costing {_jh_c}"
            elif cost.get("whole_house"):
                line += f"; the whole house cost {_jh_c}"
            else:
                line += f"; the measured devices cost {_jh_c}"
        out.append(line)
    people = []
    if user_id:   # review round 2: only the people this login may know about (the where-is rule); none with no login
        try:
            from .presence import _jh_may_see
            for p in hass.states.async_all("person"):
                if p.attributes.get("user_id") == user_id or await _jh_may_see(hass, user_id, p):
                    people.append(p)
        except Exception:
            people = []
    people = jh_residents(hass, people)   # jh-residents (step 158, C2): not_resident persons are left out
    if people:
        # jh-r17-away (step 158, review round 17 #1, truth first): only a real away state (not_home or a zone) is "out"; a person
        # whose state is unknown or unavailable is said as whereabouts unknown, never as out
        try:
            from .presence import _JH_DEAD as _jh_dead
        except Exception:
            _jh_dead = ("", "unknown", "unavailable", "none", "null")
        def _jh_known(p):
            return str(p.state or "").strip().lower() not in _jh_dead
        home = [_jh_name_of(p) for p in people if p.state == "home"]
        away = [_jh_name_of(p) for p in people if p.state != "home" and _jh_known(p)]
        unknown = [_jh_name_of(p) for p in people if p.state != "home" and not _jh_known(p)]
        bits = []
        if home:
            bits.append(", ".join(home) + (" is" if len(home) == 1 else " are") + " home")
        if away:
            bits.append(", ".join(away) + (" is" if len(away) == 1 else " are") + " out")
        if unknown:
            bits.append((unknown[0] + "'s whereabouts are unknown") if len(unknown) == 1 else
                        ("the whereabouts of " + ", ".join(unknown[:-1]) + " and " + unknown[-1] + " are unknown"))
        out.append(" and ".join(bits))
    lights = [_jh_name_of(s) for s in hass.states.async_all("light") if s.state == "on" and not _jh_is_setting(hass, s.entity_id)]
    out.append(f"{len(lights)} light{'s are' if len(lights) != 1 else ' is'} on" + (f" ({', '.join(lights[:4])}"
               + (" and more" if len(lights) > 4 else "") + ")" if lights else "") if lights else "no lights are on")
    try:
        switches, climates = _jh_heaters(hass)
        heating = [_jh_name_of(s) for s in switches + climates if _jh_heating(hass, s)]
        if switches or climates:
            out.append((", ".join(heating) + (" is" if len(heating) == 1 else " are") + " heating") if heating else "no heater is heating")
    except Exception:
        pass
    try:
        (office, _src), _a, _l, _w = await jh_work_info(hass, user_id) if user_id else ((None, ""), [], None, None)
        if office is not None:
            out.append("tomorrow is " + ("an office day" if office else "not an office day"))
    except Exception:
        pass
    try:
        wx = await _jh_weather_today(hass)
        if wx:
            out.append("weather: " + wx.strip().rstrip(".").replace("Today: ", "").replace("today: ", ""))
    except Exception:
        pass
    if not out:
        return None
    text = "; ".join(out)
    return text[0].upper() + text[1:] + (f", {h}." if h else ".")


async def _jh_round4(hass, text, honorific, lang):
    body = _jh_lead_off((text or "").strip()).strip(" ?!.,")
    if lang not in ("en", None):
        return None
    uid = _JH_ASKER.get()
    if _JH_WORK_Q.match(body):
        return await jh_work_answer(hass, honorific, uid, body)
    if _JH_SUMMARY_Q.match(body):
        return await jh_day_summary(hass, honorific, uid)
    return None


def _jh_pending_pick(pending_key, text) -> bool:
    """True when this message answers the pending "Which one?" (or a single "Do you mean X?" with yes)."""
    p = _JH_PENDING.get(pending_key)
    if not p or time.time() - p.get("at", 0) >= 90:
        return False
    if "?" in str(text or "") or "\uff1f" in str(text or ""):
        return False   # jh-r25-yes (step 167, review round 25 #5): "yes?" / "the office?" is a question, never the answer
    w = re.sub(r"\s+please$", "", re.sub(r"^(?:the|in the)\s+", "", _normalize(text or ""))).split()
    if w and any(set(w) <= set(re.sub(r"[()]", " ", x[1].lower()).split()) for x in p["options"]):   # jh-r23-dup: "Lamp (Office)"
        return True
    return len(p["options"]) == 1 and bool(re.fullmatch(_JH_YES, " ".join(w)))


def jh_forget_pending(pending_key) -> None:
    """Review 2026-11-15: a message that is no answer to JARVIS's own question ends that question (good-night yes, which one)."""
    _JH_GN_ASKED.pop(pending_key, None)
    _JH_GN_PLAN.pop(pending_key, None)
    _JH_PENDING.pop(pending_key, None)


def jh_text_lang_sure(text):
    """'de' or 'en' when the request is clearly written in it (two of its own words), else None (the HA language stays)."""
    try:
        lang = jh_text_language(text)
    except Exception:
        return None
    if lang == "de":
        return "de"
    if lang == "en":
        words = re.findall(r"[a-zà-ÿ']+", (text or "").lower())
        return "en" if sum(1 for w in words if w in _JH_STOPWORDS["en"]) >= 2 else None
    return None


# jh-r28-private (step 171, review round 28 #4): the asker's private entities for the whole instant request (None: not read)
import contextvars as _jh_cv28
_JH_PRIV28 = _jh_cv28.ContextVar("jarvis_local_private", default=None)


def _jh_private_fallback(hass):
    """jh-r28-private: the fail-closed private set when the asker's rule cannot be read: every person, tracker, geocoded
    location, mobile-app entity (and every entity of a mobile-app device) and every health reading."""
    hid = set()
    try:
        for d in ("person", "device_tracker"):
            hid |= {s.entity_id for s in hass.states.async_all(d)}
        hid |= {s.entity_id for s in hass.states.async_all("sensor") if s.entity_id.endswith("_geocoded_location")}
        from homeassistant.helpers import entity_registry as er
        ents = list(er.async_get(hass).entities.values())
        devs = {getattr(e, "device_id", None) for e in ents if getattr(e, "platform", None) == "mobile_app"} - {None}
        hid |= {str(e.entity_id) for e in ents if getattr(e, "platform", None) == "mobile_app" or getattr(e, "device_id", None) in devs}
    except Exception:  # noqa: BLE001
        pass
    try:   # the wearables biometrics knows (its own fail-closed answer, every sensor, would hide the thermometers too)
        from .agent import _jh_health_ids
        health = set(_jh_health_ids(hass))
        if health != {s.entity_id for d in ("sensor", "binary_sensor") for s in hass.states.async_all(d)}:
            hid |= health
    except Exception:  # noqa: BLE001
        pass
    return lambda e: (str(e) in hid or str(e).split(".", 1)[0] in ("person", "device_tracker")
                      or str(e).endswith("_geocoded_location"))


async def _jh_local_private(hass):
    """jh-r28-private (step 171): is_hidden(entity_id) for the login asking (_JH_ASKER; '' = voice with no login, which sees no
    one): THE rule of the AI tools and the websocket commands (agent._jh_private_ids). Fails closed (_jh_private_fallback)."""
    try:
        from . import agent as _jh_ag28
        tok = _jh_ag28._JH_CALLER.set(_JH_ASKER.get() or "")
        try:
            hidden = set(await _jh_ag28._jh_private_ids(hass))
        finally:
            _jh_ag28._JH_CALLER.reset(tok)
        return lambda e: str(e) in hidden
    except Exception as exc:  # noqa: BLE001
        _LOGGER.debug("JARVIS: the private set could not be read (%s); people's devices are hidden", exc)
        return _jh_private_fallback(hass)


def _jh_local_hidden(hass, eid) -> bool:
    """jh-r28-private (step 171, review round 28 #4): True when the asker may not read this entity (another person's phone,
    tracker or app sensor, a wearable). Outside try_local the fail-closed set counts."""
    f = _JH_PRIV28.get()
    try:
        return bool((f or _jh_private_fallback(hass))(str(eid)))
    except Exception:  # noqa: BLE001
        return True


def _jh_private_reply(hass) -> str:
    """jh-r28-private: the where-is refusal (in the reply language), never the state."""
    try:
        from .presence import _jh_refusal
        return _jh_refusal(hass)
    except Exception:  # noqa: BLE001
        return "I'm sorry, I only share that with that person or an administrator."


async def try_local(hass, text, honorific="sir", force=False, lang=None, pending_key=""):
    """jh-r19-tail (step 161, review round 19 #1): THE entry of the instant path. An order with a time, condition or negation
    tail ("turn on the heater at 7 / later / in 5 / not", "schalte die Heizung nicht ein / um 7 ein") is never acted on here:
    _JH_TAIL is set for the whole request and every actuation path calls _jh_act_guard(). Online the AI gets the order, offline
    the reply says honestly that nothing was switched. A "which one?" about such an order is not asked either (its bare answer
    would act at once). Everything that switches nothing (answers, questions) works as before."""
    tail = jh_order_tail(text, hass)   # jh-r20-name (step 162): a time word inside a real name is no tail
    if tail and force and jh_answer_elsewhere(text) and _jh_order_shape(text):
        return LocalResult(text=_jh_tail_reply(honorific, text), success=False)   # offline, in the order's own language
    asked = _JH_PENDING.get(pending_key)
    tok = _JH_TAIL.set(tail)
    ptok = _JH_PRIV28.set(await _jh_local_private(hass))   # jh-r28-private (step 171): the asker's private set, read once
    try:
        res = await _jh_try_local_body(hass, text, honorific, force=force, lang=lang, pending_key=pending_key)
    except _JHTailNoAct:
        _LOGGER.info("Local: an order with a time, condition or negation - nothing switched here: %s", (text or "")[:80])
        res = LocalResult(text=_jh_tail_reply(honorific, text), success=False) if force else None
    finally:
        _JH_TAIL.reset(tok)
        _JH_PRIV28.reset(ptok)   # jh-r28-private (step 171)
    if tail and pending_key in _JH_PENDING and _JH_PENDING.get(pending_key) is not asked:
        _JH_PENDING.pop(pending_key, None)   # no "which one?" about a timed or negated order
        res = LocalResult(text=_jh_tail_reply(honorific, text), success=False) if force else None
    return res


async def _jh_try_local_body(hass, text, honorific="sir", force=False, lang=None, pending_key=""):
    """Jarvis Home patch (2026-11-15): jh-no-act-on-questions. The instant path, with the guard: a hypothetical goes to the AI,
    a question may be answered but switches nothing (the AI gets it when the instant answer would act), a long question with
    several parts goes to the AI."""
    # review 2026-11-15: the answer to JARVIS's own question first, in any language; any other message ends that question
    # jh-r25-yes (step 167, review round 25 #5): a '?' anywhere makes it a question, never the yes or the pick
    _jh_gn_ok = (pending_key in _JH_GN_ASKED and "?" not in str(text or "")
                 and bool(_JH_GN_YES.fullmatch(_jh_command_text(_normalize(text or "")))))
    _jh_pick_ok = _jh_pending_pick(pending_key, text)
    if not _jh_gn_ok:
        _JH_GN_ASKED.pop(pending_key, None)
    if not _jh_pick_ok:
        _JH_PENDING.pop(pending_key, None)
    if _jh_gn_ok or _jh_pick_ok:
        _jh_t1, _jh_t2 = _JH_QUERY_ONLY.set(False), _JH_MULTI.set(False)
        try:
            return await _jh_try_local_inner(hass, text, honorific, force=force, lang=lang, pending_key=pending_key)
        except _JHTailNoAct:
            raise   # jh-r19-tail: try_local answers it
        except _JHNoAct:
            return None
        finally:
            _JH_QUERY_ONLY.reset(_jh_t1)
            _JH_MULTI.reset(_jh_t2)
    lang = jh_text_lang_sure(text) or lang   # review 2026-11-15: reply in the language of the request (en, de)
    if jh_answer_elsewhere(text):   # jh-reply-lang: the AI answers in the language the user wrote in
        _LOGGER.info("Local: written in another language - to the AI: %s", (text or "")[:80])
        return None
    try:   # jh-round3: instant answers and forecast questions (step 131)
        _jh_r3, _jh_r3v = _jh_round3(hass, text, honorific, _output_lang(hass, lang))
    except Exception:
        _jh_r3, _jh_r3v = None, None
    if _jh_r3 == "answer":
        return LocalResult(text=_jh_r3v, success=True)
    if _jh_r3 == "ai":
        return None
    if _jh_r3 == "rewrite":
        text = _jh_r3v
    try:   # jh-round4: work day and day summary (step 132)
        _jh_r4 = await _jh_round4(hass, text, honorific, _output_lang(hass, lang))
    except Exception:
        _jh_r4 = None
    if _jh_r4:
        return LocalResult(text=_jh_r4, success=True)
    kind = _jh_utterance_kind(text)
    if kind == "hypothetical":
        _LOGGER.info("Local: a hypothetical is no command - to the AI: %s", (text or "")[:80])
        return None
    multi = _jh_multi_part(text)
    if kind == "question" and multi:
        return None
    if not _JH_GN_YES.fullmatch(_jh_command_text(_normalize(text or ""))):   # jh-goodnight-yes: only the very next message
        _JH_GN_ASKED.pop(pending_key, None)
    t1, t2 = _JH_QUERY_ONLY.set(kind == "question"), _JH_MULTI.set(multi)
    try:
        return await _jh_try_local_inner(hass, text, honorific, force=force, lang=lang, pending_key=pending_key)
    except _JHTailNoAct:
        raise   # jh-r19-tail: try_local answers it
    except _JHNoAct:
        _LOGGER.info("Local: a question is no command, nothing was switched - to the AI: %s", (text or "")[:80])
        return None
    finally:
        _JH_QUERY_ONLY.reset(t1)
        _JH_MULTI.reset(t2)


async def _jh_try_local_inner(hass, text, honorific="sir", force=False, lang=None, pending_key=""):
    """
    PRIMARY handler. Returns LocalResult if handled, None for LLM fallback.

    When force=True (offline salvage mode), the complexity gate is bypassed:
    JARVIS attempts to extract and execute any actionable device command or
    answerable query from the text even if it looks complex, because escalating
    to the cloud LLM is not an option. Conversational/creative requests that
    have no local handler still return None — the caller supplies an honest
    offline response.

    ``lang`` overrides the resolved household output language (#511): replies are
    rendered in it via local_i18n with zero API cost. None → resolve from config.
    """
    from . import local_i18n
    # Resolve through configured_language so the spoken/pipeline language that the
    # conversation front door hands in as `lang` wins (#522): a command spoken to
    # a German satellite is confirmed in German even when output_language is Auto
    # and the system language is English. A None `lang` resolves the household
    # output language exactly as before (output_language → ui_language → system).
    lang = _output_lang(hass, lang)
    _JH_LANG.set(lang)   # jh-local-lang: the kit's replies follow it too
    normalized = _normalize(text)
    complexity = score_complexity(text)
    _jh_live = await _jh_live_answer(hass, text, honorific)   # jh-live-answers
    if _jh_live is not None:
        return _jh_live
    if _JH_TAIL.get() and _jh_order_shape(text):
        _jh_act_guard()   # jh-r19-tail (step 161, review round 19 #1): a timed, conditional or negated order: nothing here
    for _jh_k in [k for k, v in _JH_PENDING.items() if time.time() - v["at"] >= 90]:
        del _JH_PENDING[_jh_k]
    _jh_p = _JH_PENDING.pop(pending_key, None)
    if _jh_p:
        _jh_w = re.sub(r"\s+please$", "", re.sub(r"^(?:the|in the)\s+", "", normalized)).split()
        _jh_m = [x for x in _jh_p["options"] if _jh_w and set(_jh_w) <= set(re.sub(r"[()]", " ", x[1].lower()).split())]   # jh-r23-dup: the room
        if not _jh_m and len(_jh_p["options"]) == 1 and re.fullmatch(_JH_YES, " ".join(_jh_w)):
            _jh_m = _jh_p["options"]   # "Do you mean X?" has a yes for an answer
        if len(_jh_m) == 1 and _jh_p.get("colour"):
            return await _jh_try_color(hass, f"set {_jh_m[0][1].lower()} to {_jh_p['colour']}", honorific)
        if len(_jh_m) == 1:
            _jh_eid, _jh_name = _jh_m[0]
            if _jh_p["action"] == "activate":   # jh-r24-res (step 166, review round 24 #6): the scene or script chosen, only that one
                _jh_dt = _jh_eid.split(".", 1)[0]
                _jh_fn = str(getattr(hass.states.get(_jh_eid), "attributes", {}).get("friendly_name") or _jh_eid)
                _jh_mr = _jh_scene_manual_reply(hass, _jh_eid, _jh_fn, honorific, lang)
                if _jh_mr:
                    return LocalResult(text=_jh_mr, success=False)
                if _jh_dt not in ("scene", "script") or hass.states.get(_jh_eid) is None:
                    return LocalResult(text=_jh_t("jh_unavailable_nochange", name=_jh_fn, h=honorific), success=False)
                try:
                    if not await _seam_execute(hass, _jh_dt, "turn_on", {"entity_id": _jh_eid}, blocking=True, action="activate"):
                        raise RuntimeError("activation did not complete")
                    _update_ctx(entity=_jh_eid, domain=_jh_dt)
                    return LocalResult(text=local_i18n.t("scene_activating", lang, name=_jh_fn, h=honorific), success=True)
                except Exception as exc:  # noqa: BLE001
                    return LocalResult(text=local_i18n.t("scene_fail", lang, name=_jh_fn, h=honorific, exc=exc), success=False)
            if _needs_confirmation(hass, _jh_p["action"], _jh_eid):
                return None
            if getattr(hass.states.get(_jh_eid), "state", "unavailable") == "unavailable":
                # the device dropped out after the question: a command to it does nothing, so never report it done
                return LocalResult(text=_jh_t("jh_unavailable_nochange", name=_jh_name, h=honorific), success=False)
            if _jh_is_manual(hass, _jh_eid):   # jh-manual-heater: the answer to "which one" never switches one
                return LocalResult(text=_jh_manual_said(hass, _jh_eid, _jh_p["action"], honorific), success=False)
            _jh_ok = await _execute_action(hass, _jh_p["action"], _jh_eid, {})
            _update_ctx(entity=_jh_eid, domain=_jh_eid.split(".")[0], action=_jh_p["action"])
            return jh_single_result(_jh_p["action"], _jh_name, _jh_ok, {}, honorific)   # jh-r12-act: a slow device is no failure

    # High complexity → immediate LLM (skipped in force/offline mode)
    if complexity >= 70 and not force:
        _LOGGER.debug("Local: complexity %d for '%s' — LLM", complexity, text[:60])
        return None

    _jh_color = await _jh_try_color(hass, normalized, honorific, pending_key)
    if _jh_color is None:   # Jarvis Home patch (2026-11-11): a lamp's own effect or scene by name
        _jh_color = await _jh_try_effect(hass, normalized, honorific)
    if _jh_color is not None:
        return _jh_color

    # Contextual queries
    for pattern, qtype in _QUERY_PATTERNS:
        match = None if _JH_MULTI.get() else re.search(pattern, normalized)   # jh-no-act-on-questions: one question only
        if match:
            area_m = match.group(1) if match.lastindex else ""
            resp = (await _jh_who_home(hass, honorific, lang)) if qtype == "who_home" else _ctx_query(hass, qtype, honorific, area_m, lang)
            if not resp and qtype == "energy":   # Jarvis Home patch (2026-11-10): no whole-house meter - the devices, read live
                resp = await _jh_power_now(hass, honorific)
            if resp and qtype == "weather":   # jh-live-answers: today's high, low and rain from the forecast
                resp = resp + await _jh_weather_today(hass)
            if resp:
                _LOGGER.info("Local contextual: %s", qtype)
                return LocalResult(text=resp, success=True)

    # Bulk/multi-entity
    for pattern, action, domain, scope in _BULK_PATTERNS:
        match = re.fullmatch(pattern, _jh_command_text(normalized))   # jh-no-act-on-questions: the whole order
        if not match:
            continue
        area_name = match.group(1) if scope == "area" and match.lastindex else None
        if domain == "lock" and action == "unlock":
            # jh-r23-bulk (step 165, review round 23 #1): every lock of the home unlocked by one sentence is never done here; with
            # voice confirmation on it goes to the agent, which confirms each door (its bulk_control never unlocks in bulk)
            if _needs_confirmation(hass, action, ""):
                return None
            return LocalResult(text=_jh_t("jh_no_bulk_unlock", h=honorific), success=False)
        if area_name:   # jh-r16-room (review round 16): the bulk room takes the same room rule as the room order
            _jh_bsaid = re.sub(r"^(?:turn|switch|shut)\s+(?:(?:on|off)\s+)?(?:all\s+(?:of\s+)?)?(?:the\s+)?", "",
                               _jh_command_text(normalized))   # jh-r23-bulk: every word order of the room row
            _jh_one = not re.match(r"lights\b", _jh_bsaid)   # "the light on the stairs" is one light, "the lights in X" a room
            area_name = _jh_room_named(hass, area_name, None, domain, (_jh_bsaid,),
                                       ("light", "lamp") if _jh_one else ("lights", "lamps")) or None
            if not area_name:
                continue   # no room by that whole name, or the words name a device: the device path or the AI answers
        if _needs_confirmation(hass, action, ""):
            _LOGGER.info("Local: bulk '%s' needs confirmation — deferring to agent", action)
            return None   # protected bulk → agent skips/confirms per device
        # jh-r25-bulk (step 167), jh-r25-group (review round 25 #1, #10, #12, #13): THE group function decides the targets (the exact room,
        # every device of it, the kind, no settings, the hidden-twin rule, no manual heater, never a barrier or security device
        # for any action, every lock in a lock order, the garage / gate locks said); everything it left out is named
        _jh_cmd25 = _jh_command_text(normalized)
        _jh_grp = jh_group_targets(hass, domain, action, None, area_name, _jh_cmd25)
        if _jh_grp.error:
            return LocalResult(text=_jh_t("jh_none_switched", h=honorific), success=False)   # fails closed
        _jh_out = jh_group_note(hass, _jh_grp)
        if domain == "all":
            # jh-r12-act: every domain sent first, then one check over all of them; the unconfirmed ones are named
            _jh_all = [e for e in _jh_grp.ids if getattr(hass.states.get(e), "state", "") == "on"]
            if not _jh_all and _jh_grp.left_out:
                return LocalResult(text=jh_group_left_all(_jh_grp, honorific), success=False)
            _jh_conf, _jh_unc, _jh_fail = await jh_group_act(hass, _jh_grp, "turn_off", _jh_all)
            total = len(_jh_conf)
            return LocalResult(text=jh_group_text(hass, local_i18n.t(
                "bulk_alloff", lang, h=honorific, total=total,
                noun=local_i18n.noun("device", total, lang)), _jh_conf, _jh_unc, honorific, _jh_fail) + _jh_out,
                success=total > 0)
        entities = _jh_grp.targets
        if not entities and not _jh_grp.left_out:
            return LocalResult(text=_jh_t("jh_kind_none", h=honorific, what=jh_bulk_kind(domain, _jh_cmd25)[1] + (
                local_i18n.t("area_in", lang, area=area_name) if area_name else "")), success=False)
        if not entities:
            return LocalResult(text=jh_group_left_all(_jh_grp, honorific), success=False)
        _jh_todo, _jh_same, _jh_gone = _jh_split_change(hass, entities, action)   # review round 2: no fake Done
        if not _jh_todo:
            return LocalResult(text=_jh_t("jh_skip_none", h=honorific, why=_jh_skip_why(_jh_same, _jh_gone, action)) + _jh_out,
                               success=False)
        entities = _jh_todo
        _jh_conf, _jh_unc, _jh_fail = await jh_group_act(hass, _jh_grp, action, [eid for eid, fn in entities])   # jh-r12-act, r13
        ok = len(_jh_conf)
        area_str = local_i18n.t("area_in", lang, area=area_name) if area_name else ""
        return LocalResult(text=jh_group_text(hass, local_i18n.t(
            "bulk_generic", lang, h=honorific, ok=ok,
            noun=local_i18n.noun(domain, ok, lang), area=area_str,
            verb=local_i18n.bulk_verb(action, lang)) + ((" (" + _jh_skip_why(_jh_same, _jh_gone, action) + ")")
                                                        if (_jh_same or _jh_gone) else ""),
            _jh_conf, _jh_unc, honorific, _jh_fail) + _jh_out,
            success=ok > 0)

    # jh-r23-oneroom (step 165, review round 23 #1): "the light in the kitchen" (singular) is the ONE light of that room: switched
    # when the room has one light, else the which-one question (the router: nothing); never every light of the room
    _jh_1r = re.fullmatch(r"(?:(?:turn|switch|shut)\s+(on|off)\s+(?:the\s+)?(?:light|lamp)|(?:turn|switch|shut)\s+(?:the\s+)?"
                          r"(?:light|lamp)\s+(on|off))\s+(?:in|on)\s+(?:the\s+)?(.+)", _jh_command_text(normalized))
    _jh_1room = _jh_room_named(hass, _jh_1r.group(3), None, "light", (_jh_1r.group(3),), ("light", "lamp")) if _jh_1r else ""
    if _jh_1room:
        _jh_1act = "turn_on" if (_jh_1r.group(1) or _jh_1r.group(2)) == "on" else "turn_off"
        _jh_1ls = [x for x in _find_entities_in_area(hass, _jh_1room, "light", True)
                   if not _jh_is_setting(hass, x[0]) and not _jh_hidden(hass, x[0]) and _jh_is_live(hass, x[0])]
        if len(_jh_1ls) > 1:
            return _jh_ask_which(hass, pending_key, _jh_1act, _jh_1ls, honorific)
        if len(_jh_1ls) == 1:
            _jh_1e, _jh_1n = _jh_1ls[0]
            if _jh_is_manual(hass, _jh_1e):
                return LocalResult(text=_jh_manual_said(hass, _jh_1e, _jh_1act, honorific, lang), success=False)
            if _needs_confirmation(hass, _jh_1act, _jh_1e):
                return None
            if (getattr(hass.states.get(_jh_1e), "state", "") == "on") == (_jh_1act == "turn_on"):
                return LocalResult(text=_jh_t("jh_already_one_" + ("on" if _jh_1act == "turn_on" else "off"), name=_jh_1n,
                                              h=honorific), success=True)
            _jh_1ok = await _execute_action(hass, _jh_1act, _jh_1e, {})
            _update_ctx(entity=_jh_1e, domain="light", action=_jh_1act)
            return jh_single_result(_jh_1act, _jh_1n, _jh_1ok, {}, honorific)

    # Scene/script (check before single-entity to catch "activate X")
    for pattern, action, _ in _INTENT_PATTERNS:
        if action != "scene":
            continue
        match = re.fullmatch(pattern, _jh_command_text(normalized))   # jh-no-act-on-questions: the whole order
        if not match:
            continue
        name_frag = match.group(1) if match.lastindex else ""
        if not name_frag:
            continue
        # jh-r25-scene (step 167, review round 25 #15): "activate the good night scene": the pattern takes "scene" off the
        # name, so the kind said is passed on (it decides between a scene and a script of that name, no which-one question)
        _jh_said25 = "scene" if (_jh_command_text(normalized).endswith(" scene")
                                 and not (" " + str(name_frag).strip()).endswith((" scene", " script"))) else None
        _jh_sc24 = _jh_scene_candidates(hass, name_frag, said_kind=_jh_said25)
        if len(_jh_sc24) > 1:   # jh-r24-res (step 166, review round 24 #6): a scene and a script of that name: which one, never both
            return _jh_scene_which(hass, pending_key, _jh_sc24, honorific)
        found = _jh_sc24[0] if _jh_sc24 else None
        if found:
            eid, fname, dtype = found
            _jh_mr = _jh_scene_manual_reply(hass, eid, fname, honorific, lang)   # review round 5: say why
            if _jh_mr:
                return LocalResult(text=_jh_mr, success=False)
            try:
                ok = await _seam_execute(hass, dtype, "turn_on", {"entity_id": eid},
                                         blocking=True, action="activate")
                if not ok:
                    raise RuntimeError("activation did not complete")
                _update_ctx(entity=eid, domain=dtype)
                return LocalResult(text=local_i18n.t("scene_activating", lang,
                                                     name=fname, h=honorific), success=True)
            except Exception as exc:
                return LocalResult(text=local_i18n.t("scene_fail", lang, name=fname,
                                                     h=honorific, exc=exc), success=False)

    # Goodnight shortcut
    # jh-r25-gn (step 167, review round 25 #3, #4): the question promises exactly what "yes" will do: the owner's own Good Night
    # scene or script when there is one ("Shall I run your Good Night routine?"; a scene and a script of that name: which
    # one), else the built-in lights off and lock-up ("Shall I switch off the lights and lock up?"). The yes runs what was
    # asked. The lock-up names every lock that is still not locked, or unavailable.
    if re.search(r"^good\s*night(?:\s+jarvis)?$", normalized):   # jh-no-act-on-questions: a greeting, not an order
        _jh_gq = _jh_scene_candidates(hass, "goodnight") or _jh_scene_candidates(hass, "good night")
        if len(_jh_gq) > 1:
            _JH_GN_ASKED.pop(pending_key, None)
            return _jh_scene_which(hass, pending_key, _jh_gq, honorific)
        _JH_GN_ASKED[pending_key] = time.time()   # jh-goodnight-yes
        _JH_GN_PLAN[pending_key] = _jh_gq[0][0] if _jh_gq else ""
        if _jh_gq:
            return LocalResult(text=_jh_t("jh_goodnight_ask_routine", h=honorific, name=re.sub(
                r"\s+(?:routine|scene|script|mode)$", "", str(_jh_gq[0][1]).strip(), flags=re.I)), success=True)
        return LocalResult(text=_jh_t("jh_goodnight_ask", h=honorific), success=True)
    _jh_gn_at = _JH_GN_ASKED.pop(pending_key, None) if _JH_GN_YES.fullmatch(_jh_command_text(normalized)) else None
    _jh_gn_yes = _jh_gn_at is not None and time.time() - _jh_gn_at <= 120
    _jh_gn_plan = _JH_GN_PLAN.pop(pending_key, None) if _jh_gn_yes else None
    if _jh_gn_yes or re.fullmatch(
            r"(?:(?:run|start|activate|do|begin)\s+(?:the\s+)?good\s*night(?:\s+(?:routine|scene|mode))?"
            r"|good\s*night\s+(?:routine|scene|mode))", _jh_command_text(normalized)):
        if _jh_gn_plan == "":
            _jh_gsc = []   # jh-r25-gn: the question promised the lights and the lock-up, so that is what the yes runs
        elif _jh_gn_plan:   # jh-r25-gn: the routine the question named
            _jh_pst = hass.states.get(_jh_gn_plan)
            if _jh_pst is None:
                return LocalResult(text=_jh_t("jh_unavailable_nochange", name=_jh_gn_plan, h=honorific), success=False)
            _jh_gsc = [(_jh_gn_plan, str(_jh_pst.attributes.get("friendly_name") or _jh_gn_plan), _jh_gn_plan.split(".", 1)[0])]
        else:
            _jh_gsc = _jh_scene_candidates(hass, "goodnight") or _jh_scene_candidates(hass, "good night")
        _jh_gk = re.search(r"\b(scene|script)\b", _jh_command_text(normalized))   # "good night scene": the scene
        if _jh_gk and len(_jh_gsc) > 1:
            _jh_gsc = [x for x in _jh_gsc if x[2] == _jh_gk.group(1)] or _jh_gsc
        if len(_jh_gsc) > 1:   # jh-r24-res (step 166, review round 24 #6): ask which; never both, never the all-off fallback
            return _jh_scene_which(hass, pending_key, _jh_gsc, honorific)
        found = _jh_gsc[0] if _jh_gsc else None
        if found:
            eid, fname, dtype = found
            _jh_mr = _jh_scene_manual_reply(hass, eid, fname, honorific, lang)   # review round 5: no silent all-off instead
            if _jh_mr:
                return LocalResult(text=_jh_mr, success=False)
            try:
                if not await _seam_execute(hass, dtype, "turn_on", {"entity_id": eid},
                                           blocking=True, action="activate"):
                    raise RuntimeError("activation did not complete")
                return LocalResult(text=local_i18n.t("goodnight_scene", lang,
                                                     name=fname, h=honorific), success=True)
            except Exception as exc:
                _LOGGER.warning("JARVIS: goodnight scene/script '%s' failed to run: %s", eid, exc)
                # review round 8 (jh-r8-goodnight): the owner's own good-night scene or script did not run; say so, and do
                # not switch other devices in its place
                return LocalResult(text=_jh_scene_manual_reply(hass, eid, fname, honorific, lang) or local_i18n.t(
                    "scene_fail", lang, name=fname, h=honorific, exc=exc), success=False)
        # jh-r12-act (review round 12): the lights through the shared send-and-confirm step; only confirmed ones count.
        # jh-r25-group (step 167): the lights and the locks through THE group function (a lock-up takes every lock)
        _jh_gl = jh_group_targets(hass, "light", "turn_off", None, None, "")
        _jh_gn_conf, _jh_gn_unc, _jh_gn_fail = await jh_group_act(hass, _jh_gl, "turn_off", [
            e for e in _jh_gl.ids if getattr(hass.states.get(e), "state", "") == "on"])
        off_count = len(_jh_gn_conf)
        # jh-r25-gn (step 167, review round 25 #3): the locks are confirmed like the lights; every lock that is still not
        # locked (did not confirm, refused, unavailable, jammed) is named, so "lock up" is never claimed by implication
        _jh_gks = jh_group_targets(hass, "lock", "lock", None, None, "")
        _jh_kto = [e for e in _jh_gks.ids if getattr(hass.states.get(e), "state", "") == "unlocked"]
        _jh_kc, _jh_ku, _jh_kf = await jh_group_act(hass, _jh_gks, "lock", _jh_kto)
        for _jh_ke in _jh_kf:
            _LOGGER.warning("JARVIS: goodnight could not lock %s", _jh_ke)   # security: a refused lock is logged too
        _jh_kopen = dict(_jh_ku)
        _jh_kopen.update(_jh_kf)
        for _jh_ke in _jh_gks.ids:
            _jh_kst = str(getattr(hass.states.get(_jh_ke), "state", "unavailable"))
            if _jh_ke not in _jh_kto and _jh_kst not in ("locked", "locking"):
                _jh_kopen[_jh_ke] = _jh_kst
        if _jh_gks.error:
            _jh_kopen["lock.*"] = "unknown"   # fails closed: the locks could not be read, so nothing is claimed
        _jh_knote = (" " + _jh_t("jh_gn_locks_open", names=", ".join(
            "%s (%s)" % (_jh_name_of(hass.states.get(e)) if hass.states.get(e) is not None else e, s)
            for e, s in _jh_kopen.items()))) if _jh_kopen else ""
        return LocalResult(text=jh_group_text(hass, local_i18n.t(
            "goodnight_summary", lang, h=honorific, n=off_count,
            noun=local_i18n.noun("light", off_count, lang)), _jh_gn_conf, _jh_gn_unc, honorific, _jh_gn_fail)
            + jh_group_note(hass, _jh_gl) + _jh_knote + jh_group_note(hass, _jh_gks),
            success=(bool(_jh_gn_conf) or not (_jh_gn_unc or _jh_gn_fail)) and not _jh_kopen)

    # Single-entity patterns
    _last_failed_name = None  # Track for end-of-loop error
    _jh_wrong_type = None   # Jarvis Home patch (2026-11-07, round 2): a device of exactly that name that cannot do it
    _jh_noroom = None   # jh-r17-room (step 159): (room, domain) when the order named a real room that has no such device
    learned_aliases = await hass.async_add_executor_job(
        _load_learned_aliases, hass
    )
    for pattern, action, domain_hint in _INTENT_PATTERNS:
        if action == "scene":
            continue
        if action in ("query_time", "query_date", "greeting", "thanks", "status"):   # jh-no-act-on-questions: a short sentence only
            match = None if (_JH_MULTI.get() or len(normalized.split()) > 8) else re.search(pattern, normalized)
        elif action.startswith("query_"):
            match = re.search(pattern, normalized)
        else:
            match = re.fullmatch(pattern, _jh_command_text(normalized))   # the whole order, not a part of a sentence
        if not match:
            continue
        _LOGGER.info("Local intent: action=%s", action)
        if action in ("query_time", "query_date", "greeting", "thanks", "status"):
            return LocalResult(text=_query_resp(hass, action, "", "", honorific, lang), success=True)
        groups = match.groups()
        # Value/area actions: the capture is a number and/or an optional area, not
        # an entity name. Resolve the target from the captured area (if any) or a
        # sole/active default, so "pause the music", "set volume to 50" and "set
        # the temperature to 72" work without a named device instead of searching
        # for the literal number / skipping on a null name.
        if action in _MEDIA_ACTIONS or action == "set_temp":
            if action == "set_temp":
                num, area = (groups[0] if groups else None), None
            elif action == "volume_set":
                num = groups[0] if groups else None
                area = groups[1] if len(groups) > 1 else None
            else:   # media_pause/play/next, volume_up/down, mute/unmute — area only
                num, area = None, (groups[0] if groups else None)
            tgt_domain = "climate" if action == "set_temp" else "media_player"
            target = None
            if area:
                _jh_marea = _jh_room_named(hass, area, (tgt_domain,), tgt_domain, kinds=())   # jh-r16-room: the shared room rule
                found = _find_entities_in_area(hass, _jh_marea, tgt_domain, True) if _jh_marea else []
                if len(found) > 1 and tgt_domain == "media_player":   # jh-r23-dup: the one playing there, never the first
                    found = [x for x in found if getattr(hass.states.get(x[0]), "state", "") in ("playing", "paused", "buffering")] or found
                target = found[0][0] if len(found) == 1 else None   # jh-r23-res (step 165, review round 23 #2)
                if len(found) > 1:
                    return _jh_ask_which(hass, pending_key, action, found, honorific, remember=num is None)
                if not target and not _jh_marea:
                    # jh-r18-media (step 160, review round 18 #2): no room by that name, but the words name the device itself
                    # ("volume up on the tv"): that one device, never a guess between two
                    target = _jh_device_named(hass, area, tgt_domain)
                if not target:
                    # jh-r17-room (step 159, review round 17 #2): the order named a room, so a device in another room is never
                    # the answer (no default target). An unknown room, or a room with no such device: the AI answers online, and
                    # offline the reply says so honestly. No later pattern may act on these words.
                    _last_failed_name = area
                    _jh_noroom = (_jh_marea, tgt_domain) if _jh_marea else None
                    break
            if not target:   # no room named: the one active / only device, as before
                target = _default_target(hass, tgt_domain)
            if not target:
                # ambiguous (no area match, no sole/active default) → fall through
                # to the LLM online; honest failure named below in offline mode.
                _last_failed_name = area or f"a {tgt_domain.replace('_', ' ')}"
                continue
            vargs = {}
            if num is not None:
                try:
                    vargs["temperature" if action == "set_temp" else "volume_level"] = int(num)
                except (ValueError, TypeError):
                    continue
            if _needs_confirmation(hass, action, target):
                return None
            # jh-r25-media (step 167, review round 25 #2): truth first. An unavailable device is said so (Home Assistant skips
            # it silently); pausing or skipping on a player that is not playing says nothing is playing; volume or mute on a
            # player that is off says it is off; a thermostat must show the temperature it was set to (about 4 s), else the
            # reply is "not confirmed" with what it shows
            _jh_mst = hass.states.get(target)
            _jh_mname = str(getattr(_jh_mst, "attributes", {}).get("friendly_name") or target)
            _jh_mstate = str(getattr(_jh_mst, "state", "unavailable")).lower()
            if _jh_mstate in ("unavailable", "unknown"):
                return LocalResult(text=_jh_t("jh_unavailable_nochange", name=_jh_mname, h=honorific), success=False)
            # jh-r26-media (step 168, review round 26 #9): many TVs (LG webOS, Samsung, Android TV) show only 'on' while something
            # plays. "Nothing is playing" only when the player is idle, off or standby AND shows no media; any other state is
            # tried and read back (a pause must show paused / idle / off / standby, a next track another title or content id),
            # else the reply is "sent, but not confirmed"
            _jh_mattrs = getattr(_jh_mst, "attributes", None) or {}
            _jh_mmedia = any(_jh_mattrs.get(k) for k in ("media_content_id", "media_title"))
            if action in ("media_pause", "media_next") and _jh_mstate in ("idle", "off", "standby") and not _jh_mmedia:
                return LocalResult(text=_jh_t("jh_media_nothing", name=_jh_mname, h=honorific), success=False)
            _jh_msure = _jh_mstate in {"media_pause": ("playing", "buffering"),
                                       "media_next": ("playing", "paused", "buffering")}.get(action, ())
            _jh_mbefore = (_jh_mattrs.get("media_content_id"), _jh_mattrs.get("media_title"))
            if action in ("volume_up", "volume_down", "volume_set", "mute", "unmute") and _jh_mstate in ("off", "standby"):
                return LocalResult(text=_jh_t("jh_media_off", name=_jh_mname, h=honorific), success=False)
            success = await _execute_action(hass, action, target, vargs)
            success = await _jh_read_back(hass, target, action, vargs, success, _jh_msure, _jh_mbefore)   # jh-r26-media
            st = hass.states.get(target)
            fname = st.attributes.get("friendly_name", target) if st else target
            _update_ctx(entity=target, domain=target.split(".")[0], action=action)
            return jh_single_result(action, fname, success, vargs, honorific, lang)   # jh-r12-act
        name_frag = groups[0] if groups else ""
        extra_arg = groups[1] if len(groups) > 1 else None
        # jh-r15-room (review round 15): ONE room path for every word order ("turn on the X lights", "turn the X lights on",
        # "switch the X lamps off", "brighten the X lights"). As in jh-r13-room / jh-r14-room: only when the words as said name no
        # device ("the bed lights" is the Bed Lights strip), and only for the one area whose whole name is the words ("room" is
        # no room); the reply names the real area
        _jh_room = None
        if (action in ("turn_on", "turn_off", "dim", "brighten") and name_frag
                and re.search(r"\b(?:lights|lamps)\b", _jh_command_text(normalized))):
            # jh-r16-room: the shared room rule (one function for the room order, the bulk order and the media order)
            _jh_area0 = _jh_room_named(hass, name_frag, _jh_action_domains(action), domain_hint, (normalized[match.start(1):],))
            # jh-r25-group (step 167): the room's lights through THE group function (every light of the room, the kind, the
            # hidden-twin rule, no manual heater); what it left out is named
            _jh_g0 = (jh_group_targets(hass, "light", "turn_off" if action == "turn_off" else "turn_on", None, _jh_area0, "")
                      if _jh_area0 else None)
            _jh_room = ((_jh_area0, _jh_g0.targets) if _jh_g0 is not None and not _jh_g0.error
                        and (_jh_g0.targets or _jh_g0.left_out) else None)
        if _jh_room:   # jh-no-act-on-questions: "the bedroom lamps" are all the lights in that room
            if not _jh_room[1]:
                return LocalResult(text=jh_group_left_all(_jh_g0, honorific), success=False)
            _jh_args = {"brightness_pct": int(extra_arg)} if action == "dim" and extra_arg and str(extra_arg).isdigit() else {}
            _jh_todo, _jh_same, _jh_gone = _jh_split_change(hass, _jh_room[1], action)   # review round 2: no fake Done
            if not _jh_todo:
                return LocalResult(text=_jh_t("jh_skip_none", h=honorific, why=_jh_skip_why(_jh_same, _jh_gone, action)), success=False)
            _jh_conf, _jh_unc, _jh_fail = await jh_group_act(hass, _jh_g0, action, [e for e, _n in _jh_todo], _jh_args)   # jh-r12-act, r13
            _jh_ok = len(_jh_conf)
            return LocalResult(text=jh_group_text(hass, _jh_t("jh_room_done", h=honorific, n=_jh_ok, total=len(_jh_todo), room=_jh_room[0],
                                          what=_jh_t("jh_room_" + action, pct=_jh_args.get("brightness_pct", "")))
                               + ((" (" + _jh_skip_why(_jh_same, _jh_gone, action) + ")") if (_jh_same or _jh_gone) else ""),
                               _jh_conf, _jh_unc, honorific, _jh_fail) + jh_group_note(hass, _jh_g0),
                               success=_jh_ok > 0)
        if not name_frag:
            continue
        if name_frag.strip() in _JH_PRONOUNS:
            _last_failed_name = name_frag
            continue
        _jh_said = normalized[match.start(1):]
        _jh_doms = _jh_action_domains(action)
        # jh-r23-single (step 165, review round 23 #1 and #2): orders that carry no value can be finished by the which-one answer
        _jh_novalue = action in ("turn_on", "turn_off", "lock", "unlock", "open", "close", "brighten", "toggle")
        _jh_pin, _jh_many23 = None, False
        if not action.startswith("query_"):   # jh-r23-dup: a name more than one live device has is a question, never the first
            _jh_dup = _jh_same_name(hass, _jh_said, name_frag, domains=_jh_doms)
            if len(_jh_dup) > 1:
                _jh_pin, _jh_dall = _jh_one_of(hass, _jh_dup)
                if not _jh_pin:
                    if any(_needs_confirmation(hass, action, x[0]) for x in _jh_dall):
                        return None   # protected device: the agent runs the confirmation gate
                    return _jh_ask_which(hass, pending_key, action, _jh_dall, honorific, remember=_jh_novalue)
        _jh_named = _jh_pin or _jh_whole(hass, name_frag, _jh_doms, domain_hint) or _jh_whole(hass, _jh_said, _jh_doms, domain_hint)
        resolved = _jh_named or _find_entity(
            hass, name_frag, domain_hint, learned_aliases
        ) or _find_entity(hass, name_frag, None, learned_aliases)
        if resolved and not _jh_named and (_jh_wrong_kind(name_frag, resolved) or _jh_wrong_kind(_jh_said, resolved)):
            resolved = None   # the fan was asked for, so the lamp of that room is no answer
        # Jarvis Home patch (2026-11-14): "the heater" as a generic word that matched no real heater means the real heaters
        _jh_heat = _jh_generic_heater(hass, name_frag, resolved) if action in ("query_state", "turn_on", "turn_off") and not _jh_named else []
        if _jh_heat and action == "query_state":
            return LocalResult(text=_jh_heaters_answer(hass, _jh_heat, honorific), success=True)
        if _jh_heat:
            _jh_hman = [x for x in _jh_heat if _jh_is_manual(hass, x[0])]   # jh-manual-heater: never switched
            _jh_hlive = [x for x in _jh_heat if _jh_heat_state(hass.states.get(x[0])) != "unavailable" and x not in _jh_hman]
            if _JH_REQ_AREA.get() and len(_jh_hlive) > 1:   # jh-r23-single: the router path: the one in this room, else nothing
                _jh_h1 = _jh_one_of(hass, _jh_hlive)[0]
                if not _jh_h1:
                    return _jh_ask_which(hass, pending_key, action, _jh_hlive, honorific)
                _jh_hlive = [_jh_h1]
            if not _jh_hlive and _jh_hman:
                return LocalResult(text=" ".join(_jh_manual_said(hass, x[0], action, honorific, lang) for x in _jh_hman), success=False)
            if not _jh_hlive:
                return LocalResult(text=_jh_heaters_answer(hass, _jh_heat, honorific) + " " + _jh_t("jh_nothing_changed"), success=False)
            _jh_hfit = [x for x in _jh_hlive if (_jh_heat_state(hass.states.get(x[0])) == "on") == (action == "turn_off")]
            if not _jh_hfit:
                _jh_who = "They are all" if len(_jh_hlive) > 1 else f"{_jh_hlive[0][1]} is"
                return LocalResult(text=_jh_t("jh_already_" + ("many" if len(_jh_hlive) > 1 else "one") + ("_off" if action == "turn_off" else "_on"), name=_jh_hlive[0][1], h=honorific), success=True)
            if len(_jh_hfit) > 1:
                if any(_needs_confirmation(hass, action, x[0]) for x in _jh_hfit):
                    return None   # protected device: the agent runs the confirmation gate
                _JH_PENDING[pending_key] = {"action": action, "options": _jh_hfit, "at": time.time()}
                _jh_n = [x[1] for x in _jh_hfit]
                return LocalResult(text=_jh_t("jh_which", h=honorific, opts=", ".join(_jh_n[:-1]), last=_jh_n[-1]), success=False)
            resolved = _jh_named = _jh_hfit[0]
        _jh_ears = None if _jh_named else _jh_sound_many(hass, name_frag, resolved, list(_JH_SOUND_CONTROL) if action in ("turn_on", "turn_off") or not domain_hint else [domain_hint])
        if _jh_ears:
            if action not in ("turn_on", "turn_off", "lock", "unlock", "open", "close") or any(_needs_confirmation(hass, action, x[0]) for x in _jh_ears):
                return None   # a value to carry or a protected device: the agent asks
            _jh_gone = [x[1] for x in _jh_ears if getattr(hass.states.get(x[0]), "state", "unavailable") == "unavailable"]
            if _jh_gone:
                # Jarvis Home patch (2026-11-07): an unavailable device is never offered, so an answer cannot act on it
                # Jarvis Home patch (2026-11-07, round 2): by entity id, a live device may share its name with one that is gone
                _jh_ears = [x for x in _jh_ears if getattr(hass.states.get(x[0]), "state", "unavailable") != "unavailable"]
                _jh_v = "is" if len(_jh_gone) == 1 else "are"
                if not _jh_ears:
                    return LocalResult(text=_jh_t("jh_unavail_" + ("one" if len(_jh_gone) == 1 else "many"), names=", ".join(_jh_gone), h=honorific) + " " + _jh_t("jh_nothing_changed"), success=False)
                if len(_jh_ears) == 1:
                    _JH_PENDING[pending_key] = {"action": action, "options": _jh_ears, "at": time.time()}
                    return LocalResult(text=_jh_t("jh_unavail_" + ("one" if len(_jh_gone) == 1 else "many"), names=", ".join(_jh_gone), h=honorific) + " " + _jh_t("jh_do_you_mean", name=_jh_ears[0][1]), success=False)
            _JH_PENDING[pending_key] = {"action": action, "options": _jh_ears, "at": time.time()}
            _jh_n = [x[1] for x in _jh_ears]
            return LocalResult(text=_jh_t("jh_which", h=honorific, opts=", ".join(_jh_n[:-1]), last=_jh_n[-1]), success=False)
        if not resolved:
            # Track failure but keep trying other patterns/domains
            _last_failed_name = name_frag
            _LOGGER.info(
                "Local: pattern matched '%s' but entity '%s' not found "
                "(domain=%s), trying next pattern...",
                action, name_frag, domain_hint,
            )
            continue
        if action in ("turn_on", "turn_off") and not _jh_named:
            _jh_many = _jh_vague(hass, name_frag, normalized)
            _jh_many23 = bool(_jh_many)   # jh-r23-single: the vague-word rule decided; the shared check below is not asked again
            if _jh_many:
                def _jh_st(e):
                    st = hass.states.get(e)
                    return st.state if st else "unavailable"
                _jh_live = [x for x in _jh_many if _jh_st(x[0]) not in ("unavailable", "unknown")]
                _jh_dead = [x[1] for x in _jh_many if _jh_st(x[0]) in ("unavailable", "unknown")]
                if _JH_REQ_AREA.get() and len(_jh_live) > 1:   # jh-r23-single: the router path: the one in this room, else nothing
                    _jh_r1 = _jh_one_of(hass, _jh_live)[0]
                    if not _jh_r1:
                        return _jh_ask_which(hass, pending_key, action, _jh_live, honorific)
                    _jh_live, _jh_many, _jh_dead = [_jh_r1], [_jh_r1], []
                _jh_verb = "is" if len(_jh_dead) == 1 else "are"
                if not _jh_live:
                    return LocalResult(text=_jh_t("jh_unavail_" + ("one" if len(_jh_dead) == 1 else "many"), names=", ".join(_jh_dead), h=honorific) + " " + _jh_t("jh_nothing_changed"), success=False)
                _jh_fit = [x for x in _jh_live if (_jh_st(x[0]) != "off") == (action == "turn_off")]
                if not _jh_fit:
                    _jh_who = "They are all" if len(_jh_live) > 1 else f"{_jh_live[0][1]} is"
                    _jh_tail = f" {', '.join(_jh_dead)} {_jh_verb} unavailable." if _jh_dead else ""
                    return LocalResult(text=_jh_t("jh_already_" + ("many" if len(_jh_live) > 1 else "one") + ("_off" if action == "turn_off" else "_on"), name=_jh_live[0][1], h=honorific) + ((" " + _jh_t("jh_unavail_tail_" + ("one" if len(_jh_dead) == 1 else "many"), names=", ".join(_jh_dead))) if _jh_dead else ""), success=True)
                if len(_jh_fit) == 1 and action == "turn_on" and any(
                        x[1] in _jh_dead and not _jh_same_kind(hass, x[0], _jh_fit[0][0]) for x in _jh_many):
                    # the person may well mean the lamp that is gone, and switching on another kind of device is no safe guess
                    _JH_PENDING[pending_key] = {"action": action, "options": _jh_fit, "at": time.time()}
                    return LocalResult(text=_jh_t("jh_unavail_" + ("one" if len(_jh_dead) == 1 else "many"), names=", ".join(_jh_dead), h=honorific) + " " + _jh_t("jh_do_you_mean", name=_jh_fit[0][1]), success=False)
                # Jarvis Home patch (2026-11-07, round 2): by kind, not by domain. Two lights or two heaters act at once.
                if len(_jh_fit) == 1 and action == "turn_on" and _jh_fit[0][0] != resolved[0] and not _jh_same_kind(hass, _jh_fit[0][0], resolved[0]):
                    # Jarvis Home patch (2026-11-07): the name pointed at one kind of device and the only one that is off is another
                    # kind, a heater for a lamp. Switching that on is no safe guess, so ask first.
                    _jh_on = [x[1] for x in _jh_live if x not in _jh_fit]
                    _jh_lead = (_jh_t("jh_lead_on_" + ("one" if len(_jh_on) == 1 else "many"), names=", ".join(_jh_on), h=honorific) + " ") if _jh_on else ""
                    _JH_PENDING[pending_key] = {"action": action, "options": _jh_fit, "at": time.time()}
                    return LocalResult(text=_jh_lead + _jh_t("jh_do_you_mean", name=_jh_fit[0][1]), success=False)
                if len(_jh_fit) == 1:
                    resolved = _jh_fit[0]
                elif any(_needs_confirmation(hass, action, x[0]) for x in _jh_fit):
                    return None   # protected device: the agent runs the confirmation gate
                else:   # jh-r23-single: the shared which-one question (rooms named for the same name)
                    return _jh_ask_which(hass, pending_key, action, _jh_fit, honorific)
        if not _jh_named and not _jh_many23 and not action.startswith("query_"):
            # jh-r23-single (step 165, review round 23 #1): a word that is no device's whole name and fits more than one device
            # ("unlock the door" with a front and a back door, "open the blind" with two blinds) is a question, never the best score
            _jh_cb = _jh_could_be(hass, name_frag, _jh_said, resolved, action)
            if _jh_cb:
                _jh_c1, _jh_call = _jh_one_of(hass, _jh_cb, kind=_jh_class_of(name_frag))   # jh-r24-kindbulk: of the kind named
                if not _jh_call:
                    _last_failed_name = name_frag
                    continue
                if not _jh_c1:
                    if any(_needs_confirmation(hass, action, x[0]) for x in _jh_call if "." in str(x[0])):
                        return None   # protected device: the agent runs the confirmation gate
                    return _jh_ask_which(hass, pending_key, action, _jh_call, honorific, remember=_jh_novalue)
                resolved = _jh_c1
        if not _jh_class_fits(hass, name_frag, resolved):
            # jh-r24-kindbulk (step 166, review round 24 #1, #2): the device found is not of the KIND named ("the blind" is never
            # the skylight, "the light" never a plain switch, "the fan" never a heater): nothing is switched; the next pattern,
            # then the AI or the honest "couldn't find"
            _last_failed_name = name_frag
            continue
        entity_id, fname = resolved
        _jh_svc = _service_for(action, entity_id)
        if _jh_svc and _jh_svc[0] != entity_id.split(".")[0]:
            # Jarvis Home patch (2026-11-07): a lock or cover service on a lamp does nothing and Home Assistant does not
            # say so, so it would be reported done. Not this device: try the next pattern, then the agent.
            _last_failed_name = name_frag
            if str(fname).lower().replace("colour", "color") in (_jh_said_name(name_frag), _jh_said_name(_jh_said)):
                _jh_wrong_type = (fname, entity_id.split(".")[0], action)
            continue
        _jh_now = hass.states.get(entity_id)
        if _jh_now is not None and _jh_now.state == "unavailable" and not action.startswith("query_"):
            # Jarvis Home patch (2026-11-06): a command to a device that is not there does nothing, so never report it done.
            return LocalResult(text=_jh_t("jh_unavailable_nochange", name=fname, h=honorific), success=False)
        args = {}
        if action == "dim" and extra_arg:
            args["brightness_pct"] = int(extra_arg)
        elif action in ("set_temp", "set_temp_named") and extra_arg:
            args["temperature"] = int(extra_arg)
        elif action in ("set_temp", "set_temp_named") and groups:
            try: args["temperature"] = int(groups[0])
            except (ValueError, IndexError): pass
        elif action == "volume_set" and groups:
            try: args["volume_level"] = int(groups[0])
            except (ValueError, IndexError): pass
        if (action == "query_state" or not action.startswith("query_")) and _jh_is_manual(hass, entity_id):   # jh-manual-heater; review round 4: open, close, dim, set...
            return LocalResult(text=_jh_manual_said(hass, entity_id, action, honorific, lang), success=action == "query_state")
        if action.startswith("query_") and _jh_local_hidden(hass, entity_id):
            # jh-r28-private (step 171, review round 28 #4): another person's phone / app sensor or wearable: the where-is reply
            return LocalResult(text=_jh_private_reply(hass), success=False)
        if action.startswith("query_"):
            resp = _query_resp(hass, action, entity_id, fname, honorific, lang)
            if resp:
                return LocalResult(text=resp, success=True)
            continue
        if action in ("turn_on", "turn_off") and entity_id.split(".")[0] in ("light", "switch", "fan", "climate", "input_boolean", "water_heater", "humidifier"):
            _jh_was = hass.states.get(entity_id)   # jh-live-answers: never "done" when nothing would change
            if _jh_was is not None and _jh_was.state not in ("unavailable", "unknown") and (_jh_was.state != "off") == (action == "turn_on"):
                return LocalResult(text=_jh_t("jh_already_one_" + ("on" if action == "turn_on" else "off"), name=fname, h=honorific), success=True)
        if _needs_confirmation(hass, action, entity_id):
            _LOGGER.info("Local: '%s' on %s needs confirmation — deferring to agent",
                         action, entity_id)
            return None   # protected action → the agent runs the confirmation gate
        success = await _execute_action(hass, action, entity_id, args)
        _update_ctx(entity=entity_id, domain=entity_id.split(".")[0], action=action)
        return jh_single_result(action, fname, success, args, honorific, lang)   # jh-r11-confirm, jh-r12-act

    # v5.7.08: If patterns matched but entity resolution failed, ALWAYS
    # fall through to the agentic LLM. The agent has search_entities which
    # uses fuzzy matching and learned aliases — much better at finding
    # devices than the local regex resolver. Never return a local error
    # for device commands.
    # v5.9.06: In force/offline mode there's no LLM to escalate to, so return
    # an honest local error naming the device we couldn't resolve.
    if force and _jh_wrong_type:
        return LocalResult(text=_jh_wrong_type_line(*_jh_wrong_type, honorific), success=False)
    if force and _jh_noroom:   # jh-r17-room: offline, the named room has no such device - nothing was changed
        return LocalResult(text=_jh_t("jh_no_" + ("climate" if _jh_noroom[1] == "climate" else "media") + "_in_room",
                                      room=_jh_noroom[0], h=honorific), success=False)
    if _last_failed_name:
        if force:
            return LocalResult(
                text=local_i18n.t("offline_notfound", lang,
                                  name=_last_failed_name, h=honorific),
                success=False,
            )
        _LOGGER.info(
            "Local: entity '%s' not found — escalating to agentic LLM "
            "with search_entities",
            _last_failed_name,
        )
        return None  # Fall through to agent

    # ── Appliance monitor queries ───────────────────────────────────
    appliance_match = re.search(
        r"(?:let\s+me\s+know|tell\s+me|notify\s+me|alert\s+me)"
        r".*(?:when|if).*(?:laundry|wash|dryer|dry|dishwasher|dishes)"
        r".*(?:done|finish|complete|ready)",
        normalized,
    )
    if appliance_match:
        try:
            from . import appliance_monitor
            if appliance_monitor.is_running():
                st = appliance_monitor.status()
                tracking = [
                    f"{s['friendly_name']} ({s['phase']})"
                    for s in st.get("sensors", {}).values()
                ]
                if tracking:
                    return LocalResult(
                        text=local_i18n.t("appliance_tracking", lang,
                                          h=honorific, items=", ".join(tracking)),
                        success=True,
                    )
                return LocalResult(
                    text=local_i18n.t("appliance_nosensors", lang, h=honorific),
                    success=True,
                )
            else:
                return LocalResult(
                    text=local_i18n.t("appliance_notrunning", lang, h=honorific),
                    success=True,
                )
        except Exception:
            return LocalResult(
                text=local_i18n.t("appliance_generic", lang, h=honorific),
                success=True,
            )

    # Follow-up ("also turn off the kitchen")
    if _ctx_fresh() and not _JH_QUERY_ONLY.get() and re.search(r"^(?:also|and|now)\s+", normalized):   # review round 7: "...?" stays a question
        stripped = re.sub(r"^(?:also|and|now)\s+", "", normalized)
        result = await try_local(hass, stripped, honorific, force=force, lang=lang, pending_key=pending_key)
        if result and result.handled:
            return result

    # Low complexity bare entity name → state query.
    # Guard: only attempt this for SHORT, non-interrogative phrases. A bare
    # entity name is something like "kitchen lights" or "front door" — not a
    # question or a sentence about JARVIS itself. Without this guard, phrases
    # like "what are your capabilities" get fed wholesale to the resolver,
    # which wastes a full registry scan and (previously) logged noise.
    if complexity < 40 and _looks_like_entity_name(normalized):
        resolved = _find_entity(hass, normalized, None, learned_aliases)
        if resolved:
            entity_id, fname = resolved
            if _jh_local_hidden(hass, entity_id):   # jh-r28-private (step 171): a bare name of another person's sensor
                return LocalResult(text=_jh_private_reply(hass), success=False)
            state = hass.states.get(entity_id)
            if state:
                return LocalResult(text=local_i18n.t(
                    "query_state", lang, name=fname,
                    state=local_i18n.state_word(state.state, lang),
                    h=honorific), success=True)

    _LOGGER.debug("Local: no match for '%s' (complexity=%d) — LLM", text[:60], complexity)
    return None


def _looks_like_entity_name(text: str) -> bool:
    """
    Heuristic: is this short phrase plausibly a bare device/entity name
    (vs. a question or a sentence)? Used to gate the speculative state-query
    lookup so conversational input never hits the entity resolver.
    """
    t = text.strip().lower()
    if not t:
        return False
    # Questions are never bare entity names.
    if t.endswith("?"):
        return False
    words = t.split()
    # Too long to be a device name.
    if len(words) > 4:
        return False
    # Interrogatives / conversational openers.
    _QUESTION_STARTS = (
        "what", "who", "when", "where", "why", "how", "is", "are", "can",
        "could", "would", "should", "do", "does", "did", "will", "tell",
        "explain", "describe", "give", "show me how", "help",
    )
    if words[0] in _QUESTION_STARTS:
        return False
    # A leading action verb (any supported language) makes this a COMMAND, not a
    # bare device name — so an unrecognised command (e.g. a German verb this
    # engine doesn't pattern-match) escalates to the LLM instead of being misread
    # as a state query on the device embedded in it (issue #511).
    _COMMAND_VERBS = (
        # German imperatives
        "schalte", "schalt", "mach", "mache", "dimme", "dimm",
        "öffne", "offne", "schließe", "schliesse", "schliess",
        "verriegele", "verriegle", "entriegele", "entriegle",
        "starte", "aktiviere", "spiele", "pausiere",
        # English (these normally match a command pattern already; defensive)
        "turn", "switch", "dim", "brighten", "open", "close", "lock",
        "unlock", "set", "play", "pause", "activate", "run", "mute", "unmute",
    )
    if words[0] in _COMMAND_VERBS:
        return False
    # Phrases about JARVIS itself ("your X", "you Y") aren't device names.
    if "your" in words or "you" in words:
        return False
    return True
