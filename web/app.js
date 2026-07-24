// web/app.js
// Global helpers for API access, auth, and navigation.

(() => {
  const API_BASE = ""; // e.g. "" or "/api" if your FastAPI is behind a prefix

  // --- Storage helpers ---
  function getToken() {
    try {
      return localStorage.getItem("glyco.jwt") || "";
    } catch {
      return "";
    }
  }

  function setToken(tok) {
    try {
      if (tok) localStorage.setItem("glyco.jwt", tok);
      else localStorage.removeItem("glyco.jwt");
    } catch {
      // ignore storage errors (private mode, etc.)
    }
  }

  function isLoggedIn() {
    return !!getToken();
  }

  function logoutAndRedirect() {
    setToken("");
    const here = window.location.pathname.split("/").pop();
    if (here !== "login.html") {
      window.location.href = "login.html";
    }
  }

  // --- Fetch helper that injects Authorization and handles 401 globally ---
  async function fetchJSON(path, opts = {}) {
    const url = path.startsWith("http") ? path : API_BASE + path;
    const token = getToken();

    const headers = new Headers(opts.headers || {});
    headers.set("Accept", "application/json");
    if (!(opts.body instanceof FormData)) {
      headers.set("Content-Type", "application/json");
    }
    if (token) {
      headers.set("Authorization", "Bearer " + token);
    }

    const resp = await fetch(url, {
      method: opts.method || "GET",
      headers,
      body: opts.body,
      credentials: "omit",
    });

    // If unauthorized, clear token and bounce to login
    if (resp.status === 401) {
      logoutAndRedirect();
      throw new Error("Unauthorized");
    }

    // Try JSON; fall back to text for error details
    const text = await resp.text();
    let data = null;
    try {
      data = text ? JSON.parse(text) : null;
    } catch {
      // non-JSON response
      data = text;
    }

    if (!resp.ok) {
      const detail =
        (data && (data.detail || data.message)) ||
        `HTTP ${resp.status}: ${resp.statusText}`;
      throw new Error(detail);
    }
    return data;
  }

  // --- Simple auth gate for pages that require login ---
  function ensureAuth() {
    if (!isLoggedIn()) {
      logoutAndRedirect();
      return false;
    }
    return true;
  }

  // --- Date/number helpers used across pages ---
  function fmtDateTime(s) {
    try {
      const d = new Date(s);
      return d.toLocaleString();
    } catch {
      return s;
    }
  }

  function fmtDateOnly(s) {
    try {
      const d = new Date(s);
      return d.toLocaleDateString();
    } catch {
      return s;
    }
  }

  function fmtMinSec(totalSeconds) {
    const sec = Math.max(0, Number(totalSeconds) || 0);
    const m = Math.floor(sec / 60);
    const s = Math.floor(sec % 60);
    return `${m}m ${s}s`;
  }

  function fmtKm(meters) {
    const m = Number(meters) || 0;
    return m > 0 ? `${(m / 1000).toFixed(1)} km` : "—";
  }

  // --- Expose on window ---
  window.__glyco = {
    API_BASE,
    getToken,
    setToken,
    isLoggedIn,
    logoutAndRedirect,
    fetchJSON,
    ensureAuth,
    fmtDateTime,
    fmtDateOnly,
    fmtMinSec,
    fmtKm,
  };

  // Optional: wire a global logout button if present
  window.addEventListener("DOMContentLoaded", () => {
    const btn = document.getElementById("logout");
    if (btn) {
      btn.addEventListener("click", (e) => {
        e.preventDefault();
        logoutAndRedirect();
      });
    }
  });
})();
