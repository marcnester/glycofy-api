// ui/login.js — robust cookie-based login (CSP-safe, no modules)
(function () {
  // ---------- tiny DOM helpers ----------
  function $(id) { return document.getElementById(id); }
  function show(el) { if (el) el.style.display = ''; }
  function hide(el) { if (el) el.style.display = 'none'; }

  function flash(msg, type = 'notice') {
    const box = $('msg');
    if (!box) return;
    box.textContent = msg;
    box.className = (type === 'error') ? 'error' : 'notice';
    show(box);
  }

  // Default post-login destination → Plan (MVP)
  function getReturnPath() {
    try {
      const u = new URL(window.location.href);
      const ret = u.searchParams.get('return');
      // Only allow same-origin paths; otherwise fall back to Plan
      return (ret && ret.startsWith('/')) ? ret : '/ui/plan.html';
    } catch {
      return '/ui/plan.html';
    }
  }

  async function alreadyAuthenticated() {
    try {
      const r = await fetch('/users/me', { credentials: 'include' });
      return r.ok;
    } catch { return false; }
  }

  // Cookie-based login. Server sets HttpOnly cookie; response may be { ok:true }.
  async function doLogin(email, password) {
    const res = await fetch('/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
      credentials: 'include', // send/receive cookies
      body: JSON.stringify({ email, password })
    });

    if (!res.ok) {
      let detail = '';
      try {
        const ct = res.headers.get('Content-Type') || '';
        if (ct.includes('application/json')) {
          const data = await res.json();
          detail = data?.detail || data?.message || '';
        } else {
          detail = await res.text();
        }
      } catch {}
      throw new Error(detail || `HTTP ${res.status}`);
    }
    return true;
  }

  async function maybeRenderGoogleButton() {
    const wrap = $('google_wrap');
    const btn = $('google_btn');
    if (!wrap || !btn) return;
    try {
      const r = await fetch('/oauth/google/status', { headers: { 'Accept': 'application/json' }, credentials: 'include' });
      if (!r.ok) throw new Error(String(r.status));
      const info = await r.json().catch(() => ({}));
      if (info && (info.configured || info.enabled)) {
        btn.setAttribute('href', '/oauth/google/start');
        show(btn);
        hide(wrap);
      } else {
        wrap.textContent = 'Google Sign-In is not configured on this server.';
        show(wrap);
        hide(btn);
      }
    } catch {
      hide(btn);
      hide(wrap);
    }
  }

  function handleDemoPrefill() {
    const demo = $('demo');
    if (!demo) return;
    demo.addEventListener('click', () => {
      const email = $('email'); const pw = $('password');
      if (email) email.value = 'demo@glycofy.app';
      if (pw) pw.value = 'Demo1234!';
      flash('Demo credentials filled. Click “Sign in”.');
    });
  }

  function handleFormSubmit() {
    const form = $('login-form');
    const submitBtn = $('submitBtn');
    if (!form || !submitBtn) return;

    let inFlight = false;

    form.addEventListener('submit', async (e) => {
      e.preventDefault();                  // prevent browser default submission
      if (inFlight) return;
      inFlight = true;

      const email = $('email')?.value?.trim();
      const password = $('password')?.value || '';
      if (!email || !password) {
        flash('Please enter both email and password.', 'error');
        inFlight = false;
        return;
      }

      submitBtn.disabled = true;
      flash('Signing in…');

      try {
        await doLogin(email, password);

        // Allow Set-Cookie to commit, then leave the login page
        setTimeout(() => {
          window.location.replace(getReturnPath());
        }, 0);
      } catch (e2) {
        flash(e2?.message || 'Login failed', 'error');
      } finally {
        submitBtn.disabled = false;
        inFlight = false;
      }
    });
  }

  document.addEventListener('DOMContentLoaded', async () => {
    const note = $('endpointNote');
    if (note) note.textContent = `API: ${location.origin}`;

    // If already logged in, bounce off the login page immediately
    try {
      if (await alreadyAuthenticated()) {
        window.location.replace(getReturnPath());
        return;
      }
    } catch {}

    handleDemoPrefill();
    handleFormSubmit();
    void maybeRenderGoogleButton();
  });
})();