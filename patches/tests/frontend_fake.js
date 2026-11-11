// Fake Home Assistant for the frontend tests. Nothing here talks to a network: calls are recorded, answers come from the test.
window.sleep = ms => new Promise(r => setTimeout(r, ms));

// Shared storage stands in for HA's per-user data, so two panels act as two devices of one login.
window.mkStore = (value) => ({ value, reads: 0, writes: [], failRead: false, failWrite: false });

window.mkHass = (o = {}) => {
  const store = o.store || mkStore(null);
  const h = {
    states: o.states || { "conversation.jarvis": { entity_id: "conversation.jarvis", state: "ok", attributes: { friendly_name: "JARVIS" } } },
    user: o.user || { id: "u1", name: "Tester", is_admin: true },
    language: "en", locale: { language: "en" }, areas: {}, entities: {}, devices: {}, floors: {}, themes: {}, panels: {},
    store, ws: [], runs: [], binary: [], apiCalls: [], serviceCalls: [],
    pending: null,      // a promise that holds conversation/process back, set by the test
    reply: "Very good, sir.",
    runRefuse: null,    // function(msg) -> error object to refuse a voice run
    async callWS(m) {
      h.ws.push(m);
      if (m.type.startsWith("jarvis_private/")) {
        if (h.privateHold) await h.privateHold;
        if (h.privateFail) throw new Error(h.privateFail);
        if (m.type === "jarvis_private/chat_list") return { entries: (h.chats || {})[m.agent] || [] };
        return { days: m.days };
      }
      if (m.type === "frontend/get_user_data") {
        store.reads++;
        if (store.failRead) throw new Error("load failed");
        return { value: JSON.parse(JSON.stringify(store.value)) };
      }
      if (m.type === "frontend/set_user_data") {
        if (store.failWrite) throw new Error("save failed");
        store.value = JSON.parse(JSON.stringify(m.value)); store.writes.push(store.value);
        return null;
      }
      if (m.type === "conversation/process") {
        if (h.pending) await h.pending;
        return { conversation_id: "c1", response: { speech: { plain: { speech: h.reply } } } };
      }
      if (m.type === "jarvis/get_panel_data") throw new Error("no live data in this test");
      if (m.type === "assist_pipeline/pipeline/list") return { pipelines: h.pipelineRows || [{ id: "p1", conversation_engine: "conversation.jarvis" }], preferred_pipeline: "p1" };
      return {};
    },
    async callApi(method, path, body) { h.apiCalls.push({ method, path, body }); if (h.apiFail) throw new Error(h.apiFail); return {}; },
    async callService(domain, service, data) { h.serviceCalls.push({ domain, service, data }); if (h.serviceFail) throw new Error(h.serviceFail); return {}; },
    connection: {
      socket: { send: (b) => h.binary.push(Array.from(b.slice(0, 3)).concat(b.length)) },
      async subscribeEvents() { return () => {}; },
      connected: true, listeners: {},
      addEventListener(n, f) { (this.listeners[n] = this.listeners[n] || []).push(f); },
      removeEventListener(n, f) { this.listeners[n] = (this.listeners[n] || []).filter(x => x !== f); },
      fire(n) { (this.listeners[n] || []).slice().forEach(f => f()); },
      async subscribeMessage(cb, msg, opts) {
        const refuse = h.runRefuse && h.runRefuse(msg);
        if (refuse) throw refuse;
        const run = { msg, opts, cb, unsubbed: 0 };
        h.runs.push(run);
        return () => { run.unsubbed++; };
      },
    },
  };
  return h;
};

// Adds a panel to the page, on the Chat tab.
window.mkPanel = async (hass) => {
  const p = document.createElement("jarvis-panel");
  document.body.append(p);
  p.hass = hass;
  p._currentTab = "chat";
  p._render();
  await sleep(30);
  return p;
};
window.say = async (p, text) => {
  p.shadowRoot.getElementById("chat-input").value = text;
  await p._sendChat();
  await p._chatSaving;
};
window.press = (p) => p.shadowRoot.getElementById("chat-mic").dispatchEvent(new PointerEvent("pointerdown", { bubbles: true, composed: true, pointerId: 7 }));
window.until = async (f) => { for (let i = 0; i < 300 && !f(); i++) await sleep(10); await sleep(10); };
window.started = async (h) => { for (let i = 0; i < 300 && !h.runs.length; i++) await sleep(10); await sleep(10); };
window.release = (p) => (p.shadowRoot.getElementById("chat-mic") || window).dispatchEvent(new PointerEvent("pointerup", { bubbles: true, composed: true, pointerId: 7 }));

// getUserMedia under test control: resolves when the test says so, with a stream whose stops can be counted.
window.holdMic = () => {
  const real = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
  const g = { calls: 0, stops: 0, open: null };
  navigator.mediaDevices.getUserMedia = async (c) => {
    g.calls++;
    const s = await real(c);
    s.getTracks().forEach(t => { const st = t.stop.bind(t); t.stop = () => { g.stops++; st(); }; });
    if (g.gate) await g.gate;
    return s;
  };
  g.hold = () => { g.gate = new Promise(r => { g.open = r; }); };
  return g;
};
window.ready = true;
