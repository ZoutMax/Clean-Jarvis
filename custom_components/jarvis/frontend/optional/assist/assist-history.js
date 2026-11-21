/* jh-haptic (step 156) BEGIN - one shared haptic helper for the JARVIS panel, the optional cards, Household Chat and the login
   screen; the same block is in each file and the first one loaded defines it (window.jhHaptic). jhHaptic(type, el):
   - fires Home Assistant's own "haptic" event (the Home Assistant apps on Android and iOS turn it into a native haptic);
   - in a mobile browser without the app: navigator.vibrate with a pattern per type (Android browsers; iOS Safari has none);
   - only on touch screens (pointer: coarse), never with prefers-reduced-motion, never when this login switched it off
     (frontend user data "jh_haptics", default on), and at most one pulse per 80 ms (a stronger type may follow a tap at once).
   Types: selection (tabs, pickers, dropdowns), light (buttons, arrows, toggles), medium (long press), heavy, success (an action
   confirmed), warning (sent but not confirmed; a destructive confirmation), failure (refused or failed). */
(() => {
  if (window.jhHaptic) return;
  const PAT = { selection: 5, light: 10, medium: 20, heavy: 30, success: [10, 40, 10], warning: [20, 60, 20],
                failure: [30, 60, 30, 60, 30] };
  const RANK = { selection: 1, light: 1, medium: 2, heavy: 3, success: 3, warning: 3, failure: 3 };
  const S = window.__jhHapticState = { on: true, last: 0, rank: 0, loaded: null, sent: [], v: 1 };
  const mm = (q) => { try { return !!(window.matchMedia && window.matchMedia(q).matches); } catch (e) { return false; } };
  window.jhHapticLoad = (hass) => {   // this login's switch (frontend user data), read once per login
    const uid = hass && hass.user && hass.user.id;
    if (!uid || typeof hass.callWS !== "function") return Promise.resolve(S.on);
    if (S.loaded && S.loaded.uid === uid) return S.loaded.p;
    let p;
    try {
      p = Promise.resolve(hass.callWS({ type: "frontend/get_user_data", key: "jh_haptics" }))
        .then((r) => { if (r && typeof r.value === "boolean") S.on = r.value; return S.on; }).catch(() => S.on);
    } catch (e) { p = Promise.resolve(S.on); }   // offline: the default stands
    S.loaded = { uid, p };
    return p;
  };
  window.jhHapticOn = () => S.on;
  window.jhHapticSet = (hass, on) => {
    S.on = !!on;
    try { return hass.callWS({ type: "frontend/set_user_data", key: "jh_haptics", value: !!on }); } catch (e) { return Promise.resolve(); }
  };
  window.jhHaptic = (type, el) => {
    if (!PAT[type]) type = "light";
    if (!S.on || !mm("(pointer: coarse)") || mm("(prefers-reduced-motion: reduce)")) return false;
    const now = Date.now(), rank = RANK[type];
    if (now - S.last < 80 && rank <= S.rank) return false;   // throttled: a held or repeated button does not buzz on and on
    S.last = now; S.rank = rank;
    S.sent.push(type); if (S.sent.length > 50) S.sent.shift();
    const app = !!(window.externalApp || (window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.externalBus));
    const frontend = !!document.querySelector("home-assistant");
    try {
      const target = el && el.isConnected ? el : window;
      target.dispatchEvent(new CustomEvent("haptic", { detail: type, bubbles: true, composed: true }));
    } catch (e) { /* no event support */ }
    if (app && !frontend) {   // a page of the app without Home Assistant's frontend (the login screen): tell the app directly
      const msg = { id: now, type: "haptic", payload: { hapticType: type } };
      try {
        if (window.externalApp && window.externalApp.externalBus) window.externalApp.externalBus(JSON.stringify(msg));
        else window.webkit.messageHandlers.externalBus.postMessage(msg);
      } catch (e) { /* the app has no bus here */ }
    } else if (!app && navigator.vibrate) {   // a plain mobile browser: vibrate (the app does its own haptics)
      try { navigator.vibrate(PAT[type]); } catch (e) { /* not allowed */ }
    }
    return true;
  };
  // what a tap means: a choice (tabs, chips, pickers) is "selection", any other button "light"
  const SEL = '[role="tab"], .tab, [data-tab], [data-settings-section], [data-step], [aria-pressed], [aria-selected], .seg button, [class*="chip"]';
  const BTN = 'button, [role="button"], .ctrl, a.btn, ha-icon-button, ha-button, mwc-button';
  window.jhHapticWire = (root) => {   // one listener per root: taps and changes inside it give their pulse
    if (!root || root.__jhHaptic) return;
    root.__jhHaptic = true;
    const pick = (e, sel) => {
      for (const n of (e.composedPath ? e.composedPath() : [e.target])) {
        if (n === root) break;
        if (n && n.nodeType === 1 && n.matches && n.matches(sel)) return n;
      }
      return null;
    };
    root.addEventListener("click", (e) => {
      const own = pick(e, "[data-haptic]");
      if (own) { if (own.dataset.haptic !== "none") window.jhHaptic(own.dataset.haptic, own); return; }
      const sel = pick(e, SEL);
      if (sel) { if (!sel.disabled) window.jhHaptic("selection", sel); return; }
      const btn = pick(e, BTN);
      if (btn && !btn.disabled) window.jhHaptic("light", btn);
    }, true);
    root.addEventListener("change", (e) => {
      const t = e.composedPath ? e.composedPath()[0] : e.target;
      if (!t || !t.tagName) return;
      if (t.dataset && t.dataset.haptic) { if (t.dataset.haptic !== "none") window.jhHaptic(t.dataset.haptic, t); return; }
      if (t.tagName === "SELECT" || (t.tagName === "INPUT" && t.type === "range")) window.jhHaptic("selection", t);
      else if (t.tagName === "INPUT" && (t.type === "checkbox" || t.type === "radio")) window.jhHaptic("light", t);
    }, true);
  };
  if (window.confirm && !window.confirm.__jhHaptic) {   // a destructive confirmation (Remove, Clear, Delete) warns first
    const ask = window.confirm.bind(window);
    window.confirm = function (msg) { window.jhHaptic("warning"); return ask(msg); };
    window.confirm.__jhHaptic = true;
  }
})();
/* jh-haptic END */
// Clean Jarvis optional "Assist history" (step 139, optional_looks.py). Home Assistant's own Assist pop-up (ha-voice-command-dialog, from
// the top bar or the companion app) starts empty every time. This brings the conversation back:
// - per Home Assistant login and per assistant (pipeline) the last 50 messages, both sides, newest last, are shown again when the
//   pop-up opens or another assistant is picked;
// - when the last answer is less than 4.5 minutes old, the same conversation_id is used again, so the assistant still has the context
//   (Home Assistant itself forgets a conversation after 5 minutes). Older: the messages are shown and a new conversation starts;
// - a "Clear history" button (broom-and-bin icon) at the top of the pop-up wipes the history of the assistant that is shown.
// Privacy: kept only in Home Assistant's frontend user data (frontend/set_user_data, key jh-assist-history-<pipeline id>), which is
// per login on the server and readable only by that login; text only (no thinking, tool calls or audio). Deliberately no copy in the
// browser (no localStorage): an assistant's answers can hold personal data, and a copy would stay readable on a shared tablet after
// another login.
// Home Assistant's dialog is not replaced: this wraps a few methods of ha-assist-chat (the chat inside the pop-up) and adds one button.
// If Home Assistant renames what this hooks into (ha-assist-chat, its _conversation state, _conversationId, _processText), it does
// nothing and the pop-up works as before; any error inside the add-on is caught and leaves Home Assistant's own behaviour alone.
// Only the chat inside ha-voice-command-dialog is touched (not the debug chat in Settings > Voice assistants).
// Added to every page with add_extra_js_url while the switch is on, always with a content hash (?v=); a second copy does nothing.
(() => {
  if (window.__jhAssistHistory) return;
  window.__jhAssistHistory = true;
  const CHAT = "ha-assist-chat", DIALOG = "ha-voice-command-dialog";
  const KEY = "jh-assist-history-";   // + pipeline id
  const KEEP = 50;                    // messages kept per assistant
  const MAX_TEXT = 8000;              // characters kept per message
  const FRESH = 4.5 * 60 * 1000;      // a conversation_id younger than this is used again
  const SAVE_DELAY = 800;             // answers stream in: save once they settle
  const CLEAR = "jh-assist-clear";
  const BROOM = "M15,16H19V18H15V16M15,8H22V10H15V8M15,12H21V14H15V12M11,10V18H5V10H11M13,8H3V18A2,2 0 0,0 5,20H11A2,2 0 0,0 13,18V8M14,5H11L10,4H6L5,5H2V7H14V5Z";

  const greetings = new WeakSet();    // Home Assistant's "How can I help?" messages: never saved
  const restored = new WeakSet();     // messages this add-on put back
  const states = new WeakMap();       // chat element -> what this add-on knows about it
  const writes = new Map();           // user|key -> Promise of the last write (a read waits for it)

  const dialogOf = chat => { try { const h = chat.getRootNode().host; return h && h.localName === DIALOG ? h : null; } catch (e) { return null; } };
  const hassOf = chat => {
    const d = dialogOf(chat), h = (d && d.hass) || (document.querySelector("home-assistant") || {}).hass;
    return h && typeof h.callWS === "function" && h.user && h.user.id ? h : null;
  };
  const isGreeting = (chat, m) => {
    if (!m || m.who !== "hass") return false;
    if (greetings.has(m)) return true;
    try { return typeof chat._localize === "function" && m.text === chat._localize("ui.dialogs.voice_command.how_can_i_help"); } catch (e) { return false; }
  };
  const clean = (chat, conv) => (Array.isArray(conv) ? conv : [])
    .filter(m => m && !isGreeting(chat, m) && typeof m.text === "string" && m.text.trim() && m.text !== "…")
    .map(m => (m.error ? { who: m.who === "user" ? "user" : "hass", text: m.text.slice(0, MAX_TEXT), error: true }
                       : { who: m.who === "user" ? "user" : "hass", text: m.text.slice(0, MAX_TEXT) }))
    .slice(-KEEP);
  const fingerprint = (msgs, cid) => JSON.stringify([msgs, cid || null]);

  const read = (hass, key) => {
    const w = writes.get(hass.user.id + "|" + key) || Promise.resolve();
    return w.then(() => hass.callWS({ type: "frontend/get_user_data", key })).then(r => (r && r.value) || null);
  };
  const write = (hass, key, value) => {
    const id = hass.user.id + "|" + key;
    const p = (writes.get(id) || Promise.resolve()).then(() => hass.callWS({ type: "frontend/set_user_data", key, value })).catch(() => {});
    writes.set(id, p);
    return p;
  };

  // A new chat, or another assistant in it: show that assistant's saved messages again
  const start = chat => {
    const pid = chat.pipeline && chat.pipeline.id, hass = hassOf(chat);
    const st = { pid, key: pid && KEY + pid, hass, loaded: false, saved: null, timer: null };
    states.set(chat, st);
    if (!pid || !hass || !dialogOf(chat)) return;
    read(hass, st.key).then(value => {
      if (states.get(chat) !== st) return;   // closed or switched meanwhile
      const msgs = value && Array.isArray(value.messages) ? value.messages.filter(m => m && typeof m.text === "string").slice(-KEEP) : [];
      const old = msgs.map(m => ({ who: m.who === "user" ? "user" : "hass", text: m.text, thinking: "", tool_calls: {}, ...(m.error ? { error: true } : {}) }));
      const conv = Array.isArray(chat._conversation) ? chat._conversation : [];
      const typed = conv.some(m => m && m.who === "user");
      let i = 0;
      while (i < conv.length && isGreeting(chat, conv[i])) i++;   // after Home Assistant's greeting, before anything new
      old.forEach(m => restored.add(m));
      if (old.length) chat._conversation = [...conv.slice(0, i), ...old, ...conv.slice(i)];
      const cid = value && typeof value.cid === "string" && value.cid ? value.cid : null;
      if (cid && !typed && Date.now() - (Number(value.at) || 0) < FRESH && "_conversationId" in chat && chat._conversationId == null) {
        chat._conversationId = cid;
      }
      st.saved = fingerprint(clean(chat, old), cid);
      st.loaded = true;
      if (typed) save(chat);   // something was sent before the history came in
    }, () => { /* not readable now: show nothing old and never overwrite what is saved */ });
  };

  const save = chat => {
    const st = states.get(chat);
    if (!st || !st.loaded || !st.hass) return;
    clearTimeout(st.timer); st.timer = null;
    try {
      const msgs = clean(chat, chat._conversation);
      const cid = typeof chat._conversationId === "string" ? chat._conversationId : null;
      const fp = fingerprint(msgs, cid);
      if (fp === st.saved) return;
      st.saved = fp;
      write(st.hass, st.key, { v: 1, messages: msgs, cid, at: Date.now() });
    } catch (e) { /* keep the pop-up working */ }
  };
  const later = chat => {
    const st = states.get(chat);
    if (!st || !st.loaded) return;
    clearTimeout(st.timer);
    st.timer = setTimeout(() => save(chat), SAVE_DELAY);
  };

  const clear = chat => {
    const st = states.get(chat);
    if (!st || !st.key || !st.hass) return;
    clearTimeout(st.timer); st.timer = null;
    const conv = Array.isArray(chat._conversation) ? chat._conversation : [];
    chat._conversation = conv.filter(m => isGreeting(chat, m));
    if ("_conversationId" in chat) chat._conversationId = null;   // and start a new conversation
    // a new state object: a read of the old history still on its way no longer applies (review round 4: Clear pressed while
    // the history was loading put the old messages back, and the next message saved them again)
    states.set(chat, Object.assign({}, st, { loaded: true, saved: fingerprint([], null), timer: null }));
    write(st.hass, st.key, { v: 1, messages: [], cid: null, at: 0 });
  };

  // The button next to Home Assistant's help button, in the pop-up's header
  const button = chat => {
    const dialog = dialogOf(chat), root = dialog && dialog.shadowRoot;
    const header = root && root.querySelector("ha-dialog-header");
    if (!header || header.querySelector("." + CLEAR) || !customElements.get("ha-icon-button")) return;
    const b = document.createElement("ha-icon-button");
    b.className = CLEAR;
    b.slot = "actionItems";
    b.path = BROOM;
    b.label = "Clear history";
    b.title = "Clear history";
    b.addEventListener("click", ev => {
      ev.stopPropagation();
      try { window.jhHapticLoad(hassOf(chat)); window.jhHaptic && window.jhHaptic("warning", b); } catch (e) {}   // jh-haptic (step 156): clearing is destructive
      try { const c = root.querySelector(CHAT); if (c) clear(c); } catch (e) { /* nothing to clear */ }
    });
    header.insertBefore(b, header.querySelector('[slot="actionItems"]'));
  };

  customElements.whenDefined(CHAT).then(() => {
    const cls = customElements.get(CHAT), proto = cls && cls.prototype, props = cls && cls.elementProperties;
    // Not the chat this was made for: leave Home Assistant alone
    if (!proto || typeof proto.willUpdate !== "function" || typeof proto.updated !== "function" || typeof proto._processText !== "function"
        || typeof proto.disconnectedCallback !== "function" || !props || !props.has("_conversation") || !props.has("pipeline")) return;

    const willUpdate = proto.willUpdate;
    proto.willUpdate = function (changed) {
      let fresh = false, before;
      try {
        before = this._conversation;
        const was = changed && changed.has && changed.has("pipeline") ? changed.get("pipeline") : this.pipeline;
        fresh = !this.hasUpdated || (was && was.id) !== (this.pipeline && this.pipeline.id);
      } catch (e) { /* see below */ }
      willUpdate.call(this, changed);
      try {
        // Home Assistant has started the chat again (a new pop-up or another assistant): it holds at most its greeting now
        if (fresh && this._conversation !== before && Array.isArray(this._conversation) && dialogOf(this)
            && this._conversation.every(m => m && m.who === "hass")) start(this);
      } catch (e) { /* leave the chat as Home Assistant has it */ }
    };

    const updated = proto.updated;
    proto.updated = function (changed) {
      updated.call(this, changed);
      try {
        if (!dialogOf(this)) return;
        button(this);
        if (changed && changed.has && changed.has("_conversation")) later(this);
      } catch (e) { /* leave the chat as Home Assistant has it */ }
    };

    const gone = proto.disconnectedCallback;
    proto.disconnectedCallback = function () {
      try { save(this); } catch (e) { /* closing must always work */ }
      return gone.apply(this, arguments);
    };

    // Remember which message is Home Assistant's greeting (it is put in front of the chat when it has loaded)
    if (typeof proto._loadGreeting === "function") {
      const loadGreeting = proto._loadGreeting;
      proto._loadGreeting = function () {
        const p = loadGreeting.apply(this, arguments);
        Promise.resolve(p).then(() => {
          const c = this._conversation;
          if (Array.isArray(c) && c[0] && c[0].who === "hass" && !restored.has(c[0])) greetings.add(c[0]);
        }, () => {});
        return p;
      };
    }
  });
})();
