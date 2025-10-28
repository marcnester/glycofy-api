// ui/activities.js — Activities with live sync + client-side aggregation (CSP-safe)
(function () {
  const glyco = window.__glyco || {};
  if (!glyco.fetchJSON) {
    console.error("[activities] __glyco bootstrap missing (app.js must load first).");
    return;
  }
  const { $, fmt, fetchJSON, ensureAuth, setActiveNav } = glyco;

  // DOM
  const elRows = $('rows');
  const elPage = $('page');
  const elPrev = $('prev');
  const elNext = $('next');
  const elPs   = $('ps');
  const elErr  = $('act-error');

  const elFrom = $('from');
  const elTo   = $('to');
  const elRefresh = $('refresh');
  const elKcalTraining = $('kcal_training');
  const elKcalPlanned  = $('kcal_planned');
  const elActCount     = $('act_count');
  const elDonut        = $('donut');
  const elEmpty        = $('empty_state');

  const elStravaBadge  = $('strava_badge');
  const elSyncBtn      = $('sync_btn');
  const elFullSyncBtn  = $('full_sync_btn');
  const elSyncStatus   = $('sync_status');

  let page = 1;
  let pageSize = Number(elPs?.value || 25);
  let total = 0;

  function setErr(msg) {
    if (!elErr) return;
    elErr.textContent = msg || '';
    if (msg) { elErr.style.display = ''; }
    else { elErr.style.display = 'none'; }
  }

  function todayISO(d = new Date()) {
    const tz = new Date(d.getTime() - d.getTimezoneOffset() * 60000);
    return tz.toISOString().slice(0, 10);
  }
  function daysAgoISO(n) {
    const d = new Date(); d.setDate(d.getDate() - n);
    return todayISO(d);
  }
  function readDateISO(el, fallbackISO) {
    const raw = (el && el.value) ? String(el.value).trim() : '';
    if (!raw) return fallbackISO;
    if (/^\d{4}-\d{2}-\d{2}$/.test(raw)) return raw;
    const t = Date.parse(raw);
    if (!isNaN(t)) return todayISO(new Date(t));
    return fallbackISO;
  }

  function showStatus(text, extraClass) {
    if (!elSyncStatus) return;
    elSyncStatus.className = 'pill' + (extraClass ? ' ' + extraClass : '');
    elSyncStatus.textContent = text;
    elSyncStatus.style.display = '';
  }
  function hideStatus() {
    if (!elSyncStatus) return;
    elSyncStatus.style.display = 'none';
    elSyncStatus.textContent = '';
    elSyncStatus.className = 'pill';
  }

  function fmtDurationSecToMin(sec) { if (sec == null) return '—'; const m = Math.round(sec / 60); return `${m} min`; }
  function fmtMetersToKm(m) { if (m == null) return '—'; return `${fmt.round(m / 1000, 1)} km`; }

  // Sport normalization
  function normalizeSport(a) {
    const label = a.sport_label || a.sport;
    if (label && typeof label === 'string') return label;

    const t = (a.type || '').trim();
    const name = (a.name || '').toLowerCase();
    const dist = Number(a.distance_m || 0);

    const map = {
      Run: 'Running',
      TrailRun: 'Running',
      Ride: 'Cycling',
      VirtualRide: 'Cycling (Virtual)',
      WeightTraining: 'Strength',
      StrengthTraining: 'Strength',
      Walk: 'Walking',
      Hike: 'Hiking',
      Swim: 'Swimming',
      Rowing: 'Rowing',
      IndoorCycling: 'Cycling (Virtual)',
      Crossfit: 'CrossFit',
      Yoga: 'Yoga',
      Elliptical: 'Elliptical',
      StairStepper: 'Stair Stepper',
      NordicSki: 'Skiing',
      AlpineSki: 'Skiing',
      Snowboard: 'Snowboard',
    };
    if (map[t]) return map[t];

    if (t === 'Workout' || !t) {
      if (dist > 1000) {
        if (name.includes('zwift') || name.includes('trainerroad') || name.includes('virtual'))
          return 'Cycling (Virtual)';
        return 'Cycling';
      }
      if (name.includes('upper body') || name.includes('strength') || name.includes('core'))
        return 'Strength';
      return 'Workout';
    }
    return t || 'Workout';
  }

  // Paging table
  function updatePagingUI() {
    if (elPage) elPage.textContent = String(page);
    const maxPage = Math.max(1, Math.ceil(total / pageSize) || 1);
    if (elPrev) elPrev.disabled = page <= 1;
    if (elNext) elNext.disabled = page >= maxPage;
  }

  function renderActivityTable(items) {
    if (!elRows) return;
    elRows.innerHTML = '';
    if (!Array.isArray(items) || items.length === 0) {
      const tr = document.createElement('tr');
      const td = document.createElement('td');
      td.colSpan = 5;
      td.textContent = 'No activities found.';
      tr.appendChild(td);
      elRows.appendChild(tr);
      return;
    }
    for (const a of items) {
      const tr = document.createElement('tr');

      const tdDate = document.createElement('td');
      tdDate.textContent = fmt.dateISO(a.start_time || a.date || a.created_at || new Date());
      tr.appendChild(tdDate);

      const tdType = document.createElement('td');
      tdType.textContent = normalizeSport(a);
      tr.appendChild(tdType);

      const tdDur = document.createElement('td');
      tdDur.textContent = fmtDurationSecToMin(a.duration_sec ?? a.duration ?? a.elapsed_sec ?? null);
      tr.appendChild(tdDur);

      const tdKcal = document.createElement('td');
      tdKcal.textContent = a.kcal != null ? fmt.kcal(a.kcal) : '—';
      tr.appendChild(tdKcal);

      const tdDist = document.createElement('td');
      tdDist.textContent = fmtMetersToKm(a.distance_m ?? a.distance ?? null);
      tr.appendChild(tdDist);

      elRows.appendChild(tr);
    }
  }

  async function loadActivityPage() {
    if (!ensureAuth || !ensureAuth()) return;
    try {
      setErr('');
      updatePagingUI();
      const url = `/activities?page=${encodeURIComponent(page)}&page_size=${encodeURIComponent(pageSize)}`;
      const data = await fetchJSON(url);
      const items = Array.isArray(data) ? data : (data.items || []);
      total = typeof data.total === 'number'
        ? data.total
        : (Array.isArray(data) ? data.length : items.length);
      renderActivityTable(items);
      updatePagingUI();
    } catch (e) {
      console.error('[activities] loadActivityPage failed', e);
      setErr(e?.message || 'Failed to load activities.');
    }
  }

  // Client-side summary
  function renderDonut(list) {
    if (!elDonut) return;
    elDonut.innerHTML = '';
    if (!Array.isArray(list) || list.length === 0) return;

    const totalK = list.reduce((s, x) => s + (x.kcal || 0), 0) || 1;
    const size = 160, r = 60, cx = size/2, cy = size/2, stroke = 20;
    const colors = ["#ef4444","#f59e0b","#10b981","#3b82f6","#8b5cf6","#14b8a6","#eab308","#f97316"];

    const svg = document.createElementNS("http://www.w3.org/2000/svg","svg");
    svg.setAttribute("width", size);
    svg.setAttribute("height", size);

    let angle = -90;
    function arcLen(val){ return (val/totalK)*360; }

    list.forEach((seg, idx) => {
      const val = seg.kcal || 0;
      const ang = arcLen(val);
      if (val <= 0 || ang <= 0) return;

      const large = ang > 180 ? 1 : 0;
      const startRad = angle * Math.PI/180;
      const endRad = (angle + ang) * Math.PI/180;
      const x1 = cx + r * Math.cos(startRad), y1 = cy + r * Math.sin(startRad);
      const x2 = cx + r * Math.cos(endRad),   y2 = cy + r * Math.sin(endRad);

      const path = document.createElementNS("http://www.w3.org/2000/svg","path");
      path.setAttribute("d", `M ${x1} ${y1} A ${r} ${r} 0 ${large} 1 ${x2} ${y2}`);
      path.setAttribute("fill","none");
      path.setAttribute("stroke-width", stroke);
      path.setAttribute("stroke", colors[idx % colors.length]);
      svg.appendChild(path);

      angle += ang;
    });

    elDonut.appendChild(svg);

    const legend = document.createElement('div');
    legend.className = 'legend';
    list.forEach((seg, idx) => {
      const row = document.createElement('div');
      row.className = 'legend-row';
      const left = document.createElement('div');
      const key = document.createElement('span');
      key.className = 'legend-key';
      key.style.background = colors[idx % colors.length];
      left.appendChild(key);
      left.appendChild(document.createTextNode(seg.sport || 'Workout'));
      const right = document.createElement('div');
      right.textContent = `${Math.round(seg.kcal || 0)} kcal`;
      row.appendChild(left); row.appendChild(right);
      legend.appendChild(row);
    });
    elDonut.appendChild(legend);
  }

  function renderSummaryTable(days) {
    const sumRows = $('sum_rows');
    if (!sumRows) return;
    sumRows.innerHTML = '';
    if (!Array.isArray(days) || days.length === 0) {
      const tr = document.createElement('tr');
      const td = document.createElement('td');
      td.colSpan = 4; td.textContent = 'No activities in this range.';
      tr.appendChild(td); sumRows.appendChild(tr);
      return;
    }
    days.forEach(d => {
      const tr = document.createElement('tr');
      const tdDate = document.createElement('td'); tdDate.textContent = d.date || '—';
      const tdTr = document.createElement('td');   tdTr.textContent = d.training_kcal != null ? d.training_kcal : 0;
      const tdPl = document.createElement('td');   tdPl.textContent = d.planned_kcal != null ? d.planned_kcal : 0;
      const tdBy = document.createElement('td');   tdBy.textContent = d.by_sport_text || '—';
      tr.appendChild(tdDate); tr.appendChild(tdTr); tr.appendChild(tdPl); tr.appendChild(tdBy);
      sumRows.appendChild(tr);
    });
  }

  async function fetchActivitiesAll() {
    const url = `/activities?page=1&page_size=2000`;
    const data = await fetchJSON(url);
    if (Array.isArray(data)) return data;
    if (data && Array.isArray(data.items)) return data.items;
    return [];
  }

  function aggregateClientSide(items, fromISO, toISO) {
    const within = (dISO) => (dISO >= fromISO && dISO <= toISO);

    const daysMap = new Map();
    const sportMap = new Map();
    let actCount = 0;
    let totalK = 0;

    items.forEach(a => {
      const date = fmt.dateISO(a.start_time || a.date || new Date());
      if (!within(date)) return;
      const kcal = Math.round(Number(a.kcal || 0));
      const sport = normalizeSport(a);

      const d = daysMap.get(date) || { date, training_kcal: 0, planned_kcal: 0, by_sport: {} };
      d.training_kcal += kcal;
      d.by_sport[sport] = (d.by_sport[sport] || 0) + kcal;
      daysMap.set(date, d);

      sportMap.set(sport, (sportMap.get(sport) || 0) + kcal);
      actCount += 1;
      totalK += kcal;
    });

    const days = Array.from(daysMap.values()).sort((a,b)=> (a.date < b.date ? -1 : 1));
    days.forEach(d => {
      const parts = Object.entries(d.by_sport).map(([k,v]) => `${k}: ${v} kcal`);
      d.by_sport_text = parts.join(' · ');
    });

    const totals_by_sport = Array.from(sportMap.entries()).map(([sport, kcal]) => ({ sport, kcal }));
    return {
      total_training_kcal: totalK,
      total_planned_kcal: 0,
      activity_count: actCount,
      totals_by_sport,
      days
    };
  }

  async function loadSummary() {
    const defFrom = daysAgoISO(30);
    const defTo   = todayISO();
    const fromISO = readDateISO(elFrom, defFrom);
    const toISO   = readDateISO(elTo,   defTo);
    if (elFrom && elFrom.value !== fromISO) elFrom.value = fromISO;
    if (elTo && elTo.value !== toISO) elTo.value = toISO;

    try {
      const items = await fetchActivitiesAll();
      const data = aggregateClientSide(items, fromISO, toISO);

      if (elKcalTraining) elKcalTraining.textContent = (data.total_training_kcal || 0).toLocaleString();
      if (elKcalPlanned)  elKcalPlanned.textContent  = (data.total_planned_kcal  || 0).toLocaleString();
      if (elActCount)     elActCount.textContent     = (data.activity_count      || 0).toString();

      renderDonut(data.totals_by_sport || []);
      renderSummaryTable(data.days || []);

      if (elEmpty) elEmpty.style.display = (!data.days || data.days.length === 0) ? '' : 'none';
    } catch (e) {
      console.error('[activities] loadSummary failed:', e);
      if (elKcalTraining) elKcalTraining.textContent = '0';
      if (elKcalPlanned)  elKcalPlanned.textContent  = '0';
      if (elActCount)     elActCount.textContent     = '0';
      renderDonut([]);
      renderSummaryTable([]);
      if (elEmpty) elEmpty.style.display = '';
    }
  }

  // Strava status + Sync (with fallbacks)
  async function fetchStravaStatus() {
    // Try new path
    try { return await fetchJSON('/oauth/strava/status'); }
    catch (e) {
      if (String(e.message || '').includes('404')) {
        // Fallback to legacy
        try { return await fetchJSON('/oauth/status'); }
        catch { throw e; }
      } else { throw e; }
    }
  }
  async function startStravaOAuth() {
    // Try new redirecting path
    try {
      location.href = '/oauth/strava/start';
    } catch {}
    // If server uses start-url pattern, fetch and redirect
    try {
      const j = await fetchJSON('/oauth/start-url');
      if (j && j.authorize_url) location.href = j.authorize_url;
    } catch {}
  }

  async function loadStravaBadge() {
    if (!elStravaBadge) return;
    try {
      const s = await fetchStravaStatus();
      if (s && (s.connected || s.linked)) {
        elStravaBadge.textContent = 'Strava: Connected';
        elStravaBadge.classList.add('ok'); elStravaBadge.classList.remove('warn');
      } else {
        elStravaBadge.textContent = 'Strava: Not connected';
        elStravaBadge.classList.add('warn'); elStravaBadge.classList.remove('ok');
      }
    } catch {
      // Hide badge entirely if endpoint is unknown on this server
      elStravaBadge.style.display = 'none';
    }
  }

  async function doStravaSync(full = false) {
    if (elSyncBtn) elSyncBtn.disabled = true;
    if (elFullSyncBtn) elFullSyncBtn.disabled = true;
    showStatus(full ? 'Full re-sync started' : 'Sync started', 'spin');

    try {
      const res = await fetchJSON(`/sync/strava?replace=${full ? 'true' : 'false'}`, { method: 'POST' });
      const created = res?.created ?? null;
      const updated = res?.updated ?? null;
      const msg = (created != null || updated != null)
        ? `Sync complete — created ${created||0}, updated ${updated||0}`
        : 'Sync complete';
      showStatus(msg, 'ok');

      await loadActivityPage();
      await loadSummary();
      await loadStravaBadge();
    } catch (e) {
      console.error(e);
      showStatus(e?.message || 'Sync failed', 'err');
    } finally {
      if (elSyncBtn) elSyncBtn.disabled = false;
      if (elFullSyncBtn) elFullSyncBtn.disabled = false;
      setTimeout(hideStatus, 4000);
    }
  }

  // Init
  document.addEventListener('DOMContentLoaded', () => {
    if (setActiveNav) setActiveNav();

    if (!ensureAuth || !ensureAuth()) return;

    if (elFrom && !elFrom.value) elFrom.value = daysAgoISO(30);
    if (elTo && !elTo.value) elTo.value = todayISO();

    elPrev?.addEventListener('click', () => { if (page > 1) { page -= 1; void loadActivityPage(); } });
    elNext?.addEventListener('click', () => { page += 1; void loadActivityPage(); });
    elPs?.addEventListener('change', () => {
      const v = Number(elPs.value);
      pageSize = Number.isFinite(v) && v > 0 ? v : 25;
      page = 1;
      void loadActivityPage();
    });

    elRefresh?.addEventListener('click', () => loadSummary());
    elSyncBtn?.addEventListener('click', () => doStravaSync(false));
    elFullSyncBtn?.addEventListener('click', () => doStravaSync(true));

    loadActivityPage();
    loadSummary();
    loadStravaBadge();
  });
})();
