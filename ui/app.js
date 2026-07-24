// /ui/app.js
// Global bootstrap for Glycofy UI (CSP-safe, no modules).

(function () {
  // ---------- tiny DOM helpers ----------
  function $(idOrEl){ if(!idOrEl) return null; if(typeof idOrEl==='string') return document.getElementById(idOrEl)||null; if(idOrEl instanceof Element) return idOrEl; return null; }
  function qs(sel,root){ return (root||document).querySelector(sel); }
  function qsa(sel,root){ return Array.prototype.slice.call((root||document).querySelectorAll(sel)); }
  function norm(url){ return url.startsWith('/') ? url : `/${url}`; }

  // ---------- defaults ----------
  const DEFAULT_RETURN = '/ui/index.html'; // ← Home is now the new default

  // ---------- top banner for quick debug ----------
  function ensureTopBanner(){
    let b=document.getElementById('__glyco_err_banner');
    if(b) return b;
    b=document.createElement('div');
    b.id='__glyco_err_banner';
    b.style.cssText='position:fixed;top:0;left:0;right:0;z-index:9999;background:#3a1414;border-bottom:1px solid #542020;color:#ffdede;padding:8px 12px;font:13px/1.4 system-ui,-apple-system,Segoe UI,Roboto;display:none';
    document.addEventListener('DOMContentLoaded',()=>document.body.appendChild(b));
    return b;
  }
  function banner(msg){ const b=ensureTopBanner(); b.textContent=`⚠️ ${msg}`; b.style.display=''; }

  window.addEventListener('error',e=>{ console.error('[glyco:error]', e.error||e.message||e); banner(e?.message||'Script error'); });
  window.addEventListener('unhandledrejection',e=>{ console.error('[glyco:unhandled]', e.reason); banner((e?.reason&&(e.reason.message||String(e.reason)))||'Async error'); });

  // ---------- token store ----------
  const TOKEN_KEY='glyco_token';
  function getToken(){ try{ return localStorage.getItem(TOKEN_KEY)||''; }catch{ return ''; } }
  function setToken(v){ try{ v ? localStorage.setItem(TOKEN_KEY,v) : localStorage.removeItem(TOKEN_KEY); }catch{} }
  function clearToken(){ setToken(''); }

  // ---------- API + fetch ----------
  const API={
    get token(){ return getToken(); },
    set token(t){ setToken(t); },
    authHeaders(){ const t=getToken(); return t?{Authorization:`Bearer ${t}`}:{ }; }
  };

  async function fetchJSON(url, opts={}){
    const headers=Object.assign({Accept:'application/json'}, API.authHeaders(), opts.headers||{});
    const res=await fetch(norm(url), Object.assign({credentials:'include'}, opts, {headers}));
    if(res.status===401){
      // Always send unauthenticated users to login → return=Home
      const ret=encodeURIComponent(DEFAULT_RETURN);
      if(!location.pathname.endsWith('/ui/login.html')) location.replace(`/ui/login.html?return=${ret}`);
      throw new Error('Unauthorized');
    }
    const ct=res.headers.get('content-type')||'';
    if(ct.includes('application/json')){
      const data=await res.json().catch(()=>null);
      if(!res.ok) throw new Error((data&&(data.detail||data.message))||`${res.status} ${res.statusText}`);
      return data;
    }
    const txt=await res.text().catch(()=> '');
    if(!res.ok) throw new Error(txt||`${res.status} ${res.statusText}`);
    return txt||null;
  }

  // ---------- login / logout ----------
  async function doLogin(email, password, returnTo){
    console.log('[glyco:login] submitting', { email: !!email, hasPassword: !!password });
    const r=await fetch('/auth/login',{
      method:'POST',
      headers:{'Content-Type':'application/json', 'Accept':'application/json'},
      credentials:'include',
      body:JSON.stringify({email,password})
    });
    if(!r.ok){
      let d=''; try{ d=(await r.json()).detail; }catch{}
      throw new Error(d||'Login failed');
    }
    const data=await r.json();
    const token=data && data.access_token;
    if(!token) throw new Error('No access token returned');
    setToken(token);
    // Normalize any /ui/plan.html → Home
    let dest = returnTo || DEFAULT_RETURN;
    if (dest === '/ui/plan.html' || dest === '/ui/plan') dest = DEFAULT_RETURN;
    console.log('[glyco:login] success → redirect', dest);
    location.replace(dest);
  }

  async function doLogout(){
    clearToken();
    try{ await fetch('/auth/logout',{method:'POST',credentials:'include'});}catch{}
    // Always send to login → return=Home
    const ret=encodeURIComponent(DEFAULT_RETURN);
    location.replace(`/ui/login.html?return=${ret}`);
  }

  // ---------- robust login wiring ----------
  function wireLoginForm(){
    const isLoginPage = location.pathname.endsWith('/ui/login.html') || location.pathname.endsWith('/ui/login');
    if(!isLoginPage) return;

    const params=new URLSearchParams(location.search);
    let ret=params.get('return') || DEFAULT_RETURN;
    if (ret === '/ui/plan.html' || ret === '/ui/plan') ret = DEFAULT_RETURN;

    if(getToken()){
      console.log('[glyco:login] token present → redirecting to', ret);
      location.replace(ret);
      return;
    }

    const form = $('#login-form') || $('#loginForm') || qs('form') || document.body;
    const emailEl =
      $('#email') || qs('input[name="email"]') || qs('input[type="email"]') ||
      qs('input[autocomplete="username"]') || qs('input[data-field="email"]');
    const pwdEl =
      $('#password') || qs('input[name="password"]') || qs('input[type="password"]') ||
      qs('input[autocomplete="current-password"]') || qs('input[data-field="password"]');
    let submitBtn =
      $('#submitBtn') || $('#loginSubmit') || $('#signin') || qs('button[type="submit"]', form) ||
      qs('input[type="submit"]', form) || qs('button[data-action="login"]', form) ||
      qs('button', form) || qs('input[type="button"]', form);

    function val(el){ return (el && typeof el.value==='string') ? el.value.trim() : ''; }
    async function handle(e){
      if(e && e.preventDefault) e.preventDefault();
      const email = val(emailEl);
      const password = val(pwdEl);
      try{ await doLogin(email, password, ret); }
      catch(err){ alert(err.message||'Login failed'); }
    }

    if(form && form.tagName === 'FORM'){ form.addEventListener('submit', handle); }
    if(submitBtn){
      if(submitBtn.tagName === 'A') submitBtn.setAttribute('href', 'javascript:void(0)');
      submitBtn.addEventListener('click', handle);
    }
    if(emailEl) emailEl.addEventListener('keydown', (e)=>{ if(e.key==='Enter'){ e.preventDefault(); handle(e);} });
    if(pwdEl)   pwdEl.addEventListener('keydown',   (e)=>{ if(e.key==='Enter'){ e.preventDefault(); handle(e);} });
  }

  // ---------- nav highlight ----------
  function setActiveNav(){
    const path=location.pathname.replace(/\/+$/,'');
    qsa('nav a').forEach(a=>{
      const href=(a.getAttribute('href')||'').replace(/\/+$/,'');
      if(!href) return;
      a.classList.toggle('active', !!href && path.endsWith(href));
    });
  }

  // ---------- units + fmt (used by pages) ----------
  function detectUnitSystem(user){
    try{ const forced=localStorage.getItem('glyco_units'); if(forced==='us'||forced==='metric') return forced; }catch{}
    const tz=(user?.timezone||'').trim(); if(/^America\//.test(tz)) return 'us';
    if((navigator.language||'').toLowerCase().startsWith('en-us')) return 'us';
    return 'metric';
  }
  const Unit={
    kgToLb: kg => (kg!=null ? kg*2.2046226218 : null),
    lbToKg: lb => (lb!=null ? lb/2.2046226218 : null),
    cmToIn: cm => (cm!=null ? cm/2.54 : null),
    inToCm: inch => (inch!=null ? inch*2.54 : null),
    round1: n => Math.round((Number(n)+Number.EPSILON)*10)/10,
    round0: n => Math.round(Number(n)||0)
  };
  const fmt={
    kcal:n=>`${Math.round(n)} kcal`,
    g:n=>`${Math.round(n)} g`,
    dateISO(d){ if(typeof d==='string') return d.slice(0,10); const dt=d instanceof Date?d:new Date(d); return dt.toISOString().slice(0,10); },
    round(n,places=1){ const m=Math.pow(10,places); return Math.round((Number(n)+Number.EPSILON)*m)/m; }
  };

  async function selftest(){
    const out={tokenPresent:!!getToken()};
    try{
      out.me=await fetchJSON('/users/me');
      out.activities=await fetchJSON('/activities?page=1&page_size=5');
      const today=fmt.dateISO(new Date());
      out.plan=await fetchJSON(`/v1/plan/${today}`);
      console.log('[glyco:selftest]', out);
      banner('Self-test OK (see console).');
    }catch(e){ console.error('[glyco:selftest:fail]', e); banner(`Self-test failed: ${e.message||e}`); }
    return out;
  }

  // ---------- initial auth probe (cookie + bearer) ----------
  async function initialAuthProbe(){
    const isLoginPage = location.pathname.endsWith('/ui/login.html') || location.pathname.endsWith('/ui/login');
    if (isLoginPage) return; // login.js handles its own probe/redirect

    try{
      const res = await fetch('/users/me', {
        method:'GET',
        credentials:'include',
        headers: Object.assign({Accept:'application/json'}, API.authHeaders()),
        cache:'no-store'
      });
      if(res.status === 401){
        // Redirect unauthenticated users → login → return=Home
        const ret=encodeURIComponent(DEFAULT_RETURN);
        location.replace(`/ui/login.html?return=${ret}`);
        return;
      }
    }catch(e){
      console.warn('[glyco:init-probe] failed', e);
      banner('Auth probe failed; some data may not load.');
    }
  }

  // ---------- expose + init ----------
  window.__glyco = Object.assign({}, window.__glyco||{}, {
    API, fetchJSON, doLogin, doLogout,
    detectUnitSystem, Unit, fmt,
    getToken, setToken, clearToken,
    $, qs, selftest, setActiveNav
  });

  document.addEventListener('DOMContentLoaded', setActiveNav);
  document.addEventListener('DOMContentLoaded', ()=>{
    initialAuthProbe();
    wireLoginForm();
    const logoutBtn=document.getElementById('logout_btn');
    if(logoutBtn) logoutBtn.addEventListener('click', async ()=>{ await doLogout(); });
  });
})();
