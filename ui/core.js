// /ui/core.js — shared UI helpers (CSP-safe)
// Default return destination is /ui/index.html (Home)

(function () {
  "use strict";

  // ----- tiny DOM helpers -----
  function $(id) { return document.getElementById(id); }
  function qs(sel, root) { return (root || document).querySelector(sel); }

  // ----- fmt helpers -----
  const fmt = {
    round(n, d) { const p = Math.pow(10, d || 0); return Math.round((Number(n)||0) * p) / p; },
    dateISO(d) {
      try {
        const dt = (d instanceof Date) ? d : new Date(d);
        const t = new Date(dt.getTime() - dt.getTimezoneOffset()*60000);
        return t.toISOString().slice(0,10);
      } catch { return new Date().toISOString().slice(0,10); }
    },
    kcal(n){ const v = Number(n); return Number.isFinite(v) ? String(Math.round(v)) : "0"; },
  };

  // ----- token helpers -----
  const TOKEN_KEY = "glyco_token";
  function setToken() { try { localStorage.removeItem(TOKEN_KEY); } catch {} }
  function getToken() { return ""; }

  // ----- return helpers (DEFAULT → /ui/index.html) -----
  const DEFAULT_RETURN = "/ui/index.html";
  function safePath(p) {
    try { if (p && p.startsWith("/") && !p.startsWith("//")) return p; } catch {}
    return DEFAULT_RETURN;
  }
  function parseReturn(def) {
    try {
      const u = new URL(window.location.href);
      const r = u.searchParams.get("return");
      return safePath(r) || (def || DEFAULT_RETURN);
    } catch { return def || DEFAULT_RETURN; }
  }
  function redirectToReturn(def) {
    const dest = parseReturn(def);
    const here = location.pathname + location.search;
    if (here !== dest) location.replace(dest);
    else location.reload();
  }

  // ----- auth header / fetch -----
  function authHeader() {
    const h = { "Accept":"application/json","X-Requested-With":"XMLHttpRequest" };
    return h;
  }

  async function fetchJSON(url, opts) {
    const res = await fetch(url, Object.assign({ credentials:"include", headers:authHeader() }, opts||{}));
    if (res.status === 401) {
      // bounce to login with return=current page
      const ret = encodeURIComponent(location.pathname + location.search);
      location.href = "/ui/login.html?return=" + ret;
      throw Object.assign(new Error("Unauthorized"), { status: 401 });
    }
    if (!res.ok) {
      let msg = `HTTP ${res.status}`;
      try {
        const ct = res.headers.get("Content-Type") || "";
        if (ct.includes("application/json")) {
          const j = await res.json().catch(()=>null);
          msg = (j && (j.detail || j.message)) || msg;
        } else {
          msg = await res.text();
        }
      } catch {}
      const err = new Error(msg); err.status = res.status; throw err;
    }
    const ct = res.headers.get("Content-Type") || "";
    return ct.includes("application/json") ? res.json() : res.text();
  }

  // ----- single /users/me probe with 30s cache -----
  let _authMemo = null, _authAt = 0, _authInflight = null;
  function isFresh(ts) { return (Date.now() - ts) < 30000; }

  async function ensureAuth({ redirect=false } = {}) {
    if (_authInflight) return _authInflight;
    if (_authMemo && isFresh(_authAt)) return _authMemo;

    _authInflight = (async () => {
      try {
        const ok = await fetch("/users/me", { credentials:"include", headers:authHeader(), cache:"no-store" }).then(r => r.ok);
        _authMemo = ok; _authAt = Date.now();
        if (!ok && redirect) {
          const ret = encodeURIComponent(location.pathname + location.search);
          location.href = "/ui/login.html?return=" + ret;
        }
        return ok;
      } catch {
        _authMemo = false; _authAt = Date.now();
        if (redirect) {
          const ret = encodeURIComponent(location.pathname + location.search);
          location.href = "/ui/login.html?return=" + ret;
        }
        return false;
      } finally { _authInflight = null; }
    })();

    return _authInflight;
  }

  // ----- user fetch with 30s cache -----
  let _me = null, _meAt = 0, _meInflight = null;
  async function getUser() {
    if (_meInflight) return _meInflight;
    if (_me && isFresh(_meAt)) return _me;
    _meInflight = (async () => {
      const j = await fetchJSON("/users/me");
      _me = j; _meAt = Date.now(); return j;
    })();
    return _meInflight;
  }

  // ----- nav helpers -----
  function setActiveNav() {
    try {
      const path = location.pathname.replace(/\/+$/, "");
      const links = document.querySelectorAll('nav[aria-label="Primary"] a');
      links.forEach(a => {
        const hp = a.getAttribute("href") || "";
        const p = hp.replace(/\/+$/, "");
        a.classList.toggle("active", !!p && path.endsWith(p));
      });
    } catch {}
  }

  async function login(email, password) {
    const res = await fetch("/auth/login", {
      method:"POST", credentials:"include",
      headers:Object.assign({ "Content-Type":"application/json" }, authHeader()),
      body:JSON.stringify({ email, password })
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    try {
      await res.json().catch(()=>null);
    } catch {}
    _authMemo = null; _authAt = 0;
    return true;
  }

  async function logout() {
    try { await fetch("/auth/logout", { method:"POST", credentials:"include" }); } catch {}
    setToken(null);
    const ret = encodeURIComponent(DEFAULT_RETURN);
    location.href = "/ui/login.html?return=" + ret;
  }

  // expose
  window.__glyco = Object.freeze({
    $, qs, fmt,
    setToken, getToken,
    authHeader, fetchJSON,
    ensureAuth, getUser,
    setActiveNav,
    redirectToReturn,
    login, logout,
  });
})();
