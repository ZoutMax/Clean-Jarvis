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
// Clean Jarvis optional Map page: custom:path-calendar-card - pick a day, see where people went. Reads Home Assistant's own history,
// so it needs no background task. People are read live: an administrator sees every person.*, anybody else only their own person
// (the person linked to their login), also on the map itself. How many days of history are kept is an admin setting of JARVIS
// (jh_history_days, read with jarvis/history_days, saved with jarvis/update_config); JARVIS applies it every night while the Map page
// is switched on in Settings > Optional pages. Nobody else sees or can change it.
// 2026-11-15: a person's route follows the device chosen for them with the chips of the map (map-device-picker.js, remembered per
// user): that tracker's own history, or the person's when "Presence" (or nothing) is chosen. Without that module: the person, as before.
const routeOf = p => (window.jhMapDevices && window.jhMapDevices.source(p)) || p;
const COLORS = ["#00e5ff", "#ff5fa2", "#ffd166", "#7cff6b", "#b388ff", "#ff8a50", "#4dd0e1", "#f06292"];
const el = (tag, props = {}, ...kids) => { const e = Object.assign(document.createElement(tag), props); kids.forEach(k => e.append(k)); return e; };
const pad = n => String(n).padStart(2, "0");
const ymd = d => d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate());
// Map style switch (2026-11-12): Home Assistant's own map, or Google's streets / satellite tiles on top of it. Google's tiles are
// not an official service: if they stop loading, "Normal" is still Home Assistant's map. The choice is remembered per Home Assistant
// user (2026-11-15; jhPref key jh-map-style, see jh-pref.js: it follows the login to every browser and device), restored before the map
// is made, and the map stays hidden until Google's tiles are on it, so it never flashes Normal first. The choice an older build kept
// per browser (localStorage jh-paths-map-style) is taken over once. map-device-picker.js keeps its own key (jh-map-device).
const STYLES = [["normal", "Normal"], ["google", "Google"], ["satellite", "Satellite"]];
const GOOGLE = { google: "m", satellite: "y" };   // y = satellite with street names
const STYLE_PREF = "jh-map-style", STYLE_KEY = "jh-paths-map-style";
const okStyle = v => (GOOGLE[v] ? v : "normal");
const isStyle = v => !!GOOGLE[v] || v === "normal";
const readOld = () => { try { return localStorage.getItem(STYLE_KEY); } catch (e) { return null; } };
const PREF_URL = new URL("jh-pref.js", import.meta.url).href;
const loadPref = () => window.jhPref ? Promise.resolve(window.jhPref) : import(PREF_URL).then(() => window.jhPref || null, () => null);
const isAdmin = h => !h.user || !!h.user.is_admin;

class PathCalendarCard extends HTMLElement {
  setConfig(c) { this._c = c; }
  getCardSize() { return 12; }
  getGridOptions() { return { columns: "full", rows: "auto" }; }
  set hass(h) {
    const first = !this._h; this._h = h;
    if (first) { this.attachShadow({ mode: "open" }); try { window.jhHapticLoad(h); window.jhHapticWire(this.shadowRoot); } catch (e) {}   // jh-haptic (step 156)
                this._day = ymd(new Date()); this._askStyle(); this._askKeep(); this._load(); return; }
    if ((h.user && h.user.id) !== this._keepUser) this._askKeep();   // another login on this page
    if (this._hm) {
      const cfg = this._mapConfig(), key = cfg.entities.join();
      if (key !== this._mapKey) { this._mapKey = key; this._hm.setConfig(cfg); }   // new person or zone: the real card follows
      this._hm.hass = h;
    }
    const who = this._people().map(routeOf).join();
    if (who !== this._who) { this._who = who; this._load(); }   // a new user: load them too
  }
  connectedCallback() {
    // a chip picked: redraw at once; also when map-device-picker.js arrived after the routes were read (the legend then gets its "– Presence")
    this._onDev = () => { if (this._h && (this._people().map(routeOf).join() !== this._who || this._picker !== !!window.jhMapDevices)) this._load(); };
    window.addEventListener("jh-map-device", this._onDev);
    this._today = ymd(new Date());
    // back on the page after Home Assistant took the card out (another view or dashboard): follow the style again, and pick up a
    // style chosen elsewhere meanwhile (review 2026-11-15)
    if (this._P && this._h) {
      this._listenStyle();
      const v = this._P.peek(this._h, STYLE_PREF);
      if (isStyle(v) && v !== this._style) { this._style = v; if (this._ctl) this._sync(); this._applyStyle(); }
    }
    this._timer = setInterval(() => {
      const now = ymd(new Date());
      if (now !== this._today) { if (this._day === this._today) { this._day = now; this._tracks = null; } this._today = now; this._load(); }   // past midnight
      else if (this._day === now) this._load();
    }, 60000);
  }
  disconnectedCallback() { clearInterval(this._timer); window.removeEventListener("jh-map-device", this._onDev); if (this._offStyle) { this._offStyle(); this._offStyle = null; } }
  // The user's map style, asked once before the map is made (a saved value that never comes stops waiting after 3 s). The browser's
  // copy is used at once, so the buttons are right from the start. Without a saved style, the older per-browser one is saved for the
  // user and removed from the browser. Without jh-pref.js: that older one.
  // Review 2026-11-15: a style picked on this page (_styleMine) is never undone by the saved value coming in late, a style picked
  // before jh-pref.js had loaded is saved once it has, and a slow answer (over 3 s) is still applied when it comes, but the older
  // per-browser style is only moved over on a real "nothing saved", never on the timeout.
  _askStyle() {
    const h = this._h, P0 = window.jhPref, quick = P0 && P0.peek(h, STYLE_PREF);
    this._style = isStyle(quick) ? quick : okStyle(readOld());
    this._styleReady = loadPref().then(P => {
      if (!P) return;
      this._P = P;
      if (this._styleMine) P.set(h, STYLE_PREF, this._style);   // picked before jh-pref.js had loaded
      const fast = P.peek(h, STYLE_PREF);
      if (isStyle(fast) && !this._styleMine) this._style = fast;
      this._listenStyle();
      const answer = v => {   // the real answer; true when it changed the style
        if (this._styleMine) return false;
        if (isStyle(v)) { const was = this._style; this._style = v; return v !== was; }
        const old = readOld();
        if (old && v === undefined) {
          this._style = okStyle(old); P.set(h, STYLE_PREF, this._style); try { localStorage.removeItem(STYLE_KEY); } catch (e) {}
          return true;
        }
        return false;
      };
      return P.first(h, STYLE_PREF, 3000).then(v => {
        if (v !== P.TIMEOUT) { answer(v); return; }
        P.get(h, STYLE_PREF).then(late => { if (answer(late)) { if (this._ctl) this._sync(); this._applyStyle(); } });   // the map is made meanwhile
      });
    }).catch(() => {}).then(() => { if (this._ctl) this._sync(); this._applyStyle(); });
  }
  // a style picked in another tab of this browser by the same user
  _listenStyle() {
    if (this._offStyle || !this._P) return;
    this._offStyle = this._P.on(this._h, STYLE_PREF, v => { if (isStyle(v) && v !== this._style) { this._style = v; if (this._ctl) this._sync(); this._applyStyle(); } });
  }
  _mapConfig() {
    const zones = Object.keys(this._h.states).filter(e => e.startsWith("zone.")).sort();
    return { type: "map", auto_fit: false, entities: [...zones, ...this._people()] };
  }
  // privacy: an administrator sees everyone, anybody else only the person linked to their own login
  _people() {
    const h = this._h, admin = isAdmin(h), me = h.user && h.user.id;
    return Object.keys(h.states).filter(e => e.startsWith("person.") && (admin || (me && h.states[e].attributes.user_id === me))).sort();
  }
  // The days of history JARVIS keeps, for an administrator only (the command is admin-only; nobody else sees the field)
  _askKeep() {
    const h = this._h; this._keepUser = h.user && h.user.id;
    if (!isAdmin(h) || !h.callWS) { this._keep = null; if (this._ctl) this._sync(); return; }
    const token = this._keepToken = (this._keepToken || 0) + 1;
    Promise.resolve().then(() => h.callWS({ type: "jarvis/history_days" })).then(r => {
      if (token !== this._keepToken) return;
      this._keep = r && typeof r === "object" ? r : null;
      if (this._ctl) this._sync();
    }, () => { if (token === this._keepToken) { this._keep = null; if (this._ctl) this._sync(); } });
  }
  _name(e) { const s = this._h.states[e]; return (s && s.attributes.friendly_name) || e.split(".")[1]; }
  async _load() {
    const token = this._token = (this._token || 0) + 1, ids = this._people(), src = ids.map(routeOf);
    this._picker = !!window.jhMapDevices;
    this._who = src.join();
    const [y, m, d] = this._day.split("-").map(Number), from = new Date(y, m - 1, d), to = new Date(y, m - 1, d + 1);
    this._busy = !this._tracks; this._draw();
    try {
      const r = ids.length ? await this._h.callWS({ type: "history/history_during_period", start_time: from.toISOString(), end_time: to.toISOString(),
        entity_ids: src, minimal_response: false, no_attributes: false, significant_changes_only: false }) : {};
      if (token !== this._token) return;
      this._tracks = ids.map((e, i) => {
        const pts = [];
        (r[src[i]] || []).forEach(x => {
          const a = x.a || {}, lat = a.latitude, lon = a.longitude;
          if (typeof lat !== "number" || typeof lon !== "number") return;
          const l = pts[pts.length - 1];
          if (!l || l[0] !== lat || l[1] !== lon) pts.push([lat, lon, x.lu]);
        });
        // "Alex – Tablet Galaxy Tab", or "Alex – Presence" for the person's own history (only with the chips of the map)
        const dev = window.jhMapDevices && (src[i] !== e ? window.jhMapDevices.label(e) : "presence");
        return { e, src: src[i], name: this._name(e) + (dev ? " – " + dev[0].toUpperCase() + dev.slice(1) : ""), color: COLORS[i % COLORS.length], pts };
      });
      this._err = "";
    } catch (e) {
      if (token !== this._token) return;   // a newer day is already loading
      this._err = "Could not read history: " + ((e && e.message) || JSON.stringify(e)); this._tracks = [];
    }
    this._busy = false; this._draw();
  }
  _shift(n) { const [y, m, d] = this._day.split("-").map(Number); this._set(ymd(new Date(y, m - 1, d + n))); }
  _set(v) { if (!v || v > ymd(new Date())) { this._sync(); return; } this._day = v; this._tracks = null; this._load(); }
  _saveKeep() {
    const keep = this._keep; if (!keep) return;
    const raw = String(this._n.value).trim(), v = raw === "" ? null : Number(raw), { min, max } = keep;
    if (v !== null && !(Number.isInteger(v) && v >= min && v <= max)) { this._keepErr = "Days to keep must be a whole number from " + min + " to " + max + "."; this._sync(); return; }
    this._keepErr = ""; this._keepWant = v; this._sync();   // show the new number until Home Assistant confirms it
    Promise.resolve().then(() => this._h.callWS({ type: "jarvis/update_config", key: "jh_history_days", value: v }))
      .then(() => { this._keep = { ...keep, days: v }; this._keepWant = undefined; this._sync(); },
            e => { this._keepWant = undefined; this._keepErr = "Not saved: " + ((e && e.message) || e); this._sync(); });
  }
  // The real map card of the Map page (bottom sheet, people, zones, details). Only its history is replaced by the chosen day.
  async _ensureMap() {
    if (this._hm || this._hmWait || Date.now() < (this._hmRetry || 0)) return;   // a failed load waits before the next try
    this._hmWait = true;
    try {
      await this._styleReady;                            // the saved style first, so the map does not start on the wrong one
      const helpers = await window.loadCardHelpers();
      const config = this._mapConfig();
      await helpers.createCardElement(config);           // makes Home Assistant load the card's code
      await customElements.whenDefined("hui-map-card");
      const c = document.createElement("hui-map-card");
      c.setConfig(config);
      c.layout = "panel"; c.isPanel = true;
      c._subscribeHistory = () => {};                    // no rolling history: this page supplies the day
      c._getHistoryPaths = () => this._paths || [];
      c.hass = this._h;
      if (GOOGLE[this._style]) this._hide(c);            // Google or Satellite: shown once Google's tiles are on it
      this._mapKey = config.entities.join();
      this._hm = c; this._box.append(c);
    } catch (e) { this._err = "Map could not load: " + ((e && e.message) || e); this._hmRetry = Date.now() + 30000; }
    this._hmWait = false; this._draw();
  }
  _setStyle(v) { this._style = v; this._styleMine = true; if (this._P) this._P.set(this._h, STYLE_PREF, v); this._sync(); this._applyStyle(); }
  // Keeps the new map invisible (its place and size stay) until the chosen Google tiles have drawn; never longer than 5 s.
  _hide(c) {
    c.style.visibility = "hidden";
    clearTimeout(this._showWait); this._showWait = setTimeout(() => this._reveal(), 5000);
  }
  _reveal() { clearTimeout(this._showWait); if (this._hidden()) this._hm.style.visibility = ""; }
  _hidden() { return !!(this._hm && this._hm.style && this._hm.style.visibility); }
  // Google's tiles go under the routes and people, over Home Assistant's own map. Home Assistant's map is MapLibre now (Leaflet
  // before): both are handled. Done again whenever the map redraws its style (theme change) or is made anew.
  _applyStyle(until = Date.now() + 5000) {
    const m = this._hm && this._hm.shadowRoot && this._hm.shadowRoot.querySelector("ha-map");
    const eng = m && m._engine, gl = eng && eng._map && typeof eng._map.getStyle === "function" ? eng._map : null;
    const lf = m && m.leafletMap && m.Leaflet ? m.leafletMap : null;
    clearTimeout(this._styleWait);
    if (!gl && !lf) {   // not drawn yet: asked again for 5 s (often while a new map is still hidden)
      if (Date.now() < until) this._styleWait = setTimeout(() => this._applyStyle(until), this._hidden() ? 50 : 500); else this._reveal();
      return;
    }
    const want = GOOGLE[this._style] || "";
    if (!want) this._reveal();
    if (gl) this._glStyle(gl, eng, want); else this._leafletStyle(lf, m.Leaflet, want);
  }
  _glStyle(map, eng, want) {
    if (this._gMap !== map) {   // a new map: follow its style reloads
      this._gMap = map; this._gHidden = [];
      map.on("styledata", () => { if (this._gMap === map) setTimeout(() => this._glStyle(map, eng, GOOGLE[this._style] || ""), 0); });
    }
    clearTimeout(this._glWait);
    try { this._glApply(map, eng, want); this._glTries = 0; }
    catch (e) { if ((this._glTries = (this._glTries || 0) + 1) < 40) this._glWait = setTimeout(() => this._glStyle(map, eng, GOOGLE[this._style] || ""), 500); }   // style still loading
  }
  _glApply(map, eng, want) {
    const st = map.getStyle();
    const ID = "jh-google", has = !!map.getLayer(ID), custom = (eng._customLayers && eng._customLayers.has) ? eng._customLayers : new Map();
    if (has && want !== this._gKind) { map.removeLayer(ID); map.removeSource(ID); }
    if (want && !map.getLayer(ID)) {
      this._gKind = want;
      map.addSource(ID, { type: "raster", tileSize: 256, maxzoom: 20, attribution: "Map data &copy; Google",
        tiles: ["mt0", "mt1", "mt2", "mt3"].map(s => "https://" + s + ".google.com/vt/lyrs=" + want + "&x={x}&y={y}&z={z}") });
      const before = st.layers.find(l => custom.has(l.id) || l.type === "symbol");   // under the routes and Home Assistant's labels
      map.addLayer({ id: ID, type: "raster", source: ID }, before && before.id);
    }
    if (want && this._hidden()) {   // a hidden new map: shown as soon as Google's tiles have drawn
      const show = () => { if (this._gMap === map) this._reveal(); };
      if (typeof map.once === "function") map.once("idle", show); else show();
    }
    // Google draws its own street names: Home Assistant's are hidden meanwhile and come back with Normal
    const base = st.layers.filter(l => l.type === "symbol" && !custom.has(l.id));
    if (want) base.forEach(l => { if (map.getLayoutProperty(l.id, "visibility") !== "none") { map.setLayoutProperty(l.id, "visibility", "none"); this._gHidden.push(l.id); } });
    else { this._gHidden.forEach(id => { if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", "visible"); }); this._gHidden = []; }
  }
  _leafletStyle(map, L, want) {
    if (this._gLayer && (this._gMap !== map || this._gKind !== want)) { try { this._gMap.removeLayer(this._gLayer); } catch (e) { /* map already gone */ } this._gLayer = null; }
    if (want && !this._gLayer) {
      this._gLayer = L.tileLayer("https://{s}.google.com/vt/lyrs=" + want + "&x={x}&y={y}&z={z}", { subdomains: ["mt0", "mt1", "mt2", "mt3"], maxZoom: 20, zIndex: 10, attribution: "Map data &copy; Google" }).addTo(map);
      this._gMap = map; this._gKind = want;
      if (typeof this._gLayer.once === "function") this._gLayer.once("load", () => this._reveal()); else this._reveal();
    }
  }
  _updateMap(tracks) {
    // which route is which: the colour and where it comes from
    this._legend.replaceChildren(...tracks.map(t => el("span", { className: "lg" }, el("i", { style: "background:" + t.color }), t.name + (t.pts.length ? "" : " (no data)"))));
    const pts = tracks.flatMap(t => t.pts);
    this._note.textContent = !this._hm ? "Loading map..." : pts.length ? "" : this._busy ? "Loading..." : "No location data for this day.";
    this._paths = tracks.filter(t => t.pts.length).map(t => ({ color: t.color, name: t.name, fullDatetime: false, gradualOpacity: 0,
      points: t.pts.map(p => ({ point: [p[0], p[1]], timestamp: new Date(p[2] * 1000) })) }));
    if (!this._hm) { this._ensureMap(); return; }
    this._hm.requestUpdate();
    this._applyStyle();
    // the day and where each route comes from: another device chosen for a person is another route, framed anew (review 2026-11-15)
    const key = this._day + "|" + tracks.map(t => t.e + ">" + (t.src || t.e)).join();
    if (pts.length && key !== this._fit) {
      this._fit = key;
      const lats = pts.map(p => p[0]), lons = pts.map(p => p[1]);
      const fit = n => {
        const m = this._hm.shadowRoot && this._hm.shadowRoot.querySelector("ha-map");
        if (m && m.fitBounds) { try { m.fitBounds([[Math.min(...lats), Math.min(...lons)], [Math.max(...lats), Math.max(...lons)]], { pad: 0.1, zoom: 16, padding: this._hm._overviewPadding ? this._hm._overviewPadding() : m.fitPadding }); return; } catch (e) { /* not drawn yet */ } }
        if (n > 0) setTimeout(() => fit(n - 1), 500);
      };
      setTimeout(() => fit(10), 1500);   // after the people panel has its size
    }
  }
  _draw() {
    const root = this.shadowRoot;
    if (!this._ctl) {   // built once: replacing a control under the mouse would lose the click
      root.append(el("style", { textContent: `
        ha-card{padding:16px;background:transparent;border:none;box-shadow:none;color:var(--primary-text-color);display:flex;flex-direction:column;gap:12px;
          box-sizing:border-box;min-height:calc(100vh - var(--header-height, 56px));min-height:calc(100dvh - var(--header-height, 56px) - env(safe-area-inset-top, 0px))}
        .ctl{display:flex;flex-direction:column;gap:12px}
        .bar{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
        @media(max-width:600px){.dl{display:none}}
        input{font:inherit;font-size:16px;padding:8px 10px;border-radius:10px;border:1px solid var(--divider-color);background:var(--card-background-color);color:var(--primary-text-color);color-scheme:dark}
        input.n{width:76px}
        button{font:inherit;padding:8px 14px;border-radius:10px;border:1px solid var(--divider-color);background:var(--card-background-color);color:var(--primary-text-color);cursor:pointer}
        button:disabled{opacity:.4;cursor:default} button.on{background:var(--primary-color);color:#fff;border-color:var(--primary-color)}
        .sm{opacity:.7;font-size:13px}
        .seg{display:flex;margin-left:auto} .seg button{border-radius:0;margin-left:-1px} .seg button:first-child{border-radius:10px 0 0 10px;margin-left:0} .seg button:last-child{border-radius:0 10px 10px 0}
        @media(max-width:600px){.seg{margin-left:0}}
        .map{position:relative;flex:1 1 auto;min-height:320px;border-radius:12px;overflow:hidden}
        .map hui-map-card{display:block;position:absolute;inset:0}
        .none{position:absolute;left:0;right:0;top:12px;z-index:2;text-align:center;opacity:.8;pointer-events:none}
        .err{color:#ff6b6b}
        .legend{display:flex;flex-wrap:wrap;gap:6px 16px;font-size:14px} .legend:empty{display:none}
        .lg{display:flex;align-items:center;gap:6px} .lg i{width:14px;height:4px;border-radius:2px;display:inline-block}
        /* phone (review 2026-11-15): day controls on one row, style switch and days on the next, so the map (and Home Assistant's
           people sheet over its lower half) keeps the room */
        @media(max-width:600px){
          ha-card{padding:12px;gap:10px} .bar{gap:6px}
          button.day .t{display:none} button.day{min-width:44px;padding:8px 0;font-size:17px;line-height:1.1}
          input[type=date]{flex:1 1 128px;min-width:0}
          .seg{flex:1 1 210px} .seg button{flex:1 1 auto;padding:8px 6px}
          .legend{font-size:13px;gap:4px 12px}
        }` }));
      this._date = el("input", { type: "date" }); this._date.onchange = () => this._set(this._date.value); this._date.onblur = () => this._sync();
      // the words are hidden on a phone (only < and >), still read out and kept as the tooltip
      const prev = el("button", { className: "day", title: "Day before" }, "<", el("span", { className: "t", textContent: " Day before" })); prev.onclick = () => this._shift(-1);
      this._next = el("button", { className: "day", title: "Day after" }, el("span", { className: "t", textContent: "Day after " }), ">"); this._next.onclick = () => this._shift(1);
      prev.setAttribute("aria-label", "Day before"); this._next.setAttribute("aria-label", "Day after");
      this._now = el("button", { textContent: "Today" }); this._now.onclick = () => this._set(ymd(new Date()));
      this._n = el("input", { type: "number", className: "n", step: 1, title: "Days of history to keep (empty: JARVIS removes nothing)", placeholder: "-" });
      this._n.setAttribute("aria-label", "Days of history to keep"); this._n.onchange = () => this._saveKeep(); this._n.onblur = () => this._sync();
      this._dl = el("span", { className: "sm dl", textContent: "days" });
      this._styleBtns = STYLES.map(([v, label]) => { const b = el("button", { textContent: label, title: label + " map" }); b.dataset.style = v; b.onclick = () => this._setStyle(v); return b; });
      const styles = el("div", { className: "seg", title: "Map style" }, ...this._styleBtns);
      this._errBox = el("div", { className: "err" });
      this._legend = el("div", { className: "legend" });
      this._keepNote = el("div", { className: "sm" });
      this._ctl = el("div", { className: "ctl" }, el("div", { className: "bar" }, prev, this._date, this._next, this._now, this._n, this._dl, styles), this._legend, this._keepNote, this._errBox);
      this._note = el("div", { className: "none" });
      this._box = el("div", { className: "map" }, this._note);
      root.append(el("ha-card", {}, this._ctl, this._box));
    }
    this._sync();
    this._updateMap(this._tracks || []);
  }
  _sync() {   // puts the current values into the controls; never touches the field someone is typing in
    const root = this.shadowRoot, h = this._h, today = ymd(new Date());
    const keep = this._keep, show = !!keep && isAdmin(h);   // retention is an admin setting
    if (root.activeElement !== this._date) this._date.value = this._day;
    this._date.max = today;
    this._next.disabled = this._day >= today;
    this._now.className = this._day === today ? "on" : "";
    this._styleBtns.forEach(b => { b.className = b.dataset.style === this._style ? "on" : ""; });
    this._n.hidden = this._dl.hidden = !show;
    if (show) {
      this._n.min = keep.min; this._n.max = keep.max;
      const days = this._keepWant === undefined ? keep.days : this._keepWant;
      if (root.activeElement !== this._n) this._n.value = days == null ? "" : days;
    }
    // Home Assistant's own recorder purge still runs: say so when it keeps fewer days than asked here
    const short = show && keep.days != null && keep.recorder_auto_purge && keep.recorder_keep_days < keep.days;
    this._keepNote.textContent = !show ? "" : !keep.purging ? "History days apply only while the Map page is switched on in JARVIS Settings."
      : short ? "Home Assistant itself keeps only " + keep.recorder_keep_days + " days (recorder purge_keep_days in configuration.yaml)." : "";
    this._keepNote.hidden = !this._keepNote.textContent;
    const err = [this._err, this._keepErr].filter(Boolean).join(" ");
    this._errBox.textContent = err; this._errBox.hidden = !err;
  }
}
if (!customElements.get("path-calendar-card")) customElements.define("path-calendar-card", PathCalendarCard);
