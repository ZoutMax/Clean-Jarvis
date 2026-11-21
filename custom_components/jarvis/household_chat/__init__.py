"""Household Chat: WhatsApp-style messages between the people of this Home Assistant. Part of Clean Jarvis.

It runs inside the JARVIS integration while the Chat switch in JARVIS Settings > Optional pages is on (off by default):
optional_pages.py starts it with async_start and stops it with async_stop. Its data lives in <config>/jarvis/household_chat.

A Chat page in the sidebar for every login: one-to-one chats with every other person and one group chat for the whole
household. People are the Home Assistant users linked to a person (all active users when nobody is linked yet), so new
and removed people follow by themselves.

Privacy: only the people in a conversation can read it. There is no command, admin or otherwise, that reads somebody
else's chat. Live updates go only to the members' own open pages through an internal signal, not the event bus that
admins can watch. Messages stay until someone deletes them. Message text is never logged.

Phone: a new message and a nudge go to the receiver's Home Assistant app(s), unless they muted that chat. A message is
not pushed to someone who is looking at that chat right now.

JARVIS: when the JARVIS integration is installed, everyone also has a private chat with JARVIS (he answers through
his conversation agent, as the person who wrote, so his per-person memory and permissions apply), and in the house
group he answers when a message names him ("Jarvis, ..." or "@jarvis") or asks everyone a question. A group answer is
read by every member, so there he never answers as the sender: he answers as a hidden non-admin "house group" login,
with only what any household member may see (no admin tools, nobody's private memory).

Like WhatsApp: reply with a quote, one reaction per person, edit your own text within 15 minutes, delete for everyone
or for me, grey ticks when the message reached a phone or an open Chat page, blue when read. The phone notification
of a chat stacks its unread messages and goes away once the chat is read anywhere.

Online: a person is online while their phone is in use (the Home Assistant app's "Interactive" sensor is on) or a Chat
page of theirs is open; otherwise the chat shows when they were last seen. Everyone can hide their own online status.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
import re
import time
import uuid
from http import HTTPStatus
from pathlib import Path
from urllib.parse import quote

import voluptuous as vol
from aiohttp import web

from homeassistant.components import frontend, panel_custom, websocket_api
from homeassistant.components.http import KEY_HASS_USER, HomeAssistantView
from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import Context, Event, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect, async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import slugify

from .const import DATA, GROUP, MAX_PHOTO, MAX_TEXT, PAGE, PANEL_PATH, REGISTERED, SIGNAL

PANEL_OWNED = "jarvis_household_chat_panel"   # set while JARVIS's own Chat page is registered
from .store import REACTIONS, ChatStore, sniff

_LOGGER = logging.getLogger(__name__)

DIRECT_RE = re.compile(r"^d:([0-9a-f]{32})\|([0-9a-f]{32})$")
JARVIS = "jarvis"                                   # JARVIS's id as a chat member (not a Home Assistant login)
JARVIS_CONV_RE = re.compile(r"^j:([0-9a-f]{32})$")  # one private chat with JARVIS per person
JARVIS_NAMED = re.compile(r"(?:^|[^\w@])@?jarvis\b", re.I)
# a question to the whole group: JARVIS answers it too (owner's choice, 2026-11-09: "named + questions to all")
GROUP_QUESTION = re.compile(r"\b(?:every(?:one|body)|any(?:one|body)|all of you|you all|guys|people|iedereen|iemand|jullie|allemaal)\b"
                            r"|^\s*(?:who|wie)\b", re.I)
MEDIA_RE = re.compile(r"^[0-9a-f]{32}$")
GROUP_LOGIN = "JARVIS house group"   # hidden non-admin login JARVIS answers the group as (see ChatHub.group_identity)
NUDGE_GAP = 10          # seconds between two nudges from one person in one chat
PHONE_FRESH = 10 * 60   # "online" needs the phone in use AND a report this recent
PHONE_REACHABLE = 30 * 60   # an idle phone reports about every 15 min; silent longer = offline (flight mode, no signal)
RESTORED_GRACE = 30         # phone values older than our start + this were put back by Home Assistant, not reported
NUDGE_VIBRATION = "0, 500, 150, 500, 150, 800"


# ---- the house group and private data (2026-11-15)
# Everyone in the group reads JARVIS's answer, so there he answers as the non-admin house group login and may not tell where
# someone is or anything about their phone. Saying "I cannot determine her location" sounded like a failure; he now says why
# and where to ask. The sender's form of address and language only choose the words; the answer never uses the sender's rights.
PRIVATE_WORDS = re.compile(
    # review round 14: no common device or colour words ("position" of a blind, "sleep" mode, "charge" the car, "red" lights, the
    # house "alarm", the "network"): those are ordinary orders; the AI's own privacy filter still hides another person's data
    r"\b(?:where(?:abouts)?|location|located|phone|mobile|battery|charged|charging|steps|heart ?rate|"
    r"waar|locatie|telefoon|mobiel|batterij|opgeladen|accu|stappen|wo|standort|handy|akku|aufgeladen|schritte|"
    r"où|téléphone|portable|batterie|chargé|chargée|dónde|teléfono|móvil|batería|cargado|cargada|"
    # review round 13: the phone's Wi-Fi and its wake-up alarm say where someone is or what they do, too
    r"wi-?fi|wlan|ssid|ipad|next alarm|alarm clock|wake-up alarm|wekker|wecker|réveil|despertador|bateria|batteria)(?!\w)"
    r"|อยู่ที่ไหน|ที่ไหน|โทรศัพท์|มือถือ|แบต", re.I)
PRONOUNS = re.compile(r"\b(?:he|she|his|her|him|they|their|hij|zij|ze|zijn|haar|hem|sie|ihr|ihre|sein|seine|il|elle|son|sa|ses|él|ella|su|sus)\b", re.I)
PRIVATE_REPLY = {
    "en": "I only share where someone is, or their phone details, in a private chat with me{a}.",
    "de": "Wo jemand ist oder Details zu seinem Telefon sage ich nur im privaten Chat mit mir{a}.",
    "nl": "Waar iemand is, of gegevens van zijn of haar telefoon, deel ik alleen in een privéchat met mij{a}.",
    "fr": "Je ne dis où se trouve quelqu'un, ou les détails de son téléphone, que dans une conversation privée avec moi{a}.",
    "es": "Solo digo dónde está alguien, o los detalles de su teléfono, en un chat privado conmigo{a}.",
    "th": "ฉันบอกได้ว่าใครอยู่ที่ไหน หรือข้อมูลโทรศัพท์ของเขา เฉพาะในแชทส่วนตัวกับฉันเท่านั้น{a}",
}
ADDRESS_WORD = {"de": {"sir": "Herr", "madam": "gnädige Frau"}, "nl": {"sir": "meneer", "madam": "mevrouw"},
                "fr": {"sir": "monsieur", "madam": "madame"}, "es": {"sir": "señor", "madam": "señora"}}


def asks_private(text: str, others: list) -> bool:
    """A question in the group about another person's whereabouts or phone (battery, steps, sleep ...): a private-data word
    together with the name of another member of the household, or a he/she word."""
    if not text or not PRIVATE_WORDS.search(text):
        return False
    low = text.lower()
    if any(n and re.search(r"(?:^|\W)%s(?:\W|$)" % re.escape(n.lower()), low) for n in others):
        return True
    return bool(PRONOUNS.search(text))


def reply_language(text: str) -> str:
    if re.search(r"[\u0e00-\u0e7f]", text or ""):
        return "th"
    try:
        from ..local_engine import jh_text_language   # JARVIS's own detector (step 127)
        lang = jh_text_language(text)
    except Exception:  # noqa: BLE001 - the chat on its own: English
        lang = "en"
    return lang if lang in PRIVATE_REPLY else "en"


def private_reply(lang: str, address: str) -> str:
    """The refusal in the sender's language with their form of address ("sir", "madam", a first name, or none)."""
    lang = lang if lang in PRIVATE_REPLY else "en"
    if lang == "th":   # Thai: only a polite ending, no "sir" (as JARVIS's prompt rule)
        return PRIVATE_REPLY["th"].format(a=" ค่ะ" if address == "madam" else " ครับ")
    word = ADDRESS_WORD.get(lang, {}).get(address, address) if address else ""
    return PRIVATE_REPLY[lang].format(a=", " + word if word else "")


def sender_address(hass, user: str) -> str:
    """How the sender is addressed (JARVIS's per-person setting, step 114); "sir" when that cannot be read."""
    try:
        from ..presence import jh_address_for
        from .. import jarvis_config
        return jh_address_for(hass, user, str(jarvis_config.get("honorific", "sir") or "sir"))
    except Exception:  # noqa: BLE001
        return "sir"


class ChatError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code



# jh-phone-exact (step 159) BEGIN - review round 17 #3: the same block, byte for byte, is in household_chat/__init__.py
# Home Assistant's mobile_app keys its notify targets by the app's device name: two apps with the same name (two phones of one
# model, two iPhones called "iPhone", one name used by several logins) share ONE notify.mobile_app_<name>, and that service
# reaches whichever app registered last. A phone is therefore chosen by its own registration (config entry and webhook), never
# by a name: the service counts for an app only when Home Assistant's own target map says it reaches exactly that app's
# webhook, or, when that map cannot be read, when no app of ANOTHER login shares the name. Anything else is ambiguous: no push
# to it, and a warning once per name (fail closed).
_JH_PHONE_WARNED: set = set()


def _jh_phone_slug(name) -> str:
    """The notify service Home Assistant gives an app's device name: slugify("mobile_app_" + name)."""
    try:
        from homeassistant.util import slugify
        out = slugify("mobile_app_" + str(name))
        if isinstance(out, str) and out:
            return out
    except Exception:  # noqa: BLE001
        pass
    return re.sub(r"[^a-z0-9]+", "_", ("mobile_app_" + str(name)).casefold()).strip("_")


def _jh_phone_targets(hass) -> dict:
    """Home Assistant's own map of mobile_app notify services to the webhook each one reaches; {} when it cannot be read."""
    out = {}
    try:
        for svc in list(((hass.data.get("notify_services") or {}).get("mobile_app")) or []):
            targets = getattr(svc, "registered_targets", None)
            if isinstance(targets, dict):
                out.update({str(k): v for k, v in targets.items()})
    except Exception:  # noqa: BLE001
        return {}
    return out


def jh_phone_service(hass, entry, entries=None, targets=None):
    """The notify service (mobile_app_<name>) that reaches exactly this mobile_app config entry's phone, or None when no service
    does or when it is ambiguous (fail closed). entries: every mobile_app entry (read when not given); targets: as
    _jh_phone_targets (read when not given)."""
    data = getattr(entry, "data", None) or {}
    name = data.get("device_name")
    if not name:
        return None
    service = _jh_phone_slug(name)
    try:
        if not hass.services.has_service("notify", service):
            return None
    except Exception:  # noqa: BLE001
        return None
    targets = _jh_phone_targets(hass) if targets is None else targets
    if service in targets:   # Home Assistant says which app this service reaches: it must be this one
        hook = data.get("webhook_id")
        return service if hook and targets.get(service) == hook else None
    try:
        entries = list(hass.config_entries.async_entries("mobile_app")) if entries is None else entries
    except Exception:  # noqa: BLE001
        return None
    same = [e for e in entries if (getattr(e, "data", None) or {}).get("device_name")
            and _jh_phone_slug((getattr(e, "data", None) or {}).get("device_name")) == service]
    owners = {(getattr(e, "data", None) or {}).get("user_id") for e in same}
    if entry in same and owners == {data.get("user_id")} and data.get("user_id"):
        return service   # no other login's app has this name (one login's own second registration is still that login's)
    if service not in _JH_PHONE_WARNED:
        _JH_PHONE_WARNED.add(service)
        _LOGGER.warning("JARVIS: %d phones share the name %r in the Home Assistant app, so notify.%s cannot be told apart; "
                        "nothing private is pushed to it. Rename one of them in the app (Settings > Companion app).",
                        len(same), str(name), service)
    return None
# jh-phone-exact (step 159) END

class ChatHub:
    def __init__(self, hass: HomeAssistant, store: ChatStore):
        self.hass = hass
        self.store = store
        self.pages: dict[tuple, str] = {}                 # (connection, subscription) of an open Chat page -> user
        self.views: dict[int, tuple] = {}   # connection -> (user, chat on screen or None, page in use: visible and touched lately)
        self.last_nudge: dict[tuple, float] = {}
        self.announced: dict[str, tuple] = {}   # user -> last online status sent to the pages
        self.started = time.time()               # phone values from before this are restored ones, not real reports
        self.jarvis_locks: dict[str, asyncio.Lock] = {}
        self.jarvis_threads: dict[tuple, str | None] = {}
        self.was_reachable: dict[str, bool] = {}   # user -> phone reachable at the last recheck
        self.group_lock = asyncio.Lock()
        self.picture = ""                         # JARVIS's picture in the chat list (set by async_start)
        self.unsubs: list = []

    def jarvis_agent(self) -> str | None:
        """The conversation entity of the JARVIS integration, when it is installed and enabled."""
        for ent in er.async_get(self.hass).entities.values():
            if ent.domain == "conversation" and ent.platform == "jarvis" and not ent.disabled:
                return ent.entity_id
        return None

    async def members(self, conv: str) -> set[str]:
        if conv == GROUP:
            return {p["id"] for p in await self.people()}
        m = JARVIS_CONV_RE.match(conv)
        if m:
            return {m.group(1)}
        return set(conv[2:].split("|"))

    async def _db(self, fn, *args):
        return await self.hass.async_add_executor_job(fn, *args)

    # ---- online status
    def phones_of(self, user: str) -> list[tuple[str, float]]:
        """Per Home Assistant app of this user: its 'Interactive' (phone in use) sensor and when the phone last reported
        anything at all. A phone in flight mode keeps its last states, so only a recent report counts."""
        ents, devs = er.async_get(self.hass), dr.async_get(self.hass)
        out = []
        for entry in self.hass.config_entries.async_entries("mobile_app"):
            if entry.data.get("user_id") != user:
                continue
            for device in dr.async_entries_for_config_entry(devs, entry.entry_id):
                interactive, contact = None, 0.0
                real_after = self.started + RESTORED_GRACE
                for ent in er.async_entries_for_device(ents, device.id):
                    if ent.disabled:
                        continue
                    st = self.hass.states.get(ent.entity_id)
                    if st is not None and st.state not in ("unavailable", "unknown"):
                        seen = getattr(st, "last_reported", st.last_updated).timestamp()
                        if seen > real_after:          # a value Home Assistant put back at start is no report
                            contact = max(contact, seen)
                    if ent.domain == "binary_sensor" and (ent.unique_id or "").endswith("interactive"):
                        interactive = ent.entity_id
                if interactive:
                    out.append((interactive, contact))
        return out

    def phone_sensors(self, user: str) -> list[str]:
        return [sensor for sensor, _ in self.phones_of(user)]

    def owner_of_sensor(self, entity_id: str) -> str | None:
        ent = er.async_get(self.hass).async_get(entity_id)
        if not ent or not (ent.unique_id or "").endswith("interactive") or not ent.config_entry_id:
            return None
        entry = self.hass.config_entries.async_get_entry(ent.config_entry_id)
        return entry.data.get("user_id") if entry and entry.domain == "mobile_app" else None

    async def presence(self, user: str, info: dict | None = None) -> dict:
        info = info if info is not None else (await self._db(self.store.people_info)).get(user, {})
        if info.get("hide_online"):
            return {"hidden": True, "online": False, "idle": False, "last_seen": None}
        online = self.online(user)
        idle = False
        last = max(info.get("last_seen") or 0, info.get("phone_seen") or 0) or None
        now = time.time()
        newest = 0.0
        for entity_id, contact in self.phones_of(user):
            st = self.hass.states.get(entity_id)
            if st is None or not contact:                   # no real report since the start: not online
                continue
            newest = max(newest, contact)
            if st.state == "on" and now - contact < PHONE_FRESH:
                online = True
            elif now - contact < PHONE_REACHABLE:
                idle = True                                 # connected, not in use: last active when the screen went off
                if st.state == "off" and st.last_changed.timestamp() > self.started + RESTORED_GRACE:
                    last = max(last or 0, st.last_changed.timestamp())
            else:
                last = max(last or 0, contact)              # gone quiet: last seen at its last report
        if newest and newest - (info.get("phone_seen") or 0) > 60:
            await self._db(self.store.set_phone_seen, user, newest)
        if online:
            return {"hidden": False, "online": True, "idle": False, "last_seen": None}
        return {"hidden": False, "online": False, "idle": idle, "last_seen": last}

    async def announce(self, user: str, only_changes: bool = False) -> None:
        """Tells every open Chat page that this person's online status may have changed."""
        people = {p["id"] for p in await self.people()}
        if user not in people:
            return
        now = await self.presence(user)
        key = (now["online"], now["idle"], now["hidden"])
        before = self.announced.get(user)
        if only_changes and before == key:
            return
        self.announced[user] = key
        self.emit(people, {"type": "presence", "user": user, **now})
        if before is not None and not (before[0] or before[1]) and (self.reachable(user) or now["online"]):
            await self.deliver_all(user)

    def reachable(self, user: str) -> bool:
        """True when one of this person's phones really reported recently (so a push reaches it now)."""
        now = time.time()
        return any(contact and now - contact < PHONE_REACHABLE for _, contact in self.phones_of(user))

    async def deliver_all(self, user: str) -> None:
        """The phone is back: everything sent to this person in the meantime has now arrived (two grey ticks)."""
        people = await self.people()
        await self._db(self.store.join, GROUP, user)   # a newcomer starts at the newest group message, not the past
        convs = [GROUP] + [self.direct(user, p["id"]) for p in people if p["id"] != user]
        overview = await self._db(self.store.overview, user, convs)
        for conv, item in overview.items():
            last = item.get("last")
            if last and last["id"] > item.get("delivered", 0):
                await self._mark(conv, await self.members(conv), user, delivered=last["id"])

    async def recheck(self, _now=None) -> None:
        """A phone that goes quiet or comes back sends no change of its own, so the status is looked at every minute
        (also with no Chat page open: a phone that comes back gets its delivered ticks). Reachability is followed on
        its own as well: someone who hides their online status has the same status key all the time, but their phone
        coming back must still give the delivered ticks."""
        _jh_people = await self.people()
        if _jh_people:   # review round 8: a member who left loses their group mark (see access)
            await self._db(self.store.prune, GROUP, {p["id"] for p in _jh_people} | {JARVIS})
        for p in _jh_people:
            await self.announce(p["id"], only_changes=True)
            now = self.reachable(p["id"])
            if self.was_reachable.get(p["id"]) is False and now:
                await self.deliver_all(p["id"])   # does nothing when announce already delivered everything
            self.was_reachable[p["id"]] = now

    async def page_change(self, user: str, opened: bool) -> None:
        if not opened and not self.online(user):   # the page closed or is no longer in use: last seen now
            await self._db(self.store.set_last_seen, user, time.time())
        await self.announce(user)

    async def set_hide_online(self, user: str, hide: bool) -> None:
        await self._db(self.store.set_hide_online, user, hide)
        await self.announce(user)

    # ---- people and access
    async def people(self) -> list[dict]:
        persons = {}
        for st in self.hass.states.async_all("person"):
            uid = st.attributes.get("user_id")
            if uid:
                persons[uid] = st
        users = [u for u in await self.hass.auth.async_get_users() if u.is_active and not u.system_generated]
        if persons:
            users = [u for u in users if u.id in persons]
        out = []
        for u in users:
            st = persons.get(u.id)
            out.append({"id": u.id, "name": (st.name if st else "") or u.name or "?",
                        "picture": st.attributes.get("entity_picture") if st else None})
        out.sort(key=lambda p: p["name"].casefold())
        return out

    @staticmethod
    def direct(a: str, b: str) -> str:
        return "d:" + "|".join(sorted((a, b)))

    async def access(self, user: str, conv: str, send: bool = False) -> set[str]:
        """The members of conv when user may read it (send=True: and write to it). Raises ChatError otherwise."""
        if conv == GROUP:
            ids = {p["id"] for p in await self.people()}
            if ids:   # review round 8: whoever left the group starts again at the newest message when added back
                await self._db(self.store.prune, GROUP, ids | {JARVIS})
            if user in ids:
                return ids
            raise ChatError("not_allowed", "Not a member of this chat")
        j = JARVIS_CONV_RE.match(conv or "")
        if j:
            if j.group(1) != user:
                raise ChatError("not_allowed", "Not a member of this chat")
            if send and not self.jarvis_agent():
                raise ChatError("not_allowed", "JARVIS is not installed")
            return {user}
        m = DIRECT_RE.match(conv or "")
        if not m or m.group(1) >= m.group(2) or user not in (m.group(1), m.group(2)):
            raise ChatError("not_allowed", "Not a member of this chat")
        members = {m.group(1), m.group(2)}
        if send and not members <= {p["id"] for p in await self.people()}:
            raise ChatError("not_allowed", "This person has left")
        return members

    def emit(self, members: set[str], payload: dict) -> None:
        async_dispatcher_send(self.hass, SIGNAL, set(members), payload)

    def online(self, user: str) -> bool:
        """A Chat page counts only while it is in front of the person (visible and touched in the last minutes), like WhatsApp:
        a page left open on an unattended screen or in a background tab does not make someone online."""
        return any(v[0] == user and v[2] for v in self.views.values())

    def connected(self, user: str) -> bool:
        return user in self.pages.values()

    def watching(self, user: str, conv: str) -> bool:
        return (user, conv, True) in self.views.values()

    async def _mark(self, conv: str, members: set[str], user: str, delivered: int = 0, read: int = 0) -> None:
        marks = await self._db(self.store.mark, conv, user, delivered, read)
        if marks:
            self.emit(members, {"type": "marks", "conv": conv, "user": user, **marks})
        return marks

    # ---- commands
    async def bootstrap(self, user: str) -> dict:
        people = await self.people()
        if user not in {p["id"] for p in people}:
            raise ChatError("not_a_member", "This login is not linked to a person, so it has no chats")
        await self._db(self.store.join, GROUP, user)
        convs = [GROUP] + [self.direct(user, p["id"]) for p in people if p["id"] != user]
        convs += [c for c in await self._db(self.store.direct_convs, user) if c not in convs and DIRECT_RE.match(c)]
        agent = self.jarvis_agent()
        if agent:
            convs.append("j:" + user)
        overview = await self._db(self.store.overview, user, convs)
        out = []
        for conv in convs:
            item = {"id": conv, **overview[conv]}
            if conv == GROUP:
                item["kind"] = "group"
            elif conv.startswith("j:"):
                item.update(kind="jarvis", **{"with": JARVIS})
            else:
                a, b = conv[2:].split("|")
                item.update(kind="direct", **{"with": b if a == user else a})
                if overview[conv]["last"] is None and item["with"] not in {p["id"] for p in people}:
                    continue
            last = overview[conv]["last"]
            if last and last["id"] > overview[conv]["delivered"]:
                await self._mark(conv, await self.members(conv), user, delivered=last["id"])
            out.append(item)
        info = await self._db(self.store.people_info)
        for p in people:
            p.update(await self.presence(p["id"], info.get(p["id"], {})))
        return {"me": user, "people": people, "group_title": self.hass.config.location_name or "Home", "convs": out,
                "hide_online": bool(info.get(user, {}).get("hide_online")), "reactions": list(REACTIONS),
                "jarvis": {"id": JARVIS, "name": "JARVIS", "picture": self.picture, "online": True} if agent else None}

    async def history(self, user: str, conv: str, before: int | None) -> dict:
        members = await self.access(user, conv)
        if conv == GROUP:
            await self._db(self.store.join, GROUP, user)
        result = await self._db(self.store.history, conv, user, before, PAGE)
        if result["messages"] and not before:
            await self._mark(conv, members, user, delivered=result["messages"][-1]["id"])
        return result

    async def send(self, user: str, conv: str, text: str, photo: str | None, kind: str = "text",
                   reply_to: int | None = None) -> dict:
        members = await self.access(user, conv, send=True)
        text = (text or "").strip()[:MAX_TEXT]
        if kind == "nudge" and conv.startswith("j:"):
            raise ChatError("not_allowed", "JARVIS cannot be nudged")
        if kind == "nudge":
            key = (user, conv)
            if time.monotonic() - self.last_nudge.get(key, -NUDGE_GAP) < NUDGE_GAP:
                raise ChatError("too_soon", "Wait a few seconds before nudging again")
            self.last_nudge[key] = time.monotonic()
            text, photo = "", None
        elif not text and not photo:
            raise ChatError("empty", "Nothing to send")
        if photo and not MEDIA_RE.match(photo):
            raise ChatError("bad_photo", "Unknown photo")
        try:
            msg = await self._db(self.store.add, conv, user, kind, text, photo, members if conv == GROUP else None,
                                 reply_to if kind == "text" else None)
        except ValueError as exc:
            raise ChatError("bad_message", "Unknown photo or message to reply to") from exc
        self.emit(members, {"type": "message", "conv": conv, "message": msg})
        for other in members - {user}:
            if self.connected(other):   # an open Chat page got it, also in the background: delivered
                await self._mark(conv, members, other, delivered=msg["id"])
        await self.push(conv, user, msg, members - {user})
        if kind == "text" and (conv.startswith("j:") or (conv == GROUP and self.jarvis_agent()
                                                          and await self.for_jarvis_in_group(text, user))):
            self.hass.async_create_task(self._jarvis_reply(conv, members, user, msg))
        return msg

    async def for_jarvis_in_group(self, text: str, sender: str) -> bool:
        """In the house group JARVIS answers when named, and a question to everyone ("did everyone get this?", "who is
        home?"), unless it names another person (then it is for them)."""
        if JARVIS_NAMED.search(text):
            return True
        if "?" not in text or not GROUP_QUESTION.search(text):
            return False
        names = [p["name"].split()[0].lower() for p in await self.people() if p["id"] != sender and p.get("name")]
        low = text.lower()
        return not any(re.search(r"\b%s\b" % re.escape(n), low) for n in names)

    async def group_identity(self) -> str | None:
        """The login JARVIS answers the house group as. Every member reads a group answer, so it must hold only what any
        member may see: a hidden (system-generated) Home Assistant login in the normal users group, never admin, with
        no person linked, so JARVIS gives it household tools only, the household's facts and a memory of its own. It is
        made the first time it is needed and is no chat member (people() skips system-generated logins). None when it
        cannot be made or has become an admin: then JARVIS does not answer in the group (fails closed)."""
        from homeassistant.auth.const import GROUP_ID_USER
        async with self.group_lock:
            try:
                for u in await self.hass.auth.async_get_users():
                    if u.system_generated and u.name == GROUP_LOGIN:
                        return u.id if u.is_active and not u.is_admin else None
                return (await self.hass.auth.async_create_system_user(GROUP_LOGIN, group_ids=[GROUP_ID_USER])).id
            except Exception as exc:  # noqa: BLE001 - no group login: no group answer
                _LOGGER.warning("Household Chat: no house group login for JARVIS: %s", type(exc).__name__)
                return None

    async def _jarvis_reply(self, conv: str, members: set[str], user: str, msg: dict) -> None:
        """JARVIS answers through his conversation agent: in a private JARVIS chat as the person who wrote, in the house
        group as the house group login (group_identity), because everyone in the group reads the answer."""
        agent = self.jarvis_agent()
        if not agent:
            return
        async with self.jarvis_locks.setdefault(conv, asyncio.Lock()):
            if conv.startswith("j:"):
                await self._mark(conv, members, JARVIS, delivered=msg["id"], read=msg["id"])
            done = asyncio.Event()

            async def keep_typing():
                while not done.is_set():
                    self.emit(members, {"type": "typing", "conv": conv, "user": JARVIS})
                    try:
                        await asyncio.wait_for(done.wait(), 4)
                    except asyncio.TimeoutError:
                        pass

            typing = self.hass.async_create_task(keep_typing())
            answer = ""
            try:
                asker = user if conv.startswith("j:") else await self.group_identity()
                if not msg["text"]:
                    answer = "I can only read text here."
                elif asker is None:
                    answer = "I can only answer that in your private chat with me."
                elif conv == GROUP and asks_private(msg["text"], [p["name"].split()[0] for p in await self.people()
                                                                  if p["id"] != user and p.get("name")]):
                    # whereabouts or phone details of someone in front of the whole group: say why, and where to ask
                    answer = private_reply(reply_language(msg["text"]), sender_address(self.hass, user))
                else:
                    from homeassistant.components import conversation   # loaded only when JARVIS is asked something
                    # Review round 24 #5: the house group's thread belongs to its current membership. Its channel carries
                    # the membership key (store.group_epoch: who is in, and since when), so someone newly added starts a new
                    # thread with no earlier group turns in JARVIS's memory (and no reseeded rows from before they joined),
                    # as the chat itself never shows them the earlier messages. Unreadable: a thread of this message alone.
                    channel, thread = "chat:" + conv, (conv, user)
                    if conv == GROUP:
                        try:
                            epoch = await self._db(self.store.group_epoch, conv, members)
                        except Exception:  # noqa: BLE001
                            epoch = "x" + uuid.uuid4().hex[:11]
                        channel, thread = "chat:%s:%s" % (conv, epoch), (conv, user, epoch)
                        for old in [k for k in self.jarvis_threads if k[0] == conv and k[-1] != epoch]:
                            self.jarvis_threads.pop(old, None)   # a thread of an earlier membership is never continued
                    try:   # JARVIS keeps one thread per chat channel (optional_pages.JH_CHANNEL): this chat's own
                        from ..optional_pages import JH_CHANNEL
                        channel_token = JH_CHANNEL.set(channel)
                    except Exception:  # noqa: BLE001 - the chat on its own (tests): no channel
                        JH_CHANNEL, channel_token = None, None
                    result = await conversation.async_converse(
                        self.hass, msg["text"], self.jarvis_threads.get(thread), Context(user_id=asker),
                        agent_id=agent)
                    if JH_CHANNEL is not None:
                        JH_CHANNEL.reset(channel_token)
                    self.jarvis_threads[thread] = result.conversation_id
                    answer = ((result.response.speech or {}).get("plain") or {}).get("speech") or ""
            except Exception as exc:  # JARVIS being slow or down must not break the chat
                _LOGGER.warning("Household Chat: JARVIS did not answer: %s", type(exc).__name__)
            finally:
                done.set()
                await typing
            answer = answer.strip() or "I could not answer that just now."
            reply = await self._db(self.store.add, conv, JARVIS, "text", answer[:MAX_TEXT], None, None,
                                   msg["id"] if conv == GROUP else None)
            self.emit(members, {"type": "message", "conv": conv, "message": reply})
            for other in members:
                if self.connected(other):
                    await self._mark(conv, members, other, delivered=reply["id"])
            await self.push(conv, JARVIS, reply, members)

    async def read(self, user: str, conv: str, up_to: int) -> None:
        members = await self.access(user, conv)
        if await self._mark(conv, members, user, delivered=up_to, read=up_to):
            if not (await self._db(self.store.unread_lines, conv, user, 1))["total"]:
                for service in self.phones(user):   # read here: the notification on the phone goes away, as in WhatsApp
                    self.hass.async_create_task(self._notify(
                        service, {"message": "clear_notification", "data": {"tag": self.tag(conv)}}))

    async def typing(self, user: str, conv: str) -> None:
        members = await self.access(user, conv, send=True)
        self.emit(members - {user}, {"type": "typing", "conv": conv, "user": user})

    async def delete(self, user: str, message_id: int) -> dict:
        row = await self._db(self.store.message, message_id)
        if not row:
            raise ChatError("not_found", "Message not found")
        members = await self.access(user, row["conv"])
        msg = await self._db(self.store.delete, message_id, user)
        if not msg:
            raise ChatError("not_allowed", "Only your own messages can be deleted")
        members = await self._db(self.store.readers, row["conv"], message_id, members)   # review round 5: only who can see it
        self.emit(members, {"type": "update", "conv": msg["conv"], "message": msg})
        return msg

    async def edit(self, user: str, message_id: int, text: str) -> dict:
        row = await self._db(self.store.message, message_id)
        if not row:
            raise ChatError("not_found", "Message not found")
        members = await self.access(user, row["conv"])
        msg = await self._db(self.store.edit, message_id, user, (text or "").strip()[:MAX_TEXT])
        if not msg:
            raise ChatError("not_allowed", "Only your own messages can be edited, within 15 minutes")
        # review round 5: a member who joined later (or cleared, or hid it) never receives the edited text or photo
        members = await self._db(self.store.readers, row["conv"], message_id, members)
        self.emit(members, {"type": "update", "conv": msg["conv"], "message": msg})
        return msg

    async def react(self, user: str, message_id: int, emoji: str | None) -> dict:
        if emoji is not None and emoji not in REACTIONS:
            raise ChatError("bad_reaction", "Unknown reaction")
        row = await self._db(self.store.message, message_id)
        if not row:
            raise ChatError("not_found", "Message not found")
        members = await self.access(user, row["conv"])
        reactions = await self._db(self.store.react, message_id, user, emoji)
        if reactions is None:
            raise ChatError("not_allowed", "This message cannot get a reaction")
        self.emit(members, {"type": "reaction", "conv": row["conv"], "message_id": message_id, "reactions": reactions})
        return reactions

    async def hide(self, user: str, message_id: int) -> None:
        row = await self._db(self.store.message, message_id)
        if not row:
            raise ChatError("not_found", "Message not found")
        await self.access(user, row["conv"])
        await self._db(self.store.hide, message_id, user)
        self.emit({user}, {"type": "hidden", "conv": row["conv"], "message_id": message_id})

    async def clear(self, user: str, conv: str) -> None:
        await self.access(user, conv)
        await self._db(self.store.clear, conv, user)
        self.emit({user}, {"type": "cleared", "conv": conv})

    async def mute(self, user: str, conv: str, muted: bool) -> None:
        await self.access(user, conv)
        await self._db(self.store.mute, conv, user, muted)
        self.emit({user}, {"type": "muted", "conv": conv, "muted": muted})

    # ---- phone
    def phones(self, user: str) -> list[str]:
        """notify services of the Home Assistant app(s) this user signed in to. jh-phone-exact (step 159, review round 17 #3):
        only a service that reaches exactly that app (jh_phone_service); a name that several apps share gets no push (fail
        closed), so a private message never lands on another login's phone."""
        out = []
        entries = list(self.hass.config_entries.async_entries("mobile_app"))
        targets = _jh_phone_targets(self.hass)
        for entry in entries:
            if entry.data.get("user_id") == user:
                service = jh_phone_service(self.hass, entry, entries, targets)
                if service and service not in out:
                    out.append(service)
        return out

    @staticmethod
    def tag(conv: str) -> str:
        return "household-chat-" + slugify(conv)

    @staticmethod
    def line(m: dict, names: dict, group: bool) -> str:
        text = ("\U0001F4F7 " + (m["text"] or "Photo")) if m["photo"] else m["text"]
        text = text if len(text) <= 120 else text[:119] + "\u2026"
        return "%s: %s" % (names.get(m["sender"], "?"), text) if group else text

    async def push(self, conv: str, sender: str, msg: dict, recipients: set[str]) -> None:
        """New message: each receiver's phone(s) get the chat's notification with its unread messages stacked."""
        if not recipients:
            return
        muted = await self._db(self.store.muted_users, conv)
        names = {p["id"]: p["name"] for p in await self.people()}
        names[JARVIS] = "JARVIS"
        group = conv == GROUP
        nudge = msg["kind"] == "nudge"
        title = (self.hass.config.location_name or "Home") if group else names.get(sender, "?")
        url = "/%s?c=%s" % (PANEL_PATH, quote(conv, safe=""))
        base = {"url": url, "clickAction": url, "tag": self.tag(conv), "group": "household-chat", "channel": "Chat",
                "importance": "high", "priority": "high", "ttl": 0,
                "push": {"interruption-level": "time-sensitive", "sound": "default"}}
        for user in recipients:
            if user in muted or (not nudge and self.watching(user, conv)):
                continue
            phones = self.phones(user)
            if not phones:
                continue
            if nudge:
                head, body = title, "\U0001F44B %s sent you a nudge" % names.get(sender, "?")
                data = dict(base, channel="Chat nudge", vibrationPattern=NUDGE_VIBRATION, notification_icon="mdi:vibrate",
                            tag="household-chat-nudge-" + slugify(conv),
                            push={"interruption-level": "time-sensitive",
                                  "sound": {"name": "default", "critical": 0, "volume": 1.0}})
            else:
                unread = await self._db(self.store.unread_lines, conv, user)
                lines = [self.line(m, names, group) for m in unread["lines"]] or [self.line(msg, names, group)]
                head = title if unread["total"] <= 1 else "%s (%d)" % (title, unread["total"])
                body, data = "\n".join(lines)[:700], dict(base)
            for service in phones:
                self.hass.async_create_task(self._notify(service, {"title": head, "message": body, "data": data},
                                                         conv, user, msg["id"]))

    async def _notify(self, service: str, payload: dict, conv: str | None = None, user: str | None = None,
                      message_id: int | None = None) -> None:
        try:
            await self.hass.services.async_call("notify", service, payload, blocking=True)
        except Exception as exc:  # a phone that is gone must not break sending
            _LOGGER.warning("Household Chat: phone notification via %s failed: %s", service, type(exc).__name__)
            return
        if conv and user and message_id and self.reachable(user):
            # the phone is connected, so the notification is on it: two grey ticks, as in WhatsApp. A phone in flight mode
            # gets them when it comes back (deliver_all)
            await self._mark(conv, await self.members(conv), user, delivered=message_id)


# ---- websocket
def _command(schema: dict):
    def wrap(fn):
        @websocket_api.websocket_command(schema)
        @websocket_api.async_response
        async def run(hass: HomeAssistant, connection, msg):
            hub: ChatHub | None = hass.data.get(DATA)
            if hub is None:
                connection.send_error(msg["id"], "not_loaded", "Household Chat is not loaded")
                return
            try:
                result = await fn(hub, connection.user.id, msg)
            except ChatError as exc:
                connection.send_error(msg["id"], exc.code, str(exc))
                return
            connection.send_result(msg["id"], result)
        return run
    return wrap


@_command({vol.Required("type"): "household_chat/bootstrap"})
async def ws_bootstrap(hub, user, msg):
    return await hub.bootstrap(user)


@_command({vol.Required("type"): "household_chat/history", vol.Required("conv"): str,
           vol.Optional("before"): vol.Any(None, vol.All(int, vol.Range(min=1)))})
async def ws_history(hub, user, msg):
    return await hub.history(user, msg["conv"], msg.get("before"))


@_command({vol.Required("type"): "household_chat/send", vol.Required("conv"): str,
           vol.Optional("text", default=""): vol.All(str, vol.Length(max=MAX_TEXT * 2)),
           vol.Optional("photo"): vol.Any(None, str), vol.Optional("reply_to"): vol.Any(None, int)})
async def ws_send(hub, user, msg):
    return await hub.send(user, msg["conv"], msg["text"], msg.get("photo"), reply_to=msg.get("reply_to"))


@_command({vol.Required("type"): "household_chat/edit", vol.Required("message_id"): int,
           vol.Required("text"): vol.All(str, vol.Length(max=MAX_TEXT * 2))})
async def ws_edit(hub, user, msg):
    return await hub.edit(user, msg["message_id"], msg["text"])


@_command({vol.Required("type"): "household_chat/react", vol.Required("message_id"): int,
           vol.Optional("emoji"): vol.Any(None, str)})
async def ws_react(hub, user, msg):
    return await hub.react(user, msg["message_id"], msg.get("emoji"))


@_command({vol.Required("type"): "household_chat/hide", vol.Required("message_id"): int})
async def ws_hide(hub, user, msg):
    await hub.hide(user, msg["message_id"])


@_command({vol.Required("type"): "household_chat/nudge", vol.Required("conv"): str})
async def ws_nudge(hub, user, msg):
    return await hub.send(user, msg["conv"], "", None, kind="nudge")


@_command({vol.Required("type"): "household_chat/read", vol.Required("conv"): str,
           vol.Required("up_to"): vol.All(int, vol.Range(min=0))})
async def ws_read(hub, user, msg):
    await hub.read(user, msg["conv"], msg["up_to"])


@_command({vol.Required("type"): "household_chat/typing", vol.Required("conv"): str})
async def ws_typing(hub, user, msg):
    await hub.typing(user, msg["conv"])


@_command({vol.Required("type"): "household_chat/delete", vol.Required("message_id"): int})
async def ws_delete(hub, user, msg):
    return await hub.delete(user, msg["message_id"])


@_command({vol.Required("type"): "household_chat/clear", vol.Required("conv"): str})
async def ws_clear(hub, user, msg):
    await hub.clear(user, msg["conv"])


@_command({vol.Required("type"): "household_chat/mute", vol.Required("conv"): str, vol.Required("muted"): bool})
async def ws_mute(hub, user, msg):
    await hub.mute(user, msg["conv"], msg["muted"])


@_command({vol.Required("type"): "household_chat/settings", vol.Required("hide_online"): bool})
async def ws_settings(hub, user, msg):
    await hub.set_hide_online(user, msg["hide_online"])


# ---- phones and tablets per login (read-only, for the People and My phone pages)
def _apps_by_user(hass: HomeAssistant) -> dict[str, list[dict]]:
    """Login (user id) -> the Home Assistant apps signed in with it: one entry per mobile_app device. The owner is the
    login the app was set up with (config entry data user_id), not the trackers linked to a person, so a tablet signed in
    as someone is theirs without its location counting for their home/away. Only names, models and entity ids go out:
    never the webhook id, push token, cloudhook URL or anything else from the entry data."""
    ents, devs = er.async_get(hass), dr.async_get(hass)
    out: dict[str, list[dict]] = {}
    for entry in hass.config_entries.async_entries("mobile_app"):
        uid = entry.data.get("user_id")
        if not uid or entry.disabled_by:
            continue
        for device in dr.async_entries_for_config_entry(devs, entry.entry_id):
            tracker, sensors = None, []
            for ent in er.async_entries_for_device(ents, device.id, include_disabled_entities=True):
                if ent.domain == "device_tracker":
                    tracker = tracker or ent.entity_id
                elif ent.domain in ("sensor", "binary_sensor"):
                    sensors.append(ent.entity_id)
            out.setdefault(uid, []).append({
                "device_id": device.id,
                "name": device.name_by_user or device.name or str(entry.data.get("device_name") or "?"),
                "model": device.model, "manufacturer": device.manufacturer,
                "tracker": tracker, "entities": sorted(sensors)})
    for apps in out.values():
        apps.sort(key=lambda d: d["name"].casefold())
    return out


async def household_devices(hass: HomeAssistant, user) -> dict:
    """Every person with a login, and every login with apps but no person, each with the apps signed in with that login.
    An admin gets everyone; anybody else gets only their own login."""
    apps = _apps_by_user(hass)
    users = {u.id: u for u in await hass.auth.async_get_users() if u.is_active and not u.system_generated}
    people, seen = [], set()
    for st in hass.states.async_all("person"):
        uid = st.attributes.get("user_id")
        if not uid or uid in seen or uid not in users:
            continue
        seen.add(uid)
        people.append({"person": st.entity_id, "user_id": uid, "name": st.name or users[uid].name or "?",
                       "devices": apps.get(uid, [])})
    for uid, devices in apps.items():
        if uid not in seen and uid in users:
            people.append({"person": None, "user_id": uid, "name": users[uid].name or "?", "devices": devices})
    if not user.is_admin:
        people = [p for p in people if p["user_id"] == user.id]
    people.sort(key=lambda p: p["name"].casefold())
    return {"people": people}


@websocket_api.websocket_command({vol.Required("type"): "household_chat/devices"})
@websocket_api.async_response
async def ws_devices(hass: HomeAssistant, connection, msg):
    """Read-only: who is signed in on which phone or tablet (see household_devices). Needs no chat and reads no chat."""
    connection.send_result(msg["id"], await household_devices(hass, connection.user))


@websocket_api.websocket_command({vol.Required("type"): "household_chat/subscribe"})
@callback
def ws_subscribe(hass: HomeAssistant, connection, msg):
    """Live updates for this user's own chats, as long as the Chat page is open."""
    hub: ChatHub | None = hass.data.get(DATA)
    if hub is None:
        connection.send_error(msg["id"], "not_loaded", "Household Chat is not loaded")
        return
    user = connection.user.id
    key = (id(connection), msg["id"])

    @callback
    def forward(members, payload):
        if user in members:
            connection.send_message(websocket_api.event_message(msg["id"], payload))

    unsub = async_dispatcher_connect(hass, SIGNAL, forward)
    first = not hub.online(user)
    hub.pages[key] = user
    hub.views[key[0]] = (user, None, True)
    if first:
        hass.async_create_task(hub.page_change(user, True))

    @callback
    def stop():
        unsub()
        hub.pages.pop(key, None)
        if not any(k[0] == key[0] for k in hub.pages):
            hub.views.pop(key[0], None)
        if not hub.online(user):
            hass.async_create_task(hub.page_change(user, False))

    connection.subscriptions[msg["id"]] = stop
    connection.send_result(msg["id"])


@websocket_api.websocket_command({vol.Required("type"): "household_chat/view", vol.Optional("conv"): vol.Any(None, str),
                                  vol.Optional("active", default=True): bool})
@callback
def ws_view(hass: HomeAssistant, connection, msg):
    """Which chat this open Chat page shows (None: none, or the page is hidden) and whether the page is in use (visible
    and touched lately). No phone push for a chat someone is looking at; only a page in use makes someone online."""
    hub: ChatHub | None = hass.data.get(DATA)
    user = connection.user.id
    if hub is not None and any(k[0] == id(connection) for k in hub.pages):
        was = hub.online(user)
        hub.views[id(connection)] = (user, msg.get("conv") if msg["active"] else None, msg["active"])
        if was != hub.online(user):
            hass.async_create_task(hub.page_change(user, not was))
    connection.send_result(msg["id"])


COMMANDS = (ws_bootstrap, ws_history, ws_send, ws_edit, ws_react, ws_hide, ws_nudge, ws_read, ws_typing, ws_delete,
            ws_clear, ws_mute, ws_settings, ws_subscribe, ws_view, ws_devices)


# ---- photos
class UploadView(HomeAssistantView):
    url = "/api/household_chat/upload"
    name = "api:household_chat:upload"
    requires_auth = True

    async def post(self, request: web.Request) -> web.Response:
        hass = request.app["hass"]
        hub: ChatHub | None = hass.data.get(DATA)
        if hub is None:
            return self.json_message("Household Chat is not loaded", HTTPStatus.NOT_FOUND)
        user = request[KEY_HASS_USER]
        try:
            await hub.access(user.id, request.query.get("conv", ""), send=True)
        except ChatError:
            return self.json_message("Not allowed", HTTPStatus.FORBIDDEN)
        if (request.content_length or 0) > MAX_PHOTO:
            return self.json_message("Photo too large", HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
        data = bytearray()   # a photo arrives in pieces: read them all (one read() returns only what has arrived)
        async for chunk in request.content.iter_chunked(65536):
            data += chunk
            if len(data) > MAX_PHOTO:
                return self.json_message("Photo too large", HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
        data = bytes(data)
        mime = sniff(data)
        if not mime:
            return self.json_message("Not a photo", HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
        media_id = await hass.async_add_executor_job(hub.store.add_media, request.query["conv"], user.id, mime, data)
        return self.json({"id": media_id})


class MediaView(HomeAssistantView):
    url = "/api/household_chat/media/{media_id}"
    name = "api:household_chat:media"
    requires_auth = True

    async def get(self, request: web.Request, media_id: str) -> web.StreamResponse:
        hass = request.app["hass"]
        hub: ChatHub | None = hass.data.get(DATA)
        if hub is None or not MEDIA_RE.match(media_id):
            raise web.HTTPNotFound
        row = await hass.async_add_executor_job(hub.store.media, media_id)
        user = request[KEY_HASS_USER].id
        if not row or (row["msg"] is None and row["owner"] != user):
            raise web.HTTPNotFound
        try:
            await hub.access(user, row["conv"])
        except ChatError as exc:
            raise web.HTTPNotFound from exc
        if row["msg"] is not None and row["owner"] != user and user not in await hass.async_add_executor_job(
                hub.store.readers, row["conv"], row["msg"], {user}):   # review round 5: from before their join, or hidden
            raise web.HTTPNotFound
        path = hub.store.media_path(media_id, row["mime"])
        if not await hass.async_add_executor_job(Path(path).is_file):
            raise web.HTTPNotFound
        return web.FileResponse(path, headers={"Content-Type": row["mime"], "Cache-Control": "private, max-age=604800"})


# ---- start and stop (called by JARVIS's optional_pages.py)
async def async_start(hass: HomeAssistant, store_dir: str, module_url: str, picture_url: str = "") -> None:
    """Starts the chat: its store in store_dir, live updates, the commands and photo views (once per run) and the Chat page
    in the sidebar for every login. Safe to call again: a running chat is stopped first."""
    async_stop(hass)
    store = ChatStore(store_dir)
    await hass.async_add_executor_job(store.setup)
    async_stop(hass)   # review round 5: a start that ran meanwhile is cleaned up before this hub takes over
    hub = ChatHub(hass, store)
    hub.picture = picture_url

    @callback
    def phone_sensor(event_data) -> bool:   # Home Assistant passes the event data to an event filter
        eid = event_data.get("entity_id", "")
        # review round 8: any binary sensor; phone_changed keeps only a phone's in-use sensor, found by its registry unique_id
        # (an entity_id in the phone's own language, or renamed, is followed too)
        return eid.startswith("binary_sensor.")

    @callback
    def phone_changed(event: Event) -> None:
        old, new = event.data.get("old_state"), event.data.get("new_state")
        if old is not None and new is not None and old.state == new.state:
            return
        user = hub.owner_of_sensor(event.data["entity_id"])
        if user:
            hass.async_create_task(hub.announce(user))

    hub.unsubs.append(hass.bus.async_listen(EVENT_STATE_CHANGED, phone_changed, event_filter=phone_sensor))
    hub.unsubs.append(async_track_time_interval(hass, hub.recheck, timedelta(minutes=1)))
    hass.data[DATA] = hub
    if not hass.data.get(REGISTERED):
        hass.data[REGISTERED] = True
        hass.http.register_view(UploadView())
        hass.http.register_view(MediaView())
        for command in COMMANDS:
            websocket_api.async_register_command(hass, command)
    await panel_custom.async_register_panel(
        hass, frontend_url_path=PANEL_PATH, webcomponent_name="household-chat-panel", sidebar_title="Chat",
        sidebar_icon="mdi:chat", module_url=module_url, require_admin=False, config={})
    hass.data[PANEL_OWNED] = True


@callback
def async_stop(hass: HomeAssistant) -> None:
    """Stops the chat: the Chat page leaves the sidebar, live updates stop, the commands answer "not loaded". The stored
    chats stay on disk."""
    hub: ChatHub | None = hass.data.pop(DATA, None)
    if hub is not None:
        for unsub in hub.unsubs:
            try:
                unsub()
            except Exception:  # noqa: BLE001 - already gone
                pass
        hub.unsubs = []
    # review round 2: only the Chat page JARVIS registered; the old Household Chat integration's page (same path) stays
    if hass.data.pop(PANEL_OWNED, False):
        panels = hass.data.get(getattr(frontend, "DATA_PANELS", "frontend_panels")) or {}
        if PANEL_PATH in panels:
            frontend.async_remove_panel(hass, PANEL_PATH)


def running(hass: HomeAssistant) -> bool:
    return hass.data.get(DATA) is not None
