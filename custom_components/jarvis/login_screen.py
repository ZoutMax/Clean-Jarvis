"""Clean Jarvis optional login screen (step 136): the animated JARVIS look around Home Assistant's own login form.

Off by default. An administrator turns it on in JARVIS Settings > Optional pages (jh_opt_login). Two more switches there:
jh_login_status (a counts-only system status on the login screen, off by default) and jh_login_voice ("Identify yourself."
after the first tap or key press, on by default).

How it stays safe:
  * /auth/authorize keeps serving Home Assistant's OWN authorize.html, read from the installed frontend while running, with one
    stylesheet and one script added. It is never a copy: a newer Home Assistant brings its own page and the skin wraps that.
    Home Assistant's form still does the whole login (OAuth query, "Keep me logged in", MFA, errors, IP ban message, language,
    the companion app's login view).
  * The untouched page is served whenever the switch is off, the config entry is gone (JARVIS removed), the skin files are
    missing, the page cannot be read or does not look as expected. Every CSS rule waits for the script to mark the page
    (html.jl-skin), and the script undoes itself on any error, so a failing or missing script leaves the stock page.
  * Nothing is registered until the switch is first turned on. Home Assistant has no way to unregister a view, so after
    turning it off the view stays until the next restart but serves the stock page; after a restart nothing is registered.
  * The media (video, photos, planet maps, three.js, the two fonts; about 12 MB) is not in the repository. On first enable it is downloaded
    once from the pinned release asset of the repository the manifest names, checked against its SHA-256 and unpacked into /config/www/jarvis-login/
    (served by Home Assistant as /local/jarvis-login/). Without it the login screen shows a drawn starfield instead.
  * The public status endpoint gives counts only (no names, rooms or entity ids), only while both switches are on, is cached
    for 30 seconds and is rate limited per address.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import time
import zipfile
from pathlib import Path

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

SWITCH = "jh_opt_login"
STATUS_KEY = "jh_login_status"
VOICE_KEY = "jh_login_voice"
AUTHORIZE_URL = "/auth/authorize"
STATUS_URL = "/api/jarvis/login_status"
SKIN_DIR = Path(__file__).parent / "frontend" / "optional" / "login"
SKIN_URL = "/jarvis_panel_static/optional/login/"
SKIN_FILES = ("login.css", "login.js")
ASSET_SUBDIR = ("www", "jarvis-login")
ASSET_URL = "/local/jarvis-login/"
ASSET_RELEASE = {
    "tag": "login-assets-v1",
    "file": "jarvis-login-assets-v1.zip",
    "sha256": "82196dbd63a9541571b42eb849332b93cf13294b1d0a67f8d41d817b75b8a0ca",
    "size": 11771262,
    "version": "1",
}
ASSET_MAX_BYTES = 64 * 1024 * 1024
ASSET_SUFFIXES = (".mp4", ".jpg", ".png", ".js", ".txt", ".md", ".json", ".woff2", ".css")
STATUS_CACHE_SECONDS = 30
RATE_WINDOW, RATE_MAX = 60.0, 12          # per address: 12 status requests a minute (the page asks once a minute)
KEY_DOMAINS = ("light", "switch", "climate", "camera", "fan", "media_player", "lock", "cover")
_STATE = "jh_login_screen"


def asset_url(manifest: Path | None = None) -> str | None:
    """The release asset's address in the repository this integration comes from: its manifest's documentation (or
    issue_tracker) link names the GitHub repository. None when that is not a GitHub repository. The SHA-256 pins the content,
    so a wrong address can only fail, never bring other files."""
    try:
        data = json.loads((manifest or Path(__file__).parent / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    for key in ("documentation", "issue_tracker"):
        m = re.match(r"^https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?(?:/.*)?$", str(data.get(key) or ""))
        if m:
            return "https://github.com/%s/%s/releases/download/%s/%s" % (m.group(1), m.group(2), ASSET_RELEASE["tag"],
                                                                          ASSET_RELEASE["file"])
    return None


def _st(hass) -> dict:
    return hass.data.setdefault(_STATE, {})


def _entry(hass):
    try:
        for entry in hass.config_entries.async_entries(DOMAIN):
            if getattr(entry, "disabled_by", None) or str(getattr(getattr(entry, "state", None), "value", "loaded") or "loaded") not in (
                    "loaded", "setup_in_progress"):   # review round 7: a disabled or unloaded JARVIS is off
                continue
            return entry
    except Exception:  # noqa: BLE001
        return None
    return None


def _opt(hass, key, default):
    entry = _entry(hass)
    if entry is None:
        return None
    from . import jarvis_config
    return jarvis_config.runtime_get(hass, entry, key, default)


def enabled(hass) -> bool:
    """On only while JARVIS is installed and an administrator switched it on (off by default)."""
    return _opt(hass, SWITCH, False) is True


def status_on(hass) -> bool:
    return enabled(hass) and _opt(hass, STATUS_KEY, False) is True


def voice_on(hass) -> bool:
    return enabled(hass) and _opt(hass, VOICE_KEY, True) is not False


def asset_dir(hass) -> Path:
    return Path(hass.config.path(*ASSET_SUBDIR))


def assets_ready(folder: Path) -> bool:
    try:
        return (folder / ".version").read_text(encoding="utf-8").strip() == ASSET_RELEASE["version"] and (folder / "manifest.json").is_file()
    except OSError:
        return False


# ---------------------------------------------------------------------------------------------------------- the page
def inject_skin(html: str, version: str, flags: dict) -> str | None:
    """Home Assistant's authorize.html with the skin's stylesheet (end of <head>) and script (end of <body>) added. None when
    the page does not look as expected: then the caller serves the untouched page."""
    if "<ha-authorize" not in html or "data-jarvis-login" in html:
        return None
    low = html.lower()
    head, body = low.find("</head>"), low.rfind("</body>")
    if head < 0 or body < 0 or body < head:
        return None
    v = re.sub(r"[^0-9A-Za-z._-]", "", version) or "0"
    data = "".join(f' data-{k}="{1 if flags.get(k) else 0}"' for k in ("assets", "status", "voice"))
    css = f'<link rel="stylesheet" href="{SKIN_URL}login.css?v={v}" data-jarvis-login>'
    js = (f'<script src="{SKIN_URL}login.js?v={v}" data-jarvis-login{data} data-base="{ASSET_URL}" '
          f'data-status-url="{STATUS_URL}" data-voice-url="{SKIN_URL}identify.mp3?v={v}"></script>')
    return html[:head] + css + html[head:body] + js + html[body:]


def build_page(original: Path, skin_dir: Path, flags: dict) -> str | None:
    """Runs in the executor. None = serve the untouched page."""
    skin = [skin_dir / n for n in SKIN_FILES]
    if not original.is_file() or not all(f.is_file() for f in skin):
        return None
    version = str(max(f.stat().st_mtime_ns for f in skin) // 1_000_000_000)
    return inject_skin(original.read_text(encoding="utf-8"), version, flags)


def frontend_authorize() -> Path | None:
    """Home Assistant's own login page file in the installed frontend (runs in the executor)."""
    try:
        import hass_frontend
        f = Path(hass_frontend.where()) / "authorize.html"
        return f if f.is_file() else None
    except Exception:  # noqa: BLE001
        return None


def _views():
    from aiohttp import web
    from homeassistant.components.http import HomeAssistantView

    class AuthorizeView(HomeAssistantView):
        """/auth/authorize: Home Assistant's own page, with the skin while the switch is on. Public like Home Assistant's
        own page; the URL, its query (client_id, redirect_uri, state, PKCE) and the login flow are untouched."""
        requires_auth = False
        url = AUTHORIZE_URL
        name = "jarvis:login_screen"

        def __init__(self, hass, original: Path):
            self.hass, self.original = hass, original

        async def get(self, request):
            html = None
            try:
                if enabled(self.hass):
                    folder = asset_dir(self.hass)
                    flags = {"assets": await self.hass.async_add_executor_job(assets_ready, folder),
                             "status": status_on(self.hass), "voice": voice_on(self.hass)}
                    html = await self.hass.async_add_executor_job(build_page, self.original, SKIN_DIR, flags)
            except Exception:  # noqa: BLE001 - never lose the login page
                _LOGGER.exception("JARVIS login screen: could not build the page, serving Home Assistant's own")
                html = None
            if html is None:
                return web.FileResponse(self.original, headers={"Cache-Control": "no-cache"})
            return web.Response(text=html, content_type="text/html", charset="utf-8", headers={"Cache-Control": "no-cache"})

    class StatusView(HomeAssistantView):
        """Public: the login screen shows it before anyone is logged in. Counts only, cached, rate limited per address,
        and 404 unless the login screen and its status switch are both on."""
        requires_auth = False
        url = STATUS_URL
        name = "jarvis:login_status"

        def __init__(self, hass):
            self.hass = hass
            self._at, self._data, self._hits = 0.0, None, {}

        def allowed(self, who: str, now: float) -> bool:
            # jh-r30-ratelimit (step 173, review round 30 #1): purge only IPs with all-old timestamps before the per-IP
            # check, so a rate-limited IP whose data was stored stays blocked even when the size purge fires on a
            # different request's call; _hits.clear() wiped the whole dict before the return value was computed
            if len(self._hits) > 2000:
                cutoff = now - RATE_WINDOW
                self._hits = {k: v for k, v in self._hits.items() if any(t >= cutoff for t in v)}
            hits = [t for t in self._hits.get(who, []) if now - t < RATE_WINDOW]
            if len(hits) >= RATE_MAX:
                self._hits[who] = hits
                return False
            hits.append(now)
            self._hits[who] = hits
            return True

        async def get(self, request):
            if not status_on(self.hass):
                return self.json({"error": "not available"}, status_code=404)
            now = time.monotonic()
            if not self.allowed(str(request.remote or "?"), now):
                return self.json({"error": "too many requests"}, status_code=429, headers={"Retry-After": "60"})
            if self._data is None or now - self._at >= STATUS_CACHE_SECONDS:
                self._data, self._at = compute_status(self.hass), now
            return self.json(self._data, headers={"Cache-Control": "no-store"})

    return AuthorizeView, StatusView


def put_first(router, path: str, ours: set) -> bool:
    """aiohttp answers a path with the resource registered for it first, and the frontend registered /auth/authorize before
    JARVIS. Moves Home Assistant's own resource(s) behind ours; they stay registered as the fallback. True when ours comes
    first."""
    theirs = [r for r in router.resources() if r not in ours and getattr(r, "canonical", None) == path]
    for r in theirs:
        router.unindex_resource(r)
        router.index_resource(r)
    index = getattr(router, "_resource_index", None)
    if isinstance(index, dict):
        listed = index.get(path) or []
        return bool(listed) and listed[0] in ours
    return not theirs


async def async_register(hass) -> bool:
    """Registers the two views once (the first time the switch is on). False when the login page stays Home Assistant's
    own (no frontend page found, or ours could not be put in front)."""
    st = _st(hass)
    if st.get("registered") is not None:
        return st["registered"]
    st["registered"] = False
    try:
        original = await hass.async_add_executor_job(frontend_authorize)
        if original is None:
            _LOGGER.warning("JARVIS login screen: Home Assistant's login page was not found, it stays as it is")
            return False
        AuthorizeView, StatusView = _views()
        router = hass.http.app.router
        before = set(router.resources())
        hass.http.register_view(AuthorizeView(hass, original))
        ours = set(router.resources()) - before
        hass.http.register_view(StatusView(hass))
        st["registered"] = bool(ours) and put_first(router, AUTHORIZE_URL, ours)
        if not st["registered"]:
            _LOGGER.warning("JARVIS login screen: could not be put in front, Home Assistant's own login page stays")
    except Exception:  # noqa: BLE001 - Home Assistant's own login page must keep working
        _LOGGER.exception("JARVIS login screen: not set up, Home Assistant's own login page stays")
    return st["registered"]


# ---------------------------------------------------------------------------------------------------------- the status
def compute_status(hass) -> dict:
    """Counts only: devices of the key domains that are offline (unavailable) and integrations that failed to start. A device
    with several unavailable entities counts once; devices of a failing integration count as that one integration."""
    from homeassistant.config_entries import ConfigEntryState
    from homeassistant.helpers import entity_registry as er
    failing = {e.entry_id for e in hass.config_entries.async_entries()
               if e.state in (ConfigEntryState.SETUP_ERROR, ConfigEntryState.SETUP_RETRY)}
    registry = er.async_get(hass)
    offline = set()
    for state in hass.states.async_all(KEY_DOMAINS):
        if state.state != "unavailable":
            continue
        entry = registry.async_get(state.entity_id)
        if entry is not None and entry.config_entry_id in failing:
            continue
        offline.add(("d", entry.device_id) if entry is not None and entry.device_id else ("e", state.entity_id))
    devices, integrations = len(offline), len(failing)
    parts = []
    if devices:
        parts.append(f"{devices} device{'s' if devices != 1 else ''} offline")
    if integrations:
        parts.append(f"{integrations} integration{'s' if integrations != 1 else ''} failing")
    return {"updated": int(time.time()), "issues": devices + integrations,
            "summary": ", ".join(parts) if parts else "All systems online"}


# ---------------------------------------------------------------------------------------------------------- the media
def _jh_unzip(zip_path, tmp, written) -> None:
    """jh-r25-login (step 167): the checked unzip into the .unpacking folder (as before)."""
    with zipfile.ZipFile(zip_path) as z:
        total = 0
        for info in z.infolist():
            name = info.filename
            if info.is_dir():
                continue
            parts = name.split("/")
            if name.startswith(("/", "\\")) or ".." in parts or ":" in name or "\\" in name or not name.endswith(ASSET_SUFFIXES):
                raise ValueError("unexpected file in the download: " + name[:80])
            total += info.file_size
            if total > ASSET_MAX_BYTES:
                raise ValueError("the download unpacks to more than expected")
            target = tmp.joinpath(*parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
            written.append(name)


def unpack(zip_path: str, folder: Path, stop=None) -> list:
    """Unpacks the checked download into folder (files the zip names only; no paths outside it, only media and text
    files). Returns the names written."""
    fresh = not folder.exists()   # review round 7: a folder that was already there is not JARVIS's to delete
    folder.mkdir(parents=True, exist_ok=True)
    if fresh:
        (folder / ".jarvis-own").write_text("1", encoding="utf-8")
    tmp = folder.with_name(folder.name + ".unpacking")
    shutil.rmtree(tmp, ignore_errors=True)
    written = []
    try:   # jh-r25-login (step 167, review round 25 #11): a failed unpack leaves no .unpacking folder behind
        _jh_unzip(zip_path, tmp, written)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    if stop is not None and stop():   # JARVIS was removed while it unpacked: nothing is moved in, the folder goes
        shutil.rmtree(tmp, ignore_errors=True)
        if fresh:
            remove_own_files(folder)
        return []
    for name in written:
        dest = folder.joinpath(*name.split("/"))
        dest.parent.mkdir(parents=True, exist_ok=True)
        os.replace(tmp.joinpath(*name.split("/")), dest)
    shutil.rmtree(tmp, ignore_errors=True)
    (folder / ".version").write_text(ASSET_RELEASE["version"], encoding="utf-8")
    listed = set()
    try:
        listed = set(json.loads((folder / ".jarvis-files").read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    (folder / ".jarvis-files").write_text(json.dumps(sorted(listed | set(written))), encoding="utf-8")   # what JARVIS put there
    return written


def remove_own_files(folder: Path) -> list:
    """Review round 7: deletes only the files JARVIS unpacked (its list), its marker files, and directories left empty; the
    folder itself only when JARVIS made it. Never a file of the owner's."""
    gone = []
    try:
        listed = json.loads((folder / ".jarvis-files").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        listed = []
    for name in listed:
        if not isinstance(name, str) or ".." in name.split("/") or name.startswith("/"):
            continue
        p = folder.joinpath(*name.split("/"))
        if p.is_file():
            p.unlink()
            gone.append(name)
    own = (folder / ".jarvis-own").is_file()
    for marker in (".version", ".jarvis-files", ".jarvis-own"):
        try:
            (folder / marker).unlink()
        except OSError:
            pass
    for d in sorted((p for p in folder.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        try:
            d.rmdir()   # only when empty
        except OSError:
            pass
    if own:
        try:
            folder.rmdir()
        except OSError:
            pass
    return gone


async def async_fetch_assets(hass, session=None) -> dict:
    """Downloads and unpacks the media once (when not there yet). Never raises: the result is also kept for the panel."""
    st = _st(hass)
    folder = asset_dir(hass)
    if await hass.async_add_executor_job(assets_ready, folder):
        st["assets"] = {"state": "ready"}
        return st["assets"]
    if (st.get("assets") or {}).get("state") == "downloading":
        return st["assets"]
    st["assets"] = {"state": "downloading"}
    part = str(folder) + ".download"
    try:
        if session is None:
            from homeassistant.helpers.aiohttp_client import async_get_clientsession
            session = async_get_clientsession(hass)
        import aiohttp
        digest, size = hashlib.sha256(), 0
        await hass.async_add_executor_job(lambda: os.makedirs(os.path.dirname(part), exist_ok=True))
        f = await hass.async_add_executor_job(open, part, "wb")
        try:
            url = await hass.async_add_executor_job(asset_url)
            if not url:
                raise ValueError("this integration's manifest names no GitHub repository to download from")
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=900)) as resp:
                if resp.status != 200:
                    raise ValueError("download failed (HTTP %s)" % resp.status)
                async for chunk in resp.content.iter_chunked(1 << 16):
                    size += len(chunk)
                    if size > ASSET_MAX_BYTES:
                        raise ValueError("the download is larger than expected")
                    digest.update(chunk)
                    await hass.async_add_executor_job(f.write, chunk)
        finally:
            await hass.async_add_executor_job(f.close)
        if digest.hexdigest() != ASSET_RELEASE["sha256"]:
            raise ValueError("the download does not match its checksum, so it was not used")
        if st.get("removed"):   # review round 14: JARVIS was removed during the download
            raise ValueError("JARVIS was removed, so the media was not unpacked")
        # jh-r25-login (step 167, review round 25 #11): the unpack runs to its end in the executor even when this task is
        # cancelled (JARVIS removed), so it is shielded and kept: async_remove waits for it before the files go, and the
        # unpack itself moves nothing in once JARVIS was removed
        _jh_up = hass.async_add_executor_job(unpack, part, folder, lambda: bool(st.get("removed")))
        st["unpack"] = _jh_up
        await asyncio.shield(_jh_up)
        if st.get("removed"):   # review round 15: removed while unpacking: what was just written goes too
            await hass.async_add_executor_job(remove_own_files, folder)
            raise ValueError("JARVIS was removed, so the media was not kept")
        await async_serve_media(hass)   # review round 5: also when /config/www did not exist when Home Assistant started
        st["assets"] = {"state": "ready"}
        _LOGGER.info("JARVIS login screen: media downloaded to /config/%s", "/".join(ASSET_SUBDIR))
    except asyncio.CancelledError:   # review round 15: stopped (JARVIS removed): a later setup downloads again
        st["assets"] = {"state": "missing"}
        raise
    except Exception as exc:  # noqa: BLE001 - the login screen works without the media (drawn starfield)
        st["assets"] = {"state": "failed", "error": str(exc)[:200] or type(exc).__name__}
        _LOGGER.warning("JARVIS login screen: media not downloaded (%s); the drawn starfield is used", st["assets"]["error"])
    finally:
        try:
            await hass.async_add_executor_job(lambda: os.path.exists(part) and os.remove(part))
        except Exception:  # noqa: BLE001
            pass
    return st["assets"]


def panel_config(hass) -> dict:
    """What the Optional pages card shows for the login screen."""
    st = _st(hass)
    assets = dict(st.get("assets") or {"state": "missing"})   # review round 4: read at setup, never a file read in the loop
    return {STATUS_KEY: _opt(hass, STATUS_KEY, False) is True, VOICE_KEY: _opt(hass, VOICE_KEY, True) is not False,
            "jh_login_assets": assets, "jh_login_active": st.get("registered") is True}


async def async_serve_media(hass) -> None:
    """Serves /config/www/jarvis-login as /local/jarvis-login (once). Home Assistant registers /local only when /config/www
    existed at its start, so on a fresh install the downloaded media was not served until a restart (review round 5). The
    more exact path also wins while /local exists."""
    st = _st(hass)
    if st.get("served"):
        return
    try:
        from homeassistant.components.http import StaticPathConfig
        folder = asset_dir(hass)
        if not await hass.async_add_executor_job(folder.is_dir):
            return
        await hass.http.async_register_static_paths([StaticPathConfig(ASSET_URL.rstrip("/"), str(folder), True)])
        st["served"] = True
    except Exception as exc:  # noqa: BLE001 - /local may serve it anyway
        _LOGGER.debug("JARVIS login screen: media path not registered: %s", exc)


async def async_switch(hass, on: bool) -> None:
    """Follows the switch: on registers the page (once) and fetches the media in the background; off needs nothing (the
    page then serves Home Assistant's own login at once)."""
    if not on:
        return
    if await async_register(hass):
        await async_serve_media(hass)
        _st(hass).pop("removed", None)   # review round 14: the task is kept, so removing JARVIS can stop it
        _st(hass)["fetch"] = hass.async_create_background_task(async_fetch_assets(hass), "jarvis login screen media")


async def async_setup(hass, entry) -> None:
    st = _st(hass)
    if not st.get("assets"):   # what the panel shows, read once in the executor (review round 4)
        try:
            ready = await hass.async_add_executor_job(assets_ready, asset_dir(hass))
        except Exception:  # noqa: BLE001
            ready = False
        st["assets"] = {"state": "ready" if ready else "missing"}
    if enabled(hass):
        await async_switch(hass, True)


async def async_remove(hass) -> None:
    """JARVIS removed: the downloaded media goes too, and a download still running is stopped (review round 14)."""
    st = _st(hass)
    st["removed"] = True
    task = st.pop("fetch", None)
    if task is not None and not task.done():
        task.cancel()
        try:   # review round 15: let it stop before the files go
            await asyncio.wait_for(asyncio.shield(task), 30)
        except BaseException:  # noqa: BLE001 - cancelled, failed or slow: the files are removed below either way
            pass
    unpacking = st.pop("unpack", None)
    if unpacking is not None and not unpacking.done():
        try:   # jh-r25-login (step 167, review round 25 #11): a running unpack ends first (it moves nothing in now)
            await asyncio.wait_for(asyncio.shield(unpacking), 120)
        except BaseException:  # noqa: BLE001 - failed or slow: the files are removed below either way
            pass
    st["assets"] = {"state": "missing"}   # review round 15: a re-added JARVIS reads the folder again and downloads

    def rm():
        folder = asset_dir(hass)
        if folder.is_dir():   # review round 7: only the files JARVIS put there
            remove_own_files(folder)
        shutil.rmtree(folder.with_name(folder.name + ".unpacking"), ignore_errors=True)   # jh-r25-login: JARVIS's own scratch
    try:
        await hass.async_add_executor_job(rm)
    except Exception:  # noqa: BLE001
        pass
