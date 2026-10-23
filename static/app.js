"use strict";

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const colors = ["#087f73", "#79b7a1", "#b9d5c5", "#ddb778", "#819baf", "#986fba", "#d78b70", "#e1e8e8"];
const fields = {name:"Bezeichnung *", isin:"ISIN", value:"Gesamtwert", quantity:"Stückzahl", price:"Kurs", currency:"Währung des Werts", as_of:"Bewertungsdatum", bucket:"Baustein", cost_basis:"Einstandswert (optional)"};
let current = null;
let demo = false;
let csvText = "";
let preview = [];
let previewRevision = null;
let chartGeometry = null;

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}
function replace(node, children) { node.replaceChildren(...children); }
function decimal(value, digits = 1) { return value == null ? "—" : new Intl.NumberFormat("de-DE", {minimumFractionDigits:digits,maximumFractionDigits:digits}).format(value); }
function percent(value) { return value == null ? "—" : `${decimal(value)} %`; }
function signed(value) { return value == null ? "—" : `${value > 0 ? "+" : ""}${decimal(value)}`; }
function money(value) { return new Intl.NumberFormat("de-DE", {style:"currency", currency:current?.state.config.currency || "EUR", maximumFractionDigits:2}).format(value); }
function day(value) { return value ? new Intl.DateTimeFormat("de-DE", {day:"2-digit",month:"2-digit",year:"numeric",timeZone:"UTC"}).format(new Date(`${value}T12:00:00Z`)) : "—"; }
function message(text, isError = false) {
  const node = $(isError ? "#error" : "#toast");
  $(isError ? "#toast" : "#error").hidden = true;
  node.textContent = text;
  node.hidden = false;
  node.scrollIntoView({block:"nearest"});
}
async function api(path, data) {
  const options = data === undefined ? {} : {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(data)};
  const response = await fetch(path, {...options, signal:AbortSignal.timeout(path === "/api/monitor" ? 150000 : 20000)});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "Die Anfrage ist fehlgeschlagen.");
  return result;
}
async function run(button, action, mutation = false) {
  if (mutation && demo) { message("Bitte zuerst den Demo-Modus verlassen.", true); return; }
  if (button) button.disabled = true;
  $("#error").hidden = true;
  $("#toast").hidden = true;
  try { await action(); }
  catch (error) { message(error.name === "TimeoutError" ? "Die Anfrage dauert zu lange. Bitte neu laden und den gespeicherten Stand prüfen." : error.message, true); }
  finally { if (button) button.disabled = mutation && demo; }
}
function navigate(page, updateHash = true) {
  if (!["overview","holdings","import","settings","planning","automation"].includes(page)) page = "overview";
  $$(".page").forEach(node => { node.hidden = node.id !== `page-${page}`; });
  $$(".nav").forEach(node => {
    node.classList.toggle("active", node.dataset.page === page);
    if (node.dataset.page === page) node.setAttribute("aria-current", "page");
    else node.removeAttribute("aria-current");
  });
  if (updateHash) history.replaceState(null, "", `#${page}`);
  requestAnimationFrame(drawCharts);
}
document.addEventListener("click", event => {
  const target = event.target.closest("[data-page]");
  if (target) navigate(target.dataset.page);
});
function handleHash() {
  if (location.hash === "#methodology") {
    navigate("overview", false);
    $("#methodology").open = true;
    $("#methodology").scrollIntoView({block:"start"});
  } else navigate(location.hash.slice(1) || "overview", false);
}
window.addEventListener("hashchange", handleHash);

function render() {
  const {state, analysis:a, buckets} = current;
  const config = state.config;
  $("#demo-banner").hidden = !demo;
  $("#demo-toggle").textContent = demo ? "← Zurück zu meinem Depot" : "Mit Demo-Daten ausprobieren ↗";
  $("#data-status").textContent = demo ? "Fiktive Demo-Daten" : state.holdings.length ? "Importierte Depotdaten" : "Noch keine Depotdaten";
  $("#metric-total").textContent = state.holdings.length ? money(a.total) : "—";
  $("#metric-date").textContent = a.oldest_valuation ? `Bewertungen: ${day(a.oldest_valuation)}${a.latest_valuation !== a.oldest_valuation ? ` – ${day(a.latest_valuation)}` : ""}` : "Noch keine Bewertung";
  $("#metric-equity").textContent = percent(a.equity_pct);
  $("#metric-equity-detail").textContent = `Ziel ${decimal(a.equity_target,0)} % · Band ${decimal(Math.max(0,a.equity_target-config.equity_band_pp),0)}–${decimal(Math.min(100,a.equity_target+config.equity_band_pp),0)} %`;
  const p = a.performance;
  $("#metric-return").textContent = p ? `${signed(p.total_return)} %` : "—";
  $("#metric-return").className = p ? (p.total_return >= 0 ? "positive" : "negative") : "";
  $("#metric-period").textContent = p ? `${day(p.start)} – ${day(p.end)}` : "Bewertungshistorie fehlt";
  $("#metric-drawdown").textContent = p ? percent(p.max_drawdown) : "—";
  $("#chart-empty").hidden = !!p;
  $("#performance-chart").hidden = !p;
  $("#chart-mode").disabled = !p;
  $("#donut-count").textContent = a.allocations.filter(x => x.value > 0).length;
  replace($("#allocation-legend"), a.allocations.map((row,i) => {
    const node = element("div");
    node.append(element("i", undefined, `swatch palette-${i % colors.length}`), element("span",row.label),element("strong",percent(row.actual_pct)));
    return node;
  }));
  replace($("#allocation-table"), a.allocations.map((row,i) => {
    const tr = element("tr"), name = element("td");
    name.append(element("i",undefined,`swatch palette-${i % colors.length}`),document.createTextNode(row.label));
    const delta = element("td");
    delta.append(element("span",row.delta_pp == null ? "—" : `${signed(row.delta_pp)} PP`, `delta${row.outside_band ? " warn" : ""}`));
    tr.append(name,element("td",percent(row.actual_pct)),element("td",percent(row.target_pct)),delta);
    return tr;
  }));
  $("#alert-count").textContent = a.alerts.length;
  replace($("#alerts"), a.alerts.length ? a.alerts.map(alert => {
    const item = element("div",undefined,"alert-item");
    const body = element("div");
    body.append(element("h3",alert.title),element("p",alert.detail));
    item.append(element("span",alert.level === "data" ? "i" : "!",`alert-icon ${alert.level}`),body);
    return item;
  }) : [element("p","Innerhalb deiner Prüfregeln. Ein regelmäßiger Blick bleibt sinnvoll.")]);
  replace($("#holdings-table"), state.holdings.map(row => {
    const tr = element("tr"), name = element("td",row.name);
    name.append(element("span",row.isin || (row.source === "manual_cash" ? "Separat erfasst" : "Ohne ISIN"),"holding-isin"));
    tr.append(name,element("td",buckets[row.bucket]),element("td",decimal(row.quantity,4)),element("td",money(row.value)),element("td",day(row.as_of)));
    return tr;
  }));
  $("#holdings-empty").hidden = !!state.holdings.length;
  replace($("#history-table"), (p?.points || []).map(row => {
    const tr = element("tr");
    tr.append(element("td",day(row.date)),... ["portfolio","equity","world","reference"].map(k => element("td",percent(row[k]))));
    return tr;
  }));
  const cash = state.holdings.find(row => row.source === "manual_cash");
  $("#cash-amount").value = cash?.value || 0;
  $("#cash-date").value = cash?.as_of || a.as_of;
  $("#valuation-date").max = a.as_of;
  $("#cash-date").max = a.as_of;
  $$(".currency-label").forEach(node => {node.textContent = config.currency;});
  replace($("#target-inputs"), Object.entries(buckets).map(([key,label]) => {
    const wrap = element("label",`${label} (%)`);
    const input = element("input");
    Object.assign(input,{type:"number",min:0,max:100,step:0.1,required:true,value:config.targets[key],id:`target-${key}`});
    input.addEventListener("input",()=>{updateSum();$("#targets-confirmed").checked=false;});
    wrap.append(input);
    return wrap;
  }));
  $("#currency").value = config.currency;
  $("#targets-confirmed").checked = config.targets_confirmed;
  $("#equity-band").value = config.equity_band_pp;
  $("#bucket-band").value = config.bucket_band_relative;
  $("#bucket-band-pp").value = config.bucket_band_pp;
  $("#reconcile-days").value = config.reconcile_weekdays;
  $("#drawdown-limit").value = config.drawdown_alert_pct;
  $("#stale-days").value = config.stale_weekdays;
  $$("#page-import input, #page-import select, #page-import button, #cash-form input, #cash-form button, #settings-form input, #settings-form select, #settings-form button, #export").forEach(node => { node.disabled = demo; });
  updateSum();
  requestAnimationFrame(drawCharts);
  document.dispatchEvent(new Event("portfolio:render"));
}
function updateSum() {
  const sum = $$("#target-inputs input").reduce((total,node) => total + Number(node.value),0);
  $("#target-sum").textContent = `${decimal(sum)} % / 100 %`;
}
function setupCanvas(id, height) {
  const canvas = $(id);
  const width = canvas.getBoundingClientRect().width;
  if (!width) return null;
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.round(width*dpr);
  canvas.height = Math.round(height*dpr);
  const ctx = canvas.getContext("2d");
  ctx.scale(dpr,dpr);
  return {canvas,ctx,width,height};
}
function drawCharts() {
  if (!current || $("#page-overview").hidden) return;
  const donut = setupCanvas("#allocation-chart",200);
  if (donut) {
    const {ctx,width} = donut, cx=width/2, cy=100, r=77;
    ctx.lineWidth=24;
    ctx.strokeStyle="#edf1f1";
    ctx.beginPath();ctx.arc(cx,cy,r,0,Math.PI*2);ctx.stroke();
    let start=-Math.PI/2;
    current.analysis.allocations.forEach((row,i) => {
      if (!row.actual_pct) return;
      const end=start+row.actual_pct/100*Math.PI*2;
      ctx.strokeStyle=colors[i % colors.length];ctx.beginPath();ctx.arc(cx,cy,r,start+.012,Math.max(start+.012,end-.012));ctx.stroke();start=end;
    });
  }
  drawLine();
}
function drawLine(hover = null) {
  const p = current?.analysis.performance;
  if (!p) { $("#chart-detail").textContent=""; return; }
  const chart=setupCanvas("#performance-chart",270);
  if (!chart) return;
  const {ctx,width,height} = chart;
  const isEquity=$("#chart-mode").value === "equity";
  const a=isEquity ? "equity" : "portfolio", b=isEquity ? "world" : "reference";
  const firstLabel=isEquity ? "Aktienanteil" : "Gesamtdepot";
  const secondLabel=isEquity ? "MSCI World / TR-Quelle" : `${decimal(current.analysis.equity_target,0)}/${decimal(100-current.analysis.equity_target,0)}-Referenz`;
  $("#line-a").textContent=firstLabel;$("#line-b").textContent=secondLabel;
  const values=p.points.flatMap(row => [row[a],row[b]]);
  const low=Math.min(0,...values), high=Math.max(0,...values);
  const padding=Math.max((high-low)*.12,.5), min=low-padding, max=high+padding;
  const left=45,right=15,top=27,bottom=30,w=width-left-right,h=height-top-bottom;
  const x=i => left+i/(p.points.length-1)*w;
  const y=v => top+(max-v)/(max-min)*h;
  chartGeometry={left,w,count:p.points.length};
  ctx.font="10px system-ui";
  for(let i=0;i<=4;i++) {
    const yy=top+h*i/4;
    ctx.strokeStyle="#edf1f2";ctx.lineWidth=1;ctx.beginPath();ctx.moveTo(left,yy);ctx.lineTo(width-right,yy);ctx.stroke();
    ctx.fillStyle="#8b9aa0";ctx.textAlign="right";ctx.fillText(`${decimal(max-(max-min)*i/4)} %`,left-8,yy+3);
  }
  [0,Math.floor((p.points.length-1)/2),p.points.length-1].forEach((index,i) => {
    ctx.textAlign=i===0?"left":i===2?"right":"center";ctx.fillText(day(p.points[index].date),x(index),height-8);
  });
  ctx.beginPath();ctx.moveTo(x(0),top+h);p.points.forEach((row,i)=>ctx.lineTo(x(i),y(row[a])));ctx.lineTo(x(p.points.length-1),top+h);ctx.closePath();
  const gradient=ctx.createLinearGradient(0,top,0,top+h);gradient.addColorStop(0,"#087f731c");gradient.addColorStop(1,"#087f7300");ctx.fillStyle=gradient;ctx.fill();
  [[b,"#a0b1bf",true],[a,"#087f73",false]].forEach(([key,color,dashed]) => {
    ctx.strokeStyle=color;ctx.lineWidth=2;ctx.setLineDash(dashed?[4,4]:[]);ctx.beginPath();
    p.points.forEach((row,i)=>{if(i)ctx.lineTo(x(i),y(row[key]));else ctx.moveTo(x(i),y(row[key]));});ctx.stroke();ctx.setLineDash([]);
  });
  const selected=hover == null ? p.points.length-1 : hover;
  const row=p.points[selected];
  if(hover != null) {
    ctx.strokeStyle="#bdcdca";ctx.setLineDash([3,3]);ctx.beginPath();ctx.moveTo(x(selected),top);ctx.lineTo(x(selected),top+h);ctx.stroke();ctx.setLineDash([]);
    ctx.fillStyle="#087f73";ctx.beginPath();ctx.arc(x(selected),y(row[a]),4,0,Math.PI*2);ctx.fill();
  }
  $("#chart-detail").textContent=`${day(row.date)} · ${firstLabel}: ${signed(row[a])} % · ${secondLabel}: ${signed(row[b])} % · Differenz: ${signed(row[a]-row[b])} PP`;
}
$("#chart-mode").addEventListener("change",()=>drawLine());
$("#performance-chart").addEventListener("mousemove",event=>{
  if(!chartGeometry)return;
  const {left,w,count}=chartGeometry;
  drawLine(Math.max(0,Math.min(count-1,Math.round((event.offsetX-left)/w*(count-1)))));
});
$("#performance-chart").addEventListener("mouseleave",()=>drawLine());
window.addEventListener("resize",()=>requestAnimationFrame(drawCharts));

async function readFile(file) {
  if(!file)throw new Error("Bitte zuerst eine CSV-Datei auswählen.");
  if(file.size>2_000_000)throw new Error("Die CSV-Datei darf höchstens 2 MB groß sein.");
  const bytes=await file.arrayBuffer();
  try{return new TextDecoder("utf-8",{fatal:true}).decode(bytes);}
  catch{return new TextDecoder("windows-1252").decode(bytes);}
}
function clearImport() {
  csvText="";preview=[];previewRevision=null;
  $("#holdings-file").value="";$("#mapping-panel").hidden=true;$("#preview-panel").hidden=true;
}
$("#holdings-file").addEventListener("change",()=>run(null,async()=>{
  $("#preview-panel").hidden=true;$("#mapping-panel").hidden=true;preview=[];
  csvText=await readFile($("#holdings-file").files[0]);
  const inspection=await api("/api/inspect",{csv:csvText});
  replace($("#mapping-fields"),Object.entries(fields).map(([key,label])=>{
    const wrap=element("label",label),select=element("select");select.id=`map-${key}`;
    select.append(new Option("Nicht vorhanden",""));
    inspection.fields.forEach(name=>select.append(new Option(name,name)));
    select.value=inspection.mapping[key] || "";
    select.addEventListener("change",()=>{$("#preview-panel").hidden=true;preview=[];});
    wrap.append(select);return wrap;
  }));
  const canonical=inspection.fields.includes("as_of") && inspection.fields.includes("name");
  $("#number-format").value=canonical?"decimal":"german";
  $("#valuation-date").value="";
  $("#import-summary").textContent=`${inspection.row_count} Datenzeilen erkannt. ${(inspection.notices || []).join(" ") || "Bei ING-Varianten die Spaltenzuordnung und das Zahlenformat kontrollieren."}`;
  $("#mapping-panel").hidden=false;
},true));
for(const id of ["#number-format","#valuation-date"])$(id).addEventListener("change",()=>{$("#preview-panel").hidden=true;preview=[];});
$("#preview-import").addEventListener("click",()=>run($("#preview-import"),async()=>{
  const mapping=Object.fromEntries(Object.keys(fields).map(k=>[k,$(`#map-${k}`).value]));
  const result=await api("/api/preview",{csv:csvText,mapping,german:$("#number-format").value==="german",valuation_date:$("#valuation-date").value});
  preview=result.holdings;previewRevision=current.state.revision;
  replace($("#preview-table"),preview.map((row,index)=>{
    const tr=element("tr"),cell=element("td"),select=element("select",undefined,"preview-select");
    select.setAttribute("aria-label",`Baustein für ${row.name}`);select.dataset.index=index;
    select.append(new Option("Bitte zuordnen", ""));
    Object.entries(current.buckets).forEach(([key,label])=>select.append(new Option(label,key)));
    select.value=row.bucket;select.addEventListener("change",()=>{preview[index].bucket=select.value;});
    cell.append(select);tr.append(element("td",row.name),element("td",money(row.value)),element("td",day(row.as_of)),cell);return tr;
  }));
  const cash=current.state.holdings.find(h=>h.source==="manual_cash");
  $("#preview-summary").textContent=`${preview.length} Positionen · Importwert ${money(preview.reduce((s,r)=>s+r.value,0))}. ${cash ? `Zusätzlich bleibt dein separater Sicherheitsbetrag von ${money(cash.value)} bestehen. Doppelerfassung vermeiden.` : "Die bisherigen CSV-Positionen werden ersetzt."}`;
  $("#preview-panel").hidden=false;
  $("#preview-panel").scrollIntoView({block:"start"});
},true));
$("#commit-import").addEventListener("click",()=>run($("#commit-import"),async()=>{
  if(!preview.length || preview.some(row=>!row.bucket))throw new Error("Bitte jeder Position einen Baustein zuordnen.");
  current=await api("/api/holdings",{holdings:preview,revision:previewRevision});
  clearImport();render();navigate("holdings");message("Bestände gespeichert. Erfasste Sicherheitsanlagen bitte auf Vollständigkeit prüfen.");
},true));
$("#import-history").addEventListener("click",()=>run($("#import-history"),async()=>{
  if(!$("#history-confirm").checked)throw new Error("Bitte Währung, Geldfluss-Konvention und Referenzdaten bestätigen.");
  const csv=await readFile($("#history-file").files[0]);
  current=await api("/api/history",{csv,currency:current.state.config.currency,revision:current.state.revision});
  $("#history-file").value="";$("#history-confirm").checked=false;render();navigate("overview");message("Bewertungshistorie geprüft und gespeichert.");
},true));
$("#cash-form").addEventListener("submit",event=>{
  event.preventDefault();run(event.submitter,async()=>{
    current=await api("/api/cash",{amount:$("#cash-amount").value,as_of:$("#cash-date").value,revision:current.state.revision});
    render();message("Separater Sicherheitsbetrag aktualisiert.");
  },true);
});
$("#settings-form").addEventListener("submit",event=>{
  event.preventDefault();run(event.submitter,async()=>{
    const targets=Object.fromEntries(Object.keys(current.buckets).map(k=>[k,Number($(`#target-${k}`).value)]));
    const config={currency:$("#currency").value,targets,custom_buckets:current.state.config.custom_buckets,targets_confirmed:$("#targets-confirmed").checked,equity_band_pp:Number($("#equity-band").value),bucket_band_relative:Number($("#bucket-band").value),bucket_band_pp:Number($("#bucket-band-pp").value),reconcile_weekdays:Number($("#reconcile-days").value),drawdown_alert_pct:Number($("#drawdown-limit").value),stale_weekdays:Number($("#stale-days").value)};
    current=await api("/api/config",{config,revision:current.state.revision});
    render();message("Zielaufteilung und Prüfregeln gespeichert. Die Referenz wurde mit diesen Startgewichten neu berechnet.");
  },true);
});
$("#demo-toggle").addEventListener("click",()=>run($("#demo-toggle"),async()=>{
  const nextDemo=!demo;
  const response=await api(nextDemo?"/api/demo":"/api/state");
  demo=nextDemo;current=response;clearImport();render();navigate("overview");
}));
$("#export").addEventListener("click",()=>run($("#export"),async()=>{
  const data=await api("/api/export");
  const blob=new Blob([JSON.stringify(data,null,2)],{type:"application/json"});
  const url=URL.createObjectURL(blob),a=element("a");a.href=url;a.download=`depotblick-${current.analysis.as_of}.json`;
  document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
},true));
$("#today").textContent=new Intl.DateTimeFormat("de-DE",{dateStyle:"long",timeZone:"Europe/Berlin"}).format(new Date());
run(null,async()=>{current=await api("/api/state");render();handleHash();});
