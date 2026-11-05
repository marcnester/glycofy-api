(async () => {
  const $ = id => document.getElementById(id);
  const api = (url, options = {}) => window.__glyco.fetchJSON(url, {
    ...options, headers: { ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...(options.headers || {}) },
  });
  const money = value => new Intl.NumberFormat(undefined, {style:'currency',currency:'USD'}).format(value);
  const preferenceKey = 'glyco_qfc_store_v1';
  let saved = {};
  try { saved = JSON.parse(localStorage.getItem(preferenceKey) || '{}') || {}; } catch { /* Storage is optional. */ }
  let selections = new Map();
  let cards = new Map();
  let stores = new Map();
  let reviewId = null;
  let generation = 0;
  let listGeneration = 0;
  let totalIngredients = 0;
  const el = (tag, text, className) => { const node = document.createElement(tag); if (text != null) node.textContent = text; if (className) node.className = className; return node; };
  const method = () => $('modality').value === 'DELIVERY' ? 'Delivery' : 'Pickup';
  const addressText = address => [address?.addressLine1,address?.city,address?.state,address?.zipCode].filter(Boolean).join(', ');
  const errorText = err => err.message && !err.message.includes('[object Object]') ? err.message : 'We couldn’t complete that request. Please check your selection and try again.';
  function say(message, error = false) { $('status').textContent = message; $('status').classList.toggle('is-error',error); }
  function step(name) { ['store','products','review'].forEach(id => { if (id === name) $( `step-${id}`).setAttribute('aria-current','step'); else $(`step-${id}`).removeAttribute('aria-current'); }); }
  function invalidate() { generation++; reviewId = null; $('review').hidden = true; $('success').hidden = true; $('store-confirmed').checked = false; $('send').disabled = true; }
  function updateSummary() {
    $('selected-count').textContent = String(selections.size);
    $('selection-progress').textContent = `${selections.size} of ${totalIngredients} selected · ${Math.max(0,totalIngredients-selections.size)} left out`;
    $('preview').disabled = !selections.size;
    $('preview').textContent = selections.size ? `Review ${selections.size} selected product${selections.size === 1 ? '' : 's'} →` : 'Review selected products →';
    const values = [...selections.values()];
    const priced = values.filter(item => Number.isFinite(item.price));
    const total = priced.reduce((sum,item) => sum + item.price * item.quantity,0);
    $('selection-total').textContent = values.length ? `${money(total)} estimated${priced.length < values.length ? ' + unpriced products' : ''}. Before taxes and fees.` : 'Choose a product to see a package estimate.';
    applyFilter();
  }
  function applyFilter() { for (const [id,card] of cards) { const filter = $('item-filter').value; card.hidden = filter === 'selected' ? !selections.has(id) : filter === 'unmatched' ? selections.has(id) : false; } }
  function reset() { invalidate(); listGeneration++; selections = new Map(); cards = new Map(); totalIngredients = 0; $('ingredients').replaceChildren(); $('list-tools').hidden = true; $('list-count').textContent = 'No list loaded'; updateSummary(); }
  function rememberStore() {
    saved = {zip:$('zip').value,store:$('store').value,modality:$('modality').value};
    try { localStorage.setItem(preferenceKey,JSON.stringify(saved)); } catch { /* Shopping still works without storage. */ }
  }
  function selectedStore() {
    reset(); rememberStore(); const store = stores.get($('store').value);
    $('products-panel').hidden = !store;
    $('trip-store').textContent = store ? store.name.replace(/^Quality Food Center - /,'QFC ') : 'Choose your QFC';
    $('trip-address').textContent = store ? addressText(store.address) : 'Your store’s catalog and prices will appear here.';
    $('trip-modality').textContent = method();
    $('store-summary').textContent = store ? `${store.name.replace(/^Quality Food Center - /,'QFC ')} · ${method()}` : 'Choose where you shop';
    if (store) { step('products'); say('Your store is set. Load your approved grocery list to choose products.'); }
  }
  async function act(fn) { try { await fn(); } catch (err) { say(errorText(err),true); } }
  const today = new Date(); today.setDate(today.getDate()-((today.getDay()+6)%7));
  const iso = d => `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
  const params = new URLSearchParams(location.search);
  const validDate = value => /^\d{4}-\d{2}-\d{2}$/.test(value || '') && !Number.isNaN(new Date(`${value}T12:00:00`).getTime()) && iso(new Date(`${value}T12:00:00`)) === value;
  $('start').value = validDate(params.get('start')) ? params.get('start') : iso(today); today.setDate(today.getDate()+6);
  $('end').value = validDate(params.get('end')) ? params.get('end') : iso(today);
  if (/^\d{5}$/.test(saved.zip || '')) $('zip').value = saved.zip;
  if (['PICKUP','DELIVERY'].includes(saved.modality)) $('modality').value = saved.modality;
  $('store').addEventListener('change',selectedStore);
  $('modality').addEventListener('change',selectedStore);
  ['start','end'].forEach(id => $(id).addEventListener('change',reset));
  $('item-filter').addEventListener('change',applyFilter);
  async function loadStores(restore = false) {
    const result = await api(`/v1/qfc/stores?zip_code=${encodeURIComponent($('zip').value)}`);
    stores = new Map(result.stores.map(store => [store.locationId,store]));
    $('store').replaceChildren(new Option('Choose a store',''));
    for (const store of result.stores) $('store').add(new Option(`${store.name} — ${store.address.addressLine1}`,store.locationId));
    if (restore && stores.has(saved.store)) { $('store').value = saved.store; selectedStore(); $('store-settings').open = false; }
    else { $('store').value = ''; selectedStore(); step('store'); say(result.stores.length ? `${result.stores.length} QFC stores found. Choose where you shop.` : 'No QFC stores found nearby. Try another ZIP code.'); }
  }
  $('stores-form').addEventListener('submit', e => { e.preventDefault(); return act(() => loadStores()); });
  $('groceries-form').addEventListener('submit', e => { e.preventDefault(); return act(async () => {
    reset(); if (!$('store').value) throw new Error('Choose your QFC store first.');
    const version = listGeneration;
    say('Loading your approved groceries…');
    const result = await api(`/v1/qfc/groceries?start=${$('start').value}&end=${$('end').value}`);
    if (version !== listGeneration) return;
    totalIngredients = result.items.length;
    for (const item of result.items) {
      const card = el('details',null,'ingredient-card'); cards.set(item.id,card);
      const summary = el('summary'); const check = el('span','+','ingredient-check'); check.setAttribute('aria-hidden','true');
      const title = el('span'); title.append(el('span',item.name || item.id,'ingredient-name'));
      const needed = el('span',`Needed: ${item.quantity ?? 'review amount'} ${item.unit || ''}`,'ingredient-needed'); title.append(needed);
      const state = el('span','To choose','ingredient-state'); summary.append(check,title,state);
      const body = el('div',null,'ingredient-body'); const searchForm = el('form',null,'product-search');
      const query = el('input'); query.value = item.name || item.id; query.maxLength = 100; query.setAttribute('aria-label',`Search for ${item.name || item.id}`);
      const search = el('button','Find products','btn'); search.type = 'submit'; searchForm.append(query,search);
      const results = el('div',null,'product-results'); results.append(el('p','Search QFC to compare products, package sizes, and prices.','product-empty'));
      const controls = el('div',null,'package-controls'); const label = el('label','Packages to buy');
      const qty = el('input'); qty.type='number'; qty.min='1'; qty.max='99'; qty.step='1'; qty.value='1'; qty.setAttribute('aria-label',`Number of packages for ${item.name || item.id}`); label.append(qty);
      const clear = el('button','Leave this ingredient out','link-button'); clear.type='button'; controls.hidden=true; controls.append(label,clear);
      const update = () => {
        invalidate(); const selected = selections.get(item.id);
        if (selected) { selected.quantity=Number(qty.value); state.textContent='Selected'; check.textContent='✓'; needed.textContent=`${qty.value} × ${selected.description} · ${selected.size}`; }
        else { state.textContent='To choose'; check.textContent='+'; needed.textContent=`Needed: ${item.quantity ?? 'review amount'} ${item.unit || ''}`; }
        card.classList.toggle('is-selected',!!selected); controls.hidden=!selected; updateSummary();
      };
      qty.addEventListener('input',update);
      clear.addEventListener('click',() => { selections.delete(item.id); results.replaceChildren(el('p','Ingredient left out. Search again to choose a product.','product-empty')); update(); });
      searchForm.addEventListener('submit',e => { e.preventDefault(); return act(async () => {
        search.disabled=true; search.textContent='Searching…';
        try {
          const store=$('store').value; const currentList=listGeneration;
          const result=await api(`/v1/qfc/products?location_id=${store}&term=${encodeURIComponent(query.value)}`);
          if (!card.isConnected || store !== $('store').value || currentList !== listGeneration) return;
          selections.delete(item.id); results.replaceChildren(); update();
          const available=result.products.filter(p => p.upc && p.items?.length===1 && p.items[0].soldBy==='UNIT' && p.items[0].fulfillment?.[$('modality').value==='PICKUP'?'curbside':'delivery']);
          for (const product of available) {
            const pack=product.items[0]; const price=pack.price?.promo ?? pack.price?.regular;
            const choice=el('label',null,'product-choice'); const radio=el('input'); radio.type='radio'; radio.name=`qfc-${item.id}`; radio.value=product.upc;
            radio.setAttribute('aria-label',`${product.description}, ${pack.size || 'size unknown'}${Number.isFinite(price)?`, ${money(price)}`:''}`);
            const copy=el('span'); if(product.brand) copy.append(el('span',product.brand,'product-brand'));
            copy.append(el('span',product.description,'product-title'),el('span',`${pack.size || 'Size not provided'} · ${method()} available`,'product-size'));
            choice.append(radio,copy,el('span',Number.isFinite(price)?money(price):'See QFC','product-price'));
            radio.addEventListener('change',() => { if(!radio.checked) return; selections.set(item.id,{ingredient_id:item.id,upc:product.upc,quantity:Number(qty.value),description:product.description,size:pack.size || 'Size not provided',price:Number.isFinite(price)?price:null}); update(); });
            results.append(choice);
          }
          if(!available.length) results.append(el('p',`No packaged products available for ${method().toLowerCase()} in these results. Try a different search or leave this ingredient out.`,'product-empty'));
        } catch(err) { results.replaceChildren(el('p',errorText(err),'product-empty')); throw err; }
        finally { search.disabled=false; search.textContent='Find products'; }
      }); });
      body.append(searchForm,results,controls); card.append(summary,body); $('ingredients').append(card);
    }
    $('list-count').textContent=`${totalIngredients} ingredients`; $('list-tools').hidden=!totalIngredients;
    $('store-settings').open=false; step('products'); updateSummary();
    if(!totalIngredients) $('ingredients').append(el('div','There are no groceries to shop for in this approved list.','qfc-empty'));
    say('Open an ingredient to compare products. Select only what you want to add to QFC.');
  }); });
  $('preview').addEventListener('click',() => act(async () => {
    invalidate(); const version=generation; $('preview').disabled=true;
    try {
      const items=[...selections.values()].map(({ingredient_id,upc,quantity}) => ({ingredient_id,upc,quantity}));
      const result=await api('/v1/qfc/cart/preview',{method:'POST',body:JSON.stringify({start:$('start').value,end:$('end').value,location_id:$('store').value,modality:$('modality').value,items})});
      if(version!==generation) return;
      reviewId=result.review_id; const root=$('review-details'); root.replaceChildren();
      const store=el('div',null,'review-store'); const storeCopy=el('div'); storeCopy.append(el('h3',result.store),el('p',addressText(result.store_address),'review-address')); store.append(storeCopy,el('span',result.modality==='PICKUP'?'Pickup':'Delivery','method-badge')); root.append(store);
      let total=0; let unpriced=0;
      for(const [index,item] of result.items.entries()) {
        const row=el('div',null,'review-product'); const copy=el('div'); copy.append(el('strong',item.description),el('p',`${item.quantity} package${item.quantity===1?'':'s'} · ${item.size || 'Size not provided'}`));
        const price=item.price?.promo ?? item.price?.regular; const priced=Number.isFinite(price);
        if(priced) total+=price*item.quantity; else unpriced++;
        row.append(el('span',String(index+1),'review-number'),copy,el('span',priced?money(price*item.quantity):'See QFC','product-price')); root.append(row);
      }
      root.append(el('p',`Estimated products: ${money(total)}${unpriced?' + unpriced items':''}. Before taxes and fees.`,'fine-print muted'));
      $('review-warning').textContent=`${result.items.length} product${result.items.length===1?'':'s'} selected · ${result.omitted_ingredients} ingredient${result.omitted_ingredients===1?'':'s'} left out of this cart addition.`;
      $('store-confirmation-label').textContent=`I selected ${result.store} — ${addressText(result.store_address)} for ${result.modality==='PICKUP'?'Pickup':'Delivery'} on QFC.`;
      $('store-confirmed').checked=false; $('send').disabled=true; $('review').hidden=false; step('review'); $('review').scrollIntoView({behavior:'smooth',block:'start'}); say('Check your products and confirm your store on QFC. Nothing has been added yet.');
    } finally { $('preview').disabled=!selections.size; }
  }));
  $('edit-products').addEventListener('click',() => { invalidate(); step('products'); $('products-panel').scrollIntoView({behavior:'smooth',block:'start'}); });
  $('store-confirmed').addEventListener('change',() => { $('send').disabled=!reviewId || !$('store-confirmed').checked; });
  $('send').addEventListener('click',() => act(async () => {
    if(!reviewId || !$('store-confirmed').checked) return; const id=reviewId; reviewId=null; $('send').disabled=true;
    try {
      await api(`/v1/qfc/cart/${encodeURIComponent(id)}/send`,{method:'POST',body:JSON.stringify({retailer_store_confirmed:true})});
      $('review').hidden=true; $('success').hidden=false; $('success').scrollIntoView({behavior:'smooth',block:'start'}); say('Your selected products were added. Review your cart on QFC when you’re ready.');
    } catch(err) { say(`${errorText(err)} Check your QFC cart before making another selection.`,true); }
    finally { reviewId=null; }
  }));
  $('connect-form').addEventListener('submit',e => { e.preventDefault(); return act(async () => {
    const result=await api('/v1/qfc/connect',{method:'POST'}); const target=new URL(result.authorize_url);
    if(target.origin!=='https://api.kroger.com' || target.pathname!=='/v1/connect/oauth2/authorize') throw new Error('Invalid authorization destination.'); location.assign(target.href);
  }); });
  $('disconnect').addEventListener('click',() => act(async () => { await api('/v1/qfc/connection',{method:'DELETE'}); location.reload(); }));
  await act(async () => {
    const status=await api('/v1/qfc/status');
    if(!status.configured) { $('connection-badge').textContent='Not available yet'; say('QFC shopping isn’t available in this environment yet. Your grocery list is still ready to use.'); return; }
    $('connection').hidden=status.connected; $('shopping').hidden=!status.connected;
    $('connection-badge').textContent=status.connected?'✓ QFC connected':'Connect your QFC account'; $('connection-badge').classList.toggle('is-connected',status.connected);
    const outcome=params.get('connection');
    const clean=new URLSearchParams(); if(validDate(params.get('start'))) clean.set('start',params.get('start')); if(validDate(params.get('end'))) clean.set('end',params.get('end'));
    history.replaceState(null,'',location.pathname+(clean.size?`?${clean}`:''));
    say(status.connected?'Your account is connected. Choose your store to start.':outcome==='failed'?'Connection wasn’t completed. Please try connecting again.':outcome==='cancelled'?'Connection cancelled. Your cart is unchanged.':'Connect QFC to start choosing products.');
    if(status.connected && /^\d{8}$/.test(saved.store || '')) await loadStores(true);
  });
})();
