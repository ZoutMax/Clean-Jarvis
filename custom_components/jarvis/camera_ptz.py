"""Clean Jarvis step 145: pan/tilt control for a camera that can move, shown in the panel's Camera Watch under the Activity Feed.

Found dynamically, never by camera id: the camera entity belongs to the ONVIF integration, Home Assistant offers its onvif.ptz
service, and the camera's ONVIF profile has a PTZ configuration (its own flags say whether RelativeMove / ContinuousMove work and
which presets exist). Home Assistant has no generic pan/tilt API for other integrations, so only ONVIF cameras get the pad.

Who may move it: an administrator, or a login that Home Assistant itself lets control that camera (its entity permission, the
same rule the panel's device buttons rely on). The move goes through JARVIS's actuation seam (execute_actuator), like every other
device action. Two websocket commands:
  jarvis/camera_ptz_info  {entity_id}                      -> {ptz, relative, continuous, presets, can_control}
  jarvis/camera_ptz       {entity_id, action, direction?, step?, preset?}   action: move | stop | preset
  jarvis/camera_ptz_flip  {entity_id, pan?, tilt?}   (administrators) the camera's own direction (step 149)
Step 149 (jh-ptz-flip): some cameras pan or tilt the other way round (a Tapo pans opposite to ONVIF's LEFT/RIGHT; a camera
hung upside down tilts the other way). Per camera, "flip left/right" and "flip up/down" are kept in JARVIS's config on the
server (jh_ptz_flip, by entity_id), so every login and device moves it the same way. Both are off by default.
"""
from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)
_REGISTERED = "jh_camera_ptz_registered"

# step -> (RelativeMove distance, ContinuousMove seconds)
STEPS = {"small": (0.05, 0.2), "medium": (0.1, 0.5), "large": (0.25, 1.0)}
DIRECTIONS = {"up": ("tilt", "UP"), "down": ("tilt", "DOWN"), "left": ("pan", "LEFT"), "right": ("pan", "RIGHT")}
SPEED = 1.0   # step 155: full speed by default (at 0.5 some cameras tilt very slowly); per camera next to the flips
SPEEDS = (1.0, 0.75, 0.5, 0.25)
FLIP_KEY = "jh_ptz_flip"
_OTHER = {"LEFT": "RIGHT", "RIGHT": "LEFT", "UP": "DOWN", "DOWN": "UP"}


def _jarvis_entry(hass):
    try:
        from .const import DOMAIN
        return next(iter(hass.config_entries.async_entries(DOMAIN)), None)
    except Exception:  # noqa: BLE001
        return None


def _all_flips(hass) -> dict:
    try:
        from . import jarvis_config
        got = jarvis_config.runtime_get(hass, _jarvis_entry(hass), FLIP_KEY, {}) or {}
        return got if isinstance(got, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def apply_flips(hass, entity_id, data) -> dict:
    """Review round 12 (jh-r12-flips): the ONE place the camera's flips are applied to an onvif.ptz call, used by the panel's
    command and by every generic service path (execute_plan, routines). Returns a copy with pan / tilt swapped as set."""
    out = dict(data or {})
    try:
        ids = [entity_id] if isinstance(entity_id, str) else list(entity_id or [])
        f = flips(hass, ids[0]) if len(ids) == 1 else {"pan": False, "tilt": False}
    except Exception:  # noqa: BLE001 - no flip information: the call as it was asked
        return out
    for axis in ("pan", "tilt"):
        v = out.get(axis)
        if f.get(axis) and isinstance(v, str) and v.upper() in _OTHER:
            out[axis] = _OTHER[v.upper()]
    return out


def flips(hass, entity_id: str) -> dict:
    """{"pan": bool, "tilt": bool} for this camera (both False unless an administrator set them)."""
    one = _all_flips(hass).get(entity_id) or {}
    return {"pan": one.get("pan") is True, "tilt": one.get("tilt") is True}


def speed(hass, entity_id: str) -> float:
    """Step 155: this camera's move speed (kept with its flips), else full speed."""
    try:
        v = float((_all_flips(hass).get(entity_id) or {}).get("speed", SPEED))
    except (TypeError, ValueError):
        return SPEED
    return v if v in SPEEDS else SPEED


def _platform(hass: HomeAssistant, entity_id: str):
    try:
        from homeassistant.helpers import entity_registry as er
        return getattr(er.async_get(hass).async_get(entity_id), "platform", None)
    except Exception:  # noqa: BLE001
        return None


def _profile_ptz(hass: HomeAssistant, entity_id: str):
    """The ONVIF profile's PTZ configuration of this camera entity (continuous, relative, presets), or None when unknown."""
    try:
        comp = hass.data.get("camera")
        ent = comp.get_entity(entity_id) if comp is not None and hasattr(comp, "get_entity") else None
        profile = getattr(ent, "profile", None)
        return getattr(profile, "ptz", None) if profile is not None else None
    except Exception:  # noqa: BLE001
        return None


def ptz_info(hass: HomeAssistant, entity_id: str) -> dict:
    """What this camera can do. ptz False for anything that is not an ONVIF camera with a PTZ configuration."""
    none = {"ptz": False, "relative": False, "continuous": False, "presets": []}
    if not isinstance(entity_id, str) or not entity_id.startswith("camera.") or hass.states.get(entity_id) is None:
        return none
    if _platform(hass, entity_id) != "onvif" or not hass.services.has_service("onvif", "ptz"):
        return none
    ptz = _profile_ptz(hass, entity_id)
    if ptz is None:
        return none
    relative, continuous = bool(getattr(ptz, "relative", False)), bool(getattr(ptz, "continuous", False))
    if not (relative or continuous):
        return none
    presets = [str(p) for p in (getattr(ptz, "presets", None) or []) if str(p).strip()]
    return {"ptz": True, "relative": relative, "continuous": continuous,
            "presets": [{"token": p, "name": "Preset " + p} for p in presets]}


def can_control(hass: HomeAssistant, user, entity_id: str) -> bool:
    """An administrator, or a login Home Assistant lets control this camera (its own entity permission)."""
    if user is None:
        return False
    if getattr(user, "is_admin", False):
        return True
    try:
        from homeassistant.auth.permissions.const import POLICY_CONTROL
    except Exception:  # noqa: BLE001
        POLICY_CONTROL = "control"   # Home Assistant's own value
    try:
        return bool(user.permissions.check_entity(entity_id, POLICY_CONTROL))
    except Exception:  # noqa: BLE001 - no permission information: no control
        return False


def ptz_call(info: dict, action: str, direction: str | None = None, step: str | None = None, preset: str | None = None,
             flip: dict | None = None, speed: float | None = None) -> dict:
    """The onvif.ptz service data for one button. Raises ValueError for anything the camera cannot do. flip: the camera's own
    {"pan", "tilt"} flips (step 149), applied here so the arrow means what the person sees."""
    if not info.get("ptz"):
        raise ValueError("This camera cannot move")
    if action == "stop":
        return {"move_mode": "Stop"}
    if action == "preset":
        tokens = [p["token"] for p in info.get("presets") or []]
        if preset not in tokens:
            raise ValueError("Unknown preset")
        return {"move_mode": "GotoPreset", "preset": preset}
    if action != "move" or direction not in DIRECTIONS:
        raise ValueError("Unknown move")
    distance, seconds = STEPS.get(step or "medium", STEPS["medium"])
    axis, value = DIRECTIONS[direction]
    if (flip or {}).get(axis):   # step 149: this camera moves the other way round on this axis
        value = _OTHER[value]
    rate = speed if speed in SPEEDS else SPEED   # step 155
    if info.get("relative"):
        return {"move_mode": "RelativeMove", axis: value, "distance": distance, "speed": rate}
    return {"move_mode": "ContinuousMove", axis: value, "speed": rate, "continuous_duration": seconds}


def _enabled_camera(hass: HomeAssistant, entity_id: str) -> bool:
    """Only a camera Camera Watch shows (JARVIS's camera list, not switched off there)."""
    try:
        from .websocket import _get_cameras
        return any(c.get("entity_id") == entity_id and c.get("enabled") is not False for c in _get_cameras(hass))
    except Exception:  # noqa: BLE001
        return False


@websocket_api.websocket_command({vol.Required("type"): "jarvis/camera_ptz_info", vol.Required("entity_id"): str})
@websocket_api.async_response
async def ws_ptz_info(hass: HomeAssistant, connection, msg):
    eid = msg["entity_id"]
    info = ptz_info(hass, eid) if _enabled_camera(hass, eid) else {"ptz": False, "relative": False, "continuous": False, "presets": []}
    info["can_control"] = bool(info["ptz"]) and can_control(hass, getattr(connection, "user", None), eid)
    info["flip"] = flips(hass, eid)   # step 149
    info["speed"] = speed(hass, eid)   # step 155
    info["admin"] = bool(getattr(getattr(connection, "user", None), "is_admin", False))
    connection.send_result(msg["id"], info)


@websocket_api.websocket_command({
    vol.Required("type"): "jarvis/camera_ptz",
    vol.Required("entity_id"): str,
    vol.Required("action"): vol.In(["move", "stop", "preset"]),
    vol.Optional("direction"): vol.In(list(DIRECTIONS)),
    vol.Optional("step"): vol.In(list(STEPS)),
    vol.Optional("preset"): vol.All(str, vol.Length(min=1, max=64)),
})
@websocket_api.async_response
async def ws_ptz(hass: HomeAssistant, connection, msg):
    eid = msg["entity_id"]
    user = getattr(connection, "user", None)
    if not _enabled_camera(hass, eid):
        connection.send_error(msg["id"], "not_found", "Not a Camera Watch camera")
        return
    if not can_control(hass, user, eid):
        connection.send_error(msg["id"], "unauthorized", "This login may not move this camera")
        return
    try:
        data = apply_flips(hass, eid, ptz_call(ptz_info(hass, eid), msg["action"], msg.get("direction"), msg.get("step"),
                                               msg.get("preset"), speed=speed(hass, eid)))   # review round 12: the shared flip step
    except ValueError as exc:
        connection.send_error(msg["id"], "invalid", str(exc))
        return
    data["entity_id"] = eid
    try:
        from .actuation import execute_actuator
        ok, detail = await execute_actuator(hass, capability="camera.ptz", entity_id=eid, domain="onvif", service="ptz",
                                            data=data, action="ptz " + msg["action"])
    except Exception as exc:  # noqa: BLE001
        ok, detail = False, type(exc).__name__
    if not ok:
        connection.send_error(msg["id"], "failed", "The camera did not move: %s" % (detail or "no answer"))
        return
    connection.send_result(msg["id"], {"ok": True})


@websocket_api.require_admin
@websocket_api.websocket_command({
    vol.Required("type"): "jarvis/camera_ptz_flip",
    vol.Required("entity_id"): str,
    vol.Optional("pan"): bool,
    vol.Optional("tilt"): bool,
    vol.Optional("speed"): vol.In(list(SPEEDS)),   # step 155
})
@websocket_api.async_response
async def ws_ptz_flip(hass: HomeAssistant, connection, msg):
    """Step 149: an administrator sets this camera's flips; kept in JARVIS's config on the server (every login, every device)."""
    eid = msg["entity_id"]
    if not _enabled_camera(hass, eid):
        connection.send_error(msg["id"], "not_found", "Not a Camera Watch camera")
        return
    allf = {k: dict(v) for k, v in _all_flips(hass).items() if isinstance(k, str) and isinstance(v, dict)}
    one = flips(hass, eid)
    for k in ("pan", "tilt"):
        if k in msg:
            one[k] = msg[k] is True
    rate = float(msg["speed"]) if "speed" in msg else speed(hass, eid)   # step 155: kept with the flips
    if one["pan"] or one["tilt"] or rate != SPEED:
        allf[eid] = dict(one, **({"speed": rate} if rate != SPEED else {}))
    else:
        allf.pop(eid, None)
    try:
        from .const import DOMAIN
        entry = _jarvis_entry(hass)
        data = hass.data.get(DOMAIN, {}).get(getattr(entry, "entry_id", None)) if entry is not None else None
        if isinstance(data, dict):
            data.setdefault("runtime_config", {})[FLIP_KEY] = allf
        from . import jarvis_config
        await hass.async_add_executor_job(jarvis_config.set_many, {FLIP_KEY: allf})
    except Exception as exc:  # noqa: BLE001
        connection.send_error(msg["id"], "failed", "Not saved: %s" % type(exc).__name__)
        return
    connection.send_result(msg["id"], {"flip": one, "speed": rate})


async def async_setup(hass: HomeAssistant, entry) -> None:
    """Registers the two commands once (nothing else runs: the pad only asks when a camera is shown)."""
    if not hass.data.get(_REGISTERED):
        hass.data[_REGISTERED] = True
        websocket_api.async_register_command(hass, ws_ptz_info)
        websocket_api.async_register_command(hass, ws_ptz)
        websocket_api.async_register_command(hass, ws_ptz_flip)   # step 149
