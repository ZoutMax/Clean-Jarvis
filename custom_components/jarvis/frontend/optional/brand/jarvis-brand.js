/* Clean Jarvis optional look "JARVIS logo" (optional_looks.py): the JARVIS logo at the top of the sidebar, on the loading
 * screen and on every Home Assistant logo picture inside the app. Added to every page with Home Assistant's add_extra_js_url
 * while the switch is on. Review round 8: only JARVIS's own files are used (the browser tab's icon too), never Home Assistant's
 * /static/ logo addresses, which its service worker keeps cache-first. Nothing of Home Assistant's page is replaced: if what
 * this looks for is renamed, it simply finds nothing. */
(() => {
  if (window.__jhBrand) return;
  window.__jhBrand = true;
  const BRAND = "/jarvis_panel_static/optional/brand/";
  const LOGO = BRAND + "logo.svg";
  const HA_ICON = /\/static\/(icons\/(favicon|logo|mask-icon|tile-win)|images\/home-assistant-logo)/;

  const img = (size) => {
    const i = document.createElement("img");
    i.src = LOGO;
    i.alt = "JARVIS";
    i.dataset.jarvis = "1";
    i.style.cssText = `width:${size}px;height:${size}px;border-radius:50%;object-fit:cover;`;
    return i;
  };

  // the loading screen in index.html draws the Home Assistant logo as an inline svg
  const launch = () => {
    const screen = document.getElementById("ha-launch-screen");
    if (!screen || screen.dataset.jarvis) return;
    const svg = screen.querySelector("svg");
    if (!svg) return;
    screen.dataset.jarvis = "1";
    const box = svg.getBoundingClientRect();
    svg.replaceWith(img(Math.round(Math.max(box.width, box.height)) || 112));
  };

  // every picture of the Home Assistant logo inside the app (the "Unable to connect" screen, About page, ...)
  const pictures = (root, depth) => {
    if (!root || depth > 12) return;
    for (const i of root.querySelectorAll("img")) {
      if (!i.dataset.jarvis && HA_ICON.test(i.getAttribute("src") || "")) {
        i.dataset.jarvis = "1";
        i.src = LOGO;
        i.style.borderRadius = "50%";
      }
    }
    for (const e of root.querySelectorAll("*")) if (e.shadowRoot) pictures(e.shadowRoot, depth + 1);
  };

  // the browser tab's icon (and a home-screen icon added from now on): JARVIS's own files, sized as optional_looks.logo_for
  const icons = () => {
    for (const l of document.querySelectorAll('link[rel~="icon"], link[rel="apple-touch-icon"], link[rel="mask-icon"]')) {
      if (l.dataset.jarvis) continue;
      const n = parseInt((l.getAttribute("sizes") || "").split("x")[0], 10) || 0;
      l.dataset.jarvis = "1";
      l.href = BRAND + (l.rel === "mask-icon" ? "logo.svg" : l.rel === "apple-touch-icon" ? "logo-apple-180.png"
        : n > 192 ? "logo-512.png" : n > 48 ? "logo-192.png" : n > 32 ? "logo-48.png" : "logo-32.png");
    }
  };

  // a small logo before the title at the top of the sidebar
  const sidebar = () => {
    const main = document.querySelector("home-assistant")?.shadowRoot?.querySelector("home-assistant-main");
    const bar = main?.shadowRoot?.querySelector("ha-sidebar")?.shadowRoot;
    const title = bar?.querySelector(".title");
    if (!title || title.querySelector("img[data-jarvis]")) return;
    const logo = img(26);
    logo.style.marginRight = "10px";
    logo.style.verticalAlign = "middle";
    title.prepend(logo);
  };

  // cheap checks often; the deep search for logo pictures only while the app starts and after moving to another page
  const light = () => { try { launch(); sidebar(); icons(); } catch (e) { /* next round */ } };
  const deep = () => { light(); try { pictures(document, 0); } catch (e) { /* next round */ } };
  light();
  window.addEventListener("location-changed", () => setTimeout(deep, 400));
  let rounds = 0;
  const start = setInterval(() => { deep(); if (++rounds > 30) clearInterval(start); }, 1000);
  setInterval(light, 5000);
})();
