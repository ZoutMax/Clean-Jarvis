// Clean Jarvis optional look "Hide the idle media bar" (optional_looks.py, v2). On Home Assistant's own Media page (/media-browser)
// the player bar at the bottom ("Nothing playing", the progress line and the "Web browser" target button) does nothing while the
// target is "Web browser" and nothing plays there: films then open in a pop-up with their own controls. Then only the target button
// stays, small, at the bottom right, so a speaker or TV can still be picked; the rest of the bar is hidden and the media list gets
// the space back. Also while a film plays in Home Assistant's pop-up and while a picked item loads (only the spinner shows), and
// when the chosen speaker or TV is off, unavailable or gone. As soon as a working speaker or TV is the target, or something plays
// in the browser, Home Assistant's full bar is back.
// Nothing is remembered here: Home Assistant itself keeps the chosen target (per browser). Home Assistant's page is not replaced:
// this only sets an attribute and adds a few style rules. If Home Assistant renames what this hooks into (ha-bar-media-player, its
// entityId or _stateObj, ha-panel-media-browser), it does nothing and the page stays as it was.
// Added to every page with add_extra_js_url while the switch is on, always with a content hash (?v=) so no cache keeps an old copy;
// a second copy does nothing.
(() => {
  if (window.__jhMediaBarIdle) return;
  window.__jhMediaBarIdle = true;
  const BAR = "ha-bar-media-player", PANEL = "ha-panel-media-browser", ATTR = "jh-idle";
  const BROWSER = "browser";                                  // Home Assistant's id for the "Web browser" target
  const QUIET = ["idle", "off", "standby", "unavailable"];    // browser: any other state (playing, paused, buffering, new ones) keeps the bar
  const DEAD = ["off", "unavailable"];                        // speaker or TV: only these (or no entity at all) leave just the button
  const BTN = { wide: 64, narrow: 56 };                       // height of the strip that keeps the button

  const sheet = css => { try { const s = new CSSStyleSheet(); s.replaceSync(css); return s; } catch (e) { return null; } };
  const BAR_CSS = sheet(`
    :host([${ATTR}]) { background: transparent; border-top: none; height: ${BTN.wide}px; pointer-events: none; }
    :host([${ATTR}][narrow]) { height: ${BTN.narrow}px; }
    :host([${ATTR}]) .info, :host([${ATTR}]) .controls-progress:not(.buffering) { display: none; }
    :host([${ATTR}]) .controls-progress.buffering { flex: 1 1 auto; justify-content: flex-end; align-items: flex-end; padding: 0 16px 12px; }
    :host([${ATTR}]) .choose-player { flex: 1 1 auto; justify-content: flex-end; padding: 0 16px 12px; pointer-events: auto; }
    :host([${ATTR}][narrow]) .choose-player { padding: 0 12px 8px; }
    :host([${ATTR}]) .choose-player > * { pointer-events: auto; }
  `);
  const PANEL_CSS = sheet(`
    :host([${ATTR}]) ha-media-player-browse {
      height: calc(100vh - (${BTN.wide}px + var(--header-height, 0px) + var(--safe-area-inset-top, 0px) + var(--safe-area-inset-bottom, 0px)));
    }
    :host([${ATTR}][narrow]) ha-media-player-browse {
      height: calc(100vh - (${BTN.narrow}px + var(--header-height, 0px) + var(--safe-area-inset-top, 0px) + var(--safe-area-inset-bottom, 0px)));
    }
  `);
  if (!BAR_CSS || !PANEL_CSS) return;
  const addSheet = (root, s) => {
    if (root && root.adoptedStyleSheets && !root.adoptedStyleSheets.includes(s)) root.adoptedStyleSheets = [...root.adoptedStyleSheets, s];
  };

  const idle = bar => {
    const s = bar._stateObj;
    if (bar.entityId !== BROWSER) return !bar._newMediaExpected && (!s || DEAD.includes(s.state));
    // Web browser: a picked item is loading (audio then plays in the bar, a film opens the pop-up), or nothing plays here
    return !!bar._newMediaExpected || !s || QUIET.includes(s.state);
  };

  customElements.whenDefined(BAR).then(() => {
    const cls = customElements.get(BAR), proto = cls && cls.prototype;
    const props = cls && cls.elementProperties;
    const getter = proto && Object.getOwnPropertyDescriptor(proto, "_stateObj");
    // Not the bar this was made for: leave Home Assistant alone
    if (!proto || typeof proto.updated !== "function" || !props || !props.has("entityId") || !getter || !getter.get) return;
    const updated = proto.updated;
    proto.updated = function (changed) {
      updated.call(this, changed);
      try {
        const root = this.getRootNode(), panel = root && root.host;
        if (!panel || panel.localName !== PANEL) return;        // only on the Media page
        addSheet(this.shadowRoot, BAR_CSS);
        addSheet(root, PANEL_CSS);
        const on = idle(this);
        this.toggleAttribute(ATTR, on);
        panel.toggleAttribute(ATTR, on);
      } catch (e) { /* leave the bar as Home Assistant drew it */ }
    };
    // A bar already on screen before this file loaded
    const find = (r, d) => {
      if (!r || d > 8) return;
      r.querySelectorAll(BAR).forEach(b => b.requestUpdate && b.requestUpdate());
      r.querySelectorAll("*").forEach(e => e.shadowRoot && find(e.shadowRoot, d + 1));
    };
    try { find(document, 0); } catch (e) { /* nothing on screen */ }
  });
})();
