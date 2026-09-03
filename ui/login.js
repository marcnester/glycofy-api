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
    return null;
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

  let authMode = "signin";
  function setMode(mode) {
    authMode = ["signup", "forgot", "reset"].includes(mode) ? mode : "signin";
    const signingUp = authMode === "signup";
    const recovering = authMode === "forgot";
    const resetting = authMode === "reset";
    $("auth-title").textContent = signingUp ? "Create your account" : recovering ? "Reset your password" : resetting ? "Choose a new password" : "Sign in";
    $("auth-subtitle").textContent = signingUp ? "Start planning around the way you train" : recovering ? "We’ll email you a secure reset link" : resetting ? "Use at least 12 characters" : "Welcome back";
    $("submitBtn").textContent = signingUp ? "Create account" : recovering ? "Send reset link" : resetting ? "Update password" : "Sign in";
    $("googleBtn").textContent = signingUp ? "Sign up with Google" : "Continue with Google";
    $("switch-prompt").textContent = signingUp ? "Already have an account?" : "New to Glycofy?";
    $("mode-switch").textContent = signingUp ? "Sign in" : "Create an account";
    $("confirm-wrap").hidden = !signingUp;
    $("email-wrap").hidden = resetting;
    $("password-wrap").hidden = recovering;
    $("password-hint").hidden = !(signingUp || resetting);
    $("password").required = !recovering;
    $("confirm-password").required = signingUp;
    $("password").autocomplete = signingUp ? "new-password" : "current-password";
    const url = new URL(location.href);
    if (authMode === "signin") url.searchParams.delete("mode"); else url.searchParams.set("mode", authMode);
    history.replaceState(null, "", url.pathname + url.search);
    $("forgot-password").textContent = recovering || resetting ? "Back to sign in" : "Forgot password?";
    $("googleBtn").hidden = recovering || resetting;
    document.querySelector(".or").hidden = recovering || resetting;
    document.querySelector(".auth-switch").hidden = recovering || resetting;
    const box = $("msg"); if (box) box.className = "msg";
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

  async function passwordSignup(email, password) {
    const res = await fetch("/auth/signup", {
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
      const data = await res.json().catch(() => null);
      if (data?.detail === "email_in_use") throw new Error("An account already exists for this email. Sign in instead.");
      if (res.status === 422) throw new Error("Enter a valid email and a password of at least 12 characters.");
      throw new Error(data?.detail || "We could not create your account. Please try again.");
    }
    return true;
  }

  async function accountAction(path, payload) {
    const res = await fetch(path, { method: "POST", credentials: "include", headers: {"Content-Type":"application/json", "Accept":"application/json", "X-Requested-With":"XMLHttpRequest"}, body: JSON.stringify(payload) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || "We could not complete that request.");
    return data;
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
        const ret = encodeURIComponent(parseReturn(authMode === "signup" ? "/ui/profile.html" : "/ui/index.html"));
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

    const params = new URL(location.href).searchParams;
    const requestedMode = params.get("mode");
    setMode(["signup", "reset"].includes(requestedMode) ? requestedMode : "signin");
    if (params.get("verification") === "success") flash("Email verified. You can sign in now.");
    if (params.get("verification") === "invalid") flash("That verification link is invalid or expired. Sign in to request another.", "error");
    $("mode-switch")?.addEventListener("click", () => setMode(authMode === "signup" ? "signin" : "signup"));
    $("forgot-password")?.addEventListener("click", () => setMode(authMode === "forgot" || authMode === "reset" ? "signin" : "forgot"));

    const form = $("login-form"); const submitBtn = $("submitBtn");
    if (!form || !submitBtn) return;
    let inflight = false;

    form.addEventListener("submit", async (e) => {
      e.preventDefault(); if (inflight) return; inflight = true;

      const email = ($("email")?.value || "").trim();
      const pw = $("password")?.value || "";
      const confirmation = $("confirm-password")?.value || "";
      if (authMode !== "reset" && !email) { flash("Please enter your email.", "error"); inflight = false; return; }
      if (authMode !== "forgot" && !pw) { flash("Please enter a password.", "error"); inflight = false; return; }
      if (authMode === "reset" && !params.get("token")) { flash("This reset link is incomplete. Request a new one.", "error"); inflight = false; return; }
      if (authMode === "signup" && pw.length < 12) { flash("Use at least 12 characters for your password.", "error"); inflight = false; return; }
      if (authMode === "signup" && pw !== confirmation) { flash("Those passwords do not match.", "error"); inflight = false; return; }

      submitBtn.disabled = true;
      submitBtn.dataset.prevText = submitBtn.textContent;
      submitBtn.textContent = authMode === "signup" ? "Creating account…" : authMode === "forgot" ? "Sending…" : authMode === "reset" ? "Updating…" : "Signing in…";
      form.querySelectorAll("input,button").forEach(el => el.disabled = true);
      flash(authMode === "signup" ? "Creating your account…" : "Signing in…");

      try {
        if (authMode === "forgot") {
          const result = await accountAction("/auth/forgot-password", {email});
          flash(result.message || "If that account exists, reset instructions have been sent.");
          form.querySelectorAll("input,button").forEach(el => el.disabled = false); inflight = false; return;
        }
        if (authMode === "reset") {
          await accountAction("/auth/reset-password", {token: params.get("token"), password: pw});
          const clean = new URL(location.href); clean.searchParams.delete("token"); clean.searchParams.delete("mode"); history.replaceState(null, "", clean.pathname + clean.search);
          setMode("signin"); flash("Password updated. Sign in with your new password.");
          form.reset(); form.querySelectorAll("input,button").forEach(el => el.disabled = false); inflight = false; return;
        }
        if (authMode === "signup") await passwordSignup(email, pw); else await passwordLogin(email, pw);
        const ok = await ensureAuth({ force: true });
        if (!ok) throw new Error("Authentication failed after login.");
        flash(authMode === "signup" ? "Account created. Let’s complete your athlete profile…" : "Login successful. Redirecting…");
        setTimeout(() => redirectToReturn(authMode === "signup" ? "/ui/profile.html" : "/ui/index.html"), 300);
      } catch (err) {
        console.error("[login] failed:", err);
        flash(err?.message || "Login failed. Please try again.", "error");
        submitBtn.disabled = false;
        submitBtn.textContent = authMode === "signup" ? "Create account" : "Sign in";
        form.querySelectorAll("input,button").forEach(el => el.disabled = false);
        inflight = false;
      }
    });
  });
})();
