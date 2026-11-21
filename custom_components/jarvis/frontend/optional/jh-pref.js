// Clean Jarvis: jhPref - a choice on a page stays as the user left it (every selection stays the default until the user changes
// it). Kept per Home Assistant user in Home Assistant's own frontend user data (frontend/set_user_data, so it follows the login
// to every browser, phone and tablet), with localStorage as the instant copy for this browser (per user, so two logins on one tablet
// keep their own). Served by JARVIS next to the optional cards (frontend/optional); it needs no dashboard resource: a card loads it
// itself with  import(new URL("jh-pref.js", import.meta.url).href)  so it always comes from the same place as the card.
// API (window.jhPref):
//   peek(hass, key)          the value from this browser's copy, right away (undefined when none)
//   get(hass, key)           Promise of the saved value: the user data, else this browser's copy, else undefined. Asked once per page load.
//                            A late answer never undoes a choice (review 2026-11-15): when the value was set on this page or in another
//                            tab while the question was on its way, get gives that newer value, and the old answer is not copied over it.
//   saved(hass, key)         Promise of the user data exactly as it was read, even after a set (undefined when nothing is saved, this
//                            browser's copy when it cannot be read). For a card that keeps several things under one key and merges.
//   first(hass, key, ms)     get, but gives P.TIMEOUT when no answer came within ms (default 3000). A timeout is not "nothing saved":
//                            never save or move an older value on it; wait for get / saved for the real answer.
//   changed(hass, key)       how many times the value was set on this page or arrived from another tab (to see if it was touched)
//   set(hass, key, value)    saves it in both and tells the other cards in this browser; returns a Promise (never rejects)
//   on(hass, key, fn)        fn(value) when the value is set by another card or another tab of this browser; returns an off() function
// Keys are free text; use one per choice, for example "jh-map-selection" (the Map and Paths pages: person, device per person, map style).
(() => {
  if (window.jhPref) return;
  const uid = h => (h && h.user && h.user.id) || "anon";
  const slot = (h, key) => key + "@" + uid(h);
  const read = (h, key) => {
    try { const v = localStorage.getItem(slot(h, key)); return v == null ? undefined : JSON.parse(v); } catch (e) { return undefined; }
  };
  const write = (h, key, value) => {
    try { localStorage.setItem(slot(h, key), JSON.stringify(value)); } catch (e) { /* private window or full: this visit only */ }
  };
  const asked = new Map();   // slot -> Promise of the value (the newest: a set replaces it)
  const raw = new Map();     // slot -> Promise of the user data as read, kept after a set
  const gen = new Map();     // slot -> number of sets on this page and values from other tabs
  const stamp = id => gen.get(id) || 0;
  const bump = id => gen.set(id, stamp(id) + 1);
  const TIMEOUT = Symbol("jhPref timeout");
  const ask = (h, key) => {
    const id = slot(h, key);
    if (!raw.has(id)) {
      const at = stamp(id);
      const q = h && h.callWS ? Promise.resolve().then(() => h.callWS({ type: "frontend/get_user_data", key })) : Promise.reject(new Error("no connection"));
      raw.set(id, q.then(r => {
        const v = r ? r.value : undefined;
        if (v === undefined || v === null) return stamp(id) === at ? read(h, key) : undefined;
        if (stamp(id) === at) write(h, key, v);   // a newer value was set meanwhile: the browser's copy keeps that one
        return v;
      }, () => read(h, key)));
      // what get gives: the answer, unless the value was set while it was on its way
      asked.set(id, raw.get(id).then(v => (stamp(id) === at ? v : read(h, key))));
    }
  };
  window.jhPref = {
    TIMEOUT,
    peek: read,
    get(h, key) { ask(h, key); return asked.get(slot(h, key)); },
    saved(h, key) { ask(h, key); return raw.get(slot(h, key)); },
    first(h, key, ms) {
      const p = this.get(h, key);
      return Promise.race([p, new Promise(r => setTimeout(() => r(TIMEOUT), ms === undefined ? 3000 : ms))]);
    },
    changed(h, key) { return stamp(slot(h, key)); },
    set(h, key, value) {
      const id = slot(h, key);
      bump(id); write(h, key, value);
      asked.set(id, Promise.resolve(value));
      window.dispatchEvent(new CustomEvent("jh-pref", { detail: { key, value, user: uid(h) } }));
      if (!h || !h.callWS) return Promise.resolve();
      return Promise.resolve().then(() => h.callWS({ type: "frontend/set_user_data", key, value })).catch(() => {});
    },
    on(h, key, fn) {
      const id = slot(h, key);
      const here = e => { if (e.detail && e.detail.key === key && e.detail.user === uid(h)) fn(e.detail.value); };
      const tab = e => {
        if (e.key !== id || e.newValue == null) return;
        try { const v = JSON.parse(e.newValue); bump(id); asked.set(id, Promise.resolve(v)); fn(v); } catch (x) { /* not ours */ }
      };
      window.addEventListener("jh-pref", here); window.addEventListener("storage", tab);
      return () => { window.removeEventListener("jh-pref", here); window.removeEventListener("storage", tab); };
    },
  };
})();
