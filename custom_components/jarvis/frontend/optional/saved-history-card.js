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
// Clean Jarvis optional page "Saved history views" (custom:saved-history-card). Each login saves its own History selections and
// reopens them from a dropdown. They are kept per Home Assistant user (frontend user data key "saved_history_views"), so they
// follow the login to every browser, phone and tablet, and nobody else sees them. Served by JARVIS (frontend/optional).
class SavedHistoryCard extends HTMLElement {
  setConfig(config) { this._config = config || {}; this._views = []; }
  getCardSize() { return 4; }
  getGridOptions() { return { columns: 'full', rows: 'auto' }; }
  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first) { this._build(); this._load(); }
    try { window.jhHapticLoad(hass); window.jhHapticWire(this.shadowRoot); } catch (e) {}   // jh-haptic (step 156)
    if (this._picker) this._picker.hass = hass;
  }
  async _load() {
    try {
      const r = await this._hass.callWS({ type: 'frontend/get_user_data', key: 'saved_history_views' });
      this._views = (r && Array.isArray(r.value)) ? r.value : [];
      this._loaded = true;
    } catch (e) { this._views = []; this._loaded = false; }
    this._render();
  }
  // The latest list from HA (another device may have changed it). A read that fails throws, so nothing is saved over the
  // views Home Assistant holds (review 2026-11-15: a failed read wiped them on the next save).
  async _fresh() {
    const r = await this._hass.callWS({ type: 'frontend/get_user_data', key: 'saved_history_views' });
    if (r && r.value != null && !Array.isArray(r.value)) throw new Error('the saved views could not be read');
    this._loaded = true;
    return (r && Array.isArray(r.value)) ? r.value : [];
  }
  _change(fn) {   // re-fetch, apply the change, save, one at a time: no lost views when two devices edit
    const run = async () => {
      try {
        const v = await this._fresh(); fn(v);
        await this._hass.callWS({ type: 'frontend/set_user_data', key: 'saved_history_views', value: v });
        this._views = v; this._render(); return true;
      } catch (e) { this._say('Not saved: ' + ((e && e.message) || e)); return false; }
    };
    this._queue = (this._queue || Promise.resolve()).then(run);
    return this._queue;
  }
  _url(v) {
    if (v.url) return v.url;
    const t = v.target || {}, q = [];
    ['entity_id', 'device_id', 'area_id', 'label_id', 'floor_id'].forEach(k => {
      const val = t[k]; if (val && (!Array.isArray(val) || val.length)) q.push(k + '=' + encodeURIComponent([].concat(val).join(',')));
    });
    return '/history' + (q.length ? '?' + q.join('&') : '');
  }
  _open(v) {
    history.pushState(null, '', this._url(v));
    window.dispatchEvent(new CustomEvent('location-changed', { detail: { replace: false } }));
  }
  _build() {
    const root = this.attachShadow({ mode: 'open' });
    root.innerHTML = `
      <style>
        ha-card { padding: 16px; }
        h2 { margin: 0 0 12px; font-size: 18px; font-weight: 500; }
        .dd { position: relative; }
        .dd-btn { width: 100%; display: flex; justify-content: space-between; align-items: center; padding: 10px 12px; border-radius: 8px;
                  border: 1px solid var(--divider-color); background: var(--card-background-color); color: var(--primary-text-color); font-size: 15px; cursor: pointer; }
        .list { display: none; margin-top: 4px; border: 1px solid var(--divider-color); border-radius: 8px; overflow: hidden; }
        .list.open { display: block; }
        .row { display: flex; align-items: center; border-bottom: 1px solid var(--divider-color); }
        .row:last-child { border-bottom: none; }
        .name { flex: 1; padding: 10px 12px; cursor: pointer; color: var(--primary-text-color); }
        .name:hover { background: var(--secondary-background-color); }
        .del { width: 40px; height: 40px; border: none; background: none; color: var(--error-color, #db4437); font-size: 20px; cursor: pointer; }
        .empty { padding: 10px 12px; color: var(--secondary-text-color); }
        .tabs { display: flex; gap: 8px; margin: 16px 0 8px; }
        .tab { padding: 6px 12px; border-radius: 16px; border: 1px solid var(--divider-color); background: none; color: var(--primary-text-color); cursor: pointer; }
        .tab.on { background: var(--primary-color); color: var(--text-primary-color, #fff); border-color: var(--primary-color); }
        .pane { display: none; } .pane.on { display: block; }
        .name { overflow-wrap: anywhere; min-width: 0; }
        input { width: 100%; box-sizing: border-box; padding: 9px 10px; margin: 6px 0; border-radius: 6px; border: 1px solid var(--divider-color);
                background: var(--card-background-color); color: var(--primary-text-color); font-size: 14px; }
        .save { margin-top: 8px; padding: 8px 16px; border: none; border-radius: 6px; background: var(--primary-color); color: var(--text-primary-color, #fff); cursor: pointer; }
        .msg { font-size: 12px; color: var(--secondary-text-color); margin-top: 6px; min-height: 16px; }
      </style>
      <ha-card>
        <h2>Saved history views</h2>
        <div class="dd">
          <button class="dd-btn" id="dd"><span id="ddlabel">Choose a saved view</span><span>&#9662;</span></button>
          <div class="list" id="list"></div>
        </div>
        <div class="tabs"><button class="tab on" data-p="new">+ New view</button><button class="tab" data-p="link">Save from link</button></div>
        <div class="pane on" id="p-new">
          <input id="n1" placeholder="Name, e.g. Heaters this week">
          <div id="pickwrap"></div>
          <button class="save" id="s1">Save view</button>
        </div>
        <div class="pane" id="p-link">
          <input id="n2" placeholder="Name">
          <input id="u2" placeholder="Paste a History link (.../history?...)">
          <button class="save" id="s2">Save link</button>
        </div>
        <div class="msg" id="msg"></div>
      </ha-card>`;
    const $ = id => root.getElementById(id);
    $('dd').onclick = () => $('list').classList.toggle('open');
    root.querySelectorAll('.tab').forEach(b => b.onclick = () => {
      root.querySelectorAll('.tab').forEach(x => x.classList.toggle('on', x === b));
      $('p-new').classList.toggle('on', b.dataset.p === 'new'); $('p-link').classList.toggle('on', b.dataset.p === 'link');
    });
    this._target = {};
    if (customElements.get('ha-selector')) {
      const p = document.createElement('ha-selector');
      p.selector = { target: {} }; p.value = {}; p.label = 'What to show';
      p.addEventListener('value-changed', e => { this._target = e.detail.value || {}; p.value = this._target; });
      this._picker = p; $('pickwrap').appendChild(p);
    } else {
      $('pickwrap').innerHTML = '<input id="ents" placeholder="Entity ids, comma separated (e.g. light.kitchen, switch.kitchen_heater)">';
    }
    const msg = t => { $('msg').textContent = t; setTimeout(() => { if ($('msg').textContent === t) $('msg').textContent = ''; }, 4000); };
    this._say = msg;
    $('s1').onclick = async () => {
      const name = $('n1').value.trim(); let target = this._target || {};
      const ents = root.getElementById('ents');
      if (ents) target = { entity_id: ents.value.split(',').map(s => s.trim()).filter(Boolean) };
      if (!name) return msg('Give the view a name.');
      if (!Object.values(target).some(v => v && [].concat(v).length)) return msg('Pick at least one area, device, entity or label.');
      if (await this._change(v => v.push({ name, target }))) { $('n1').value = ''; msg('Saved "' + name + '".'); }
    };
    $('s2').onclick = async () => {
      const name = $('n2').value.trim(), raw = $('u2').value.trim();
      if (!name) return msg('Give the view a name.');
      let url;
      try { const u = new URL(raw, location.origin); if (!u.pathname.endsWith('/history')) throw 0; url = '/history' + u.search; } catch (e) { return msg('That is not a History link.'); }
      if (await this._change(v => v.push({ name, url }))) { $('n2').value = ''; $('u2').value = ''; msg('Saved "' + name + '".'); }
    };
    this._root = root;
  }
  _render() {
    if (!this._root) return;
    const list = this._root.getElementById('list'); list.innerHTML = '';
    const views = this._views || [];
    this._root.getElementById('ddlabel').textContent = views.length ? 'Choose a saved view (' + views.length + ')' : 'No saved views yet';
    if (!views.length) { list.innerHTML = '<div class="empty">Nothing saved yet - create one below.</div>'; return; }
    views.forEach((v, i) => {
      const row = document.createElement('div'); row.className = 'row';
      const n = document.createElement('div'); n.className = 'name'; n.textContent = v.name; n.title = this._url(v); n.onclick = () => this._open(v);
      const d = document.createElement('button'); d.className = 'del'; d.textContent = '\u00d7'; d.title = 'Delete "' + v.name + '"';
      d.onclick = async ev => { ev.stopPropagation(); if (window.confirm('Delete saved view "' + v.name + '"?')) { await this._change(list => { const j = list.findIndex(x => x.name === v.name && JSON.stringify(x) === JSON.stringify(v)); if (j >= 0) list.splice(j, 1); }); } };
      row.appendChild(n); row.appendChild(d); list.appendChild(row);
    });
  }
}
if (!customElements.get('saved-history-card')) {
  customElements.define('saved-history-card', SavedHistoryCard);
  window.customCards = window.customCards || [];
  window.customCards.push({ type: 'saved-history-card', name: 'Saved history views', description: 'Save History page selections and reopen them from a dropdown.' });
}
