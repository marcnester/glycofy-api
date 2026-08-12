(function(){
  const glyco=window.__glyco||{};
  if(!glyco.fetchJSON)return;
  const $=id=>document.getElementById(id);
  const CATEGORIES=["Produce","Meat & Seafood","Dairy & Eggs","Grains & Bakery","Pantry","Other"];
  let data=null;
  let edits={};

  function localISO(date){return new Date(date.getTime()-date.getTimezoneOffset()*60000).toISOString().slice(0,10)}
  function monday(date){const copy=new Date(date);const offset=(copy.getDay()+6)%7;copy.setDate(copy.getDate()-offset);return copy}
  function addDays(iso,n){const d=new Date(iso+"T12:00:00");d.setDate(d.getDate()+n);return localISO(d)}
  function storageKey(){return `glyco_grocery:${$("start_date").value}:${$("end_date").value}`}
  function loadEdits(){try{edits=JSON.parse(localStorage.getItem(storageKey())||"{}")}catch{edits={}}}
  function saveEdits(){localStorage.setItem(storageKey(),JSON.stringify(edits))}
  function stateFor(item){
    if(!edits[item.id])edits[item.id]={done:false,pantry:false,quantity:item.quantity,unit:item.unit};
    return edits[item.id];
  }
  function scaledQuantity(item){
    const state=stateFor(item);
    if(state.quantity==null)return "";
    return Math.round(Number(state.quantity)*Number($("servings").value||1)*100)/100;
  }
  function escapeCsv(value){return `"${String(value??"").replace(/"/g,'""')}"`}
  function visibleItems(){return (data?.items||[]).filter(item=>!stateFor(item).pantry)}
  function listText(){
    const grouped=new Map();
    visibleItems().forEach(item=>{
      if(!grouped.has(item.category))grouped.set(item.category,[]);
      const qty=scaledQuantity(item);const unit=stateFor(item).unit||"";
      grouped.get(item.category).push(`- ${item.name}${qty!==""?` — ${qty} ${unit}`:""}`.trim());
    });
    return Array.from(grouped,([category,items])=>`${category}\n${items.join("\n")}`).join("\n\n");
  }
  function download(name,text,type){
    const url=URL.createObjectURL(new Blob([text],{type}));
    const a=document.createElement("a");a.href=url;a.download=name;document.body.appendChild(a);a.click();a.remove();URL.revokeObjectURL(url);
  }
  function updateProgress(){
    const items=visibleItems();const done=items.filter(item=>stateFor(item).done).length;
    $("progress_text").textContent=`${done} of ${items.length} collected`;
  }
  function render(){
    const root=$("grocery_list");root.innerHTML="";
    if(!data?.items?.length){root.innerHTML='<div class="empty"><strong>No grocery items found.</strong><p class="muted">Create meal plans for this date range, then update the list.</p></div>';updateProgress();return}
    const byCategory={};
    data.items.forEach(item=>{(byCategory[item.category]||(byCategory[item.category]=[])).push(item)});
    CATEGORIES.concat(Object.keys(byCategory).filter(c=>!CATEGORIES.includes(c))).forEach(category=>{
      const items=byCategory[category];if(!items?.length)return;
      const section=document.createElement("section");section.className="category";
      const heading=document.createElement("h2");heading.textContent=`${category} · ${items.filter(i=>!stateFor(i).pantry).length}`;section.appendChild(heading);
      items.forEach(item=>{
        const state=stateFor(item);const row=document.createElement("div");row.className=`grocery-item${state.done?" is-done":""}`;row.hidden=state.pantry;
        const check=document.createElement("input");check.type="checkbox";check.checked=state.done;check.setAttribute("aria-label",`Collected ${item.name}`);
        check.addEventListener("change",()=>{state.done=check.checked;saveEdits();row.classList.toggle("is-done",state.done);updateProgress()});
        const name=document.createElement("div");name.className="item-name";name.textContent=item.name;
        const use=document.createElement("span");use.className="item-use";use.textContent=`Used in ${item.uses.length} meal${item.uses.length===1?"":"s"}`;name.appendChild(use);
        const qty=document.createElement("input");qty.className="quantity";qty.type="number";qty.min="0";qty.step="any";qty.value=scaledQuantity(item);qty.setAttribute("aria-label",`${item.name} quantity`);
        qty.addEventListener("change",()=>{state.quantity=Number(qty.value)/Number($("servings").value||1);saveEdits()});
        const unit=document.createElement("input");unit.className="unit";unit.value=state.unit||"";unit.placeholder="unit";unit.setAttribute("aria-label",`${item.name} unit`);
        unit.addEventListener("change",()=>{state.unit=unit.value.trim();saveEdits()});
        const pantry=document.createElement("button");pantry.className="pantry-btn";pantry.type="button";pantry.textContent="I have this";
        pantry.addEventListener("click",()=>{state.pantry=true;saveEdits();render()});
        row.append(check,name,qty,unit,pantry);section.appendChild(row);
      });root.appendChild(section);
    });
    updateProgress();
  }
  async function load(){
    const notice=$("notice");notice.hidden=true;$("grocery_list").innerHTML='<p class="muted">Building your grocery list…</p>';
    try{
      data=await glyco.fetchJSON(`/v1/plan/grocery-list/week?start=${encodeURIComponent($("start_date").value)}&end=${encodeURIComponent($("end_date").value)}`);
      loadEdits();
      if(data.missing_dates.length){notice.textContent=`${data.missing_dates.length} selected day${data.missing_dates.length===1?" has":"s have"} no meal plan yet. Only planned days are included.`;notice.hidden=false}
      render();
    }catch(error){const root=$("grocery_list");root.replaceChildren();const message=document.createElement("div");message.className="empty";message.textContent=error.message||"Could not load grocery list.";root.appendChild(message)}
  }
  document.addEventListener("DOMContentLoaded",()=>{
    const query=new URLSearchParams(location.search);const start=query.get("start")||localISO(monday(new Date()));
    $("start_date").value=start;$("end_date").value=query.get("end")||addDays(start,6);
    $("load_btn").addEventListener("click",load);
    $("servings").addEventListener("change",render);
    $("clear_btn").addEventListener("click",()=>{Object.values(edits).forEach(state=>{state.done=false;state.pantry=false});saveEdits();render()});
    $("copy_btn").addEventListener("click",async()=>{await navigator.clipboard.writeText(listText());$("copy_btn").textContent="Copied";setTimeout(()=>$("copy_btn").textContent="Copy list",1500)});
    $("print_btn").addEventListener("click",()=>window.print());
    $("txt_btn").addEventListener("click",()=>download("glycofy-grocery-list.txt",listText(),"text/plain;charset=utf-8"));
    $("csv_btn").addEventListener("click",()=>download("glycofy-grocery-list.csv",["category,item,quantity,unit,collected"].concat(visibleItems().map(item=>[item.category,item.name,scaledQuantity(item),stateFor(item).unit,stateFor(item).done].map(escapeCsv).join(","))).join("\n"),"text/csv;charset=utf-8"));
    $("logout_btn").addEventListener("click",()=>glyco.doLogout&&glyco.doLogout());
    load();
  });
})();
