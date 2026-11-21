"""Clean Jarvis optional looks (step 137): the JARVIS logo everywhere, and the idle media bar hidden. Both off by default, each a
JARVIS Settings switch (Optional pages), admin only. No configuration.yaml lines: Home Assistant's own frontend API
(add_extra_js_url / remove_extra_js_url) and JARVIS's own views.

JARVIS logo (jh_opt_brand):
  * A small script shows the JARVIS logo at the top of the sidebar, on the loading screen, on the logo pictures inside the app
    ("Unable to connect", About) and as the browser tab's icon. It is added to every page with add_extra_js_url while the
    switch is on and removed again when it is turned off.
  * Review round 8: Home Assistant's own logo files (/static/...) are never answered by JARVIS any more. Home Assistant's
    service worker keeps those addresses cache-first, so a browser would have kept the JARVIS logo after the switch is off or
    JARVIS is removed. The script points at JARVIS's own files instead (/jarvis_panel_static/optional/brand/...), which go away
    with it. So the login page and the home-screen icon keep Home Assistant's logo (the JARVIS login screen has its own).
Idle media bar (jh_opt_mediabar):
  * On Home Assistant's Media page the player bar shrinks to its target button while nothing can be controlled there: the target
    is Web browser and nothing plays, a film plays in Home Assistant's pop-up, an item is loading, or the speaker is off,
    unavailable or gone. The script is added with add_extra_js_url and always loaded with a content hash (?v=), so a proxy or
    browser cache can never keep serving an old copy.
Every script URL carries the file's content hash, so an update reaches every browser at once.
"""
from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

BRAND_KEY = "jh_opt_brand"
MEDIABAR_KEY = "jh_opt_mediabar"
ASSIST_KEY = "jh_opt_assist"          # step 139: Assist history in Home Assistant's Assist pop-up
OPT_DIR = Path(__file__).parent / "frontend" / "optional"
BRAND_DIR = OPT_DIR / "brand"
STATIC = "/jarvis_panel_static/optional/"
BRAND_JS = "brand/jarvis-brand.js"
MEDIABAR_JS = "mediabar/media-bar-idle.js"
ASSIST_JS = "assist/assist-history.js"
ICON = re.compile(r"^(favicon|maskable_icon|tile-win)[^/]*\.(png|ico)$")
SIZE = re.compile(r"(\d+)x(\d+)")
_STATE = "jh_optional_looks"


def _st(hass) -> dict:
    return hass.data.setdefault(_STATE, {})


def _on(hass, key) -> bool:
    try:
        entries = [e for e in hass.config_entries.async_entries(DOMAIN) if not getattr(e, "disabled_by", None)
                   and str(getattr(getattr(e, "state", None), "value", "loaded") or "loaded") in ("loaded", "setup_in_progress")]
    except Exception:  # noqa: BLE001
        return False
    if not entries:   # review round 7: a disabled or unloaded JARVIS shows Home Assistant's own logos
        return False
    from . import jarvis_config
    return jarvis_config.runtime_get(hass, entries[0], key, False) is True


def brand_on(hass) -> bool:
    return _on(hass, BRAND_KEY)


def mediabar_on(hass) -> bool:
    return _on(hass, MEDIABAR_KEY)


def script_url(rel: str) -> str:
    """The script's address with its content hash, so a cache never serves an old copy."""
    try:
        digest = hashlib.sha1((OPT_DIR / rel).read_bytes()).hexdigest()[:12]
    except OSError:
        digest = "0"
    return STATIC + rel + "?v=" + digest


# ---------------------------------------------------------------------------------------------------------- logo files
def logo_for(name: str) -> Path:
    """The JARVIS picture that fits a Home Assistant logo file."""
    if name.endswith(".svg"):
        return BRAND_DIR / "logo.svg"
    if name.endswith(".ico"):
        return BRAND_DIR / "logo-32.png"
    m = SIZE.search(name)
    size = max(int(m.group(1)), int(m.group(2))) if m else 192
    if size == 180:
        return BRAND_DIR / "logo-apple-180.png"
    return BRAND_DIR / ("logo-512.png" if size > 192 else "logo-192.png" if size > 48 else "logo-48.png" if size > 32 else "logo-32.png")


# Review round 8 (jh-r8-logo): no view answers Home Assistant's own /static/ logo files any more (its service worker caches them
# cache-first, so a browser kept the JARVIS logo after the switch was off). jarvis-brand.js uses JARVIS's own files (logo_for
# is the size rule it follows).


async def async_brand(hass, on: bool) -> None:
    from homeassistant.components import frontend
    st = _st(hass)
    url = st.get("brand_js")
    if url:
        frontend.remove_extra_js_url(hass, url)
        st["brand_js"] = None
    if not on:
        return
    st["brand_js"] = await hass.async_add_executor_job(script_url, BRAND_JS)
    frontend.add_extra_js_url(hass, st["brand_js"])


async def _script(hass, slot: str, rel: str, on: bool) -> None:
    """Adds or removes one page script (with its content hash) through Home Assistant's own add_extra_js_url."""
    from homeassistant.components import frontend
    st = _st(hass)
    url = st.get(slot)
    if url:
        frontend.remove_extra_js_url(hass, url)
        st[slot] = None
    if on:
        st[slot] = await hass.async_add_executor_job(script_url, rel)
        frontend.add_extra_js_url(hass, st[slot])


async def async_mediabar(hass, on: bool) -> None:
    await _script(hass, "mediabar_js", MEDIABAR_JS, on)


async def async_assist(hass, on: bool) -> None:
    """Step 139: Assist history (the add-on keeps each login's own messages in that login's Home Assistant user data)."""
    await _script(hass, "assist_js", ASSIST_JS, on)


async def async_switch(hass, key: str, on: bool) -> None:
    if key == BRAND_KEY:
        await async_brand(hass, on)
    elif key == MEDIABAR_KEY:
        await async_mediabar(hass, on)
    elif key == ASSIST_KEY:
        await async_assist(hass, on)


async def async_setup(hass, entry) -> None:
    if brand_on(hass):
        await async_brand(hass, True)
    if mediabar_on(hass):
        await async_mediabar(hass, True)
    if _on(hass, ASSIST_KEY):
        await async_assist(hass, True)

    def unload():   # JARVIS reloaded or removed: Home Assistant's own pages without the scripts
        try:
            from homeassistant.components import frontend
            st = _st(hass)
            for k in ("brand_js", "mediabar_js", "assist_js"):
                if st.get(k):
                    frontend.remove_extra_js_url(hass, st[k])
                    st[k] = None
        except Exception:  # noqa: BLE001
            pass
    entry.async_on_unload(unload)
