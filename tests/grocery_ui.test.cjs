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
  const stored=new Map([['glyco_grocery:2026-11-04:2026-11-04',JSON.stringify({egg:{quantity:250,unit:'oz',packageQuantity:8,packageUnit:'oz'}})]]);
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
  await pantry.children[1].children[4].fire('click');
  assert.equal(JSON.parse(requests.filter(r=>r.url.endsWith('/preferences')).at(-1).options.body).in_pantry,false);
  row=node('grocery_list').children[0].children[1];
  row.children[0].checked=true; row.children[0].fire('change');
  assert.match(node('progress_text').textContent,/1 of 1 collected/);
  assert.equal(JSON.parse(stored.get('glyco_grocery:v3:2026-11-04:2026-11-04')).egg.done,true);
  assert.equal(JSON.parse(stored.get('glyco_grocery:2026-11-04:2026-11-04')).egg.unit,'oz');
  assert.equal(requests.some(r=>r.options.method && !r.url.endsWith('/preferences')),false);
});
