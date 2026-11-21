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
// Clean Jarvis optional "My phone" page. Each person sees the sensors of their own phones and tablets, nothing else, one device at a time.
// A phone or tablet is yours when its Home Assistant app is signed in with your login (JARVIS's read-only
// jarvis/user_devices command, which gives anybody but an admin only their own devices). Nothing has to be linked to the person,
// so a tablet's location never counts for home/away. With an older JARVIS without that command it falls back to the trackers linked to your person in
// Settings > People. With more than one device a dropdown at the top picks which one to show, phones first; the choice is remembered
// per Home Assistant user (jhPref, see jh-pref.js: it follows the login to every browser and device), restored before the first
// render; a choice made per browser by an older build is taken over once. Without a remembered choice it shows the device the page is opened on (its model in the browser's user agent), else the
// first phone. Devices are shown exactly as registered in Home Assistant: the device name (your own name for it when set) and its
// model, "name (model)" when the two differ. Everything is read live, the list uses Home Assistant's own entities card.
const PP_KEY = 'jh-my-phone';   // the jhPref key, and the localStorage key of the older per-browser choice (read once)
// jh-pref.js (null when it cannot load: the choice then lasts this visit)
const ppPref = () => window.jhPref ? Promise.resolve(window.jhPref) : import(new URL("jh-pref.js", import.meta.url).href).then(() => window.jhPref || null, () => null);

// What kind of device. A phone line always means a phone: an iPhone, or a device with a phone state, SIM, mobile data or SIM signal
// sensor. An iPad is a tablet. Without a phone line it is a watch only when the model or maker says so (a Galaxy Watch model, any
// "Watch", Wear OS) or it has Wear OS's own sensors (on-body, wet mode); health sensors such as heart rate or steps never decide it,
// phones and tablets have them too. Anything else is a tablet. Same rule as jhKind in person-page-card.js.
const ppKind = d => {
  // review round 7: each sensor with its language-free key from Home Assistant ('sensor.pixel_8_telefoonstatus _phone_state')
  const model = d.model || '', maker = d.manufacturer || '', all = (d.all || []).map(e => (d.keys && d.keys[e]) ? e + ' _' + d.keys[e] : e);
  if (/^iphone/i.test(model)) return 'phone';
  if (/^ipad/i.test(model)) return 'tablet';
  if (all.some(e => /_(phone_state|sim_\d+|mobile_data)$/.test(e))) return 'phone';
  if (/watch|wear ?os|^sm-r\d/i.test(model + ' ' + maker) || all.some(e => /_(on_body_sensor|wet_mode)$/.test(e))) return 'watch';
  return 'tablet';
};
// The registered name, plus the registered model in brackets when it is not the same text
const ppName = d => d.model && d.model !== d.name ? d.name + ' (' + d.model + ')' : d.name;

class PhonePageCard extends HTMLElement {
  setConfig(c) { this._c = c || {}; }
  getCardSize() { return 12; }
  connectedCallback() { if (this._P && !this._off) this._listen(); }
  disconnectedCallback() { if (this._off) { this._off(); this._off = null; } }
  set hass(h) {
    this._h = h;
    if (!this._built) this._build();
    try { window.jhHapticLoad(h); window.jhHapticWire(this.shadowRoot || this); } catch (e) {}   // jh-haptic (step 156)
    if (!this._prefAsked) this._askPref();
    this._load();
    (this._cards || []).forEach(c => { c.hass = h; });
    this._refresh();
  }
  _refresh() {
    const sig = this._sig();
    if (sig !== this._last) { this._last = sig; this._show(); }
  }
  // The login -> apps answer, asked again whenever the entity registry changes (a new or removed app changes it).
  _load() {
    const h = this._h;
    if (this._state === 'off' || this._state === 'busy' || this._state === 'wait' || (this._state === 'ok' && this._reg === h.entities)) return;
    if (!h.callWS) { this._state = 'off'; return; }
    this._state = 'busy'; this._reg = h.entities;
    h.callWS({ type: 'jarvis/user_devices' }).then(r => {
      if (Array.isArray(r?.people)) { this._list = r.people; this._state = 'ok'; } else { this._list = null; this._state = 'off'; }
    }, e => {
      // no such command (an older JARVIS): the old way from now on. Any other error (a dropped connection, Home Assistant still starting) keeps the
      // last good answer, or, before the first one, is asked again in 5 s instead of falling back for the whole visit (review 2026-11-15)
      // (asked once more after 30 s: right after a restart JARVIS may not be loaded yet)
      if (e?.code === 'unknown_command' && !this._list) {
        this._state = 'off';
        if (!this._again) { this._again = true; setTimeout(() => { if (this._state === 'off' && !this._list) { this._state = 'idle'; if (this._h) { this._load(); } } }, 30000); }
        return;
      }
      if (this._list) { this._state = 'ok'; return; }
      this._state = 'wait'; clearTimeout(this._retry);
      this._retry = setTimeout(() => { if (this._state === 'wait') { this._state = 'idle'; if (this._h) this._load(); } }, 5000);
    }).finally(() => this._refresh());
  }
  // { name, logins, devices: [{ id, name, model, manufacturer, all }] } for this login; null when there is none; undefined while the
  // first answer is on its way. The name is already the registered one (name_by_user, else name).
  _me() {
    const h = this._h, uid = h.user?.id;
    if (this._list) {
      const p = this._list.find(x => x.user_id === uid);
      if (!p) return null;   // review round 10: the list has come and this login is not in it: the empty state, never a blank card
      return { name: p.name, logins: true, devices: (p.devices || []).map(d => ({ id: d.device_id, name: d.name || d.device_id, model: d.model || '',
        manufacturer: d.manufacturer || '', all: d.entities || [], keys: d.keys || {} })) };
    }
    if (this._state !== 'off' && this._state !== 'wait') return undefined;   // 'wait': the fallback until the list is asked again
    const s = Object.values(h.states).find(x => x.entity_id.startsWith('person.') && x.attributes.user_id === uid);
    if (!s) return null;
    const reg = h.entities || {}, ids = [...new Set((s.attributes.device_trackers || []).map(t => reg[t]?.device_id).filter(Boolean))];
    return { name: s.attributes.friendly_name || s.entity_id, logins: false, devices: ids.map(id => ({ id,
      name: h.devices?.[id]?.name_by_user || h.devices?.[id]?.name || id, model: h.devices?.[id]?.model || '', manufacturer: h.devices?.[id]?.manufacturer || '',
      all: Object.keys(reg).filter(e => reg[e]?.device_id === id && !e.startsWith('device_tracker.')) })) };
  }
  // [{id, kind, name, model, ents}] per device of the person with something to show, phones first
  _devices(p) {
    const h = this._h, order = { phone: 0, tablet: 1, watch: 2 };
    return p.devices.map(d => ({ id: d.id, kind: ppKind(d), name: d.name, model: d.model, ents: d.all.filter(e => h.states[e]).sort() }))
      .filter(d => d.ents.length).sort((a, b) => order[a.kind] - order[b.kind] || a.name.localeCompare(b.name));
  }
  // The saved choice of this user: asked once, before the first render (it stops waiting after 3 s). Without one, the older
  // per-browser choice is taken over (saved for the user and removed from the browser). Review 2026-11-15: a timeout is not "nothing
  // saved": the page then shows the older choice for now, saves nothing, and takes the real answer when it comes (unless a device was
  // picked meanwhile); the older choice is moved over only when that real answer is "nothing saved".
  _askPref() {
    this._prefAsked = true;
    const h = this._h, old = () => { try { return localStorage.getItem(PP_KEY); } catch (e) { return null; } };
    const answer = (P, v) => {
      if (typeof v === 'string' && v) { if (!this._pick) this._pref = v; return; }
      const was = old();
      if (was && v === undefined) { if (!this._pick) { this._pref = was; P.set(h, PP_KEY, was); } try { localStorage.removeItem(PP_KEY); } catch (e) {} }
    };
    ppPref().then(P => {
      if (!P) { this._pref = old(); return; }
      this._P = P; this._listen();
      return P.first(h, PP_KEY, 3000).then(v => {
        if (v !== P.TIMEOUT) { answer(P, v); return; }
        this._pref = P.peek(h, PP_KEY) || old() || this._pref;   // for now only
        P.get(h, PP_KEY).then(late => { const was = this._pref; answer(P, late); if (this._pref !== was) this._refresh(); });
      });
    }).catch(() => {}).then(() => { this._prefReady = true; this._refresh(); });
  }
  // a choice made in another card or tab of this browser for the same user
  _listen() { this._off = this._P.on(this._h, PP_KEY, v => { if (typeof v === 'string' && v !== this._pref) { this._pref = v; this._pick = null; this._refresh(); } }); }
  _saved() { return this._pref || null; }
  // The model of the device this page runs on, when the browser tells it (Android puts it in the user agent; Chrome's client hints
  // give it when the user agent hides it). Only used when exactly one of the person's devices has that model.
  _here(devs) {
    if (this._hint === undefined) {
      this._hint = null;
      try {
        navigator.userAgentData?.getHighEntropyValues?.(['model']).then(v => { if (v?.model) { this._hint = v.model; this._refresh(); } }, () => {});
      } catch (e) {}
    }
    const ua = (navigator.userAgent || '') + ' ' + (this._hint || '');
    const reEsc = s => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');   // jh-r19-xss (step 161): a RegExp escape, not an HTML one, so not named esc
    const hits = devs.filter(d => d.model && d.model.length > 3 && new RegExp('(^|[^a-z0-9])' + reEsc(d.model) + '($|[^a-z0-9])', 'i').test(ua));
    return hits.length === 1 ? hits[0] : null;
  }
  _chosen(devs) {
    const want = this._pick || this._saved();
    return devs.find(d => d.id === want) || this._here(devs) || devs[0];
  }
  _sig() {
    const p = this._me();
    if (p === undefined || !this._prefReady) return 'wait';
    const devs = p ? this._devices(p) : [];
    return JSON.stringify([p?.name, devs, devs.length ? this._chosen(devs).id : null]);
  }
  _build() {
    this._built = true;
    this.innerHTML = `<style>
      .pp{max-width:760px;margin:0 auto;padding:16px;color:var(--primary-text-color);box-sizing:border-box}
      .pp-body{display:flex;flex-direction:column;gap:16px}
      .pp-pick{display:flex;gap:12px;align-items:center;padding:12px 16px}
      .pp-pick select{flex:1;min-width:0;font:inherit;padding:8px 10px;border-radius:8px;background:var(--card-background-color);color:inherit;border:1px solid var(--divider-color)}
      .pp-msg{padding:24px;text-align:center;color:var(--secondary-text-color)}
      @media (max-width:500px){.pp{padding:8px}}
    </style><div class="pp"><div class="pp-body"><ha-card class="pp-card" style="display:none"><div class="pp-pick"><label for="pp-sel">Device</label><select id="pp-sel"></select></div></ha-card><div class="pp-list"></div></div></div>`;
    this._pickCard = this.querySelector('.pp-card'); this._sel = this.querySelector('select'); this._out = this.querySelector('.pp-list');
    this._sel.onchange = () => {
      this._pick = this._pref = this._sel.value;
      if (this._P) this._P.set(this._h, PP_KEY, this._sel.value);
      this._refresh();
    };
  }
  async _show() {
    const p = this._me(), sig = this._last;
    if (p === undefined || !this._prefReady) return;
    this._cards = []; this._out.innerHTML = '';
    const msg = t => { this._pickCard.style.display = 'none'; const d = document.createElement('div'); d.className = 'pp-msg'; d.textContent = t; this._out.appendChild(d); };
    if (!p) return msg('No person is linked to this login.');
    const devs = this._devices(p);
    if (!devs.length) return msg(p.logins ? 'No phone or tablet is signed in to Home Assistant as ' + p.name + '.' : 'No phone or tablet is linked to ' + p.name + '.');
    const d = this._chosen(devs), cap = s => s[0].toUpperCase() + s.slice(1);
    this._sel.innerHTML = '';
    devs.forEach(x => { const o = document.createElement('option'); o.value = x.id; o.textContent = cap(x.kind) + ' \u00b7 ' + ppName(x); o.selected = x === d; this._sel.appendChild(o); });
    this._pickCard.style.display = devs.length < 2 ? 'none' : '';
    const helpers = await window.loadCardHelpers();
    if (sig !== this._last) return;
    // the sensor names without the device name in front: the title says which device (review 2026-11-15, cut off on a phone)
    const short = e => { const f = this._h.states[e]?.attributes?.friendly_name, n = d.name;
      return typeof f === 'string' && n && f.length > n.length + 1 && f.startsWith(n + ' ') ? { entity: e, name: cap(f.slice(n.length + 1)) } : e; };
    const el = helpers.createCardElement({ type: 'entities', title: p.name + ' - ' + d.kind + ' ' + ppName(d) + ' (' + d.ents.length + ')', entities: d.ents.map(short) });
    el.hass = this._h;
    this._cards = [el]; this._out.innerHTML = ''; this._out.appendChild(el);
  }
}
if (!customElements.get('phone-page-card')) customElements.define('phone-page-card', PhonePageCard);
