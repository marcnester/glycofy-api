// /ui/plan.js — Plan page with AI overlay support (meta.ai_idea) + persisted AI meals + Week(AI) + instructions
// v2025-11-20c
(function () {
  const {
    ensureAuth,
    fetchJSON,
    getUser,
    setActiveNav,
    redirectToReturn,
    logout,
  } = window.__glyco || {};
  if (!ensureAuth) {
    window.location.href =
      '/ui/login.html?return=' +
      encodeURIComponent(location.pathname + location.search);
    return;
  }

  const $ = (id) => document.getElementById(id);
  const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, (ch) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  })[ch]);
  document.body.classList.add('ui-root');
  setActiveNav && setActiveNav('Plan');

  // Detect layout version (new cards vs legacy list)
  const NEW_LAYOUT = !!document.querySelector('.meal-card[data-slot]');

  // Header controls (support both old + new IDs)
  const dateInput = $('date_input') || $('plan-date-chip');
  let dateDisplay = $('plan-date-display') || $('plan_date_display') || $('plan-current-date');

  // Ensure the selected plan date is always visible, even if plan.html
  // does not already include a dedicated date element.
  function ensureDateDisplay() {
    if (dateDisplay) return dateDisplay;

    const h1 = document.querySelector('h1');
    if (!h1 || !h1.parentNode) return null;

    const el = document.createElement('div');
    el.id = 'plan-date-display';
    el.className = 'muted plan-date-display';
    el.style.marginTop = '4px';
    el.style.marginBottom = '14px';
    el.style.fontSize = '1rem';
    el.style.fontWeight = '600';
    el.style.letterSpacing = '0.01em';
    h1.insertAdjacentElement('afterend', el);
    dateDisplay = el;
    return dateDisplay;
  }

  const prevBtn = $('prev_day') || $('plan-prev');
  const nextBtn = $('next_day') || $('plan-next');
  const lockBtn = $('lock_toggle') || $('plan-lock');
  const regenBtn = $('regen_btn') || $('plan-regen');
  const weekBtn = $('plan-ai-week') || $('week_ai_btn');
  const dlTxt = $('dl_txt');
  const dlCsv = $('dl_csv');
  const groceryListLink = $('grocery-list-link');
  const flashBox = $('plan-msg') || $('flash');

  const totalsEl =
    $('totals') || $('plan-totals') || document.getElementById('totals');
  const tKcal = $('plan-total-kcal') || $('t_kcal');
  const tP = $('plan-total-protein') || $('t_p');
  const tC = $('plan-total-carbs') || $('t_c');
  const tF = $('plan-total-fat') || $('t_f');
  const tSrc = $('plan-source') || $('t_src');
  const tLock = $('plan-locked') || $('t_lock');

  const mealsRoot =
    $('meals') || document.querySelector('.plan-meals') || document.body;
  const emptyEl = $('empty_state');
  const createBtn = $('create_btn');

  // Busy overlay
  const busyEl = $('plan-busy');
  const busyMsg = $('plan-busy-msg');
  const busyMeta = $('plan-busy-meta');
  let busyStartedAt = 0;
  let busyTimer = null;

  const WEEKLY_PROGRESS_STAGES = [
    [0, 'Reviewing your goals and training…'],
    [5, 'Designing 28 meals as one balanced week…'],
    [14, 'Balancing macros and weekly variety…'],
    [24, 'Checking diet and ingredient exclusions…'],
    [34, 'Adding quantities and cooking instructions…'],
    [45, 'Saving your personalized week…'],
  ];

  function updateBusyProgress(serverMessage) {
    if (!busyStartedAt) return;
    const elapsed = Math.max(0, Math.floor((Date.now() - busyStartedAt) / 1000));
    const localStage = WEEKLY_PROGRESS_STAGES.reduce(
      (current, stage) => (elapsed >= stage[0] ? stage : current),
      WEEKLY_PROGRESS_STAGES[0]
    );
    if (busyMsg) busyMsg.textContent = serverMessage || localStage[1];
    if (busyMeta) {
      busyMeta.textContent = `${elapsed}s elapsed · You can safely leave this page; planning will continue.`;
    }
  }

  function setBusy(on, msg) {
    if (!busyEl) return;
    busyEl.style.display = on ? 'flex' : 'none';
    if (on) {
      busyStartedAt = Date.now();
      updateBusyProgress(msg);
      clearInterval(busyTimer);
      busyTimer = setInterval(() => updateBusyProgress(), 1000);
    } else {
      clearInterval(busyTimer);
      busyTimer = null;
      busyStartedAt = 0;
    }
  }

  // Logout wiring for this page
  const logoutBtn =
    document.querySelector('[data-nav="logout"]') || $('logout_btn');
  if (logoutBtn) {
    logoutBtn.addEventListener('click', async (e) => {
      e.preventDefault();
      try {
        if (typeof logout === 'function') {
          await logout();
          return;
        }
      } catch (err) {
        console.error('Logout failed', err);
      }
      // Fallback: just hard-redirect
      window.location.href = '/ui/login.html';
    });
  }

  // ----- state -----
  let CURRENT_PLAN = null;
  let CURRENT_DATE = null;
  let AI_REASONS = {}; // slot -> reason string
  let AI_FREEFORM = {}; // slot -> { idea, description, approx_macros, ingredients[], instructions[] }

  // ----- utils -----
  function flash(msg, type = 'ok') {
    if (!flashBox) return;
    flashBox.textContent = msg;
    flashBox.style.display = '';

    if (flashBox.id === 'plan-msg') {
      flashBox.className =
        'plan-msg ' + (type === 'error' ? 'plan-msg--error' : 'plan-msg--ok');
    } else {
      flashBox.className =
        'flash ' + (type === 'error' ? 'flash--error' : 'flash--ok');
    }

    setTimeout(() => {
      if (flashBox) flashBox.style.display = 'none';
    }, 4200);
  }

  const fmt = (n) => (n == null || isNaN(n) ? '—' : Math.round(Number(n)));

  function todayLocalISO() {
    const d = new Date();
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, '0');
    const day = String(d.getDate()).padStart(2, '0');
    return `${y}-${m}-${day}`;
  }

  function isoDate(val) {
    if (!val) return todayLocalISO();
    if (/^\d{4}-\d{2}-\d{2}$/.test(val)) return val;
    const d = new Date(val);
    if (!isNaN(d)) {
      const y = d.getFullYear();
      const m = String(d.getMonth() + 1).padStart(2, '0');
      const day = String(d.getDate()).padStart(2, '0');
      return `${y}-${m}-${day}`;
    }
    return todayLocalISO();
  }

  function formatDateForChip(iso) {
    try {
      const [y, m, d] = iso.split('-').map(Number);
      const dt = new Date(y, m - 1, d);
      return dt.toLocaleDateString(undefined, {
        weekday: 'short',
        month: 'short',
        day: 'numeric',
      });
    } catch {
      return iso;
    }
  }

  function formatDateFull(iso) {
    try {
      const [y, m, d] = iso.split('-').map(Number);
      const dt = new Date(y, m - 1, d);
      return dt.toLocaleDateString(undefined, {
        weekday: 'long',
        month: 'long',
        day: 'numeric',
        year: 'numeric',
      });
    } catch {
      return iso;
    }
  }

  function getDateFromURL() {
    const u = new URL(window.location.href);
    const qd = u.searchParams.get('date');
    if (qd && /^\d{4}-\d{2}-\d{2}$/.test(qd)) return qd;
    return todayLocalISO();
  }

  function getCurrentDate() {
    if (CURRENT_DATE) return CURRENT_DATE;
    if (dateInput) {
      if (dateInput.tagName === 'INPUT' && dateInput.value) {
        return isoDate(dateInput.value);
      }
      if (dateInput.dataset && dateInput.dataset.isoDate) {
        return isoDate(dateInput.dataset.isoDate);
      }
    }
    return getDateFromURL();
  }

  function setDate(d) {
    const iso = isoDate(d);
    CURRENT_DATE = iso;

    if (dateInput) {
      if (dateInput.tagName === 'INPUT') {
        dateInput.value = iso;
      } else {
        dateInput.textContent = formatDateForChip(iso);
        if (dateInput.dataset) {
          dateInput.dataset.isoDate = iso;
        }
      }
    }

    // Always show a readable plan date to the user.
    const display = ensureDateDisplay();
    if (display) {
      display.textContent = formatDateFull(iso);
      if (display.dataset) {
        display.dataset.isoDate = iso;
      }
    }

    const next = location.pathname + '?date=' + encodeURIComponent(iso);
    if (location.href !== next) history.replaceState(null, '', next);
  }

  function normalizeSlot(s) {
    return String(s || '').trim().toLowerCase();
  }

  // Canonical slots we always want the AI to fill
  const CANONICAL_SLOTS = ['breakfast', 'lunch', 'dinner', 'snack'];

  // ---------- Normalizers for ingredients, instructions, reasons ----------

  function normalizeIngredients(raw) {
    if (!raw) return [];
    const out = [];
    const arr = Array.isArray(raw) ? raw : [raw];

    for (const it of arr) {
      if (it == null) continue;

      if (typeof it === 'string') {
        const text = it.trim();
        if (!text) continue;
        out.push({ name: text, amount: '', unit: '' });
        continue;
      }

      if (typeof it === 'object') {
        const name =
          it.name ||
          it.item ||
          it.title ||
          it.text ||
          it.ingredient ||
          '';
        const amount =
          it.amount != null && it.amount !== ''
            ? String(it.amount)
            : it.qty != null && it.qty !== ''
            ? String(it.qty)
            : it.quantity != null && it.quantity !== ''
            ? String(it.quantity)
            : '';
        const unit = it.unit || it.units || '';

        if (!name && !amount && !unit) continue;
        out.push({
          name: name || 'Item',
          amount,
          unit,
        });
        continue;
      }

      // Numbers / other primitives
      out.push({ name: String(it), amount: '', unit: '' });
    }

    return out;
  }

  function normalizeInstructions(raw) {
    if (!raw) return [];
    let arr = [];

    if (Array.isArray(raw)) {
      arr = raw;
    } else if (typeof raw === 'string') {
      // Split on newlines if it came as a blob
      arr = raw.split(/\r?\n+/);
    } else {
      arr = [String(raw)];
    }

    return arr
      .map((s) => String(s || '').trim())
      .filter((s) => s.length > 0);
  }

  function getReasonForSlot(slot) {
    const norm = normalizeSlot(slot);
    if (AI_REASONS[norm]) return AI_REASONS[norm];

    const meals = (CURRENT_PLAN && CURRENT_PLAN.meals) || [];
    for (const m of meals) {
      if (normalizeSlot(m.meal_type) !== norm) continue;
      const meta = m.meta || {};

      const fromMeta =
        meta.ai_reason ||
        meta.reason ||
        (meta.ai_idea && meta.ai_idea.reason) ||
        null;
      if (fromMeta) return String(fromMeta);

      const fromTopLevel =
        (m.ai_idea && m.ai_idea.reason) || m.reason || null;
      if (fromTopLevel) return String(fromTopLevel);

      const macroParts = [];
      if (m.protein_g != null) macroParts.push(`${fmt(m.protein_g)} g protein`);
      if (m.carbs_g != null) macroParts.push(`${fmt(m.carbs_g)} g carbs`);
      if (m.fat_g != null) macroParts.push(`${fmt(m.fat_g)} g fat`);
      const title = m.title || `${norm} meal`;
      return `${title} was selected to fit your ${norm} target${
        macroParts.length ? ` (${macroParts.join(', ')})` : ''
      } and the diet preference from your profile.`;
    }

    return null;
  }

  // Build per-meal targets for the LLM, always including all 4 slots.
  function buildMealTargetsFromPlan(plan) {
    const bySlot = {};
    (plan?.meals || []).forEach((m) => {
      const slot = normalizeSlot(m.meal_type);
      if (slot) bySlot[slot] = m;
    });

    const totals = plan?.totals || {};
    const totalKcal = Number(totals.kcal || 0);
    const totalP = Number(totals.protein_g || 0);
    const totalC = Number(totals.carbs_g || 0);
    const totalF = Number(totals.fat_g || 0);

    const divisor = CANONICAL_SLOTS.length || 1;

    // Fallbacks if totals are missing or zero
    const fallbackKcal =
      totalKcal > 0 ? totalKcal / divisor : 600; // rough per-meal default
    const fallbackP = totalP > 0 ? totalP / divisor : 30;
    const fallbackC = totalC > 0 ? totalC / divisor : 70;
    const fallbackF = totalF > 0 ? totalF / divisor : 20;

    const out = [];

    CANONICAL_SLOTS.forEach((slot) => {
      const m = bySlot[slot] || {};
      out.push({
        slot,
        kcal:
          m.kcal != null && m.kcal !== ''
            ? Number(m.kcal)
            : Number(fallbackKcal),
        protein_g:
          m.protein_g != null && m.protein_g !== ''
            ? Number(m.protein_g)
            : Number(fallbackP),
        carbs_g:
          m.carbs_g != null && m.carbs_g !== ''
            ? Number(m.carbs_g)
            : Number(fallbackC),
        fat_g:
          m.fat_g != null && m.fat_g !== ''
            ? Number(m.fat_g)
            : Number(fallbackF),
      });
    });

    return out;
  }

  function buildWeekRange(startIso, days) {
    const out = [];
    const base = new Date(startIso + 'T00:00:00');
    for (let i = 0; i < days; i++) {
      const d = new Date(base);
      d.setDate(base.getDate() + i);
      const y = d.getFullYear();
      const m = String(d.getMonth() + 1).padStart(2, '0');
      const day = String(d.getDate()).padStart(2, '0');
      out.push(`${y}-${m}-${day}`);
    }
    return out;
  }

  const WEEKLY_JOB_STORAGE_KEY = 'glycofy.weeklyPlanningJob';

  async function pollWeeklyJob(jobId) {
    while (true) {
      const response = await fetch(`/v1/llm/recommend/weekly/jobs/${jobId}`, {
        credentials: 'include',
      });
      if (!response.ok) {
        throw new Error(`Weekly planning status failed: ${response.status}`);
      }
      const job = await response.json();
      if (job.status === 'completed') return job.result;
      if (job.status === 'failed') {
        throw new Error(job.error || 'Weekly AI planning failed.');
      }
      updateBusyProgress(job.message || null);
      await new Promise((resolve) => setTimeout(resolve, 1500));
    }
  }

  async function startWeeklyJob(payload, context) {
    const response = await fetch('/v1/llm/recommend/weekly/jobs', {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!response.ok) {
      const body = await response.json().catch(() => null);
      throw new Error(body?.detail || `Weekly AI planning failed: ${response.status}`);
    }
    const job = await response.json();
    sessionStorage.setItem(
      WEEKLY_JOB_STORAGE_KEY,
      JSON.stringify({ jobId: job.job_id, ...context })
    );
    const result = await pollWeeklyJob(job.job_id);
    sessionStorage.removeItem(WEEKLY_JOB_STORAGE_KEY);
    return result;
  }

  // ----- API calls -----
  async function loadPlan(d) {
    const r = await fetch(`/v1/plan/${d}`, { credentials: 'include' });
    if (r.status === 404) return null;
    if (!r.ok) throw new Error(`Failed to load plan: ${r.status}`);
    return r.json();
  }

  async function createPlan(d, engine = 'heuristic') {
    const r = await fetch(
      `/v1/plan/${d}?engine=${encodeURIComponent(engine)}&replace=false`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({}),
      }
    );
    if (!r.ok) throw new Error(`Create failed: ${r.status}`);
    return r.json();
  }

  async function ensurePlan(d) {
    let plan = await loadPlan(d);
    if (!plan) plan = await createPlan(d, 'heuristic');
    return plan;
  }

  async function lockToggleAPI(d, lock) {
    const r = await fetch(`/v1/plan/${d}/lock?lock=${lock ? 'true' : 'false'}`, {
      method: 'POST',
      credentials: 'include',
    });
    if (!r.ok) throw new Error(`Lock toggle failed: ${r.status}`);
    return r.json();
  }

  async function regenerate(d, engine = 'heuristic') {
    const r = await fetch(
      `/v1/plan/${d}/regenerate?engine=${encodeURIComponent(engine)}`,
      {
        method: 'POST',
        credentials: 'include',
      }
    );
    if (!r.ok) throw new Error(`Regenerate failed: ${r.status}`);
    return r.json();
  }

  // ----- LLM calls -----
  async function llmRecommendAll(d, plan) {
    // NEW: always include all 4 canonical meals for this date
    const meals = buildMealTargetsFromPlan(plan);
    const payload = { date: d, totals: plan?.totals || null, meals };
    const r = await fetch('/v1/llm/recommend', {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!r.ok) throw new Error(`LLM recommend failed: ${r.status}`);
    const rec = await r.json();
    console.log('LLM recommend response', d, rec);
    return rec;
  }

  async function llmRecommendOne(d, meal) {
    const tgt = {
      slot: meal.meal_type || 'snack',
      kcal: Number(meal.kcal || 0),
      protein_g: Number(meal.protein_g || 0),
      carbs_g: Number(meal.carbs_g || 0),
      fat_g: Number(meal.fat_g || 0),
    };
    const payload = { date: d, totals: CURRENT_PLAN?.totals || null, meals: [tgt] };
    const r = await fetch('/v1/llm/recommend', {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!r.ok) throw new Error(`LLM recommend (one) failed: ${r.status}`);
    const rec = await r.json();
    console.log('LLM recommend (one) response', d, rec);
    return rec;
  }

  // ---------- Freeform extraction helpers ----------
  function extractFreeformForSlot(slotRaw, source) {
    const slot = normalizeSlot(slotRaw || source?.slot);
    if (!slot || !source) return;

    const meta = source.meta || {};
    const ai = meta.ai_idea || source.ai_idea || null;

    let idea = null;
    let desc = null;
    let approx = null;
    let ing = null;
    let instr = null;

    if (ai) {
      idea = ai.title || ai.idea || ai.name || null;
      desc = ai.description || null;
      approx = ai.approx_macros || ai.macros || null;
      ing = ai.ingredients || ai.items || null;
      instr = ai.instructions || ai.steps || null;
    }

    // Fallbacks in case future responses move fields up a level
    if (!idea && source.idea) idea = source.idea;
    if (!desc && source.description) desc = source.description;
    if (!approx && (source.approx_macros || source.macros)) {
      approx = source.approx_macros || source.macros;
    }
    if (!ing && (source.ingredients || source.items)) {
      ing = source.ingredients || source.items;
    }
    if (!instr && (source.instructions || source.steps)) {
      instr = source.instructions || source.steps;
    }

    const normIngredients = normalizeIngredients(ing);
    const normInstructions = normalizeInstructions(instr);

    if (!idea && !normIngredients.length && !normInstructions.length) return;

    AI_FREEFORM[slot] = {
      idea,
      description: desc,
      approx_macros: approx,
      ingredients: normIngredients,
      instructions: normInstructions,
    };
  }

  function normalizeItemsArray(rec) {
    if (!rec) return [];
    if (Array.isArray(rec.items)) return rec.items;
    if (rec.items && typeof rec.items === 'object') {
      return Object.entries(rec.items).map(([slot, val]) => ({
        slot,
        ...(val || {}),
      }));
    }
    return [];
  }

  function buildAiIdeaPayload(slot) {
    const ff = AI_FREEFORM[slot];
    if (!ff) return null;

    const approx = ff.approx_macros || ff.macros || null;
    const ingredients = Array.isArray(ff.ingredients) ? ff.ingredients : [];

    const ingPayload = ingredients.map((it) => ({
      name: it.name || 'Item',
      amount: it.amount || it.qty || '',
    }));

    const payload = {
      ingredients: ingPayload,
    };
    if (ff.idea) payload.title = ff.idea;
    if (ff.description) payload.description = ff.description;
    if (approx) payload.approx_macros = approx;
    if (Array.isArray(ff.instructions) && ff.instructions.length) {
      payload.instructions = ff.instructions.slice();
    }

    return payload;
  }

  // ----- Apply helpers (recipes + freeform AI) -----
  function toCleanItems(items, validSlots) {
    const seen = new Set();
    const out = [];
    for (const it of items || []) {
      const rawSlot = it && (it.slot || it.meal_type || '');
      const slot = normalizeSlot(rawSlot);
      if (!slot) continue;
      if (validSlots && !validSlots.has(slot)) continue;
      if (seen.has(slot)) continue;

      const payload = { slot };

      const recipeId =
        it.recipe_id ||
        (it.recipe && it.recipe.id != null ? it.recipe.id : null);
      if (recipeId != null) payload.recipe_id = recipeId;

      // Prefer ai_idea from the LLM item; fallback to overlay if missing.
      let aiIdea = null;
      if (it.ai_idea) {
        aiIdea = it.ai_idea;
      } else {
        aiIdea = buildAiIdeaPayload(slot);
      }
      if (aiIdea) payload.ai_idea = aiIdea;

      if (!payload.recipe_id && !payload.ai_idea) continue;

      seen.add(slot);
      out.push(payload);
    }
    return out;
  }

  async function applyRecommendationsOnce(d, items) {
    const r = await fetch(`/v1/plan/${d}/apply_recommendations`, {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ items }),
    });
    return r;
  }

  async function applyRecommendationsRobust(d, items) {
    if (!items || !items.length) {
      return CURRENT_PLAN || (await ensurePlan(d));
    }

    const freshPlan = await ensurePlan(d);

    // NEW: allow all canonical slots + whatever exists in the plan.
    const validSlots = new Set(CANONICAL_SLOTS);
    (freshPlan?.meals || []).forEach((m) => {
      const s = normalizeSlot(m?.meal_type);
      if (s) validSlots.add(s);
    });

    const clean = toCleanItems(items, validSlots);
    if (clean.length === 0) return freshPlan;

    let r = await applyRecommendationsOnce(d, clean);
    if (r.status === 404) {
      await ensurePlan(d);
      r = await applyRecommendationsOnce(d, clean);
    }
    if (!r.ok) {
      const txt = await r.text().catch(() => '');
      throw new Error(
        `Apply recommendations failed: ${r.status} ${txt || ''}`.trim()
      );
    }

    // IMPORTANT: re-load from backend so we get fully-normalized meals
    const updated = await loadPlan(d);
    return updated || (await r.json());
  }

  async function runAIForDate(d, updateUI) {
    const iso = isoDate(d);
    // Always ensure plan & unlock for that day
    try {
      await ensurePlan(iso);
      await lockToggleAPI(iso, false);
    } catch (e) {
      console.warn('runAIForDate: ensure/lock failed', iso, e);
    }

    const plan = await ensurePlan(iso);
    const rec = await llmRecommendAll(iso, plan);
    const itemsArr = normalizeItemsArray(rec);
    const applicableSlots = new Set(
      toCleanItems(itemsArr, new Set(CANONICAL_SLOTS)).map((item) =>
        normalizeSlot(item.slot)
      )
    );
    const missingSlots = CANONICAL_SLOTS.filter(
      (slot) => !applicableSlots.has(slot)
    );
    if (missingSlots.length) {
      throw new Error(
        `AI returned an incomplete day (missing: ${missingSlots.join(
          ', '
        )}). The existing plan was not changed.`
      );
    }

    if (updateUI) {
      AI_REASONS = {};
      AI_FREEFORM = {};
      itemsArr.forEach((it) => {
        const slot = normalizeSlot(it.slot);
        if (!slot) return;
        if (it.reason) AI_REASONS[slot] = String(it.reason);
        extractFreeformForSlot(slot, it);
      });
    }

    const applied = await applyRecommendationsRobust(iso, itemsArr);
    if (updateUI) {
      CURRENT_PLAN = applied;
      renderPlan(CURRENT_PLAN);
    }
  }

  // ----- Renderers -----
  function renderTotals(plan) {
    const t = plan?.totals || {};
    if (tKcal) tKcal.textContent = fmt(t.kcal);
    if (tP) tP.textContent = fmt(t.protein_g);
    if (tC) tC.textContent = fmt(t.carbs_g);
    if (tF) tF.textContent = fmt(t.fat_g);
    if (tSrc) tSrc.textContent = plan?.source || 'heuristic';
    if (tLock) tLock.textContent = plan?.locked ? 'Yes' : 'No';
  }

  function renderMeals(plan) {
    const meals = plan?.meals || [];
    const sorted = meals
      .slice()
      .sort((a, b) => (a.order_index || 0) - (b.order_index || 0));

    if (NEW_LAYOUT) {
      const bySlot = {};
      for (const m of sorted) {
        const slot = normalizeSlot(m.meal_type);
        if (slot) bySlot[slot] = m;
      }

      document.querySelectorAll('.meal-card[data-slot]').forEach((card) => {
        const slot = normalizeSlot(card.getAttribute('data-slot'));
        const base = bySlot[slot] || {};
        const ff = AI_FREEFORM[slot];

        const macrosEl = card.querySelector(
          '.meal-macros, .meal__macros, .meal-meta, .meal__meta'
        );
        const bodyEl = card.querySelector(
          '.meal-body, .meal__body, .meal-items, .meal__items'
        );
        const badgeEl = card.querySelector('.meal-badge, .meal__badge, .chip');
        const titleEl = card.querySelector('.meal-title, .meal__title, h3');
        const slotLabelEl = card.querySelector('.meal-slot-label');

        // slot label (e.g., Breakfast, Lunch)
        if (slotLabelEl) {
          slotLabelEl.textContent =
            slot.charAt(0).toUpperCase() + slot.slice(1);
        }

        // title (meal name)
        if (titleEl) {
          if (ff && ff.idea) titleEl.textContent = ff.idea;
          else if (base && base.title) titleEl.textContent = base.title;
          else if (base && base.meal_type)
            titleEl.textContent = base.meal_type;
        }

        // badge: AI pick if any LLM reason or freeform overlay
        if (badgeEl) {
          if (ff || getReasonForSlot(slot)) {
            badgeEl.textContent = 'AI pick';
            badgeEl.classList.add('meal-badge--ai');
            badgeEl.style.display = 'inline-flex';
          } else {
            badgeEl.textContent = '';
            badgeEl.style.display = 'none';
          }
        }

        // macros
        if (macrosEl) {
          if (ff && ff.approx_macros) {
            const a = ff.approx_macros;
            macrosEl.textContent = `${fmt(a.kcal)} kcal · P ${fmt(
              a.protein_g
            )} g · C ${fmt(a.carbs_g)} g · F ${fmt(a.fat_g)} g`;
          } else if (base && base.kcal != null) {
            macrosEl.textContent = `${fmt(base.kcal)} kcal · P ${fmt(
              base.protein_g
            )} g · C ${fmt(base.carbs_g)} g · F ${fmt(base.fat_g)} g`;
          } else {
            macrosEl.textContent = '';
          }
        }

        // ingredients + instructions
        if (bodyEl) {
          bodyEl.innerHTML = '';

          const srcIng =
          (ff &&
            Array.isArray(ff.ingredients) &&
            ff.ingredients.length &&
            ff.ingredients) ||

          (base &&
            Array.isArray(base.ingredients) &&
            base.ingredients.length &&
            base.ingredients) ||

          // 🔥 NEW: fallback to meta.ai_idea ingredients
          (base &&
            base.meta &&
            base.meta.ai_idea &&
            Array.isArray(base.meta.ai_idea.ingredients) &&
            base.meta.ai_idea.ingredients.length &&
            base.meta.ai_idea.ingredients) ||

          null;

          const ingList = normalizeIngredients(srcIng);

          if (ingList.length) {
            const ul = document.createElement('ul');
            ul.className = 'meal-ingredients';
            ingList.forEach((it) => {
              const li = document.createElement('li');

              const nameSpan = document.createElement('span');
              nameSpan.className = 'meal-ingredient-name';
              nameSpan.textContent = it.name || 'Item';

              const amt =
                it.amount ||
                (it.qty != null && it.qty !== '' ? String(it.qty) : '');
              const unit = it.unit || '';
              const displayAmt = amt || unit ? `${amt} ${unit}`.trim() : '';

              if (displayAmt) {
                const amtSpan = document.createElement('span');
                amtSpan.className = 'meal-ingredient-amount';
                amtSpan.textContent = displayAmt;
                li.appendChild(nameSpan);
                li.appendChild(amtSpan);
              } else {
                li.appendChild(nameSpan);
              }
              ul.appendChild(li);
            });
            bodyEl.appendChild(ul);
          } else {
            const p = document.createElement('p');
            p.className = 'meal-empty';
            p.textContent = 'No meal planned yet.';
            bodyEl.appendChild(p);
          }

          // Instructions block (from AI or base)
          const instrSrc =
            (ff &&
              Array.isArray(ff.instructions) &&
              ff.instructions.length &&
              ff.instructions) ||
            (base &&
              Array.isArray(base.instructions) &&
              base.instructions.length &&
              base.instructions) ||
            null;

          const instrList = normalizeInstructions(instrSrc);
          if (instrList.length) {
            const details = document.createElement('details');
            details.className = 'meal-instructions';

            const summary = document.createElement('summary');
            summary.textContent = 'Instructions';
            details.appendChild(summary);

            const ol = document.createElement('ol');
            instrList.forEach((step) => {
              const li = document.createElement('li');
              li.textContent = step;
              ol.appendChild(li);
            });
            details.appendChild(ol);

            bodyEl.appendChild(details);
          }
        }
      });
    } else {
      // Legacy layout
      if (!mealsRoot) return;
      mealsRoot.innerHTML = '';

      for (const m of sorted) {
        const slot = normalizeSlot(m.meal_type);
        const ff = AI_FREEFORM[slot];

        let view = Object.assign({}, m);
        if (ff) {
          view.title = ff.idea || m.title || m.meal_type;
          const a = ff.approx_macros || {};
          if (a.kcal != null) view.kcal = a.kcal;
          if (a.protein_g != null) view.protein_g = a.protein_g;
          if (a.carbs_g != null) view.carbs_g = a.carbs_g;
          if (a.fat_g != null) view.fat_g = a.fat_g;
          view.ingredients = ff.ingredients || [];
          view.instructions = ff.instructions || [];
        }

        const ingList = normalizeIngredients(view.ingredients);
        const instrList = normalizeInstructions(view.instructions);

        const card = document.createElement('div');
        card.className = 'meal';
        card.innerHTML = `
          <div class="meal__head">
            <div class="meal-slot-label">${
              slot.charAt(0).toUpperCase() + slot.slice(1)
            }</div>
            <h3 class="meal__title">${escapeHtml(view.title || 'Meal')}</h3>
            <div class="meal__actions">
              <button class="btn btn--ghost" data-ai-swap="${slot}">Swap (AI)</button>
              <button class="btn btn--ghost" data-ai-why="${slot}">Why?</button>
            </div>
          </div>
          <div class="meal__macros">
            <span>${fmt(view.kcal)} kcal</span>
            <span>P ${fmt(view.protein_g)} g</span>
            <span>C ${fmt(view.carbs_g)} g</span>
            <span>F ${fmt(view.fat_g)} g</span>
          </div>
          <ul class="items">
            ${ingList
              .map((it) => {
                const qty = it.amount || '';
                const unit = it.unit || '';
                const suffix =
                  qty || unit
                    ? ` <span class="muted">(${escapeHtml(qty)} ${escapeHtml(unit)})</span>`
                    : '';
                return `<li><span>${escapeHtml(it.name || 'Item')}${suffix}</span></li>`;
              })
              .join('')}
          </ul>
          ${
            instrList.length
              ? `<details class="meal-instructions">
                   <summary>Instructions</summary>
                   <ol>
                     ${instrList
                       .map((step) => `<li>${escapeHtml(step)}</li>`)
                       .join('')}
                   </ol>
                 </details>`
              : ''
          }
        `;
        mealsRoot.appendChild(card);
      }
    }
  }

  function renderPlan(plan) {
    CURRENT_PLAN = plan;
    const d = plan?.date || getCurrentDate();

    if (dlTxt) dlTxt.href = `/v1/plan/${d}/grocery.txt`;
    if (dlCsv) dlCsv.href = `/v1/plan/${d}/grocery.csv`;
    if (groceryListLink) groceryListLink.href = `/ui/grocery.html?start=${encodeURIComponent(d)}`;

    if (!plan) {
      if (mealsRoot && !NEW_LAYOUT) mealsRoot.innerHTML = '';
      if (emptyEl) emptyEl.style.display = '';
      if (totalsEl) totalsEl.style.display = 'none';
      return;
    }

    if (emptyEl) emptyEl.style.display = 'none';
    if (totalsEl) totalsEl.style.display = '';
    renderTotals(plan);
    renderMeals(plan);
  }

  // ----- AI banner -----
  function injectAIBanner() {
    const hdr = document.querySelector('.panel__header');
    if (!hdr || hdr.parentNode.querySelector('[data-ai-banner]')) return;
    const note = document.createElement('div');
    note.setAttribute('data-ai-banner', '1');
    note.className = 'muted';
    note.style.marginTop = '6px';
    note.textContent =
      '🤖 The AI uses your Diet and safety exclusions from Profile.';
    hdr.parentNode.insertBefore(note, hdr.nextSibling);
  }

  // ----- header controls -----
  if (prevBtn) {
    prevBtn.addEventListener('click', () => {
      const d0 = getCurrentDate();
      const d = new Date(d0 + 'T00:00:00');
      d.setDate(d.getDate() - 1);
      const iso = isoDate(d);
      setDate(iso);
      boot();
    });
  }

  if (nextBtn) {
    nextBtn.addEventListener('click', () => {
      const d0 = getCurrentDate();
      const d = new Date(d0 + 'T00:00:00');
      d.setDate(d.getDate() + 1);
      const iso = isoDate(d);
      setDate(iso);
      boot();
    });
  }

  if (lockBtn) {
    lockBtn.addEventListener('click', async () => {
      const d = getCurrentDate();
      setDate(d);
      const next = !(CURRENT_PLAN?.locked);
      const plan = await lockToggleAPI(d, next);
      renderPlan(plan);
      flash(next ? 'Plan locked.' : 'Plan unlocked.');
    });
  }

  if (regenBtn) {
    regenBtn.addEventListener('click', async () => {
      const d = getCurrentDate();
      setDate(d);
      const plan = await regenerate(d, 'heuristic');
      AI_REASONS = {};
      AI_FREEFORM = {};
      renderPlan(plan);
      flash('Plan regenerated (heuristic).');
    });
  }

  if (createBtn) {
    createBtn.addEventListener('click', async () => {
      const d = getCurrentDate();
      setDate(d);
      const plan = await createPlan(d, 'heuristic');
      AI_REASONS = {};
      AI_FREEFORM = {};
      renderPlan(plan);
      flash('Plan created.');
    });
  }

  // ----- "Today (AI)" -----
  (function ensureAIButton() {
    const existing = $('plan-ai-all') || $('ai_apply_btn');
    const btn = existing || document.createElement('button');

    if (!existing && regenBtn) {
      btn.id = 'ai_apply_btn';
      btn.className = 'btn';
      regenBtn.insertAdjacentElement('afterend', btn);
    }

    if (!btn) return;

    btn.textContent = 'Today (AI)';
    btn.title = "Use AI to update today's meals only";

    if (btn.__aiBound) return;
    btn.__aiBound = true;

    btn.addEventListener('click', async () => {
      const d = getCurrentDate();
      setDate(d);
      setBusy(true, 'Asking AI for today…');
      try {
        await runAIForDate(d, true);
        flash('AI suggestions applied for today.');
      } catch (err) {
        console.error(err);
        flash(
          String(err.message || 'Failed to apply AI suggestions.'),
          'error'
        );
      } finally {
        setBusy(false);
      }
    });
  })();

  // ----- Week (AI) — uses /v1/llm/recommend/weekly/apply_payload ONLY -----
  if (weekBtn && !weekBtn.__weekBound) {
    weekBtn.__weekBound = true;
    weekBtn.addEventListener('click', async () => {
      const start = isoDate(getCurrentDate());
      setDate(start);

      // Build the 7-day range and a nice label like "Sun Nov 17 – Sat Nov 23"
      const days = buildWeekRange(start, 7);
      const prettyStart = formatDateForChip(days[0]);
      const prettyEnd = formatDateForChip(days[days.length - 1]);

      setBusy(
        true,
        `Planning your week with AI (${prettyStart} – ${prettyEnd})…`
      );
      try {
        // 1) Ensure each day's plan exists, unlock it, and build weekly payload
        if (busyMsg) {
          busyMsg.textContent = `Preparing your AI week plan (${prettyStart} – ${prettyEnd})…`;
        }
        // Prepare all seven days concurrently. Previously this made up to 21
        // serial API requests (ensure, unlock, ensure again) before AI work
        // even began. Reuse the loaded plan and unlock only when necessary.
        const preparedDays = await Promise.all(
          days.map(async (d) => {
            const iso = isoDate(d);
            try {
              let plan = await ensurePlan(iso);
              if (plan?.locked) plan = await lockToggleAPI(iso, false);
              const meals = buildMealTargetsFromPlan(plan);
              if (!meals.length) return null;
              return { date: iso, totals: plan?.totals || null, meals };
            } catch (e) {
              console.warn('Week(AI): prepare failed for', iso, e);
              return null;
            }
          })
        );
        const weeklyDaysPayload = preparedDays.filter(Boolean);

        if (!weeklyDaysPayload.length) {
          throw new Error('No meals found to plan for the week.');
        }

        // 2) Start a resumable background job and poll lightweight status.
        const data = await startWeeklyJob(
          { days: weeklyDaysPayload },
          { startIso: start, prettyStart, prettyEnd }
        );
        console.log('LLM weekly response', data);

        const outDays = Array.isArray(data.days) ? data.days : [];
        const startIso = isoDate(start);

        // 3) Use LLM metadata for overlays (AI badges / "Why") on the start day only
        AI_REASONS = {};
        AI_FREEFORM = {};
        const todayData =
          outDays.find((d) => isoDate(d.date || '') === startIso) || null;
        if (todayData) {
          const itemsArr = normalizeItemsArray(todayData);
          itemsArr.forEach((it) => {
            const slot = normalizeSlot(it.slot);
            if (!slot) return;
            if (it.reason) AI_REASONS[slot] = String(it.reason);
            extractFreeformForSlot(slot, it);
          });
        }

        // 4) Reload the start day's plan from the backend and render with overlays
        const refreshed = await loadPlan(startIso);
        if (refreshed) {
          setDate(startIso);
          renderPlan(refreshed);
        }

        flash(
          `AI suggestions applied for this week (${prettyStart} – ${prettyEnd}).`
        );
      } catch (err) {
        console.error(err);
        flash(
          String(err.message || 'Failed to plan the week with AI.'),
          'error'
        );
      } finally {
        setBusy(false);
      }
    });
  }

  // A weekly job continues on the server if the page is refreshed or the user
  // navigates away. Reconnect to it when this planner is opened again.
  (async function resumeWeeklyJob() {
    let saved = null;
    try {
      saved = JSON.parse(sessionStorage.getItem(WEEKLY_JOB_STORAGE_KEY) || 'null');
    } catch (_) {
      sessionStorage.removeItem(WEEKLY_JOB_STORAGE_KEY);
    }
    if (!saved?.jobId) return;
    setBusy(true, 'Reconnecting to your AI week…');
    try {
      await pollWeeklyJob(saved.jobId);
      sessionStorage.removeItem(WEEKLY_JOB_STORAGE_KEY);
      const refreshed = await loadPlan(saved.startIso || getCurrentDate());
      if (refreshed) renderPlan(refreshed);
      flash(
        `AI suggestions applied for this week (${saved.prettyStart || 'start'} – ${saved.prettyEnd || 'end'}).`
      );
    } catch (err) {
      sessionStorage.removeItem(WEEKLY_JOB_STORAGE_KEY);
      flash(String(err.message || 'Failed to resume weekly planning.'), 'error');
    } finally {
      setBusy(false);
    }
  })();

  // ----- per-meal Swap / Why -----
  if (mealsRoot) {
    mealsRoot.addEventListener('click', async (e) => {
      const target = e.target.closest(
        '[data-ai-swap],[data-ai-why],[data-action="swap-ai"],[data-action="why"]'
      );
      if (!target) return;

      const isSwap =
        target.hasAttribute('data-ai-swap') ||
        target.getAttribute('data-action') === 'swap-ai';
      const isWhy =
        target.hasAttribute('data-ai-why') ||
        target.getAttribute('data-action') === 'why';

      let slotAttr =
        target.getAttribute('data-ai-swap') ||
        target.getAttribute('data-ai-why') ||
        target.getAttribute('data-slot') ||
        (target.closest('[data-slot]') &&
          target.closest('[data-slot]').getAttribute('data-slot'));

      const slot = normalizeSlot(slotAttr);
      if (!slot) return;

      if (isWhy) {
        const reason = getReasonForSlot(slot) || 'No reason available.';
        alert(`Why this pick (${slot}):\n\n${reason}`);
        return;
      }

      if (isSwap) {
        const d = getCurrentDate();
        setDate(d);
        const meal = (CURRENT_PLAN?.meals || []).find(
          (mm) => normalizeSlot(mm.meal_type) === slot
        );
        if (!meal) return;

        try {
          if (CURRENT_PLAN?.locked) await lockToggleAPI(d, false);
          await ensurePlan(d);
          const rec = await llmRecommendOne(d, meal);

          const itemsArr = normalizeItemsArray(rec);
          let item = itemsArr[0] || null;

          // If no structured item but we have an ai_idea embedded, fake one
          if (!item && rec.items && typeof rec.items === 'object') {
            const firstKey = Object.keys(rec.items)[0];
            if (firstKey) {
              item = { slot: firstKey, ...(rec.items[firstKey] || {}) };
            }
          }

          if (!item) {
            flash(`No AI alternative found for ${slot}.`, 'error');
            return;
          }

          const normSlot = normalizeSlot(item.slot || slot);
          if (item.reason) AI_REASONS[normSlot] = String(item.reason);

          extractFreeformForSlot(normSlot, item);

          const applied = await applyRecommendationsRobust(d, [item]);
          CURRENT_PLAN = applied;
          renderPlan(CURRENT_PLAN);
          flash(`Swapped ${normSlot} with AI suggestion.`);
        } catch (err) {
          console.error(err);
          flash(String(err.message || 'Failed to swap.'), 'error');
        }
      }
    });
  }

  // ----- boot -----
  async function boot() {
    await ensureAuth();
    injectAIBanner();

    const d = getCurrentDate();
    setDate(d);

    try {
      const me = await getUser();
      if (!me) throw new Error('unauth');
    } catch {
      return redirectToReturn && redirectToReturn('/ui/login.html');
    }

    let plan = await loadPlan(d);
    if (!plan) plan = await createPlan(d, 'heuristic');
    AI_REASONS = {};
    AI_FREEFORM = {};
    renderPlan(plan);
  }

  boot().catch((err) => {
    console.error(err);
    flash('Failed to initialize Plan page.', 'error');
  });
})();
