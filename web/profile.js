// ui/profile.js — Profile & OAuth page (CSP-safe, no modules)
(function () {
  const gly = (window.__glyco || {});
  const { $, qs, ensureAuth, fetchJSON, detectUnitSystem } = gly;

  // ------- Toast -------
  function ensureToast() {
    let el = document.getElementById('toast');
    if (!el) {
      el = document.createElement('div');
      el.id = 'toast';
      el.style.cssText = 'position:fixed;bottom:16px;left:50%;transform:translateX(-50%);background:#222;color:#fff;padding:8px 12px;border-radius:8px;display:none;z-index:9999;font:13px/1.4 system-ui,-apple-system,Segoe UI,Roboto;';
      document.body.appendChild(el);
    }
    return el;
  }
  function toast(msg, isErr = false) {
    const t = ensureToast();
    t.textContent = msg;
    t.style.background = isErr ? '#7a0f0f' : '#222';
    t.style.display = '';
    setTimeout(() => (t.style.display = 'none'), 2500);
  }

  // ------- Elements -------
  const els = {};
  function bindEls() {
    const byId = (id) => document.getElementById(id);

    els.acctEmail = byId('acctEmail');
    els.acctId = byId('acctId');
    els.unitBadge = byId('unitBadge');
    els.saveStatus = byId('saveStatus');

    els.form = byId('prefsForm');
    els.sex = byId('sex');
    els.dob = byId('dob');
    els.height_cm = byId('height_cm');
    els.weight_kg = byId('weight_kg');
    els.diet_pref = byId('diet_pref');
    els.goal = byId('goal');
    els.timezone = byId('timezone');

    els.appsStatus = byId('appsStatus');
    els.refetchStatusBtn = byId('refetchStatusBtn');
    els.linkStravaBtn = byId('linkStravaBtn');
    els.syncStravaBtn = byId('syncStravaBtn');

    els.logoutBtn = byId('logout_btn') || byId('logoutBtn');
  }

  // ------- API -------
  async function getMe() {
    return fetchJSON('/users/me');
  }
  async function putMe(payload) {
    return fetchJSON('/users/me', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
  }
  async function getOAuthStatus() {
    return fetchJSON('/oauth/status');
  }
  async function getStravaStartUrl() {
    return fetchJSON('/oauth/start-url');
  }
  async function syncStravaSinceMonthStart() {
    const now = new Date();
    const since = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), 1))
      .toISOString().slice(0, 10);
    return fetchJSON(`/imports/strava/sync?since=${encodeURIComponent(since)}`, { method: 'POST' });
  }

  // ------- Fill UI -------
  function setVal(el, v) { if (el) el.value = (v ?? ''); }
  function fillAccount(me) {
    if (els.acctEmail) els.acctEmail.textContent = me?.email || '(unknown)';
    if (els.acctId) els.acctId.textContent = `User ID: ${me?.id ?? '—'}`;

    const unit = detectUnitSystem ? detectUnitSystem(me || {}) : 'metric';
    if (els.unitBadge) {
      els.unitBadge.textContent = unit === 'us' ? 'Units: US' : 'Units: Metric';
      els.unitBadge.classList.remove('hidden');
    }
  }
  function fillPrefs(me) {
    setVal(els.sex, me?.sex);
    setVal(els.dob, me?.dob ? String(me.dob).slice(0, 10) : '');
    setVal(els.height_cm, me?.height_cm);
    setVal(els.weight_kg, me?.weight_kg);
    setVal(els.diet_pref, me?.diet_pref);
    setVal(els.goal, me?.goal);
    setVal(els.timezone, me?.timezone);
  }
  function renderOAuthStatus(status) {
    if (!els.appsStatus) return;
    try {
      const s = status && status.strava ? status.strava : null;
      if (!status || s == null) {
        els.appsStatus.innerHTML = `<span class="muted">Strava not configured</span>`;
        return;
      }
      if (s.configured === false) {
        els.appsStatus.innerHTML = `<span class="muted">Strava not configured on server</span>`;
        return;
      }
      if (s.linked) {
        const expires = s.expires_at ? new Date(s.expires_at * 1000).toLocaleString() : '(unknown)';
        els.appsStatus.innerHTML = `
          <div class="row wrap">
            <span class="pill">Linked: Strava</span>
            <span class="muted">Athlete: ${s.external_athlete_id || '(unknown)'} </span>
            <span class="muted">Scope: ${s.scope || '(none)'}</span>
            <span class="muted">Expires: ${expires}</span>
          </div>`;
      } else {
        els.appsStatus.innerHTML = `<span class="muted">Strava not linked</span>`;
      }
    } catch (e) {
      console.error(e);
      els.appsStatus.textContent = 'Unable to render status';
    }
  }

  // ------- Events -------
  function wireEvents() {
    if (els.logoutBtn) {
      els.logoutBtn.addEventListener('click', async () => {
        try { localStorage.removeItem('glyco_token'); } catch {}
        try { await fetch('/auth/logout', { method: 'POST', credentials: 'include' }); } catch {}
        window.location.href = '/ui/login.html';
      });
    }

    if (els.refetchStatusBtn) {
      els.refetchStatusBtn.addEventListener('click', async () => {
        try { renderOAuthStatus(await getOAuthStatus()); toast('Status refreshed'); }
        catch (e) { console.error(e); toast(e?.message || 'Failed to refresh', true); }
      });
    }

    if (els.linkStravaBtn) {
      els.linkStravaBtn.addEventListener('click', async () => {
        try {
          const { authorize_url } = await getStravaStartUrl();
          window.location.href = authorize_url || '/oauth/start-url';
        } catch (e) {
          console.error(e);
          toast(e?.message || 'Failed to start Strava link', true);
        }
      });
    }

    if (els.syncStravaBtn) {
      els.syncStravaBtn.addEventListener('click', async () => {
        try {
          const r = await syncStravaSinceMonthStart();
          toast(`Synced: created ${r.created ?? 0}, updated ${r.updated ?? 0}, skipped ${r.skipped ?? 0}`);
        } catch (e) {
          const m = e?.detail || e?.message || String(e);
          if (/404/.test(m)) toast('Sync endpoint not available on this server build.', true);
          else toast(m, true);
        }
      });
    }

    if (els.form) {
      els.form.addEventListener('submit', async (evt) => {
        evt.preventDefault();
        const payload = {
          sex: els.sex?.value || null,
          dob: els.dob?.value || null,
          height_cm: parseFloat(els.height_cm?.value || '0') || null,
          weight_kg: parseFloat(els.weight_kg?.value || '0') || null,
          diet_pref: els.diet_pref?.value || null,
          goal: els.goal?.value || null,
          timezone: els.timezone?.value || null,
        };
        try {
          const updated = await putMe(payload);
          fillPrefs(updated);
          if (els.saveStatus) els.saveStatus.textContent = 'Saved ✓';
          toast('Preferences saved');
        } catch (e) {
          console.error(e);
          if (els.saveStatus) els.saveStatus.textContent = 'Save failed';
          toast(e?.message || 'Save failed', true);
        }
      });
    }
  }

  // ------- Init -------
  async function init() {
    try { if (!ensureAuth || !ensureAuth()) { return; } } catch { return; }
    bindEls();

    try {
      const me = await getMe();              // should be 200 now
      fillAccount(me);
      fillPrefs(me);
    } catch (e) {
      console.error(e);
      toast('Failed loading profile', true);
      return;
    }

    try { renderOAuthStatus(await getOAuthStatus()); }
    catch (e) { console.error(e); if (els.appsStatus) els.appsStatus.textContent = 'Unable to load status'; }

    wireEvents();
  }

  document.addEventListener('DOMContentLoaded', init);
})();
