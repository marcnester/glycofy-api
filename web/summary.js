// summary.js
const API_BASE = "";
const $ = (s) => document.querySelector(s);
function getToken(){ return localStorage.getItem("glycofy_token") || ""; }
function number(n){ return new Intl.NumberFormat().format(n); }
function iso(d){ return d.toISOString().slice(0,10); }
function daysBetween(a,b){ return Math.round((b - a)/86400000)+1; }

async function api(path, opts={}){
  const headers = Object.assign(
    {"Content-Type":"application/json"},
    getToken() ? {Authorization:`Bearer ${getToken()}`} : {},
    opts.headers||{}
  );
  const res = await fetch(API_BASE + path, {...opts, headers});
  const ct = res.headers.get("content-type") || "";
  const body = ct.includes("application/json") ? await res.json() : await res.text();
  if(!res.ok) throw {status:res.status, body};
  return body;
}

// Elements
const logoutBtn = $("#logoutBtn");
const dietPref = $("#dietPref");
const refreshBtn = $("#refreshBtn");
const rangeBadge = $("#rangeBadge");
const metaEl = $("#meta");
const errorEl = $("#error");
const tableWrap = $("#tableWrap");

const kpiTraining = $("#kpiTraining");
const kpiPlanned  = $("#kpiPlanned");
const kpiNet      = $("#kpiNet");
const kpiActivities = $("#kpiActivities");

// Chart refs
let kcalChart = null;
let sportChart = null;
const kcalCanvas = $("#kcalChart");
const sportCanvas = $("#sportChart");
const kcalEmpty = $("#kcalEmpty");
const sportEmpty = $("#sportEmpty");

// Default 7-day window
let from = new Date(); from.setDate(from.getDate() - 6);
let to = new Date();

// ---------- helpers ----------
function destroyChart(c){
  if(c && typeof c.destroy === "function"){ c.destroy(); }
}
function setOverlay(el, show){
  if(!el) return;
  el.hidden = !show;
  el.style.display = show ? "grid" : "none";
}
function showCanvas(canvas, show){
  if(!canvas) return;
  canvas.style.display = show ? "block" : "none";
}

// ---------- charts ----------
function buildKcalChart(days) {
  destroyChart(kcalChart);

  const labels = days.map(d => d.date.slice(5));
  const training = days.map(d => d.training_kcal || 0);
  const planned  = days.map(d => d.planned_kcal || 0);

  const hasDays = days.length > 0;

  if(!hasDays){
    showCanvas(kcalCanvas, false);
    setOverlay(kcalEmpty, true);
    return;
  }

  // There IS data (even if values are zeros) -> hide overlay, show canvas
  setOverlay(kcalEmpty, false);
  showCanvas(kcalCanvas, true);

  kcalChart = new Chart(kcalCanvas.getContext("2d"), {
    type: "bar",
    data: {
      labels,
      datasets: [
        { label: "Training kcal", data: training },
        { label: "Planned kcal", data: planned },
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { position: "top" },
        tooltip: { mode: "index", intersect: false }
      },
      scales: {
        x: { grid: { display:false } },
        y: { beginAtZero: true }
      }
    }
  });
}

function buildSportChart(days){
  destroyChart(sportChart);

  // Aggregate by sport across days
  const map = new Map(); // sport -> kcal
  for(const d of days){
    for(const a of (d.activities || [])){
      const s = (a.sport || "other").toLowerCase();
      map.set(s, (map.get(s) || 0) + (a.kcal || 0));
    }
  }
  const labels = [...map.keys()];
  const values = [...map.values()];

  const hasSport = labels.length > 0;

  if(!hasSport){
    showCanvas(sportCanvas, false);
    setOverlay(sportEmpty, true);
    return;
  }

  // There IS sport data -> hide overlay, show canvas
  setOverlay(sportEmpty, false);
  showCanvas(sportCanvas, true);

  sportChart = new Chart(sportCanvas.getContext("2d"), {
    type: "doughnut",
    data: { labels, datasets: [{ data: values }] },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { position: "right" },
        tooltip: { callbacks: { label: (ctx) => `${ctx.label}: ${number(ctx.parsed)} kcal` } }
      },
      cutout: "65%"
    }
  });
}

// ---------- table ----------
function renderTable(days){
  const head = `<thead class="sticky-head"><tr>
    <th>Date</th><th class="num">Training kcal</th><th class="num">Planned kcal</th><th>Activities</th><th>Meals</th>
  </tr></thead>`;
  const rows = days.map(d => `
    <tr>
      <td>${d.date}</td>
      <td class="num">${number(d.training_kcal||0)}</td>
      <td class="num">${number(d.planned_kcal||0)}</td>
      <td>${(d.activities||[]).map(a=>`<div class="act"><span class="badge">${a.sport}</span> ${a.kcal||0} kcal</div>`).join("") || "<small>—</small>"}</td>
      <td>${(d.meals||[]).map(m=>`<div>${m.title}</div>`).join("") || "<small>—</small>"}</td>
    </tr>`).join("");
  tableWrap.innerHTML = `<table class="simple zebra">${head}<tbody>${rows}</tbody></table>`;
}

// ---------- load ----------
async function load(){
  try{
    // reset overlays to a known state before fetch/render
    setOverlay(kcalEmpty, false);
    setOverlay(sportEmpty, false);
    showCanvas(kcalCanvas, false);
    showCanvas(sportCanvas, false);

    errorEl.hidden = true;
    rangeBadge.textContent = `${iso(from)} → ${iso(to)} · ${daysBetween(from,to)} days`;
    const q = new URLSearchParams({ from: iso(from), to: iso(to) });
    if(dietPref.value) q.set("diet_pref", dietPref.value);
    const data = await api(`/v1/summary?${q.toString()}`);

    // KPIs
    kpiTraining.textContent = number(data.total_training_kcal || 0);
    kpiPlanned.textContent  = number(data.total_planned_kcal || 0);
    kpiNet.textContent      = number((data.total_planned_kcal||0) - (data.total_training_kcal||0));
    kpiActivities.textContent = number(data.total_activities || 0);

    // Charts + table
    const days = data.days || [];
    buildKcalChart(days);
    buildSportChart(days);
    renderTable(days);
    metaEl.textContent = `${days.length} rows`;
  }catch(e){
    if(e.status === 401){ window.location.href = "/ui/login.html"; return; }
    errorEl.textContent = "Failed to load summary.";
    errorEl.hidden = false;

    showCanvas(kcalCanvas, false);
    showCanvas(sportCanvas, false);
    setOverlay(kcalEmpty, true);
    setOverlay(sportEmpty, true);
  }
}

logoutBtn?.addEventListener("click", ()=>{ localStorage.removeItem("glycofy_token"); window.location.href="/ui/login.html"; });
refreshBtn?.addEventListener("click", load);

window.addEventListener("DOMContentLoaded", ()=>{
  if(!getToken()){ window.location.href="/ui/login.html"; return; }
  load();
});