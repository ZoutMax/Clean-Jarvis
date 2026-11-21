"""Clean Jarvis (step 139): every finished Assist turn of the other assistants, for the admin-only Chat history page.

JARVIS keeps its own conversations in its database. The other conversation agents (Home Assistant's own, a cloud or local LLM
agent, any assistant) only exist in Home Assistant's pipeline debug list (10 runs, in memory). While the Chat history page is
switched on, this listens to Home Assistant's own conversation chat logs (conversation.chat_log.async_subscribe_chat_logs, the
feed of the conversation debug view) and keeps one question and the answer text of each turn, whatever started it (the Assist
pop-up, voice, the companion app, a script).

Privacy: kept in a private store (.storage/jarvis.assist_log) and handed out only through the admin-only chat history commands
(optional_extras). Never in a sensor, attribute or event. Text only (no thinking, tool calls or tool results), the latest KEEP
turns; an administrator can delete entries, which is saved at once.
JARVIS's own turns are left out (its database has them). JARVIS adds no answer to Home Assistant's chat log, so Home Assistant
closes such a log at once ("deleted") with only the question in it: a turn that has neither an answer nor an agent when its
log is closed is dropped (review round 4). An answer that names a JARVIS entity as its agent is dropped too.
One store and one log per Home Assistant run, whether recording or not (review round 4: two copies overwrote each other's
saves, so deleted text came back). If Home Assistant renames the feed, nothing is recorded and a warning is logged.
"""
from __future__ import annotations

from collections import deque
import logging
from typing import Any

_LOGGER = logging.getLogger(__name__)
KEEP = 300            # turns kept
MAX_TEXT = 8000       # characters kept per question or answer
SAVE_DELAY = 10       # seconds: one write for a burst of new turns
DATA = "jarvis_assist_log"
STORE_KEY = "jarvis.assist_log"


class AssistLog:
    def __init__(self, hass, store, turns: list) -> None:
        self.hass = hass
        self.store = store
        self.turns: deque = deque((t for t in turns if isinstance(t, dict) and t.get("id")), maxlen=KEEP)
        self.open: dict = {}   # conversation_id -> the turn waiting for its answer
        self.unsub = None

    def rows(self) -> list:
        return [t for t in self.turns if t.get("a") or t.get("q")]

    def _data(self) -> dict:
        return {"turns": list(self.turns)}

    async def async_delete(self, ids) -> int:
        """Deletes these turns and writes the store at once."""
        want = {str(i) for i in ids}
        before = len(self.turns)
        self.turns = deque((t for t in self.turns if t.get("id") not in want), maxlen=KEEP)
        self.open = {k: v for k, v in self.open.items() if v.get("id") not in want}
        await self.async_flush()
        return before - len(self.turns)

    async def async_flush(self) -> None:
        try:
            await self.store.async_save(self._data())
        except Exception:  # noqa: BLE001
            _LOGGER.warning("JARVIS: Assist log not saved")

    def _save(self) -> None:
        try:
            self.store.async_delay_save(self._data, SAVE_DELAY)
        except Exception:  # noqa: BLE001
            _LOGGER.debug("Assist log: not saved")

    def _drop(self, turn) -> None:
        self.turns = deque((t for t in self.turns if t is not turn), maxlen=KEEP)
        self._save()

    def _jarvis_agent(self, agent: str) -> bool:
        try:
            from homeassistant.helpers import entity_registry as er
            entry = er.async_get(self.hass).async_get(agent)
            return getattr(entry, "platform", None) == "jarvis"
        except Exception:  # noqa: BLE001
            return False

    def on_event(self, conversation_id: str, event_type: Any, data: dict) -> None:
        try:
            from homeassistant.util import dt as dt_util
            kind = getattr(event_type, "value", event_type)
            if kind == "deleted":
                turn = self.open.pop(conversation_id, None)
                if turn is not None and not turn.get("a") and not turn.get("agent"):
                    self._drop(turn)   # nothing was answered into the log: a JARVIS turn (or none at all)
                return
            if kind != "content_added":
                return
            content = (data or {}).get("content") or {}
            role, text = content.get("role"), content.get("content")
            if role == "user" and isinstance(text, str) and text.strip():
                now = dt_util.utcnow().isoformat()
                turn = {"id": "a:%s:%s" % (conversation_id, now), "t": now, "conv": conversation_id,
                        "agent": "", "agent_name": "", "q": text[:MAX_TEXT], "a": ""}
                self.open[conversation_id] = turn
                self.turns.append(turn)   # kept also when no answer ever comes (the pop-up was closed while it streamed in)
                self._save()
            elif role == "assistant" and isinstance(text, str) and text.strip():
                turn = self.open.get(conversation_id)
                if turn is None:
                    return   # an answer without a question (an announcement, a timer): not a chat
                agent = content.get("agent_id") or ""
                if agent and not turn["agent"]:
                    if self._jarvis_agent(agent):   # JARVIS keeps its own conversations
                        self.open.pop(conversation_id, None)
                        self._drop(turn)
                        return
                    turn["agent"] = agent
                    state = self.hass.states.get(agent)
                    turn["agent_name"] = (state.attributes.get("friendly_name") if state is not None else None) or agent
                # a tool call can come with a first sentence; the final answer follows in a later message of the same turn
                turn["a"] = (((turn["a"] + "\n") if turn["a"] else "") + text)[:MAX_TEXT]
                self._save()
        except Exception:   # a chat must never fail because of this
            _LOGGER.exception("Assist log: turn not recorded")


async def async_load_only(hass):
    """The one log of this Home Assistant run (loaded once), recording or not."""
    log = hass.data.get(DATA)
    if log is not None:
        return log
    from homeassistant.helpers.storage import Store
    store = Store(hass, 1, STORE_KEY, private=True)
    saved = await store.async_load() or {}
    log = hass.data.get(DATA)
    if log is None:   # another caller may have loaded it meanwhile
        log = hass.data[DATA] = AssistLog(hass, store, saved.get("turns") or [])
    return log


def get(hass):
    return hass.data.get(DATA)


async def async_start(hass):
    """Starts recording (once). None when Home Assistant has no chat log feed."""
    try:
        from homeassistant.components.conversation.chat_log import async_subscribe_chat_logs
    except ImportError:
        _LOGGER.warning("JARVIS: Home Assistant has no conversation chat log feed; other assistants' chats are not recorded")
        return None
    log = await async_load_only(hass)
    if log.unsub is None:
        log.unsub = async_subscribe_chat_logs(hass, log.on_event)
    return log


def async_stop(hass) -> None:
    """Stops recording; the log (and its store) stays, and what it holds is written at once."""
    log = hass.data.get(DATA)
    if log is None:
        return
    if log.unsub:
        try:
            log.unsub()
        except Exception:  # noqa: BLE001
            pass
        log.unsub = None
    try:
        hass.async_create_task(log.async_flush())
    except Exception:  # noqa: BLE001
        pass


async def async_remove(hass) -> None:
    """JARVIS removed: the recorded turns go too. No flush first (review round 5: a flush still writing raced the unlink and
    the file came back): the same Store removes the file, which also cancels its pending delayed save."""
    log = hass.data.pop(DATA, None)
    if log is not None and log.unsub:
        try:
            log.unsub()
        except Exception:  # noqa: BLE001
            pass
        log.unsub = None
    try:
        if log is not None:
            await log.store.async_remove()
        else:
            from homeassistant.helpers.storage import Store
            await Store(hass, 1, STORE_KEY, private=True).async_remove()
    except Exception:  # noqa: BLE001
        pass
