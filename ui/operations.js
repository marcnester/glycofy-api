const number = new Intl.NumberFormat();

async function loadOperations() {
  const state = document.getElementById("state");
  const hours = document.getElementById("window").value;
  state.textContent = "Refreshing…";
  try {
    const response = await fetch(`/v1/operations/ai-summary?hours=${hours}`, { credentials: "same-origin" });
    if (response.status === 401) {
      location.href = "/ui/login.html";
      return;
    }
    if (!response.ok) throw new Error(response.status === 404 ? "This dashboard is available only to configured operators." : "Operational data is unavailable.");
    const data = await response.json();
    document.getElementById("requests").textContent = number.format(data.requests);
    document.getElementById("failures").textContent = `${(data.failure_rate * 100).toFixed(1)}%`;
    document.getElementById("failureBar").style.width = `${Math.min(100, data.failure_rate * 100)}%`;
    document.getElementById("latency").textContent = data.latency_ms.p95 == null ? "—" : `${(data.latency_ms.p95 / 1000).toFixed(1)}s`;
    document.getElementById("cost").textContent = `$${data.estimated_cost_usd.toFixed(3)}`;
    document.getElementById("tokens").textContent = number.format(data.tokens.input + data.tokens.output);
    document.getElementById("tokenDetail").textContent = `${number.format(data.tokens.input)} input · ${number.format(data.tokens.output)} output`;
    document.getElementById("jobs").textContent = number.format(data.weekly_jobs.active);
    document.getElementById("jobDetail").textContent = `${data.weekly_jobs.failed} failed in this window`;
    state.textContent = `Updated ${new Date().toLocaleTimeString()} · ${data.privacy}`;
  } catch (error) {
    state.textContent = error.message;
  }
}

function rows(items, formatter, empty) {
  if (!items.length) return empty;
  return items.slice(0, 6).map(formatter).join("<hr style='border:0;border-top:1px solid var(--border);margin:12px 0'>");
}

async function loadBeta() {
  try {
    const [summaryResponse, feedbackResponse, jobsResponse] = await Promise.all([
      fetch("/v1/operations/beta-summary?days=30", {credentials:"same-origin"}),
      fetch("/v1/operations/feedback?limit=20", {credentials:"same-origin"}),
      fetch("/v1/operations/failed-jobs?limit=20", {credentials:"same-origin"})
    ]);
    if (![summaryResponse, feedbackResponse, jobsResponse].every(response => response.ok)) return;
    const [summary, feedback, jobs] = await Promise.all([summaryResponse.json(), feedbackResponse.json(), jobsResponse.json()]);
    document.getElementById("activeUsers").textContent = number.format(summary.active_users);
    document.getElementById("plansCompleted").textContent = number.format(summary.events.weekly_plan_completed || 0);
    document.getElementById("groceryApprovals").textContent = number.format(summary.events.grocery_approved || 0);
    document.getElementById("newFeedback").textContent = number.format(summary.feedback.new);
    document.getElementById("feedbackQueue").innerHTML = rows(feedback, item => `<strong>${item.category}</strong> · ${item.page_path}<br>${item.message.replace(/[<>&]/g, character => ({"<":"&lt;", ">":"&gt;", "&":"&amp;"}[character]))}<br><small>${item.browser} · ${item.viewport} · ${item.request_id || "no request ID"}</small>`, "No feedback yet.");
    document.getElementById("failedJobs").innerHTML = rows(jobs, item => `<strong>${item.error_code || "Unknown failure"}</strong><br><small>Reference ${item.error_reference || "unavailable"} · attempt ${item.attempt_count}</small>`, "No failed jobs.");
  } catch (_) { /* AI operations remain useful if beta metrics are unavailable */ }
}

async function loadSecurity() {
  try {
    const hours = document.getElementById("window").value;
    const response = await fetch(`/v1/operations/security-summary?hours=${hours}`, {credentials:"same-origin"});
    if (!response.ok) return;
    const summary = await response.json();
    document.getElementById("securityEvents").textContent = number.format(summary.events);
    document.getElementById("securityAlerts").textContent = number.format(summary.by_severity.alert || 0);
    document.getElementById("recentAlerts").innerHTML = rows(
      summary.recent_alerts,
      item => `<strong>${item.event_type}</strong> · ${item.outcome}<br><small>${new Date(item.occurred_at).toLocaleString()} · Request ${item.request_id || "unavailable"}</small>`,
      "No security alerts in this window."
    );
  } catch (_) { /* Other operations data remains available during partial failure. */ }
}

async function loadNutritionSource() {
  const source = document.getElementById("nutritionSource");
  const detail = document.getElementById("nutritionSourceDetail");
  try {
    const [healthResponse, summaryResponse] = await Promise.all([
      fetch("/v1/operations/nutrition-source-health", {credentials:"same-origin"}),
      fetch("/v1/operations/nutrition-validation-summary", {credentials:"same-origin"})
    ]);
    if (!healthResponse.ok || !summaryResponse.ok) throw new Error("Unavailable");
    const [data, summary] = await Promise.all([healthResponse.json(), summaryResponse.json()]);
    source.textContent = data.status === "ok" && data.required ? "USDA available" : "Needs attention";
    detail.textContent = `${data.data_type} · FDC ${data.fdc_id} · ${data.nutrients_present.join(", ")}`;
    const pending = (summary.queue.queued || 0) + (summary.queue.retry || 0);
    document.getElementById("nutritionCatalog").textContent = `${number.format(summary.catalog_foods)} verified foods`;
    document.getElementById("nutritionQueue").textContent = `${number.format(pending)} pending · ${number.format(summary.due)} ready for retry · ${number.format(summary.queue.abandoned || 0)} need review`;
  } catch (_) {
    source.textContent = "Unavailable";
    detail.textContent = "USDA is not responding; planning can continue with guarded estimates.";
    document.getElementById("nutritionCatalog").textContent = "Status unavailable";
    document.getElementById("nutritionQueue").textContent = "Validation coverage could not be loaded.";
  }
}

document.getElementById("window").addEventListener("change", () => { loadOperations(); loadSecurity(); });
loadOperations();
loadBeta();
loadSecurity();
loadNutritionSource();
