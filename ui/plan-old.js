// ui/plan.js — Plan page renderer for Glycofy (CSP-safe, no modules)
// Consumes /v1/plan/{date} with shape:
// { date, diet_pref, locked, totals:{kcal,protein_g,carbs_g,fat_g},
//   meals:[{ meal_type, title, kcal, protein_g, carbs_g, fat_g, ingredients?, instructions? }], ... }

(function () {
  const { $, qs, show, hide, fmt, ensureAuth, fetchJSON } = window.__glyco || {};

  // ------------------------- DOM lookup helpers -------------------------
  function el(idOrSel) {
    return document.getElementById(idOrSel) || document.querySelector(idOrSel);
  }

  function ensureChild(parent, selector, create) {
    let node = parent.querySelector(selector);
    if (!node && typeof create === 'function') {
      node = create();
      parent.appendChild(node);
    }
    return node;
  }

  // Try several ID/data-* conventions so this works with older/newer HTML
  function findMealContainer(type) {
    return (
      el(`meal_${type}`) ||
      el(`#meal_${type}`) ||
      el(`[data-meal="${type}"]`) ||
      el(`#${type}`) ||
      el(`section.meal-${type}`) ||
      null
    );
  }

  // Create a pleasant default block if the HTML didn't provide one
  function createMealShell(type, pretty) {
    const wrap = document.createElement('section');
    wrap.className = `panel meal meal-${type}`;
    wrap.setAttribute('data-meal', type);

    const header = document.createElement('div');
    header.className = 'meal-header';

    const title = document.createElement('h3');
    title.className = 'meal-title';
    title.textContent = pretty;

    const summary = document.createElement('div');
    summary.className = 'meal-summary muted';

    header.appendChild(title);
    header.appendChild(summary);

    const body = document.createElement('div');
    body.className = 'meal-body';

    const list = document.createElement('ul');
    list.className = 'meal-items';
    list.style.margin = '6px 0 0 0';

    body.appendChild(list);
    wrap.appendChild(header);
    wrap.appendChild(body);
    return wrap;
  }

  function getOrBuildMealUI(type) {
    const pretty = {
      breakfast: 'Breakfast',
      lunch: 'Lunch',
      dinner: 'Dinner',
      snack: 'Snack',
    }[type] || type;

    let root = findMealContainer(type);
    if (!root) {
      // If not present in DOM, append to a generic grid or to main
      const grid =
        el('#meals_grid') ||
        el('.meals-grid') ||
        el('#main') ||
        el('main') ||
        document.body;
      root = createMealShell(type, pretty);
      grid.appendChild(root);
    }

    // Ensure the three subareas exist
    const header = ensureChild(root, '.meal-header', () => {
      const div = document.createElement('div');
      div.className = 'meal-header';
      return div;
    });

    const title =
      header.querySelector('.meal-title') ||
      (function () {
        const h = document.createElement('h3');
        h.className = 'meal-title';
        header.appendChild(h);
        return h;
      })();

    const summary =
      header.querySelector('.meal-summary') ||
      (function () {
        const s = document.createElement('div');
        s.className = 'meal-summary muted';
        header.appendChild(s);
        return s;
      })();

    const body = ensureChild(root, '.meal-body', () => {
      const div = document.createElement('div');
      div.className = 'meal-body';
      return div;
    });

    const list =
      body.querySelector('.meal-items') ||
      (function () {
        const ul = document.createElement('ul');
        ul.className = 'meal-items';
        ul.style.marginTop = '6px';
        body.appendChild(ul);
        return ul;
      })();

    return { root, header, title, summary, list, pretty };
  }

  // ------------------------- Date & navigation -------------------------
  function dateToISO(d) {
    return (d instanceof Date ? d : new Date(d)).toISOString().slice(0, 10);
    }
  function addDays(iso, n) {
    const d = new Date(iso + 'T00:00:00');
    d.setDate(d.getDate() + n);
    return dateToISO(d);
  }

  let currentISO = dateToISO(new Date());

  // ------------------------- Rendering -------------------------
  function renderTotals(totals) {
    // Totals cards (try multiple IDs used across our templates)
    const kcalEl = el('total_kcal') || el('#total_kcal') || el('[data-total="kcal"]');
    const pEl = el('total_protein') || el('#total_protein') || el('[data-total="protein"]');
    const cEl = el('total_carbs') || el('#total_carbs') || el('[data-total="carbs"]');
    const fEl = el('total_fat') || el('#total_fat') || el('[data-total="fat"]');

    if (kcalEl) kcalEl.textContent = (totals?.kcal ?? '—').toString();
    if (pEl) pEl.textContent = (totals?.protein_g ?? '—').toString();
    if (cEl) cEl.textContent = (totals?.carbs_g ?? '—').toString();
    if (fEl) fEl.textContent = (totals?.fat_g ?? '—').toString();
  }

  function renderHeader(dateISO, locked) {
    const title =
      el('dateTitle') || el('#dateTitle') || el('h1.page-title') || el('h1');
    if (title) {
      try {
        const d = new Date(dateISO + 'T00:00:00');
        const pretty = d.toLocaleDateString(undefined, {
          weekday: 'long',
          year: 'numeric',
          month: 'long',
          day: 'numeric',
        });
        title.textContent = pretty;
      } catch {
        title.textContent = dateISO;
      }
    }
    const lockBadge =
      el('lockBadge') || el('#lockBadge') || el('[data-lock-badge]');
    if (lockBadge) {
      lockBadge.textContent = locked ? 'Locked' : 'Unlocked';
      lockBadge.classList.toggle('locked', !!locked);
    }
  }

  function formatMacroLine(node) {
    const kcal = node?.kcal ?? '—';
    const p = node?.protein_g ?? '—';
    const c = node?.carbs_g ?? '—';
    const f = node?.fat_g ?? '—';
    return `${kcal} kcal · P ${p} · C ${c} · F ${f}`;
  }

  function clearChildren(elm) {
    while (elm && elm.firstChild) elm.removeChild(elm.firstChild);
  }

  function renderMeals(meals) {
    // Normalize into a map by type; your shape already uses meal_type
    const byType = { breakfast: [], lunch: [], dinner: [], snack: [] };
    (meals || []).forEach((m) => {
      const key = (m?.meal_type || '').toLowerCase();
      if (byType[key]) byType[key].push(m);
    });

    Object.entries(byType).forEach(([type, list]) => {
      const ui = getOrBuildMealUI(type);
      // If multiple entries exist for a meal_type, aggregate macros and list ingredients beneath.
      // Title shows the first recipe title (or the meal name if none).
      const first = list[0];
      ui.title.textContent = first?.title ? `${ui.pretty} — ${first.title}` : ui.pretty;
      ui.summary.textContent = list.length
        ? formatMacroLine({
            kcal: sum(list.map((x) => +x.kcal || 0)),
            protein_g: sum(list.map((x) => +x.protein_g || 0)),
            carbs_g: sum(list.map((x) => +x.carbs_g || 0)),
            fat_g: sum(list.map((x) => +x.fat_g || 0)),
          })
        : '— kcal · P — · C — · F —';

      clearChildren(ui.list);

      if (!list.length) {
        const li = document.createElement('li');
        li.className = 'muted';
        li.textContent = 'No items yet.';
        ui.list.appendChild(li);
        return;
      }

      list.forEach((m) => {
        const li = document.createElement('li');
        li.className = 'meal-entry';
        const head = document.createElement('div');
        head.className = 'meal-entry-head';
        head.textContent = m.title || '(Untitled)';
        const macro = document.createElement('div');
        macro.className = 'meal-entry-macros muted';
        macro.textContent = formatMacroLine(m);
        head.appendChild(macro);

        const ingred = Array.isArray(m.ingredients) ? m.ingredients : [];
        const ingList = document.createElement('ul');
        ingList.className = 'meal-entry-ingredients';
        ingred.forEach((it) => {
          const ii = document.createElement('li');
          ii.textContent = String(it);
          ingList.appendChild(ii);
        });

        li.appendChild(head);
        if (ingred.length) li.appendChild(ingList);

        if (m.instructions) {
          const instr = document.createElement('div');
          instr.className = 'meal-entry-instructions muted';
          instr.style.marginTop = '4px';
          instr.textContent = m.instructions;
          li.appendChild(instr);
        }
        ui.list.appendChild(li);
      });
    });
  }

  function sum(arr) {
    let s = 0;
    for (let i = 0; i < arr.length; i++) s += Number(arr[i]) || 0;
    return Math.round((s + Number.EPSILON) * 100) / 100;
  }

  // ------------------------- Data fetch -------------------------
  async function loadPlan(iso) {
    const url = `/v1/plan/${iso}`;
    const data = await fetchJSON(url); // cookie or bearer handled by fetchJSON
    renderHeader(data?.date || iso, !!data?.locked);
    renderTotals(data?.totals || null);
    renderMeals(data?.meals || []);
  }

  // ------------------------- Wiring -------------------------
  function wireNav() {
    const prevBtn = el('prevBtn') || el('#prevBtn') || el('button.prev');
    const nextBtn = el('nextBtn') || el('#nextBtn') || el('button.next');
    const todayBtn = el('todayBtn') || el('#todayBtn') || el('button.today');
    const lockBtn = el('lockBtn') || el('#lockBtn') || el('button.lock');

    if (prevBtn) prevBtn.addEventListener('click', () => navigate(-1));
    if (nextBtn) nextBtn.addEventListener('click', () => navigate(1));
    if (todayBtn) todayBtn.addEventListener('click', () => gotoToday());

    if (lockBtn) {
      lockBtn.addEventListener('click', async () => {
        try {
          // Minimal optimistic toggle; server-side lock endpoint available in prior builds.
          // If your API is /v1/plan/{d}/lock?lock=true|false, call it here.
          // For now we just reload to reflect actual server state.
          await loadPlan(currentISO);
        } catch (e) {
          console.error(e);
        }
      });
    }

    // Keyboard ← → shortcuts
    document.addEventListener('keydown', (e) => {
      if (e.key === 'ArrowLeft') navigate(-1);
      else if (e.key === 'ArrowRight') navigate(1);
    });
  }

  async function navigate(deltaDays) {
    currentISO = addDays(currentISO, deltaDays);
    await loadPlan(currentISO);
  }
  async function gotoToday() {
    currentISO = dateToISO(new Date());
    await loadPlan(currentISO);
  }

  // ------------------------- Init -------------------------
  async function init() {
    try {
      if (!ensureAuth()) return;
      wireNav();
      // If the page already encoded a date (e.g., ?d=YYYY-MM-DD), honor it
      try {
        const u = new URL(location.href);
        const d = u.searchParams.get('d');
        if (d) currentISO = d;
      } catch {}
      await loadPlan(currentISO);
    } catch (e) {
      console.error('[plan:init]', e);
      // Show a small banner if app.js banner exists
      try {
        const banner = document.getElementById('__glyco_err_banner');
        if (banner) {
          banner.textContent = `Failed to load plan: ${e?.message || e}`;
          banner.style.display = '';
        }
      } catch {}
    }
  }

  document.addEventListener('DOMContentLoaded', init);
})();
