"""Clean Jarvis optional pages: Map, People (private), My phone and Household Chat.

Each page is a JARVIS Settings switch (jh_opt_map, jh_opt_people, jh_opt_phone, jh_opt_chat), off by default. For Map, People and
My phone an administrator gets Setup and Remove buttons in Settings > Optional pages while the switch is on; they create the
dashboard, the card resources and the view through Home Assistant's own commands (the same ones its dashboard editor uses) and
remember what they made in jh_opt_created, so Remove deletes exactly that. Nothing here writes a dashboard by itself.
Household Chat (the household_chat package next to this file) needs no setup: the Chat switch alone starts it and puts the Chat
page in the sidebar, and switching it off takes the page away again (the chats stay stored).

This module holds the server side:
  * jarvis/user_devices - which phones and tablets are signed in with which login (the Home Assistant app's own config entry,
    not the trackers linked to a person). An administrator gets everyone, anybody else only their own login. Only names, models
    and entity ids leave Home Assistant: never a webhook id, push token or any other part of the app's config entry.
  * jarvis/history_days - the number of days of history kept, for an administrator (the setting itself is jh_history_days,
    written with jarvis/update_config, which is admin-only).
  * the nightly purge: while the Map page is switched on and a number of days is set, JARVIS asks the recorder at 04:12 to keep
    that many days. No automation is created for it. Home Assistant's own auto purge (recorder purge_keep_days, 10 days by
    default) still runs as well; to keep more than that, set purge_keep_days at least as high or turn auto_purge off in
    configuration.yaml (Home Assistant has no screen for it).
  * the chat's data: <config>/jarvis/household_chat. When it does not exist yet and the former separate Household Chat
    integration left its store in <config>/household_chat, that store is COPIED once (never moved, changed or deleted).
    While the former integration is still installed or loaded, JARVIS's chat stays off (both would register the same
    commands and the same Chat page) and Settings says what to do.
Everything is read live: persons, logins, apps and their entities follow added and removed people and devices.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

FEATURES = ("map", "people", "phone", "chat", "overview", "history", "watch", "chatlog", "login",   # step 136: login screen
            "brand", "mediabar", "tuya", "assist")   # step 137: logo, idle media bar, Tuya clean-up; step 139: Assist history
# the ones with Setup / Remove (dashboards); chat and the answer watcher are the switch alone (step 128 added overview,
# history and chatlog; see optional_extras.py)
PAGES = ("map", "people", "phone", "overview", "history", "chatlog")
SWITCHES = tuple("jh_opt_" + f for f in FEATURES)
CREATED_KEY = "jh_opt_created"
HISTORY_KEY = "jh_history_days"
MIN_DAYS, MAX_DAYS = 1, 365
PURGE_AT = (4, 12, 0)
ASSETS = ("jh-pref.js", "path-calendar-card.js", "map-device-picker.js", "person-page-card.js", "phone-page-card.js",
          "household-chat-panel.js", "saved-history-card.js", "chat-history-card.js")
STATIC = "/jarvis_panel_static/optional/"
CHAT_STATUS = "jarvis_chat_status"
ASSET_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frontend", "optional")
_ASSET_HASH: dict[str, str] = {}

# Which chat a request comes from (step 132), set by Household Chat around its call to the conversation agent
# ("chat:<conversation>"); empty for the JARVIS panel and voice. conversation.py keeps one thread per channel and login.
import contextvars as _jh_cv
JH_CHANNEL = _jh_cv.ContextVar("jarvis_chat_channel", default="")
_REGISTERED = "jh_optional_pages_registered"


def _entry(hass: HomeAssistant):
    """The JARVIS entry that is on. Review round 8: a disabled or unloaded JARVIS is off (as login_screen._entry)."""
    try:
        for entry in hass.config_entries.async_entries(DOMAIN):
            if getattr(entry, "disabled_by", None) or str(getattr(getattr(entry, "state", None), "value", "loaded") or "loaded") not in (
                    "loaded", "setup_in_progress"):
                continue
            return entry
    except Exception:  # noqa: BLE001
        return None
    return None


def _any_entry(hass: HomeAssistant) -> bool:
    try:
        return bool(hass.config_entries.async_entries(DOMAIN))
    except Exception:  # noqa: BLE001
        return False


def _chat_still_on(hass: HomeAssistant, on: bool) -> bool:
    """Review round 8: after every wait, the chat may only go on while JARVIS is on and its Chat switch still is."""
    if not _any_entry(hass):   # review round 15: JARVIS was removed in this run: off; never set up at all (tests): as asked
        return False if hass.data.get("jh_chat_removed") else on
    return _entry(hass) is not None and enabled(hass, "chat")


def _opt(hass: HomeAssistant, key: str, default=None):
    from . import jarvis_config
    return jarvis_config.runtime_get(hass, _entry(hass), key, default)


def enabled(hass: HomeAssistant, feature: str) -> bool:
    """A page is on only when its switch was turned on (off by default)."""
    return _opt(hass, "jh_opt_" + feature, False) is True


def clean_days(value) -> int | None:
    """Whole days 1-365, or None to keep no setting (then nothing is purged by JARVIS). Raises ValueError otherwise."""
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        raise ValueError("Days to keep must be a number")
    try:
        days = int(float(value))
    except (TypeError, ValueError) as exc:
        raise ValueError("Days to keep must be a number") from exc
    if days != float(value) or not MIN_DAYS <= days <= MAX_DAYS:
        raise ValueError("Days to keep must be a whole number from %d to %d" % (MIN_DAYS, MAX_DAYS))
    return days


def history_days(hass: HomeAssistant) -> int | None:
    try:
        return clean_days(_opt(hass, HISTORY_KEY, None))
    except ValueError:
        return None


_SAFE = re.compile(r"^[A-Za-z0-9_\-./?=&:]{1,300}$")
# Review round 8: an owner's earlier resource address ('was') is only ever written back to Home Assistant's own resource, so any
# printable address is kept (a %20, +, ~ or , in it), never a control character.
_WAS = re.compile(r"^[^\x00-\x1f\x7f]{1,2000}$")


def clean_created(value) -> dict:
    """What Setup made, as the panel reports it: {page: {dashboard, made, adopted, resources: [{id, url, was}]}}.
    Only known features and plain short strings are kept, so the setting can never carry anything else."""
    if value in (None, ""):
        return {}
    if not isinstance(value, dict):
        raise ValueError("Expected an object")
    out: dict[str, dict] = {}
    for feature, rec in value.items():
        if feature not in PAGES:
            raise ValueError("Unknown page: %s" % feature)
        if rec in (None, {}):
            continue
        if not isinstance(rec, dict):
            raise ValueError("Expected an object for %s" % feature)
        item: dict[str, Any] = {}
        for k in ("dashboard", "ha_map"):   # step 138: ha_map = Home Assistant's own Map dashboard that Set up made admin-only
            v = rec.get(k)
            if v not in (None, ""):
                if not isinstance(v, str) or not _SAFE.match(v):
                    raise ValueError("Bad %s for %s" % (k, feature))
                item[k] = v
        for k in ("made", "adopted"):
            if k in rec:
                item[k] = rec[k] is True
        res = []
        for r in rec.get("resources") or []:
            if not isinstance(r, dict) or not isinstance(r.get("id"), str) or not _SAFE.match(r["id"]):
                raise ValueError("Bad resource for %s" % feature)
            one = {"id": r["id"]}
            for k in ("url", "was"):
                v = r.get(k)
                if k == "was" and isinstance(v, str) and v.split("?")[0].startswith(STATIC):
                    continue   # review round 11: JARVIS's own static address is no earlier address (Remove then deletes it)
                if v not in (None, ""):
                    if not isinstance(v, str) or not (_WAS if k == "was" else _SAFE).match(v):
                        raise ValueError("Bad resource %s for %s" % (k, feature))
                    one[k] = v
            res.append(one)
        if len(res) > 10:
            raise ValueError("Too many resources for %s" % feature)
        if res:
            item["resources"] = res
        if item:
            out[feature] = item
    return out


def created(hass: HomeAssistant) -> dict:
    try:
        return clean_created(_opt(hass, CREATED_KEY, None))
    except ValueError:
        return {}


def asset_hashes() -> dict:
    """Content hash per card file, for the resource address (?v=...), so a browser loads a new version after an update."""
    return dict(_ASSET_HASH)


def _hash_assets() -> dict:
    out = {}
    for name in ASSETS:
        try:
            with open(os.path.join(ASSET_DIR, name), "rb") as f:
                out[name] = hashlib.sha1(f.read()).hexdigest()[:10]
        except OSError:
            continue
    return out


def panel_config(hass: HomeAssistant) -> dict:
    """The part of the panel's config the Optional pages card needs."""
    out = {key: enabled(hass, key[len("jh_opt_"):]) for key in SWITCHES}
    out[CREATED_KEY] = created(hass)
    out[HISTORY_KEY] = history_days(hass)
    out["jh_opt_assets"] = asset_hashes()
    out["jh_chat_status"] = chat_status(hass)
    try:
        from .optional_extras import watch_days
        out["jh_watch_days"] = watch_days(hass)
    except Exception:  # noqa: BLE001
        pass
    out["jh_watch_notify"] = _opt(hass, "jh_watch_notify", False) is True
    out["jh_overview_cams_all"] = _opt(hass, "jh_overview_cams_all", False) is True   # step 158 (C4): Home overview cameras
    try:   # step 136: the login screen's own switches and the state of its media
        from .login_screen import panel_config as _login_cfg
        out.update(_login_cfg(hass))
    except Exception:  # noqa: BLE001
        pass
    try:   # step 137: the Tuya clean-up only acts when Tuya Local is installed
        out["jh_tuya_local"] = bool(hass.config_entries.async_entries("tuya_local"))
    except Exception:  # noqa: BLE001
        out["jh_tuya_local"] = False
    return out


# ---- Household Chat inside JARVIS
def old_chat(hass: HomeAssistant) -> str | None:
    """The former separate Household Chat integration: "installed" (it has a config entry), "restart" (removed, but its
    commands and photo views stay registered until Home Assistant restarts) or None."""
    from .household_chat.const import OLD_DOMAIN
    if hass.config_entries.async_entries(OLD_DOMAIN):
        return "installed"
    if OLD_DOMAIN in hass.config.components or hass.data.get(OLD_DOMAIN + "_registered"):
        return "restart"
    return None


def chat_status(hass: HomeAssistant) -> dict:
    st = dict(hass.data.get(CHAT_STATUS) or {})
    try:
        from .household_chat import running
        st["running"] = running(hass)
    except Exception:  # noqa: BLE001
        st["running"] = False
    if not st["running"]:
        try:
            st["old"] = old_chat(hass)
        except Exception:  # noqa: BLE001
            pass
    return st


def copy_old_chat_store(config_dir: str) -> str:
    """Copies the former integration's store into JARVIS's chat folder, once. Returns "copied", "kept" (JARVIS already has a
    chat store, nothing is overwritten), "busy" (the target folder holds other files) or "none" (no old store). The old folder
    is only read: never moved, changed or deleted. The copy goes to a temporary folder first and is renamed when complete."""
    old = os.path.join(config_dir, "household_chat")
    new = os.path.join(config_dir, "jarvis", "household_chat")
    if os.path.exists(os.path.join(new, "chat.db")):
        return "kept"
    if not os.path.isfile(os.path.join(old, "chat.db")):
        return "none"
    if os.path.isdir(new):
        if os.listdir(new):
            return "busy"
        os.rmdir(new)
    tmp = new + ".copying"
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(os.path.dirname(new), exist_ok=True)
    shutil.copytree(old, tmp)
    os.replace(tmp, new)
    return "copied"


async def async_chat(hass: HomeAssistant, on: bool) -> dict:
    """Starts or stops Household Chat to follow the Chat switch. Returns the status the panel shows. Review round 5, 6: one at
    a time, and inside the lock the saved switch decides (requests can reach the lock in another order than they were saved;
    the value is stored before this is called). Without a JARVIS entry (tests, setup) the argument is used."""
    import asyncio
    lock = hass.data.setdefault("jh_chat_lock", asyncio.Lock())
    async with lock:
        try:   # review round 8: entries but none on (disabled or unloaded): the chat stops
            now = _chat_still_on(hass, on)
        except Exception:  # noqa: BLE001
            now = on
        return await _async_chat_now(hass, now)


async def _async_chat_now(hass: HomeAssistant, on: bool) -> dict:
    from . import household_chat as chat
    if not on:
        chat.async_stop(hass)
        hass.data[CHAT_STATUS] = {}
        return chat_status(hass)
    old = old_chat(hass)
    if old:
        chat.async_stop(hass)
        hass.data[CHAT_STATUS] = {"old": old}
        _LOGGER.warning("JARVIS: Household Chat stays off: the former Household Chat integration is %s",
                        "still installed" if old == "installed" else "loaded until Home Assistant restarts")
        return chat_status(hass)
    copied = await hass.async_add_executor_job(copy_old_chat_store, hass.config.config_dir)
    if not _chat_still_on(hass, on):   # review round 8: JARVIS went off (or the switch) while the old chats were copied
        chat.async_stop(hass)
        hass.data[CHAT_STATUS] = {}
        return chat_status(hass)
    if copied == "busy":   # starting would make a new empty store there and the old chats would never be copied
        chat.async_stop(hass)
        hass.data[CHAT_STATUS] = {"copied": "busy"}
        _LOGGER.warning("JARVIS: Household Chat stays off: <config>/jarvis/household_chat holds files but no chats")
        return chat_status(hass)
    if copied == "copied":
        _LOGGER.warning("JARVIS: the chats of the former Household Chat integration were copied (the old folder is kept)")
    v = asset_hashes().get("household-chat-panel.js", "1")
    await chat.async_start(hass, hass.config.path("jarvis", "household_chat"), STATIC + "household-chat-panel.js?v=" + v,
                           STATIC + "household-chat-jarvis.png")
    if not _chat_still_on(hass, on):   # review round 8: unloaded while the chat store was set up: never left running
        chat.async_stop(hass)
        hass.data[CHAT_STATUS] = {}
        return chat_status(hass)
    hass.data[CHAT_STATUS] = {"copied": copied}
    return chat_status(hass)


# ---- phones and tablets per login
def sensor_key(unique_id, webhook_id: str) -> str | None:
    """Review round 7: the companion app's own sensor key ('battery_level', 'phone_state', ...), the same in every language,
    from the registry unique_id ('<webhook id>_<key>'). The webhook id itself never leaves Home Assistant."""
    uid = str(unique_id or "")
    if webhook_id and uid.startswith(webhook_id + "_"):
        return uid[len(webhook_id) + 1:] or None
    return None


def apps_by_user(hass: HomeAssistant) -> dict[str, list[dict]]:
    """Login (user id) -> the Home Assistant apps signed in with it, one entry per mobile_app device. The owner is the login the
    app was set up with (config entry data user_id), not the trackers linked to a person, so a tablet signed in as someone is
    theirs without its location counting for their home or away. Only names, models and entity ids go out."""
    from homeassistant.helpers import device_registry as dr, entity_registry as er
    ents, devs = er.async_get(hass), dr.async_get(hass)
    out: dict[str, list[dict]] = {}
    for entry in hass.config_entries.async_entries("mobile_app"):
        uid = entry.data.get("user_id")
        if not uid or entry.disabled_by:
            continue
        hook = str(entry.data.get("webhook_id") or "")
        for device in dr.async_entries_for_config_entry(devs, entry.entry_id):
            tracker, sensors, keys = None, [], {}
            for ent in er.async_entries_for_device(ents, device.id, include_disabled_entities=True):
                if ent.domain == "device_tracker":
                    tracker = tracker or ent.entity_id
                elif ent.domain in ("sensor", "binary_sensor"):
                    sensors.append(ent.entity_id)
                    key = sensor_key(getattr(ent, "unique_id", None), hook)
                    if key:
                        keys[ent.entity_id] = key
            out.setdefault(uid, []).append({
                "device_id": device.id,
                "name": device.name_by_user or device.name or str(entry.data.get("device_name") or "?"),
                "model": device.model, "manufacturer": device.manufacturer,
                "tracker": tracker, "entities": sorted(sensors), "keys": keys})
    for apps in out.values():
        apps.sort(key=lambda d: d["name"].casefold())
    return out


async def user_devices(hass: HomeAssistant, user) -> dict:
    """Every person with a login, and every login with apps but no person, each with the apps signed in with that login.
    An administrator gets everyone; anybody else only their own login (also when they have no person)."""
    apps = apps_by_user(hass)
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
    if user is None or not user.is_admin:
        mine = getattr(user, "id", None)
        people = [p for p in people if mine and p["user_id"] == mine]
    people.sort(key=lambda p: p["name"].casefold())
    return {"people": people}


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): "jarvis/opt_created_set", vol.Required("feature"): str,
                                  vol.Optional("rec"): vol.Any(dict, None)})
@websocket_api.async_response
async def ws_opt_created_set(hass: HomeAssistant, connection, msg):
    """Review round 11 (jh-r11-created): what Set up made for ONE page, merged into the saved record on the server (never the
    panel's possibly old copy of the whole record), so two devices or tabs cannot wipe each other's pages. rec None removes
    that page's record. Returns the merged record."""
    import asyncio
    lock = hass.data.setdefault("jh_opt_created_lock", asyncio.Lock())
    async with lock:
        entry = _entry(hass) or next(iter(hass.config_entries.async_entries(DOMAIN)), None)
        from . import jarvis_config
        try:
            now = clean_created(jarvis_config.runtime_get(hass, entry, "jh_opt_created", None))
            feature = msg["feature"]
            if feature not in PAGES:
                raise ValueError("Unknown page: %s" % feature)
            rec = msg.get("rec")
            if rec:
                now.update(clean_created({feature: rec}))
            else:
                now.pop(feature, None)
        except ValueError as exc:
            connection.send_error(msg["id"], "invalid_jh_opt_created", str(exc))
            return
        if entry is not None:
            data = hass.data.get(DOMAIN, {}).get(entry.entry_id)
            if isinstance(data, dict):
                data.setdefault("runtime_config", {})["jh_opt_created"] = now
        await hass.async_add_executor_job(jarvis_config.set_many, {"jh_opt_created": now})
    if msg["feature"] == "overview" and msg.get("rec"):
        # jh-r18-remove (step 160, review round 18 #8): Set up adopts the dashboard (again), so an earlier Remove's "removed" mark,
        # which stops every sync of it, is cleared before the sync below
        try:
            from .optional_extras import async_overview_forget_removed
            await async_overview_forget_removed(hass, (now.get("overview") or {}).get("dashboard"))
        except Exception:  # noqa: BLE001
            pass
    if now:
        try:   # the Home overview follows its record, as with jarvis/update_config
            from .optional_extras import async_overview_sync
            hass.async_create_task(async_overview_sync(hass))
        except Exception:  # noqa: BLE001
            pass
    connection.send_result(msg["id"], {"jh_opt_created": now})


@websocket_api.websocket_command({vol.Required("type"): "jarvis/user_devices"})
@websocket_api.async_response
async def ws_user_devices(hass: HomeAssistant, connection, msg):
    """Read-only: who is signed in on which phone or tablet (see user_devices)."""
    connection.send_result(msg["id"], await user_devices(hass, connection.user))


def _recorder_info(hass: HomeAssistant) -> dict:
    try:
        from homeassistant.components.recorder import get_instance
        inst = get_instance(hass)
        return {"recorder_keep_days": int(inst.keep_days), "recorder_auto_purge": bool(inst.auto_purge)}
    except Exception:  # noqa: BLE001 - no recorder, or it changed: nothing to tell
        return {}


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): "jarvis/history_days"})
@callback
def ws_history_days(hass: HomeAssistant, connection, msg):
    """For an administrator: the days of history JARVIS keeps (None: not set, JARVIS purges nothing), the limits, whether the
    Map page is on (the purge only runs then) and, when readable, Home Assistant's own recorder retention."""
    connection.send_result(msg["id"], {"days": history_days(hass), "min": MIN_DAYS, "max": MAX_DAYS,
                                       "purging": enabled(hass, "map"), **_recorder_info(hass)})


async def async_purge(hass: HomeAssistant, _now=None) -> bool:
    """The nightly purge: only while the Map page is on and a number of days is set. Returns True when it asked the recorder."""
    days = history_days(hass)
    if not enabled(hass, "map") or days is None:
        return False
    if not hass.services.has_service("recorder", "purge"):
        _LOGGER.warning("JARVIS: history was not cleaned, the recorder is not loaded")
        return False
    await hass.services.async_call("recorder", "purge", {"keep_days": days, "repack": False}, blocking=False)
    _LOGGER.info("JARVIS: history older than %d days is being removed", days)
    return True


async def async_setup(hass: HomeAssistant, entry) -> None:
    """Registers the commands (once) and the nightly purge for this config entry."""
    global _ASSET_HASH
    _ASSET_HASH = await hass.async_add_executor_job(_hash_assets)
    if not hass.data.get(_REGISTERED):
        hass.data[_REGISTERED] = True
        websocket_api.async_register_command(hass, ws_user_devices)
        websocket_api.async_register_command(hass, ws_history_days)
        websocket_api.async_register_command(hass, ws_opt_created_set)   # review round 11
    from homeassistant.helpers.event import async_track_time_change
    h, m, s = PURGE_AT

    async def nightly(now):
        try:
            await async_purge(hass, now)
        except Exception as exc:  # noqa: BLE001 - a failed purge must not break JARVIS
            _LOGGER.warning("JARVIS: nightly history purge failed: %s", type(exc).__name__)

    entry.async_on_unload(async_track_time_change(hass, nightly, hour=h, minute=m, second=s))
    try:   # step 128: Home overview sync, answer watcher, chat history
        from . import optional_extras
        await optional_extras.async_setup(hass, entry)
    except Exception as exc:  # noqa: BLE001
        _LOGGER.warning("JARVIS: optional extras did not start: %s", type(exc).__name__)
    try:   # step 136: the optional login screen (nothing is registered while it is off)
        from . import login_screen
        await login_screen.async_setup(hass, entry)
    except Exception as exc:  # noqa: BLE001 - Home Assistant's own login page stays
        _LOGGER.warning("JARVIS: login screen did not start: %s", type(exc).__name__)
    for _jh_mod in ("optional_looks", "tuya_tidy", "camera_ptz"):   # step 137: logo, media bar, Tuya clean-up (each does nothing while off); step 145: camera pan/tilt commands
        try:
            mod = __import__(__package__ + "." + _jh_mod, fromlist=["async_setup"])
            await mod.async_setup(hass, entry)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("JARVIS: %s did not start: %s", _jh_mod, type(exc).__name__)
    from . import household_chat as chat
    entry.async_on_unload(lambda: chat.async_stop(hass))
    if enabled(hass, "chat"):
        try:
            await async_chat(hass, True)
        except Exception as exc:  # noqa: BLE001 - the chat must not break JARVIS
            _LOGGER.warning("JARVIS: Household Chat did not start: %s", type(exc).__name__)
