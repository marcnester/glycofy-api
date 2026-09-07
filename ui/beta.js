(function () {
  "use strict";
  const originalFetch = window.fetch.bind(window);
  const pagePath = location.pathname;
  const viewport = () => innerWidth < 600 ? "mobile" : innerWidth < 960 ? "tablet" : "desktop";
  const sessionId = sessionStorage.getItem("glycofy.betaSession") || crypto.randomUUID();
  sessionStorage.setItem("glycofy.betaSession", sessionId);
  let lastRequestId = sessionStorage.getItem("glycofy.lastRequestId");
  let analyticsEnabled = false;
  const TOUR_KEY = "glycofy.welcomeTour.v1";

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

  function buildResilienceUI() {
    const main = document.querySelector("main");
    if (main && !main.id) main.id = "main-content";
    if (main && !document.querySelector(".skip-link")) {
      const skip = document.createElement("a");
      skip.className = "skip-link";
      skip.href = "#" + main.id;
      skip.textContent = "Skip to content";
      document.body.prepend(skip);
    }
    const offline = document.createElement("div");
    offline.className = "connection-banner";
    offline.setAttribute("role", "status");
    offline.setAttribute("aria-live", "polite");
    offline.hidden = navigator.onLine;
    offline.textContent = "You’re offline. Glycofy will reconnect when your connection returns.";
    document.body.append(offline);
    const syncConnection = () => { offline.hidden = navigator.onLine; };
    addEventListener("online", syncConnection);
    addEventListener("offline", syncConnection);
  }

  function buildWelcomeTour() {
    if (!/^\/ui\/(index|plan|profile|activities|grocery|plan-week)\.html$/.test(pagePath)) return;
    try { if (localStorage.getItem(TOUR_KEY)) return; } catch (_) { return; }
    const steps = [
      {eyebrow:"Welcome to Glycofy", title:"Fuel the way you train", copy:"Glycofy combines your athlete profile, training context, and food preferences to build practical meal plans for performance."},
      {eyebrow:"1 · Training", title:"Give every plan context", copy:"Connect Strava, import a TrainingPeaks CSV, or add upcoming sessions. More training context means more precise fueling and recovery guidance."},
      {eyebrow:"2 · Plan", title:"Build today or the full week", copy:"Generate an AI plan, review the nutrition and cooking guidance, then swap or log meals as your schedule changes."},
      {eyebrow:"3 · Grocery", title:"Turn the plan into action", copy:"Review one normalized shopping list, adjust household servings, mark pantry items, and export or hand off the final list."},
      {eyebrow:"You’re ready", title:"Make Glycofy better with you", copy:"Use Send feedback from any page if something feels unclear, slow, or exceptional. Technical context is attached—never your meals, health profile, or workouts."}
    ];
    let index = 0;
    const dialog = document.createElement("dialog");
    dialog.className = "welcome-tour";
    dialog.setAttribute("aria-labelledby", "welcome-tour-title");
    dialog.innerHTML = `<div class="welcome-tour__body"><div class="welcome-tour__progress" aria-label="Tour progress"></div><p class="welcome-tour__eyebrow"></p><h2 id="welcome-tour-title"></h2><p class="welcome-tour__copy"></p><div class="welcome-tour__actions"><button class="btn welcome-tour__skip" type="button">Skip tour</button><span class="welcome-tour__spacer"></span><button class="btn welcome-tour__back" type="button">Back</button><button class="btn welcome-tour__next" type="button">Next</button></div></div>`;
    document.body.append(dialog);
    const progress = dialog.querySelector(".welcome-tour__progress");
    const back = dialog.querySelector(".welcome-tour__back");
    const next = dialog.querySelector(".welcome-tour__next");
    const finish = () => { try { localStorage.setItem(TOUR_KEY, "complete"); } catch (_) {} dialog.close(); dialog.remove(); };
    const render = () => {
      const step = steps[index];
      dialog.querySelector(".welcome-tour__eyebrow").textContent = step.eyebrow;
      dialog.querySelector("h2").textContent = step.title;
      dialog.querySelector(".welcome-tour__copy").textContent = step.copy;
      progress.innerHTML = steps.map((_, i) => `<span class="${i === index ? "active" : ""}" aria-hidden="true"></span>`).join("");
      progress.setAttribute("aria-label", `Step ${index + 1} of ${steps.length}`);
      back.hidden = index === 0;
      next.textContent = index === steps.length - 1 ? "Start planning" : "Next";
    };
    dialog.querySelector(".welcome-tour__skip").addEventListener("click", finish);
    back.addEventListener("click", () => { index -= 1; render(); });
    next.addEventListener("click", () => { if (index === steps.length - 1) finish(); else { index += 1; render(); } });
    dialog.addEventListener("cancel", e => { e.preventDefault(); finish(); });
    render();
    dialog.showModal();
  }

  originalFetch("/v1/beta/config", {credentials:"include"}).then(async response => {
    if (!response.ok) return;
    const config = await response.json();
    analyticsEnabled = config.analytics_enabled;
    buildResilienceUI();
    if (config.feedback_enabled) buildFeedback();
    buildWelcomeTour();
    event(pagePath.includes("grocery") ? "grocery_opened" : "page_view");
  }).catch(() => {});
})();
