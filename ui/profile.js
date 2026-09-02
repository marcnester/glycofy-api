// /ui/profile.js — Profile + Meal Preferences + editable display_name + resilient Strava status
// v2025-11-18-nameedit2
(function () {
  // ---- shared helpers (no hard dependency) ----
  const api = window.__glyco || {};
  const ensureAuth = api.ensureAuth || (async () => true);
  const getUser = api.getUser || (async () => ({}));
  const fetchJSON = async (url, init = {}) => {
    const res = await fetch(url, { credentials: "include", ...init });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    try { return await res.json(); } catch { return {}; }
  };
  const redirectToReturn = api.redirectToReturn || ((p) => (window.location.href = p));

  // ---- dom helpers ----
  const $ = (s) => document.querySelector(s);
  const $$ = (s) => Array.from(document.querySelectorAll(s));
  const flashBox = $("#flash");

  function flash(msg, kind = "ok") {
    if (!flashBox) return;
    flashBox.textContent = msg;
    flashBox.className = "flash " + (kind === "error" ? "flash--error" : "flash--ok");
    flashBox.style.display = "block";
    setTimeout(() => (flashBox.style.display = "none"), 2500);
  }

  // Name editing state
  const nameEl = $("#user_name");
  let currentName = null;
  let savingName = false;

  function setNameText(val) {
    if (!nameEl) return;
    nameEl.textContent = val && String(val).trim() ? val : "—";
  }

  // ---- boot ----
  (async function init() {
    const authed = await ensureAuth();
    if (!authed) return redirectToReturn("/ui/login.html");
    bindLogout();
    bindNameEditing();
    await renderUser();
    await loadPreferences();
    await renderStravaStatus();
  })().catch((e) => {
    console.error(e);
    flash("Failed to initialize profile.", "error");
  });

  // ---- user summary ----
  async function renderUser() {
    try {
      const me = await getUser();

      const displayName =
        me?.display_name ||
        me?.name ||
        (me?.email ? me.email.split("@")[0] : null) ||
        "—";

      currentName = displayName;
      setNameText(displayName);

      const emailEl = $("#user_email");
      if (emailEl) emailEl.textContent = me?.email || "—";

      const unitsEl = $("#user_units");
      if (unitsEl) unitsEl.textContent = (me?.units || "US").toUpperCase();
    } catch (e) {
      console.warn("getUser failed", e);
    }
  }

  function bindLogout() {
    $("#logout_btn")?.addEventListener("click", async () => {
      try { await fetch("/auth/logout", { method: "POST", credentials: "include" }); } catch {}
      window.location.href = "/ui/login.html";
    });
  }

  function bindNameEditing() {
    if (!nameEl) return;

    // Ensure contenteditable is on (also set in HTML for safety)
    nameEl.setAttribute("contenteditable", "true");

    // Hitting Enter commits the change instead of inserting a newline
    nameEl.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        nameEl.blur();
      }
    });

    nameEl.addEventListener("blur", async () => {
      if (!currentName && currentName !== "") {
        // If we haven't fetched the user yet, skip
        return;
      }

      const raw = nameEl.textContent || "";
      const next = raw.trim();

      // Empty → revert to previous
      if (!next) {
        setNameText(currentName);
        return;
      }

      // No change
      if (next === currentName || savingName) return;

      savingName = true;
      try {
        const body = { display_name: next };
        const updated = await fetchJSON("/users/me", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });

        const newName =
          updated.display_name ||
          updated.name ||
          next;

        currentName = newName;
        setNameText(newName);
        flash("Name updated");
      } catch (err) {
        console.error("display_name update failed", err);
        flash("Could not save name", "error");
        setNameText(currentName);
      } finally {
        savingName = false;
      }
    });
  }

  // ---- preferences (diet + exclusions) ----
  const DIET_DEFAULT = "omnivore";
  const statusEl = $("#pref_status");
  const dietRadios = $$('input[name="diet"]');
  const allergenChecks = $$('input[name="allergen"]');
  const exclInput = $("#exclusions_input");
  let saveTimer = null;

  function setPrefStatus(msg, kind) {
    if (!statusEl) return;
    statusEl.textContent = msg;
    statusEl.className = "muted save-hint" + (kind ? " " + kind : "");
  }

  function parseExclusions(raw) {
    if (!raw) return [];
    if (Array.isArray(raw)) {
      return raw.map((s) => String(s).trim()).filter(Boolean);
    }
    return String(raw || "")
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
  }

  async function loadPreferences() {
    try {
      let prefs = null;
      try {
        prefs = await fetchJSON("/v1/preferences");
      } catch (e) {
        console.warn("GET /v1/preferences failed", e);
      }

      // Backend returns { diet, ingredient_exclusions }
      const diet =
        (prefs && (prefs.diet || prefs.diet_preference)) ||
        DIET_DEFAULT;

      const exclusionsList = parseExclusions(
        prefs && (prefs.ingredient_exclusions ?? prefs.diet_exclusions)
      );
      const allergens = new Set(parseExclusions(prefs && prefs.allergens));

      // Set radio
      (dietRadios.find((r) => r.value === String(diet).toLowerCase()) || dietRadios[0]).checked = true;

      // Set textbox as a comma-separated string
      if (exclInput) {
        exclInput.value = exclusionsList.join(", ");
      }
      allergenChecks.forEach((input) => { input.checked = allergens.has(input.value); });

      // Wire change listeners once
      dietRadios.forEach((r) => r.addEventListener("change", requestSave));
      allergenChecks.forEach((input) => input.addEventListener("change", requestSave));
      exclInput?.addEventListener("input", requestSave);

      setPrefStatus("Auto-saves");
    } catch (e) {
      console.error("loadPreferences", e);
      setPrefStatus("Unable to load preferences", "error");
    }
  }

  function currentPrefs() {
    const dietVal = (dietRadios.find((r) => r.checked) || dietRadios[0]).value;
    // Send exclusions as a single comma-separated string; backend normalizes.
    const exclusionsStr = String(exclInput?.value || "");
    return {
      diet: dietVal,
      ingredient_exclusions: exclusionsStr,
      allergens: allergenChecks.filter((input) => input.checked).map((input) => input.value),
    };
  }

  function requestSave() {
    clearTimeout(saveTimer);
    setPrefStatus("Saving…");
    saveTimer = setTimeout(savePreferences, 600);
  }

  async function savePreferences() {
    try {
      await fetchJSON("/v1/preferences", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(currentPrefs()),
      });
      setPrefStatus("Saved");
    } catch (e) {
      console.warn("savePreferences", e);
      setPrefStatus("Could not save", "error");
    }
  }

  // ---- Strava status + actions ----
  async function renderStravaStatus() {
    const pill = $("#strava_status");
    const btnManage = $("#strava_manage");
    const btnReconnect = $("#strava_reconnect");
    const btnDisconnect = $("#strava_disconnect");

    function setState(kind, text) {
      if (!pill) return;
      pill.textContent = text;
      pill.className = "pill " + (kind === "ok" ? "pill--ok" : "pill--warn");
    }

    // Truthy helpers
    const truthy = (v) =>
      v === true ||
      (typeof v === "string" &&
        ["true", "yes", "1", "connected", "ok"].includes(v.toLowerCase())) ||
      v === 1;

    function looksConnected(d) {
      if (!d || typeof d !== "object") return false;
      // common flags
      if (truthy(d.connected) || truthy(d.linked) || truthy(d.ok)) return true;
      if (typeof d.status === "string" && truthy(d.status)) return true;
      if (d.status && typeof d.status === "object" && truthy(d.status.connected)) return true;
      // nested shapes we’ve seen
      if (
        d.strava &&
        (truthy(d.strava.connected) ||
          d.strava.status === "connected" ||
          (d.strava.athlete && d.strava.athlete.id))
      )
        return true;
      if (d.athlete && (typeof d.athlete === "object" ? !!d.athlete.id : truthy(d.athlete)))
        return true;
      if (d.account && typeof d.account === "object") return true;
      // presence of token-ish fields (without exposing them)
      if (d.access_token || d.refresh_token || d.token || d.expires_at) return true;
      // final fallback: any 200 JSON with at least one key often means "ok"
      if (Object.keys(d).length > 0) return true;
      return false;
    }

    async function checkOnce() {
      try {
        const url = `/oauth/strava/status?t=${Date.now()}`;
        const res = await fetch(url, {
          credentials: "include",
          headers: { "Cache-Control": "no-cache", Pragma: "no-cache" },
        });
        if (!res.ok) {
          setState("warn", "Not connected");
          return false;
        }
        let data = {};
        try {
          data = await res.json();
        } catch {}
        const ok = looksConnected(data);
        if (ok) {
          setState("ok", "Connected");
          if (btnManage) btnManage.style.display = "";
          if (btnDisconnect) btnDisconnect.style.display = "";
        } else {
          setState("warn", "Not connected");
        }
        return ok;
      } catch (e) {
        console.warn("status check failed", e);
        setState("warn", "Not connected");
        return false;
      }
    }

    // Initial check + retries if just linked
    const initial = await checkOnce();
    const params = new URLSearchParams(location.search);
    const justLinked = params.has("linked");
    if (!initial && justLinked) {
      [300, 1000, 2500].forEach((ms) => setTimeout(checkOnce, ms));
    } else if (initial && justLinked) {
      // clean the URL once we’ve confirmed
      params.delete("linked");
      const clean = location.pathname + (params.toString() ? "?" + params.toString() : "");
      history.replaceState(null, "", clean);
    }

    // Actions
    btnReconnect?.addEventListener("click", () => {
      const ret = encodeURIComponent("/ui/profile.html?linked=strava");
      window.location.href = `/oauth/strava/start?return=${ret}`;
    });

    btnManage?.addEventListener("click", () => {
      window.open("https://www.strava.com/settings/apps", "_blank", "noopener,noreferrer");
    });

    btnDisconnect?.addEventListener("click", async () => {
      try {
        const response = await fetch("/oauth/strava/disconnect", { method: "POST", credentials: "include" });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        flash("Disconnected Strava");
        setTimeout(() => renderStravaStatus(), 250);
      } catch {
        flash("Failed to disconnect Strava", "error");
      }
    });
  }
})();
