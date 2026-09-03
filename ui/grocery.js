(function () {
  const glyco = window.__glyco || {};
  if (!glyco.fetchJSON) return;
  const $ = (id) => document.getElementById(id);
  const CATEGORIES = ["Produce", "Meat & Seafood", "Dairy & Eggs", "Grains & Bakery", "Other", "Pantry"];
  let data = null;
  let edits = {};
  let approval = null;
  let editingItem = null;

  function localISO(date) { return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 10); }
  function monday(date) { const copy = new Date(date); copy.setDate(copy.getDate() - ((copy.getDay() + 6) % 7)); return copy; }
  function addDays(iso, count) { const date = new Date(`${iso}T12:00:00`); date.setDate(date.getDate() + count); return localISO(date); }
  function storageKey() { return `glyco_grocery:${$("start_date").value}:${$("end_date").value}`; }
  function loadEdits() { try { edits = JSON.parse(localStorage.getItem(storageKey()) || "{}"); } catch { edits = {}; } }
  function saveEdits(trackChange = true) { localStorage.setItem(storageKey(), JSON.stringify(edits)); if (trackChange) markChanged(); }
  function rangeQuery() { return `start=${encodeURIComponent($("start_date").value)}&end=${encodeURIComponent($("end_date").value)}`; }
  function approvalDate(value) { return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }).format(new Date(value)); }

  function renderApproval() {
    const card = $("approval_title").closest(".approval-card");
    const status = $("approval_status");
    const button = $("approve_btn");
    card.classList.toggle("is-approved", Boolean(approval && !approval.stale));
    card.classList.toggle("is-stale", Boolean(approval?.stale));
    if (data?.missing_dates?.length) {
      status.textContent = "Complete the missing meal-plan days before approving this shopping list.";
      button.textContent = "Plan all days first";
      button.disabled = true;
    } else if (approval?.stale) {
      button.disabled = false;
      status.textContent = "Your meal plan changed after approval. Review the updated ingredients and approve again.";
      button.textContent = "Review and reapprove";
    } else if (approval) {
      button.disabled = false;
      status.textContent = `Approved ${approvalDate(approval.approved_at)} for ${approval.servings} serving${approval.servings === 1 ? "" : "s"}. Package choices and pantry status are saved in this snapshot.`;
      button.textContent = "Approved ✓";
    } else {
      button.disabled = false;
      status.textContent = "Confirm every day is planned, adjust servings, package choices, and what you already have.";
      button.textContent = "Approve shopping list";
    }
  }
  function markChanged() { if (approval && !approval.stale) { approval = { ...approval, stale: true }; renderApproval(); } }
  function stateFor(item) {
    const preference = item.preference || {};
    const existing = edits[item.id] || {};
    edits[item.id] = {
      done: false,
      pantry: Boolean(preference.in_pantry),
      quantity: item.quantity,
      unit: item.unit,
      preferredBrand: preference.preferred_brand || "",
      packageQuantity: preference.package_quantity || item.package?.package_size || null,
      packageUnit: preference.package_unit || item.package?.package_unit || item.unit || "",
      ...existing,
    };
    if (existing.packageQuantity === undefined) edits[item.id].packageQuantity = preference.package_quantity || item.package?.package_size || null;
    if (existing.packageUnit === undefined) edits[item.id].packageUnit = preference.package_unit || item.package?.package_unit || item.unit || "";
    if (existing.preferredBrand === undefined) edits[item.id].preferredBrand = preference.preferred_brand || "";
    return edits[item.id];
  }
  function scaledQuantity(item) {
    const state = stateFor(item);
    return state.quantity == null ? "" : Math.round(Number(state.quantity) * Number($("servings").value || 1) * 100) / 100;
  }
  function packagePlan(item) {
    const state = stateFor(item);
    const needed = Number(scaledQuantity(item));
    const size = Number(state.packageQuantity);
    if (!needed || !size || state.packageUnit !== state.unit) return null;
    const count = Math.ceil(needed / size);
    const purchase = Math.round(count * size * 100) / 100;
    return { count, size, unit: state.packageUnit, purchase, remainder: Math.round(Math.max(0, purchase - needed) * 100) / 100 };
  }
  function unitText(amount, unit) {
    if (Number(amount) === 1 || ["oz", "g", "kg", "lb", "tsp", "tbsp"].includes(unit)) return unit;
    return { cup: "cups", item: "items", piece: "pieces", can: "cans" }[unit] || unit;
  }
  function packageText(item) {
    const state = stateFor(item);
    const plan = packagePlan(item);
    if (!plan) return "Set a package size for purchase guidance";
    const brand = state.preferredBrand ? `${state.preferredBrand} · ` : "";
    const leftover = plan.remainder > 0 ? ` · about ${plan.remainder} ${unitText(plan.remainder, plan.unit)} left` : " · exact amount";
    return `${brand}Buy ${plan.count} × ${plan.size} ${unitText(plan.size, plan.unit)}${leftover}`;
  }
  function escapeCsv(value) { return `"${String(value ?? "").replace(/"/g, '""')}"`; }
  function visibleItems() { return (data?.items || []).filter((item) => !stateFor(item).pantry); }
  function listText() {
    const grouped = new Map();
    visibleItems().forEach((item) => {
      if (!grouped.has(item.category)) grouped.set(item.category, []);
      grouped.get(item.category).push(`- ${item.name} — need ${scaledQuantity(item)} ${stateFor(item).unit || ""}; ${packageText(item)}`.trim());
    });
    return Array.from(grouped, ([category, items]) => `${category}\n${items.join("\n")}`).join("\n\n");
  }
  function download(name, value, type) {
    const url = URL.createObjectURL(new Blob([value], { type }));
    const anchor = document.createElement("a");
    anchor.href = url; anchor.download = name; document.body.appendChild(anchor); anchor.click(); anchor.remove(); URL.revokeObjectURL(url);
  }
  function updateProgress() {
    const items = visibleItems();
    const done = items.filter((item) => stateFor(item).done).length;
    const pantry = (data?.items || []).length - items.length;
    $("progress_text").textContent = `${done} of ${items.length} collected${pantry ? ` · ${pantry} already on hand` : ""}`;
  }
  async function persistPreference(item) {
    const state = stateFor(item);
    await glyco.fetchJSON("/v1/plan/grocery-list/preferences", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ingredient_key: item.id, in_pantry: Boolean(state.pantry), preferred_brand: state.preferredBrand || null, package_quantity: state.packageQuantity ? Number(state.packageQuantity) : null, package_unit: state.packageUnit || null }),
    });
  }
  function buildRow(item, pantryView = false) {
    const state = stateFor(item);
    const row = document.createElement("div");
    row.className = `grocery-item${state.done ? " is-done" : ""}`;
    const check = document.createElement("input");
    check.type = "checkbox"; check.checked = state.done; check.disabled = pantryView; check.setAttribute("aria-label", `Collected ${item.name}`);
    check.addEventListener("change", () => { state.done = check.checked; saveEdits(false); row.classList.toggle("is-done", state.done); updateProgress(); });
    const name = document.createElement("div");
    name.className = "item-name"; name.textContent = item.name;
    const use = document.createElement("span");
    use.className = "item-use"; use.textContent = item.measurement_summary ? `${item.measurement_summary} · Used in ${item.uses.length} meals` : `Used in ${item.uses.length} meal${item.uses.length === 1 ? "" : "s"}`;
    const purchase = document.createElement("span");
    purchase.className = "purchase-guidance"; purchase.textContent = pantryView ? "Already in your pantry" : packageText(item);
    name.append(use, purchase);
    const qty = document.createElement("input");
    qty.className = "quantity"; qty.type = "number"; qty.min = "0"; qty.step = "any"; qty.value = scaledQuantity(item); qty.disabled = pantryView; qty.setAttribute("aria-label", `${item.name} needed quantity`);
    qty.addEventListener("change", () => { state.quantity = Number(qty.value) / Number($("servings").value || 1); saveEdits(); purchase.textContent = packageText(item); });
    const unit = document.createElement("input");
    unit.className = "unit"; unit.value = state.unit || ""; unit.placeholder = "unit"; unit.disabled = pantryView; unit.setAttribute("aria-label", `${item.name} needed unit`);
    unit.addEventListener("change", () => { state.unit = unit.value.trim(); saveEdits(); purchase.textContent = packageText(item); });
    const options = document.createElement("button");
    options.className = "pantry-btn"; options.type = "button"; options.textContent = pantryView ? "Need this" : "Options";
    options.addEventListener("click", async () => {
      if (pantryView) { state.pantry = false; await persistPreference(item); saveEdits(); render(); } else openOptions(item);
    });
    row.append(check, name, qty, unit, options);
    return row;
  }
  function render() {
    const root = $("grocery_list"); root.innerHTML = "";
    if (!data?.items?.length) { root.innerHTML = '<div class="empty"><strong>No grocery items found.</strong><p class="muted">Create meal plans for this date range, then update the list.</p></div>'; updateProgress(); return; }
    const byCategory = {};
    visibleItems().forEach((item) => { (byCategory[item.category] ||= []).push(item); });
    CATEGORIES.concat(Object.keys(byCategory).filter((category) => !CATEGORIES.includes(category))).forEach((category) => {
      const items = byCategory[category]; if (!items?.length) return;
      const section = document.createElement(category === "Pantry" ? "details" : "section"); section.className = `category${category === "Pantry" ? " category--pantry" : ""}`;
      const heading = document.createElement(category === "Pantry" ? "summary" : "h2"); heading.textContent = `${category}${category === "Pantry" ? " check" : ""} · ${items.length}`; section.appendChild(heading);
      items.forEach((item) => section.appendChild(buildRow(item))); root.appendChild(section);
    });
    const pantryItems = (data.items || []).filter((item) => stateFor(item).pantry);
    if (pantryItems.length) {
      const section = document.createElement("details"); section.className = "category category--pantry pantry-owned";
      const heading = document.createElement("summary"); heading.textContent = `Already have · ${pantryItems.length}`; section.appendChild(heading);
      pantryItems.forEach((item) => section.appendChild(buildRow(item, true))); root.appendChild(section);
    }
    updateProgress();
  }
  function openOptions(item) {
    editingItem = item;
    const state = stateFor(item);
    $("grocery-options-title").textContent = item.name; $("grocery-brand").value = state.preferredBrand || ""; $("grocery-package-quantity").value = state.packageQuantity || ""; $("grocery-package-unit").value = state.packageUnit || state.unit || ""; $("grocery-in-pantry").checked = Boolean(state.pantry); $("grocery-options-error").textContent = ""; $("grocery-options-dialog").showModal();
  }
  async function load() {
    const notice = $("notice"); notice.hidden = true; $("grocery_list").innerHTML = '<p class="muted">Building your grocery list…</p>';
    try {
      data = await glyco.fetchJSON(`/v1/plan/grocery-list/week?${rangeQuery()}`);
      const approvalResponse = await glyco.fetchJSON(`/v1/plan/grocery-list/approval?${rangeQuery()}`); approval = approvalResponse.approval; loadEdits();
      if (approval && !approval.stale) {
        $("servings").value = approval.servings;
        approval.items.forEach((item) => { edits[item.id] = { done: false, pantry: Boolean(item.pantry), quantity: item.quantity == null ? null : Number(item.quantity) / approval.servings, unit: item.unit, preferredBrand: item.preferred_brand || item.preference?.preferred_brand || "", packageQuantity: item.package_quantity || item.package?.package_size || null, packageUnit: item.package_unit || item.package?.package_unit || item.unit || "" }; });
      }
      if (data.missing_dates.length) { notice.textContent = `${data.missing_dates.length} selected day${data.missing_dates.length === 1 ? " has" : "s have"} no meal plan yet. Only planned days are included.`; notice.hidden = false; }
      render(); renderApproval();
    } catch (error) { const root = $("grocery_list"); root.replaceChildren(); const message = document.createElement("div"); message.className = "empty"; message.textContent = error.message || "Could not load grocery list."; root.appendChild(message); }
  }
  async function approve() {
    const button = $("approve_btn"); button.disabled = true; button.textContent = "Approving…";
    try {
      const items = (data?.items || []).map((item) => { const state = stateFor(item); const pack = packagePlan(item); return { id: item.id, quantity: scaledQuantity(item) === "" ? null : Number(scaledQuantity(item)), unit: state.unit || "", pantry: Boolean(state.pantry), preferred_brand: state.preferredBrand || null, package_quantity: state.packageQuantity ? Number(state.packageQuantity) : null, package_unit: state.packageUnit || null, purchase_count: pack?.count || null }; });
      const result = await glyco.fetchJSON(`/v1/plan/grocery-list/approval?${rangeQuery()}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ servings: Number($("servings").value || 1), items }) });
      approval = result.approval; saveEdits(false); renderApproval();
    } catch (error) { const notice = $("notice"); notice.textContent = error.message || "Could not approve this list."; notice.hidden = false; renderApproval(); }
    finally { button.disabled = Boolean(data?.missing_dates?.length); }
  }

  document.addEventListener("DOMContentLoaded", () => {
    const query = new URLSearchParams(location.search); const start = query.get("start") || localISO(monday(new Date())); $("start_date").value = start; $("end_date").value = query.get("end") || addDays(start, 6);
    $("load_btn").addEventListener("click", load); $("servings").addEventListener("change", () => { markChanged(); render(); }); $("approve_btn").addEventListener("click", approve);
    $("clear_btn").addEventListener("click", () => { Object.values(edits).forEach((state) => { state.done = false; }); saveEdits(false); render(); });
    $("copy_btn").addEventListener("click", async () => { await navigator.clipboard.writeText(listText()); $("copy_btn").textContent = "Copied"; setTimeout(() => { $("copy_btn").textContent = "Copy list"; }, 1500); });
    $("print_btn").addEventListener("click", () => window.print()); $("txt_btn").addEventListener("click", () => download("glycofy-grocery-list.txt", listText(), "text/plain;charset=utf-8"));
    $("csv_btn").addEventListener("click", () => download("glycofy-grocery-list.csv", ["category", "item", "needed_quantity", "unit", "preferred_brand", "package_size", "package_unit", "packages_to_buy", "collected"].concat(visibleItems().map((item) => { const state = stateFor(item); const pack = packagePlan(item); return [item.category, item.name, scaledQuantity(item), state.unit, state.preferredBrand, state.packageQuantity, state.packageUnit, pack?.count, state.done].map(escapeCsv).join(","); })).join("\n"), "text/csv;charset=utf-8"));
    $("grocery-options-cancel").addEventListener("click", () => $("grocery-options-dialog").close());
    $("grocery-options-form").addEventListener("submit", async (event) => {
      event.preventDefault(); if (!editingItem) return; const state = stateFor(editingItem); state.preferredBrand = $("grocery-brand").value.trim(); state.packageQuantity = $("grocery-package-quantity").value ? Number($("grocery-package-quantity").value) : null; state.packageUnit = $("grocery-package-unit").value.trim(); state.pantry = $("grocery-in-pantry").checked;
      try { await persistPreference(editingItem); saveEdits(); $("grocery-options-dialog").close(); render(); } catch (error) { $("grocery-options-error").textContent = error.message || "Could not save this preference."; }
    });
    $("logout_btn").addEventListener("click", () => glyco.doLogout && glyco.doLogout()); load();
  });
})();
