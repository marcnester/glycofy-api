// ui/summary.js
(function () {
  const { $, fetchJSON, ensureAuth, fmt } = window.__glyco || {};
  if (!window.__glyco) { console.error('[summary] __glyco bootstrap missing'); return; }

  const el = {
    from: $('from'),
    to: $('to'),
    diet: $('diet_pref'),
    refresh: $('refresh'),
    rows: $('rows'),
    kTrain: $('kcal_training'),
    kPlan: $('kcal_planned'),
    actCount: $('act_count'),
    donut: $('donut'),
    empty: $('empty_state'),
    badge: $('strava_badge'),
    connect: $('connect_btn'),
    sync: $('sync_btn'),
    resync: $('resync_btn'),
    syncStatus: $('sync_status'),
  };

  function iso(d) {
    const dt = d instanceof Date ? d : new Date(d);
    const tzo = dt.getTimezoneOffset() * 60000;
    return new Date(dt - tzo).toISOString().slice(0, 10);
  }
  function defaultRange() {
    const to = new Date();
    const from = new Date();
    from.setDate(to.getDate() - 29);
    return { from: iso(from), to: iso(to) };
  }

  async function refreshStravaStatus() {
    // Only toggle visibility; the link already points to /oauth/strava/start
    try {
      const info = await fetchJSON('/oauth/strava/status');
      if (info?.connected) {
        if (el.badge) el.badge.textContent = 'Strava: Connected';
        if (el.connect) el.connect.style.display = 'none';
      } else {
        if (el.badge) el.badge.textContent = 'Strava: Not connected';
        if (el.connect) el.connect.style.display = '';
      }
    } catch {
      if (el.badge) el.badge.textContent = 'Strava: Unknown';
      if (el.connect) el.connect.style.display = '';
    }
  }
  function setSyncStatus(msg) { if (el.syncStatus) el.syncStatus.textContent = msg || ''; }
  async function runSync(replace=false){
    try{
      setSyncStatus(replace ? 'Re-syncing…' : 'Syncing…');
      const q = replace ? '?replace=true' : '';
      const res = await fetchJSON('/sync/strava' + q, { method: 'POST' });
      setSyncStatus(`Done. Inserted ${res.inserted}, total ${res.total}.`);
      await load(); // refresh summary after sync
    }catch(e){
      setSyncStatus(e?.message || 'Sync failed.');
    }
  }

  function inRange(dateISO, fromISO, toISO) { return dateISO >= fromISO && dateISO <= toISO; }
  function kcalForActivity(a) { return typeof a.kcal === 'number' ? a.kcal : Math.round((a.duration_sec || 0) / 60 * 7); }

  function groupByDateAndSport(items, fromISO, toISO) {
    const days = new Map();
    for (const it of items) {
      const d = fmt.dateISO(it.start_time || it.date || new Date());
      if (!inRange(d, fromISO, toISO)) continue;
      const sport = (it.type || it.sport || 'Workout').toString();
      const kc = kcalForActivity(it);
      let rec = days.get(d);
      if (!rec) { rec = { bySport: new Map(), trainKcal: 0, plannedKcal: 0 }; days.set(d, rec); }
      rec.trainKcal += kc;
      rec.bySport.set(sport, (rec.bySport.get(sport) || 0) + kc);
    }
    return days;
  }

  function formatNumber(n) { return (Math.round(n)).toLocaleString(); }

  function renderTable(daysMap) {
    el.rows.innerHTML = '';
    const days = Array.from(daysMap.keys()).sort();
    if (days.length === 0) {
      el.empty.style.display = '';
      return;
    }
    el.empty.style.display = 'none';

    for (const d of days) {
      const rec = daysMap.get(d);
      const tr = document.createElement('tr');

      const tdDate = document.createElement('td'); tdDate.textContent = d; tr.appendChild(tdDate);
      const tdTrain = document.createElement('td'); tdTrain.textContent = formatNumber(rec.trainKcal); tr.appendChild(tdTrain);
      const tdPlan = document.createElement('td'); tdPlan.textContent = formatNumber(rec.plannedKcal || 0); tr.appendChild(tdPlan);

      const tdBySport = document.createElement('td');
      const pieces = [];
      for (const [sport, kc] of Array.from(rec.bySport.entries()).sort((a,b)=>b[1]-a[1])) {
        pieces.push(`${sport}: ${formatNumber(kc)} kcal`);
      }
      tdBySport.textContent = pieces.join(' · ');
      tr.appendChild(tdBySport);

      el.rows.appendChild(tr);
    }
  }

  function renderKPIs(daysMap, activityCount) {
    let totalTrain = 0, totalPlanned = 0;
    for (const rec of daysMap.values()) {
      totalTrain += rec.trainKcal;
      totalPlanned += rec.plannedKcal || 0;
    }
    el.kTrain.textContent = formatNumber(totalTrain);
    el.kPlan.textContent = formatNumber(totalPlanned);
    el.actCount.textContent = String(activityCount);
  }

  function renderDonut(daysMap) {
    el.donut.innerHTML = '';
    const sportTotals = new Map();
    for (const rec of daysMap.values()) {
      for (const [sport, kc] of rec.bySport.entries()) {
        sportTotals.set(sport, (sportTotals.get(sport) || 0) + kc);
      }
    }
    const entries = Array.from(sportTotals.entries()).sort((a,b)=>b[1]-a[1]);
    const total = entries.reduce((s, [,kc]) => s + kc, 0);
    if (total <= 0) {
      const none = document.createElement('div');
      none.className = 'muted';
      none.textContent = 'No training kcal in this range.';
      el.donut.appendChild(none);
      return;
    }

    const size = 160, radius = 70, cx = size/2, cy = size/2, circ = 2*Math.PI*radius;
    let acc = 0;
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('width', String(size));
    svg.setAttribute('height', String(size));
    svg.setAttribute('viewBox', `0 0 ${size} ${size}`);

    const bg = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
    bg.setAttribute('cx', String(cx)); bg.setAttribute('cy', String(cy));
    bg.setAttribute('r', String(radius));
    bg.setAttribute('fill', 'none'); bg.setAttribute('stroke', '#132132'); bg.setAttribute('stroke-width', '18');
    svg.appendChild(bg);

    function pickColor(i) { const hue = (i * 47) % 360; return `hsl(${hue} 70% 60%)`; }

    entries.forEach(([sport, kc], i) => {
      const frac = kc / total;
      const len = circ * frac;
      const seg = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
      seg.setAttribute('cx', String(cx)); seg.setAttribute('cy', String(cy));
      seg.setAttribute('r', String(radius));
      seg.setAttribute('fill', 'none');
      seg.setAttribute('stroke', pickColor(i));
      seg.setAttribute('stroke-width', '18');
      seg.setAttribute('stroke-dasharray', `${len} ${circ - len}`);
      seg.setAttribute('stroke-dashoffset', String(-acc));
      seg.setAttribute('transform', `rotate(-90 ${cx} ${cy})`);
      svg.appendChild(seg);
      acc += len;
    });

    el.donut.appendChild(svg);

    const legend = document.createElement('div');
    legend.className = 'legend';
    entries.forEach(([sport, kc], i) => {
      const row = document.createElement('div');
      row.className = 'legend-row';
      const left = document.createElement('div');
      const key = document.createElement('span');
      key.className = 'legend-key';
      key.style.background = pickColor(i);
      left.appendChild(key);
      left.appendChild(document.createTextNode(sport));
      const right = document.createElement('div');
      right.textContent = `${formatNumber(kc)} kcal`;
      row.appendChild(left);
      row.appendChild(right);
      legend.appendChild(row);
    });
    el.donut.appendChild(legend);
  }

  async function load() {
    if (!ensureAuth()) return;

    const def = defaultRange();
    const fromISO = el.from.value || def.from;
    const toISO = el.to.value || def.to;

    const data = await fetchJSON('/activities?page=1&page_size=1000');
    const items = Array.isArray(data) ? data : (data.items || []);
    const daysMap = groupByDateAndSport(items, fromISO, toISO);

    el.empty.style.display = (Array.from(daysMap.keys()).length === 0) ? '' : 'none';
    renderTable(daysMap);
    renderKPIs(daysMap, items.filter(a => inRange(fmt.dateISO(a.start_time || a.date || new Date()), fromISO, toISO)).length);
    renderDonut(daysMap);
  }

  document.addEventListener('DOMContentLoaded', async () => {
    if (!ensureAuth()) return;

    const { from, to } = defaultRange();
    if (el.from && !el.from.value) el.from.value = from;
    if (el.to && !el.to.value) el.to.value = to;

    fetchJSON('/users/me').then(u => {
      if (el.diet && !el.diet.value && u?.diet_pref) el.diet.value = u.diet_pref;
    }).catch(()=>{});

    el.refresh?.addEventListener('click', () => { void load(); });

    await refreshStravaStatus();
    el.sync?.addEventListener('click', () => void runSync(false));
    el.resync?.addEventListener('click', () => {
      if (confirm('This will delete your stored activities and pull fresh from Strava. Continue?')) {
        void runSync(true);
      }
    });

    void load();
  });
})();