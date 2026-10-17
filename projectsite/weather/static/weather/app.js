(() => {
  "use strict";
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));
  const dashboard = $("#dashboard");
  const form = $("#search-form");
  const cityInput = $("#city-input");
  const toastEl = $("#toast");
  const root = document.documentElement;

  const store = {
    get(key) { try { return localStorage.getItem(key); } catch (_) { return null; } },
    set(key, value) { try { localStorage.setItem(key, value); } catch (_) { /* private mode */ } },
    remove(key) { try { localStorage.removeItem(key); } catch (_) { /* private mode */ } },
  };

  /* ---------------- Toast ---------------- */
  let toastTimer;
  function toast(message) {
    toastEl.textContent = message;
    toastEl.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toastEl.classList.remove("show"), 5000);
  }

  /* ---------------- Theme: system / light / dark ---------------- */
  const darkQuery = window.matchMedia("(prefers-color-scheme: dark)");
  function applyTheme(pref) {
    const dark = pref === "dark" || (pref === "system" && darkQuery.matches);
    root.setAttribute("data-theme", dark ? "dark" : "light");
    root.setAttribute("data-theme-pref", pref);
    $$("[data-theme-choice]").forEach((button) =>
      button.setAttribute("aria-pressed", String(button.dataset.themeChoice === pref)));
  }
  const savedTheme = store.get("ow-theme");
  applyTheme(savedTheme === "light" || savedTheme === "dark" ? savedTheme : "system");
  $$("[data-theme-choice]").forEach((button) => button.addEventListener("click", () => {
    store.set("ow-theme", button.dataset.themeChoice);
    applyTheme(button.dataset.themeChoice);
  }));
  const onSystemChange = () => { if (root.getAttribute("data-theme-pref") === "system") applyTheme("system"); };
  darkQuery.addEventListener ? darkQuery.addEventListener("change", onSystemChange) : darkQuery.addListener(onSystemChange);

  /* ---------------- Units ---------------- */
  function currentUnits() { return dashboard.dataset.units === "imperial" ? "imperial" : "metric"; }
  function syncUnitButtons() {
    $$("[data-unit]").forEach((button) =>
      button.setAttribute("aria-pressed", String(button.dataset.unit === currentUnits())));
  }
  function rememberUnits(units) {
    store.set("ow-units", units);
    document.cookie = `ow_units=${units}; Max-Age=31536000; Path=/; SameSite=Lax`;
  }
  $$("[data-unit]").forEach((button) => button.addEventListener("click", () => {
    const units = button.dataset.unit;
    if (units === currentUnits()) return;
    rememberUnits(units);
    const params = new URLSearchParams(location.search);
    params.set("units", units);
    params.delete("refresh");
    load(`${location.pathname}?${params}`);
  }));

  /* ---------------- City-local clock and "updated" text ---------------- */
  function tickClock() {
    const time = $("#city-time");
    const date = $("#city-date");
    if (time) {
      const shifted = new Date(Date.now() + Number(time.dataset.offset || 0) * 1000);
      time.textContent = shifted.toLocaleTimeString([], { hour: "numeric", minute: "2-digit", timeZone: "UTC" });
      if (date) date.textContent = shifted.toLocaleDateString([], { weekday: "short", day: "numeric", month: "short", timeZone: "UTC" });
    }
    const updated = $("#updated-text");
    if (updated && updated.dataset.fetched) {
      const fetched = new Date(updated.dataset.fetched);
      if (!Number.isNaN(fetched.getTime())) {
        const minutes = Math.floor((Date.now() - fetched.getTime()) / 60000);
        const clock = fetched.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
        updated.textContent = minutes < 1 ? `Updated just now (${clock})`
          : minutes < 60 ? `Updated ${minutes} min ago (${clock})` : `Updated at ${clock}`;
      }
    }
  }
  setInterval(tickClock, 15000);

  /* ---------------- Recent places ---------------- */
  const RECENT_KEY = "ow-recent";
  function getRecent() {
    try {
      const list = JSON.parse(store.get(RECENT_KEY) || "[]");
      return Array.isArray(list) ? list.filter((p) => p && typeof p.label === "string"
        && Number.isFinite(p.lat) && Number.isFinite(p.lon)).slice(0, 5) : [];
    } catch (_) { return []; }
  }
  function saveRecent(place) {
    const list = [place, ...getRecent().filter((p) => p.label !== place.label)].slice(0, 5);
    store.set(RECENT_KEY, JSON.stringify(list));
    renderRecent();
  }
  function renderRecent() {
    const list = getRecent();
    const wrap = $("#recent");
    const ul = $("#recent-list");
    ul.replaceChildren();
    wrap.hidden = list.length === 0;
    list.forEach((place) => {
      const li = document.createElement("li");
      const button = document.createElement("button");
      button.type = "button";
      button.className = "chip";
      button.textContent = place.label;
      button.title = `Show weather for ${place.label}`;
      // Recent places reopen by their exact coordinates, so they always resolve to the same spot.
      button.addEventListener("click", () => load(coordsUrl(place.lat, place.lon)));
      li.appendChild(button);
      ul.appendChild(li);
    });
  }
  $("#clear-recent").addEventListener("click", () => {
    store.remove(RECENT_KEY);
    renderRecent();
    toast("Recent searches cleared.");
  });

  /* ---------------- Loading and navigation ---------------- */
  let controller = null;
  let failedRequest = null;   // the attempt that never reached the server, so "Try again" repeats it
  let lastUrl = location.pathname + location.search;

  function setBusy(busy, message) {
    dashboard.classList.toggle("is-loading", busy);
    dashboard.setAttribute("aria-busy", String(busy));
    document.body.classList.toggle("is-busy", busy);
    $("#loading").hidden = !busy;
    if (message) $("#loading-text").textContent = message;
    $("#search-button").disabled = busy;
    $("#location-button").disabled = busy;
  }

  function coordsUrl(lat, lon) {
    const params = new URLSearchParams({ lat: lat.toFixed(4), lon: lon.toFixed(4), units: currentUnits() });
    return `${location.pathname}?${params}`;
  }

  function afterRender() {
    syncUnitButtons();
    if (dashboard.dataset.cityValue !== undefined && !new URLSearchParams(location.search).has("lat")) {
      cityInput.value = dashboard.dataset.cityValue;
    } else if (new URLSearchParams(location.search).has("lat")) {
      cityInput.value = "";
    }
    const data = $("#recent-data");
    if (data) {
      try {
        const place = JSON.parse(data.textContent);
        if (place && typeof place.label === "string") saveRecent(place);
      } catch (_) { /* ignore */ }
    }
    tickClock();
  }

  async function load(url, { push = true, shownUrl = url, message } = {}) {
    if (controller) controller.abort();
    controller = new AbortController();
    setBusy(true, message || "Getting the latest weather…");
    try {
      const response = await fetch(url, { signal: controller.signal, headers: { "X-Requested-With": "fetch" }, credentials: "same-origin" });
      const html = await response.text();
      const doc = new DOMParser().parseFromString(html, "text/html");
      const fresh = $("#dashboard", doc);
      if (!fresh) { location.href = url; return; }   // unexpected page: fall back to a normal navigation
      failedRequest = null;
      dashboard.innerHTML = fresh.innerHTML;
      dashboard.dataset.units = fresh.dataset.units;
      dashboard.dataset.cityValue = fresh.dataset.cityValue;
      document.title = doc.title;
      if (push && shownUrl !== lastUrl) history.pushState({}, "", shownUrl);
      else if (!push) history.replaceState({}, "", shownUrl);
      lastUrl = shownUrl;
      afterRender();
      dashboard.focus({ preventScroll: true });
    } catch (error) {
      if (error.name === "AbortError") return;
      failedRequest = { url, options: { push, shownUrl, message } };
      dashboard.replaceChildren($("#tpl-offline").content.cloneNode(true));
    } finally {
      if (!controller || !controller.signal.aborted) setBusy(false);
    }
  }

  window.addEventListener("popstate", () => {
    lastUrl = location.pathname + location.search;
    load(lastUrl, { push: false, shownUrl: lastUrl });
  });

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const city = cityInput.value.trim();
    if (!city) { cityInput.focus(); toast("Enter a city name to search."); return; }
    const params = new URLSearchParams({ city, units: currentUnits() });
    load(`${location.pathname}?${params}`);
  });

  $("#refresh-button").addEventListener("click", () => {
    const params = new URLSearchParams(location.search);
    params.set("units", currentUnits());
    params.delete("refresh");
    const shown = `${location.pathname}${params.toString() ? "?" + params : ""}`;
    params.set("refresh", "1");
    load(`${location.pathname}?${params}`, { push: false, shownUrl: shown, message: "Refreshing…" });
  });

  dashboard.addEventListener("click", (event) => {
    if (!event.target.closest("[data-action='retry']")) return;
    if (failedRequest) load(failedRequest.url, failedRequest.options);
    else $("#refresh-button").click();
  });

  /* ---------------- Geolocation ---------------- */
  const locationButton = $("#location-button");
  const locationLabel = $("#location-label");
  locationButton.addEventListener("click", () => {
    if (!("geolocation" in navigator)) {
      toast("This browser can't share your location. Search for a city instead.");
      return;
    }
    if (window.isSecureContext === false) {
      toast("Location needs a secure page (HTTPS or localhost). Search for a city instead.");
      return;
    }
    locationButton.disabled = true;
    locationLabel.textContent = "Finding you…";
    navigator.geolocation.getCurrentPosition(
      (position) => {
        locationLabel.textContent = "Use my location";
        // Latitude and longitude go to the server as-is; the weather is fetched by coordinates, not by name.
        load(coordsUrl(position.coords.latitude, position.coords.longitude), { message: "Getting weather for your location…" });
      },
      (error) => {
        locationLabel.textContent = "Use my location";
        locationButton.disabled = false;
        const messages = {
          1: "Location access was blocked. Allow it in your browser's site settings, or search for a city.",
          2: "Your device couldn't work out where you are. Try again, or search for a city.",
          3: "Finding your location took too long. Try again, or search for a city.",
        };
        toast(messages[error.code] || "Couldn't get your location. Search for a city instead.");
      },
      { enableHighAccuracy: false, timeout: 10000, maximumAge: 300000 }
    );
  });

  /* ---------------- Start ---------------- */
  if (!document.cookie.includes("ow_units=") && store.get("ow-units")) {
    // Cookie missing but a preference was saved: reuse it for the next request, and now if it differs.
    const saved = store.get("ow-units");
    if ((saved === "metric" || saved === "imperial")) {
      rememberUnits(saved);
      if (saved !== currentUnits() && !new URLSearchParams(location.search).has("units")) {
        const params = new URLSearchParams(location.search);
        params.set("units", saved);
        load(`${location.pathname}?${params}`, { push: false, shownUrl: `${location.pathname}?${params}` });
      }
    }
  }
  syncUnitButtons();
  renderRecent();
  afterRender();
  tickClock();
})();
