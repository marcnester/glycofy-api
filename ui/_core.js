// ui/_core.js — shared helpers (CSP-safe, no modules)
(function () {
  // ---------- tiny DOM helpers ----------
  function $(id) { return document.getElementById(id); }
  function show(el) { if (el) el.style.display = ''; }
  function hide(el) { if (el) el.style.display = 'none'; }
  function setText(id, text) { const el = $(id); if (el) el.textContent = text; }

  // ---------- token helpers ----------
  const TOKEN_KEY = 'glyco_token';
  function setToken() { try { localStorage.removeItem(TOKEN_KEY); } catch {} }
  function getToken() { return ''; }

  // ---------- fetch helper (adds Bearer if present; cookie optional) ----------
  async function fetchJSON(path, opts = {}) {
    const headers = Object.assign({ 'Content-Type': 'application/json' }, opts.headers || {});
    const res = await fetch(path, {
      method: opts.method || 'GET',
      body: opts.body ? JSON.stringify(opts.body) : undefined,
      headers,
      credentials: 'include',                                     // harmless if JWT-only
      cache: 'no-store',
    });
    if (!res.ok) {
      let msg = `${res.status} ${res.statusText}`;
      try {
        const ct = res.headers.get('Content-Type') || '';
        if (ct.includes('application/json')) {
          const j = await res.json();
          msg = j?.detail || j?.message || msg;
        } else {
          msg = await res.text();
        }
      } catch {}
      const err = new Error(msg);
      err.status = res.status;
      throw err;
    }
    return res.status === 204 ? null : res.json();
  }

  // ---------- auth helpers ----------
  async function getMe() {
    const r = await fetch('/users/me', { credentials: 'include', cache: 'no-store' });
    if (!r.ok) throw new Error('Not authenticated');
    return r.json();
  }

  async function ensureAuth() {
    try { await getMe(); return true; } catch { return false; }
  }

  // ---------- export ----------
  window.__glyco = {
    $, show, hide, setText,
    setToken, getToken,
    fetchJSON, getMe, ensureAuth,
  };
})();
