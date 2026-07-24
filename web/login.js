// ui/login.js
(function () {
  const API = ""; // same-origin API calls
  const form = document.getElementById("loginForm");
  const toastEl = document.getElementById("toast");

  function showToast(msg, isError = false) {
    toastEl.textContent = msg;
    toastEl.classList.toggle("error", !!isError);
    toastEl.style.display = "block";
    setTimeout(() => (toastEl.style.display = "none"), 3000);
  }

  async function safeJSON(res) {
    const ctype = res.headers.get("content-type") || "";
    if (ctype.includes("application/json")) return await res.json();
    const txt = await res.text();
    try {
      return JSON.parse(txt);
    } catch {
      return { raw: txt };
    }
  }

  async function login(email, password) {
    const res = await fetch(`${API}/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });

    if (!res.ok) {
      const j = await safeJSON(res);
      const msg = j?.detail || `Login failed (${res.status})`;
      throw new Error(msg);
    }

    return await res.json(); // { access_token, token_type }
  }

  function storeToken(token) {
    // Consistent key/shape used throughout the app
    localStorage.setItem("glycofy_auth", JSON.stringify({ access_token: token }));
  }

  async function handleLogin(evt) {
    evt.preventDefault();
    const email = document.getElementById("email").value.trim();
    const password = document.getElementById("password").value;

    if (!email || !password) {
      showToast("Please enter both email and password", true);
      return;
    }

    try {
      const data = await login(email, password);
      if (!data?.access_token) throw new Error("Invalid response from server");

      storeToken(data.access_token);
      showToast("Login successful!");

      // redirect to intended page or to /ui/plan.html by default
      const ret = new URLSearchParams(location.search).get("return");
      window.location.href = ret || "/ui/plan.html";
    } catch (e) {
      console.error(e);
      showToast(e.message || "Login failed", true);
    }
  }

  function init() {
    form.addEventListener("submit", handleLogin);
  }

  document.addEventListener("DOMContentLoaded", init);
})();
