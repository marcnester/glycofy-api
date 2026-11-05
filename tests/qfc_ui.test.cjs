const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

class Element {
  constructor(tag = 'div') { this.tag = tag; this.value = ''; this.children = []; this.events = {}; this.hidden = false; this.isConnected = true; }
  addEventListener(name, action) { this.events[name] = action; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  setAttribute() {}
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
    if (url.includes('/products?')) return { products: [{ upc: '0001111079769', description: 'Eggs', items: [{ soldBy: 'UNIT', size: '12 ct', price: { regular: 4.79 } }] }] };
    if (url.endsWith('/preview')) {
      assert.equal(options.headers['Content-Type'], 'application/json');
      assert.deepEqual(JSON.parse(options.body).items, [{ ingredient_id: 'egg', upc: '0001111079769', quantity: 1 }]);
      return { review_id: 'test-review', store: 'Northgate', modality: 'PICKUP', omitted_ingredients: 0, notice: 'Review', items: [{ quantity: 1, description: 'Eggs', size: '12 ct' }] };
    }
    if (url.endsWith('/send')) return { status: 'sent' };
    throw new Error(`Unexpected test request: ${url}`);
  };
  const context = vm.createContext({
    window: { __glyco: { fetchJSON: api } },
    document: { getElementById: node, createElement: tag => new Element(tag) },
    Date, Map, Number, String, URL, URLSearchParams, encodeURIComponent,
    location: { search: '', pathname: '/ui/qfc.html' }, history: { replaceState() {} },
    Option: class extends Element { constructor(label, value) { super('option'); this.textContent = label; this.value = value; } },
  });
  await vm.runInContext(fs.readFileSync('ui/qfc.js', 'utf8'), context);
  node('store').value = '70500866'; node('modality').value = 'PICKUP';
  node('groceries-form').fire('submit'); await settle();
  const card = node('ingredients').children[0];
  const search = card.children.find(e => e.tag === 'button');
  const select = card.children.find(e => e.tag === 'select');
  search.fire('click'); await settle();
  select.value = '0001111079769'; select.fire('change');
  node('preview').fire('click'); await settle();
  assert.equal(node('review').hidden, false);
  assert.equal(requests.some(r => r.url.endsWith('/send')), false);
  node('send').fire('click'); await settle();
  node('send').fire('click'); await settle();
  assert.equal(requests.filter(r => r.url.endsWith('/send')).length, 1);
});
