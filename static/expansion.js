"use strict";

let transactionCSV = "";
let transactionRevision = null;
const periodLabels = {day:"Letztes Tagesintervall",week:"Letzte 7 Tage",month:"Laufender Monat",year:"Seit Jahresbeginn",since_start:"Seit Historienbeginn"};

function renderExpansion() {
  if (!current) return;
  const {state,analysis:a} = current;
  $("#decision-label").textContent = a.decision.label;
  $("#decision-reason").textContent = a.decision.reason;
  $("#decision-label").className = a.decision.data_complete ? "decision-ready" : "decision-data";
  replace($("#period-table"),Object.entries(periodLabels).map(([key,label])=>{
    const row=a.periods[key],tr=element("tr");
    tr.append(element("td",label),...["portfolio","equity","world","reference"].map(k=>element("td",row ? percent(row[k]) : "—")));
    return tr;
  }));
  $("#volatility-note").textContent = a.volatility_annualized == null ? "Volatilität noch nicht verfügbar: mindestens 20 ausreichend dicht aufeinanderfolgende Tagesrenditen erforderlich. Wochen- oder Monatswerte werden nicht als Tageswerte annualisiert." : `Annualisierte Volatilität: ${percent(a.volatility_annualized)} · ${a.volatility_observations} Tagesrenditen · Annahme 252 Handelstage/Jahr.`;
  replace($("#basis-table"),a.position_returns.map(row=>{
    const tr=element("tr");tr.append(element("td",row.label),element("td",row.cost_basis==null?"—":money(row.cost_basis)),element("td",row.unrealized_gain==null?"—":money(row.unrealized_gain)),element("td",percent(row.unrealized_return_pct)));return tr;
  }));
  replace($("#factor-table"),a.fund_comparisons.map(row=>{
    const tr=element("tr");tr.append(element("td",row.label),element("td",`${day(row.start)} – ${day(row.end)}`),element("td",percent(row.return_pct)),element("td",percent(row.benchmark_pct)),element("td",`${signed(row.difference_pp)} PP`));return tr;
  }));
  $("#factor-empty").hidden=!!a.fund_comparisons.length;
  const market=state.market;
  $("#market-provider").value=market.provider;
  $("#benchmark-symbol").value=market.benchmark_symbol;
  $("#benchmark-label").value=market.benchmark_label;
  $("#history-source").value=market.history_source;
  $("#market-confirmed").checked=market.confirmed;
  replace($("#symbol-fields"),state.opening_holdings.filter(h=>h.id!=="CASH").map(row=>{
    const label=element("label",`${row.name} · ${row.id}`);
    const input=element("input");input.dataset.instrument=row.id;input.value=market.symbols[row.id]||"";input.maxLength=32;input.placeholder=row.bucket==="safe"?"Leer lassen für festes Guthaben":"Geprüftes Kurssymbol einschließlich Börse";
    label.append(input);return label;
  }));
  $("#quote-count").textContent=`${state.quotes.length} Kursbeobachtungen gespeichert. ${state.automatic_history.length} automatische Depotbewertungen.`;
  $("#transaction-count").textContent=`${state.transactions.length} Buchungen gespeichert. Positions-IDs werden in der Kurszuordnung angezeigt.`;
  const scheduler=a.scheduler||{};
  $("#scheduler-status").textContent=demo?"Demo-Modus: keine Kursabrufe, Buchungen oder geplanten Läufe.":`${scheduler.running?"Zeitplan läuft in dieser Umgebung":"Zeitplan derzeit nicht aktiv"}. Vorgesehen: Mo–Fr 22:15 Uhr, Wochenbericht Sonntag 18:00 Uhr (Europe/Berlin). Die Cloud-Sitzung muss dafür laufen; ein dauerhafter Hostingbetrieb ist nicht eingerichtet.`;
  $("#last-update").textContent=a.monitor.last_error?`Letzte Aktualisierung fehlgeschlagen: ${a.monitor.last_error}`:a.monitor.last_success?`Letzte erfolgreiche Bewertung: ${new Date(a.monitor.last_success).toLocaleString("de-DE",{timeZone:"Europe/Berlin"})}`:"Noch keine automatische Bewertung durchgeführt.";
  replace($("#notice-history"),[...a.monitor.notices].reverse().slice(0,20).map(notice=>{
    const item=element("div",undefined,"alert-item"),body=element("div");
    body.append(element("h3",notice.title),element("p",new Date(notice.time).toLocaleString("de-DE",{timeZone:"Europe/Berlin"})));
    item.append(element("span",notice.level==="data"?"i":"!",`alert-icon ${notice.level}`),body);return item;
  }));
  if(!a.monitor.notices.length)$("#notice-history").append(element("p","Noch keine protokollierten Hinweise. Ein Prüflauf erstellt sie."));
  replace($("#event-list"),a.open_events.map(event=>{
    const p=element("p",`${day(event.date)} · ${event.title} · `),link=element("a","Erfasste Quelle prüfen");link.href=event.url;link.target="_blank";link.rel="noopener noreferrer";p.append(link);return p;
  }));
  $$("#page-planning input, #page-planning button, #page-automation input, #page-automation select, #page-automation button, #transactions-file, #transactions-preview, #transactions-commit").forEach(node=>{node.disabled=demo;});
  $("#contribution-note").hidden=true;
  $("#contribution-table").replaceChildren();
  $("#transactions-preview-panel").hidden=true;
  $("#report-text").textContent="Bericht laden, um den aktuellen Datenstand zu prüfen.";
}
document.addEventListener("portfolio:render",renderExpansion);

$("#contribution-form").addEventListener("submit",event=>{
  event.preventDefault();run(event.submitter,async()=>{
    const result=await api("/api/contributions",{amount:$("#contribution-amount").value,fee:$("#contribution-fee").value});
    replace($("#contribution-table"),result.rows.map(row=>{
      const tr=element("tr");tr.append(element("td",row.label),element("td",money(row.amount)),element("td",money(row.fee)),element("td",percent(row.after_pct)),element("td",percent(row.target_pct)));return tr;
    }));
    $("#contribution-note").textContent=`${current.analysis.decision.data_complete?"":"Datenlage unvollständig: nur Rechenbeispiel, zunächst Prüfpunkte klären. "}${result.note} Gebühren gesamt: ${money(result.fees)}. Nicht zugewiesenes Guthaben: ${money(result.unallocated_cash)}.`;
    $("#contribution-note").hidden=false;
  },true);
});
$("#market-form").addEventListener("submit",event=>{
  event.preventDefault();run(event.submitter,async()=>{
    const market={provider:$("#market-provider").value,symbols:Object.fromEntries($$("#symbol-fields input").map(node=>[node.dataset.instrument,node.value])),benchmark_symbol:$("#benchmark-symbol").value,benchmark_label:$("#benchmark-label").value,history_source:$("#history-source").value,confirmed:$("#market-confirmed").checked};
    current=await api("/api/market",{market,revision:current.state.revision});render();message("Kurszuordnung gespeichert. Noch keine Kurse abgerufen.");
  },true);
});
async function monitorAction(button,update,useCache=false) {
  await run(button,async()=>{
    const mode=$("#report-mode").value;
    current=await api("/api/monitor",{update,use_cache:useCache,mode,revision:current.state.revision});render();
    const result=await api(`/api/report?mode=${mode}`);
    $("#report-text").textContent=result.markdown;
    if(update&&current.analysis.monitor.last_error)message(current.analysis.monitor.last_error,true);
    else message(update?"Bewertung und lokale Hinweise aktualisiert.":"Prüfung abgeschlossen. Bericht und Hinweise sind lokal gespeichert.");
  },true);
}
$("#refresh-quotes").addEventListener("click",()=>monitorAction($("#refresh-quotes"),true));
$("#revalue-cache").addEventListener("click",()=>monitorAction($("#revalue-cache"),true,true));
$("#run-report").addEventListener("click",()=>monitorAction($("#run-report"),false));
$("#report-mode").addEventListener("change",()=>run(null,async()=>{
  const result=await api(`/api/report?mode=${$("#report-mode").value}`);$("#report-text").textContent=result.markdown;
}));
$("#download-report").addEventListener("click",()=>run($("#download-report"),async()=>{
  const mode=$("#report-mode").value,result=await api(`/api/report?mode=${mode}`);
  const url=URL.createObjectURL(new Blob([result.markdown],{type:"text/markdown;charset=utf-8"}));
  const a=element("a");a.href=url;a.download=`depotblick-${mode}-${current.analysis.as_of}.md`;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
},true));
for(const [button,file,path,label] of [["#quotes-import","#quotes-file","/api/quotes","Kursdatei gespeichert. Die gemeinsame Bewertung kann jetzt gestartet werden."],["#events-import","#events-file","/api/events","Ereignishinweise gespeichert. Quellen wurden nicht automatisch verifiziert."]]) {
  $(button).addEventListener("click",()=>run($(button),async()=>{
    const csv=await readFile($(file).files[0]);current=await api(path,{csv,revision:current.state.revision});$(file).value="";render();message(label);
  },true));
}
$("#transactions-file").addEventListener("change",()=>{$("#transactions-preview-panel").hidden=true;transactionCSV="";});
$("#transactions-preview").addEventListener("click",()=>run($("#transactions-preview"),async()=>{
  transactionCSV=await readFile($("#transactions-file").files[0]);transactionRevision=current.state.revision;
  const result=await api("/api/transactions/preview",{csv:transactionCSV});
  replace($("#transactions-preview-table"),result.transactions.map(row=>{
    const tr=element("tr");tr.append(element("td",row.id),element("td",day(row.date)),element("td",row.type),element("td",row.instrument_id||"Verrechnungskonto"),element("td",money(row.amount)));return tr;
  }));
  $("#transactions-summary").textContent=`${result.added} neue Buchungen, ${result.existing} bereits identisch vorhanden. Transaktionen werden ergänzt, nicht doppelt gebucht. Erst eine neue Kursbewertung aktualisiert den Depotwert.`;
  $("#transactions-preview-panel").hidden=false;
},true));
$("#transactions-commit").addEventListener("click",()=>run($("#transactions-commit"),async()=>{
  if(!transactionCSV)throw new Error("Bitte zuerst die Buchungen prüfen.");
  current=await api("/api/transactions",{csv:transactionCSV,revision:transactionRevision});transactionCSV="";$("#transactions-file").value="";render();message("Buchungen gespeichert. Gemeinsame Kursbewertung aktualisieren.");
},true));
if(current)renderExpansion();

$("#confirm-reconciliation").addEventListener("click",()=>run($("#confirm-reconciliation"),async()=>{
  if(!confirm("Sind Stückzahlen, Verrechnungskonto und alle neuen Buchungen tatsächlich mit ING abgeglichen? Es werden keine Bankdaten abgerufen."))return;
  current=await api("/api/reconcile",{date:current.analysis.as_of,revision:current.state.revision});render();message("Dein manueller Bestandsabgleich wurde vermerkt.");
},true));
$("#restart-history").addEventListener("click",()=>run($("#restart-history"),async()=>{
  if(!confirm("Bisherige automatische Bewertungen archivieren und den Renditevergleich mit der nächsten Bewertung neu beginnen? Die alte Reihe bleibt im Datenexport erhalten."))return;
  current=await api("/api/history/restart",{revision:current.state.revision});render();message("Bisherige automatische Historie archiviert. Die nächste Bewertung setzt einen neuen Startpunkt.");
},true));
