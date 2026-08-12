// ui/activities.js — fast Strava badge + robust donut (kcal or counts), CSP-safe
(function () {
  // -------- immediate, no-core Strava status ping (snappy badge) --------
  (function quickBadge() {
    const badgeEl = document.getElementById('strava_badge');
    if (!badgeEl) return;
    fetch('/oauth/strava/status', { credentials: 'include' })
      .then(r => r.json().catch(() => ({})))
      .then(j => {
        const s = j?.strava || j || {};
        const configured = s.configured !== false;
        const linked = !!s.linked;
        if (!configured) {
          badgeEl.textContent = 'Unavailable';
          badgeEl.className = 'pill warn';
          return;
        }
        badgeEl.textContent = linked ? 'Connected' : 'Not connected';
        badgeEl.className = 'pill ' + (linked ? 'ok' : 'warn');
      })
      .catch(() => {
        badgeEl.textContent = 'Unavailable';
        badgeEl.className = 'pill warn';
      });
  })();

  // -------- wait for core (but don’t block UX if it’s slow) --------
  function waitForCore(ms = 2000) {
    const t0 = Date.now();
    return new Promise(res => {
      (function poll() {
        if (window.__glyco && window.__glyco.fetchJSON) return res(window.__glyco);
        if (Date.now() - t0 > ms) return res({});
        setTimeout(poll, 25);
      })();
    });
  }

  // tiny DOM helpers
  const $ = (id) => document.getElementById(id);
  const q = (sel) => document.querySelector(sel);
  const pick = (...c) => c.map(s => (typeof s === 'string' ? q(s) : s)).find(Boolean) || null;

  // refs
  const elRows = $('rows'), elPage = $('page'), elPrev = $('prev'), elNext = $('next'), elPs = $('ps'), elErr = $('act-error');
  const elFrom = $('from'), elTo = $('to'), elRefresh = $('refresh');
  const elKcalTraining = $('kcal_training'), elKcalPlanned = $('kcal_planned'), elActCount = $('act_count');
  const elDonut = $('donut'), elDonutMode = $('donut_mode'), elEmpty = $('empty_state');
  const elStravaBadge = $('strava_badge'), elSyncBtn = $('sync_btn'), elFullSyncBtn = $('full_sync_btn'), elSyncStatus = $('sync_status');
  const elSumRows = $('sum_rows'), elSumPrev = $('sum_prev'), elSumNext = $('sum_next'), elSumPage = $('sum_page'), elSumPs = $('sum_ps');
  const elLogout = $('logout_btn');
  const elCsv = $('dl_csv') || pick('[data-download="csv"]','a[href="#csv"]','a[href="#CSV"]','a[href$="activities.csv"]');

  // state
  let page = 1, pageSize = 10, total = 0;
  let sumPage = 1, sumPageSize = 10, lastSummaryDays = [];
  const API_MAX = 500;

  // utils
  function setErr(m){ if(!elErr) return; elErr.textContent=m||''; elErr.style.display=m?'':'none'; }
  function todayISO(d=new Date()){ const t=new Date(d.getTime()-d.getTimezoneOffset()*60000); return t.toISOString().slice(0,10); }
  function daysAgoISO(n){ const d=new Date(); d.setDate(d.getDate()-n); return todayISO(d); }
  function readDateISO(el, fb){ const raw=(el&&el.value)?String(el.value).trim():''; if(!raw) return fb; if(/^\d{4}-\d{2}-\d{2}$/.test(raw)) return raw; const t=Date.parse(raw); return isNaN(t)?fb:todayISO(new Date(t)); }
  function showStatus(t, cls){ if(!elSyncStatus) return; elSyncStatus.className='pill'+(cls?' '+cls:''); elSyncStatus.textContent=t; elSyncStatus.style.display=''; }
  function hideStatus(){ if(!elSyncStatus) return; elSyncStatus.style.display='none'; elSyncStatus.textContent=''; elSyncStatus.className='pill'; }
  function fmtMin(s){ if(s==null) return '—'; return `${Math.round(s/60)} min`; }
  function fmtKm(m){ if(m==null) return '—'; return `${Math.round((m/1000)*10)/10} km`; }

  function normalizeSport(a){
    const label=a.sport_label||a.sport; if(label && typeof label==='string') return label;
    const t=(a.type||'').trim(); const name=(a.name||'').toLowerCase(); const dist=Number(a.distance_m||0);
    const map={Run:'Running',TrailRun:'Running',Ride:'Cycling',VirtualRide:'Cycling (Virtual)',WeightTraining:'Strength',StrengthTraining:'Strength',Walk:'Walking',Hike:'Hiking',Swim:'Swimming',Rowing:'Rowing',IndoorCycling:'Cycling (Virtual)'};
    if(map[t]) return map[t];
    if(!t || t==='Workout'){ if(dist>1000){ if(name.includes('zwift')||name.includes('trainerroad')||name.includes('virtual')) return 'Cycling (Virtual)'; return 'Cycling'; }
      if(name.includes('strength')||name.includes('core')) return 'Strength'; return 'Workout'; }
    return t;
  }

  function updatePaging(){ if(elPage) elPage.textContent=String(page); const max=Math.max(1,Math.ceil(total/(pageSize||10))||1); if(elPrev) elPrev.disabled=page<=1; if(elNext) elNext.disabled=page>=max; }

  function renderActivityTable(items){
    if(!elRows) return; elRows.innerHTML='';
    if(!Array.isArray(items)||items.length===0){
      const tr=document.createElement('tr'); const td=document.createElement('td'); td.colSpan=5; td.textContent='No activities found.'; tr.appendChild(td); elRows.appendChild(tr); return;
    }
    for(const a of items){
      const tr=document.createElement('tr');
      const d=(a.start_time||a.date||new Date()).toString().slice(0,10);
      const values=[d,normalizeSport(a),fmtMin(a.duration_s??a.duration_sec??a.elapsed_sec??null),a.kcal!=null?Math.round(a.kcal):'—',fmtKm(a.distance_m??a.distance??null)];
      values.forEach((value)=>{const td=document.createElement('td');td.textContent=String(value);tr.appendChild(td);});
      elRows.appendChild(tr);
    }
  }

  // ---------- Robust donut (circle stroke-dasharray) ----------
  function renderDonutBy(list, mode /* 'kcal' | 'count' */){
    if(!elDonut) return;
    elDonut.innerHTML = '';

    if(!Array.isArray(list) || list.length === 0){
      if (elDonutMode) elDonutMode.textContent = '';
      return;
    }

    const valKey = mode === 'count' ? 'count' : 'kcal';
    const totalV = list.reduce((s,x)=> s + (Number(x[valKey]) || 0), 0);
    if (totalV <= 0){
      if (elDonutMode) elDonutMode.textContent = '';
      return;
    }

    // SVG sizes
    const size = 180;
    const stroke = 24;
    const r = (size - stroke) / 2; // keep stroke fully inside viewbox
    const cx = size/2, cy = size/2;
    const circ = 2 * Math.PI * r;

    const colors = ["#ef4444","#f59e0b","#10b981","#3b82f6","#8b5cf6","#14b8a6","#eab308","#f97316"];

    const svg = document.createElementNS("http://www.w3.org/2000/svg","svg");
    svg.setAttribute("width", size); svg.setAttribute("height", size);
    svg.setAttribute("viewBox", `0 0 ${size} ${size}`);

    // background ring
    const bg = document.createElementNS("http://www.w3.org/2000/svg","circle");
    bg.setAttribute("cx", cx); bg.setAttribute("cy", cy); bg.setAttribute("r", r);
    bg.setAttribute("fill", "none"); bg.setAttribute("stroke", "rgba(255,255,255,0.06)");
    bg.setAttribute("stroke-width", String(stroke));
    svg.appendChild(bg);

    // segments stacked as circles with dasharray, rotated -90° to start at top
    let offset = 0; // proportion [0,1)
    list.forEach((seg, idx) => {
      const v = Number(seg[valKey] || 0);
      if (v <= 0) return;
      const ratio = v / totalV;
      const dash = circ * ratio;
      const circle = document.createElementNS("http://www.w3.org/2000/svg","circle");
      circle.setAttribute("cx", cx); circle.setAttribute("cy", cy); circle.setAttribute("r", r);
      circle.setAttribute("fill", "none");
      circle.setAttribute("stroke", colors[idx % colors.length]);
      circle.setAttribute("stroke-width", String(stroke));
      circle.setAttribute("stroke-linecap", "butt");
      circle.setAttribute("transform", `rotate(-90 ${cx} ${cy})`);
      circle.setAttribute("stroke-dasharray", `${dash} ${circ - dash}`);
      circle.setAttribute("stroke-dashoffset", String(-circ * offset));
      svg.appendChild(circle);
      offset += ratio;
    });

    elDonut.appendChild(svg);

    // Legend
    const legend = document.createElement('div');
    legend.className = 'legend';
    list.forEach((seg, idx) => {
      const row = document.createElement('div'); row.className = 'legend-row';
      const left = document.createElement('div');
      const key = document.createElement('span'); key.className = 'legend-key'; key.style.background = colors[idx % colors.length];
      left.appendChild(key); left.appendChild(document.createTextNode(seg.sport || 'Workout'));
      const right = document.createElement('div');
      right.textContent = mode === 'count' ? `${seg.count || 0}` : `${Math.round(seg.kcal || 0)} kcal`;
      row.appendChild(left); row.appendChild(right); legend.appendChild(row);
    });
    elDonut.appendChild(legend);

    if (elDonutMode) {
      elDonutMode.textContent = mode === 'count' ? '(by count)' : '(by kcal)';
    }
  }

  function badge(text, kind){ if(!elStravaBadge) return; elStravaBadge.textContent=text; elStravaBadge.className='pill '+(kind==='ok'?'ok':'warn'); }

  async function init(){
    const core = await waitForCore();  // may be {}
    const hasCore = !!core.fetchJSON;

    // helpers (fallbacks if core missing)
    const fetchJSON = hasCore ? core.fetchJSON : async (url, opts={})=>{
      const r = await fetch(url,{ credentials:'include', headers:{'Content-Type':'application/json'}, ...opts});
      if(!r.ok){ const t=await r.text().catch(()=>`HTTP ${r.status}`); const e=new Error(t||`HTTP ${r.status}`); e.status=r.status; throw e; }
      return r.headers.get('content-type')?.includes('json') ? r.json() : r.text();
    };
    const fmt = hasCore && core.fmt ? core.fmt : { dateISO:(d)=>new Date(d).toISOString().slice(0,10), kcal:(n)=>String(Math.round(Number(n||0))) };

    if (hasCore) { if (!(await core.ensureAuth({ redirect:true }))) return; }

    pageSize = Number(elPs?.value||10)||10;
    sumPageSize = Number(elSumPs?.value||10)||10;
    if (elFrom && !elFrom.value) elFrom.value = daysAgoISO(30);
    if (elTo && !elTo.value) elTo.value = todayISO();

    // wire
    elPrev?.addEventListener('click', ()=>{ if(page>1){ page--; void loadPage(); } });
    elNext?.addEventListener('click', ()=>{ page++; void loadPage(); });
    elPs?.addEventListener('change', ()=>{ pageSize=Math.max(1,Math.min(API_MAX,Number(elPs.value)||10)); page=1; void loadPage(); });

    elSumPrev?.addEventListener('click', ()=>{ if(sumPage>1){ sumPage--; renderSummaryPage(lastSummaryDays,sumPage,sumPageSize); } });
    elSumNext?.addEventListener('click', ()=>{ const max=Math.max(1,Math.ceil((lastSummaryDays.length||0)/sumPageSize)); if(sumPage<max){ sumPage++; renderSummaryPage(lastSummaryDays,sumPage,sumPageSize); } });
    elSumPs?.addEventListener('change', ()=>{ sumPageSize=Math.max(1,Math.min(API_MAX,Number(elSumPs.value)||10)); sumPage=1; renderSummaryPage(lastSummaryDays,sumPage,sumPageSize); });

    elRefresh?.addEventListener('click', ()=>{ page=1; void Promise.all([loadPage(), loadSummary()]); });
    elSyncBtn?.addEventListener('click', ()=> doSync(false));
    elFullSyncBtn?.addEventListener('click', ()=> doSync(true));

    elCsv?.addEventListener('click',(e)=>{ e.preventDefault?.(); void downloadCSV(); });
    (elLogout || document.querySelector('[data-nav="logout"]'))?.addEventListener('click', ()=>{
      try{ core.setToken?.(null); document.cookie='glyco_auth=; Max-Age=0; path=/; SameSite=Lax;'; }catch{} window.location.href='/ui/login.html';
    });

    await Promise.all([loadPage(), loadSummary(), refreshStravaBadge()]);
    setTimeout(refreshStravaBadge, 300);

    async function loadPage(){
      try{
        setErr('');
        const fromISO=readDateISO(elFrom,daysAgoISO(30)), toISO=readDateISO(elTo,todayISO());
        const qs=new URLSearchParams({page:String(page),page_size:String(pageSize),from:fromISO,to:toISO}).toString();
        const data=await fetchJSON(`/activities?${qs}`);
        const items=Array.isArray(data)?data:(data.items||[]);
        total = typeof data.total==='number'?data.total:items.length;
        renderActivityTable(items); updatePaging();
      }catch(e){ setErr(e?.message||'Failed to load activities.'); }
    }

    async function fetchAll(fromISO,toISO){
      const psize=250; let p=1,out=[],t=null;
      while(true){
        const qs=new URLSearchParams({page:String(p),page_size:String(psize),from:fromISO,to:toISO}).toString();
        const res=await fetchJSON(`/activities?${qs}`); const items=Array.isArray(res)?res:(res.items||[]);
        if(t==null) t = res.total ?? items.length ?? 0;
        out=out.concat(items); if(out.length>=t) break; p+=1; if(p>200) break;
      }
      return out;
    }

    function aggregate(items,fromISO,toISO){
      const within=d=>(d>=fromISO && d<=toISO); const days=new Map(), sports=new Map(), counts=new Map();
      let totalK=0, totalN=0;
      items.forEach(a=>{
        const d = fmt.dateISO ? fmt.dateISO(a.start_time||a.date||new Date()) : todayISO(new Date(a.start_time||a.date||Date.now()));
        if(!within(d)) return;
        const sport=normalizeSport(a);
        const kcal=Math.round(Number(a.kcal||0));
        const row = days.get(d)||{date:d,training_kcal:0,planned_kcal:0,by_sport:{}};
        row.training_kcal+=kcal; row.by_sport[sport]=(row.by_sport[sport]||0)+kcal; days.set(d,row);
        sports.set(sport,(sports.get(sport)||0)+kcal);
        counts.set(sport,(counts.get(sport)||0)+1);
        totalK+=kcal; totalN+=1;
      });
      const dayList=Array.from(days.values()).sort((a,b)=> (a.date<b.date?-1:1));
      dayList.forEach(d=> d.by_sport_text = Object.entries(d.by_sport).map(([k,v])=>`${k}: ${v} kcal`).join(' · '));
      const pieK = Array.from(sports.entries()).map(([sport,kcal])=>({sport,kcal}));
      const pieN = Array.from(counts.entries()).map(([sport,count])=>({sport,count}));
      return { dayList, pieK, pieN, totalK, totalN };
    }

    function renderSummaryPage(days,p,ps){
      if(!elSumRows) return; elSumRows.innerHTML='';
      if(!Array.isArray(days)||days.length===0){ const tr=document.createElement('tr'); const td=document.createElement('td'); td.colSpan=4; td.textContent='No activities in this range.'; tr.appendChild(td); elSumRows.appendChild(tr); return; }
      const start=(p-1)*ps,end=Math.min(days.length,start+ps);
      days.slice(start,end).forEach(d=>{ const tr=document.createElement('tr'); [d.date||'—',d.training_kcal||0,d.planned_kcal||0,d.by_sport_text||'—'].forEach(value=>{const td=document.createElement('td');td.textContent=String(value);tr.appendChild(td)}); elSumRows.appendChild(tr); });
      elSumPage && (elSumPage.textContent=String(p)); const max=Math.max(1,Math.ceil(days.length/ps)); elSumPrev&&(elSumPrev.disabled=p<=1); elSumNext&&(elSumNext.disabled=p>=max);
    }

    async function loadSummary(){
      const fromISO=readDateISO(elFrom,daysAgoISO(30)), toISO=readDateISO(elTo,todayISO());
      if(elFrom && elFrom.value!==fromISO) elFrom.value=fromISO;
      if(elTo && elTo.value!==toISO) elTo.value=toISO;
      try{
        const items=await fetchAll(fromISO,toISO);
        const { dayList, pieK, pieN, totalK, totalN } = aggregate(items,fromISO,toISO);

        elKcalTraining && (elKcalTraining.textContent=String(totalK||0));
        elKcalPlanned  && (elKcalPlanned.textContent ='0');
        elActCount     && (elActCount.textContent    =String(totalN||0));

        // donut: prefer kcal; if 0, fallback to counts
        if (Array.isArray(pieK) && pieK.length && (pieK.reduce((s,x)=>s+(x.kcal||0),0) > 0)) {
          renderDonutBy(pieK,'kcal');
        } else {
          renderDonutBy(pieN,'count');
        }

        lastSummaryDays = dayList || [];
        sumPage = 1;
        renderSummaryPage(lastSummaryDays,sumPage,sumPageSize);
        elEmpty && (elEmpty.style.display = (!lastSummaryDays || lastSummaryDays.length===0) ? '' : 'none');
      }catch(e){
        elKcalTraining && (elKcalTraining.textContent='0');
        elKcalPlanned  && (elKcalPlanned.textContent ='0');
        elActCount     && (elActCount.textContent   ='0');
        elDonut && (elDonut.innerHTML='');
        if (elDonutMode) elDonutMode.textContent = '';
        lastSummaryDays=[]; renderSummaryPage(lastSummaryDays,1,sumPageSize);
        elEmpty && (elEmpty.style.display='');
      }
    }

    async function refreshStravaBadge(){
      try{
        const r=await fetch('/oauth/strava/status',{credentials:'include'});
        if(r.status===401){ badge('Not connected','warn'); elSyncBtn&&(elSyncBtn.disabled=true); elFullSyncBtn&&(elFullSyncBtn.disabled=true); return; }
        if(!r.ok){ badge('Unavailable','warn'); elSyncBtn&&(elSyncBtn.disabled=true); elFullSyncBtn&&(elFullSyncBtn.disabled=true); return; }
        const j=await r.json(); const s=j?.strava||j||{}; const configured=s.configured!==false; const linked=!!s.linked;
        if(!configured){ badge('Unavailable','warn'); elSyncBtn&&(elSyncBtn.disabled=true); elFullSyncBtn&&(elFullSyncBtn.disabled=true); return; }
        if(linked){ badge('Connected','ok'); elSyncBtn&&(elSyncBtn.disabled=false); elFullSyncBtn&&(elFullSyncBtn.disabled=false); }
        else { badge('Not connected','warn'); elSyncBtn&&(elSyncBtn.disabled=true); elFullSyncBtn&&(elFullSyncBtn.disabled=true); }
      }catch{ badge('Unavailable','warn'); elSyncBtn&&(elSyncBtn.disabled=true); elFullSyncBtn&&(elFullSyncBtn.disabled=true); }
    }

    async function doSync(full){
      elSyncBtn&&(elSyncBtn.disabled=true); elFullSyncBtn&&(elFullSyncBtn.disabled=true);
      showStatus(full?'Full re-sync started':'Sync started','spin');
      try{
        const res=await fetchJSON(`/sync/strava?replace=${full?'true':'false'}`,{method:'POST'});
        const msg=(res && (res.created!=null || res.updated!=null)) ? `Sync complete — created ${res.created||0}, updated ${res.updated||0}` : 'Sync complete';
        showStatus(msg,'ok'); await loadPage(); await loadSummary(); await refreshStravaBadge();
      }catch(e){ showStatus(e?.message||'Sync failed','err'); }
      finally{ elSyncBtn&&(elSyncBtn.disabled=false); elFullSyncBtn&&(elFullSyncBtn.disabled=false); setTimeout(hideStatus,3500); }
    }

    function toCSV(rows){
      const cols=["date","start_time","sport","duration_s","kcal","distance_m","source_provider","source_id"];
      const head=cols.join(","); const lines=rows.map(a=>{
        const d=(a.start_time||a.date||new Date()).toString().slice(0,10);
        const vals=[d,a.start_time||"",a.sport||"",a.duration_s??a.duration_sec??a.elapsed_sec??"",a.kcal??"",a.distance_m??a.distance??"",a.source_provider??"",a.source_id??""];
        return vals.map(v=>{const s=String(v==null?'':v); return /[",\n]/.test(s)?`"${s.replace(/"/g,'""')}"`:s;}).join(",");
      });
      return [head].concat(lines).join("\r\n");
    }

    async function downloadCSV(){
      try{
        setErr('');
        const fromISO=readDateISO(elFrom,daysAgoISO(30)), toISO=readDateISO(elTo,todayISO());
        showStatus('Preparing CSV…','spin');
        const items=await (async function fetchAll(fromISO,toISO){
          const psize=250; let p=1,out=[],t=null;
          while(true){
            const qs=new URLSearchParams({page:String(p),page_size:String(psize),from:fromISO,to:toISO}).toString();
            const res=await fetch('/activities?'+qs,{credentials:'include'});
            const txtCT=res.headers.get('content-type')||'';
            const data=txtCT.includes('json')?await res.json():await res.text();
            const items=Array.isArray(data)?data:(data.items||[]);
            if(t==null) t = data.total ?? items.length ?? 0;
            out=out.concat(items); if(out.length>=t) break; p+=1; if(p>200) break;
          }
          return out;
        })(fromISO,toISO);
        const blob=new Blob([toCSV(items)],{type:"text/csv;charset=utf-8"});
        const url=URL.createObjectURL(blob); const a=document.createElement('a'); a.href=url; a.download=`glycofy_activities_${fromISO}_to_${toISO}.csv`;
        document.body.appendChild(a); a.click(); setTimeout(()=>{URL.revokeObjectURL(url); a.remove(); hideStatus();},0);
      }catch(e){ setErr(e?.message||'CSV download failed'); showStatus('CSV failed','err'); setTimeout(hideStatus,3000); }
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => { void init(); }, { once:true });
  } else {
    void init();
  }

  window.addEventListener('pageshow', (e)=>{ if (e.persisted) { const b=$('strava_badge'); if (b) b.textContent='Checking…'; setTimeout(()=>{ void init(); }, 80); } });
})();
