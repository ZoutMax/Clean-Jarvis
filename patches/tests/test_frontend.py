"""Frontend regression tests in real headless Chrome: the patched jarvis-panel.js, with a fake hass.
Pages are served from 127.0.0.1 and Chrome is blocked from every other host, so the live site cannot be reached.

Run:  python -m pytest patches/tests/test_frontend.py   (JARVIS_BUILT as in jh_harness.py)
"""
import json, os, sys, tempfile, time, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cdp_lite, jh_harness

KIT = os.path.dirname(HERE)
CARDS = ()   # the owner's private dashboard cards are not part of Clean Jarvis
PAGE = '<!doctype html><html><body>%s<script src="/frontend_fake.js"></script></body></html>'
browser = None
tmp = None


def setUpModule():
    global browser, tmp
    if not cdp_lite.find_chrome():
        raise unittest.SkipTest("Chrome is not installed, so the frontend was NOT tested")
    panel = os.path.join(jh_harness.built_dir(), "frontend", "jarvis-panel.js")
    tmp = tempfile.TemporaryDirectory()
    files = {"/frontend_fake.js": os.path.join(HERE, "frontend_fake.js"), "/jarvis-panel.js": panel}
    for name in CARDS:
        files["/%s.js" % name] = os.path.join(KIT, name + ".js")
    for name, scripts in {"panel": ["jarvis-panel"], **{c: [c] for c in CARDS}}.items():
        path = os.path.join(tmp.name, name + ".html")
        with open(path, "w", encoding="utf-8") as f:
            f.write(PAGE % "".join('<script src="/%s.js"></script>' % s for s in scripts))
        files["/%s.html" % name] = path
    browser = cdp_lite.Browser(files)


def tearDownModule():
    if browser:
        browser.close()
    if tmp:
        tmp.cleanup()


class Base(unittest.TestCase):
    page = None

    def setUp(self):
        browser.open("/%s.html" % self.page)

    def tearDown(self):
        self.assertEqual(browser.errors, [], "uncaught errors in the page")


def msgs(side="p"):
    return "%s._chatMessages.map(m => m.text)" % side


class ChatHistory(Base):
    page = "panel"

    def test_two_devices_keep_each_others_messages(self):
        r = browser.run("""
          const store = mkStore({ messages: [{ role: 'user', text: 'old' }] });
          const pc = await mkPanel(mkHass({ store })), phone = await mkPanel(mkHass({ store }));
          await sleep(30);
          await say(phone, 'from phone');     // the PC does not know about this one
          await say(pc, 'from pc');
          return { stored: store.value.messages.map(m => m.text), pc: pc._chatMessages.map(m => m.text) };""")
        for text in ("old", "from phone", "from pc"):
            self.assertIn(text, r["stored"])
            self.assertIn(text, r["pc"])
        self.assertEqual(len(r["stored"]), 5)     # old + 2 questions + 2 replies

    def test_a_deleted_message_does_not_come_back_from_the_other_device(self):
        r = browser.run("""
          const store = mkStore(null);
          const pc = await mkPanel(mkHass({ store }));
          await say(pc, 'keep'); await say(pc, 'delete me');
          const phone = await mkPanel(mkHass({ store })); await sleep(30);
          pc._deleteChatMessage(pc._chatMessages.findIndex(m => m.text === 'delete me'));
          await pc._chatSaving;
          await say(phone, 'later');
          return { stored: store.value.messages.map(m => m.text), phone: phone._chatMessages.map(m => m.text) };""")
        self.assertNotIn("delete me", r["stored"])
        self.assertNotIn("delete me", r["phone"])
        self.assertIn("keep", r["stored"])

    def test_a_failed_load_cannot_wipe_the_history(self):
        r = browser.run("""
          const saved = Array.from({ length: 150 }, (_, i) => ({ role: 'user', text: 'm' + i }));
          const store = mkStore({ messages: saved });
          store.failRead = true;
          const h = mkHass({ store }), p = await mkPanel(h);
          await say(p, 'hello');
          const writesWhileFailing = store.writes.length, loaded = p._chatLoaded;
          store.failRead = false;
          await say(p, 'again');
          return { writesWhileFailing, loaded, n: store.value.messages.length, shown: p._chatMessages.length,
                   first: store.value.messages[0].text, last: store.value.messages.at(-1).text };""")
        self.assertEqual(r["writesWhileFailing"], 0)
        self.assertFalse(r["loaded"])
        self.assertEqual(r["n"], 154)
        self.assertEqual(r["shown"], 154)
        self.assertEqual(r["first"], "m0")
        self.assertEqual(r["last"], "Very good, sir.")

    def test_history_saved_by_the_old_build_keeps_its_order(self):
        r = browser.run("""
          const store = mkStore({ messages: [{ role: 'user', text: 'a' }, { role: 'jarvis', text: 'b' }, { role: 'user', text: 'a' }] });
          const p = await mkPanel(mkHass({ store }));
          await sleep(30);
          await say(p, 'new');
          return store.value.messages.map(m => m.text);""")
        self.assertEqual(r, ["a", "b", "a", "new", "Very good, sir."])

    def test_a_message_sent_during_a_save_is_not_lost(self):
        r = browser.run("""
          const store = mkStore(null), h = mkHass({ store }), p = await mkPanel(h);
          await sleep(20);
          p._chatMessages.push({ role: 'user', text: 'one' });
          const s = p._saveChat();
          p._chatMessages.push({ role: 'user', text: 'two' });
          await s; await p._saveChat();
          return store.value.messages.map(m => m.text);""")
        self.assertEqual(r, ["one", "two"])

    def test_a_message_from_a_still_open_old_panel_stays_at_the_end(self):
        r = browser.run("""
          const store = mkStore({ messages: [{ role: 'user', text: 'new 1', id: 'a', t: 1700000000000 },
                                             { role: 'jarvis', text: 'new 2', id: 'b', t: 1700000001000 },
                                             { role: 'user', text: 'typed on the old panel' }] });
          const p = await mkPanel(mkHass({ store }));
          await sleep(30);
          const shown = p._chatMessages.map(m => m.text);
          await say(p, 'later');
          return { shown, stored: store.value.messages.map(m => m.text) };""")
        self.assertEqual(r["shown"], ["new 1", "new 2", "typed on the old panel"])
        self.assertEqual(r["stored"], ["new 1", "new 2", "typed on the old panel", "later", "Very good, sir."])

    def test_two_devices_saving_at_the_same_moment_keep_both_messages(self):
        r = browser.run("""
          const store = mkStore({ messages: [] });
          const ha = mkHass({ store }), hb = mkHass({ store });
          const a = await mkPanel(ha), b = await mkPanel(hb);
          await sleep(30);
          let readers = 0, open; const both = new Promise(x => { open = x; });
          for (const h of [ha, hb]) {     // each device finishes its read before either one writes
            const ws = h.callWS; let first = true;
            h.callWS = async (m) => {
              const res = await ws(m);
              if (m.type === 'frontend/get_user_data' && first) { first = false; if (++readers === 2) open(); await both; }
              return res;
            };
          }
          a._chatMessages.push({ role: 'user', text: 'from A' });
          b._chatMessages.push({ role: 'user', text: 'from B' });
          await Promise.all([a._saveChat(), b._saveChat()]);
          return store.value.messages.map(m => m.text).sort();""")
        self.assertEqual(r, ["from A", "from B"])

    def test_a_save_that_nobody_overwrote_writes_once(self):
        r = browser.run("""
          const store = mkStore(null), p = await mkPanel(mkHass({ store }));
          await sleep(30);
          await say(p, 'one');
          return store.writes.length;""")
        self.assertEqual(r, 1)    # one write for the exchange, nothing extra from the check

    def test_clean_wipes_what_another_device_stored(self):
        r = browser.run("""
          const store = mkStore({ messages: [{ role: 'user', text: 'x', id: 'x1', t: 5 }] });
          const p = await mkPanel(mkHass({ store })); await sleep(20);
          store.value.messages.push({ role: 'user', text: 'y', id: 'y1', t: 6 });
          p._clearChat(); await p._chatSaving;
          const phone = await mkPanel(mkHass({ store })); await sleep(30);
          return { stored: store.value.messages.length, phone: phone._chatMessages.length };""")
        self.assertEqual(r, {"stored": 0, "phone": 0})

    def test_clean_while_thinking_keeps_the_reply_out(self):
        r = browser.run("""
          const store = mkStore(null), h = mkHass({ store }), p = await mkPanel(h);
          let go; h.pending = new Promise(x => { go = x; });
          p._chatSpeak = true;
          let paused = 0; p._chatAudio = { pause() { paused++; } };
          p.shadowRoot.getElementById('chat-input').value = 'question';
          const sent = p._sendChat();
          await sleep(10);
          p._clearChat();
          const busy = p._chatBusy;
          go(); await sent; await p._chatSaving; await sleep(20);
          return { busy, paused, msgs: p._chatMessages.length, conv: p._chatConvId, stored: store.value.messages.length, speech: h.runs.length,
                   input: p.shadowRoot.getElementById('chat-input').disabled };""")
        self.assertEqual(r, {"busy": False, "paused": 1, "msgs": 0, "conv": None, "stored": 0, "speech": 0, "input": False})

    def test_clean_asks_first_on_the_page_and_cancel_keeps_everything(self):
        r = browser.run("""
          const store = mkStore({ messages: [{ role: 'user', text: 'keep me', id: 'k1', t: 5 }] });
          const p = await mkPanel(mkHass({ store })); await sleep(30);
          let dialogs = 0; window.confirm = () => { dialogs++; return true; };
          const $ = (id) => p.shadowRoot.getElementById(id), writes = store.writes.length;
          $('chat-clear').click(); await sleep(20);
          const asking = { clean: $('chat-clear').hidden, ask: $('chat-ask').hidden, text: $('chat-ask').textContent, focus: p.shadowRoot.activeElement?.id,
                           msgs: p._chatMessages.length, writes: store.writes.length - writes };
          p._render(); await sleep(20);
          const afterRender = { clean: $('chat-clear').hidden, ask: $('chat-ask').hidden };
          $('chat-ask-no').click(); await sleep(20);
          return { asking, afterRender, cancelled: { clean: $('chat-clear').hidden, ask: $('chat-ask').hidden }, msgs: p._chatMessages.map(m => m.text),
                   stored: store.value.messages.map(m => m.text), writes: store.writes.length - writes, dialogs };""")
        self.assertEqual(r["asking"], {"clean": True, "ask": False, "text": "Delete all messages on every device?CANCELYES, DELETE ALL",
                                       "focus": "chat-ask-no", "msgs": 1, "writes": 0})
        self.assertEqual(r["afterRender"], {"clean": True, "ask": False})
        self.assertEqual(r["cancelled"], {"clean": False, "ask": True})
        self.assertEqual(r["msgs"], ["keep me"])
        self.assertEqual(r["stored"], ["keep me"])
        self.assertEqual(r["writes"], 0)
        self.assertEqual(r["dialogs"], 0, "no browser dialog")

    def test_clean_then_yes_deletes_on_every_device(self):
        r = browser.run("""
          const store = mkStore({ messages: [{ role: 'user', text: 'x', id: 'x1', t: 5 }, { role: 'jarvis', text: 'y', id: 'y1', t: 6 }] });
          const p = await mkPanel(mkHass({ store })); await sleep(30);
          const $ = (id) => p.shadowRoot.getElementById(id);
          $('chat-clear').click(); await sleep(10);
          $('chat-ask-yes').click(); await p._chatSaving;
          const phone = await mkPanel(mkHass({ store })); await sleep(30);
          return { stored: store.value.messages.length, msgs: p._chatMessages.length, phone: phone._chatMessages.length, clean: $('chat-clear').hidden, ask: $('chat-ask').hidden };""")
        self.assertEqual(r, {"stored": 0, "msgs": 0, "phone": 0, "clean": False, "ask": True})

    def test_a_double_tap_on_clean_cannot_confirm_and_the_question_closes_by_itself(self):
        r = browser.run("""
          const store = mkStore({ messages: [{ role: 'user', text: 'keep me', id: 'k1', t: 5 }] });
          const p = await mkPanel(mkHass({ store })); await sleep(30);
          p._chatAskWait = 150;
          const $ = (id) => p.shadowRoot.getElementById(id);
          $('chat-clear').scrollIntoView();
          const b = $('chat-clear').getBoundingClientRect(), x = b.x + b.width / 2, y = b.y + b.height / 2;
          $('chat-clear').click(); await sleep(10);
          const under = p.shadowRoot.elementFromPoint(x, y);
          const second = under ? (under.closest('#chat-ask-yes') ? 'yes' : under.closest('#chat-ask-no') ? 'no' : 'other') : 'none';
          under?.click(); await sleep(250);
          return { second, ask: $('chat-ask').hidden, clean: $('chat-clear').hidden, stored: store.value.messages.length, msgs: p._chatMessages.length };""")
        self.assertNotEqual(r["second"], "yes")
        self.assertEqual({k: r[k] for k in ("ask", "clean", "stored", "msgs")}, {"ask": True, "clean": False, "stored": 1, "msgs": 1})

    def test_the_question_text_starts_where_clean_was_at_every_width(self):
        # review 2: the comment said CANCEL sits where CLEAN was. Measured, the question text starts there (at some widths CLEAN's
        # centre is on the title line just before it), so a second tap on CLEAN's spot hits no button at all.
        try:
            for w in (320, 375, 412, 600, 768, 1100):
                browser.cdp("Emulation.setDeviceMetricsOverride", width=w, height=800, deviceScaleFactor=1, mobile=False)
                browser.open("/panel.html")
                r = browser.run("""
                  const p = await mkPanel(mkHass({ store: mkStore({ messages: [{ role: 'user', text: 'keep me', id: 'k1', t: 5 }] }) })); await sleep(30);
                  const $ = (id) => p.shadowRoot.getElementById(id);
                  $('chat-clear').scrollIntoView();
                  const b = $('chat-clear').getBoundingClientRect();
                  const spots = [[b.x + b.width / 2, b.y + b.height / 2], [b.x + 3, b.y + 3], [b.x + b.width - 3, b.y + b.height - 3]];
                  $('chat-clear').click(); await sleep(20);
                  return spots.map(([x, y]) => { const u = p.shadowRoot.elementFromPoint(x, y);
                    return !u ? 'none' : u.closest('#chat-ask-yes') ? 'yes' : u.closest('#chat-ask-no') ? 'cancel' : u.id || u.tagName; });""")
                self.assertNotIn("yes", r, "width %d" % w)
                self.assertNotIn("cancel", r, "width %d" % w)
                self.assertIn("chat-ask", r, "width %d: the question text is not where CLEAN was" % w)
                self.assertTrue(set(r) <= {"chat-ask", "SPAN"}, "width %d: %s" % (w, r))   # the question text or the title line
        finally:
            browser.cdp("Emulation.clearDeviceMetricsOverride")

    def test_the_red_cross_has_a_bigger_touch_target_in_the_same_place(self):
        r = browser.run("""
          const p = await mkPanel(mkHass({ store: mkStore({ messages: [{ role: 'user', text: 'one message', id: 'a', t: 5 }] }) })); await sleep(30);
          const x = p.shadowRoot.querySelector('.chat-del').getBoundingClientRect(), m = p.shadowRoot.querySelector('.chat-msg').getBoundingClientRect();
          return { w: x.width, h: x.height, right: Math.round(m.right - x.right), top: Math.round(x.top - m.top) };""")
        self.assertGreaterEqual(r["w"], 26)
        self.assertGreaterEqual(r["h"], 26)
        self.assertLessEqual(r["right"], 1)    # in the corner, inside the message's 1 px border
        self.assertLessEqual(r["top"], 1)


class Mic(Base):
    page = "panel"

    def test_a_tap_during_the_permission_prompt_starts_nothing(self):
        r = browser.run("""
          const g = holdMic(); g.hold();
          const h = mkHass(), p = await mkPanel(h);
          press(p); await sleep(20); release(p); await sleep(20);
          g.open(); await until(() => g.stops); await sleep(80);   // the browser's own mic start can take longer than any fixed wait
          return { runs: h.runs.length, stops: g.stops, msgs: p._chatMessages.length, stop: !!p._micStop, on: p.shadowRoot.getElementById('chat-mic').classList.contains('on') };""")
        self.assertEqual(r, {"runs": 0, "stops": 1, "msgs": 0, "stop": False, "on": False})

    def test_release_before_run_start_ends_the_run_and_shows_no_false_error(self):
        r = browser.run("""
          holdMic();
          const h = mkHass(), p = await mkPanel(h);
          press(p); await started(h);
          const run = h.runs[0];
          release(p); await sleep(20);
          const before = h.binary.length;
          run.cb({ type: 'run-start', data: { runner_data: { stt_binary_handler_id: 5 } } });
          const ends = h.binary.slice(before).filter(b => b[0] === 5 && b.at(-1) === 1).length;
          run.cb({ type: 'error', data: { code: 'stt-no-text-recognized', message: 'No text recognized' } });
          await sleep(30);
          return { ends, unsub: run.unsubbed, msgs: p._chatMessages.length };""")
        self.assertEqual(r, {"ends": 1, "unsub": 1, "msgs": 0})

    def test_a_real_error_while_held_is_still_shown(self):
        r = browser.run("""
          holdMic();
          const h = mkHass(), p = await mkPanel(h);
          press(p); await started(h);
          const run = h.runs[0];
          run.cb({ type: 'run-start', data: { runner_data: { stt_binary_handler_id: 5 } } });
          run.cb({ type: 'error', data: { code: 'stt-stream-failed', message: 'Speech failed' } });
          await sleep(30);
          return { text: p._chatMessages.map(m => m.text), stop: !!p._micStop };""")
        self.assertEqual(r, {"text": ["Voice: Speech failed"], "stop": False})

    def test_audio_setup_failure_frees_the_mic_and_says_so(self):
        r = browser.run("""
          const g = holdMic();
          window.AudioContext = class { constructor() { throw new Error('no audio here'); } };
          const h = mkHass(), p = await mkPanel(h);
          press(p); await until(() => p._chatMessages.length);
          return { stops: g.stops, text: p._chatMessages.map(m => m.text), runs: h.runs.length, stop: !!p._micStop, starting: !!p._micStarting };""")
        self.assertEqual(r, {"stops": 1, "text": ["Microphone not available: no audio here"], "runs": 0, "stop": False, "starting": False})

    def test_a_browser_that_refuses_16khz_falls_back_to_its_own_rate(self):
        r = browser.run("""
          holdMic();
          const Real = window.AudioContext;
          window.AudioContext = class extends Real {
            constructor(o) { super(o && {}); this.asked = o; }
            createMediaStreamSource(s) { if (this.asked) throw new Error('rates differ'); return super.createMediaStreamSource(s); }
          };
          const h = mkHass(), p = await mkPanel(h);
          press(p); await started(h);
          const rate = h.runs[0].msg.input.sample_rate, real = p._chatMessages.length;
          release(p); await sleep(20);
          return { rate, real, native: new Real().sampleRate };""")
        self.assertEqual(r["rate"], round(r["native"]))
        self.assertEqual(r["real"], 0)

    def test_press_release_press_makes_one_recording(self):
        r = browser.run("""
          const g = holdMic(); g.hold();
          const h = mkHass(), p = await mkPanel(h);
          press(p); await sleep(10); release(p); await sleep(10); press(p); await sleep(10);
          g.open(); await started(h);
          const calls = g.calls, runs = h.runs.length, on = !!p._micStop;
          release(p); await sleep(30);
          return { calls, runs, on, off: !p._micStop, stops: g.stops };""")
        self.assertEqual(r, {"calls": 1, "runs": 1, "on": True, "off": True, "stops": 1})

    def test_hold_asks_for_no_vad_and_listening_shows_after_run_start(self):
        r = browser.run("""
          holdMic();
          const h = mkHass(), p = await mkPanel(h);
          press(p); await started(h); await sleep(700);
          const input = p.shadowRoot.getElementById('chat-input');
          const before = input.placeholder, early = h.binary.length;
          h.runs[0].cb({ type: 'run-start', data: { runner_data: { stt_binary_handler_id: 9 } } });
          const flushed = h.binary.length, during = input.placeholder;
          await until(() => h.binary.length > flushed);
          const live = h.binary.length;
          release(p); await sleep(30);
          return { noVad: h.runs[0].msg.input.no_vad, before, during, after: input.placeholder, early, flushed, live, tag: h.binary.every(b => b[0] === 9) };""")
        self.assertTrue(r["noVad"])
        self.assertNotEqual(r["before"], "Listening...")
        self.assertEqual(r["during"], "Listening...")
        self.assertEqual(r["after"], r["before"])
        self.assertEqual(r["early"], 0)
        self.assertGreater(r["flushed"], 0, "audio from before HA was ready must not be dropped")
        self.assertGreater(r["live"], r["flushed"])
        self.assertTrue(r["tag"])

    def test_a_home_assistant_that_refuses_no_vad_still_records(self):
        r = browser.run("""
          holdMic();
          const h = mkHass(), p = await mkPanel(h);
          h.runRefuse = (m) => m.input.no_vad ? { code: 'invalid_format', message: 'extra keys' } : null;
          press(p); await started(h);
          const out = { runs: h.runs.length, noVad: h.runs[0]?.msg.input.no_vad ?? null, msgs: p._chatMessages.length, on: !!p._micStop };
          release(p); await sleep(30);
          return out;""")
        self.assertEqual(r, {"runs": 1, "noVad": None, "msgs": 0, "on": True})

    def test_a_refused_run_frees_the_mic_and_says_so(self):
        r = browser.run("""
          const g = holdMic();
          const h = mkHass(), p = await mkPanel(h);
          h.runRefuse = () => ({ code: 'pipeline-not-found', message: 'No pipeline' });
          press(p); await until(() => p._chatMessages.length);
          return { text: p._chatMessages.map(m => m.text), stops: g.stops, stop: !!p._micStop };""")
        self.assertEqual(r, {"text": ["Voice: No pipeline"], "stops": 1, "stop": False})

    def test_real_microphone_stream_reaches_the_run(self):
        r = browser.run("""
          const h = mkHass(), p = await mkPanel(h);
          press(p); await started(h);
          h.runs[0].cb({ type: 'run-start', data: { runner_data: { stt_binary_handler_id: 3 } } });
          await sleep(500);
          release(p); await sleep(50);
          const frames = h.binary.filter(b => b.at(-1) > 1).length, end = h.binary.at(-1);
          h.runs[0].cb({ type: 'stt-end', data: { stt_output: { text: 'lights on' } } });
          await sleep(30);
          return { frames, end, box: p.shadowRoot.getElementById('chat-input').value, unsub: h.runs[0].unsubbed };""")
        self.assertGreater(r["frames"], 1)
        self.assertEqual(r["end"], [3, 1])
        self.assertEqual(r["box"], "lights on")
        self.assertEqual(r["unsub"], 1)


WAKE = """
  window.mkWake = async (o = {}) => {
    const h = mkHass();
    h.pipelineRows = [{ id: 'p1', name: 'Assistant', conversation_engine: 'conversation.jarvis', wake_word_entity: 'wake_word.engine', wake_word_id: 'word', ...(o.row || {}) }];
    const p = await mkPanel(h);
    p._wakeBase = o.base ?? 500;
    return { h, p };
  };
  window.wakeBtn = (p) => p.shadowRoot.getElementById('chat-wake');
  window.wakeRuns = (h) => h.runs.filter(r => r.msg.start_stage === 'wake_word');
  window.wakeOn = async (p, h, n = 1) => { wakeBtn(p).click(); await until(() => wakeRuns(h).length >= n); };
  window.begin = (r, id = 6) => r.cb({ type: 'run-start', data: { runner_data: { stt_binary_handler_id: id } } });
  window.heard = (r) => r.cb({ type: 'wake_word-end', data: { wake_word_output: { wake_word_id: 'word', wake_word_phrase: 'Word', timestamp: 1 } } });
  window.words = (r, text) => r.cb({ type: 'stt-end', data: { stt_output: { text } } });
"""

# Hold to talk, one utterance per call: press, Home Assistant is ready, release, the words come back.
TALK = """
  window.talker = (p, h) => {
    let n = h.runs.length;
    return async (text) => {
      press(p); await until(() => h.runs.length > n);
      const run = h.runs.at(-1); n = h.runs.length;
      run.cb({ type: 'run-start', data: { runner_data: { stt_binary_handler_id: 2 } } });
      release(p); await sleep(20);
      run.cb({ type: 'stt-end', data: { stt_output: { text } } });
    };
  };
  window.asked = (h) => h.ws.filter(m => m.type === 'conversation/process').map(m => m.text);
"""


class HeyJarvis(Base):
    """The wake word switch, against a fake pipeline that behaves as Home Assistant 2026.11.0's does."""
    page = "panel"

    def setUp(self):
        super().setUp()
        browser.js("localStorage.clear()")
        browser.run(WAKE + "return 1;")

    def tearDown(self):
        browser.js("localStorage.clear()")
        super().tearDown()

    def test_off_by_default_nothing_listens(self):
        r = browser.run("""
          const g = holdMic(); const { h, p } = await mkWake(); await sleep(50);
          const b = wakeBtn(p);
          return { calls: g.calls, runs: h.runs.length, on: b.classList.contains('on'), pressed: b.getAttribute('aria-pressed'), title: b.title, beside: b.previousElementSibling.id };""")
        self.assertEqual(r["calls"], 0)
        self.assertEqual(r["runs"], 0)
        self.assertFalse(r["on"])
        self.assertEqual(r["pressed"], "false")
        self.assertIn("off", r["title"])
        self.assertEqual(r["beside"], "chat-mic")

    def test_the_switch_starts_exactly_the_run_home_assistant_expects(self):
        r = browser.run("""
          holdMic(); const { h, p } = await mkWake(); await wakeOn(p, h);
          const b = wakeBtn(p), run = h.runs[0];
          return { msg: run.msg, opts: run.opts, stored: localStorage.getItem('jarvis_chat_wake'), on: b.classList.contains('on'), pressed: b.getAttribute('aria-pressed'), title: b.title,
                   ctx: Math.round(p._wakeMic.ctx.sampleRate) };""")
        self.assertEqual(r["msg"], {"type": "assist_pipeline/run", "pipeline": "p1", "start_stage": "wake_word", "end_stage": "stt",
                                    "input": {"sample_rate": r["ctx"], "timeout": 0, "no_vad": True}, "timeout": 300})
        self.assertEqual(r["ctx"], 16000)
        self.assertEqual(r["opts"], {"resubscribe": False})
        self.assertEqual(r["stored"], "1")
        self.assertTrue(r["on"])
        self.assertEqual(r["pressed"], "true")
        self.assertIn("on", r["title"])

    def test_audio_goes_only_to_the_handler_of_the_run_and_only_after_run_start(self):
        r = browser.run("""
          let other = 0;
          const f = window.fetch; window.fetch = (...a) => { other++; return f(...a); };
          const { h, p } = await mkWake(); await wakeOn(p, h);
          await sleep(500);
          const before = h.binary.length;
          begin(h.runs[0], 6);
          await until(() => h.binary.length > 1);
          return { before, frames: h.binary.length, tags: [...new Set(h.binary.map(b => b[0]))], other };""")
        self.assertEqual(r["before"], 0)
        self.assertGreater(r["frames"], 1)
        self.assertEqual(r["tags"], [6])
        self.assertEqual(r["other"], 0)

    def test_the_wake_word_gives_a_short_beep_and_nothing_else_does(self):
        r = browser.run("""
          holdMic(); const { h, p } = await mkWake(); await wakeOn(p, h);
          let beeps = 0; const real = p._wakeBeep.bind(p); p._wakeBeep = () => { beeps++; real(); };
          const run = h.runs[0]; begin(run);
          run.cb({ type: 'wake_word-end', data: { wake_word_output: {} } });
          const quiet = beeps;
          heard(run);
          return { quiet, after: beeps };""")
        self.assertEqual(r, {"quiet": 0, "after": 1})

    def test_a_pause_after_the_wake_word_does_not_end_the_command(self):
        r = browser.run("""
          holdMic(); const { h, p } = await mkWake(); await wakeOn(p, h);
          Object.assign(p, { _wakeSkip: 0, _wakeQuiet: 120, _wakeWait: 5000 });
          p._wakeMic.proc.onaudioprocess = null;   // only the levels below, not the fake device's beep
          const run = h.runs[0]; begin(run); heard(run);
          const conn = p._hass.connection, quiet = new Int16Array(512), loud = new Int16Array(512).fill(5000);
          const ends = () => h.binary.filter(b => b[0] === 6 && b.length === 2 && b[1] === 1).length;
          for (let i = 0; i < 5; i++) { p._wakeLevel(conn, p._wakeRun, quiet); await sleep(40); }
          const afterPause = { ends: ends(), hot: wakeBtn(p).classList.contains('hot') };
          p._wakeLevel(conn, p._wakeRun, loud); await sleep(60); p._wakeLevel(conn, p._wakeRun, quiet);
          const talking = ends();
          await sleep(150); p._wakeLevel(conn, p._wakeRun, quiet);
          return { afterPause, talking, done: ends(), hid: p._wakeRun?.hid ?? null };""")
        self.assertEqual(r, {"afterPause": {"ends": 0, "hot": True}, "talking": 0, "done": 1, "hid": None})

    def test_no_speech_after_the_wake_word_sends_nothing(self):
        r = browser.run("""
          holdMic(); const { h, p } = await mkWake({ base: 20 }); await wakeOn(p, h);
          Object.assign(p, { _wakeSkip: 0, _wakeWait: 80 });
          p._wakeMic.proc.onaudioprocess = null;
          begin(h.runs[0]); heard(h.runs[0]); const run = p._wakeRun;
          await sleep(120); p._wakeLevel(p._hass.connection, run, new Int16Array(512));
          await until(() => wakeRuns(h).length > 1);
          return { over: run.over, hot: wakeBtn(p).classList.contains('hot'), sent: h.ws.filter(m => m.type === 'conversation/process').length };""")
        self.assertEqual(r, {"over": True, "hot": False, "sent": 0})

    def test_a_lone_filler_word_from_silence_is_not_sent(self):
        r = browser.run("""
          holdMic(); const { h, p } = await mkWake({ base: 20 }); await wakeOn(p, h);
          const run = h.runs[0]; begin(run); heard(run); words(run, 'Okay.');
          await until(() => wakeRuns(h).length > 1); await sleep(1400);
          return { box: p.shadowRoot.getElementById('chat-input').value, sent: h.ws.filter(m => m.type === 'conversation/process').length };""")
        self.assertEqual(r, {"box": "", "sent": 0})

    def test_nothing_heard_shows_no_listening_and_starts_again(self):
        r = browser.run("""
          holdMic(); const { h, p } = await mkWake({ base: 20 }); await wakeOn(p, h);
          const run = h.runs[0]; begin(run);
          const box = p.shadowRoot.getElementById('chat-input'), before = box.placeholder;
          run.cb({ type: 'wake_word-end', data: { wake_word_output: {} } });
          const during = box.placeholder;
          run.cb({ type: 'run-end' });
          await until(() => wakeRuns(h).length > 1);
          return { same: during === before, runs: wakeRuns(h).length, msgs: p._chatMessages.length };""")
        self.assertEqual(r, {"same": True, "runs": 2, "msgs": 0})

    def test_a_wake_word_shows_listening_sends_the_words_and_listens_again(self):
        r = browser.run("""
          const g = holdMic(); const { h, p } = await mkWake(); await wakeOn(p, h);
          const run = h.runs[0]; begin(run);
          const box = p.shadowRoot.getElementById('chat-input'), hint = box.placeholder;
          heard(run);
          const hot = { hint: box.placeholder, cls: wakeBtn(p).classList.contains('hot') };
          run.cb({ type: 'stt-vad-end', data: {} });
          const ends = h.binary.filter(b => b[0] === 6 && b.at(-1) === 1).length;
          await sleep(300);
          const quiet = h.binary.length;
          await sleep(300);
          const still = h.binary.length === quiet;
          words(run, 'lights on');
          await sleep(30);
          const shown = box.value, unsub = run.unsubbed, backHint = box.placeholder === hint, cold = wakeBtn(p).classList.contains('hot'), stops = g.stops;
          await until(() => wakeRuns(h).length > 1);
          return { hot, ends, still, shown, unsub, backHint, cold, stops, sent: h.ws.filter(m => m.type === 'conversation/process').map(m => m.text),
                   said: p._chatMessages.map(m => m.text), runs: wakeRuns(h).length, on: p._wakeOn };""")
        self.assertEqual(r["hot"], {"hint": "Listening...", "cls": True})
        self.assertEqual(r["ends"], 1)
        self.assertTrue(r["still"], "no audio after the end of the command")
        self.assertEqual(r["shown"], "lights on")
        self.assertEqual(r["unsub"], 1)
        self.assertTrue(r["backHint"])
        self.assertFalse(r["cold"])
        self.assertEqual(r["stops"], 1, "the microphone rests while the words are sent")
        self.assertEqual(r["sent"], ["lights on"])
        self.assertEqual(r["said"], ["lights on", "Very good, sir."])
        self.assertEqual(r["runs"], 2)
        self.assertTrue(r["on"])

    def test_it_rests_while_jarvis_speaks_so_it_cannot_wake_itself(self):
        r = browser.run("""
          const audio = [];
          window.Audio = class { constructor(u) { this.url = u; this.paused = true; this.ended = false; audio.push(this); } play() { this.paused = false; return Promise.resolve(); } pause() { this.paused = true; } };
          const g = holdMic(); const { h, p } = await mkWake(); p._chatSpeak = true; await wakeOn(p, h);
          const run = h.runs[0]; begin(run); heard(run); words(run, 'what time is it');
          await until(() => h.runs.some(x => x.msg.start_stage === 'tts'));
          const tts = h.runs.find(x => x.msg.start_stage === 'tts');
          await sleep(700);
          const whilePreparing = wakeRuns(h).length;
          tts.cb({ type: 'tts-end', data: { tts_output: { url: '/x.mp3' } } }); tts.cb({ type: 'run-end' });
          await sleep(1000);
          const whileSpeaking = wakeRuns(h).length, mic = !!p._wakeMic, text = tts.msg.input.text;
          audio[0].ended = true; audio[0].paused = true;
          await until(() => wakeRuns(h).length > 1);
          return { whilePreparing, whileSpeaking, mic, text, after: wakeRuns(h).length };""")
        self.assertEqual(r, {"whilePreparing": 1, "whileSpeaking": 1, "mic": False, "text": "Very good, sir.", "after": 2})

    def test_audio_that_failed_to_play_does_not_keep_it_asleep(self):
        r = browser.run("""
          const { h, p } = await mkWake(); await wakeOn(p, h);
          p._chatAudio = { paused: false, ended: false, error: null, pause() {} };
          p._wakeSync(); await sleep(50);
          const asleep = !p._wakeRun;
          p._chatAudio.error = { code: 4 };
          await until(() => wakeRuns(h).length > 1);
          return { asleep, runs: wakeRuns(h).length };""")
        self.assertEqual(r, {"asleep": True, "runs": 2})

    def test_the_manual_mic_pauses_it_and_still_works_the_same(self):
        r = browser.run("""
          const g = holdMic(); const { h, p } = await mkWake(); await wakeOn(p, h);
          const wake = h.runs[0]; begin(wake);
          press(p); await until(() => h.runs.length > 1);
          const manual = h.runs[1];
          await sleep(30);
          const paused = { unsub: wake.unsubbed, ended: h.binary.filter(b => b[0] === 6 && b.at(-1) === 1).length, stops: g.stops, mic: !!p._wakeMic };
          await sleep(700);
          const held = wakeRuns(h).length;
          manual.cb({ type: 'run-start', data: { runner_data: { stt_binary_handler_id: 8 } } });
          release(p); await sleep(600);
          const released = wakeRuns(h).length;
          words(manual, 'hello');
          await until(() => wakeRuns(h).length > 1);
          return { paused, held, released, manual: { stage: manual.msg.start_stage, noVad: manual.msg.input.no_vad }, sent: h.ws.filter(m => m.type === 'conversation/process').map(m => m.text) };""")
        self.assertEqual(r["paused"], {"unsub": 1, "ended": 1, "stops": 1, "mic": False})
        self.assertEqual(r["held"], 1)
        self.assertEqual(r["released"], 1, "the words of the manual mic are still coming")
        self.assertEqual(r["manual"], {"stage": "stt", "noVad": True})
        self.assertEqual(r["sent"], ["hello"])

    def test_a_typed_message_pauses_it_too(self):
        r = browser.run("""
          const { h, p } = await mkWake(); await wakeOn(p, h);
          let go; h.pending = new Promise(x => { go = x; });
          p.shadowRoot.getElementById('chat-input').value = 'question';
          const sent = p._sendChat();
          await sleep(30);
          const paused = h.runs[0].unsubbed;
          await sleep(600);
          const runs = wakeRuns(h).length;
          go(); await sent;
          await until(() => wakeRuns(h).length > 1);
          return { paused, runs, after: wakeRuns(h).length };""")
        self.assertEqual(r, {"paused": 1, "runs": 1, "after": 2})

    def test_timeouts_restart_quietly_with_a_growing_wait(self):
        r = browser.run("""
          holdMic(); const { h, p } = await mkWake({ base: 40 }); await wakeOn(p, h);
          const gaps = [];
          for (let i = 1; i <= 4; i++) {
            const t = Date.now();
            h.runs[i - 1].cb({ type: 'error', data: { code: i === 2 ? 'duplicate_wake_up_detected' : 'wake-word-timeout', message: 'Wake word was not detected' } });
            h.runs[i - 1].cb({ type: 'run-end' });
            await until(() => wakeRuns(h).length > i);
            gaps.push(Date.now() - t);
          }
          return { gaps, runs: wakeRuns(h).length, msgs: p._chatMessages.length, unsub: h.runs.slice(0, 4).map(x => x.unsubbed), mic: !!p._wakeMic };""")
        self.assertEqual(r["runs"], 5, "one restart per ending, a late run-end adds none")
        self.assertEqual(r["msgs"], 0)
        self.assertEqual(r["unsub"], [1, 1, 1, 1])
        self.assertTrue(r["mic"], "a restart keeps the microphone open")
        self.assertLess(r["gaps"][0], r["gaps"][3])
        self.assertGreaterEqual(r["gaps"][3], 300)

    def test_a_real_error_is_said_once_and_it_keeps_trying(self):
        r = browser.run("""
          holdMic(); const { h, p } = await mkWake({ base: 10 }); await wakeOn(p, h);
          for (let i = 1; i <= 3; i++) {
            h.runs[i - 1].cb({ type: 'error', data: { code: 'stt-stream-failed', message: 'Speech failed' } });
            await until(() => wakeRuns(h).length > i);
          }
          return { text: p._chatMessages.map(m => m.text), on: p._wakeOn, runs: wakeRuns(h).length };""")
        self.assertEqual(r["text"], ["Hey JARVIS could not listen: Speech failed. I will keep trying, sir."])
        self.assertTrue(r["on"])
        self.assertEqual(r["runs"], 4)

    def test_a_refused_run_is_tried_again(self):
        r = browser.run("""
          holdMic(); const { h, p } = await mkWake({ base: 10 });
          let refuse = true;
          h.runRefuse = () => refuse ? { code: 'stt-provider-missing', message: 'No speech engine' } : null;
          wakeBtn(p).click();
          await until(() => p._chatMessages.length);
          refuse = false;
          await until(() => wakeRuns(h).length);
          return { text: p._chatMessages.map(m => m.text), runs: wakeRuns(h).length, on: p._wakeOn };""")
        self.assertEqual(r, {"text": ["Hey JARVIS could not listen: No speech engine. I will keep trying, sir."], "runs": 1, "on": True})

    def test_no_wake_word_on_the_assistant_says_so_and_turns_off(self):
        r = browser.run("""
          const g = holdMic(); const { h, p } = await mkWake({ row: { wake_word_entity: null, wake_word_id: null } });
          wakeBtn(p).click(); await until(() => p._chatMessages.length);
          return { text: p._chatMessages.map(m => m.text), on: wakeBtn(p).classList.contains('on'), stored: localStorage.getItem('jarvis_chat_wake'), calls: g.calls, runs: h.runs.length };""")
        self.assertEqual(r["text"], ["No wake word is set for this assistant, sir. Choose one in Home Assistant under Settings, Voice assistants. Hey JARVIS is off."])
        self.assertEqual({k: r[k] for k in ("on", "stored", "calls", "runs")}, {"on": False, "stored": "0", "calls": 0, "runs": 0})

    def test_home_assistant_refusing_for_a_missing_wake_engine_does_the_same(self):
        for how in ("""h.runRefuse = () => ({ code: 'wake-engine-missing', message: 'No wake word engine' }); wakeBtn(p).click();""",
                    """await wakeOn(p, h); h.runs[0].cb({ type: 'error', data: { code: 'wake-provider-missing', message: 'No provider' } });"""):
            browser.js("localStorage.clear()")
            r = browser.run("""
              const g = holdMic(); const { h, p } = await mkWake();
              """ + how + """
              await until(() => p._chatMessages.length); await sleep(30);
              const out = { text: p._chatMessages.length, first: p._chatMessages[0].text.slice(0, 11), on: p._wakeOn, stored: localStorage.getItem('jarvis_chat_wake'), mic: !!p._wakeMic, stops: g.stops === g.calls };
              p.remove(); return out;""")
            self.assertEqual(r, {"text": 1, "first": "No wake wor", "on": False, "stored": "0", "mic": False, "stops": True})

    def test_turning_it_off_stops_cleanly(self):
        r = browser.run("""
          const g = holdMic(); const { h, p } = await mkWake({ base: 10 }); await wakeOn(p, h);
          const run = h.runs[0]; begin(run); await sleep(20);
          wakeBtn(p).click(); await sleep(50);
          run.cb({ type: 'run-end' });
          await sleep(100);
          return { unsub: run.unsubbed, end: h.binary.at(-1), stops: g.stops, mic: !!p._wakeMic, runs: wakeRuns(h).length, stored: localStorage.getItem('jarvis_chat_wake'), on: wakeBtn(p).classList.contains('on'), timer: !!p._wakeTimer };""")
        self.assertEqual(r, {"unsub": 1, "end": [6, 1], "stops": 1, "mic": False, "runs": 1, "stored": "0", "on": False, "timer": False})

    def test_leaving_the_panel_stops_and_coming_back_resumes(self):
        r = browser.run("""
          const g = holdMic(); const { h, p } = await mkWake({ base: 10 }); await wakeOn(p, h);
          const run = h.runs[0]; begin(run);
          p.remove(); await sleep(50);
          const left = { unsub: run.unsubbed, stops: g.stops, mic: !!p._wakeMic, timer: !!p._wakeTimer, hooks: h.connection.listeners.ready.length, stored: localStorage.getItem('jarvis_chat_wake') };
          document.body.append(p);
          await until(() => wakeRuns(h).length > 1);
          return { left, runs: wakeRuns(h).length };""")
        self.assertEqual(r, {"left": {"unsub": 1, "stops": 1, "mic": False, "timer": False, "hooks": 0, "stored": "1"}, "runs": 2})

    def test_a_lost_microphone_stops_and_says_so(self):
        r = browser.run("""
          const g = holdMic(); const { h, p } = await mkWake({ base: 10 }); await wakeOn(p, h);
          const run = h.runs[0]; begin(run);
          p._wakeMic.stream.getAudioTracks()[0].dispatchEvent(new Event('ended'));
          await sleep(100);
          return { text: p._chatMessages.map(m => m.text), on: p._wakeOn, unsub: run.unsubbed, stops: g.stops, runs: wakeRuns(h).length };""")
        self.assertEqual(r, {"text": ["The microphone was lost, sir. Hey JARVIS is off."], "on": False, "unsub": 1, "stops": 1, "runs": 1})

    def test_a_refused_microphone_says_so_and_turns_off(self):
        r = browser.run("""
          navigator.mediaDevices.getUserMedia = async () => { throw new Error('Permission denied'); };
          const { h, p } = await mkWake();
          wakeBtn(p).click(); await until(() => p._chatMessages.length);
          return { text: p._chatMessages.map(m => m.text), on: p._wakeOn, runs: h.runs.length };""")
        self.assertEqual(r, {"text": ["Microphone not available: Permission denied. Hey JARVIS is off."], "on": False, "runs": 0})

    def test_a_dropped_connection_stops_and_a_new_one_resumes(self):
        r = browser.run("""
          const g = holdMic(); const { h, p } = await mkWake({ base: 10 }); await wakeOn(p, h);
          const c = h.connection;
          c.connected = false; c.fire('disconnected'); await sleep(50);
          const down = { unsub: h.runs[0].unsubbed, stops: g.stops, mic: !!p._wakeMic, on: p._wakeOn };
          await sleep(100);
          const quiet = wakeRuns(h).length;
          c.connected = true; c.fire('ready');
          await until(() => wakeRuns(h).length > 1);
          return { down, quiet, runs: wakeRuns(h).length };""")
        self.assertEqual(r, {"down": {"unsub": 1, "stops": 1, "mic": False, "on": True}, "quiet": 1, "runs": 2})

    def test_it_is_remembered_per_browser_and_a_render_keeps_it(self):
        r = browser.run("""
          holdMic(); localStorage.setItem('jarvis_chat_wake', '1');
          const { h, p } = await mkWake(); await until(() => wakeRuns(h).length);
          const run = h.runs[0]; begin(run); heard(run);
          p._render(); await sleep(20);
          const b = wakeBtn(p);
          return { runs: wakeRuns(h).length, on: b.classList.contains('on'), hot: b.classList.contains('hot'), hint: p.shadowRoot.getElementById('chat-input').placeholder, pressed: b.getAttribute('aria-pressed') };""")
        self.assertEqual(r, {"runs": 1, "on": True, "hot": True, "hint": "Listening...", "pressed": "true"})

    def test_blocked_storage_does_not_break_the_panel(self):
        r = browser.run("""
          holdMic();
          const blocked = () => { throw new Error('storage blocked'); };
          const get = Storage.prototype.getItem, set = Storage.prototype.setItem;
          Storage.prototype.getItem = blocked; Storage.prototype.setItem = blocked;
          const { h, p } = await mkWake(); await wakeOn(p, h);
          const out = { runs: wakeRuns(h).length, on: p._wakeOn };
          wakeBtn(p).click(); await sleep(30);
          Storage.prototype.getItem = get; Storage.prototype.setItem = set;
          return { ...out, off: p._wakeOn };""")
        self.assertEqual(r, {"runs": 1, "on": True, "off": False})

    def test_a_fresh_run_comes_before_the_timeout_but_never_cuts_a_command(self):
        r = browser.run("""
          holdMic(); const { h, p } = await mkWake();
          p._wakeTurn = 40; await wakeOn(p, h);
          await until(() => wakeRuns(h).length > 1);
          const turned = h.runs[0].unsubbed, second = h.runs[1];
          begin(second); heard(second);
          await sleep(120);
          return { turned, during: wakeRuns(h).length, unsub: second.unsubbed };""")
        self.assertEqual(r["turned"], 1)
        self.assertEqual(r["unsub"], 0)
        self.assertEqual(r["during"], 2)

    def test_speech_to_text_that_never_answers_does_not_hang_it(self):
        r = browser.run("""
          holdMic(); const { h, p } = await mkWake({ base: 10 }); p._wakeDog = 30;
          await wakeOn(p, h);
          const run = h.runs[0]; begin(run); heard(run);
          await until(() => wakeRuns(h).length > 1);
          return { unsub: run.unsubbed, hot: wakeBtn(p).classList.contains('hot'), runs: wakeRuns(h).length };""")
        self.assertEqual(r, {"unsub": 1, "hot": False, "runs": 2})

    def test_events_from_an_ended_run_change_nothing(self):
        r = browser.run("""
          holdMic(); const { h, p } = await mkWake(); await wakeOn(p, h);
          const run = h.runs[0]; begin(run);
          wakeBtn(p).click(); await sleep(30);
          heard(run); words(run, 'do not send'); run.cb({ type: 'error', data: { code: 'stt-stream-failed', message: 'x' } });
          await sleep(1400);
          return { box: p.shadowRoot.getElementById('chat-input').value, msgs: p._chatMessages.length, hot: wakeBtn(p).classList.contains('hot') };""")
        self.assertEqual(r, {"box": "", "msgs": 0, "hot": False})

    def test_switching_off_while_the_permission_prompt_is_open_leaves_no_microphone(self):
        r = browser.run("""
          const g = holdMic(); g.hold(); const { h, p } = await mkWake();
          wakeBtn(p).click(); await sleep(30);
          wakeBtn(p).click(); await sleep(30);
          g.open(); await until(() => g.stops); await sleep(50);
          return { stops: g.stops, runs: h.runs.length, mic: !!p._wakeMic };""")
        self.assertEqual(r, {"stops": 1, "runs": 0, "mic": False})

    def test_it_rests_while_a_message_is_being_typed(self):
        # round 2: typing means the box has the focus and holds text, and the button says that it rests
        r = browser.run("""
          const g = holdMic(); const { h, p } = await mkWake(); await wakeOn(p, h);
          const run = h.runs[0]; begin(run);
          const box = p.shadowRoot.getElementById('chat-input'), type = (t) => { box.value = t; box.dispatchEvent(new Event('input', { bubbles: true })); };
          const look = () => ({ rest: wakeBtn(p).classList.contains('rest'), on: wakeBtn(p).classList.contains('on'), title: wakeBtn(p).title });
          box.focus(); type('turn off the');
          await sleep(30);
          const rested = { unsub: run.unsubbed, mic: !!p._wakeMic, stops: g.stops }, resting = look();
          await sleep(800);
          const during = wakeRuns(h).length;
          type('');
          await until(() => wakeRuns(h).length > 1);
          return { rested, resting, during, after: wakeRuns(h).length, on: p._wakeOn, back: look() };""")
        self.assertEqual(r["rested"], {"unsub": 1, "mic": False, "stops": 1})
        self.assertEqual((r["during"], r["after"], r["on"]), (1, 2, True))
        self.assertEqual((r["resting"]["rest"], r["resting"]["on"]), (True, True))
        self.assertIn("resting while you type", r["resting"]["title"])
        self.assertNotIn("listening for the wake word", r["resting"]["title"])
        self.assertEqual((r["back"]["rest"], r["back"]["on"]), (False, True))
        self.assertIn("listening for the wake word", r["back"]["title"])

    def test_text_left_in_a_box_that_is_not_in_use_does_not_make_it_deaf(self):
        # review 2: any text in the box stopped the microphone for good while the button still said it was listening
        r = browser.run("""
          holdMic(); const { h, p } = await mkWake({ base: 20 }); await wakeOn(p, h);
          const run = h.runs[0]; begin(run);
          const box = p.shadowRoot.getElementById('chat-input');
          box.value = 'remind me to'; box.dispatchEvent(new Event('input', { bubbles: true }));
          await sleep(800);
          const kept = { unsub: run.unsubbed, live: !!p._wakeRun && !p._wakeRun.over, mic: !!p._wakeMic, rest: wakeBtn(p).classList.contains('rest'),
                         listening: wakeBtn(p).title.includes('listening for the wake word') };
          heard(run); words(run, 'buy milk');
          await until(() => wakeRuns(h).length > 1); await sleep(1400);
          return { kept, again: wakeRuns(h).length, box: box.value, sent: h.ws.filter(m => m.type === 'conversation/process').length };""")
        self.assertEqual(r["kept"], {"unsub": 0, "live": True, "mic": True, "rest": False, "listening": True})
        self.assertEqual(r["again"], 2, "it listens again after words were added to the draft")
        self.assertEqual(r["box"], "remind me to buy milk", "spoken words never replace a typed draft")
        self.assertEqual(r["sent"], 0, "a draft waits for Send")

    def test_leaving_the_box_ends_the_rest_at_once_and_coming_back_rests_again(self):
        # headless Chrome sends focus and blur only to a page that has the focus, as a real tablet's page has
        browser.cdp("Emulation.setFocusEmulationEnabled", enabled=True)
        self.addCleanup(browser.cdp, "Emulation.setFocusEmulationEnabled", enabled=False)
        r = browser.run("""
          holdMic(); const { h, p } = await mkWake(); await wakeOn(p, h);
          const run = h.runs[0]; begin(run);
          const later = p._wakeLater.bind(p); p._wakeLater = (ms) => later(ms * 20);   // the half-second look now takes 10 s: only the focus can end the rest in time
          const box = p.shadowRoot.getElementById('chat-input');
          const look = () => ({ rest: wakeBtn(p).classList.contains('rest'), title: wakeBtn(p).title });
          box.focus(); box.value = 'remind me to'; box.dispatchEvent(new Event('input', { bubbles: true }));
          await sleep(50);
          const resting = { unsub: run.unsubbed, mic: !!p._wakeMic, ...look() };
          box.blur();
          await until(() => wakeRuns(h).length > 1);
          const left = { runs: wakeRuns(h).length, live: !!p._wakeRun && !p._wakeRun.over, ...look() };
          box.focus(); await sleep(50);
          return { resting, left, back: { unsub: wakeRuns(h).at(-1).unsubbed, mic: !!p._wakeMic, ...look() }, box: box.value };""")
        self.assertEqual((r["resting"]["unsub"], r["resting"]["mic"], r["resting"]["rest"]), (1, False, True))
        self.assertIn("resting while you type", r["resting"]["title"])
        self.assertEqual((r["left"]["runs"], r["left"]["live"], r["left"]["rest"]), (2, True, False))
        self.assertIn("listening for the wake word", r["left"]["title"])
        self.assertEqual((r["back"]["unsub"], r["back"]["mic"], r["back"]["rest"]), (1, False, True))
        self.assertIn("resting while you type", r["back"]["title"])
        self.assertEqual(r["box"], "remind me to")

    def test_a_focused_empty_box_alone_does_not_stop_it(self):
        r = browser.run("""
          holdMic(); const { h, p } = await mkWake(); await wakeOn(p, h);
          const run = h.runs[0]; begin(run);
          p.shadowRoot.getElementById('chat-input').focus();
          p._wakeSync(); await sleep(600);
          return { unsub: run.unsubbed, runs: wakeRuns(h).length, live: !!p._wakeRun && !p._wakeRun.over && !!p._wakeMic };""")
        self.assertEqual(r, {"unsub": 0, "runs": 1, "live": True})

    # review 1, round 3: the box loses the focus when the mouse goes down on Send, the click itself comes a little later
    def rest_then_click(self, button, hold):
        browser.cdp("Emulation.setFocusEmulationEnabled", enabled=True)
        self.addCleanup(browser.cdp, "Emulation.setFocusEmulationEnabled", enabled=False)
        at = browser.run("""
          window.G = holdMic(); const { h, p } = await mkWake(); window.H = h; window.P = p; await wakeOn(p, h);
          begin(h.runs[0]);
          h.pending = new Promise(() => {});   // the reply stays out, so only the click itself could start a run
          const box = p.shadowRoot.getElementById('chat-input'); box.focus();
          box.value = 'hello there'; box.dispatchEvent(new Event('input', { bubbles: true })); await sleep(700);
          window.before = { runs: wakeRuns(h).length, mic: G.calls, rest: wakeBtn(p).classList.contains('rest') };
          const s = p.shadowRoot.getElementById(%s).getBoundingClientRect();
          return { x: s.x + s.width / 2, y: s.y + s.height / 2 };""" % json.dumps(button))
        browser.click(at["x"], at["y"], hold=hold)

    def test_a_mouse_click_on_send_while_it_rests_starts_no_extra_run(self):
        for hold in (0.08, 0.3):
            self.rest_then_click("chat-send", hold)
            time.sleep(0.6)
            r = browser.run("return { before, runs: wakeRuns(H).length, mic: G.calls, asked: H.ws.filter(m => m.type === 'conversation/process').map(m => m.text) };")
            self.assertTrue(r["before"]["rest"])
            self.assertEqual(r["asked"], ["hello there"])
            self.assertEqual((r["runs"], r["mic"]), (r["before"]["runs"], r["before"]["mic"]), "no wake run and no microphone between the press and the click")
            browser.open("/panel.html")
            browser.js("localStorage.clear()")
            browser.run(WAKE + "return 1;")

    def test_a_click_on_another_button_of_the_bar_ends_the_rest_after_a_moment(self):
        self.rest_then_click("chat-speak", 0.05)
        time.sleep(0.25)
        soon = browser.run("return { runs: wakeRuns(H).length, rest: P._wakeRest };")
        r = browser.run("""
          await until(() => wakeRuns(H).length > before.runs);
          return { before, runs: wakeRuns(H).length, rest: wakeBtn(P).classList.contains('rest'), title: wakeBtn(P).title, live: !!P._wakeRun && !P._wakeRun.over };""")
        self.assertEqual(soon, {"runs": r["before"]["runs"], "rest": True}, "a short grace first")
        self.assertEqual((r["runs"], r["rest"], r["live"]), (r["before"]["runs"] + 1, False, True), "then it listens again: the box is no longer in use")
        self.assertIn("listening for the wake word", r["title"])

    def test_its_own_spoken_words_waiting_to_be_sent_are_no_typing(self):
        # review 1, round 3: the box keeps the focus after every reply, so Hey JARVIS's own words made the button say it rested for typing
        r = browser.run("""
          holdMic(); const { h, p } = await mkWake(); await wakeOn(p, h);
          const run = h.runs[0]; begin(run);
          const box = p.shadowRoot.getElementById('chat-input'); box.focus();
          heard(run); words(run, 'lamp on');
          await sleep(150);
          const waiting = { box: box.value, focused: p.shadowRoot.activeElement === box, rest: wakeBtn(p).classList.contains('rest'), title: wakeBtn(p).title };
          await until(() => h.ws.some(m => m.type === 'conversation/process'));
          return { waiting, sent: h.ws.filter(m => m.type === 'conversation/process').map(m => m.text) };""")
        self.assertEqual((r["waiting"]["box"], r["waiting"]["focused"], r["waiting"]["rest"]), ("lamp on", True, False))
        self.assertNotIn("resting while you type", r["waiting"]["title"])
        self.assertEqual(r["sent"], ["lamp on"])

    def test_a_typed_draft_with_spoken_words_added_still_rests(self):
        # the words are added after a typed draft and wait for Send: that box holds typing, so the rest stays
        r = browser.run("""
          holdMic(); const { h, p } = await mkWake(); await wakeOn(p, h);
          const run = h.runs[0]; begin(run);
          const box = p.shadowRoot.getElementById('chat-input'); box.focus();
          box.value = 'remind me to'; box.dispatchEvent(new Event('input', { bubbles: true })); await sleep(50);
          p._chatSpoken('buy milk'); await sleep(50);
          return { box: box.value, rest: wakeBtn(p).classList.contains('rest'), typing: p._wakeTyping() };""")
        self.assertEqual(r, {"box": "remind me to buy milk", "rest": True, "typing": True})


class Rerender(Base):
    page = "panel"

    def test_typed_draft_survives_a_full_render(self):
        r = browser.run("""
          const p = await mkPanel(mkHass());
          const i = p.shadowRoot.getElementById('chat-input');
          i.value = 'half <b>a</b> & thought'; i.dispatchEvent(new Event('input', { bubbles: true }));
          p.narrow = true; await sleep(20);
          return { same: p.shadowRoot.getElementById('chat-input') !== i, value: p.shadowRoot.getElementById('chat-input').value };""")
        self.assertEqual(r, {"same": True, "value": "half <b>a</b> & thought"})

    def test_a_new_area_does_not_rebuild_the_chat_tab(self):
        r = browser.run("""
          const p = await mkPanel(mkHass());
          let renders = 0; const real = p._render.bind(p); p._render = () => { renders++; real(); };
          p._liveData = { areas: [] };
          const h = p._hass, ws = h.callWS;
          h.callWS = async (m) => m.type === 'jarvis/get_panel_data' ? { areas: [{ id: 'a' }, { id: 'b' }] } : ws(m);
          await p._fetchLiveData(); await sleep(20);
          return { renders, tab: p._currentTab };""")
        self.assertEqual(r["renders"], 0)

    def test_spoken_text_is_sent_after_a_render(self):
        r = browser.run("""
          holdMic();
          const h = mkHass(), p = await mkPanel(h);
          press(p); await started(h);
          const run = h.runs[0];
          run.cb({ type: 'run-start', data: { runner_data: { stt_binary_handler_id: 2 } } });
          release(p); await sleep(20);
          run.cb({ type: 'stt-end', data: { stt_output: { text: 'dim the lamp' } } });
          p.narrow = true; await sleep(20);
          const shown = p.shadowRoot.getElementById('chat-input').value;
          await sleep(1400); await p._chatSaving;
          return { shown, texts: p._chatMessages.map(m => m.text), box: p.shadowRoot.getElementById('chat-input').value };""")
        self.assertEqual(r, {"shown": "dim the lamp", "texts": ["dim the lamp", "Very good, sir."], "box": ""})

    def test_pressing_send_before_the_timer_does_not_send_twice(self):
        r = browser.run("""
          holdMic();
          const h = mkHass(), p = await mkPanel(h);
          press(p); await started(h);
          const run = h.runs[0];
          run.cb({ type: 'run-start', data: { runner_data: { stt_binary_handler_id: 2 } } });
          release(p); await sleep(20);
          run.cb({ type: 'stt-end', data: { stt_output: { text: 'once' } } });
          await p._sendChat(); await sleep(1400); await p._chatSaving;
          return p._chatMessages.filter(m => m.role === 'user').map(m => m.text);""")
        self.assertEqual(r, ["once"])

    def test_spoken_words_never_replace_a_typed_draft_and_wait_for_send(self):
        r = browser.run("""
          holdMic();
          const h = mkHass(), p = await mkPanel(h);
          const box = p.shadowRoot.getElementById('chat-input');
          box.value = 'turn the'; box.dispatchEvent(new Event('input', { bubbles: true }));
          press(p); await started(h);
          const run = h.runs[0];
          run.cb({ type: 'run-start', data: { runner_data: { stt_binary_handler_id: 2 } } });
          release(p); await sleep(20);
          run.cb({ type: 'stt-end', data: { stt_output: { text: 'lamp on' } } });
          await sleep(1500);
          return { box: p.shadowRoot.getElementById('chat-input').value, draft: p._chatDraft, wait: p._spokenWait || 0,
                   sent: h.ws.filter(m => m.type === 'conversation/process').length };""")
        self.assertEqual(r, {"box": "turn the lamp on", "draft": "turn the lamp on", "wait": 0, "sent": 0})

    def test_words_spoken_while_jarvis_answers_are_sent_once_the_reply_is_in(self):
        r = browser.run("""
          holdMic();
          const h = mkHass(), p = await mkPanel(h);
          let go; h.pending = new Promise(x => { go = x; });
          p.shadowRoot.getElementById('chat-input').value = 'first';
          const first = p._sendChat(); await sleep(10);
          press(p); await started(h);
          const run = h.runs[0];
          run.cb({ type: 'run-start', data: { runner_data: { stt_binary_handler_id: 2 } } });
          release(p); await sleep(20);
          run.cb({ type: 'stt-end', data: { stt_output: { text: 'second' } } });
          await sleep(1500);
          const asked = () => h.ws.filter(m => m.type === 'conversation/process').map(m => m.text);
          const waiting = { box: p.shadowRoot.getElementById('chat-input').value, asked: asked() };
          go(); await first;
          await until(() => asked().length > 1); await until(() => !p._chatBusy); await p._chatSaving;
          return { waiting, asked: asked(), texts: p._chatMessages.map(m => m.text), box: p.shadowRoot.getElementById('chat-input').value, wait: p._spokenWait };""")
        self.assertEqual(r["waiting"], {"box": "second", "asked": ["first"]})
        self.assertEqual(r["asked"], ["first", "second"])
        self.assertEqual(r["texts"], ["first", "Very good, sir.", "second", "Very good, sir."])
        self.assertEqual((r["box"], r["wait"]), ("", 0))

    # review 2, round 2: spoken words that wait to be sent are no typed draft, and CLEAN or the red-cross cancel drop them
    def test_two_quick_utterances_are_joined_and_sent_once(self):
        r = browser.run(TALK + """
          holdMic();
          const h = mkHass(), p = await mkPanel(h), talk = talker(p, h), box = () => p.shadowRoot.getElementById('chat-input');
          await talk('lamp on'); await sleep(400); await talk('and the heater');
          const shown = box().value;
          await sleep(1500); await until(() => !p._chatBusy); await p._chatSaving;
          return { shown, asked: asked(h), box: box().value, wait: p._spokenWait };""")
        self.assertEqual(r, {"shown": "lamp on and the heater", "asked": ["lamp on and the heater"], "box": "", "wait": 0})

    def test_spoken_words_that_were_edited_are_a_draft_and_wait_for_send(self):
        r = browser.run(TALK + """
          holdMic();
          const h = mkHass(), p = await mkPanel(h), talk = talker(p, h), box = () => p.shadowRoot.getElementById('chat-input');
          await talk('lamp on'); await sleep(100);
          box().value = 'lamp on please'; box().dispatchEvent(new Event('input', { bubbles: true }));
          await talk('now'); await sleep(1500);
          return { box: box().value, asked: asked(h), wait: p._spokenWait };""")
        self.assertEqual(r, {"box": "lamp on please now", "asked": [], "wait": 0})

    def test_words_spoken_twice_while_jarvis_answers_go_together_once(self):
        r = browser.run(TALK + """
          holdMic();
          const h = mkHass(), p = await mkPanel(h), talk = talker(p, h), box = () => p.shadowRoot.getElementById('chat-input');
          let go; h.pending = new Promise(x => { go = x; });
          box().value = 'first'; const first = p._sendChat(); await sleep(10);
          await talk('second'); await sleep(1500); await talk('third'); await sleep(400);
          const waiting = { box: box().value, asked: asked(h) };
          go(); await first;
          await until(() => asked(h).length > 1); await until(() => !p._chatBusy); await p._chatSaving; await sleep(400);
          return { waiting, asked: asked(h), box: box().value, wait: p._spokenWait };""")
        self.assertEqual(r["waiting"], {"box": "second third", "asked": ["first"]})
        self.assertEqual(r["asked"], ["first", "second third"])
        self.assertEqual((r["box"], r["wait"]), ("", 0))

    def test_clean_yes_drops_spoken_words_that_wait_for_the_reply(self):
        r = browser.run(TALK + """
          holdMic();
          const h = mkHass(), p = await mkPanel(h), talk = talker(p, h), $ = (id) => p.shadowRoot.getElementById(id);
          let go; h.pending = new Promise(x => { go = x; });
          $('chat-input').value = 'first'; const first = p._sendChat(); await sleep(10);
          await talk('second'); await sleep(1400);
          const waiting = $('chat-input').value;
          $('chat-clear').click(); await sleep(10);
          $('chat-ask-yes').click(); await sleep(800);
          go(); await first; await sleep(400); await p._chatSaving;
          return { waiting, asked: asked(h), msgs: p._chatMessages.map(m => m.text), box: $('chat-input').value, wait: p._spokenWait };""")
        self.assertEqual(r, {"waiting": "second", "asked": ["first"], "msgs": [], "box": "", "wait": 0})

    def test_the_red_cross_cancel_drops_spoken_words_that_wait_and_later_words_still_go(self):
        r = browser.run(TALK + """
          holdMic();
          const h = mkHass(), p = await mkPanel(h), talk = talker(p, h), box = () => p.shadowRoot.getElementById('chat-input');
          let go; h.pending = new Promise(x => { go = x; });
          box().value = 'first'; const first = p._sendChat(); await sleep(10);
          await talk('second'); await sleep(1400);
          const waiting = box().value;
          [...p.shadowRoot.querySelectorAll('.chat-del')].at(-1).click(); await sleep(800);
          const cancelled = { asked: asked(h), msgs: p._chatMessages.map(m => m.text), box: box().value, busy: p._chatBusy };
          go(); await first; await sleep(100);
          await talk('third'); await sleep(1500); await until(() => !p._chatBusy); await p._chatSaving;
          return { waiting, cancelled, asked: asked(h), msgs: p._chatMessages.map(m => m.text), box: box().value, wait: p._spokenWait };""")
        self.assertEqual(r["waiting"], "second")
        self.assertEqual(r["cancelled"], {"asked": ["first"], "msgs": [], "box": "", "busy": False})
        self.assertEqual(r["asked"], ["first", "third"])
        self.assertEqual(r["msgs"], ["third", "Very good, sir."])
        self.assertEqual((r["box"], r["wait"]), ("", 0))

    def test_mic_keeps_its_state_through_a_render_and_stops_on_release(self):
        browser.run("""
          holdMic();
          window.h = mkHass(); window.p = await mkPanel(h);
          p.scrollIntoView(); p.shadowRoot.getElementById('chat-mic').scrollIntoView();""")
        # Headless Chrome only applies the pending layout on its first input event, and the button then jumps about 47 px.
        # So send one input first, and press only once the button has stopped moving and really is under the mouse.
        browser.cdp("Input.dispatchMouseEvent", type="mouseMoved", x=1, y=1)
        box = browser.run("""
          const spot = () => { const b = p.shadowRoot.getElementById('chat-mic').getBoundingClientRect(); return [b.x + b.width / 2, b.y + b.height / 2]; };
          let last = spot(), calm = 0;
          for (let i = 0; i < 300 && calm < 10; i++) {
            await sleep(20);
            const now = spot();
            calm = now[0] === last[0] && now[1] === last[1] ? calm + 1 : 0;
            last = now;
          }
          return { x: last[0], y: last[1], hit: p.shadowRoot.elementFromPoint(last[0], last[1])?.closest('#chat-mic') ? true : false };""")
        self.assertTrue(box["hit"], "the mic button is not under the mouse")
        browser.cdp("Input.dispatchMouseEvent", type="mouseMoved", x=box["x"], y=box["y"])
        browser.cdp("Input.dispatchMouseEvent", type="mousePressed", x=box["x"], y=box["y"], button="left", clickCount=1)
        browser.run("await until(() => p._micStop);")
        during = browser.run("""
          const old = p.shadowRoot.getElementById('chat-mic');
          p._render(); await sleep(20);
          const b = p.shadowRoot.getElementById('chat-mic');
          return { replaced: b !== old, on: b.classList.contains('on'), live: !!p._micStop };""")
        self.assertEqual(during, {"replaced": True, "on": True, "live": True})
        browser.cdp("Input.dispatchMouseEvent", type="mouseReleased", x=box["x"], y=box["y"], button="left", clickCount=1)
        after = browser.run("""
          await until(() => !p._micStop);
          return { live: !!p._micStop, on: p.shadowRoot.getElementById('chat-mic').classList.contains('on') };""")
        self.assertEqual(after, {"live": False, "on": False})


class CameraWatchStart(Base):
    """Which camera Camera Watch opens on: the Core setting first, then the remembered one, then the first."""
    page = "panel"

    def pick(self, default, stored):
        return browser.run("""
          const el = await mkPanel(mkHass({}));
          localStorage.setItem('jarvis_active_cam', %s);
          el._liveData = {config: {camera_watch_default: %s, cameras: [{entity_id: 'camera.a'}, {entity_id: 'camera.b'}]}};
          el._activeCam = null;
          el._setupCameras();
          return el._activeCam;""" % (json.dumps(stored), json.dumps(default)))

    def test_core_setting_wins(self):
        self.assertEqual(self.pick("camera.a", "camera.b"), "camera.a")

    def test_no_default_keeps_the_remembered_camera(self):
        self.assertEqual(self.pick("", "camera.b"), "camera.b")

    def test_a_removed_camera_falls_back(self):
        self.assertEqual(self.pick("camera.gone", "camera.b"), "camera.b")
        self.assertEqual(self.pick("camera.gone", "camera.gone"), "camera.a")


# Camera frames for the dark-room tests, drawn in the browser so the tests need no image files: a dark room as three USB webcams
# gave it on 2026-11-07 (almost all level 2, a few faint spots of light), pure black as an idle Nest stream decodes, and daylight.
FRAMES = """
  const frame = (kind, k = 0) => {
    const c = document.createElement('canvas'); c.width = 640; c.height = 360; const x = c.getContext('2d');
    x.fillStyle = { dark: 'rgb(2,2,2)', black: 'rgb(0,0,0)', bright: 'rgb(120,124,128)' }[kind]; x.fillRect(0, 0, 640, 360);
    let seed = 7; const rnd = () => (seed = (seed * 1103515245 + 12345) % 2147483648) / 2147483648;
    if (kind === 'dark') for (let i = 0; i < 60; i++) { const v = 12 + Math.floor(rnd() * 30); x.fillStyle = `rgb(${v},${v},${v})`; x.fillRect(Math.floor(rnd() * 636), Math.floor(rnd() * 356), 3, 3); }
    if (kind === 'dark' && k) { x.fillStyle = 'rgb(30,30,30)'; x.fillRect(16 + 44 * k, 300, 4, 4); }   // live: no two frames alike
    if (kind === 'bright') { x.fillStyle = 'rgb(230,220,200)'; x.fillRect(80, 60, 200, 140); }
    return c.toDataURL('image/jpeg', 0.92).split(',')[1];
  };
  // A cold headless Chrome can encode its first canvas as pure black: each frame is decoded again and drawn anew until it shows
  // its spots of light (only the black frame may be empty).
  const lit = async (b64) => {
    const img = new Image(); img.src = 'data:image/jpeg;base64,' + b64; await img.decode();
    const c = document.createElement('canvas'); c.width = 640; c.height = 360; const x = c.getContext('2d', { willReadFrequently: true });
    x.drawImage(img, 0, 0); const d = x.getImageData(0, 0, 640, 360).data;
    for (let i = 0; i < d.length; i += 4) if (d[i] > 8) return true;
    return false;
  };
  const good = async (kind, k = 0) => {
    for (let t = 0; t < 10; t++) { const b = frame(kind, k); if (kind === 'black' || await lit(b)) return b; await sleep(100); }
    throw new Error('the browser drew no ' + kind + ' frame');
  };
  const out = { dark: await good('dark'), black: await good('black'), bright: await good('bright') };
  for (let k = 1; k < 13; k++) out['dark' + k] = await good('dark', k);   // 13 dark frames, a prime: a sample every 2 s never meets the same one
  return out;"""

# Camera Watch on one camera, in the shadow root of a panel on the Chat tab. The watchdog waits a quarter of its real time.
CAM = """
  window.mkCam = async (o = {}) => {
    const h = mkHass(), call = h.callWS, cams = ['camera.living', ...(o.more || [])];
    cams.forEach((e) => { h.states[e] = { entity_id: e, state: 'idle', attributes: { access_token: 't1', friendly_name: e } }; });
    h.callWS = async (m) => {
      if (m.type === 'camera/capabilities') { h.ws.push(m); return { frontend_stream_types: o.types || [] }; }
      if (m.type === 'jarvis/camera_snapshot') { h.ws.push(m); return { image: o.jarvis || null }; }   // as the backend answers when it finds no usable frame
      return call(m);
    };
    const p = await mkPanel(h);
    const arm = p._armCamWatchdog.bind(p); p._armCamWatchdog = (e, img, mode, ms) => arm(e, img, mode, ms / 4);
    window.realTime = () => { p._armCamWatchdog = arm; };   // round 5: from here on the watchdog keeps its real windows
    if (o.retry && p._camRetryLater) {   // round 5: the NO FRAME retry comes o.retry times sooner than its real minute
      const later = p._camRetryLater.bind(p); p._camRetryLater = (e, ms) => later(e, ms / o.retry);
    }
    const feed = document.createElement('div'); feed.className = 'cam-feed'; feed.id = 'cam-feed';
    feed.innerHTML = '<div class="cam-none">NO CAMERA SELECTED</div><div class="cam-tag" id="cam-tag"></div>';
    const strip = document.createElement('div'); strip.className = 'cam-strip'; strip.id = 'cam-strip';
    p.shadowRoot.append(feed, strip);
    p._liveData = { config: { cameras: cams.map((e) => ({ entity_id: e, name: e })) } };
    p._activeCam = null; p._setupCameras();
    window.P = p; window.H = h;
    return p;
  };
  window.camNow = () => {
    const f = P.shadowRoot.getElementById('cam-feed'), img = f.querySelector('img'), b = f.querySelector('.cam-dark'), none = f.querySelector('.cam-none');
    return { badge: b ? b.textContent : null, none: none ? none.textContent : null, mode: P._camMode, remembered: P._camModeByEntity['camera.living'] || null,
             shown: !!img && getComputedStyle(img).display !== 'none' && img.naturalWidth > 0, look: img && P._camLook ? P._camLook(img) : null,
             src: img ? (img.getAttribute('src') || '').split('?')[0].slice(0, 40) : null,
             shots: H.ws.filter(m => m.type === 'jarvis/camera_snapshot').length,
             badgeH: b ? b.getBoundingClientRect().height : 0, imgH: img ? img.getBoundingClientRect().height : 0 };
  };
  window.waitFor = async (f, ms) => { const end = Date.now() + ms; while (Date.now() < end && !f()) await sleep(50); await sleep(20); return !!f(); };
"""


def frame_file(state):
    """The next frame: a list in state['frame'] is a live camera whose frames all differ (two real frames of a dark room do), one file
    is a camera that sends the same picture again and again. The last one sent is kept in state['last']."""
    f = state["frame"]
    if isinstance(f, (list, tuple)):
        state["n"] = state.get("n", 0) + 1
        f = f[state["n"] % len(f)]
    state["last"] = f
    return f


def mjpeg(state):
    """A live MJPEG stream as Home Assistant's camera proxy sends it: the frames of state['frame'], four times a second. With
    state['freeze'] the connection stays open but no new frame comes (HA's still stream sends a frame only when the picture changed)."""
    def serve(h):
        h.send_response(200)
        h.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        h.send_header("Cache-Control", "no-store")
        h.end_headers()
        try:
            while not state.get("stop"):
                if state.get("freeze"):
                    time.sleep(0.1)
                    continue
                with open(frame_file(state), "rb") as f:
                    body = f.read()
                h.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n" % len(body) + body + b"\r\n")
                h.wfile.flush()
                time.sleep(0.25)
        except OSError:
            pass   # the page closed the stream
    return serve


def still(state):
    """A still from Home Assistant's camera proxy: the camera's current frame."""
    def serve(h):
        with open(frame_file(state), "rb") as f:
            body = f.read()
        h.send_response(200)
        h.send_header("Content-Type", "image/jpeg")
        h.send_header("Cache-Control", "no-store")
        h.send_header("Content-Length", str(len(body)))
        h.end_headers()
        h.wfile.write(body)
    return serve


class CameraInTheDark(Base):
    """Camera Watch in an unlit room (round 3): a real but very dark picture is shown with an honest badge and is never taken for a
    black Nest stream; a stream that decodes pure black or nothing at all still goes down the chain to the JARVIS tier as before."""
    page = "panel"
    frames = None
    STREAM, STILL = "/api/camera_proxy_stream/camera.living", "/api/camera_proxy/camera.living"

    def setUp(self):
        super().setUp()
        if not CameraInTheDark.frames:
            import base64
            CameraInTheDark.b64 = browser.run(FRAMES)
            CameraInTheDark.frames = {}
            for kind, data in CameraInTheDark.b64.items():
                path = os.path.join(tmp.name, "cam-%s.jpg" % kind)
                with open(path, "wb") as f:
                    f.write(base64.b64decode(data))
                CameraInTheDark.frames[kind] = path
        self.state = {"frame": self.live("dark")}
        browser.files[self.STREAM] = mjpeg(self.state)
        browser.files[self.STILL] = still(self.state)
        browser.js("localStorage.clear()")   # no camera remembered from another test
        browser.run(CAM + "return 1;")

    def tearDown(self):
        self.state["stop"] = self.state["closed"] = True
        browser.files.pop(self.STREAM, None)
        browser.files.pop(self.STILL, None)
        super().tearDown()

    def live(self, kind):
        """A live camera's frames: a dark room's frames all differ a little, black and bright ones are one picture."""
        return [self.frames["dark"]] + [self.frames["dark%d" % k] for k in range(1, 13)] if kind == "dark" else self.frames[kind]

    def light(self, kind):
        self.state["frame"] = self.live(kind)

    def no_frame(self, r):
        """The honest end of the chain (the dash between the words is not compared, see the badge test)."""
        self.assertTrue((r["none"] or "").startswith("NO FRAME") and "camera idle or unreachable" in r["none"], r)

    def go_dark(self):
        """Camera Watch opens on the unlit room and ends on its dark picture with the badge."""
        r = browser.run("await mkCam(); const ok = await waitFor(() => camNow().badge, 9000); return { ok, ...camNow() };")
        self.assertTrue(r["ok"], r)
        self.assertEqual((r["mode"], r["none"]), ("stream", None), r)
        return r

    def broken(self, how):
        """The camera breaks after the dark: its open stream ends, and a new stream or still request is refused (Home Assistant
        answers 403 for an unavailable camera) or hangs (HTTP 200 and nothing more, as a still stream does while its source is down)."""
        self.state["stop"] = True
        self.hits = {"stream": 0, "still": 0}

        def serve(name):
            def answer(h):
                self.hits[name] += 1
                if how == "error":
                    h.send_error(403)
                    return
                h.send_response(200)
                h.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame" if name == "stream" else "image/jpeg")
                h.end_headers()
                end = time.time() + 30
                while time.time() < end and not self.state.get("closed"):
                    time.sleep(0.1)
            return answer
        browser.files[self.STREAM] = serve("stream")
        browser.files[self.STILL] = serve("still")

    def test_a_dark_room_shows_its_picture_with_an_honest_badge_and_light_clears_it(self):
        r = browser.run("await mkCam(); await waitFor(() => camNow().badge, 9000); return camNow();")
        self.assertIsNotNone(r["badge"], r)
        # it claims only what is seen (the test page reads the script without a charset, so the dash is not compared)
        self.assertTrue(r["badge"].startswith("VERY DARK") and r["badge"].endswith(" too little light to see"), r["badge"])
        self.assertNotIn("works", r["badge"])
        self.assertTrue(r["shown"], "the dark picture stays visible")
        self.assertIsNone(r["none"], "no NO FRAME and no error over it")
        self.assertEqual((r["mode"], r["remembered"], r["src"]), ("stream", None, self.STREAM), "back on the live stream, not stuck on the JARVIS tier")
        self.assertGreaterEqual(r["shots"], 1, "the JARVIS tier was tried first and had nothing better")
        self.assertLess(r["badgeH"], r["imgH"] * 0.25, "the badge covers only a small part of the picture")
        self.light("bright")   # a lamp goes on: the live picture brightens and the badge goes
        r = browser.run("await waitFor(() => !camNow().badge, 6000); return camNow();")
        self.assertEqual((r["badge"], r["shown"], r["look"], r["mode"]), (None, True, "bright", "stream"))
        self.light("dark")     # and off again: the badge comes back
        r = browser.run("await waitFor(() => camNow().badge, 6000); return camNow();")
        self.assertIsNotNone(r["badge"])
        self.assertTrue(r["shown"])
        # a new stream token (Home Assistant changes it every few minutes) attaches the stream again but does not walk the chain again
        r = browser.run("""
          const before = camNow().shots;
          H.states['camera.living'].attributes.access_token = 't2'; P._renderCameraFeed();
          await sleep(3500);
          return { ...camNow(), before };""")
        self.assertEqual(r["shots"], r["before"], "no new JARVIS snapshot")
        self.assertEqual((r["mode"], r["remembered"], r["src"]), ("stream", None, self.STREAM))
        self.assertIsNotNone(r["badge"])
        self.assertTrue(r["shown"])

    def test_a_stream_that_decodes_pure_black_still_goes_down_the_chain_as_before(self):
        self.light("black")
        r = browser.run("""
          await mkCam();
          await waitFor(() => (camNow().none || '').startsWith('NO FRAME'), 9000);
          await sleep(2500);   // the badge looks every 2 s: it must not come
          return camNow();""")
        self.assertTrue((r["none"] or "").startswith("NO FRAME"), r)
        self.assertEqual((r["badge"], r["remembered"], r["mode"], r["shown"]), (None, "jarvis", "jarvis", False))

    def test_a_camera_that_sends_nothing_still_goes_down_the_chain_as_before(self):
        browser.files.pop(self.STREAM)
        browser.files.pop(self.STILL)
        r = browser.run("await mkCam(); await waitFor(() => (camNow().none || '').startsWith('NO FRAME'), 6000); await sleep(300); return camNow();")
        self.assertTrue((r["none"] or "").startswith("NO FRAME"), r)
        self.assertEqual((r["badge"], r["remembered"], r["shown"]), (None, "jarvis", False))

    def test_a_better_picture_from_the_jarvis_tier_still_wins(self):
        r = browser.run("""
          await mkCam({ jarvis: %s });
          await waitFor(() => (camNow().src || '').startsWith('data:image/jpeg'), 9000);
          await sleep(2500);
          return camNow();""" % json.dumps(self.b64["bright"]))
        self.assertTrue((r["src"] or "").startswith("data:image/jpeg"), r)
        self.assertEqual((r["badge"], r["remembered"], r["shown"], r["none"]), (None, "jarvis", True, None))

    def test_a_webrtc_only_camera_is_never_called_working_but_dark(self):
        # Home Assistant lists no hls for a WebRTC-only camera (Nest): what its proxy sends is no proof that the camera works
        r = browser.run("""
          await mkCam({ types: ['web_rtc'] });
          await waitFor(() => (camNow().none || '').startsWith('NO FRAME'), 9000);
          await sleep(2500);
          return { ...camNow(), asked: H.ws.filter(m => m.type === 'camera/capabilities').map(m => m.entity_id) };""")
        self.assertTrue((r["none"] or "").startswith("NO FRAME"), r)
        self.assertEqual((r["badge"], r["remembered"]), (None, "jarvis"))
        self.assertEqual(r["asked"], ["camera.living"])

    def test_another_camera_never_shows_the_badge_of_the_dark_one(self):
        r = browser.run("""
          await mkCam({ more: ['camera.hall'] });   // the hall camera sends nothing at all in this test
          await waitFor(() => camNow().badge, 9000);
          const dark = camNow().badge;
          P._selectCam('camera.hall');
          const at_once = camNow().badge;
          await waitFor(() => (camNow().none || '').startsWith('NO FRAME'), 6000); await sleep(2200);
          return { dark, at_once, later: camNow(), active: P._activeCam };""")
        self.assertIsNotNone(r["dark"])
        self.assertIsNone(r["at_once"], "the badge goes with the camera it was about")
        self.assertEqual((r["active"], r["later"]["badge"]), ("camera.hall", None))
        self.assertTrue((r["later"]["none"] or "").startswith("NO FRAME"))

    def test_diag_calls_a_dark_picture_very_dark_and_keeps_black_stream_for_black(self):
        r = browser.run("""
          const h = mkHass(), call = h.callWS;
          h.callWS = async (m) => m.type === 'jarvis/camera_diagnostics' ? { platforms: { generic: 1 } } : call(m);
          const p = await mkPanel(h);
          const feed = document.createElement('div'); feed.id = 'cam-feed'; feed.className = 'cam-feed'; p.shadowRoot.append(feed);
          p._activeCam = 'camera.living';
          const line = async (b64) => {
            feed.textContent = ''; const img = new Image(); img.src = 'data:image/jpeg;base64,' + b64; feed.append(img); await img.decode();
            await p._runCamDiagnostics(); const box = feed.querySelector('.cam-diag'), t = box.textContent; box.remove(); return t; };
          return { dark: await line(%s), black: await line(%s) };""" % (json.dumps(self.b64["dark"]), json.dumps(self.b64["black"])))
        self.assertIn("very dark picture", r["dark"])
        self.assertNotIn("camera works", r["dark"], "one frame shows no working camera, only a dark picture")
        self.assertNotIn("BLACK STREAM", r["dark"])
        self.assertIn("BLACK STREAM", r["black"])
        self.assertNotIn("very dark", r["black"])

    # Round 4: once a camera was dark it is not sent back to that picture for ever. Breaking, hanging, freezing or turning black
    # after the dark ends on NO FRAME, with few requests, and the badge goes as soon as no new frame comes.
    def test_a_camera_that_breaks_after_the_dark_ends_on_no_frame_with_few_requests(self):
        self.go_dark()
        self.broken("error")
        r = browser.run("""
          const s0 = camNow().shots;
          H.states['camera.living'].attributes.access_token = 't2'; P._renderCameraFeed();   // the next token: the stream is attached again
          await sleep(8000);
          return { ...camNow(), newShots: camNow().shots - s0 };""")
        self.no_frame(r)
        self.assertEqual((r["badge"], r["shown"], r["remembered"]), (None, False, "jarvis"))
        self.assertLessEqual(self.hits["stream"], 2, self.hits)   # the round-3 build made 567 of each in 8 s
        self.assertLessEqual(self.hits["still"], 3, self.hits)
        self.assertLessEqual(r["newShots"], 3, r)

    def test_a_camera_that_hangs_after_the_dark_ends_on_no_frame(self):
        self.go_dark()
        self.broken("hang")
        r = browser.run("""
          H.states['camera.living'].attributes.access_token = 't2'; P._renderCameraFeed();
          await waitFor(() => (camNow().none || '').startsWith('NO FRAME'), 9000); await sleep(2500);
          return camNow();""")
        self.no_frame(r)
        self.assertEqual((r["badge"], r["shown"], r["remembered"], r["mode"]), (None, False, "jarvis", "jarvis"))
        self.assertLessEqual(self.hits["stream"], 2, self.hits)
        self.assertLessEqual(self.hits["still"], 3, self.hits)

    def black_after_dark(self, new_token):
        self.go_dark()
        self.light("black")
        r = browser.run("""
          if (%s) { H.states['camera.living'].attributes.access_token = 't2'; P._renderCameraFeed(); }
          await waitFor(() => (camNow().none || '').startsWith('NO FRAME'), 9000); await sleep(2500);
          return camNow();""" % json.dumps(new_token))
        self.no_frame(r)
        self.assertEqual((r["badge"], r["shown"], r["remembered"], r["mode"]), (None, False, "jarvis", "jarvis"))

    def test_pure_black_after_the_dark_on_the_open_stream_still_goes_down_the_chain(self):
        self.black_after_dark(False)

    def test_pure_black_after_the_dark_and_a_new_token_still_goes_down_the_chain(self):
        self.black_after_dark(True)

    def test_the_badge_goes_when_the_picture_freezes(self):
        self.go_dark()
        self.state["frame"] = self.frames["dark"]   # the camera's source froze on one picture: its stills give it again and again,
        time.sleep(1)                               # the open stream sends it once more (Home Assistant sends changed pictures only)
        self.state["freeze"] = True                 # and then no new frame comes
        r = browser.run("""
          const t0 = Date.now(), ok = await waitFor(() => !camNow().badge, 9000), gone = Date.now() - t0, seen = [];
          for (let i = 0; i < 60; i++) { seen.push(!!camNow().badge); await sleep(100); }   // and it does not come back
          return { ok, gone, back: seen.some(Boolean), ...camNow() };""")
        self.assertTrue(r["ok"], r)
        self.assertLess(r["gone"], 8000, r)
        self.assertFalse(r["back"], r)

    def test_the_badge_goes_when_the_stream_ends_and_it_ends_on_no_frame(self):
        self.go_dark()
        self.state["stop"] = True   # unplugged: the MJPEG connection ends, the picture keeps its last frame, and nothing answers now
        browser.files.pop(self.STREAM)
        browser.files.pop(self.STILL)
        r = browser.run("""
          const t0 = Date.now(), ok = await waitFor(() => !camNow().badge, 9000), gone = Date.now() - t0;
          await waitFor(() => (camNow().none || '').startsWith('NO FRAME'), 9000);
          return { ok, gone, ...camNow() };""")
        self.assertTrue(r["ok"], r)
        self.assertLess(r["gone"], 8000, r)
        self.no_frame(r)
        self.assertEqual((r["badge"], r["remembered"]), (None, "jarvis"))

    def test_dark_then_bright_clears_the_badge_and_stays_live(self):
        self.go_dark()
        self.light("bright")
        r = browser.run("""
          const s0 = camNow().shots; const ok = await waitFor(() => !camNow().badge, 6000);
          await sleep(4000);   // several watchdog windows: a light picture is never sent down the chain
          return { ok, ...camNow(), newShots: camNow().shots - s0 };""")
        self.assertTrue(r["ok"], r)
        self.assertEqual((r["badge"], r["none"], r["mode"], r["remembered"], r["look"], r["shown"], r["newShots"]),
                         (None, None, "stream", None, "bright", True, 0))

    def test_no_tier_switch_more_than_once_a_second(self):
        browser.files.pop(self.STREAM)   # every tier fails at once, and the switches are asked for as fast as possible
        browser.files.pop(self.STILL)
        r = browser.run("""
          await mkCam(); await sleep(300);
          const log = []; let mode = P._camMode;   // every switch that runs sets the mode, so each one is logged
          Object.defineProperty(P, '_camMode', { configurable: true, get: () => mode, set: (v) => { log.push([v, Date.now()]); mode = v; } });
          const end = Date.now() + 4000;
          const still = () => P._camFallback('camera.living'), jarvis = () => P._camJarvisFallback('camera.living');
          while (Date.now() < end) {   // the worst case, a loop that flaps: the switch that changes the tier is asked for first
            (mode === 'still' ? [jarvis, still] : [still, jarvis]).forEach((f) => f());
            P._camDarkBack('camera.living');
            await sleep(10);
          }
          const gaps = log.slice(1).map((e, i) => e[1] - log[i][1]);
          return { n: log.length, modes: log.map((e) => e[0]), minGap: gaps.length ? Math.min(...gaps) : null };""")
        self.assertGreaterEqual(r["n"], 3, r)   # it still switches, about once a second
        self.assertLessEqual(r["n"], 5, r)
        self.assertGreaterEqual(r["minGap"], 990, r)
        self.assertIn("still", r["modes"])
        self.assertIn("jarvis", r["modes"])

    # Round 5 (96d): a still is judged only once it has loaded, so slow stills keep a dark camera on its picture with a steady badge,
    # and stills that fail still go down the chain. While NO FRAME shows, the chain is tried again about once a minute (the owner's
    # rule): a camera that works again is shown, one that stays broken stays on NO FRAME with one walk down the chain per try.
    def on_stills(self, slow):
        """Camera Watch on the stills tier of a dark camera: its MJPEG stream hangs, its stills work. Once its picture shows with the
        badge, every still takes slow() seconds (a phone on mobile data, go2rtc waiting for a keyframe), and the watchdog keeps its
        real windows from then on."""
        self.hits = {"still": 0}
        inner = still(self.state)

        def hang(h):
            h.send_response(200)
            h.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            h.end_headers()
            end = time.time() + 120
            while time.time() < end and not self.state.get("closed"):
                time.sleep(0.1)

        def slow_still(h):
            self.hits["still"] += 1
            if self.state.get("slow"):
                time.sleep(self.state["slow"]())
            inner(h)
        browser.files[self.STREAM] = hang
        browser.files[self.STILL] = slow_still
        r = browser.run("""await mkCam(); const ok = await waitFor(() => camNow().badge && camNow().mode === 'still', 15000);
          realTime(); return { ok, ...camNow() };""")
        self.assertTrue(r["ok"], r)
        self.state["slow"] = slow
        self.hits["still"] = 0

    def steady_on_stills(self, seconds=32):
        r = browser.run("""
          let n = 0, on = 0, flips = 0, last = null, noFrame = 0; const modes = new Set(), end = Date.now() + %d;
          while (Date.now() < end) {
            const c = camNow(), v = !!c.badge; n++; if (v) on++; if (last !== null && v !== last) flips++; last = v;
            if (c.none) noFrame++; modes.add(c.mode); await sleep(100);
          }
          const shown = await waitFor(() => camNow().shown, 2500);   // a still that is loading has no pixels yet
          return { ...camNow(), shown, n, on, flips, noFrame, modes: [...modes] };""" % (seconds * 1000))
        self.assertEqual((r["noFrame"], r["flips"], r["on"], r["modes"]), (0, 0, r["n"], ["still"]), r)
        self.assertTrue(r["shown"] and (r["badge"] or "").startswith("VERY DARK"), r)
        self.assertIsNone(r["remembered"], r)
        self.assertGreaterEqual(self.hits["still"], seconds // 2 - 3, self.hits)   # the stills kept coming, one every 2 s

    def test_stills_of_1_1_s_keep_a_dark_camera_on_its_picture_with_a_steady_badge(self):
        self.on_stills(lambda: 1.1)
        self.steady_on_stills()

    def test_uneven_slow_stills_keep_a_dark_camera_on_its_picture_with_a_steady_badge(self):
        import random
        rnd = random.Random(5)
        self.on_stills(lambda: rnd.uniform(0.3, 1.6))
        self.steady_on_stills()

    def stills_fail(self, how):
        self.on_stills(lambda: 1.1)
        time.sleep(6)   # some slow stills at the real windows first
        r = browser.run("return camNow();")
        self.assertTrue((r["badge"] or "").startswith("VERY DARK") and r["mode"] == "still" and not r["none"], r)
        if how == "error":
            browser.files[self.STILL] = lambda h: h.send_error(403)
        else:
            def hang(h):   # HTTP 200 and nothing more
                h.send_response(200)
                h.send_header("Content-Type", "image/jpeg")
                h.end_headers()
                end = time.time() + 30
                while time.time() < end and not self.state.get("closed"):
                    time.sleep(0.1)
            browser.files[self.STILL] = hang
        r = browser.run("""const t0 = Date.now(), ok = await waitFor(() => (camNow().none || '').startsWith('NO FRAME'), 12000);
          return { ok, after: Date.now() - t0, ...camNow() };""")
        self.assertTrue(r["ok"], r)
        self.no_frame(r)
        self.assertEqual((r["badge"], r["shown"], r["remembered"]), (None, False, "jarvis"), r)
        return r

    def test_stills_that_fail_after_slow_ones_still_go_down_the_chain(self):
        r = self.stills_fail("error")
        # one failed still is no end while the last dark one is less than 5 s old (round 5 follow-up); stills that keep failing go
        # down the chain once 5 s have passed since the last still that loaded, then the JARVIS tier has nothing either
        self.assertLess(r["after"], 8000, r)

    def test_one_failed_still_among_slow_ones_keeps_a_dark_camera_on_its_picture(self):
        # round 5 follow-up (review of 96d): one still in 6 answered with HTTP 500, as a phone on mobile data can see, is no NO FRAME
        self.on_stills(lambda: 1.1)
        slow_still, n = browser.files[self.STILL], {"i": 0, "failed": 0}

        def flaky(h):
            n["i"] += 1
            if n["i"] % 6 == 0:
                n["failed"] += 1
                h.send_error(500)
                return
            slow_still(h)
        browser.files[self.STILL] = flaky
        r = browser.run("""let n = 0, noFrame = 0; const modes = new Set(), end = Date.now() + 30000;
          while (Date.now() < end) { const c = camNow(); n++; if (c.none) noFrame++; modes.add(c.mode); await sleep(100); }
          return { n, noFrame, modes: [...modes], ...camNow() };""")
        self.assertEqual((r["noFrame"], r["modes"], r["remembered"]), (0, ["still"], None), r)
        self.assertGreaterEqual(n["failed"], 2, n)

    def test_stills_that_no_longer_load_still_go_down_the_chain(self):
        r = self.stills_fail("hang")
        self.assertLess(r["after"], 9000, r)   # no still loaded within the 5 s window

    def counted_stream(self):
        hits = {"stream": 0}
        inner = browser.files[self.STREAM]

        def serve(h):
            hits["stream"] += 1
            inner(h)
        browser.files[self.STREAM] = serve
        return lambda: hits["stream"]

    def outage_then_back(self, tries, back, phase=20):
        """NO FRAME during the outage stays up through the tries (one every 6 s here, a minute for real), and once the camera works
        again its picture is back within about one try."""
        r = browser.run("const ok = await waitFor(() => (camNow().none || '').startsWith('NO FRAME'), 15000); return { ok, ...camNow() };")
        self.assertTrue(r["ok"], r)
        t0 = tries()
        r = browser.run("""let n = 0, off = 0; const end = Date.now() + %d;
          while (Date.now() < end) { const c = camNow(); n++; if (!(c.none || '').startsWith('NO FRAME') || c.shown || c.badge) off++; await sleep(100); }
          return { n, off, ...camNow() };""" % (phase * 1000))
        self.assertEqual(r["off"], 0, r)
        self.assertGreaterEqual(tries() - t0, 2, "the chain was tried again during the outage")
        self.assertLessEqual(tries() - t0, phase // 6 + 1, "one walk down the chain per try")
        # the camera works again between two tries (one that comes back while a try is on the stills tier is shown on stills)
        self.assertTrue(browser.run("return await waitFor(() => P._camMode === 'jarvis', 9000);"))
        back()
        r = browser.run("""const t0 = Date.now(), ok = await waitFor(() => camNow().badge && camNow().shown && !camNow().none, 15000);
          return { ok, after: Date.now() - t0, ...camNow() };""")
        self.assertTrue(r["ok"], r)
        self.assertLess(r["after"], 6000 + 4000, r)   # one try (6 s here) and a walk down the chain
        self.assertEqual((r["mode"], r["remembered"], r["src"]), ("stream", None, self.STREAM), r)
        self.assertTrue(r["badge"].startswith("VERY DARK"), r)

    def test_a_dark_camera_comes_back_after_a_freeze_that_ended_on_no_frame(self):
        # outage A of the review: Home Assistant's generic camera while its source is down; the open stream sends no new frame and
        # the stills give the last picture again
        tries = self.counted_stream()
        r = browser.run("await mkCam({ retry: 10 }); const ok = await waitFor(() => camNow().badge, 9000); return { ok, ...camNow() };")
        self.assertTrue(r["ok"], r)
        self.state["frame"] = self.frames["dark"]
        time.sleep(0.6)
        self.state["freeze"] = True
        self.outage_then_back(tries, lambda: self.state.update(frame=self.live("dark"), freeze=False))

    def test_a_dark_camera_comes_back_after_its_stream_ended_on_no_frame(self):
        # outage B of the review: the stream ends and new requests get 403, then the camera works again
        r = browser.run("await mkCam({ retry: 10 }); const ok = await waitFor(() => camNow().badge, 9000); return { ok, ...camNow() };")
        self.assertTrue(r["ok"], r)
        self.broken("error")

        def back():
            self.state = {"frame": self.live("dark")}
            browser.files[self.STREAM] = mjpeg(self.state)
            browser.files[self.STILL] = still(self.state)
        self.outage_then_back(lambda: self.hits["stream"], back)

    def test_a_camera_that_works_again_in_daylight_is_shown_without_the_badge(self):
        self.broken("error")   # broken from the start, never seen dark
        r = browser.run("await mkCam({ retry: 10 }); const ok = await waitFor(() => (camNow().none || '').startsWith('NO FRAME'), 9000); return { ok, ...camNow() };")
        self.assertTrue(r["ok"], r)
        self.state = {"frame": self.frames["bright"]}
        browser.files[self.STREAM] = mjpeg(self.state)
        browser.files[self.STILL] = still(self.state)
        r = browser.run("""const t0 = Date.now(), ok = await waitFor(() => camNow().shown && !camNow().none, 15000); await sleep(2500);
          return { ok, after: Date.now() - t0, ...camNow() };""")
        self.assertTrue(r["ok"], r)
        self.assertLess(r["after"], 6000 + 4000, r)
        self.assertEqual((r["badge"], r["none"], r["mode"], r["remembered"], r["look"], r["shown"]), (None, None, "stream", None, "bright", True), r)

    def stays_broken(self, how):
        r = browser.run("await mkCam({ retry: 10 }); const ok = await waitFor(() => camNow().badge, 9000); return { ok, ...camNow() };")
        self.assertTrue(r["ok"], r)
        self.broken(how)
        r = browser.run("const ok = await waitFor(() => (camNow().none || '').startsWith('NO FRAME'), 15000); return { ok, ...camNow() };")
        self.assertTrue(r["ok"], r)
        self.hits.update(stream=0, still=0)
        r = browser.run("""let n = 0, off = 0; const s0 = camNow().shots, end = Date.now() + 36000;
          while (Date.now() < end) { const c = camNow(); n++; if (!(c.none || '').startsWith('NO FRAME') || c.shown || c.badge) off++; await sleep(100); }
          return { n, off, newShots: camNow().shots - s0, ...camNow() };""")
        self.assertEqual((r["off"], r["remembered"]), (0, "jarvis"), r)   # it stays on NO FRAME
        # 36 s with a try 6 s after each NO FRAME (a minute for real): 3 to 6 walks down the chain, each one stream request and
        # a few stills
        self.assertGreaterEqual(self.hits["stream"], 3, self.hits)
        self.assertLessEqual(self.hits["stream"], 7, self.hits)
        self.assertLessEqual(self.hits["still"], 3 * self.hits["stream"], self.hits)
        self.assertLessEqual(r["newShots"], 14, r)   # the JARVIS tier's own 6 s cadence and one shot per walk

    def test_a_camera_that_stays_broken_stays_on_no_frame_with_one_walk_per_try(self):
        self.stays_broken("error")

    def test_a_camera_that_keeps_hanging_stays_on_no_frame_with_one_walk_per_try(self):
        self.stays_broken("hang")

    def test_the_tries_stop_when_another_camera_is_picked_or_the_panel_is_left(self):
        self.broken("error")   # the living room camera is broken from the start; the hall camera sends nothing at all
        r = browser.run("""await mkCam({ retry: 10, more: ['camera.hall'] });
          const ok = await waitFor(() => (camNow().none || '').startsWith('NO FRAME'), 9000);
          await waitFor(() => false, 7000);   // one try comes after 6 s
          return { ok, ...camNow() };""")
        self.assertTrue(r["ok"], r)
        self.assertGreaterEqual(self.hits["stream"], 2, self.hits)
        browser.run("P._selectCam('camera.hall'); return 1;")
        time.sleep(1)
        before = dict(self.hits)
        time.sleep(14)
        self.assertEqual(self.hits, before, "no more tries for the camera that is no longer shown")
        r = browser.run("""P._selectCam('camera.living'); const t0 = Date.now();
          const ok = await waitFor(() => (camNow().none || '').startsWith('NO FRAME'), 3000);
          return { ok, ...camNow() };""")
        self.assertTrue(r["ok"], r)
        tried = self.hits["stream"]
        r = browser.run("""const ok = await waitFor(() => P._camMode !== 'jarvis', 9000);   // the next try for it
          await waitFor(() => P._camMode === 'jarvis' && (camNow().none || '').startsWith('NO FRAME'), 9000); await sleep(300);
          P.remove(); return { ok, ...camNow() };""")
        self.assertTrue(r["ok"], r)
        self.assertGreater(self.hits["stream"], tried, self.hits)
        before = dict(self.hits)
        time.sleep(14)
        self.assertEqual(self.hits, before, "no more tries once the panel was left")

    def test_tries_never_switch_tiers_more_than_once_a_second(self):
        browser.files.pop(self.STREAM)   # every tier fails at once, and a try comes 10 ms after every NO FRAME
        browser.files.pop(self.STILL)
        r = browser.run("""
          await mkCam({ retry: 6000 }); await waitFor(() => (camNow().none || '').startsWith('NO FRAME'), 6000);
          const log = []; let mode = P._camMode;   // every switch that runs sets the mode, so each one is logged
          Object.defineProperty(P, '_camMode', { configurable: true, get: () => mode, set: (v) => { log.push([v, Date.now()]); mode = v; } });
          await sleep(6000);
          const gaps = log.slice(1).map((e, i) => e[1] - log[i][1]);
          return { n: log.length, modes: log.map((e) => e[0]), minGap: gaps.length ? Math.min(...gaps) : null, ...camNow() };""")
        self.assertGreaterEqual(r["n"], 4, r)   # it is tried again and again,
        self.assertGreaterEqual(r["minGap"], 990, r)   # but never more than one tier switch a second
        self.assertIn("stream", r["modes"])
        self.assertIn("jarvis", r["modes"])


class LeaveThePanel(Base):
    page = "panel"

    def test_leaving_removes_listeners_and_stops_speech_and_mic(self):
        r = browser.run("""
          const seen = { resize: 0, pointerup: 0 };
          const add = window.addEventListener.bind(window), rem = window.removeEventListener.bind(window);
          window.addEventListener = (n, f, o) => { if (n in seen) seen[n]++; add(n, f, o); };
          window.removeEventListener = (n, f, o) => { if (n in seen) seen[n]--; rem(n, f, o); };
          const g = holdMic();
          const h = mkHass(), p = await mkPanel(h);
          press(p); await started(h);
          let paused = 0; p._chatAudio = { pause() { paused++; } };
          const gen = p._speakGen, on = { ...seen };
          p.remove(); await sleep(20);
          const left = { ...seen };
          document.body.append(p); await sleep(30);
          return { on, left, back: { ...seen }, paused, stops: g.stops, mic: !!p._micStop, audio: p._chatAudio, genMoved: p._speakGen !== gen };""")
        self.assertEqual(r["on"], {"resize": 1, "pointerup": 1})
        self.assertEqual(r["left"], {"resize": 0, "pointerup": 0})
        self.assertEqual(r["back"], {"resize": 1, "pointerup": 1})
        self.assertEqual(r["paused"], 1)
        self.assertEqual(r["stops"], 1)
        self.assertFalse(r["mic"])
        self.assertIsNone(r["audio"])
        self.assertTrue(r["genMoved"])

    def test_a_reply_that_arrives_after_leaving_stays_silent(self):
        r = browser.run("""
          const store = mkStore(null), h = mkHass({ store }), p = await mkPanel(h);
          let go; h.pending = new Promise(x => { go = x; });
          p._chatSpeak = true;
          p.shadowRoot.getElementById('chat-input').value = 'question';
          const sent = p._sendChat();
          await sleep(10);
          p.remove();
          go(); await sent; await p._chatSaving; await sleep(20);
          return { connected: p.isConnected, speech: h.runs.length, stored: store.value.messages.map(m => m.text) };""")
        self.assertEqual(r, {"connected": False, "speech": 0, "stored": ["question", "Very good, sir."]})

    def test_a_reply_on_the_open_panel_is_still_spoken(self):
        r = browser.run("""
          const h = mkHass(), p = await mkPanel(h);
          p._chatSpeak = true;
          await say(p, 'question'); await until(() => h.runs.length);
          return h.runs.length;""")
        self.assertEqual(r, 1)

    def test_speech_still_being_prepared_does_not_start_after_leaving(self):
        r = browser.run("""
          let played = 0;
          window.Audio = class { play() { played++; return Promise.resolve(); } pause() {} };
          const h = mkHass(), p = await mkPanel(h);
          p._chatSpeak = true;
          await p._speakChat('hello');
          p.remove();
          h.runs[0].cb({ type: 'tts-end', data: { tts_output: { url: '/x.mp3' } } });
          return played;""")
        self.assertEqual(r, 0)


# As in Home Assistant: unsubscribing returns a promise, and a second unsubscribe of the same run is refused.
HA_UNSUB = """
  window.haUnsub = (h) => {
    const sub = h.connection.subscribeMessage.bind(h.connection);
    h.connection.subscribeMessage = async (cb, msg, opts) => {
      const u = await sub(cb, msg, opts), run = h.runs.at(-1);
      if (msg.start_stage === 'tts' && h.endFirst) h.endFirst.forEach((t) => cb({ type: t, data: t === 'error' ? { code: 'tts-failed', message: 'no voice' } : {} }));
      return () => { u(); return run.unsubbed > 1 ? Promise.reject(new Error('Subscription not found')) : Promise.resolve(); };
    };
  };
"""


class SpokenReplies(Base):
    page = "panel"

    def setUp(self):
        super().setUp()
        browser.run(HA_UNSUB + "return 1;")

    def test_a_failed_voice_unsubscribes_once(self):
        r = browser.run("""
          const h = mkHass(), p = await mkPanel(h); haUnsub(h);
          p._chatSpeak = true;
          await p._speakChat('hello');
          const run = h.runs[0];
          run.cb({ type: 'error', data: { code: 'tts-failed', message: 'no voice' } });
          run.cb({ type: 'run-end', data: {} });
          await sleep(50);
          return { unsub: run.unsubbed, busy: p._speakBusy };""")
        self.assertEqual(r, {"unsub": 1, "busy": 0})    # tearDown: no uncaught "Subscription not found"

    def test_a_voice_that_ends_before_the_subscription_returns_unsubscribes_once(self):
        r = browser.run("""
          const h = mkHass(), p = await mkPanel(h); haUnsub(h);
          h.endFirst = ['error', 'run-end'];
          p._chatSpeak = true;
          await p._speakChat('hello'); await sleep(50);
          return { unsub: h.runs[0].unsubbed, busy: p._speakBusy };""")
        self.assertEqual(r, {"unsub": 1, "busy": 0})

    def test_a_normal_voice_still_plays_and_unsubscribes_once(self):
        r = browser.run("""
          const audio = [];
          window.Audio = class { constructor(u) { this.url = u; audio.push(this); } play() { return Promise.resolve(); } pause() {} };
          const h = mkHass(), p = await mkPanel(h); haUnsub(h);
          p._chatSpeak = true;
          await p._speakChat('hello');
          const run = h.runs[0];
          run.cb({ type: 'tts-end', data: { tts_output: { url: '/x.mp3' } } });
          run.cb({ type: 'run-end', data: {} });
          await sleep(50);
          return { unsub: run.unsubbed, urls: audio.map(a => a.url) };""")
        self.assertEqual(r, {"unsub": 1, "urls": ["/x.mp3"]})




if __name__ == "__main__":
    unittest.main()
