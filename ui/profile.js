// ui/profile.js — profile load/save, Strava status/link/sync, plan regen (CSP-safe)
(function(){
  const api = (window.__glyco || {});
  if (!api.fetchJSON) { console.error('[profile] __glyco missing'); return; }
  const { $, fmt, fetchJSON, ensureAuth } = api;

  // Alerts
  function ok(msg){ const el = $('alert_ok'); if(el){ el.textContent = msg; el.style.display='block'; } }
  function err(msg){ const el = $('alert_err'); if(el){ el.textContent = msg; el.style.display='block'; } }
  function clearAlerts(){ ['alert_ok','alert_err'].forEach(id => { const el=$(id); if(el){ el.style.display='none'; el.textContent=''; } }); }

  // Units
  const Unit = {
    kgToLb: (kg) => (kg == null || isNaN(kg) ? null : kg * 2.2046226218),
    lbToKg: (lb) => (lb == null || isNaN(lb) ? null : lb / 2.2046226218),
    cmToIn: (cm) => (cm == null || isNaN(cm) ? null : cm / 2.54),
    inToCm: (inch) => (inch == null || isNaN(inch) ? null : inch * 2.54),
    round1: (n) => (n == null || isNaN(n) ? '' : Math.round(n * 10) / 10),
  };
  function detectUnitSystem(){
    try {
      const tz = Intl.DateTimeFormat().resolvedOptions().timeZone || '';
      const lang = (navigator.language || '').toLowerCase();
      if (tz.startsWith('America/') || lang.startsWith('en-us')) return 'us';
    } catch {}
    return 'metric';
  }
  function updateUnitLabels(unitSystem){
    const hu = $('height_unit'); const wu = $('weight_unit');
    if (unitSystem === 'us') { if (hu) hu.textContent = '(in)'; if (wu) wu.textContent = '(lb)'; }
    else { if (hu) hu.textContent = '(cm)'; if (wu) wu.textContent = '(kg)'; }
  }
  function setPlaceholders(unitSystem){
    const h = $('height_cm'); const w = $('weight_kg');
    if (unitSystem === 'us') { if (h) h.placeholder = 'e.g. 72 (in)'; if (w) w.placeholder = 'e.g. 180 (lb)'; }
    else { if (h) h.placeholder = 'e.g. 180 (cm)'; if (w) w.placeholder = 'e.g. 80 (kg)'; }
  }
  function applyDisplayUnits(targetSystem) {
    const hEl = $('height_cm'); const wEl = $('weight_kg');
    if (!hEl || !wEl) return;
    const currentH = hEl.dataset.units || 'metric';
    const currentW = wEl.dataset.units || 'metric';
    const toUS = (targetSystem === 'us');

    if (currentH !== (toUS ? 'us' : 'metric')) {
      const num = parseFloat(hEl.value);
      if (!isNaN(num)) hEl.value = toUS ? Unit.round1(Unit.cmToIn(num)) : Unit.round1(Unit.inToCm(num));
      hEl.dataset.units = toUS ? 'us' : 'metric';
    }
    if (currentW !== (toUS ? 'us' : 'metric')) {
      const num = parseFloat(wEl.value);
      if (!isNaN(num)) wEl.value = toUS ? Unit.round1(Unit.kgToLb(num)) : Unit.round1(Unit.lbToKg(num));
      wEl.dataset.units = toUS ? 'us' : 'metric';
    }
    updateUnitLabels(targetSystem);
    setPlaceholders(targetSystem);
  }

  function val(id){ const el = $(id); return el ? el.value : ''; }
  function set(id, v){ const el = $(id); if(!el) return; el.value = (v==null ? '' : v); }

  async function loadUser(){
    const u = await fetchJSON('/users/me');
    const unitSystem = detectUnitSystem();
    updateUnitLabels(unitSystem); setPlaceholders(unitSystem);

    set('email', u.email || '');
    set('sex', u.sex || '');
    set('dob', (u.dob || '').slice(0,10));
    set('diet_pref', u.diet_pref || 'omnivore');
    set('goal', u.goal || 'maintain');
    set('timezone', u.timezone || '');

    if (unitSystem === 'us') {
      set('height_cm', Unit.round1(Unit.cmToIn(parseFloat(u.height_cm))));
      set('weight_kg', Unit.round1(Unit.kgToLb(parseFloat(u.weight_kg))));
      $('height_cm') && ($('height_cm').dataset.units = 'us');
      $('weight_kg') && ($('weight_kg').dataset.units = 'us');
    } else {
      set('height_cm', (u.height_cm == null ? '' : u.height_cm));
      set('weight_kg', (u.weight_kg == null ? '' : u.weight_kg));
      $('height_cm') && ($('height_cm').dataset.units = 'metric');
      $('weight_kg') && ($('weight_kg').dataset.units = 'metric');
    }
  }

  async function saveUser(){
    clearAlerts();

    const hEl = $('height_cm'); const wEl = $('weight_kg');
    const hUnits = hEl?.dataset.units || 'metric';
    const wUnits = wEl?.dataset.units || 'metric';

    const hVal = parseFloat(val('height_cm'));
    const wVal = parseFloat(val('weight_kg'));

    const payload = {
      sex: val('sex') || null,
      dob: val('dob') || null,
      height_cm: (hUnits === 'us') ? Unit.inToCm(hVal) : (isNaN(hVal) ? null : hVal),
      weight_kg: (wUnits === 'us') ? Unit.lbToKg(wVal) : (isNaN(wVal) ? null : wVal),
      diet_pref: val('diet_pref') || null,
      goal: val('goal') || null,
      timezone: val('timezone') || null,
    };

    // Try common variants in order: PUT /users/me → POST /users/me/update → POST /users/me
    let saved = null;
    try {
      saved = await fetchJSON('/users/me', {
        method:'PUT', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload)
      });
    } catch (e) {
      if (String(e.message || '').includes('405')) {
        try {
          saved = await fetchJSON('/users/me/update', {
            method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload)
          });
        } catch (e2) {
          saved = await fetchJSON('/users/me', {
            method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload)
          });
        }
      } else { throw e; }
    }

    ok('Profile saved.');

    // Refresh today’s plan so Plan reflects latest user info
    try {
      const today = fmt.dateISO(new Date());
      await fetchJSON(`/v1/plan/${today}?diet_pref=${encodeURIComponent(saved?.diet_pref || payload.diet_pref || 'omnivore')}`);
      ok('Today’s plan refreshed.');
    } catch {}
  }

  // Strava (fallbacks supported)
  async function fetchStravaStatus() {
    try { return await fetchJSON('/oauth/strava/status'); }
    catch (e) {
      if (String(e.message || '').includes('404')) {
        try { return await fetchJSON('/oauth/status'); }
        catch { throw e; }
      } else { throw e; }
    }
  }
  async function loadStravaStatus(){
    try{
      const s = await fetchStravaStatus();
      const status = $('strava_status');
      const linkBtn = $('strava_link');
      const syncBtn = $('strava_sync');

      const linked = !!(s && (s.connected || s.linked));
      if (linked) {
        if (status) status.innerHTML = 'linked <span class="ok">●</span>';
        if (linkBtn) { linkBtn.textContent = 'Re-link'; linkBtn.style.display = ''; }
        if (syncBtn)  { syncBtn.style.display = ''; }
      } else {
        if (status) status.innerHTML = 'not linked <span class="warn">●</span>';
        if (linkBtn) { linkBtn.textContent = 'Link Strava'; linkBtn.style.display = ''; }
        if (syncBtn)  { syncBtn.style.display = 'none'; }
      }
    }catch{
      // Hide if endpoint doesn't exist on this server
      const row = $('strava_status');
      if (row && row.parentElement) row.parentElement.parentElement.style.display = 'none';
    }
  }

  async function startStravaOAuth(){
    // Try redirect path
    try { location.href = '/oauth/strava/start'; return; } catch {}
    // Fallback to start-url pattern
    try {
      const j = await fetchJSON('/oauth/start-url');
      if (j && j.authorize_url) location.href = j.authorize_url;
    } catch(e){
      err(e.message || 'Failed to start Strava OAuth');
    }
  }

  async function syncStravaNow(){
    clearAlerts();
    try{
      const res = await fetchJSON(`/sync/strava?replace=false`, { method:'POST' });
      ok(`Synced Strava — created ${res.created||0}, updated ${res.updated||0}.`);
      await loadStravaStatus();
    }catch(e){
      err(e.message || 'Sync failed.');
    }
  }

  document.addEventListener('DOMContentLoaded', async () => {
    if (!ensureAuth || !ensureAuth()) return;

    $('save_btn')?.addEventListener('click', () => saveUser().catch(e => err(e.message || String(e))));
    $('regen_btn')?.addEventListener('click', async () => {
      clearAlerts();
      try {
        const today = fmt.dateISO(new Date());
        const dp = val('diet_pref') || 'omnivore';
        await fetchJSON(`/v1/plan/${today}?diet_pref=${encodeURIComponent(dp)}`);
        ok('Today’s plan regenerated.');
      } catch(e){ err(e.message || 'Could not regenerate.'); }
    });

    $('strava_link')?.addEventListener('click', startStravaOAuth);
    $('strava_sync')?.addEventListener('click', syncStravaNow);

    $('timezone')?.addEventListener('change', () => {
      const tz = val('timezone');
      const target = (typeof tz === 'string' && tz.startsWith('America/')) ? 'us' : 'metric';
      applyDisplayUnits(target);
    });

    try{
      await loadUser();
      await loadStravaStatus();
    }catch(e){
      err(e.message || 'Failed to load profile.');
    }
  });
})();
