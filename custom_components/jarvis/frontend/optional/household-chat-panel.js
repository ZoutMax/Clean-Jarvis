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
/* Household Chat: the Chat page in the sidebar. Plain web component, no build step.
 * Talks to the household_chat websocket commands; live updates come from household_chat/subscribe.
 * Every user text is put in the page with textContent, never as HTML. */
const T = {
  en: {
    chats: "Chats", typing: "typing…", isTyping: (n) => `${n} is typing…`, type: "Message", today: "Today",
    yesterday: "Yesterday", deleted: "This message was deleted", youDeleted: "You deleted this message", photo: "Photo",
    unread: (n) => (n === 1 ? "1 unread message" : `${n} unread messages`), mute: "Mute notifications",
    unmute: "Unmute notifications", clear: "Clear chat", clearAsk: "Clear this chat for you? The others keep their copy.",
    del: "Delete for everyone", delAsk: "Delete this message for everyone?", cancel: "Cancel", ok: "OK", you: "You",
    nobody: "No messages yet. Say hello!", failed: "Not sent. Tap to try again.", former: "Former member",
    pick: "Pick a chat", offline: "Connecting…", tooBig: "That photo is too large.", nudge: "Nudge",
    nudged: (n) => `${n} sent a nudge`, youNudged: "You sent a nudge", tooSoon: "Wait a few seconds before nudging again.",
    notMember: "This login is not linked to a person, so it has no chats.", left: "This person has left.",
    send: "Send", attach: "Send a photo", back: "Back", menu: "Menu", muted: "Muted", members: (n) => `${n} people`,
    online: "online", offline: "offline", idle: (w) => (w ? `idle · last active ${w}` : "idle"), lastSeen: (w) => `last seen ${w}`, at: (d, t) => `${d} at ${t}`, hideOnline: "Hide my online status",
    showOnline: "Show my online status",
    reply: "Reply", edit: "Edit", edited: "edited", delMe: "Delete for me", replyingTo: (n) => `Replying to ${n}`,
    editing: "Edit message", newMsgs: "New messages", jarvisHint: "Ask JARVIS anything, as in the JARVIS chat.",
    emoji: "Emoji", noRecent: "Emoji you use show up here.",
    emojiGroups: { recent: "Recent", smileys: "Smileys", people: "People", nature: "Animals & nature", food: "Food & drink",
      activity: "Activities & travel", objects: "Objects", symbols: "Symbols & flags" },
  },
  nl: {
    chats: "Chats", typing: "typt…", isTyping: (n) => `${n} typt…`, type: "Bericht", today: "Vandaag",
    yesterday: "Gisteren", deleted: "Dit bericht is verwijderd", youDeleted: "Je hebt dit bericht verwijderd", photo: "Foto",
    unread: (n) => (n === 1 ? "1 ongelezen bericht" : `${n} ongelezen berichten`), mute: "Meldingen dempen",
    unmute: "Meldingen aan", clear: "Chat wissen", clearAsk: "Deze chat voor jou wissen? De anderen houden hun kopie.",
    del: "Verwijderen voor iedereen", delAsk: "Dit bericht voor iedereen verwijderen?", cancel: "Annuleren", ok: "OK",
    you: "Jij", nobody: "Nog geen berichten. Zeg hallo!", failed: "Niet verstuurd. Tik om opnieuw te proberen.",
    former: "Oud-lid", pick: "Kies een chat", offline: "Verbinden…", tooBig: "Die foto is te groot.", nudge: "Por",
    nudged: (n) => `${n} gaf een por`, youNudged: "Je gaf een por", tooSoon: "Wacht een paar seconden voor je weer port.",
    notMember: "Deze login hoort niet bij een persoon en heeft dus geen chats.", left: "Deze persoon is vertrokken.",
    send: "Versturen", attach: "Foto versturen", back: "Terug", menu: "Menu", muted: "Gedempt", members: (n) => `${n} personen`,
    online: "online", offline: "offline", idle: (w) => (w ? `inactief · laatst actief ${w}` : "inactief"), lastSeen: (w) => `laatst gezien ${w}`, at: (d, t) => `${d} om ${t}`, hideOnline: "Mijn online-status verbergen",
    showOnline: "Mijn online-status tonen",
    reply: "Beantwoorden", edit: "Bewerken", edited: "bewerkt", delMe: "Verwijderen voor mij",
    replyingTo: (n) => `Antwoord aan ${n}`, editing: "Bericht bewerken", newMsgs: "Nieuwe berichten",
    jarvisHint: "Vraag JARVIS wat je wilt, net als in de JARVIS-chat.",
    emoji: "Emoji", noRecent: "Emoji die je gebruikt komen hier.",
    emojiGroups: { recent: "Recent", smileys: "Smileys", people: "Mensen", nature: "Dieren en natuur", food: "Eten en drinken",
      activity: "Activiteiten en reizen", objects: "Voorwerpen", symbols: "Symbolen en vlaggen" },
  },
};
const EMOJI = [
  ["\u{1F600}", "smileys", "😀 😃 😄 😁 😆 😅 😂 🤣 🥲 😊 😇 🙂 🙃 😉 😌 😍 🥰 😘 😗 😙 😚 😋 😛 😝 😜 🤪 🤨 🧐 🤓 😎 🥸 🤩 🥳 😏 😒 😞 😔 😟 😕 🙁 ☹️ 😣 😖 😫 😩 🥺 😢 😭 😤 😠 😡 🤬 🤯 😳 🥵 🥶 😱 😨 😰 😥 😓 🤗 🤔 🫣 🤭 🫢 🤫 🤥 😶 😐 😑 😬 🙄 😯 😦 😧 😮 😲 🥱 😴 🤤 😪 😵 🤐 🥴 🤢 🤮 🤧 😷 🤒 🤕 🤑 🤠 😈 👿 🤡 💩 👻 💀 👽 🤖 🎃 😺 😸 😹 😻 😼 😽 🙀 😿 😾"],
  ["\u{1F44B}", "people", "👋 🤚 🖐️ ✋ 🖖 👌 🤌 🤏 ✌️ 🤞 🫰 🤟 🤘 🤙 👈 👉 👆 👇 ☝️ 👍 👎 ✊ 👊 🤛 🤜 👏 🙌 🫶 👐 🤲 🤝 🙏 ✍️ 💅 💪 🦵 🦶 👂 👃 🧠 👀 👅 👄 💋 👶 🧒 👦 👧 🧑 👨 👩 🧓 👴 👵 🙋 🙅 🙆 💁 🤷 🤦 🙇 💃 🕺 👫 👪 🧑‍🍳 🧑‍💻 🧑‍🔧 👮 🤰 🎅"],
  ["\u{1F431}", "nature", "🐶 🐱 🐭 🐹 🐰 🦊 🐻 🐼 🐨 🐯 🦁 🐮 🐷 🐸 🐵 🙈 🙉 🙊 🐔 🐧 🐦 🐤 🦆 🦅 🦉 🐺 🐴 🦄 🐝 🦋 🐌 🐞 🐢 🐍 🐙 🐠 🐟 🐬 🐳 🦈 🐘 🦒 🐈 🐕 🌵 🎄 🌲 🌳 🌴 🌱 🌿 🍀 🍁 🍂 🌷 🌹 🌺 🌸 🌼 🌻 🌞 🌝 🌛 ⭐ 🌟 ✨ ⚡ 🔥 🌈 ☀️ ⛅ ☁️ 🌧️ ⛈️ ❄️ ☃️ 💧 🌊"],
  ["\u{1F354}", "food", "🍏 🍎 🍐 🍊 🍋 🍌 🍉 🍇 🍓 🫐 🍒 🍑 🥭 🍍 🥥 🥝 🍅 🥑 🥦 🥒 🌶️ 🌽 🥕 🥔 🥐 🍞 🥖 🧀 🥚 🍳 🥓 🍗 🍖 🌭 🍔 🍟 🍕 🥪 🌮 🌯 🥗 🍝 🍜 🍲 🍛 🍣 🍱 🥟 🍤 🍚 🍙 🍰 🎂 🧁 🍩 🍪 🍫 🍬 🍭 🍦 ☕ 🍵 🧃 🥤 🍺 🍻 🥂 🍷 🥃 🍸 🍹 🧊"],
  ["\u26BD", "activity", "⚽ 🏀 🏈 ⚾ 🎾 🏐 🎱 🏓 🏸 🥊 🚴 🏊 🏃 🧘 🎮 🎲 🎯 🎳 🎸 🎹 🎤 🎧 🎬 🎨 🏆 🥇 🎉 🎊 🎈 🎁 🚗 🚕 🚌 🚑 🚒 🚓 🏍️ 🚲 🛴 ✈️ 🚀 🚂 🚢 ⛵ 🏠 🏡 🏢 🏥 🏫 🏖️ 🏝️ ⛰️ 🗽 🗼 🌍"],
  ["\u{1F4A1}", "objects", "⌚ 📱 💻 🖥️ 📷 📺 📞 ⏰ 💡 🔦 🕯️ 💰 💳 🔑 🔒 🔨 🛠️ 🧹 🛒 🎓 📚 📝 ✏️ 📅 📌 📎 ✂️ 🗑️ 💊 🩺 🌡️ 🧸 🎀 💤 💬 💭 🎵 🔔 🔕"],
  ["\u2764\uFE0F", "symbols", "❤️ 🧡 💛 💚 💙 💜 🖤 🤍 🤎 💔 ❣️ 💕 💞 💓 💗 💖 💘 💝 💯 ✅ ❌ ❓ ❗ ⚠️ 🚫 ➕ ➖ ✔️ 🆗 🆕 🔴 🟠 🟡 🟢 🔵 🟣 ⭕ 🏳️ 🏴 🇳🇱 🇹🇭 🇬🇧 🇺🇸 🇧🇪 🇩🇪 🇫🇷"],
];
const GROUP = "g:house";
const RECENT_KEY = "household_chat_recent_emoji";
const PAGE_HINT = 120; // px from the bottom that still counts as "at the bottom"
const ICONS = {
  send: "M2,21L23,12L2,3V10L17,12L2,14V21Z",
  attach: "M16.5,6V17.5A4,4 0 0,1 12.5,21.5A4,4 0 0,1 8.5,17.5V5A2.5,2.5 0 0,1 11,2.5A2.5,2.5 0 0,1 13.5,5V15.5A1,1 0 0,1 12.5,16.5A1,1 0 0,1 11.5,15.5V6H10V15.5A2.5,2.5 0 0,0 12.5,18A2.5,2.5 0 0,0 15,15.5V5A4,4 0 0,0 11,1A4,4 0 0,0 7,5V17.5A5.5,5.5 0 0,0 12.5,23A5.5,5.5 0 0,0 18,17.5V6H16.5Z",
  back: "M20,11V13H8L13.5,18.5L12.08,19.92L4.16,12L12.08,4.08L13.5,5.5L8,11H20Z",
  dots: "M12,16A2,2 0 0,1 14,18A2,2 0 0,1 12,20A2,2 0 0,1 10,18A2,2 0 0,1 12,16M12,10A2,2 0 0,1 14,12A2,2 0 0,1 12,14A2,2 0 0,1 10,12A2,2 0 0,1 12,10M12,4A2,2 0 0,1 14,6A2,2 0 0,1 12,8A2,2 0 0,1 10,6A2,2 0 0,1 12,4Z",
  home: "M10,20V14H14V20H19V12H22L12,3L2,12H5V20H10Z",
  bell: "M21,19V20H3V19L5,17V11C5,7.9 7.03,5.17 10,4.29C10,4.19 10,4.1 10,4A2,2 0 0,1 12,2A2,2 0 0,1 14,4C14,4.1 14,4.19 14,4.29C16.97,5.17 19,7.9 19,11V17L21,19M14,21A2,2 0 0,1 12,23A2,2 0 0,1 10,21",
  muted: "M20.84,22.73L18.11,20H3V19L5,17V11C5,9.86 5.29,8.73 5.83,7.72L1.11,3L2.39,1.73L22.11,21.46L20.84,22.73M19,15.8V11C19,7.9 16.97,5.17 14,4.29C14,4.19 14,4.1 14,4A2,2 0 0,0 12,2A2,2 0 0,0 10,4C10,4.1 10,4.19 10,4.29C9.39,4.47 8.8,4.74 8.26,5.09L19,15.8M12,23A2,2 0 0,0 14,21H10A2,2 0 0,0 12,23Z",
  check: "M9,20.42L2.79,14.21L5.62,11.38L9,14.77L18.88,4.88L21.71,7.71L9,20.42Z",
  clock: "M12,20A8,8 0 0,0 20,12A8,8 0 0,0 12,4A8,8 0 0,0 4,12A8,8 0 0,0 12,20M12,2A10,10 0 0,1 22,12A10,10 0 0,1 12,22C6.47,22 2,17.5 2,12A10,10 0 0,1 12,2M12.5,7V12.25L17,14.92L16.25,16.15L11,13V7H12.5Z",
  alert: "M13,13H11V7H13M13,17H11V15H13M12,2A10,10 0 0,0 2,12A10,10 0 0,0 12,22A10,10 0 0,0 22,12A10,10 0 0,0 12,2Z",
  chevron: "M7.41,8.58L12,13.17L16.59,8.58L18,10L12,16L6,10L7.41,8.58Z",
  smile: "M12,2C6.47,2 2,6.5 2,12A10,10 0 0,0 12,22A10,10 0 0,0 22,12A10,10 0 0,0 12,2M12,20A8,8 0 0,1 4,12A8,8 0 0,1 12,4A8,8 0 0,1 20,12A8,8 0 0,1 12,20M15.5,11A1.5,1.5 0 0,0 17,9.5A1.5,1.5 0 0,0 15.5,8A1.5,1.5 0 0,0 14,9.5A1.5,1.5 0 0,0 15.5,11M8.5,11A1.5,1.5 0 0,0 10,9.5A1.5,1.5 0 0,0 8.5,8A1.5,1.5 0 0,0 7,9.5A1.5,1.5 0 0,0 8.5,11M12,17.5C14.33,17.5 16.31,16.04 17.11,14H6.89C7.69,16.04 9.67,17.5 12,17.5Z",
  clockrecent: "M13.5,8H12V13L16.28,15.54L17,14.33L13.5,12.25V8M13,3A9,9 0 0,0 4,12H1L4.96,16.03L9,12H6A7,7 0 0,1 13,5A7,7 0 0,1 20,12A7,7 0 0,1 13,19C11.07,19 9.32,18.21 8.06,16.94L6.64,18.36C8.27,20 10.5,21 13,21A9,9 0 0,0 22,12A9,9 0 0,0 13,3",
};

const STYLE = `
:host { display:block; height:calc(100vh - var(--safe-area-inset-top, 0px) - var(--safe-area-inset-bottom, 0px));
  height:calc(100dvh - var(--safe-area-inset-top, 0px) - var(--safe-area-inset-bottom, 0px)); overflow:hidden; --mine:#d9fdd3; --theirs:#ffffff; --wall:#efeae2; --tick-read:#53bdeb;
  --accent:#00a884; color:var(--primary-text-color); font-family:var(--ha-font-family-body, Roboto, sans-serif); }
:host([dark]) { --mine:#005c4b; --theirs:#202c33; --wall:#0b141a; }
* { box-sizing:border-box; -webkit-tap-highlight-color:transparent; }
.rows, .msgs, .emojis .grid { overscroll-behavior:contain; scrollbar-width:thin; }
.app.narrow .rows, .app.narrow .msgs, .app.narrow .emojis .grid { scrollbar-width:none; }
.app.narrow .rows::-webkit-scrollbar, .app.narrow .msgs::-webkit-scrollbar, .app.narrow .emojis .grid::-webkit-scrollbar { display:none; }
.app.narrow .bubble { -webkit-user-select:none; user-select:none; -webkit-touch-callout:none; }
[hidden] { display:none !important; }
.app { display:flex; height:100%; background:var(--primary-background-color); overflow:hidden; }
.list { width:360px; max-width:100%; display:flex; flex-direction:column; border-right:1px solid var(--divider-color);
  background:var(--card-background-color); }
.conv { flex:1; display:flex; flex-direction:column; min-width:0; background:var(--wall); position:relative; }
.app.narrow .list { width:100%; border-right:none; }
.app.narrow.open .list { display:none; }
.app.narrow:not(.open) .conv { display:none; }
header { height:56px; display:flex; align-items:center; gap:8px; padding:0 8px 0 4px; flex-shrink:0;
  background:var(--app-header-background-color, var(--primary-color)); color:var(--app-header-text-color, #fff); }
header .title { font-size:18px; font-weight:500; flex:1; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
header .sub { font-size:12px; opacity:.85; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
header .who { flex:1; min-width:0; display:flex; flex-direction:column; justify-content:center; }
header .who .title { flex:none; font-size:16px; }
.iconbtn { width:40px; height:40px; border:none; background:none; color:inherit; border-radius:50%; cursor:pointer;
  display:flex; align-items:center; justify-content:center; flex-shrink:0; padding:0; }
.iconbtn:hover { background:rgba(127,127,127,.18); }
.iconbtn svg { width:24px; height:24px; fill:currentColor; }
.pad { width:12px; }
.rows { flex:1; overflow-y:auto; }
.row { display:flex; gap:12px; align-items:center; padding:10px 14px; cursor:pointer; border-bottom:1px solid var(--divider-color); }
.row:hover { background:var(--secondary-background-color); }
.row.sel { background:var(--secondary-background-color); }
.avatar { width:46px; height:46px; border-radius:50%; flex-shrink:0; display:flex; align-items:center; justify-content:center;
  color:#fff; font-weight:500; font-size:18px; overflow:hidden; background:#888; }
header .avatar { width:38px; height:38px; font-size:15px; }
.avatar img { width:100%; height:100%; object-fit:cover; }
.av { position:relative; flex-shrink:0; display:flex; }
.av .dot { position:absolute; right:0; bottom:1px; width:13px; height:13px; border-radius:50%; background:#25d366;
  border:2px solid var(--card-background-color); }
header .av .dot { width:11px; height:11px; border-color:var(--app-header-background-color, var(--primary-color)); }
.av .dot.idle { background:#f5a623; }
.av .dot.away { background:#8a8f98; }
.avatar svg { width:60%; height:60%; fill:#fff; }
.mid { flex:1; min-width:0; }
.line1, .line2 { display:flex; align-items:center; gap:6px; }
.name { flex:1; font-size:16px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.when { font-size:12px; color:var(--secondary-text-color); flex-shrink:0; }
.when.new { color:var(--accent); font-weight:500; }
.preview { flex:1; font-size:14px; color:var(--secondary-text-color); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; min-height:20px; }
.preview.typing { color:var(--accent); }
.badge { min-width:20px; height:20px; padding:0 6px; border-radius:10px; background:var(--accent); color:#fff; font-size:12px;
  display:flex; align-items:center; justify-content:center; font-weight:500; }
.mutedicon svg { width:16px; height:16px; fill:var(--secondary-text-color); display:block; }
.msgs { flex:1; overflow-y:auto; padding:8px 4% 12px; display:flex; flex-direction:column; }
.empty { margin:auto; color:var(--secondary-text-color); text-align:center; padding:24px; }
.day { position:sticky; top:6px; z-index:2; align-self:center; margin:10px 0 6px; padding:5px 12px; border-radius:8px; font-size:12.5px;
  background:var(--card-background-color); color:var(--secondary-text-color); box-shadow:0 1px .5px rgba(0,0,0,.13); }
.unreadbar { align-self:stretch; text-align:center; margin:8px -4%; padding:5px; font-size:12.5px;
  background:rgba(127,127,127,.18); color:var(--secondary-text-color); }
.event { align-self:center; margin:6px 0; padding:5px 12px; border-radius:8px; font-size:13px;
  background:var(--card-background-color); color:var(--secondary-text-color); }
.m { display:flex; margin:1px 0; position:relative; }
.m.first { margin-top:8px; }
.m.mine { justify-content:flex-end; }
.bubble { max-width:min(78%, 560px); padding:6px 8px 7px 9px; border-radius:8px; background:var(--theirs);
  box-shadow:0 1px .5px rgba(0,0,0,.13); position:relative; overflow-wrap:anywhere; }
.m.mine .bubble { background:var(--mine); }
.m.first:not(.mine) .bubble { border-top-left-radius:0; }
.m.first.mine .bubble { border-top-right-radius:0; }
.sender { font-size:12.8px; font-weight:500; margin-bottom:2px; }
.text { white-space:pre-wrap; font-size:14.5px; line-height:19px; }
.text a { color:var(--primary-color); }
.text.gone { font-style:italic; color:var(--secondary-text-color); }
.meta { float:right; margin:6px 0 -4px 10px; font-size:11px; color:var(--secondary-text-color); display:flex; align-items:center; gap:3px;
  position:relative; top:2px; }
.ticks { display:inline-flex; }
.ticks svg { width:15px; height:15px; fill:var(--secondary-text-color); }
.ticks.double svg + svg { margin-left:-9px; }
.ticks.read svg { fill:var(--tick-read); }
.ticks.failed svg { fill:var(--error-color, #db4437); }
.photo { display:block; max-width:100%; width:300px; max-height:340px; object-fit:cover; border-radius:6px; cursor:zoom-in;
  background:rgba(127,127,127,.2); min-height:120px; }
.more { position:absolute; top:2px; right:2px; width:24px; height:24px; border:none; border-radius:50%; cursor:pointer;
  background:inherit; color:var(--secondary-text-color); display:none; padding:0; }
.more svg { width:20px; height:20px; fill:currentColor; }
.bubble:hover .more, .more:focus { display:block; }
.m.failed .bubble { cursor:pointer; }
.composer { display:flex; align-items:flex-end; gap:6px; padding:6px 8px 8px; background:var(--card-background-color); flex-shrink:0; }
.pill { flex:1; min-width:0; display:flex; align-items:flex-end; background:var(--secondary-background-color); border-radius:24px;
  padding:0 4px; }
.pill .iconbtn { width:38px; height:42px; border-radius:50%; }
.composer textarea { flex:1; min-width:0; resize:none; border:none; outline:none; padding:11px 6px; font:inherit;
  font-size:15px; line-height:20px; height:42px; max-height:140px; overflow-y:hidden; scrollbar-width:thin;
  background:transparent; color:var(--primary-text-color); }
.app.narrow .composer textarea { scrollbar-width:none; }
.app.narrow .composer textarea::-webkit-scrollbar { display:none; }
.composer .iconbtn { color:var(--secondary-text-color); }
.composer .sendbtn { background:var(--accent); color:#fff; width:44px; height:44px; flex-shrink:0; }
.composer .sendbtn:hover { background:var(--accent); filter:brightness(1.08); }
.nudgebtn { font-size:21px; }
.notice { padding:8px 12px; text-align:center; font-size:13px; color:var(--secondary-text-color); background:var(--card-background-color); }
.toast { position:absolute; left:50%; bottom:76px; transform:translateX(-50%); background:rgba(0,0,0,.8); color:#fff; padding:8px 14px;
  border-radius:8px; font-size:13px; z-index:5; max-width:90%; }
.pickhint { margin:auto; color:var(--secondary-text-color); font-size:15px; }
.menu { position:absolute; z-index:6; background:var(--card-background-color); color:var(--primary-text-color); border-radius:8px;
  box-shadow:0 4px 18px rgba(0,0,0,.3); padding:6px 0; min-width:200px; }
.menu button { display:block; width:100%; text-align:left; background:none; border:none; padding:11px 18px; font:inherit;
  font-size:14.5px; color:inherit; cursor:pointer; }
.menu button:hover { background:var(--secondary-background-color); }
.overlay { position:fixed; inset:0; background:rgba(0,0,0,.85); display:flex; align-items:center; justify-content:center; z-index:9; }
.overlay img { max-width:96vw; max-height:92vh; }
.dialog { position:absolute; inset:0; background:rgba(0,0,0,.4); display:flex; align-items:center; justify-content:center; z-index:8; }
.dialog .box { background:var(--card-background-color); border-radius:12px; padding:20px; max-width:340px; margin:16px; }
.dialog .btns { display:flex; justify-content:flex-end; gap:8px; margin-top:16px; }
.dialog .btns button { background:none; border:none; color:var(--accent); font:inherit; font-weight:500; padding:8px 12px; cursor:pointer; }
.quote { border-left:4px solid var(--qc, var(--accent)); background:rgba(127,127,127,.14); border-radius:6px;
  padding:4px 8px; margin:0 0 4px; font-size:13px; cursor:pointer; overflow:hidden; }
.quote .qn { font-weight:500; color:var(--qc, var(--accent)); font-size:12.5px; }
.quote .qt { color:var(--secondary-text-color); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.reacts { display:flex; gap:4px; flex-wrap:wrap; margin:-4px 8px 4px; position:relative; z-index:1; }
.m.mine .reacts { justify-content:flex-end; }
.react { background:var(--card-background-color); border:1px solid var(--divider-color); border-radius:12px; padding:1px 7px;
  font-size:13px; cursor:pointer; color:var(--primary-text-color); font-family:inherit; }
.react.minec { border-color:var(--accent); }
.m { flex-wrap:wrap; }
.m .reacts { flex-basis:100%; }
.edited { font-style:italic; margin-right:3px; }
.flash .bubble { animation:flash 1.2s; }
@keyframes flash { 0%,60% { box-shadow:0 0 0 3px var(--accent); } 100% { box-shadow:0 1px .5px rgba(0,0,0,.13); } }
.bar { display:flex; align-items:center; gap:8px; padding:6px 10px 0; background:var(--card-background-color); }
.bar .quote { flex:1; margin:0; cursor:default; }
.emojirow { display:flex; justify-content:space-around; padding:4px 8px 8px; border-bottom:1px solid var(--divider-color); }
.emojirow button { font-size:22px; padding:4px 6px; width:auto; border-radius:50%; }
.tobottom { position:absolute; right:18px; bottom:76px; width:42px; height:42px; border-radius:50%; border:none; cursor:pointer;
  background:var(--card-background-color); color:var(--secondary-text-color); box-shadow:0 2px 6px rgba(0,0,0,.3); z-index:4;
  display:flex; align-items:center; justify-content:center; }
.tobottom svg { width:26px; height:26px; fill:currentColor; }
.tobottom .badge { position:absolute; top:-6px; right:-4px; }
.hint { margin:auto; color:var(--secondary-text-color); text-align:center; padding:24px; }
.emojis { position:absolute; left:8px; right:8px; bottom:64px; height:300px; max-height:45vh; z-index:6; display:flex;
  flex-direction:column; background:var(--card-background-color); border-radius:12px; box-shadow:0 4px 18px rgba(0,0,0,.35); }
.emojis .tabs { display:flex; border-bottom:1px solid var(--divider-color); padding:2px 4px; flex-shrink:0; }
.emojis .tabs button { flex:1; background:none; border:none; font-size:20px; padding:6px 0; cursor:pointer; opacity:.55;
  border-bottom:2px solid transparent; color:var(--secondary-text-color); }
.emojis .tabs button.on { opacity:1; border-bottom-color:var(--accent); }
.emojis .tabs svg { width:20px; height:20px; fill:currentColor; }
.emojis .grid { flex:1; overflow-y:auto; display:grid; grid-template-columns:repeat(auto-fill, minmax(40px, 1fr)); padding:6px; align-content:start; }
.emojis .grid button { background:none; border:none; font-size:26px; line-height:1; padding:6px 0; cursor:pointer; border-radius:8px;
  font-family:"Segoe UI Emoji","Apple Color Emoji","Noto Color Emoji",sans-serif; }
.emojis .grid button:hover { background:var(--secondary-background-color); }
.emojis .empty2 { grid-column:1/-1; color:var(--secondary-text-color); padding:16px; text-align:center; font-size:13px; }
@keyframes shake { 10%,90% { transform:translateX(-2px); } 20%,80% { transform:translateX(4px); }
  30%,50%,70% { transform:translateX(-8px); } 40%,60% { transform:translateX(8px); } }
.shake { animation:shake .7s cubic-bezier(.36,.07,.19,.97) 2; }
/* jh-r22-motion (step 164, review round 22 #8): with reduced motion a nudge and a found message give a still cue, no movement */
@media (prefers-reduced-motion: reduce) {
  .shake, .flash .bubble { animation:none; }
  .shake { outline:3px solid var(--accent); outline-offset:-3px; }
  .flash .bubble { box-shadow:0 0 0 3px var(--accent); }
}
/* Wide (PC) layout like WhatsApp Desktop/Web: chat list ~30% (340-500px), conversation pane full width,
   bubbles hug the pane edges with a side gutter of clamp(24px, 6%, 90px), max 65% / 640px wide.
   The gutter sits on the rows (not on .msgs) so the unread bar can still span the whole pane.
   Composer, reply bar, emoji panel and scroll button use the same gutter. Phone (.narrow) is untouched. */
.app:not(.narrow) .list { width:clamp(340px, 30%, 500px); flex-shrink:0; }
.app:not(.narrow) .msgs { padding-left:0; padding-right:0; }
.app:not(.narrow) .m, .app:not(.narrow) .event { margin-left:clamp(24px, 6%, 90px); margin-right:clamp(24px, 6%, 90px); }
.app:not(.narrow) .unreadbar { margin:8px 0; }
.app:not(.narrow) .bubble { max-width:min(65%, 640px); }
.app:not(.narrow) .composer { padding-left:clamp(24px, 6%, 90px); padding-right:clamp(24px, 6%, 90px); }
.app:not(.narrow) .bar { padding-left:clamp(24px, 6%, 90px); padding-right:clamp(24px, 6%, 90px); }
.app:not(.narrow) .emojis { left:clamp(24px, 6%, 90px); right:clamp(24px, 6%, 90px); }
.app:not(.narrow) .tobottom { right:clamp(24px, 6%, 90px); }
`;

const el = (tag, cls, text) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined && text !== null) e.textContent = text;
  return e;
};
const svg = (path) => {
  const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  s.setAttribute("viewBox", "0 0 24 24");
  const p = document.createElementNS("http://www.w3.org/2000/svg", "path");
  p.setAttribute("d", path);
  s.appendChild(p);
  return s;
};
const iconBtn = (path, title, cls) => {
  const b = el("button", "iconbtn" + (cls ? " " + cls : ""));
  b.type = "button";
  b.title = title;
  b.setAttribute("aria-label", title);
  b.appendChild(svg(path));
  return b;
};
const hue = (s) => {
  let h = 0;
  for (const c of s || "") h = (h * 31 + c.charCodeAt(0)) % 360;
  return h;
};
const URL_RE = /(https?:\/\/[^\s<>"']+)/g;

class HouseholdChatPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    try { window.jhHapticWire(this.shadowRoot); } catch (e) {}   // jh-haptic (step 156)
    this._people = new Map();
    this._convs = new Map();
    this._msgs = new Map();
    this._marks = new Map();
    this._more = new Map();
    this._typing = new Map();
    this._signed = new Map();
    this._pending = [];
    this._open = null;
    this._me = null;
    this._built = false;
    this._lastTyping = 0;
    this._onVisible = () => this._visibility();
    this._onReady = () => { this._reload(); for (const p of this._pending.filter((x) => x.failed)) this._retry(p); };
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    try { window.jhHapticLoad(hass); } catch (e) {}   // jh-haptic
    if (this._menuBtn) this._menuBtn.hass = hass;
    const dark = !!(hass.themes && hass.themes.darkMode);
    if (dark !== this.hasAttribute("dark")) this.toggleAttribute("dark", dark);
    if (first && this.isConnected) this._start();
  }
  get hass() { return this._hass; }
  set narrow(v) { this._narrow = !!v; if (this._menuBtn) this._menuBtn.narrow = this._narrow; this._layout(); requestAnimationFrame(() => this._fit()); }
  set route(v) { this._route = v; }
  set panel(v) { this._panelCfg = v; }

  connectedCallback() {
    document.addEventListener("visibilitychange", this._onVisible);
    this._onResize = () => this._fit();
    window.addEventListener("resize", this._onResize);
    if (window.visualViewport) window.visualViewport.addEventListener("resize", this._onResize);
    this._onPop = () => { if (this._pushed && this._narrow && this._open) { this._pushed = false; this._close(true); } };
    window.addEventListener("popstate", this._onPop);
    requestAnimationFrame(() => this._fit());
    // people added or removed in Home Assistant show up by themselves, also while the page stays open
    this._peopleTimer = setInterval(() => { if (document.visibilityState === "visible") this._refresh(); }, 120000);
    this._lastInput = Date.now();
    this._onTouch = () => this._touched();
    for (const ev of ["pointerdown", "keydown", "touchstart", "wheel"]) window.addEventListener(ev, this._onTouch, { passive: true });
    window.addEventListener("focus", this._onTouch);
    window.addEventListener("blur", this._onVisible);
    this._activeTimer = setInterval(() => { if (this._active() !== this._sentActive) this._sendView(); }, 30000);
    if (this._hass) this._start();
  }

  disconnectedCallback() {
    document.removeEventListener("visibilitychange", this._onVisible);
    window.removeEventListener("resize", this._onResize);
    if (window.visualViewport) window.visualViewport.removeEventListener("resize", this._onResize);
    window.removeEventListener("popstate", this._onPop);
    clearInterval(this._peopleTimer);
    clearInterval(this._activeTimer);
    for (const ev of ["pointerdown", "keydown", "touchstart", "wheel"]) window.removeEventListener(ev, this._onTouch);
    window.removeEventListener("focus", this._onTouch);
    window.removeEventListener("blur", this._onVisible);
    this._stop();
  }

  _fit() {
    // exactly the visible screen between the panel's top and the phone's bottom bar: Home Assistant pads the panel by the
    // phone's safe areas (status bar, navigation buttons), so that padding is left free; the typing bar stays above the
    // keyboard and the navigation buttons, and there is no page scroll bar
    const top = Math.max(0, this.getBoundingClientRect().top);
    let bottom = 0;
    try { bottom = parseFloat(getComputedStyle(this.parentElement).paddingBottom) || 0; } catch (e) { /* no parent yet */ }
    const view = window.visualViewport ? window.visualViewport.height : window.innerHeight;
    const keyboard = window.visualViewport ? Math.max(0, window.innerHeight - window.visualViewport.height) : 0;
    const h = view - top - (keyboard > 80 ? 0 : bottom);   // with the keyboard open the bottom bar is under the keyboard
    if (h > 200) this.style.height = Math.round(h) + "px";
  }

  get t() {
    const lang = ((this._hass && ((this._hass.locale && this._hass.locale.language) || this._hass.language)) || "en").split("-")[0];
    return T[lang] || T.en;
  }

  // ---------- life cycle
  async _start() {
    if (this._starting || this._unsub) return;
    this._starting = true;
    this._build();
    try {
      this._hass.connection.addEventListener("ready", this._onReady);
      this._unsub = await this._hass.connection.subscribeMessage((ev) => this._event(ev), { type: "household_chat/subscribe" });
      this._sendView();
      await this._reload();
    } catch (err) {
      this._showNotice(err && err.code === "not_loaded" ? "Household Chat is not loaded." : String((err && err.message) || err));
    } finally {
      this._starting = false;
    }
  }

  _stop() {
    if (this._hass) this._hass.connection.removeEventListener("ready", this._onReady);
    if (this._unsub) { try { this._unsub(); } catch (e) { /* closed */ } }
    this._unsub = null;
  }

  async _reload() {
    let data;
    try {
      data = await this._hass.callWS({ type: "household_chat/bootstrap" });
    } catch (err) {
      this._showNotice(err && err.code === "not_a_member" ? this.t.notMember : String((err && err.message) || err));
      return;
    }
    this._me = data.me;
    this._hideOnline = !!data.hide_online;
    this._jarvis = data.jarvis || null;
    this._reactions = data.reactions || ["\u{1F44D}", "\u2764\uFE0F", "\u{1F602}", "\u{1F62E}", "\u{1F622}", "\u{1F64F}"];
    this._groupTitle = data.group_title;
    this._people = new Map(data.people.map((p) => [p.id, p]));
    this._convs = new Map(data.convs.map((c) => [c.id, c]));
    this._showNotice(null);
    const wanted = new URLSearchParams(location.search).get("c");
    if (wanted && this._convs.has(wanted) && !this._deepLinked) {
      this._deepLinked = true;
      this._open = wanted;
    }
    if (!this._open && !this._narrow) this._open = GROUP;
    if (this._open && !this._convs.has(this._open)) this._open = null;
    this._renderList();
    if (this._open) await this._openConv(this._open, true);
    else this._renderConv();
    this._layout();
    this._loadTicks();
  }

  async _loadTicks() {
    // the ticks in the chat list need the others' delivered/read marks of chats whose last message is mine
    for (const conv of this._convs.values()) {
      if (!conv.last || conv.last.sender !== this._me || this._marks.has(conv.id)) continue;
      try {
        const res = await this._hass.callWS({ type: "household_chat/history", conv: conv.id });
        if (!this._msgs.has(conv.id)) { this._msgs.set(conv.id, res.messages); this._more.set(conv.id, res.has_more); }
        this._marks.set(conv.id, res.marks || {});
      } catch (e) { /* the ticks stay grey until the chat is opened */ }
    }
    this._renderList();
  }

  // ---------- events from the server
  _event(ev) {
    const conv = this._convs.get(ev.conv);
    if (ev.type === "message") {
      const m = ev.message;
      const list = this._msgs.get(ev.conv);
      if (list && !list.some((x) => x.id === m.id)) list.push(m);
      if (conv) {
        conv.last = m;
        if (m.sender !== this._me && !(this._open === ev.conv && this._seeing())) conv.unread = (conv.unread || 0) + 1;
      } else {
        this._reload();
        return;
      }
      this._typing.delete(ev.conv);
      if (m.kind === "nudge" && m.sender !== this._me) this._nudged(ev.conv);
      this._renderList();
      if (this._open === ev.conv) {
        const atBottom = this._seeing();
        if (!atBottom && m.sender !== this._me) this._newBelow = (this._newBelow || 0) + 1;
        this._renderMessages(m.sender === this._me ? "bottom" : "keep-or-bottom");
        this._markRead();
      }
    } else if (ev.type === "reaction") {
      const list = this._msgs.get(ev.conv);
      const msg = list && list.find((x) => x.id === ev.message_id);
      if (msg) { msg.reactions = ev.reactions; if (this._open === ev.conv) this._renderMessages("keep"); }
    } else if (ev.type === "hidden") {
      const list = this._msgs.get(ev.conv);
      if (list) this._msgs.set(ev.conv, list.filter((x) => x.id !== ev.message_id));
      if (conv && conv.last && conv.last.id === ev.message_id) { this._refresh(); }
      if (this._open === ev.conv) this._renderMessages("keep");
    } else if (ev.type === "update") {
      const list = this._msgs.get(ev.conv);
      if (list) { const i = list.findIndex((x) => x.id === ev.message.id); if (i >= 0) list[i] = ev.message; }
      if (conv && conv.last && conv.last.id === ev.message.id) conv.last = ev.message;
      this._renderList();
      if (this._open === ev.conv) this._renderMessages("keep");
    } else if (ev.type === "marks") {
      const marks = this._marks.get(ev.conv) || {};
      marks[ev.user] = { delivered: ev.delivered, read: ev.read };
      this._marks.set(ev.conv, marks);
      if (ev.user === this._me && conv) { conv.read = ev.read; if (ev.read >= ((conv.last && conv.last.id) || 0)) conv.unread = 0; this._renderList(); }
      if (this._open === ev.conv) this._updateTicks();
      else this._renderList();
    } else if (ev.type === "typing") {
      this._typing.set(ev.conv, { user: ev.user, until: Date.now() + 5000 });
      this._renderList();
      if (this._open === ev.conv) this._renderHeader();
      clearTimeout(this._typingTimer);
      this._typingTimer = setTimeout(() => { this._renderList(); if (this._open) this._renderHeader(); }, 5100);
    } else if (ev.type === "cleared") {
      this._msgs.delete(ev.conv);
      this._reload();
    } else if (ev.type === "presence") {
      const p = this._people.get(ev.user);
      if (p) {
        Object.assign(p, { online: ev.online, idle: !!ev.idle, last_seen: ev.last_seen, hidden: ev.hidden });
        this._renderList();
        const open = this._open && this._convs.get(this._open);
        if (open && open.kind === "direct" && open.with === ev.user) this._renderHeader();
      }
    } else if (ev.type === "muted" && conv) {
      conv.muted = ev.muted;
      this._renderList();
      if (this._open === ev.conv) this._renderHeader();
    }
  }

  _seeing() {
    if (document.visibilityState !== "visible") return false;
    const box = this._msgsBox;
    return !box || box.scrollHeight - box.scrollTop - box.clientHeight < PAGE_HINT;
  }

  _visibility() {
    this._sendView();
    if (document.visibilityState === "visible") { this._markRead(); this._refresh(); }
  }

  async _refresh() {
    // the people and chat list again (new or removed people, new chats); open chats keep their messages and scroll place
    if (!this._hass || !this._me || this._refreshing) return;
    this._refreshing = true;
    try {
      const data = await this._hass.callWS({ type: "household_chat/bootstrap" });
      this._people = new Map(data.people.map((p) => [p.id, p]));
      this._groupTitle = data.group_title;
      this._hideOnline = !!data.hide_online;
      this._jarvis = data.jarvis || null;
      this._convs = new Map(data.convs.map((c) => [c.id, c]));
      if (this._open && !this._convs.has(this._open)) { this._close(); return; }
      this._renderList();
      if (this._open) this._renderHeader();
      this._loadTicks();
    } catch (e) {
      /* offline for a moment: the next try comes by itself */
    } finally {
      this._refreshing = false;
    }
  }

  _active() {
    // in use = visible and touched in the last 2 minutes (or focused on a computer), like WhatsApp's "online"
    return document.visibilityState === "visible" && (Date.now() - (this._lastInput || 0) < 120000 || (document.hasFocus && document.hasFocus()));
  }

  _sendView() {
    if (!this._hass || !this._unsub) return;
    const active = this._active();
    this._sentActive = active;
    this._hass.callWS({ type: "household_chat/view", conv: active ? this._open : null, active }).catch(() => {});
  }

  _touched() {
    this._lastInput = Date.now();
    if (!this._sentActive) this._sendView();
  }

  // ---------- skeleton
  _build() {
    if (this._built) return;
    this._built = true;
    const root = this.shadowRoot;
    root.innerHTML = "";
    const style = el("style");
    style.textContent = STYLE;
    root.appendChild(style);
    this._app = el("div", "app");
    // list pane
    this._list = el("div", "list");
    const lh = el("header");
    if (customElements.get("ha-menu-button")) {
      this._menuBtn = document.createElement("ha-menu-button");
      this._menuBtn.hass = this._hass;
      this._menuBtn.narrow = this._narrow;
      lh.appendChild(this._menuBtn);
    } else {
      lh.appendChild(el("span", "pad"));
    }
    this._listTitle = el("div", "title", this.t.chats);
    lh.appendChild(this._listTitle);
    this._list.appendChild(lh);
    this._notice = el("div", "notice");
    this._notice.hidden = true;
    this._list.appendChild(this._notice);
    this._rows = el("div", "rows");
    this._list.appendChild(this._rows);
    // conversation pane
    this._conv = el("div", "conv");
    this._app.appendChild(this._list);
    this._app.appendChild(this._conv);
    root.appendChild(this._app);
    this._layout();
  }

  _layout() {
    if (!this._app) return;
    this._app.classList.toggle("narrow", !!this._narrow);
    this._app.classList.toggle("open", !!this._open);
  }

  _showNotice(text) {
    if (!this._notice) return;
    this._notice.hidden = !text;
    this._notice.textContent = text || "";
  }

  // ---------- helpers
  _name(uid) {
    if (uid === this._me) return this.t.you;
    if (uid === "jarvis") return "JARVIS";
    const p = this._people.get(uid);
    return p ? p.name : this.t.former;
  }

  _title(conv) {
    if (conv.kind === "group") return this._groupTitle || "Home";
    if (conv.kind === "jarvis") return "JARVIS";
    const p = this._people.get(conv.with);
    return p ? p.name : this.t.former;
  }

  _members(convId) {
    // the humans whose ticks count; in a JARVIS chat JARVIS himself reads the messages
    if (convId === GROUP) return [...this._people.keys()];
    if (convId.startsWith("j:")) return [this._me, "jarvis"];
    return convId.slice(2).split("|");
  }

  _avatar(conv) {
    const wrap = el("div", "av");
    wrap.appendChild(this._face(conv));
    const p = conv.kind === "direct" ? this._people.get(conv.with) : conv.kind === "jarvis" ? this._jarvis : null;
    // green: using the phone (or the chat open); orange: phone connected, not in use; grey: no connection (last seen)
    if (p && !p.hidden) wrap.appendChild(el("span", p.online ? "dot" : p.idle ? "dot idle" : "dot away"));
    return wrap;
  }

  _seen(p) {
    if (!p || p.hidden) return "";
    if (p.online) return this.t.online;
    let when = "";
    if (p.last_seen) {
      let day = this._dayLabel(p.last_seen);
      if (day === this.t.today || day === this.t.yesterday) day = day.toLowerCase();
      when = this.t.at(day, this._time(p.last_seen));
    }
    if (p.idle) return this.t.idle(when);
    return when ? this.t.lastSeen(when) : this.t.offline;
  }

  _face(conv) {
    const a = el("div", "avatar");
    if (conv.kind === "group") {
      a.style.background = "var(--accent)";
      a.appendChild(svg(ICONS.home));
      return a;
    }
    const p = conv.kind === "jarvis" ? this._jarvis : this._people.get(conv.with);
    if (p && p.picture) {
      const img = el("img");
      img.alt = "";
      img.src = p.picture;
      a.appendChild(img);
    } else {
      a.style.background = `hsl(${hue(conv.with)},45%,45%)`;
      a.textContent = ((p && p.name) || "?").trim().charAt(0).toUpperCase();
    }
    return a;
  }

  _lang() { return ((this._hass.locale && this._hass.locale.language) || this._hass.language || "en"); }

  _time(ts) {
    return new Date(ts * 1000).toLocaleTimeString(this._lang(), { hour: "2-digit", minute: "2-digit" });
  }

  _dayLabel(ts) {
    const d = new Date(ts * 1000);
    const today = new Date();
    const y = new Date(); y.setDate(today.getDate() - 1);
    const same = (a, b) => a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
    if (same(d, today)) return this.t.today;
    if (same(d, y)) return this.t.yesterday;
    const opts = today - d < 6 * 864e5 ? { weekday: "long" } : { day: "numeric", month: "long", year: "numeric" };
    return d.toLocaleDateString(this._lang(), opts);
  }

  _listWhen(ts) {
    const d = new Date(ts * 1000);
    const label = this._dayLabel(ts);
    return label === this.t.today ? this._time(ts) : (label === this.t.yesterday ? label : d.toLocaleDateString(this._lang(), { day: "numeric", month: "numeric" }));
  }

  _preview(conv) {
    const m = conv.last;
    if (!m) return "";
    if (m.kind === "nudge") return m.sender === this._me ? this.t.youNudged : this.t.nudged(this._name(m.sender));
    if (m.deleted) return m.sender === this._me ? this.t.youDeleted : this.t.deleted;
    let text = m.photo ? "\u{1F4F7} " + (m.text || this.t.photo) : m.text;
    if (conv.kind === "group" && m.sender !== this._me) text = this._name(m.sender) + ": " + text;
    return text;
  }

  _tickState(convId, m) {
    if (m.pending) return "pending";
    if (m.failed) return "failed";
    const others = this._members(convId).filter((u) => u !== this._me);
    if (!others.length) return "sent";
    const marks = this._marks.get(convId) || {};
    const conv = this._convs.get(convId);
    if (conv && conv.kind === "direct" && !marks[conv.with] && conv.theirMarks) Object.assign(marks, conv.theirMarks);
    const all = (key) => others.every((u) => marks[u] && marks[u][key] >= m.id);
    if (all("read")) return "read";
    if (all("delivered")) return "delivered";
    return "sent";
  }

  _ticks(state) {
    const t = el("span", "ticks");
    if (state === "pending") { t.appendChild(svg(ICONS.clock)); return t; }
    if (state === "failed") { t.classList.add("failed"); t.appendChild(svg(ICONS.alert)); return t; }
    t.appendChild(svg(ICONS.check));
    if (state !== "sent") { t.classList.add("double"); t.appendChild(svg(ICONS.check)); }
    if (state === "read") t.classList.add("read");
    return t;
  }

  _toast(text) {
    window.jhHaptic && window.jhHaptic("failure");   // jh-haptic: every chat toast is a refusal or a failure
    if (!this._conv) return;
    const old = this._conv.querySelector(".toast");
    if (old) old.remove();
    const t = el("div", "toast", text);
    this._conv.appendChild(t);
    setTimeout(() => t.remove(), 3500);
  }

  // ---------- chat list
  _renderList() {
    if (!this._rows) return;
    const convs = [...this._convs.values()];
    const pin = (c) => (c.last ? 0 : c.kind === "group" ? 2 : c.kind === "jarvis" ? 1 : 0);
    convs.sort((a, b) => {
      if (pin(a) || pin(b)) return pin(b) - pin(a);
      return ((b.last && b.last.ts) || 0) - ((a.last && a.last.ts) || 0) || this._title(a).localeCompare(this._title(b));
    });
    this._rows.textContent = "";
    for (const conv of convs) {
      const row = el("div", "row" + (conv.id === this._open ? " sel" : ""));
      row.setAttribute("role", "button");
      row.tabIndex = 0;
      row.appendChild(this._avatar(conv));
      const mid = el("div", "mid");
      const l1 = el("div", "line1");
      l1.appendChild(el("span", "name", this._title(conv)));
      if (conv.last) l1.appendChild(el("span", "when" + (conv.unread ? " new" : ""), this._listWhen(conv.last.ts)));
      const l2 = el("div", "line2");
      const typing = this._typing.get(conv.id);
      if (typing && typing.until > Date.now()) {
        l2.appendChild(el("span", "preview typing", conv.kind === "group" ? this.t.isTyping(this._name(typing.user)) : this.t.typing));
      } else {
        if (conv.last && conv.last.sender === this._me && conv.last.kind === "text" && !conv.last.deleted) {
          l2.appendChild(this._ticks(this._tickState(conv.id, conv.last)));
        }
        l2.appendChild(el("span", "preview", this._preview(conv)));
      }
      if (conv.muted) { const mi = el("span", "mutedicon"); mi.appendChild(svg(ICONS.muted)); l2.appendChild(mi); }
      if (conv.unread) l2.appendChild(el("span", "badge", String(conv.unread)));
      mid.appendChild(l1);
      mid.appendChild(l2);
      row.appendChild(mid);
      const go = () => this._openConv(conv.id);
      row.addEventListener("click", go);
      row.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
      this._rows.appendChild(row);
    }
  }

  // ---------- one conversation
  async _openConv(id, keepHistory) {
    const conv = this._convs.get(id);
    if (!conv) return;
    const changed = this._open !== id;
    this._open = id;
    if (changed && this._narrow && !this._pushed) {
      try { history.pushState(history.state, "", location.pathname + location.search); this._pushed = true; } catch (e) { /* no history */ }
    }
    this._layout();
    this._unreadFrom = changed || !this._msgs.has(id) ? ((conv.unread && conv.read) || null) : this._unreadFrom;
    this._unreadCount = conv.unread || 0;
    if (changed) this._draftFor(id);
    this._renderList();
    this._renderConv();
    if (!this._msgs.has(id) || keepHistory) {
      try {
        const res = await this._hass.callWS({ type: "household_chat/history", conv: id });
        this._msgs.set(id, res.messages);
        this._marks.set(id, res.marks || {});
        this._more.set(id, res.has_more);
      } catch (err) {
        this._toast(String((err && err.message) || err));
      }
    }
    if (this._open !== id) return;
    this._renderMessages("unread");
    this._sendView();
    this._markRead();
    if (!this._narrow && this._input) this._input.focus();
  }

  _draftFor(id) {
    this._drafts = this._drafts || {};
    if (this._input && this._draftConv) this._drafts[this._draftConv] = this._input.value;
    this._draftConv = id;
  }

  _renderConv() {
    const box = this._conv;
    if (!box) return;
    box.textContent = "";
    const conv = this._open && this._convs.get(this._open);
    if (!conv) {
      box.appendChild(el("div", "pickhint", this.t.pick));
      return;
    }
    this._header = el("header");
    box.appendChild(this._header);
    this._renderHeader();
    this._msgsBox = el("div", "msgs");
    this._msgsBox.addEventListener("scroll", () => this._onScroll());
    box.appendChild(this._msgsBox);
    const canSend = conv.kind === "group" || (conv.kind === "jarvis" ? !!this._jarvis : this._people.has(conv.with));
    if (!canSend) {
      box.appendChild(el("div", "notice", this.t.left));
      this._input = null;
      return;
    }
    const comp = el("div", "composer");
    const attach = iconBtn(ICONS.attach, this.t.attach);
    const file = el("input");
    file.type = "file";
    file.accept = "image/*";
    file.hidden = true;
    attach.addEventListener("click", () => file.click());
    file.addEventListener("change", () => { if (file.files && file.files[0]) this._sendPhoto(file.files[0]); file.value = ""; });
    const nudge = el("button", "iconbtn nudgebtn", "\u{1F44B}");
    nudge.type = "button";
    nudge.title = this.t.nudge;
    nudge.setAttribute("aria-label", this.t.nudge);
    nudge.addEventListener("click", () => this._nudge());
    this._input = el("textarea");
    this._input.rows = 1;
    this._input.placeholder = this.t.type;
    this._input.value = (this._drafts && this._drafts[conv.id]) || "";
    this._input.addEventListener("input", () => { this._grow(); this._typed(); });
    this._input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey && !this._narrow && !e.isComposing) { e.preventDefault(); this._sendText(); }
    });
    this._input.addEventListener("paste", (e) => {
      if (conv.kind === "jarvis") return;   // JARVIS reads text here: a pasted picture is not sent to him
      const item = [...((e.clipboardData && e.clipboardData.items) || [])].find((i) => i.type.startsWith("image/"));
      if (item) { e.preventDefault(); this._sendPhoto(item.getAsFile()); }
    });
    const send = iconBtn(ICONS.send, this.t.send, "sendbtn");
    send.addEventListener("click", () => this._sendText());
    const smile = iconBtn(ICONS.smile, this.t.emoji, "smilebtn");
    this._smileBtn = smile;
    smile.addEventListener("click", (e) => { e.stopPropagation(); this._toggleEmojis(); });
    this._input.addEventListener("keydown", (e) => { if (e.key === "Escape") this._closeEmojis(); });
    const pill = el("div", "pill");
    pill.appendChild(smile);
    pill.appendChild(this._input);
    if (conv.kind !== "jarvis") {
      pill.appendChild(nudge);
      pill.appendChild(attach);
      pill.appendChild(file);
    }
    comp.appendChild(pill);
    comp.appendChild(send);
    this._bar = el("div", "bar");
    this._bar.hidden = true;
    box.appendChild(this._bar);
    box.appendChild(comp);
    this._toBottom = el("button", "tobottom");
    this._toBottom.type = "button";
    this._toBottom.title = this.t.newMsgs;
    this._toBottom.appendChild(svg(ICONS.chevron));
    this._toBottom.hidden = true;
    this._toBottom.addEventListener("click", () => { this._newBelow = 0; this._msgsBox.scrollTop = this._msgsBox.scrollHeight; this._onScroll(); });
    box.appendChild(this._toBottom);
    if (this._replyTo && this._replyTo.conv !== conv.id) this._replyTo = null;
    if (this._editing && this._editing.conv !== conv.id) this._editing = null;
    this._renderBar();
    this._grow();
  }

  _renderHeader() {
    const h = this._header;
    const conv = this._open && this._convs.get(this._open);
    if (!h || !conv) return;
    h.textContent = "";
    if (this._narrow) {
      const back = iconBtn(ICONS.back, this.t.back);
      back.addEventListener("click", () => this._close());
      h.appendChild(back);
    } else {
      h.appendChild(el("span", "pad"));
    }
    h.appendChild(this._avatar(conv));
    const who = el("div", "who");
    who.appendChild(el("div", "title", this._title(conv)));
    const typing = this._typing.get(conv.id);
    let sub = "";
    if (typing && typing.until > Date.now()) sub = conv.kind === "group" ? this.t.isTyping(this._name(typing.user)) : this.t.typing;
    else if (conv.kind === "group") sub = [...this._people.values()].map((p) => (p.id === this._me ? this.t.you : p.name))
      .concat(this._jarvis ? ["JARVIS"] : []).join(", ");
    else if (conv.kind === "jarvis") sub = this.t.online;
    else sub = this._seen(this._people.get(conv.with));
    if (conv.muted) sub = (sub ? sub + " · " : "") + this.t.muted;
    if (sub) who.appendChild(el("div", "sub", sub));
    h.appendChild(who);
    const menu = iconBtn(ICONS.dots, this.t.menu);
    menu.addEventListener("click", (e) => {
      const rect = menu.getBoundingClientRect();
      const host = this._conv.getBoundingClientRect();
      this._menu([
        [conv.muted ? this.t.unmute : this.t.mute, () => this._hass.callWS({ type: "household_chat/mute", conv: conv.id, muted: !conv.muted })],
        [this.t.clear, () => this._confirm(this.t.clearAsk, () => this._hass.callWS({ type: "household_chat/clear", conv: conv.id }))],
        [this._hideOnline ? this.t.showOnline : this.t.hideOnline, async () => {
          await this._hass.callWS({ type: "household_chat/settings", hide_online: !this._hideOnline });
          this._hideOnline = !this._hideOnline;
        }],
      ], rect.right - host.left - 230, rect.bottom - host.top);
      e.stopPropagation();
    });
    h.appendChild(menu);
  }

  _close(fromBack) {
    if (!fromBack && this._pushed) { this._pushed = false; try { history.back(); } catch (e) { /* no history */ } }
    this._closeEmojis();
    this._draftFor(null);
    this._open = null;
    this._sendView();
    this._layout();
    this._renderList();
  }

  _renderMessages(scroll) {
    const box = this._msgsBox;
    const id = this._open;
    if (!box || !id) return;
    const fromBottom = box.scrollHeight - box.scrollTop;
    const wasAtBottom = box.scrollHeight - box.scrollTop - box.clientHeight < PAGE_HINT;
    const list = [...(this._msgs.get(id) || []), ...this._pending.filter((p) => p.conv === id)];
    const conv = this._convs.get(id);
    box.textContent = "";
    if (!list.length) {
      box.appendChild(el("div", "empty", conv && conv.kind === "jarvis" ? this.t.jarvisHint : this.t.nobody));
      return;
    }
    let lastDay = null;
    let prev = null;
    let unreadEl = null;
    for (const m of list) {
      const day = this._dayLabel(m.ts);
      if (day !== lastDay) { box.appendChild(el("div", "day", day)); lastDay = day; prev = null; }
      if (this._unreadFrom && !unreadEl && m.id > this._unreadFrom && m.sender !== this._me && !m.pending) {
        unreadEl = el("div", "unreadbar", this.t.unread(this._unreadCount || 1));
        box.appendChild(unreadEl);
        prev = null;
      }
      if (m.kind === "nudge") {
        box.appendChild(el("div", "event", "\u{1F44B} " + (m.sender === this._me ? this.t.youNudged : this.t.nudged(this._name(m.sender)))));
        prev = null;
        continue;
      }
      const mine = m.sender === this._me;
      const first = !prev || prev.sender !== m.sender || m.ts - prev.ts > 600;
      const row = el("div", "m" + (mine ? " mine" : "") + (first ? " first" : "") + (m.failed ? " failed" : ""));
      row.dataset.id = m.id || "";
      const b = el("div", "bubble");
      if (conv && conv.kind === "group" && !mine && first) {
        const s = el("div", "sender", this._name(m.sender));
        s.style.color = `hsl(${hue(m.sender)},55%,${this.hasAttribute("dark") ? 65 : 38}%)`;
        b.appendChild(s);
      }
      if (m.deleted) {
        b.appendChild(el("span", "text gone", mine ? this.t.youDeleted : this.t.deleted));
      } else {
        if (m.reply) b.appendChild(this._quote(m.reply, true));
        if (m.photo || m.localPhoto) b.appendChild(this._photo(m));
        if (m.text) b.appendChild(this._linkify(m.text));
      }
      const meta = el("span", "meta");
      if (m.edited && !m.deleted) meta.appendChild(el("span", "edited", this.t.edited));
      meta.appendChild(el("span", "", this._time(m.ts)));
      if (mine && !m.deleted) meta.appendChild(this._ticks(this._tickState(id, m)));
      b.appendChild(meta);
      if (!m.pending && !m.failed && m.id) {
        const more = el("button", "more");
        more.type = "button";
        more.setAttribute("aria-label", this.t.menu);
        more.appendChild(svg(ICONS.chevron));
        const open = (e) => {
          if (e.type === "contextmenu") window.jhHaptic && window.jhHaptic("medium", b);   // jh-haptic: a long press
          e.preventDefault();
          e.stopPropagation();
          const r = (e.type === "contextmenu" ? b : more).getBoundingClientRect();
          const host = this._conv.getBoundingClientRect();
          this._messageMenu(m, mine ? r.right - host.left - 230 : r.left - host.left, r.bottom - host.top);
        };
        more.addEventListener("click", open);
        b.addEventListener("contextmenu", open);   // right click, and a long press on a phone
        b.appendChild(more);
      }
      if (m.failed) { b.title = this.t.failed; b.addEventListener("click", () => this._retry(m)); }
      row.appendChild(b);
      const reacts = this._reactionChips(m);
      if (reacts) row.appendChild(reacts);
      box.appendChild(row);
      prev = m;
    }
    if (scroll === "older") box.scrollTop = box.scrollHeight - fromBottom;
    else if (scroll === "keep") box.scrollTop = box.scrollHeight - fromBottom;
    else if (scroll === "unread" && unreadEl) unreadEl.scrollIntoView({ block: "center" });
    else if (scroll === "bottom" || scroll === "unread" || (scroll === "keep-or-bottom" && wasAtBottom)) box.scrollTop = box.scrollHeight;
    else box.scrollTop = box.scrollHeight - fromBottom;
    this._stickDays();
  }
  // Every date pill is sticky in the same list, so the ones scrolled past all stay at the top on top of each other, and a wider
  // older one ("14 November 2026") showed around a narrower newer one ("Yesterday"). Only the newest stuck pill is shown (as in
  // WhatsApp); the others come back when scrolled to (review 2026-11-15).
  _stickDays() {
    const box = this._msgsBox;
    if (!box || !box.isConnected) return;
    // pills stuck at the top all sit at the same height: each one with the next pill at its height is covered by that one
    const days = [...box.querySelectorAll(":scope > .day")], tops = days.map((d) => d.getBoundingClientRect().top);
    days.forEach((d, i) => { d.style.visibility = i + 1 < days.length && Math.abs(tops[i + 1] - tops[i]) < 1.5 ? "hidden" : ""; });
  }

  _updateTicks() {
    if (!this._msgsBox || !this._open) return;
    const list = this._msgs.get(this._open) || [];
    for (const row of this._msgsBox.querySelectorAll(".m.mine")) {
      const m = list.find((x) => String(x.id) === row.dataset.id);
      const old = row.querySelector(".ticks");
      if (m && old && !m.deleted) old.replaceWith(this._ticks(this._tickState(this._open, m)));
    }
  }

  _linkify(text) {
    const span = el("span", "text");
    let last = 0;
    for (const match of text.matchAll(URL_RE)) {
      if (match.index > last) span.appendChild(document.createTextNode(text.slice(last, match.index)));
      const a = el("a", "", match[0]);
      a.href = match[0];
      a.target = "_blank";
      a.rel = "noopener noreferrer";
      span.appendChild(a);
      last = match.index + match[0].length;
    }
    if (last < text.length) span.appendChild(document.createTextNode(text.slice(last)));
    return span;
  }

  _photo(m) {
    const img = el("img", "photo");
    img.alt = this.t.photo;
    img.loading = "lazy";
    if (m.localPhoto) img.src = m.localPhoto;
    else this._signedUrl(m.photo).then((url) => { if (url) img.src = url; });
    img.addEventListener("load", () => { if (this._msgsBox && this._stickBottom) this._msgsBox.scrollTop = this._msgsBox.scrollHeight; });
    img.addEventListener("click", () => {
      const o = el("div", "overlay");
      const big = el("img");
      big.src = img.src;
      big.alt = this.t.photo;
      o.appendChild(big);
      o.addEventListener("click", () => o.remove());
      this.shadowRoot.appendChild(o);
    });
    return img;
  }

  async _signedUrl(mediaId) {
    const hit = this._signed.get(mediaId);
    if (hit && hit.until > Date.now()) return hit.url;
    try {
      const res = await this._hass.callWS({ type: "auth/sign_path", path: `/api/household_chat/media/${mediaId}`, expires: 3600 });
      this._signed.set(mediaId, { url: res.path, until: Date.now() + 3000 * 1000 });
      return res.path;
    } catch (e) {
      return null;
    }
  }

  async _onScroll() {
    const box = this._msgsBox;
    if (!box) return;
    this._stickDays();
    this._stickBottom = box.scrollHeight - box.scrollTop - box.clientHeight < PAGE_HINT;
    if (this._stickBottom) this._newBelow = 0;
    this._showToBottom();
    if (this._stickBottom) this._markRead();
    const id = this._open;
    if (box.scrollTop < 80 && this._more.get(id) && !this._loadingOlder) {
      const list = this._msgs.get(id) || [];
      if (!list.length) return;
      this._loadingOlder = true;
      try {
        const res = await this._hass.callWS({ type: "household_chat/history", conv: id, before: list[0].id });
        this._msgs.set(id, [...res.messages, ...(this._msgs.get(id) || [])]);
        this._more.set(id, res.has_more);
        if (this._open === id) this._renderMessages("older");
      } finally {
        this._loadingOlder = false;
      }
    }
  }

  _markRead() {
    const id = this._open;
    const conv = id && this._convs.get(id);
    if (!conv || !this._seeing()) return;
    const list = this._msgs.get(id) || [];
    const top = list.length ? list[list.length - 1].id : 0;
    if (!top || (conv.read || 0) >= top) { if (conv.unread) { conv.unread = 0; this._renderList(); } return; }
    conv.read = top;
    conv.unread = 0;
    this._renderList();
    this._hass.callWS({ type: "household_chat/read", conv: id, up_to: top }).catch(() => {});
  }

  _grow() {
    // grows line by line up to about 6 lines (like WhatsApp); only then it scrolls, so nothing jumps while typing
    const t = this._input;
    if (!t) return;
    const box = this._msgsBox;
    const atBottom = box && box.scrollHeight - box.scrollTop - box.clientHeight < PAGE_HINT;
    t.style.height = "42px";
    const want = Math.min(Math.max(t.scrollHeight, 42), 140);
    t.style.height = want + "px";
    t.style.overflowY = t.scrollHeight > 140 ? "auto" : "hidden";
    if (atBottom) box.scrollTop = box.scrollHeight;   // the newest message stays in view while the box grows
  }

  _typed() {
    const now = Date.now();
    if (!this._open || !this._input.value.trim() || now - this._lastTyping < 3000) return;
    this._lastTyping = now;
    this._hass.callWS({ type: "household_chat/typing", conv: this._open }).catch(() => {});
  }

  // ---------- sending
  _recent() {
    try { return JSON.parse(localStorage.getItem(RECENT_KEY) || "[]").slice(0, 32); } catch (e) { return []; }
  }

  _toggleEmojis() {
    if (this._emojiBox) { this._closeEmojis(); return; }
    const box = el("div", "emojis");
    const tabs = el("div", "tabs");
    const grid = el("div", "grid");
    const recent = this._recent();
    const groups = [[null, "recent", recent.join(" ")], ...EMOJI];
    const show = (i) => {
      [...tabs.children].forEach((b, j) => b.classList.toggle("on", i === j));
      grid.textContent = "";
      const list = groups[i][2].split(" ").filter(Boolean);
      if (!list.length) grid.appendChild(el("div", "empty2", this.t.noRecent));
      for (const e of list) {
        const b = el("button", "", e);
        b.type = "button";
        b.addEventListener("click", (ev) => { ev.stopPropagation(); this._insertEmoji(e); });
        grid.appendChild(b);
      }
      grid.scrollTop = 0;
    };
    groups.forEach((g, i) => {
      const b = el("button", "", g[0] || "");
      b.type = "button";
      b.title = this.t.emojiGroups[g[1]] || g[1];
      if (!g[0]) b.appendChild(svg(ICONS.clockrecent));
      b.addEventListener("click", (ev) => { ev.stopPropagation(); show(i); });
      tabs.appendChild(b);
    });
    box.appendChild(tabs);
    box.appendChild(grid);
    box.addEventListener("click", (e) => e.stopPropagation());
    this._conv.appendChild(box);
    this._emojiBox = box;
    show(recent.length ? 0 : 1);
    this._emojiAway = (e) => { const path = e.composedPath(); if (!path.includes(box) && !path.includes(this._smileBtn)) this._closeEmojis(); };
    setTimeout(() => document.addEventListener("click", this._emojiAway, true), 0);
  }

  _closeEmojis() {
    if (this._emojiBox) this._emojiBox.remove();
    this._emojiBox = null;
    if (this._emojiAway) document.removeEventListener("click", this._emojiAway, true);
  }

  _insertEmoji(e) {
    const t = this._input;
    if (!t) return;
    const a = t.selectionStart ?? t.value.length, b = t.selectionEnd ?? t.value.length;
    t.setRangeText(e, a, b, "end");
    t.dispatchEvent(new Event("input"));
    if (!this._narrow) t.focus();
    try {
      const list = [e, ...this._recent().filter((x) => x !== e)].slice(0, 32);
      localStorage.setItem(RECENT_KEY, JSON.stringify(list));
    } catch (err) { /* private window: no recent list */ }
  }

  async _sendText() {
    this._closeEmojis();
    const text = this._input ? this._input.value.trim() : "";
    if (!text || !this._open) return;
    this._input.value = "";
    if (this._drafts) delete this._drafts[this._open];
    this._grow();
    this._input.focus();
    if (this._editing) {
      const id = this._editing.msg.id;
      this._editing = null;
      this._renderBar();
      try { await this._hass.callWS({ type: "household_chat/edit", message_id: id, text }); }
      catch (err) { this._toast(String((err && err.message) || err)); }
      return;
    }
    const reply = this._replyTo && this._replyTo.conv === this._open ? this._replyTo.msg : null;
    this._replyTo = null;
    this._renderBar();
    await this._deliver({ conv: this._open, text, reply_to: reply ? reply.id : null, reply: reply ? this._snippet(reply) : null });
  }

  _snippet(m) {
    return { id: m.id, sender: m.sender, text: (m.text || "").slice(0, 160), photo: !!m.photo, deleted: !!m.deleted, kind: m.kind };
  }

  _quote(r, clickable) {
    const q = el("div", "quote");
    q.style.setProperty("--qc", r.sender === this._me ? "var(--accent)" : `hsl(${hue(r.sender)},55%,${this.hasAttribute("dark") ? 65 : 38}%)`);
    q.appendChild(el("div", "qn", r.unavailable ? "" : this._name(r.sender)));
    q.appendChild(el("div", "qt", r.unavailable ? "\u2026" : r.deleted ? this.t.deleted : r.photo ? "\u{1F4F7} " + (r.text || this.t.photo) : r.text));
    if (clickable) q.addEventListener("click", (e) => { e.stopPropagation(); this._jumpTo(r.id); });
    return q;
  }

  _jumpTo(id) {
    const row = this._msgsBox && this._msgsBox.querySelector(`.m[data-id="${id}"]`);
    if (!row) return;
    row.scrollIntoView({ block: "center" });
    row.classList.remove("flash");
    void row.offsetWidth;
    row.classList.add("flash");
  }

  _renderBar() {
    const bar = this._bar;
    if (!bar) return;
    bar.textContent = "";
    const job = this._editing ? { label: this.t.editing, msg: this._editing.msg } : this._replyTo ? { label: null, msg: this._replyTo.msg } : null;
    bar.hidden = !job;
    if (!job) return;
    const q = this._quote(this._snippet(job.msg), false);
    if (job.label) q.querySelector(".qn").textContent = job.label;
    bar.appendChild(q);
    const x = iconBtn("M19,6.41L17.59,5L12,10.59L6.41,5L5,6.41L10.59,12L5,17.59L6.41,19L12,13.41L17.59,19L19,17.59L13.41,12L19,6.41Z", this.t.cancel);
    x.addEventListener("click", () => {
      if (this._editing && this._input) this._input.value = "";
      this._editing = null;
      this._replyTo = null;
      this._renderBar();
      this._grow();
    });
    bar.appendChild(x);
  }

  _reactionChips(m) {
    const all = Object.entries(m.reactions || {});
    if (!all.length || m.deleted) return null;
    const wrap = el("div", "reacts");
    const counts = new Map();
    for (const [, e] of all) counts.set(e, (counts.get(e) || 0) + 1);
    const mineE = (m.reactions || {})[this._me];
    for (const [e, n] of counts) {
      const b = el("button", "react" + (e === mineE ? " minec" : ""), n > 1 ? `${e} ${n}` : e);
      b.type = "button";
      b.title = all.filter(([, x]) => x === e).map(([u]) => this._name(u)).join(", ");
      b.addEventListener("click", () => this._react(m, e));
      wrap.appendChild(b);
    }
    return wrap;
  }

  _react(m, emoji) {
    this._hass.callWS({ type: "household_chat/react", message_id: m.id, emoji }).catch((err) => this._toast(String((err && err.message) || err)));
  }

  _messageMenu(m, x, y) {
    const mine = m.sender === this._me;
    const items = [];
    if (!m.deleted && m.kind === "text") {
      const row = el("div", "emojirow");
      for (const e of this._reactions) {
        const b = el("button", "", e);
        b.type = "button";
        b.addEventListener("click", () => { row.closest(".menu").remove(); this._react(m, e); });
        row.appendChild(b);
      }
      items.push(row);
      items.push([this.t.reply, () => { this._editing = null; this._replyTo = { conv: this._open, msg: m }; this._renderBar(); if (this._input) this._input.focus(); }]);
      if (mine && m.text && Date.now() / 1000 - m.ts < 15 * 60) {
        items.push([this.t.edit, () => {
          this._replyTo = null;
          this._editing = { conv: this._open, msg: m };
          if (this._input) { this._input.value = m.text; this._grow(); this._input.focus(); }
          this._renderBar();
        }]);
      }
      if (mine) items.push([this.t.del, () => this._confirm(this.t.delAsk, () => this._hass.callWS({ type: "household_chat/delete", message_id: m.id }))]);
    }
    items.push([this.t.delMe, () => this._hass.callWS({ type: "household_chat/hide", message_id: m.id })]);
    this._menu(items, x, y);
  }

  _showToBottom() {
    const b = this._toBottom;
    if (!b || !this._msgsBox) return;
    const box = this._msgsBox;
    const far = box.scrollHeight - box.scrollTop - box.clientHeight > 300;
    b.hidden = !far && !this._newBelow;
    const old = b.querySelector(".badge");
    if (old) old.remove();
    if (this._newBelow) b.appendChild(el("span", "badge", String(this._newBelow)));
  }

  async _deliver(job) {
    const local = { pending: true, conv: job.conv, sender: this._me, ts: Date.now() / 1000, kind: "text", text: job.text || "",
      localPhoto: job.preview || null, reply: job.reply || null, job, key: Math.random() };
    this._pending.push(local);
    if (this._open === job.conv) this._renderMessages("bottom");
    try {
      let photo = null;
      if (job.blob) photo = await this._upload(job.conv, job.blob);
      const msg = await this._hass.callWS({ type: "household_chat/send", conv: job.conv, text: job.text || "", ...(photo ? { photo } : {}),
        ...(job.reply_to ? { reply_to: job.reply_to } : {}) });
      this._pending = this._pending.filter((p) => p !== local);
      window.jhHaptic && window.jhHaptic("success");   // jh-haptic: the server has it
      const list = this._msgs.get(job.conv);
      if (list && !list.some((x) => x.id === msg.id)) list.push(msg);
      const conv = this._convs.get(job.conv);
      if (conv) conv.last = msg;
      this._renderList();
    } catch (err) {
      local.pending = false;
      local.failed = true;
      window.jhHaptic && window.jhHaptic("failure");   // jh-haptic
      if (err && err.message === "too_large") this._toast(this.t.tooBig);
    }
    if (this._open === job.conv) this._renderMessages("bottom");
  }

  _retry(m) {
    this._pending = this._pending.filter((p) => p !== m);
    this._deliver(m.job);
  }

  async _sendPhoto(file) {
    if (!file || !this._open) return;
    const conv = this._open;
    const text = this._input ? this._input.value.trim() : "";
    if (this._input) { this._input.value = ""; this._grow(); }
    let blob = file;
    try { blob = await this._shrink(file); } catch (e) { /* send the original */ }
    if (blob.size > 8 * 1024 * 1024) { this._toast(this.t.tooBig); return; }
    await this._deliver({ conv, text, blob, preview: URL.createObjectURL(blob) });
  }

  async _shrink(file) {
    if (file.type === "image/gif") return file;
    const bitmap = await createImageBitmap(file);
    const scale = Math.min(1, 1600 / Math.max(bitmap.width, bitmap.height));
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(bitmap.width * scale);
    canvas.height = Math.round(bitmap.height * scale);
    canvas.getContext("2d").drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    return await new Promise((resolve, reject) => canvas.toBlob((b) => (b ? resolve(b) : reject(new Error("no blob"))), "image/jpeg", 0.85));
  }

  async _upload(conv, blob) {
    const path = `/api/household_chat/upload?conv=${encodeURIComponent(conv)}`;
    const init = { method: "POST", body: blob, headers: { "Content-Type": blob.type || "image/jpeg" } };
    let res;
    if (this._hass.fetchWithAuth) res = await this._hass.fetchWithAuth(path, init);
    else {
      init.headers.Authorization = "Bearer " + this._hass.auth.data.access_token;
      res = await fetch(path, init);
    }
    if (res.status === 413) throw new Error("too_large");
    if (!res.ok) throw new Error("upload " + res.status);
    return (await res.json()).id;
  }

  async _nudge() {
    if (!this._open) return;
    try {
      await this._hass.callWS({ type: "household_chat/nudge", conv: this._open });
      window.jhHaptic && window.jhHaptic("success");   // jh-haptic
      this._shake();
    } catch (err) {
      this._toast(err && err.code === "too_soon" ? this.t.tooSoon : String((err && err.message) || err));
    }
  }

  _nudged(convId) {
    // jh-r22-motion (step 164, review round 22 #8): the buzz goes through the shared haptics rule (this login's haptics switch,
    // touch screens only, never with prefers-reduced-motion), not straight to navigator.vibrate
    try { if (window.jhHaptic) window.jhHaptic("warning", this._app); } catch (e) { /* no haptics */ }
    if (this._open === convId || !this._open) this._shake();
  }

  _shake() {
    if (!this._app) return;
    this._app.classList.remove("shake");
    void this._app.offsetWidth;
    this._app.classList.add("shake");
    setTimeout(() => this._app && this._app.classList.remove("shake"), 1500);
  }

  // ---------- small menus and questions
  _menu(items, x, y) {
    const old = this._conv.querySelector(".menu");
    if (old) old.remove();
    const m = el("div", "menu");
    m.style.left = Math.max(8, x) + "px";
    m.style.top = y + "px";
    for (const item of items) {
      if (item instanceof HTMLElement) { m.appendChild(item); continue; }
      const [label, fn] = item;
      const b = el("button", "", label);
      b.type = "button";
      b.addEventListener("click", () => { m.remove(); Promise.resolve(fn()).catch((err) => this._toast(String((err && err.message) || err))); });
      m.appendChild(b);
    }
    this._conv.appendChild(m);
    const away = (e) => { if (!e.composedPath().includes(m)) { m.remove(); document.removeEventListener("click", away, true); } };
    setTimeout(() => document.addEventListener("click", away, true), 0);
  }

  _confirm(text, fn) {
    window.jhHaptic && window.jhHaptic("warning");   // jh-haptic: a destructive confirmation
    const d = el("div", "dialog");
    const box = el("div", "box");
    box.appendChild(el("div", "", text));
    const btns = el("div", "btns");
    const no = el("button", "", this.t.cancel);
    const yes = el("button", "", this.t.ok);
    no.type = yes.type = "button";
    no.addEventListener("click", () => d.remove());
    yes.addEventListener("click", () => { d.remove(); Promise.resolve(fn()).catch((err) => this._toast(String((err && err.message) || err))); });
    btns.appendChild(no);
    btns.appendChild(yes);
    box.appendChild(btns);
    d.appendChild(box);
    this._conv.appendChild(d);
  }
}

if (!customElements.get("household-chat-panel")) customElements.define("household-chat-panel", HouseholdChatPanel);
