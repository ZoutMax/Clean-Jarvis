"""Zero-API localization for the local intent engine's canned replies (#511).

The local fast path (``local_engine.try_local``) answers without any model call,
so its confirmations and query answers were hard-coded English regardless of the
household's ``output_language``. A German user got "Bodenleuchte Flur is currently
off, Sir." This module holds *static* per-language templates and vocabulary so
those replies follow the configured language with **no API call and no added
latency** — exactly the opposite trade-off from routing every reply through a
translation model.

Design:
  * English (``en``) is the canonical catalogue. Any missing language OR any
    missing key falls back to the English template, so an English household is
    byte-for-byte unchanged and a partially-translated language never shows a
    blank.
  * Templates use ``str.format`` named fields. ``t()`` never raises: a bad field
    set falls back to the English template, then to the raw template.
  * Entity friendly-names, the user's honorific, numbers and units pass through
    untouched — only JARVIS's own words are translated.

Adding a language = adding its dict under each table below (ideally
native-speaker reviewed). Nothing else changes.
"""
from __future__ import annotations

from typing import Optional

# ── Varied lead-ins (MCU style; _pick keeps them from repeating) ─────────────
_LEADINS = {
    "en": {
        "ack": ["Done", "Right away", "As you wish", "Consider it done", "At once"],
        "sec": ["Secured", "Done", "Locked up"],
    },
    "de": {
        "ack": ["Erledigt", "Sofort", "Wie Sie wünschen", "Sehr wohl", "Sogleich"],
        "sec": ["Gesichert", "Erledigt", "Verriegelt"],
    },
}

# ── HA state values that surface inside replies ("… is currently {state}") ───
_STATE_WORDS = {
    "de": {
        "on": "an", "off": "aus", "open": "offen", "opening": "öffnet",
        "closed": "geschlossen", "closing": "schließt", "locked": "verriegelt",
        "unlocked": "entriegelt", "home": "zu Hause", "not_home": "abwesend",
        "idle": "inaktiv", "playing": "spielt", "paused": "pausiert",
        "unavailable": "nicht verfügbar", "unknown": "unbekannt",
        "heat": "Heizen", "cool": "Kühlen", "auto": "Automatik",
    },
}

# ── Pluralisable nouns: (singular, plural) per language ──────────────────────
# English plurals reproduce the engine's prior literal output exactly (note the
# intentional "persons", matching the old `person{'s'}` code — do not "fix").
_NOUNS = {
    "en": {
        "light": ("light", "lights"), "lock": ("lock", "locks"),
        "door": ("door", "doors"), "person": ("person", "persons"),
        "device": ("device", "devices"), "fan": ("fan", "fans"),
        "window": ("window", "windows"), "cover": ("cover", "covers"),
        "switch": ("switch", "switches"),
    },
    "de": {
        "light": ("Licht", "Lichter"), "lock": ("Schloss", "Schlösser"),
        "door": ("Tür", "Türen"), "person": ("Person", "Personen"),
        "device": ("Gerät", "Geräte"), "fan": ("Ventilator", "Ventilatoren"),
        "window": ("Fenster", "Fenster"), "cover": ("Rollo", "Rollos"),
        "switch": ("Schalter", "Schalter"),
    },
}

# ── Bulk-action verb as it reads in a summary line ───────────────────────────
# English reproduces the old `action.replace("_", " ")`. German uses the past
# participle, which is where it naturally sits in the sentence.
_BULK_VERB = {
    "en": {
        "turn_on": "turn on", "turn_off": "turn off", "lock": "lock",
        "unlock": "unlock", "open": "open", "close": "close",
    },
    "de": {
        "turn_on": "eingeschaltet", "turn_off": "ausgeschaltet",
        "lock": "verriegelt", "unlock": "entriegelt",
        "open": "geöffnet", "close": "geschlossen",
    },
}

# ── Time-of-day greeting opener ──────────────────────────────────────────────
_GREET = {
    "en": ("Good morning", "Good afternoon", "Good evening"),
    "de": ("Guten Morgen", "Guten Tag", "Guten Abend"),
}

# German weekday / month names so the date reply is actually German (strftime
# emits English regardless of locale). English keeps strftime.
_DE_WEEKDAYS = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag",
                "Samstag", "Sonntag"]
_DE_MONTHS = ["", "Januar", "Februar", "März", "April", "Mai", "Juni", "Juli",
              "August", "September", "Oktober", "November", "Dezember"]

# ── Message templates ────────────────────────────────────────────────────────
_TEMPLATES = {
    "en": {
        # _resp (command acknowledgments)
        "turn_on": "{ack}, {h}. {name} is on.",
        "turn_off": "{ack}, {h}. {name} is off.",
        "toggle": "{ack}, {h}. {name} toggled.",
        "lock": "{sec}, {h}. {name} is locked.",
        "unlock": "{ack}, {h}. {name} is unlocked.",
        "open": "Opening {name} now, {h}.",
        "close": "Closing {name} now, {h}.",
        "dim": "{ack}, {h}. {name} at {bp}%.",
        "brighten": "{ack}, {h}. {name} at full brightness.",
        "set_temp": "{ack}, {h}. Temperature set to {temp}°.",
        "set_temp_named": "{ack}, {h}. {name} set to {temp}°.",
        "media_pause": "Paused, {h}.",
        "media_play": "Playing now, {h}.",
        "media_next": "Next track, {h}.",
        "volume_up": "Volume up, {h}.",
        "volume_down": "Volume down, {h}.",
        "volume_set": "Volume at {vol}%, {h}.",
        "mute": "Muted, {h}.",
        "unmute": "Unmuted, {h}.",
        "resp_fallback": "{ack}, {h}. {action} applied to {name}.",
        "resp_fail": ("I wasn't able to {action} {name}, {h} — there may be a "
                      "connectivity issue."),
        # _query_resp
        "query_time": "It's currently {time}, {h}.",
        "query_date": "Today is {date}, {h}.",
        "greet_plain": "{g}, {h}.",
        "greet_help": "{g}, {h}. What can I do for you?",
        "greet_disposal": "{g}, {h}. At your disposal.",
        "thanks_1": "Of course, {h}.",
        "thanks_2": "My pleasure, {h}.",
        "thanks_3": "Anytime, {h}.",
        "query_temp": "The temperature in {name} is currently {state}{unit}, {h}.",
        "query_temp_fail": ("I'm unable to read the temperature for {name} at the "
                            "moment, {h}."),
        "query_state": "{name} is currently {state}, {h}.",
        "query_state_fail": ("I'm unable to determine the status of {name} at the "
                             "moment, {h}."),
        # _home_status
        "status_header": "Home status, {h}.",
        "status_lights": "{n} {noun} on.",
        "status_locks_ok": "All locks secured.",
        "status_locks_unlocked": "{n} {noun} unlocked.",
        "status_doors": "{n} {noun} open.",
        "status_people": "{n} {noun} home.",
        # _ctx_query
        "who_none": "No one appears to be home at the moment, {h}.",
        "who_one": "{name} is currently home, {h}.",
        "who_many": "{names} and {last} are currently home, {h}.",
        "open_none": "Everything is closed and secured, {h}.",
        "open_list": "Currently open or unlocked: {items}.",
        "open_unlocked_suffix": " (unlocked)",
        "lights_none": "All lights are off, {h}.",
        "lights_list": "{c} {noun} on: {listing}{ellipsis}.",
        "energy": "Current power consumption is {state} {unit}, {h}.",
        "energy_none": "I don't have a total power sensor configured, {h}.",
        "weather": ("Currently {condition} outside, {h}. Temperature is {temp}° "
                    "with {humidity}% humidity."),
        "weather_outdoor": "The outdoor temperature is {state}{unit}, {h}.",
        "weather_none": "I don't have weather data available at the moment, {h}.",
        "area_none": "I couldn't find an area matching '{area}', {h}.",
        "area_empty": "No devices found in {name}, {h}.",
        "area_list": "{name} has {n} {noun}: {listing}{more}.",
        "area_more": " and {n} more",
        # try_local inline
        "bulk_alloff": "Done, {h}. {total} {noun} turned off.",
        "bulk_generic": "Done, {h}. {ok} {noun}{area} {verb}.",
        "area_in": " in {area}",
        "scene_activating": "Activating {name} now, {h}.",
        "scene_fail": "I wasn't able to activate {name}, {h}. {exc}",
        "goodnight_scene": "Goodnight, {h}. {name} activated. Rest well.",
        "goodnight_summary": "Goodnight, {h}. {n} {noun} off. Rest well.",   # Clean Jarvis: no lock claim
        "offline_notfound": ("I couldn't find a device matching '{name}', {h}, and "
                             "I'm offline so I can't do a deeper search right now. "
                             "Try the exact device name."),
        "appliance_tracking": ("Already on it, {h}. I'm monitoring {items}. I'll "
                               "announce when the cycle completes."),
        "appliance_nosensors": ("I'm monitoring for appliance cycles, {h}, but I "
                                "haven't found any power sensors matching your "
                                "appliances yet. Make sure the power monitoring "
                                "sensor has 'washer', 'dryer', or 'dishwasher' in "
                                "its name."),
        "appliance_notrunning": ("The appliance monitor isn't running at the "
                                 "moment, {h}. It starts automatically with the "
                                 "observer. Check if the observer is enabled."),
        "appliance_generic": ("I'll keep an eye on it, {h}. If I have a power "
                              "sensor for that appliance, I'll announce when the "
                              "cycle finishes."),
        # Clean Jarvis (Jarvis Home patch): honest all-clear and temperature texts
        "jh_no_sensors": "I have no door, window or lock sensors in this home, {h}.",
        "jh_not_reporting": "Not reporting: {items}.",
        "jh_open_none_dark": "Nothing reports open, {h}, but these are not reporting: {items}.",
        "jh_temp_none": "{name} does not report a temperature at the moment, {h}.",
        "jh_manual_cmd": "{name} starts with its own button, {h}; its plug stays on, so I do not switch it.",
        "jh_manual_now_heating": "It is heating now ({w} W).",
        "jh_manual_now_standby": "It is standing by ({w} W).",
        "jh_manual_now_off": "Its plug is off, so it cannot heat.",
        "jh_manual_now_unavailable": "Its plug is not reporting.",
        "jh_manual_now_noreading": "Its plug is on; I have no power reading for it.",
        "jh_manual_q_heating": "{name} is heating ({w} W), {h}.",
        "jh_manual_q_standby": "{name} is standing by ({w} W), {h}.",
        "jh_manual_q_off": "{name} is off at the plug, {h}, so it cannot heat.",
        "jh_manual_q_unavailable": "{name} is not reporting, {h}.",
        "jh_manual_q_noreading": "The plug of {name} is on, {h}, but I have no power reading for it.",
        # Clean Jarvis (jh-local-lang): the kit's own instant replies
        "jh_nothing_changed": "Nothing was changed.",
        "jh_which": "Which one, {h}: {opts} or {last}?",
        # jh-r24-kind (step 166, review round 24 #1): a group order whose kind of device the home does not have
        "jh_kind_none": "I found no {what}, {h}. Nothing was switched.",
        # jh-r25 (step 167, review round 25 #2, #3, #4, #12, #13)
        "jh_goodnight_ask_routine": "Good night, {h}. Shall I run your {name} routine?",
        "jh_gn_locks_open": "Still not locked: {names}.",
        "jh_group_locks_incl": "Garage and gate locks included: {names}.",
        "jh_media_nothing": "Nothing is playing on {name}, {h}. Nothing was changed.",
        "jh_media_off": "{name} is off, {h}. Nothing was changed.",
        "jh_lbl_hidden": "hidden",
        "jh_lbl_manual": "manual heater",
        # jh-r27 (step 170, review round 27 #1)
        "jh_lbl_unknown": "unknown kind",
        "jh_lbl_nogroup": "never in groups",
        "jh_group_unknown": "Not in group orders (unknown kind): {names}.",
        "jh_group_nogroup": "Never in groups (label): {names}.",
        # jh-r23-bulk (step 165, review round 23 #1, #2): the group-order and same-name replies
        "jh_no_bulk_unlock": "I don't unlock every lock at once, {h}. Tell me which door to unlock. Nothing was unlocked.",
        "jh_bulk_left_out": "Left out, as a group order never moves them: {names}; name one to switch it.",
        "jh_bulk_left_out_all": "Nothing was switched, {h}: a group order never moves {names}. Name the one you mean.",
        "jh_cant_tell": "More than one device has that name and I can't tell them apart, {h}. Give one of them another name in "
                        "Home Assistant. Nothing was changed.",
        "jh_do_you_mean": "Do you mean {name}?",
        "jh_list": "{items} and {last}",
        "jh_offline_nochange": "{name} is offline, {h}. Nothing was changed.",
        "jh_unavailable_nochange": "{name} is unavailable, {h}. Nothing was changed.",
        "jh_unavail_one": "{names} is unavailable, {h}.",
        "jh_unavail_many": "{names} are unavailable, {h}.",
        "jh_unavail_tail_one": "{names} is unavailable.",
        "jh_unavail_tail_many": "{names} are unavailable.",
        "jh_already_one_on": "{name} is already on, {h}.",
        "jh_already_one_off": "{name} is already off, {h}.",
        "jh_state_unconfirmed": "Sent, {h}, but {name} has not confirmed it (it is {state}).",   # jh-r11-confirm
        "jh_not_confirmed": "Sent but not confirmed: {names}.",
        "jh_none_confirmed": "Sent, {h}, but nothing has confirmed it yet.",   # jh-r12-act
        "jh_none_switched": "Nothing was switched, {h}.",   # jh-r13-failed
        "jh_no_media_in_room": "There is no media player in {room}, {h}. Nothing was changed.",   # jh-r17-room (step 159)
        "jh_no_climate_in_room": "There is no thermostat in {room}, {h}. Nothing was changed.",
        "jh_not_switched": "Not switched: {names}.",
        "jh_already_many_on": "They are all already on, {h}.",
        "jh_already_many_off": "They are all already off, {h}.",
        "jh_lead_on_one": "{names} is already on, {h}.",
        "jh_lead_on_many": "{names} are already on, {h}.",
        "jh_no_colours": "{name} cannot show colours, {h}.",
        "jh_colour_fail": "I wasn't able to set {name} to {value}, {h}.",
        "jh_colour_done": "Done, {h}. {name} is {value}.",
        "jh_sent_unconfirmed": "I sent {value} to {name}, {h}, but it has not confirmed the change.",
        "jh_effect_fail": "I wasn't able to start {value} on {name}, {h}.",
        "jh_effect_done": "Done, {h}. {name} is playing {value}.",
        "jh_wrong_kind": "{name} is {kind}, {h}, so I can't {do}. Nothing was changed.",
        "jh_power_now": "Right now the {n} monitored devices draw {total}, {h}.",
        "jh_power_most": "Most of it: {items}.",
        "jh_power_unread": "{names} could not be read.",
        "jh_power_more": "There is no whole-house meter, so the house uses more.",
        "jh_hstate_one": "{names} is {state}",
        "jh_hstate_many": "{names} are {state}",
        "jh_heat_standing_by": "on, standing by",
        "jh_heat_energy_today": "Today the heaters used {total} kWh, {h}: {items}.",
        "jh_heat_energy_missing": "No daily reading for {names}.",
        "jh_online": "{name} is online, {h}.",
        "jh_offline_since": "{name} has been offline since {when}, {h}.",
        "jh_offline_now": "{name} is offline, {h}.",
        "jh_when_day": "{date}, {time}",
        "jh_wx_today": "Today: {parts}.",
        "jh_wx_hilo": "high {hi}°, low {lo}°",
        "jh_wx_hi": "high {hi}°",
        "jh_wx_rain": "{mm} mm rain",
        "jh_wx_pct": "{pct}% chance of rain",
        "jh_cam_fail": "I could not get a picture from {name}, {h}.",
        "jh_goodnight_ask": "Good night, {h}. Shall I switch off the lights and lock up?",
        "jh_room_done": "Done, {h}. {n} of {total} lights in {room} {what}.",
        "jh_room_turn_on": "on", "jh_room_turn_off": "off", "jh_room_dim": "at {pct}%", "jh_room_brighten": "at full brightness",
        "jh_days_until_one": "There is 1 day until {what} ({date}), {h}.",
        "jh_days_until_many": "There are {n} days until {what} ({date}), {h}.",
        "jh_no_lock": "There's no lock on the {what}, {h}. Nothing was changed.",
        "jh_can_intro": "Here, {h}, I can {items}.",
        "jh_can_light": "switch and dim the lights", "jh_can_heat": "control the heating", "jh_can_fan": "switch the fans and heater plugs",
        "jh_can_switch": "switch plugs and switches", "jh_can_cover": "open and close covers", "jh_can_lock": "lock and unlock",
        "jh_can_media": "control the media players", "jh_can_scene": "start scenes", "jh_can_camera": "look through the cameras",
        "jh_can_weather": "tell you the weather and the forecast", "jh_can_calendar": "read your calendar",
        "jh_can_person": "tell you who is home", "jh_can_energy": "report power and energy use",
        "jh_can_talk": "answer questions and set reminders",
        "jh_journal_on": "on", "jh_journal_off": "off",
        "jh_journal_none": "I have not switched anything today, {h}.",
        "jh_skip_none": "Nothing was changed, {h}: {why}.", "jh_skip_same": "{names} already {state}",
        "jh_skip_gone": "{names} unavailable",
        "jh_journal_since": "(I have only kept track since {time}, when I last started.)",
        "jh_journal_list_on": "Today I switched on: {items}, {h}.",
        "jh_journal_list_off": "Today I switched off: {items}, {h}.",
        "jh_journal_list_any": "Today I did this: {items}, {h}.",
        "jh_dur_m": "{m} minutes", "jh_dur_h": "{h} hours", "jh_dur_hm": "{h} hours and {m} minutes", "jh_dur_days": "{n} days",
        "jh_how_long": "{name} has been {state} for {dur} (since {since}), {h}.",
        "jh_how_long_not": "{name} is {state} now, {h} (since {since}).",
        "jh_cost_today": "So far today: {total} {unit}, {h} ({items}).",
        "jh_filler_ack": "Very good, {h}.",
        "jh_vague_lead_all": "Do all of what", "jh_vague_lead_on": "Turn on what", "jh_vague_lead_off": "Turn off what",
        "jh_vague_lead_do": "Do what",
        "jh_kind_word_lights": "lights", "jh_kind_word_heaters": "heaters",
        "jh_and_more": " and {n} more",
        "jh_vague_propose_off": "Turn off the {kind} that are on now ({names})?",
        "jh_vague_propose_on": "Turn on the {kind} that are off now ({names})?",
        "jh_vague_none_off": "No {kind} are on right now. Tell me what you would like me to do.",
        "jh_vague_none_on": "No {kind} are off right now. Tell me what you would like me to do.",
        "jh_vague_nothing": "I have nothing recent to go on. Tell me what to do, for example \"turn off the living room lights\".",
    },
    "de": {
        "turn_on": "{ack}, {h}. {name} ist an.",
        "turn_off": "{ack}, {h}. {name} ist aus.",
        "toggle": "{ack}, {h}. {name} umgeschaltet.",
        "lock": "{sec}, {h}. {name} ist verriegelt.",
        "unlock": "{ack}, {h}. {name} ist entriegelt.",
        "open": "{name} wird geöffnet, {h}.",
        "close": "{name} wird geschlossen, {h}.",
        "dim": "{ack}, {h}. {name} auf {bp}%.",
        "brighten": "{ack}, {h}. {name} auf voller Helligkeit.",
        "set_temp": "{ack}, {h}. Temperatur auf {temp}° gestellt.",
        "set_temp_named": "{ack}, {h}. {name} auf {temp}° gestellt.",
        "media_pause": "Pausiert, {h}.",
        "media_play": "Wird abgespielt, {h}.",
        "media_next": "Nächster Titel, {h}.",
        "volume_up": "Lauter, {h}.",
        "volume_down": "Leiser, {h}.",
        "volume_set": "Lautstärke auf {vol}%, {h}.",
        "mute": "Stummgeschaltet, {h}.",
        "unmute": "Stummschaltung aufgehoben, {h}.",
        "resp_fallback": "{ack}, {h}. Aktion auf {name} angewendet.",
        "resp_fail": ("Ich konnte {name} nicht schalten, {h} — möglicherweise ein "
                      "Verbindungsproblem."),
        "query_time": "Es ist jetzt {time}, {h}.",
        "query_date": "Heute ist {date}, {h}.",
        "greet_plain": "{g}, {h}.",
        "greet_help": "{g}, {h}. Was kann ich für Sie tun?",
        "greet_disposal": "{g}, {h}. Zu Ihren Diensten.",
        "thanks_1": "Gern geschehen, {h}.",
        "thanks_2": "Sehr gerne, {h}.",
        "thanks_3": "Jederzeit, {h}.",
        "query_temp": "Die Temperatur in {name} beträgt derzeit {state}{unit}, {h}.",
        "query_temp_fail": ("Ich kann die Temperatur für {name} momentan nicht "
                            "auslesen, {h}."),
        "query_state": "{name} ist derzeit {state}, {h}.",
        "query_state_fail": ("Ich kann den Status von {name} momentan nicht "
                             "ermitteln, {h}."),
        "status_header": "Hausstatus, {h}.",
        "status_lights": "{n} {noun} an.",
        "status_locks_ok": "Alle Schlösser gesichert.",
        "status_locks_unlocked": "{n} {noun} entriegelt.",
        "status_doors": "{n} {noun} offen.",
        "status_people": "{n} {noun} zu Hause.",
        "who_none": "Momentan scheint niemand zu Hause zu sein, {h}.",
        "who_one": "{name} ist derzeit zu Hause, {h}.",
        "who_many": "{names} und {last} sind derzeit zu Hause, {h}.",
        "open_none": "Alles ist geschlossen und gesichert, {h}.",
        "open_list": "Derzeit offen oder entriegelt: {items}.",
        "open_unlocked_suffix": " (entriegelt)",
        "lights_none": "Alle Lichter sind aus, {h}.",
        "lights_list": "{c} {noun} an: {listing}{ellipsis}.",
        "energy": "Aktueller Stromverbrauch: {state} {unit}, {h}.",
        "energy_none": "Ich habe keinen Gesamt-Stromsensor konfiguriert, {h}.",
        "weather": ("Draußen derzeit {condition}, {h}. Temperatur {temp}° bei "
                    "{humidity}% Luftfeuchtigkeit."),
        "weather_outdoor": "Die Außentemperatur beträgt {state}{unit}, {h}.",
        "weather_none": "Mir liegen momentan keine Wetterdaten vor, {h}.",
        "area_none": "Ich konnte keinen Bereich finden, der zu '{area}' passt, {h}.",
        "area_empty": "Keine Geräte in {name} gefunden, {h}.",
        "area_list": "{name} hat {n} {noun}: {listing}{more}.",
        "area_more": " und {n} weitere",
        "bulk_alloff": "Erledigt, {h}. {total} {noun} ausgeschaltet.",
        "bulk_generic": "Erledigt, {h}. {ok} {noun}{area} {verb}.",
        "area_in": " in {area}",
        "scene_activating": "{name} wird aktiviert, {h}.",
        "scene_fail": "Ich konnte {name} nicht aktivieren, {h}. {exc}",
        "goodnight_scene": "Gute Nacht, {h}. {name} aktiviert. Schlafen Sie gut.",
        "goodnight_summary": "Gute Nacht, {h}. {n} {noun} aus. Schlafen Sie gut.",
        "offline_notfound": ("Ich konnte kein Gerät finden, das zu '{name}' passt, "
                             "{h}, und ich bin offline, kann also gerade nicht "
                             "tiefer suchen. Versuchen Sie den genauen "
                             "Gerätenamen."),
        "appliance_tracking": ("Bin bereits dran, {h}. Ich überwache {items}. Ich "
                               "melde mich, wenn der Zyklus abgeschlossen ist."),
        "appliance_nosensors": ("Ich achte auf Geräte-Zyklen, {h}, habe aber noch "
                                "keine Leistungssensoren gefunden, die zu Ihren "
                                "Geräten passen. Stellen Sie sicher, dass der "
                                "Leistungssensor 'washer', 'dryer' oder "
                                "'dishwasher' im Namen trägt."),
        "appliance_notrunning": ("Die Geräteüberwachung läuft momentan nicht, {h}. "
                                 "Sie startet automatisch mit dem Observer. Prüfen "
                                 "Sie, ob der Observer aktiviert ist."),
        "appliance_generic": ("Ich behalte es im Auge, {h}. Wenn ich einen "
                              "Leistungssensor für dieses Gerät habe, melde ich "
                              "mich, wenn der Zyklus endet."),
        "jh_no_sensors": "In diesem Zuhause habe ich keine Tür-, Fenster- oder Schlosssensoren, {h}.",
        "jh_not_reporting": "Keine Meldung von: {items}.",
        "jh_open_none_dark": "Nichts meldet offen, {h}, aber diese melden sich nicht: {items}.",
        "jh_temp_none": "{name} meldet derzeit keine Temperatur, {h}.",
        "jh_manual_cmd": "{name} startet mit der eigenen Taste, {h}; der Stecker bleibt an, darum schalte ich ihn nicht.",
        "jh_manual_now_heating": "Er heizt gerade ({w} W).",
        "jh_manual_now_standby": "Er ist in Bereitschaft ({w} W).",
        "jh_manual_now_off": "Sein Stecker ist aus, er kann also nicht heizen.",
        "jh_manual_now_unavailable": "Sein Stecker meldet sich nicht.",
        "jh_manual_now_noreading": "Sein Stecker ist an; ich habe keinen Leistungswert dafür.",
        "jh_manual_q_heating": "{name} heizt ({w} W), {h}.",
        "jh_manual_q_standby": "{name} ist in Bereitschaft ({w} W), {h}.",
        "jh_manual_q_off": "{name} ist am Stecker aus, {h}, und kann nicht heizen.",
        "jh_manual_q_unavailable": "{name} meldet sich nicht, {h}.",
        "jh_manual_q_noreading": "Der Stecker von {name} ist an, {h}, aber ich habe keinen Leistungswert dafür.",
        "jh_nothing_changed": "Es wurde nichts geändert.",
        "jh_which": "Welches, {h}: {opts} oder {last}?",
        "jh_kind_none": "Ich habe keine {what} gefunden, {h}. Es wurde nichts geschaltet.",
        "jh_goodnight_ask_routine": "Gute Nacht, {h}. Soll ich Ihre Routine {name} ausführen?",
        "jh_gn_locks_open": "Noch nicht verriegelt: {names}.",
        "jh_group_locks_incl": "Garagen- und Torschlösser eingeschlossen: {names}.",
        "jh_media_nothing": "Auf {name} läuft nichts, {h}. Es wurde nichts geändert.",
        "jh_media_off": "{name} ist aus, {h}. Es wurde nichts geändert.",
        "jh_lbl_hidden": "ausgeblendet",
        "jh_lbl_manual": "manuelle Heizung",
        "jh_lbl_unknown": "unbekannte Art",
        "jh_lbl_nogroup": "nie in Gruppen",
        "jh_group_unknown": "Nicht in Gruppenbefehlen (unbekannte Art): {names}.",
        "jh_group_nogroup": "Nie in Gruppen (Label): {names}.",
        "jh_no_bulk_unlock": "Ich entriegle nicht alle Schlösser auf einmal, {h}. Sagen Sie mir, welche Tür. Es wurde nichts entriegelt.",
        "jh_bulk_left_out": "Ausgelassen, da ein Gruppenbefehl sie nie bewegt: {names}; nennen Sie eines, um es zu schalten.",
        "jh_bulk_left_out_all": "Es wurde nichts geschaltet, {h}: ein Gruppenbefehl bewegt {names} nie. Nennen Sie das Gerät.",
        "jh_cant_tell": "Mehrere Geräte haben diesen Namen und ich kann sie nicht unterscheiden, {h}. Geben Sie einem davon in Home "
                        "Assistant einen anderen Namen. Es wurde nichts geändert.",
        "jh_do_you_mean": "Meinen Sie {name}?",
        "jh_list": "{items} und {last}",
        "jh_offline_nochange": "{name} ist offline, {h}. Es wurde nichts geändert.",
        "jh_unavailable_nochange": "{name} ist nicht verfügbar, {h}. Es wurde nichts geändert.",
        "jh_unavail_one": "{names} ist nicht verfügbar, {h}.",
        "jh_unavail_many": "{names} sind nicht verfügbar, {h}.",
        "jh_unavail_tail_one": "{names} ist nicht verfügbar.",
        "jh_unavail_tail_many": "{names} sind nicht verfügbar.",
        "jh_already_one_on": "{name} ist bereits an, {h}.",
        "jh_already_one_off": "{name} ist bereits aus, {h}.",
        "jh_state_unconfirmed": "Gesendet, {h}, aber {name} hat es nicht bestätigt (Zustand: {state}).",
        "jh_not_confirmed": "Gesendet, aber nicht bestätigt: {names}.",
        "jh_none_confirmed": "Gesendet, {h}, aber noch nichts hat es bestätigt.",
        "jh_none_switched": "Es wurde nichts geschaltet, {h}.",
        "jh_no_media_in_room": "In {room} gibt es keinen Mediaplayer, {h}. Es wurde nichts geändert.",
        "jh_no_climate_in_room": "In {room} gibt es kein Thermostat, {h}. Es wurde nichts geändert.",
        "jh_not_switched": "Nicht geschaltet: {names}.",
        "jh_already_many_on": "Sie sind alle bereits an, {h}.",
        "jh_already_many_off": "Sie sind alle bereits aus, {h}.",
        "jh_lead_on_one": "{names} ist bereits an, {h}.",
        "jh_lead_on_many": "{names} sind bereits an, {h}.",
        "jh_no_colours": "{name} kann keine Farben zeigen, {h}.",
        "jh_colour_fail": "Ich konnte {name} nicht auf {value} stellen, {h}.",
        "jh_colour_done": "Erledigt, {h}. {name} ist {value}.",
        "jh_sent_unconfirmed": "Ich habe {value} an {name} gesendet, {h}, aber die Änderung ist nicht bestätigt.",
        "jh_effect_fail": "Ich konnte {value} auf {name} nicht starten, {h}.",
        "jh_effect_done": "Erledigt, {h}. {name} spielt {value}.",
        "jh_wrong_kind": "{name} ist {kind}, {h}, darum kann ich {do}. Es wurde nichts geändert.",
        "jh_kind_light": "eine Lampe", "jh_kind_switch": "ein Schalter", "jh_kind_lock": "ein Schloss",
        "jh_kind_cover": "eine Abdeckung", "jh_kind_climate": "ein Klimagerät", "jh_kind_fan": "ein Ventilator",
        "jh_kind_media_player": "ein Mediaplayer",
        "jh_do_open": "es nicht öffnen", "jh_do_close": "es nicht schließen", "jh_do_lock": "es nicht verriegeln",
        "jh_do_unlock": "es nicht entriegeln", "jh_do_dim": "es nicht dimmen", "jh_do_brighten": "es nicht heller machen",
        "jh_do_set_temp_named": "seine Temperatur nicht einstellen",
        "jh_power_now": "Gerade ziehen die {n} überwachten Geräte {total}, {h}.",
        "jh_power_most": "Davon am meisten: {items}.",
        "jh_power_unread": "{names} konnten nicht gelesen werden.",
        "jh_power_more": "Es gibt keinen Zähler für das ganze Haus, das Haus verbraucht also mehr.",
        "jh_hstate_one": "{names} ist {state}",
        "jh_hstate_many": "{names} sind {state}",
        "jh_heat_on": "an", "jh_heat_heating": "am Heizen", "jh_heat_standing_by": "an, in Bereitschaft",
        "jh_heat_energy_today": "Heute haben die Heizungen {total} kWh verbraucht, {h}: {items}.",
        "jh_heat_energy_missing": "Kein Tageswert für {names}.",
        "jh_online": "{name} ist online, {h}.",
        "jh_offline_since": "{name} ist seit {when} offline, {h}.",
        "jh_offline_now": "{name} ist offline, {h}.",
        "jh_when_day": "{date}, {time}",
        "jh_wx_today": "Heute: {parts}.",
        "jh_wx_hilo": "Höchstwert {hi}°, Tiefstwert {lo}°",
        "jh_wx_hi": "Höchstwert {hi}°",
        "jh_wx_rain": "{mm} mm Regen",
        "jh_wx_pct": "{pct}% Regenwahrscheinlichkeit",
        "jh_cam_fail": "Ich konnte kein Bild von {name} bekommen, {h}.",
        "jh_goodnight_ask": "Gute Nacht, {h}. Soll ich die Lichter ausschalten und abschließen?",
        "jh_room_done": "Erledigt, {h}. {n} von {total} Lichtern in {room} {what}.",
        "jh_room_turn_on": "an", "jh_room_turn_off": "aus", "jh_room_dim": "auf {pct}%", "jh_room_brighten": "auf voller Helligkeit",
        "jh_days_until_one": "Es ist noch 1 Tag bis {what} ({date}), {h}.",
        "jh_days_until_many": "Es sind noch {n} Tage bis {what} ({date}), {h}.",
        "jh_no_lock": "Es gibt kein Schloss an {what}, {h}. Es wurde nichts geändert.",
        "jh_can_intro": "Hier kann ich, {h}: {items}.",
        "jh_can_light": "Lichter schalten und dimmen", "jh_can_heat": "die Heizung steuern", "jh_can_fan": "Ventilatoren und Heizstecker schalten",
        "jh_can_switch": "Stecker und Schalter schalten", "jh_can_cover": "Rollläden öffnen und schließen", "jh_can_lock": "ab- und aufschließen",
        "jh_can_media": "Mediaplayer steuern", "jh_can_scene": "Szenen starten", "jh_can_camera": "durch die Kameras schauen",
        "jh_can_weather": "das Wetter und die Vorhersage sagen", "jh_can_calendar": "Ihren Kalender vorlesen",
        "jh_can_person": "sagen, wer zu Hause ist", "jh_can_energy": "Strom- und Energieverbrauch melden",
        "jh_can_talk": "Fragen beantworten und Erinnerungen setzen",
        "jh_journal_on": "an", "jh_journal_off": "aus",
        "jh_journal_none": "Ich habe heute nichts geschaltet, {h}.",
        "jh_skip_none": "Es wurde nichts geändert, {h}: {why}.", "jh_skip_same": "{names} schon {state}",
        "jh_skip_gone": "{names} nicht erreichbar",
        "jh_journal_since": "(Ich zähle erst seit {time} mit, seit meinem letzten Start.)",
        "jh_journal_list_on": "Heute habe ich eingeschaltet: {items}, {h}.",
        "jh_journal_list_off": "Heute habe ich ausgeschaltet: {items}, {h}.",
        "jh_journal_list_any": "Heute habe ich das getan: {items}, {h}.",
        "jh_dur_m": "{m} Minuten", "jh_dur_h": "{h} Stunden", "jh_dur_hm": "{h} Stunden und {m} Minuten", "jh_dur_days": "{n} Tagen",
        "jh_how_long": "{name} ist seit {dur} {state} (seit {since}), {h}.",
        "jh_how_long_not": "{name} ist jetzt {state}, {h} (seit {since}).",
        "jh_cost_today": "Bisher heute: {total} {unit}, {h} ({items}).",
        "jh_filler_ack": "Sehr wohl, {h}.",
        "jh_vague_lead_all": "Alles was", "jh_vague_lead_on": "Was soll ich einschalten", "jh_vague_lead_off": "Was soll ich ausschalten",
        "jh_vague_lead_do": "Was soll ich tun",
        "jh_kind_word_lights": "Lichter", "jh_kind_word_heaters": "Heizungen",
        "jh_and_more": " und {n} weitere",
        "jh_vague_propose_off": "Die {kind} ausschalten, die gerade an sind ({names})?",
        "jh_vague_propose_on": "Die {kind} einschalten, die gerade aus sind ({names})?",
        "jh_vague_none_off": "Gerade sind keine {kind} an. Sagen Sie mir, was ich tun soll.",
        "jh_vague_none_on": "Gerade sind keine {kind} aus. Sagen Sie mir, was ich tun soll.",
        "jh_vague_nothing": "Ich habe nichts Aktuelles, woran ich mich halten kann. Sagen Sie mir, was ich tun soll, zum Beispiel \"schalte die Wohnzimmerlichter aus\".",
        "jh_heat_on_no_power_reading": "an (kein Leistungswert)", "jh_heat_off": "aus", "jh_heat_unavailable": "nicht verfügbar",
    },
}


def _norm(lang: Optional[str]) -> str:
    """Primary subtag, lowercased; '' / None → 'en'."""
    if not lang:
        return "en"
    return str(lang).split("-")[0].strip().lower() or "en"


def t(key: str, lang: Optional[str] = "en", **kw) -> str:
    """Render message ``key`` in ``lang``; never raises.

    Falls back to the English template when the language or key is missing, and
    again (bare template) if a format field is absent, so a response is always
    produced."""
    code = _norm(lang)
    en = _TEMPLATES["en"]
    tmpl = _TEMPLATES.get(code, {}).get(key)
    if tmpl is None:
        tmpl = en.get(key, "")
    try:
        return tmpl.format(**kw)
    except Exception:
        try:
            return en.get(key, "").format(**kw)
        except Exception:
            return en.get(key, "")


def leadins(kind: str, lang: Optional[str] = "en") -> list:
    """The varied lead-in words for ``kind`` ('ack' / 'sec') in ``lang``."""
    code = _norm(lang)
    table = _LEADINS.get(code) or _LEADINS["en"]
    return table.get(kind) or _LEADINS["en"].get(kind, [""])


def greet_word(hour: int, lang: Optional[str] = "en") -> str:
    """Time-of-day greeting opener for ``hour`` (0–23) in ``lang``."""
    code = _norm(lang)
    morning, afternoon, evening = _GREET.get(code) or _GREET["en"]
    if hour < 12:
        return morning
    if hour < 18:
        return afternoon
    return evening


def state_word(state, lang: Optional[str] = "en") -> str:
    """Translate an HA state value ('on'/'off'/'open'/…) for display.

    Unknown states (and all English) pass through unchanged, so a numeric or
    free-form state is never mangled."""
    code = _norm(lang)
    if code == "en":
        return str(state)
    return _STATE_WORDS.get(code, {}).get(str(state), str(state))


def noun(key: str, n: int, lang: Optional[str] = "en") -> str:
    """The singular/plural form of a known noun for count ``n`` in ``lang``.

    Falls back to the English noun table, then to the raw key, so an unmapped
    noun still renders."""
    code = _norm(lang)
    forms = _NOUNS.get(code, {}).get(key) or _NOUNS["en"].get(key)
    if not forms:
        return key
    return forms[0] if n == 1 else forms[1]


def bulk_verb(action: str, lang: Optional[str] = "en") -> str:
    """How a bulk action reads in a summary line ('turn on' / 'eingeschaltet')."""
    code = _norm(lang)
    table = _BULK_VERB.get(code) or _BULK_VERB["en"]
    return table.get(action) or _BULK_VERB["en"].get(action, action.replace("_", " "))


def format_time(dt, lang: Optional[str] = "en") -> str:
    """Localised clock time. English keeps 12-hour AM/PM; others use 24-hour."""
    code = _norm(lang)
    if code == "en":
        return dt.strftime("%I:%M %p")
    return dt.strftime("%H:%M")


def format_date(dt, lang: Optional[str] = "en") -> str:
    """Localised long date. English uses strftime; German uses its own names."""
    code = _norm(lang)
    if code == "de":
        return f"{_DE_WEEKDAYS[dt.weekday()]}, {dt.day}. {_DE_MONTHS[dt.month]} {dt.year}"
    return dt.strftime("%A, %B %d, %Y")
