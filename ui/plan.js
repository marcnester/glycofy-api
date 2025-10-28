// /ui/plan.js
(function () {
  const ready = (fn) => document.addEventListener('DOMContentLoaded', fn);
  if (!window.__glyco) {
    console.error('[plan] __glyco bootstrap missing (app.js must load first).');
    return;
  }
  const { API, fmt, fetchJSON, setActiveNav, ensureAuth } = window.__glyco;
  const $ = (id) => document.getElementById(id);

  // ---------- Tabs ----------
  const tabDay = $('tab_day');
  const tabWeek = $('tab_week');
  const viewDay = $('view_day');
  const viewWeek = $('view_week');
  function showDay(){ tabDay?.classList.add('active'); tabWeek?.classList.remove('active'); if(viewDay) viewDay.style.display=''; if(viewWeek) viewWeek.style.display='none'; }
  function showWeek(){ tabWeek?.classList.add('active'); tabDay?.classList.remove('active'); if(viewWeek) viewWeek.style.display=''; if(viewDay) viewDay.style.display='none'; }
  tabDay?.addEventListener('click', showDay);
  tabWeek?.addEventListener('click', showWeek);

  // ---------- Strava controls on Plan ----------
  const sbadge = $('strava_badge');
  const sconnect = $('strava_connect');
  const ssync = $('strava_sync');
  const sresync = $('strava_resync');
  const sstatus = $('sync_status');
  function setSyncStatus(msg){ if (sstatus) sstatus.textContent = msg || ''; }
  async function refreshStravaStatus(){
    try{
      const info = await fetchJSON('/oauth/strava/status');
      if (info?.connected){
        if (sbadge) sbadge.textContent = 'Strava: Connected';
        if (sconnect) sconnect.style.display = 'none';
      } else {
        if (sbadge) sbadge.textContent = 'Strava: Not connected';
        if (sconnect) sconnect.style.display = '';
      }
    }catch(e){
      if (sbadge) sbadge.textContent = 'Strava: Unknown';
      if (sconnect) sconnect.style.display = '';
      console.warn('[plan] strava status failed:', e);
    }
  }
  async function runSync(replace=false){
    try{
      setSyncStatus(replace ? 'Re-syncing…' : 'Syncing…');
      const q = replace ? '?replace=true' : '';
      const res = await fetchJSON('/sync/strava' + q, { method: 'POST' });
      setSyncStatus(`Done. Inserted ${res.inserted}, total ${res.total}.`);
    }catch(e){
      setSyncStatus(e?.message || 'Sync failed.');
    }
  }
  ssync?.addEventListener('click', () => void runSync(false));
  sresync?.addEventListener('click', () => {
    if (confirm('This will replace stored activities with a fresh pull from Strava. Continue?')) {
      void runSync(true);
    }
  });

  // ---------- Day view ----------
  const els = {
    date: $('planDate'),
    diet: $('dietSelect'),
    refresh: $('refreshBtn'),
    swapSnack: $('swapSnackBtn'),
    lock: $('lockBtn'),
    tdee: $('tdeePill'),
    train: $('trainPill'),
    prot: $('protPill'),
    carb: $('carbPill'),
    fat: $('fatPill'),
    lockState: $('lockState'),
    meals: $('meals'),
    mealsEmpty: $('mealsEmpty'),
    grocery: $('grocery'),
    gTxt: $('gTxt'),
    gCsv: $('gCsv'),
    status: $('status'),
  };

  function todayISO(d = new Date()) {
    const tz = new Date(d.getTime() - d.getTimezoneOffset() * 60000);
    return tz.toISOString().slice(0, 10);
  }

  function renderPlan(p) {
    const t = p.targets || p.totals || {};
    if (els.tdee)  els.tdee.textContent  = `TDEE: ${t.tdee_kcal ? `${Math.round(t.tdee_kcal)} kcal` : '—'}`;
    if (els.train) els.train.textContent = `Training: ${t.training_kcal ? `${Math.round(t.training_kcal)} kcal` : '—'}`;
    if (els.prot)  els.prot.textContent  = `Protein: ${t.protein_g != null ? `${Math.round(t.protein_g)} g` : '—'}`;
    if (els.carb)  els.carb.textContent  = `Carbs: ${t.carbs_g != null ? `${Math.round(t.carbs_g)} g` : '—'}`;
    if (els.fat)   els.fat.textContent   = `Fat: ${t.fat_g != null ? `${Math.round(t.fat_g)} g` : '—'}`;
    if (els.lockState) els.lockState.textContent = p.locked ? `Plan for ${p.date} is locked.` : `Plan for ${p.date} is editable.`;

    if (els.gTxt) els.gTxt.href = `/v1/plan/${p.date}/grocery.txt`;
    if (els.gCsv) els.gCsv.href = `/v1/plan/${p.date}/grocery.csv`;

    if (els.meals) {
      els.meals.innerHTML = '';
      const meals = p.meals || [];
      if (els.mealsEmpty) els.mealsEmpty.style.display = meals.length ? 'none' : '';
      for (const m of meals) {
        const wrap = document.createElement('div');
        wrap.className = 'meal';
        wrap.innerHTML = `
          <header>
            <strong>${m.title || m.meal_type || m.name || 'Meal'}</strong>
            <span class="muted">${Math.round(m.kcal||0)} kcal · P ${Math.round(m.protein_g||0)}g · C ${Math.round(m.carbs_g||0)}g · F ${Math.round(m.fat_g||0)}g</span>
          </header>
          ${Array.isArray(m.ingredients) ? `<ul style="margin:8px 0 0 18px">${m.ingredients.map(i=>`<li>${i}</li>`).join('')}</ul>` : ''}
          ${m.instructions ? `<div class="muted" style="margin-top:8px">${m.instructions}</div>` : ''}
        `;
        els.meals.appendChild(wrap);
      }
    }

    const gl = p.grocery_list || [];
    if (els.grocery) els.grocery.textContent = gl.length ? gl.join('\n') : '—';
  }

  async function loadPlan() {
    if (!ensureAuth || !ensureAuth()) return;
    const d = (els.date?.value && els.date.value.trim()) || todayISO();
    const diet = (els.diet?.value && els.diet.value.trim()) || 'omnivore';

    if (els.status) els.status.textContent = 'Loading…';
    try {
      const p = await fetchJSON(`/v1/plan/${d}?diet_pref=${encodeURIComponent(diet)}`);
      renderPlan(p);
      if (els.status) els.status.textContent = '';
      if (els.lock) els.lock.textContent = p.locked ? 'Unlock' : 'Lock';
    } catch (e) {
      console.error('[plan] loadPlan failed', e);
      if (els.status) els.status.textContent = e?.message || 'Failed to load plan.';
    }
  }
  async function swapSnack() {
    const d = (els.date?.value && els.date.value.trim()) || todayISO();
    try { await fetchJSON(`/v1/plan/${d}/swap?meal_type=snack`, { method: 'POST' }); loadPlan(); } catch (e) { console.error(e); if (els.status) els.status.textContent = e?.message || 'Swap failed.'; }
  }
  async function toggleLock() {
    const d = (els.date?.value && els.date.value.trim()) || todayISO();
    const toLock = (els.lock?.textContent || '').toLowerCase() !== 'unlock';
    try { await fetchJSON(`/v1/plan/${d}/lock?lock=${toLock ? 'true' : 'false'}`, { method: 'POST' }); loadPlan(); } catch (e) { console.error(e); if (els.status) els.status.textContent = e?.message || 'Lock toggle failed.'; }
  }

  // ---------- Week view ----------
  const W = {
    start: $('week_start'),
    diet: $('week_diet_pref'),
    load: $('week_load'),
    regen: $('week_regen'),
    grid: $('week_grid'),
    gtxt: $('week_g_txt'),
    gcsv: $('week_g_csv'),
    status: $('week_status'),
  };
  function startOfWeekUTC(d){ const dt=new Date(Date.UTC(d.getUTCFullYear(),d.getUTCMonth(),d.getUTCDate())); const dow=dt.getUTCDay(); const off=(dow+6)%7; dt.setUTCDate(dt.getUTCDate()-off); return dt; }
  function datesOfWeek(s){ const arr=[]; for(let i=0;i<7;i++){ const d=new Date(s); d.setUTCDate(s.getUTCDate()+i); arr.push(fmt.dateISO(d)); } return arr; }
  function mealMeta(m){ const x=[]; if(m?.kcal!=null)x.push(`${Math.round(m.kcal)} kcal`); if(m?.protein_g!=null)x.push(`${Math.round(m.protein_g)}g P`); if(m?.carbs_g!=null)x.push(`${Math.round(m.carbs_g)}g C`); if(m?.fat_g!=null)x.push(`${Math.round(m.fat_g)}g F`); return x.join(' • '); }
  function renderDayCard(container, plan){
    const card=document.createElement('div'); card.className='day-card';
    const head=document.createElement('div'); head.className='day-head';
    const h3=document.createElement('h3'); h3.textContent=plan.date;
    const badge=document.createElement('div'); badge.className='pill'; badge.textContent=plan.locked?'Locked':'Draft';
    head.appendChild(h3); head.appendChild(badge); card.appendChild(head);
    const tg=document.createElement('div'); tg.className='targets';
    const t=plan.targets||plan.totals||{};
    [ `TDEE ${Math.round(t.tdee_kcal||0)} kcal`, `P ${Math.round(t.protein_g||0)}g`, `C ${Math.round(t.carbs_g||0)}g`, `F ${Math.round(t.fat_g||0)}g` ]
      .forEach(txt=>{ const el=document.createElement('span'); el.className='pill'; el.textContent=txt; tg.appendChild(el); });
    card.appendChild(tg);
    const mealsWrap=document.createElement('div');
    (plan.meals||[]).forEach(m=>{ const box=document.createElement('div'); box.className='meal-sm';
      const title=document.createElement('div'); title.textContent=`${m.meal_type?.toUpperCase()||'MEAL'} — ${m.title||m.name||'Untitled'}`;
      const meta=document.createElement('div'); meta.className='meta'; meta.textContent=mealMeta(m);
      box.appendChild(title); box.appendChild(meta); mealsWrap.appendChild(box); });
    if((plan.meals||[]).length===0){ const none=document.createElement('div'); none.className='muted'; none.textContent='No meals.'; mealsWrap.appendChild(none); }
    card.appendChild(mealsWrap); container.appendChild(card);
  }
  async function fetchDayPlan(dateISO, dietPref){ return fetchJSON(`/v1/plan/${dateISO}?diet_pref=${encodeURIComponent(dietPref||'omnivore')}`); }
  async function loadWeek(){
    if (W.status) W.status.textContent='Loading…';
    const startISO=W.start?.value; const diet=W.diet?.value||'omnivore';
    if(!startISO){ if (W.status) W.status.textContent='Pick a start date.'; return; }
    const days=datesOfWeek(new Date(startISO+'T00:00:00Z')); if(W.grid) W.grid.innerHTML='';
    try{ const plans=[]; for(const d of days) plans.push(await fetchDayPlan(d,diet)); plans.forEach(p=>renderDayCard(W.grid,p)); window.__glyco_weekPlans=plans; if (W.status) W.status.textContent='Week loaded.'; }
    catch(e){ if (W.status) W.status.textContent=e.message||'Failed to load week.'; }
  }
  function buildCombinedGrocery(format){
    const plans=window.__glyco_weekPlans||[]; const items=new Map();
    for(const p of plans) for(const raw of (p.grocery_list||[])){ const key=(raw||'').trim(); if(!key) continue; items.set(key,(items.get(key)||0)+1); }
    if(format==='csv'){ const lines=['item,count']; for(const [k,v] of items.entries()){ const safe='"'+k.replace(/"/g,'""')+'"'; lines.push(`${safe},${v}`);} return lines.join('\n'); }
    const lines=[]; for(const [k,v] of items.entries()) lines.push(v>1?`- ${k}  x${v}`:`- ${k}`); return lines.join('\n');
  }
  function download(filename, text){
    const blob=new Blob([text],{type:'text/plain;charset=utf-8'}); const url=URL.createObjectURL(blob);
    const a=document.createElement('a'); a.href=url; a.download=filename; document.body.appendChild(a); a.click();
    setTimeout(()=>{ URL.revokeObjectURL(url); a.remove(); },0);
  }

  // ---------- Init (safe order) ----------
  ready(async () => {
    if (setActiveNav) setActiveNav();
    if (!ensureAuth || !ensureAuth()) return;

    // Always show Day view first
    showDay();

    // Pre-fill date BEFORE first fetch
    if (els.date && !els.date.value) els.date.value = todayISO();

    // Load user to set diet, then fetch plan
    try {
      const me = await fetchJSON('/users/me');
      if (els.diet && !els.diet.value && me?.diet_pref) els.diet.value = me.diet_pref;
    } catch (e) {
      console.warn('[plan] /users/me failed (continuing):', e);
    }

    // Wire buttons
    els.refresh?.addEventListener('click', loadPlan);
    els.swapSnack?.addEventListener('click', swapSnack);
    els.lock?.addEventListener('click', toggleLock);

    // Week defaults & controls
    const wkStart = (function(){ const t=new Date(); const wk=startOfWeekUTC(t); return fmt.dateISO(wk); })();
    if (W.start && !W.start.value) W.start.value = wkStart;
    try {
      const me2 = await fetchJSON('/users/me');
      if (W.diet && !W.diet.value && me2?.diet_pref) W.diet.value = me2.diet_pref;
    } catch {}

    W.load?.addEventListener('click', () => loadWeek());
    W.regen?.addEventListener('click', () => loadWeek());
    W.gtxt?.addEventListener('click', (e)=>{ e.preventDefault(); download('grocery-week.txt', buildCombinedGrocery('txt')); });
    W.gcsv?.addEventListener('click', (e)=>{ e.preventDefault(); download('grocery-week.csv', buildCombinedGrocery('csv')); });

    // Strava badge
    void refreshStravaStatus();

    // Finally, fetch the plan
    await loadPlan();
  });
})();
