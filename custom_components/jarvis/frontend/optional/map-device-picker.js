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
// Clean Jarvis optional Map page (owner's request, 2026-11-15: the device selection right after the person): on Home Assistant's own
// Map page, picking a person in the People list shows that person's phones and tablets right under their name, as chips. A chip shows
// that device on the map (its own tracker, focused like a person). The devices are the apps signed in to Home Assistant as that
// person (JARVIS's read-only jarvis/user_devices: an admin gets everyone, anybody else only themselves), with the same
// kind rule and labels as the People page, phones first. Nothing has to be linked to the person, so a tablet never counts for home/away.
// The chosen device per person stays chosen (jhPref key jh-map-device, per Home Assistant user, see jh-pref.js): the next time that
// person is picked, the map goes straight to it. "Presence" goes back to the person and is remembered the same way.
// Home Assistant's page, overview and tabs are not replaced: this only adds the chips, the device's marker while it is chosen, the
// person > device names, and draws a person whose chip is a device as that device only (with the owner's photo). If Home Assistant renames what this hooks into, it does nothing and the page stays as it was.
(() => {
  if (window.__jhMapDevices) return;
  window.__jhMapDevices = true;
  const PREF_KEY = "jh-map-device", PREF_URL = new URL("jh-pref.js", import.meta.url).href;
  // pref: the choice per person; mine: what was picked on this page (it wins over a saved value that comes in late, review
  // 2026-11-15); based: the saved value has come in, so a save cannot wipe the other people's choices. raw/sig: the last
  // jarvis/user_devices answer and what it was built with (trackers that have a state, person names).
  const S = { people: null, owner: {}, reg: undefined, user: undefined, busy: false, pref: {}, mine: {}, based: false, P: null, prefAsked: false,
    raw: null, sig: "", cards: new Set(), views: new Set() };
  const hassNow = () => { const ha = document.querySelector("home-assistant"); return ha && ha.hass; };

  // Same rule as jhKind / jhName in person-page-card.js
  const kindOf = d => {
    // review round 7: each sensor with its language-free key from Home Assistant
    const model = d.model || "", maker = d.manufacturer || "", all = (d.entities || []).map(e => (d.keys && d.keys[e]) ? e + " _" + d.keys[e] : e);
    if (/^iphone/i.test(model)) return "phone";
    if (/^ipad/i.test(model)) return "tablet";
    if (all.some(e => /_(phone_state|sim_\d+|mobile_data)$/.test(e))) return "phone";
    if (/watch|wear ?os|^sm-r\d/i.test(model + " " + maker) || all.some(e => /_(on_body_sensor|wet_mode)$/.test(e))) return "watch";
    return "tablet";
  };
  const devName = d => (d.model && d.model !== d.name ? d.name + " (" + d.model + ")" : d.name);
  const cap = s => s[0].toUpperCase() + s.slice(1);
  // A tracker that Home Assistant can put on the map: a state with a latitude and longitude. A chosen device without one (a tablet
  // in "unknown", an app logged out) stands back for the person: their own marker and Presence route; the chip stays lit.
  const placed = st => !!(st && st.attributes && typeof st.attributes.latitude === "number" && typeof st.attributes.longitude === "number");

  const refresh = () => {
    window.dispatchEvent(new Event("jh-map-device"));   // the Paths card's routes follow the choice
    S.cards.forEach(c => { if (!c.isConnected) { S.cards.delete(c); return; } c._mapEntities = c._getMapEntities(); c.requestUpdate(); });
    S.views.forEach(v => { if (!v.isConnected) { S.views.delete(v); return; } v.requestUpdate(); });
  };
  // person entity -> { name, devices: [{ id, tracker, kind, label }] }; tracker -> { person, dev }
  const build = (h, list) => {
    const order = { phone: 0, tablet: 1, watch: 2 }, people = {}, owner = {};
    (list || []).forEach(p => {
      if (!p.person) return;   // a login without a person has no row in the People list
      const devices = (p.devices || []).filter(d => d.tracker && h.states[d.tracker]).map(d => {
        const dev = { id: d.device_id, tracker: d.tracker, name: d.name || d.device_id, model: d.model || "", kind: kindOf(d) };
        dev.label = dev.kind + " " + devName(dev);
        return dev;
      }).sort((a, b) => order[a.kind] - order[b.kind] || a.name.localeCompare(b.name));
      const name = (h.states[p.person] && h.states[p.person].attributes.friendly_name) || p.name || p.person;
      people[p.person] = { name, devices };
      devices.forEach(dev => { owner[dev.tracker] = { person: p.person, dev, name: name + " - " + dev.label }; });
    });
    S.people = people; S.owner = owner; S.raw = list || []; S.sig = sigOf(h, S.raw);
  };
  // What build() depends on besides the answer: which of its trackers have a state, and the person names (a rename does not change
  // the entity registry, so it would not ask again)
  const sigOf = (h, list) => (list || []).map(p => (p.person ? p.person + "=" + ((h.states[p.person] && h.states[p.person].attributes.friendly_name) || "") + ":" +
    (p.devices || []).filter(d => d.tracker && h.states[d.tracker]).map(d => d.tracker).join("+") : "")).join("|");
  // Asked again when the entity registry (an app added or removed) or the login changes
  const load = h => {
    if (!h || !h.callWS) return;
    if (!S.prefAsked) {
      S.prefAsked = true;
      (window.jhPref ? Promise.resolve() : import(PREF_URL)).then(() => {
        const P = S.P = window.jhPref;
        if (!P) return;
        const now = hassNow() || h;
        const quick = P.peek(now, PREF_KEY);
        if (quick && typeof quick === "object") S.pref = { ...quick, ...S.mine };
        P.on(now, PREF_KEY, v => { if (v && typeof v === "object") { S.pref = S.based ? { ...v } : { ...v, ...S.mine }; refresh(); } });
        // The saved value as read, with what was picked meanwhile on top (also a pick made before jh-pref.js had loaded): saved then
        return P.saved(now, PREF_KEY).then(v => {
          S.based = true;
          S.pref = { ...(v && typeof v === "object" ? v : S.pref), ...S.mine };
          if (Object.keys(S.mine).length) P.set(hassNow() || now, PREF_KEY, S.pref);
        });
      }).catch(() => { /* no jh-pref.js: the choice lasts this visit */ }).then(refresh);
    }
    if (S.busy) return;
    if (S.people && S.reg === h.entities && S.user === (h.user && h.user.id)) {
      // a tracker got its state, or a person was renamed: built again from the last answer, nothing is asked
      if (S.raw && sigOf(h, S.raw) !== S.sig) { build(h, S.raw); refresh(); }
      return;
    }
    S.busy = true; S.reg = h.entities; S.user = h.user && h.user.id;
    Promise.resolve().then(() => h.callWS({ type: "jarvis/user_devices" })).then(r => build(h, r && r.people), e => {
      if (!S.people || (e && e.code === "unknown_command")) build(h, []);   // an older JARVIS without the command: no chips; other errors keep the last list
    }).then(() => { S.busy = false; refresh(); });
  };
  // Saved once the saved value has come in (merged with it then, see load); before that it is kept in S.mine
  const remember = (person, value) => {
    S.mine = { ...S.mine, [person]: value };
    S.pref = { ...S.pref, [person]: value };
    window.dispatchEvent(new Event("jh-map-device"));
    const h = hassNow();
    if (S.P && S.based && h) S.P.set(h, PREF_KEY, S.pref);
  };
  // The picture of the person who owns a tracker (person.* entity_picture), or nothing
  const photoOf = (states, tracker) => {
    const own = S.owner[tracker], p = own && states && states[own.person];
    return (p && p.attributes && p.attributes.entity_picture) || "";
  };
  const deviceOf = person => {
    const p = S.people && S.people[person], want = S.pref[person];
    return p && want && want !== "person" ? p.devices.find(d => d.id === want) || null : null;
  };

  // For the Paths card: the tracker whose route stands for a person (their chosen device), or null for the person's own history
  window.jhMapDevices = {
    source(person) { const d = deviceOf(person), h = hassNow(); return d && h && placed(h.states[d.tracker]) ? d.tracker : null; },
    label(person) { const d = deviceOf(person); return d ? d.label : ""; },
  };

  customElements.whenDefined("hui-map-card").then(() => {
    const proto = customElements.get("hui-map-card").prototype;
    if (proto._jhMapDevices || typeof proto._getMapEntities !== "function" || typeof proto.willUpdate !== "function") return;
    proto._jhMapDevices = true;
    const getEntities = proto._getMapEntities, willUpdate = proto.willUpdate, onSelect = proto._handleOverviewSelect;
    // A person's device is named "Alex - tablet Galaxy Tab"; a chosen device that the page leaves out (a person's own phone) is added
    // while it is chosen.
    proto._getMapEntities = function (...args) {
      const list = getEntities.apply(this, args);
      if (!Array.isArray(list) || this.layout !== "panel") return list;
      // the owner's photo when the person has one (see ha-map below), else the device icon: the full name is in the panel
      const look = id => (photoOf(this._states, id) ? {} : { label_mode: "icon" });
      const out = list.map(e => (S.owner[e.entity_id] && !e.name ? { ...e, name: S.owner[e.entity_id].name, ...look(e.entity_id) } : e));
      return out;
    };
    proto.willUpdate = function (changed, ...rest) {
      if (this.layout === "panel") {
        S.cards.add(this); load(hassNow());
        if (changed && changed.has && changed.has("_overviewSelected") && this._mapEntities) this._mapEntities = this._getMapEntities();
      }
      const r = willUpdate.call(this, changed, ...rest);
      return r;
    };
    // Picking a person whose device was chosen before goes straight to that device
    if (typeof onSelect === "function") {
      proto._handleOverviewSelect = function (ev, ...rest) {
        const id = ev && ev.detail && ev.detail.entityId, dev = id && String(id).startsWith("person.") ? deviceOf(id) : null;
        if (dev && typeof this._select === "function" && this._states && placed(this._states[dev.tracker])) { this._select(dev.tracker); return undefined; }
        return onSelect.call(this, ev, ...rest);
      };
    }
    // Fix 2026-11-15 (picking one person zoomed in, picking another did not): Home Assistant's own focus only zooms in as far as it
    // takes to clear the nearest other marker (hui-map-card _focusZoom). Someone at home is next to the Home zone and their tablet,
    // so the map zoomed in; someone away from every zone and device has nothing near, so the map only slid over at the zoom it had.
    // Now every pick (a person in the list, a chip, a marker) centres on what is shown for that person, their chosen device or else
    // their own position, at street level (FOCUS_ZOOM, or closer when Home Assistant needs that to clear a neighbour or the map is
    // already closer), every time it is picked. Nothing else moves the map: after a pick, panning and zooming by hand stay as they
    // are (only Home Assistant's own re-centring when the people panel changes size remains, at the zoom of that moment). Nothing
    // to show: the view stays and a short note says so. Same for every person and every map style; who is listed is unchanged.
    const focus = proto._focusEntity, resize = proto._handleOverviewResize;
    if (typeof focus === "function") {
      if (typeof resize === "function") {
        proto._handleOverviewResize = function (...args) { this._jhResizing = true; try { return resize.apply(this, args); } finally { this._jhResizing = false; } };
      }
      proto._focusEntity = function (id, ...rest) {
        if (this.layout !== "panel") return focus.call(this, id, ...rest);
        const now = Date.now();
        // a pick, or the panel settling right after one (its new size refits while the zoom is still moving in)
        if (this._jhResizing && now >= (this._jhPickUntil || 0)) return focus.call(this, id, ...rest);
        if (!this._jhResizing) this._jhPickUntil = now + 1500;
        try { if (frame(this, id)) return undefined; } catch (e) { /* Home Assistant changed its map: its own focus below */ }
        return focus.call(this, id, ...rest);
      };
    }
  });
  const FOCUS_ZOOM = 16;
  const centerOf = (card, id) => {
    if (typeof card._entityCenter === "function") return card._entityCenter(id);
    const st = card._states && card._states[id];
    return placed(st) ? [st.attributes.latitude, st.attributes.longitude] : undefined;
  };
  const nameOf = (card, id) => {
    const own = S.owner[id], st = card._states && card._states[own ? own.person : id];
    return (st && st.attributes && st.attributes.friendly_name) || (own && S.people && S.people[own.person] && S.people[own.person].name) || String(id).split(".").pop();
  };
  // true when handled here: framed, or nothing to frame and the note shown
  const frame = (card, id) => {
    const map = card._map;
    if (!map || typeof map.fitBounds !== "function") return false;
    const center = centerOf(card, id);
    if (!center) { note(card, "No location for " + nameOf(card, id)); return true; }
    note(card, "");
    const view = typeof map.getView === "function" ? map.getView() : undefined, cur = view && view.zoom;
    let zoom = FOCUS_ZOOM;
    if (typeof cur === "number") {
      zoom = Math.max(zoom, cur);
      if (typeof card._focusZoom === "function") { const z = card._focusZoom(center, cur); if (typeof z === "number" && isFinite(z)) zoom = Math.max(zoom, z); }
    }
    const inView = typeof map.containsLocation === "function" ? map.containsLocation(center) : true;
    map.fitBounds([center], { pad: 0.2, zoom, padding: typeof card._overviewPadding === "function" ? card._overviewPadding() : undefined, fly: !inView });
    return true;
  };
  // "No location for <name>": a small pill at the top of the map for a few seconds; the view is left as it was
  const NOTE_CSS = "position:absolute;left:50%;top:12px;transform:translateX(-50%);z-index:5;pointer-events:none;white-space:nowrap;" +
    "font:500 13px/1.6 var(--ha-font-family-body, Roboto, sans-serif);color:#fff;background:rgba(20,20,20,.82);padding:2px 12px;" +
    "border-radius:12px;box-shadow:0 1px 4px rgba(0,0,0,.45)";
  const note = (card, text) => {
    const root = card.shadowRoot;
    if (!root) return;
    let n = root.querySelector(".jh-map-note");
    clearTimeout(card._jhNoteWait);
    if (!text) { if (n) n.remove(); return; }
    if (!n) { n = document.createElement("div"); n.className = "jh-map-note"; n.setAttribute("role", "status"); n.style.cssText = NOTE_CSS; root.append(n); }
    n.textContent = text;
    card._jhNoteWait = setTimeout(() => { if (n.isConnected) n.remove(); }, 4000);
  };

  // A person's device on the map wears the owner's photo, like the person's own marker (2026-11-15). Only the map of the Map page
  // and Paths page sees it: while that map draws, its own copy of the states has the photo on the device. Home Assistant's states are
  // not changed.
  // The chosen device's name under its marker ("Tablet Galaxy Tab", 2026-11-15); none on Presence. A small dark pill that moves
  // with the marker and lets clicks through. Home Assistant's map card can only put text inside a marker (label_mode), not under it.
  const LABEL_CSS = "position:absolute;left:50%;top:100%;transform:translate(-50%,2px);white-space:nowrap;pointer-events:none;" +
    "font:500 11px/1.5 var(--ha-font-family-body, Roboto, sans-serif);color:#fff;background:rgba(20,20,20,.78);padding:0 7px;" +
    "border-radius:9px;box-shadow:0 1px 3px rgba(0,0,0,.45);z-index:1";
  const labelOne = (m, text) => {
    const root = m && m.shadowRoot;
    if (!root) return false;
    let tag = root.querySelector(".jh-dev-label");
    if (!text) { if (tag) tag.remove(); return true; }
    if (!tag) {
      tag = document.createElement("div"); tag.className = "jh-dev-label"; tag.style.cssText = LABEL_CSS;
      const box = root.querySelector(".marker") || root;
      if (box !== root && getComputedStyle(box).position === "static") box.style.position = "relative";
      box.append(tag);
    }
    if (tag.textContent !== text) tag.textContent = text;
    return true;
  };
  // the markers and the small avatars Home Assistant draws for markers grouped in a zone
  const labelMarkers = (map, labels) => [map._entityMarkers, map._clusterAvatars].forEach(ms => {
    if (!ms || typeof ms.forEach !== "function") return;
    ms.forEach((m, id) => {
      if (!m || typeof m !== "object" || !("shadowRoot" in m)) return;
      const text = labels.get(id) || "";
      if (!labelOne(m, text) || text) Promise.resolve(m.updateComplete).then(() => labelOne(m, labels.get(id) || ""));   // after it renders
    });
  });

  customElements.whenDefined("ha-map").then(() => {
    const proto = customElements.get("ha-map").prototype, draw = proto._drawEntities;
    if (proto._jhMapPhotos || typeof draw !== "function") return;
    proto._jhMapPhotos = true;
    proto._drawEntities = function (...args) {
      const host = this.getRootNode && this.getRootNode().host, states = this._states;
      const ours = host && host.localName === "hui-map-card" && host.layout === "panel" && states && Array.isArray(this.entities);
      if (!ours) return draw.apply(this, args);
      // "Only the chosen device" (2026-11-15): a person whose chip is a phone or tablet is drawn as that device only, in the
      // person's place (same size, colour and selection); on Presence the person is drawn as before. Other people are untouched.
      const idOf = e => (typeof e === "string" ? e : e.entity_id);
      const have = new Set(this.entities.map(idOf)), labels = new Map();
      let swapped = false;
      const list = [];
      this.entities.forEach(e => {
        const id = idOf(e), shown = host._overviewSelected, mine = shown && S.owner[shown] && S.owner[shown].person === id ? S.owner[shown].dev : null;
        const d = String(id).startsWith("person.") ? mine || deviceOf(id) : null;
        if (!d || !placed(states[d.tracker])) { list.push(e); return; }
        swapped = true; labels.set(d.tracker, cap(d.label));
        if (have.has(d.tracker)) {   // the device is drawn already: keep it, and keep the person's selection on it
          const i = this.entities.findIndex(x => idOf(x) === d.tracker), x = this.entities[i];
          if (typeof e !== "string" && e.selected && typeof x !== "string" && !x.selected) this._jhSel = d.tracker;
          return;
        }
        const own = S.owner[d.tracker];
        list.push(typeof e === "string" ? d.tracker : { ...e, entity_id: d.tracker, name: own ? own.name : e.name, selected: !!e.selected || shown === d.tracker });
      });
      const entities = swapped ? list.map(x => (this._jhSel && typeof x !== "string" && idOf(x) === this._jhSel ? { ...x, selected: true } : x)) : this.entities;
      this._jhSel = null;
      const ids = entities.map(idOf).filter(id => states[id] && !states[id].attributes.entity_picture && photoOf(states, id));
      if (!swapped && !ids.length) { try { return draw.apply(this, args); } finally { labelMarkers(this, labels); } }
      const copy = { ...states };
      ids.forEach(id => { copy[id] = { ...states[id], attributes: { ...states[id].attributes, entity_picture: photoOf(states, id) } }; });
      // this draw only, on this map only; no update is triggered and Home Assistant's states are not changed
      Object.defineProperty(this, "_states", { value: copy, configurable: true, writable: true });
      if (swapped) Object.defineProperty(this, "entities", { value: entities, configurable: true, writable: true });
      try { return draw.apply(this, args); } finally { delete this._states; if (swapped) delete this.entities; labelMarkers(this, labels); }
    };
  });

  customElements.whenDefined("hui-map-overview").then(() => {
    const proto = customElements.get("hui-map-overview").prototype;
    if (proto._jhMapDeviceChips || typeof proto.updated !== "function") return;
    proto._jhMapDeviceChips = true;
    const updated = proto.updated, entityName = proto._entityName;
    if (typeof entityName === "function") {
      proto._entityName = function (st, ...rest) { const own = st && S.owner[st.entity_id]; return own ? own.name : entityName.call(this, st, ...rest); };
    }
    proto.updated = function (...args) {
      const r = updated.apply(this, args);
      S.views.add(this);
      try { this._jhChips(); } catch (e) { /* HA changed its overview: no chips */ }
      return r;
    };
    // The chips under the name in the person's (or their device's) details
    proto._jhChips = function () {
      const root = this.shadowRoot, header = root && root.querySelector(".detail-header");
      const old = root && root.querySelector(".jh-devs");
      const sel = this.selected, person = sel && (String(sel).startsWith("person.") ? sel : S.owner[sel] && S.owner[sel].person);
      const p = person && S.people && S.people[person];
      if (!header || !p || !p.devices.length) { if (old) old.remove(); return; }
      // the lit chip: the device shown, or (with the person open) the remembered one, which is also where the Paths route comes from
      const mine = sel === person ? deviceOf(person) : p.devices.find(d => d.tracker === sel), active = mine ? mine.tracker : person;
      const h = hassNow(), here = d => !h || placed(h.states[d.tracker]);
      const sig = person + "|" + active + "|" + p.devices.map(d => d.id + d.label + here(d)).join();
      if (old && old.dataset.sig === sig && old.previousElementSibling === header) return;
      if (old) old.remove();
      const box = document.createElement("div");
      box.className = "jh-devs"; box.dataset.sig = sig;
      box.setAttribute("role", "group"); box.setAttribute("aria-label", "Phone or tablet");
      box.style.cssText = "display:flex;flex-wrap:wrap;gap:6px;padding:4px 16px 10px";
      const card = this.getRootNode() && this.getRootNode().host;
      const chip = (text, on, pick, note) => {
        const b = document.createElement("button");
        b.type = "button"; b.textContent = text; b.setAttribute("aria-pressed", on ? "true" : "false");
        if (note) { b.title = note; b.className = "jh-noloc"; b.append(Object.assign(document.createElement("span"), { textContent: " \u00b7 no location" })); }
        b.style.cssText = "font:inherit;font-size:13px;min-height:32px;padding:4px 12px;border-radius:16px;cursor:pointer;border:1px solid " +
          (on ? "var(--primary-color)" : "var(--divider-color)") + ";background:" + (on ? "var(--primary-color)" : "transparent") +
          ";color:" + (on ? "var(--text-primary-color, #fff)" : "var(--primary-text-color)");
        b.onclick = e => { e.stopPropagation(); try { window.jhHapticLoad(hassNow()); window.jhHaptic && window.jhHaptic("selection", b); } catch (x) {} pick(); };   // jh-haptic (step 156)
        return b;
      };
      box.append(chip("Presence", active === person, () => { remember(person, "person"); if (card && card._select) card._select(person); }));
      // a device without a location stays chosen, but the map shows the person (their own marker) until it reports one
      p.devices.forEach(d => box.append(chip(cap(d.label), active === d.tracker, () => { remember(person, d.id); if (card && card._select) card._select(here(d) ? d.tracker : person); },
        here(d) ? "" : cap(d.label) + " has no location now: the map shows " + p.name + " instead")));
      header.after(box);
    };
  });
})();
