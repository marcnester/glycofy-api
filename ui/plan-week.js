// ui/plan-week.js — fetch each day’s plan, show meals, aggregate weekly grocery (CSP-safe)
(function () {
  const glyco = window.__glyco || {};
  if (!glyco.fetchJSON) {
    console.error('[week] /ui/app.js must load before /ui/plan-week.js');
    return;
  }
  const { $, fmt, fetchJSON, ensureAuth } = glyco;

  // ---------- Small helpers ----------
  function ok(msg){ const el=$('alert_ok'); if(el){ el.textContent=msg; el.style.display='block'; } }
  function err(msg){ const el=$('alert_err'); if(el){ el.textContent=msg; el.style.display='block'; } }
  function clearAlerts(){ ['alert_ok','alert_err'].forEach(id => { const el=$(id); if(el){ el.style.display='none'; el.textContent=''; } }); }

  function mondayUTC(d){
    // Monday as start-of-week (UTC)
    const dt = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()));
    const dow = dt.getUTCDay();           // 0=Sun..6=Sat
    const offset = (dow + 6) % 7;         // Sun->6, Mon->0, ...
    dt.setUTCDate(dt.getUTCDate() - offset);
    return dt;
  }
  function weekDates(startUtc){
    const list = [];
    for (let i=0;i<7;i++){
      const d = new Date(startUtc);
      d.setUTCDate(startUtc.getUTCDate() + i);
      list.push(fmt.dateISO(d));
    }
    return list;
  }

  function setDefaults(){
    const startEl = $('week_start');
    if (startEl && !startEl.value) startEl.value = fmt.dateISO(mondayUTC(new Date()));
  }

  // ---------- Rendering ----------
  const mName  = (m) => m?.name ?? m?.title ?? m?.meal_type ?? 'Untitled';
  const mKcal  = (m) => m?.kcal ?? m?.calories ?? 0;
  const mProt  = (m) => m?.protein_g ?? m?.protein ?? 0;
  const mCarb  = (m) => m?.carbs_g   ?? m?.carbs   ?? 0;
  const mFat   = (m) => m?.fat_g     ?? m?.fat     ?? 0;

  function metaLine(m){
    return `${Math.round(mKcal(m))} kcal · ${Math.round(mProt(m))}g P · ${Math.round(mCarb(m))}g C · ${Math.round(mFat(m))}g F`;
    }

  function dayTotals(meals){
    return meals.reduce((a,m)=>({
      kcal: a.kcal + mKcal(m),
      p:    a.p    + mProt(m),
      c:    a.c    + mCarb(m),
      f:    a.f    + mFat(m),
    }), {kcal:0,p:0,c:0,f:0});
  }

  function renderDayCard(container, plan){
    const meals = Array.isArray(plan?.meals) ? plan.meals : [];
    const totals = dayTotals(meals);

    const card = document.createElement('div');
    card.className = 'day-card';

    // Header
    const head = document.createElement('div');
    head.className = 'day-head';
    const h3 = document.createElement('h3');
    h3.textContent = plan.date;
    const badge = document.createElement('div');
    badge.className = 'badge';
    badge.textContent = 'Draft';
    head.appendChild(h3);
    head.appendChild(badge);
    card.appendChild(head);

    // Macro pills (computed)
    const pillsWrap = document.createElement('div');
    pillsWrap.className = 'targets';
    ['TDEE 0 kcal', `P ${Math.round(totals.p)}g`, `C ${Math.round(totals.c)}g`, `F ${Math.round(totals.f)}g`]
      .forEach(text => { const pill = document.createElement('span'); pill.className = 'pill'; pill.textContent = text; pillsWrap.appendChild(pill); });
    card.appendChild(pillsWrap);

    // Meals
    const list = document.createElement('div'); list.className = 'meals';
    if (meals.length === 0){
      const none = document.createElement('div'); none.className = 'muted'; none.textContent = 'No meals generated.'; list.appendChild(none);
    } else {
      for (const m of meals){
        const box = document.createElement('div'); box.className = 'meal';
        const title = document.createElement('div'); title.textContent = `MEAL — ${mName(m)}`;
        const meta  = document.createElement('div'); meta.className = 'meta'; meta.textContent = metaLine(m);
        box.appendChild(title); box.appendChild(meta);
        list.appendChild(box);
      }
    }
    card.appendChild(list);

    container.appendChild(card);
  }

  // ---------- Data ----------
  async function fetchDayPlan(dateISO, dietPref){
    const url = `/v1/plan/${dateISO}?diet_pref=${encodeURIComponent(dietPref || 'omnivore')}`;
    return fetchJSON(url);
  }

  async function loadWeek(){
    clearAlerts();
    const startStr = $('week_start')?.value;
    const diet     = $('diet_pref')?.value || 'omnivore';
    if (!startStr){ err('Pick a start date.'); return; }

    const dates = weekDates(new Date(startStr + 'T00:00:00Z'));
    const grid = $('week_grid');
    if (grid) grid.innerHTML = '';

    try{
      // Load all 7 days in parallel
      const plans = await Promise.all(dates.map(d => fetchDayPlan(d, diet)));

      // Render
      for (const p of plans) renderDayCard(grid, p);

      // Save for export
      window.__glyco_weekPlans = plans;
      ok('Week loaded.');
    }catch(e){
      console.error('[week] load failed', e);
      err(e.message || 'Failed to load week.');
    }
  }

  // ---------- Export from ingredients (not grocery_list) ----------
  function aggregateIngredients(plans){
    const counts = new Map();
    for (const p of plans || []){
      for (const m of (p?.meals || [])){
        if (Array.isArray(m.ingredients)){
          for (const raw of m.ingredients){
            const key = (raw || '').trim();
            if (!key) continue;
            counts.set(key, (counts.get(key) || 0) + 1);
          }
        }
      }
    }
    return counts;
  }

  function download(filename, text){
    const blob = new Blob([text], {type:'text/plain;charset=utf-8'});
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a'); a.href = url; a.download = filename; document.body.appendChild(a);
    a.click(); setTimeout(()=>{ URL.revokeObjectURL(url); a.remove(); }, 0);
  }

  function exportTxt(){
    const map = aggregateIngredients(window.__glyco_weekPlans);
    const lines = [];
    for (const [k,v] of map.entries()){
      lines.push(v>1 ? `- ${k}  x${v}` : `- ${k}`);
    }
    download('grocery-week.txt', lines.join('\n'));
  }

  function exportCsv(){
    const map = aggregateIngredients(window.__glyco_weekPlans);
    const lines = ['item,count'];
    for (const [k,v] of map.entries()){
      const safe = '"' + k.replace(/"/g, '""') + '"';
      lines.push(`${safe},${v}`);
    }
    download('grocery-week.csv', lines.join('\n'));
  }

  // ---------- Init ----------
  document.addEventListener('DOMContentLoaded', () => {
    if (!ensureAuth || !ensureAuth()) return;

    setDefaults();

    // Default diet from user if empty
    fetchJSON('/users/me').then(u => {
      const el = $('diet_pref'); if (el && !el.value && u?.diet_pref) el.value = u.diet_pref;
    }).catch(()=>{});

    $('load_btn')?.addEventListener('click', () => { void loadWeek(); });
    $('regen_btn')?.addEventListener('click', () => { void loadWeek(); }); // same as load for now
    $('export_txt')?.addEventListener('click', exportTxt);
    $('export_csv')?.addEventListener('click', exportCsv);

    // Auto-load current week
    void loadWeek();
  });
})();