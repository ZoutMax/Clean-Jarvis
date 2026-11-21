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
// Clean Jarvis optional page "Chat history" (custom:chat-history-card), for administrators: past JARVIS conversations of every
// login from JARVIS's own conversation store (jarvis/chat_history, admin-only), newest first, with a filter by login that is
// remembered per Home Assistant user (jhPref key jh-chat-history-who, see jh-pref.js) and restored before the first draw, and a
// Delete per entry (jarvis/chat_history_delete; JARVIS's long-term memory is not touched). Anybody else sees only a notice: the
// commands refuse them. Served by JARVIS (frontend/optional).
const CH_PREF = "jh-chat-history-who";
const CH_PREF_URL = new URL("jh-pref.js", import.meta.url).href;
const chLoadPref = () => window.jhPref ? Promise.resolve(window.jhPref) : import(CH_PREF_URL).then(() => window.jhPref || null, () => null);
const chEl = (tag, props = {}, ...kids) => { const e = Object.assign(document.createElement(tag), props); kids.forEach(k => e.append(k)); return e; };
const chWhen = t => { try { const d = new Date(String(t).endsWith("Z") || String(t).includes("+") ? t : t + "Z"); return isNaN(d) ? String(t || "") : d.toLocaleString(); } catch (e) { return String(t || ""); } };

class ChatHistoryCard extends HTMLElement {
  setConfig(c) { this._c = c || {}; }
  getCardSize() { return 12; }
  getGridOptions() { return { columns: "full", rows: "auto" }; }
  set hass(h) {
    const first = !this._h; this._h = h;
    if (!first) return;
    this.attachShadow({ mode: "open" });
    try { window.jhHapticLoad(h); window.jhHapticWire(this.shadowRoot); } catch (e) {}   // jh-haptic (step 156)
    this._who = "*"; this._entries = null; this._ask = null; this._msg = "";
    if (h.user && !h.user.is_admin) { this._draw(); return; }
    chLoadPref().then(P => {
      if (!P) return;
      this._P = P;
      this._off = P.on(h, CH_PREF, v => { if (typeof v === "string" && v !== this._who) { this._who = v; this._load(); } });
      return P.first(h, CH_PREF, 3000).then(v => { if (typeof v === "string") this._who = v; });
    }).catch(() => {}).then(() => this._load());
  }
  disconnectedCallback() { if (this._off) { this._off(); this._off = null; } }
  async _load(more) {
    const token = this._token = (this._token || 0) + 1;
    const last = more && this._entries && this._entries.length ? this._entries[this._entries.length - 1].id : undefined;
    try {
      const r = await this._h.callWS(Object.assign({ type: "jarvis/chat_history", who: this._who, limit: 50 }, last ? { before: last } : {}));
      if (token !== this._token) return;
      this._whos = Array.isArray(r.whos) ? r.whos : [];
      if (!this._whos.some(w => w.id === this._who)) {   // a saved login that is gone: everyone, saved, and loaded again (review round 5)
        this._who = "*";
        try { if (this._P && this._P.set) this._P.set(this._h, CH_PREF, "*"); } catch (e) { /* a local choice only */ }
        return this._load();
      }
      this._entries = (more ? this._entries || [] : []).concat(Array.isArray(r.entries) ? r.entries : []);
      this._more = !!r.more; this._err = "";
    } catch (e) {
      if (token !== this._token) return;
      this._err = "Could not load the chats: " + ((e && e.message) || e);
    }
    this._draw();
  }
  async _delete(entry) {
    try {
      await this._h.callWS({ type: "jarvis/chat_history_delete", ids: entry.ids });
      this._entries = (this._entries || []).filter(x => x !== entry); this._msg = "Deleted.";
    } catch (e) { this._msg = "Not deleted: " + ((e && e.message) || e); }
    this._ask = null; this._draw();
  }
  _draw() {
    const root = this.shadowRoot, h = this._h;
    root.textContent = "";
    root.append(chEl("style", { textContent: `
      ha-card{padding:16px;background:transparent;border:none;box-shadow:none;color:var(--primary-text-color);display:flex;flex-direction:column;gap:12px;box-sizing:border-box}
      .bar{display:flex;flex-wrap:wrap;gap:10px;align-items:center} .sm{opacity:.7;font-size:13px}
      select,button{font:inherit;padding:8px 12px;border-radius:10px;border:1px solid var(--divider-color);background:var(--card-background-color);color:var(--primary-text-color);cursor:pointer;max-width:100%}
      select{font-size:16px;min-width:0;flex:1 1 200px} button.del{color:var(--error-color,#db4437)} button.yes{background:var(--error-color,#db4437);color:#fff;border-color:var(--error-color,#db4437)}
      .chat{border:1px solid var(--divider-color);border-radius:12px;padding:12px 14px;display:flex;flex-wrap:wrap;gap:8px 12px;align-items:flex-start}
      .body{flex:1 1 220px;min-width:0} .top{display:flex;flex-wrap:wrap;gap:4px 10px;align-items:baseline;margin-bottom:6px} .who{font-weight:600;color:var(--primary-color)}
      .q{font-weight:600;margin-bottom:4px;overflow-wrap:anywhere;white-space:pre-wrap} .a{opacity:.85;white-space:pre-wrap;overflow-wrap:anywhere}
      .btns{display:flex;gap:6px;flex:none;margin-left:auto} .none{opacity:.7;padding:24px;text-align:center}` }));
    const card = chEl("ha-card");
    if (h.user && !h.user.is_admin) {
      card.append(chEl("div", { className: "none", textContent: "Only an administrator can see the chat history." }));
      root.append(card); return;
    }
    const pick = chEl("select");
    pick.setAttribute("aria-label", "Whose chats");
    (this._whos && this._whos.length ? this._whos : [{ id: "*", name: "Everyone" }]).forEach(w => pick.append(chEl("option", { value: w.id, textContent: w.name, selected: w.id === this._who })));
    pick.onchange = () => { this._who = pick.value; this._ask = null; this._msg = ""; if (this._P) this._P.set(h, CH_PREF, pick.value); this._load(); };
    card.append(chEl("div", { className: "bar" }, pick, chEl("span", { className: "sm", textContent: this._entries ? this._entries.length + " shown" : "" })));
    if (this._msg) card.append(chEl("div", { className: "sm", textContent: this._msg }));
    const list = this._entries || [];
    if (!list.length) card.append(chEl("div", { className: "none", textContent: this._err || (this._entries ? "No chats here." : "Loading chats...") }));
    list.forEach(c => {
      const btns = chEl("div", { className: "btns" });
      if (this._ask === c) {
        const yes = chEl("button", { className: "yes", textContent: "Delete" }); yes.onclick = () => this._delete(c);
        const no = chEl("button", { textContent: "Cancel" }); no.onclick = () => { this._ask = null; this._draw(); };
        btns.append(yes, no);
      } else {
        const del = chEl("button", { className: "del", textContent: "Delete" }); del.dataset.haptic = "warning"; del.onclick = () => { this._ask = c; this._draw(); };
        btns.append(del);
      }
      card.append(chEl("div", { className: "chat" }, chEl("div", { className: "body" },
        chEl("div", { className: "top" }, chEl("span", { className: "who", textContent: c.who || "" }), chEl("span", { className: "sm", textContent: chWhen(c.t) }),
          c.source && c.source !== "unknown" ? chEl("span", { className: "sm", textContent: c.source }) : ""),
        chEl("div", { className: "q", textContent: c.q || "" }), chEl("div", { className: "a", textContent: c.a || "(no answer)" })), btns));
    });
    if (this._more) { const more = chEl("button", { textContent: "Older chats" }); more.onclick = () => this._load(true); card.append(more); }
    root.append(card);
  }
}
if (!customElements.get("chat-history-card")) customElements.define("chat-history-card", ChatHistoryCard);
