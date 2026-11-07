const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
class Element {
  constructor(tag='div') { this.tag=tag; this.value=''; this.children=[]; this.events={}; this.attributes={}; this.classList={toggle(){}}; }
  set innerHTML(value) { this.children=[]; this.html=value; }
  get innerHTML() { return this.html || ''; }
  append(...children) { this.children.push(...children); }
  appendChild(child) { this.children.push(child); }
  replaceChildren(...children) { this.children=children; }
  addEventListener(name,fn) { this.events[name]=fn; }
  setAttribute(name,value) { this.attributes[name]=value; }
  closest() { return this; }
  fire(name) { return this.events[name]({preventDefault(){}}); }
}
const tick=()=>new Promise(resolve=>setImmediate(resolve));
test('grocery page ignores legacy units and preserves Already have/Need this edits',async()=>{
  const nodes=new Map();
  const node=id=>{if(!nodes.has(id)) nodes.set(id,new Element()); return nodes.get(id);};
  node('servings').value='1';
  const stored=new Map([['glyco_grocery:v3:2026-11-04:2026-11-04',JSON.stringify({egg:{quantity:250,unit:'oz',packageQuantity:8,packageUnit:'oz'}})]]);
  const requests=[]; let failPreference=true;
  const api=async(url,options={})=>{
    requests.push({url,options});
    if(url.includes('/week?')) return {missing_dates:[],items:[{id:'egg',name:'Eggs',category:'Dairy & Eggs',quantity:6,unit:'item',uses:[],package:{package_size:12,package_unit:'item',source:'estimated'}}]};
    if(url.includes('/approval?')) return {approval:{stale:true,items:[]}};
    if(url.includes('/shopping?')) return {configured:false};
    if(url.endsWith('/preferences')) { if(failPreference) { failPreference=false; throw Error('synthetic save failure'); } return {}; }
    throw Error('Unexpected request: '+url);
  };
  const events={};
  const context=vm.createContext({window:{__glyco:{fetchJSON:api}},document:{getElementById:node,createElement:tag=>new Element(tag),addEventListener:(name,fn)=>events[name]=fn},localStorage:{getItem:key=>stored.get(key)||null,setItem:(key,value)=>stored.set(key,value)},location:{search:'?start=2026-11-04&end=2026-11-04'},Date,URLSearchParams,Intl,Map,Number,String,encodeURIComponent});
  vm.runInContext(fs.readFileSync('ui/grocery.js','utf8'),context); events.DOMContentLoaded(); await tick(); await tick();
  let row=node('grocery_list').children[0].children[1];
  assert.equal(row.children[2].value,6); assert.equal(row.children[3].value,'item');
  assert.match(row.children[1].children[1].textContent,/Estimated.*12 items/);
  assert.match(node('approval_status').textContent,/grocery list needs a fresh review/);
  const already=row.children[1].children[2];
  await already.fire('click'); assert.match(already.textContent,/Could not save/);
  assert.equal(node('grocery_list').children[0].children[1].children[2].disabled,false);
  await already.fire('click');
  assert.match(node('progress_text').textContent,/1 already on hand/);
  const preference=JSON.parse(requests.filter(r=>r.url.endsWith('/preferences')).at(-1).options.body);
  assert.equal(preference.in_pantry,true);
  const pantry=node('grocery_list').children[0];
  assert.match(pantry.children[0].textContent,/Already have/);
  failPreference=true;
  await pantry.children[1].children[4].fire('click');
  assert.match(pantry.children[1].children[4].textContent,/Could not save/);
  assert.match(node('progress_text').textContent,/1 already on hand/);
  await pantry.children[1].children[4].fire('click');
  assert.equal(JSON.parse(requests.filter(r=>r.url.endsWith('/preferences')).at(-1).options.body).in_pantry,false);
  row=node('grocery_list').children[0].children[1];
  row.children[0].checked=true; row.children[0].fire('change');
  assert.match(node('progress_text').textContent,/1 of 1 collected/);
  assert.equal(JSON.parse(stored.get('glyco_grocery:v4:2026-11-04:2026-11-04')).egg.done,true);
  assert.equal(JSON.parse(stored.get('glyco_grocery:v3:2026-11-04:2026-11-04')).egg.unit,'oz');
  assert.equal(requests.some(r=>r.options.method && !r.url.endsWith('/preferences')),false);
});

async function reviewHarness({configured=true,missing=[],failSave=false}={}) {
  const nodes=new Map(); const node=id=>{if(!nodes.has(id))nodes.set(id,new Element());return nodes.get(id);};
  Element.prototype.showModal=function(){this.open=true;}; Element.prototype.close=function(){this.open=false;};
  node('servings').value='1'; const requests=[], destinations=[]; let resolveSave;
  const api=async(url,options={})=>{
    requests.push({url,options});
    if(url.endsWith('/qfc/status'))return {configured,connected:false};
    if(url.includes('/week?'))return {missing_dates:missing,items:[{id:'cod',name:'Cod fillets',category:'Meat & Seafood',quantity:18,unit:'oz',uses:[]}]};
    if(url.includes('/approval?')) {if(options.method==='POST'){if(failSave)throw Error('Save failed');await new Promise(resolve=>{resolveSave=resolve;});return {approval:{servings:1,approved_at:'2026-11-05T00:00:00Z',items:[]}};}return {approval:null};}
    if(url.includes('/shopping?'))return {configured:false};
    throw Error('Unexpected '+url);
  };
  const events={};const context=vm.createContext({window:{__glyco:{fetchJSON:api}},document:{getElementById:node,createElement:tag=>new Element(tag),addEventListener:(name,fn)=>events[name]=fn},localStorage:{getItem:()=>null,setItem(){}},location:{search:'?start=2026-12-01&end=2026-12-01',assign:url=>destinations.push(url)},Date,URLSearchParams,Intl,Map,Number,String,encodeURIComponent});
  vm.runInContext(fs.readFileSync('ui/grocery.js','utf8'),context);events.DOMContentLoaded();await tick();await tick();
  return {node,requests,destinations,release:()=>resolveSave()};
}
test('review opens without approval; cancel writes nothing; continue saves once before QFC',async()=>{
  const h=await reviewHarness();
  assert.match(h.node('approve_btn').textContent,/Review list & shop at QFC/);
  assert.equal(h.node('list-settings').open,false);
  h.node('review_sticky_btn').fire('click');assert.equal(h.node('grocery-review-dialog').open,true);
  assert.equal(h.node('review-items').children[0].children[1].textContent,'18 oz');
  assert.equal(h.node('review-item-details').open,false);
  assert.equal(h.node('review-buy-count').textContent,'1');
  assert.match(h.node('review-items-summary').textContent,/View 1 ingredient and amounts/);
  h.node('review-cancel').fire('click');assert.equal(h.node('grocery-review-dialog').open,false);
  assert.equal(h.requests.filter(r=>r.options.method==='POST').length,0);
  h.node('approve_btn').fire('click');const pending=h.node('review-continue').fire('click');
  await h.node('review-continue').fire('click');assert.equal(h.destinations.length,0);
  assert.equal(h.requests.filter(r=>r.options.method==='POST').length,1);
  const body=JSON.parse(h.requests.find(r=>r.options.method==='POST').options.body);
  assert.equal(body.items[0].quantity,18);assert.equal(body.items[0].unit,'oz');
  h.release();await pending;assert.deepEqual(h.destinations,['/ui/qfc.html?start=2026-12-01&end=2026-12-01']);
  assert.equal(h.requests.some(r=>/\/send|\/cart/.test(r.url)),false);
});
test('failed review save stays open and never navigates; date changes block review',async()=>{
  const h=await reviewHarness({failSave:true});h.node('approve_btn').fire('click');await h.node('review-continue').fire('click');
  assert.equal(h.node('grocery-review-dialog').open,true);assert.equal(h.node('review-error').textContent,'Save failed');assert.equal(h.destinations.length,0);
  h.node('review-cancel').fire('click');h.node('end_date').value='2026-12-02';h.node('end_date').fire('change');
  assert.equal(h.node('approve_btn').disabled,true);assert.equal(h.node('review_sticky_btn').disabled,true);
  h.node('approve_btn').fire('click');assert.equal(h.node('grocery-review-dialog').open,false);
});
test('QFC unavailable offers save-only and partial date ranges can be reviewed',async()=>{
  const h=await reviewHarness({configured:false});h.node('approve_btn').fire('click');assert.equal(h.node('review-continue').hidden,true);
  const pending=h.node('review-save').fire('click');h.release();await pending;
  assert.equal(h.node('grocery-review-dialog').open,false);assert.equal(h.destinations.length,0);
  const missing=await reviewHarness({missing:['2026-12-02']});assert.equal(missing.node('approve_btn').disabled,false);
  missing.node('approve_btn').fire('click');assert.equal(missing.node('grocery-review-dialog').open,true);
  assert.match(missing.node('review-exclusions').textContent,/Unplanned dates excluded: Dec 2/);
  assert.equal(missing.node('review-item-details').open,false);
  assert.equal(missing.node('review-buy-count').textContent,'1');
  assert.match(missing.node('approval_status').textContent,/Shop for the days already planned/);
});
