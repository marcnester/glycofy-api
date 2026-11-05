const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

class Element {
  constructor(tag = 'div') { this.tag = tag; this.value = ''; this.children = []; this.events = {}; this.hidden = false; this.isConnected = true; this.classList = { toggle() {} }; this.attributes = {};  }
  addEventListener(name, action) { this.events[name] = action; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  setAttribute(name, value) { this.attributes[name] = value; }
  removeAttribute(name) { delete this.attributes[name]; }
  scrollIntoView() { this.scrolled = true; }
  add(option) { this.children.push(option); }
  fire(name) { return this.events[name]({ preventDefault() {} }); }
}
const settle = () => new Promise(resolve => setImmediate(resolve));

test('real QFC UI builds JSON preview from selected product and writes cart only after Add', async () => {
  const nodes = new Map();
  const node = id => { if (!nodes.has(id)) nodes.set(id, new Element()); return nodes.get(id); };
  const requests = [];
  const api = async (url, options = {}) => {
    requests.push({ url, options });
    if (url.endsWith('/status')) return { configured: true, connected: true };
    if (url.includes('/groceries?')) return { items: [{ id: 'egg', name: 'Eggs', quantity: 2, unit: 'items' }] };
    if (url.includes('/products?')) return { products: [{ upc: '0001111079769', description: 'Eggs', items: [{ soldBy: 'UNIT', size: '12 ct', price: { regular: 4.79 }, fulfillment: { curbside: true, delivery: true } }] }] };
    if (url.endsWith('/preview')) {
      assert.equal(options.headers['Content-Type'], 'application/json');
      assert.deepEqual(JSON.parse(options.body).items, [{ ingredient_id: 'egg', upc: '0001111079769', quantity: 1 }]);
      return { review_id: 'test-review', store: 'Northgate', store_address: {addressLine1:'11100 Roosevelt Way NE',city:'Seattle',state:'WA',zipCode:'98125'}, modality: 'PICKUP', omitted_ingredients: 0, notice: 'Review', items: [{ quantity: 1, description: 'Eggs', size: '12 ct', price: { regular: 4.79 } }] };
    }
    if (url.endsWith('/send')) { assert.equal(options.headers['Content-Type'], 'application/json'); assert.equal(JSON.parse(options.body).retailer_store_confirmed, true); return { status: 'sent' }; }
    throw new Error(`Unexpected test request: ${url}`);
  };
  const context = vm.createContext({
    window: { __glyco: { fetchJSON: api } },
    document: { getElementById: node, createElement: tag => new Element(tag) },
    Date, Map, Number, String, URL, URLSearchParams, encodeURIComponent, Intl,
    localStorage: { getItem() { return null; }, setItem() {} },
    location: { search: '', pathname: '/ui/qfc.html' }, history: { replaceState() {} },
    Option: class extends Element { constructor(label, value) { super('option'); this.textContent = label; this.value = value; } },
  });
  await vm.runInContext(fs.readFileSync('ui/qfc.js', 'utf8'), context);
  node('store').value = '70500866'; node('modality').value = 'PICKUP';
  node('groceries-form').fire('submit'); await settle();
  const card = node('ingredients').children[0];
  const body = card.children[1];
  await body.children[0].fire('submit');
  const radio = body.children[1].children[0].children[0];
  radio.checked = true; radio.fire('change');
  assert.equal(node('selected-count').textContent, '1');
  assert.match(node('selection-total').textContent, /4.79/);
  node('item-filter').value = 'unmatched'; node('item-filter').fire('change');
  assert.equal(card.hidden, true);
  node('item-filter').value = 'all'; node('item-filter').fire('change');
  assert.equal(card.hidden, false);
  node('preview').fire('click'); await settle();
  assert.equal(node('review').hidden, false);
  assert.equal(node('review').scrolled, true);
  assert.match(node('store-confirmation-label').textContent, /11100 Roosevelt Way NE/);
  assert.match(node('store-confirmation-label').textContent, /Pickup/);
  assert.equal(requests.some(r => r.url.endsWith('/send')), false);
  assert.equal(node('send').disabled, true);
  node('send').fire('click'); await settle();
  assert.equal(requests.filter(r => r.url.endsWith('/send')).length, 0);
  node('store-confirmed').checked = true; node('store-confirmed').fire('change');
  assert.equal(node('send').disabled, false);
  const firstSend = node('send').fire('click');
  const secondSend = node('send').fire('click');
  await Promise.all([firstSend, secondSend]);
  assert.equal(requests.filter(r => r.url.endsWith('/send')).length, 1);
});

test('grocery QFC entry carries the current approved date range without changing groceries', async () => {
  const nodes = new Map();
  const node = id => { if (!nodes.has(id)) nodes.set(id, new Element()); return nodes.get(id); };
  node('start_date').value = '2026-11-04'; node('end_date').value = '2026-11-04';
  const requests = [];
  const context = vm.createContext({
    window: { __glyco: { fetchJSON: async url => { requests.push(url); return { configured: true, connected: true }; } } },
    document: { getElementById: node }, URLSearchParams,
  });
  await vm.runInContext(fs.readFileSync('ui/qfc-entry.js', 'utf8'), context);
  assert.equal(node('qfc-entry').hidden, false);
  assert.equal(node('qfc-shop-link').href, '/ui/qfc.html?start=2026-11-04&end=2026-11-04');
  node('end_date').value = '2026-11-05';
  node('qfc-shop-link').fire('click');
  assert.equal(node('qfc-shop-link').href, '/ui/qfc.html?start=2026-11-04&end=2026-11-05');
  assert.deepEqual(requests, ['/v1/qfc/status']);
});

test('QFC suggests package counts from store coverage and exposes a manual shortfall',async()=>{
  const nodes=new Map(); const node=id=>{if(!nodes.has(id))nodes.set(id,new Element());return nodes.get(id);};
  const coverage={status:'short',required_quantity:18,unit:'oz',quantity_per_package:12,suggested_packages:2};
  const api=async(url,options={})=>{
    if(url.endsWith('/status'))return {configured:true,connected:true};
    if(url.includes('/groceries?'))return {items:[{id:'cod fillet',name:'Cod fillets',quantity:18,unit:'oz'}]};
    if(url.includes('/products?')) {assert.match(url,/ingredient_id=cod%20fillet/);return {products:[{upc:'0001111079769',description:'Cod fillets',coverage,items:[{soldBy:'UNIT',size:'12 oz',fulfillment:{curbside:true}}]}]};}
    throw Error('Unexpected request: '+url);
  };
  const context=vm.createContext({window:{__glyco:{fetchJSON:api}},document:{getElementById:node,createElement:tag=>new Element(tag)},Date,Map,Number,String,URL,URLSearchParams,encodeURIComponent,Intl,localStorage:{getItem(){return null},setItem(){}},location:{search:'',pathname:'/ui/qfc.html'},history:{replaceState(){}},Option:class extends Element{constructor(label,value){super('option');this.value=value;}}});
  await vm.runInContext(fs.readFileSync('ui/qfc.js','utf8'),context);
  node('store').value='70500866';node('modality').value='PICKUP';
  await node('groceries-form').fire('submit');
  const body=node('ingredients').children[0].children[1]; await body.children[0].fire('submit');
  const radio=body.children[1].children[0].children[0];radio.checked=true;radio.fire('change');
  const controls=body.children[2],qty=controls.children[0].children[0];
  assert.equal(qty.value,'2');assert.match(controls.children[2].textContent,/selected 24 oz.*covers/);
  qty.value='1';qty.fire('input');assert.match(controls.children[2].textContent,/6 oz short/);
  assert.equal(node('send').disabled,true);
});
