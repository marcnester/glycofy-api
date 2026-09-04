(function () {
  "use strict";
  const originalFetch = window.fetch.bind(window);
  const pagePath = location.pathname;
  const viewport = () => innerWidth < 600 ? "mobile" : innerWidth < 960 ? "tablet" : "desktop";
  const sessionId = sessionStorage.getItem("glycofy.betaSession") || crypto.randomUUID();
  sessionStorage.setItem("glycofy.betaSession", sessionId);
  let lastRequestId = sessionStorage.getItem("glycofy.lastRequestId");
  let analyticsEnabled = false;

  async function event(eventName) {
    if (!analyticsEnabled) return;
    try {
      await originalFetch("/v1/beta/events", {
        method: "POST", credentials: "include", keepalive: true,
        headers: {"Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest"},
        body: JSON.stringify({event_name: eventName, page_path: pagePath, viewport: viewport(), session_id: sessionId})
      });
    } catch (_) { /* analytics must never interrupt the product */ }
  }

  window.fetch = async function (input, init) {
    const response = await originalFetch(input, init);
    const requestId = response.headers.get("x-request-id");
    if (requestId) {
      lastRequestId = requestId;
      sessionStorage.setItem("glycofy.lastRequestId", requestId);
    }
    const url = typeof input === "string" ? input : input.url;
    const method = String((init && init.method) || "GET").toUpperCase();
    if (!response.ok && response.status >= 500) event("request_failed");
    if (response.ok && method === "POST" && url.includes("/recommend/weekly/jobs")) event("weekly_plan_started");
    if (response.ok && method === "POST" && url.includes("grocery-list/approval")) event("grocery_approved");
    if (response.ok && method === "POST" && url.includes("grocery-list/shopping")) event("grocery_handoff_started");
    if (response.ok && ["PUT", "PATCH"].includes(method) && url === "/users/me" && String(init?.body || "").includes("height_cm")) event("onboarding_completed");
    if (response.ok && method === "GET" && /weekly\/jobs\/[a-f0-9]+/.test(url)) {
      response.clone().json().then(data => {
        const key = `glycofy.betaCompleted.${data.job_id}`;
        if (data.status === "completed" && !sessionStorage.getItem(key)) {
          sessionStorage.setItem(key, "1");
          event("weekly_plan_completed");
        }
      }).catch(() => {});
    }
    return response;
  };

  function buildFeedback() {
    const button = document.createElement("button");
    button.className = "beta-feedback-button";
    button.type = "button";
    button.textContent = "Send feedback";
    button.setAttribute("aria-haspopup", "dialog");
    const dialog = document.createElement("dialog");
    dialog.className = "beta-feedback-dialog";
    dialog.innerHTML = `<form method="dialog" class="beta-feedback-form"><div class="beta-feedback-head"><div><span class="beta-feedback-kicker">Glycofy beta</span><h2>Help us make this exceptional</h2></div><button class="beta-feedback-close" value="cancel" aria-label="Close">×</button></div><label>What would you like to share?<select name="category"><option value="idea">An idea</option><option value="issue">Something is broken</option><option value="confusing">Something is confusing</option><option value="praise">Something works well</option><option value="other">Other</option></select></label><label>How is Glycofy feeling?<select name="rating"><option value="">No rating</option><option value="5">Excellent</option><option value="4">Good</option><option value="3">Okay</option><option value="2">Frustrating</option><option value="1">Blocked</option></select></label><label>Your feedback<textarea name="message" minlength="3" maxlength="1200" required placeholder="Tell us what happened or what would make this better."></textarea></label><p class="beta-feedback-privacy">We attach this page, browser type, screen size, and a request ID. We never attach meals, health information, or activity details.</p><p class="beta-feedback-state" role="status"></p><button class="btn beta-feedback-submit" value="send">Send feedback</button></form>`;
    document.body.append(button, dialog);
    button.addEventListener("click", () => { dialog.showModal(); event("feedback_opened"); });
    dialog.addEventListener("click", e => { if (e.target === dialog) dialog.close(); });
    dialog.querySelector("form").addEventListener("submit", async e => {
      if (e.submitter?.value !== "send") return;
      e.preventDefault();
      const form = e.currentTarget;
      const state = form.querySelector(".beta-feedback-state");
      const submit = form.querySelector(".beta-feedback-submit");
      submit.disabled = true; state.textContent = "Sending…";
      const data = new FormData(form);
      try {
        const response = await originalFetch("/v1/beta/feedback", {method:"POST", credentials:"include", headers:{"Content-Type":"application/json", "X-Requested-With":"XMLHttpRequest"}, body:JSON.stringify({category:data.get("category"), rating:data.get("rating") ? Number(data.get("rating")) : null, message:data.get("message"), page_path:pagePath, viewport:viewport(), related_request_id:lastRequestId})});
        if (!response.ok) throw new Error("Feedback could not be sent.");
        state.textContent = "Thank you — your feedback is in."; event("feedback_sent");
        setTimeout(() => { dialog.close(); form.reset(); state.textContent = ""; }, 900);
      } catch (error) { state.textContent = error.message; } finally { submit.disabled = false; }
    });
  }

  originalFetch("/v1/beta/config", {credentials:"include"}).then(async response => {
    if (!response.ok) return;
    const config = await response.json();
    analyticsEnabled = config.analytics_enabled;
    if (config.feedback_enabled) buildFeedback();
    event(pagePath.includes("grocery") ? "grocery_opened" : "page_view");
  }).catch(() => {});
})();
