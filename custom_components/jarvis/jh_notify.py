"""Clean Jarvis (step 158, C3): where JARVIS's private and admin alerts go.

JARVIS pushes to one notify service set in Settings > Notifications. On many homes that is notify.notify, which reaches every
phone and tablet with the Home Assistant app, also other people's. Alerts that hold private or admin data (sentinel and system
alerts, the answer watcher, the AI's send_notification for an administrator, the proactive briefing with its calendar and
mail facts, the cognitive core's pushes about people's routines) now go, by default, to the administrators' own phones only:
the notify.mobile_app_* service of every app signed in with an active administrator login, read live from Home Assistant's
mobile_app entries (a new phone or a new administrator is followed, nothing is hard-coded). Step 159 (review round 17 #3): a
phone is chosen by its own app registration, never by its name alone; two apps that share a device name share one notify
service, so such a service is used only when Home Assistant says it reaches exactly the administrator's app.

Setting jh_notify_private (Settings > Notifications, "Private alerts"):
  * "admin_phones" (default): the administrators' phones. When none of them can take it, the alert is HELD in the Home
    Assistant log (administrators only); it never falls back to a service that may reach someone else (step 160, review
    round 18 #3, #9: an allowlist, see is_broadcast);
  * "notify_service": the notify service above, whatever it is (the owner's explicit choice).
Alerts for the whole household (an intrusion with a snapshot, a washer that finished) are unchanged. Nothing is sent when no
notify service is set at all (JARVIS's pushes stay opt-in, as before).
"""
from __future__ import annotations

import logging
import re

_LOGGER = logging.getLogger(__name__)

KEY = "jh_notify_private"
ADMIN_PHONES = "admin_phones"
NOTIFY_SERVICE = "notify_service"
CHOICES = (ADMIN_PHONES, NOTIFY_SERVICE)
DEFAULT = ADMIN_PHONES


def clean_choice(value) -> str:
    """The saved value, checked: one of CHOICES. A ValueError carries a message for the panel."""
    if value in CHOICES:
        return value
    raise ValueError("Private alerts must be one of: " + ", ".join(CHOICES))


def mode(hass=None) -> str:
    """The jh_notify_private setting; unset or unreadable is the default (admin phones, the stricter one)."""
    try:
        from . import jarvis_config
        value = jarvis_config.get(KEY, DEFAULT)
    except Exception:  # noqa: BLE001
        return DEFAULT
    return value if value in CHOICES else DEFAULT


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


async def admin_phones(hass) -> list:
    """notify service names (mobile_app_<device>) of every Home Assistant app signed in with an active administrator, live.
    jh-phone-exact (step 159, review round 17 #3): only a service that reaches exactly that app (jh_phone_service)."""
    out = []
    try:
        entries = list(hass.config_entries.async_entries("mobile_app"))
    except Exception:  # noqa: BLE001
        return out
    targets = _jh_phone_targets(hass)
    for entry in entries:
        data = getattr(entry, "data", None) or {}
        uid, name = data.get("user_id"), data.get("device_name")
        if not uid or not name:
            continue
        try:
            user = await hass.auth.async_get_user(uid)
        except Exception:  # noqa: BLE001
            user = None
        if user is None or not getattr(user, "is_admin", False) or getattr(user, "is_active", True) is False:
            continue
        service = jh_phone_service(hass, entry, entries, targets)
        if service and service not in out:
            out.append(service)
    return out


async def private_phones(hass, configured) -> list:
    """The administrators' phones a private alert goes to, or [] when there is none, pushes are off (no notify service set) or
    the setting is notify_service. With [] in admin_phones mode, send_private holds the alert (jh-r18-allow)."""
    if not str(configured or "").strip() or mode(hass) != ADMIN_PHONES:
        return []
    phones = await admin_phones(hass)
    if not phones:
        _LOGGER.debug("JARVIS: no administrator phone found, a private alert goes to %s", configured)
    return phones


async def _call(hass, service, payload, blocking):
    if blocking:
        await hass.services.async_call("notify", service, dict(payload), blocking=True)   # notify: a communication
        return True
    try:
        await hass.services.async_call("notify", service, dict(payload), blocking=False)
        return True
    except Exception as exc:  # noqa: BLE001
        _LOGGER.debug("JARVIS: notify.%s failed: %s", service, exc)
        return False


# jh-r18-closed (step 159b, the coordinator's fail-closed rule): a private alert never falls back to a service that may reach
# other people. When it cannot go to an administrator's own phone, it is HELD: written to the Home Assistant log (Settings >
# System > Logs, administrators only) and the caller says honestly that it was not delivered. Not a persistent notification:
# Home Assistant shows those to every login (persistent_notification/subscribe has no admin check).
HELD = "held:admin_log"


async def _group_services(hass) -> set:
    """notify services of Home Assistant's notify group platform (each one reaches several devices)."""
    out = set()
    try:
        for svc in list(((hass.data.get("notify_services") or {}).get("group")) or []):
            name = getattr(svc, "_service_name", None)
            if name:
                out.add(str(name))
    except Exception:  # noqa: BLE001
        pass
    return out


async def is_broadcast(hass, configured) -> bool:
    """jh-r18-allow (step 160, review round 18 #3, #9): an ALLOWLIST, fail closed. While Private alerts is "Admin phones only",
    the only service that counts as private is an active administrator's OWN Home Assistant app: a notify.mobile_app_<name>
    service that Home Assistant ties to exactly one app registration (jh_phone_service), signed in with an active
    administrator. Everything else may reach other people and is a broadcast: notify.notify (every app), a notify group,
    notify.persistent_notification (Home Assistant shows those to every login), Telegram, Pushover, e-mail, a script, a
    member's or a shared mobile_app service, one no registration is found for, and anything that cannot be read. An owner who
    wants one of those for private alerts picks "The device above" (notify_service), an explicit choice."""
    dom, _sep, svc = str(configured or "").strip().partition(".")
    if dom != "notify" or not svc.startswith("mobile_app_"):
        return True
    try:
        entries = list(hass.config_entries.async_entries("mobile_app"))
    except Exception:  # noqa: BLE001
        return True   # cannot tell whose phone it is: fail closed
    targets = _jh_phone_targets(hass)
    for entry in entries:
        if jh_phone_service(hass, entry, entries, targets) != svc:
            continue
        uid = (getattr(entry, "data", None) or {}).get("user_id")
        try:
            user = await hass.auth.async_get_user(uid) if uid else None
        except Exception:  # noqa: BLE001
            user = None
        if user is not None and getattr(user, "is_admin", False) and getattr(user, "is_active", True) is not False:
            return False   # exactly this administrator's own app
    return True


def _hold(payload: dict, why: str) -> list:
    title = str((payload or {}).get("title") or "JARVIS")
    message = str((payload or {}).get("message") or "")
    _LOGGER.warning("JARVIS: a private alert was NOT delivered (%s). It is kept here, for administrators only. %s: %s",
                    why, title, message[:500])
    return [HELD]


async def send_private(hass, configured, payload: dict, blocking: bool = False) -> list:
    """A private alert to the administrators' phones. Returns the notify services it really went to; [HELD] when it was held
    (written to the administrators-only log, nothing sent); [] means the caller sends it to the configured service: pushes are
    off, the setting is notify_service (the owner's explicit choice), or the configured service is an administrator's own app
    (is_broadcast False). jh-r17-notify (step 159): one phone that fails no longer stops the others.
    jh-r18-closed (step 159b): when administrator phones exist but none took it, or none exists and the configured service is a
    broadcast (is_broadcast), it is held - never sent to a service that may reach other people. jh-r18-allow (step 160):
    is_broadcast is an allowlist, so with no administrator phone only an administrator's own app could still be used."""
    if not str(configured or "").strip() or mode(hass) != ADMIN_PHONES:
        return []
    phones = await private_phones(hass, configured)
    sent = []
    for service in phones:
        try:
            ok = await _call(hass, service, payload, blocking)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("JARVIS: a private alert to notify.%s failed: %s", service, type(exc).__name__)
            ok = False
        if ok:
            sent.append("notify." + service)
    if sent:
        return sent
    if phones:
        return _hold(payload, "no administrator phone took it, and %s may reach other people" % configured)
    if await is_broadcast(hass, configured):
        return _hold(payload, "no administrator phone was found, and %s is no administrator's own phone, so it may reach "
                              "other people" % configured)
    return []


async def send(hass, configured, payload: dict, blocking: bool = False) -> list:
    """For a configured notify.<service> only (the answer watcher): the administrators' phones in admin_phones mode, else the
    configured service. Returns where it went."""
    configured = str(configured or "").strip()
    if not configured.startswith("notify.") or len(configured) <= len("notify."):
        return []
    sent = await send_private(hass, configured, payload, blocking)
    if sent:
        return sent
    return [configured] if await _call(hass, configured.split(".", 1)[1], payload, blocking) else []
