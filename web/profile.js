(function () {
  const API = "";
  const toastEl = document.getElementById("toast");

  function showToast(msg, isError=false) {
    toastEl.textContent = msg;
    toastEl.classList.toggle("error", !!isError);
    toastEl.style.display = "block";
    setTimeout(() => (toastEl.style.display = "none"), 3000);
  }

  function getToken() {
    try {
      const raw = localStorage.getItem("glycofy_auth");
      if (!raw) return null;
      const parsed = JSON.parse(raw);
      return parsed.access_token || null;
    } catch {
      return null;
    }
  }

  function authHeaders() {
    const t = getToken();
    return t ? { Authorization: `Bearer ${t}` } : {};
  }

  function requireAuth() {
    const t = getToken();
    if (!t) {
      // If you have a dedicated login page, send them there.
      // We’ll reuse the plan page flow that does login inline if present.
      window.location.href = "/ui/";
      throw new Error("No token");
    }
  }

  async function safeJSON(res) {
    const ctype = res.headers.get("content-type") || "";
    if (ctype.includes("application/json")) return await res.json();
    const txt = await res.text();
    try { return JSON.parse(txt); } catch { return { raw: txt }; }
  }

  async function getMe() {
    const res = await fetch(`${API}/users/me`, { headers: authHeaders() });
    if (res.status === 401) throw new Error("Unauthorized");
    if (!res.ok) throw new Error(`GET /users/me ${res.status}`);
    return await res.json();
  }

  async function putMe(payload) {
    const res = await fetch(`${API}/users/me`, {
      method: "PUT",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify(payload),
    });
    if (res.status === 401) throw new Error("Unauthorized");
    if (!res.ok) {
      const j = await safeJSON(res);
      throw new Error(j.detail || `PUT /users/me ${res.status}`);
    }
    return await res.json();
  }

  async function getOAuthStatus() {
    const res = await fetch(`${API}/oauth/status`, { headers: authHeaders() });
    if (res.status === 401) throw new Error("Unauthorized");
    if (!res.ok) throw new Error(`GET /oauth/status ${res.status}`);
    return await res.json();
  }

  async function getStravaStartUrl() {
    const res = await fetch(`${API}/oauth/start-url`, { headers: authHeaders() });
    if (res.status === 401) throw new Error("Unauthorized");
    if (!res.ok) {
      const j = await safeJSON(res);
      throw new Error(j.detail || `GET /oauth/start-url ${res.status}`);
    }
    return await res.json();
  }

  async function syncStrava() {
    const now = new Date();
    const since = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), 1))
      .toISOString().slice(0, 10); // YYYY-MM-DD
    const res = await fetch(`${API}/imports/strava/sync?since=${since}`, {
      method: "POST",
      headers: authHeaders(),
    });
    if (res.status === 401) throw new Error("Unauthorized");
    if (!res.ok) {
      const j = await safeJSON(res);
      throw new Error(j.detail || `POST /imports/strava/sync ${res.status}`);
    }
    return await res.json();
  }

  function fillAccount(me) {
    document.getElementById("acctEmail").textContent = me.email || "(unknown)";
    document.getElementById("acctId").textContent = `User ID: ${me.id ?? "—"}`;
  }

  function fillPrefs(me) {
    const pick = (id, val) => { const el = document.getElementById(id); if (el) el.value = val ?? ""; };
    pick("sex", me.sex);
    pick("dob", me.dob ? me.dob.slice(0,10) : "");
    pick("height_cm", me.height_cm);
    pick("weight_kg", me.weight_kg);
    pick("diet_pref", me.diet_pref);
    pick("goal", me.goal);
    pick("timezone", me.timezone);
  }

  function renderOAuthStatus(status) {
    const el = document.getElementById("appsStatus");
    const s = status?.strava || {};
    if (!status || !("strava" in status)) {
      el.innerHTML = `<span class="status-bad">Strava not configured</span>`;
      return;
    }
    if (!s.configured) {
      el.innerHTML = `<span class="status-bad">Strava not configured on server</span>`;
      return;
    }
    if (s.linked) {
      const expires = s.expires_at ? new Date(s.expires_at * 1000).toLocaleString() : "(unknown)";
      el.innerHTML = `
        <div class="row wrap">
          <span class="pill">Linked: Strava</span>
          <span class="status-ok">Athlete: ${s.external_athlete_id || "(unknown)"} </span>
          <span class="muted">Scope: ${s.scope || "(none)"}</span>
          <span class="muted">Expires: ${expires}</span>
        </div>`;
    } else {
      el.innerHTML = `<span class="status-warn">Strava not linked</span>`;
    }
  }

  function wireEvents() {
    document.getElementById("logoutBtn").addEventListener("click", () => {
      localStorage.removeItem("glycofy_auth");
      window.location.href = "/ui/";
    });

    document.getElementById("refetchStatusBtn").addEventListener("click", async () => {
      try {
        const status = await getOAuthStatus();
        renderOAuthStatus(status);
        showToast("Status refreshed");
      } catch (e) {
        console.error(e);
        showToast(String(e.message || e), true);
      }
    });

    document.getElementById("linkStravaBtn").addEventListener("click", async () => {
      try {
        const { authorize_url } = await getStravaStartUrl();
        if (!authorize_url) throw new Error("No authorize_url returned");
        window.location.href = authorize_url;
      } catch (e) {
        console.error(e);
        showToast(String(e.message || e), true);
      }
    });

    document.getElementById("syncStravaBtn").addEventListener("click", async () => {
      try {
        const res = await syncStrava();
        showToast(`Synced: created ${res.created}, updated ${res.updated}, skipped ${res.skipped}`);
      } catch (e) {
        console.error(e);
        showToast(String(e.message || e), true);
      }
    });

    document.getElementById("prefsForm").addEventListener("submit", async (evt) => {
      evt.preventDefault();
      const payload = {
        sex: document.getElementById("sex").value || null,
        dob: document.getElementById("dob").value || null,
        height_cm: parseFloat(document.getElementById("height_cm").value || "0") || null,
        weight_kg: parseFloat(document.getElementById("weight_kg").value || "0") || null,
        diet_pref: document.getElementById("diet_pref").value || null,
        goal: document.getElementById("goal").value || null,
        timezone: document.getElementById("timezone").value || null,
      };
      try {
        const updated = await putMe(payload);
        document.getElementById("saveStatus").textContent = "Saved ✓";
        fillPrefs(updated);
        showToast("Preferences saved");
      } catch (e) {
        console.error(e);
        document.getElementById("saveStatus").textContent = "Save failed";
        showToast(String(e.message || e), true);
      }
    });
  }

  async function init() {
    try {
      requireAuth();
    } catch {
      return;
    }
    wireEvents();
    try {
      const me = await getMe();
      fillAccount(me);
      fillPrefs(me);
    } catch (e) {
      console.error(e);
      showToast("Failed loading profile", true);
      return;
    }
    try {
      const status = await getOAuthStatus();
      renderOAuthStatus(status);
    } catch (e) {
      console.error(e);
      document.getElementById("appsStatus").textContent = "Unable to load status";
    }
  }

  document.addEventListener("DOMContentLoaded", init);
})();
