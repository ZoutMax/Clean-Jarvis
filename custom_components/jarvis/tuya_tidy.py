"""Clean Jarvis optional "Tuya lamp clean-up" (step 137, jh_opt_tuya). Off by default; admin only. It acts only when the Tuya
Local integration (make-all/tuya-local) is installed and has entries; otherwise it does nothing at all.

While it is on:
  * the Music mode is no longer offered as a lamp Effect. Tuya Local has no setting for this, but its device files support
    `hidden: true` on a value: a lamp that reports it is still shown correctly, it is only left out of the choices. Only the
    device files of the Tuya Local entries in use are touched; the first change keeps a copy of the original in
    <config>/jarvis/tuya_local_originals. A Tuya Local update brings the full files back, so it is applied again at every start.
    The changed lamps are reloaded (unavailable for a few seconds; their light does not change).
  * the scene preset selects of Tuya Local lamps (translation key "scene") are disabled, so Home Assistant never overwrites the
    lamp's own scene from the Tuya app. New lamps get the same at the next start or switch.
Turning it off puts every device file it changed back from its copy (only while the file is still the one it wrote) and
enables again exactly the selects it disabled. Nothing is hard-coded about any lamp: everything follows the installed entries.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

KEY = "jh_opt_tuya"
TUYA_LOCAL = "tuya_local"
HIDE = ("music",)                 # mode values taken out of the choices
PRESET_KEY = "scene"              # translation key of Tuya Local's scene preset selects
STORE_REL = ("jarvis", "tuya_tidy.json")
ORIG_REL = ("jarvis", "tuya_local_originals")


def enabled(hass) -> bool:
    try:
        entries = hass.config_entries.async_entries(DOMAIN)
    except Exception:  # noqa: BLE001
        return False
    if not entries:
        return False
    from . import jarvis_config
    return jarvis_config.runtime_get(hass, entries[0], KEY, False) is True


_ENTITY = re.compile(r"^\s*(?:-\s*)?entity:\s*['\"]?([a-z_]+)", re.I)


def is_lamp_file(text: str) -> bool:
    """A Tuya Local device file with a light entity (primary or secondary)."""
    return any((m := _ENTITY.match(line)) and m.group(1).lower() == "light" for line in text.split("\n"))


def hide_values(text: str, hide=HIDE, only_entity="light") -> tuple[str, int]:
    """Adds `hidden: true` to every mapping item whose dps_val is one of `hide`, only inside a `light` entity of the file
    (review round 4: never in a fan's, speaker's or diffuser's mode list). Returns (new text, items changed). An item that
    already has a `hidden:` key is left alone."""
    lines = text.split("\n")
    out, changed, i, entity = [], 0, 0, None
    item = re.compile(r"^(\s*)- dps_val:\s*['\"]?(%s)['\"]?\s*$" % "|".join(re.escape(h) for h in hide), re.I)
    while i < len(lines):
        line = lines[i]
        out.append(line)
        em = _ENTITY.match(line)
        if em:
            entity = em.group(1).lower()
        m = item.match(line)
        if m and (only_entity is None or entity == only_entity):
            key_indent = len(m.group(1)) + 2
            j, has_hidden = i + 1, False
            while j < len(lines) and lines[j].strip() and len(lines[j]) - len(lines[j].lstrip()) >= key_indent:
                if len(lines[j]) - len(lines[j].lstrip()) == key_indent and lines[j].strip().startswith("hidden:"):
                    has_hidden = True
                j += 1
            if not has_hidden:
                out.append(" " * key_indent + "hidden: true")
                changed += 1
        i += 1
    return "\n".join(out), changed


def _write(path: str, text: str) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)


def trim_files(devices_dir: str, types, orig_dir: str) -> list:
    """hide_values on <devices_dir>/<type>.yaml for each type in use. Returns the types whose file changed."""
    done = []
    for conf_type in sorted({t for t in types if isinstance(t, str) and re.fullmatch(r"[A-Za-z0-9_\-]+", t)}):
        path = os.path.join(devices_dir, conf_type + ".yaml")
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as f:
            text = f.read()
        if not is_lamp_file(text):   # review round 4: lamps only
            continue
        new, changed = hide_values(text)
        if not changed:
            continue
        os.makedirs(orig_dir, exist_ok=True)
        keep = os.path.join(orig_dir, conf_type + ".yaml")
        shutil.copyfile(path, keep)          # the file as Tuya Local shipped it (this version)
        _write(path, new)
        done.append(conf_type)
    return done


def restore_files(devices_dir: str, orig_dir: str) -> list:
    """Puts back each kept original whose device file is still exactly the trimmed version of it (a Tuya Local update in
    between already brought its own file: then nothing is touched). Returns the types put back."""
    done = []
    if not os.path.isdir(orig_dir):
        return done
    for name in sorted(os.listdir(orig_dir)):
        if not name.endswith(".yaml"):
            continue
        keep, path = os.path.join(orig_dir, name), os.path.join(devices_dir, name)
        with open(keep, encoding="utf-8") as f:
            orig = f.read()
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                now = f.read()
            if now == hide_values(orig)[0] and now != orig:
                _write(path, orig)
                done.append(name[:-5])
        os.remove(keep)
    return done


def devices_dir(hass) -> str | None:
    try:
        import importlib.util
        spec = importlib.util.find_spec("custom_components.tuya_local")
        if spec and spec.submodule_search_locations:
            d = os.path.join(list(spec.submodule_search_locations)[0], "devices")
            return d if os.path.isdir(d) else None
    except Exception:  # noqa: BLE001
        pass
    d = hass.config.path("custom_components", TUYA_LOCAL, "devices")
    return d if os.path.isdir(d) else None


def _load(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    _write(path, json.dumps(data, indent=1))


async def async_apply(hass) -> dict:
    """Hides Music and disables the preset selects. Does nothing without Tuya Local entries. Returns what it did."""
    entries = [e for e in hass.config_entries.async_entries(TUYA_LOCAL) if getattr(e, "source", "") != "ignore"]
    if not entries:
        return {"tuya_local": False}
    out = {"tuya_local": True, "files": [], "disabled": []}
    folder = await hass.async_add_executor_job(devices_dir, hass)
    if folder:
        types = [(e.data or {}).get("type") for e in entries]
        out["files"] = await hass.async_add_executor_job(trim_files, folder, types, hass.config.path(*ORIG_REL))
        for e in entries:
            if (e.data or {}).get("type") in out["files"] and getattr(e, "state", None) is not None:
                try:
                    await hass.config_entries.async_reload(e.entry_id)
                except Exception as exc:  # noqa: BLE001
                    _LOGGER.warning("JARVIS Tuya clean-up: %s was not reloaded: %s", e.title, type(exc).__name__)
    from homeassistant.helpers import entity_registry as er
    reg = er.async_get(hass)
    store = hass.config.path(*STORE_REL)
    data = _ids(reg, await hass.async_add_executor_job(_load, store))
    # review round 10 (jh-r10-tuya): every select is kept by its registry id, so a renamed entity_id is still the same select.
    # handled: every select this ever disabled (never disabled again by it); mine: the ones whose disable is still JARVIS's
    # (an owner's own change, seen by the registry listener below, takes a select out of mine for good)
    mine, handled = set(data["disabled"]), set(data["handled"]) | set(data["disabled"])
    mine = {u for u in mine if (e := _entry_by_id(reg, u)) is not None and e.disabled_by == er.RegistryEntryDisabler.USER}
    lamps = {e.device_id for e in reg.entities.values() if e.platform == TUYA_LOCAL and e.domain == "light" and e.device_id}
    for ent in list(reg.entities.values()):
        # review round 4: only a lamp's preset select, and never again one this already handled (the owner may have turned
        # it back on in Home Assistant's entity settings)
        if (ent.platform == TUYA_LOCAL and ent.domain == "select" and getattr(ent, "translation_key", None) == PRESET_KEY
                and ent.disabled_by is None and ent.device_id in lamps and ent.id not in handled):
            with _busy(hass):
                reg.async_update_entity(ent.entity_id, disabled_by=er.RegistryEntryDisabler.USER)
            mine.add(ent.id)
            handled.add(ent.id)
            out["disabled"].append(ent.entity_id)
    hass.data[_MINE] = set(mine)
    await hass.async_add_executor_job(_save, store, {"disabled": sorted(mine), "handled": sorted(handled)})
    if out["files"] or out["disabled"]:
        _LOGGER.info("JARVIS Tuya clean-up: Music hidden in %d lamp types, %d scene presets disabled", len(out["files"]), len(out["disabled"]))
    return out


_MINE = "jh_tuya_mine"
_BUSY = "jh_tuya_busy"


class _busy:
    """While JARVIS itself changes a select, the registry listener must not take that change for the owner's."""
    def __init__(self, hass):
        self.hass = hass

    def __enter__(self):
        self.hass.data[_BUSY] = self.hass.data.get(_BUSY, 0) + 1

    def __exit__(self, *a):
        self.hass.data[_BUSY] = self.hass.data.get(_BUSY, 1) - 1


def _entry_by_id(reg, uid):
    """The registry entry with this stable id (it keeps its id when its entity_id is renamed), or None."""
    try:
        got = reg.entities.get_entry(uid)
        if got is not None:
            return got
    except Exception:  # noqa: BLE001 - an older registry without get_entry
        pass
    return next((e for e in reg.entities.values() if getattr(e, "id", None) == uid), None)


def _ids(reg, data: dict) -> dict:
    """The stored lists as registry ids; an entity_id stored by an earlier version is turned into its id (when it still exists)."""
    def conv(values):
        out = []
        for v in values or []:
            if isinstance(v, str) and "." in v:   # an entity_id (ids have no dot)
                e = reg.async_get(v)
                if e is not None:
                    out.append(e.id)
            elif isinstance(v, str) and v:
                out.append(v)
        return out
    return {"disabled": conv(data.get("disabled")), "handled": conv(data.get("handled"))}


async def _forget_owned(hass, uid) -> None:
    store = hass.config.path(*STORE_REL)
    data = await hass.async_add_executor_job(_load, store)
    disabled = [u for u in (data.get("disabled") or []) if u != uid]
    handled = sorted(set(data.get("handled") or []) | {uid})
    await hass.async_add_executor_job(_save, store, {"disabled": disabled, "handled": handled})


def _listen(hass, entry) -> None:
    """Review round 10: an owner who changes a tracked select's enabled state (in Home Assistant's entity settings) makes that
    select his own: it is never enabled again by switching the clean-up off or removing JARVIS."""
    from homeassistant.core import callback
    from homeassistant.helpers import entity_registry as er

    @callback
    def changed(event) -> None:
        data = event.data or {}
        if data.get("action") != "update" or "disabled_by" not in (data.get("changes") or {}) or hass.data.get(_BUSY):
            return
        ent = er.async_get(hass).async_get(data.get("entity_id") or "")
        uid = getattr(ent, "id", None)
        if uid and uid in hass.data.get(_MINE, set()):
            hass.data[_MINE].discard(uid)
            hass.async_create_task(_forget_owned(hass, uid))

    entry.async_on_unload(hass.bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, changed))


async def async_undo(hass) -> dict:
    """Puts back the device files and enables again the selects this disabled (not ones the owner disabled himself)."""
    out = {"files": [], "enabled": []}
    folder = await hass.async_add_executor_job(devices_dir, hass)
    if folder:
        out["files"] = await hass.async_add_executor_job(restore_files, folder, hass.config.path(*ORIG_REL))
        for e in hass.config_entries.async_entries(TUYA_LOCAL):
            if (e.data or {}).get("type") in out["files"]:
                try:
                    await hass.config_entries.async_reload(e.entry_id)
                except Exception:  # noqa: BLE001
                    pass
    from homeassistant.helpers import entity_registry as er
    reg = er.async_get(hass)
    store = hass.config.path(*STORE_REL)
    data = _ids(reg, await hass.async_add_executor_job(_load, store))
    for uid in data["disabled"]:   # review round 10: by registry id; one the owner changed himself is no longer in this list
        ent = _entry_by_id(reg, uid)
        if ent is not None and ent.disabled_by == er.RegistryEntryDisabler.USER:
            with _busy(hass):
                reg.async_update_entity(ent.entity_id, disabled_by=None)
            out["enabled"].append(ent.entity_id)
    hass.data[_MINE] = set()
    # review round 11: the ones JARVIS just enabled again leave 'handled' (a later switch-on may disable them again); the ones
    # the owner took over stay in it, so they are never disabled again
    keep = sorted(set(data["handled"]) - set(data["disabled"]))
    await hass.async_add_executor_job(_save, store, {"disabled": [], "handled": keep})
    return out


async def async_switch(hass, on: bool) -> dict:
    """Review round 14: one switch at a time (a lock kept in hass.data), and only the latest order runs, so an off during a
    slow first on always runs after it and can never leave the presets disabled with the switch off."""
    import asyncio
    lock = hass.data.setdefault(DOMAIN + "_tuya_lock", asyncio.Lock())
    seq = hass.data[DOMAIN + "_tuya_seq"] = hass.data.get(DOMAIN + "_tuya_seq", 0) + 1
    async with lock:
        try:
            if seq != hass.data.get(DOMAIN + "_tuya_seq"):   # a later order came in meanwhile: that order does the work
                return {"skipped": True}
            return await (async_apply(hass) if on else async_undo(hass))
        except Exception as exc:  # noqa: BLE001 - never break JARVIS or the lamps
            _LOGGER.warning("JARVIS Tuya clean-up failed: %s", exc)
            return {"error": str(exc)}


async def async_setup(hass, entry) -> None:
    """At every start while the switch is on (a Tuya Local update restores its full device files)."""
    try:   # review round 10: what JARVIS disabled, and the listener that sees the owner's own changes
        from homeassistant.helpers import entity_registry as er
        hass.data[_MINE] = set(_ids(er.async_get(hass), await hass.async_add_executor_job(_load, hass.config.path(*STORE_REL)))["disabled"])
        _listen(hass, entry)
    except Exception as exc:  # noqa: BLE001
        _LOGGER.debug("JARVIS Tuya clean-up: registry listener not started: %s", exc)
    if not enabled(hass):
        return
    from homeassistant.core import CoreState

    wait = {}

    async def run(_event=None):
        wait.pop("unsub", None)
        if not _still_on(hass):   # review round 16: switched off, removed or reloaded while Home Assistant was starting
            return
        await async_switch(hass, True)
    if hass.state is CoreState.running:
        hass.async_create_background_task(run(), "jarvis tuya clean-up")
    else:
        from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
        wait["unsub"] = hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, run)

        def stop_wait():   # the start hook does not outlive this JARVIS entry
            unsub = wait.pop("unsub", None)
            if unsub:
                unsub()
        entry.async_on_unload(stop_wait)


def _still_on(hass) -> bool:
    """Review round 16: the clean-up is still wanted now: a loaded, not disabled JARVIS entry (as optional_pages._entry) with
    the switch on. Unsure: False."""
    try:
        from .optional_pages import _entry
        from . import jarvis_config
        e = _entry(hass)
        return e is not None and jarvis_config.runtime_get(hass, e, KEY, False) is True
    except Exception:  # noqa: BLE001
        return False
