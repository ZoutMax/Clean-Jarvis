"""Clean Jarvis (review round 4, 5): when JARVIS is removed (Settings > Devices & services > JARVIS > Delete), put back what its
optional switches changed outside JARVIS, so removing it restores Home Assistant as it was:
  * the Tuya lamp clean-up: device files back from their copies, the preset selects it disabled enabled again;
  * Home Assistant's own Map dashboard, when the Map page's Set up made it admin-only;
  * the Lovelace resources Set up made (deleted) or took over (their old address back), and the dashboards Set up made;
  * the views JARVIS wrote into an adopted Home overview;
  * the login screen's downloaded media (/config/www/jarvis-login) and the other assistants' recorded Assist turns.
Each part is tried on its own; a failure is logged and never stops the removal.
Home Assistant's dashboards and resources collections are found through Home Assistant itself on every version: the old
hass.data dict, the LovelaceData object, or the collection behind its own websocket command (review round 5: on current Home
Assistant the dashboards collection is not in hass.data at all).
"""
from __future__ import annotations

import logging

_LOGGER = logging.getLogger(__name__)


def _from_ws(hass, command: str):
    """The storage collection behind one of Home Assistant's own collection websocket commands, or None."""
    try:
        handler = (hass.data.get("websocket_api") or {}).get(command)
        f = handler[0] if isinstance(handler, tuple) else handler
        for _ in range(8):
            owner = getattr(f, "__self__", None)
            coll = getattr(owner, "storage_collection", None) if owner is not None else None
            if coll is not None:
                return coll
            f = getattr(f, "__wrapped__", None)
            if f is None:
                break
    except Exception:  # noqa: BLE001
        pass
    return None


def _dashboards(hass):
    data = hass.data.get("lovelace")
    coll = data.get("dashboards_collection") if isinstance(data, dict) else getattr(data, "dashboards_collection", None)
    return coll or _from_ws(hass, "lovelace/dashboards/update")


def _resources(hass):
    data = hass.data.get("lovelace")
    coll = data.get("resources") if isinstance(data, dict) else getattr(data, "resources", None)
    if coll is not None and hasattr(coll, "async_update_item"):
        return coll
    return _from_ws(hass, "lovelace/resources/update")


async def async_map_back(hass, ha_map_id) -> bool:
    """Home Assistant's own Map dashboard (the one Set up made admin-only) visible to everyone again."""
    coll = _dashboards(hass)
    if coll is None or not ha_map_id:
        if ha_map_id:
            _LOGGER.warning("JARVIS removed: Home Assistant's dashboards were not found, so its Map stays admin-only; "
                            "turn Admin only off for it in Settings > Dashboards")
        return False
    try:
        for item in coll.async_items():
            if str(item.get("id")) == str(ha_map_id) and item.get("require_admin"):
                await coll.async_update_item(item["id"], {"require_admin": False})
                return True
    except Exception as exc:  # noqa: BLE001
        _LOGGER.warning("JARVIS removed: Home Assistant's Map was not put back: %s", exc)
    return False


async def async_pages_back(hass, made_all: dict) -> None:
    """Resources Set up made are deleted, ones it took over get their old address back; dashboards Set up made are deleted;
    an adopted Home overview loses the views JARVIS wrote into it."""
    res = _resources(hass)
    boards = _dashboards(hass)
    for feature, rec in (made_all or {}).items():
        for r in rec.get("resources") or []:
            if res is None:
                break
            try:
                if r.get("was") and not str(r["was"]).split("?")[0].startswith("/jarvis_panel_static/optional/"):   # review round 11
                    await res.async_update_item(r["id"], {"url": r["was"]})
                else:
                    await res.async_delete_item(r["id"])
            except Exception as exc:  # noqa: BLE001
                _LOGGER.warning("JARVIS removed: resource %s not put back: %s", r.get("id"), exc)
        if rec.get("made") and rec.get("dashboard") and boards is not None:
            try:
                for item in boards.async_items():
                    if item.get("url_path") == rec["dashboard"]:
                        await boards.async_delete_item(item["id"])
            except Exception as exc:  # noqa: BLE001
                _LOGGER.warning("JARVIS removed: dashboard %s not removed: %s", rec.get("dashboard"), exc)
        if feature == "overview" and not rec.get("made") and rec.get("dashboard"):
            try:
                from .optional_extras import async_overview_unsync
                await async_overview_unsync(hass, rec["dashboard"])
            except Exception as exc:  # noqa: BLE001
                _LOGGER.warning("JARVIS removed: Home overview views not taken out: %s", exc)
    if res is None and any((r or {}).get("resources") for r in (made_all or {}).values()):
        _LOGGER.warning("JARVIS removed: Home Assistant's dashboard resources were not found; remove the "
                        "/jarvis_panel_static/optional/ resources in Settings > Dashboards > Resources")


async def async_on_remove(hass, entry) -> None:
    hass.data["jh_chat_removed"] = True   # review round 15: a chat start still waiting may not go on
    try:   # review round 15: through the switch's lock, so a running clean-up finishes first and is then undone
        from . import tuya_tidy
        await tuya_tidy.async_switch(hass, False)
    except Exception as exc:  # noqa: BLE001
        _LOGGER.warning("JARVIS removed: Tuya clean-up not undone: %s", exc)
    made_all = {}
    try:
        from . import jarvis_config
        from .optional_pages import clean_created
        made_all = clean_created(jarvis_config.runtime_get(hass, entry, "jh_opt_created", None))
    except Exception as exc:  # noqa: BLE001
        _LOGGER.warning("JARVIS removed: the optional pages' setup was not read: %s", exc)
    if (made_all.get("map") or {}).get("ha_map"):
        await async_map_back(hass, made_all["map"]["ha_map"])
    await async_pages_back(hass, made_all)
    try:   # review round 9: what Set up made is undone, so a reinstall starts from "Not set up" (nothing writes a dashboard by itself)
        from . import jarvis_config as _jh_jc
        await hass.async_add_executor_job(_jh_jc.delete, "jh_opt_created")
        from .const import DOMAIN as _jh_domain
        _jh_rc = (hass.data.get(_jh_domain, {}).get(getattr(entry, "entry_id", None), {}) or {}).get("runtime_config")
        if isinstance(_jh_rc, dict):
            _jh_rc.pop("jh_opt_created", None)
    except Exception as exc:  # noqa: BLE001
        _LOGGER.warning("JARVIS removed: the optional pages' setup record was not cleared: %s", exc)
    for mod in ("login_screen", "assist_log"):
        try:
            m = __import__(__package__ + "." + mod, fromlist=["async_remove"])
            await m.async_remove(hass)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("JARVIS removed: %s not cleaned up: %s", mod, exc)
    try:   # review round 20 #7: the answer watcher's flagged questions and answers (other people's chat text) go too
        from . import optional_extras
        await optional_extras.async_watch_remove(hass)
    except Exception as exc:  # noqa: BLE001
        _LOGGER.warning("JARVIS removed: answer watcher log not cleaned up: %s", exc)
    try:   # review round 21 #5 (step 163): the hidden "JARVIS house group" system login that Household Chat made goes too (a
        # system-generated user cannot be deleted in Home Assistant's screens); a reinstall makes it again when it is needed
        try:
            from .household_chat import GROUP_LOGIN as _jh_group_login
        except Exception:  # noqa: BLE001 - the chat module cannot load: its name, as household_chat defines it
            _jh_group_login = "JARVIS house group"
        for _jh_u in list(await hass.auth.async_get_users()):
            if getattr(_jh_u, "system_generated", False) and getattr(_jh_u, "name", None) == _jh_group_login:
                await hass.auth.async_remove_user(_jh_u)
    except Exception as exc:  # noqa: BLE001
        _LOGGER.warning("JARVIS removed: the house group's login was not removed: %s", exc)
