const API_BASE = "";
const $ = (s) => document.querySelector(s);

function getToken(){ return localStorage.getItem("glycofy_token") || ""; }
function setToken(t){ t ? localStorage.setItem("glycofy_token", t) : localStorage.removeItem("glycofy_token"); }

async function api(path, opts={}){
  const headers = Object.assign({"Content-Type":"application/json"}, getToken() ? {Authorization:`Bearer ${getToken()}`} : {}, opts.headers||{});
  const res = await fetch(API_BASE + path, {...opts, headers});
  const ct = res.headers.get("content-type")||"";
  const body = ct.includes("application/json") ? await res.json() : await res.text();
  if(!res.ok) throw {status:res.status, body};
  return body;
}

const logoutBtn = $("#logoutBtn");
const fromDate  = $("#fromDate");
const toDate    = $("#toDate");
const sportSel  = $("#sport");
const filterBtn = $("#filterBtn");
const clearBtn  = $("#clearBtn");
const syncBtn   = $("#syncBtn");
const errorEl   = $("#error");
const tableWrap = $("#tableWrap");
const metaEl    = $("#meta");
const pageBadge = $("#pageBadge");
const prevBtn   = $("#prevBtn");
const nextBtn   = $("#nextBtn");

let page = 1;
let pageSize = 25;
let lastTotal = 0;

function fmt(n){ return new Intl.NumberFormat().format(n); }
function fmtDate(s){ return (s||"").replace("T"," ").slice(0,16); }
function km(m){ return Math.round((m || 0)/10)/100; }

function buildQuery(){
  const params = new URLSearchParams();
  params.set("page", String(page));
  params.set("page_size", String(pageSize));
  if(fromDate.value) params.set("from", fromDate.value);
  if(toDate.value) params.set("to", toDate.value);
  if(sportSel.value) params.set("sport", sportSel.value);
  return params.toString();
}

function renderTable(payload){
  const items = payload.items || [];
  lastTotal = payload.total || items.length;

  const head = `
    <thead><tr>
      <th>Date</th>
      <th>Sport</th>
      <th class="num">Duration (min)</th>
      <th class="num">Distance (km)</th>
      <th class="num">kcal</th>
      <th>Source</th>
    </tr></thead>`;

  const rows = items.map(a => `
    <tr>
      <td>${fmtDate(a.start_time)}</td>
      <td><span class="badge">${a.sport||"—"}</span></td>
      <td class="num">${fmt(Math.round((a.duration_s||0)/60))}</td>
      <td class="num">${(km(a.distance_m)).toFixed(2)}</td>
      <td class="num">${fmt(a.kcal||0)}</td>
      <td>${a.source_provider ? `${a.source_provider}${a.source_id ? " • #" + a.source_id : ""}` : "<small>—</small>"}</td>
    </tr>`).join("");

  tableWrap.innerHTML = `<table class="activities">${head}<tbody>${rows||""}</tbody></table>`;

  const start = (page-1)*pageSize + 1;
  const end = Math.min(page*pageSize, lastTotal);
  metaEl.textContent = lastTotal ? `Showing ${start}–${end} of ${fmt(lastTotal)}` : "No activities found";
  pageBadge.textContent = `Page ${page}`;
  prevBtn.disabled = page <= 1;
  nextBtn.disabled = page*pageSize >= lastTotal;
}

async function load(){
  try{
    errorEl.hidden = true;
    const q = buildQuery();
    const data = await api(`/activities?${q}`);
    renderTable(data);
  }catch(err){
    if(err.status === 401){ window.location.href = "/ui/login.html"; return; }
    errorEl.textContent = "Failed to load activities.";
    errorEl.hidden = false;
  }
}

async function syncStrava(){
  try{
    errorEl.hidden = true;
    const since = new Date(Date.now() - 30*24*3600*1000).toISOString().slice(0,10);
    const params = new URLSearchParams({ since });
    const res = await api(`/imports/strava/sync?${params.toString()}`, { method:"POST" });
    await load();
    metaEl.textContent = `Synced Strava: +${res.created} new, ${res.updated} updated, ${res.skipped} skipped`;
  }catch(err){
    if(err.status === 401){ window.location.href = "/ui/login.html"; return; }
    errorEl.textContent = "Strava sync failed.";
    errorEl.hidden = false;
  }
}

logoutBtn.addEventListener("click", () => { setToken(""); window.location.href = "/ui/login.html"; });
filterBtn.addEventListener("click", () => { page = 1; load(); });
clearBtn.addEventListener("click", () => { fromDate.value = ""; toDate.value = ""; sportSel.value = ""; page = 1; load(); });
syncBtn.addEventListener("click", syncStrava);
prevBtn.addEventListener("click", () => { if(page>1){ page--; load(); } });
nextBtn.addEventListener("click", () => { if(page*pageSize < lastTotal){ page++; load(); } });

window.addEventListener("DOMContentLoaded", () => {
  if(!getToken()){ window.location.href = "/ui/login.html"; return; }
  load();
});