"""Shared names. The websocket commands, the panel path and the photo API keep the names of the former separate
Household Chat integration (household_chat/..., /household-chat, /api/household_chat/...), so the Chat page, phone
notification links and saved bookmarks keep working. Inside Home Assistant the chat lives under JARVIS's own keys."""
DATA = "jarvis_household_chat"                  # hass.data key of the running chat (the old integration used "household_chat")
REGISTERED = "jarvis_household_chat_registered"  # commands and photo views registered once per Home Assistant run
OLD_DOMAIN = "household_chat"                    # the former separate integration
PANEL_PATH = "household-chat"
SIGNAL = "jarvis_household_chat_event"
GROUP = "g:house"
MAX_TEXT = 4000
MAX_PHOTO = 8 * 1024 * 1024
PAGE = 50
TYPING_GAP = 2.5
