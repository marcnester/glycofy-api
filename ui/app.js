// ui/app.js
// Global bootstrap for Glycofy UI (no modules; CSP-friendly).
// Provides: window.__glyco = {
//   API, $, qs, show, hide, fmt, ensureAuth, fetchJSON, setActiveNav,
//   detectUnitSystem, Unit, selftest
// }

(function () {
  // ---------- Safe DOM helpers ----------
  function $(idOrEl) {
    if (!idOrEl) return null;
    if (typeof idOrEl === 'string') return document.getElementById(idOrEl) || null;
    if (idOrEl instanceof Element) return idOrEl;
    return null;
  }
  function qs(sel, root) { return (root || document).querySelector(sel); }
  function show(idOrEl) { const el = $(idOrEl); if (!el) return; try { el.classList?.remove('hidden'); if (el.style) el.style.display = ''; } catch {} }
  function hide(idOrEl) { const el = $(idOrEl); if (!el) return; try { el.classList?.add('hidden'); if (el.style) el.style.display = 'none'; } catch {} }

  // ---------- Tiny top error banner ----------
  function ensureTopBanner() {
    let b = document.getElementById('__glyco_err_banner');
    if (b) return b;
    b = document.createElement('div');
    b.id = '__glyco_err_banner';
    b.style.cssText = 'position:fixed;top:0;left:0;right:0;z-index:9999;background:#3a1414;border-bottom:1px solid #542020;color:#ffdede;padding:8px 12px;font:13px/1.4 system-ui, -apple-system, Segoe UI, Roboto;';
    b.style.display = 'none';
    document.addEventListener('DOMContentLoaded', () => {
      document.body.appendChild(b);
    });
    return b;
  }
  function bannerError(msg) {
    const b = ensureTopBanner();
    b.textContent = `⚠️ ${msg}`;
    b.style.display = '';
  }

  // Global error wiring (safe for CSP; no eval)
  window.addEventListener('error', (e) => {
    console.error('[glyco:error]', e.error || e.message || e);
    bannerError(e?.message || 'A script error occurred. See console.');
  });
  window.addEventListener('unhandledrejection', (e) => {
    console.error('[glyco:unhandled]', e.reason);
    bannerError((e?.reason && (e.reason.message || String(e.reason))) || 'An async error occurred. See console.');
  });

  // ---------- Formatting helpers ----------
  const fmt = {
    kcal(n) { return `${Math.round(n)} kcal`; },
    g(n) { return `${Math.round(n)} g`; },
    dateISO(d) {
      if (typeof d === 'string') return d.slice(0, 10);
      const dt = d instanceof Date ? d : new Date(d);
      return dt.toISOString().slice(0, 10);
    },
    round(n, places=1){
      const m = Math.pow(10, places);
      return Math.round((n + Number.EPSILON) * m) / m;
    }
  };

  // ---------- Token storage ----------
  const TOKEN_KEY = 'glyco_token';
  function getToken() { try { return localStorage.getItem(TOKEN_KEY); } catch { return null; } }
  function setToken(val) { try { if (!val) localStorage.removeItem(TOKEN_KEY); else localStorage.setItem(TOKEN_KEY, val); } catch {} }

  // ---------- API wrapper ----------
  const API = {
    get token() { return getToken(); },
    set token(t) { setToken(t); },
    authHeaders() {
      const t = getToken();
      const h = {};
      if (t) h['Authorization'] = `Bearer ${t}`;
      return h;
    }
  };

  // Centralized fetch with 401 handling and JSON parsing
  async function fetchJSON(url, opts = {}) {
    const headers = Object.assign(
      { 'Accept': 'application/json' },
      API.authHeaders(),            // header auth for classic login
      opts.headers || {}
    );

    // **IMPORTANT**: include credentials so HttpOnly cookie (Google login) is sent
    const fetchOpts = Object.assign(
      { credentials: 'include' },   // <--- this fixes 401s after OAuth
      opts,
      { headers }
    );

    const res = await fetch(url, fetchOpts);

    if (res.status === 401) {
      const ret = encodeURIComponent(location.pathname + location.search);
      location.href = `/ui/login.html?return=${ret}`;
      throw new Error('Unauthorized');
    }

    const ct = res.headers.get('Content-Type') || '';
    if (ct.includes('application/json')) {
      const data = await res.json().catch(() => (null));
      if (!res.ok) {
        const detail = data && (data.detail || data.message);
        throw new Error(detail || `${res.status} ${res.statusText}`);
      }
      return data;
    }

    const txt = await res.text().catch(() => '');
    if (!res.ok) throw new Error(txt || `${res.status} ${res.statusText}`);
    return txt;
  }

  // ---------- Auth helper ----------
  function ensureAuth() {
    // If we have a token in localStorage, we’re good.
    if (API.token) return true;

    // We might still be authenticated via HttpOnly cookie (Google).
    // We can’t read the cookie from JS, but fetchJSON() now includes credentials,
    // so any protected call will work. We keep this synchronous helper unchanged,
    // and rely on fetchJSON’s 401 handling to redirect if needed.
    if (location.pathname.endsWith('/ui/login.html')) return false;
    return true;
  }

  // ---------- Navigation state ----------
  function setActiveNav() {
    const path = location.pathname;
    document.querySelectorAll('nav a').forEach(a => {
      const href = a.getAttribute('href');
      if (!href) return;
      if (href === '/ui/' && path === '/ui/') {
        a.classList.add('active');
      } else if (href !== '/ui/' && path.endsWith(href)) {
        a.classList.add('active');
      } else {
        a.classList.remove('active');
      }
    });
  }

  // ---------- Units: detect & convert (UI only; API remains kg/cm) ----------
  function detectUnitSystem(user) {
    try {
      const forced = localStorage.getItem('glyco_units');
      if (forced === 'us' || forced === 'metric') return forced;
    } catch {}

    const tz = (user?.timezone || '').trim();
    const usTZ = /^America\//.test(tz);
    if (usTZ) return 'us';
    if ((navigator.language || '').toLowerCase().startsWith('en-us')) return 'us';
    return 'metric';
  }

  const Unit = {
    kgToLb: kg => (kg != null ? (kg * 2.2046226218) : null),
    lbToKg: lb => (lb != null ? (lb / 2.2046226218) : null),
    cmToIn: cm => (cm != null ? (cm / 2.54) : null),
    inToCm: inch => (inch != null ? (inch * 2.54) : null),
    round1: n => Math.round((n + Number.EPSILON) * 10) / 10,
    round0: n => Math.round(n)
  };

  // ---------- Self test ----------
  async function selftest() {
    const out = { tokenPresent: !!API.token };
    try {
      out.me = await fetchJSON('/users/me');
      out.activities = await fetchJSON('/activities?page=1&page_size=5');
      const today = fmt.dateISO(new Date());
      out.plan = await fetchJSON(`/v1/plan/${today}?diet_pref=${encodeURIComponent(out.me?.diet_pref || 'omnivore')}`);
      console.log('[glyco:selftest]', out);
      bannerError('Self-test OK (see console). If page is blank, a page script likely can’t find expected DOM IDs.');
    } catch (e) {
      console.error('[glyco:selftest:fail]', e);
      bannerError(`Self-test failed: ${e.message || e}`);
    }
    return out;
  }

  // Expose global API
  window.__glyco = {
    API, $, qs, show, hide, fmt, ensureAuth, fetchJSON, setActiveNav,
    detectUnitSystem, Unit, selftest
  };

  // Initialize nav highlighting if a nav exists
  document.addEventListener('DOMContentLoaded', setActiveNav);

  // Consistent logout across pages
  document.addEventListener('DOMContentLoaded', () => {
    const logoutBtn = document.getElementById('logout_btn');
    if (logoutBtn) {
      logoutBtn.addEventListener('click', async () => {
        try { localStorage.removeItem('glyco_token'); } catch {}
        // Clear HttpOnly cookie on the server
        try { await fetch('/auth/logout', { method: 'POST', credentials: 'include' }); } catch {}
        window.location.href = '/ui/login.html';
      });
    }
  });
})();