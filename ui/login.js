// /ui/login.js — self-contained login (no core.js dependency)
// Default post-login destination: /ui/index.html
(function () {
  "use strict";

  // --- DOM helpers ---
  const $ = (id) => document.getElementById(id);
  const show = (el) => { if (el) el.style.display = ""; };
  function flash(msg, type = "notice") {
    const box = $("msg");
    if (!box) return;
    box.textContent = msg || "";
    box.className = "msg " + (type === "error" ? "error" : "notice");
    show(box);
  }

  // --- return handling ---
  function sanitizeReturnPath(p) {
    try {
      if (p && p.startsWith("/") && !p.startsWith("//")) return p;
    } catch {}
    return "/ui/index.html";
  }
  function parseReturn(def) {
    try {
      const u = new URL(location.href);
      let v = u.searchParams.get("return");
      // force home if caller tried to send us to /ui/plan.html
      if (v === "/ui/plan.html" || v === "/ui/plan") v = null;
      return sanitizeReturnPath(v) || (def || "/ui/index.html");
    } catch {}
    return def || "/ui/index.html";
  }
  function redirectToReturn(def) {
    const dest = parseReturn(def);
    const here = location.pathname + location.search;
    if (here !== dest) location.replace(dest);
    else location.reload();
  }

  // --- /users/me probe ---
  let _probe = null;
  async function ensureAuth({ force = false } = {}) {
    if (force) _probe = null;
    if (_probe) return _probe;
    const headers = { "Accept": "application/json", "X-Requested-With": "XMLHttpRequest" };
    _probe = fetch("/users/me", { credentials: "include", headers, cache: "no-store" })
      .then(r => r.ok).catch(() => false);
    return _probe;
  }

  // --- password login ---
  async function passwordLogin(email, password) {
    const res = await fetch("/auth/login", {
      method: "POST",
      credentials: "include",
      headers: {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-Requested-With": "XMLHttpRequest"
      },
      body: JSON.stringify({ email, password })
    });
    if (!res.ok) {
      let msg = `HTTP ${res.status}`;
      try {
        const ct = res.headers.get("Content-Type") || "";
        if (ct.includes("application/json")) {
          const j = await res.json().catch(() => null);
          msg = (j && (j.detail || j.message)) || msg;
        } else msg = (await res.text()) || msg;
      } catch {}
      throw new Error(msg);
    }
    return true;
  }

  // --- Google login button ---
  async function wireGoogle() {
    const btn = $("googleBtn"); if (!btn) return;
    try {
      const r = await fetch("/oauth/google/status", { credentials: "include" });
      const j = await r.json().catch(() => ({}));
      if (!j || !j.configured) { btn.disabled = true; btn.title = "Google not configured"; return; }
      btn.addEventListener("click", (e) => {
        e.preventDefault();
        const ret = encodeURIComponent(parseReturn("/ui/index.html"));
        location.href = "/oauth/google/start?return=" + ret;
      });
    } catch {
      btn.disabled = true; btn.title = "Google status unavailable";
    }
  }

  // --- init ---
  document.addEventListener("DOMContentLoaded", async () => {
    const note = $("endpointNote"); if (note) note.textContent = location.origin;

    try {
      const ok = await ensureAuth({ force: true });
      if (ok) {
        flash("Already signed in. Redirecting…");
        setTimeout(() => redirectToReturn("/ui/index.html"), 250);
        return;
      }
    } catch {}

    await wireGoogle();

    const form = $("login-form"); const submitBtn = $("submitBtn");
    if (!form || !submitBtn) return;
    let inflight = false;

    form.addEventListener("submit", async (e) => {
      e.preventDefault(); if (inflight) return; inflight = true;

      const email = ($("email")?.value || "").trim();
      const pw = $("password")?.value || "";
      if (!email || !pw) { flash("Please enter both email and password.", "error"); inflight = false; return; }

      submitBtn.disabled = true;
      submitBtn.dataset.prevText = submitBtn.textContent;
      submitBtn.textContent = "Signing in…";
      form.querySelectorAll("input,button").forEach(el => el.disabled = true);
      flash("Signing in…");

      try {
        await passwordLogin(email, pw);
        const ok = await ensureAuth({ force: true });
        if (!ok) throw new Error("Authentication failed after login.");
        flash("Login successful. Redirecting…");
        setTimeout(() => redirectToReturn("/ui/index.html"), 300);
      } catch (err) {
        console.error("[login] failed:", err);
        flash(err?.message || "Login failed. Please try again.", "error");
        submitBtn.disabled = false;
        submitBtn.textContent = submitBtn.dataset.prevText || "Sign in";
        form.querySelectorAll("input,button").forEach(el => el.disabled = false);
        inflight = false;
      }
    });
  });
})();
