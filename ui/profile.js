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
    bindAthleteSetup();
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

      populateAthleteSetup(me);
    } catch (e) {
      console.warn("getUser failed", e);
    }
  }

  // ---- athlete setup ----
  const athleteForm = $("#athlete_form");
  const athleteUnits = $("#athlete_units");
  const athleteSex = $("#athlete_sex");
  const athleteDob = $("#athlete_dob");
  const athleteHeight = $("#athlete_height");
  const athleteWeight = $("#athlete_weight");
  const athleteGoal = $("#athlete_goal");
  const athleteStatus = $("#athlete_status");
  let displayedUnits = "US";

  const round1 = (value) => Math.round(value * 10) / 10;

  function updateAthleteCompletion() {
    const fields = [athleteUnits, athleteSex, athleteDob, athleteHeight, athleteWeight, athleteGoal];
    const complete = fields.filter((field) => String(field?.value || "").trim()).length;
    const percent = Math.round((complete / fields.length) * 100);
    const label = $("#athlete_completion");
    const fill = $("#athlete_completion_fill");
    if (label) label.textContent = `${percent}% complete`;
    if (fill) fill.style.width = `${percent}%`;
  }

  function configureMeasurementFields(units, convert = false) {
    const metric = units === "Metric";
    if (convert && athleteHeight?.value && athleteWeight?.value) {
      const height = Number(athleteHeight.value);
      const weight = Number(athleteWeight.value);
      athleteHeight.value = round1(metric ? height * 2.54 : height / 2.54);
      athleteWeight.value = round1(metric ? weight / 2.2046226218 : weight * 2.2046226218);
    }
    $("#height_label").textContent = metric ? "Height (cm)" : "Height (in)";
    $("#weight_label").textContent = metric ? "Weight (kg)" : "Weight (lb)";
    athleteHeight.min = metric ? "100" : "39";
    athleteHeight.max = metric ? "250" : "98";
    athleteWeight.min = metric ? "30" : "66";
    athleteWeight.max = metric ? "400" : "882";
    displayedUnits = units;
  }

  function populateAthleteSetup(me) {
    const units = me?.units === "Metric" ? "Metric" : "US";
    athleteUnits.value = units;
    athleteSex.value = me?.sex || "";
    athleteDob.value = me?.dob || "";
    athleteGoal.value = me?.goal || "";
    configureMeasurementFields(units);
    if (me?.height_cm) athleteHeight.value = round1(units === "Metric" ? me.height_cm : me.height_cm / 2.54);
    if (me?.weight_kg) athleteWeight.value = round1(units === "Metric" ? me.weight_kg : me.weight_kg * 2.2046226218);
    updateAthleteCompletion();
  }

  function bindAthleteSetup() {
    if (!athleteForm) return;
    const oldest = new Date();
    oldest.setFullYear(oldest.getFullYear() - 120);
    const youngest = new Date();
    youngest.setFullYear(youngest.getFullYear() - 13);
    athleteDob.min = oldest.toISOString().slice(0, 10);
    athleteDob.max = youngest.toISOString().slice(0, 10);

    [athleteSex, athleteDob, athleteHeight, athleteWeight, athleteGoal].forEach((field) => {
      field?.addEventListener("input", updateAthleteCompletion);
    });
    athleteUnits?.addEventListener("change", () => {
      const next = athleteUnits.value === "Metric" ? "Metric" : "US";
      configureMeasurementFields(next, next !== displayedUnits);
      updateAthleteCompletion();
    });

    athleteForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (!athleteForm.checkValidity()) {
        athleteForm.reportValidity();
        athleteStatus.textContent = "Please complete every field with a valid value.";
        return;
      }
      const metric = athleteUnits.value === "Metric";
      const height = Number(athleteHeight.value);
      const weight = Number(athleteWeight.value);
      const payload = {
        units: athleteUnits.value,
        sex: athleteSex.value,
        dob: athleteDob.value,
        height_cm: round1(metric ? height : height * 2.54),
        weight_kg: round1(metric ? weight : weight / 2.2046226218),
        goal: athleteGoal.value,
        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
      };
      const saveButton = $("#athlete_save");
      saveButton.disabled = true;
      athleteStatus.textContent = "Saving…";
      try {
        const updated = await fetchJSON("/users/me", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        const unitsEl = $("#user_units");
        if (unitsEl) unitsEl.textContent = updated.units.toUpperCase();
        athleteStatus.textContent = "Saved. Your next plan will use these athlete details.";
        flash("Athlete setup saved");
        updateAthleteCompletion();
      } catch (error) {
        console.error("athlete setup update failed", error);
        athleteStatus.textContent = "Could not save athlete setup.";
        flash("Could not save athlete setup", "error");
      } finally {
        saveButton.disabled = false;
      }
    });
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
    const detail = $("#strava_detail");
    const btnManage = $("#strava_manage");
    const btnReconnect = $("#strava_reconnect");
    const btnDisconnect = $("#strava_disconnect");
    const disconnectDialog = $("#strava_disconnect_dialog");
    const disconnectCancel = $("#strava_disconnect_cancel");
    const disconnectConfirm = $("#strava_disconnect_confirm");

    function setState(kind, text) {
      if (!pill) return;
      pill.textContent = text;
      pill.className = "pill " + (kind === "ok" ? "pill--ok" : "pill--warn");
    }

    function showConnected(connected) {
      if (btnManage) btnManage.style.display = connected ? "" : "none";
      if (btnDisconnect) btnDisconnect.style.display = connected ? "" : "none";
      if (btnReconnect) btnReconnect.style.display = connected ? "none" : "inline-flex";
      if (detail) {
        detail.textContent = connected
          ? "Your activities sync automatically"
          : "Connect to automatically import your activities";
      }
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
          truthy(d.strava.linked) ||
          d.strava.status === "connected" ||
          (d.strava.athlete && d.strava.athlete.id))
      )
        return true;
      if (d.athlete && (typeof d.athlete === "object" ? !!d.athlete.id : truthy(d.athlete)))
        return true;
      if (d.account && typeof d.account === "object") return true;
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
          showConnected(false);
          return false;
        }
        let data = {};
        try {
          data = await res.json();
        } catch {}
        const ok = looksConnected(data);
        if (ok) {
          setState("ok", "Connected");
          showConnected(true);
        } else {
          setState("warn", "Not connected");
          showConnected(false);
        }
        return ok;
      } catch (e) {
        console.warn("status check failed", e);
        setState("warn", "Not connected");
        showConnected(false);
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

    btnDisconnect?.addEventListener("click", () => disconnectDialog?.showModal());
    disconnectCancel?.addEventListener("click", () => disconnectDialog?.close());
    disconnectDialog?.addEventListener("click", (event) => {
      if (event.target === disconnectDialog) disconnectDialog.close();
    });

    disconnectConfirm?.addEventListener("click", async () => {
      disconnectConfirm.disabled = true;
      disconnectConfirm.textContent = "Disconnecting…";
      try {
        const response = await fetch("/oauth/strava/disconnect", { method: "POST", credentials: "include" });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        disconnectDialog?.close();
        flash("Disconnected Strava");
        setTimeout(() => renderStravaStatus(), 250);
      } catch {
        flash("Failed to disconnect Strava", "error");
      } finally {
        disconnectConfirm.disabled = false;
        disconnectConfirm.textContent = "Disconnect";
      }
    });
  }
})();
