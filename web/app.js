// /ui/app.js

// ---- Token helpers
export function getToken() {
  // prefer memory → local → session (keep simple)
  if (window.ACCESS && typeof window.ACCESS === 'string') return window.ACCESS;
  const t = localStorage.getItem('ACCESS')
        || sessionStorage.getItem('ACCESS')
        || localStorage.getItem('access_token')
        || sessionStorage.getItem('access_token')
        || localStorage.getItem('TOKEN')
        || sessionStorage.getItem('TOKEN')
        || localStorage.getItem('token')
        || sessionStorage.getItem('token');
  if (t) window.ACCESS = t;
  return t || '';
}

export function setToken(t) {
  window.ACCESS = t;
  localStorage.setItem('ACCESS', t);
  sessionStorage.setItem('ACCESS', t);
}

export function clearToken() {
  window.ACCESS = '';
  ['ACCESS','access_token','TOKEN','token'].forEach(k=>{
    localStorage.removeItem(k); sessionStorage.removeItem(k);
  });
}

// ---- Redirect to login if not authenticated
export function ensureAuthOrRedirect(returnUrl) {
  const t = getToken();
  if (t) return t;
  const target = '/ui/login.html?return=' + encodeURIComponent(returnUrl || window.location.pathname + window.location.search);
  window.location.replace(target);
  throw new Error('Auth required; redirecting to ' + target);
}

// ---- API wrapper that injects Authorization header
export async function api(path, opts={}) {
  const t = getToken();
  const headers = Object.assign({}, opts.headers || {});
  if (t) headers['Authorization'] = 'Bearer ' + t;
  const r = await fetch(path, { ...opts, headers });
  const ct = r.headers.get('content-type') || '';
  const isJson = ct.includes('application/json');
  const data = isJson ? await r.json() : await r.text();
  if (!r.ok) {
    if (r.status === 401) {
      // Likely expired or missing → force login
      ensureAuthOrRedirect(window.location.pathname + window.location.search);
    }
    throw new Error(isJson ? JSON.stringify(data) : data);
  }
  return data;
}

// ---- Utility DOM helpers
export function $(sel, root=document){ return root.querySelector(sel); }
export function $all(sel, root=document){ return Array.from(root.querySelectorAll(sel)); }
export function el(tag, attrs={}, children=[]) {
  const n = document.createElement(tag);
  for (const [k,v] of Object.entries(attrs)) {
    if (k === 'class') n.className = v;
    else if (k === 'text') n.textContent = v;
    else if (k === 'html') n.innerHTML = v;
    else n.setAttribute(k, v);
  }
  (Array.isArray(children) ? children : [children]).forEach(c=>{
    if (typeof c === 'string') n.appendChild(document.createTextNode(c));
    else if (c) n.appendChild(c);
  });
  return n;
}

export function todayISO() {
  const d = new Date();
  const yyyy = d.getFullYear();
  const mm = String(d.getMonth()+1).padStart(2,'0');
  const dd = String(d.getDate()).padStart(2,'0');
  return `${yyyy}-${mm}-${dd}`;
}
