(async () => {
  const $ = id => document.getElementById(id);
  const api = window.__glyco.fetchJSON;
  let selections = new Map();
  let reviewId = null;
  let generation = 0;
  function say(message) { $('status').textContent = message; }
  function invalidate() { generation++; reviewId = null; $('review').hidden = true; }
  function reset() { invalidate(); selections = new Map(); $('ingredients').replaceChildren(); $('preview').disabled = true; }
  function el(tag, text) { const node = document.createElement(tag); if (text) node.textContent = text; return node; }
  async function act(fn) { try { await fn(); } catch (err) { say(err.message || 'Unable to complete request.'); } }
  const today = new Date();
  today.setDate(today.getDate() - ((today.getDay() + 6) % 7));
  const iso = d => `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
  $('start').value = iso(today); today.setDate(today.getDate()+6); $('end').value = iso(today);
  ['store','modality','start','end'].forEach(id => $(id).addEventListener('change', reset));
  $('stores-form').addEventListener('submit', e => { e.preventDefault(); act(async () => {
    reset(); const result = await api(`/v1/qfc/stores?zip_code=${encodeURIComponent($('zip').value)}`);
    $('store').replaceChildren(new Option('Choose a store', ''));
    for (const s of result.stores) $('store').add(new Option(`${s.name} — ${s.address.addressLine1}`, s.locationId));
    say(`${result.stores.length} QFC stores found. Choose your store.`);
  }); });
  $('groceries-form').addEventListener('submit', e => { e.preventDefault(); act(async () => {
    reset(); if (!$('store').value) throw new Error('Choose your QFC store first.');
    const result = await api(`/v1/qfc/groceries?start=${$('start').value}&end=${$('end').value}`);
    const version = generation;
    for (const item of result.items) {
      const card = el('article'); card.append(el('h3', item.name || item.id), el('p', `Needed: ${item.quantity ?? 'review amount'} ${item.unit || ''}`));
      const query = el('input'); query.value = item.name || item.id; query.maxLength = 100; query.setAttribute('aria-label', `Search for ${item.name || item.id}`);
      const search = el('button', 'Find products'); search.type = 'button';
      const select = el('select'); select.setAttribute('aria-label', `Product for ${item.name || item.id}`); select.add(new Option('Leave out of cart', ''));
      const label = el('label', 'Number of packages'); const qty = el('input'); qty.type = 'number'; qty.min = '1'; qty.max = '99'; qty.step = '1'; qty.value = '1'; label.append(qty);
      const update = () => { invalidate(); if (select.value) selections.set(item.id, {ingredient_id:item.id, upc:select.value, quantity:Number(qty.value)}); else selections.delete(item.id); };
      select.addEventListener('change', update); qty.addEventListener('input', update);
      search.addEventListener('click', () => act(async () => {
        search.disabled = true;
        try {
          const store = $('store').value;
          const result = await api(`/v1/qfc/products?location_id=${store}&term=${encodeURIComponent(query.value)}`);
          if (!card.isConnected || store !== $('store').value) return;
          selections.delete(item.id); invalidate(); select.replaceChildren(new Option('Leave out of cart', ''));
          for (const p of result.products) {
            const packs = p.items || []; if (!p.upc || packs.length !== 1 || packs[0].soldBy !== 'UNIT') continue;
            const pack = packs[0]; const price = pack.price?.promo ?? pack.price?.regular;
            select.add(new Option(`${p.description} — ${pack.size || 'size unknown'}${price == null ? '' : ` — $${price.toFixed(2)}`}`, p.upc));
          }
        } finally { search.disabled = false; }
      }));
      card.append(query, search, select, label); $('ingredients').append(card);
    }
    if (version === generation) $('preview').disabled = !result.items.length;
    say('Choose products and package counts, then review. Nothing has been added yet.');
  }); });
  $('preview').addEventListener('click', () => act(async () => {
    invalidate(); const version = generation; $('preview').disabled = true;
    try {
      const result = await api('/v1/qfc/cart/preview', {method:'POST',body:JSON.stringify({start:$('start').value,end:$('end').value,location_id:$('store').value,modality:$('modality').value,items:[...selections.values()]})});
      if (version !== generation) return;
      reviewId = result.review_id; $('review-details').replaceChildren(el('h3', `${result.store} · ${result.modality}`));
      for (const item of result.items) $('review-details').append(el('p', `${item.quantity} package(s) × ${item.description} (${item.size || 'size unknown'})`));
      $('review-warning').textContent = `${result.omitted_ingredients} ingredient(s) left out. ${result.notice}`;
      $('review').hidden = false; $('send').disabled = false; say('Review the products below before adding them.');
    } finally { $('preview').disabled = false; }
  }));
  $('send').addEventListener('click', () => act(async () => {
    if (!reviewId) return; const id = reviewId; $('send').disabled = true;
    try { await api(`/v1/qfc/cart/${encodeURIComponent(id)}/send`, {method:'POST'}); say('Products added. Open QFC to check your cart, store, and checkout.'); }
    catch (err) { say(`${err.message || 'Result uncertain.'} Check your QFC cart before trying again.`); }
    finally { reviewId = null; }
  }));
  $('disconnect').addEventListener('click', () => act(async () => { await api('/v1/qfc/connection', {method:'DELETE'}); location.reload(); }));
  await act(async () => {
    const status = await api('/v1/qfc/status');
    if (!status.configured) { say('QFC shopping is not enabled in this environment yet.'); return; }
    $('connection').hidden = status.connected; $('shopping').hidden = !status.connected;
    const outcome = new URLSearchParams(location.search).get('connection');
    history.replaceState(null, '', location.pathname);
    say(status.connected ? 'QFC connected. Choose your store to begin.' : outcome === 'failed' ? 'Authorization failed. Please connect again.' : outcome === 'cancelled' ? 'Authorization cancelled. Your cart is unchanged.' : 'Connect your account to begin.');
  });
})();
