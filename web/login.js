const API_BASE = "";
const $ = (s) => document.querySelector(s);

function setToken(t){ t ? localStorage.setItem("glycofy_token", t) : localStorage.removeItem("glycofy_token"); }
function getToken(){ return localStorage.getItem("glycofy_token") || ""; }

async function api(path, opts={}){
  const headers = Object.assign({"Content-Type":"application/json"}, opts.headers || {});
  const res = await fetch(API_BASE + path, { ...opts, headers });
  const ct = res.headers.get("content-type") || "";
  const body = ct.includes("application/json") ? await res.json() : await res.text();
  if(!res.ok) throw {status: res.status, body};
  return body;
}

const form = $("#loginForm");
const email = $("#email");
const password = $("#password");
const errorEl = $("#error");
const loginBtn = $("#loginBtn");

function showError(msg){
  errorEl.textContent = msg || "Login failed. Check your credentials.";
  errorEl.hidden = false;
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  errorEl.hidden = true;
  loginBtn.disabled = true;
  try{
    const data = await api("/auth/login", {
      method: "POST",
      body: JSON.stringify({ email: email.value.trim(), password: password.value })
    });
    if(!data || !data.access_token) throw new Error("No token returned");
    setToken(data.access_token);
    // go to Plan
    window.location.href = "/ui/";
  }catch(err){
    showError(err?.body?.detail || "Invalid email or password.");
    loginBtn.disabled = false;
  }
});

window.addEventListener("DOMContentLoaded", () => {
  if(getToken()){
    // already logged in
    window.location.href = "/ui/";
  }
});
