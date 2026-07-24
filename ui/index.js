// /ui/index.js — Home dashboard (CSP-safe, no inline script)
// v2025-11-17
(function () {
  const G = window.__glyco || {};
  const { logout, setActiveNav } = G;

  // ---------------- tiny helpers ----------------
  const $ = (id) => document.getElementById(id);

  function todayISO() {
    const d = new Date();
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, "0");
    const da = String(d.getDate()).padStart(2, "0");
    return `${y}-${m}-${da}`;
  }

  function linkWithDate(anchor, path, date) {
    if (!anchor) return;
    const u = new URL(path, window.location.origin);
    u.searchParams.set("date", date);
    anchor.href = u.pathname + "?" + u.searchParams.toString();
  }

  async function getMe() {
    const r = await fetch("/users/me", { credentials: "include" });
    if (!r.ok) throw new Error("unauth");
    return r.json();
  }

  async function getPlan(date) {
    const r = await fetch(`/v1/plan/${date}`, { credentials: "include" });
    if (r.status === 404) {
      const err = new Error("notfound");
      err.status = 404;
      throw err;
    }
    if (!r.ok) throw new Error(String(r.status));
    return r.json();
  }

  async function seedPlanIfMissing(date) {
    await fetch(`/v1/plan/${date}?engine=heuristic&replace=false`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
      },
      body: JSON.stringify({}),
      credentials: "include",
    });
  }

  function putStat(id, val) {
    const el = $(id);
    if (!el) return;
    el.textContent = val == null ? "—" : val;
  }

  function renderUser(me) {
    const name = me?.name || me?.email || "athlete";
    if ($("accUser")) $("accUser").textContent = me?.email || "(unknown)";
    if ($("accStatus")) {
      $("accStatus").textContent = "Signed in";
      $("accStatus").className = "ok";
    }
    if ($("accAuth")) $("accAuth").textContent = "Cookie session";

    if ($("heroTitle")) $("heroTitle").textContent = `Welcome back, ${name}!`;
    if ($("heroSub"))
      $("heroSub").textContent = "Your plan for today is ready to tune.";
  }

  function renderSignedOut() {
    if ($("accStatus")) {
      $("accStatus").textContent = "Signed out";
      $("accStatus").className = "warn";
    }
    if ($("accAuth")) $("accAuth").textContent = "No active session";
    if ($("heroTitle"))
      $("heroTitle").textContent = "Eat with intent. Train with data.";
    if ($("heroSub"))
      $("heroSub").textContent = "Sign in to build meals that match your goals.";
    if ($("goLogin")) $("goLogin").style.display = "";
  }

  function renderPlanSummary(plan) {
    const t = plan?.totals || {};
    putStat("sCal", t.kcal != null ? Math.round(t.kcal) : "—");
    putStat("sP", t.protein_g != null ? Math.round(t.protein_g) : "—");
    putStat("sC", t.carbs_g != null ? Math.round(t.carbs_g) : "—");
    putStat("sF", t.fat_g != null ? Math.round(t.fat_g) : "—");

    const byType = { breakfast: null, lunch: null, dinner: null, snack: null };
    for (const m of plan?.meals || []) {
      if (m && byType[m.meal_type] == null) {
        byType[m.meal_type] = m;
      }
    }

    const order = ["breakfast", "lunch", "dinner", "snack"];
    const list = $("todayList");
    if (!list) return;
    list.innerHTML = "";

    for (const mt of order) {
      const m = byType[mt];
      const titleBase = mt.charAt(0).toUpperCase() + mt.slice(1);
      const title =
        m && m.title ? `${titleBase}: ${m.title}` : titleBase;

      const kcal = m?.kcal != null ? Math.round(m.kcal) : "—";
      const P = m?.protein_g != null ? Math.round(m.protein_g) : "—";
      const C = m?.carbs_g != null ? Math.round(m.carbs_g) : "—";
      const F = m?.fat_g != null ? Math.round(m.fat_g) : "—";

      const row = document.createElement("div");
      row.className = "row";
      row.innerHTML = `
        <div>${title}</div>
        <div class="muted">${kcal} kcal · P ${P} · C ${C} · F ${F}</div>
      `;
      list.appendChild(row);
    }
  }

  function bindLogout(el) {
    if (!el) return;
    el.addEventListener("click", async (e) => {
      e.preventDefault?.();
      try {
        if (typeof logout === "function") {
          await logout();
        } else {
          await fetch("/auth/logout", {
            method: "POST",
            credentials: "include",
          });
        }
      } catch {
        // ignore
      }
      const ret = encodeURIComponent("/ui/index.html");
      window.location.href = `/ui/login.html?return=${ret}`;
    });
  }

  function initStaticBits() {
    if ($("yr")) $("yr").textContent = new Date().getFullYear();
    if ($("apiNote")) $("apiNote").textContent = "API: " + window.location.origin;
    const openPlanner = $("openPlanner");
    if (openPlanner) {
      openPlanner.addEventListener("click", () => {
        const d = todayISO();
        window.location.href =
          "/ui/plan.html?date=" + encodeURIComponent(d);
      });
    }
    const goLogin = $("goLogin");
    if (goLogin) {
      goLogin.addEventListener("click", () => {
        const ret = encodeURIComponent("/ui/index.html");
        window.location.href = `/ui/login.html?return=${ret}`;
      });
    }
  }

  async function main() {
    initStaticBits();
    if (typeof setActiveNav === "function") {
      setActiveNav("Home");
    }

    const d = todayISO();

    // Carry today's date into links
    linkWithDate($("adjustMeals"), "/ui/plan.html", d);
    linkWithDate($("viewActivities"), "/ui/activities.html", d);
    linkWithDate($("openProfile"), "/ui/profile.html", d);

    // Bind logout buttons (topbar + card)
    bindLogout(document.querySelector("#logout_btn,[data-nav='logout']"));
    bindLogout($("doLogout"));

    try {
      const me = await getMe();
      renderUser(me);

      let plan;
      try {
        plan = await getPlan(d);
      } catch (e) {
        if (e && e.status === 404) {
          await seedPlanIfMissing(d);
          plan = await getPlan(d);
        } else {
          throw e;
        }
      }
      renderPlanSummary(plan);

      if ($("dlTxt")) {
        $("dlTxt").href = `/v1/plan/${d}/grocery.txt`;
        $("dlTxt").style.display = "";
      }
      if ($("dlCsv")) {
        $("dlCsv").href = `/v1/plan/${d}/grocery.csv`;
        $("dlCsv").style.display = "";
      }
    } catch {
      // not signed in
      renderSignedOut();
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", main);
  } else {
    main();
  }
})();
