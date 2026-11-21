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
/* Clean Jarvis optional login screen (login_screen.py) for Home Assistant's own login page (/auth/authorize).
   JARVIS adds this script next to Home Assistant's <ha-authorize>, which still does the whole login (OAuth query,
   "Keep me logged in", MFA, errors, the IP ban message, language, Help, the companion app's login view). This script only
   adds things around it: the space background (the downloaded video, photos and three.js planets, or a drawn starfield
   when they are not there), the corner HUD, the counts-only system status (its own switch), the JARVIS core whose J flashes
   red on key presses, the typed greeting and "Identify yourself." once when the page opens, or on the first tap or key
   press when the browser blocks sound (its own switch).
   It never reads, stores or sends what is typed, and loads nothing from other sites. Any failure leaves Home Assistant's
   page as it is: every CSS rule waits for html.jl-skin, which this script sets, and it undoes itself on an error. */
(function () {
  "use strict";
  if (window.__jarvisLoginSkin) return;
  try { window.jhHapticWire(document); } catch (e) {}   // jh-haptic (step 156): taps on the login page
  window.__jarvisLoginSkin = true;

  var me = document.currentScript || document.querySelector("script[data-jarvis-login]") || { dataset: {} };
  var cfg = me.dataset || {};
  var ASSETS = cfg.base || "/local/jarvis-login/";          // the downloaded media (video, photos, planet maps, three.js)
  var HAVE_ASSETS = cfg.assets === "1";
  var STATUS_URL = cfg.statusUrl || "";                    // counts only; shown only when its switch is on
  var SHOW_STATUS = cfg.status === "1" && !!STATUS_URL;
  var THREE_URL = ASSETS + "three.min.js";
  var THREE_SRI = "sha512-dLxUelApnYxpLt6K2iomGngnHO83iUvZytA3YjDUCjT0HDOHKXnVYdf3hU4JjM8uEhxf9nD1/ey98U3t2vZ0qQ==";
  var CREDITS = "";                                        // from the download's manifest.json (its credit line)
  var MEDIA = {};                                          // the files the download holds
  // review round 6: "TLS" only when the page really came over https
  var LINK = location.protocol === "https:" ? "SECURE LINK · TLS" : "NOT ENCRYPTED (HTTP)";

  var doc = document, root = doc.documentElement;
  var still = !!(window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches);
  var added = [];
  // phones, small tablets and slow devices: fewer stars, simpler planets, lower pixel ratio
  var small = Math.min(innerWidth, innerHeight) < 600 || Math.min(screen.width || 9999, screen.height || 9999) < 600;
  var lowPower = small || (navigator.hardwareConcurrency || 8) <= 4;
  function hidden() { return doc.visibilityState === "hidden"; }

  function el(tag, attrs, html) {
    var e = doc.createElement(tag);
    for (var k in attrs || {}) e.setAttribute(k, attrs[k]);
    if (html) e.innerHTML = html;
    return e;
  }
  function esc(s) {   // jh-r19-xss (step 161): data put into markup
    return String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }
  function safe(name, fn) {
    try { fn(); } catch (err) { if (window.console) console.warn("JARVIS login: " + name + " skipped", err); }
  }
  function undo() {
    root.classList.remove("jl-skin");
    added.forEach(function (n) { if (n && n.parentNode) n.parentNode.removeChild(n); });
  }

  var content = doc.querySelector("body > .content");
  if (!content || !doc.querySelector("ha-authorize")) return;   // not the page we know: leave it alone

  /* ---------------------------------------------------------------- page structure */
  var core, statusBox;
  try {
    var bg = el("div", { id: "jl-bg", "aria-hidden": "true" },
      (HAVE_ASSETS ? '<video muted loop playsinline preload="none" poster="' + esc(ASSETS) + 'space-poster.jpg">' +
        '<source src="' + esc(ASSETS) + 'space.mp4" type="video/mp4"></video>' : '') +
      '<div class="jl-photo jl-a"></div><div class="jl-photo jl-b"></div>' +
      '<canvas id="jl-planets"></canvas><canvas id="jl-sky"></canvas><div class="jl-shade"></div>' +
      '<div class="jl-corner jl-tl"></div><div class="jl-corner jl-tr"></div><div class="jl-corner jl-bl"></div><div class="jl-corner jl-br"></div>');
    statusBox = el("div", { id: "jl-status", "class": "jl-hud", role: "status", "aria-live": "polite" },
      '<div><span class="jl-dot ok"></span><span class="jl-sttext">CHECKING SYSTEMS…</span></div><div class="jl-stsub">' + LINK + '</div>');
    core = el("div", { id: "jl-core", "aria-hidden": "true" },
      '<svg viewBox="0 0 512 512"><defs>' +
        '<radialGradient id="jl-jbg" cx="50%" cy="45%" r="55%"><stop offset="0" stop-color="#0d3352"/><stop offset="0.7" stop-color="#061726"/><stop offset="1" stop-color="#030a12"/></radialGradient>' +
        '<filter id="jl-jglow" x="-30%" y="-30%" width="160%" height="160%"><feGaussianBlur stdDeviation="5" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>' +
      '</defs>' +
      '<circle cx="256" cy="256" r="252" fill="url(#jl-jbg)"/>' +
      '<g class="jl-rings" fill="none" stroke="#33d6ff" filter="url(#jl-jglow)">' +
        '<g class="jl-r1"><circle cx="256" cy="256" r="226" stroke-width="7" stroke-dasharray="210 22 60 22 120 22" opacity="0.95"/></g>' +
        '<circle cx="256" cy="256" r="204" stroke-width="2" opacity="0.55"/>' +
        '<g class="jl-r3"><circle cx="256" cy="256" r="186" stroke-width="16" stroke-dasharray="4 9" opacity="0.5"/></g>' +
        '<g class="jl-r2"><circle cx="256" cy="256" r="160" stroke-width="6" stroke-dasharray="330 30 90 30 40 30" opacity="0.95" transform="rotate(-40 256 256)"/></g>' +
        '<circle class="jl-pulse" cx="256" cy="256" r="130" stroke-width="3" opacity="0.8"/>' +
      '</g>' +
      '<path class="jl-j" d="M292 168 V292 a50 50 0 0 1 -100 0" fill="none" stroke="#eafcff" stroke-width="36" stroke-linecap="round" filter="url(#jl-jglow)"/>' +
      '</svg><div class="jl-scan"></div>');
    var hudr = el("div", { id: "jl-hudr", "class": "jl-hud", "aria-hidden": "true" },
      '<div>HOME ASSISTANT</div><div class="jl-name">J.A.R.V.I.S.</div>');
    var note = el("div", { id: "jl-note" });

    var body = doc.body;
    body.insertBefore(bg, body.firstChild);
    if (SHOW_STATUS) body.insertBefore(statusBox, content);
    body.insertBefore(core, content);
    body.appendChild(hudr);
    body.appendChild(note);
    added.push(bg, statusBox, core, hudr, note);
    root.classList.add("jl-skin");
    if (!HAVE_ASSETS) root.classList.add("jl-nomedia");
  } catch (err) {
    undo();
    if (window.console) console.warn("JARVIS login skin off", err);
    return;
  }

  /* ---------------------------------------------------------------- what the download holds, and its credit line */
  var mediaReady = HAVE_ASSETS ? fetch(ASSETS + "manifest.json", { cache: "no-cache", credentials: "same-origin" })
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (m) {
      if (!m || !Array.isArray(m.files)) return false;
      m.files.forEach(function (f) { MEDIA[f] = true; });
      CREDITS = String(m.credits || "");
      note.textContent = CREDITS;
      if (MEDIA["fonts/fonts.css"]) {   // the live look's fonts (Orbitron, Inter), from the download instead of another site
        var ff = el("link", { rel: "stylesheet", href: ASSETS + "fonts/fonts.css" });
        doc.head.appendChild(ff); added.push(ff);
      }
      return true;
    })
    .catch(function () { return false; }) : Promise.resolve(false);

  /* ---------------------------------------------------------------- Home Assistant's dark colours, always
     The login form takes its colours from Home Assistant's design tokens, which follow the device's light/dark
     setting. The skin is always dark, so copy Home Assistant's own dark token values (whatever this version has)
     onto html.jl-skin. login.css then sets the few brand colours on top. */
  safe("dark tokens", function () {
    var style = el("style", { id: "jl-dark" }); doc.head.appendChild(style); added.push(style);
    var last = "";
    function copy() {
      var out = [];
      Array.prototype.forEach.call(doc.styleSheets, function (sheet) {
        var rules; try { rules = sheet.cssRules; } catch (e) { return; }
        Array.prototype.forEach.call(rules || [], function (r) {
          if (!r.media || !/prefers-color-scheme:\s*dark/.test(r.media.mediaText || r.conditionText || "")) return;
          Array.prototype.forEach.call(r.cssRules || [], function (inner) {
            if (inner.style && /^(html|:root)$/.test((inner.selectorText || "").trim())) out.push(inner.style.cssText);
          });
        });
      });
      var css = out.length ? "html.jl-skin{" + out.join("") + "}" : "";
      if (css !== last) { style.textContent = css; last = css; }
    }
    copy();
    var n = 0, t = setInterval(function () { copy(); if (++n > 20) clearInterval(t); }, 400);   // tokens arrive with HA's script
  });

  /* ---------------------------------------------------------------- typed greeting under the title */
  safe("greeting", function () {
    var h = new Date().getHours();
    var text = (h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening") + ". I am JARVIS.";
    // set on <ha-authorize> only (a page-wide variable would restyle the whole page on every letter), and only
    // while typing: no timer keeps running afterwards
    var host = doc.querySelector("ha-authorize");
    function show(v) { host.style.setProperty("--jl-typed", JSON.stringify(v)); }
    if (still) { show(text); return; }
    var i = 0;
    (function type() {
      show(text.slice(0, i) + (i < text.length ? "▍" : ""));
      if (i++ < text.length) setTimeout(type, 55);
    })();
  });

  /* ---------------------------------------------------------------- the J flashes red on every key press
     One listener on the document: keydown is composed, so it also arrives from inside shadow DOM. Only a key press
     in a text field of the login form counts. The key itself is never looked at. */
  safe("key flash", function () {
    var TEXTY = { text: 1, password: 1, email: 1, number: 1, tel: 1, search: 1, "": 1 };
    var timer;
    doc.addEventListener("keydown", function (e) {
      var path = e.composedPath ? e.composedPath() : [e.target];
      var t = path[0];
      if (!t || t.tagName !== "INPUT" || !TEXTY[(t.getAttribute("type") || "").toLowerCase()]) return;
      var inForm = path.some(function (n) { return n && n.tagName === "HA-AUTHORIZE"; });
      if (!inForm) return;
      core.classList.add("key");
      clearTimeout(timer);
      timer = setTimeout(function () { core.classList.remove("key"); }, 140);
    }, { capture: true, passive: true });
  });

  /* ---------------------------------------------------------------- on-screen keyboard: keep the focused field visible */
  safe("keyboard", function () {
    var focused = null;
    function reveal() {
      if (!focused || !focused.isConnected) return;
      var r = focused.getBoundingClientRect(), vv = window.visualViewport, top = vv ? vv.offsetTop : 0, h = vv ? vv.height : innerHeight;
      if (r.top < top + 8 || r.bottom > top + h - 8) focused.scrollIntoView({ block: "center", behavior: still ? "auto" : "smooth" });
    }
    doc.addEventListener("focusin", function (e) {
      var path = e.composedPath ? e.composedPath() : [e.target];
      var t = path[0];
      focused = t && t.tagName === "INPUT" && path.some(function (n) { return n && n.tagName === "HA-AUTHORIZE"; }) ? t : null;
      if (focused) setTimeout(reveal, 350);       // after the keyboard has opened
    });
    doc.addEventListener("focusout", function () { focused = null; });
    if (window.visualViewport) visualViewport.addEventListener("resize", function () { setTimeout(reveal, 50); });
    addEventListener("resize", function () { setTimeout(reveal, 50); });
  });

  /* ---------------------------------------------------------------- live status (counts only), every 60 s */
  if (SHOW_STATUS) safe("status", function () {
    var dot = statusBox.querySelector(".jl-dot"), tx = statusBox.querySelector(".jl-sttext"), sub = statusBox.querySelector(".jl-stsub");
    function show(level, text, subText) {
      dot.className = "jl-dot " + level;
      tx.className = "jl-sttext" + (level === "ok" ? "" : " jl-st-" + level);
      tx.textContent = text;
      sub.textContent = subText || LINK;
    }
    function load() {
      fetch(STATUS_URL, { cache: "no-store", credentials: "same-origin" })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (j) {
          if (!j || typeof j.issues !== "number") { show("warn", "STATUS UNKNOWN"); return; }
          var age = Date.now() / 1000 - (j.updated || 0);
          if (age > 900) { show("warn", "STATUS STALE", "LAST CHECK " + Math.round(age / 60) + " MIN AGO"); return; }
          if (!j.issues) { show("ok", "ALL SYSTEMS ONLINE"); return; }
          show(j.issues >= 3 ? "bad" : "warn", j.issues + (j.issues === 1 ? " ISSUE" : " ISSUES") + " DETECTED",
               String(j.summary || "DETAILS AFTER LOGIN").toUpperCase());
        })
        .catch(function () { show("warn", "STATUS UNKNOWN"); });
    }
    load();
    setInterval(load, 60000);
  });

  /* ---------------------------------------------------------------- heavy layers wait for the login form
     The video (8 MB) and the planet maps are loaded only after Home Assistant's login form is on screen: started
     together with the page they held up Home Assistant's own login-flow request through the tunnel. */
  function whenFormReady(cb) {
    var t0 = Date.now();
    (function check() {
      var a = doc.querySelector("ha-authorize");
      if ((a && a.querySelector("input, ha-alert[alert-type=error]")) || Date.now() - t0 > 15000) { setTimeout(cb, 600); return; }
      setTimeout(check, 200);
    })();
  }

  /* ---------------------------------------------------------------- video, photos as fallback */
  if (HAVE_ASSETS) whenFormReady(function () { safe("video", function () {
    var bg = doc.getElementById("jl-bg"), v = bg.querySelector("video");
    var a = bg.querySelector(".jl-a"), b = bg.querySelector(".jl-b");
    if (!v) return;
    function photos() {
      if (bg.classList.contains("jl-photos")) return;
      bg.classList.add("jl-photos");                          // the photos are only downloaded now
      a.style.backgroundImage = "url(" + ASSETS + "space-1.jpg)";
      b.style.backgroundImage = "url(" + ASSETS + "space-2.jpg)";
      if (v.error) v.style.display = "none";
      a.classList.add("on");
      var on = a;
      swap = setInterval(function () { var next = on === a ? b : a; next.classList.add("on"); on.classList.remove("on"); on = next; }, 40000);
    }
    var swap;
    // a video that starts late (slow connection) still takes over from the photos
    v.addEventListener("playing", function () { clearInterval(swap); a.classList.remove("on"); b.classList.remove("on"); v.style.display = ""; });
    if (still) { v.removeAttribute("autoplay"); return; }   // poster only, nothing moves
    // iOS / Android only autoplay a muted inline video: set all of it as properties too
    v.muted = true; v.defaultMuted = true; v.playsInline = true; v.setAttribute("webkit-playsinline", ""); v.autoplay = true;
    doc.addEventListener("visibilitychange", function () {
      if (hidden()) v.pause(); else if (v.style.display !== "none") { var q = v.play(); if (q && q.catch) q.catch(function () {}); }
    });
    v.addEventListener("error", photos, true);
    v.preload = "auto";
    v.load();
    var p = v.play && v.play();
    if (p && p.catch) p.catch(function () {});
    setTimeout(function () { if (v.error || v.readyState < 3) photos(); }, 6000);
  }); });

  /* ---------------------------------------------------------------- sky canvas: stars, galaxies, comets, shooting stars */
  safe("sky", function () {
    var c = doc.getElementById("jl-sky"), x = c.getContext("2d"), W, H, dpr = lowPower ? 1 : Math.min(window.devicePixelRatio || 1, 2), T = 0, last = 0;
    function rnd(a, b) { return a + Math.random() * (b - a); }
    function pick(a) { return a[Math.floor(Math.random() * a.length)]; }
    var gal = [], stars = [], comets = [], shoots = [];
    function init() {
      W = innerWidth; H = innerHeight; c.width = W * dpr; c.height = H * dpr; x.setTransform(dpr, 0, 0, dpr, 0, 0);
      gal = [];
      for (var g = 0; g < (lowPower ? 3 : 4); g++) {
        var arms = [];
        for (var q = 0; q < (lowPower ? 140 : 260); q++) { var d = Math.pow(Math.random(), .7); arms.push({ d: d, ang: d * 4.2 + (q % 2) * Math.PI + rnd(-.35, .35), s: rnd(.4, 1.3) }); }
        gal.push({ cx: rnd(.1, .9), cy: rnd(.1, .9), wx: rnd(.02, .06), wy: rnd(.02, .05), ph: rnd(0, 6.3), r: rnd(16, 34), t: rnd(0, 6.3),
          sp: rnd(.0005, .0014) * (Math.random() < .5 ? -1 : 1), tilt: rnd(.3, .75), hue: ["255,200,150", "180,205,255", "255,170,215", "170,250,235"][g], arms: arms });
      }
      stars = [];
      for (var s = 0; s < Math.round(W * H / (lowPower ? 9000 : 4500)); s++) stars.push({ x: rnd(0, W), y: rnd(0, H), z: Math.random(), a: rnd(0, 6.3) });
      if (still) frame(0);
    }
    function galaxy(g) {
      var gx = (g.cx + Math.sin(T * .00005 + g.ph) * g.wx) * W, gy = (g.cy + Math.cos(T * .00004 + g.ph) * g.wy) * H;
      x.save(); x.translate(gx, gy); x.rotate(g.t); x.scale(1, g.tilt);
      var gr = x.createRadialGradient(0, 0, 0, 0, 0, g.r * 1.6);
      gr.addColorStop(0, "rgba(255,250,235,.8)"); gr.addColorStop(.12, "rgba(" + g.hue + ",.45)"); gr.addColorStop(1, "rgba(" + g.hue + ",0)");
      x.fillStyle = gr; x.beginPath(); x.arc(0, 0, g.r * 1.6, 0, 6.283); x.fill();
      g.arms.forEach(function (p) { var rr = p.d * g.r * 2.1; x.fillStyle = "rgba(" + g.hue + "," + (.5 * (1 - p.d) + .1) + ")"; x.fillRect(Math.cos(p.ang) * rr, Math.sin(p.ang) * rr, p.s, p.s); });
      x.restore();
    }
    function qb(o, t) {
      var u = 1 - t;
      return { x: u * u * o.x0 + 2 * u * t * o.cx + t * t * o.x1, y: u * u * o.y0 + 2 * u * t * o.cy + t * t * o.y1,
               dx: 2 * u * (o.cx - o.x0) + 2 * t * (o.x1 - o.cx), dy: 2 * u * (o.cy - o.y0) + 2 * t * (o.y1 - o.cy) };
    }
    function newComet() {
      var side = pick([0, 1, 2]), x0, y0, x1, y1;
      if (side === 0) { x0 = -80; y0 = rnd(0, H * .6); x1 = W + 80; y1 = rnd(0, H); }
      else if (side === 1) { x0 = W + 80; y0 = rnd(0, H * .6); x1 = -80; y1 = rnd(0, H); }
      else { x0 = rnd(0, W); y0 = -80; x1 = rnd(0, W); y1 = H + 80; }
      return { x0: x0, y0: y0, x1: x1, y1: y1, cx: (x0 + x1) / 2 + rnd(-W * .35, W * .35), cy: (y0 + y1) / 2 + rnd(-H * .35, H * .35), t: 0, dt: rnd(.0004, .0007), len: rnd(130, 240) };
    }
    function drawComet(cm) {
      var p = qb(cm, cm.t), L = Math.hypot(p.dx, p.dy) || 1, ux = -p.dx / L, uy = -p.dy / L, nx = -uy, ny = ux;
      var pts = [];
      for (var k = 0; k <= 12; k++) pts.push(qb(cm, Math.max(0, cm.t - k * cm.len / (L * 1200))));
      x.lineCap = "round";
      for (var k3 = 0; k3 < pts.length - 1; k3++) {
        var f = 1 - k3 / pts.length;
        x.strokeStyle = "rgba(180,230,255," + (.75 * f) + ")"; x.lineWidth = 2.6 * f + .4;
        x.beginPath(); x.moveTo(pts[k3].x, pts[k3].y); x.lineTo(pts[k3 + 1].x, pts[k3 + 1].y); x.stroke();
      }
      var tx = p.x + ux * cm.len, ty = p.y + uy * cm.len, dust = x.createLinearGradient(p.x, p.y, tx, ty);
      dust.addColorStop(0, "rgba(255,240,215,.45)"); dust.addColorStop(1, "rgba(255,240,215,0)");
      x.fillStyle = dust; x.beginPath(); x.moveTo(p.x + nx * 3, p.y + ny * 3);
      x.quadraticCurveTo(p.x + ux * cm.len * .5 + nx * cm.len * .14, p.y + uy * cm.len * .5 + ny * cm.len * .14, tx + nx * cm.len * .3, ty + ny * cm.len * .3);
      x.lineTo(tx + nx * cm.len * .1, ty + ny * cm.len * .1);
      x.quadraticCurveTo(p.x + ux * cm.len * .5, p.y + uy * cm.len * .5, p.x - nx * 3, p.y - ny * 3); x.closePath(); x.fill();
      var co = x.createRadialGradient(p.x, p.y, 0, p.x, p.y, 15);
      co.addColorStop(0, "#fff"); co.addColorStop(.25, "rgba(200,240,255,.6)"); co.addColorStop(1, "rgba(200,240,255,0)");
      x.fillStyle = co; x.beginPath(); x.arc(p.x, p.y, 15, 0, 6.283); x.fill();
    }
    var slowAvg = 16, skip = false;
    function frame(now) {
      var raw = (now || 0) - last || 16;
      slowAvg = slowAvg * 0.95 + Math.min(raw, 500) * 0.05;
      if (slowAvg > 40 && !still) { skip = !skip; if (skip) { if (hidden()) paused = true; else requestAnimationFrame(frame); return; } }   // slow device: draw every other frame
      var dt = Math.min(50, raw); last = now || 0; T += dt; var k = dt / 16;
      x.clearRect(0, 0, W, H);
      gal.forEach(function (g) { if (!still) g.t += g.sp * k; galaxy(g); });
      stars.forEach(function (s) {
        if (!still) { s.x -= (.03 + s.z * .25) * k; if (s.x < 0) { s.x = W; s.y = rnd(0, H); } s.a += (.02 + s.z * .03) * k; }
        var r = .4 + s.z * 1.3, tw = .35 + .65 * Math.abs(Math.sin(s.a));
        x.fillStyle = "rgba(235,245,255," + tw + ")"; x.beginPath(); x.arc(s.x, s.y, r, 0, 6.283); x.fill();
      });
      if (!still) {
        if (comets.length < 2 && Math.random() < .0015 * k) comets.push(newComet());
        comets = comets.filter(function (cm) { cm.t += cm.dt * k; drawComet(cm); return cm.t < 1; });
        if (shoots.length < 1 && Math.random() < .004 * k) {
          var sx = rnd(0, W), sy = rnd(0, H * .5);
          shoots.push({ x0: sx, y0: sy, x1: sx - rnd(250, 450), y1: sy + rnd(120, 260), cx: sx - rnd(80, 200), cy: sy + rnd(0, 40), t: 0 });
        }
        shoots = shoots.filter(function (s) {
          s.t += .03 * k;
          var a = qb(s, Math.min(1, s.t)), b = qb(s, Math.max(0, s.t - .2)), g2 = x.createLinearGradient(a.x, a.y, b.x, b.y);
          g2.addColorStop(0, "rgba(255,255,255," + Math.max(0, 1 - s.t) + ")"); g2.addColorStop(1, "rgba(255,255,255,0)");
          x.strokeStyle = g2; x.lineWidth = 1.6; x.beginPath(); x.moveTo(a.x, a.y);
          for (var q = 1; q <= 6; q++) { var m = qb(s, Math.max(0, s.t - q * .2 / 6)); x.lineTo(m.x, m.y); }
          x.stroke(); return s.t < 1;
        });
        if (hidden()) paused = true; else requestAnimationFrame(frame);
      }
    }
    var paused = false;   // the browser already slows hidden tabs; this stops the loop completely until visible again
    doc.addEventListener("visibilitychange", function () { if (!hidden() && paused && !still) { paused = false; last = 0; requestAnimationFrame(frame); } });
    init();
    addEventListener("resize", init);
    if (!still) requestAnimationFrame(frame);
  });

  /* ---------------------------------------------------------------- 3D planets (the downloaded three.js r128, checked by its hash)
     Only with the download; a planet whose map the download does not hold is left out. */
  if (HAVE_ASSETS) whenFormReady(function () { setTimeout(function () { mediaReady.then(function (ok) { safe("planets", function () {
    if (!ok || !MEDIA["three.min.js"]) return;
    var probe = doc.createElement("canvas");
    if (!(probe.getContext("webgl") || probe.getContext("experimental-webgl"))) return;
    var s = el("script", { src: THREE_URL, integrity: THREE_SRI, crossorigin: "anonymous", referrerpolicy: "no-referrer" });
    s.onload = function () { safe("planets scene", planets); };
    doc.head.appendChild(s); added.push(s);
  }); }); }, 1500); });

  function planets() {
    var T3 = window.THREE; if (!T3) return;
    var cv = doc.getElementById("jl-planets");
    var ren = new T3.WebGLRenderer({ canvas: cv, alpha: true, antialias: !lowPower, powerPreference: lowPower ? "low-power" : "default" });
    ren.setPixelRatio(lowPower ? 1 : Math.min(window.devicePixelRatio || 1, 1.5));
    var SEG = lowPower ? 32 : 64, SEG2 = lowPower ? 24 : 48; ren.setClearColor(0x000000, 0);
    if (ren.outputEncoding !== undefined) ren.outputEncoding = T3.sRGBEncoding;
    var scene = new T3.Scene(), cam = new T3.PerspectiveCamera(42, 1, .1, 2000); cam.position.set(0, 0, 90);
    var sun = new T3.DirectionalLight(0xfff4e6, 1.9); sun.position.set(-120, 60, 80); scene.add(sun);
    scene.add(new T3.AmbientLight(0x223344, .18));
    var L = new T3.TextureLoader(), base = ASSETS + "tex/";
    function tex(n) { var t = L.load(base + n, redraw); if (t.encoding !== undefined) t.encoding = T3.sRGBEncoding; t.anisotropy = lowPower ? 1 : 4; return t; }
    var bodies = [];
    function has(n) { return !!MEDIA["tex/" + n]; }
    function body(o) {
      if (!has(o.map)) return null;   // not in the download: left out
      if (o.clouds && !has(o.clouds)) o.clouds = null;
      if (o.ring && !has(o.ring)) o.ring = null;
      var mesh = new T3.Mesh(new T3.SphereGeometry(o.r, SEG, SEG2), new T3.MeshStandardMaterial({ map: tex(o.map), roughness: o.rough || 1, metalness: 0 }));
      var pivot = new T3.Object3D(); pivot.rotation.z = o.tilt || 0; pivot.add(mesh); scene.add(pivot);
      var b = { o: o, mesh: mesh, pivot: pivot, a: o.start || 0, kids: [] };
      if (o.clouds) {
        var cm = new T3.Mesh(new T3.SphereGeometry(o.r * 1.015, SEG, SEG2), new T3.MeshStandardMaterial({ map: tex(o.clouds), transparent: true, opacity: .85, depthWrite: false, alphaMap: tex(o.clouds) }));
        mesh.add(cm); b.clouds = cm;
      }
      if (o.atmo) pivot.add(new T3.Mesh(new T3.SphereGeometry(o.r * 1.06, 48, 32), new T3.MeshBasicMaterial({ color: o.atmo, transparent: true, opacity: .14, side: T3.BackSide, depthWrite: false })));
      if (o.ring) {
        var rg = new T3.RingGeometry(o.r * 1.25, o.r * 2.3, 128, 1), pos = rg.attributes.position, uv = rg.attributes.uv, v = new T3.Vector3();
        for (var i = 0; i < pos.count; i++) { v.fromBufferAttribute(pos, i); uv.setXY(i, (v.length() - o.r * 1.25) / (o.r * 1.05), .5); }
        var rt = tex(o.ring), rm = new T3.Mesh(rg, new T3.MeshStandardMaterial({ map: rt, alphaMap: rt, transparent: true, side: T3.DoubleSide, depthWrite: false, roughness: 1 }));
        rm.rotation.x = Math.PI / 2; pivot.add(rm);
      }
      bodies.push(b); return b;
    }
    // around the edges and deep behind, never filling the middle where the login card is
    var jup = body({ map: "2k_jupiter.jpg", r: 9, orbit: { c: [-70, -28, -60], R: 28, inc: .35, sp: .006 }, start: 1.2, rot: .12, tilt: .05 });
    body({ map: "2k_saturn.jpg", ring: "2k_saturn_ring_alpha.png", r: 6, orbit: { c: [75, 26, -90], R: 30, inc: -.25, sp: -.005 }, start: 4, rot: .1, tilt: .47 });
    var earth = body({ map: "2k_earth_daymap.jpg", clouds: "2k_earth_clouds.jpg", atmo: 0x5fb4ff, r: 3.6, rough: .8, orbit: { c: [52, -30, -10], R: 14, inc: .5, sp: .012 }, start: 2, rot: .25, tilt: .41 });
    var mars = body({ map: "2k_mars.jpg", atmo: 0xff9a6a, r: 2, orbit: { c: [-48, 30, -20], R: 16, inc: -.6, sp: .015 }, start: 5.3, rot: .24, tilt: .44 });
    body({ map: "2k_neptune.jpg", atmo: 0x6f9cff, r: 4.5, orbit: { c: [-15, 45, -180], R: 40, inc: .2, sp: .003 }, start: 3.4, rot: .16, tilt: .49 });
    body({ map: "2k_uranus.jpg", atmo: 0x9fe8f0, r: 3.6, orbit: { c: [110, -45, -160], R: 35, inc: .3, sp: -.0035 }, start: .4, rot: .14, tilt: 1.71 });
    body({ map: "2k_mercury.jpg", r: 1.4, orbit: { c: [-30, -38, -25], R: 12, inc: .12, sp: .02 }, start: .9, rot: .03, tilt: .01 });
    body({ map: "2k_venus_atmosphere.jpg", atmo: 0xffe2a8, r: 2.4, orbit: { c: [-80, 5, -40], R: 20, inc: .15, sp: .009 }, start: 2.7, rot: .05, tilt: 3.09 });
    function moon(parent, r, d, sp, inc) {
      if (!parent || !has("2k_moon.jpg")) return;
      var m = new T3.Mesh(new T3.SphereGeometry(r, 32, 24), new T3.MeshStandardMaterial({ map: tex("2k_moon.jpg"), roughness: 1 }));
      scene.add(m); parent.kids.push({ m: m, d: d, sp: sp, inc: inc, a: Math.random() * 6.28 });
    }
    moon(earth, .98, 8, .25, .09); moon(jup, .9, 15, .32, .04); moon(jup, .7, 19, .21, .06); moon(mars, .35, 4.5, .5, .02);
    var rocks = [], rockMat = new T3.MeshStandardMaterial({ color: 0x5e5852, roughness: 1, flatShading: true });
    for (var i = 0; i < (lowPower ? 5 : 12); i++) {
      var g = new T3.IcosahedronGeometry(Math.random() * .5 + .2, 1), p = g.attributes.position, vv = new T3.Vector3();
      for (var k = 0; k < p.count; k++) { vv.fromBufferAttribute(p, k); vv.multiplyScalar(.7 + Math.random() * .5); p.setXYZ(k, vv.x, vv.y * (.6 + Math.random() * .5), vv.z); }
      g.computeVertexNormals();
      var rock = new T3.Mesh(g, rockMat); scene.add(rock);
      rocks.push({ m: rock, a: Math.random() * 6.28, R: 110 + Math.random() * 40, y: (Math.random() - .5) * 30, sp: .01 + Math.random() * .012,
        spin: new T3.Vector3(Math.random(), Math.random(), Math.random()).multiplyScalar(.02) });
    }
    function size() { var w = innerWidth, h = innerHeight; ren.setSize(w, h, false); cam.aspect = w / h; cam.fov = w < 900 ? 55 : 42; cam.updateProjectionMatrix(); redraw(); }
    var t0 = performance.now(), prev = t0;
    function place(t) {
      bodies.forEach(function (b) {
        var ob = b.o.orbit, x = Math.cos(b.a) * ob.R, z = Math.sin(b.a) * ob.R;
        b.pivot.position.set(ob.c[0] + x, ob.c[1] + z * Math.sin(ob.inc), ob.c[2] + z * Math.cos(ob.inc));
        b.kids.forEach(function (kk) { kk.m.position.set(b.pivot.position.x + Math.cos(kk.a) * kk.d, b.pivot.position.y + Math.sin(kk.a) * kk.d * Math.sin(kk.inc + .2), b.pivot.position.z + Math.sin(kk.a) * kk.d); });
      });
      rocks.forEach(function (r) { r.m.position.set(Math.cos(r.a) * r.R, r.y + Math.sin(r.a * 2) * 4 - 10, Math.sin(r.a) * r.R * .4 - 220); });
      cam.position.set(Math.sin(t * .02) * 8, Math.cos(t * .017) * 4, 90 + Math.sin(t * .011) * 6); cam.lookAt(0, 0, -40);
    }
    function redraw() { if (still) { place(0); ren.render(scene, cam); } }    // reduced motion: one still picture, redrawn as maps arrive
    // adapts to the device: if the scene cannot keep about 20 frames a second it first draws at a lower resolution,
    // then stops moving (one still picture), so the login form itself always stays responsive
    var slow = 0, frames = 0, pr = ren.getPixelRatio(), windowStart = performance.now();
    function adapt(raw) {
      frames++;
      if (raw > 0.05) slow++;
      if (performance.now() - windowStart < 2000) return true;   // judged every 2 seconds
      windowStart = performance.now();
      var bad = slow / frames > 0.5; frames = 0; slow = 0;
      if (!bad) return true;
      if (pr > 0.6) { pr = Math.max(0.5, pr * 0.6); ren.setPixelRatio(pr); size(); return true; }
      return false;
    }
    function loop(now) {
      var raw = (now - prev) / 1000;
      var dt = Math.min(.05, raw); prev = now; var t = (now - t0) / 1000;
      if (!adapt(raw)) { place(t); ren.render(scene, cam); return; }   // too slow: keep the last picture
      bodies.forEach(function (b) {
        b.a += b.o.orbit.sp * dt * 6; b.mesh.rotation.y += b.o.rot * dt; if (b.clouds) b.clouds.rotation.y += b.o.rot * .35 * dt;
        b.kids.forEach(function (kk) { kk.a += kk.sp * dt; kk.m.rotation.y += .05 * dt; });
      });
      rocks.forEach(function (r) { r.a += r.sp * dt; r.m.rotation.x += r.spin.x; r.m.rotation.y += r.spin.y; r.m.rotation.z += r.spin.z; });
      place(t); ren.render(scene, cam);
      if (hidden()) stopped = true; else requestAnimationFrame(loop);
    }
    var stopped = false;
    doc.addEventListener("visibilitychange", function () { if (!hidden() && stopped && !still) { stopped = false; prev = performance.now(); requestAnimationFrame(loop); } });
    size(); addEventListener("resize", size);
    if (!still) requestAnimationFrame(loop);
  }
})();

/* "Identify yourself." once when the login page opens, as the live login does: tried about 0.6 s after the page has loaded;
 * browsers often block sound before the visitor has interacted with the page, and then it plays on the first tap, click or
 * key press instead. Only while its switch is on (data-voice) and the skin is on this page. At most once per page load. */
(function () {
  try {
    var me = document.querySelector("script[data-jarvis-login]");
    var cfg = (me && me.dataset) || {};
    if (cfg.voice !== "1" || !cfg.voiceUrl || !document.documentElement.classList.contains("jl-skin")) return;
    var played = false, busy = false, EVENTS = ["pointerdown", "keydown", "touchstart"];
    var a = new Audio(cfg.voiceUrl); a.preload = "auto"; a.volume = 0.9;
    function off() { EVENTS.forEach(function (e) { document.removeEventListener(e, onFirst, true); }); }
    function say() {
      if (played || busy) return;
      busy = true;
      var p;
      try { p = a.play(); } catch (e) { busy = false; return; }
      if (p && p.then) p.then(function () { played = true; busy = false; off(); }, function () { busy = false; });   // refused: wait for a tap
      else { played = true; busy = false; off(); }
    }
    function onFirst() { say(); }
    EVENTS.forEach(function (e) { document.addEventListener(e, onFirst, true); });
    var start = function () { setTimeout(say, 600); };
    if (document.readyState === "complete") start(); else window.addEventListener("load", start);
  } catch (e) { /* sound is optional */ }
})();
