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

_BULK_PATTERNS = [
    (r"turn\s+off\s+(?:all\s+)?(?:the\s+)?lights?$", "turn_off", "light", None),
    (r"turn\s+on\s+(?:all\s+)?(?:the\s+)?lights?$",  "turn_on",  "light", None),
    (r"turn\s+off\s+(?:all\s+)?(?:the\s+)?lights?\s+(?:in|on)\s+(?:the\s+)?(.+)$",
     "turn_off", "light", "area"),
    (r"turn\s+on\s+(?:all\s+)?(?:the\s+)?lights?\s+(?:in|on)\s+(?:the\s+)?(.+)$",
     "turn_on", "light", "area"),
    (r"unlock\s+(?:all\s+)?(?:the\s+)?doors?$", "unlock", "lock", None),
    (r"(?<!un)lock\s+(?:all\s+)?(?:the\s+)?doors?$",   "lock",   "lock", None),
    (r"close\s+(?:all\s+)?(?:the\s+)?(?:covers?|blinds?|shades?)$", "close", "cover", None),
    (r"open\s+(?:all\s+)?(?:the\s+)?(?:covers?|blinds?|shades?)$",  "open",  "cover", None),
    (r"turn\s+off\s+(?:all\s+)?(?:the\s+)?fans?$", "turn_off", "fan", None),
    (r"turn\s+on\s+(?:all\s+)?(?:the\s+)?fans?$",  "turn_on",  "fan", None),
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
    (r"(?:are\s+)?(?:any|which)\s+(?:lights?)\s+on",     "lights_on"),
    (r"(?:how\s+many)\s+lights?\s+(?:are\s+)?on",        "lights_on"),
    # Jarvis Home patch (2026-11-09): a question about a period goes to JARVIS (energy_usage), not to the power-now answer
    (r"(?:what(?:'s| is)\s+(?:the\s+)?)?(?:energy|power)\s+(?:usage|consumption)(?!.*\b(?:today|yesterday|week|month|year|far|now|since|last|this|used)\b)", "energy"),
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
    if a in b:
        contain = len(a) / len(b) * 80
    elif b in a:
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
    if not frag or (resolved and frag in str(resolved[1]).lower().replace("colour", "color")):
        return []
    hits = _jh_sound_hits(hass, frag, domains or _JH_SOUND_DOMAINS)
    return hits if len(hits) > 1 else []


def _jh_heaters(hass):
    """Jarvis Home patch (2026-11-06): heater switches and climate devices, read live. A switch counts when
    it has the Home Assistant label Heater or Heating on the entity or its device, or heater or radiator in
    its id or name. Setting entities such as a child lock or display never count."""
    def labelled(eid):
        try:
            from homeassistant.helpers import device_registry as dr, entity_registry as er
            entry = er.async_get(hass).async_get(eid)
            if entry is None:
                return False
            found = set(entry.labels or ())
            if entry.device_id:
                device = dr.async_get(hass).async_get(entry.device_id)
                found |= set(getattr(device, "labels", None) or ())
            return bool({x.lower() for x in found} & {"heater", "heating"})
        except Exception:
            return False
    switches = []
    for s in hass.states.async_all("switch"):
        words = f"{s.entity_id} {s.attributes.get('friendly_name') or ''}".lower()
        if not _jh_is_setting(hass, s.entity_id) and ("heater" in words or "radiator" in words or labelled(s.entity_id)):
            switches.append(s)
    return switches, list(hass.states.async_all("climate"))


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

    for domain in domains:
        for state in hass.states.async_all(domain):
            eid = state.entity_id
            if _jh_is_setting(hass, eid):
                continue
            fname = (state.attributes.get("friendly_name") or "").lower().replace("colour", "color")
            fname_words = set(fname.split())

            # Tier 1: exact
            if fname == fragment:
                _LOGGER.info("Entity resolve: exact '%s' → %s", fragment, eid)
                return (eid, state.attributes.get("friendly_name", eid))

            # Tier 2: substring
            if fragment in fname:
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

            # Tier 4: fuzzy similarity
            fuzz = _fuzzy_score(fragment, fname)
            if fuzz > 85 and fuzz > best_score:
                best_score = fuzz
                best_match = (eid, state.attributes.get("friendly_name", eid))
                continue

            # Tier 5: entity_id substring
            frag_u = fragment.replace(" ", "_")
            if frag_u in eid:
                score = len(frag_u) / max(len(eid), 1) * 80
                if score > best_score:
                    best_score = score
                    best_match = (eid, state.attributes.get("friendly_name", eid))

    # ── Tier 5b: STT correction retry ──────────────────────────────
    if (not best_match or best_score < 50):
        corrected = fragment
        for wrong, right in _STT_CORRECTIONS.items():
            if wrong in corrected:
                corrected = corrected.replace(wrong, right)
        if corrected != fragment:
            _LOGGER.info("Entity resolve: STT correction '%s' → '%s'", fragment, corrected)
            # Retry with corrected form (non-recursive, just one pass)
            for domain in domains:
                for state in hass.states.async_all(domain):
                    if _jh_is_setting(hass, state.entity_id):
                        continue
                    fname = (state.attributes.get("friendly_name") or "").lower()
                    if corrected in fname:
                        score = len(corrected) / max(len(fname), 1) * 90
                        if score > best_score:
                            best_score = score
                            best_match = (state.entity_id,
                                          state.attributes.get("friendly_name", state.entity_id))
                    fuzz = _fuzzy_score(corrected, fname)
                    if fuzz > 85 and fuzz > best_score:
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
            for area in area_reg.async_list_areas():
                if fragment in area.name.lower():
                    for entry in ent_reg.entities.values():
                        if entry.domain != domain_hint or _jh_is_setting(hass, entry.entity_id):
                            continue
                        in_area = entry.area_id == area.id
                        if not in_area and entry.device_id:
                            device = dev_reg.async_get(entry.device_id)
                            in_area = device and device.area_id == area.id
                        if in_area:
                            state = hass.states.get(entry.entity_id)
                            if state:
                                return (entry.entity_id,
                                        state.attributes.get("friendly_name", entry.entity_id))
        except Exception:
            pass

    if best_match and best_score > 25:
        _LOGGER.info("Entity resolve: '%s' → %s (score=%.0f)", fragment, best_match[0], best_score)
        return best_match

    _jh_heard = _jh_sound_hits(hass, fragment, domains)
    if len(_jh_heard) == 1:   # two sound-alikes are for the caller to ask about
        _LOGGER.info("Entity resolve: sounds like '%s' → %s", fragment, _jh_heard[0][0])
        return _jh_heard[0]

    # A failed resolution is normal, expected control flow: the phrase looked
    # vaguely command-like but matched no device, so the caller falls through
    # to the LLM (which has fuzzy search + aliases). DEBUG, not WARNING — this
    # is not an error condition and should not surface in the user's log.
    _LOGGER.debug("Entity resolve unmatched: '%s' (domain=%s, best=%.0f)", fragment, domain_hint, best_score)
    return None


def _find_entities_in_area(hass, area_name, domain):
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
            if area_name.lower() in area.name.lower():
                target = area
                break
        if not target:
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


def _jh_vague(hass, name_fragment, spoken=""):
    """Jarvis Home patch (2026-11-05): names of every device a vague word could mean, read live.
    "the bathroom" and "the bathroom light" reach this as the same fragment, so the spoken words decide (2026-11-06):
    only a name really said is exact, a bare room word is vague, and "light" or "lamp" leaves the other devices out."""
    frag = _jh_frag(name_fragment)
    if not frag:
        return []
    said = spoken.lower().replace("colour", "color")
    light_word = bool(re.search(rf"\b{re.escape(frag)}\s+(?:lights?|lamps?)\b", said))
    hits, exact = [], []
    for dom in ("light", "switch", "climate"):
        for st in hass.states.async_all(dom):
            nm = st.attributes.get("friendly_name") or ""
            low = nm.lower().replace("colour", "color")
            if _jh_is_setting(hass, st.entity_id):
                continue
            if light_word and dom != "light" and not re.search(r"\b(?:lights?|lamps?)\b", low):
                continue
            if low == frag and st.state not in ("unavailable", "unknown"):
                return []
            if frag in low and nm not in [h[1] for h in hits]:
                hits.append((st.entity_id, nm))
                if low != frag and _jh_frag(nm) == frag and re.search(rf"\b{re.escape(low)}\b", said):
                    exact.append(nm)
    if len(exact) == 1:
        return []
    return hits if len(hits) > 1 else []


# Jarvis Home patch (2026-11-06, round 3): a word like fan or door is cut from a name to find "the bedroom" in "the bedroom lamp",
# but it can be the very word that tells two real devices apart.
_JH_KIND_DOMAINS = {"fan": ("fan",), "fans": ("fan",), "lock": ("lock",), "locks": ("lock",), "door": ("lock", "cover"), "doors": ("lock", "cover"),
                    "cover": ("cover",), "covers": ("cover",), "thermostat": ("climate",)}


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
    if exact:
        return next((x for x in exact if x[0].split(".")[0] == prefer), exact[0])
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
                             "allume éteins eteins ouvre ferme baisse monte enciende apaga abre cierra accendi spegni apri chiudi").split())
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


def _jh_names_device(hass, body):
    """A kind of device, or the name of a real device or room, read live."""
    # Jarvis Home patch (2026-11-07): "an alarm for 7" is a wake-up time. A reminder or timer set for it is not a device command,
    # so device_command takes those words out first (round 2). An "alarm" that is left is the alarm system, in every home.
    if _JH_KIND_RE.search(body):
        return True
    names = []
    for dom in _JH_ACT_DOMAINS:
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
            if match.group(1) and _find_scene_or_script(hass, match.group(1)):
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


def _find_scene_or_script(hass, name_fragment):
    fragment = name_fragment.lower().strip()
    for suffix in ("scene", "script", "routine", "mode"):
        fragment = re.sub(rf"\s+{suffix}$", "", fragment)
    fragment = fragment.strip()
    for domain, dtype in [("scene", "scene"), ("script", "script")]:
        for state in hass.states.async_all(domain):
            fname = (state.attributes.get("friendly_name") or "").lower()
            if fname == fragment or fragment in fname:
                return (state.entity_id,
                        state.attributes.get("friendly_name", state.entity_id), dtype)
            if fragment.replace(" ", "_") in state.entity_id:
                return (state.entity_id,
                        state.attributes.get("friendly_name", state.entity_id), dtype)
    _jh_heard = _jh_sound_hits(hass, fragment, ("scene", "script"))
    if len(_jh_heard) == 1:
        return (*_jh_heard[0], _jh_heard[0][0].split(".")[0])
    return None


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


async def _execute_action(hass, action, entity_id, args):
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

def _resp(action, fname, success, args=None, h="sir"):
    if not success:
        # JARVIS reports failure calmly and precisely, no hand-wringing.
        return (f"I wasn't able to {action.replace('_', ' ')} {fname}, {h} — "
                f"there may be a connectivity issue.")

    # Understated lead-ins, MCU style. Varied so confirmations never sound
    # canned. Each is something JARVIS would actually say.
    ack = _pick(["Done", "Right away", "As you wish", "Consider it done", "At once"])
    sec = _pick(["Secured", "Done", "Locked up"])

    bp = (args or {}).get('brightness_pct', '?')
    temp = (args or {}).get('temperature', '?')
    vol = (args or {}).get('volume_level', '?')

    r = {
        "turn_on":        f"{ack}, {h}. {fname} is on.",
        "turn_off":       f"{ack}, {h}. {fname} is off.",
        "toggle":         f"{ack}, {h}. {fname} toggled.",
        "lock":           f"{sec}, {h}. {fname} is locked.",
        "unlock":         f"{ack}, {h}. {fname} is unlocked.",
        "open":           f"Opening {fname} now, {h}.",
        "close":          f"Closing {fname} now, {h}.",
        "dim":            f"{ack}, {h}. {fname} at {bp}%.",
        "brighten":       f"{ack}, {h}. {fname} at full brightness.",
        "set_temp":       f"{ack}, {h}. Temperature set to {temp}°.",
        "set_temp_named": f"{ack}, {h}. {fname} set to {temp}°.",
        "media_pause":    f"Paused, {h}.",
        "media_play":     f"Playing now, {h}.",
        "media_next":     f"Next track, {h}.",
        "volume_up":      f"Volume up, {h}.",
        "volume_down":    f"Volume down, {h}.",
        "volume_set":     f"Volume at {vol}%, {h}.",
        "mute":           f"Muted, {h}.",
        "unmute":         f"Unmuted, {h}.",
    }
    return r.get(action, f"{ack}, {h}. {action} applied to {fname}.")


def _query_resp(hass, action, entity_id, fname, h="sir"):
    if action == "query_time":
        from homeassistant.util import dt as dt_util
        return f"It's currently {dt_util.now().strftime('%I:%M %p')}, {h}."
    if action == "query_date":
        from homeassistant.util import dt as dt_util
        return f"Today is {dt_util.now().strftime('%A, %B %d, %Y')}, {h}."
    if action == "greeting":
        from homeassistant.util import dt as dt_util
        hour = dt_util.now().hour
        g = "Good morning" if hour < 12 else ("Good afternoon" if hour < 18 else "Good evening")
        tail = _pick([f"{g}, {h}.",
                      f"{g}, {h}. What can I do for you?",
                      f"{g}, {h}. At your disposal."])
        return tail
    if action == "thanks":
        return _pick([f"Of course, {h}.",
                      f"My pleasure, {h}.",
                      f"Anytime, {h}."])
    if action == "query_temp":
        state = hass.states.get(entity_id)
        if state:
            # Jarvis Home patch (2026-11-08): a thermostat or heater reports its room temperature in current_temperature, not in
            # its state ("off", "heat"), and a value that is no number is never read out as a temperature ("off°")
            value = state.attributes.get("current_temperature") if entity_id.split(".")[0] in ("climate", "water_heater") else state.state
            try:
                float(value)
            except (TypeError, ValueError):
                return f"{fname} does not report a temperature at the moment, {h}."
            unit = state.attributes.get("unit_of_measurement", "°")
            return f"The temperature in {fname} is currently {value}{unit}, {h}."
        return f"I'm unable to read the temperature for {fname} at the moment, {h}."
    if action == "query_state":
        state = hass.states.get(entity_id)
        if state:
            return f"{fname} is currently {state.state}, {h}."
        return f"I'm unable to determine the status of {fname} at the moment, {h}."
    if action == "status":
        return _home_status(hass, h)
    return ""


def _jh_offline(hass):
    """Jarvis Home patch (2026-11-06): door, window and lock devices with no usable state. An all-clear must
    not rest on them."""
    found = [s for s in hass.states.async_all("binary_sensor")
             if s.attributes.get("device_class") in ("door", "window", "garage_door")]
    found += list(hass.states.async_all("cover")) + list(hass.states.async_all("lock"))
    return [s.attributes.get("friendly_name", s.entity_id) for s in found
            if s.state in ("unavailable", "unknown", "jammed")]


def _home_status(hass, h="sir"):
    lights_on = sum(1 for s in hass.states.async_all("light") if s.state == "on")
    locks_ul = sum(1 for s in hass.states.async_all("lock") if s.state == "unlocked")
    doors_open = sum(1 for s in hass.states.async_all("binary_sensor")
                     if s.attributes.get("device_class") == "door" and s.state == "on")
    people = sum(1 for s in hass.states.async_all("person") if s.state == "home")
    parts = [f"Home status, {h}."]
    parts.append(f"{lights_on} light{'s' if lights_on != 1 else ''} on.")
    _jh_dark = _jh_offline(hass)
    _jh_locks = hass.states.async_all("lock")  # Jarvis Home patch: only report locks that exist
    if locks_ul:
        parts.append(f"{locks_ul} lock{'s' if locks_ul != 1 else ''} unlocked.")
    elif _jh_locks and all(s.state == "locked" for s in _jh_locks):
        parts.append("All locks secured.")
    if doors_open:
        parts.append(f"{doors_open} door{'s' if doors_open != 1 else ''} open.")
    if _jh_dark:
        parts.append(f"Not reporting: {', '.join(_jh_dark)}.")
    parts.append(f"{people} person{'s' if people != 1 else ''} home.")
    return " ".join(parts)


# ── Contextual queries ──────────────────────────────────────────────────────

def _ctx_query(hass, qtype, h="sir", area_match=""):
    if qtype == "who_home":
        ppl = [s.attributes.get("friendly_name", s.entity_id)
               for s in hass.states.async_all("person") if s.state == "home"]
        if not ppl:
            return f"No one appears to be home at the moment, {h}."
        if len(ppl) == 1:
            return f"{ppl[0]} is currently home, {h}."
        return f"{', '.join(ppl[:-1])} and {ppl[-1]} are currently home, {h}."

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
                items.append(f"{s.attributes.get('friendly_name', s.entity_id)} ({s.state})")
        for s in hass.states.async_all("lock"):
            if s.state == "unlocked":
                items.append(s.attributes.get("friendly_name", s.entity_id) + " (unlocked)")
            elif s.state not in ("locked", "unavailable", "unknown", "jammed"):
                items.append(f"{s.attributes.get('friendly_name', s.entity_id)} ({s.state})")
        if not items:
            if not (hass.states.async_all("lock") or hass.states.async_all("cover") or any(
                    s.attributes.get("device_class") in ("door", "window", "garage_door")
                    for s in hass.states.async_all("binary_sensor"))):
                return f"I have no door, window or lock sensors in this home, {h}."
            if _jh_offline(hass):
                return f"Nothing reports open, {h}, but these are not reporting: {', '.join(_jh_offline(hass))}."
            return f"Everything is closed and secured, {h}."
        _jh_tail = f" Not reporting: {', '.join(_jh_offline(hass))}." if _jh_offline(hass) else ""
        return f"Currently open or unlocked: {', '.join(items)}.{_jh_tail}"

    if qtype == "lights_on":
        on = [s.attributes.get("friendly_name", s.entity_id)
              for s in hass.states.async_all("light") if s.state == "on"]
        if not on:
            return f"All lights are off, {h}."
        c = len(on)
        listing = ", ".join(on[:5])
        return f"{c} light{'s' if c != 1 else ''} on: {listing}{'...' if c > 5 else ''}."

    if qtype == "energy":
        for s in hass.states.async_all("sensor"):
            if s.attributes.get("device_class") == "power" and "total" in s.entity_id.lower():
                unit = s.attributes.get("unit_of_measurement", "W")
                return f"Current power consumption is {s.state} {unit}, {h}."
        return None   # Jarvis Home patch: no whole-house power sensor - JARVIS answers (energy_status adds up the devices)

    if qtype == "weather":
        for s in hass.states.async_all("weather"):
            temp = s.attributes.get("temperature", "—")
            humidity = s.attributes.get("humidity", "—")
            condition = s.state.replace("_", " ")
            return f"Currently {condition} outside, {h}. Temperature is {temp}° with {humidity}% humidity."
        for s in hass.states.async_all("sensor"):
            if ("outdoor" in s.entity_id.lower() or "outside" in s.entity_id.lower()):
                if s.attributes.get("device_class") == "temperature":
                    unit = s.attributes.get("unit_of_measurement", "°")
                    return f"The outdoor temperature is {s.state}{unit}, {h}."
        return f"I don't have weather data available at the moment, {h}."

    if qtype == "area_devices" and area_match:
        try:
            from homeassistant.helpers import (
                area_registry as areg, entity_registry as er, device_registry as dr)
            area_reg = areg.async_get(hass)
            ent_reg = er.async_get(hass)
            dev_reg = dr.async_get(hass)
            target = None
            for area in area_reg.async_list_areas():
                if area_match.lower() in area.name.lower():
                    target = area
                    break
            if not target:
                return f"I couldn't find an area matching '{area_match}', {h}."
            devs = []
            for entry in ent_reg.entities.values():
                in_area = entry.area_id == target.id
                if not in_area and entry.device_id:
                    device = dev_reg.async_get(entry.device_id)
                    in_area = device and device.area_id == target.id
                if in_area:
                    state = hass.states.get(entry.entity_id)
                    if state:
                        devs.append(f"{state.attributes.get('friendly_name', entry.entity_id)} ({state.state})")
            if not devs:
                return f"No devices found in {target.name}, {h}."
            listing = ", ".join(devs[:10])
            more = f" and {len(devs) - 10} more" if len(devs) > 10 else ""
            return f"{target.name} has {len(devs)} device{'s' if len(devs) != 1 else ''}: {listing}{more}."
        except Exception:
            pass
    return None


# ── Main entry point ─────────────────────────────────────────────────────────

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
        return LocalResult(text=f"Which one, {honorific}: {', '.join(_jh_n[:-1])} or {_jh_n[-1]}?", success=False)
    entity_id, fname = found
    state = hass.states.get(entity_id)
    if state is None or state.state in ("unavailable", "unknown"):
        return LocalResult(text=f"{fname} is offline, {honorific}. Nothing was changed.", success=False)
    modes = set(state.attributes.get("supported_color_modes") or [])
    if not modes & {"hs", "rgb", "rgbw", "rgbww", "xy"}:
        return LocalResult(text=f"{fname} cannot show colours, {honorific}.", success=False)
    if _needs_confirmation(hass, "turn_on", entity_id):
        return None
    colour = m.group(2)
    if not await _seam_execute(hass, "light", "turn_on", {"entity_id": entity_id, "color_name": colour},
                               blocking=True, action="turn_on"):
        return LocalResult(text=f"I wasn't able to set {fname} to {colour}, {honorific}.", success=False)
    _update_ctx(entity=entity_id, domain="light", action="turn_on")
    import asyncio
    for _ in range(8):
        if _jh_shows(hass.states.get(entity_id), want):
            return LocalResult(text=f"Done, {honorific}. {fname} is {colour}.", success=True)
        await asyncio.sleep(0.25)
    return LocalResult(text=f"I sent {colour} to {fname}, {honorific}, but it has not confirmed the change.", success=False)


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
            return LocalResult(text=f"{fname} is offline, {honorific}. Nothing was changed.", success=False)
        if _needs_confirmation(hass, "turn_on", entity_id):
            return None
        if not await _seam_execute(hass, "light", "turn_on", {"entity_id": entity_id, "effect": pick},
                                   blocking=True, action="turn_on"):
            return LocalResult(text=f"I wasn't able to start {pick} on {fname}, {honorific}.", success=False)
        _update_ctx(entity=entity_id, domain="light", action="turn_on")
        import asyncio
        for _ in range(8):
            now = hass.states.get(entity_id)
            if now is not None and now.state == "on" and now.attributes.get("effect") == pick:
                return LocalResult(text=f"Done, {honorific}. {fname} is playing {pick}.", success=True)
            await asyncio.sleep(0.25)
        return LocalResult(text=f"I sent {pick} to {fname}, {honorific}, but it has not confirmed the change.", success=False)
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
    return f"{fname} is {kind}, {honorific}, so I can't {do}. Nothing was changed."


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
    line = f"Right now the {count} monitored devices draw {fmt(now['total_watts'])}, {h}."
    big = [d for d in now["devices"] if d["watts"] >= 1][:3]
    if big:
        line += " Most of it: " + ", ".join(f"{d['name']} {fmt(d['watts'])}" for d in big) + "."
    if now["unreadable"]:
        line += f" {', '.join(now['unreadable'][:3])} could not be read."
    return line + " There is no whole-house meter, so the house uses more."


async def try_local(hass, text, honorific="sir", force=False, pending_key=""):
    """
    PRIMARY handler. Returns LocalResult if handled, None for LLM fallback.

    When force=True (offline salvage mode), the complexity gate is bypassed:
    JARVIS attempts to extract and execute any actionable device command or
    answerable query from the text even if it looks complex, because escalating
    to the cloud LLM is not an option. Conversational/creative requests that
    have no local handler still return None — the caller supplies an honest
    offline response.
    """
    normalized = _normalize(text)
    complexity = score_complexity(text)
    for _jh_k in [k for k, v in _JH_PENDING.items() if time.time() - v["at"] >= 90]:
        del _JH_PENDING[_jh_k]
    _jh_p = _JH_PENDING.pop(pending_key, None)
    if _jh_p:
        _jh_w = re.sub(r"\s+please$", "", re.sub(r"^(?:the|in the)\s+", "", normalized)).split()
        _jh_m = [x for x in _jh_p["options"] if _jh_w and set(_jh_w) <= set(x[1].lower().split())]
        if not _jh_m and len(_jh_p["options"]) == 1 and re.fullmatch(_JH_YES, " ".join(_jh_w)):
            _jh_m = _jh_p["options"]   # "Do you mean X?" has a yes for an answer
        if len(_jh_m) == 1 and _jh_p.get("colour"):
            return await _jh_try_color(hass, f"set {_jh_m[0][1].lower()} to {_jh_p['colour']}", honorific)
        if len(_jh_m) == 1:
            _jh_eid, _jh_name = _jh_m[0]
            if _needs_confirmation(hass, _jh_p["action"], _jh_eid):
                return None
            if getattr(hass.states.get(_jh_eid), "state", "unavailable") == "unavailable":
                # the device dropped out after the question: a command to it does nothing, so never report it done
                return LocalResult(text=f"{_jh_name} is unavailable, {honorific}. Nothing was changed.", success=False)
            _jh_ok = await _execute_action(hass, _jh_p["action"], _jh_eid, {})
            _update_ctx(entity=_jh_eid, domain=_jh_eid.split(".")[0], action=_jh_p["action"])
            return LocalResult(text=_resp(_jh_p["action"], _jh_name, _jh_ok, {}, honorific), success=_jh_ok)

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
        match = re.search(pattern, normalized)
        if match:
            area_m = match.group(1) if match.lastindex else ""
            resp = _ctx_query(hass, qtype, honorific, area_m)
            if not resp and qtype == "energy":   # Jarvis Home patch (2026-11-10): no whole-house meter - the devices, read live
                resp = await _jh_power_now(hass, honorific)
            if resp:
                _LOGGER.info("Local contextual: %s", qtype)
                return LocalResult(text=resp, success=True)

    # Bulk/multi-entity
    for pattern, action, domain, scope in _BULK_PATTERNS:
        match = re.search(pattern, normalized)
        if not match:
            continue
        area_name = match.group(1) if scope == "area" and match.lastindex else None
        if _needs_confirmation(hass, action, ""):
            _LOGGER.info("Local: bulk '%s' needs confirmation — deferring to agent", action)
            return None   # protected bulk → agent skips/confirms per device
        if domain == "all":
            total = 0
            for d in ("light", "switch", "fan"):
                ents = [(s.entity_id, s.attributes.get("friendly_name", s.entity_id))
                        for s in hass.states.async_all(d)
                        if s.state == "on" and not _jh_is_setting(hass, s.entity_id)]  # Jarvis Home patch: settings stay out
                for eid, fn in ents:
                    if await _execute_action(hass, "turn_off", eid, {}):
                        total += 1
            return LocalResult(text=f"Done, {honorific}. {total} device{'s' if total != 1 else ''} turned off.", success=True)
        entities = _find_entities_in_area(hass, area_name, domain) if area_name else [
            (s.entity_id, s.attributes.get("friendly_name", s.entity_id))
            for s in hass.states.async_all(domain) if not _jh_is_setting(hass, s.entity_id)]
        if not entities:
            continue
        ok = 0
        for eid, fn in entities:
            if await _execute_action(hass, action, eid, {}):
                ok += 1
        area_str = f" in {area_name}" if area_name else ""
        verb = action.replace("_", " ")
        return LocalResult(text=f"Done, {honorific}. {ok} {domain}{'s' if ok != 1 else ''}{area_str} {verb}.", success=ok > 0)

    # Scene/script (check before single-entity to catch "activate X")
    for pattern, action, _ in _INTENT_PATTERNS:
        if action != "scene":
            continue
        match = re.search(pattern, normalized)
        if not match:
            continue
        name_frag = match.group(1) if match.lastindex else ""
        if not name_frag:
            continue
        found = _find_scene_or_script(hass, name_frag)
        if found:
            eid, fname, dtype = found
            try:
                ok = await _seam_execute(hass, dtype, "turn_on", {"entity_id": eid},
                                         blocking=True, action="activate")
                if not ok:
                    raise RuntimeError("activation did not complete")
                _update_ctx(entity=eid, domain=dtype)
                return LocalResult(text=f"Activating {fname} now, {honorific}.", success=True)
            except Exception as exc:
                return LocalResult(text=f"I wasn't able to activate {fname}, {honorific}. {exc}", success=False)

    # Goodnight shortcut
    if re.search(r"^good\s*night(?:\s+jarvis)?$", normalized):
        found = _find_scene_or_script(hass, "goodnight") or _find_scene_or_script(hass, "good night")
        if found:
            eid, fname, dtype = found
            try:
                if not await _seam_execute(hass, dtype, "turn_on", {"entity_id": eid},
                                           blocking=True, action="activate"):
                    raise RuntimeError("activation did not complete")
                return LocalResult(text=f"Goodnight, {honorific}. {fname} activated. Rest well.", success=True)
            except Exception as exc:
                _LOGGER.warning("JARVIS: goodnight scene/script '%s' failed to run: %s", eid, exc)
        off_count = 0
        for s in hass.states.async_all("light"):
            if s.state == "on" and not _jh_is_setting(hass, s.entity_id):
                if await _seam_execute(hass, "light", "turn_off",
                                       {"entity_id": s.entity_id},
                                       blocking=False, action="turn_off"):
                    off_count += 1
                else:
                    _LOGGER.debug("JARVIS: goodnight could not turn off %s", s.entity_id)
        for s in hass.states.async_all("lock"):
            if s.state == "unlocked":
                if not await _seam_execute(hass, "lock", "lock",
                                           {"entity_id": s.entity_id},
                                           blocking=False, action="lock"):
                    _LOGGER.warning("JARVIS: goodnight could not lock %s", s.entity_id)
        return LocalResult(text=f"Goodnight, {honorific}. {off_count} lights off. Rest well.", success=True)

    # Single-entity patterns
    _last_failed_name = None  # Track for end-of-loop error
    _jh_wrong_type = None   # Jarvis Home patch (2026-11-07, round 2): a device of exactly that name that cannot do it
    learned_aliases = await hass.async_add_executor_job(
        _load_learned_aliases, hass
    )
    for pattern, action, domain_hint in _INTENT_PATTERNS:
        if action == "scene":
            continue
        match = re.search(pattern, normalized)
        if not match:
            continue
        _LOGGER.info("Local intent: action=%s", action)
        if action in ("query_time", "query_date", "greeting", "thanks", "status"):
            return LocalResult(text=_query_resp(hass, action, "", "", honorific), success=True)
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
                found = _find_entities_in_area(hass, area, tgt_domain)
                target = found[0][0] if found else None
            if not target:
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
            success = await _execute_action(hass, action, target, vargs)
            st = hass.states.get(target)
            fname = st.attributes.get("friendly_name", target) if st else target
            _update_ctx(entity=target, domain=target.split(".")[0], action=action)
            return LocalResult(text=_resp(action, fname, success, vargs, honorific),
                               success=success)
        name_frag = groups[0] if groups else ""
        extra_arg = groups[1] if len(groups) > 1 else None
        if not name_frag:
            continue
        if name_frag.strip() in _JH_PRONOUNS:
            _last_failed_name = name_frag
            continue
        _jh_said = normalized[match.start(1):]
        _jh_doms = _jh_action_domains(action)
        _jh_named = _jh_whole(hass, name_frag, _jh_doms, domain_hint) or _jh_whole(hass, _jh_said, _jh_doms, domain_hint)
        resolved = _jh_named or _find_entity(
            hass, name_frag, domain_hint, learned_aliases
        ) or _find_entity(hass, name_frag, None, learned_aliases)
        if resolved and not _jh_named and (_jh_wrong_kind(name_frag, resolved) or _jh_wrong_kind(_jh_said, resolved)):
            resolved = None   # the fan was asked for, so the lamp of that room is no answer
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
                    return LocalResult(text=f"{', '.join(_jh_gone)} {_jh_v} unavailable, {honorific}. Nothing was changed.", success=False)
                if len(_jh_ears) == 1:
                    _JH_PENDING[pending_key] = {"action": action, "options": _jh_ears, "at": time.time()}
                    return LocalResult(text=f"{', '.join(_jh_gone)} {_jh_v} unavailable, {honorific}. Do you mean {_jh_ears[0][1]}?", success=False)
            _JH_PENDING[pending_key] = {"action": action, "options": _jh_ears, "at": time.time()}
            _jh_n = [x[1] for x in _jh_ears]
            return LocalResult(text=f"Which one, {honorific}: {', '.join(_jh_n[:-1])} or {_jh_n[-1]}?", success=False)
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
            if _jh_many:
                def _jh_st(e):
                    st = hass.states.get(e)
                    return st.state if st else "unavailable"
                _jh_live = [x for x in _jh_many if _jh_st(x[0]) not in ("unavailable", "unknown")]
                _jh_dead = [x[1] for x in _jh_many if _jh_st(x[0]) in ("unavailable", "unknown")]
                _jh_verb = "is" if len(_jh_dead) == 1 else "are"
                if not _jh_live:
                    return LocalResult(text=f"{', '.join(_jh_dead)} {_jh_verb} unavailable, {honorific}. Nothing was changed.", success=False)
                _jh_fit = [x for x in _jh_live if (_jh_st(x[0]) != "off") == (action == "turn_off")]
                if not _jh_fit:
                    _jh_who = "They are all" if len(_jh_live) > 1 else f"{_jh_live[0][1]} is"
                    _jh_tail = f" {', '.join(_jh_dead)} {_jh_verb} unavailable." if _jh_dead else ""
                    return LocalResult(text=f"{_jh_who} already {'off' if action == 'turn_off' else 'on'}, {honorific}.{_jh_tail}", success=True)
                if len(_jh_fit) == 1 and action == "turn_on" and any(
                        x[1] in _jh_dead and not _jh_same_kind(hass, x[0], _jh_fit[0][0]) for x in _jh_many):
                    # the person may well mean the lamp that is gone, and switching on another kind of device is no safe guess
                    _JH_PENDING[pending_key] = {"action": action, "options": _jh_fit, "at": time.time()}
                    return LocalResult(text=f"{', '.join(_jh_dead)} {_jh_verb} unavailable, {honorific}. Do you mean {_jh_fit[0][1]}?", success=False)
                # Jarvis Home patch (2026-11-07, round 2): by kind, not by domain. Two lights or two heaters act at once.
                if len(_jh_fit) == 1 and action == "turn_on" and _jh_fit[0][0] != resolved[0] and not _jh_same_kind(hass, _jh_fit[0][0], resolved[0]):
                    # Jarvis Home patch (2026-11-07): the name pointed at one kind of device and the only one that is off is another
                    # kind, a heater for a lamp. Switching that on is no safe guess, so ask first.
                    _jh_on = [x[1] for x in _jh_live if x not in _jh_fit]
                    _jh_lead = f"{', '.join(_jh_on)} {'is' if len(_jh_on) == 1 else 'are'} already on, {honorific}. " if _jh_on else ""
                    _JH_PENDING[pending_key] = {"action": action, "options": _jh_fit, "at": time.time()}
                    return LocalResult(text=f"{_jh_lead}Do you mean {_jh_fit[0][1]}?", success=False)
                if len(_jh_fit) == 1:
                    resolved = _jh_fit[0]
                elif any(_needs_confirmation(hass, action, x[0]) for x in _jh_fit):
                    return None   # protected device: the agent runs the confirmation gate
                else:
                    _JH_PENDING[pending_key] = {"action": action, "options": _jh_fit, "at": time.time()}
                    _jh_n = [x[1] for x in _jh_fit]
                    return LocalResult(text=f"Which one, {honorific}: {', '.join(_jh_n[:-1])} or {_jh_n[-1]}?", success=False)
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
            return LocalResult(text=f"{fname} is unavailable, {honorific}. Nothing was changed.", success=False)
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
        if action.startswith("query_"):
            resp = _query_resp(hass, action, entity_id, fname, honorific)
            if resp:
                return LocalResult(text=resp, success=True)
            continue
        if _needs_confirmation(hass, action, entity_id):
            _LOGGER.info("Local: '%s' on %s needs confirmation — deferring to agent",
                         action, entity_id)
            return None   # protected action → the agent runs the confirmation gate
        success = await _execute_action(hass, action, entity_id, args)
        _update_ctx(entity=entity_id, domain=entity_id.split(".")[0], action=action)
        return LocalResult(text=_resp(action, fname, success, args, honorific), success=success)

    # v5.7.08: If patterns matched but entity resolution failed, ALWAYS
    # fall through to the agentic LLM. The agent has search_entities which
    # uses fuzzy matching and learned aliases — much better at finding
    # devices than the local regex resolver. Never return a local error
    # for device commands.
    # v5.9.06: In force/offline mode there's no LLM to escalate to, so return
    # an honest local error naming the device we couldn't resolve.
    if force and _jh_wrong_type:
        return LocalResult(text=_jh_wrong_type_line(*_jh_wrong_type, honorific), success=False)
    if _last_failed_name:
        if force:
            return LocalResult(
                text=(
                    f"I couldn't find a device matching '{_last_failed_name}', "
                    f"{honorific}, and I'm offline so I can't do a deeper search "
                    f"right now. Try the exact device name."
                ),
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
                        text=(
                            f"Already on it, {honorific}. I'm monitoring "
                            f"{', '.join(tracking)}. I'll announce when "
                            f"the cycle completes."
                        ),
                        success=True,
                    )
                return LocalResult(
                    text=(
                        f"I'm monitoring for appliance cycles, {honorific}, "
                        f"but I haven't found any power sensors matching "
                        f"your appliances yet. Make sure the power monitoring "
                        f"sensor has 'washer', 'dryer', or 'dishwasher' "
                        f"in its name."
                    ),
                    success=True,
                )
            else:
                return LocalResult(
                    text=(
                        f"The appliance monitor isn't running at the moment, "
                        f"{honorific}. It starts automatically with the "
                        f"observer. Check if the observer is enabled."
                    ),
                    success=True,
                )
        except Exception:
            return LocalResult(
                text=(
                    f"I'll keep an eye on it, {honorific}. If I have a "
                    f"power sensor for that appliance, I'll announce when "
                    f"the cycle finishes."
                ),
                success=True,
            )

    # Follow-up ("also turn off the kitchen")
    if _ctx_fresh() and re.search(r"^(?:also|and|now)\s+", normalized):
        stripped = re.sub(r"^(?:also|and|now)\s+", "", normalized)
        result = await try_local(hass, stripped, honorific, force=force, pending_key=pending_key)
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
            state = hass.states.get(entity_id)
            if state:
                return LocalResult(text=f"{fname} is currently {state.state}, {honorific}.", success=True)

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
    # Phrases about JARVIS itself ("your X", "you Y") aren't device names.
    if "your" in words or "you" in words:
        return False
    return True
