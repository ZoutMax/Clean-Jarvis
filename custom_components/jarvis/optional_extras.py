"""Clean Jarvis optional extras (step 128): Home overview, Saved history views, Answer watcher, Chat history.

Each is a JARVIS Settings switch in Settings > Optional pages, off by default (jh_opt_overview, jh_opt_history, jh_opt_watch,
jh_opt_chatlog). The pages are made by the panel's admin-only Set up button through Home Assistant's own dashboard commands
(see optional_pages.py); this module is the server side:

  * Home overview: a dashboard built from Home Assistant's own Home strategies, where cameras are in sections only admins see
    (step 158: or everyone, the setting jh_overview_cams_all, off by default) and each phone or tablet in a section only admins and the login it is signed in with see. JARVIS keeps it current: every
    5 minutes and shortly after an area, device or entity changes, while the switch is on and Set up made or adopted the
    dashboard. Only views JARVIS wrote, and that nobody edited since, are rewritten; a view edited in Home Assistant is the
    owner's from then on, a deleted one stays deleted, added views are never touched. Its memory: <config>/.storage.
  * Answer watcher: every JARVIS answer is checked read-only (slow, unsure phrases, another language than the question, an
    action promised while nothing was switched, a gendered pronoun about a person, no reply). Flagged turns are kept in an
    admin-only log for jh_watch_days days (1-90, default 14). The question and answer text is only ever sent to
    administrators (the command is admin-only), and the optional notification (jh_watch_notify) carries no chat text.
  * Chat history: past JARVIS conversations of all users from JARVIS's own conversation store, for administrators only, with
    a filter by login and single entries that can be deleted (JARVIS's long-term memory is not touched).
  * Saved history views need nothing here: each login keeps its own views in its Home Assistant user data.
Everything is read live from Home Assistant; nothing names one home.
"""
from __future__ import annotations

import copy
import json
import logging
import re
import time
from datetime import timedelta

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

_LOGGER = logging.getLogger(__name__)

OVERVIEW_EVERY = timedelta(minutes=5)
OVERVIEW_SETTLE = 30            # seconds after a registry change before the overview is rebuilt
WATCH_MIN, WATCH_MAX, WATCH_DEFAULT = 1, 90, 14
WATCH_MAX_ROWS = 2000
SLOW_S = 25
_REG = "jh_optional_extras_registered"
_STATE = "jh_optional_extras"


def _opt(hass, key, default=None):
    from .optional_pages import _opt as opt
    return opt(hass, key, default)


def enabled(hass, feature: str) -> bool:
    from .optional_pages import enabled as en
    return en(hass, feature)


def _state(hass) -> dict:
    return hass.data.setdefault(_STATE, {})


# ============================================================================ Home overview
SHARED_GROUPS = (("Lights", ("light",)), ("Climate", ("climate", "humidifier", "fan", "water_heater")),
                 ("Media players", ("media_player",)), ("Scenes", ("scene",)))
GROUPED = {d for _, types in SHARED_GROUPS for d in types}
SKIP_DOMAINS = {"automation", "script", "device_tracker", "person", "zone", "event", "notify", "tag", "update", "sun",
                "persistent_notification", "configurator", "geo_location", "ai_task", "todo", "conversation", "stt",
                "tts", "wake_word", "camera"}
DEFAULT_SHORTCUTS = ("weather", "climate", "light", "media_players", "energy", "maintenance", "security")


def user_condition(users):
    return [{"condition": "user", "users": sorted(users)}]


class Home:
    """What exists in Home Assistant now, as plain data (so the view builder can be tested without Home Assistant)."""

    def __init__(self, states, reg, devs, areas, admins, phones, admin_only=(), shortcuts=None, cams_all=False, health=()):
        self.states, self.reg, self.devs, self.areas = states, reg, devs, areas
        # jh-r25-health (step 167, review round 25 #7): health / wearable entities (agent._jh_health_ids, the rule of
        # _jh_private_ids) are private like a phone: a device that carries one (a Withings mat, a Garmin, an Oura) is shown to
        # administrators only (its owner is not known for sure: fails closed), never in a shared area or Devices section; a
        # health entity on a phone stays with the phone's rule (administrators and the phone's own login)
        self.health = set(health or ())
        self.admins, self.phones, self.admin_only, self.shortcuts = set(admins), dict(phones), set(admin_only), shortcuts
        self.cams_all = cams_all is True   # jh-overview-cams (step 158, C4): the Cameras sections for everyone, not only admins
        if not self.admins:   # never write sections that nobody (or everybody) would see by mistake
            raise ValueError("no active administrator")
        # jh-r22-cams (step 164, review round 22 #6): a camera in the registry is private whether or not it has a state yet (an
        # integration that is retrying at boot writes the state later, and no registry event follows)
        self.cams = sorted({e for e in self.states if e.startswith("camera.") and self.visible(e)}
                           | {e for e in self.reg if e.startswith("camera.") and self.reg_visible(e)})

    def cams_of(self, dev):
        """The camera entities that sit on this device."""
        return sorted(c for c in self.cams if (self.reg.get(c) or {}).get("device_id") == dev) if dev else []

    def reg_visible(self, eid):
        """jh-r22-cams: an entity in the registry that is not hidden or disabled (a state is not needed)."""
        r = self.reg.get(eid)
        return r is not None and not r.get("hidden_by") and not r.get("disabled_by")

    def visible(self, eid):
        r = self.reg.get(eid) or {}
        return eid in self.states and not r.get("hidden_by") and not r.get("disabled_by")

    def area_of(self, eid):
        r = self.reg.get(eid) or {}
        return r.get("area_id") or (self.devs.get(r.get("device_id")) or {}).get("area_id")

    def device_name(self, dev_id):
        d = self.devs.get(dev_id) or {}
        return d.get("name_by_user") or d.get("name") or dev_id

    def cam_section(self, cams) -> dict:
        """The Cameras grid: only administrators see it, unless the setting jh_overview_cams_all says everyone."""
        sec = {"type": "grid", "cards": [{"type": "heading", "heading": "Cameras", "icon": "mdi:cctv"}] + [
            {"type": "picture-entity", "entity": c, "show_state": False, "show_name": False,
             "grid_options": {"columns": 6, "rows": 2}} for c in cams]}
        if not self.cams_all:
            sec["visibility"] = user_condition(self.admins)
        return sec

    def viewers(self, dev_id):
        owner = self.phones.get(dev_id)
        return self.admins | ({owner} if owner else set())

    def in_area(self, area_id):
        return [e for e in sorted(self.states) if self.visible(e) and self.area_of(e) == area_id]

    def health_devs(self):
        """jh-r25-health: the devices (not phones) that carry a health entity."""
        return {(self.reg.get(e) or {}).get("device_id") for e in self.health} - {None} - set(self.phones)

    def health_loose(self):
        """jh-r25-health: health entities with no device."""
        return {e for e in self.health if not (self.reg.get(e) or {}).get("device_id")}

    def health_in(self, area_id):
        """jh-r25-health: (devices, loose entities) with health data in this area, read from the registries; area_id None:
        the ones with no area (the Devices page)."""
        devs = sorted(d for d in self.health_devs()
                      if ((self.devs.get(d) or {}).get("area_id") or None) == area_id
                      or any(r.get("device_id") == d and (self.area_of(e) or None) == area_id for e, r in self.reg.items()))
        loose = sorted(e for e in self.health_loose() if (self.area_of(e) or None) == area_id)
        return devs, loose

    def private_in(self, area_id):
        """jh-r22-cams (step 164, review round 22 #6): what is private in an area is read from the registries, not from the
        current states: a camera or a phone's entity with no state yet still counts, and a phone device whose own area is this
        one counts too."""
        ents = set(self.in_area(area_id)) | {e for e in self.reg if self.reg_visible(e) and self.area_of(e) == area_id}
        phones = sorted(({(self.reg.get(e) or {}).get("device_id") for e in ents} & set(self.phones))
                        | {d for d in self.phones if (self.devs.get(d) or {}).get("area_id") == area_id})
        return phones, sorted(e for e in ents if e in self.cams)


def area_view(home: Home, area: dict) -> dict:
    """One area page: Home Assistant's own Home area page when nothing private is there, else the same parts built from
    section strategies, with cameras for administrators and each phone or tablet for administrators and its own login."""
    aid, name = area["area_id"], area["name"]
    phones, cams = home.private_in(aid)
    hdevs, hloose = home.health_in(aid)   # jh-r25-health (step 167, review round 25 #7)
    base = {"title": name, "path": "areas-" + aid, "subview": True}
    if not phones and not cams and not hdevs and not hloose:
        return dict(base, strategy={"type": "home-area", "area": aid})
    shared = [e for e in home.in_area(aid)
              if (home.reg.get(e) or {}).get("device_id") not in home.phones and e not in cams
              and (home.reg.get(e) or {}).get("device_id") not in hdevs and e not in home.health
              and not (home.reg.get(e) or {}).get("entity_category") and e.split(".")[0] not in SKIP_DOMAINS]
    exclude = {}
    if phones or hdevs:
        exclude["device_id"] = sorted(set(phones) | set(hdevs))
    if cams or hloose:
        exclude["entity_id"] = sorted(set(cams) | set(hloose))
    target = dict({"area_id": aid}, **({"exclude": exclude} if exclude else {}))
    sections = []
    for title, types in SHARED_GROUPS:
        if any(e.split(".")[0] in types for e in shared):
            sections.append({"strategy": {"type": "entities", "title": title, "target": target, "entity_types": list(types)}})
    by_dev, loose = {}, []
    for e in shared:
        if e.split(".")[0] in GROUPED:
            continue
        dev = (home.reg.get(e) or {}).get("device_id")
        (by_dev.setdefault(dev, []) if dev else loose).append(e)
    for dev in sorted(by_dev, key=lambda d: home.device_name(d).lower()):
        grouped = sorted(e for e, r in home.reg.items() if r.get("device_id") == dev and e.split(".")[0] in GROUPED)
        grouped += home.cams_of(dev)   # review round 2: a camera on the same device stays in the admins' section only
        strat = {"type": "device", "device_id": dev}
        if grouped:
            strat["exclude_entities"] = sorted(set(grouped))
        sections.append({"strategy": strat})
    if loose:
        sections.append({"strategy": {"type": "entities", "title": "Others", "target": {"entity_id": loose},
                                      "entity_types": sorted({e.split(".")[0] for e in loose})}})
    if cams:
        sections.append(home.cam_section(cams))
    for dev in sorted(phones, key=lambda d: home.device_name(d).lower()):
        sections.append({"strategy": {"type": "device", "device_id": dev}, "visibility": user_condition(home.viewers(dev))})
    sections += health_sections(home, hdevs, hloose)
    badges = [{"type": "entity", "entity": area[k], "color": color}
              for k, color in (("temperature_entity_id", "red"), ("humidity_entity_id", "indigo")) if area.get(k)]
    view = dict(base, type="sections", header={"badges_position": "bottom"}, max_columns=min(3, max(2, len(sections))),
                sections=sections)
    if badges:
        view["badges"] = badges
    return view


def health_sections(home: Home, devs, loose) -> list:
    """jh-r25-health (step 167, review round 25 #7): a health device or loose health entity only for administrators."""
    out = [{"strategy": {"type": "device", "device_id": d}, "visibility": user_condition(home.admins)}
           for d in sorted(devs, key=lambda d: home.device_name(d).lower())]
    if loose:
        out.append({"strategy": {"type": "entities", "title": "Health", "target": {"entity_id": sorted(loose)},
                                 "entity_types": sorted({e.split(".")[0] for e in loose})},
                    "visibility": user_condition(home.admins)})
    return out


def devices_view(home: Home) -> dict:
    """The Devices page (devices with no area). Home Assistant's own strategy is used when nothing private is among them;
    otherwise the same parts are built from section strategies, with each phone or tablet only for administrators and its
    own login and cameras only for administrators (review 2026-11-15: app devices normally have no area)."""
    base = {"title": "Devices", "path": "other-devices", "subview": True, "icon": "mdi:devices"}
    loose_phones = sorted(d for d in home.phones if not (home.devs.get(d) or {}).get("area_id"))
    loose_cams = [c for c in home.cams if not home.area_of(c)]
    hdevs, hloose = home.health_in(None)   # jh-r25-health (step 167, review round 25 #7)
    if not loose_phones and not loose_cams and not hdevs and not hloose:
        return dict(base, strategy={"type": "home-other-devices"})
    shared_devs, loose = set(), []
    for e in sorted(home.states):
        if not home.visible(e) or home.area_of(e) or e in home.cams or e.split(".")[0] in SKIP_DOMAINS:
            continue
        r = home.reg.get(e) or {}
        if r.get("entity_category"):
            continue
        dev = r.get("device_id")
        if dev in home.phones or dev in hdevs or e in home.health:   # jh-r25-health: in the admins' sections below
            continue
        if dev:
            shared_devs.add(dev)
        else:
            loose.append(e)
    sections = [{"strategy": dict({"type": "device", "device_id": d}, **({"exclude_entities": home.cams_of(d)} if home.cams_of(d) else {}))}
                for d in sorted(shared_devs, key=lambda d: home.device_name(d).lower())]
    if loose:
        sections.append({"strategy": {"type": "entities", "title": "Others", "target": {"entity_id": loose},
                                      "entity_types": sorted({e.split(".")[0] for e in loose})}})
    if loose_cams:
        sections.append(home.cam_section(loose_cams))
    for dev in sorted(loose_phones, key=lambda d: home.device_name(d).lower()):
        sections.append({"strategy": {"type": "device", "device_id": dev}, "visibility": user_condition(home.viewers(dev))})
    sections += health_sections(home, hdevs, hloose)
    return dict(base, type="sections", max_columns=3, sections=sections)


def admin_only_shortcut(home: Home, shortcut) -> bool:
    path = shortcut.get("path") if isinstance(shortcut, dict) else None
    if not isinstance(path, str) or not path.startswith("/"):
        return False
    return re.split(r"[/?#]", path.lstrip("/"), maxsplit=1)[0] in home.admin_only


def generate(home: Home) -> list:
    shortcuts = copy.deepcopy(home.shortcuts) if home.shortcuts is not None else [{"type": "summary", "key": k} for k in DEFAULT_SHORTCUTS]
    shortcuts = [sc for sc in shortcuts if not admin_only_shortcut(home, sc)]
    views = [{"icon": "mdi:home", "path": "overview", "strategy": {"type": "home-overview", "shortcuts": shortcuts}}]
    views += [area_view(home, a) for a in home.areas]
    views += [{"title": "Media players", "path": "media-players", "subview": True, "icon": "mdi:multimedia",
               "strategy": {"type": "home-media-players"}},
              devices_view(home)]
    return views


def merge(current: list, generated: list, mem: dict):
    """Only views JARVIS wrote and nobody changed are replaced or removed. mem: {written: {path: view}, yours: [], deleted: []}."""
    written, yours, deleted = mem.setdefault("written", {}), set(mem.get("yours", [])), set(mem.get("deleted", []))   # (marker kept)
    gen = {v["path"]: v for v in generated}
    order = [v["path"] for v in generated]
    notes, out, present = [], [], set()
    for v in current:
        p = v.get("path")
        present.add(p)
        if p in written and p not in yours:
            if v != written[p]:
                yours.add(p)
                notes.append("view %s was edited in Home Assistant and is left alone from now on" % p)
                out.append(v)
            elif p in gen:
                out.append(gen[p])
                written[p] = gen[p]
            else:
                written.pop(p)
                notes.append("view %s removed (its area is gone)" % p)
        elif p in gen and p not in written and p not in yours and v == gen[p]:
            out.append(v)
            written[p] = gen[p]
        else:
            out.append(v)
    for p in list(written):
        if p not in present:
            written.pop(p)
            deleted.add(p)
            notes.append("view %s was deleted in Home Assistant and is not added again" % p)
    for i, p in enumerate(order):
        if p in present or p in deleted or p in yours:
            continue
        before = [q for q in order[:i] if any(v.get("path") == q for v in out)]
        at = max((n for n, v in enumerate(out) if v.get("path") in before), default=-1) + 1
        out.insert(at, gen[p])
        written[p] = gen[p]
        present.add(p)
        notes.append("view %s added" % p)
    mem["yours"], mem["deleted"] = sorted(yours), sorted(deleted)
    return out, notes


def _lovelace_dashboards(hass) -> dict:
    """Home Assistant's dashboards (url path -> its config store), across the versions that keep them differently."""
    data = None
    try:
        from homeassistant.components.lovelace.const import LOVELACE_DATA
        data = hass.data.get(LOVELACE_DATA)
    except Exception:  # noqa: BLE001 - an older Home Assistant
        data = None
    if data is None:
        data = hass.data.get("lovelace")
    if isinstance(data, dict):
        return data.get("dashboards") or {}
    return getattr(data, "dashboards", None) or {}


async def read_home(hass) -> Home:
    """The live Home Assistant picture the overview is built from. Owner of a phone or tablet: the login its Home Assistant
    app is signed in with (as jarvis/user_devices)."""
    from homeassistant.helpers import area_registry as ar, device_registry as dr, entity_registry as er
    states = {s.entity_id: s for s in hass.states.async_all()}
    reg = {e.entity_id: {"device_id": e.device_id, "area_id": e.area_id, "hidden_by": e.hidden_by, "disabled_by": e.disabled_by,
                         "entity_category": e.entity_category}
           for e in er.async_get(hass).entities.values()}
    devs = {d.id: {"area_id": d.area_id, "name": d.name, "name_by_user": d.name_by_user}
            for d in dr.async_get(hass).devices.values()}
    areas = []
    for a in ar.async_get(hass).async_list_areas():
        areas.append({"area_id": a.id, "name": a.name, "temperature_entity_id": getattr(a, "temperature_entity_id", None),
                      "humidity_entity_id": getattr(a, "humidity_entity_id", None)})
    admins = {u.id for u in await hass.auth.async_get_users() if u.is_admin and u.is_active and not u.system_generated}
    phones = {}
    devreg = dr.async_get(hass)
    for entry in hass.config_entries.async_entries("mobile_app"):
        for d in dr.async_entries_for_config_entry(devreg, entry.entry_id):
            phones[d.id] = entry.data.get("user_id") or None
    admin_only = set()
    for url, store in _lovelace_dashboards(hass).items():
        cfg = getattr(store, "config", None) or {}
        if url and isinstance(cfg, dict) and cfg.get("require_admin"):
            admin_only.add(url)
    for url, panel in (hass.data.get("frontend_panels") or {}).items():
        if getattr(panel, "require_admin", False):
            admin_only.add(url)
    # jh-r25-health (step 167, review round 25 #7): the health entities by the same rule as _jh_private_ids; fails closed
    # (every sensor and binary sensor counts as health when the rule cannot be read)
    try:
        from .agent import _jh_health_ids
        health = set(_jh_health_ids(hass))
    except Exception:  # noqa: BLE001
        health = {e for e in states if e.split(".", 1)[0] in ("sensor", "binary_sensor")}
    return Home(states, reg, devs, areas, admins, phones, admin_only, cams_all=_opt(hass, "jh_overview_cams_all", False) is True,
                health=health)


def unmerge(current: list, mem: dict) -> tuple[list, list]:
    """Review round 5: the views JARVIS wrote and nobody changed, taken out again (Remove on an adopted dashboard). Returns
    (the views left, the paths taken out)."""
    written, yours = mem.get("written") or {}, set(mem.get("yours") or [])
    out, gone = [], []
    for v in current or []:
        p = v.get("path")
        if p in written and p not in yours and v == written[p]:
            gone.append(p)
            continue
        out.append(v)
    return out, gone


# jh-r18-remove (step 160, review round 18 #8): Remove on an adopted Home overview is atomic. The panel first calls
# jarvis/overview_unsync and only then clears the record (jarvis/opt_created_set); a sync that ran in between found no memory
# and put every JARVIS view back for good. Now sync and unsync share one lock (no sync can save between unsync's load and save,
# or the other way round), and unsync leaves a "removed" mark in the sync memory (it survives a restart) that stops every
# later sync of that dashboard until Set up adopts it again (forget_removed, from jarvis/opt_created_set).
def _overview_lock(hass):
    import asyncio
    st = _state(hass)
    lock = st.get("overview_lock")
    if lock is None:
        lock = st["overview_lock"] = asyncio.Lock()
    return lock


def _mem_store(hass):
    from homeassistant.helpers.storage import Store
    st = _state(hass)
    mem_store = st.get("overview_store") or Store(hass, 1, "jarvis.overview_sync")
    st["overview_store"] = mem_store
    return mem_store


async def async_overview_forget_removed(hass, url) -> None:
    """Set up adopts this dashboard (again): the "removed" mark of an earlier Remove is cleared, so the sync runs afresh."""
    if not url:
        return
    async with _overview_lock(hass):
        mem_store = _mem_store(hass)
        mem = (await mem_store.async_load()) or {}
        if (mem.get(url) or {}).get("removed"):
            mem.pop(url)
            await mem_store.async_save(mem)


async def async_overview_unsync(hass, url: str) -> list:
    """Takes the views JARVIS wrote out of this dashboard (views you changed stay) and forgets its sync memory."""
    async with _overview_lock(hass):   # jh-r18-remove
        return await _overview_unsync(hass, url)


async def _overview_unsync(hass, url: str) -> list:
    from homeassistant.helpers.storage import Store
    st = _state(hass)
    mem_store = st.get("overview_store") or Store(hass, 1, "jarvis.overview_sync")
    st["overview_store"] = mem_store
    mem = (await mem_store.async_load()) or {}
    mine = mem.get(url) or {}
    store = _lovelace_dashboards(hass).get(url)
    gone = []
    if store is not None and mine:
        try:
            cfg = await store.async_load(False)
        except Exception:  # noqa: BLE001
            cfg = None
        if isinstance(cfg, dict) and "strategy" not in cfg:
            views, gone = unmerge(cfg.get("views") or [], mine)
            if gone:
                await store.async_save(dict(cfg, views=views))
    # jh-r18-remove: not just forgotten - marked removed, so no sync of this dashboard can put the views back before (or after)
    # the panel clears the record
    mem[url] = {"removed": True}
    await mem_store.async_save(mem)
    return gone


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): "jarvis/overview_unsync", vol.Required("dashboard"): str})
@websocket_api.async_response
async def ws_overview_unsync(hass, connection, msg):
    """Administrators only (review round 5): Remove on an adopted Home overview takes JARVIS's views out of it again."""
    connection.send_result(msg["id"], {"removed": await async_overview_unsync(hass, msg["dashboard"])})


async def async_overview_sync(hass) -> list:
    """Rebuilds the Home overview views. Returns notes (empty: nothing changed). Does nothing while the switch is off, before
    Set up, or when the dashboard was deleted or turned into something else in Home Assistant."""
    if not enabled(hass, "overview"):
        return []
    if getattr(hass, "is_running", True) is False:
        return []   # jh-r22-cams (step 164, review round 22 #6): not before Home Assistant has started (states still coming in)
    async with _overview_lock(hass):   # jh-r18-remove: never between unsync's load and save, nor the other way round
        return await _overview_sync(hass)


async def _overview_sync(hass) -> list:
    if not enabled(hass, "overview"):   # read again inside the lock
        return []
    from .optional_pages import created
    url = (created(hass).get("overview") or {}).get("dashboard")
    store = _lovelace_dashboards(hass).get(url) if url else None
    if store is None:
        return []
    fresh = False
    try:
        cfg = await store.async_load(False)
    except Exception as exc:  # noqa: BLE001 - ConfigNotFound: an empty new dashboard
        cfg = {}
        fresh = type(exc).__name__ == "ConfigNotFound"
    if not isinstance(cfg, dict) or "strategy" in cfg:
        return []
    from homeassistant.helpers.storage import Store
    st = _state(hass)
    mem_store = st.get("overview_store") or Store(hass, 1, "jarvis.overview_sync")
    st["overview_store"] = mem_store
    mem = (await mem_store.async_load()) or {}
    if (mem.get(url) or {}).get("removed"):   # jh-r18-remove: Remove took the views out; only Set up brings them back
        return []
    # review round 3: a dashboard made anew starts afresh. Home Assistant gives a dashboard made again at the same address the same
    # id, so the sign is that it has never been saved (ConfigNotFound), as after REMOVE then SET UP or after deleting it in
    # Settings > Dashboards. One the owner emptied has a saved config (views: []) and keeps its memory, so the views they deleted
    # stay deleted. A different id (an older Home Assistant) also counts as new.
    marker = str((getattr(store, "config", None) or {}).get("id") or "")
    if fresh or (marker and (mem.get(url) or {}).get("marker", marker) != marker):
        mem[url] = {}
    mine = mem.setdefault(url, {})
    mine["marker"] = marker
    before = json.dumps(mine, sort_keys=True)
    try:
        home = await read_home(hass)
    except ValueError:
        return []
    views, notes = merge(cfg.get("views") or [], generate(home), mine)
    if views != (cfg.get("views") or []):
        await store.async_save(dict(cfg, views=views))
        notes.append("saved")
    if json.dumps(mine, sort_keys=True) != before:
        await mem_store.async_save(mem)
    return notes


# ============================================================================ Answer watcher
UNSURE = [r"\bI don'?t have\b", r"\bI do not have\b", r"\bno current\b", r"\bunable\b", r"\bcan'?t\b", r"\bcannot\b",
          r"\bnot available\b", r"\bunavailable\b", r"\bno data\b", r"\bI'?m not sure\b", r"\bnot sure\b", r"\bwhich one\b",
          r"\bsorry\b", r"\berror\b", r"\bfailed\b", r"\bI could not\b", r"\bcouldn'?t\b", r"\bno information\b",
          r"\bdon'?t know\b", r"\bnot configured\b", r"\bno .{0,20}sensor\b", r"\(no response\)"]
UNSURE_RE = [re.compile(p, re.I) for p in UNSURE]
PROMISE_RE = re.compile(r"\b(I'?ve|I have|I)\s+(just\s+)?(turned|switched|set|started|stopped|opened|closed|locked|unlocked|"
                        r"dimmed|brightened|activated|deactivated|enabled|disabled|paused|played|muted|changed|adjusted|raised|"
                        r"lowered)\b|^\s*done\b|\bdone,\s|\b(is|are) now (on|off|set|open|closed|locked|unlocked)\b", re.I)
PRONOUN_RE = re.compile(r"\b(he|him|his|himself|she|her|hers|herself)\b", re.I)


def watch_reasons(q: str, a: str | None, seconds: float | None, acted: bool | None, people=(), lang_of=None) -> list:
    """Why an answer looks wrong (empty: it looks fine). Read-only heuristics; a flag is a hint for the owner, never an action."""
    why = []
    if a is None or not str(a).strip():
        why.append("no reply")
    else:
        for rx in UNSURE_RE:
            m = rx.search(a)
            if m:
                why.append('says "%s"' % m.group(0))
                break
        if acted is False and PROMISE_RE.search(a):
            why.append("says an action was done but nothing was switched")
        if lang_of:
            lq, la = lang_of(q), lang_of(a)
            if lq and la and lq != la:
                why.append("answered in %s to a question in %s" % (la, lq))
        if PRONOUN_RE.search(a):
            low = (q + " " + a).lower()
            if any(n and re.search(r"\b%s\b" % re.escape(n), low) for n in people):
                why.append('gendered pronoun "%s" about a person' % PRONOUN_RE.search(a).group(0))
    if seconds is not None and seconds > SLOW_S:
        why.append("slow: %d s" % round(seconds))
    out = []
    for w in why:
        if w not in out:
            out.append(w)
    return out


def _lang(text):
    """'en', 'de', 'nl', ... or 'other' (another script); None when too short to tell."""
    if not text or len(re.findall(r"\w+", text)) < 3:
        return None
    try:
        from .local_engine import jh_text_language
        return jh_text_language(text)
    except Exception:  # noqa: BLE001
        return None


def watch_days(hass) -> int:
    try:
        d = int(_opt(hass, "jh_watch_days", WATCH_DEFAULT))
    except (TypeError, ValueError):
        return WATCH_DEFAULT
    return min(WATCH_MAX, max(WATCH_MIN, d))


def clean_watch_days(value) -> int:
    if isinstance(value, bool):
        raise ValueError("Days must be a number")
    try:
        d = int(float(value))
    except (TypeError, ValueError) as exc:
        raise ValueError("Days must be a number") from exc
    if d != float(value) or not WATCH_MIN <= d <= WATCH_MAX:
        raise ValueError("Days must be a whole number from %d to %d" % (WATCH_MIN, WATCH_MAX))
    return d


async def _watch_store(hass):
    st = _state(hass)
    if "watch" in st:
        return st["watch"]
    import asyncio
    lock = st.setdefault("watch_lock", asyncio.Lock())
    async with lock:   # review 2026-11-15: two answers flagged at once share one list
        if "watch" not in st:
            from homeassistant.helpers.storage import Store
            store = Store(hass, 1, WATCH_KEY, private=True)   # review round 20 #7: owner-only file, as assist_log
            st["watch_store"] = store
            st["watch"] = ((await store.async_load()) or {}).get("rows", [])
    return st["watch"]


async def async_note_turn(hass, question: str, answer: str | None, user_id: str = "", device_id: str = "",
                          started: float | None = None) -> list:
    """Called after every JARVIS answer (conversation.py). Checks it while the Answer watcher is on; a flagged turn is kept.
    Returns the reasons (empty: not flagged, or the watcher is off)."""
    if not enabled(hass, "watch"):
        return []
    now = time.time()
    acted = None
    try:
        from . import actuation
        if started is not None:
            acted = any(ts >= started for ts, _c, _e in list(getattr(actuation, "_JH_JOURNAL", [])))
    except Exception:  # noqa: BLE001
        acted = None
    people = [str(s.attributes.get("friendly_name") or s.entity_id.split(".", 1)[1]).split()[0].lower()
              for s in hass.states.async_all("person")]
    why = watch_reasons(question or "", answer, (now - started) if started else None, acted, people, _lang)
    if not why:
        return []
    rows = await _watch_store(hass)
    name = ""
    if user_id:
        try:
            u = await hass.auth.async_get_user(user_id)
            name = (u.name if u else "") or ""
        except Exception:  # noqa: BLE001
            name = ""
    rows.append({"ts": round(now, 1), "user_id": user_id or "", "who": name or ("voice, no login" if not user_id else "?"),
                 "source": device_id or "", "q": (question or "")[:1000], "a": (answer or "")[:2000], "why": why})
    _prune(hass, rows)
    await _watch_save(hass)
    if _opt(hass, "jh_watch_notify", False) is True:
        await _watch_notify(hass, why)
    return why


def _prune(hass, rows: list) -> bool:
    """Keeps the rows of the last jh_watch_days days (at most WATCH_MAX_ROWS). True when any row went."""
    cut = time.time() - watch_days(hass) * 86400
    keep = [r for r in rows if r.get("ts", 0) >= cut][-WATCH_MAX_ROWS:]
    gone = len(keep) != len(rows)
    rows[:] = keep
    return gone


WATCH_KEY = "jarvis.answer_watch"
WATCH_PRUNE_EVERY_H = 6


async def async_watch_prune(hass) -> bool:
    """Review round 20 #7: the retention holds whether the watcher is on or off. Called at setup and every few hours (the
    watcher's card and its CLEAR LIST button are hidden while it is off, so nothing else would prune). Saves only when a row
    went. True when any row went."""
    rows = await _watch_store(hass)
    if _prune(hass, rows):
        await _watch_save(hass)
        return True
    return False


async def async_watch_remove(hass) -> None:
    """Review round 20 #7: JARVIS removed: the flagged questions and answers go too (as assist_log's turns). The same Store
    removes the file, which also cancels its pending delayed save."""
    st = _state(hass)
    store = st.pop("watch_store", None)
    st.pop("watch", None)
    try:
        if store is None:
            from homeassistant.helpers.storage import Store
            store = Store(hass, 1, WATCH_KEY, private=True)
        await store.async_remove()
    except Exception as exc:  # noqa: BLE001
        _LOGGER.warning("JARVIS removed: answer watcher log not deleted: %s", type(exc).__name__)


async def _watch_save(hass) -> None:
    st = _state(hass)
    store = st.get("watch_store")
    if store is not None:
        store.async_delay_save(lambda: {"rows": st.get("watch", [])}, 5)


async def _watch_notify(hass, why: list) -> None:
    """A notification for administrators, without any chat text: only what kind of problem it was. At most one every
    10 minutes. It goes to JARVIS's notify service when one is set (step 158: as a private alert, by default only to the
    administrators' phones, jh_notify), else to Home Assistant's notifications."""
    st = _state(hass)
    if time.time() - st.get("watch_notified", 0) < 600:
        return
    st["watch_notified"] = time.time()
    text = "JARVIS answer watcher: an answer was flagged (%s). Details: JARVIS > Settings > Optional pages." % "; ".join(
        w.split(":")[0].split('"')[0].strip() for w in why)
    svc = str(_opt(hass, "notify_service", "") or "")
    try:
        from . import jh_notify
        # jh-r18-closed: a held alert (it may not go to a broadcast service) still shows here; the text holds no chat content
        if not (svc.startswith("notify.") and hass.services.has_service("notify", svc.split(".", 1)[1])
                and (await jh_notify.send(hass, svc, {"title": "JARVIS", "message": text})) not in ([], [jh_notify.HELD])):
            await hass.services.async_call("persistent_notification", "create",
                                           {"title": "JARVIS", "message": text, "notification_id": "jarvis_answer_watch"},
                                           blocking=False)
    except Exception as exc:  # noqa: BLE001
        _LOGGER.debug("answer watcher notification: %s", exc)


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): "jarvis/answer_watch", vol.Optional("limit", default=200): vol.All(int, vol.Range(min=1, max=2000))})
@websocket_api.async_response
async def ws_answer_watch(hass, connection, msg):
    """Administrators only: the flagged answers, newest first, with the question and answer text."""
    rows = await _watch_store(hass)
    _prune(hass, rows)
    connection.send_result(msg["id"], {"rows": list(reversed(rows))[: msg["limit"]], "days": watch_days(hass),
                                       "on": enabled(hass, "watch")})


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): "jarvis/answer_watch_clear"})
@websocket_api.async_response
async def ws_answer_watch_clear(hass, connection, msg):
    rows = await _watch_store(hass)
    rows.clear()
    await _watch_save(hass)
    connection.send_result(msg["id"], {"rows": []})


# ============================================================================ Chat history (admin)
SKIP_SOURCES = ("briefing", "sentinel", "camera", "summary")


def chat_pairs(rows: list) -> list:
    """Rows of JARVIS's conversation store (oldest first) -> question/answer pairs: a user turn and the next assistant turn of
    the same login and device. A question without an answer is kept with an empty answer."""
    out, open_q = [], {}
    for r in rows:
        key = (r.get("user_id") or "", r.get("device_id") or "")
        if r.get("role") == "user":
            if key in open_q:
                out.append(open_q.pop(key))
            open_q[key] = {"id": r["id"], "ids": [r["id"]], "t": r.get("timestamp", ""), "user_id": key[0], "source": key[1],
                           "q": r.get("content", ""), "a": ""}
        elif r.get("role") == "assistant" and key in open_q:
            p = open_q.pop(key)
            p["a"], p["ids"] = r.get("content", ""), p["ids"] + [r["id"]]
            out.append(p)
    out.extend(open_q.values())
    out.sort(key=lambda p: p["id"])
    return out


def _chat_rows(who: str, before: int | None, limit: int) -> list:
    from . import database
    where, args = ["device_id NOT IN (%s)" % ",".join("?" * len(SKIP_SOURCES))], list(SKIP_SOURCES)
    if who not in ("*", None):
        where.append("user_id = ?")
        args.append(who)
    if before:
        where.append("id < ?")
        args.append(int(before))
    with database._connect() as conn:
        rows = conn.execute("SELECT id, timestamp, device_id, role, content, user_id FROM conversations WHERE "
                            + " AND ".join(where) + " ORDER BY id DESC LIMIT ?", (*args, limit * 2 + 2)).fetchall()
    return [dict(r) for r in reversed(rows)]


def _chat_answers(pairs: list) -> None:
    """Review round 3: a question whose answer fell outside the page (two logins or devices talking at once) gets it from the
    store: the next row of the same login and device, when that is JARVIS's answer."""
    from . import database
    todo = [p for p in pairs if not p.get("a") and len(p.get("ids") or []) == 1]
    if not todo:
        return
    with database._connect() as conn:
        for p in todo:
            r = conn.execute("SELECT id, role, content FROM conversations WHERE id > ? AND COALESCE(user_id, '') = ? "
                             "AND COALESCE(device_id, '') = ? ORDER BY id LIMIT 1", (int(p["id"]), p["user_id"], p["source"])).fetchone()
            if r is not None and r["role"] == "assistant":
                p["a"], p["ids"] = r["content"], p["ids"] + [r["id"]]


def _chat_delete(ids: list) -> int:
    """Deletes these turns, and with each question the answer that followed it for the same login and device (review round 3:
    an answer never stays behind in JARVIS's thread when its question is deleted)."""
    from . import database
    if not ids:
        return 0
    ids = {int(i) for i in ids}
    with database._connect() as conn:
        for i in list(ids):
            q = conn.execute("SELECT role, COALESCE(user_id, '') AS u, COALESCE(device_id, '') AS d FROM conversations WHERE id = ?",
                             (i,)).fetchone()
            if q is None or q["role"] != "user":
                continue
            r = conn.execute("SELECT id, role FROM conversations WHERE id > ? AND COALESCE(user_id, '') = ? "
                             "AND COALESCE(device_id, '') = ? ORDER BY id LIMIT 1", (i, q["u"], q["d"])).fetchone()
            if r is not None and r["role"] == "assistant":
                ids.add(int(r["id"]))
        cur = conn.execute("DELETE FROM conversations WHERE id IN (%s)" % ",".join("?" * len(ids)), sorted(ids))
        return cur.rowcount


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): "jarvis/chat_history", vol.Optional("who", default="*"): str,
                                  vol.Optional("before"): vol.Any(None, int),
                                  vol.Optional("limit", default=50): vol.All(int, vol.Range(min=1, max=200))})
@websocket_api.async_response
async def ws_chat_history(hass, connection, msg):
    """Administrators only: past JARVIS conversations of every login, newest first. who = a user id, "" for voice with no
    login, or "*" for everyone; "assist" for the other assistants' Assist chats (step 139, assist_log.py), "assist:<agent>"
    for one of them. Also the list of logins and assistants that can be filtered on."""
    others = await _assist_rows(hass)
    if msg["who"] == "assist" or msg["who"].startswith("assist:"):
        want = msg["who"][len("assist:"):] if msg["who"].startswith("assist:") else None
        entries = [_assist_entry(t) for t in reversed(others) if want is None or t.get("agent") == want]
        connection.send_result(msg["id"], {"entries": entries, "whos": await _chat_whos(hass, others), "more": False})
        return
    rows = await hass.async_add_executor_job(_chat_rows, msg["who"], msg.get("before"), msg["limit"])
    pairs = chat_pairs(rows)
    pairs = [p for p in pairs if msg["who"] == "*" or p["user_id"] == msg["who"]]
    cut = len(pairs) > msg["limit"]   # review round 4: more pairs than shown also means "Older chats"
    pairs = pairs[-msg["limit"]:]
    await hass.async_add_executor_job(_chat_answers, pairs)   # review round 3: an answer beyond the page edge
    try:
        from .household_chat import GROUP_LOGIN
    except Exception:  # noqa: BLE001
        GROUP_LOGIN = "JARVIS house group"
    users = {}
    for u in await hass.auth.async_get_users():
        if not u.system_generated:
            users[u.id] = u.name
        elif u.name == GROUP_LOGIN:   # review 2026-11-15: JARVIS's answers in the house group chat
            users[u.id] = "House group chat"
    for p in pairs:
        p["who"] = users.get(p["user_id"]) or ("voice, no login" if not p["user_id"] else "former login")
    connection.send_result(msg["id"], {"entries": list(reversed(pairs)), "whos": await _chat_whos(hass, others, users),
                                       "more": cut or (bool(rows) and len(rows) >= msg["limit"] * 2 + 2)})


async def _assist_rows(hass) -> list:
    """The other assistants' recorded Assist turns (step 139), oldest first; [] when none are stored."""
    try:
        from . import assist_log
        log = await assist_log.async_load_only(hass)
        return log.rows()
    except Exception:  # noqa: BLE001
        return []


def _assist_entry(t: dict) -> dict:
    return {"id": t.get("id"), "ids": [t.get("id")], "t": t.get("t", ""), "user_id": "", "source": "Assist",
            "who": t.get("agent_name") or "Another assistant", "q": t.get("q", ""), "a": t.get("a", "")}


async def _chat_whos(hass, others: list, users: dict | None = None) -> list:
    if users is None:
        try:
            from .household_chat import GROUP_LOGIN
        except Exception:  # noqa: BLE001
            GROUP_LOGIN = "JARVIS house group"
        users = {}
        for u in await hass.auth.async_get_users():
            if not u.system_generated:
                users[u.id] = u.name
            elif u.name == GROUP_LOGIN:
                users[u.id] = "House group chat"
    whos = [{"id": "*", "name": "Everyone"}] + [{"id": k, "name": v or "?"} for k, v in sorted(users.items(), key=lambda kv: (kv[1] or "").casefold())] + [
        {"id": "", "name": "Voice, no login"}]
    agents = {}
    for t in others:
        if t.get("agent"):
            agents[t["agent"]] = t.get("agent_name") or t["agent"]
    if others:   # step 139: the other assistants' Assist chats
        whos.append({"id": "assist", "name": "Other assistants (Assist)"})
        whos += [{"id": "assist:" + a, "name": "Assist: " + n} for a, n in sorted(agents.items(), key=lambda kv: kv[1].casefold())]
    return whos


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): "jarvis/chat_history_delete",
                                  vol.Required("ids"): vol.All([vol.Any(int, str)], vol.Length(min=1, max=400))})
@websocket_api.async_response
async def ws_chat_history_delete(hass, connection, msg):
    """Administrators only: deletes these turns from JARVIS's conversation store (not from its long-term memory); ids that
    start with "a:" are the other assistants' recorded Assist turns (step 139)."""
    numbers = [i for i in msg["ids"] if isinstance(i, int) and not isinstance(i, bool)]
    assist = [i for i in msg["ids"] if isinstance(i, str) and i.startswith("a:")]
    n = await hass.async_add_executor_job(_chat_delete, numbers) if numbers else 0
    if assist:
        from . import assist_log
        n += await (await assist_log.async_load_only(hass)).async_delete(assist)   # review round 4: saved at once
    connection.send_result(msg["id"], {"deleted": n})


# ============================================================================ setup
async def async_setup(hass: HomeAssistant, entry) -> None:
    if not hass.data.get(_REG):
        hass.data[_REG] = True
        for cmd in (ws_answer_watch, ws_answer_watch_clear, ws_chat_history, ws_chat_history_delete, ws_overview_unsync):
            websocket_api.async_register_command(hass, cmd)
    from homeassistant.helpers.event import async_call_later, async_track_time_interval
    from .optional_pages import _entry as _pages_entry
    st = _state(hass)

    async def run(_now=None):
        st["overview_wait"] = None
        if _pages_entry(hass) is None:   # review round 16: no overview write while JARVIS is unloaded, disabled or being removed
            return
        try:
            notes = await async_overview_sync(hass)
            if notes:
                _LOGGER.info("JARVIS: Home overview: %s", "; ".join(notes))
        except Exception as exc:  # noqa: BLE001 - the overview must not break JARVIS
            _LOGGER.warning("JARVIS: Home overview not updated: %s", type(exc).__name__)

    @callback
    def changed(_event=None):
        if not enabled(hass, "overview"):
            return
        if st.get("overview_wait"):
            st["overview_wait"]()
        st["overview_wait"] = async_call_later(hass, OVERVIEW_SETTLE, run)

    entry.async_on_unload(async_track_time_interval(hass, run, OVERVIEW_EVERY))

    def cancel_wait():   # review round 16: the 30 s re-sync after a registry change does not outlive JARVIS
        wait = st.pop("overview_wait", None)
        if wait:
            wait()
    entry.async_on_unload(cancel_wait)
    for event in ("entity_registry_updated", "device_registry_updated", "area_registry_updated"):
        entry.async_on_unload(hass.bus.async_listen(event, changed))
    st["overview_run"] = run
    await async_assist_log(hass, enabled(hass, "chatlog"))   # step 139: the other assistants' Assist chats, while Chat history is on

    async def watch_prune(_now=None):   # review round 20 #7: jh_watch_days holds while the watcher is off too
        try:
            await async_watch_prune(hass)
        except Exception as exc:  # noqa: BLE001 - never break JARVIS
            _LOGGER.warning("JARVIS: answer watcher log not pruned: %s", type(exc).__name__)
    await watch_prune()
    from datetime import timedelta as _td
    entry.async_on_unload(async_track_time_interval(hass, watch_prune, _td(hours=WATCH_PRUNE_EVERY_H)))

    def stop_log():
        try:
            from . import assist_log
            assist_log.async_stop(hass)
        except Exception:  # noqa: BLE001
            pass
    entry.async_on_unload(stop_log)


async def async_assist_log(hass, on: bool) -> None:
    """Step 139: records the other assistants' Assist turns while the Chat history page is on (stored turns are kept when it is
    switched off; an administrator deletes them on the page)."""
    try:
        from . import assist_log
        if on:
            await assist_log.async_start(hass)
        else:
            assist_log.async_stop(hass)
    except Exception as exc:  # noqa: BLE001 - never break JARVIS or a chat
        _LOGGER.warning("JARVIS: Assist recording not changed: %s", type(exc).__name__)
