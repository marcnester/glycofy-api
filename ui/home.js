// ui/home.js
(function () {
  const { fetchJSON } = window.__glyco; // uses credentials: 'include'

  async function loadDashboard() {
    try {
      const data = await fetchJSON("/dashboard/today");

      // --- Nutrition totals ---
      if (data.nutrition && data.nutrition.totals) {
        const t = data.nutrition.totals;
        const kcalEl = document.getElementById("kcal");
        const pEl = document.getElementById("protein");
        const cEl = document.getElementById("carbs");
        const fEl = document.getElementById("fat");

        if (kcalEl) kcalEl.textContent = t.kcal;
        if (pEl) pEl.textContent = `${t.protein_g} g`;
        if (cEl) cEl.textContent = `${t.carbs_g} g`;
        if (fEl) fEl.textContent = `${t.fat_g} g`;
      }

      // --- Meals table ---
      if (data.nutrition && Array.isArray(data.nutrition.meals)) {
        const rows = document.getElementById("meals-rows");
        if (rows) {
          rows.innerHTML = "";
          for (const m of data.nutrition.meals) {
            const tr = document.createElement("tr");
            [m.name, m.kcal, m.protein_g, m.carbs_g, m.fat_g].forEach((value) => {
              const td = document.createElement("td");
              td.textContent = String(value ?? "");
              tr.appendChild(td);
            });
            rows.appendChild(tr);
          }
        }
      }

      // --- Activities list ---
      if (data.activities && Array.isArray(data.activities.latest)) {
        const actsEl = document.getElementById("acts");
        if (actsEl) {
          actsEl.replaceChildren();
          data.activities.latest.forEach((a, index) => {
              const distKm = (a.distance_m / 1000).toFixed(1);
              const kcal = Math.round(a.kcal);
              const time = new Date(a.started).toLocaleString();
              if (index) actsEl.appendChild(document.createElement("br"));
              actsEl.appendChild(document.createTextNode(`${time} — ${a.sport} · ${distKm} km · ${kcal} kcal`));
            });
        }
      }
    } catch (err) {
      console.error("Dashboard load failed:", err);
      const errBox = document.getElementById("dash-error");
      if (errBox) {
        errBox.textContent = "Failed to load dashboard.";
        errBox.style.display = "";
      }
    }
  }

  document.addEventListener("DOMContentLoaded", loadDashboard);
})();
