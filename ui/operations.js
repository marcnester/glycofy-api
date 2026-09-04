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

document.getElementById("window").addEventListener("change", loadOperations);
loadOperations();
