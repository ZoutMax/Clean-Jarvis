"""Jarvis Home patch kit - re-applies our JARVIS (Jarvis-AIO) modifications after an update.

Idempotent: every patch checks for its marker first, so running it twice changes nothing.
All or nothing: patches run on a copy in memory and files are only written when every patch applied. A failure exits with code 1.
Run inside the HA container:   docker exec homeassistant python3 /config/jarvis_patches/apply.py
(or with a different JARVIS folder as first argument, for testing). Afterwards: restart Home Assistant,
then reload the JARVIS integration and press Ctrl+F5 in the browser.

Patches (2026-11-03/04):
 1 llm_provider.py  reasoning_effort "none" when not thinking (Ollama ignores think:false) + keep model loaded
 2 agent.py         live context / per-message recall moved to the latest user message (prompt cache reuse)
 3 agent.py         "Honesty about this house" rule
 4 conversation.py  per-message memory/knowledge marked so agent.py can move it (part of 2)
 5 websocket.py     Residence subtitle shows who is home when there are no room sensors
 6 jarvis-panel.js  Residence ALL view = wireframe model of the apartment (residence-model.js)
 7 jarvis-panel.js  Camera Watch always opens on the camera chosen in Core settings, not the last-clicked one
 8 agent.py         entity last_changed passed to the model in local time instead of UTC
 9 jarvis-panel.js  all default Residence styles stay; My residence and My Resid are added after them
10 /config/www      Remote page card + photo and saved-history card restored from the kit
11 jarvis-panel.js  Residence list: My apartment (wireframe) + My apart low (same model as soft glass); floor tabs hidden
12 agent.py         after read_email, only read-only tools work for the rest of that request (fake mail cannot act)
13 agent.py         actions must really run: a device command with no tool call is sent back once, then a fixed honest line
14 briefing.py + proactive_briefing.py  spoken briefings read real facts only, no AI text (the model invented locks and garages)
15 briefing.py + proactive_briefing.py  briefing text cleaned for speech (units, weather words, no workday calendar)
18 local_engine.py  fixed quick answers no longer claim locks are secured or all is closed when no such devices exist
20 local_engine.py  forecast questions reach the forecast tool
22 const.py         clean base plus JARVIS voice: truth rules first, calm style with sir, no backstory or house examples; wit is left to banter_level
23 agent.py         the floor plan is the true room list; other HA areas are labels
24 agent.py         exact light counts and heater states computed in code and given as live facts
25 jarvis-panel.js  Chat tab: history saved per user, Clean button at the title, red cross on every message
26 memory.py + conversation.py  chat memory and threads separated per HA user, automatic for new users
30 local_engine.py  name matching is strict; config entities such as backlights are never command targets
31 const.py + migrations.py  clean_jarvis is a real directive preset and the default; other presets work again
32 agent.py         after a command, wait up to 4 s for the device to report the expected state
33 jarvis-panel.js  Chat box always fills the window, messages scroll inside it
34 local_engine.py  filler words around a device name are ignored, so simple commands stay on the instant path
35 local_engine.py  colour commands run on the instant path, using each lamp's real colour support
36 jarvis-panel.js  Chat tab: mic button and spoken replies through Home Assistant's own voice pipeline
37 jarvis-panel.js  TTS on/off button above the mic; turning it off stops speech at once
38 jarvis-panel.js  TTS and mic buttons use matching square icons
39 jarvis-panel.js  message box, icon stack and Send share the same height
40 jarvis-panel.js  Chat voice uses the assistant whose agent is JARVIS, read live from its settings
41 jarvis-panel.js  one-line message box with TTS and mic side by side at the same height
42 jarvis-panel.js  Chat tab hint text removed
43 jarvis-panel.js  mic is press and hold to talk
44 jarvis-panel.js + local_engine.py  Listening hint, spoken words shown in the input bar before sending; colour and color spellings match on both sides
46 jarvis-panel.js  deleting the last message while JARVIS is thinking cancels it in the window
47 config_flow.py + agent.py + briefing.py + translations  Core settings switches for action checks, mail block and facts-only briefings
48 jarvis-panel.js  animated Thinking indicator with live seconds, tied to the real request
49 local_engine.py  'switch X on' word order; a vague name asks which device instead of guessing
50 local_engine.py  a vague name uses live state: one match acts at once, and the answer to 'which one' finishes the command
51 websocket.py + config_flow.py  Core setting camera_watch_default chooses which camera Camera Watch opens on
54 agent.py + local_engine.py  closing offers trimmed, colour rule for the AI
60 local_engine.py  audit: right device from live state, 'which one' per conversation, pronouns go to the AI, colour through the seam
61 local_engine.py  audit: no all-clear from offline or jammed sensors and locks
62 local_engine.py  audit: settings entities stay out of every command path
63 database.py + memory_thread.py + conversation.py  audit: conversations carry the user id, a new chat is seeded with that person's turns only
64 memory.py + memory_pgvector.py  audit: per-user recall inside every backend, punctuation safe FTS5 search
65 identity.py + conversation.py  audit: curated facts follow the logged-in user, presence only for voice with no login
66 agent.py  audit: admin tools need an admin login (allowlist), remember follows the login, delegated work keeps the login
67 __init__.py  audit: jarvis services are admin only unless listed; automations are not affected
68 websocket.py  audit: non-admins get their own memory and facts; logs, faces, intrusion log and connection details are for admins
69 recognition.py + intrusion.py  audit: face photos leave www and travel inline to admins; intrusion stills get unguessable names for phone pushes
70 agent.py  round 3: on a device command a reply needs a tool that worked behind it; failures are tracked per tool; ordinary chat is never retried or replaced
71 agent.py + briefing.py + proactive_briefing.py  audit: dynamic heaters and energy, include flags honoured, no instructions read aloud
72 conversation.py + websocket.py + camera.py  audit: no chat text in the HA log, routines per person, blank camera polls back off
73 jarvis-panel.js  audit: chat history merges by id across devices, mic start and stop are safe, a pause no longer ends a held recording, drafts survive a render, CLEAN cancels a pending reply and asks first on the page
74 agent.py  round 2: execute_plan stays within device control for non-admins, scheduled work keeps its owner, follow-ups, goals and usual times are per person
75 followups.py + goals.py + cognitive_core.py + database.py + websocket.py + rca.py  round 2 + 3: follow-ups and goals carry their owner, their log lines are private, Ollama address for admins only
76 local_engine.py  round 2 + 3: a bare room word is vague, an unavailable device is never reported as done, fan or door in a name tells devices apart, yes answers 'Do you mean', device_command for the truth check
77 conversation.py  a history key belongs to its owner; a non-admin's yes to an offer runs one allowed action and builds no autonomy
78 websocket.py + jarvis-panel.js  the Core switches and the Camera Watch default are also shown and writable in the panel
79 local_engine.py  spoken punctuation never changes a command: it is stripped before local matching and before device_command
80 local_engine.py + agent.py  sound-alike names (disk finds Disc) as a last fallback; two sound matches ask which one
81 jarvis-panel.js  Hey JARVIS switch in the Chat tab: Home Assistant's own wake word pipeline, off by default, remembered per browser; spoken words never replace a typed draft
Audit 10 (2026-11-07), Python:
84 local_engine.py  a whole name must fit the action (no lock service on a lamp), 'which one' skips unavailable devices, a vague room
                    word never switches on another kind of device unasked, no all-clear while a lock or cover is moving, 'set an
                    alarm' is no device command without an alarm panel, settings known by entity category only
85 agent.py  confirmed only for an end state that was read, a claim with a closing question is still a claim, a not-found read is
            no read, timer and list tools by whole words, remember from a limited login writes nothing shared, rooms match by case
86 briefing.py + proactive_briefing.py  facts-only briefings read no model notes, event times in local time, the Workday calendar
                    is skipped by its integration instead of a word filter
87 recognition.py + intrusion.py + __init__.py  photos the original left in www move to the private folder at startup, older
                    intrusion stills get unguessable names
88 conversation.py  voice with no login finds its earlier turns on the same satellite
89 websocket.py  'Last camera used' sticks, diagnostics and hazard keep addresses and coordinates for admins
90 camera.py  an offline camera backs off like a blank one and is logged once
91 cognitive_core.py  no SAFETY warning about an alarm panel the home does not have
Audit 10 round 2 (2026-11-07), Python:
94 local_engine.py + agent.py + conversation.py + websocket.py + config_flow.py + jarvis-panel.js  the alarm is a kind of device
                    in every home (an alarm for a time or an alarm clock is not), a vague room word swaps devices at once only
                    between two lights or two heaters, offline a device of the wrong type is named, -ing and Dutch claims with a
                    question are claims, a device still opening keeps that state, voice with no login on a satellite is seeded
                    with a short window, 'Last camera used' is saved as last
Audit 10 round 2 (2026-11-07), Chat tab:
95 jarvis-panel.js  Hey JARVIS rests only while a message is typed and says so on its button; spoken words that wait to be sent
                    join the next ones and go once; CLEAN and the red-cross cancel drop them
Audit 10 round 3 (2026-11-07), panel:
96 jarvis-panel.js  Camera Watch shows a very dark but real picture with a VERY DARK badge instead of NO FRAME and is not stuck on
                    the JARVIS tier; a click on Send while Hey JARVIS rests starts no extra run; its own waiting words are no typing
                    96c (round 4): the dark picture comes back only for the walk that saw it and while new frames keep coming, a
                    camera that breaks, hangs or turns black after being dark ends on NO FRAME, at most one tier switch a second;
                    the badge says 'VERY DARK — too little light to see' and goes when the frames stop
                    96d (round 5): a still is judged only once it has loaded, so slow stills keep a dark camera on its picture
                    with a steady badge; while NO FRAME shows, the chain is tried again about once a minute (owner's rule) and a
                    camera that works again is shown; a late JARVIS-tier answer after the tile left that tier changes nothing
Audit 10 round 3 (2026-11-07), Python:
97 local_engine.py + agent.py  an alarm with a time anywhere is a wake-up alarm (not after arm, disarm, activate), a question-word
                    clause asks only at the end, -ing claims only where a clause starts, Dutch 'is aan' claims, a lead-in such as
                    'Wat fijn,' asks nothing, a moving device plus one waiting for confirmation are both named
Audit 10 round 4 (2026-11-07), Python:
98 local_engine.py + agent.py  an alarm clock and 'my wake-up alarm' are never a device, also after activate; arm, disarm,
                    activate and deactivate keep a time only for 'the alarm'; alarm-system words (away mode, zone, system, panel,
                    security, arm) and 'the alarm' switched on or off keep a time later in the sentence; 'alarm 1' is no time;
                    an -ing device word claims unless before, after, without, avoid, about, of, by, for, from, the, current,
                    an article or 'your', an offer (try, suggest) or a not comes just before it; choices or please after a
                    question, a condition and 'het is aan u' claim nothing
Audit 10 round 5 (2026-11-08), Python:
100 local_engine.py + agent.py  'the house / home / burglar / intruder / security alarm' is the alarm system, also with a time
                    ('turn on/off the alarm at 7' stays a wake-up alarm); a condition lets off only the state of its own subject
                    ('When it is on', not 'Once again the heater is on' or 'As soon as you asked the heater is on'), and only
                    when no tool reported a failure; the choices test is linear (no seconds-long backtracking on the event loop);
                    with, than, me and 'Before I start' let an -ing word off, 'setting' is a noun unless an object, 'to' or a
                    be-form goes with it
"""
import json, os, shutil, sys, tempfile

BASE = sys.argv[1] if len(sys.argv) > 1 else "/config/custom_components/jarvis"
KIT = os.path.dirname(os.path.abspath(__file__))
results = []

# All-or-nothing: patches change a staged copy in memory, and commit() writes every file only when no
# patch failed. A failed anchor can therefore never leave a dependent patch half installed.
_stage = {}      # path -> patched text
_originals = {}  # path -> text before the first patch, saved once as .orig-kit

def _read(p):
    if p in _stage:
        return _stage[p]
    with open(p, encoding="utf-8") as f:
        return f.read()

def _failed():
    return [r for r in results if r.startswith("[FAILED")]

def edit(rel, marker, steps, label):
    p = os.path.join(BASE, rel)
    try:
        s = _read(p)
        if marker in s:
            results.append(f"[already] {label}"); return
        orig = s
        for old, new in steps:
            if s.count(old) != 1:
                results.append(f"[FAILED ] {label}: anchor not found ({s.count(old)}x) - JARVIS code changed, patch by hand"); return
            s = s.replace(old, new(old) if callable(new) else new)
    except Exception as exc:
        results.append(f"[FAILED ] {label}: {type(exc).__name__}: {exc}"); return
    _originals.setdefault(p, orig)
    _stage[p] = s
    results.append(f"[applied] {label}")

def kit_read(name):
    try:
        with open(os.path.join(KIT, name), encoding="utf-8") as f:
            return f.read()
    except OSError as exc:
        results.append(f"[FAILED ] kit file {name}: {exc}")
        return ""

def labels(tag, new):
    """Add Core settings labels to every translation file, the English text where no translation exists, so all
    languages keep the structure of en.json. A missing section is a failure, not a silent skip."""
    folder = os.path.join(BASE, "translations")
    others = sorted("translations/" + n for n in os.listdir(folder) if n.endswith(".json") and n != "en.json") if os.path.isdir(folder) else []
    for f in ["translations/en.json", "strings.json"] + others:
        p = os.path.join(BASE, f)
        if not os.path.exists(p):
            continue
        try:
            d = json.loads(_read(p))
            core = d["options"]["step"]["core"]
            data = core.setdefault("data", {})
        except (KeyError, TypeError, ValueError) as exc:
            results.append(f"[FAILED ] {tag} labels: {f}: {type(exc).__name__}: {exc}"); continue
        if all(k in data for k in new):
            results.append(f"[already] {tag} labels: {f}"); continue
        for k, v in new.items():
            data.setdefault(k, v)
        _stage[p] = json.dumps(d, ensure_ascii=False, indent=2)
        results.append(f"[applied] {tag} labels: {f}")

class _NotRestored(Exception):
    """A swap failed and some files that were already swapped in could not be put back."""

def _put_back(p, old):
    """Return one target to what it was before the swap: its old bytes, or no file when there was none. The file keeps its
    mode, as on the way in: mkstemp makes the file 0600, which would leave it owner-only (2026-11-07, round 2)."""
    if old is None:
        if os.path.exists(p):
            os.unlink(p)
        return
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(p), prefix=".kit-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(old)
        if os.path.exists(p):
            shutil.copymode(p, tmp)   # the swapped-in file got the original mode, so this is the mode the file had
        os.replace(tmp, p)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

def _atomic_writes(jobs):
    """Write every file next to its target first, then swap them in, so a full disk or a crash cannot
    leave an empty or half-written integration file. When one swap fails (a file held open on Windows),
    the files already swapped in are put back, so the install is never half patched (2026-11-07)."""
    temps, done = [], []
    try:
        for p, text in jobs:
            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(p), prefix=".kit-", suffix=".tmp")
            temps.append((tmp, p))
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
            if os.path.exists(p):
                shutil.copymode(p, tmp)
        for tmp, p in temps:
            old = None
            if os.path.exists(p):
                with open(p, "rb") as f:
                    old = f.read()
            os.replace(tmp, p)
            done.append((p, old))
    except Exception as exc:
        stuck = []
        for p, old in reversed(done):
            try:
                _put_back(p, old)
            except OSError:
                stuck.append(os.path.basename(p))
        if stuck:
            raise _NotRestored(f"{type(exc).__name__}: {exc}; could not put back: {', '.join(stuck)}") from exc
        raise
    finally:
        for tmp, _p in temps:
            if os.path.exists(tmp):
                os.unlink(tmp)

def commit():
    """Write the staged files, or nothing at all when any patch failed."""
    if _failed():
        results.append("[STOPPED] a patch failed, so no file was changed")
        return
    jobs = [(p + ".orig-kit", t) for p, t in _originals.items() if not os.path.exists(p + ".orig-kit")]
    jobs += list(_stage.items())
    try:
        _atomic_writes(jobs)
    except _NotRestored as exc:
        results.append(f"[FAILED ] writing the patched files: {exc}. The install is half patched: close what holds "
                       "those files and run the kit again, it finishes the rest")
    except Exception as exc:
        results.append(f"[FAILED ] writing the patched files: {type(exc).__name__}: {exc}. Every file was put back, "
                       "so no file was changed")

def _report_crash(kind, value, tb):
    """An unexpected error still shows what happened. Nothing is on disk yet at that point."""
    print("\n".join(results))
    print("\nFAILED: the kit stopped early, no JARVIS file was changed.")
    sys.__excepthook__(kind, value, tb)

if __name__ == "__main__":
    sys.excepthook = _report_crash

# 1 ------------------------------------------------------------------------------------------
edit("llm_provider.py", '"reasoning_effort": "none"', [
    ('"think": bool(thinking),', '"think": bool(thinking), **({} if thinking else {"reasoning_effort": "none"}),'),
    ('OLLAMA_KEEP_ALIVE = "30m"', 'OLLAMA_KEEP_ALIVE = -1'),
], "1 llm_provider: no hidden thinking + keep model loaded")

# 2 + 3 --------------------------------------------------------------------------------------
LIVE = ('    # Jarvis Home patch (2026-11-03): live context moved from the system prompt to the latest user\n'
        '    # message, so the static prompt prefix stays identical between turns and the KV cache is reused.\n'
        '    _live = (_recall + "\\n\\n" + situation_block + awareness_block + cog_status).strip()\n'
        '    if _live:\n'
        '        for _i in range(len(full_messages) - 1, 0, -1):\n'
        '            _m = full_messages[_i]\n'
        '            if _m.get("role") == "user" and isinstance(_m.get("content"), str):\n'
        '                full_messages[_i] = {**_m, "content": _m["content"] + "\\n\\n[Live home context - for reference, not part of the request]\\n" + _live}\n'
        '                break\n')
RECALL = ('    # Jarvis Home patch (2026-11-03): per-message recall (memory/knowledge) that conversation.py\n'
          '    # appends to the persona is moved into the live block, keeping the system prompt static.\n'
          '    persona, _rsep, _recall = persona.partition("\\n\\n## Current context (live data)\\n")\n'
          '    _recall = ("## Current context (live data)\\n" + _recall) if _rsep else ""\n')
SP = '    system_prompt = (\n        f"{persona}\\n\\n"\n'
FM = '    full_messages = [{"role": "system", "content": system_prompt}] + messages\n'
edit("agent.py", "Jarvis Home patch (2026-11-03): live context moved", [
    ('        f"{situation_block}"\n        f"{awareness_block}"\n        f"{cog_status}\\n\\n"\n', '        f"\\n"\n'),
    (SP, lambda old: RECALL + old),
    (FM, lambda old: old + LIVE),
], "2 agent: prompt-cache fix (live context to the latest message)")
RULE = ('        f"## Honesty about this house\\n"\n'
        '        f"Only describe Home Assistant menus, options, integrations or devices that you have "\n'
        '        f"actually seen through your tools. Never invent settings, menu names or setup steps. "\n'
        '        f"If a device is not in Home Assistant, say so plainly instead of guessing how to add it.\\n\\n"\n')
edit("agent.py", "## Honesty about this house", [
    ('        f"## How you reason\\n"\n', lambda old: RULE + old),
], "3 agent: honesty rule")

# 4 ------------------------------------------------------------------------------------------
edit("conversation.py", "## Recalled for this message", [
    ('                persona = persona + "\\n\\n" + mem_context\n', '                persona = persona + "\\n\\n## Recalled for this message\\n" + mem_context\n'),
    ('                persona = persona + "\\n\\n" + kn_block\n', '                persona = persona + "\\n\\n## Recalled for this message\\n" + kn_block\n'),
], "4 conversation: mark per-message recall")

# 5 ------------------------------------------------------------------------------------------
edit("websocket.py", "show who is home when there are no room presence sensors", [
    ('                "subtitle":   "no presence detected",\n',
     '                # Jarvis Home patch (2026-11-04): show who is home when there are no room presence sensors.\n'
     '                "subtitle":   ((", ".join(sorted(st.attributes.get("friendly_name", st.entity_id) for st in hass.states.async_all("person") if st.state == "home")) + " home \\u00b7 room unknown (no motion sensors)") if anyone else "nobody home"),\n'),
], "5 websocket: Residence presence text")

# 6 ------------------------------------------------------------------------------------------
MODEL = kit_read("residence-model.js")
if not MODEL.endswith("\n"):
    MODEL += "\n"
edit("frontend/jarvis-panel.js", "Jarvis Home patch (2026-11-04b)", [
    ("    const mount = this.shadowRoot?.querySelector('#res-iso');\n    if (!mount || typeof JARVIS3D === 'undefined') return;\n",
     lambda old: old + MODEL),
], "6 panel: apartment wireframe model")

# 7 ------------------------------------------------------------------------------------------
edit("frontend/jarvis-panel.js", "Jarvis Home patch (2026-11-04c)", [
    ("      this._activeCam = remembered ? stored : this._cams[0].entity_id;\n",
     "      // Jarvis Home patch (2026-11-04c): every page open starts on the camera chosen in Core settings.\n"
     "      // With no camera chosen there, the remembered one stays, as in the original.\n"
     "      const _jhCam = this._liveData?.config?.camera_watch_default;\n      this._activeCam = this._cams.some(c => c.entity_id === _jhCam) ? _jhCam : (remembered ? stored : this._cams[0].entity_id);\n"),
], "7 panel: Camera Watch opens on the chosen camera")

# 8 ------------------------------------------------------------------------------------------
edit("agent.py", "Jarvis Home patch (2026-11-04d)", [
    ('"last_changed": state.last_changed.isoformat() if state.last_changed',
     '# Jarvis Home patch (2026-11-04d): local time, the model read UTC stamps out as if they were local.\n'
     '                "last_changed": state.last_changed.astimezone().isoformat() if state.last_changed'),
], "8 agent: last_changed in local time")

# 9 ------------------------------------------------------------------------------------------
edit("frontend/jarvis-panel.js", "Jarvis Home patch (2026-11-04e)", [
    ("      cabin:     { label: 'Cabin',      roof: 'gable', pitch: 1.25 },\n",
     lambda o: o + "      // Jarvis Home patch (2026-11-04e): the owner's own apartment model, added after the ready-made styles.\n"
                   "      my_apartment: { label: 'My residence', roof: 'flat', pitch: 0 },\n"
                   "      appart_light: { label: 'My Resid', roof: 'flat', pitch: 0 },\n"),
], "9 panel: My apartment and My apart low added after the default styles")

# 11 -----------------------------------------------------------------------------------------
edit("frontend/jarvis-panel.js", "Jarvis Home patch (2026-11-04f)", [
    ("      apartment: { roof: 'flat',  pitch: 0.12, dormersFront: 0, dormersRear: 0 },\n",
     "      apartment: { roof: 'flat',  pitch: 0.12, dormersFront: 0, dormersRear: 0 },\n"
     "      my_apartment: { roof: 'flat',  pitch: 0.12, dormersFront: 0, dormersRear: 0 },\n"
     "      appart_light: { roof: 'flat',  pitch: 0.12, dormersFront: 0, dormersRear: 0 },\n"),
    ("    const f = this._currentFloor || 'all';\n    return f === 'bsmt' ? 'b' : f;\n",
     "    // Jarvis Home patch (2026-11-04f): the owner's model has one floor; other styles keep their floors.\n"
     "    if (['my_apartment', 'appart_light'].includes(this._residenceStyle())) { this._currentFloor = 'all'; return 'all'; }\n"
     "    const f = this._currentFloor || 'all';\n    return f === 'bsmt' ? 'b' : f;\n"),
    ('        <div class="floor-tabs">\n          <button class="floor-tab ${this._currentFloor === \'all\'',
     '        <div class="floor-tabs" style="${[\'my_apartment\', \'appart_light\'].includes(this._residenceStyle()) ? \'display:none\' : \'\'}">\n          <button class="floor-tab ${this._currentFloor === \'all\''),
], "11 panel: My apart low in the style list, floor tabs hidden for the owner's styles")

# 12 -----------------------------------------------------------------------------------------
GUARD = ('# Jarvis Home patch (2026-11-05): email is written by strangers. Once read_email ran, only the\n'
         '# read-only tools below work for the rest of that request, so text in a mail can never drive the\n'
         '# home, store a memory, schedule anything or send data out. An allowlist, so new tools start blocked.\n'
         'import contextvars as _contextvars\n'
         '_MAIL_TAINT = _contextvars.ContextVar("jarvis_mail_taint", default=None)\n'
         '_MAIL_SAFE_TOOLS = frozenset({"read_email", "calendar_agenda", "weather_forecast", "get_home_summary",\n'
         '                              "get_entity_state", "search_entities", "get_area_devices", "energy_status",\n'
         '                              "cognitive_status", "connectivity_status", "activity_history"})\n'
         '\n\n'
         'def _mail_guard(tool_name: str) -> Optional[str]:\n'
         '    state = _MAIL_TAINT.get()\n'
         '    if not state or not state.get("read") or tool_name in _MAIL_SAFE_TOOLS:\n'
         '        return None\n'
         '    _LOGGER.warning("JARVIS: blocked tool %s after read_email in the same request", tool_name)\n'
         '    return json.dumps({"error": "Blocked for safety: email was read in this request, so no actions are allowed. "\n'
         '                                "Tell the user nothing was changed and to ask for the action in a new message."})\n'
         '\n\n')
edit("agent.py", "_mail_guard", [
    ('async def _exec_read_email(hass: HomeAssistant, args: dict) -> str:\n', lambda o: GUARD + o),
    ('        from . import mail\n        limit = int(args.get("limit", 5) or 5)\n',
     lambda o: '        _mail_state = _MAIL_TAINT.get()\n        if _mail_state is not None:\n            _mail_state["read"] = True\n' + o),
    ('    # Custom JARVIS tools\n    if tool_name in _TOOL_MAP:\n',
     lambda o: '    _blocked = _mail_guard(tool_name)\n    if _blocked:\n        return _blocked\n' + o),
    ('    objective = str(args.get("objective", "")).strip()\n    capability = str(args.get("capability", "")).strip()\n',
     lambda o: '    _blocked = _mail_guard("delegate_task")\n    if _blocked:\n        return _blocked\n' + o),
    ('    from .llm_provider import create_provider\n\n    # Build system prompt with home context\n',
     lambda o: '    if depth == 0:\n        _MAIL_TAINT.set({"read": False})\n' + o),
], "12 agent: no actions after reading email in the same request")

# 13 -----------------------------------------------------------------------------------------
PROMISE = '# Jarvis Home patch (2026-11-05): the local model sometimes says it is doing something without calling\n# a tool (issue #413). Such a first reply is sent back once: call the tool, or say it cannot be done.\nimport re as _re\n_PROMISE_NUDGE = ("This is a command for a device and you called no tool, so nothing has happened. Call search_entities or the right tool now "\n                  "and answer from its result. If no tool can do it or no such device exists, say so plainly.")\n\n\n'
edit("agent.py", "_PROMISE_NUDGE", [
    ('def _no_action_note(served_by: str, n_tools: int) -> str:\n', lambda o: PROMISE + o),
    ('    _acted = False         # Phase AE shadow: did this turn execute a tool action?\n',
     lambda o: o + '    _nudged = False        # Jarvis Home patch: one retry for a reply that promised an action\n'
                   '    _changed = False       # Jarvis Home patch: a tool that can change something really ran\n'
                   '    _cmd, _asked = await _jh_turn(hass, user_input)\n'),
    ('            _has_text = bool(text and text.strip())\n            _emit_conversational_shadow(acted=_acted, answered=_has_text)\n',
     lambda o: ('            if not _nudged and tools and text and ((_cmd and not _changed)):\n'
                '                _nudged = True\n'
                '                _LOGGER.warning("JARVIS: reply promised an action with no tool call, sent back once")\n'
                '                working.append({"role": "assistant", "content": text})\n'
                '                working.append({"role": "user", "content": _PROMISE_NUDGE})\n'
                '                continue\n'
                '            if _nudged and _cmd and not _changed and "already" not in (text or "").lower():\n'
                '                _LOGGER.warning("JARVIS: command ended with no control tool, fixed reply used")\n'
                '                text = f"That did not go through, {(config or {}).get(\'honorific\', \'sir\')}. Nothing was changed."\n') + o),
    ('            _acted = True   # Phase AE shadow: the turn took a tool action\n',
     lambda o: o + '            _changed = _changed or call["name"] not in _MAIL_SAFE_TOOLS\n'),
    ('f"If a device is not in Home Assistant, say so plainly instead of guessing how to add it.\\n\\n"\n',
     'f"If a device is not in Home Assistant, say so plainly instead of guessing how to add it. "\n'
     '        f"Nothing runs in the background: a check or action happens only by calling a tool in this reply. "\n'
     '        f"Never promise a later report, and never state the result of something you did not run.\\n\\n"\n'),
], "13 agent: actions must really run")

# 14 -----------------------------------------------------------------------------------------
def between(rel, start, end):
    try:
        t = _read(os.path.join(BASE, rel))
    except OSError:
        return "\0 anchor missing"
    i = t.find(start); j = t.find(end, i)
    return t[i:j] if i >= 0 and j > i else "\0 anchor missing"

FACTS = ('def _facts_home(hass, people: bool = False) -> list[str]:\n'
         '    """Jarvis Home patch (2026-11-05): real facts for spoken briefings, straight from HA states."""\n'
         '    lines = []\n'
         '    if people:\n'
         '        home = [s.attributes.get("friendly_name", s.entity_id) for s in hass.states.async_all("person") if s.state == "home"]\n'
         '        if home:\n'
         '            lines.append(f"At home: {\', \'.join(home)}.")\n'
         '    on = [s.attributes.get("friendly_name", s.entity_id) for s in hass.states.async_all("switch")\n'
         '          if "heater" in s.entity_id and "child_lock" not in s.entity_id and s.state == "on"]\n'
         '    on += [s.attributes.get("friendly_name", s.entity_id) + " (powered on, heat level unknown)" for s in hass.states.async_all("climate") if s.state == "heat"]\n'
         '    lines.append(f"Heaters on: {\', \'.join(on)}." if on else "No heaters are on.")\n'
         '    used = []\n'
         '    for s in hass.states.async_all("sensor"):\n'
         '        if s.entity_id.endswith("_energy_today"):\n'
         '            try:\n'
         '                kwh = float(s.state)\n'
         '            except (TypeError, ValueError):\n'
         '                continue\n'
         '            if kwh >= 0.1:\n'
         '                used.append(f"{s.attributes.get(\'friendly_name\', s.entity_id).replace(\' energy today\', \'\')} {kwh:.1f} kilowatt hours")\n'
         '    if used:\n'
         '        lines.append(f"Energy today: {\', \'.join(used)}.")\n'
         '    return lines\n\n\n')
edit("briefing.py", "_facts_home", [
    ('def _plain_briefing(greeting: str, honorific: str, context_lines: list[str]) -> str:\n', lambda o: FACTS + o),
    ('            context_lines.append(snap_summary)\n', '            pass  # Jarvis Home patch: camera summaries are AI text, not read aloud\n'),
    (between("briefing.py", '    try:\n        result = await hass.async_add_executor_job(\n            lambda: groq_client.chat(', '    # The model sometimes returns an empty completion'),
     '    # Jarvis Home patch (2026-11-05): no AI text. A small model invented locks, garages and actions.\n'
     '    context_lines.extend(_facts_home(hass))\n'
     '    briefing_text = _plain_briefing(greeting, honorific, context_lines)\n\n'),
], "14a briefing: scheduled briefings read real facts only")
edit("proactive_briefing.py", "_facts_home", [
    ('        extra_context = f"{person_name} just arrived home. This is a welcome briefing."\n', '        extra_context = f"{person_name} just arrived home."\n'),
    ('        extra_context += f"\\n\\n{snap_summary}"\n', '        pass  # Jarvis Home patch: camera summaries are AI text, not read aloud\n'),
    (between("proactive_briefing.py", '        task = (\n            f"You are delivering a proactive briefing', '        if not briefing_text:\n            return\n'),
     '        # Jarvis Home patch (2026-11-05): no AI text, real facts only.\n'
     '        from .briefing import _facts_home, _plain_briefing, _time_greeting\n'
     '        context_lines.extend(_facts_home(hass, people=True))\n'
     '        briefing_text = _plain_briefing(_time_greeting(), honorific, context_lines)\n'),
], "14b proactive: arrival briefings read real facts only")

# 15 -----------------------------------------------------------------------------------------
SPEAK = ('def _speakable(text: str) -> str:\n'
         '    """Jarvis Home patch (2026-11-05): make the facts sound right when read aloud."""\n'
         '    import re\n'
         '    text = text.replace("\\u00b0C", " degrees").replace("clear-night", "clear night").replace("partlycloudy", "partly cloudy")\n'
         '    text = re.sub(r"(\\d)W\\b", r"\\1 watts", text)\n'
         '    return text.replace(" Power is drawing", " is drawing")\n\n\n')
edit("briefing.py", "_speakable", [
    ('def _facts_home(hass, people: bool = False) -> list[str]:\n', lambda o: SPEAK + o),
    (".replace(' energy today', '')", ".replace(' energy today', '').replace(' Energy today', '')"),
    ('        if msg and start:\n', '        if msg and start and "workday" not in msg.lower():\n'),
    ('    context_lines.extend(_facts_home(hass))\n    briefing_text = _plain_briefing(greeting, honorific, context_lines)\n',
     '    context_lines.extend(_facts_home(hass))\n    briefing_text = _speakable(_plain_briefing(greeting, honorific, context_lines))\n'),
], "15a briefing: text cleaned for speech")
edit("proactive_briefing.py", "_speakable", [
    ('        from .briefing import _facts_home, _plain_briefing, _time_greeting\n', '        from .briefing import _facts_home, _plain_briefing, _speakable, _time_greeting\n'),
    ('        briefing_text = _plain_briefing(_time_greeting(), honorific, context_lines)\n',
     '        briefing_text = _speakable(_plain_briefing(_time_greeting(), honorific, context_lines))\n'),
], "15b proactive: text cleaned for speech")

# 18 -----------------------------------------------------------------------------------------
edit("local_engine.py", "Jarvis Home patch: only report", [
    ('    parts = [f"All systems nominal, {h}."]\n', '    parts = [f"Home status, {h}."]\n'),
    ('    parts.append("All locks secured." if not locks_ul else\n                 f"{locks_ul} lock{\'s\' if locks_ul != 1 else \'\'} unlocked.")\n',
     '    if hass.states.async_all("lock"):  # Jarvis Home patch: only report locks that exist\n'
     '        parts.append("All locks secured." if not locks_ul else\n'
     '                     f"{locks_ul} lock{\'s\' if locks_ul != 1 else \'\'} unlocked.")\n'),
    ('            return f"Everything is closed and secured, {h}."\n',
     '            if not (hass.states.async_all("lock") or hass.states.async_all("cover") or any(\n'
     '                    s.attributes.get("device_class") in ("door", "window", "garage_door")\n'
     '                    for s in hass.states.async_all("binary_sensor"))):\n'
     '                return f"I have no door, window or lock sensors in this home, {h}."\n'
     '            return f"Everything is closed and secured, {h}."\n'),
    ('{off_count} lights off, all locks secured. Rest well.', '{off_count} lights off. Rest well.'),
], "18 local_engine: quick answers only report devices that exist")

# 20 -----------------------------------------------------------------------------------------
edit("local_engine.py", "tomorrow|tonight", [
    ('(r"(?:what(?:\'s| is)\\s+(?:the\\s+)?)weather",        "weather"),',
     '(r"(?:what(?:\'s| is)\\s+(?:the\\s+)?)weather(?!.*\\b(?:tomorrow|tonight|week|weekend|forecast|later|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\\b)", "weather"),'),
], "20b local_engine: forecast questions go to the forecast tool")

# 22 -----------------------------------------------------------------------------------------
VOICE_PERSONA = (
    'JARVIS_PERSONA = """\\\n'
    'You are JARVIS, the AI assistant for this home.\n\n'
    '## Truth comes first\n'
    '- Everything you say about this home must come from a tool result or the live context you were given.\n'
    '- Check with a tool before stating whether something is on, off, open, closed or locked.\n'
    '- Only say you did something after a tool really did it.\n'
    '- If a device is not in your tools or context, it does not exist in this home. Say so and stop.\n'
    '- Never invent devices, rooms, readings, actions or results. If you do not know, say so.\n'
    '- A question about a device is not a command to change it.\n\n'
    '## Voice\n'
    '- Address the owner as {honorific}.\n'
    '- Calm, precise and understated, with a British tone. No exclamation marks.\n'
    '- Answer what was asked, then stop. One or two short sentences for anything about the home.\n'
    '- Do not offer extra help or ask what else you can do.\n'
    '- After an action, confirm in a few words, for example "Done, {honorific}."\n'
    '- Never joke about the state of the home, and never let wit replace a fact.\n')
CLEAN_DIRECTIVE = ('CLEAN_DIRECTIVE = ("## PRIME DIRECTIVE\\n\\nHelp the people in this home with accurate information and the actions they ask for. "\n'
                   '                   "Be correct and brief. Do only what is asked.")\n\n\n')
edit("const.py", "Truth comes first", [
    (between("const.py", 'JARVIS_PERSONA = """', '"""\n'), VOICE_PERSONA),
    ('def get_directive(preset_name: str = DEFAULT_DIRECTIVE_PRESET,\n', lambda o: CLEAN_DIRECTIVE + o),
], "22 const: clean base plus JARVIS voice")

# 23 -----------------------------------------------------------------------------------------
ROOMS = ('# Jarvis Home patch (2026-11-05): the floor plan is the true room list, read live from the JARVIS config.\n'
         'def _floor_rooms(hass) -> list:\n'
         '    try:\n'
         '        with open(hass.config.path("jarvis", "config.json"), encoding="utf-8") as fh:\n'
         '            raw = json.load(fh).get("floor_plan_rooms") or {}\n'
         '        if isinstance(raw, str):\n'
         '            raw = json.loads(raw)\n'
         '        names = []\n'
         '        for floor in raw.values():\n'
         '            for room in (floor.get("rooms") or []):\n'
         '                if room.get("type") != "door" and room.get("name") and room["name"] not in names:\n'
         '                    names.append(room["name"])\n'
         '        return names\n'
         '    except Exception:\n'
         '        return []\n\n\n'
         'def _rooms_note(hass, rooms: list) -> str:\n'
         '    if not rooms:\n'
         '        return ""\n'
         '    from homeassistant.helpers import area_registry as _ar\n'
         '    labels = [a.name for a in _ar.async_get(hass).async_list_areas() if a.name not in rooms]\n'
         '    note = f"\\n\\nRooms in this home, from the floor plan ({len(rooms)}): {\', \'.join(rooms)}. This is the true room list."\n'
         '    if labels:\n'
         '        note += f" {\', \'.join(labels)} are Home Assistant labels, not rooms: never count or name them as rooms."\n'
         '    return note\n\n\n')
edit("agent.py", "_floor_rooms", [
    ('def _no_action_note(served_by: str, n_tools: int) -> str:\n', lambda o: ROOMS + o),
    ('    home_context = _build_home_context(hass, _learned)\n',
     lambda o: o + '    home_context += _rooms_note(hass, await hass.async_add_executor_job(_floor_rooms, hass))\n'),
], "23 agent: floor plan is the true room list")

# 24 -----------------------------------------------------------------------------------------
FACTS24 = ('# Jarvis Home patch (2026-11-05): counts and heater states come from code, the model is unreliable at both.\n'
           'def _live_facts(hass) -> str:\n'
           '    lights = [s for s in hass.states.async_all("light") if "backlight" not in s.entity_id]\n'
           '    on = [s.attributes.get("friendly_name", s.entity_id) for s in lights if s.state == "on"]\n'
           '    out = [f"Exact facts, use these numbers: {len(lights)} lights in total, {len(on)} on" + (f" ({\', \'.join(on)})." if on else ".")]\n'
           '    heat = [f"{s.attributes.get(\'friendly_name\', s.entity_id)}: {s.state}" for s in hass.states.async_all("switch")\n'
           '            if "heater" in s.entity_id and "child_lock" not in s.entity_id]\n'
           '    heat += [f"{s.attributes.get(\'friendly_name\', s.entity_id)}: " + ("powered on, heat level unknown, it may not be producing heat" if s.state == "heat" else s.state)\n'
           '             for s in hass.states.async_all("climate")]\n'
           '    if heat:\n'
           '        out.append("Heaters: " + "; ".join(heat) + ".")\n'
           '    return " ".join(out)\n\n\n')
edit("agent.py", "_live_facts", [
    ('def _no_action_note(served_by: str, n_tools: int) -> str:\n', lambda o: FACTS24 + o),
    ('_live = (_recall + "\\n\\n" + situation_block + awareness_block + cog_status).strip()',
     '_live = (_live_facts(hass) + "\\n\\n" + _recall + "\\n\\n" + situation_block + awareness_block + cog_status).strip()'),
], "24 agent: exact counts and heater states from code")

# 25 -----------------------------------------------------------------------------------------
CHAT_METHODS = ('  // Jarvis Home patch (2026-11-05): chat history saved per HA user, so it follows the login on every device.\n'
                '  async _loadChat() {\n'
                '    if (this._chatLoaded || !this._hass) return;\n'
                '    this._chatLoaded = true;\n'
                '    try {\n'
                '      const res = await this._hass.callWS({ type: "frontend/get_user_data", key: "jarvis_chat" });\n'
                '      const saved = res?.value?.messages;\n'
                '      if (Array.isArray(saved) && saved.length && !this._chatMessages.length) {\n'
                '        this._chatMessages = saved;\n'
                '        this._refreshChat();\n'
                '      }\n'
                '    } catch (err) { console.warn("JARVIS chat: history not loaded", err); }\n'
                '  }\n\n'
                '  _saveChat() {\n'
                '    if (!this._hass) return;\n'
                '    this._hass.callWS({ type: "frontend/set_user_data", key: "jarvis_chat",\n'
                '      value: { messages: this._chatMessages.slice(-200) } })\n'
                '      .catch((err) => console.warn("JARVIS chat: history not saved", err));\n'
                '  }\n\n'
                '  _deleteChatMessage(i) {\n'
                '    this._chatMessages.splice(i, 1);\n'
                '    this._saveChat();\n'
                '    this._refreshChat();\n'
                '  }\n\n')
CHAT_CSS = ('  .chat-msg { position: relative; padding-right: 26px !important; }\n'
            '  .chat-del { position: absolute; top: 0; right: 0; width: 26px; height: 28px; background: none; border: 0; color: #ff4d4d;\n'
            '    cursor: pointer; font-size: 13px; line-height: 1; padding: 0; }   /* the whole corner is the target, the cross stays where it was */\n'
            '  .chat-del:hover { color: #ff1f1f; }\n'
            '  .chat-clean { margin-left: 12px; background: none; border: 1px solid var(--line-hot); color: var(--cyan);\n'
            '    font-family: var(--font-mono); font-size: 10px; letter-spacing: 0.08em; padding: 2px 8px;\n'
            '    border-radius: var(--radius); cursor: pointer; }\n'
            '  .chat-clean:hover { background: var(--cyan-faint); }\n')
edit("frontend/jarvis-panel.js", "_deleteChatMessage", [
    ('    this._chatConvId = null;        // HA conversation_id, for multi-turn context\n',
     lambda o: o + '    this._chatLoaded = false;       // saved history fetched once per page\n'),
    ('    const rows = this._chatMessages.map((m) => {\n', '    const rows = this._chatMessages.map((m, i) => {\n'),
    ('return `<div class="${cls}"><span class="chat-who">${who}</span><span class="chat-text">${this._esc(m.text)}</span></div>`;',
     'return `<div class="${cls}"><button class="chat-del" data-i="${i}" title="Delete">\u2715</button><span class="chat-who">${who}</span><span class="chat-text">${this._esc(m.text)}</span></div>`;'),
    ('  _clearChat() {\n    this._chatMessages = [];\n    this._chatConvId = null;\n    this._refreshChat();\n  }\n',
     CHAT_METHODS + '  _clearChat() {\n    this._chatMessages = [];\n    this._chatConvId = null;\n    this._saveChat();\n    this._refreshChat();\n  }\n'),
    ('      this._chatBusy = false;\n      this._refreshChat();\n      this.shadowRoot?.getElementById("chat-input")?.focus();\n',
     '      this._chatBusy = false;\n      this._saveChat();\n      this._refreshChat();\n      this.shadowRoot?.getElementById("chat-input")?.focus();\n'),
    ('        <span>Talk to JARVIS</span>\n        <span class="side">${this._chatAgentLabel()}</span>\n',
     '        <span>Talk to JARVIS<button id="chat-clear" class="chat-clean">CLEAN</button></span>\n        <span class="side">${this._chatAgentLabel()}</span>\n'),
    (' History clears on reload. <button id="chat-clear" class="chat-clear">clear</button></div>', '</div>'),
    ('    if (chatClear) chatClear.addEventListener("click", () => this._clearChat());\n',
     lambda o: o + ('    const chatLogEl = this.shadowRoot.getElementById("chat-log");\n'
                    '    if (chatLogEl) {\n'
                    '      chatLogEl.addEventListener("click", (e) => {\n'
                    '        const del = e.target.closest(".chat-del");\n'
                    '        if (del) this._deleteChatMessage(Number(del.dataset.i));\n'
                    '      });\n'
                    '      this._loadChat();\n'
                    '    }\n')),
    ('  .chat-who {\n    font-family: var(--font-mono); font-size: 9px; letter-spacing: 0.08em;\n', lambda o: CHAT_CSS + o),
], "25 panel: chat saved per user, Clean button, red cross per message")

# 26 -----------------------------------------------------------------------------------------
edit("memory.py", "Jarvis Home patch: per-user", [
    ('    device_id: str = "",\n    conversation_id: str = "",\n) -> bool:\n',
     '    device_id: str = "",\n    conversation_id: str = "",\n    user_id: str = "",\n) -> bool:\n'),
    ('        "conversation_id": conversation_id or "",\n        "timestamp": ts,\n',
     '        "conversation_id": conversation_id or "",\n        "user_id": user_id or "",  # Jarvis Home patch: per-user memory\n        "timestamp": ts,\n'),
    ('    k: int = 5,\n    hours: Optional[int] = None,\n) -> list[dict]:\n',
     '    k: int = 5,\n    hours: Optional[int] = None,\n    user_id: Optional[str] = None,\n) -> list[dict]:\n'),
    ('    if _pgvector_available:\n        try:\n            from . import memory_pgvector\n            return memory_pgvector.search(',
     '    if _pgvector_available and user_id is None:\n        try:\n            from . import memory_pgvector\n            return memory_pgvector.search('),
    ('    if _chromadb_available and _collection is not None:\n        try:\n            where = None\n',
     '    if _chromadb_available and _collection is not None and user_id is None:\n        try:\n            where = None\n'),
    ('            rows = conn.execute(sql, (query_clean, k)).fetchall()\n',
     '            rows = conn.execute(sql, (query_clean, k if user_id is None else 500)).fetchall()\n'),
    ('                    pass\n                memories.append({\n                    "text": row["content"],\n',
     '                    pass\n                if user_id is not None and meta.get("user_id", "") != user_id:\n                    continue\n                if len(memories) >= k:\n                    break\n                memories.append({\n                    "text": row["content"],\n'),
    ('def get_conversation_context(query: str, k: int = 3) -> str:\n', 'def get_conversation_context(query: str, k: int = 3, user_id: Optional[str] = None) -> str:\n'),
    ('    memories = search_memory(query, k=k)\n', '    memories = search_memory(query, k=k, user_id=user_id)\n'),
], "26a memory: per-user storage and recall")
edit("conversation.py", "_jh_uid", [
    ('        cid       = user_input.conversation_id or user_input.device_id or "default"\n',
     '        _jh_uid   = getattr(getattr(user_input, "context", None), "user_id", None) or ""  # Jarvis Home patch: per-user\n'
     '        cid       = user_input.conversation_id or user_input.device_id or (f"user_{_jh_uid}" if _jh_uid else "default")\n'),
    ('                lambda: store_memory(user_input.text, role="user",\n',
     '                lambda: store_memory(user_input.text, role="user", user_id=_jh_uid,\n'),
    ('                get_conversation_context, user_input.text, 3,\n', '                get_conversation_context, user_input.text, 3, _jh_uid,\n'),
    ('lambda: store_memory(response_text, role="assistant",\n', 'lambda: store_memory(response_text, role="assistant", user_id=_jh_uid,\n'),
], "26b conversation: threads and memory use the logged-in user")

# 30 -----------------------------------------------------------------------------------------
HELPER = ('def _jh_is_setting(hass, eid: str) -> bool:\n'
          '    """Jarvis Home patch: config and diagnostic entities are never spoken command targets."""\n'
          '    try:\n'
          '        from homeassistant.helpers import entity_registry as _er\n'
          '        entry = _er.async_get(hass).async_get(eid)\n'
          '        if entry is not None and entry.entity_category is not None:\n'
          '            return True\n'
          '    except Exception:\n'
          '        pass\n'
          '    return "backlight" in eid or "child_lock" in eid\n\n\n')
edit("local_engine.py", "_jh_is_setting", [
    ('def _find_entity(hass, name_fragment, domain_hint=None, learned_aliases=None):\n', lambda o: HELPER + o),
    ('        for state in hass.states.async_all(domain):\n            eid = state.entity_id\n            fname = (state.attributes.get("friendly_name") or "").lower()\n            fname_words = set(fname.split())\n',
     '        for state in hass.states.async_all(domain):\n            eid = state.entity_id\n            if _jh_is_setting(hass, eid):\n                continue\n            fname = (state.attributes.get("friendly_name") or "").lower()\n            fname_words = set(fname.split())\n'),
    ('            if fuzz > 55 and fuzz > best_score:\n                best_score = fuzz\n                best_match = (eid,',
     '            if fuzz > 85 and fuzz > best_score:\n                best_score = fuzz\n                best_match = (eid,'),
    ('                    if fuzz > 55 and fuzz > best_score:\n                        best_score = fuzz\n',
     '                    if fuzz > 85 and fuzz > best_score:\n                        best_score = fuzz\n'),
], "30 local_engine: strict name matching, settings never targeted")

# 31 -----------------------------------------------------------------------------------------
edit("const.py", '"clean_jarvis"', [
    ('DEFAULT_DIRECTIVE_PRESET   = "guardian_steward"', 'DEFAULT_DIRECTIVE_PRESET   = "clean_jarvis"'),
    ('"Be correct and brief. Do only what is asked.")\n',
     lambda o: o + 'DIRECTIVE_PRESETS = {"clean_jarvis": CLEAN_DIRECTIVE, **DIRECTIVE_PRESETS}\n'),
], "31a const: clean_jarvis preset is the default")
edit("migrations.py", 'setdefault("directive_preset", "clean_jarvis")', [
    ('options.setdefault("directive_preset", "guardian_steward")', 'options.setdefault("directive_preset", "clean_jarvis")'),
], "31b migrations: new installs use clean_jarvis")

# 32 -----------------------------------------------------------------------------------------
edit("agent.py", "_jh_want", [
    ('        new_state = hass.states.get(entity_id)\n        return json.dumps({\n            "success": True,\n            "entity_id": entity_id,\n            "previous_state": prev_state,\n            "new_state": new_state.state if new_state else "unknown",\n            "action": action,\n',
     '        # Jarvis Home patch (2026-11-05): cloud devices report back late, so wait for the expected state.\n'
     '        _jh_want = _EXPECTED_STATES.get(action)\n'
     '        _jh_want = tuple(_jh_want) if isinstance(_jh_want, (tuple, list, set)) else ((_jh_want,) if _jh_want else ())\n'
     '        for _ in range(16):\n'
     '            new_state = hass.states.get(entity_id)\n'
     '            if not _jh_want or (new_state and new_state.state in _jh_want):\n'
     '                break\n'
     '            await asyncio.sleep(0.25)\n'
     '        new_state = hass.states.get(entity_id)\n'
     '        return json.dumps({\n            "success": True,\n            "entity_id": entity_id,\n            "previous_state": prev_state,\n            "new_state": new_state.state if new_state else "unknown",\n'
     '            "confirmed": (not _jh_want) or bool(new_state and new_state.state in _jh_want),\n'
     '            "action": action,\n'),
], "32 agent: wait for the device to confirm a command")

# 33 -----------------------------------------------------------------------------------------
edit("frontend/jarvis-panel.js", "_sizeChat", [
    ('  .chat-log {\n    flex: 1 1 auto;\n    min-height: 240px;\n    max-height: 56vh;\n',
     '  .chat-log {\n    flex: 1 1 0;\n    min-height: 0;\n'),
    ('  _deleteChatMessage(i) {\n',
     lambda o: ('  // Jarvis Home patch (2026-11-05): the chat box fills the window on any screen; messages scroll inside it.\n'
                '  _sizeChat() {\n'
                '    const pn = this.shadowRoot?.querySelector(".chat-panel");\n'
                '    if (!pn) return;\n'
                '    const ft = this.shadowRoot.querySelector(".footer");\n'
                '    const h = window.innerHeight - pn.getBoundingClientRect().top - (ft ? ft.offsetHeight : 0) - 28;\n'
                '    pn.style.height = Math.max(320, Math.round(h)) + "px";\n'
                '    const log = this.shadowRoot.getElementById("chat-log");\n'
                '    if (log) log.scrollTop = log.scrollHeight;\n'
                '  }\n\n') + o),
    ('      this._loadChat();\n    }\n',
     '      this._loadChat();\n'
     '      this._sizeChat();\n'
     '      requestAnimationFrame(() => this._sizeChat());\n'
     '      if (!this._chatResize) {\n'
     '        this._chatResize = () => this._sizeChat();\n'
     '        window.addEventListener("resize", this._chatResize);\n'
     '      }\n'
     '    }\n'),
], "33 panel: chat box fills the window")

# 34 -----------------------------------------------------------------------------------------
edit("local_engine.py", "Jarvis Home patch: filler", [
    ('                   "please", "now", "for me", "jarvis"):\n', '                   "please", "now", "for me", "jarvis", "again"):\n'),
    ('    fragment = fragment.strip()\n    if not fragment:\n        return None\n',
     '    fragment = re.sub(r"^(?:my|light|lamp|plug|switch)\\s+(?=\\S)", "", fragment.strip())  # Jarvis Home patch: filler words\n'
     '    fragment = fragment.strip()\n    if not fragment:\n        return None\n'),
], "34 local_engine: filler words ignored in device names")

# 35 -----------------------------------------------------------------------------------------
COLOR = ('_JH_COLOR_RE = re.compile(r"^(?:please\\s+)?(?:switch|set|change|make|turn|put)\\s+(?:the\\s+)?(.+?)\\s+(?:to\\s+|in\\s+)?([a-z]+)(?:\\s+colou?r)?$")\n\n\n'
         'async def _jh_try_color(hass, normalized, honorific):\n'
         '    """Jarvis Home patch (2026-11-05): colour commands, read from the lamp\'s real abilities."""\n'
         '    m = _JH_COLOR_RE.match(normalized)\n'
         '    if not m or m.group(1).split()[0] in ("on", "off"):\n'
         '        return None\n'
         '    from homeassistant.util.color import color_name_to_rgb\n'
         '    try:\n'
         '        color_name_to_rgb(m.group(2))\n'
         '    except ValueError:\n'
         '        return None\n'
         '    found = _find_entity(hass, m.group(1), "light")\n'
         '    if not found:\n'
         '        return None\n'
         '    entity_id, fname = found\n'
         '    state = hass.states.get(entity_id)\n'
         '    modes = set((state.attributes.get("supported_color_modes") or []) if state else [])\n'
         '    if not modes & {"hs", "rgb", "rgbw", "rgbww", "xy"}:\n'
         '        return LocalResult(text=f"{fname} cannot show colours, {honorific}.", success=False)\n'
         '    await hass.services.async_call("light", "turn_on", {"entity_id": entity_id, "color_name": m.group(2)}, blocking=True)\n'
         '    return LocalResult(text=f"Done, {honorific}. {fname} is {m.group(2)}.", success=True)\n\n\n')
edit("local_engine.py", "_jh_try_color", [
    ('async def try_local(hass, text, honorific="sir", force=False):\n', lambda o: COLOR + o),
    ('    # Contextual queries\n    for pattern, qtype in _QUERY_PATTERNS:\n',
     lambda o: '    _jh_color = await _jh_try_color(hass, normalized, honorific)\n    if _jh_color is not None:\n        return _jh_color\n\n' + o),
], "35 local_engine: instant colour commands")

# 36 -----------------------------------------------------------------------------------------
VOICE_JS = r"""  // Jarvis Home patch (2026-11-05): voice in the Chat tab, through Home Assistant's own Assist pipeline.
  async _toggleMic() {
    if (this._micStop) { this._micStop(); return; }
    const conn = this._hass?.connection;
    const fail = (t) => { this._chatMessages.push({ role: "error", text: t }); this._refreshChat(); };
    if (!conn) return;
    if (!navigator.mediaDevices?.getUserMedia) { fail("The microphone needs the https address of this page."); return; }
    let stream;
    try { stream = await navigator.mediaDevices.getUserMedia({ audio: true }); }
    catch (err) { fail("Microphone not available: " + (err?.message || err)); return; }
    const ctx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 16000 });
    const src = ctx.createMediaStreamSource(stream);
    const proc = ctx.createScriptProcessor(4096, 1, 1);
    let hid = null, done = false, unsub = null;
    const btn = () => this.shadowRoot?.getElementById("chat-mic");
    const stop = () => {
      if (done) return;
      done = true;
      try { proc.disconnect(); src.disconnect(); } catch (e) {}
      stream.getTracks().forEach((t) => t.stop());
      ctx.close();
      if (hid !== null) { try { conn.socket.send(new Uint8Array([hid])); } catch (e) {} }
      this._micStop = null;
      btn()?.classList.remove("on");
    };
    this._micStop = stop;
    btn()?.classList.add("on");
    try {
      unsub = await conn.subscribeMessage((ev) => {
        if (ev.type === "run-start") hid = ev.data?.runner_data?.stt_binary_handler_id ?? null;
        else if (ev.type === "stt-vad-end") stop();
        else if (ev.type === "stt-end") {
          stop();
          if (unsub) unsub();
          const text = (ev.data?.stt_output?.text || "").trim();
          const input = this.shadowRoot?.getElementById("chat-input");
          if (text && input) { input.value = text; this._sendChat(); }
        } else if (ev.type === "error") {
          stop();
          if (unsub) unsub();
          fail("Voice: " + (ev.data?.message || ev.data?.code || "not understood"));
        }
      }, { type: "assist_pipeline/run", start_stage: "stt", end_stage: "stt", input: { sample_rate: 16000 }, timeout: 30 });
    } catch (err) { stop(); fail("Voice: " + (err?.message || err)); return; }
    proc.onaudioprocess = (e) => {
      if (hid === null || done) return;
      const f = e.inputBuffer.getChannelData(0);
      const pcm = new Int16Array(f.length);
      for (let i = 0; i < f.length; i++) pcm[i] = Math.max(-1, Math.min(1, f[i])) * 0x7fff;
      const msg = new Uint8Array(1 + pcm.byteLength);
      msg[0] = hid;
      msg.set(new Uint8Array(pcm.buffer), 1);
      try { conn.socket.send(msg); } catch (err) {}
    };
    src.connect(proc);
    proc.connect(ctx.destination);
  }

  async _speakChat(text) {
    if (!this._chatSpeak || !text) return;
    try {
      const unsub = await this._hass.connection.subscribeMessage((ev) => {
        if (ev.type === "tts-end") {
          const url = ev.data?.tts_output?.url;
          this._chatLastAudio = url;
          if (url) new Audio(url).play().catch((e) => console.warn("JARVIS chat: audio blocked", e));
        }
        if (ev.type === "run-end" || ev.type === "error") unsub();
      }, { type: "assist_pipeline/run", start_stage: "tts", end_stage: "tts", input: { text } });
    } catch (err) { console.warn("JARVIS chat: speech failed", err); }
  }

  _toggleSpeak() {
    this._chatSpeak = !this._chatSpeak;
    try { localStorage.setItem("jarvis_chat_speak", this._chatSpeak ? "1" : "0"); } catch (e) {}
    const b = this.shadowRoot?.getElementById("chat-speak");
    if (b) { b.classList.toggle("on", this._chatSpeak); b.textContent = this._chatSpeak ? "VOICE ON" : "VOICE OFF"; }
  }

"""
VOICE_CSS = ('  .chat-clean.on, .chat-mic.on { background: var(--cyan-faint); }\n'
             '  .chat-mic { background: none; border: 1px solid var(--line-hot); color: var(--cyan); font-family: var(--font-mono);\n'
             '    font-size: 11px; letter-spacing: 0.08em; padding: 0 14px; border-radius: var(--radius); cursor: pointer; }\n'
             '  .chat-mic.on { color: #ff4d4d; border-color: #ff4d4d; }\n')
edit("frontend/jarvis-panel.js", "_toggleMic", [
    ('    this._chatLoaded = false;       // saved history fetched once per page\n',
     lambda o: o + '    this._chatSpeak = false;\n    try { this._chatSpeak = localStorage.getItem("jarvis_chat_speak") === "1"; } catch (e) {}\n'),
    ('  _clearChat() {\n    this._chatMessages = [];\n', lambda o: VOICE_JS + o),
    ('      this._chatMessages.push({ role: "jarvis", text: reply });\n', lambda o: o + '      this._speakChat(reply);\n'),
    ('<button id="chat-clear" class="chat-clean">CLEAN</button></span>',
     '<button id="chat-clear" class="chat-clean">CLEAN</button><button id="chat-speak" class="chat-clean ${this._chatSpeak ? \'on\' : \'\'}">${this._chatSpeak ? \'VOICE ON\' : \'VOICE OFF\'}</button></span>'),
    ('        <button id="chat-send" class="chat-send"',
     '        <button id="chat-mic" class="chat-mic" title="Talk to JARVIS">MIC</button>\n        <button id="chat-send" class="chat-send"'),
    ('      this._sizeChat();\n      requestAnimationFrame(() => this._sizeChat());\n',
     lambda o: o + '      this.shadowRoot.getElementById("chat-mic")?.addEventListener("click", () => this._toggleMic());\n'
                   '      this.shadowRoot.getElementById("chat-speak")?.addEventListener("click", () => this._toggleSpeak());\n'),
    ('  .chat-clean:hover { background: var(--cyan-faint); }\n', lambda o: o + VOICE_CSS),
], "36 panel: mic and spoken replies in the Chat tab")

# 37 -----------------------------------------------------------------------------------------
edit("frontend/jarvis-panel.js", "chat-voice", [
    ('<button id="chat-speak" class="chat-clean ${this._chatSpeak ? \'on\' : \'\'}">${this._chatSpeak ? \'VOICE ON\' : \'VOICE OFF\'}</button></span>', '</span>'),
    ('        <button id="chat-mic" class="chat-mic" title="Talk to JARVIS">MIC</button>\n',
     '        <div class="chat-voice">\n'
     '          <button id="chat-speak" class="chat-tts ${this._chatSpeak ? \'on\' : \'\'}" title="Spoken replies on or off">${this._chatSpeak ? \'TTS ON\' : \'TTS OFF\'}</button>\n'
     '          <button id="chat-mic" class="chat-mic" title="Talk to JARVIS">MIC</button>\n'
     '        </div>\n'),
    ('if (url) new Audio(url).play().catch((e) => console.warn("JARVIS chat: audio blocked", e));',
     'if (url && this._chatSpeak) {\n'
     '            this._chatAudio?.pause();\n'
     '            this._chatAudio = new Audio(url);\n'
     '            this._chatAudio.play().catch((e) => console.warn("JARVIS chat: audio blocked", e));\n'
     '          }'),
    ('    if (b) { b.classList.toggle("on", this._chatSpeak); b.textContent = this._chatSpeak ? "VOICE ON" : "VOICE OFF"; }\n',
     '    if (b) { b.classList.toggle("on", this._chatSpeak); b.textContent = this._chatSpeak ? "TTS ON" : "TTS OFF"; }\n'
     '    if (!this._chatSpeak && this._chatAudio) { this._chatAudio.pause(); this._chatAudio = null; }\n'),
    ('  .chat-mic.on { color: #ff4d4d; border-color: #ff4d4d; }\n',
     lambda o: o + ('  .chat-voice { display: flex; flex-direction: column; gap: 4px; }\n'
                    '  .chat-voice button { flex: 1 1 0; min-height: 26px; }\n'
                    '  .chat-tts { background: none; border: 1px solid var(--line-hot); color: var(--text-dim); font-family: var(--font-mono);\n'
                    '    font-size: 10px; letter-spacing: 0.08em; padding: 0 10px; border-radius: var(--radius); cursor: pointer; white-space: nowrap; }\n'
                    '  .chat-tts.on { color: var(--cyan); background: var(--cyan-faint); }\n')),
], "37 panel: TTS button above the mic")

# 38 -----------------------------------------------------------------------------------------
ICONS = ("  _chatIcon(path) { return `<svg viewBox=\"0 0 24 24\" width=\"18\" height=\"18\" fill=\"currentColor\" aria-hidden=\"true\"><path d=\"${path}\"/></svg>`; }\n\n"
         "  _ttsIcon() {\n"
         "    return this._chatIcon(this._chatSpeak\n"
         "      ? \"M14,3.23V5.29C16.89,6.15 19,8.83 19,12C19,15.17 16.89,17.84 14,18.7V20.77C18,19.86 21,16.28 21,12C21,7.72 18,4.14 14,3.23M16.5,12C16.5,10.23 15.5,8.71 14,7.97V16C15.5,15.29 16.5,13.76 16.5,12M3,9V15H7L12,20V4L7,9H3Z\"\n"
         "      : \"M12,4L9.91,6.09L12,8.18M4.27,3L3,4.27L7.73,9H3V15H7L12,20V13.27L16.25,17.53C15.58,18.04 14.83,18.46 14,18.7V20.77C15.38,20.45 16.63,19.82 17.68,18.96L19.73,21L21,19.73L12,10.73M19,12C19,12.94 18.8,13.82 18.46,14.64L19.97,16.15C20.62,14.91 21,13.5 21,12C21,7.72 18,4.14 14,3.23V5.29C16.89,6.15 19,8.83 19,12M16.5,12C16.5,10.23 15.5,8.71 14,7.97V10.18L16.45,12.63C16.5,12.43 16.5,12.21 16.5,12Z\");\n"
         "  }\n\n"
         "  _micIcon() {\n"
         "    return this._chatIcon(\"M12,2A3,3 0 0,1 15,5V11A3,3 0 0,1 12,14A3,3 0 0,1 9,11V5A3,3 0 0,1 12,2M19,11C19,14.53 16.39,17.44 13,17.93V21H11V17.93C7.61,17.44 5,14.53 5,11H7A5,5 0 0,0 12,16A5,5 0 0,0 17,11H19Z\");\n"
         "  }\n\n")
ICON_CSS = ('  .chat-voice { flex: none; width: 30px; align-self: stretch; justify-content: space-between; }\n'
            '  .chat-voice button { flex: none; width: 30px; height: 30px; min-height: 0; padding: 0; display: flex;\n'
            '    align-items: center; justify-content: center; }\n')
edit("frontend/jarvis-panel.js", "_ttsIcon", [
    ('  _toggleSpeak() {\n', lambda o: ICONS + o),
    ('title="Spoken replies on or off">${this._chatSpeak ? \'TTS ON\' : \'TTS OFF\'}</button>', 'title="Spoken replies on or off" aria-label="Spoken replies">${this._ttsIcon()}</button>'),
    ('title="Talk to JARVIS">MIC</button>', 'title="Talk to JARVIS" aria-label="Microphone">${this._micIcon()}</button>'),
    ('b.textContent = this._chatSpeak ? "TTS ON" : "TTS OFF"; }', 'b.innerHTML = this._ttsIcon(); }'),
    ('  .chat-tts.on { color: var(--cyan); background: var(--cyan-faint); }\n', lambda o: o + ICON_CSS),
], "38 panel: icon buttons for TTS and mic")

# 39 -----------------------------------------------------------------------------------------
edit("frontend/jarvis-panel.js", "chat-compose-even", [
    ('  .chat-voice { flex: none; width: 30px; align-self: stretch; justify-content: space-between; }\n',
     lambda o: ('  /* chat-compose-even */\n'
                '  .chat-compose { align-items: stretch !important; }\n'
                '  .chat-compose .chat-input { height: 64px; min-height: 64px; box-sizing: border-box; }\n'
                '  .chat-compose .chat-send { height: 64px; align-self: stretch; }\n'
                '  .chat-voice { height: 64px; }\n') + o),
], "39 panel: compose row is one even height")

# 40 -----------------------------------------------------------------------------------------
PIPE = ('  // Jarvis Home patch (2026-11-05): use the speech settings of the assistant that runs JARVIS.\n'
        '  async _chatPipeline() {\n'
        '    try {\n'
        '      const res = await this._hass.callWS({ type: "assist_pipeline/pipeline/list" });\n'
        '      const agent = this._jarvisAgentId();\n'
        '      const all = (res?.pipelines || []).filter((x) => x.conversation_engine === agent);\n'
        '      const pick = all.find((x) => x.id === res?.preferred_pipeline) || all[0];\n'
        '      return pick?.id;\n'
        '    } catch (err) { return undefined; }\n'
        '  }\n\n')
edit("frontend/jarvis-panel.js", "_chatPipeline", [
    ('  _toggleSpeak() {\n', lambda o: PIPE + o),
    ('      unsub = await conn.subscribeMessage((ev) => {\n', lambda o: '      const pipeId = await this._chatPipeline();\n' + o),
    ('{ type: "assist_pipeline/run", start_stage: "stt", end_stage: "stt", input: { sample_rate: 16000 }, timeout: 30 }',
     '{ type: "assist_pipeline/run", pipeline: pipeId, start_stage: "stt", end_stage: "stt", input: { sample_rate: 16000 }, timeout: 30 }'),
    ('      const unsub = await this._hass.connection.subscribeMessage((ev) => {\n', lambda o: '      const pipeId = await this._chatPipeline();\n' + o),
    ('{ type: "assist_pipeline/run", start_stage: "tts", end_stage: "tts", input: { text } }',
     '{ type: "assist_pipeline/run", pipeline: pipeId, start_stage: "tts", end_stage: "tts", input: { text } }'),
], "40 panel: Chat voice follows the JARVIS assistant settings")

# 41 -----------------------------------------------------------------------------------------
edit("frontend/jarvis-panel.js", "chat-compose-oneline", [
    ('  .chat-compose .chat-input { height: 64px; min-height: 64px; box-sizing: border-box; }\n  .chat-compose .chat-send { height: 64px; align-self: stretch; }\n  .chat-voice { height: 64px; }\n',
     '  /* chat-compose-oneline */\n  .chat-compose .chat-input { height: 40px; min-height: 40px; box-sizing: border-box; }\n  .chat-compose .chat-send { height: 40px; align-self: stretch; }\n'),
    ('  .chat-voice { flex: none; width: 30px; align-self: stretch; justify-content: space-between; }\n',
     '  .chat-voice { flex: none; flex-direction: row !important; width: auto; height: 40px; gap: 8px !important; align-self: stretch; }\n'),
    ('  .chat-voice button { flex: none; width: 30px; height: 30px; min-height: 0; padding: 0; display: flex;\n',
     '  .chat-voice button { flex: none; width: 40px; height: 40px; min-height: 0; padding: 0; display: flex;\n'),
], "41 panel: one-line box, icons side by side")

# 42 -----------------------------------------------------------------------------------------
edit("frontend/jarvis-panel.js", "chat-hint-removed", [
    (between("frontend/jarvis-panel.js", '      <div class="chat-hint">Same brain as voice', '</div>\n') + '</div>\n', '      <!-- chat-hint-removed -->\n'),
], "42 panel: Chat tab hint text removed")

# 43 -----------------------------------------------------------------------------------------
edit("frontend/jarvis-panel.js", "_micHeld", [
    ('      this.shadowRoot.getElementById("chat-mic")?.addEventListener("click", () => this._toggleMic());\n',
     '      const micBtn = this.shadowRoot.getElementById("chat-mic");\n'
     '      if (micBtn) {\n'
     '        micBtn.addEventListener("pointerdown", (e) => {\n'
     '          e.preventDefault();\n'
     '          try { micBtn.setPointerCapture(e.pointerId); } catch (err) {}\n'
     '          this._micHeld = true;\n'
     '          if (!this._micStop) this._toggleMic();\n'
     '        });\n'
     '        const release = () => { this._micHeld = false; if (this._micStop) this._micStop(); };\n'
     '        ["pointerup", "pointercancel", "lostpointercapture"].forEach((n) => micBtn.addEventListener(n, release));\n'
     '        micBtn.addEventListener("contextmenu", (e) => e.preventDefault());\n'
     '      }\n'),
    ('    src.connect(proc);\n    proc.connect(ctx.destination);\n',
     '    src.connect(proc);\n    proc.connect(ctx.destination);\n    if (!this._micHeld) stop();  // released while the mic was starting\n'),
    ('title="Talk to JARVIS" aria-label="Microphone"', 'title="Hold to talk" aria-label="Hold to talk"'),
    ('  .chat-mic.on { color: #ff4d4d; border-color: #ff4d4d; }\n', lambda o: o + '  .chat-mic { touch-action: none; user-select: none; -webkit-user-select: none; }\n'),
], "43 panel: press and hold to talk")

# 44 -----------------------------------------------------------------------------------------
PREVIEW = r"""    // Jarvis Home patch (2026-11-05): show that the mic is listening.
    const input0 = this.shadowRoot?.getElementById("chat-input");
    const hint0 = input0?.placeholder;
    if (input0) input0.placeholder = "Listening...";
"""
edit("frontend/jarvis-panel.js", "show that the mic is listening", [
    ('    let hid = null, done = false, unsub = null;\n', '    let hid = null, done = false, unsub = null, pipeId;\n'),
    ('      const pipeId = await this._chatPipeline();\n      unsub = await conn.subscribeMessage((ev) => {\n',
     '      pipeId = await this._chatPipeline();\n      unsub = await conn.subscribeMessage((ev) => {\n'),
    ('    this._micStop = stop;\n    btn()?.classList.add("on");\n', lambda o: PREVIEW + o),
    ('      if (done) return;\n      done = true;\n      try { proc.disconnect(); src.disconnect(); } catch (e) {}\n',
     '      if (done) return;\n      done = true;\n'
     '      if (input0) input0.placeholder = hint0;\n'
     '      try { proc.disconnect(); src.disconnect(); } catch (e) {}\n'),
    ('          if (text && input) { input.value = text; this._sendChat(); }\n',
     '          if (text && input) { input.value = text; setTimeout(() => this._sendChat(), 1200); }\n'),
], "44a panel: words appear while talking")
edit("local_engine.py", 'replace("colour", "color")', [
    ('    fragment = name_fragment.lower().strip()\n\n    # Strip common suffixes\n',
     '    fragment = name_fragment.lower().strip().replace("colour", "color")\n\n    # Strip common suffixes\n'),
    ('            if _jh_is_setting(hass, eid):\n                continue\n            fname = (state.attributes.get("friendly_name") or "").lower()\n',
     '            if _jh_is_setting(hass, eid):\n                continue\n            fname = (state.attributes.get("friendly_name") or "").lower().replace("colour", "color")\n'),
], "44b local_engine: colour and color match")

# 46 -----------------------------------------------------------------------------------------
edit("frontend/jarvis-panel.js", "_chatReq", [
    ('    this._chatMessages.push({ role: "user", text });\n    this._chatBusy = true;\n',
     '    this._chatMessages.push({ role: "user", text });\n    const reqId = (this._chatReq = (this._chatReq || 0) + 1);\n    this._chatBusy = true;\n'),
    ('      this._chatConvId = res?.conversation_id || this._chatConvId;\n',
     lambda o: '      if (reqId !== this._chatReq) return;  // cancelled with the red cross\n' + o),
    ('    } catch (err) {\n      this._chatMessages.push({\n        role: "error",\n        text: "Couldn\'t reach JARVIS',
     '    } catch (err) {\n      if (reqId !== this._chatReq) return;\n      this._chatMessages.push({\n        role: "error",\n        text: "Couldn\'t reach JARVIS'),
    ('    } finally {\n      this._chatBusy = false;\n      this._saveChat();\n',
     '    } finally {\n      if (reqId !== this._chatReq) return;\n      this._chatBusy = false;\n      this._saveChat();\n'),
    ('  _deleteChatMessage(i) {\n',
     lambda o: o + ('    if (this._chatBusy && i === this._chatMessages.length - 1 && this._chatMessages[i]?.role === "user") {\n'
                    '      this._chatReq = (this._chatReq || 0) + 1;\n'
                    '      this._chatBusy = false;\n'
                    '    }\n')),
], "46 panel: red cross cancels a pending message")

# 47 -----------------------------------------------------------------------------------------
OPT = ('def _jh_opt(hass, key: str) -> bool:\n'
       '    """Jarvis Home patch (2026-11-05): on/off switches from the JARVIS Core settings, on by default."""\n'
       '    try:\n'
       '        from . import jarvis_config\n'
       '        from .directive_helper import get_entry\n'
       '        return bool(jarvis_config.runtime_get(hass, get_entry(hass), key, True))\n'
       '    except Exception:\n'
       '        return True\n\n\n')
edit("agent.py", "_jh_opt", [
    ('def _mail_guard(tool_name: str) -> Optional[str]:\n', lambda o: OPT + o),
    ('    if depth == 0:\n        _MAIL_TAINT.set({"read": False})\n',
     '    if depth == 0:\n        _MAIL_TAINT.set({"read": False} if _jh_opt(hass, "jh_mail_block") else None)\n'),
    ('    _cmd, _asked = await _jh_turn(hass, user_input)\n',
     lambda o: o + '    _strict = _jh_opt(hass, "jh_strict_actions")\n'),
    ('            if not _nudged and tools and text and ((_cmd and not _changed)',
     '            if _strict and not _nudged and tools and text and ((_cmd and not _changed)'),
], "47a agent: switches for action checks and mail block")
FACTS_ON = ('def _jh_facts_only_on(hass) -> bool:\n'
            '    \"\"\"Jarvis Home patch (2026-11-05): the Core switch for facts-only briefings. If it cannot be read, stay with facts.\"\"\"\n'
            '    try:\n'
            '        from . import jarvis_config\n'
            '        from .directive_helper import get_entry\n'
            '        return bool(jarvis_config.runtime_get(hass, get_entry(hass), "jh_facts_only_briefings", True))\n'
            '    except Exception:\n'
            '        return True\n\n\n')
edit("briefing.py", "jh_facts_only_briefings", [
    ('def _facts_home(hass, people: bool = False) -> list[str]:\n', lambda o: FACTS_ON + o),   # 71 rewrites what comes after it
    ('    briefing_text = _speakable(_plain_briefing(greeting, honorific, context_lines))\n',
     '    if _jh_facts_only_on(hass):\n'
     '        briefing_text = _speakable(_plain_briefing(greeting, honorific, context_lines))\n'
     '    else:\n'
     '        try:\n'
     '            result = await hass.async_add_executor_job(lambda: groq_client.chat(\n'
     '                messages=[{"role": "system", "content": system}, {"role": "user", "content": f"Context:\\n{context}"}],\n'
     '                max_tokens=1500, temperature=0.6))\n'
     '            briefing_text = (result.get("text") or "").strip()\n'
     '        except Exception as exc:\n'
     '            _LOGGER.error("JARVIS briefing error: %s", exc)\n'
     '            briefing_text = ""\n'),
], "47b briefing: facts-only switch")
edit("config_flow.py", "jh_strict_actions", [
    ('                                              DEFAULT_WEB_RESEARCH_LLM_FALLBACK)):\n                selector.BooleanSelector(),\n        })\n        return self.async_show_form(step_id="core"',
     '                                              DEFAULT_WEB_RESEARCH_LLM_FALLBACK)):\n                selector.BooleanSelector(),\n'
     '            vol.Optional("jh_strict_actions", description=self._sv("jh_strict_actions", True)):\n                selector.BooleanSelector(),\n'
     '            vol.Optional("jh_mail_block", description=self._sv("jh_mail_block", True)):\n                selector.BooleanSelector(),\n'
     '            vol.Optional("jh_facts_only_briefings", description=self._sv("jh_facts_only_briefings", True)):\n                selector.BooleanSelector(),\n'
     '        })\n        return self.async_show_form(step_id="core"'),
], "47c config_flow: switches in Core settings")
labels("47d", {"jh_strict_actions": "Check that actions really ran (recommended)",
               "jh_mail_block": "Block actions after reading an email (recommended)",
               "jh_facts_only_briefings": "Scheduled briefings use real facts only, no AI text (recommended)"})

# 48 -----------------------------------------------------------------------------------------
THINK_CSS = ('  .chat-think { display: inline-flex; align-items: center; gap: 8px; font-family: var(--font-mono); font-size: 12px; letter-spacing: 0.06em; }\n'
             '  .chat-spark { width: 16px; height: 16px; color: var(--cyan); animation: chatSpark 1.6s ease-in-out infinite; transform-origin: center; }\n'
             '  .chat-think-t { background: linear-gradient(90deg, var(--text-dim) 25%, var(--cyan) 50%, var(--text-dim) 75%);\n'
             '    background-size: 220% 100%; -webkit-background-clip: text; background-clip: text; color: transparent; animation: chatShimmer 1.8s linear infinite; }\n'
             '  .chat-think-s { color: var(--text-dim); font-size: 11px; opacity: 0.8; }\n'
             '  @keyframes chatSpark { 0%, 100% { transform: scale(0.8) rotate(0deg); opacity: 0.55; } 50% { transform: scale(1.1) rotate(90deg); opacity: 1; } }\n'
             '  @keyframes chatShimmer { from { background-position: 120% 0; } to { background-position: -120% 0; } }\n')
THINK_JS = ('  // Jarvis Home patch (2026-11-05): the indicator lives only while a request is really pending.\n'
            '  _startThink() {\n'
            '    this._chatStart = Date.now();\n'
            '    clearInterval(this._thinkTimer);\n'
            '    this._thinkTimer = setInterval(() => {\n'
            '      if (!this._chatBusy) { clearInterval(this._thinkTimer); return; }\n'
            '      const el = this.shadowRoot?.querySelector(".chat-think-s");\n'
            '      if (el) el.textContent = Math.floor((Date.now() - this._chatStart) / 1000) + "s";\n'
            '    }, 1000);\n'
            '  }\n\n')
edit("frontend/jarvis-panel.js", "_startThink", [
    ('<div class="chat-msg chat-jarvis chat-typing"><span class="chat-who">JARVIS</span><span class="chat-text">\u2026</span></div>',
     '<div class="chat-msg chat-jarvis chat-typing"><span class="chat-who">JARVIS</span><span class="chat-think"><svg class="chat-spark" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M12 1l2.6 8.4L23 12l-8.4 2.6L12 23l-2.6-8.4L1 12l8.4-2.6z"/></svg><span class="chat-think-t">Thinking</span><span class="chat-think-s">${Math.floor((Date.now() - (this._chatStart || Date.now())) / 1000)}s</span></span></div>'),
    ('  _deleteChatMessage(i) {\n', lambda o: THINK_JS + o),
    ('    const reqId = (this._chatReq = (this._chatReq || 0) + 1);\n    this._chatBusy = true;\n',
     lambda o: o + '    this._startThink();\n'),
    ('  .chat-clean:hover { background: var(--cyan-faint); }\n', lambda o: o + THINK_CSS),
], "48 panel: animated Thinking indicator")

# 49 -----------------------------------------------------------------------------------------
VAGUE = ('def _jh_vague(hass, name_fragment):\n'
         '    """Jarvis Home patch (2026-11-05): names of every device a vague word could mean, read live."""\n'
         '    frag = re.sub(r"^(?:my|the|light|lamp|plug|switch)\\s+", "", name_fragment.lower().strip()).replace("colour", "color")\n'
         '    hits = []\n'
         '    for dom in ("light", "switch", "climate"):\n'
         '        for st in hass.states.async_all(dom):\n'
         '            nm = st.attributes.get("friendly_name") or ""\n'
         '            if _jh_is_setting(hass, st.entity_id):\n'
         '                continue\n'
         '            if nm.lower().replace("colour", "color") == frag:\n'
         '                return []\n'
         '            if frag in nm.lower().replace("colour", "color") and nm not in hits:\n'
         '                hits.append(nm)\n'
         '    return hits if len(hits) > 1 else []\n\n\n')
edit("local_engine.py", "_jh_vague", [
    ('def _find_scene_or_script(hass, name_fragment):\n', lambda o: VAGUE + o),
    ('    (r"switch\\s+off\\s+(?:the\\s+)?(.+?)(?:\\s+light(?:s)?)?$",    "turn_off", "light"),\n',
     lambda o: o + '    (r"(?:switch|turn)\\s+(?:the\\s+)?(.+?)\\s+on$",  "turn_on",  "light"),\n'
                   '    (r"(?:switch|turn)\\s+(?:the\\s+)?(.+?)\\s+off$", "turn_off", "light"),\n'),
    ('        entity_id, fname = resolved\n        args = {}\n',
     lambda o: ('        if action in ("turn_on", "turn_off"):\n'
                '            _jh_many = _jh_vague(hass, name_frag)\n'
                '            if _jh_many:\n'
                '                return LocalResult(text=f"Which one, {honorific}: {\', \'.join(_jh_many[:-1])} or {_jh_many[-1]}?", success=False)\n') + o),
], "49 local_engine: word order and vague names")

# 50 -----------------------------------------------------------------------------------------
edit("local_engine.py", "_JH_PENDING", [
    ('def _jh_vague(hass, name_fragment):\n', lambda o: '_JH_PENDING = {}\n\n\n' + o),
    ('            if frag in nm.lower().replace("colour", "color") and nm not in hits:\n                hits.append(nm)\n',
     '            if frag in nm.lower().replace("colour", "color") and nm not in [h[1] for h in hits]:\n                hits.append((st.entity_id, nm))\n'),
    ("""            if _jh_many:\n                return LocalResult(text=f"Which one, {honorific}: {', '.join(_jh_many[:-1])} or {_jh_many[-1]}?", success=False)\n""",
     '            if _jh_many:\n'
     '                def _jh_on(e):\n'
     '                    st = hass.states.get(e)\n'
     '                    return bool(st) and st.state not in ("off", "unavailable", "unknown")\n'
     '                _jh_fit = [x for x in _jh_many if _jh_on(x[0]) == (action == "turn_off")]\n'
     '                if not _jh_fit:\n'
     '                    return LocalResult(text=f"They are all already {\'off\' if action == \'turn_off\' else \'on\'}, {honorific}.", success=True)\n'
     '                if len(_jh_fit) == 1:\n'
     '                    resolved = _jh_fit[0]\n'
     '                else:\n'
     '                    import time as _jh_t\n'
     '                    _JH_PENDING.update(action=action, options=_jh_fit, at=_jh_t.time())\n'
     '                    _jh_n = [x[1] for x in _jh_fit]\n'
     '                    return LocalResult(text=f"Which one, {honorific}: {\', \'.join(_jh_n[:-1])} or {_jh_n[-1]}?", success=False)\n'),
    ('    normalized = _normalize(text)\n    complexity = score_complexity(text)\n',
     lambda o: o + ('    if _JH_PENDING:\n'
                    '        import time as _jh_t\n'
                    '        _jh_p = dict(_JH_PENDING)\n'
                    '        _JH_PENDING.clear()\n'
                    '        _jh_w = re.sub(r"^(?:the|in the)\\s+", "", normalized)\n'
                    '        _jh_m = [x for x in _jh_p["options"] if _jh_w and _jh_w in x[1].lower()]\n'
                    '        if _jh_t.time() - _jh_p["at"] < 90 and len(_jh_m) == 1:\n'
                    '            _jh_ok = await _execute_action(hass, _jh_p["action"], _jh_m[0][0], {})\n'
                    '            return LocalResult(text=_resp(_jh_p["action"], _jh_m[0][1], _jh_ok, {}, honorific), success=_jh_ok)\n')),
], "50 local_engine: vague names use live state, follow-up answers work")

# 51 -----------------------------------------------------------------------------------------
edit("websocket.py", "camera_watch_default", [
    ('"banter_level":         _runtime_opt(hass, entry, "banter_level", 1),\n',
     lambda o: o + '            "camera_watch_default": str(_runtime_opt(hass, entry, "camera_watch_default", "") or ""),\n'),
    ('"banter_level",              # int: 0 plain', lambda o: '"camera_watch_default",      # str: camera Camera Watch opens on\n    ' + o),
], "51a websocket: camera_watch_default setting")
edit("config_flow.py", "camera_watch_default", [
    ('            vol.Optional("jh_strict_actions", description=self._sv("jh_strict_actions", True)):\n',
     lambda o: '            vol.Optional("camera_watch_default", description=self._sv("camera_watch_default", "")):\n'
               '                selector.EntitySelector(selector.EntitySelectorConfig(domain="camera")),\n' + o),
], "51b config_flow: camera setting in Core")
labels("51c", {"camera_watch_default": "Camera Watch opens on this camera"})

# 54 -----------------------------------------------------------------------------------------
TRIM = ('_JH_OFFER_RE = _re.compile(r"(?:how (?:may|can) i|what (?:else )?can i|would you like|shall i|do you want me|let me know|anything else|is there anything|feel free)", _re.I)\n\n\n'
        'def _jh_trim_offer(text: str) -> str:\n'
        '    """Jarvis Home patch (2026-11-05): drop a closing offer of help, keep the answer before it."""\n'
        '    parts = _re.split(r"(?<=[.!?])\\s+", (text or "").strip())\n'
        '    while len(parts) > 1 and _JH_OFFER_RE.search(parts[-1]):\n'
        '        parts.pop()\n'
        '    return " ".join(parts)\n\n\n')
edit("agent.py", "_jh_trim_offer", [
    ('def _no_action_note(served_by: str, n_tools: int) -> str:\n', lambda o: TRIM + o),
    ('            return text.strip() if _has_text else _empty_reply_fallback(config)\n',
     '            return _jh_trim_offer(text) if _has_text else _empty_reply_fallback(config)\n'),
    ('f"Never promise a later report, and never state the result of something you did not run.\\n\\n"\n',
     'f"Never promise a later report, and never state the result of something you did not run. "\n'
     '        f"To change a lamp colour use the HassLightSet tool. Never say a lamp has no colour without checking it.\\n\\n"\n'),
], "54a agent: closing offers, polite commands, colour rule")
edit("local_engine.py", "could you", [
    ('_JH_COLOR_RE = re.compile(r"^(?:please\\s+)?(?:switch|set|change|make|turn|put)',
     '_JH_COLOR_RE = re.compile(r"^(?:(?:hey\\s+)?jarvis[,\\s]+)?(?:please\\s+|could you\\s+|can you\\s+|would you\\s+)*(?:switch|set|change|make|turn|put)'),
    ('    if not m or m.group(1).split()[0] in ("on", "off"):\n', '    if not m or m.group(1).split()[0] in ("on", "off") or m.group(2) in ("please", "now"):\n'),
    ('    (r"(?:switch|turn)\\s+(?:the\\s+)?(.+?)\\s+off$", "turn_off", "light"),\n',
     lambda o: o + '    (r"(?:switch|turn)\\s+(?:the\\s+)?(.+?)\\s+on\\s+please$",  "turn_on",  "light"),\n'
                   '    (r"(?:switch|turn)\\s+(?:the\\s+)?(.+?)\\s+off\\s+please$", "turn_off", "light"),\n'),
], "54b local_engine: polite commands")

# 60 -----------------------------------------------------------------------------------------
# Audit fixes (2026-11-06). These sections work on the text left by the patches above, so keep them after 54.
FRAG = r'''def _jh_frag(name_fragment):
    """Jarvis Home patch (2026-11-06): one name clean-up shared by the resolver and the vague check."""
    fragment = name_fragment.lower().strip().replace("colour", "color")
    changed = True
    while changed:
        changed = False
        for suffix in ("light", "lights", "lamp", "lamps", "switch", "switches",
                       "fan", "fans", "lock", "locks", "door", "doors",
                       "cover", "covers", "thermostat", "sensor",
                       "please", "now", "for me", "jarvis", "again"):
            shorter = re.sub(rf"\s+{suffix}$", "", fragment)
            if shorter != fragment:
                fragment, changed = shorter, True
    return re.sub(r"^(?:my|the|light|lamp|plug|switch)\s+(?=\S)", "", fragment.strip()).strip()  # Jarvis Home patch: filler words


'''
VAGUE60 = r'''    frag = _jh_frag(name_fragment)
    if not frag:
        return []
    hits, exact = [], []
    for dom in ("light", "switch", "climate"):
        for st in hass.states.async_all(dom):
            nm = st.attributes.get("friendly_name") or ""
            low = nm.lower().replace("colour", "color")
            if _jh_is_setting(hass, st.entity_id):
                continue
            if low == frag:
                return []
            if frag in low and nm not in [h[1] for h in hits]:
                hits.append((st.entity_id, nm))
                if _jh_frag(nm) == frag:
                    exact.append(nm)
    if len(exact) == 1:
        return []
'''
FIT60 = r'''                def _jh_st(e):
                    st = hass.states.get(e)
                    return st.state if st else "unavailable"
                _jh_live = [x for x in _jh_many if _jh_st(x[0]) not in ("unavailable", "unknown")]
                _jh_dead = [x[1] for x in _jh_many if _jh_st(x[0]) in ("unavailable", "unknown")]
                _jh_verb = "is" if len(_jh_dead) == 1 else "are"
                if not _jh_live:
                    return LocalResult(text=f"{', '.join(_jh_dead)} {_jh_verb} unavailable, {honorific}. Nothing was changed.", success=False)
                _jh_fit = [x for x in _jh_live if (_jh_st(x[0]) != "off") == (action == "turn_off")]
                if not _jh_fit:
                    _jh_who = "They are all" if len(_jh_live) > 1 else f"{_jh_live[0][1]} is"
                    _jh_tail = f" {', '.join(_jh_dead)} {_jh_verb} unavailable." if _jh_dead else ""
                    return LocalResult(text=f"{_jh_who} already {'off' if action == 'turn_off' else 'on'}, {honorific}.{_jh_tail}", success=True)
                if len(_jh_fit) == 1:
                    resolved = _jh_fit[0]
                elif any(_needs_confirmation(hass, action, x[0]) for x in _jh_fit):
                    return None   # protected device: the agent runs the confirmation gate
                else:
                    _JH_PENDING[pending_key] = {"action": action, "options": _jh_fit, "at": time.time()}
                    _jh_n = [x[1] for x in _jh_fit]
                    return LocalResult(text=f"Which one, {honorific}: {', '.join(_jh_n[:-1])} or {_jh_n[-1]}?", success=False)
'''
PENDING60 = r'''    for _jh_k in [k for k, v in _JH_PENDING.items() if time.time() - v["at"] >= 90]:
        del _JH_PENDING[_jh_k]
    _jh_p = _JH_PENDING.pop(pending_key, None)
    if _jh_p:
        _jh_w = re.sub(r"\s+please$", "", re.sub(r"^(?:the|in the)\s+", "", normalized)).split()
        _jh_m = [x for x in _jh_p["options"] if _jh_w and set(_jh_w) <= set(x[1].lower().split())]
        if len(_jh_m) == 1:
            _jh_eid, _jh_name = _jh_m[0]
            if _needs_confirmation(hass, _jh_p["action"], _jh_eid):
                return None
            _jh_ok = await _execute_action(hass, _jh_p["action"], _jh_eid, {})
            _update_ctx(entity=_jh_eid, domain=_jh_eid.split(".")[0], action=_jh_p["action"])
            return LocalResult(text=_resp(_jh_p["action"], _jh_name, _jh_ok, {}, honorific), success=_jh_ok)

'''
COLOR60 = r'''    entity_id, fname = found
    state = hass.states.get(entity_id)
    if state is None or state.state in ("unavailable", "unknown"):
        return LocalResult(text=f"{fname} is offline, {honorific}. Nothing was changed.", success=False)
    modes = set(state.attributes.get("supported_color_modes") or [])
    if not modes & {"hs", "rgb", "rgbw", "rgbww", "xy"}:
        return LocalResult(text=f"{fname} cannot show colours, {honorific}.", success=False)
    if _needs_confirmation(hass, "turn_on", entity_id):
        return None
    colour = m.group(2)
    if not await _seam_execute(hass, "light", "turn_on", {"entity_id": entity_id, "color_name": colour},
                               blocking=True, action="turn_on"):
        return LocalResult(text=f"I wasn't able to set {fname} to {colour}, {honorific}.", success=False)
    _update_ctx(entity=entity_id, domain="light", action="turn_on")
    import asyncio
    for _ in range(8):
        if _jh_shows(hass.states.get(entity_id), want):
            return LocalResult(text=f"Done, {honorific}. {fname} is {colour}.", success=True)
        await asyncio.sleep(0.25)
    return LocalResult(text=f"I sent {colour} to {fname}, {honorific}, but it has not confirmed the change.", success=False)


'''
SHOWS60 = r'''def _jh_shows(state, want) -> bool:
    """Jarvis Home patch (2026-11-06): the lamp is on and reports roughly the asked colour. Lamps round
    colours, so a wide margin only rejects a lamp that still shows something clearly different."""
    if state is None or state.state != "on":
        return False
    got = state.attributes.get("rgb_color")
    if not got:
        return True
    return sum((int(a) - int(b)) ** 2 for a, b in zip(got, want)) <= 130 ** 2


'''
HEATERS60 = r'''def _jh_heaters(hass):
    """Jarvis Home patch (2026-11-06): heater switches and climate devices, read live. A switch counts when
    it has the Home Assistant label Heater or Heating on the entity or its device, or heater or radiator in
    its id or name. Setting entities such as a child lock or display never count."""
    def labelled(eid):
        try:
            from homeassistant.helpers import device_registry as dr, entity_registry as er
            entry = er.async_get(hass).async_get(eid)
            if entry is None:
                return False
            found = set(entry.labels or ())
            if entry.device_id:
                device = dr.async_get(hass).async_get(entry.device_id)
                found |= set(getattr(device, "labels", None) or ())
            return bool({x.lower() for x in found} & {"heater", "heating"})
        except Exception:
            return False
    switches = []
    for s in hass.states.async_all("switch"):
        words = f"{s.entity_id} {s.attributes.get('friendly_name') or ''}".lower()
        if not _jh_is_setting(hass, s.entity_id) and ("heater" in words or "radiator" in words or labelled(s.entity_id)):
            switches.append(s)
    return switches, list(hass.states.async_all("climate"))


'''
edit("local_engine.py", "_jh_frag", [
    ('def _find_entity(hass, name_fragment, domain_hint=None, learned_aliases=None):\n', lambda o: FRAG + HEATERS60 + o),
    (between("local_engine.py", '    fragment = name_fragment.lower().strip().replace("colour", "color")\n\n    # Strip common suffixes\n', '    if not fragment:\n        return None\n'),
     '    fragment = _jh_frag(name_fragment)\n'),
    (between("local_engine.py", '    frag = re.sub(r"^(?:my|the|light|lamp|plug|switch)\\s+", "", name_fragment', '    return hits if len(hits) > 1 else []\n'), VAGUE60),
    (between("local_engine.py", '                def _jh_on(e):\n', '        entity_id, fname = resolved\n        args = {}\n'), FIT60),
    (between("local_engine.py", '    if _JH_PENDING:\n        import time as _jh_t\n', '    # High complexity \u2192 immediate LLM'), PENDING60),
    ('async def try_local(hass, text, honorific="sir", force=False):\n', 'async def try_local(hass, text, honorific="sir", force=False, pending_key=""):\n'),
    ('        result = await try_local(hass, stripped, honorific, force=force)\n', '        result = await try_local(hass, stripped, honorific, force=force, pending_key=pending_key)\n'),
    ('_JH_PENDING = {}\n',
     '# Jarvis Home patch (2026-11-06): one open "which one" question per conversation, never shared between people.\n'
     '_JH_PENDING = {}\n'
     '# Words that point back at something said before. Only the AI has that context, so they are never device names.\n'
     '_JH_PRONOUNS = frozenset(("it", "that", "this", "them", "those", "these", "everything", "all"))\n'),
    ('        if not name_frag:\n            continue\n        resolved = _find_entity(\n',
     '        if not name_frag:\n            continue\n'
     '        if name_frag.strip() in _JH_PRONOUNS:\n            _last_failed_name = name_frag\n            continue\n'
     '        resolved = _find_entity(\n'),
    ('or m.group(2) in ("please", "now"):\n', 'or m.group(2) in ("please", "now") or m.group(1).strip() in _JH_PRONOUNS:\n'),
    ('    try:\n        color_name_to_rgb(m.group(2))\n', '    try:\n        want = color_name_to_rgb(m.group(2))\n'),
    (between("local_engine.py", '    entity_id, fname = found\n    state = hass.states.get(entity_id)\n    modes = set(', 'async def try_local('), COLOR60),
    ('async def _jh_try_color(hass, normalized, honorific):\n', lambda o: SHOWS60 + o),
], "60 local_engine: right device, pending per conversation, pronouns, colour through the seam")

# 61 -----------------------------------------------------------------------------------------
OFFLINE61 = r'''def _jh_offline(hass):
    """Jarvis Home patch (2026-11-06): door, window and lock devices with no usable state. An all-clear must
    not rest on them."""
    found = [s for s in hass.states.async_all("binary_sensor")
             if s.attributes.get("device_class") in ("door", "window", "garage_door")]
    found += list(hass.states.async_all("cover")) + list(hass.states.async_all("lock"))
    return [s.attributes.get("friendly_name", s.entity_id) for s in found
            if s.state in ("unavailable", "unknown", "jammed")]


'''
edit("local_engine.py", "_jh_offline", [
    ('def _home_status(hass, h="sir"):\n', lambda o: OFFLINE61 + o),
    ('    if hass.states.async_all("lock"):  # Jarvis Home patch: only report locks that exist\n'
     '        parts.append("All locks secured." if not locks_ul else\n'
     '                     f"{locks_ul} lock{\'s\' if locks_ul != 1 else \'\'} unlocked.")\n',
     '    _jh_dark = _jh_offline(hass)\n'
     '    _jh_locks = hass.states.async_all("lock")  # Jarvis Home patch: only report locks that exist\n'
     '    if locks_ul:\n'
     '        parts.append(f"{locks_ul} lock{\'s\' if locks_ul != 1 else \'\'} unlocked.")\n'
     '    elif _jh_locks and all(s.state == "locked" for s in _jh_locks):\n'
     '        parts.append("All locks secured.")\n'),
    ('    parts.append(f"{people} person{\'s\' if people != 1 else \'\'} home.")\n    return " ".join(parts)\n',
     '    if _jh_dark:\n'
     '        parts.append(f"Not reporting: {\', \'.join(_jh_dark)}.")\n'
     '    parts.append(f"{people} person{\'s\' if people != 1 else \'\'} home.")\n    return " ".join(parts)\n'),
    ('            return f"Everything is closed and secured, {h}."\n',
     '            if _jh_offline(hass):\n'
     '                return f"Nothing reports open, {h}, but these are not reporting: {\', \'.join(_jh_offline(hass))}."\n'
     '            return f"Everything is closed and secured, {h}."\n'),
    ('        return f"Currently open or unlocked: {\', \'.join(items)}."\n',
     '        _jh_tail = f" Not reporting: {\', \'.join(_jh_offline(hass))}." if _jh_offline(hass) else ""\n'
     '        return f"Currently open or unlocked: {\', \'.join(items)}.{_jh_tail}"\n'),
], "61 local_engine: no all-clear from offline sensors or locks")

# 62 -----------------------------------------------------------------------------------------
edit("local_engine.py", "Jarvis Home patch: settings stay out", [
    ('                        for s in hass.states.async_all(d) if s.state == "on"]\n',
     '                        for s in hass.states.async_all(d)\n'
     '                        if s.state == "on" and not _jh_is_setting(hass, s.entity_id)]  # Jarvis Home patch: settings stay out\n'),
    ('            for s in hass.states.async_all(domain)]\n        if not entities:\n',
     '            for s in hass.states.async_all(domain) if not _jh_is_setting(hass, s.entity_id)]\n        if not entities:\n'),
    ('        for s in hass.states.async_all("light"):\n            if s.state == "on":\n                if await _seam_execute(hass, "light", "turn_off",\n',
     '        for s in hass.states.async_all("light"):\n            if s.state == "on" and not _jh_is_setting(hass, s.entity_id):\n                if await _seam_execute(hass, "light", "turn_off",\n'),
    ('                for state in hass.states.async_all(domain):\n                    fname = (state.attributes.get("friendly_name") or "").lower()\n                    if corrected in fname:\n',
     '                for state in hass.states.async_all(domain):\n                    if _jh_is_setting(hass, state.entity_id):\n                        continue\n                    fname = (state.attributes.get("friendly_name") or "").lower()\n                    if corrected in fname:\n'),
    ('                        if entry.domain != domain_hint:\n                            continue\n',
     '                        if entry.domain != domain_hint or _jh_is_setting(hass, entry.entity_id):\n                            continue\n'),
    ('                if is_excluded(hass, entry.entity_id):\n                    continue\n                state = hass.states.get(entry.entity_id)\n                if state:\n                    results.append(',
     '                if is_excluded(hass, entry.entity_id) or _jh_is_setting(hass, entry.entity_id):\n                    continue\n                state = hass.states.get(entry.entity_id)\n                if state:\n                    results.append('),
], "62 local_engine: settings are never targets on any command path")

# 63 -----------------------------------------------------------------------------------------
USERCOL = r'''_JH_USER_COLUMN = set()


def _jh_user_column(conn) -> None:
    """Jarvis Home patch (2026-11-06): each conversation row names the logged-in user, so a new chat is only
    seeded with that person's own turns. Rows from before this patch stay ownerless and are never seeded."""
    if str(DB_PATH) in _JH_USER_COLUMN:
        return
    if "user_id" not in {r[1] for r in conn.execute("PRAGMA table_info(conversations)")}:
        try:
            conn.execute("ALTER TABLE conversations ADD COLUMN user_id TEXT NOT NULL DEFAULT ''")
        except sqlite3.OperationalError as exc:
            if "duplicate column" not in str(exc).lower():
                raise
    conn.execute("CREATE INDEX IF NOT EXISTS idx_conv_user ON conversations(user_id)")
    _JH_USER_COLUMN.add(str(DB_PATH))


'''
THREAD = r'''def get_thread_messages(hours: int = 48, limit: int = 12, user_id: str = "", device_id: str = "") -> list[dict]:
    """Jarvis Home patch (2026-11-06): the newest turns of one person, oldest first. A logged-in user gets
    their own rows; with no login only ownerless rows of the same conversation or device. Never all rows."""
    if user_id:
        where, who = "user_id = ?", user_id
    elif device_id:
        where, who = "user_id = '' AND device_id = ?", device_id
    else:
        return []
    try:
        since = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=hours)).isoformat()
        with _connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM conversations WHERE timestamp > ? AND {where} "
                "AND device_id NOT IN ('briefing', 'sentinel', 'camera', 'summary') "
                "ORDER BY timestamp DESC, id DESC LIMIT ?",
                (since, who, limit),
            ).fetchall()
        return [dict(r) for r in reversed(rows)]
    except Exception as exc:
        _LOGGER.warning("JARVIS DB read error: %s", exc)
        return []


'''
edit("database.py", "_jh_user_column", [
    ('def _connect() -> sqlite3.Connection:\n', lambda o: USERCOL + o),
    ('        conn.executescript(SCHEMA)\n        conn.commit()\n', '        conn.executescript(SCHEMA)\n        _jh_user_column(conn)\n        conn.commit()\n'),
    ('def save_message(role: str, content: str, device_id: str = "unknown") -> None:\n',
     'def save_message(role: str, content: str, device_id: str = "unknown", user_id: str = "") -> None:\n'),
    ('                "INSERT INTO conversations (timestamp, device_id, role, content) VALUES (?,?,?,?)",\n'
     '                (datetime.now(timezone.utc).replace(tzinfo=None).isoformat(), device_id, role, content),\n',
     '                "INSERT INTO conversations (timestamp, device_id, role, content, user_id) VALUES (?,?,?,?,?)",\n'
     '                (datetime.now(timezone.utc).replace(tzinfo=None).isoformat(), device_id, role, content, user_id or ""),\n'),
    ('def get_recent_messages(\n', lambda o: THREAD + o),
], "63a database: conversations remember the logged-in user")
edit("memory_thread.py", "get_thread_messages", [
    ('async def load_recent(hass, hours: int = DEFAULT_HOURS, limit: int = DEFAULT_MAX) -> list:\n',
     'async def load_recent(hass, hours: int = DEFAULT_HOURS, limit: int = DEFAULT_MAX,\n'
     '                      user_id: str = "", device_id: str = "") -> list:\n'),
    ('        from .database import get_recent_messages\n        rows = await hass.async_add_executor_job(get_recent_messages, hours, None, limit)\n',
     '        from .database import get_thread_messages  # Jarvis Home patch: one person only\n'
     '        rows = await hass.async_add_executor_job(get_thread_messages, hours, limit, user_id, device_id)\n'),
], "63b memory_thread: a new chat is seeded with that person's turns only")
edit("conversation.py", "seed only this person", [
    ('    async def _maybe_seed_history(self, cid: str, history: list) -> None:\n',
     '    async def _maybe_seed_history(self, cid: str, history: list, user_id: str = "") -> None:\n'),
    ('        seeded = await memory_thread.load_recent(self.hass, hours, limit)\n',
     '        # Jarvis Home patch: seed only this person\'s turns, or this device\'s when nobody is logged in.\n'
     '        seeded = await memory_thread.load_recent(self.hass, hours, limit, user_id=user_id, device_id=cid)\n'),
    ('        await self._maybe_seed_history(cid, history)\n', '        await self._maybe_seed_history(cid, history, _jh_uid)\n'),
    ('        save_message("user", user_input.text, device_id=cid)\n', '        save_message("user", user_input.text, device_id=cid, user_id=_jh_uid)\n'),
    ('                    salvage = await try_local(\n                        self.hass, user_input.text, honorific, force=True\n                    )\n',
     '                    salvage = await try_local(\n                        self.hass, user_input.text, honorific, force=True, pending_key=cid\n                    )\n'),
    ('                            salvage = await try_local(\n                                self.hass, user_input.text, honorific, force=True\n                            )\n',
     '                            salvage = await try_local(\n                                self.hass, user_input.text, honorific, force=True, pending_key=cid\n                            )\n'),
    ('                        salvage = await try_local(\n                            self.hass, user_input.text, honorific, force=True\n                        )\n',
     '                        salvage = await try_local(\n                            self.hass, user_input.text, honorific, force=True, pending_key=cid\n                        )\n'),
    ('                    save_message, "assistant", response_text, cid,\n', '                    save_message, "assistant", response_text, cid, _jh_uid,\n'),
    ('            local_result = None if _direct else await try_local(\n                self.hass, user_input.text, honorific)\n',
     '            local_result = None if _direct else await try_local(\n                self.hass, user_input.text, honorific, pending_key=cid)\n'),
], "63c conversation: seed and save per person, pending question per conversation")

# 64 -----------------------------------------------------------------------------------------
_MEM_OLD_FTS = '            query_clean = " OR ".join(query.split()[:8])  # limit query terms\n'
edit("memory.py", "Jarvis Home patch: quoted", [
    ('    if _pgvector_available and user_id is None:\n        try:\n            from . import memory_pgvector\n            return memory_pgvector.search(query, k=k, hours=hours)\n',
     '    if _pgvector_available:\n        try:\n            from . import memory_pgvector\n            return memory_pgvector.search(query, k=k, hours=hours, user_id=user_id)\n'),
    ('    if _chromadb_available and _collection is not None and user_id is None:\n', '    if _chromadb_available and _collection is not None:\n'),
    ('                where = {"timestamp": {"$gte": cutoff}}\n\n',
     '                where = {"timestamp": {"$gte": cutoff}}\n'
     '            if user_id is not None:  # Jarvis Home patch: the backend itself keeps people apart\n'
     '                where = {"$and": [where, {"user_id": user_id}]} if where else {"user_id": user_id}\n\n'),
    (_MEM_OLD_FTS,
     '            # Jarvis Home patch: quoted words, so punctuation in a sentence is no FTS5 syntax error.\n'
     '            import re\n'
     '            words = re.findall(r"\\w+", query)[:8]  # limit query terms\n'
     '            if not words:\n'
     '                conn.close()\n'
     '                return []\n'
     '            query_clean = " OR ".join(\'"\' + w + \'"\' for w in words)\n'),
], "64a memory: per-user recall in every backend, punctuation safe")
edit("memory_pgvector.py", "user_id", [
    ('                    f"  embedding vector({dim})"\n                    ")")\n',
     '                    f"  embedding vector({dim})"\n                    ")")\n'
     '                cur.execute(f"ALTER TABLE {_TABLE} ADD COLUMN IF NOT EXISTS user_id text DEFAULT \'\'")\n'),
    ('                    "(id, content, role, device_id, conversation_id, ts, embedding) "\n                    "VALUES (%s, %s, %s, %s, %s, %s, %s::vector) "\n',
     '                    "(id, content, role, device_id, conversation_id, user_id, ts, embedding) "\n                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s::vector) "\n'),
    ('                     metadata.get("device_id", ""), metadata.get("conversation_id", ""),\n',
     '                     metadata.get("device_id", ""), metadata.get("conversation_id", ""), metadata.get("user_id", ""),\n'),
    ('def search(query: str, k: int = 5, hours: Optional[int] = None) -> list[dict]:\n',
     'def search(query: str, k: int = 5, hours: Optional[int] = None, user_id: Optional[str] = None) -> list[dict]:\n'),
    ('        where = ""\n        if hours:\n            cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)\n            where = "WHERE ts >= %s"\n            params.append(cutoff)\n',
     '        filters = []\n        if hours:\n            filters.append("ts >= %s")\n            params.append(datetime.now(timezone.utc) - timedelta(hours=hours))\n'
     '        if user_id is not None:  # Jarvis Home patch: None is the admin search, a string is one person\n'
     '            filters.append("COALESCE(user_id, \'\') = %s")\n            params.append(user_id)\n'
     '        where = ("WHERE " + " AND ".join(filters)) if filters else ""\n'),
], "64b memory_pgvector: rows carry the user, search filters on it")

# 65 -----------------------------------------------------------------------------------------
SUBJECT65 = r'''def subject_for_user(hass: HomeAssistant, user_id: str) -> str:
    """Jarvis Home patch (2026-11-06): the knowledge subject of a logged-in Home Assistant user, from the person
    entity linked to that login. A login with no person gets a private subject of its own, never a shared one.
    Empty when nobody is logged in, so only voice turns fall back to the presence guess."""
    if not user_id:
        return ""
    try:
        for state in hass.states.async_all("person"):
            if state.attributes.get("user_id") == user_id:
                return normalize(state.attributes.get("friendly_name") or state.entity_id.split(".", 1)[-1])
    except Exception as exc:
        _LOGGER.debug("identity: person lookup failed: %s", exc)
    return f"user_{user_id}"


'''
edit("identity.py", "subject_for_user", [
    ('def resolve_subject(hass: HomeAssistant, **kwargs) -> str:\n', lambda o: SUBJECT65 + o),
], "65a identity: logged-in user maps to their own subject")
edit("conversation.py", "subject_for_user", [
    ('            ident = identity.resolve(\n                self.hass, device_id=getattr(user_input, "device_id", None))\n            subjects = [identity.subject_for(ident), "household"]\n',
     '            # Jarvis Home patch: a login says who this is. Presence is only a guess for voice with no login.\n'
     '            _jh_subject = identity.subject_for_user(self.hass, _jh_uid)\n'
     '            if _jh_subject:\n'
     '                subjects = [_jh_subject, "household"]\n'
     '                _jh_user = await self.hass.auth.async_get_user(_jh_uid)\n'
     '                if _jh_user is not None and _jh_user.is_admin:\n'
     '                    subjects.append(identity.DEFAULT_PERSONAL_SUBJECT)  # older unattributed facts\n'
     '            else:\n'
     '                ident = identity.resolve(\n'
     '                    self.hass, device_id=getattr(user_input, "device_id", None))\n'
     '                subjects = [identity.subject_for(ident), "household"]\n'),
], "65b conversation: curated facts follow the logged-in user")

# 66 -----------------------------------------------------------------------------------------
GATE66 = r'''# Jarvis Home patch (2026-11-06): the login behind this request, shared with delegated sub-agents.
_JH_CALLER = _contextvars.ContextVar("jarvis_caller", default=None)
# What any logged-in household member may use: device control, questions, reminders. An allowlist, so a new tool
# starts as admin only. Home Assistant's own Assist tools keep their own rules. Voice with no login is not limited.
_JH_HOUSEHOLD_TOOLS = frozenset({
    "control_device", "bulk_control", "execute_plan", "run_scene_or_script",
    "get_entity_state", "search_entities", "get_area_devices", "get_home_summary",
    "remember", "cognitive_status", "connectivity_status", "review_suggestions", "root_cause",
    "schedule_followup", "manage_followups", "create_goal", "update_goal", "manage_goals",
    "web_research", "delegate_task", "calendar_agenda", "weather_forecast", "energy_status",
    "hazard_report", "activity_history", "who_do_you_see", "where_last_seen", "usual_times",
    "look_at_camera", "search_documents", "dismiss_intrusion", "acknowledge_alert"})


async def _jh_denied(hass, tool_name: str, user_input) -> Optional[str]:
    """Refuse an admin tool for a logged-in non-admin. Fails closed when the user cannot be read."""
    if tool_name not in _TOOL_MAP or tool_name in _JH_HOUSEHOLD_TOOLS:
        return None
    uid = _JH_CALLER.get() or getattr(getattr(user_input, "context", None), "user_id", None)
    if not uid:
        return None
    try:
        user = await hass.auth.async_get_user(uid)
    except Exception:
        user = None
    if user is not None and user.is_admin:
        return None
    _LOGGER.warning("JARVIS: blocked admin tool %s for a non-admin login", tool_name)
    return json.dumps({"error": "Not allowed: this needs an administrator login. Nothing was read or changed. "
                                "Tell the user so plainly."})


'''
edit("agent.py", "_jh_denied", [
    ('def _no_action_note(served_by: str, n_tools: int) -> str:\n', lambda o: GATE66 + o),
    ('    _blocked = _mail_guard(tool_name)\n    if _blocked:\n        return _blocked\n    # Custom JARVIS tools\n',
     '    _blocked = await _jh_denied(hass, tool_name, user_input) or _mail_guard(tool_name)\n    if _blocked:\n        return _blocked\n    # Custom JARVIS tools\n'),
    ('    if depth == 0:\n        _MAIL_TAINT.set(',
     '    if depth == 0:\n        _JH_CALLER.set(getattr(getattr(user_input, "context", None), "user_id", None))\n        _MAIL_TAINT.set('),
    ('    data = await hass.async_add_executor_job(_load_learned)\n    if key not in data:\n        data[key] = {}\n    data[key][name] = value\n    await hass.async_add_executor_job(_save_learned, data)\n',
     '    # Jarvis Home patch: the learned file goes into every prompt, so a logged-in person\'s preference stays in\n'
     '    # the knowledge store under their own subject.\n'
     '    if not (key == "preference" and _JH_CALLER.get()):\n'
     '        data = await hass.async_add_executor_job(_load_learned)\n'
     '        if key not in data:\n            data[key] = {}\n'
     '        data[key][name] = value\n'
     '        await hass.async_add_executor_job(_save_learned, data)\n'),
    ('                k_subject = identity.resolve_subject(hass)  # this person, or "primary"\n',
     '                k_subject = (identity.subject_for_user(hass, _JH_CALLER.get() or "")\n'
     '                             or identity.resolve_subject(hass))  # this person, or "primary"\n'),
], "66 agent: admin tools need an admin login, remember follows the login")

# 67 -----------------------------------------------------------------------------------------
edit("__init__.py", "_JH_OPEN_SERVICES", [
    ('    def _register_service(name: str, handler, schema=None) -> None:\n',
     '    # Jarvis Home patch (2026-11-06): the services any household login may call, the ones the panel and daily\n'
     '    # use need. An allowlist, so a new service starts as admin only. Automations carry no user and are not limited.\n'
     '    _JH_OPEN_SERVICES = frozenset({"analyze_camera", "briefing", "scene_by_intent", "routine", "add_reminder",\n'
     '                                   "check_packages", "nap", "shush", "unshush", "observer_status"})\n\n'
     '    def _register_service(name: str, handler, schema=None) -> None:\n'),
    ('        if schema is None:\n            hass.services.async_register(DOMAIN, name, handler)\n',
     '        if name not in _JH_OPEN_SERVICES:\n'
     '            from homeassistant.helpers.service import async_register_admin_service\n'
     '            async_register_admin_service(hass, DOMAIN, name, handler,\n'
     '                                         schema=vol.Schema({}, extra=vol.ALLOW_EXTRA) if schema is None else schema)\n'
     '            return\n'
     '        if schema is None:\n            hass.services.async_register(DOMAIN, name, handler)\n'),
], "67 __init__: jarvis services are admin only unless listed")

# 68 -----------------------------------------------------------------------------------------
PANEL68 = r'''        if not _is_admin(connection):
            # Jarvis Home patch (2026-11-06): the panel stays open to everyone, the owner's connection details do not.
            for _jh_key in ("imap_host", "imap_user", "llm_base_url", "custom_base_url", "ollama_base_url",
                            "searxng_url", "departure_osrm_url", "hazard_lat", "hazard_lon"):
                if _jh_key in result.get("config", {}):
                    result["config"][_jh_key] = ""
'''
KNOW68 = r'''        if not _is_admin(connection):
            # Jarvis Home patch (2026-11-06): a non-admin sees only their own facts and the household's.
            from . import identity
            mine = [identity.subject_for_user(hass, connection.user.id), knowledge.DEFAULT_SUBJECT]
            wanted = [msg["subject"]] if msg.get("subject") else mine
            allowed = [s for s in wanted if s in mine]
            facts = await hass.async_add_executor_job(lambda: knowledge.all_facts(subjects=allowed)) if allowed else []
            connection.send_result(msg["id"], {"facts": facts, "stats": {"total": len(facts), "by_kind": {}, "by_subject": {}}})
            return
'''
FACES68 = r'''        if not _is_admin(connection):
            # Jarvis Home patch (2026-11-06): faces and residents are for admins. An empty list keeps the panel calm.
            connection.send_result(msg["id"], {
                "recent": [], "residents": [], "references": [], "auto_references": [],
                "llm_face_recognition": False, "llm_face_autotrain": False, "recognition_source": "both"})
            return
'''
edit("websocket.py", "the owner's connection details do not", [
    ('        connection.send_result(msg["id"], result)\n    except Exception as exc:\n        _LOGGER.exception("ws_get_panel_data failed: %s", exc)\n',
     lambda o: PANEL68 + o),
    ('        subject = msg.get("subject")\n        facts = await hass.async_add_executor_job(lambda: knowledge.all_facts(subject=subject))\n',
     lambda o: KNOW68 + o),
    ('        results = await hass.async_add_executor_job(\n            lambda: search_memory(msg["query"], k=msg["k"])\n        )\n',
     '        # Jarvis Home patch: an admin searches everything, anyone else only their own memory.\n'
     '        _jh_k = max(1, min(int(msg["k"]), 50))\n'
     '        _jh_who = None if _is_admin(connection) else connection.user.id\n'
     '        results = await hass.async_add_executor_job(\n            lambda: search_memory(msg["query"], k=_jh_k, user_id=_jh_who)\n        )\n'),
    ('    """Return JARVIS internal debug log entries."""\n    connection.send_result(msg["id"], {"entries": list(_DEBUG_LOG)})\n',
     '    """Return JARVIS internal debug log entries. They hold what people said, so admins only; others get none."""\n'
     '    connection.send_result(msg["id"], {"entries": list(_DEBUG_LOG) if _is_admin(connection) else []})\n'),
    ('                      "remove_reference") and _deny_non_admin(connection, msg):\n            return\n',
     lambda o: o + FACES68),
    ('        recent = recognition.recent_faces(hass, int(msg.get("limit", 20)))\n',
     '        recent = await hass.async_add_executor_job(\n'
     '            recognition.inline_snapshots, recognition.recent_faces(hass, int(msg.get("limit", 20))))\n'),
    ('            await intrusion.async_load(hass)\n            connection.send_result(msg["id"], {\n                "events": intrusion.get_log(msg.get("limit", 50)),\n',
     '            await intrusion.async_load(hass)\n            connection.send_result(msg["id"], {\n                "events": intrusion.get_log(msg.get("limit", 50)) if _is_admin(connection) else [],\n'),
    ('        else:\n            connection.send_result(msg["id"], intrusion.status())\n',
     '        else:\n            _jh_status = intrusion.status()\n'
     '            if not _is_admin(connection):\n                _jh_status = {**_jh_status, "last_snapshot": None}\n'
     '            connection.send_result(msg["id"], _jh_status)\n'),
    ('    it is not admin-gated \u2014 the whole Faces panel sits behind Home Assistant\n    auth, and this only ever returns a photo the user themselves enrolled."""\n    try:\n',
     '    it is not admin-gated \u2014 the whole Faces panel sits behind Home Assistant\n    auth, and this only ever returns a photo the user themselves enrolled."""\n'
     '    if not _is_admin(connection):  # Jarvis Home patch: a resident\'s photo is not for every login\n'
     '        connection.send_result(msg["id"], {"image": None})\n        return\n    try:\n'),
], "68 websocket: per-person reads, admin-only logs, faces and connection details")

# 69 -----------------------------------------------------------------------------------------
# Faces: only the panel shows them, so the files move out of www and travel inline to admins.
# Intrusion stills stay in www because a phone push must fetch them with no login, under a name nobody can guess.
INLINE69 = r'''def inline_snapshots(rows: list) -> list:
    """Jarvis Home patch (2026-11-06): face photos are private files, sent to the panel as data URLs. Runs on
    the executor. A row with an old public URL or a missing file shows no photo."""
    import base64
    out = []
    for row in rows or []:
        row = dict(row)
        url = str(row.get("snapshot_url") or "")
        row["snapshot_url"] = None
        if url.startswith(FACE_SNAPSHOT_URL_BASE + "/"):
            try:
                with open(os.path.join(_face_snap_dir, os.path.basename(url)), "rb") as f:
                    row["snapshot_url"] = "data:image/jpeg;base64," + base64.b64encode(f.read()).decode("ascii")
            except OSError:
                pass
        out.append(row)
    return out


'''
edit("recognition.py", "inline_snapshots", [
    ('FACE_SNAPSHOT_URL_BASE = "/local/jarvis/faces"\n',
     '# Jarvis Home patch (2026-11-06): not under www, which is served without a login. This is a tag, not a web path.\n'
     'FACE_SNAPSHOT_URL_BASE = "jarvis-private/faces"\n'
     '_face_snap_dir = ""   # set at the first capture, from the Home Assistant config folder\n'),
    ('def _store_face_snapshot(path: str, data: bytes) -> None:\n', lambda o: INLINE69 + o),
    ('        snap_dir = config_path_str("www", "jarvis", "faces", hass=hass)\n',
     '        global _face_snap_dir\n        snap_dir = _face_snap_dir = config_path_str("jarvis", "face_snaps", hass=hass)\n'),
    ('        snap_dir = _cps("www", "jarvis", "faces", hass=hass)\n',
     '        global _face_snap_dir\n        snap_dir = _face_snap_dir = _cps("jarvis", "face_snaps", hass=hass)\n'),
    ('                if path and os.path.exists(path) and key not in _FACE_SNAPSHOTS:\n',
     '                if (path and os.path.exists(path) and key not in _FACE_SNAPSHOTS\n'
     '                        and str(snap.get("url", "")).startswith(FACE_SNAPSHOT_URL_BASE)):\n'
     '                    global _face_snap_dir\n'
     '                    _face_snap_dir = os.path.dirname(path)\n'),
], "69a recognition: face photos are private, not on the public web folder")
edit("intrusion.py", "token_urlsafe", [
    ('        fname = f"{tag}_{slug}_{ts}.jpg"\n',
     '        import secrets\n'
     '        # Jarvis Home patch (2026-11-06): www is served with no login, so the name must not be guessable.\n'
     '        fname = f"{tag}_{slug}_{ts}_{secrets.token_urlsafe(16)}.jpg"\n'),
], "69b intrusion: snapshot names cannot be guessed")

# 70 -----------------------------------------------------------------------------------------
TRUTH70 = r'''# Jarvis Home patch (2026-11-06, round 3): what a reply may claim depends on what the tools really did in this request.
# The rule set is small on purpose, after word lists for state claims kept replacing good answers:
#  - A device command (local_engine.device_command) needs a device tool that worked behind its reply. A reply is judged by its
#    first sentence: unless that is a question or states a failure it counts as a claim, in any wording, whatever follows it:
#    one retry, then the honest fixed line.
#  - Anything else is ordinary chat and is never retried or replaced, whatever it says. A false statement there is for the
#    persona and the live facts to prevent. This check does not catch it.
#  - Failures are kept per acting tool, so one success cannot hide another tool's failure. A failed read on a question
#    gets the honest line instead of an answer with nothing behind it.
#  - A tool waiting for confirmation has changed nothing, so a reply that says otherwise gets the honest waiting line.
_JH_ACT_TOOLS = frozenset({
    "control_device", "bulk_control", "execute_plan", "run_scene_or_script", "set_mode", "delegate_task",
    "remember", "ignore_entity", "unignore_entity", "manage_autonomy", "approve_suggestion", "dismiss_suggestion",
    "create_automation", "schedule_followup", "manage_followups", "create_goal", "update_goal", "manage_goals",
    "dismiss_intrusion", "acknowledge_alert", "ingest_documents"})
# Only these move a device, so only these can back a reply to a device command. Home Assistant's own tools count too (Hass*
# actions, and scripts it exposes as tools), except the ones that keep a timer or a list or only talk.
_JH_DEVICE_TOOLS = frozenset({"control_device", "bulk_control", "execute_plan", "run_scene_or_script"})
_JH_NOT_DEVICE_RE = _re.compile(r"timer|list|todo|shopping|calendar|broadcast|nevermind|respond")
# Filled by delegate_task: the state of each sub-agent run, so the parent knows what its tools really did.
_JH_SUB = _contextvars.ContextVar("jarvis_sub_agents", default=None)
# These look things up in the world, not in this house: an answer from memory after one failed is still an answer.
_JH_NOT_LIVE = frozenset({"web_research", "search_documents"})
_JH_TOOL_WORDS = {"control_device": "the device", "bulk_control": "the group command", "execute_plan": "the plan",
                  "run_scene_or_script": "the scene", "remember": "the note", "schedule_followup": "the follow-up",
                  "create_goal": "the goal", "update_goal": "the goal"}
_JH_CANNOT_RE = _re.compile(
    r"\b(?:no such|not found|cannot|can['’]?t|could not|couldn['’]?t|unable|not able|not allowed|no device"
    r"|does(?: not|n['’]?t) exist|(?:do not|don['’]?t) have|(?:did not|didn['’]?t) (?:work|go through|respond|change|confirm)"
    r"|not confirmed|unconfirmed|(?:has not|hasn['’]?t) (?:confirmed|responded)|failed|nothing was changed|unavailable|offline"
    r"|there(?: is|['’]s) no|i (?:see|find) no|niet gelukt|lukt niet|kan niet|kan geen|niet gevonden|mislukt|niet beschikbaar)\b", _re.I)
# Figures of speech with a failure word in them. They say nothing about the action.
_JH_IDIOM_RE = _re.compile(
    r"\b(?:cannot|can['’]?t) (?:wait|complain|be too|help but|thank)\b|\bthere(?: is|['’]s) no (?:need|rush|problem|trouble|doubt|hurry)\b"
    r"|\b(?:do not|don['’]?t) have to\b", _re.I)
# A reply that waits for the person. "confirmed" is left out on purpose: that word claims the opposite.
_JH_WAIT_RE = _re.compile(
    r"\b(?:confirm|confirmation|bevestig\w*|bevestiging|bestätig\w*|approval|approve|go[- ]ahead|permission|toestemming|waiting|awaiting|wacht\w*"
    r"|say yes|say the word)\b", _re.I)
_JH_ALREADY_RE = _re.compile(r"\b(?:already|al|reeds|schon|bereits)\b", _re.I)
_JH_TOOLS_NUDGE = ("A tool you called in this request changed nothing or reported a failure, so do not say it is done. "
                   "Call the right tool now if one can do it, or say plainly what did not happen and why.")


async def _jh_turn(hass, user_input) -> tuple:
    """(is this a device command, is this a question), from the words the person really used."""
    try:
        from .local_engine import _load_learned_aliases, device_command, is_question
        text = getattr(user_input, "text", "") or ""
        aliases = await hass.async_add_executor_job(_load_learned_aliases, hass)
        return device_command(hass, text, aliases), is_question(text)
    except Exception as exc:
        _LOGGER.debug("JARVIS: could not sort this turn: %s", exc)
        return False, False


def _jh_new_state() -> dict:
    """changed: any acting tool worked. device: a device tool worked. waiting: what is waiting for confirmation.
    step: which round of tool calls this is, because a call in the same round cannot be a correction of its neighbour."""
    st = {"acted": False, "changed": False, "device": False, "waiting": [], "read": False, "read_failed": False, "bad": {}, "step": 0}
    box = _JH_SUB.get()
    if box is not None:
        box.append(st)
    return st


def _jh_sub_report(box: list) -> dict:
    """What the tools of a delegated run really did, for the parent's check."""
    return {"changed": any(s["changed"] for s in box), "device": any(s["device"] for s in box),
            "waiting": [w for s in box for w in s["waiting"]], "bad": {k: b for s in box for k, b in s["bad"].items()}}


def _jh_label(name: str, args, data, hass) -> str:
    """What to call this action in a reply: the device's own name when there is one."""
    args = args if isinstance(args, dict) else {}
    data = data if isinstance(data, dict) else {}
    eid = args.get("entity_id") or data.get("entity_id")
    if isinstance(eid, str) and eid:
        st = hass.states.get(eid) if hass is not None else None
        return (st.attributes.get("friendly_name") if st else None) or eid
    if isinstance(args.get("name"), str) and args["name"]:
        return args["name"]
    return _JH_TOOL_WORDS.get(name) or "the " + name.replace("_", " ")


def _jh_target(args) -> str:
    eid = args.get("entity_id") if isinstance(args, dict) else None
    return eid if isinstance(eid, str) else ""


def _jh_corrects(bad: dict, name: str, args, step: int) -> bool:
    """Does this success, in a later round, put right that wrong id or tool name? Same tool and action, and the same kind of
    device or the same device under another domain. The fan is not put right by a lamp that also had to go on."""
    if not bad["soft"] or bad["step"] >= step:
        return False
    if bad["tool"] is None:
        return True   # a tool name that does not exist
    action = args.get("action") if isinstance(args, dict) else None
    if bad["tool"] != name or bad["action"] != action:
        return False
    old, new = bad["target"], _jh_target(args)
    return not old or not new or old.split(".")[0] == new.split(".")[0] or old.split(".")[-1] == new.split(".")[-1]


def _jh_note_tool(st: dict, name: str, result, args=None, hass=None) -> None:
    """Record what one finished tool call really means, per tool. Only a tool that can change something and did not
    report a failure counts as changed. Hass* tools are Home Assistant's own actions, Get* its reads."""
    st["acted"] = True
    low = name.lower()
    acting = name in _JH_ACT_TOOLS or (name not in _TOOL_MAP and not low.startswith("get")
                                       and not low.startswith("hassget") and "_get_" not in low)
    device = name in _JH_DEVICE_TOOLS or (acting and name not in _TOOL_MAP and not _JH_NOT_DEVICE_RE.search(low))
    raw = result if isinstance(result, str) else str(result)
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        data = None
    label = _jh_label(name, args, data, hass)
    key = f"{name}:{label}"

    def bad(kind, what=label, where=key, soft=False, tool=name):
        st["bad"][where] = {"what": what, "kind": kind, "soft": soft, "tool": tool, "step": st["step"], "target": _jh_target(args),
                            "action": args.get("action") if isinstance(args, dict) else None}

    failed = False
    if isinstance(data, dict):
        sub = data.get("jh_tools")
        if name == "delegate_task" and isinstance(sub, dict):
            # A delegate is worth what its own tools did: a read-only run backs nothing.
            st["changed"] = st["changed"] or bool(sub.get("changed"))
            st["device"] = st["device"] or bool(sub.get("device"))
            st["waiting"] += sub.get("waiting") or []
            st["bad"].update(sub.get("bad") or {})
            return
        status = str(data.get("status", "")).lower()
        if status in ("awaiting_confirmation", "needs_confirmation") or data.get("needs_confirmation"):
            st["waiting"].append(label)
            return
        if data.get("confirmed") is False and data.get("sent"):
            bad("unconfirmed")
            return
        intent = data.get("data") if isinstance(data.get("data"), dict) else {}
        if name == "execute_plan" and data.get("failed"):
            steps = [r for r in data.get("results") or [] if isinstance(r, dict) and not r.get("ok")]
            for r in steps or [{}]:
                bad("failed", str(r.get("description") or label)[:60], f"{key}:{r.get('step')}")
            st["changed"] = st["changed"] or bool(data.get("succeeded"))
            st["device"] = st["device"] or bool(data.get("succeeded"))
            return
        if name == "bulk_control" and data.get("count") and data.get("count") < (data.get("total") or 0):
            bad("failed", f"{data['total'] - data['count']} of the {data['total']} devices")
            st["changed"] = st["device"] = True
            return
        if intent.get("failed") and intent.get("success"):
            # Home Assistant fills both lists when only some targets failed.
            gone = [str(t.get("name") or t.get("id") or "a device") if isinstance(t, dict) else str(t) for t in intent["failed"]]
            bad("failed", _jh_join(gone))
            st["changed"] = True
            st["device"] = st["device"] or device
            return
        failed = bool(data.get("error") or data.get("success") is False or data.get("response_type") == "error"
                      or (name == "bulk_control" and not data.get("count"))
                      or (name == "execute_plan" and not data.get("succeeded"))
                      or bool(intent.get("failed")))
    else:
        failed = raw.strip().lower().startswith(("error", "failed", "blocked", "unknown tool", "couldn't", "could not"))
    if failed:
        if acting:
            # A wrong id or tool name stays a failure until a later call puts that same request right.
            low_raw = raw.lower()
            unknown = "unknown tool" in low_raw
            bad("failed", soft=unknown or "not found" in low_raw, tool=None if unknown else name)
        elif name not in _JH_NOT_LIVE:
            st["read_failed"] = True
    elif acting:
        st["changed"] = True
        st["device"] = st["device"] or device
        st["bad"].pop(key, None)
        for where, was in list(st["bad"].items()):
            if _jh_corrects(was, name, args, st["step"]):
                del st["bad"][where]
    else:
        st["read"] = True
        st["read_failed"] = False


def _jh_failures(st: dict) -> list:
    return list(st["bad"].values())


def _jh_join(words: list) -> str:
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]


def _jh_what_failed(st: dict, honorific: str) -> str:
    """The line that names what did not work."""
    gone = [b["what"] for b in _jh_failures(st) if b["kind"] == "failed"]
    silent = [b["what"] for b in _jh_failures(st) if b["kind"] == "unconfirmed"]
    parts = ([f"{_jh_join(gone)} did not go through"] if gone else []) + ([f"{_jh_join(silent)} has not confirmed it"] if silent else [])
    line = " and ".join(parts)
    return f"{line[:1].upper()}{line[1:]}, {honorific}."


def _jh_first(text: str) -> str:
    """The opening sentence: what a reply says first is what it says. A closing question or remark excuses nothing."""
    return _re.split(r"(?<=[.!?;])\s+|\n+|\s+[-–—]+\s+", (text or "").strip(), maxsplit=1)[0].strip()


def _jh_problem(text: str, st: dict, cmd: bool, asked: bool = False) -> Optional[str]:
    """What is wrong with this reply: "failed", "waiting", "read", "empty", or None when it can stand."""
    first = _jh_first(text)
    if first.endswith("?") or _JH_CANNOT_RE.search(_JH_IDIOM_RE.sub(" ", first)):
        return None   # it asks, or it already says what went wrong
    if _jh_failures(st):
        return "failed"
    if st["waiting"]:
        return None if _JH_WAIT_RE.search(first) else "waiting"
    if asked and st["read_failed"]:
        return "read"
    if cmd and not st["device"] and not (st["read"] and _JH_ALREADY_RE.search(first)):
        return "empty"
    return None


def _jh_nudge(text: str, st: dict, cmd: bool) -> Optional[str]:
    """The message that sends a reply to a device command back once, or None. Ordinary chat is never sent back."""
    if not cmd or _jh_problem(text, st, cmd) not in ("failed", "empty"):
        return None
    return _JH_TOOLS_NUDGE if st["acted"] else _PROMISE_NUDGE


def _jh_final(text: str, st: dict, cmd: bool, honorific: str, asked: bool = False) -> str:
    """Last check, when no retry is left."""
    problem = _jh_problem(text, st, cmd, asked)
    if problem is None:
        return text
    if problem == "read":
        return f"I could not read that, {honorific}."
    waits = _jh_join(list(dict.fromkeys(st["waiting"]))) if st["waiting"] else ""
    waits = f"{waits[:1].upper()}{waits[1:]} is waiting for your confirmation, {honorific}." if waits else ""
    if problem == "waiting":
        return f"Only part of that went through, {honorific}. {waits}" if st["changed"] else f"{waits} Nothing has been changed yet."
    if problem == "failed":
        note = f"{_jh_what_failed(st, honorific)} {waits}".rstrip()
        if not cmd:
            return f"{text.rstrip()} {note}"   # the reply stands, what failed is added to it
        if st["changed"] or waits:
            return f"Only part of that went through, {honorific}. {note}"
        kinds = {b["kind"] for b in _jh_failures(st)}
        if kinds == {"unconfirmed"}:
            return f"The command was sent, {honorific}, but the device has not confirmed it."
        if kinds != {"failed"}:
            return note
    elif st["changed"]:
        return f"That did not reach any device, {honorific}."   # something else ran, a reminder for one
    return f"That did not go through, {honorific}. Nothing was changed."


'''
LOOP70 = r'''            _jh_hon = (config or {}).get("honorific", "sir")
            if _strict and not _nudged and tools and text:
                _jh_back = _jh_nudge(text, _jh_st, _cmd)
                if _jh_back:
                    _nudged = True
                    _LOGGER.warning("JARVIS: reply to a device command not backed by a tool result, sent back once")
                    working.append({"role": "assistant", "content": text})
                    working.append({"role": "user", "content": _jh_back})
                    continue
            if _strict and text:
                text = _jh_final(text, _jh_st, _cmd, _jh_hon, _asked)
'''
TRIM70 = r'''_JH_OFFER_RE = _re.compile(r"^\W*(?:(?:and|also|so)\W+)?(?:sir\W+)?(?:how (?:may|can) i|what (?:else )?can i|would you like|shall i|do you want me|let me know|anything else|is there anything|feel free)", _re.I)


def _jh_trim_offer(text: str, keep: bool = False) -> str:
    """Jarvis Home patch: drop closing sentences that are only an offer of help, and keep everything else as
    written. keep=True when a tool is waiting for confirmation, because then the closing question is the point."""
    text = (text or "").strip()
    while not keep:
        last = None
        for last in _re.finditer(r"(?<=[.!?])\s+", text):
            pass
        if last is None or not _JH_OFFER_RE.search(text[last.end():]):
            break
        text = text[:last.start()]
    return text


'''
REACHED70 = r'''# Jarvis Home patch (2026-11-06): these devices have their own words for on, such as heat, idle or playing.
_JH_OFF_WORDS = {"climate": ("off",), "water_heater": ("off",), "humidifier": ("off",), "media_player": ("off", "standby")}
# Only these really show on or off. Scenes, scripts, buttons, vacuums, valves, mowers and the like have other states or none,
# so a command to them cannot be checked this way: it counts as landed, as long as the device is there at all.
_JH_ON_OFF = frozenset({"light", "switch", "fan", "input_boolean", "siren", "remote", "automation", "group"})


def _jh_reached(entity_id: str, expected, state) -> bool:
    domain = entity_id.split(".")[0]
    off = _JH_OFF_WORDS.get(domain)
    value = str(state).lower()
    if off and tuple(expected) == ("on",):
        return value not in off + ("unavailable", "unknown")
    if off and tuple(expected) == ("off",):
        return value in off
    if tuple(expected) in (("on",), ("off",)) and (domain not in _JH_ON_OFF or (domain == "group" and value not in ("on", "off"))):
        return value != "unavailable"   # a group of covers or locks shows open, closed or locked instead
    return value in expected


'''
CONFIRM70 = r'''        def _jh_landed(st):
            return not _jh_want or (bool(st) and (_jh_reached(entity_id, _jh_want, st.state)
                                                  or str(st.state).lower() in _TRANSITIONAL))
        for _ in range(16):
            if _jh_landed(hass.states.get(entity_id)):
                break
            await asyncio.sleep(0.25)
        new_state = hass.states.get(entity_id)
        _jh_ok = _jh_landed(new_state)
        _jh_out = {
            "success": _jh_ok,
            "entity_id": entity_id,
            "previous_state": prev_state,
            "new_state": new_state.state if new_state else "unknown",
            "confirmed": _jh_ok,
            "action": action,
            "area": wm_area,
        }
        if not _jh_ok:
            _jh_out["sent"] = True
            _jh_out["warning"] = ("The command was sent but the device did not report the expected state. "
                                  "Tell the user it is not confirmed. Do not say it is done.")
        return json.dumps(_jh_out)
'''
edit("agent.py", "_jh_note_tool", [
    ('def _no_action_note(served_by: str, n_tools: int) -> str:\n', lambda o: TRUTH70 + o),
    ('    _changed = False       # Jarvis Home patch: a tool that can change something really ran\n',
     '    _jh_st = _jh_new_state()  # Jarvis Home patch: what the tools of this request really did\n'),
    (between("agent.py", '            if _strict and not _nudged and tools and text and ((_cmd and not _changed)', '            _has_text = bool(text and text.strip())\n'), LOOP70),
    ('            _changed = _changed or call["name"] not in _MAIL_SAFE_TOOLS\n', '            _jh_note_tool(_jh_st, call["name"], result_str, call.get("args"), hass)\n'),
    ('        # Execute tools\n        for call in tool_calls:\n            if call["name"] == "delegate_task":\n',
     lambda o: '        _jh_st["step"] += 1\n' + o),
    # a delegate reports what its own tools did, so the parent's check does not have to take its word for it
    ('        with _ctx:\n            result = await run_agent(\n                hass,\n                messages=[{"role": "user", "content": objective}],\n',
     lambda o: '        _jh_box = []\n        _jh_tok = _JH_SUB.set(_jh_box)\n' + o),
    ('        out = {"objective": objective, "result": result}\n',
     '        _JH_SUB.reset(_jh_tok)\n        out = {"objective": objective, "result": result, "jh_tools": _jh_sub_report(_jh_box)}\n'),
    ('            return _jh_trim_offer(text) if _has_text else _empty_reply_fallback(config)\n',
     '            return _jh_trim_offer(text, _jh_st["waiting"]) if _has_text else _empty_reply_fallback(config)\n'),
    ('        _final = (result.get("text", "") or "").strip()\n',
     '        _final = (result.get("text", "") or "").strip()\n'
     '        if _strict and _final:\n'
     '            _final = _jh_final(_final, _jh_st, _cmd, (config or {}).get("honorific", "sir"), _asked)\n'),
    ('    except Exception:\n        try:\n            from . import persona\n            hon = (config.get("honorific", "sir") if isinstance(config, dict) else "sir")\n            return persona.completed(hon)\n        except Exception:\n            return "I\'ve completed the requested actions, sir."\n',
     '    except Exception:\n        hon = (config.get("honorific", "sir") if isinstance(config, dict) else "sir")\n'
     '        if _jh_st["changed"]:\n            return f"Part of that ran, {hon}, but I could not confirm all of it."\n'
     '        return f"That did not go through, {hon}. Nothing was changed."\n'),
    ('    acted = {"v": False}\n', '    acted = {"v": False}\n    _jh_st = _jh_new_state()  # Jarvis Home patch: same checks as the native loop\n'),
    ('            acted["v"] = True\n            return await _execute_tool(hass, tool_name, args, hass_api, user_input)\n',
     '            acted["v"] = True\n            _jh_st["step"] += 1   # this protocol runs one tool per round\n'
     '            _jh_res = await _execute_tool(hass, tool_name, args, hass_api, user_input)\n'
     '            _jh_note_tool(_jh_st, tool_name, _jh_res, args, hass)\n            return _jh_res\n'),
    ('    text = (res.get("text") or "").strip()\n    _emit_conversational_shadow(acted=acted["v"], answered=bool(text))\n',
     '    text = (res.get("text") or "").strip()\n'
     '    if text and _jh_opt(hass, "jh_strict_actions"):\n'
     '        _cmd, _asked = await _jh_turn(hass, user_input)\n'
     '        text = _jh_final(text, _jh_st, _cmd, (config or {}).get("honorific", "sir"), _asked)\n'
     '    _emit_conversational_shadow(acted=acted["v"], answered=bool(text))\n'),
    ('    return text if text else _empty_reply_fallback(config)\n', '    return _jh_trim_offer(text, _jh_st["waiting"]) if text else _empty_reply_fallback(config)\n'),
    (between("agent.py", '_JH_OFFER_RE = _re.compile(', '# Jarvis Home patch (2026-11-06): the login behind this request'), TRIM70),
    ('_EXPECTED_STATES = {\n', lambda o: REACHED70 + o),
    ('    return s in expected\n', '    return _jh_reached(entity_id, expected, s)\n'),
    (between("agent.py", '        for _ in range(16):\n            new_state = hass.states.get(entity_id)\n            if not _jh_want or', '    except Exception as exc:\n        return json.dumps({"error": f"Failed: {exc}", "entity_id": entity_id})\n\n\nasync def _exec_get_entity_state'), CONFIRM70),
    ('"from the home context or from get_entities results."\n',
     '"from the home context or from get_entities results. If the result "\n                "has a warning, tell the user the action is not confirmed."\n'),
], "70 agent: replies are checked against what the tools really did")

# 71 -----------------------------------------------------------------------------------------
LIVE71 = r'''def _live_facts(hass) -> str:
    from .local_engine import _jh_heaters, _jh_is_setting
    lights = [s for s in hass.states.async_all("light") if not _jh_is_setting(hass, s.entity_id)]
    on = [s.attributes.get("friendly_name", s.entity_id) for s in lights if s.state == "on"]
    out = [f"Exact facts, use these numbers: {len(lights)} lights in total, {len(on)} on" + (f" ({', '.join(on)})." if on else ".")]
    switches, climates = _jh_heaters(hass)
    heat = [f"{s.attributes.get('friendly_name', s.entity_id)}: {s.state}" for s in switches]
    for s in climates:
        action = s.attributes.get("hvac_action")
        detail = (f"{s.state}, {action}" if action else
                  "powered on, heat level unknown, it may not be producing heat" if s.state in ("heat", "heat_cool", "auto") else s.state)
        heat.append(f"{s.attributes.get('friendly_name', s.entity_id)}: {detail}")
    if heat:
        out.append("Heaters: " + "; ".join(heat) + ".")
    return " ".join(out)


'''
FACTS71 = r'''def _facts_home(hass, people: bool = False, energy: bool = True) -> list[str]:
    """Jarvis Home patch: real facts for spoken briefings, straight from HA states. Heaters come from
    local_engine._jh_heaters, and a heater line is only spoken when heater data exists."""
    def name(s):
        return s.attributes.get("friendly_name", s.entity_id)
    lines = []
    if people:
        home = [name(s) for s in hass.states.async_all("person") if s.state == "home"]
        if home:
            lines.append(f"At home: {', '.join(home)}.")
    if not energy:
        return lines
    from .local_engine import _jh_heaters
    switches, climates = _jh_heaters(hass)
    on = [name(s) for s in switches if s.state == "on"]
    for s in climates:
        action = s.attributes.get("hvac_action")
        if action == "heating":
            on.append(name(s))
        elif not action and s.state in ("heat", "heat_cool", "auto"):
            on.append(name(s) + " (powered on, heat level unknown)")
    dark = [name(s) for s in switches + climates if s.state in ("unavailable", "unknown")]
    if on:
        lines.append(f"Heaters on: {', '.join(on)}.")
    elif (switches or climates) and not dark:
        lines.append("No heaters are on.")
    if dark:
        lines.append(f"Not reporting: {', '.join(dark)}.")
    used = []
    for s in hass.states.async_all("sensor"):
        scale = {"Wh": 0.001, "kWh": 1.0, "MWh": 1000.0}.get(s.attributes.get("unit_of_measurement"))
        label = f"{s.entity_id} {name(s)}".lower()
        if scale is None or not (s.attributes.get("meter_period") == "daily" or "today" in label or "daily" in label):
            continue
        try:
            kwh = float(s.state) * scale
        except (TypeError, ValueError):
            continue
        if kwh >= 0.1:
            used.append(f"{name(s).replace(' energy today', '').replace(' Energy today', '')} {kwh:.1f} kilowatt hours")
    if used:
        lines.append(f"Energy today: {', '.join(used)}.")
    return lines


'''
edit("agent.py", "from .local_engine import _jh_heaters", [
    (between("agent.py", 'def _live_facts(hass) -> str:\n', '_JH_OFFER_RE = _re.compile('), LIVE71),
], "71a agent: exact facts use the shared heater and setting rules")
edit("briefing.py", "energy: bool = True", [
    (between("briefing.py", 'def _facts_home(hass, people: bool = False) -> list[str]:\n', 'def _plain_briefing(greeting'), FACTS71),
    ('    # Camera detection summary from proactive briefing system\n',
     '    _jh_facts_only = _jh_facts_only_on(hass)\n\n'
     '    # Camera detection summary from proactive briefing system\n'),
    ('        if snap_summary:\n            pass  # Jarvis Home patch: camera summaries are AI text, not read aloud\n',
     '        if snap_summary and not _jh_facts_only:  # Jarvis Home patch: AI text, never read aloud as fact\n            context_lines.append(snap_summary)\n'),
    ('    context = "\\n".join(context_lines)\n\n    # Ask Groq to compose the briefing\n',
     '    context_lines.extend(_facts_home(hass, energy=include_energy))\n'
     '    context = "\\n".join(context_lines)\n\n    # Ask Groq to compose the briefing\n'),
    ('    context_lines.extend(_facts_home(hass))\n    if _jh_facts_only_on(hass):\n',
     '    if _jh_facts_only:\n'),
    ('reading gathered facts instead")\n        briefing_text = _plain_briefing(greeting, honorific, context_lines)\n',
     'reading gathered facts instead")\n        briefing_text = _speakable(_plain_briefing(greeting, honorific, context_lines))\n'),
], "71b briefing: dynamic heaters and energy, include flags and the facts switch honoured")
COUNTS71 = r'''def get_snapshot_counts(hours: float = 12) -> str:
    """Jarvis Home patch (2026-11-06): camera detections as plain counts, without the AI description."""
    by_camera: dict = {}
    for s in get_recent_snapshots(hours):
        by_camera[s.camera_name] = by_camera.get(s.camera_name, 0) + 1
    if not by_camera:
        return ""
    return "Camera detections: " + ", ".join(f"{cam} {n}" for cam, n in by_camera.items()) + "."


'''
edit("proactive_briefing.py", "get_snapshot_counts", [
    ('def get_snapshot_summary(hours: float = 12) -> str:\n', lambda o: COUNTS71 + o),
    ('        extra_context = (\n            "Multiple security events detected in a short period. "\n            "Summarize what happened and any concerns."\n        )\n    elif reason == "scheduled":\n        extra_context = "This is a scheduled briefing."\n',
     '        # Jarvis Home patch: these lines are read aloud as they stand, so they state facts only.\n'
     '        extra_context = f"Multiple security events were detected in a short period. {get_snapshot_counts(hours=4)}".strip()\n'),
], "71c proactive: the security briefing states facts, no instructions read aloud")

# 72 -----------------------------------------------------------------------------------------
edit("conversation.py", "Jarvis Home patch: no words in the log", [
    ('        _LOGGER.warning(\n            "JARVIS async_process ENTRY: text=\'%s\' device_id=\'%s\'",\n            user_input.text[:60], device_id,\n        )\n',
     '        # Jarvis Home patch: no words in the log. People paste logs into bug reports, and jarvis_log above has them for admins.\n'
     '        _LOGGER.debug("JARVIS async_process ENTRY: %d characters, device_id=\'%s\'", len(user_input.text), device_id)\n'),
], "72a conversation: chat text stays out of the Home Assistant log")
edit("websocket.py", "only their own routines", [
    ('        routines = await hass.async_add_executor_job(_get_person_routines)\n',
     '        routines = await hass.async_add_executor_job(_get_person_routines)\n'
     '        if not _is_admin(connection):  # Jarvis Home patch: a non-admin sees only their own routines\n'
     '            from . import identity\n'
     '            mine = identity.subject_for_user(hass, connection.user.id)\n'
     '            routines = {k: v for k, v in routines.items() if identity.normalize(k) == mine}\n'),
    ('        img = await cam._get_best_image(hass, entity_id)\n        if not img:\n            _snap_log(entity_id,\n',
     '        img = await cam._get_best_image(hass, entity_id, poll=True)\n        if not img:\n            _snap_log(entity_id,\n'),
], "72b websocket: routines per person, panel snapshots are marked as polls")
BLANK72 = r'''# Jarvis Home patch (2026-11-06): when a camera stayed blank after a stream wake. A panel poll then skips the wake
# for a minute, and the warning is logged once per blank spell. Event captures always try the wake.
import time as _jh_time
_JH_BLANK: dict = {}
_JH_BLANK_HOLD = 60.0


'''
edit("camera.py", "_JH_BLANK", [
    ('async def _get_best_image(hass: HomeAssistant, entity_id: str) -> Optional[bytes]:\n',
     lambda o: BLANK72 + 'async def _get_best_image(hass: HomeAssistant, entity_id: str, poll: bool = False) -> Optional[bytes]:\n'),
    ('    good = _usable(image, min_size=SMALL_SUSPECT_SIZE) if image else None\n    if good:\n        return good\n    tiny_first = _usable(image) if image else None   # small-but-nonblank fallback\n',
     '    good = _usable(image, min_size=SMALL_SUSPECT_SIZE) if image else None\n    if good:\n        _JH_BLANK.pop(entity_id, None)\n        return good\n    tiny_first = _usable(image) if image else None   # small-but-nonblank fallback\n'
     '    if poll and _jh_time.monotonic() - _JH_BLANK.get(entity_id, -_JH_BLANK_HOLD) < _JH_BLANK_HOLD:\n        return tiny_first\n'),
    ('        if good and (not tiny_first or len(good) > len(tiny_first)):\n            return good\n',
     '        if good and (not tiny_first or len(good) > len(tiny_first)):\n            _JH_BLANK.pop(entity_id, None)\n            return good\n'),
    ('        _LOGGER.warning(\n            "JARVIS: %s still blank/empty after stream wake (%d bytes) — "\n',
     '        _jh_log = _LOGGER.debug if entity_id in _JH_BLANK else _LOGGER.warning\n'
     '        _JH_BLANK[entity_id] = _jh_time.monotonic()\n'
     '        _jh_log(\n            "JARVIS: %s still blank/empty after stream wake (%d bytes) — "\n'),
], "72c camera: a blank camera is not woken on every panel poll, and warns once")

# 73 -----------------------------------------------------------------------------------------
CHAT_SYNC = r"""  // Jarvis Home patch (2026-11-06): history follows the login on every device. A save reads what is stored first and merges
  // by message id, so a phone and a PC cannot erase each other. Deleted ids travel in "removed". Nothing is written unless
  // that read worked, so a failed load cannot wipe the history.
  _chatTag() {
    this._chatMessages.forEach((m) => {
      m.t = m.t || Date.now();
      m.id = m.id || m.t.toString(36) + Math.random().toString(36).slice(2, 8);
    });
  }

  _chatMerge(stored) {
    this._chatTag();
    const gone = new Set(Array.isArray(stored?.removed) ? stored.removed : []);
    this._chatRemoved.forEach((id) => gone.add(id));
    const byId = new Map();
    let last = 0;
    (Array.isArray(stored?.messages) ? stored.messages : []).forEach((m, i) => {
      if (!m || typeof m.text !== "string") return;
      const id = m.id || "old" + i + "-" + m.role + "-" + m.text.length;   // saved before ids existed: the same on every device
      last = m.t || last + 1;   // no time of its own: it stays right after the message before it
      byId.set(id, { ...m, id, t: last });
    });
    if (this._chatWipe) byId.forEach((m, id) => gone.add(id));
    this._chatMessages.forEach((m) => byId.set(m.id, m));
    return {
      messages: [...byId.values()].filter((m) => !gone.has(m.id)).sort((a, b) => a.t - b.t).slice(-200),
      removed: [...gone].slice(-1000),
    };
  }

  _chatApply(list) {
    const same = list.length === this._chatMessages.length && list.every((m, i) => m === this._chatMessages[i]);
    this._chatMessages = list;
    if (!same) this._refreshChat();
  }

  async _loadChat() {
    if (this._chatLoaded || this._chatLoading || !this._hass) return;
    this._chatLoading = true;
    try {
      const res = await this._hass.callWS({ type: "frontend/get_user_data", key: "jarvis_chat" });
      this._chatApply(this._chatMerge(res?.value).messages);
      this._chatLoaded = true;
    } catch (err) { console.warn("JARVIS chat: history not loaded", err); }
    this._chatLoading = false;
  }

  // Home Assistant has no compare-and-set, so two devices saving at the same moment can overwrite each other. A moment after a
  // write, read it back and put back what went missing. This narrows the window; it cannot close it.
  async _chatRecheck(value) {
    try {
      await new Promise((r) => setTimeout(r, 500));
      const res = await this._hass.callWS({ type: "frontend/get_user_data", key: "jarvis_chat" });
      const have = new Set((res?.value?.messages || []).map((m) => m?.id));
      if (value.messages.every((m) => have.has(m.id))) return;
      const again = this._chatMerge(res?.value);
      this._chatApply(again.messages);
      await this._hass.callWS({ type: "frontend/set_user_data", key: "jarvis_chat", value: again });
    } catch (err) { console.warn("JARVIS chat: history not checked", err); }
  }

  _saveChat() {
    if (!this._hass) return Promise.resolve();
    const run = async () => {
      let wipe = false;
      try {
        const res = await this._hass.callWS({ type: "frontend/get_user_data", key: "jarvis_chat" });
        const value = this._chatMerge(res?.value);
        wipe = this._chatWipe;
        this._chatWipe = false;
        this._chatApply(value.messages);
        this._chatLoaded = true;
        await this._hass.callWS({ type: "frontend/set_user_data", key: "jarvis_chat", value });
        await this._chatRecheck(value);
      } catch (err) {
        this._chatWipe = this._chatWipe || wipe;   // the next save tries again with everything still in memory
        console.warn("JARVIS chat: history not saved", err);
      }
    };
    this._chatSaving = (this._chatSaving || Promise.resolve()).then(run);
    return this._chatSaving;
  }

"""
MIC_JS = r"""  // Jarvis Home patch (2026-11-06): voice in the Chat tab, through Home Assistant's own Assist pipeline. One recording at a
  // time, and the microphone is released on every path. Audio is kept from the first moment and sent when HA is ready, so the
  // first word is not lost. While the button is held a pause does not end the recording: the release does.
  async _toggleMic() {
    if (this._micStop) { this._micStop(); return; }
    if (this._micStarting) return;
    const conn = this._hass?.connection;
    const fail = (t) => { this._chatMessages.push({ role: "error", text: t }); this._refreshChat(); };
    if (!conn) return;
    if (!navigator.mediaDevices?.getUserMedia) { fail("The microphone needs the https address of this page."); return; }
    this._micStarting = true;
    let stream, ctx, src, proc, hid = null, done = false, unsub = null, ended = false, quiet = false;
    const early = [];
    const box = () => this.shadowRoot?.getElementById("chat-input");
    const btn = () => this.shadowRoot?.getElementById("chat-mic");
    const hint0 = box()?.placeholder;
    const endByte = () => { try { conn.socket.send(new Uint8Array([hid])); } catch (e) {} };
    const stop = () => {
      if (done) return;
      done = true;
      this._micStarting = false;
      this._micStop = null;
      if (box()) box().placeholder = hint0;
      try { proc?.disconnect(); src?.disconnect(); } catch (e) {}
      stream?.getTracks().forEach((t) => t.stop());
      ctx?.close()?.catch?.(() => {});
      if (hid !== null) endByte();
      btn()?.classList.remove("on");
    };
    const finish = () => {
      ended = true;
      if (!unsub) return;
      const u = unsub;
      unsub = null;
      Promise.resolve().then(u).catch(() => {});
    };
    const send = (pcm) => {
      const msg = new Uint8Array(1 + pcm.byteLength);
      msg[0] = hid;
      msg.set(new Uint8Array(pcm.buffer), 1);
      try { conn.socket.send(msg); } catch (err) {}
    };
    try { stream = await navigator.mediaDevices.getUserMedia({ audio: true }); }
    catch (err) { this._micStarting = false; fail("Microphone not available: " + (err?.message || err)); return; }
    if (!this._micHeld) { stop(); return; }   // released during the permission prompt: no run, no message
    try {
      const AC = window.AudioContext || window.webkitAudioContext;
      try {
        ctx = new AC({ sampleRate: 16000 });
        src = ctx.createMediaStreamSource(stream);
      } catch (err) {   // some browsers refuse a 16 kHz context on the microphone; Home Assistant converts other rates itself
        ctx?.close()?.catch?.(() => {});
        ctx = new AC();
        src = ctx.createMediaStreamSource(stream);
      }
      proc = ctx.createScriptProcessor(4096, 1, 1);
      proc.onaudioprocess = (e) => {
        if (done) return;
        const f = e.inputBuffer.getChannelData(0);
        const pcm = new Int16Array(f.length);
        for (let i = 0; i < f.length; i++) pcm[i] = Math.max(-1, Math.min(1, f[i])) * 0x7fff;
        if (hid !== null) send(pcm);
        else if (early.length < 400) early.push(pcm);
      };
      src.connect(proc);
      proc.connect(ctx.destination);
    } catch (err) { stop(); fail("Microphone not available: " + (err?.message || err)); return; }
    this._micStop = stop;
    this._micStarting = false;
    btn()?.classList.add("on");
    const onEvent = (ev) => {
      if (ev.type === "run-start") {
        hid = ev.data?.runner_data?.stt_binary_handler_id ?? null;
        if (hid === null) return;
        early.splice(0).forEach(send);
        if (done) { quiet = true; endByte(); }   // released before HA was ready: a tap, so no complaint if nothing was said
        else if (box()) box().placeholder = "Listening...";   // show that the mic is listening
      } else if (ev.type === "stt-vad-end") stop();
      else if (ev.type === "stt-end") {
        stop();
        finish();
        const text = (ev.data?.stt_output?.text || "").trim();
        if (text) {
          this._chatDraft = text;
          if (box()) box().value = text;
          setTimeout(() => { if (this._chatDraft === text) this._sendChat(text); }, 1200);
        }
      } else if (ev.type === "error") {
        stop();
        finish();
        if (!quiet) fail("Voice: " + (ev.data?.message || ev.data?.code || "not understood"));
      } else if (ev.type === "run-end") { stop(); finish(); }
    };
    try {
      const pipeId = await this._chatPipeline();
      if (done) return;   // released before anything was asked of Home Assistant
      const ask = (extra) => conn.subscribeMessage(onEvent, { type: "assist_pipeline/run", pipeline: pipeId, start_stage: "stt", end_stage: "stt",
        input: { sample_rate: Math.round(ctx.sampleRate), ...extra }, timeout: 30 });
      try { unsub = await ask({ no_vad: true }); }
      catch (err) {
        if (err?.code !== "invalid_format") throw err;
        unsub = await ask({});   // a Home Assistant without no_vad here: a pause then ends the recording
      }
      if (ended) finish();
    } catch (err) { stop(); fail("Voice: " + (err?.message || err)); }
  }

"""
CLEAR_JS = r"""  _stopChatAudio() {
    this._speakGen = (this._speakGen || 0) + 1;   // speech still being prepared must not start either
    if (this._chatAudio) { this._chatAudio.pause(); this._chatAudio = null; }
  }

  _clearChat() {
    this._chatTag();
    this._chatMessages.forEach((m) => this._chatRemoved.add(m.id));
    this._chatMessages = [];
    this._chatConvId = null;
    this._chatReq = (this._chatReq || 0) + 1;   // a reply still on its way stays out of the cleared chat
    this._chatBusy = false;
    this._stopChatAudio();
    this._chatWipe = true;
    this._saveChat();
    this._refreshChat();
  }

  // Jarvis Home patch (2026-11-07): CLEAN deletes the history on every device of this login, so it asks first, on the page
  // itself. The question text starts where CLEAN was, with CANCEL and YES after it, so a second tap on that spot hits no button
  // and cannot confirm. The question closes by itself; a render keeps it.
  _chatAskSet(on) {
    this._chatAsk = on;
    clearTimeout(this._chatAskT);
    this._chatAskT = on ? setTimeout(() => this._chatAskSet(false), this._chatAskWait ?? 10000) : null;
    const root = this.shadowRoot;
    root?.getElementById("chat-clear")?.toggleAttribute("hidden", on);
    root?.getElementById("chat-ask")?.toggleAttribute("hidden", !on);
    if (on) root?.getElementById("chat-ask-no")?.focus();
  }
"""
ASK_CSS = ('  .chat-ask { display: inline-flex; flex-wrap: wrap; align-items: center; gap: 8px; margin-left: 12px; color: #ff4d4d;\n'
           '    font-family: var(--font-mono); font-size: 10px; letter-spacing: 0.08em; }\n'
           '  .chat-ask .chat-clean { margin-left: 0; }\n'
           '  .chat-ask .chat-yes { color: #ff4d4d; border-color: #ff4d4d; }\n'
           '  .chat-ask[hidden], .chat-clean[hidden] { display: none; }\n')
P = "frontend/jarvis-panel.js"
edit(P, "_chatMerge", [
    ('    this._chatLoaded = false;       // saved history fetched once per page\n',
     lambda o: o + '    this._chatRemoved = new Set();  // ids deleted on this device\n    this._chatDraft = "";           // the message box survives a full render\n'),
    (between(P, '  // Jarvis Home patch (2026-11-05): chat history saved per HA user', '  // Jarvis Home patch (2026-11-05): the chat box fills'), CHAT_SYNC),
    ('    this._chatMessages.splice(i, 1);\n',
     '    this._chatTag();\n    if (this._chatMessages[i]) this._chatRemoved.add(this._chatMessages[i].id);\n    this._chatMessages.splice(i, 1);\n'),
    ('  _refreshChat() {\n    const log = ', '  _refreshChat() {\n    this._chatTag();\n    const log = '),
    (between(P, "  // Jarvis Home patch (2026-11-05): voice in the Chat tab", "  async _speakChat(text) {"), MIC_JS),
    ('    if (!this._chatSpeak || !text) return;\n',
     '    if (!this._chatSpeak || !text || !this.isConnected) return;   // a reply that lands after leaving the panel stays silent\n    const gen = this._speakGen;\n'),
    ('if (url && this._chatSpeak) {\n', 'if (url && this._chatSpeak && gen === this._speakGen) {\n'),
    ('  _clearChat() {\n    this._chatMessages = [];\n    this._chatConvId = null;\n    this._saveChat();\n    this._refreshChat();\n  }\n', CLEAR_JS),
    ('  async _sendChat() {\n', '  async _sendChat(spoken) {\n'),
    ('    const text = (input?.value || "").trim();\n    if (!text) return;\n    if (input) input.value = "";\n',
     '    const text = (input?.value || spoken || "").trim();\n    if (!text) return;\n    if (input) input.value = "";\n    this._chatDraft = "";\n'),
    ('        || this._currentTab === "memory" || this._currentTab === "suggestions";\n',
     '        || this._currentTab === "memory" || this._currentTab === "suggestions" || this._currentTab === "chat";\n'),
    ('autocomplete="off"></textarea>', 'autocomplete="off">${this._esc(this._chatDraft)}</textarea>'),
    ('<button id="chat-mic" class="chat-mic" title="Hold to talk"', '<button id="chat-mic" class="chat-mic ${this._micStop ? \'on\' : \'\'}" title="Hold to talk"'),
    ('    if (chatInput) {\n      chatInput.addEventListener("keydown", (e) => {\n',
     '    if (chatInput) {\n      chatInput.addEventListener("input", () => { this._chatDraft = chatInput.value; });\n      chatInput.addEventListener("keydown", (e) => {\n'),
    ('          this._micHeld = true;\n          if (!this._micStop) this._toggleMic();\n        });\n'
     '        const release = () => { this._micHeld = false; if (this._micStop) this._micStop(); };\n'
     '        ["pointerup", "pointercancel", "lostpointercapture"].forEach((n) => micBtn.addEventListener(n, release));\n',
     '          this._micHeld = true;\n          this._micPointer = e.pointerId;\n          if (!this._micStop) this._toggleMic();\n        });\n'),
    ('        window.addEventListener("resize", this._chatResize);\n      }\n',
     lambda o: o + ('      if (!this._micRelease) {   // on the window: a full render may replace the button while it is held\n'
                    '        this._micRelease = (e) => {\n'
                    '          if (e.pointerId !== this._micPointer) return;\n'
                    '          this._micHeld = false;\n'
                    '          if (this._micStop) this._micStop();\n'
                    '        };\n'
                    '        ["pointerup", "pointercancel"].forEach((n) => window.addEventListener(n, this._micRelease));\n'
                    '      }\n')),
    ('  disconnectedCallback() {\n    this._stopIntervals();\n  }\n',
     '  disconnectedCallback() {\n    this._stopIntervals();\n'
     '    if (this._chatResize) { window.removeEventListener("resize", this._chatResize); this._chatResize = null; }\n'
     '    if (this._micRelease) {\n'
     '      ["pointerup", "pointercancel"].forEach((n) => window.removeEventListener(n, this._micRelease));\n'
     '      this._micRelease = null;\n'
     '    }\n'
     '    this._micHeld = false;\n'
     '    if (this._micStop) this._micStop();\n'
     '    this._stopChatAudio();\n'
     '  }\n'),
    # CLEAN asks first (audit 2026-11-07): the question and its two buttons are always in the page, shown or hidden by _chatAskSet
    ('        <span>Talk to JARVIS<button id="chat-clear" class="chat-clean">CLEAN</button></span>\n',
     '        <span>Talk to JARVIS<button id="chat-clear" class="chat-clean" ${this._chatAsk ? \'hidden\' : \'\'}>CLEAN</button>'
     '<span id="chat-ask" class="chat-ask" ${this._chatAsk ? \'\' : \'hidden\'}>Delete all messages on every device?'
     '<button id="chat-ask-no" class="chat-clean">CANCEL</button><button id="chat-ask-yes" class="chat-clean chat-yes">YES, DELETE ALL</button></span></span>\n'),
    ('    if (chatClear) chatClear.addEventListener("click", () => this._clearChat());\n',
     '    if (chatClear) chatClear.addEventListener("click", () => this._chatAskSet(true));\n'
     '    this.shadowRoot.getElementById("chat-ask-no")?.addEventListener("click", () => this._chatAskSet(false));\n'
     '    this.shadowRoot.getElementById("chat-ask-yes")?.addEventListener("click", () => { this._chatAskSet(false); this._clearChat(); });\n'),
    ('  .chat-clean:hover { background: var(--cyan-faint); }\n', lambda o: o + ASK_CSS),
], "73 panel: chat history merges across devices, safe mic start and stop, hold ignores pauses, drafts survive a render, CLEAN asks first, then cancels")

# 74 -----------------------------------------------------------------------------------------
LIMIT74 = r'''# Jarvis Home patch (2026-11-06): what a logged-in non-admin may do through execute_plan. It takes any domain and service from
# the model, so without this a plan could reach admin services. An allowlist of ordinary device verbs on the device's own domain.
_JH_PLAN_SERVICES = frozenset({
    "turn_on", "turn_off", "toggle", "lock", "unlock", "open", "open_cover", "close_cover", "stop_cover", "set_cover_position",
    "media_play", "media_pause", "media_stop", "media_play_pause", "media_next_track", "media_previous_track",
    "volume_up", "volume_down", "volume_set", "volume_mute", "set_temperature", "set_hvac_mode", "set_fan_mode", "set_preset_mode",
    "set_percentage", "oscillate", "set_direction", "set_humidity", "start", "pause", "stop", "return_to_base", "locate",
    "open_valve", "close_valve", "start_mowing", "dock",
    "press", "select_option", "select_next", "select_previous", "set_value", "select_source", "play_media", "send_command"})
# homeassistant.turn_on/off/toggle pass the call on to the device's own domain, so these are the same as asking that domain.
_JH_PLAN_GENERIC = frozenset({"media_player", "climate", "scene", "script"})
_JH_PLAN_REFUSED = ("Not allowed for this login: a plan may only use normal device actions on the device's own domain, "
                    "and this step is not one of them. Nothing was changed.")
_JH_TARGET_KEYS = frozenset({"entity_id", "area_id", "device_id", "floor_id", "label_id", "target"})


async def _jh_limited_caller(hass) -> Optional[str]:
    """The login behind this request when that login is limited. None for an admin, and for voice with no login."""
    uid = _JH_CALLER.get()
    if not uid:
        return None
    try:
        user = await hass.auth.async_get_user(uid)
    except Exception:
        user = None
    return None if user is not None and user.is_admin else uid


def _jh_plan_refused(domain: str, service: str, entity_id: str) -> bool:
    target = str(entity_id).split(".")[0]
    if domain == "homeassistant":
        return service not in ("turn_on", "turn_off", "toggle") or target not in _JH_ON_OFF | _JH_PLAN_GENERIC
    if domain == "script" and target == "script" and service == str(entity_id).split(".", 1)[-1] and service != "reload":
        return False   # script.<name> is that script's own run service
    return domain != target or service not in _JH_PLAN_SERVICES


def _jh_own_presence(hass, uid: str, rows: list) -> list:
    """Only the routines of the person linked to this login: the person entity and its own trackers."""
    own = set()
    for st in hass.states.async_all("person"):
        if st.attributes.get("user_id") == uid:
            own.add(st.entity_id)
            own.update(st.attributes.get("device_trackers") or [])
    return [r for r in rows if r.get("entity_id") in own]


async def _jh_not_mine(hass, row: Optional[dict]) -> bool:
    """A limited login may only touch its own follow-ups and goals. An ownerless row is not theirs."""
    mine = await _jh_limited_caller(hass)
    if mine is None:
        return False
    owner = (row or {}).get("user_id")
    return not row or ("?" if owner is None else owner) != mine   # "?" is how an old row with no owner runs


'''
edit("agent.py", "_jh_limited_caller", [
    ('def _no_action_note(served_by: str, n_tools: int) -> str:\n', lambda o: LIMIT74 + o),
    # run_agent: a scheduled job runs with the rights of the person who asked for it
    ('    extra_directive: Optional[str] = None,\n) -> str:\n    """\n    Run the JARVIS agentic LLM loop (v5.7.07).\n',
     '    extra_directive: Optional[str] = None,\n    jh_owner: str = "",\n) -> str:\n    """\n    Run the JARVIS agentic LLM loop (v5.7.07).\n'),
    ('        _JH_CALLER.set(getattr(getattr(user_input, "context", None), "user_id", None))\n',
     '        _JH_CALLER.set(getattr(getattr(user_input, "context", None), "user_id", None) or jh_owner or None)\n'),
    # execute_plan
    ('''                            "ok": False, "error": f"entity '{entity_id}' not found"})\n            continue\n''',
     lambda o: o + ('        if _jh_limited and _jh_plan_refused(domain, service, entity_id):\n'
                    '            _LOGGER.warning("JARVIS: blocked plan step %s.%s for a non-admin login", domain, service)\n'
                    '            results.append({"step": i + 1, "description": desc, "ok": False, "error": _JH_PLAN_REFUSED})\n'
                    '            continue\n')),
    ('    from .kernel import plan as _kplan\n\n    results = []\n    succeeded = 0\n',
     '    from .kernel import plan as _kplan\n\n'
     '    # Jarvis Home patch: the service call carries the login, so Home Assistant applies that person\'s own rights too.\n'
     '    _jh_limited = await _jh_limited_caller(hass)\n'
     '    _jh_ctx = None\n'
     '    if _JH_CALLER.get():\n'
     '        from homeassistant.core import Context\n'
     '        _jh_ctx = Context(user_id=_JH_CALLER.get())\n'
     '    results = []\n    succeeded = 0\n'),
    ('        svc_data = {"entity_id": entity_id, **extra}\n',
     '        # Jarvis Home patch: the step acts on the entity the checks above saw, never on a wider target in service_data.\n'
     '        svc_data = {**{k: v for k, v in extra.items() if k not in _JH_TARGET_KEYS}, "entity_id": entity_id}\n'),
    ('            await hass.services.async_call(_d, _svc, dict(_data), blocking=True)\n',
     '            await hass.services.async_call(_d, _svc, dict(_data), blocking=True, context=_jh_ctx)\n'),
    # follow-ups: the owner is read here, because the executor thread does not see the request's login
    ('    from . import followups\n    res = await hass.async_add_executor_job(\n        lambda: followups.schedule(\n',
     '    from . import followups\n    _jh_uid = _JH_CALLER.get() or ""\n    res = await hass.async_add_executor_job(\n        lambda: followups.schedule(\n'),
    ('            context=args.get("context", "") or ""))\n', '            context=args.get("context", "") or "", user_id=_jh_uid))\n'),
    ('''        ok = await hass.async_add_executor_job(\n            lambda: followups.cancel(int(fid)))\n''',
     '''        _jh_mine = await _jh_limited_caller(hass)\n        ok = await hass.async_add_executor_job(\n            lambda: followups.cancel(int(fid), user_id=_jh_mine))\n'''),
    ('    rows = await hass.async_add_executor_job(followups.pending)\n    if not rows:\n        return "No follow-ups pending."\n',
     '    rows = await hass.async_add_executor_job(followups.pending)\n'
     '    _jh_mine = await _jh_limited_caller(hass)\n'
     '    if _jh_mine is not None:\n        rows = [r for r in rows if r.get("user_id") == _jh_mine]\n'
     '    if not rows:\n        return "No follow-ups pending."\n'),
    # goals
    ('    from . import goals\n    res = await hass.async_add_executor_job(\n        lambda: goals.create(\n',
     '    from . import goals\n    _jh_uid = _JH_CALLER.get() or ""\n    res = await hass.async_add_executor_job(\n        lambda: goals.create(\n'),
    ('            deadline_minutes=args.get("deadline_minutes")))\n', '            deadline_minutes=args.get("deadline_minutes"), user_id=_jh_uid))\n'),
    ('        return "update_goal needs goal_id."\n',
     lambda o: o + '    if await _jh_not_mine(hass, await hass.async_add_executor_job(lambda: goals.get(int(gid)))):\n'
                   '        return f"Couldn\'t update goal #{gid}: no goal #{gid}"\n'),
    ('            return "Which goal? Give me its id (use list first)."\n',
     lambda o: o + '        if await _jh_not_mine(hass, await hass.async_add_executor_job(lambda: goals.get(int(gid)))):\n'
                   '            return f"No active goal #{gid} found."\n'),
    ('        if not g:\n            return f"No goal #{gid}."\n',
     '        if not g or await _jh_not_mine(hass, g):\n            return f"No goal #{gid}."\n'),
    ('    rows = await hass.async_add_executor_job(goals.active)\n    if not rows:\n        return "No active goals."\n',
     '    rows = await hass.async_add_executor_job(goals.active)\n'
     '    _jh_mine = await _jh_limited_caller(hass)\n'
     '    if _jh_mine is not None:\n        rows = [g for g in rows if g.get("user_id") == _jh_mine]\n'
     '    if not rows:\n        return "No active goals."\n'),
    # reads that name other people
    ('    if who:\n        rows = [r for r in rows\n                if who in str(r.get("entity_id", "")).lower()\n',
     lambda o: '    _jh_mine = await _jh_limited_caller(hass)\n    if _jh_mine is not None:\n        rows = _jh_own_presence(hass, _jh_mine, rows)\n' + o),
    ('        return json.dumps(status)\n    except Exception as exc:\n        return json.dumps({"error": str(exc)})\n\n\nasync def _exec_connectivity_status',
     lambda o: ('        if await _jh_limited_caller(hass) is not None:\n'
                '            # Jarvis Home patch: other people\'s goals and the facts behind the beliefs are not for every login.\n'
                '            if isinstance(status.get("self_model"), dict):\n'
                '                status["self_model"]["commitments"] = [c for c in status["self_model"].get("commitments", [])\n'
                '                                                       if not str(c).startswith("goal:")]\n'
                '            if isinstance(status.get("beliefs"), dict):\n'
                '                status["beliefs"]["sample"] = []\n') + o),
    # root cause: the timeline quotes what people asked for, so a limited login gets its own lines only
    ('    result = await hass.async_add_executor_job(\n        lambda: rca.analyze(entity_id, event_time, window))\n',
     '    _jh_mine = await _jh_limited_caller(hass)\n'
     '    result = await hass.async_add_executor_job(\n        lambda: rca.analyze(entity_id, event_time, window, viewer=_jh_mine))\n'),
], "74 agent: plans stay within device control for non-admins, scheduled work keeps its owner, follow-ups and goals are per person")

# 75 -----------------------------------------------------------------------------------------
OWNER75 = r'''        # Jarvis Home patch (2026-11-06): who asked for this. '' is voice with no login. Rows from before this patch stay NULL:
        # nobody knows whose they are, so they run with the limits of an unknown login.
        if "user_id" not in {r[1] for r in conn.execute("PRAGMA table_info(%s)")}:
            try:
                conn.execute("ALTER TABLE %s ADD COLUMN user_id TEXT")
            except sqlite3.OperationalError as exc:
                if "duplicate column" not in str(exc).lower():
                    raise
'''
edit("followups.py", "ADD COLUMN user_id", [
    ('        conn.execute("CREATE INDEX IF NOT EXISTS idx_fu_due ON followups(status, due_ts)")\n',
     lambda o: o + OWNER75 % ("followups", "followups")),
    ('def schedule(instruction: str, delay_minutes: float, *, context: str = "",\n             now: Optional[datetime] = None, db_path: Optional[str] = None) -> dict:\n',
     'def schedule(instruction: str, delay_minutes: float, *, context: str = "",\n             now: Optional[datetime] = None, db_path: Optional[str] = None,\n             user_id: str = "") -> dict:\n'),
    ('                "INSERT INTO followups (created_ts, due_ts, instruction, context) "\n                "VALUES (?,?,?,?)",\n                (t.isoformat(sep=" "), due.isoformat(sep=" "), instruction, context))\n',
     '                "INSERT INTO followups (created_ts, due_ts, instruction, context, user_id) "\n                "VALUES (?,?,?,?,?)",\n                (t.isoformat(sep=" "), due.isoformat(sep=" "), instruction, context, user_id or ""))\n'),
    ('''def cancel(followup_id: int, *, db_path: Optional[str] = None) -> bool:\n    db_path = db_path or DB_PATH\n    try:\n        with _connect(db_path) as conn:\n            cur = conn.execute(\n                "UPDATE followups SET status='cancelled' "\n                "WHERE id=? AND status='pending'", (int(followup_id),))\n''',
     '''def cancel(followup_id: int, *, db_path: Optional[str] = None, user_id: Optional[str] = None) -> bool:\n    """With user_id, only that person's own follow-up is cancelled."""\n    db_path = db_path or DB_PATH\n    try:\n        with _connect(db_path) as conn:\n            cur = conn.execute(\n                "UPDATE followups SET status='cancelled' "\n                "WHERE id=? AND status='pending' AND (? IS NULL OR user_id=?)",\n                (int(followup_id), user_id, user_id))\n'''),
    ('            result = await runner(row["instruction"], row.get("context") or "")\n',
     '            result = await runner(row["instruction"], row.get("context") or "", row.get("user_id"))\n'),
    ('                        category="followup", urgency="low",\n', '                        category="followup", urgency="low", user_id=row.get("user_id") or "",\n'),
], "75a followups: every follow-up carries its owner")
edit("goals.py", "ADD COLUMN user_id", [
    ('        conn.execute("CREATE INDEX IF NOT EXISTS idx_goal_due ON goals(status, next_check_ts)")\n',
     lambda o: o + OWNER75 % ("goals", "goals")),
    ('           now: Optional[datetime] = None,\n           db_path: Optional[str] = None) -> dict:\n    """Open a new goal.',
     '           now: Optional[datetime] = None,\n           db_path: Optional[str] = None,\n           user_id: str = "") -> dict:\n    """Open a new goal.'),
    ('                "next_check_ts, check_interval_min, deadline_ts) "\n                "VALUES (?,?,?,?,?,?,?,?)",\n',
     '                "next_check_ts, check_interval_min, deadline_ts, user_id) "\n                "VALUES (?,?,?,?,?,?,?,?,?)",\n'),
    ('                 _iso(t), interval, deadline))   # due immediately: first engagement\n',
     '                 _iso(t), interval, deadline, user_id or ""))   # due immediately: first engagement\n'),
    ('            result = (await runner(prompt, "") or "").strip()\n',
     '            result = (await runner(prompt, "", g.get("user_id")) or "").strip()\n'),
    ('                    category="goal", urgency="low",\n', '                    category="goal", urgency="low", user_id=g.get("user_id") or "",\n'),
], "75b goals: every goal carries its owner")
edit("cognitive_core.py", "jh_owner", [
    ('    async def _run(instruction: str, context: str) -> str:\n        from .agent import run_agent\n',
     '    async def _run(instruction: str, context: str, owner=None) -> str:\n        from .agent import run_agent\n'),
    ('            temperature=0.4,\n            config=config,\n        )\n    return _run\n',
     '            temperature=0.4,\n            config=config,\n'
     '            # Jarvis Home patch (2026-11-06): the person who asked keeps their limits later on. "?" is no real login, so\n'
     '            # a row with an unknown owner gets the limits of an unknown login. "" is voice with no login, as when it was asked.\n'
     '            jh_owner="?" if owner is None else owner,\n'
     '        )\n    return _run\n'),
], "75c cognitive_core: follow-ups and goals run with their owner's limits")
edit("database.py", "_JH_ACTIVITY_OWNER", [
    ('    conn.execute("CREATE INDEX IF NOT EXISTS idx_conv_user ON conversations(user_id)")\n',
     lambda o: ('    # _JH_ACTIVITY_OWNER: a follow-up or goal line in the activity log belongs to the person who asked for it.\n'
                '    if "user_id" not in {r[1] for r in conn.execute("PRAGMA table_info(activity_log)")}:\n'
                '        try:\n'
                '            conn.execute("ALTER TABLE activity_log ADD COLUMN user_id TEXT NOT NULL DEFAULT \'\'")\n'
                '        except sqlite3.OperationalError as exc:\n'
                '            if "duplicate column" not in str(exc).lower():\n'
                '                raise\n') + o),
    ('    was_spoken: bool = False,\n    source: str = "observer",\n) -> None:\n    """Persist an activity log entry',
     '    was_spoken: bool = False,\n    source: str = "observer",\n    user_id: str = "",\n) -> None:\n    """Persist an activity log entry'),
    ('                "(timestamp, entity_id, category, urgency, message, was_spoken, source) "\n                "VALUES (?,?,?,?,?,?,?)",\n',
     '                "(timestamp, entity_id, category, urgency, message, was_spoken, source, user_id) "\n                "VALUES (?,?,?,?,?,?,?,?)",\n'),
    ('                    1 if was_spoken else 0,\n                    source,\n', '                    1 if was_spoken else 0,\n                    source,\n                    user_id,\n'),
], "75d database: activity lines can name their owner")
edit("websocket.py", "_JH_PRIVATE_ACTIVITY", [
    ('# ─── Activity log WebSocket command ──────────────────────────────────────────\n',
     lambda o: o + '\n# Jarvis Home patch (2026-11-06): these lines quote what a person asked for and what came back.\n_JH_PRIVATE_ACTIVITY = ("followup", "goal")\n'),
    ('        # Format for the panel\n        result = []\n        for e in entries:\n            ts_str = e.get("timestamp", "")\n',
     lambda o: ('        if not _is_admin(connection):\n'
                '            entries = [e for e in entries if e.get("category") not in _JH_PRIVATE_ACTIVITY\n'
                '                       or e.get("user_id") == connection.user.id]\n') + o),
    ('                "id": g.get("id"),\n                "title": g.get("title", ""),\n                "outcome": g.get("outcome", ""),\n',
     lambda o: '                "owner": g.get("user_id"),\n' + o),
    ('                if _jh_key in result.get("config", {}):\n                    result["config"][_jh_key] = ""\n',
     lambda o: o + '            result["goals"] = [g for g in result.get("goals", []) if g.get("owner") == connection.user.id]\n'),
    ('                lambda: goals.create(title, outcome, **kwargs))\n',
     '                lambda: goals.create(title, outcome, user_id=connection.user.id, **kwargs))\n'),
    ('                "base": base or "",\n', '                "base": (base or "") if _is_admin(connection) else "",\n'),
    ('''                int(msg.get("window_secs") or rca.DEFAULT_WINDOW_SECS)))\n''',
     '''                int(msg.get("window_secs") or rca.DEFAULT_WINDOW_SECS),\n'''
     '''                viewer=None if _is_admin(connection) else connection.user.id))\n'''),
], "75e websocket: follow-up and goal lines and goals are per person, Ollama address for admins only")
VIEWER75 = r'''# Jarvis Home patch (2026-11-06, round 3): the activity log and the commands table quote what a person asked for. viewer is a
# limited login, None an admin or voice. A limited login sees follow-up and goal lines of its own only, and for a command
# only who gave it, because its text can be a private request such as a reminder.
_JH_PRIVATE_ACTIVITY = ("followup", "goal")


def _jh_for_viewer(ev: dict, viewer: Optional[str]) -> dict:
    if viewer is not None:
        ev["actions"] = [a for a in ev.get("actions", []) if a.get("category") not in _JH_PRIVATE_ACTIVITY or a.get("user_id") == viewer]
        ev["jh_limited"] = True
    return ev


'''
edit("rca.py", "_jh_for_viewer", [
    ('# ── candidate causes ─', lambda o: VIEWER75 + o),
    ('''                        "cause": f"{who} asked: \\"{cmd['text']}\\" {int(secs)}s earlier",\n''',
     '''                        "cause": (f"{who} asked for this {int(secs)}s earlier" if ev.get("jh_limited")\n'''
     '''                                  else f"{who} asked: \\"{cmd['text']}\\" {int(secs)}s earlier"),\n'''),
    ('''                      "text": f"{cmd.get('person', 'someone')}: \\"{cmd['text']}\\""})\n''',
     '''                      "text": (f"{cmd.get('person', 'someone')} gave a command" if ev.get("jh_limited")\n'''
     '''                               else f"{cmd.get('person', 'someone')}: \\"{cmd['text']}\\"")})\n'''),
    ('            activity_db: str = ACTIVITY_DB) -> dict:\n', '            activity_db: str = ACTIVITY_DB, *, viewer: Optional[str] = None) -> dict:\n'),
    ('        ev = gather(entity_id, event_time, window_secs, patterns_db, activity_db)\n',
     '        ev = _jh_for_viewer(gather(entity_id, event_time, window_secs, patterns_db, activity_db), viewer)\n'),
], "75f rca: root cause shows a limited login no one else's follow-ups, goals or request text")

# 76 -----------------------------------------------------------------------------------------
VAGUE76 = r'''_JH_YES = r"yes|yes sir|yes do|yeah|yep|sure|ok|okay|do it|go ahead|please do|that one|ja|ja hoor|jawel|doe maar"


def _jh_vague(hass, name_fragment, spoken=""):
    """Jarvis Home patch (2026-11-05): names of every device a vague word could mean, read live.
    "the bathroom" and "the bathroom light" reach this as the same fragment, so the spoken words decide (2026-11-06):
    only a name really said is exact, a bare room word is vague, and "light" or "lamp" leaves the other devices out."""
    frag = _jh_frag(name_fragment)
    if not frag:
        return []
    said = spoken.lower().replace("colour", "color")
    light_word = bool(re.search(rf"\b{re.escape(frag)}\s+(?:lights?|lamps?)\b", said))
    hits, exact = [], []
    for dom in ("light", "switch", "climate"):
        for st in hass.states.async_all(dom):
            nm = st.attributes.get("friendly_name") or ""
            low = nm.lower().replace("colour", "color")
            if _jh_is_setting(hass, st.entity_id):
                continue
            if light_word and dom != "light" and not re.search(r"\b(?:lights?|lamps?)\b", low):
                continue
            if low == frag and st.state not in ("unavailable", "unknown"):
                return []
            if frag in low and nm not in [h[1] for h in hits]:
                hits.append((st.entity_id, nm))
                if low != frag and _jh_frag(nm) == frag and re.search(rf"\b{re.escape(low)}\b", said):
                    exact.append(nm)
    if len(exact) == 1:
        return []
    return hits if len(hits) > 1 else []


'''
WHOLE76 = r'''# Jarvis Home patch (2026-11-06, round 3): a word like fan or door is cut from a name to find "the bedroom" in "the bedroom lamp",
# but it can be the very word that tells two real devices apart.
_JH_KIND_DOMAINS = {"fan": ("fan",), "fans": ("fan",), "lock": ("lock",), "locks": ("lock",), "door": ("lock", "cover"), "doors": ("lock", "cover"),
                    "cover": ("cover",), "covers": ("cover",), "thermostat": ("climate",)}


def _jh_said_name(spoken):
    name = re.sub(r"\s+(?:on|off)$", "", re.sub(r"\s+(?:please|now|for me|jarvis|again)$", "", spoken.lower().strip()))
    return re.sub(r"^(?:my|the)\s+", "", name).replace("colour", "color").strip()


def _jh_whole(hass, spoken):
    """The one device whose name holds the whole name as said, suffix word included, in any domain."""
    name = _jh_said_name(spoken)
    words = name.split()
    if len(words) < 2 or words[-1] not in _JH_KIND_DOMAINS:
        return None
    bare, hits = " ".join(words[:-1]), []
    for dom in ("light", "switch", "lock", "cover", "climate", "fan", "media_player"):
        for st in hass.states.async_all(dom):
            if _jh_is_setting(hass, st.entity_id):
                continue
            nm = (st.attributes.get("friendly_name") or "").lower().replace("colour", "color")
            found = (st.entity_id, st.attributes.get("friendly_name", st.entity_id))
            if nm == name:
                return found
            if set(words) <= set(nm.split()) or (nm == bare and dom in _JH_KIND_DOMAINS[words[-1]]):
                hits.append(found)
    return hits[0] if len(hits) == 1 else None


def _jh_wrong_kind(spoken, resolved):
    """True when the words asked for a fan, a lock or the like and the match is neither that kind nor named so."""
    words = _jh_said_name(spoken).split()
    if len(words) < 2 or words[-1] not in _JH_KIND_DOMAINS:
        return False
    return resolved[0].split(".")[0] not in _JH_KIND_DOMAINS[words[-1]] and words[-1] not in str(resolved[1]).lower().split()


# Is this turn a device command? The truth check in agent.py asks, so a wrong yes costs a good answer and a wrong no lets a claim
# through. It leans hard to no: only the opening word makes an order, so "the heating went off" and a bare "door open" are
# statements. A question is never a command, and chat that begins like one needs a device to be one.
_JH_FILLER_RE = re.compile(r"^(?:(?:ok(?:ay)?|hey|hi|hello|yo|sir|jarvis|please|kindly|just|now|then|and|also|so|go ahead and|graag"
                           r"|alsjeblieft|alstublieft|bitte|por favor)\b[\s,]*)+")
_JH_POLITE_RE = re.compile(r"^(?:could you|can you|would you|will you|i want you to|i need you to|i'd like you to|kun je|kan je|kunt u|wil je|wilt u)\b[\s,]*")
_JH_TAIL_RE = re.compile(r"[\s,]+(?:please|sir|jarvis|now|for me|thanks|thank you|alsjeblieft|alstublieft|graag|bitte|por favor)$")
_JH_ASK_RE = re.compile(r"^(?:tell me|check if|check whether|what|what's|whats|which|who|who's|whose|where|where's|when|why|how|is|are|was|were|am|do|does|did|has|have|had|should"
                        r"|would|could|can|will|shall|isn't|aren't|wat|welke|wie|waar|wanneer|waarom|hoe|staat|ben|zijn|heeft|hebben|kan|kun|moet"
                        r"|ist|sind|warum|wann|wo|wer|comment|pourquoi|quand|quel|quelle|qué|que|cómo|como|cuándo|dónde|cosa|quando|perché)\b")
# Words that make a sentence about something, not an order to it.
_JH_NO_ORDER_RE = re.compile(r"\b(?:don't|do not|never|not|niet|geen|nicht|if|when|als|wanneer|about|over|story|poem|joke|song|essay|letter|talking|praten"
                             r"|verhaal|gedicht|grap|tell|explain\w*|write|translate|remember|remind|what|how|why|wat|hoe|i|i'm|i've|we|ik|wij|you"
                             r"|he|she|they|is|are|was|were|ben|zijn|staat|ist|sind)\b")
_JH_ORDER_VERBS = frozenset(("turn switch set dim brighten open close shut lock unlock kill stop start pause resume play mute unmute activate deactivate "
                             "enable disable raise lower toggle zet doe draai sluit schakel dimmen schalte schalt öffne schließe stell dimme "
                             "allume éteins eteins ouvre ferme baisse monte enciende apaga abre cierra accendi spegni apri chiudi").split())
# Too common to be an order alone: these only open one together with an end word, as in "put the lamp on".
_JH_SOFT_VERBS = frozenset("put make maak mach".split())
_JH_ORDER_ENDS = frozenset("on off up down open shut closed brighter dimmer darker warmer cooler colder hotter higher lower aan uit dicht omhoog omlaag hoger lager kouder an aus auf zu".split())
# Dutch puts the verb last after a polite opening: "kun je de lamp aanzetten".
_JH_ORDER_LAST = frozenset("zetten doen aanzetten uitzetten aandoen uitdoen openen sluiten dimmen".split())
# Kinds of device, not devices: a claim about the blinds is false in a house without blinds too.
_JH_KIND_RE = re.compile(r"\b(?:lights?|lamps?|heat|heating|heaters?|volume|brightness|radiators?|thermostats?|aircon|air conditioning|tv|television|blinds?|curtains?|shutters?|shades?"
                         r"|doors?|gates?|garage|windows?|fans?|locks?|plugs?|sockets?|speakers?|music|vacuum|alarm"
                         r"|licht|lichten|lampen|verwarming|kachels?|thermostaat|televisie|gordijnen|gordijn|rolluiken|rolluik|zonwering|deuren|deur|poort"
                         r"|ventilator|stekker|muziek|stofzuiger|lampe|lampen|heizung|fernseher|tür|lumi[eè]res?|chauffage|télé|porte|volets?"
                         r"|luz|luces|lámpara|calefacción|puerta|luce|luci|lampada|riscaldamento|porta)\b")
_JH_WARMTH_RE = re.compile(r"(?:make|get) it (?:a (?:bit|little) |slightly )?(?:warmer|cooler|colder|hotter)|maak het (?:wat |een beetje )?(?:warmer|kouder)")
_JH_ARTICLE = r"\s(?:the|my|a|an|de|het|een)\s"
_JH_ACT_DOMAINS = ("light", "switch", "fan", "cover", "lock", "climate", "media_player", "vacuum", "humidifier", "water_heater", "siren", "scene",
                   "script", "input_boolean", "button", "input_button", "valve", "remote", "lawn_mower", "group", "alarm_control_panel")


def _jh_opening(text):
    """(the words without greetings and politeness, was it a polite request, did it end in a question mark)."""
    said = (text or "").strip().lower().replace("’", "'")
    body = _JH_FILLER_RE.sub("", said)
    polite = _JH_POLITE_RE.match(body)
    if polite:
        body = _JH_FILLER_RE.sub("", body[polite.end():])
    return body, bool(polite), said.endswith("?")


def is_question(text):
    body, polite, mark = _jh_opening(text)
    return mark or polite or bool(_JH_ASK_RE.match(body))


def _jh_names_device(hass, body):
    """A kind of device, or the name of a real device or room, read live."""
    if _JH_KIND_RE.search(body):
        return True
    names = []
    for dom in _JH_ACT_DOMAINS:
        names += [str(s.attributes.get("friendly_name") or "") for s in hass.states.async_all(dom) if not _jh_is_setting(hass, s.entity_id)]
    try:
        from homeassistant.helpers import area_registry as areg
        for area in areg.async_get(hass).async_list_areas():
            names += [area.name, *(getattr(area, "aliases", None) or ())]
    except Exception:
        pass
    # "set the mood in the bedroom" is about the room, not an order to it, and "sun's up" is no device called Sun
    return any(len(n) >= 3 and re.search(rf"(?<!\bin )(?<!\bat )(?<!\bto )(?<!\bof )(?<!\bfor )(?<!\bwith )(?<!\bfrom )\b{re.escape(n.lower())}\b(?!['\u2019])", re.sub(_JH_ARTICLE, " ", body)) for n in names)


def _jh_local_order(hass, body, aliases):
    """The local command patterns, whole sentence only, with a real target where they name one."""
    if _JH_WARMTH_RE.fullmatch(body) or any(re.fullmatch(p[0], body) for p in _BULK_PATTERNS):
        return True
    for pattern, action, hint in _INTENT_PATTERNS:
        match = re.fullmatch(pattern, body)
        if not match:
            continue
        if action in _MEDIA_ACTIONS or action == "set_temp":
            return True
        if action == "scene":
            if match.group(1) and _find_scene_or_script(hass, match.group(1)):
                return True
        elif action in ("turn_on", "turn_off", "dim", "brighten", "lock", "unlock", "open", "close", "set_temp_named"):
            name = match.group(1).strip()
            if name in _JH_PRONOUNS:
                return True
            found = _find_entity(hass, name, hint, aliases) or _find_entity(hass, name, None, aliases)
            if found and found[0].split(".")[0] in _JH_ACT_DOMAINS:
                return True
    return False


def device_command(hass, text, aliases=None):
    """True when these words order a device about. By the local patterns, or, in any language, by an action word that opens the
    sentence (after please, the assistant's name or a polite opening) and a device or room name after it. Nothing else counts:
    a sentence that opens with its own subject is a statement, and missing a command costs less than replacing ordinary chat."""
    body, polite, mark = _jh_opening(text)
    if (mark and not polite) or (not polite and _JH_ASK_RE.match(body)):
        return False
    body = _normalize(body)
    while _JH_TAIL_RE.search(body):
        body = _JH_TAIL_RE.sub("", body)
    if not body:
        return False
    if _jh_local_order(hass, body, aliases):
        return True
    words = body.split()
    if words[0] in _JH_ORDER_VERBS or (words[0] in _JH_SOFT_VERBS and words[-1] in _JH_ORDER_ENDS):
        target = " ".join(words[1:])   # "lock in the plan": the verb itself is no device
    elif polite and words[-1] in _JH_ORDER_LAST:
        target = body
    else:
        return False
    return not _JH_NO_ORDER_RE.search(body) and _jh_names_device(hass, target)


'''
DEAD76 = ('        _jh_now = hass.states.get(entity_id)\n'
          '        if _jh_now is not None and _jh_now.state == "unavailable" and not action.startswith("query_"):\n'
          '            # Jarvis Home patch (2026-11-06): a command to a device that is not there does nothing, so never report it done.\n'
          '            return LocalResult(text=f"{fname} is unavailable, {honorific}. Nothing was changed.", success=False)\n')
edit("local_engine.py", "_jh_now", [
    (between("local_engine.py", 'def _jh_vague(hass, name_fragment):\n', 'def _find_scene_or_script(hass, name_fragment):\n'), VAGUE76 + WHOLE76),
    ('            _jh_many = _jh_vague(hass, name_frag)\n', '            _jh_many = _jh_vague(hass, name_frag, normalized)\n'),
    ('        if action in ("turn_on", "turn_off"):\n            _jh_many =', '        if action in ("turn_on", "turn_off") and not _jh_named:\n            _jh_many ='),
    ('        resolved = _find_entity(\n            hass, name_frag, domain_hint, learned_aliases\n        ) or _find_entity(hass, name_frag, None, learned_aliases)\n',
     '        _jh_said = normalized[match.start(1):]\n'
     '        _jh_named = _jh_whole(hass, name_frag) or _jh_whole(hass, _jh_said)\n'
     '        resolved = _jh_named or _find_entity(\n            hass, name_frag, domain_hint, learned_aliases\n        ) or _find_entity(hass, name_frag, None, learned_aliases)\n'
     '        if resolved and not _jh_named and (_jh_wrong_kind(name_frag, resolved) or _jh_wrong_kind(_jh_said, resolved)):\n'
     '            resolved = None   # the fan was asked for, so the lamp of that room is no answer\n'),
    ('        _jh_m = [x for x in _jh_p["options"] if _jh_w and set(_jh_w) <= set(x[1].lower().split())]\n',
     '        _jh_m = [x for x in _jh_p["options"] if _jh_w and set(_jh_w) <= set(x[1].lower().split())]\n'
     '        if not _jh_m and len(_jh_p["options"]) == 1 and re.fullmatch(_JH_YES, " ".join(_jh_w)):\n'
     '            _jh_m = _jh_p["options"]   # "Do you mean X?" has a yes for an answer\n'),
    ('                if len(_jh_fit) == 1:\n                    resolved = _jh_fit[0]\n',
     '                if len(_jh_fit) == 1 and action == "turn_on" and any(\n'
     '                        x[1] in _jh_dead and x[0].split(".")[0] != _jh_fit[0][0].split(".")[0] for x in _jh_many):\n'
     '                    # the person may well mean the lamp that is gone, and switching on another kind of device is no safe guess\n'
     '                    _JH_PENDING[pending_key] = {"action": action, "options": _jh_fit, "at": time.time()}\n'
     '                    return LocalResult(text=f"{\', \'.join(_jh_dead)} {_jh_verb} unavailable, {honorific}. Do you mean {_jh_fit[0][1]}?", success=False)\n'
     '                if len(_jh_fit) == 1:\n                    resolved = _jh_fit[0]\n'),
    ('        entity_id, fname = resolved\n        args = {}\n', lambda o: '        entity_id, fname = resolved\n' + DEAD76 + '        args = {}\n'),
], "76 local_engine: a bare room word is vague, an unavailable device is never reported as done")

# 77 -----------------------------------------------------------------------------------------
OWNER77 = r'''# Jarvis Home patch (2026-11-06): a history key belongs to someone. conversation/process takes any conversation id from any login,
# and the id user_<login> can be worked out, so without this a limited login could read another person's live chat.
_JH_KEY_OWNERS: dict = {}   # history key -> the login that first used it, "" for voice with no login. Lives as long as the histories do.


async def _jh_limited_login(hass, uid: str) -> bool:
    """True for a logged-in non-admin, and for a login that cannot be read. Voice with no login is not limited."""
    if not uid:
        return False
    try:
        user = await hass.auth.async_get_user(uid)
    except Exception:
        user = None
    return user is None or not user.is_admin


def _jh_key_owner(hass, cid: str):
    """Whose history this key is, or None when nobody has used it yet."""
    if cid.startswith("user_"):
        return cid[len("user_"):]
    if cid == "default":
        return ""
    if cid in _JH_KEY_OWNERS:
        return _JH_KEY_OWNERS[cid]
    try:
        from homeassistant.helpers import device_registry as _dr
        if _dr.async_get(hass).async_get(cid) is not None:
            return ""   # a voice device: what is said there belongs to no login
    except Exception:
        pass
    return None


def _jh_history_key(hass, cid: str, uid: str, limited: bool) -> str:
    """The key this turn may use. A limited login asking for a key of someone else gets its own private key instead."""
    owner = _jh_key_owner(hass, cid)
    if limited and owner is not None and owner != uid:
        _LOGGER.warning("JARVIS: ignored a conversation id that belongs to someone else")
        return f"user_{uid}"
    if owner is None:
        _JH_KEY_OWNERS[cid] = uid
    return cid


def _jh_offer_refused(offer: dict) -> bool:
    """A limited login may accept an offer only for an ordinary device action, the same list its plans use."""
    from .agent import _jh_plan_refused
    data = (offer or {}).get("action_data") or {}
    ids = data.get("entity_ids") or []
    return not ids or any(_jh_plan_refused(str(data.get("domain")), str(data.get("service")), e) for e in ids)


'''
edit("conversation.py", "_jh_history_key", [
    ('class JarvisAgent(', lambda o: OWNER77 + o),
    ('        cid       = user_input.conversation_id or user_input.device_id or (f"user_{_jh_uid}" if _jh_uid else "default")\n',
     lambda o: o + '        _jh_limited = await _jh_limited_login(self.hass, _jh_uid)\n'
                   '        cid = _jh_history_key(self.hass, cid, _jh_uid, _jh_limited)\n'),
    ('                    res = await cognitive_core.accept_pending_offer()\n                    if res.get("now_autonomous"):\n',
     '                    # Jarvis Home patch: a limited login may say yes to this one action, but its yes builds no standing autonomy.\n'
     '                    res = None if _jh_limited and _jh_offer_refused(pending) else await cognitive_core.accept_pending_offer(record=not _jh_limited)\n'
     '                    if res is None:\n'
     '                        offer_reply = f"That needs an administrator login, {honorific}. Nothing was changed."\n'
     '                    elif res.get("now_autonomous"):\n'),
], "77a conversation: a history key belongs to its owner, a limited login's yes runs one allowed action only")
edit("cognitive_core.py", "accept_pending_offer(record", [
    ('async def accept_pending_offer() -> dict:\n', 'async def accept_pending_offer(record: bool = True) -> dict:\n'),
    ('    if ok and pkey and _CORE.autonomy_mgr:\n        grant = await _CORE.autonomy_mgr.async_record_acceptance(\n',
     '    if ok and not record:\n        _CORE.actions_taken += 1   # Jarvis Home patch: done once, nothing learned from it\n        return {"ok": True}\n'
     '    if ok and pkey and _CORE.autonomy_mgr:\n        grant = await _CORE.autonomy_mgr.async_record_acceptance(\n'),
], "77b cognitive_core: an offer can be accepted without counting toward a grant")

# 78 -----------------------------------------------------------------------------------------
# Every Core option can also be set in the panel. Writing stays admin only: jarvis/update_config needs an admin login.
JH_SWITCHES = (("jh_strict_actions", "Check that actions really ran"),
               ("jh_mail_block", "Block actions after reading an email"),
               ("jh_facts_only_briefings", "Scheduled briefings use real facts only"))   # arrival and security briefings are always facts only
edit("websocket.py", '"jh_strict_actions"', [
    ('    "camera_watch_default",      # str: camera Camera Watch opens on\n',
     lambda o: o + "".join('    "%s",   # bool: Core switch, on by default\n' % k for k, _ in JH_SWITCHES)),
    ('            "camera_watch_default": str(_runtime_opt(hass, entry, "camera_watch_default", "") or ""),\n',
     lambda o: o + "".join('                "%s": bool(_runtime_opt(hass, entry, "%s", True)),\n' % (k, k) for k, _ in JH_SWITCHES)),
], "78a websocket: Core switches can be read and written from the panel")
edit("frontend/jarvis-panel.js", "data-cfg-key=\"camera_watch_default\"", [
    ("          <div class=\"cfg-row\"><label>LLM web-search fallback</label>${tog('web_research_llm_fallback', !!c.web_research_llm_fallback)}</div>\n",
     lambda o: o + "".join("          <div class=\"cfg-row\"><label>%s</label>${tog('%s', c.%s !== false)}</div>\n" % (t, k, k) for k, t in JH_SWITCHES)
               + "          <div class=\"cfg-row\"><label>Camera Watch opens on</label><select class=\"cfg-field\" data-cfg-key=\"camera_watch_default\">${this._optsLabeled([['', 'Last camera used'], ...(Array.isArray(c.cameras) ? c.cameras : []).map(x => [x.entity_id, x.name || x.entity_id])], String(c.camera_watch_default || ''))}</select></div>\n"),
], "78b panel: Core switches and the Camera Watch default in Persona & Core")

# 79 -----------------------------------------------------------------------------------------
# Whisper wrote "Switch, stick, green." and the commas broke both the colour pattern and device_command, so the model claimed the
# lamp was green with no tool call. Commands are matched on the words only, for typed and spoken text alike.
CLEAN79 = r'''# Jarvis Home patch (2026-11-06): punctuation is how speech-to-text writes a pause, so it never changes what a command does.
# Digits keep what numbers and times need (21.5, 1,5, 7:30, -5, 50%), and words keep their apostrophes and hyphens.
_JH_MARKS_RE = re.compile(r"(?<!\d)[,:]|[,:](?!\d)|[!?;…¡¿\"“”„«»()\[\]{}–—。、，！？；：]|(?<!\w)['`]|['`](?!\w)|(?<!\S)-+(?!\S)")


def _jh_clean(text: str) -> str:
    text = str(text or "").replace("’", "'").replace("‘", "'")
    text = re.sub(r"(?<!\d)\.|\.(?!\d)", "", text)   # gone without a space, so "a.m." stays one word
    return " ".join(_JH_MARKS_RE.sub(" ", text).split())


'''
edit("local_engine.py", "_jh_clean", [
    ('def _normalize(text: str) -> str:\n    text = text.lower().strip()\n    text = re.sub(r"[.,!?;:]+$", "", text)\n',
     lambda o: CLEAN79 + 'def _normalize(text: str) -> str:\n    text = _jh_clean(text.lower())\n'),
    ('    """Score 0-100. Higher = needs LLM. <70 handled locally."""\n    t = text.lower()\n',
     '    """Score 0-100. Higher = needs LLM. <70 handled locally."""\n    t = _jh_clean(text).lower()\n'),
    ('    body = _JH_FILLER_RE.sub("", said)\n', '    body = _JH_FILLER_RE.sub("", _jh_clean(said))\n'),
    # A rising voice gets a question mark, so an order stays an order. A real question is told by its opening words.
    ('    body, polite, mark = _jh_opening(text)\n    if (mark and not polite) or (not polite and _JH_ASK_RE.match(body)):\n',
     '    body, polite, _mark = _jh_opening(text)\n    if not polite and _JH_ASK_RE.match(body):\n'),
    ('    m = _JH_COLOR_RE.match(normalized)\n',
     '    said = _jh_opening(normalized)[0]   # "Jarvis, switch the lamp green, please" is the same colour command\n'
     '    while _JH_TAIL_RE.search(said):\n        said = _JH_TAIL_RE.sub("", said)\n'
     '    m = _JH_COLOR_RE.match(said)\n'),
], "79 local_engine: punctuation is stripped before local matching and before device_command")

# 80 -----------------------------------------------------------------------------------------
# Speech-to-text spells a name by ear: "disk" for a device called Disc. Only for comparing, and only after every normal tier found
# nothing. Names shown or spoken stay as they are.
SOUND80 = r'''def _jh_sound(text):
    """Jarvis Home patch (2026-11-06): how a name sounds, for comparing only. k, ck and q become c, ph becomes f, z becomes s and
    doubled letters count once. Digits stay as they are, so Lamp 11 is never Lamp 1."""
    t = _jh_clean(text).lower().replace("colour", "color").replace("ph", "f").replace("ck", "c")
    t = re.sub(r"[kq]", "c", t).replace("z", "s")
    return re.sub(r"([^\W\d_])\1+", r"\1", t)


def _jh_sound_hits(hass, fragment, domains):
    """The real names that sound like these words, read live: the whole name when one sounds alike, else names that hold every
    word. Whole words only, and three letters at least, so a sound never stretches to an unrelated name."""
    want = _jh_sound(fragment)
    if len(want.replace(" ", "")) < 3:
        return []
    words = set(want.split())
    whole, same, part = [], [], []
    for domain in domains:
        for st in hass.states.async_all(domain):
            name = st.attributes.get("friendly_name") or ""
            if not name or _jh_is_setting(hass, st.entity_id):
                continue
            sound = _jh_sound(name)
            heard = set(sound.split())
            box = whole if sound == want else same if heard == words else part if words <= heard else None
            if box is not None and name not in [x[1] for x in box]:
                box.append((st.entity_id, name))
    return whole or same or part


_JH_SOUND_DOMAINS = ("light", "switch", "lock", "cover", "climate", "fan", "media_player", "sensor", "binary_sensor")
_JH_SOUND_CONTROL = ("light", "switch", "lock", "cover", "climate", "fan", "media_player")


def _jh_sound_many(hass, name_fragment, resolved, domains=None):
    """Two or more devices that sound like these words, when normal matching did not find the name. Never guess between them."""
    frag = _jh_frag(name_fragment)
    if not frag or (resolved and frag in str(resolved[1]).lower().replace("colour", "color")):
        return []
    hits = _jh_sound_hits(hass, frag, domains or _JH_SOUND_DOMAINS)
    return hits if len(hits) > 1 else []


'''
SOUND_TIER80 = ('    _jh_heard = _jh_sound_hits(hass, fragment, domains)\n'
                '    if len(_jh_heard) == 1:   # two sound-alikes are for the caller to ask about\n'
                '        _LOGGER.info("Entity resolve: sounds like \'%s\' → %s", fragment, _jh_heard[0][0])\n'
                '        return _jh_heard[0]\n\n')
ASK80 = ('        _jh_ears = None if _jh_named else _jh_sound_many(hass, name_frag, resolved, list(_JH_SOUND_CONTROL) if action in ("turn_on", "turn_off") or not domain_hint else [domain_hint])\n'
         '        if _jh_ears:\n'
         '            if action not in ("turn_on", "turn_off", "lock", "unlock", "open", "close") or any(_needs_confirmation(hass, action, x[0]) for x in _jh_ears):\n'
         '                return None   # a value to carry or a protected device: the agent asks\n'
         '            _JH_PENDING[pending_key] = {"action": action, "options": _jh_ears, "at": time.time()}\n'
         '            _jh_n = [x[1] for x in _jh_ears]\n'
         '            return LocalResult(text=f"Which one, {honorific}: {\', \'.join(_jh_n[:-1])} or {_jh_n[-1]}?", success=False)\n')
edit("local_engine.py", "_jh_sound", [
    ('def _jh_heaters(hass):\n', lambda o: SOUND80 + o),
    ('    # A failed resolution is normal, expected control flow: the phrase looked\n', lambda o: SOUND_TIER80 + o),
    # scenes and scripts: one sound-alike runs, two go to the agent
    ('            if fragment.replace(" ", "_") in state.entity_id:\n                return (state.entity_id,\n'
     '                        state.attributes.get("friendly_name", state.entity_id), dtype)\n    return None\n',
     '            if fragment.replace(" ", "_") in state.entity_id:\n                return (state.entity_id,\n'
     '                        state.attributes.get("friendly_name", state.entity_id), dtype)\n'
     '    _jh_heard = _jh_sound_hits(hass, fragment, ("scene", "script"))\n'
     '    if len(_jh_heard) == 1:\n'
     '        return (*_jh_heard[0], _jh_heard[0][0].split(".")[0])\n'
     '    return None\n'),
    # areas
    ('        if not target:\n            return []\n        for entry in ent_reg.entities.values():\n            if entry.domain != domain:\n',
     '        if not target:\n'
     '            _jh_rooms = [a for a in area_reg.async_list_areas() if len(_jh_sound(area_name)) >= 3 and _jh_sound(a.name) == _jh_sound(area_name)]\n'
     '            target = _jh_rooms[0] if len(_jh_rooms) == 1 else None\n'
     '        if not target:\n            return []\n        for entry in ent_reg.entities.values():\n            if entry.domain != domain:\n'),
    # single-entity commands: ask before a sound-alike is ever acted on
    ('        if not resolved:\n            # Track failure but keep trying other patterns/domains\n',
     lambda o: ASK80 + o),
    # the truth check: a device said by ear is still a device
    ('    return any(len(n) >= 3 and re.search(rf"(?<!\\bin )(?<!\\bat )(?<!\\bto )(?<!\\bof )(?<!\\bfor )(?<!\\bwith )(?<!\\bfrom )\\b{re.escape(n.lower())}\\b(?![\'\\u2019])", re.sub(_JH_ARTICLE, " ", body)) for n in names)\n',
     '    hay = re.sub(_JH_ARTICLE, " ", body)\n'
     '    ears = _jh_sound(hay)\n'
     '    def said(name, words):\n'
     '        return len(name) >= 3 and re.search(rf"(?<!\\bin )(?<!\\bat )(?<!\\bto )(?<!\\bof )(?<!\\bfor )(?<!\\bwith )(?<!\\bfrom )\\b{re.escape(name)}\\b(?![\'\\u2019])", words)\n'
     '    return any(said(n.lower(), hay) or said(_jh_sound(n), ears) for n in names)\n'),
    ('            if found and found[0].split(".")[0] in _JH_ACT_DOMAINS:\n                return True\n',
     lambda o: o + '            if any(x[0].split(".")[0] in _JH_ACT_DOMAINS for x in _jh_sound_many(hass, name, None)):\n                return True\n'),
    # colours: two lamps that sound alike are asked about, and the answer finishes the colour
    ('async def _jh_try_color(hass, normalized, honorific):\n', 'async def _jh_try_color(hass, normalized, honorific, pending_key=""):\n'),
    ('    found = _find_entity(hass, m.group(1), "light")\n    if not found:\n        return None\n',
     '    found = _find_entity(hass, m.group(1), "light")\n    if not found:\n'
     '        _jh_ears = _jh_sound_many(hass, m.group(1), None, ["light"])\n'
     '        if not _jh_ears:\n            return None\n'
     '        _JH_PENDING[pending_key] = {"action": "turn_on", "colour": m.group(2), "options": _jh_ears, "at": time.time()}\n'
     '        _jh_n = [x[1] for x in _jh_ears]\n'
     '        return LocalResult(text=f"Which one, {honorific}: {\', \'.join(_jh_n[:-1])} or {_jh_n[-1]}?", success=False)\n'),
    ('    _jh_color = await _jh_try_color(hass, normalized, honorific)\n', '    _jh_color = await _jh_try_color(hass, normalized, honorific, pending_key)\n'),
    ('        if len(_jh_m) == 1:\n            _jh_eid, _jh_name = _jh_m[0]\n',
     '        if len(_jh_m) == 1 and _jh_p.get("colour"):\n'
     '            return await _jh_try_color(hass, f"set {_jh_m[0][1].lower()} to {_jh_p[\'colour\']}", honorific)\n'
     '        if len(_jh_m) == 1:\n            _jh_eid, _jh_name = _jh_m[0]\n'),
], "80 local_engine: sound-alike names as a last fallback, two sound matches ask which one")
edit("agent.py", "_jh_sound", [
    ('                fuzz = _fuzzy(query, fname)\n                if fuzz > 45:\n                    score = fuzz * 0.7  # Scale down fuzzy scores\n',
     lambda o: o + '                from .local_engine import _jh_sound\n'
                   '                if len(_jh_sound(query)) >= 3 and _jh_sound(query) == _jh_sound(fname):\n'
                   '                    score = 64   # Jarvis Home patch: sounds the same, just under every normal match\n'),
], "80b agent: search_entities ranks a name that sounds the same")

# 81 -----------------------------------------------------------------------------------------
# Written against Home Assistant 2026.11.0, components/assist_pipeline:
#  websocket_api.py  a wake_word run takes input {sample_rate, timeout, ...}. input.timeout is seconds of silence before the error
#                    wake-word-timeout and defaults to 3; 0 turns that off (default_pipeline.py only arms it when above 0). The
#                    top-level timeout (300 s) ends the whole run with the error "timeout". The binary handler id comes in run-start
#                    and is unregistered when the run ends. An empty binary message ends the audio. Unsubscribing cancels the run.
#  default_pipeline.py  wake_word-end has an empty wake_word_output when the audio ended with no detection. Refusals before run-start
#                    are websocket errors with the same code as the error event: wake-engine-missing, wake-provider-missing.
#                    stt-no-text-recognized and duplicate_wake_up_detected are normal outcomes.
WAKE_JS = r"""  // Jarvis Home patch (2026-11-06): the audio path of hold to talk and Hey JARVIS alike: microphone to 16 bit PCM at 16 kHz.
  _micPipe(stream, onPcm) {
    const AC = window.AudioContext || window.webkitAudioContext;
    let ctx, src;
    try {
      try {
        ctx = new AC({ sampleRate: 16000 });
        src = ctx.createMediaStreamSource(stream);
      } catch (err) {   // some browsers refuse a 16 kHz context on the microphone; Home Assistant converts other rates itself
        ctx?.close()?.catch?.(() => {});
        ctx = new AC();
        src = ctx.createMediaStreamSource(stream);
      }
      const proc = ctx.createScriptProcessor(4096, 1, 1);
      proc.onaudioprocess = (e) => {
        const f = e.inputBuffer.getChannelData(0);
        const pcm = new Int16Array(f.length);
        for (let i = 0; i < f.length; i++) pcm[i] = Math.max(-1, Math.min(1, f[i])) * 0x7fff;
        onPcm(pcm);
      };
      src.connect(proc);
      proc.connect(ctx.destination);
      return { ctx, src, proc };
    } catch (err) { ctx?.close()?.catch?.(() => {}); throw err; }
  }

  _micSend(conn, hid, pcm) {
    const msg = new Uint8Array(1 + pcm.byteLength);
    msg[0] = hid;
    msg.set(new Uint8Array(pcm.buffer), 1);
    try { conn.socket.send(msg); } catch (err) {}
  }

  // Words that came from the microphone: shown in the box, then sent unless they were changed. A typed draft is never replaced
  // and never sent by itself (audit 2026-11-07): the words are added after it and wait for Send. While JARVIS is still answering
  // the words wait in the box and go as soon as the reply is in.
  _chatSpoken(text) {
    const box = this.shadowRoot?.getElementById("chat-input");
    const draft = (box ? box.value : this._chatDraft || "").trim();
    if (draft) {
      this._chatDraft = draft + " " + text;
      if (box) box.value = this._chatDraft;
      return;
    }
    this._chatDraft = text;
    if (box) box.value = text;
    this._spokenWait = (this._spokenWait || 0) + 1;
    this._wakeSync();
    const send = () => {
      if (this._chatDraft === text && this._chatBusy && this.isConnected) { setTimeout(send, 300); return; }
      this._spokenWait--;
      if (this._chatDraft === text) this._sendChat(text);
    };
    setTimeout(send, 1200);
  }

  // Jarvis Home patch (2026-11-06): Hey JARVIS. While the switch is on and the panel is open the microphone feeds Home Assistant's
  // own pipeline, from wake word to speech-to-text; audio goes nowhere else. _wakeSync decides everything from the live state: it
  // is safe to call at any time and from anywhere. While the person talks by hand, has a draft in the box or JARVIS answers it lets
  // go of the microphone and looks again every half second, so JARVIS cannot wake itself or take over a message being typed. A
  // focused box alone does not count: the box keeps the focus after every reply.
  _wakeBusy() {
    const a = this._chatAudio;
    return !!(this._micHeld || this._micStop || this._micStarting || Date.now() < (this._micUntil || 0) || this._spokenWait
      || this._chatBusy || this._speakBusy || (a && !a.paused && !a.ended && !a.error) || (this._chatDraft || "").trim());
  }

  _wakeSync() {
    const conn = this._hass?.connection;
    if (!this._wakeOn || !this.isConnected || !conn || conn.connected === false) { this._wakeHalt(); return; }
    this._wakeHook(conn);
    if (this._wakeBusy()) {
      this._wakeHalt();
      this._wakeLater(500);
    } else if (!this._wakeRun && !this._wakeTimer) this._wakeStart(conn);
  }

  _wakeLater(ms) {
    clearTimeout(this._wakeTimer);
    this._wakeTimer = setTimeout(() => { this._wakeTimer = null; this._wakeSync(); }, ms);
  }

  _wakeHook(conn) {   // the socket can drop and return without the panel noticing
    if (this._wakeConn === conn || !conn.addEventListener) return;
    this._wakeUnhook();
    this._wakeConn = conn;
    this._wakeSeen = () => this._wakeSync();
    conn.addEventListener("disconnected", this._wakeSeen);
    conn.addEventListener("ready", this._wakeSeen);
  }

  _wakeUnhook() {
    if (!this._wakeConn) return;
    this._wakeConn.removeEventListener?.("disconnected", this._wakeSeen);
    this._wakeConn.removeEventListener?.("ready", this._wakeSeen);
    this._wakeConn = null;
  }

  _wakeHalt() {
    clearTimeout(this._wakeTimer);
    this._wakeTimer = null;
    this._wakeRun?.end();
    this._wakeRun = null;
    const mic = this._wakeMic;
    this._wakeMic = null;
    if (mic) {
      if (mic.resume) window.removeEventListener("pointerdown", mic.resume, true);
      mic.stream.getAudioTracks().forEach((t) => t.removeEventListener("ended", mic.lost));
      try { mic.proc.disconnect(); mic.src.disconnect(); } catch (e) {}
      mic.stream.getTracks().forEach((t) => t.stop());
      mic.ctx.close()?.catch?.(() => {});
    }
    if (this._wakeHot) this._wakeMark(false);
  }

  _wakeMark(hot) {
    this._wakeHot = hot;
    this._wakeShow();
  }

  _wakeShow() {
    const btn = this.shadowRoot?.getElementById("chat-wake");
    if (btn) {
      btn.classList.toggle("on", !!this._wakeOn);
      btn.classList.toggle("hot", !!this._wakeHot);
      btn.setAttribute("aria-pressed", this._wakeOn ? "true" : "false");
      btn.title = this._wakeHot ? "Hey JARVIS heard you and is listening"
        : this._wakeOn ? "Hey JARVIS is on: listening for the wake word. Click to turn it off"
        : "Hey JARVIS is off. Click to listen for the wake word";
    }
    const box = this.shadowRoot?.getElementById("chat-input");
    if (!box) return;
    if (this._wakeHot && box.dataset.hint === undefined) {
      box.dataset.hint = box.placeholder;
      box.placeholder = "Listening...";
    } else if (!this._wakeHot && box.dataset.hint !== undefined) {
      box.placeholder = box.dataset.hint;
      delete box.dataset.hint;
    }
  }

  _wakeSet(on, why) {
    this._wakeOn = on;
    try { localStorage.setItem("jarvis_chat_wake", on ? "1" : "0"); } catch (e) {}
    this._wakeFails = 0;
    this._wakeWarned = false;
    this._wakeSync();
    this._wakeShow();
    if (why) { this._chatMessages.push({ role: "error", text: why }); this._refreshChat(); }
  }

  _wakeNone() {
    return `No wake word is set for this assistant, ${this._honorific()}. Choose one in Home Assistant under Settings, Voice assistants. Hey JARVIS is off.`;
  }

  async _wakeOpen(conn, gone) {   // true when the microphone is ready for this run
    if (this._wakeMic) return true;
    const off = (t) => { if (!gone()) this._wakeSet(false, t + " Hey JARVIS is off."); return false; };
    if (!navigator.mediaDevices?.getUserMedia) return off("The microphone needs the https address of this page.");
    let stream, mic;
    try { stream = await navigator.mediaDevices.getUserMedia({ audio: true }); }
    catch (err) { return off("Microphone not available: " + (err?.message || err) + "."); }
    if (gone()) { stream.getTracks().forEach((t) => t.stop()); return false; }
    try {
      mic = { stream, ...this._micPipe(stream, (pcm) => {
        const run = this._wakeRun;
        if (run && run.hid !== null && !run.over) {
          this._micSend(conn, run.hid, pcm);
          this._wakeLevel(conn, run, pcm);
        }
      }) };
    } catch (err) {
      stream.getTracks().forEach((t) => t.stop());
      return off("Microphone not available: " + (err?.message || err) + ".");
    }
    mic.lost = () => { if (this._wakeMic === mic) this._wakeSet(false, `The microphone was lost, ${this._honorific()}. Hey JARVIS is off.`); };
    stream.getAudioTracks().forEach((t) => t.addEventListener("ended", mic.lost));
    if (mic.ctx.state === "suspended") {   // a page opened with the switch on: the browser waits for a first touch
      mic.resume = () => { mic.ctx.resume?.()?.catch?.(() => {}); };
      mic.resume();
      window.addEventListener("pointerdown", mic.resume, true);
    }
    this._wakeMic = mic;
    return true;
  }

  async _wakeStart(conn) {
    const run = { t0: Date.now(), hid: null, unsub: null, over: false, hot: false };
    this._wakeRun = run;
    const gone = () => this._wakeRun !== run;
    run.end = () => {
      if (run.over) return;
      run.over = true;
      clearTimeout(run.dog);
      clearTimeout(run.turn);
      if (run.hid !== null) { try { conn.socket.send(new Uint8Array([run.hid])); } catch (e) {} }
      run.hid = null;
      if (run.unsub) { const u = run.unsub; run.unsub = null; Promise.resolve().then(u).catch(() => {}); }
    };
    const row = await this._chatPipelineRow();
    if (gone()) return;
    if (row && !row.wake_word_entity) { this._wakeSet(false, this._wakeNone()); return; }
    if (!(await this._wakeOpen(conn, gone)) || gone()) return;
    const onEvent = (ev) => {
      if (run.over) return;
      if (ev.type === "run-start") run.hid = ev.data?.runner_data?.stt_binary_handler_id ?? null;
      else if (ev.type === "wake_word-end") {
        if (!Object.keys(ev.data?.wake_word_output || {}).length) return;   // the audio ended and nothing was heard
        run.hot = true;
        run.hotAt = Date.now();
        this._wakeMark(true);
        this._wakeBeep();
        run.dog = setTimeout(() => this._wakeAgain(run), this._wakeDog ?? 30000);   // no answer from speech-to-text
      } else if (ev.type === "stt-vad-end") {
        if (run.hid !== null) { try { conn.socket.send(new Uint8Array([run.hid])); } catch (e) {} }
        run.hid = null;
      } else if (ev.type === "stt-end") {
        const text = (ev.data?.stt_output?.text || "").trim();
        // Speech-to-text invents a short word such as "Okay." from silence: a lone filler word is not a command.
        if (!text || /^(ok(ay)?|thank you|thanks|you|bye|yeah|hmm+|uh+|um+)[.!?]*$/i.test(text)) { this._wakeAgain(run); return; }
        run.end();
        this._wakeRun = null;
        this._wakeFails = 0;
        this._wakeWarned = false;
        this._wakeMark(false);
        this._chatSpoken(text);
      } else if (ev.type === "error") this._wakeError(run, ev.data?.code, ev.data?.message);
      else if (ev.type === "run-end") { run.hid = null; this._wakeAgain(run); }
    };
    try {
      const unsub = await conn.subscribeMessage(onEvent, { type: "assist_pipeline/run", pipeline: row?.id, start_stage: "wake_word", end_stage: "stt",
        input: { sample_rate: Math.round(this._wakeMic.ctx.sampleRate), timeout: 0, no_vad: true }, timeout: 300 }, { resubscribe: false });
      if (run.over) { Promise.resolve().then(unsub).catch(() => {}); return; }
      run.unsub = unsub;
      // HA ends a run at its timeout, wherever the person is in a sentence. A fresh run before that keeps a command whole.
      run.turn = setTimeout(() => {
        if (run.over || run.hot) return;
        run.end();
        this._wakeRun = null;
        this._wakeSync();
      }, this._wakeTurn ?? 270000);
    } catch (err) { if (!run.over) this._wakeError(run, err?.code, err?.message || String(err)); }
  }

  // A short soft tone when the wake word is heard (2026-11-07), made in the browser: no file, and it is over before the
  // first 0.5 s of speech that the end-of-speech check skips, so speech-to-text does not hear it.
  _wakeBeep() {
    const ctx = this._wakeMic?.ctx;
    if (!ctx || ctx.state !== "running") return;
    try {
      const osc = ctx.createOscillator(), gain = ctx.createGain(), t = ctx.currentTime;
      osc.type = "sine";
      osc.frequency.setValueAtTime(880, t);
      gain.gain.setValueAtTime(0.0001, t);
      gain.gain.exponentialRampToValueAtTime(0.18, t + 0.015);
      gain.gain.exponentialRampToValueAtTime(0.0001, t + 0.14);
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.start(t);
      osc.stop(t + 0.15);
    } catch (e) {}
  }

  // Home Assistant's own end-of-speech check stops after 0.7 s of quiet, so a pause after the wake word ended the command. The
  // page decides instead: it skips the end of the wake word, waits for the person to start, and ends after a real pause. When
  // nobody speaks, nothing is sent. Levels are 16 bit; the gate follows the room's own noise.
  _wakeLevel(conn, run, pcm) {
    let sum = 0;
    for (let i = 0; i < pcm.length; i++) sum += pcm[i] * pcm[i];
    const rms = Math.sqrt(sum / (pcm.length || 1)), now = Date.now();
    if (!run.hot) {
      const n = this._wakeNoise ?? rms;
      if (rms < n * 4 + 200) this._wakeNoise = n * 0.9 + rms * 0.1;
      return;
    }
    const since = now - run.hotAt;
    if (since < (this._wakeSkip ?? 500)) return;
    if (rms > Math.max(this._wakeGate ?? 700, (this._wakeNoise || 0) * 3)) { run.spoke = run.spoke || now; run.loud = now; }
    const finished = run.spoke && now - run.loud > (this._wakeQuiet ?? 1300);
    if (!finished && since < (run.spoke ? (this._wakeMax ?? 15000) : (this._wakeWait ?? 7000))) return;
    if (!run.spoke) { this._wakeAgain(run); return; }
    try { conn.socket.send(new Uint8Array([run.hid])); } catch (e) {}
    run.hid = null;
  }

  _wakeError(run, code, message) {
    run.hid = null;
    if (code === "wake-engine-missing" || code === "wake-provider-missing") {
      run.end();
      this._wakeSet(false, this._wakeNone());
      return;
    }
    const soft = ["wake-word-timeout", "timeout", "duplicate_wake_up_detected", "wake_word_detection_aborted", "stt-no-text-recognized"].includes(code);
    if (!soft && !this._wakeWarned) {
      this._wakeWarned = true;
      this._chatMessages.push({ role: "error", text: `Hey JARVIS could not listen: ${message || code}. I will keep trying, ${this._honorific()}.` });
      this._refreshChat();
    }
    this._wakeAgain(run, !soft);
  }

  // Listen again after a wait that grows while runs keep ending early. Ordinary endings stay within a few seconds.
  _wakeAgain(run, hard) {
    run.end();
    if (this._wakeRun === run) this._wakeRun = null;
    this._wakeMark(false);
    if (Date.now() - run.t0 > 10000) this._wakeFails = 0;
    const fails = this._wakeFails || 0;
    this._wakeFails = fails + 1;
    this._wakeLater(Math.min(hard ? 30000 : 4000, (this._wakeBase ?? 500) * 2 ** fails));
  }

"""
WAKE_ICON = ('  _wakeIcon() {\n'
             '    return this._chatIcon("M17,20C16.71,20 16.44,19.94 16.24,19.85C15.53,19.5 15.03,18.97 14.53,17.47C14,15.91 13.06,15.18 12.14,14.47C11.35,13.86 10.53,13.23 9.82,11.94C9.29,11 9,9.93 9,9C9,6.2 11.2,4 14,4C16.8,4 19,6.2 19,9H21C21,5.07 17.93,2 14,2C10.07,2 7,5.07 7,9C7,10.26 7.38,11.65 8.07,12.9C9,14.55 10.05,15.38 10.92,16.05C11.73,16.67 12.31,17.12 12.63,18.1C13.23,19.92 14,20.94 15.36,21.65C15.86,21.88 16.43,22 17,22C19.21,22 21,20.21 21,18H19C19,19.1 18.1,20 17,20M7.64,2.64L6.22,1.22C4.23,3.21 3,5.96 3,9C3,12.04 4.23,14.79 6.22,16.78L7.63,15.37C6,13.74 5,11.5 5,9C5,6.5 6,4.26 7.64,2.64M11.5,9C11.5,10.38 12.62,11.5 14,11.5C15.38,11.5 16.5,10.38 16.5,9C16.5,7.62 15.38,6.5 14,6.5C12.62,6.5 11.5,7.62 11.5,9Z");\n'
             '  }\n\n')
WAKE_CSS = ('  .chat-wake { background: none; border: 1px solid var(--line-hot); color: var(--text-dim); border-radius: var(--radius); cursor: pointer; }\n'
            '  .chat-wake.on { color: var(--cyan); background: var(--cyan-faint); border-color: var(--cyan); }\n'
            '  .chat-wake.hot { color: #ff4d4d; border-color: #ff4d4d; }\n')
PIPE_ROW81 = ('  async _chatPipelineRow() {\n'
              '    try {\n'
              '      const res = await this._hass.callWS({ type: "assist_pipeline/pipeline/list" });\n'
              '      const agent = this._jarvisAgentId();\n'
              '      const rows = res?.pipelines || [];\n'
              '      const all = rows.filter((x) => x.conversation_engine === agent);\n'
              '      return all.find((x) => x.id === res?.preferred_pipeline) || all[0] || rows.find((x) => x.id === res?.preferred_pipeline) || null;\n'
              '    } catch (err) { return null; }\n'
              '  }\n\n'
              '  async _chatPipeline() {\n'
              '    return (await this._chatPipelineRow())?.id;\n'
              '  }\n\n')
edit(P, "_wakeSync", [
    ('    try { this._chatSpeak = localStorage.getItem("jarvis_chat_speak") === "1"; } catch (e) {}\n',
     lambda o: o + '    this._wakeOn = false;           // Hey JARVIS: off until this browser turned it on\n'
                   '    try { this._wakeOn = localStorage.getItem("jarvis_chat_wake") === "1"; } catch (e) {}\n'),
    ('      this._updateLiveValues();\n    }\n  }\n  get hass() { return this._hass; }\n',
     '      this._updateLiveValues();\n    }\n    if (this._wakeOn && !this._wakeRun && !this._wakeTimer) this._wakeSync();\n  }\n  get hass() { return this._hass; }\n'),
    ('      this._startIntervals();\n    }\n  }\n\n  disconnectedCallback() {\n',
     '      this._startIntervals();\n      this._wakeSync();\n    }\n  }\n\n  disconnectedCallback() {\n'),
    ('    if (this._micStop) this._micStop();\n    this._stopChatAudio();\n  }\n',
     '    if (this._micStop) this._micStop();\n    this._stopChatAudio();\n    this._wakeHalt();\n    this._wakeUnhook();\n  }\n'),
    ('    this._micStarting = true;\n', '    this._micStarting = true;\n    this._wakeSync();\n'),
    ('      this._micStop = null;\n      if (box()) box().placeholder = hint0;\n',
     '      this._micStop = null;\n      this._micUntil = Date.now() + 2500;   // the words are still on their way from Home Assistant\n      if (box()) box().placeholder = hint0;\n'),
    ('    const finish = () => {\n      ended = true;\n', '    const finish = () => {\n      ended = true;\n      this._micUntil = 0;\n'),
    (between(P, "    const send = (pcm) => {\n      const msg = new Uint8Array(1 + pcm.byteLength);\n", "    try { stream = await navigator.mediaDevices.getUserMedia({ audio: true }); }\n"),
     '    const send = (pcm) => this._micSend(conn, hid, pcm);\n'),
    (between(P, "      const AC = window.AudioContext || window.webkitAudioContext;\n", '    } catch (err) { stop(); fail("Microphone not available: "'),
     '      ({ ctx, src, proc } = this._micPipe(stream, (pcm) => {\n'
     '        if (done) return;\n'
     '        if (hid !== null) send(pcm);\n'
     '        else if (early.length < 400) early.push(pcm);\n'
     '      }));\n'),
    ('        if (text) {\n          this._chatDraft = text;\n          if (box()) box().value = text;\n'
     '          setTimeout(() => { if (this._chatDraft === text) this._sendChat(text); }, 1200);\n        }\n',
     '        if (text) this._chatSpoken(text);\n'),
    ('  async _speakChat(text) {\n', lambda o: WAKE_JS + o),
    ('    const gen = this._speakGen;\n    try {\n      const pipeId = await this._chatPipeline();\n',
     '    const gen = this._speakGen;\n'
     '    let preparing = true;\n'
     '    const ready = () => { if (preparing) { preparing = false; this._speakBusy--; } };\n'
     '    this._speakBusy = (this._speakBusy || 0) + 1;   // Hey JARVIS rests from here until the voice has finished\n'
     '    this._wakeSync();\n'
     '    try {\n      const pipeId = await this._chatPipeline();\n'),
    ('        if (ev.type === "run-end" || ev.type === "error") unsub();\n',
     '        if (ev.type === "run-end" || ev.type === "error") { finished = true; ready(); drop(); }\n'),
    # Home Assistant can end the run before subscribeMessage has returned: the callback must not touch unsub before it exists,
    # or it throws, ready() never runs and Hey JARVIS waits for a voice that never comes. A failed voice sends "error" and then
    # "run-end" (audit 2026-11-07): unsubscribe once, as the mic does, or the second call is an uncaught "Subscription not found".
    ('      const unsub = await this._hass.connection.subscribeMessage((ev) => {\n        if (ev.type === "tts-end") {\n',
     '      let unsub = null, finished = false;\n'
     '      const drop = () => { const u = unsub; unsub = null; if (u) Promise.resolve().then(u).catch(() => {}); };\n'
     '      unsub = await this._hass.connection.subscribeMessage((ev) => {\n        if (ev.type === "tts-end") {\n'),
    ('      }, { type: "assist_pipeline/run", pipeline: pipeId, start_stage: "tts", end_stage: "tts", input: { text } });\n',
     '      }, { type: "assist_pipeline/run", pipeline: pipeId, start_stage: "tts", end_stage: "tts", input: { text } });\n      if (finished) drop();\n'),
    ('    } catch (err) { console.warn("JARVIS chat: speech failed", err); }\n',
     '    } catch (err) { ready(); console.warn("JARVIS chat: speech failed", err); }\n'),
    ('  _micIcon() {\n', lambda o: WAKE_ICON + o),
    (between(P, "  async _chatPipeline() {\n", "  _toggleSpeak() {\n"), PIPE_ROW81),
    ('    this._chatBusy = true;\n    this._startThink();\n', '    this._chatBusy = true;\n    this._wakeSync();\n    this._startThink();\n'),
    ('<button id="chat-mic" class="chat-mic ${this._micStop ? \'on\' : \'\'}" title="Hold to talk" aria-label="Hold to talk">${this._micIcon()}</button>\n',
     lambda o: o + '          <button id="chat-wake" class="chat-wake ${this._wakeOn ? \'on\' : \'\'}" aria-label="Hey JARVIS" aria-pressed="false">${this._wakeIcon()}</button>\n'),
    ('      this.shadowRoot.getElementById("chat-speak")?.addEventListener("click", () => this._toggleSpeak());\n',
     lambda o: o + '      this.shadowRoot.getElementById("chat-wake")?.addEventListener("click", () => this._wakeSet(!this._wakeOn));\n      this._wakeShow();\n'),
    ('  .chat-tts.on { color: var(--cyan); background: var(--cyan-faint); }\n', lambda o: o + WAKE_CSS),
    # typing makes Hey JARVIS rest at once, so a false wake cannot take over a message being typed (95: only while the box has the
    # focus, and the button says so)
    ('      chatInput.addEventListener("input", () => { this._chatDraft = chatInput.value; });\n',
     '      chatInput.addEventListener("input", () => { this._chatDraft = chatInput.value; if (this._wakeOn) this._wakeSync(); });\n'),
], "81 panel: Hey JARVIS switch, Home Assistant's own wake word pipeline")

# 82 -----------------------------------------------------------------------------------------
# Home summary: settings such as a heater's display backlight are not lights, as in the live facts.
edit("home_state.py", "_jh_is_setting", [
    ('    for state in wm.devices(domain="light"):\n        lights_total += 1\n',
     '    from .local_engine import _jh_is_setting\n    for state in wm.devices(domain="light"):\n'
     '        if _jh_is_setting(hass, state["entity_id"]):\n            continue\n        lights_total += 1\n'),
], "82 home_state.py   settings are not counted as lights")

# 83 -----------------------------------------------------------------------------------------
# Camera DIAG: the original marks it a fault whenever no Nest camera exists. Any camera integration is fine; only a home
# with no cameras at all gets the warning.
edit("frontend/jarvis-panel.js", "Home Assistant has no camera entities", [
    ('${/nest/.test(plats) ? "" : "  <span class=\'cam-diag-bad\'>— NO nest-platform entities: the Google Nest integration isn\'t delivering cameras to HA. Fix that first (SDM credentials / integration setup); JARVIS can only consume what HA has.</span>"}',
     '${plats === "none" ? "  <span class=\'cam-diag-bad\'>— Home Assistant has no camera entities: add a camera integration first; JARVIS can only use the cameras Home Assistant has.</span>" : ""}'),
], "83 jarvis-panel.js camera DIAG does not require Nest")

# 84 -----------------------------------------------------------------------------------------
# Audit 10 (2026-11-07), local engine. Each step is its own marker, so an install that already has 1-83 gets only these.
WHOLE84 = r'''def _jh_action_domains(action):
    """Jarvis Home patch (2026-11-07): the device domains an action can really work on, from the service it would call.
    None when the action works on the device's own domain (turn on, turn off, toggle) or calls nothing."""
    svc = _service_for(action, "x.x")
    return (svc[0],) if svc and action not in ("turn_on", "turn_off", "toggle") else None


def _jh_whole(hass, spoken, domains=None, prefer=None):
    """The one device whose name holds the whole name as said, suffix word included. Only devices the action can work on
    count (2026-11-07: "lock the front door" may never land on a lamp called Front Door), and an exact name in the
    preferred domain wins over an exact name in another domain."""
    name = _jh_said_name(spoken)
    words = name.split()
    if len(words) < 2 or words[-1] not in _JH_KIND_DOMAINS:
        return None
    bare, hits, exact = " ".join(words[:-1]), [], []
    for dom in ("light", "switch", "lock", "cover", "climate", "fan", "media_player"):
        if domains and dom not in domains:
            continue
        for st in hass.states.async_all(dom):
            if _jh_is_setting(hass, st.entity_id):
                continue
            nm = (st.attributes.get("friendly_name") or "").lower().replace("colour", "color")
            found = (st.entity_id, st.attributes.get("friendly_name", st.entity_id))
            if nm == name:
                exact.append(found)
            elif set(words) <= set(nm.split()) or (nm == bare and dom in _JH_KIND_DOMAINS[words[-1]]):
                hits.append(found)
    if exact:
        return next((x for x in exact if x[0].split(".")[0] == prefer), exact[0])
    return hits[0] if len(hits) == 1 else None
'''
edit("local_engine.py", "def _jh_action_domains(action):", [
    (between("local_engine.py", "def _jh_whole(hass, spoken):\n", "\n\ndef _jh_wrong_kind(spoken, resolved):\n"), WHOLE84),
    ('        _jh_named = _jh_whole(hass, name_frag) or _jh_whole(hass, _jh_said)\n',
     '        _jh_doms = _jh_action_domains(action)\n'
     '        _jh_named = _jh_whole(hass, name_frag, _jh_doms, domain_hint) or _jh_whole(hass, _jh_said, _jh_doms, domain_hint)\n'),
    ('        entity_id, fname = resolved\n        _jh_now = hass.states.get(entity_id)\n',
     '        entity_id, fname = resolved\n'
     '        _jh_svc = _service_for(action, entity_id)\n'
     '        if _jh_svc and _jh_svc[0] != entity_id.split(".")[0]:\n'
     '            # Jarvis Home patch (2026-11-07): a lock or cover service on a lamp does nothing and Home Assistant does not\n'
     '            # say so, so it would be reported done. Not this device: try the next pattern, then the agent.\n'
     '            _last_failed_name = name_frag\n'
     '            continue\n'
     '        _jh_now = hass.states.get(entity_id)\n'),
], "84a local_engine: a device found by its whole name must fit the action (no lock service on a lamp)")
EARS84 = ('            _jh_gone = [x[1] for x in _jh_ears if getattr(hass.states.get(x[0]), "state", "unavailable") == "unavailable"]\n'
          '            if _jh_gone:\n'
          '                # Jarvis Home patch (2026-11-07): an unavailable device is never offered, so an answer cannot act on it\n'
          '                _jh_ears = [x for x in _jh_ears if x[1] not in _jh_gone]\n'
          '                _jh_v = "is" if len(_jh_gone) == 1 else "are"\n'
          '                if not _jh_ears:\n'
          '                    return LocalResult(text=f"{\', \'.join(_jh_gone)} {_jh_v} unavailable, {honorific}. Nothing was changed.", success=False)\n'
          '                if len(_jh_ears) == 1:\n'
          '                    _JH_PENDING[pending_key] = {"action": action, "options": _jh_ears, "at": time.time()}\n'
          '                    return LocalResult(text=f"{\', \'.join(_jh_gone)} {_jh_v} unavailable, {honorific}. Do you mean {_jh_ears[0][1]}?", success=False)\n')
edit("local_engine.py", "an unavailable device is never offered", [
    ('                return None   # a value to carry or a protected device: the agent asks\n'
     '            _JH_PENDING[pending_key] = {"action": action, "options": _jh_ears, "at": time.time()}\n',
     '                return None   # a value to carry or a protected device: the agent asks\n' + EARS84 +
     '            _JH_PENDING[pending_key] = {"action": action, "options": _jh_ears, "at": time.time()}\n'),
    ('            if _needs_confirmation(hass, _jh_p["action"], _jh_eid):\n                return None\n'
     '            _jh_ok = await _execute_action(hass, _jh_p["action"], _jh_eid, {})\n',
     '            if _needs_confirmation(hass, _jh_p["action"], _jh_eid):\n                return None\n'
     '            if getattr(hass.states.get(_jh_eid), "state", "unavailable") == "unavailable":\n'
     '                # the device dropped out after the question: a command to it does nothing, so never report it done\n'
     '                return LocalResult(text=f"{_jh_name} is unavailable, {honorific}. Nothing was changed.", success=False)\n'
     '            _jh_ok = await _execute_action(hass, _jh_p["action"], _jh_eid, {})\n'),
], "84b local_engine: 'which one' never offers an unavailable device, and the answer checks it again")
edit("local_engine.py", "Switching that on is no safe guess, so ask first", [
    ('                if len(_jh_fit) == 1:\n                    resolved = _jh_fit[0]\n',
     '                if len(_jh_fit) == 1 and action == "turn_on" and _jh_fit[0][0].split(".")[0] != resolved[0].split(".")[0]:\n'
     '                    # Jarvis Home patch (2026-11-07): the name pointed at one kind of device and the only one that is off is another\n'
     '                    # kind, a heater for a lamp. Switching that on is no safe guess, so ask first.\n'
     '                    _jh_on = [x[1] for x in _jh_live if x not in _jh_fit]\n'
     '                    _jh_lead = f"{\', \'.join(_jh_on)} {\'is\' if len(_jh_on) == 1 else \'are\'} already on, {honorific}. " if _jh_on else ""\n'
     '                    _JH_PENDING[pending_key] = {"action": action, "options": _jh_fit, "at": time.time()}\n'
     '                    return LocalResult(text=f"{_jh_lead}Do you mean {_jh_fit[0][1]}?", success=False)\n'
     '                if len(_jh_fit) == 1:\n                    resolved = _jh_fit[0]\n'),
], "84c local_engine: a vague room word never switches on another kind of device without asking")
edit("local_engine.py", "only a closed cover and a locked lock are secure", [
    ('        for s in hass.states.async_all("cover"):\n            if s.state == "open":\n'
     '                items.append(s.attributes.get("friendly_name", s.entity_id))\n'
     '        for s in hass.states.async_all("lock"):\n            if s.state == "unlocked":\n'
     '                items.append(s.attributes.get("friendly_name", s.entity_id) + " (unlocked)")\n',
     '        # Jarvis Home patch (2026-11-07): only a closed cover and a locked lock are secure. A cover on its way or a latch that is\n'
     '        # open is named with its state, so the all-clear never rests on them. Offline ones stay under "not reporting".\n'
     '        for s in hass.states.async_all("cover"):\n            if s.state == "open":\n'
     '                items.append(s.attributes.get("friendly_name", s.entity_id))\n'
     '            elif s.state not in ("closed", "unavailable", "unknown"):\n'
     '                items.append(f"{s.attributes.get(\'friendly_name\', s.entity_id)} ({s.state})")\n'
     '        for s in hass.states.async_all("lock"):\n            if s.state == "unlocked":\n'
     '                items.append(s.attributes.get("friendly_name", s.entity_id) + " (unlocked)")\n'
     '            elif s.state not in ("locked", "unavailable", "unknown", "jammed"):\n'
     '                items.append(f"{s.attributes.get(\'friendly_name\', s.entity_id)} ({s.state})")\n'),
], "84d local_engine: no all-clear while a lock is open or unlocking or a cover is moving")
edit("local_engine.py", "A reminder or timer set for it is not a device command", [
    ('                         r"|doors?|gates?|garage|windows?|fans?|locks?|plugs?|sockets?|speakers?|music|vacuum|alarm"\n',
     '                         r"|doors?|gates?|garage|windows?|fans?|locks?|plugs?|sockets?|speakers?|music|vacuum"\n'),
    ('    if _JH_KIND_RE.search(body):\n        return True\n    names = []\n',
     '    if _JH_KIND_RE.search(body):\n        return True\n'
     '    # Jarvis Home patch (2026-11-07): "alarm" is a device only in a home with an alarm panel, and "an alarm for 7" is a wake-up\n'
     '    # time, not the panel. A reminder or timer set for it is not a device command.\n'
     '    if (re.search(r"\\balarm\\b", body) and not re.search(r"\\balarm\\s+(?:for|at|in|om|um)\\b", body)\n'
     '            and hass.states.async_all("alarm_control_panel")):\n'
     '        return True\n'
     '    names = []\n'),
], "84e local_engine: 'set an alarm' is no device command unless the home has an alarm panel")
edit("local_engine.py", "never words in an id", [
    ('    return "backlight" in eid or "child_lock" in eid\n',
     '    return False   # Jarvis Home patch (2026-11-07): Home Assistant\'s entity category decides, never words in an id\n'),
], "84f local_engine: settings are known by their entity category only, not by backlight or child_lock in the id")

# 85 -----------------------------------------------------------------------------------------
# Audit 10 (2026-11-07), agent.py: what a tool result may count for, and what a limited login may write.
LANDED85 = ('        # Jarvis Home patch (2026-11-07): a lock still locking or a cover still opening has not landed, so keep waiting, and an\n'
            '        # action with no end state to check (brightness, temperature, volume, toggle) is never reported as confirmed.\n'
            '        def _jh_landed(st):\n'
            '            return bool(st) and _jh_reached(entity_id, _jh_want, st.state)\n'
            '        for _ in range(16):\n'
            '            if not _jh_want or _jh_landed(hass.states.get(entity_id)):\n'
            '                break\n'
            '            await asyncio.sleep(0.25)\n'
            '        new_state = hass.states.get(entity_id)\n'
            '        _jh_now = str(new_state.state).lower() if new_state else "unknown"\n'
            '        if _jh_want:\n'
            '            _jh_ok = _jh_conf = _jh_landed(new_state)\n'
            '        else:\n'
            '            _jh_ok, _jh_conf = _jh_now not in ("unavailable", "unknown"), None\n')
edit("agent.py", "action with no end state to check", [
    (between("agent.py", "        def _jh_landed(st):\n", "        _jh_out = {\n"), LANDED85),
    ('            "confirmed": _jh_ok,\n', '            "confirmed": _jh_conf,\n'),
    ('        if not _jh_ok:\n            _jh_out["sent"] = True\n',
     '        if not _jh_ok and not _jh_want:\n'
     '            _jh_out["warning"] = (f"The device is {_jh_now}, so the command changed nothing. Tell the user so plainly. "\n'
     '                                  "Do not say it is done.")\n'
     '        elif not _jh_ok and _jh_now in _TRANSITIONAL:\n'
     '            _jh_out.update(sent=True, in_progress=True, warning=(\n'
     '                f"The command was sent and the device is still {_jh_now}: it has not reached the end state yet. "\n'
     '                f"Tell the user it is {_jh_now} and not confirmed. Do not say it is done."))\n'
     '        elif not _jh_ok:\n            _jh_out["sent"] = True\n'),
], "85a agent: control_device confirms only an end state it read; moving devices and unread values are not confirmed")
ASKS85 = r'''# Jarvis Home patch (2026-11-07): "Done, sir, anything else?" is one sentence that ends in a question mark, and it still claims.
_JH_CLOSING_RE = _re.compile(r"^\W*(?:(?:and|also|so)\W+)?(?:sir\W+)?(?:how (?:may|can) i|what (?:else )?can i|let me know|anything else"
                             r"|is there anything|feel free|(?:would you like|do you need|need) anything)", _re.I)
_JH_QOPEN_RE = _re.compile(r"^\W*(?:(?:very well|certainly|of course|right|ok(?:ay)?|sir|yes|no)\W+)*(?:shall|should|would|will|do|does|did"
                           r"|can|could|may|is|are|was|were|which|what|how|where|when|who|why|zal|wil|moet|kan|welke|wat|hoe)\b", _re.I)
_JH_CLAIM_RE = _re.compile(r"\b(?:done|switched|turned|set|locked|unlocked|opened|closed|dimmed|activated|started|stopped|changed"
                           r"|(?:is|are)\s+(?:now\s+)?(?:on|off|open|closed|locked|unlocked))\b", _re.I)


def _jh_asks(first: str) -> bool:
    """A real question: it ends in a question mark and opens as a question, or what comes before the question claims
    nothing and the question is more than a closing offer of help."""
    if not first.endswith("?"):
        return False
    if _JH_QOPEN_RE.match(first):
        return True
    parts = _re.split(r"[,:]\s*", first)
    if len(parts) < 2:
        return True
    return not (_JH_CLOSING_RE.search(parts[-1]) or _JH_CLAIM_RE.search(" ".join(parts[:-1])))


'''
edit("agent.py", "def _jh_asks(first: str) -> bool:", [
    ('def _jh_problem(text: str, st: dict, cmd: bool, asked: bool = False) -> Optional[str]:\n', lambda o: ASKS85 + o),
    ('    if first.endswith("?") or _JH_CANNOT_RE.search(_JH_IDIOM_RE.sub(" ", first)):\n',
     '    if _jh_asks(first) or _JH_CANNOT_RE.search(_JH_IDIOM_RE.sub(" ", first)):\n'),
], "85b agent: a claim with a closing question in the same sentence is still a claim")
edit("agent.py", "every entity asked for came back with an error", [
    ('    else:\n        failed = raw.strip().lower().startswith(("error", "failed", "blocked", "unknown tool", "couldn\'t", "could not"))\n',
     '    elif isinstance(data, list) and data and all(isinstance(x, dict) and x.get("error") for x in data):\n'
     '        failed = True   # Jarvis Home patch (2026-11-07): every entity asked for came back with an error, so nothing was read\n'
     '    else:\n        failed = raw.strip().lower().startswith(("error", "failed", "blocked", "unknown tool", "couldn\'t", "could not"))\n'),
], "85c agent: a read where every entity was not found is a failed read, not a read")
NOTDEV85 = r'''# Jarvis Home patch (2026-11-07): whole words of the tool name, so a script called kitchen_playlist is still a device tool.
_JH_NOT_DEVICE_WORDS = frozenset("timer timers list lists todo shopping calendar broadcast nevermind respond".split())


def _jh_not_device(name: str) -> bool:
    """Home Assistant tools that keep a timer or a list or only talk: HassStartTimer, HassListAddItem, HassBroadcast."""
    words = _re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name).lower().replace("_", " ").replace("-", " ").split()
    return any(w in _JH_NOT_DEVICE_WORDS for w in words)
'''
edit("agent.py", "def _jh_not_device(name: str) -> bool:", [
    ('_JH_NOT_DEVICE_RE = _re.compile(r"timer|list|todo|shopping|calendar|broadcast|nevermind|respond")\n', NOTDEV85),
    ('(acting and name not in _TOOL_MAP and not _JH_NOT_DEVICE_RE.search(low))', '(acting and name not in _TOOL_MAP and not _jh_not_device(name))'),
], "85d agent: a timer or list tool is told apart by whole words, so a playlist script still counts as a device")
edit("agent.py", "a limited login cannot write there", [
    ('    # Jarvis Home patch: the learned file goes into every prompt, so a logged-in person\'s preference stays in\n'
     '    # the knowledge store under their own subject.\n'
     '    if not (key == "preference" and _JH_CALLER.get()):\n',
     '    # Jarvis Home patch (2026-11-07): the learned file and the household facts reach every person\'s prompt, an admin\'s too, so\n'
     '    # a limited login cannot write there: no household alias, and a routine is kept under that person\'s own subject.\n'
     '    _jh_limited = await _jh_limited_caller(hass)\n'
     '    if _jh_limited and key == "alias":\n'
     '        return json.dumps({"error": "Not allowed: a household alias needs an administrator login. Nothing was saved. "\n'
     '                                    "Tell the user so plainly."})\n'
     '    # Jarvis Home patch: the learned file goes into every prompt, so a logged-in person\'s preference stays in\n'
     '    # the knowledge store under their own subject.\n'
     '    if not (key == "preference" and _JH_CALLER.get()) and not _jh_limited:\n'),
    ('                k_subject = knowledge.DEFAULT_SUBJECT\n',
     '                from . import identity\n'
     '                k_subject = identity.subject_for_user(hass, _jh_limited) if _jh_limited else knowledge.DEFAULT_SUBJECT\n'),
    ('            await hass.async_add_executor_job(\n'
     '                lambda: knowledge.remember(name, value, subject=k_subject,\n'
     '                                           kind=k_kind, source="stated"))\n'
     '        except Exception as exc:\n'
     '            _LOGGER.debug("knowledge mirror failed: %s", exc)\n',
     '            _jh_kept = await hass.async_add_executor_job(\n'
     '                lambda: knowledge.remember(name, value, subject=k_subject,\n'
     '                                           kind=k_kind, source="stated"))\n'
     '        except Exception as exc:\n'
     '            _jh_kept = None\n'
     '            _LOGGER.debug("knowledge mirror failed: %s", exc)\n'
     '        if _jh_kept is None and ((key == "preference" and _JH_CALLER.get()) or _jh_limited):\n'
     '            # the knowledge store was the only place it went, so nothing was kept\n'
     '            return json.dumps({"error": "Could not store that, so nothing was saved. Tell the user so plainly."})\n'),
], "85e agent: remember from a limited login writes nothing shared, and a failed store is not reported as remembered")
edit("agent.py", "room list leaves out outdoor zones and stairs too", [
    ('                if room.get("type") != "door" and room.get("name") and room["name"] not in names:\n'
     '                    names.append(room["name"])\n',
     '                # Jarvis Home patch (2026-11-07): the panel\'s room list leaves out outdoor zones and stairs too, and case and\n'
     '                # spaces do not make another room\n'
     '                if (room.get("type") not in ("door", "outdoor", "stairs") and str(room.get("name") or "").strip()\n'
     '                        and room["name"].strip().casefold() not in [n.casefold() for n in names]):\n'
     '                    names.append(room["name"].strip())\n'),
    ('    labels = [a.name for a in _ar.async_get(hass).async_list_areas() if a.name not in rooms]\n',
     '    _jh_rooms = {r.strip().casefold() for r in rooms}\n'
     '    labels = [a.name for a in _ar.async_get(hass).async_list_areas() if a.name.strip().casefold() not in _jh_rooms]\n'),
], "85f agent: floor-plan rooms match Home Assistant areas whatever the case, outdoor zones are not rooms")

# 86 -----------------------------------------------------------------------------------------
# Audit 10 (2026-11-07), briefings that are read aloud.
WORKDAY86 = r'''def _jh_workday_calendar(hass, entity_id: str) -> bool:
    """Jarvis Home patch (2026-11-07): the Workday integration's calendar marks working days and holds no events to read out.
    Known by the integration that made it, never by a word in an event title, so "Workday deadline" in a real calendar stays."""
    try:
        from homeassistant.helpers import entity_registry as er
        return getattr(er.async_get(hass).async_get(entity_id), "platform", None) == "workday"
    except Exception:
        return False


'''
edit("briefing.py", "_jh_workday_calendar", [
    ('def _gather_open_things(hass: HomeAssistant) -> list[str]:\n',
     lambda o: WORKDAY86 + 'def _gather_open_things(hass: HomeAssistant, plain: bool = False) -> list[str]:\n'),
    ('            if name in controllable_names:\n',
     '            if name in controllable_names or plain:   # Jarvis Home patch (2026-11-07): plain facts carry no note for a model\n'),
    ('    open_things = _gather_open_things(hass)\n', '    open_things = _gather_open_things(hass, plain=_jh_facts_only_on(hass))\n'),
    ('        for ts, detail in rows:\n            events.append(f"{ts[11:16]}: {detail}")\n',
     '        for ts, detail in rows:\n'
     '            try:   # Jarvis Home patch (2026-11-07): stored in UTC, spoken in the home\'s local time\n'
     '                d = datetime.fromisoformat(str(ts))\n'
     '                d = d if d.tzinfo else d.replace(tzinfo=timezone.utc)\n'
     '                loc = dt_util.as_local(d)\n'
     '                ts = (loc if isinstance(loc, datetime) else d.astimezone()).isoformat()\n'
     '            except Exception:\n'
     '                pass\n'
     '            events.append(f"{ts[11:16]}: {detail}")\n'),
    ('        if msg and start and "workday" not in msg.lower():\n',
     '        if msg and start and not _jh_workday_calendar(hass, state.entity_id):\n'),
], "86a briefing: facts-only briefings read no model notes, event times in local time, no fixed word filter on events")
edit("proactive_briefing.py", "_gather_open_things(hass, plain=True)", [
    ('        open_things = _gather_open_things(hass)\n', '        open_things = _gather_open_things(hass, plain=True)   # read aloud as it stands\n'),
], "86b proactive_briefing: arrival and security briefings read no model notes aloud")

# 87 -----------------------------------------------------------------------------------------
# Audit 10 (2026-11-07): what the original JARVIS left in www, served at /local with no login, leaves it at startup.
MOVE87 = r'''def _jh_faces_out_of_www() -> None:
    """Jarvis Home patch (2026-11-07): the original JARVIS kept face photos in www/jarvis/faces, which Home Assistant serves at
    /local with no login, under names anyone can guess. At startup they move into the private folder, every saved pin follows
    its photo, and the public folder is removed once it is empty. Nothing is deleted. Runs on the executor; never raises."""
    global _face_snap_dir
    try:
        old = config_path_str("www", "jarvis", "faces")
        if not os.path.isdir(old):
            return
        import shutil
        new = config_path_str("jarvis", "face_snaps")
        os.makedirs(new, exist_ok=True)
        moved = {}
        for name in sorted(os.listdir(old)):
            src = os.path.join(old, name)
            if not os.path.isfile(src):
                continue
            stem, ext = os.path.splitext(name)
            dst, n = os.path.join(new, name), 1
            while os.path.exists(dst):   # a newer private photo has this name: keep both
                dst, n = os.path.join(new, f"{stem}_www{n}{ext}"), n + 1
            shutil.move(src, dst)
            moved[name] = dst
        if moved and os.path.exists(RECOG_CACHE_PATH):
            with open(RECOG_CACHE_PATH) as f:
                data = json.load(f)
            snaps = data.get("snapshots") if isinstance(data, dict) else None
            changed = False
            for snap in (snaps.values() if isinstance(snaps, dict) else ()):
                if not isinstance(snap, dict) or not str(snap.get("url", "")).startswith("/local/jarvis/faces/"):
                    continue
                base = os.path.basename(str(snap.get("path") or snap.get("url") or ""))
                if base in moved:
                    snap["url"] = f"{FACE_SNAPSHOT_URL_BASE}/{os.path.basename(moved[base])}"
                    snap["path"] = moved[base]
                    changed = True
            if changed:
                _persist_write(data)
        _face_snap_dir = _face_snap_dir or new
        try:
            os.rmdir(old)
        except OSError:
            pass
        if moved:
            _LOGGER.info("JARVIS: moved %d face photo(s) out of the public www folder into %s", len(moved), new)
    except Exception as exc:
        _LOGGER.warning("JARVIS: could not move old face photos out of the public www folder: %s", exc)


'''
edit("recognition.py", "_jh_faces_out_of_www", [
    ('def warm_start() -> None:\n', lambda o: MOVE87 + o),
    ('    _persist_load()\n\n\ndef _normalize_score', '    _jh_faces_out_of_www()\n    _persist_load()\n\n\ndef _normalize_score'),
    ('        if url.startswith(FACE_SNAPSHOT_URL_BASE + "/"):\n',
     '        if url.startswith((FACE_SNAPSHOT_URL_BASE + "/", "/local/jarvis/faces/")):   # an old row finds its moved photo\n'),
], "87a recognition: face photos the original left in www move to the private folder at startup, pins follow them")
RETOKEN87 = r'''def retoken_old_stills() -> None:
    """Jarvis Home patch (2026-11-07): stills saved before the unguessable names (tag_camera_epoch.jpg) are still served from
    www with no login. Each gets a random part as well, and the intrusion log points at the new name. Nothing is deleted.
    Runs on the executor at startup; never raises."""
    import re, secrets
    try:
        folder = config_path_str("www", "jarvis", "intrusion")
        if not os.path.isdir(folder):
            return
        renamed = {}
        for name in os.listdir(folder):
            if not re.fullmatch(r".+_\d{9,11}\.jpg", name) or re.fullmatch(r".+_\d{9,11}_[A-Za-z0-9_-]{22}\.jpg", name):
                continue
            new = f"{name[:-4]}_{secrets.token_urlsafe(16)}.jpg"
            os.replace(os.path.join(folder, name), os.path.join(folder, new))
            renamed[name] = new
        if not renamed:
            return
        for ev in _load_log():
            if not isinstance(ev, dict):
                continue
            for key in ("snapshot_url", "snapshot_path"):
                val = str(ev.get(key) or "")
                base = val.replace("\\", "/").rsplit("/", 1)[-1]
                if base in renamed:
                    ev[key] = val[:len(val) - len(base)] + renamed[base]
        _save_log()
        _LOGGER.info("JARVIS: gave %d older intrusion still(s) an unguessable name", len(renamed))
    except Exception as exc:
        _LOGGER.warning("JARVIS: could not rename older intrusion stills: %s", exc)


'''
edit("intrusion.py", "def retoken_old_stills", [
    ('def last_snapshot() -> Optional[dict]:\n', lambda o: RETOKEN87 + o),
], "87b intrusion: stills from before the unguessable names get one too, the log follows")
edit("__init__.py", "intrusion.retoken_old_stills", [
    ('        _LOGGER.debug("JARVIS recognition cache warm-start failed (non-fatal): %s", exc)\n',
     lambda o: o + '    try:   # Jarvis Home patch (2026-11-07): older intrusion stills in www get unguessable names too\n'
                   '        from . import intrusion as _jh_intrusion\n'
                   '        await hass.async_add_executor_job(_jh_intrusion.retoken_old_stills)\n'
                   '    except Exception as exc:\n'
                   '        _LOGGER.debug("JARVIS intrusion still rename failed (non-fatal): %s", exc)\n'),
], "87c __init__: rename older intrusion stills at startup")

# 88 -----------------------------------------------------------------------------------------
# Audit 10 (2026-11-07): each voice session gets a new conversation id, so voice with no login is kept per satellite.
edit("conversation.py", "_jh_dev", [
    ('    async def _maybe_seed_history(self, cid: str, history: list, user_id: str = "") -> None:\n',
     '    async def _maybe_seed_history(self, cid: str, history: list, user_id: str = "", device_id: str = "") -> None:\n'),
    ('        seeded = await memory_thread.load_recent(self.hass, hours, limit, user_id=user_id, device_id=cid)\n',
     '        seeded = await memory_thread.load_recent(self.hass, hours, limit, user_id=user_id, device_id=device_id or cid)\n'),
    ('        await self._maybe_seed_history(cid, history, _jh_uid)\n',
     '        # Jarvis Home patch (2026-11-07): voice with no login is stored and seeded per satellite. Each voice session has a new\n'
     '        # conversation id, so turns kept under that id were never found again. A login keeps its own rows, as before.\n'
     '        _jh_dev = cid if _jh_uid else (getattr(user_input, "device_id", None) or cid)\n'
     '        await self._maybe_seed_history(cid, history, _jh_uid, _jh_dev)\n'),
    ('        save_message("user", user_input.text, device_id=cid, user_id=_jh_uid)\n',
     '        save_message("user", user_input.text, device_id=_jh_dev, user_id=_jh_uid)\n'),
    ('                    save_message, "assistant", response_text, cid, _jh_uid,\n',
     '                    save_message, "assistant", response_text, _jh_dev, _jh_uid,\n'),
], "88 conversation: voice with no login finds its earlier turns on the same satellite")

# 89 -----------------------------------------------------------------------------------------
# Audit 10 (2026-11-07), websocket.py.
WS89 = r'''def _jh_cam_default(hass, entry) -> str:
    """Jarvis Home patch (2026-11-07): 'Last camera used' is saved from the panel as an empty value. That choice must win over
    a camera picked earlier in the Core options, so an empty value set in the panel counts here instead of being skipped."""
    try:
        from . import jarvis_config
        data = hass.data.get(DOMAIN, {}).get(getattr(entry, "entry_id", None), {}) if entry is not None else {}
        rc = data.get("runtime_config", {}) if isinstance(data, dict) else {}
        if "camera_watch_default" in rc:
            return str(rc["camera_watch_default"] or "")
        stored = jarvis_config.get("camera_watch_default", None)
        if stored is not None:
            return str(stored or "")
    except Exception:
        pass
    return str(_runtime_opt(hass, entry, "camera_watch_default", "") or "")


def _jh_hide_addresses(res):
    """Jarvis Home patch (2026-11-07): the health check names the LLM host and port, which get_panel_data hides from
    non-admins. For them the address is dropped and blanked out of the details."""
    import copy, re
    if not isinstance(res, dict):
        return res
    out = copy.deepcopy(res)
    pat = re.compile(r"[a-z][a-z0-9+.-]*://[^\s,;)'\"]+|\('[^']*',\s*\d{1,5}\)|\[[0-9a-f:]+\](?::\d+)?"
                     r"|\b(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?\b|\b[a-z][\w-]*(?:\.[\w-]+)*:\d{2,5}\b", re.I)
    for svc in out.get("services") or []:
        if isinstance(svc, dict):
            svc.pop("base", None)
            if isinstance(svc.get("detail"), str):
                svc["detail"] = pat.sub("(address hidden)", svc["detail"])
    return out


'''
edit("websocket.py", "def _jh_cam_default", [
    ('def _honorific_payload(value) -> str:\n', lambda o: WS89 + o),
    ('"camera_watch_default": str(_runtime_opt(hass, entry, "camera_watch_default", "") or ""),\n',
     '"camera_watch_default": _jh_cam_default(hass, entry),\n'),
    ('        res = await diagnostics.run_service_health(hass)\n        connection.send_result(msg["id"], res)\n',
     '        res = await diagnostics.run_service_health(hass)\n'
     '        if not _is_admin(connection):\n'
     '            res = _jh_hide_addresses(res)   # connection details are for admins\n'
     '        connection.send_result(msg["id"], res)\n'),
    ('            res = await hazard_monitor.status(hass)\n        connection.send_result(msg["id"], res)\n',
     '            res = await hazard_monitor.status(hass)\n'
     '        if not _is_admin(connection) and isinstance(res, dict):\n'
     '            res = {**res, "center": None}   # Jarvis Home patch (2026-11-07): hidden from non-admins, as in get_panel_data\n'
     '        connection.send_result(msg["id"], res)\n'),
], "89 websocket: 'Last camera used' sticks, diagnostics and hazard keep addresses and coordinates for admins")

# 90 -----------------------------------------------------------------------------------------
edit("camera.py", "an offline camera raises here", [
    ('        _LOGGER.error("JARVIS: snapshot retry failed for %s: %s", entity_id, exc)\n',
     '        # Jarvis Home patch (2026-11-07): an offline camera raises here. Hold it like a blank one, so panel polls stop waking\n'
     '        # it, and say so once per outage instead of an error on every poll.\n'
     '        _jh_log = _LOGGER.debug if entity_id in _JH_BLANK else _LOGGER.warning\n'
     '        _JH_BLANK[entity_id] = _jh_time.monotonic()\n'
     '        _jh_log("JARVIS: snapshot retry failed for %s: %s", entity_id, exc)\n'),
], "90 camera: an offline camera backs off like a blank one and is logged once")

# 91 -----------------------------------------------------------------------------------------
edit("cognitive_core.py", "a home with no alarm panel at all", [
    ('    if indeterminate:\n        _log_alarm_indeterminate(reason, lockdown_active=mgr.active)\n',
     '    if indeterminate:\n'
     '        # Jarvis Home patch (2026-11-07): a home with no alarm panel at all has no alarm fault. Still hold (a panel may load\n'
     '        # later), but only a panel that exists and cannot be read is a SAFETY warning.\n'
     '        if _CORE.hass.states.async_all("alarm_control_panel"):\n'
     '            _log_alarm_indeterminate(reason, lockdown_active=mgr.active)\n'
     '        else:\n'
     '            _LOGGER.debug("Lockdown: no alarm panel in Home Assistant (%s), holding lockdown state", reason)\n'),
], "91 cognitive_core: no SAFETY warning about an alarm panel the home does not have")

# 94 -----------------------------------------------------------------------------------------
# Audit 10 round 2 (2026-11-07), Python. Every step has its own new marker and its anchors are the text 84-91 left, so an
# install that already has 84-91 gets only these, and a clean release gets 84-91 and then these.
# a: "alarm" is a kind of device again in every home, also one without an alarm panel. An alarm for a time and an alarm clock
#    are not the alarm system. Arm and disarm are orders.
WAKE94 = r'''# Jarvis Home patch (2026-11-07, round 2): an alarm for a time is a wake-up alarm or a reminder, and an alarm clock is a clock.
# Neither is the alarm system, so they are taken out before the device words are read; "switch on the alarm" stays a command.
_JH_WAKE_ALARM_RE = re.compile(r"\balarms?\s+clocks?\b|\balarms?\s+(?:for|at|in|om|um|to)\s+(?:the\s+)?"
                               r"(?:\d|half|quarter|noon|midnight|tomorrow|morning|one|two|three|four|five|six|seven|eight|nine|ten"
                               r"|eleven|twelve|fifteen|twenty|thirty|forty|fifty|een|twee|drie|vier|vijf|zes|zeven|acht|negen|tien"
                               r"|elf|twaalf|kwart|morgen|ochtend|eins|zwei|sechs|sieben|neun|zehn|zwölf|halb|viertel|früh)")
'''
edit("local_engine.py", "_JH_WAKE_ALARM_RE", [
    ('                         r"|doors?|gates?|garage|windows?|fans?|locks?|plugs?|sockets?|speakers?|music|vacuum"\n',
     '                         r"|doors?|gates?|garage|windows?|fans?|locks?|plugs?|sockets?|speakers?|music|vacuum|alarms?"\n'),
    ('_JH_WARMTH_RE = re.compile(', lambda o: WAKE94 + o),
    ('    if _JH_KIND_RE.search(body):\n        return True\n'
     '    # Jarvis Home patch (2026-11-07): "alarm" is a device only in a home with an alarm panel, and "an alarm for 7" is a wake-up\n'
     '    # time, not the panel. A reminder or timer set for it is not a device command.\n'
     '    if (re.search(r"\\balarm\\b", body) and not re.search(r"\\balarm\\s+(?:for|at|in|om|um)\\b", body)\n'
     '            and hass.states.async_all("alarm_control_panel")):\n'
     '        return True\n',
     '    # Jarvis Home patch (2026-11-07): "an alarm for 7" is a wake-up time. A reminder or timer set for it is not a device command,\n'
     '    # so device_command takes those words out first (round 2). An "alarm" that is left is the alarm system, in every home.\n'
     '    if _JH_KIND_RE.search(body):\n        return True\n'),
    ('        body = _JH_TAIL_RE.sub("", body)\n    if not body:\n        return False\n',
     '        body = _JH_TAIL_RE.sub("", body)\n'
     '    body = " ".join(_JH_WAKE_ALARM_RE.sub(" ", body).split())   # Jarvis Home patch (2026-11-07, round 2): no wake-up alarm\n'
     '    if not body:\n        return False\n'),
    ('_JH_ORDER_VERBS = frozenset(("turn switch set ', '_JH_ORDER_VERBS = frozenset(("arm disarm turn switch set '),
], "94a local_engine: the alarm is a kind of device in every home; an alarm for a time or an alarm clock is not one")
# b: a vague room word swaps one device for another at once only between two lights or two heaters, never by domain alone: a
#    lamp plug, a heater plug and the amplifier plug are all switches.
SAMEKIND94 = r'''def _jh_same_kind(hass, a, b):
    """Jarvis Home patch (2026-11-07, round 2): two lights, or two heaters (a climate device, or a switch _jh_heaters counts).
    Only these may stand in for each other when a vague room word is said. The domain alone says nothing: a lamp plug, a heater
    plug and the amplifier plug are all switches."""
    if a.split(".")[0] == "light" and b.split(".")[0] == "light":
        return True
    switches, climates = _jh_heaters(hass)
    heat = {s.entity_id for s in switches} | {s.entity_id for s in climates}
    return a in heat and b in heat


'''
edit("local_engine.py", "def _jh_same_kind(hass, a, b):", [
    ('def _find_entity(hass, name_fragment, domain_hint=None, learned_aliases=None):\n', lambda o: SAMEKIND94 + o),
    ('                        x[1] in _jh_dead and x[0].split(".")[0] != _jh_fit[0][0].split(".")[0] for x in _jh_many):\n',
     '                        x[1] in _jh_dead and not _jh_same_kind(hass, x[0], _jh_fit[0][0]) for x in _jh_many):\n'),
    ('                if len(_jh_fit) == 1 and action == "turn_on" and _jh_fit[0][0].split(".")[0] != resolved[0].split(".")[0]:\n',
     '                # Jarvis Home patch (2026-11-07, round 2): by kind, not by domain. Two lights or two heaters act at once.\n'
     '                if len(_jh_fit) == 1 and action == "turn_on" and _jh_fit[0][0] != resolved[0] and not _jh_same_kind(hass, _jh_fit[0][0], resolved[0]):\n'),
], "94b local_engine: a vague room word switches on another device at once only when both are lights or both are heaters")
# c: offline, a device that has exactly the name said but cannot do the action is named, instead of "no device matching";
#    'which one' drops unavailable sound-alikes by entity id, so a live device with the same name stays.
WRONG94 = r'''_JH_TYPE_WORDS = {"light": "a light", "switch": "a switch", "lock": "a lock", "cover": "a cover", "climate": "a climate device",
                  "fan": "a fan", "media_player": "a media player"}
_JH_DO_WORDS = {"open": "open it", "close": "close it", "lock": "lock it", "unlock": "unlock it", "dim": "dim it",
                "brighten": "brighten it", "set_temp_named": "set its temperature"}


def _jh_wrong_type_line(fname, domain, action, honorific):
    """Jarvis Home patch (2026-11-07, round 2): the honest offline line for a device that has exactly the name said but cannot do
    what was asked, a lock asked to open. It names that device instead of saying that no such device exists."""
    kind = _JH_TYPE_WORDS.get(domain) or f"a {domain.replace('_', ' ')}"
    do = _JH_DO_WORDS.get(action) or f"{action.replace('_', ' ')} it"
    return f"{fname} is {kind}, {honorific}, so I can't {do}. Nothing was changed."


'''
edit("local_engine.py", "def _jh_wrong_type_line(", [
    ('async def try_local(hass, text, honorific="sir", force=False, pending_key=""):\n', lambda o: WRONG94 + o),
    ('    _last_failed_name = None  # Track for end-of-loop error\n',
     lambda o: o + '    _jh_wrong_type = None   # Jarvis Home patch (2026-11-07, round 2): a device of exactly that name that cannot do it\n'),
    ('            # say so, so it would be reported done. Not this device: try the next pattern, then the agent.\n'
     '            _last_failed_name = name_frag\n',
     lambda o: o + '            if str(fname).lower().replace("colour", "color") in (_jh_said_name(name_frag), _jh_said_name(_jh_said)):\n'
                   '                _jh_wrong_type = (fname, entity_id.split(".")[0], action)\n'),
    ('    if _last_failed_name:\n        if force:\n',
     lambda o: '    if force and _jh_wrong_type:\n'
               '        return LocalResult(text=_jh_wrong_type_line(*_jh_wrong_type, honorific), success=False)\n' + o),
    ('                _jh_ears = [x for x in _jh_ears if x[1] not in _jh_gone]\n',
     '                # Jarvis Home patch (2026-11-07, round 2): by entity id, a live device may share its name with one that is gone\n'
     '                _jh_ears = [x for x in _jh_ears if getattr(hass.states.get(x[0]), "state", "unavailable") != "unavailable"]\n'),
    ('            if box is not None and name not in [x[1] for x in box]:\n                box.append((st.entity_id, name))\n',
     '            if box is not None and name not in [x[1] for x in box]:\n                box.append((st.entity_id, name))\n'
     '            elif box is not None and st.state != "unavailable":\n'
     '                # Jarvis Home patch (2026-11-07, round 2): one name, two devices: the live one is kept, not the one that is gone\n'
     '                box[:] = [(st.entity_id, name) if x[1] == name and getattr(hass.states.get(x[0]), "state", "unavailable") == "unavailable"\n'
     '                          else x for x in box]\n'),
], "94c local_engine: offline, a device of the wrong type is named; unavailable sound-alikes are dropped by entity id")
# d: the claim check also knows "Turning the heater on now", Dutch claims and Dutch closings, and a clause that opens with a
#    question word is the question ("Set to which temperature, sir?").
CLOSING94_OLD = r'''_JH_CLOSING_RE = _re.compile(r"^\W*(?:(?:and|also|so)\W+)?(?:sir\W+)?(?:how (?:may|can) i|what (?:else )?can i|let me know|anything else"
                             r"|is there anything|feel free|(?:would you like|do you need|need) anything)", _re.I)
'''
CLOSING94 = r'''_JH_CLOSING_RE = _re.compile(r"^\W*(?:(?:and|also|so|en|ook)\W+)?(?:(?:sir|meneer|mevrouw)\W+)?(?:how (?:may|can) i|what (?:else )?can i"
                             r"|let me know|anything else|is there anything|feel free|(?:would you like|do you need|need) anything"
                             # Jarvis Home patch (2026-11-07, round 2): Dutch closings
                             r"|(?:verder |anders )?nog iets|kan ik (?:je |u )?(?:nog |verder )?(?:iets|ergens|mee helpen|helpen)"
                             r"|laat (?:het )?(?:me|maar) weten)", _re.I)
'''
CLAIM94_OLD = r'''_JH_CLAIM_RE = _re.compile(r"\b(?:done|switched|turned|set|locked|unlocked|opened|closed|dimmed|activated|started|stopped|changed"
                           r"|(?:is|are)\s+(?:now\s+)?(?:on|off|open|closed|locked|unlocked))\b", _re.I)
'''
CLAIM94 = r'''_JH_CLAIM_RE = _re.compile(r"\b(?:done|switched|turned|set|locked|unlocked|opened|closed|dimmed|activated|started|stopped|changed"
                           r"|(?:is|are)\s+(?:now\s+)?(?:on|off|open|closed|locked|unlocked)"
                           # Jarvis Home patch (2026-11-07, round 2): "Turning the heater on now" claims as much, and so does Dutch
                           r"|turning|switching|opening|closing|locking|unlocking|dimming|brightening|setting|starting|stopping"
                           r"|activating|deactivating|arming|disarming|raising|lowering|adjusting|changing"
                           r"|(?:staat|staan|gaat|gaan)\s+(?:nu\s+)?(?:aan|uit|open|dicht)|(?:aan|uit)gezet|(?:aan|uit)gedaan"
                           r"|(?:in|uit)geschakeld|geopend|gesloten|vergrendeld|ontgrendeld|gedimd|gedaan|geregeld|op slot)\b", _re.I)
'''
ASKS94_OLD = r'''    parts = _re.split(r"[,:]\s*", first)
    if len(parts) < 2:
        return True
    return not (_JH_CLOSING_RE.search(parts[-1]) or _JH_CLAIM_RE.search(" ".join(parts[:-1])))
'''
ASKS94 = r'''    parts = _re.split(r"[,:]\s*", first)
    if len(parts) < 2:
        return True
    # Jarvis Home patch (2026-11-07, round 2): a clause that opens with a question word is the question, so a claim word inside it
    # claims nothing ("Set to which temperature, sir?"). A claim before it still claims.
    ask = next((i for i, p in enumerate(parts) if _JH_QWORD_RE.search(" ".join(p.split()[:3]))), len(parts) - 1)
    return not (_JH_CLOSING_RE.search(parts[-1]) or _JH_CLAIM_RE.search(" ".join(parts[:ask])))
'''
QWORD94 = r'''_JH_QWORD_RE = _re.compile(r"\b(?:which|what|how|when|where|who|whom|whose|why|welke|welk|wat|hoeveel|hoe|wanneer|waar|wie"
                           r"|waarom)\b", _re.I)
'''
edit("agent.py", "_JH_QWORD_RE = _re.compile(", [
    (CLOSING94_OLD, CLOSING94),
    (CLAIM94_OLD, lambda o: CLAIM94 + QWORD94),
    (ASKS94_OLD, ASKS94),
], "94d agent: -ing claims, Dutch claims and closings are claims; a clause that opens with a question word is a question")
# e: a device that was still moving keeps the state that was read. A reply that says so stands; a claim that it is done is
#    replaced with a line that names the state.
MOVING94 = r'''# Jarvis Home patch (2026-11-07, round 2): only for a device that was read while still moving: what says it has got there.
# "is opening" does not, "is open" does.
_JH_ARRIVED_RE = _re.compile(r"\b(?:done|finished|complete|opened|closed|shut|locked|unlocked|secured"
                             r"|(?:is|are|now)\s+(?:now\s+)?(?:fully\s+)?(?:open|closed|shut|locked|unlocked|secure)"
                             r"|geopend|gesloten|dicht|vergrendeld|ontgrendeld|op slot)\b", _re.I)


def _jh_moving_ok(first: str, st: dict) -> bool:
    """Every failure is a device still on its way (opening, locking), and the reply names that state and no end state."""
    bad = _jh_failures(st)
    if not bad or any(b["kind"] != "moving" for b in bad) or _JH_ARRIVED_RE.search(first):
        return False
    return all(_re.search(r"\b" + _re.escape(b["state"]) + r"\b", first, _re.I) for b in bad)


def _jh_moving_line(st: dict, honorific: str) -> str:
    """The line for devices that were still moving when they were read: the name and the state that was read."""
    moving = [f"{b['what']} is still {b['state']}" for b in _jh_failures(st) if b["kind"] == "moving"]
    line = _jh_join(moving)
    return f"{line[:1].upper()}{line[1:]}, {honorific}; {'it has' if len(moving) == 1 else 'they have'} not confirmed yet."


'''
edit("agent.py", "def _jh_moving_ok(first: str, st: dict) -> bool:", [
    ('        if data.get("confirmed") is False and data.get("sent"):\n            bad("unconfirmed")\n            return\n',
     '        if data.get("confirmed") is False and data.get("sent"):\n            bad("unconfirmed")\n'
     '            if data.get("in_progress") and str(data.get("new_state") or "").lower() in _TRANSITIONAL:\n'
     '                # Jarvis Home patch (2026-11-07, round 2): the state that was read stays with it, so "is opening" can stand\n'
     '                st["bad"][key].update(kind="moving", state=str(data["new_state"]).lower())\n'
     '            return\n'),
    ('    parts = ([f"{_jh_join(gone)} did not go through"] if gone else []) + ([f"{_jh_join(silent)} has not confirmed it"] if silent else [])\n',
     lambda o: o + '    parts += [f"{b[\'what\']} is still {b[\'state\']}" for b in _jh_failures(st) if b["kind"] == "moving"]   # round 2\n'),
    ('def _jh_problem(text: str, st: dict, cmd: bool, asked: bool = False) -> Optional[str]:\n', lambda o: MOVING94 + o),
    ('    if _jh_failures(st):\n        return "failed"\n',
     '    if _jh_failures(st):\n'
     '        if not _jh_moving_ok(first, st):\n'
     '            return "failed"\n'
     '        if not st["waiting"]:\n'
     '            return None   # Jarvis Home patch (2026-11-07, round 2): it says the device is still on its way, as was read\n'),
    ('        kinds = {b["kind"] for b in _jh_failures(st)}\n',
     lambda o: o + '        if kinds == {"moving"}:\n            return _jh_moving_line(st, honorific)\n'),
], "94e agent: a device still opening or locking keeps that state; a reply that says so stands, a done claim names the state")
# f: voice with no login is seeded per satellite (88), and a satellite is shared, so it gets a short window only.
edit("conversation.py", "_JH_SAT_SEED_TURNS", [
    ('MAX_HISTORY  = 20   # messages kept in per-conversation context window\n',
     lambda o: o + '# Jarvis Home patch (2026-11-07, round 2): voice with no login is seeded per satellite, and a satellite is shared, so the\n'
                   '# seed is a short conversation window only: one person\'s requests do not reach the next person. A login is unchanged.\n'
                   '_JH_SAT_SEED_TURNS = 6\n_JH_SAT_SEED_MINUTES = 10\n'),
    ('        seeded = await memory_thread.load_recent(self.hass, hours, limit, user_id=user_id, device_id=device_id or cid)\n',
     '        if not user_id and device_id and device_id != cid:   # a satellite: the last few turns of the last few minutes\n'
     '            hours, limit = min(hours, _JH_SAT_SEED_MINUTES / 60), min(limit, _JH_SAT_SEED_TURNS)\n'
     '        seeded = await memory_thread.load_recent(self.hass, hours, limit, user_id=user_id, device_id=device_id or cid)\n'),
], "94f conversation: voice with no login on a satellite is seeded with the last 6 turns of the last 10 minutes only")
# g: 'Last camera used' is saved as "last". An empty value keeps its old meaning: not set here, the next place decides.
CAM94 = r'''def _jh_cam_default(hass, entry) -> str:
    """Jarvis Home patch (2026-11-07, round 2): 'Last camera used' is saved from the panel as "last", an explicit choice that wins
    over a camera picked in the Core options. An empty value keeps its old meaning: not set there, so the next place decides
    (runtime config, then config.json, then the Core options)."""
    return str(_runtime_opt(hass, entry, "camera_watch_default", "") or "")
'''
edit("websocket.py", 'saved from the panel as "last"', [
    (between("websocket.py", "def _jh_cam_default(hass, entry) -> str:\n", "\n\ndef _jh_hide_addresses(res):\n"), CAM94),
], "94g websocket: 'Last camera used' is the explicit value last, an empty value falls through as before")
edit("config_flow.py", "which is no camera", [
    ('            vol.Optional("camera_watch_default", description=self._sv("camera_watch_default", "")):\n',
     '            # Jarvis Home patch (2026-11-07, round 2): \'Last camera used\' is stored as "last", which is no camera, so the field\n'
     '            # stays empty instead of failing the form\n'
     '            vol.Optional("camera_watch_default", description=self._sv("camera_watch_default", "")\n'
     '                         if "." in str(self._cur("camera_watch_default", "") or "") else {}):\n'),
], "94h config_flow: the Core camera field is left empty when 'Last camera used' is chosen")
edit("frontend/jarvis-panel.js", "['last', 'Last camera used']", [
    ("[['', 'Last camera used'], ", "[['last', 'Last camera used'], "),
], "94i panel: 'Last camera used' is saved as last")

# 95 -----------------------------------------------------------------------------------------
# Audit 10 round 2 (2026-11-07), Chat tab. Its own marker, and every anchor is in the panel both as patches 73 and 81 made it
# before audit 10 and as they make it now, so an install patched earlier gets this too.
#  - Hey JARVIS rests only while a message is typed: the box has the focus and holds text. The button says so, and it listens
#    again as soon as the box loses the focus or is emptied. Text left in a box that is not in use no longer makes it deaf.
#  - Spoken words that still wait to be sent are no typed draft: a second utterance joins them and both go as one message.
#  - CLEAN > YES and the red-cross cancel drop spoken words that wait to be sent (_chatCut, a generation counter).
SPOKEN95 = r"""  // Words that came from the microphone: shown in the box, then sent unless they were changed. A typed draft is never replaced
  // and never sent by itself (audit 2026-11-07): the words are added after it and wait for Send. While JARVIS is still answering
  // the words wait in the box and go as soon as the reply is in. Words that still wait to be sent are no typed draft (round 2):
  // the next utterance joins them and both go as one message. CLEAN and the red-cross cancel drop them (_spokenCut).
  _chatSpoken(text) {
    const box = this.shadowRoot?.getElementById("chat-input");
    const draft = (box ? box.value : this._chatDraft || "").trim();
    if (draft && draft === this._spokenText) text = draft + " " + text;
    else if (draft) {
      this._chatDraft = draft + " " + text;
      if (box) box.value = this._chatDraft;
      this._wakeSync();   // the run that heard these words has ended: Hey JARVIS listens again unless a message is typed
      return;
    }
    this._chatDraft = this._spokenText = text;
    if (box) box.value = text;
    this._spokenWait = (this._spokenWait || 0) + 1;
    this._wakeSync();
    const cut = this._chatCut || 0;
    const send = () => {
      const live = this._chatDraft === text && cut === (this._chatCut || 0);
      if (live && this._chatBusy && this.isConnected) { setTimeout(send, 300); return; }
      this._spokenWait--;
      if (this._spokenText === text) this._spokenText = null;
      if (live) this._sendChat(text);
    };
    setTimeout(send, 1200);
  }

  // CLEAN and the red-cross cancel: spoken words that still wait to be sent are dropped, from the box too. A typed draft stays.
  _spokenCut() {
    this._chatCut = (this._chatCut || 0) + 1;
    const text = this._spokenText;
    this._spokenText = null;
    if (!text || this._chatDraft !== text) return;
    this._chatDraft = "";
    const box = this.shadowRoot?.getElementById("chat-input");
    if (box) box.value = "";
  }

"""
WAKE95 = r"""  // Jarvis Home patch (2026-11-06): Hey JARVIS. While the switch is on and the panel is open the microphone feeds Home Assistant's
  // own pipeline, from wake word to speech-to-text; audio goes nowhere else. _wakeSync decides everything from the live state: it
  // is safe to call at any time and from anywhere. While the person talks by hand or JARVIS answers it lets go of the microphone
  // and looks again every half second, so JARVIS cannot wake itself. While a message is typed (the box has the focus and holds
  // text) it rests too, and its button says so; leaving or emptying the box ends that at once (audit 2026-11-07, round 2). A
  // focused empty box does not count, nor does text in a box that is not in use: the box keeps the focus after every reply.
  _wakeBusy() {
    const a = this._chatAudio;
    return !!(this._micHeld || this._micStop || this._micStarting || Date.now() < (this._micUntil || 0) || this._spokenWait
      || this._chatBusy || this._speakBusy || (a && !a.paused && !a.ended && !a.error));
  }

  _wakeTyping() {
    const box = this.shadowRoot?.getElementById("chat-input");
    return !!box && this.shadowRoot.activeElement === box && !!box.value.trim();
  }

"""
SYNC95 = r"""  _wakeSync() {
    const conn = this._hass?.connection;
    const rest = !!this._wakeOn && this._wakeTyping();
    if (rest !== !!this._wakeRest) {   // resting while a message is typed shows on the button; when it ends, listen at once
      this._wakeRest = rest;
      if (!rest) { clearTimeout(this._wakeTimer); this._wakeTimer = null; }
      this._wakeShow();
    }
    if (!this._wakeOn || !this.isConnected || !conn || conn.connected === false) { this._wakeHalt(); return; }
    this._wakeHook(conn);
    if (rest || this._wakeBusy()) {
      this._wakeHalt();
      this._wakeLater(500);
    } else if (!this._wakeRun && !this._wakeTimer) this._wakeStart(conn);
  }

"""
edit("frontend/jarvis-panel.js", "_spokenCut", [
    (between("frontend/jarvis-panel.js", "  // Words that came from the microphone: shown in the box", "  // Jarvis Home patch (2026-11-06): Hey JARVIS. While the switch"), SPOKEN95),
    (between("frontend/jarvis-panel.js", "  // Jarvis Home patch (2026-11-06): Hey JARVIS. While the switch", "  _wakeSync() {\n"), WAKE95),
    (between("frontend/jarvis-panel.js", "  _wakeSync() {\n", "  _wakeLater(ms) {\n"), SYNC95),
    ('      btn.classList.toggle("hot", !!this._wakeHot);\n',
     lambda o: o + '      btn.classList.toggle("rest", !!(this._wakeOn && this._wakeRest));\n'),
    ('        : this._wakeOn ? "Hey JARVIS is on: listening for the wake word. Click to turn it off"\n',
     lambda o: '        : this._wakeOn && this._wakeRest ? "Hey JARVIS is resting while you type. It listens again when you leave or empty the box. Click to turn it off"\n' + o),
    ('  .chat-wake.hot { color: #ff4d4d; border-color: #ff4d4d; }\n', lambda o: o + '  .chat-wake.on.rest { border-style: dashed; opacity: 0.55; }\n'),
    ('      chatInput.addEventListener("keydown", (e) => {\n',
     lambda o: '      // Hey JARVIS rests only while a message is typed, so it looks again when the box gets or loses the focus or changes\n'
               '      ["focus", "blur", "input"].forEach((n) => chatInput.addEventListener(n, () => { if (this._wakeOn) setTimeout(() => this._wakeSync(), 0); }));\n' + o),
    ('    this._chatBusy = false;\n    this._stopChatAudio();\n    this._chatWipe = true;\n',
     '    this._chatBusy = false;\n    this._spokenCut();   // spoken words still waiting to be sent go too\n    this._stopChatAudio();\n    this._chatWipe = true;\n'),
    ('      this._chatReq = (this._chatReq || 0) + 1;\n      this._chatBusy = false;\n    }\n',
     '      this._chatReq = (this._chatReq || 0) + 1;\n      this._chatBusy = false;\n      this._spokenCut();   // spoken words still waiting to be sent go too\n    }\n'),
], "95 panel: Hey JARVIS rests only while a message is typed and says so, quick spoken words go together, CLEAN and cancel drop waiting words")

# 96 -----------------------------------------------------------------------------------------
# Audit 10 round 3 (2026-11-07), panel. Two new steps with their own markers, anchored on upstream camera code and on the text 95
# left, so an install that already has 1-95 gets only these.
# a: a dark room is no broken camera. The Camera Watch chain (stream, stills, JARVIS snapshot) took a real but very dark picture for
#    a black Nest stream, ended on 'NO FRAME' and stayed on the JARVIS tier. A dark picture with detail in it is now remembered;
#    when the later tiers give nothing better the panel goes back to it, shows it with a small honest badge, and the badge goes
#    once the picture is light. Pure black frames, cameras that decode nothing and WebRTC-only cameras escalate as before.
# b: Hey JARVIS: a mouse click on Send (or another control of the bar) while it rests for typing starts no extra run, and its own
#    spoken words that wait to be sent do not count as typing.
CAM96 = r"""  // Jarvis Home patch (2026-11-07, round 3): a dark room is no broken camera. A tier that decodes a real picture that is only very
  // dark (dim on average, yet with detail in it) is remembered per camera. The chain still tries the next tiers, as it must for a
  // Nest stream that decodes pure black; when they give nothing better, the panel goes back to that dark picture, shows it with a
  // small badge and does not stay on the JARVIS tier, so the picture brightens as soon as a light goes on. A frame with no detail
  // at all, a camera that decodes nothing, and a WebRTC-only camera (Home Assistant lists no hls for it) escalate as before.
  _camFrameLook(img) {
    try {
      if (!img || !img.naturalWidth || !img.naturalHeight) return null;
      const w = Math.min(320, img.naturalWidth), h = Math.max(1, Math.round(w * img.naturalHeight / img.naturalWidth));
      const c = document.createElement("canvas");
      c.width = w; c.height = h;
      const ctx = c.getContext("2d");
      if (!ctx) return null;
      ctx.drawImage(img, 0, 0, w, h);
      const d = ctx.getImageData(0, 0, w, h).data;
      let sum = 0, top = 0;
      for (let i = 0; i < d.length; i += 4) {
        sum += (d[i] + d[i + 1] + d[i + 2]) / 3;
        top = Math.max(top, d[i], d[i + 1], d[i + 2]);
      }
      return { luma: sum / (d.length / 4), top };
    } catch (_) { return null; }
  }

  // "dark": a real but very dark picture, "bright": light enough, "black": nothing in it, null: no frame or it cannot be read
  _camLook(img) {
    const f = this._camFrameLook(img);
    if (!f) return null;
    return f.luma > 10 ? "bright" : f.top >= 8 ? "dark" : "black";
  }

  // Home Assistant's own camera capabilities, asked once per camera; while unknown the camera counts as not WebRTC-only
  _camWebrtcOnly(src) {
    const caps = this._camCaps || (this._camCaps = {});
    if (!(src in caps) && this._hass && this._hass.callWS) {
      caps[src] = null;
      Promise.resolve().then(() => this._hass.callWS({ type: "camera/capabilities", entity_id: src }))
        .then((r) => { caps[src] = r || {}; }, () => { caps[src] = {}; });
    }
    const t = (caps[src] && caps[src].frontend_stream_types) || [];
    return t.includes("web_rtc") && !t.includes("hls");
  }

  // The watchdog found a dim frame: a dark but real one is remembered. True when this camera already went back to its dark
  // picture once, so the chain is not walked again (a new stream token every few minutes arms the watchdog again).
  _camDarkNote(entity, img, mode) {
    const seen = this._camDarkSeen || (this._camDarkSeen = {});
    if (this._camLook(img) !== "dark" || this._camWebrtcOnly(this._camSource(entity))) return false;
    if (!seen[entity]) seen[entity] = { mode };
    else if (mode === "stream") seen[entity].mode = "stream";
    return !!seen[entity].back && this._camDarkBack(entity, mode);
  }

  // The later tiers gave nothing better: the dark picture again, live, with the badge
  _camDarkBack(entity, mode) {
    const seen = this._camDarkSeen && this._camDarkSeen[entity];
    if (!seen || entity !== this._activeCam || this._camWebrtcOnly(this._camSource(entity))) return false;
    const feed = this.shadowRoot?.getElementById("cam-feed");
    const img = feed && feed.querySelector("img");
    if (!img) return false;
    seen.back = true;
    mode = mode || seen.mode;
    delete this._camModeByEntity[entity];   // never stuck on the JARVIS tier
    if (this._camWsTimer) { clearInterval(this._camWsTimer); this._camWsTimer = null; }
    if (this._camWatchdog) { clearTimeout(this._camWatchdog); this._camWatchdog = null; }
    if (this._camStillTimer) { clearInterval(this._camStillTimer); this._camStillTimer = null; }
    feed.querySelector(".cam-none")?.remove();
    img.style.display = "";
    const src = this._camSource(entity), tok = this._camToken(src);
    this._camMode = mode;
    if (mode === "stream") {
      const url = tok ? `/api/camera_proxy_stream/${src}?token=${encodeURIComponent(tok)}` : `/api/camera_proxy_stream/${src}`;
      if (img.getAttribute("src") !== url) img.src = url;
    } else {
      const still = () => { img.src = `/api/camera_proxy/${src}?token=${encodeURIComponent(tok || "")}&_=${Date.now()}`; };
      still();
      this._camStillTimer = setInterval(() => { if (this._activeCam === entity) still(); }, 2000);
    }
    const strip = this.shadowRoot?.querySelector("#cam-strip");
    if (strip) strip.innerHTML = `<span>SRC <b>${this._esc(this._camName(entity))}</b></span><span>${mode === "stream" ? "MJPEG" : "STILLS"}</span>`;
    this._camDarkWatch(entity);
    return true;
  }

  // The badge follows the picture: shown while it is dark but real, gone once it is light enough or shows nothing at all
  _camDarkWatch(entity) {
    clearInterval(this._camDarkTimer);
    const tick = () => {
      const feed = this.shadowRoot?.getElementById("cam-feed");
      let badge = feed && feed.querySelector(".cam-dark");
      if (!this.isConnected || this._activeCam !== entity || !feed) {
        clearInterval(this._camDarkTimer); this._camDarkTimer = null;
        badge?.remove();
        return;
      }
      const pic = feed.querySelector("img");
      const dark = !!pic && pic.style.display !== "none" && this._camLook(pic) === "dark";
      if (dark && !badge) {
        badge = document.createElement("div");
        badge.className = "cam-dark";
        badge.dataset.cam = entity;
        badge.textContent = "VERY DARK — the camera works, but there is too little light in the room";
        feed.appendChild(badge);
      } else if (!dark && badge) badge.remove();
    };
    this._camDarkTimer = setInterval(tick, 2000);
    setTimeout(tick, 500);   // a stream that was attached again shows its first frame by then
    tick();
  }

"""
edit("frontend/jarvis-panel.js", "_camDarkBack", [
    ("  _camFallback(entity) {\n", lambda o: CAM96 + o),
    ("        if (luma === null || luma > 10) return;   // real (or unverifiable) frame\n      }\n",
     "        if (luma === null || luma > 10) return;   // real (or unverifiable) frame\n"
     "        if (this._camDarkNote(entity, img, expectMode)) return;   // Jarvis Home: a dark room seen before, the chain is not walked again\n"
     "      }\n"),
    ('    const hint = (text) => {\n      let d = feed.querySelector(".cam-none");\n',
     '    const hint = (text) => {\n'
     '      if (this._camDarkBack(entity)) return;   // Jarvis Home: nothing better than the dark picture seen before, so that is shown\n'
     '      let d = feed.querySelector(".cam-none");\n'),
    ("          tileLine += luma <= 10\n",
     "          tileLine += luma <= 10 && this._camLook(tileImg) === \"dark\"\n"
     "            ? `  <span class=\"cam-diag-bad\" style=\"color:#ffd166\">lum ${luma.toFixed(1)} — very dark picture (the camera works, but there is too little light)</span>`\n"
     "            : luma <= 10\n"),
    ('      feed.querySelector(".cam-none")?.remove();\n      let img = feed.querySelector("img");\n',
     lambda o: '      feed.querySelectorAll(".cam-dark").forEach((b) => { if (b.dataset.cam !== entity) b.remove(); });   // Jarvis Home: it was about another camera\n' + o),
    ("  .cam-strip b { color: var(--text); }\n",
     lambda o: o + "  .cam-dark { position: absolute; left: 50%; bottom: 10px; transform: translateX(-50%); z-index: 6; max-width: 92%; box-sizing: border-box;\n"
                   "    font-family: var(--font-mono); font-size: 10px; letter-spacing: 0.06em; color: #ffd166; text-align: center; pointer-events: none;\n"
                   "    background: rgba(8,12,20,0.72); padding: 4px 10px; border: 1px solid rgba(255,209,102,0.45); }\n"),
], "96a panel: a dark room shows its real picture with a VERY DARK badge instead of NO FRAME, and is not stuck on the JARVIS tier")
edit("frontend/jarvis-panel.js", "_wakeGrace", [
    ('  _wakeTyping() {\n    const box = this.shadowRoot?.getElementById("chat-input");\n'
     '    return !!box && this.shadowRoot.activeElement === box && !!box.value.trim();\n  }\n',
     '  // Round 3: Hey JARVIS\'s own words that wait to be sent are no typing. A click on Send or another control of the bar takes the\n'
     '  // focus from the box a moment before the click itself, so a rest gets a second of grace (_wakeGrace) instead of starting a\n'
     '  // new run that the send would stop again at once.\n'
     '  _wakeTyping() {\n    const box = this.shadowRoot?.getElementById("chat-input");\n'
     '    const text = box ? box.value.trim() : "";\n'
     '    if (!text || text === this._spokenText) return false;\n'
     '    return this.shadowRoot.activeElement === box || Date.now() < (this._wakeGrace || 0);\n  }\n'),
    ('      ["focus", "blur", "input"].forEach((n) => chatInput.addEventListener(n, () => { if (this._wakeOn) setTimeout(() => this._wakeSync(), 0); }));\n',
     lambda o: o + '      const wakeGrace = () => { if (this._wakeRest) this._wakeGrace = Date.now() + 1000; };   // a click in the bar ends no rest yet\n'
                   '      chatInput.addEventListener("blur", (e) => { if (e.relatedTarget && e.relatedTarget.closest && e.relatedTarget.closest(".chat-compose")) wakeGrace(); });\n'
                   '      chatInput.closest(".chat-compose")?.addEventListener("pointerdown", (e) => { if (e.target.closest && e.target.closest("button")) wakeGrace(); }, true);\n'),
], "96b panel: a click on Send while Hey JARVIS rests starts no extra run, its own waiting words are no typing")
# c (round 4): the dark picture comes back only for the walk down the chain that saw it. 96a went back to any camera that was ever
#    dark: a camera that was dark once and then broke made an endless stream / still / JARVIS loop (fast errors), stayed on a hanging
#    stream, or ended on a pure black stream. A walk counter (bumped when the feed is attached, and when the panel goes back) now says
#    which walk saw the dark frame; going back arms the watchdog again, a dark frame counts only while new frames keep coming, and no
#    tier switch happens more than once a second per camera. The badge says only what is seen ('VERY DARK — too little light to
#    see') and goes when no new frame came for 5 s (the MJPEG connection ended or the frame froze). Anchored on upstream camera code
#    and on the text 96a left, so an install that already has 1-97 gets only this.
CAM96C = r"""  // Jarvis Home patch (2026-11-07, round 4): the dark picture comes back only for the walk down the chain that saw it, and only
  // while new frames keep coming, so a camera that was dark once and then errors, hangs or decodes pure black ends on NO FRAME as
  // before. No tier switch happens more than once a second per camera: true here means the switch waits and then runs once, a
  // second after the last one, unless the feed was attached again in between.
  _camPace(entity, again) {
    const last = this._camSwitchAt || (this._camSwitchAt = {});
    const pend = this._camPaced || (this._camPaced = {});
    if (pend[entity] && pend[entity].walk === this._camWalk) return true;   // one already waits for this walk: it goes first
    if (pend[entity]) { clearTimeout(pend[entity].timer); delete pend[entity]; }
    const wait = (last[entity] || 0) + 1000 - Date.now();
    if (wait <= 0) { last[entity] = Date.now(); return false; }
    const walk = this._camWalk;
    pend[entity] = { walk, timer: setTimeout(() => {
      delete pend[entity];
      if (this.isConnected && this._activeCam === entity && this._camWalk === walk) again();
    }, wait) };
    return true;
  }

  // True while new frames keep coming: the picture changed within the last 5 s (its first look counts as new, it was decoded after
  // the feed was attached). An MJPEG stream that ended or froze keeps its last frame without any event, so the pixels are compared.
  _camFresh(img) {
    try {
      if (!img || !img.naturalWidth || !img.naturalHeight) return false;
      const w = Math.min(1280, img.naturalWidth), h = Math.max(1, Math.round(w * img.naturalHeight / img.naturalWidth));
      const c = this._camSigCanvas || (this._camSigCanvas = document.createElement("canvas"));
      if (c.width !== w || c.height !== h) { c.width = w; c.height = h; }
      const ctx = c.getContext("2d", { willReadFrequently: true });
      if (!ctx) return false;
      ctx.drawImage(img, 0, 0, w, h);
      const d = ctx.getImageData(0, 0, w, h).data;
      let sig = 2166136261;
      for (let i = 0; i < d.length; i += 4) sig = Math.imul(sig ^ (d[i] + 3 * d[i + 1] + 7 * d[i + 2]), 16777619);
      const now = Date.now();
      if (img._jhFrameSig !== sig) { img._jhFrameSig = sig; img._jhFrameAt = now; }
      return now - img._jhFrameAt < 5000;
    } catch (_) { return false; }
  }

"""
edit("frontend/jarvis-panel.js", "_camPace(", [
    ("  _camFallback(entity) {\n", lambda o: CAM96C + o),
    ('      this._lastCamKey = key;\n      this._camMode = "stream";\n',
     lambda o: o + "      this._camWalk = (this._camWalk || 0) + 1;   // Jarvis Home: a new walk down the chain (round 4)\n"),
    ('    if (entity !== this._activeCam) return;\n    this._camMode = "still";\n',
     '    if (entity !== this._activeCam) return;\n'
     '    if (this._camPace(entity, () => this._camFallback(entity))) return;   // Jarvis Home: at most one tier switch a second\n'
     '    this._camMode = "still";\n'),
    ('    if (entity !== this._activeCam) return;\n    this._camMode = "jarvis";\n',
     '    if (entity !== this._activeCam) return;\n'
     '    if (this._camPace(entity, () => this._camJarvisFallback(entity))) return;   // Jarvis Home: at most one tier switch a second\n'
     '    this._camMode = "jarvis";\n'),
    ('    if (this._camLook(img) !== "dark" || this._camWebrtcOnly(this._camSource(entity))) return false;\n'
     '    if (!seen[entity]) seen[entity] = { mode };\n'
     '    else if (mode === "stream") seen[entity].mode = "stream";\n'
     '    return !!seen[entity].back && this._camDarkBack(entity, mode);\n',
     '    // round 4: only a frame that is still live, and it is remembered for this walk down the chain alone\n'
     '    if (this._camLook(img) !== "dark" || !this._camFresh(img) || this._camWebrtcOnly(this._camSource(entity))) return false;\n'
     '    const s = seen[entity] || (seen[entity] = {});\n'
     '    if (s.walk !== this._camWalk || mode === "stream") s.mode = mode;   // the best tier that showed it in this walk\n'
     '    s.walk = this._camWalk;\n'
     '    return !!s.back && this._camDarkBack(entity, mode);\n'),
    ('    if (!seen || entity !== this._activeCam || this._camWebrtcOnly(this._camSource(entity))) return false;\n',
     '    // round 4: only when this walk down the chain saw the dark picture itself; one from an earlier walk proves nothing now\n'
     '    if (!seen || seen.walk !== this._camWalk || entity !== this._activeCam || this._camWebrtcOnly(this._camSource(entity))) return false;\n'),
    ('    if (!img) return false;\n    seen.back = true;\n',
     '    if (!img) return false;\n'
     '    if (this._camPace(entity, () => this._camDarkBack(entity, mode))) return true;   // at most one tier switch a second\n'
     '    this._camWalk = (this._camWalk || 0) + 1;   // a new walk: after a later failure it comes back only for a new dark frame\n'
     '    seen.back = true;\n'),
    ('    this._camDarkWatch(entity);\n    return true;\n  }\n',
     '    this._armCamWatchdog(entity, img, mode, mode === "stream" ? 6000 : 5000);   // round 4: a later failure still goes down the chain\n'
     '    this._camDarkWatch(entity);\n    return true;\n  }\n'),
    ('      const dark = !!pic && pic.style.display !== "none" && this._camLook(pic) === "dark";\n',
     '      const look = pic && pic.style.display !== "none" ? this._camLook(pic) : null;\n'
     '      const dark = look === "dark" && this._camFresh(pic);   // round 4: only while new frames keep coming\n'
     '      // dark again after a light spell (the watchdog ended then): a new one, so a later freeze or failure still goes down the chain\n'
     '      if (look === "dark" && !this._camWatchdog && (this._camMode === "stream" || this._camMode === "still"))\n'
     '        this._armCamWatchdog(entity, pic, this._camMode, this._camMode === "stream" ? 6000 : 5000);\n'),
    ('        badge.textContent = "VERY DARK — the camera works, but there is too little light in the room";\n',
     '        badge.textContent = "VERY DARK — too little light to see";   // round 4: only what is seen\n'),
    ("— very dark picture (the camera works, but there is too little light)</span>",
     "— very dark picture (too little light to see)</span>"),
], "96c panel: the dark picture comes back only for the walk that saw it and while frames keep coming; at most one tier switch a second")
# d (round 5): two review findings on 96c. 1: on the stills tier the watchdog and the badge judged a still while it was still
#    loading (no pixels yet), so a working dark camera whose stills take a second or more (a phone on mobile data, go2rtc waiting
#    for a keyframe) fell to NO FRAME and its badge blinked. A still is now judged only once it has loaded: the still watchdog waits
#    for the still that loads while the last one loaded within its window, and the badge goes by the last still that loaded. Stills
#    that fail (an error, or none loaded within the window) still go down the chain. 2: after any NO FRAME a camera never came back
#    in an open panel. The owner's rule (2026-11-08): while the tile shows NO FRAME, the normal chain is tried again about once a
#    minute, one try at a time and never faster than one tier switch a second; NO FRAME stays up while it is tried, frames that come
#    are shown (with the badge when dark), a camera that stays broken stays on NO FRAME. A late answer of the JARVIS tier, after the
#    tile left that tier, changes nothing. Anchored on upstream camera code and on the text 96a and 96c left, so an install that
#    already has 1-98 gets only this.
CAM96D = r"""  // Jarvis Home patch (2026-11-08, round 5): a still is judged only once it has loaded. The stills tier asks for the next still
  // every 2 s, and one can take a second or more (a phone on mobile data, go2rtc waiting for a keyframe); while it loads the picture
  // has no pixels to judge. The still watchdog waits for that still while the last one loaded within its window, and the badge goes
  // by the last still that loaded. A still that fails (an error, or none loaded within the window) still goes down the chain.
  _camLoadHook(img) {
    if (!img || img._jhLoadHook) return;
    img._jhLoadHook = true;
    img.addEventListener("load", () => {
      if (!img.naturalWidth) return;
      if (this._camMode === "still") {
        img._jhStillAt = Date.now();
        img._jhStillLook = this._camLook(img);
        if (img._jhStillLook === "dark") this._camFresh(img);   // new frames are timed by the stills that came in
      }
      this._camRetryShow(img);
    });
  }

  // The last still that loaded, while the next one still loads and the last one is less than 5 s old; null otherwise
  _camStillHeld(img) {
    // a still that failed (complete, no pixels) is held over like one that loads, until the next one comes (round 5 follow-up)
    if (this._camMode !== "still" || !img || (img.complete && img.naturalWidth) || img.style.display === "none") return null;
    const now = Date.now();
    if (!(now - (img._jhStillAt || 0) < 5000)) return null;
    return { look: img._jhStillLook || null, fresh: now - (img._jhFrameAt || 0) < 5000 };
  }

  // The still watchdog fired while a still loads and the last one loaded within its window: this still is judged once it is in or
  // has failed, or once the window since the last one that loaded has passed. True while it waits. The watchdog is armed again with
  // 0 then: it judges at once, or waits for the next still if that one already loads.
  _camStillWait(entity, img, ms) {
    const win = ms || (img && img._jhStillWin) || 0, at = img && img._jhStillAt;
    // a still that failed waits for the next one too, while the last one loaded within the window (round 5 follow-up)
    if (!img || (img.complete && img.naturalWidth) || !at || !(Date.now() - at < win)) return false;
    img._jhStillWin = win;
    let timer = null;
    const go = () => {
      img.removeEventListener("load", go); img.removeEventListener("error", go);
      if (this._camWatchdog !== timer) return;   // ended or replaced meanwhile
      clearTimeout(timer); this._camWatchdog = null;
      if (this._activeCam === entity && this._camMode === "still") this._armCamWatchdog(entity, img, "still", 0);
    };
    timer = setTimeout(go, at + win - Date.now() + 50);
    this._camWatchdog = timer;
    img.addEventListener("load", go); img.addEventListener("error", go);
    return true;
  }

  // One still that fails while the last still, a dark one, loaded less than 5 s ago is no end: the next still comes within 2 s, and
  // stills that keep failing still go down the chain (the watchdog, or the next error once 5 s have passed).
  _camStillDarkRecent(img) {
    return this._camMode === "still" && !!img && img._jhStillLook === "dark" && Date.now() - (img._jhStillAt || 0) < 5000;
  }

  // Jarvis Home patch (2026-11-08, round 5, the owner's rule): while the tile shows NO FRAME for a camera, the normal chain (stream,
  // stills, then the JARVIS tier) is tried again about once a minute, one try at a time and never faster than one tier switch a
  // second. NO FRAME stays up while it is tried; a camera that delivers frames again is shown (a dark picture by the dark path, with
  // its badge). A camera that stays broken stays on NO FRAME, with about one walk down the chain a minute. A try that comes due after
  // the tile was left, the panel was closed or another camera was picked does nothing.
  _camRetryLater(entity, ms) {
    if (this._camRetryTimer && this._camRetryCam === entity) return;   // one try at a time
    clearTimeout(this._camRetryTimer);
    this._camRetryCam = entity;
    this._camRetryTimer = setTimeout(() => { this._camRetryTimer = null; this._camRetryNow(entity); }, ms);
  }

  _camRetryNow(entity) {
    const feed = this.shadowRoot?.getElementById("cam-feed");
    const img = feed && feed.querySelector("img"), none = feed && feed.querySelector(".cam-none");
    if (!this.isConnected || !img || !none || this._activeCam !== entity || this._camMode !== "jarvis"
        || img.style.display !== "none" || !(none.textContent || "").startsWith("NO FRAME")) return;
    if (this._camPace(entity, () => this._camRetryNow(entity))) return;   // at most one tier switch a second
    const strip = this.shadowRoot.getElementById("cam-strip"), label = strip ? strip.innerHTML : "";
    delete this._camModeByEntity[entity];   // the chain is walked as on a first attach
    this._lastCamKey = "";
    this._renderCameraFeed();
    feed.appendChild(none);                 // NO FRAME stays up until a frame comes
    img.style.display = "none";
    if (strip) strip.innerHTML = label;
    this._camRetryFor = entity;
  }

  // A light picture on the stream or stills tier while a try still shows NO FRAME: it is shown
  _camRetryShow(img) {
    const entity = this._activeCam;
    if (!entity || entity !== this._camRetryFor || !img || !img.naturalWidth || img.style.display !== "none"
        || (this._camMode !== "stream" && this._camMode !== "still")) return;
    const luma = this._frameLuma(img);
    if (luma !== null && luma <= 10) return;   // a dark picture comes back by the dark path, with its badge; black is no picture
    const feed = this.shadowRoot?.getElementById("cam-feed");
    if (!feed || !feed.contains(img)) return;
    this._camRetryFor = null;
    feed.querySelector(".cam-none")?.remove();
    img.style.display = "";
    const strip = this.shadowRoot.getElementById("cam-strip");
    if (strip) strip.innerHTML = `<span>SRC <b>${this._esc(this._camName(entity))}</b></span><span>${this._camMode === "stream" ? "MJPEG" : "STILLS"}</span>`;
  }

"""
edit("frontend/jarvis-panel.js", "_camStillWait(", [
    ("  _camFallback(entity) {\n", lambda o: CAM96D + o),
    ('    if (this._camWatchdog) { clearTimeout(this._camWatchdog); }\n'
     '    this._camWatchdog = setTimeout(() => {\n'
     '      this._camWatchdog = null;\n'
     '      if (this._activeCam !== entity || this._camMode !== expectMode) return;\n',
     '    if (this._camWatchdog) { clearTimeout(this._camWatchdog); }\n'
     '    this._camLoadHook(img);   // Jarvis Home (round 5): stills are judged once they have loaded, a retry shows what comes\n'
     '    this._camWatchdog = setTimeout(() => {\n'
     '      this._camWatchdog = null;\n'
     '      if (this._activeCam !== entity || this._camMode !== expectMode) return;\n'
     '      if (expectMode === "still" && this._camStillWait(entity, img, ms)) return;   // Jarvis Home (round 5): judged once it is in\n'),
    ("        if (luma === null || luma > 10) return;   // real (or unverifiable) frame\n        if (this._camDarkNote(",
     "        if (luma === null || luma > 10) { this._camRetryShow(img); return; }   // real (or unverifiable) frame (round 5: a retry shows it)\n"
     "        if (this._camDarkNote("),
    ('        const res = await this._hass.callWS({ type: "jarvis/camera_snapshot", entity_id: entity });\n',
     lambda o: o + '        if (this._activeCam !== entity || this._camMode !== "jarvis") return;   // Jarvis Home (round 5): a late answer changes nothing\n'),
    ('    const hint = (text) => {\n      if (this._camDarkBack(entity)) return;',
     '    const hint = (text) => {\n'
     '      if (this._activeCam !== entity || this._camMode !== "jarvis") return;   // Jarvis Home (round 5): a late answer changes nothing\n'
     '      if (this._camDarkBack(entity)) return;'),
    ('      d.textContent = text;\n      img.style.display = "none";\n    };\n',
     '      d.textContent = text;\n      img.style.display = "none";\n'
     '      feed.querySelectorAll(".cam-dark").forEach((b) => b.remove());   // Jarvis Home (round 5): no picture, so no badge on it\n'
     '      if (text.startsWith("NO FRAME")) this._camRetryLater(entity, 60000);   // Jarvis Home (round 5): tried again about once a minute\n'
     '    };\n'),
    ('      const look = pic && pic.style.display !== "none" ? this._camLook(pic) : null;\n'
     '      const dark = look === "dark" && this._camFresh(pic);   // round 4: only while new frames keep coming\n',
     '      const held = this._camStillHeld(pic);   // round 5: while the next still loads, the last one that loaded is judged\n'
     '      const look = held ? held.look : pic && pic.style.display !== "none" ? this._camLook(pic) : null;\n'
     '      const dark = look === "dark" && (held ? held.fresh : this._camFresh(pic));   // round 4: only while new frames keep coming\n'),
    # one failed still of a dark camera is not the end (the next still comes within 2 s); stills that keep failing still escalate
    ('          else if (this._camMode === "still") this._camJarvisFallback(entity);\n',
     '          else if (this._camMode === "still" && !this._camStillDarkRecent(img)) this._camJarvisFallback(entity);   // Jarvis Home (round 5)\n'),
], "96d panel: a still is judged once it has loaded; while NO FRAME shows, the chain is tried again about once a minute")

# 97 -----------------------------------------------------------------------------------------
# Audit 10 round 3 (2026-11-07), Python. New steps with their own new markers, anchored on the text 94 left, so an install that
# already has 1-95 gets only these, and a clean release gets 94 and then these.
# a: an alarm with a time anywhere in the sentence is a wake-up alarm ("set a 7 am alarm", "set my morning alarm", "zet mijn alarm
#    op 7 uur"), except after arm, disarm, activate or deactivate: "arm the alarm at 11 pm" is the alarm system.
WAKE97 = r'''# Jarvis Home patch (2026-11-07, round 3): an alarm with a time anywhere in the sentence is a wake-up alarm too, and so is "my
# wake-up alarm". Arm, disarm, activate and deactivate are orders to the alarm system, with or without a time.
_JH_ALARM_ORDERS = frozenset("arm disarm activate deactivate".split())
_JH_ALARM_TIME_RE = re.compile(r"\d|\b(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s*(?:am|pm)\b|\bo'?\s?clock\b"
                               r"|\b(?:uur|tomorrow|morgen|morgens|morning|mornings|ochtend|ochtends|minutes?|minuten|minuut|hours?)\b"
                               r"|\b(?:my|mijn)\s+(?:wake[- ]?up|weekday|weekend)\s+alarms?\b")


def _jh_no_wake_alarm(body):
    """The words without a wake-up alarm, so what is left is read for a device. After arm, disarm, activate or deactivate the
    alarm stays: "arm the alarm at 11 pm" is the alarm system. "turn off the alarm" with no time is the alarm system too."""
    words = body.split()
    if not words or words[0] in _JH_ALARM_ORDERS:
        return body
    body = " ".join(_JH_WAKE_ALARM_RE.sub(" ", body).split())
    if re.search(r"\balarms?\b", body) and _JH_ALARM_TIME_RE.search(body):
        body = " ".join(re.sub(r"\balarms?\b", " ", body).split())
    return body


'''
edit("local_engine.py", "def _jh_no_wake_alarm(body):", [
    ('_JH_WARMTH_RE = re.compile(', lambda o: WAKE97 + o),
    ('    body = " ".join(_JH_WAKE_ALARM_RE.sub(" ", body).split())   # Jarvis Home patch (2026-11-07, round 2): no wake-up alarm\n',
     '    body = _jh_no_wake_alarm(body)   # Jarvis Home patch (2026-11-07, round 2 + 3): no wake-up alarm; arm and disarm are orders\n'),
], "97a local_engine: an alarm with a time anywhere is a wake-up alarm, except after arm, disarm, activate or deactivate")
# b: a question-word clause is the question only when it is the last clause or only an address follows it, so a relative clause
#    in the middle of a claim hides nothing; an -ing word claims only where a clause starts, after now or after I am / is / are;
#    Dutch "is (nu) aan" claims; a short lead-in such as "Wat fijn," or "How lovely," does not make the sentence a question.
CLAIMING97_OLD = r'''                           # Jarvis Home patch (2026-11-07, round 2): "Turning the heater on now" claims as much, and so does Dutch
                           r"|turning|switching|opening|closing|locking|unlocking|dimming|brightening|setting|starting|stopping"
                           r"|activating|deactivating|arming|disarming|raising|lowering|adjusting|changing"
                           r"|(?:staat|staan|gaat|gaan)\s+(?:nu\s+)?(?:aan|uit|open|dicht)|(?:aan|uit)gezet|(?:aan|uit)gedaan"
'''
CLAIMING97 = r'''                           # Jarvis Home patch (2026-11-07, round 2 + 3): Dutch claims, "is (nu) aan" too. An -ing word is read
                           # by _JH_ING_RE, only where it can claim: "Before turning anything on" claims nothing.
                           r"|(?:staat|staan|gaat|gaan|is|zijn)\s+(?:nu\s+)?(?:aan|uit|open|dicht)|(?:aan|uit)gezet|(?:aan|uit)gedaan"
'''
ING97 = r'''# Jarvis Home patch (2026-11-07, round 3): an -ing word claims where a clause starts ("Turning the heater on now"), after "now", or
# after I am / is / are ("I'm switching it on", "The heater is turning on"). Not after before, without, to avoid or "the current".
_JH_ING_WORDS = (r"(?:turning|switching|opening|closing|locking|unlocking|dimming|brightening|setting|starting|stopping|activating"
                 r"|deactivating|arming|disarming|raising|lowering|adjusting|changing)\b")
_JH_ING_RE = _re.compile(r"^\W*(?:(?:and|so|also|just|now)\s+)*" + _JH_ING_WORDS + r"|\bnow\s+" + _JH_ING_WORDS
                         + r"|\b(?:am|is|are|i['’]m|we['’]re|they['’]re|it['’]s)\s+(?:(?:now|just|also)\s+)?" + _JH_ING_WORDS, _re.I)
# A clause that is only an address: "Set to which temperature, sir?"
_JH_ADDRESS_RE = _re.compile(r"\W*(?:sir|madam|ma['’]am|meneer|mevrouw)\W*", _re.I)
# A short opening with what, how, when or where and then a comma is an exclamation or a lead-in, not the question: "Wat fijn,
# meneer, de verwarming staat aan, ...?", "How lovely, the heater is on, ...?", "When you asked, I switched on the heater, ...?".
_JH_LEADIN_RE = _re.compile(r"^\W*(?:(?:very well|certainly|of course|right|ok(?:ay)?|sir|yes|no)\W+)*(?:what|how|when|where|wat|hoe"
                            r"|wanneer|waar)\b(?:\s+[\w'’]+){0,3}\s*[,:]", _re.I)
'''
ASKS97_OLD = r'''    # Jarvis Home patch (2026-11-07, round 2): a clause that opens with a question word is the question, so a claim word inside it
    # claims nothing ("Set to which temperature, sir?"). A claim before it still claims.
    ask = next((i for i, p in enumerate(parts) if _JH_QWORD_RE.search(" ".join(p.split()[:3]))), len(parts) - 1)
    return not (_JH_CLOSING_RE.search(parts[-1]) or _JH_CLAIM_RE.search(" ".join(parts[:ask])))
'''
ASKS97 = r'''    # Jarvis Home patch (2026-11-07, round 2 + 3): a clause that opens with a question word is the question, so a claim word inside
    # it claims nothing ("Set to which temperature, sir?"), but only when it is the last clause or only an address follows it. One
    # in the middle is a relative clause: "The heater, which was off, is now on, sir, shall I set it to 21?" still claims.
    end = len(parts)
    while end > 1 and _JH_ADDRESS_RE.fullmatch(parts[end - 1]):
        end -= 1
    ask = end - 1 if _JH_QWORD_RE.search(" ".join(parts[end - 1].split()[:3])) else len(parts) - 1
    said = parts[:ask]
    return not (_JH_CLOSING_RE.search(parts[-1]) or _JH_CLAIM_RE.search(" ".join(said)) or any(_JH_ING_RE.search(p) for p in said))
'''
edit("agent.py", "_JH_ING_RE = _re.compile(", [
    (CLAIMING97_OLD, CLAIMING97),
    (QWORD94, lambda o: o + ING97),
    ('    if _JH_QOPEN_RE.match(first):\n        return True\n',
     '    if _JH_QOPEN_RE.match(first) and not _JH_LEADIN_RE.match(first):   # round 3: "Wat fijn, ..." opens no question\n'
     '        return True\n'),
    (ASKS97_OLD, ASKS97),
], "97b agent: a question-word clause asks only at the end; -ing claims where a clause starts; Dutch 'is aan'; no lead-in question")
# c: a device still on its way together with a device that waits for confirmation: both are named, never "Nothing has been
#    changed yet" while the garage door is opening.
WAIT97_OLD = '''    if problem == "waiting":
        return f"Only part of that went through, {honorific}. {waits}" if st["changed"] else f"{waits} Nothing has been changed yet."
'''
WAIT97 = '''    if problem == "waiting" and _jh_failures(st):
        # Jarvis Home patch (2026-11-07, round 3): a failure gets here only when it is a device still on its way (opening, locking).
        # It was sent and it moves, so "Nothing has been changed yet" would be false: the moving one and the waiting one are named.
        return f"Only part of that went through, {honorific}. {_jh_what_failed(st, honorific)} {waits}"
''' + WAIT97_OLD
edit("agent.py", "a failure gets here only when it is a device still on its way", [
    (WAIT97_OLD, WAIT97),
], "97c agent: a device still moving plus one waiting for confirmation names both, never 'Nothing has been changed yet'")

# 98 -----------------------------------------------------------------------------------------
# Audit 10 round 4 (2026-11-07), Python. New steps with their own new markers, anchored on the text 97 left, so an install that
# already has 1-97 gets only these. The markers of 97 stay in the files, so 97 never runs twice.
# a: an alarm clock and "my wake-up alarm" are taken out always, also after arm or activate ("deactivate my alarm clock"). Arm,
#    disarm, activate and deactivate keep a time only for "the alarm". Words only the alarm system has (away, night or home as a
#    mode, mode, zone, system, panel, security, beveiliging, arm, disarm) keep the alarm with a time later in the sentence, and so
#    does "the alarm" with on or off ("turn the alarm off at 7", "zet het alarm aan om 23 uur", "turn off the alarm in 10 minutes").
#    A number that is no time ("alarm 1", "zone 2") makes no wake-up alarm.
WAKE98 = r'''# Jarvis Home patch (2026-11-07, round 3 + 4): a wake-up alarm is no device. An alarm clock and "my wake-up / weekday / weekend
# alarm" are always taken out, also after arm or activate ("deactivate my alarm clock"). An alarm with a time is a wake-up alarm
# ("set a 7 am alarm", "zet mijn alarm op 7 uur"), unless the words are about the alarm system: arm, disarm, activate or
# deactivate "the alarm", or a word only the alarm system has ("set the alarm to away mode at 11 pm", "turn off alarm in zone 2").
_JH_ALARM_ORDERS = frozenset("arm disarm activate deactivate".split())
_JH_ALARM_CLOCK_RE = re.compile(r"\balarms?\s+clocks?\b|\b(?:my|mijn)\s+(?:wake[- ]?up|weekday|weekend)\s+alarms?\b")
_JH_THE_ALARM_RE = re.compile(r"\b(?:the|het)\s+alarms?\b")
# Words only the alarm system has. Away, home and night count as a mode ("to away", "op afwezig", "night mode"), not "at night".
_JH_ALARM_SYSTEM_RE = re.compile(r"\b(?:arm|arms|arming|armed|disarm|disarms|disarming|disarmed|mode|modus|zones?|system|systeem"
                                 r"|panel|security|beveiliging)\b|\b(?:to|on|op|naar)\s+(?:the\s+|de\s+)?(?:away|home|night|afwezig"
                                 r"|thuis|nacht)\b")
# "the alarm" switched on or off: "turn the alarm off at 7", "zet het alarm aan om 23 uur", "schakel het alarm in om 23 uur".
_JH_ALARM_SWITCH_RE = re.compile(r"^(?:turn|switch|put|zet|doe)\b.*\b(?:on|off|aan|uit)\b|^schakel\b.*\b(?:in|uit)\b"
                                 r"|\b(?:aan|uit)(?:zetten|doen)\b|\b(?:in|uit)schakelen\b")
# A time: "7 am", "7:30", "at 7", "om 7", "tomorrow", "in 20 minutes". A number that is not a time is a name or a zone: "alarm 1".
_JH_ALARM_WHEN_RE = re.compile(r"\d\s*(?:[:.h]\s*\d|h\b|am\b|pm\b|uur\b|u\b|o'?\s?clock\b|min|hour|hrs?\b|sec|uren\b)"
                               r"|\b(?:at|for|om|op|to|until|till|tot|by|in|around|rond|tegen|voor)\s+(?:about\s+|ongeveer\s+|half\s+)?\d"
                               r"|\b(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s*(?:am|pm)\b|\bo'?\s?clock\b"
                               r"|\b(?:uur|tomorrow|morgen|morgens|morning|mornings|ochtend|ochtends|minutes?|minuten|minuut|hours?)\b")


def _jh_no_wake_alarm(body):
    """The words without a wake-up alarm, so what is left is read for a device. "turn off the alarm" with no time is the alarm
    system. "turn off the alarm at 7" stays a wake-up alarm, as in rounds 1 to 3; "turn off the alarm in 10 minutes" is the alarm
    system: a delay is how the system is set, a wake-up alarm is set for a time."""
    body = " ".join(_JH_ALARM_CLOCK_RE.sub(" ", body).split())
    words = body.split()
    if not words or not re.search(r"\balarms?\b", body):
        return body
    the = _JH_THE_ALARM_RE.search(body)
    if (the and words[0] in _JH_ALARM_ORDERS) or _JH_ALARM_SYSTEM_RE.search(body):
        return body
    switch = bool(the and _JH_ALARM_SWITCH_RE.search(body))
    body = " ".join(_JH_WAKE_ALARM_RE.sub(lambda m: m.group(0) if switch and re.match(r"alarms?\s+in\b", m.group(0)) else " ",
                                          body).split())
    if not switch and re.search(r"\balarms?\b", body) and _JH_ALARM_WHEN_RE.search(body):
        body = " ".join(re.sub(r"\balarms?\b", " ", body).split())
    return body


'''
edit("local_engine.py", "_JH_ALARM_SYSTEM_RE = re.compile(", [
    (WAKE97, WAKE98),
], "98a local_engine: an alarm clock and my wake-up alarm always go; the alarm system keeps its words with a time; alarm 1 is no time")
# b: an -ing device word claims unless the word before it says otherwise (before, after, without, avoid, about, of, by, for, from,
#    the, current); a last clause that only lists choices or says please is like "sir"; a condition such as "When it is on" claims
#    nothing; the Dutch claim words never match "het is aan u".
CLAIMING98 = r'''                           # Jarvis Home patch (2026-11-07, round 2 + 3 + 4): Dutch claims, "is (nu) aan" too, but not "het is
                           # aan u". An -ing word is read by _jh_ing_claims: "Before turning anything on" claims nothing.
                           r"|(?:staat|staan|gaat|gaan|is|zijn)\s+(?:nu\s+)?(?:aan|uit|open|dicht)(?!\s+(?:u|jou|mij)\b)"
                           r"|(?:aan|uit)gezet|(?:aan|uit)gedaan"
'''
ING98 = r'''# Jarvis Home patch (2026-11-07, round 3 + 4): an -ing device word claims ("Turning the heater on now", "the heater's turning on",
# "I'll be switching it on", "I have begun turning it on"), unless the word just before it makes it none: "Before turning anything
# on", "To avoid switching the wrong one", "Without changing anything", "At the current setting of 20", "instead of", "the setting".
# An offer is no claim either ("I can try turning it on again", "I'd suggest switching"), nor is a "not" or "never" in the two words
# before it ("I am not changing anything yet", "I won't be switching anything"). "I tried switching" is not let off: it says so.
_JH_ING_WORDS = (r"(?:turning|switching|opening|closing|locking|unlocking|dimming|brightening|setting|starting|stopping|activating"
                 r"|deactivating|arming|disarming|raising|lowering|adjusting|changing)\b")
_JH_ING_RE = _re.compile(r"(?:\b([\w'’]+)\W+)?(?:\b([\w'’]+)\W+)?\b" + _JH_ING_WORDS, _re.I)
_JH_ING_NOT = frozenset("before after without avoid about of by for from the current a an your its this "   # "your setting"
                        "try suggest recommend consider".split())
# A clause that is only an address: "Set to which temperature, sir?"
_JH_ADDRESS_RE = _re.compile(r"\W*(?:sir|madam|ma['’]am|meneer|mevrouw)\W*", _re.I)
# A clause that only says please, or only lists choices: "Set to which temperature, sir, please?", "..., sir, 20 or 22?". The words
# before a list of choices may be more choices: "..., sir, 20, 21 or 22?"
_JH_PLEASE_RE = _re.compile(r"\W*(?:please|alstublieft|alsjeblieft|graag)\W*", _re.I)
_JH_CHOICES_RE = _re.compile(r"\W*(?:[\w'’°%.-]+\s+){0,3}[\w'’°%.-]+\s+(?:or|of)\s+(?:[\w'’°%.-]+\s+){0,3}[\w'’°%.-]+"
                             r"(?:\s+(?:please|sir|madam|meneer|mevrouw|alstublieft))?\W*", _re.I)
_JH_ITEM_RE = _re.compile(r"\W*[\w'’°%.-]+(?:\s+[\w'’°%.-]+){0,2}\W*")
# A condition says nothing about the device now: "When it is on, sir, shall I set it to 21?", "If the door is open, ...?"
_JH_IF_RE = _re.compile(r"^\W*(?:(?:and|so|but|en|maar)\s+)?(?:if|when|whenever|once|as soon as|als|zodra|wanneer|indien)\b", _re.I)
_JH_STATE_RE = _re.compile(r"\b(?:is|are|staat|staan|zijn)\s+(?:now\s+|nu\s+)?(?:on|off|open|closed|locked|unlocked|aan|uit|dicht)\b",
                           _re.I)
# A short opening with what, how, when or where and then a comma is an exclamation or a lead-in, not the question: "Wat fijn,
# meneer, de verwarming staat aan, ...?", "How lovely, the heater is on, ...?", "When you asked, I switched on the heater, ...?".
_JH_LEADIN_RE = _re.compile(r"^\W*(?:(?:very well|certainly|of course|right|ok(?:ay)?|sir|yes|no)\W+)*(?:what|how|when|where|wat|hoe"
                            r"|wanneer|waar)\b(?:\s+[\w'’]+){0,3}\s*[,:]", _re.I)


def _jh_ing_claims(clause: str) -> bool:
    """An -ing device word in this clause says it is happening, unless the word before it says otherwise."""
    for m in _JH_ING_RE.finditer(clause):
        near = [(w or "").lower().replace("’", "'") for w in m.groups()]   # two words back, one word back
        if (near[1] or near[0]) in _JH_ING_NOT or any(w in ("not", "never") or w.endswith("n't") for w in near):
            continue
        return True
    return False
'''
ASKS98 = r'''    # Jarvis Home patch (2026-11-07, round 2 + 3 + 4): a clause that opens with a question word is the question, so a claim word
    # inside it claims nothing ("Set to which temperature, sir?"), but only when it is the last clause or only an address, "please"
    # or a list of choices follows it ("..., sir, 20 or 22?"). One in the middle is a relative clause: "The heater, which was off,
    # is now on, sir, shall I set it to 21?" still claims.
    end, listing = len(parts), False
    while end > 1:
        tail = parts[end - 1]
        if _JH_CHOICES_RE.fullmatch(tail):
            listing = True
        elif not (_JH_ADDRESS_RE.fullmatch(tail) or _JH_PLEASE_RE.fullmatch(tail)
                  or (listing and _JH_ITEM_RE.fullmatch(tail) and not _JH_CLAIM_RE.search(tail))):
            break
        end -= 1
    ask = end - 1 if _JH_QWORD_RE.search(" ".join(parts[end - 1].split()[:3])) else len(parts) - 1
    said = [_JH_STATE_RE.sub(" ", p) if _JH_IF_RE.match(p) else p for p in parts[:ask]]   # round 4: a condition claims nothing
    return not (_JH_CLOSING_RE.search(parts[-1]) or _JH_CLAIM_RE.search(" ".join(said)) or any(_JH_CLAIM_RE.search(p) for p in said)
                or any(_jh_ing_claims(p) for p in said))
'''
edit("agent.py", "def _jh_ing_claims(clause: str) -> bool:", [
    (CLAIMING97, CLAIMING98),
    (ING97, ING98),
    (ASKS97, ASKS98),
], "98b agent: -ing claims unless after before, without, avoid, the ...; choices or please after a question; a condition; 'aan u'")

# 100 ----------------------------------------------------------------------------------------
# Audit 10 round 5 (2026-11-08), Python. New steps with their own new markers, anchored on the text 98 left, so an install that
# already has 1-98 gets only these. The markers of 98 stay in the files, so 98 never runs twice.
# a: "the house / home / burglar / intruder / security alarm" is "the alarm", and those words are words only the alarm system has:
#    "activate the burglar alarm at 11 pm" and "deactivate the home alarm at 7" are device commands again, with or without an alarm
#    panel. Wake-up alarms are unchanged, and so is "turn on/off the alarm at <time>": a wake-up alarm, as in rounds 1 to 4 (owner).
ALARM100_OLD = r'''_JH_THE_ALARM_RE = re.compile(r"\b(?:the|het)\s+alarms?\b")
# Words only the alarm system has. Away, home and night count as a mode ("to away", "op afwezig", "night mode"), not "at night".
_JH_ALARM_SYSTEM_RE = re.compile(r"\b(?:arm|arms|arming|armed|disarm|disarms|disarming|disarmed|mode|modus|zones?|system|systeem"
                                 r"|panel|security|beveiliging)\b|\b(?:to|on|op|naar)\s+(?:the\s+|de\s+)?(?:away|home|night|afwezig"
                                 r"|thuis|nacht)\b")
'''
ALARM100 = r'''# Jarvis Home patch (2026-11-08, round 5): "the house / home / burglar / intruder / security alarm" is "the alarm" too, and those
# words are the alarm system's own: "activate the burglar alarm at 11 pm", "deactivate the home alarm at 7".
_JH_THE_ALARM_RE = re.compile(r"\b(?:the|het)\s+(?:(?:house|home|burglar|intruder|security)\s+)?alarms?\b")
# Words only the alarm system has. Away, home and night count as a mode ("to away", "op afwezig", "night mode"), not "at night".
_JH_ALARM_SYSTEM_RE = re.compile(r"\b(?:arm|arms|arming|armed|disarm|disarms|disarming|disarmed|mode|modus|zones?|system|systeem"
                                 r"|panel|security|beveiliging|burglar|intruder)\b|(?<!google\s)(?<!nest\s)\b(?:house|home)\s+alarms?\b"
                                 r"|\b(?:to|on|op|naar)\s+(?:the\s+|de\s+)?(?:away|home|night|afwezig|thuis|nacht)\b")
'''
edit("local_engine.py", "(?:house|home|burglar|intruder|security)", [
    (ALARM100_OLD, ALARM100),
], "100a local_engine: the house, home, burglar, intruder or security alarm is the alarm system, also with a time")
# b: a condition lets off only the state of its own subject ("When it is on", "If the front door is open", "Once the heater is
#    on"), so "Once again the heater is on" and "As soon as you asked the heater is on" claim again; and only when no tool reported
#    a failure: after a failed control_device "Once the heater is on, sir, ...?" is replaced again, as in round 3.
COND100_OLD = r'''# A condition says nothing about the device now: "When it is on, sir, shall I set it to 21?", "If the door is open, ...?"
_JH_IF_RE = _re.compile(r"^\W*(?:(?:and|so|but|en|maar)\s+)?(?:if|when|whenever|once|as soon as|als|zodra|wanneer|indien)\b", _re.I)
_JH_STATE_RE = _re.compile(r"\b(?:is|are|staat|staan|zijn)\s+(?:now\s+|nu\s+)?(?:on|off|open|closed|locked|unlocked|aan|uit|dicht)\b",
                           _re.I)
'''
COND100 = r'''# A condition says nothing about the device now: "When it is on, sir, shall I set it to 21?", "If the door is open, ...?"
# Jarvis Home patch (2026-11-08, round 5): only the state of the condition's own subject is let off, right after if, when or once:
# a pronoun, an article and up to three words, or a name and up to two more words. "Once again the heater is on" and "As soon as
# you asked the heater is on" still claim. _jh_problem lets a condition off only when no tool reported a failure.
_JH_COND_WHO = (r"(?:it|they|this|that|these|those|both|all|everything|het|ze|die|dat|dit|deze|alles|beide"
                r"|(?:the|a|an|my|your|our|its|their|this|that|these|those|any|all|both|every|each|de|het|een|mijn|uw|je|jouw|onze"
                r"|ons|alle|elke|deze|dit)(?:\s+[\w'’-]+){1,3}?|(?-i:[A-Z][A-Za-z0-9])[\w'’-]*(?:\s+[\w'’-]+){0,2}?)")
_JH_COND_STATE = r"\s+(?:is|are|staat|staan|zijn)\s+(?:now\s+|nu\s+)?(?:on|off|open|closed|locked|unlocked|aan|uit|dicht)\b"
_JH_COND_RE = _re.compile(r"^\W*(?:(?:and|so|but|en|maar)\s+)?(?:if|when|whenever|once(?!\s+(?:again|more)\b)|as soon as|als|zodra"
                          r"|wanneer|indien)\s+" + _JH_COND_WHO + _JH_COND_STATE
                          + r"(?:\s+(?:and|or|en|of)\s+" + _JH_COND_WHO + _JH_COND_STATE + r")?", _re.I)
'''
edit("agent.py", "_JH_COND_RE = _re.compile(", [
    (COND100_OLD, COND100),
    # _jh_asks keeps its line (the marker of 85b) and asks _jh_asks_if, which holds the old body and knows whether a tool failed
    ('def _jh_asks(first: str) -> bool:\n'
     '    """A real question: it ends in a question mark and opens as a question, or what comes before the question claims\n'
     '    nothing and the question is more than a closing offer of help."""\n',
     lambda o: o + '    return _jh_asks_if(first, True)\n\n\n'
     'def _jh_asks_if(first: str, cond: bool) -> bool:\n'
     '    """_jh_asks. Jarvis Home patch (2026-11-08, round 5): a condition may claim nothing only when cond is true, that is when\n'
     '    no tool reported a failure: after a failed tool "Once the heater is on, sir, shall I set it to 21?" is no honest question."""\n'),
    ('    said = [_JH_STATE_RE.sub(" ", p) if _JH_IF_RE.match(p) else p for p in parts[:ask]]   # round 4: a condition claims nothing\n',
     '    # round 4 + 5: a condition claims nothing about its own subject, and only while no tool reported a failure\n'
     '    said = [_JH_COND_RE.sub(" ", p, count=1) if cond else p for p in parts[:ask]]\n'),
    ('    if _jh_asks(first) or _JH_CANNOT_RE.search(_JH_IDIOM_RE.sub(" ", first)):\n',
     '    if (_jh_asks_if(first, not any(b["kind"] == "failed" for b in _jh_failures(st)))   # round 5: not after a failed tool\n'
     '            or _JH_CANNOT_RE.search(_JH_IDIOM_RE.sub(" ", first))):\n'),
    # a condition that opens with "when" is no question either, so it is read like the others ("When the living room lights are
    # on, sir, ...?" was taken as a question by its first word and stood after a failed tool)
    ('    if _JH_QOPEN_RE.match(first) and not _JH_LEADIN_RE.match(first):   # round 3: "Wat fijn, ..." opens no question\n',
     '    if (_JH_QOPEN_RE.match(first) and not _JH_LEADIN_RE.match(first)   # round 3: "Wat fijn, ..." opens no question\n'
     '            and not _JH_COND_RE.match(first)):   # round 5: nor does a condition ("When the lights are on, ...")\n'),
], "100b agent: a condition lets off only its own subject's state, not 'once again', and only when no tool failed")
# c: the choices test is linear: its edges never take a character a choice may hold, so a failed match cannot backtrack over a long
#    run of quotes, dots or hyphens (seconds on the HA event loop), and a clause of more than 100 characters is no list of choices.
CHOICES100_OLD = r'''_JH_CHOICES_RE = _re.compile(r"\W*(?:[\w'’°%.-]+\s+){0,3}[\w'’°%.-]+\s+(?:or|of)\s+(?:[\w'’°%.-]+\s+){0,3}[\w'’°%.-]+"
                             r"(?:\s+(?:please|sir|madam|meneer|mevrouw|alstublieft))?\W*", _re.I)
_JH_ITEM_RE = _re.compile(r"\W*[\w'’°%.-]+(?:\s+[\w'’°%.-]+){0,2}\W*")
'''
CHOICES100 = r'''# Jarvis Home patch (2026-11-08, round 5): the edges never take a character a choice may hold, so a failed match cannot backtrack
# over a long run of quotes, dots or hyphens (that took seconds), and a clause of more than 100 characters is no list of choices.
_JH_CHOICES_RE = _re.compile(r"[^\w'’°%.-]*(?:[\w'’°%.-]+\s+){0,3}[\w'’°%.-]+\s+(?:or|of)\s+(?:[\w'’°%.-]+\s+){0,3}[\w'’°%.-]+"
                             r"(?:\s+(?:please|sir|madam|meneer|mevrouw|alstublieft))?[^\w'’°%.-]*", _re.I)
_JH_ITEM_RE = _re.compile(r"[^\w'’°%.-]*[\w'’°%.-]+(?:\s+[\w'’°%.-]+){0,2}[^\w'’°%.-]*")
_JH_CHOICES_MAX = 100
'''
LOOP100_OLD = r'''        if _JH_CHOICES_RE.fullmatch(tail):
            listing = True
        elif not (_JH_ADDRESS_RE.fullmatch(tail) or _JH_PLEASE_RE.fullmatch(tail)
                  or (listing and _JH_ITEM_RE.fullmatch(tail) and not _JH_CLAIM_RE.search(tail))):
'''
LOOP100 = r'''        short = len(tail) <= _JH_CHOICES_MAX   # round 5: a long clause is no list of choices
        if short and _JH_CHOICES_RE.fullmatch(tail):
            listing = True
        elif not (_JH_ADDRESS_RE.fullmatch(tail) or _JH_PLEASE_RE.fullmatch(tail)
                  or (listing and short and _JH_ITEM_RE.fullmatch(tail) and not _JH_CLAIM_RE.search(tail))):
'''
edit("agent.py", "_JH_CHOICES_MAX = ", [
    (CHOICES100_OLD, CHOICES100),
    (LOOP100_OLD, LOOP100),
], "100c agent: the choices test is linear and skips clauses longer than 100 characters")
# d: with, than and me let an -ing word off ("happy to help with switching", "rather than switching", "instead of me switching"),
#    and so does "Before I start changing anything"; "setting" is a noun ("The thermostat setting is 20", "the highest setting")
#    unless an object or "to" follows it or a pronoun or a form of be or will comes just before it ("I am setting the heater to 21").
ING100_OLD = r'''_JH_ING_NOT = frozenset("before after without avoid about of by for from the current a an your its this "   # "your setting"
                        "try suggest recommend consider".split())
'''
ING100 = r'''_JH_ING_NOT = frozenset("before after without avoid about of by for from the current a an your its this "   # "your setting"
                        "try suggest recommend consider".split())
# round 5: "help with switching", "rather than switching", "instead of me switching" claim nothing; "going ahead with switching",
# "proceeding with switching" and "no sooner said than switching" still claim, so these words count only after the word before them
_JH_ING_PAIRS = frozenset({("help", "with"), ("helping", "with"), ("assist", "with"), ("assisting", "with"), ("rather", "than"),
                           ("of", "me"), ("without", "me")})
# Jarvis Home patch (2026-11-08, round 5): "setting" is a noun ("The thermostat setting is 20", "the highest setting") unless an
# object or "to" follows it ("setting it / the heater / up", "Setting Living Room Heater to 21") or a pronoun or a form of be,
# have or will comes just before it ("I am setting", "I'll be setting"). "Before I start changing anything" claims nothing: I or we
# and start or begin, right after before, until, when, if, once, after or unless ("We start turning it on" still claims).
_JH_SET_VERB_BEFORE = frozenset("am is are was were be been being i we you they he she i'm im we're you're they're he's she's it's "
                                "that's i'll we'll you'll it'll they'll i've we've i'd we'd now just also still already".split())
_JH_SET_OBJECT_RE = _re.compile(r"\W+(?:it|them|him|her|the|a|an|up|to|this|that|these|those|your|my|our|their|his|all|both|every"
                                r"|each|everything|now)\b", _re.I)
_JH_TO_RE = _re.compile(r"\bto\b", _re.I)
# ... and only with a word before it and nothing, a form of be, "of" or "'s" after it ("The thermostat setting is 20", "the highest
# setting"): a clipped "Setting heater at 21", "Setting 21 degrees" or "Setting things up" is a claim
_JH_SET_NOUN_AFTER_RE = _re.compile(r"\W*$|\s+(?:is|was|are|were|of|has|had|stays|remains)\b|['’]s\b", _re.I)
_JH_SUB_END_RE = _re.compile(r"\b(?:before|until|till|when|whenever|if|once|after|unless)\W*$", _re.I)
'''
INGFN100_OLD = r'''    for m in _JH_ING_RE.finditer(clause):
        near = [(w or "").lower().replace("’", "'") for w in m.groups()]   # two words back, one word back
        if (near[1] or near[0]) in _JH_ING_NOT or any(w in ("not", "never") or w.endswith("n't") for w in near):
            continue
        return True
'''
INGFN100 = r'''    last_to = max((t.start() for t in _JH_TO_RE.finditer(clause)), default=-1)   # round 5: "Setting Living Room Heater to 21"
    for m in _JH_ING_RE.finditer(clause):
        near = [(w or "").lower().replace("’", "'") for w in m.groups()]   # two words back, one word back
        if (near[1] or near[0]) in _JH_ING_NOT or any(w in ("not", "never") or w.endswith("n't") for w in near):
            continue
        if (near[0], near[1]) in _JH_ING_PAIRS:
            continue   # round 5: "help with switching", "rather than switching"
        if (near[0] in ("i", "we") and near[1] in ("start", "begin")
                and _JH_SUB_END_RE.search(clause, max(0, m.start() - 12), m.start())):   # round 5: "Before I start changing"
            continue
        if (m.group(0).lower().endswith("setting") and (near[1] or near[0]) and (near[1] or near[0]) not in _JH_SET_VERB_BEFORE
                and not _JH_SET_OBJECT_RE.match(clause, m.end()) and last_to < m.end()
                and _JH_SET_NOUN_AFTER_RE.match(clause, m.end())):
            continue   # round 5: "setting" is a noun here
        return True
'''
edit("agent.py", "_JH_SET_VERB_BEFORE = frozenset(", [
    (ING100_OLD, ING100),
    (INGFN100_OLD, INGFN100),
], "100d agent: with, than, me and 'I start' let an -ing word off; 'setting' is a noun unless an object or a be-form goes with it")

# 101 ----------------------------------------------------------------------------------------
# Live test of 1.0.0 (2026-11-08): "What is the temperature in the living room?" found the Living Room Heater (a climate device)
# and the original JARVIS read its state out as a temperature: "The temperature in Living Room Heater is currently off°, sir."
# A thermostat or heater reports its room temperature in current_temperature; a value that is no number is never read out as a
# temperature, so JARVIS says the device reports none. Anchored on upstream code with its own new marker.
TEMP101_OLD = '''        if state:
            unit = state.attributes.get("unit_of_measurement", "°")
            return f"The temperature in {fname} is currently {state.state}{unit}, {h}."
'''
TEMP101 = '''        if state:
            # Jarvis Home patch (2026-11-08): a thermostat or heater reports its room temperature in current_temperature, not in
            # its state ("off", "heat"), and a value that is no number is never read out as a temperature ("off°")
            value = state.attributes.get("current_temperature") if entity_id.split(".")[0] in ("climate", "water_heater") else state.state
            try:
                float(value)
            except (TypeError, ValueError):
                return f"{fname} does not report a temperature at the moment, {h}."
            unit = state.attributes.get("unit_of_measurement", "°")
            return f"The temperature in {fname} is currently {value}{unit}, {h}."
'''
edit("local_engine.py", "a value that is no number is never read out as a temperature", [
    (TEMP101_OLD, TEMP101),
], "101 local_engine: a heater's temperature is its current_temperature; a state that is no number is no temperature")

# 102 ----------------------------------------------------------------------------------------
# Live question (2026-11-08): "What is our electricity consumption so far this month?" JARVIS only had energy_status (the power
# right now) and answered "Home Assistant does not track total monthly consumption", which is not true: the Energy dashboard keeps
# it. A read-only tool energy_usage reads the dashboard's statistics for a period (today ... this year): the total and the biggest
# users, and it says what the figure covers - the whole house with a grid meter, otherwise only the monitored devices. Household
# members may use it, and it stays usable after reading mail (it reads nothing outside the home).
EU102_TOOL_OLD = '''                "autonomous) and never sheds critical loads."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
'''
EU102_TOOL = '''                "autonomous) and never sheds critical loads. For energy USED over a "
                "period (today, this month) use energy_usage."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "energy_usage",
            "description": (
                "Energy (kWh) used over a period, from Home Assistant's Energy "
                "dashboard: the total and the biggest users. Use for 'how much "
                "electricity did we use this month / today / last week'. The "
                "result says what the total covers; say that plainly (for "
                "example: only the monitored devices, when there is no "
                "whole-house meter)."
            ),
            "parameters": {"type": "object", "properties": {
                "period": {"type": "string",
                           "enum": ["today", "yesterday", "this_week", "last_week",
                                    "this_month", "last_month", "this_year"],
                           "description": "Which period; default this_month."},
            }},
        },
    },
'''
EU102_EXEC_OLD = '''async def _exec_hazard_report(hass: HomeAssistant, args: dict) -> str:
'''
EU102_EXEC = '''async def _exec_energy_usage(hass: HomeAssistant, args: dict) -> str:
    """Jarvis Home patch (2026-11-08): energy used over a period, from Home Assistant's Energy dashboard (recorder statistics).
    Says what the figure covers: without a grid meter on the dashboard it is only the monitored devices, never the house."""
    try:
        from datetime import timedelta
        from homeassistant.util import dt as dt_util
        from homeassistant.components.energy.data import async_get_manager
        from homeassistant.components.recorder import get_instance
        from homeassistant.components.recorder.statistics import statistics_during_period
        period = str(args.get("period") or "this_month").strip().lower().replace(" ", "_")
        now = dt_util.now()
        day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        if period == "today":
            start, end = day, now
        elif period == "yesterday":
            start, end = day - timedelta(days=1), day
        elif period == "this_week":
            start, end = day - timedelta(days=day.weekday()), now
        elif period == "last_week":
            end = day - timedelta(days=day.weekday())
            start = end - timedelta(days=7)
        elif period == "last_month":
            end = day.replace(day=1)
            start = (end - timedelta(days=1)).replace(day=1)
        elif period == "this_year":
            start, end = day.replace(month=1, day=1), now
        else:
            period, start, end = "this_month", day.replace(day=1), now
        prefs = (await async_get_manager(hass)).data or {}
        grid_ids = []
        for source in prefs.get("energy_sources") or []:
            if source.get("type") != "grid":
                continue
            for flow in source.get("flow_from") or []:
                if flow.get("stat_energy_from"):
                    grid_ids.append(flow["stat_energy_from"])
            if source.get("stat_energy_from"):
                grid_ids.append(source["stat_energy_from"])
        devices = [(d["stat_consumption"], d.get("name")) for d in prefs.get("device_consumption") or []
                   if d.get("stat_consumption")]
        ids = set(grid_ids) | {sid for sid, _ in devices}
        if not ids:
            return json.dumps({"error": "the Energy dashboard has no energy sensors, so Home Assistant records no consumption"})
        stats = await get_instance(hass).async_add_executor_job(
            statistics_during_period, hass, dt_util.as_utc(start), dt_util.as_utc(end), ids, "hour", None, {"change"})
        used = {sid: sum((row.get("change") or 0) for row in (stats or {}).get(sid, [])) for sid in ids}

        def label(sid, name):
            state = hass.states.get(sid)
            return name or (state.attributes.get("friendly_name") if state else None) or sid

        rows = sorted(({"name": label(sid, name), "kwh": round(used.get(sid, 0), 2)} for sid, name in devices),
                      key=lambda r: -r["kwh"])
        result = {"period": period, "from": start.isoformat(timespec="minutes"), "to": end.isoformat(timespec="minutes"),
                  "unit": "kWh", "biggest_users": [r for r in rows if r["kwh"] > 0][:8]}
        if grid_ids:
            result.update(total_kwh=round(sum(used.get(s, 0) for s in grid_ids), 2),
                          covers="the whole house (the grid meter on the Energy dashboard)",
                          monitored_devices_kwh=round(sum(r["kwh"] for r in rows), 2))
        else:
            result.update(total_kwh=round(sum(r["kwh"] for r in rows), 2),
                          covers=("only the %d monitored devices on the Energy dashboard; there is no whole-house meter, "
                                  "so the household's real total is higher" % len(rows)))
        return json.dumps(result)
    except Exception as exc:
        return json.dumps({"error": "energy usage could not be read: %s" % exc})


async def _exec_hazard_report(hass: HomeAssistant, args: dict) -> str:
'''
edit("agent.py", "async def _exec_energy_usage(", [
    (EU102_TOOL_OLD, EU102_TOOL),
    (EU102_EXEC_OLD, EU102_EXEC),
    ('''    "energy_status":       _exec_energy_status,
''', '''    "energy_status":       _exec_energy_status,
    "energy_usage":        _exec_energy_usage,
'''),
    ('''"calendar_agenda", "weather_forecast", "energy_status",
    "hazard_report",''', '''"calendar_agenda", "weather_forecast", "energy_status", "energy_usage",
    "hazard_report",'''),
    ('''"get_entity_state", "search_entities", "get_area_devices", "energy_status",
                              "cognitive_status",''', '''"get_entity_state", "search_entities", "get_area_devices", "energy_status",
                              "energy_usage", "cognitive_status",'''),
    ('''                    "energy_status", "activity_history", "get_entity_state", "root_cause"},''',
     '''                    "energy_status", "energy_usage", "activity_history", "get_entity_state", "root_cause"},'''),
    ('        f"user actually asks for the current time (\'what time is it?\').\\n\\n"\n        f"## Critical rules\\n"\n', '        f"user actually asks for the current time (\'what time is it?\').\\n\\n"\n        f"### Energy USED is not the power right now\\n"\n        f"For electricity or energy USED over a period - \'how much did we use "\n        f"this month / today / last week\', \'our consumption so far\' - call "\n        f"energy_usage and give its total and biggest users, saying what the "\n        f"total covers. Home Assistant does keep this (the Energy dashboard): "\n        f"never say it only tracks the current power. energy_status is only the "\n        f"power drawn right now.\\n\\n"\n        f"## Critical rules\\n"\n'),
], "102 agent: energy_usage - energy used over a period from the Energy dashboard, saying what the total covers")
# energy_usage reads Home Assistant's energy component, so the manifest names it (hassfest and the manifest test require it)
edit("manifest.json", '"energy"', [
    ('  "after_dependencies": [\n    "conversation",\n', '  "after_dependencies": [\n    "conversation",\n    "energy",\n'),
], "102 manifest: energy is an after-dependency (energy_usage reads the Energy dashboard)")

# 103 ----------------------------------------------------------------------------------------
# Live test of 1.0.1 (2026-11-09): "What is our power consumption until now for this month?" never reached JARVIS: the instant
# path matched "power consumption" and, with no whole-house power sensor, ended with "I don't have a total power sensor
# configured". A question about a period (today, this month, so far ...) now goes to JARVIS, who uses energy_usage, and the
# instant path never ends in that dead end: without a whole-house sensor it lets JARVIS answer (energy_status adds up devices).
edit("local_engine.py", "a question about a period goes to JARVIS", [
    ('    (r"(?:what(?:\'s| is)\\s+(?:the\\s+)?)?(?:energy|power)\\s+(?:usage|consumption)", "energy"),\n',
     '    # Jarvis Home patch (2026-11-09): a question about a period goes to JARVIS (energy_usage), not to the power-now answer\n'
     '    (r"(?:what(?:\'s| is)\\s+(?:the\\s+)?)?(?:energy|power)\\s+(?:usage|consumption)'
     '(?!.*\\b(?:today|yesterday|week|month|year|far|now|since|last|this|used)\\b)", "energy"),\n'),
    ('        return f"I don\'t have a total power sensor configured, {h}."\n',
     '        return None   # Jarvis Home patch: no whole-house power sensor - JARVIS answers (energy_status adds up the devices)\n'),
], "103 local_engine: energy questions about a period go to JARVIS; no 'no total power sensor' dead end")

# 104 ----------------------------------------------------------------------------------------
# Usage review (2026-11-08): a lone "Okay." (speech-to-text invents it from silence, or a reply that answers nothing) went to the AI
# and JARVIS took it as a yes to the last topic: "I understand you'd like me to open the garage door". The Chat tab already ignores
# such words; now every way in does: a lone filler word that answers no question of JARVIS is not a command. By voice nothing is
# said, in text a short acknowledgement; nothing is stored or done. After a question from JARVIS, "Okay." still counts as the answer.
edit("conversation.py", "a lone filler word that answers no question", [
    ('        await self._maybe_seed_history(cid, history, _jh_uid, _jh_dev)\n\n        history.append({"role": "user", "content": user_input.text})\n',
     '        await self._maybe_seed_history(cid, history, _jh_uid, _jh_dev)\n\n'
     '        # Jarvis Home patch (2026-11-09): a lone filler word that answers no question of JARVIS is not a command\n'
     '        import re as _jh_re\n'
     '        _jh_asked = bool(history) and history[-1].get("role") == "assistant" and str(history[-1].get("content") or "").rstrip().endswith("?")\n'
     '        if not _jh_asked and _jh_re.fullmatch(r"\\s*(?:ok(?:ay)?|oké|thank you|thanks|thx|you|bye|yeah|alright|all right|cool|hmm+|uh+|um+)[\\s.!?,]*",\n'
     '                                              (user_input.text or "").lower()):\n'
     '            _jh_ir = intent.IntentResponse(language=user_input.language)\n'
     '            _jh_ir.async_set_speech("" if device_id else f"Very good, {honorific}.")\n'
     '            return conversation.ConversationResult(response=_jh_ir, conversation_id=user_input.conversation_id or cid)\n\n'
     '        history.append({"role": "user", "content": user_input.text})\n'),
], "104 conversation: a lone filler word that answers no question is not a command (all ways in, like the Chat tab)")

# 105 ----------------------------------------------------------------------------------------
# Live check of 1.0.3 (2026-11-10): "What is the power consumption?" was answered without any tool call with "This month we've
# used 64.01 kWh", the answer from two days earlier: the person's saved chat held that answer four times and the model repeated
# it. It was stale, and it was not the power right now. Without a whole-house meter nothing could give the power right now:
# energy_status only knows a whole-house meter, and the instant path handed the question to JARVIS.
#  - energy.jh_devices_power_now reads what the devices on the Energy dashboard draw right now: a device's power entry there,
#    else the one power sensor on the same device as its energy sensor. It says what that covers.
#  - The instant path answers "what is the power consumption" with it when there is no whole-house meter (no AI, no wait).
#  - energy_status adds it as devices_now when there is no whole-house meter.
#  - A reply that gives an energy or power figure (kWh, kW, W) while no tool ran in this request is sent back once: earlier
#    answers in the chat are out of date, read it now. A figure the person said themselves does not count.
PN105_ENERGY = '''# Jarvis Home patch (2026-11-10): what the devices on the Energy dashboard draw right now, read live.
async def jh_devices_power_now(hass) -> Optional[dict]:
    """The power the devices on the Energy dashboard draw right now. A device's power sensor is its power entry on the
    dashboard, else the one power sensor on the same device as its energy sensor; a device without one is left out.
    None when no device has a power sensor. Never raises."""
    try:
        from homeassistant.components.energy.data import async_get_manager
        from homeassistant.helpers import device_registry as dr, entity_registry as er
        prefs = (await async_get_manager(hass)).data or {}
        ents, devs = er.async_get(hass), dr.async_get(hass)
        rows, unread = [], []
        for item in prefs.get("device_consumption") or []:
            entry = ents.async_get(item.get("stat_consumption") or "")
            device_id = getattr(entry, "device_id", None)
            power_id = item.get("stat_rate")
            if not power_id and device_id:
                same = [e.entity_id for e in list(ents.entities.values())
                        if getattr(e, "device_id", None) == device_id and _jh_is_power(hass.states.get(e.entity_id))]
                power_id = same[0] if len(same) == 1 else None
            if not power_id:
                continue   # this device only reports energy
            state = hass.states.get(power_id)
            device = devs.async_get(device_id) if device_id else None
            name = (item.get("name") or getattr(device, "name_by_user", None) or getattr(device, "name", None)
                    or (state.attributes.get("friendly_name") if state is not None else None) or power_id)
            watts = _jh_watts(state)
            if watts is None:
                unread.append(name)
            else:
                rows.append({"name": name, "watts": round(watts, 1)})
        if not rows and not unread:
            return None
        rows.sort(key=lambda r: -r["watts"])
        return {"total_watts": round(sum(r["watts"] for r in rows)), "devices": rows, "unreadable": unread,
                "covers": ("only the %d monitored devices on the Energy dashboard that measure power; there is no "
                           "whole-house meter, so the household draws more" % (len(rows) + len(unread)))}
    except Exception:
        return None


def _jh_is_power(state) -> bool:
    return (state is not None and state.attributes.get("device_class") == "power"
            and str(state.attributes.get("unit_of_measurement") or "").lower() in ("w", "kw"))


def _jh_watts(state) -> Optional[float]:
    try:
        watts = float(state.state)
    except (AttributeError, TypeError, ValueError):
        return None   # unavailable, unknown or missing
    return watts * 1000 if str(state.attributes.get("unit_of_measurement") or "").lower() == "kw" else watts


'''
#  - Without a configured or named whole-house meter, the original took the BIGGEST power sensor as the house: in a home with
#    plugs only, "how much power are we using" became one heater. That guess now holds only when it is the one power sensor.
edit("energy.py", "async def jh_devices_power_now(", [
    ("def evaluate_for_proactive(hass) -> Optional[dict]:\n", lambda old: PN105_ENERGY + old),
    ("        if candidates:\n            return max(candidates, key=lambda item: item[1])[0]\n        return None\n",
     "        if len(candidates) == 1:   # Jarvis Home patch (2026-11-10): the biggest of several is one device, not the house\n"
     "            return candidates[0][0]\n        return None\n"),
    ("    if candidates:\n        return max(candidates, key=lambda item: item[1])[0]\n    return None\n",
     "    if len(candidates) == 1:   # Jarvis Home patch (2026-11-10): the biggest of several is one device, not the house\n"
     "        return candidates[0][0]\n    return None\n"),
], "105 energy: what the Energy dashboard devices draw right now; the biggest plug is no whole-house meter")

PN105_LOCAL = '''async def _jh_power_now(hass, h):
    """Jarvis Home patch (2026-11-10): 'what is the power consumption' without a whole-house meter: what the monitored devices
    draw right now, read live, and that the house draws more. None when nothing can be read: then JARVIS answers."""
    from .energy import jh_devices_power_now
    now = await jh_devices_power_now(hass)
    if not now or not now["devices"]:
        return None

    def fmt(w):
        return f"{w / 1000:.1f} kW" if w >= 1000 else f"{w:.0f} W"

    count = len(now["devices"]) + len(now["unreadable"])
    line = f"Right now the {count} monitored devices draw {fmt(now['total_watts'])}, {h}."
    big = [d for d in now["devices"] if d["watts"] >= 1][:3]
    if big:
        line += " Most of it: " + ", ".join(f"{d['name']} {fmt(d['watts'])}" for d in big) + "."
    if now["unreadable"]:
        line += f" {', '.join(now['unreadable'][:3])} could not be read."
    return line + " There is no whole-house meter, so the house uses more."


'''
edit("local_engine.py", "async def _jh_power_now(", [
    ('async def try_local(hass, text, honorific="sir", force=False, pending_key=""):\n', lambda old: PN105_LOCAL + old),
    ("            resp = _ctx_query(hass, qtype, honorific, area_m)\n            if resp:\n",
     "            resp = _ctx_query(hass, qtype, honorific, area_m)\n"
     "            if not resp and qtype == \"energy\":   # Jarvis Home patch (2026-11-10): no whole-house meter - the devices, read live\n"
     "                resp = await _jh_power_now(hass, honorific)\n"
     "            if resp:\n"),
], "105 local_engine: 'what is the power consumption' answers what the monitored devices draw right now")

edit("agent.py", "_JH_LIVE_NUDGE", [
    ('                "autonomous) and never sheds critical loads. For energy USED over a "\n'
     '                "period (today, this month) use energy_usage."\n',
     '                "autonomous) and never sheds critical loads. Without a whole-house "\n'
     '                "meter, devices_now gives what the monitored devices draw right now "\n'
     '                "and what that covers. For energy USED over a "\n'
     '                "period (today, this month) use energy_usage."\n'),
    ("        res = await hass.async_add_executor_job(energy.power_status, None, states)\n        return json.dumps(res)\n",
     "        res = await hass.async_add_executor_job(energy.power_status, None, states)\n"
     "        if res.get(\"watts\") is None:   # Jarvis Home patch (2026-11-10): no whole-house meter - what the devices draw now\n"
     "            res[\"devices_now\"] = await energy.jh_devices_power_now(hass)\n"
     "        return json.dumps(res)\n"),
    ('_PROMISE_NUDGE = ("This is a command for a device and you called no tool, so nothing has happened. Call search_entities or the right tool now "\n'
     '                  "and answer from its result. If no tool can do it or no such device exists, say so plainly.")\n',
     lambda old: old + '''# Jarvis Home patch (2026-11-10): an energy or power figure needs a reading in this request. Earlier answers in the chat are out
# of date (1.0.3 repeated "64.01 kWh this month" from two days before, with no tool call). A figure the person said is fine.
_JH_LIVE_NUDGE = ("You gave an energy or power figure without reading it in this request. Earlier answers in this chat are out of date. "
                  "Call energy_status (the power right now) or energy_usage (energy used over a period) and answer from its result.")
_JH_FIGURE_RE = _re.compile(r"(\\d[\\d.,]*)\\s*(?:kwh|kilowatt[- ]hours?|kw|kilowatts?|w|watts?)\\b", _re.I)


def _jh_live_figure(text: str, said: str) -> bool:
    """The reply gives an energy or power figure that the person did not say themselves."""
    theirs = {m.group(1) for m in _JH_FIGURE_RE.finditer(said or "")}
    return any(m.group(1) not in theirs for m in _JH_FIGURE_RE.finditer(text or ""))
'''),
    ('                    _LOGGER.warning("JARVIS: reply to a device command not backed by a tool result, sent back once")\n'
     '                    working.append({"role": "assistant", "content": text})\n'
     '                    working.append({"role": "user", "content": _jh_back})\n'
     '                    continue\n'
     '            if _strict and text:\n'
     '                text = _jh_final(text, _jh_st, _cmd, _jh_hon, _asked)\n',
     '                    _LOGGER.warning("JARVIS: reply to a device command not backed by a tool result, sent back once")\n'
     '                    working.append({"role": "assistant", "content": text})\n'
     '                    working.append({"role": "user", "content": _jh_back})\n'
     '                    continue\n'
     '            # Jarvis Home patch (2026-11-10): an energy or power figure with no reading behind it in this request\n'
     '            if (_strict and not _nudged and tools and text and not _jh_st["step"]\n'
     '                    and _jh_live_figure(text, getattr(user_input, "text", "") or "")):\n'
     '                _nudged = True\n'
     '                _LOGGER.warning("JARVIS: energy or power figure with no reading behind it, sent back once")\n'
     '                working.append({"role": "assistant", "content": text})\n'
     '                working.append({"role": "user", "content": _JH_LIVE_NUDGE})\n'
     '                continue\n'
     '            if _strict and text:\n'
     '                text = _jh_final(text, _jh_st, _cmd, _jh_hon, _asked)\n'),
], "105 agent: power right now in energy_status; an energy or power figure needs a reading in this request")

# 106 ----------------------------------------------------------------------------------------
# Live check of 1.0.4 (2026-11-11): "Set Ufo to Blue sky" (a lamp Effect: a Smart Life scene) took 75 s and was answered
# "Done, sir. Ufo is now at blue sky color" while the lamp did not change: the instant colour path only knows colour names
# ("blue sky" is none), and the model has no tool for a lamp's effects, so it switched the lamp on and claimed the rest.
#  - The instant path now also starts a lamp's own effect by name: "set/switch/turn/put <lamp> to <effect>" and
#    "start/play <effect> on <lamp>". The name must be in that lamp's effect_list (read live, so every effect or scene a
#    lamp offers works, and a new one works without a change here). "Done" only when the lamp reports that effect.
#  - Anything else (no such lamp, no such effect) falls through unchanged.
PN106_LOCAL = r'''_JH_EFFECT_TO_RE = re.compile(r"^(?:(?:hey\s+)?jarvis[,\s]+)?(?:please\s+|could you\s+|can you\s+|would you\s+)*"
                              r"(?:switch|set|change|make|turn|put|start)\s+(?:the\s+)?(.+?)\s+(?:to|in|on)\s+(?:the\s+)?(.+?)"
                              r"(?:\s+(?:scene|effect|mode))?$")
_JH_EFFECT_ON_RE = re.compile(r"^(?:(?:hey\s+)?jarvis[,\s]+)?(?:please\s+|could you\s+|can you\s+|would you\s+)*"
                              r"(?:start|play|show|put on)\s+(?:the\s+)?(.+?)(?:\s+(?:scene|effect|mode))?\s+on\s+(?:the\s+)?(.+?)$")


async def _jh_try_effect(hass, normalized, honorific):
    """Jarvis Home patch (2026-11-11): start a lamp's own effect or scene by its name, read from the lamp's effect_list."""
    said = _jh_opening(normalized)[0]
    while _JH_TAIL_RE.search(said):
        said = _JH_TAIL_RE.sub("", said)
    pairs = []
    m = _JH_EFFECT_TO_RE.match(said)
    if m:
        pairs.append((m.group(1), m.group(2)))
    m = _JH_EFFECT_ON_RE.match(said)
    if m:
        pairs.append((m.group(2), m.group(1)))
    for lamp, wanted in pairs:
        if lamp.strip() in _JH_PRONOUNS or not wanted.strip():
            continue
        found = _find_entity(hass, lamp, "light")
        if not found or not str(found[0]).startswith("light."):
            continue
        entity_id, fname = found
        state = hass.states.get(entity_id)
        if state is None:
            continue
        pick = next((e for e in state.attributes.get("effect_list") or []
                     if str(e).lower() not in ("off", "none") and str(e).lower() == wanted.strip().lower()), None)
        if pick is None:
            continue
        if state.state in ("unavailable", "unknown"):
            return LocalResult(text=f"{fname} is offline, {honorific}. Nothing was changed.", success=False)
        if _needs_confirmation(hass, "turn_on", entity_id):
            return None
        if not await _seam_execute(hass, "light", "turn_on", {"entity_id": entity_id, "effect": pick},
                                   blocking=True, action="turn_on"):
            return LocalResult(text=f"I wasn't able to start {pick} on {fname}, {honorific}.", success=False)
        _update_ctx(entity=entity_id, domain="light", action="turn_on")
        import asyncio
        for _ in range(8):
            now = hass.states.get(entity_id)
            if now is not None and now.state == "on" and now.attributes.get("effect") == pick:
                return LocalResult(text=f"Done, {honorific}. {fname} is playing {pick}.", success=True)
            await asyncio.sleep(0.25)
        return LocalResult(text=f"I sent {pick} to {fname}, {honorific}, but it has not confirmed the change.", success=False)
    return None


'''
edit("local_engine.py", "async def _jh_try_effect(", [
    ("_JH_TYPE_WORDS = {", lambda old: PN106_LOCAL + old),
    ("    _jh_color = await _jh_try_color(hass, normalized, honorific, pending_key)\n",
     "    _jh_color = await _jh_try_color(hass, normalized, honorific, pending_key)\n"
     "    if _jh_color is None:   # Jarvis Home patch (2026-11-11): a lamp's own effect or scene by name\n"
     "        _jh_color = await _jh_try_effect(hass, normalized, honorific)\n"),
], "106 local_engine: start a lamp's effect or scene by name (from its effect_list), Done only when the lamp shows it")

# 107 ----------------------------------------------------------------------------------------
# Phone layout (2026-11-11): at phone width the header logo text ran off the screen (version + status badge beside it),
# the chat box got a third of the row, the Faces camera list stuck out of its card, Logs categories overlapped the text
# and long entity ids stuck out of Suggestions. Phone-only rules; the desktop layout is unchanged.
PN107_CSS = r"""  /* jh-phone-fit 2026-11-11 */
  @media (max-width: 600px) {
    .masthead { grid-template-columns: auto minmax(0, 1fr); }
    .masthead .greeting { display: none; }
    .masthead-right { grid-column: 1 / -1; justify-content: space-between; }
    .brand { min-width: 0; justify-content: flex-end; gap: 10px; letter-spacing: 0.18em; white-space: nowrap; }
    .brand > span { display: none; }   /* version and status badge: the lockdown switch still shows the state */
    .chat-compose { flex-wrap: wrap; }
    .chat-compose .chat-input { flex: 1 1 100%; }   /* full-width box, buttons on a second row */
    .chat-compose .chat-input::placeholder { font-size: 11px; }
    .chat-voice { flex: 1 1 auto; }
    .chat-compose .chat-send { flex: 0 0 auto; margin-left: auto; }
    .faces-card { min-width: 0; }
    .faces-card-snap { justify-content: center; max-width: 100%; }
    .faces-card-snap .faces-snap-cam { max-width: 100%; min-width: 0; }
    .log-entry { grid-template-columns: auto 1fr; gap: 2px 10px; }
    .log-entry .log-msg { grid-column: 1 / -1; }   /* time and category on one line, the message below */
    .sug-ev, .sug-ev li { overflow-wrap: anywhere; }
  }
"""
edit("frontend/jarvis-panel.js", "jh-phone-fit", [
    ("  /* ───────────────── very small phones (≤380px) ───────────────── */",
     lambda old: PN107_CSS + old),
], "107 panel: phone layout (header, chat box, Faces, Logs, Suggestions fit a phone screen)")

# 99 -----------------------------------------------------------------------------------------
commit()

# 10 ---- (left out) the original kit copied the owner's private dashboard pages here; Clean Jarvis does not ship them.

print("\n".join(results))
if _failed():
    print("\nFAILED: fix the lines above. Do not restart Home Assistant yet.")
    sys.exit(1)
if any(r.startswith(("[applied]", "[copied ]")) for r in results):
    print("\nNEXT: restart Home Assistant, then reload the JARVIS integration and press Ctrl+F5.")
