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
// Clean Jarvis optional People page: a person dropdown and a device dropdown for a page, plus a wrapper that points Home Assistant's own cards at the chosen
// person's chosen device (phones first). The person and the device per person stay chosen per Home Assistant user (jhPref key
// jh-person-page, see jh-pref.js: it follows the login to every browser and device), restored before the first render; the choice an
// older build kept per browser is taken over once.
// Which phones and tablets belong to whom comes from the Home Assistant login each app is signed in with (Household Chat's
// JARVIS's read-only jarvis/user_devices command): a tablet signed in as Alex shows under Alex without being linked to that
// person, so its location never counts for their home/away. A login with apps but no person shows under the login's own name.
// With an older JARVIS without that command it falls back to the trackers linked to the person in Settings > People, and then a
// non-admin still sees only their own person.
// Everything is read live; which sensors to show lives in the page config.
const JH_KEY = 'jh-person-page';   // localStorage key of the older per-browser person choice (read once); also the jhPref key

// The saved choice of this user: { person: person key, devices: { person key: device id } }. Asked once per page load, before the
// first render (it stops waiting after 3 s). Without jh-pref.js the older per-browser choice is used.
// Review 2026-11-15: a timeout is not "nothing saved". The page then starts from this browser's copy (or the older per-browser
// choice) for now, saves nothing, and merges the real answer in when it comes: what was picked on this page (mine) stays, everything
// else (the other people's devices) comes from the answer. Nothing is saved before that answer is in (based), so a save can never
// wipe the devices remembered for the other people; a pick made meanwhile is saved together with the answer.
const jhPrefS = { asked: false, ready: false, based: false, P: null, h: null, v: { person: null, devices: {} }, mine: { person: undefined, devices: {} } };
const jhPrefNorm = v => ({ person: v && typeof v.person === 'string' ? v.person : null,
  devices: v && v.devices && typeof v.devices === 'object' && !Array.isArray(v.devices) ? { ...v.devices } : {} });
const jhPrefOld = () => {
  const get = k => { try { return localStorage.getItem(k); } catch (e) { return null; } };
  let devices = {}; try { devices = JSON.parse(get('jh-person-device') || '{}') || {}; } catch (e) {}
  return { person: get(JH_KEY), devices };
};
const jhPrefTouched = () => jhPrefS.mine.person !== undefined || Object.keys(jhPrefS.mine.devices).length > 0;
// v with the picks made on this page on top
const jhPrefMerge = v => {
  const out = jhPrefNorm(v), mine = jhPrefS.mine;
  if (mine.person !== undefined) out.person = mine.person;
  Object.entries(mine.devices).forEach(([k, id]) => { if (id == null) delete out.devices[k]; else out.devices[k] = id; });
  return out;
};
const jhPrefAsk = h => {
  const S = jhPrefS;
  S.h = h;
  if (S.asked) return;
  S.asked = true;
  const changed = () => window.dispatchEvent(new Event('jh-person-changed'));
  const done = () => { S.ready = true; window.__jhPerson = undefined; window.__jhDevice = {}; changed(); };
  (window.jhPref ? Promise.resolve(window.jhPref) : import(new URL("jh-pref.js", import.meta.url).href).then(() => window.jhPref || null, () => null)).then(P => {
    if (!P) { S.v = jhPrefNorm(jhPrefOld()); return; }
    S.P = P;
    const quick = P.peek(h, JH_KEY), old = jhPrefOld(), hasOld = !!(old.person || Object.keys(old.devices).length);
    // a choice made in another card or tab of this browser for the same user
    P.on(h, JH_KEY, v => { if (v && typeof v === 'object') { S.v = S.based ? jhPrefNorm(v) : jhPrefMerge(v); window.__jhPerson = undefined; window.__jhDevice = {}; changed(); } });
    const answer = v => {   // the real answer
      S.based = true;
      if (v && typeof v === 'object') { S.v = jhPrefMerge(v); if (jhPrefTouched()) jhPrefSave(); return; }
      if (v === undefined && hasOld) {   // once: saved for the user, then removed from the browser
        S.v = jhPrefMerge(old); jhPrefSave();
        try { localStorage.removeItem(JH_KEY); localStorage.removeItem('jh-person-device'); } catch (e) {}
        return;
      }
      S.v = jhPrefMerge(S.v);
      if (jhPrefTouched()) jhPrefSave();
    };
    return Promise.race([P.saved(h, JH_KEY), new Promise(r => setTimeout(() => r(P.TIMEOUT), 3000))]).then(v => {
      if (v !== P.TIMEOUT) { answer(v); return; }
      S.v = jhPrefNorm(quick && typeof quick === 'object' ? quick : hasOld ? old : null);   // for now only
      P.saved(h, JH_KEY).then(late => { answer(late); changed(); });
    });
  }).catch(() => {}).then(done);
};
const jhPrefSave = () => { const S = jhPrefS; if (S.P && S.h && S.based) S.P.set(S.h, JH_KEY, { person: S.v.person, devices: { ...S.v.devices } }); };

// The login -> apps list, asked again whenever the entity registry changes (a new or removed app changes it) or the login changes.
const jhLogins = { list: null, state: 'idle', reg: undefined, user: undefined, retry: 0 };   // state: idle | busy | ok | wait (asked again soon) | off (no command)
const jhLoad = h => {
  jhPrefAsk(h);
  const L = jhLogins;
  if (L.state === 'off' || L.state === 'busy' || L.state === 'wait' || (L.state === 'ok' && L.reg === h.entities && L.user === h.user?.id)) return;
  if (!h.callWS) { L.state = 'off'; return; }
  L.state = 'busy'; L.reg = h.entities; L.user = h.user?.id;
  h.callWS({ type: 'jarvis/user_devices' }).then(r => {
    if (Array.isArray(r?.people)) { L.list = r.people; L.state = 'ok'; } else { L.list = null; L.state = 'off'; }
  }, e => {
    // no such command (an older JARVIS): the old way from now on. Any other error (a dropped connection, Home Assistant still starting) keeps the
    // last good list, or, before the first one, shows the fallback meanwhile and asks again in 5 s (review 2026-11-15)
    // (asked once more after 30 s: right after a restart JARVIS may not be loaded yet)
    if (e?.code === 'unknown_command' && !L.list) {
      L.state = 'off';
      if (!L.again) { L.again = true; setTimeout(() => { if (L.state === 'off' && !L.list) { L.state = 'idle'; if (jhPrefS.h) jhLoad(jhPrefS.h); } }, 30000); }
      return;
    }
    if (L.list) { L.state = 'ok'; return; }
    L.state = 'wait'; clearTimeout(L.retry);
    L.retry = setTimeout(() => { if (L.state === 'wait') { L.state = 'idle'; if (jhPrefS.h) jhLoad(jhPrefS.h); } }, 5000);
  }).finally(() => window.dispatchEvent(new Event('jh-person-changed')));
};

// People: [{ key, id (person entity or null), name, user, logins (true: devices from the login), devices: [{ id, name, model, tracker, all }] }]
// null while the first answer is on its way.
const jhPeople = h => {
  if (jhLogins.list) return jhLogins.list.map(p => ({ key: p.person || 'user:' + p.user_id, id: p.person, name: p.name, user: p.user_id, logins: true,
    devices: (p.devices || []).map(d => ({ id: d.device_id, name: d.name || d.device_id, model: d.model || '', manufacturer: d.manufacturer || '', tracker: d.tracker, all: d.entities || [], keys: d.keys || {} })) }))
    .sort((a, b) => a.name.localeCompare(b.name));
  if (jhLogins.state !== 'off' && jhLogins.state !== 'wait') return null;   // 'wait': the fallback until the list is asked again
  const reg = h.entities || {};
  const admin = !h.user || h.user.is_admin;   // privacy: anybody but an admin only ever sees their own person
  return Object.values(h.states).filter(s => s.entity_id.startsWith('person.') && (admin || s.attributes.user_id === h.user.id)).map(s => {
    const ids = [...new Set((s.attributes.device_trackers || []).map(t => reg[t]?.device_id).filter(Boolean))];
    const devices = ids.map(id => {
      const own = Object.keys(reg).filter(e => reg[e]?.device_id === id);
      return { id, name: h.devices?.[id]?.name_by_user || h.devices?.[id]?.name || id, model: h.devices?.[id]?.model || '', manufacturer: h.devices?.[id]?.manufacturer || '',
               tracker: own.find(e => e.startsWith('device_tracker.')), all: own.filter(e => !e.startsWith('device_tracker.')) };
    });
    return { key: s.entity_id, id: s.entity_id, name: s.attributes.friendly_name || s.entity_id, user: s.attributes.user_id, logins: false, devices };
  }).sort((a, b) => a.name.localeCompare(b.name));
};
const jhSaved = () => jhPrefS.v.person;
const jhChosen = h => {
  const ppl = jhPeople(h), want = window.__jhPerson || jhSaved();
  if (!ppl || !jhPrefS.ready) return null;
  return ppl.find(p => p.key === want) || ppl.find(p => p.user && p.user === h.user?.id) || ppl[0];
};
// What kind of device. A phone line always means a phone: an iPhone, or a device with a phone state, SIM, mobile data or SIM signal
// sensor. An iPad is a tablet. Without a phone line it is a watch only when the model or maker says so (a Galaxy Watch model, any
// "Watch", Wear OS) or it has Wear OS's own sensors (on-body, wet mode); health sensors such as heart rate or steps never decide it,
// phones and tablets have them too. Anything else is a tablet. Same rule as ppKind in phone-page-card.js.
const jhKind = d => {
  // review round 7: each sensor with its language-free key from Home Assistant ('sensor.pixel_8_telefoonstatus _phone_state')
  const model = d.model || '', maker = d.manufacturer || '', all = (d.all || []).map(e => (d.keys && d.keys[e]) ? e + ' _' + d.keys[e] : e);
  if (/^iphone/i.test(model)) return 'phone';
  if (/^ipad/i.test(model)) return 'tablet';
  if (all.some(e => /_(phone_state|sim_\d+|mobile_data)$/.test(e))) return 'phone';
  if (/watch|wear ?os|^sm-r\d/i.test(model + ' ' + maker) || all.some(e => /_(on_body_sensor|wet_mode)$/.test(e))) return 'watch';
  return 'tablet';
};
// The registered name (name_by_user, else name), plus the registered model in brackets when it is not the same text
const jhName = d => d.model && d.model !== d.name ? d.name + ' (' + d.model + ')' : d.name;
// Every device of the person with more than a tracker, phones first, with the entities that have a state now. label is the kind plus
// the registered device name (and model), such as 'phone Pixel 8'.
const jhDevices = (h, p) => {
  const order = { phone: 0, tablet: 1, watch: 2 };
  const list = p.devices.map(d => ({ ...d, kind: jhKind(d), ents: d.all.filter(e => h.states[e]) })).filter(d => d.ents.length)
    .sort((a, b) => order[a.kind] - order[b.kind] || a.name.localeCompare(b.name));
  list.forEach(d => { d.label = d.kind + ' ' + jhName(d); });
  return list;
};
// The chosen device of a person: remembered per person per user ({person key: device id}, in jhPref with the person), so picking a
// person again goes straight to the device chosen for them; without one, or when it is gone, that person's first phone. The page
// shows only this device.
window.__jhDevice = window.__jhDevice || {};
const jhDevSaved = () => jhPrefS.v.devices;
const jhSetDevice = (key, id) => {
  window.__jhDevice[key] = id;
  jhPrefS.mine.devices[key] = id == null ? null : id;
  if (id == null) delete jhPrefS.v.devices[key]; else jhPrefS.v.devices[key] = id;
  jhPrefSave();
};
const jhChosenDevice = (h, p) => {
  const list = jhDevices(h, p), want = p.key in window.__jhDevice ? window.__jhDevice[p.key] : jhDevSaved()[p.key];
  return list.find(d => d.id === want) || list[0] || null;
};
const jhPhone = (h, p) => jhChosenDevice(h, p)?.ents || [];
const jhCap = s => s[0].toUpperCase() + s.slice(1);
// A sensor's name without the device name in front ("Pixel 8 Battery level" -> "Battery level"): the heading already says which
// device, and on a phone the long names were cut off (review 2026-11-15). Only for list cards; null when there is nothing to take off.
const jhShort = (h, e, dev) => {
  const f = h.states[e]?.attributes?.friendly_name, n = dev?.name;
  return typeof f === 'string' && n && f.length > n.length + 1 && f.startsWith(n + ' ') ? jhCap(f.slice(n.length + 1)) : null;
};
const JH_NAMED = ['entities', 'history-graph', 'glance'];

class PersonPickerCard extends HTMLElement {
  setConfig(c) { this._c = c || {}; }
  _esc(s) {   // jh-r19-xss (step 161): the card's label comes from the dashboard config
    return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }
  getCardSize() { return 1; }
  getGridOptions() { return { columns: 'full', rows: 'auto' }; }
  connectedCallback() { this._on = () => this._h && this._draw(); window.addEventListener('jh-person-changed', this._on); }
  disconnectedCallback() { window.removeEventListener('jh-person-changed', this._on); }
  set hass(h) {
    this._h = h; jhLoad(h);
    try { window.jhHapticLoad(h); window.jhHapticWire(this); } catch (e) {}   // jh-haptic (step 156)
    if (!this._sel) {
      this.innerHTML = `<style>
        .jp{display:flex;gap:12px;align-items:center;padding:12px 16px;color:var(--primary-text-color)}
        .jp select{flex:1;font:inherit;padding:8px 10px;border-radius:8px;background:var(--card-background-color);color:inherit;border:1px solid var(--divider-color)}
        .jp-note{padding:0 16px 12px;color:var(--secondary-text-color)}
        .jp label{min-width:56px}
        .jp-dev{padding-top:0}
      </style><ha-card><div class="jp"><label>${this._esc(this._c.label || 'Person')}</label><select class="jp-person"></select></div>
        <div class="jp jp-dev" style="display:none"><label>Device</label><select class="jp-device"></select></div><div class="jp-note" hidden></div></ha-card>`;
      this._sel = this.querySelector('.jp-person'); this._dsel = this.querySelector('.jp-device'); this._drow = this.querySelector('.jp-dev');
      this._note = this.querySelector('.jp-note');
      this._sel.onchange = () => {
        window.__jhPerson = this._sel.value;
        jhPrefS.v.person = jhPrefS.mine.person = this._sel.value; jhPrefSave();   // that person's device stays as it was chosen for them
        window.dispatchEvent(new Event('jh-person-changed'));
      };
      this._dsel.onchange = () => {
        const p = jhChosen(this._h);
        if (p) jhSetDevice(p.key, this._dsel.value);
        window.dispatchEvent(new Event('jh-person-changed'));
      };
    }
    this._draw();
  }
  _draw() {
    const h = this._h, ppl = jhPeople(h), cur = jhChosen(h);
    if (!ppl || !jhPrefS.ready) return;
    const key = ppl.map(p => p.key + p.name).join() + '|' + cur?.key;
    if (key !== this._key) {
      this._key = key; this._sel.innerHTML = '';
      ppl.forEach(p => { const o = document.createElement('option'); o.value = p.key; o.textContent = p.name; o.selected = p.key === cur?.key; this._sel.appendChild(o); });
    }
    const devs = cur ? jhDevices(h, cur) : [], dev = cur && jhChosenDevice(h, cur);
    const dkey = devs.map(d => d.id + d.kind + jhName(d)).join() + '|' + dev?.id;
    if (dkey !== this._dkey) {
      this._dkey = dkey; this._dsel.innerHTML = '';
      devs.forEach(d => { const o = document.createElement('option'); o.value = d.id; o.textContent = jhCap(d.kind) + ' \u00b7 ' + jhName(d); o.selected = d.id === dev?.id; this._dsel.appendChild(o); });
    }
    this._drow.style.display = devs.length ? '' : 'none';
    this._dsel.disabled = devs.length < 2;
    const none = !!cur && !jhPhone(h, cur).length;
    this._note.hidden = !none;
    if (none) this._note.textContent = cur.logins ? 'No phone or tablet is signed in to Home Assistant as ' + cur.name + '.'
      : 'No phone or tablet is linked to ' + cur.name + ' (Settings > People > ' + cur.name + ' > Track device).';
  }
}
if (!customElements.get('person-picker-card')) customElements.define('person-picker-card', PersonPickerCard);

// card: any Home Assistant card, shown for the chosen device of the chosen person. In its entities list, "person" means that
// device's tracker (the person when the device has no tracker) and any other word is a sensor name on that device, such as
// battery_level. "*" means every other sensor switched on on it, apart from those in exclude. People in listed_only get the named
// sensors but not "*". {person} in a title or heading becomes the person's name, {device} the device: kind plus registered name,
// such as 'phone Pixel 8'.
// each_device: true with cards: [...] instead of card: the cards for the chosen device, left out as a whole when it shows nothing.
// An entities card with nothing to show is left out, and so is a heading with nothing under it.
class PersonCard extends HTMLElement {
  setConfig(c) {
    if (c?.each_device ? !Array.isArray(c.cards) : !c?.card) throw new Error(c?.each_device ? 'cards is required' : 'card is required');
    this._c = c;
  }
  getCardSize() { return this._el?.getCardSize?.() || 3; }
  getGridOptions() { return { columns: 'full', rows: 'auto' }; }
  connectedCallback() { this._on = () => this._update(); window.addEventListener('jh-person-changed', this._on); }
  disconnectedCallback() { window.removeEventListener('jh-person-changed', this._on); }
  set hass(h) { this._h = h; jhLoad(h); if (this._el) this._el.hass = h; this._update(); }
  _one(src, p, dev, mine, words) {
    let text = JSON.stringify(src);
    Object.entries(words).forEach(([k, v]) => { text = text.split('{' + k + '}').join(JSON.stringify(v).slice(1, -1)); });
    const out = JSON.parse(text);
    if (src.entities) {
      const known = [...src.entities, ...(this._c.exclude || [])], held = (this._c.listed_only || []).includes(p.id || p.key);
      // review round 7: the language-free key too. Review round 8: a name matches the entity_id OR the app's key, so a key that
      // differs from the English entity_id (dnd_sensor, wifi_signal_strength) still finds its place
      const kk = e => (dev && dev.keys && dev.keys[e]) || '';
      const hit = (e, k) => e.endsWith('_' + k) || kk(e) === k || kk(e).endsWith('_' + k);
      const rest = held ? [] : mine.filter(e => !/^(device_tracker|notify)\./.test(e) && !known.some(k => hit(e, k))).sort();
      const me = dev?.tracker && this._h.states[dev.tracker] ? [dev.tracker] : p.id ? [p.id] : [];
      out.entities = [...new Set(src.entities.flatMap(k => k === 'person' ? me : k === '*' ? rest : mine.filter(e => hit(e, k))))];
      if (JH_NAMED.includes(src.type)) out.entities = out.entities.map(e => { const name = jhShort(this._h, e, dev); return name ? { entity: e, name } : e; });
    }
    return out;
  }
  _resolve(p) {
    const d = jhChosenDevice(this._h, p), words = { person: p.name, device: d ? d.label : '' };
    if (!this._c.each_device) return this._one(this._c.card, p, d, d ? d.ents : [], words);
    const own = d ? this._c.cards.map(c => this._one(c, p, d, d.ents, words)).filter(c => !c.entities || c.entities.length) : [];
    return { type: 'vertical-stack', cards: own.some(c => c.type !== 'heading') ? own : [] };
  }
  async _update() {
    const h = this._h, p = h && jhChosen(h);
    if (!p) return;
    const cfg = this._resolve(p), sig = JSON.stringify(cfg);
    if (sig === this._sig) return;
    this._sig = sig;
    const empty = this._c.each_device ? !cfg.cards.length : this._c.card.entities && !cfg.entities.length;
    this.style.display = empty ? 'none' : 'block';
    this.innerHTML = ''; this._el = null;
    if (empty) return;
    const helpers = await window.loadCardHelpers();
    if (sig !== this._sig) return;
    const el = helpers.createCardElement(cfg);
    el.hass = this._h; this._el = el; this.innerHTML = ''; this.appendChild(el);
  }
}
if (!customElements.get('person-card')) customElements.define('person-card', PersonCard);
