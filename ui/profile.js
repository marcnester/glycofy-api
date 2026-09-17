// /ui/profile.js — Profile + Meal Preferences + editable display_name + resilient Strava status
// v2025-11-18-nameedit2
(function () {
  // ---- shared helpers (no hard dependency) ----
  const api = window.__glyco || {};
  const ensureAuth = api.ensureAuth || (async () => true);
  const getUser = api.getUser || (async () => ({}));
  const fetchJSON = async (url, init = {}) => {
    const res = await fetch(url, { credentials: "include", ...init });
    if (!res.ok) {
      let detail = "";
      try {
        const payload = await res.json();
        detail = typeof payload?.detail === "string" ? payload.detail : "";
      } catch {}
      const requestId = res.headers.get("x-request-id");
      throw new Error([detail || `HTTP ${res.status}`, requestId ? `Reference: ${requestId}` : ""].filter(Boolean).join(" · "));
    }
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
    bindAccountControls();
    bindSecurityControls();
    await renderUser();
    await loadPreferences();
    await loadLearnedPreferences();
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

      const verification = $("#email_verification_status");
      const resend = $("#resend_verification");
      if (verification) verification.textContent = me?.email_verified ? "Verified" : "Not verified — verify your email to protect account recovery.";
      if (resend) resend.hidden = Boolean(me?.email_verified);

      const unitsEl = $("#user_units");
      if (unitsEl) unitsEl.textContent = (me?.units || "US").toUpperCase();

      populateAthleteSetup(me);
    } catch (e) {
      console.warn("getUser failed", e);
    }
  }

  function bindAccountControls() {
    const resend = $("#resend_verification");
    resend?.addEventListener("click", async () => {
      resend.disabled = true;
      try {
        const result = await fetchJSON("/auth/resend-verification", {method:"POST"});
        flash(result.verification_sent ? "Verification email sent." : "Email is already verified or delivery is not configured.");
      } catch { flash("Could not send verification email.", "error"); }
      finally { resend.disabled = false; }
    });

    const passwordForm = $("#change_password_form");
    const passwordStatus = $("#change_password_status");
    $("#change_password")?.addEventListener("click", () => {
      passwordForm.hidden = false;
      $("#current_password")?.focus();
    });
    $("#change_password_cancel")?.addEventListener("click", () => {
      passwordForm.reset();
      passwordForm.hidden = true;
      if (passwordStatus) passwordStatus.textContent = "";
    });
    passwordForm?.addEventListener("submit", async (event) => {
      event.preventDefault();
      const currentPassword = $("#current_password")?.value || "";
      const newPassword = $("#new_password")?.value || "";
      const confirmation = $("#confirm_new_password")?.value || "";
      if (newPassword !== confirmation) {
        if (passwordStatus) passwordStatus.textContent = "New passwords do not match.";
        return;
      }
      const submit = $("#change_password_submit");
      submit.disabled = true;
      if (passwordStatus) passwordStatus.textContent = "Updating…";
      try {
        await fetchJSON("/auth/change-password", {
          method: "POST",
          headers: {"Content-Type":"application/json", "X-Requested-With":"XMLHttpRequest"},
          body: JSON.stringify({current_password: currentPassword, new_password: newPassword}),
        });
        window.location.replace("/ui/login.html?password=changed");
      } catch (error) {
        if (passwordStatus) passwordStatus.textContent = error.message || "Could not change password.";
        submit.disabled = false;
      }
    });

    const dialog = $("#delete_account_dialog");
    const input = $("#delete_confirmation");
    const confirm = $("#delete_account_confirm");
    $("#delete_account")?.addEventListener("click", () => { if (input) input.value = ""; if (confirm) confirm.disabled = true; dialog?.showModal(); });
    $("#delete_account_cancel")?.addEventListener("click", () => dialog?.close());
    dialog?.addEventListener("cancel", (event) => {
      event.preventDefault();
      dialog.close();
    });
    dialog?.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        dialog.close();
      }
    });
    input?.addEventListener("input", () => { if (confirm) confirm.disabled = input.value !== "DELETE"; });
    confirm?.addEventListener("click", async () => {
      confirm.disabled = true; confirm.textContent = "Deleting…";
      try {
        await fetchJSON("/users/me", {method:"DELETE", headers:{"Content-Type":"application/json", "X-Requested-With":"XMLHttpRequest"}, body:JSON.stringify({confirmation:input?.value || ""})});
        window.location.replace("/ui/login.html?account=deleted");
      } catch {
        flash("Account deletion failed. Your data has not been deleted.", "error");
        confirm.disabled = false; confirm.textContent = "Delete permanently";
      }
    });
  }

  function fromBase64url(value) {
    const normalized = value.replace(/-/g, "+").replace(/_/g, "/");
    const binary = atob(normalized + "=".repeat((4 - normalized.length % 4) % 4));
    return Uint8Array.from(binary, char => char.charCodeAt(0));
  }
  function toBase64url(value) {
    const bytes = new Uint8Array(value);
    let binary = ""; bytes.forEach(byte => { binary += String.fromCharCode(byte); });
    return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  }
  function prepareCreationOptions(options) {
    return {
      ...options,
      challenge: fromBase64url(options.challenge),
      user: {...options.user, id: fromBase64url(options.user.id)},
      excludeCredentials: (options.excludeCredentials || []).map(item => ({...item, id: fromBase64url(item.id)})),
    };
  }
  function serializeAttestation(credential) {
    return {
      id: credential.id,
      rawId: toBase64url(credential.rawId),
      type: credential.type,
      authenticatorAttachment: credential.authenticatorAttachment,
      response: {
        attestationObject: toBase64url(credential.response.attestationObject),
        clientDataJSON: toBase64url(credential.response.clientDataJSON),
        transports: credential.response.getTransports ? credential.response.getTransports() : [],
      },
    };
  }

  function securityRow(primary, secondary, actionLabel, onAction) {
    const row = document.createElement("div");
    row.style.cssText = "display:flex;align-items:center;justify-content:space-between;gap:12px;padding:10px 0;border-top:1px solid rgba(255,255,255,.08)";
    const copy = document.createElement("div");
    const title = document.createElement("div"); title.textContent = primary;
    const detail = document.createElement("small"); detail.className = "muted"; detail.textContent = secondary;
    copy.append(title, detail); row.append(copy);
    if (actionLabel) {
      const button = document.createElement("button");
      button.type = "button"; button.className = "btn btn--pill"; button.textContent = actionLabel;
      button.addEventListener("click", onAction); row.append(button);
    }
    return row;
  }

  async function loadPasskeys() {
    const target = $("#passkey_list"); if (!target) return;
    const data = await fetchJSON("/auth/passkeys"); target.replaceChildren();
    if (!data.passkeys.length) target.append(securityRow("No passkeys yet", "Add one for phishing-resistant sign-in."));
    data.passkeys.forEach(item => target.append(securityRow(
      item.name,
      `Added ${new Date(item.created_at).toLocaleDateString()}${item.last_used_at ? ` · Last used ${new Date(item.last_used_at).toLocaleString()}` : ""}`,
      "Remove",
      async () => {
        try { await fetchJSON(`/auth/passkeys/${item.id}`, {method:"DELETE", headers:{"X-Requested-With":"XMLHttpRequest"}}); await loadPasskeys(); flash("Passkey removed."); }
        catch (error) { $("#passkey_status").textContent = error.message; }
      }
    )));
  }

  async function loadSessions() {
    const target = $("#session_list"); if (!target) return;
    const data = await fetchJSON("/auth/sessions"); target.replaceChildren();
    data.sessions.forEach(item => target.append(securityRow(
      `${item.device}${item.current ? " · This device" : ""}`,
      `${item.auth_method} sign-in · Active ${new Date(item.last_seen_at).toLocaleString()}`,
      item.current ? null : "Sign out",
      async () => {
        try { await fetchJSON(`/auth/sessions/${item.id}`, {method:"DELETE", headers:{"X-Requested-With":"XMLHttpRequest"}}); await loadSessions(); flash("Device signed out."); }
        catch (error) { $("#session_status").textContent = error.message; }
      }
    )));
  }

  function bindSecurityControls() {
    const add = $("#add_passkey");
    if (!window.PublicKeyCredential || !navigator.credentials) {
      if (add) { add.disabled = true; add.title = "Passkeys are not supported by this browser."; }
    }
    add?.addEventListener("click", async () => {
      add.disabled = true; const status = $("#passkey_status"); status.textContent = "Waiting for your device…";
      try {
        const options = await fetchJSON("/auth/passkeys/register/options", {method:"POST", headers:{"X-Requested-With":"XMLHttpRequest"}});
        const credential = await navigator.credentials.create({publicKey:prepareCreationOptions(options.publicKey)});
        if (!credential) throw new Error("Passkey setup was cancelled.");
        const suggestedName = /iPhone|iPad/i.test(navigator.userAgent) ? "Apple device" : "My device";
        await fetchJSON("/auth/passkeys/register/complete", {
          method:"POST", headers:{"Content-Type":"application/json", "X-Requested-With":"XMLHttpRequest"},
          body:JSON.stringify({challenge_id:options.challenge_id, credential:serializeAttestation(credential), name:suggestedName}),
        });
        status.textContent = "Passkey added."; await loadPasskeys();
      } catch (error) {
        if (error?.name !== "NotAllowedError") status.textContent = error.message || "Could not add passkey.";
      } finally { add.disabled = false; }
    });
    $("#terminate_other_sessions")?.addEventListener("click", async event => {
      event.currentTarget.disabled = true;
      try { const result = await fetchJSON("/auth/sessions/terminate-others", {method:"POST", headers:{"X-Requested-With":"XMLHttpRequest"}}); $("#session_status").textContent = `${result.terminated} other device${result.terminated === 1 ? "" : "s"} signed out.`; await loadSessions(); }
      catch (error) { $("#session_status").textContent = error.message; }
      finally { event.currentTarget.disabled = false; }
    });
    loadPasskeys().catch(error => { $("#passkey_status").textContent = error.message; });
    loadSessions().catch(error => { $("#session_status").textContent = error.message; });
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
  const snackCountInput = $("#daily_snack_count");
  const snackTimeInputs = $$(".snack-time");
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

  function updateSnackTimeVisibility() {
    const count = Number(snackCountInput?.value || 0);
    snackTimeInputs.forEach((input, index) => {
      input.hidden = index >= count;
      input.disabled = index >= count;
    });
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
      if (snackCountInput) snackCountInput.value = String(prefs?.daily_snack_count ?? 1);
      const snackTimes = Array.isArray(prefs?.snack_times) ? prefs.snack_times : ["15:00"];
      snackTimeInputs.forEach((input, index) => {
        input.value = snackTimes[index] || ["10:00", "15:00", "19:30"][index];
      });
      updateSnackTimeVisibility();

      // Wire change listeners once
      dietRadios.forEach((r) => r.addEventListener("change", requestSave));
      allergenChecks.forEach((input) => input.addEventListener("change", requestSave));
      exclInput?.addEventListener("input", requestSave);
      snackCountInput?.addEventListener("change", () => {
        updateSnackTimeVisibility();
        requestSave();
      });
      snackTimeInputs.forEach((input) => input.addEventListener("change", requestSave));

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
      daily_snack_count: Number(snackCountInput?.value || 0),
      snack_times: snackTimeInputs.filter((input) => !input.disabled).map((input) => input.value),
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
      setPrefStatus(`Could not save · ${e.message || "Please try again"}`, "error");
    }
  }

  function learnedPreferenceChip(label, value, tone = "positive") {
    const chip = document.createElement("span");
    chip.className = "radio-chip";
    chip.textContent = `${label}: ${value}`;
    if (tone === "negative") chip.style.borderColor = "rgba(244,169,163,.42)";
    return chip;
  }

  async function loadLearnedPreferences() {
    const empty = $("#learned_preferences_empty");
    const content = $("#learned_preferences_content");
    const summary = $("#learned_preferences_summary");
    const groups = $("#learned_preferences_groups");
    const reset = $("#reset_learned_preferences");
    if (!empty || !content || !groups || !reset) return;
    try {
      const data = await fetchJSON("/v1/feedback/preferences");
      const count = Number(data.preference_signal_count || 0) + Number(data.feedback_count || 0);
      empty.hidden = count > 0;
      content.hidden = count === 0;
      reset.hidden = count === 0;
      groups.innerHTML = "";
      if (summary) summary.textContent = `${count} meal signal${count === 1 ? "" : "s"} shaping future plans`;
      const sections = [
        ["Favorite meal", data.favorite_meals, "positive"],
        ["Preferred protein", data.favored_proteins, "positive"],
        ["Preferred style", data.favored_meal_styles, "positive"],
        ["Preferred ingredient", data.favored_ingredients, "positive"],
        ["Avoid", data.avoid_repeating, "negative"],
        ["Less often", data.avoided_ingredients, "negative"],
      ];
      sections.forEach(([label, values, tone]) => {
        (Array.isArray(values) ? values.slice(0, 4) : []).forEach((value) => {
          groups.appendChild(learnedPreferenceChip(label, value, tone));
        });
      });
    } catch (error) {
      console.warn("Could not load learned preferences", error);
      empty.textContent = "Learned preferences are temporarily unavailable.";
    }
  }

  $("#reset_learned_preferences")?.addEventListener("click", async () => {
    if (!window.confirm("Reset meal learning? Your diet, allergies, and other safety exclusions will not change.")) return;
    const button = $("#reset_learned_preferences");
    button.disabled = true;
    try {
      await fetchJSON("/v1/feedback/preferences", { method: "DELETE" });
      flash("Meal learning reset. Safety preferences were preserved.");
      await loadLearnedPreferences();
    } catch (error) {
      flash(error.message || "Could not reset meal learning.", "error");
    } finally {
      button.disabled = false;
    }
  });

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
