"use strict";

const tradeLabels = {buy:"Kauf prüfen",sell:"Verkauf prüfen",reserve:"Guthaben erhöhen",use_cash:"Guthaben einsetzen"};

function renderDecisions() {
  if (!current) return;
  const {state,analysis} = current;
  replace($("#custom-bucket-list"),Object.entries(state.config.custom_buckets).map(([key,item])=>{
    const row=element("div",undefined,"custom-bucket-row");
    row.append(element("span",`${item.label} · ${item.asset_class==="equity"?"Aktienanlage":"Andere Anlage"}`));
    const button=element("button","Entfernen","text-button");button.type="button";
    button.disabled=demo;
    button.addEventListener("click",()=>run(button,async()=>{
      current=await api("/api/buckets/remove",{key,revision:current.state.revision});
      clearImport();render();message("Unbenutzter Baustein entfernt. Zielaufteilung erneut bestätigen.");
    },true));
    row.append(button);return row;
  }));
  $$("#bucket-form input, #bucket-form select, #bucket-form button, #trade-form input, #trade-form button").forEach(node=>{node.disabled=demo;});
  $("#trade-result").hidden=true;
  const context=analysis.market_context;
  $("#market-context-label").textContent=context.label;
  $("#market-context-source").textContent=`${context.benchmark} · ${context.as_of?`Stand ${day(context.as_of)} · ${context.observations} Tageswerte · ${context.source}`:"Kursdaten fehlen"}`;
  $("#market-context-reason").textContent=context.reason;
  const metrics=[
    ["return20_pct","Veränderung über 20 Tagesintervalle","Bereinigter Referenzkurs gegenüber 20 Beobachtungen zuvor"],
    ["volatility20_pct","Annualisierte Schwankung","20 Tagesrenditen · Stichprobenstandardabweichung × √252 · Prüfgrenze 25 %"],
    ["sma200_gap_pct","Abstand zum 200-Tage-Durchschnitt","Bereinigter Kurs relativ zum Mittel der letzten 200 Tageswerte"],
    ["drawdown252_pct","Rückgang vom Hoch","Höchster bereinigter Kurs der letzten 252 Tageswerte · Prüfgrenze −15 %"]
  ];
  replace($("#market-context-table"),metrics.map(([key,label,description])=>{
    const tr=element("tr");tr.append(element("td",label),element("td",percent(context.metrics[key])),element("td",description));return tr;
  }));
  replace($("#market-context-signals"),context.signals.map(signal=>element("li",signal.text)));
}

document.addEventListener("portfolio:render",renderDecisions);

$("#bucket-form").addEventListener("submit",event=>{
  event.preventDefault();run(event.submitter,async()=>{
    current=await api("/api/buckets",{label:$("#bucket-name").value,asset_class:$("#bucket-class").value,revision:current.state.revision});
    $("#bucket-name").value="";clearImport();render();
    message("Baustein angelegt. Zielgewichte auf insgesamt 100 % verteilen und bestätigen; danach steht er im Import zur Auswahl.");
  },true);
});

$("#trade-form").addEventListener("submit",event=>{
  event.preventDefault();run(event.submitter,async()=>{
    const result=await api("/api/recommendations",{amount:$("#trade-amount").value,fee:$("#trade-fee").value,minimum:$("#trade-minimum").value,allow_sales:$("#trade-sales").checked});
    $("#trade-status").textContent=result.status==="blocked"?"Zuerst Daten und Zielaufteilung prüfen. Es werden keine konkreten Handelsbeträge vorgeschlagen.":`${result.status==="no_change"?"Kein Vorschlag innerhalb dieser Vorgaben.":"Vorschläge zur Prüfung, keine Orders."} Bewertungsstand ${day(result.valuation_date)} · Geschätzte Gebühren ${money(result.estimated_fees)} · Depotwert nach Einzahlung und Gebühren ${money(result.after_total)}.`;
    replace($("#trade-reasons"),result.reasons.map(reason=>element("li",reason)));
    replace($("#trade-table"),result.rows.map(row=>{
      const tr=element("tr"),name=element("td",`${tradeLabels[row.action]} · ${row.label}`);
      if(row.positions.length)name.append(element("small",`Erfasste Positionen: ${row.positions.join(", ")}`,"holding-isin"));
      tr.append(name,element("td",money(row.amount)),element("td",money(row.fee)),element("td",`${percent(row.after_pct)} / ${percent(row.target_pct)}`),element("td",row.reason));return tr;
    }));
    $("#trade-remaining").textContent=result.status==="blocked"?"":result.remaining_deviations.length?`Restabweichungen außerhalb des Zielbands: ${result.remaining_deviations.map(row=>`${row.label} ${percent(row.after_pct)} bei ${percent(row.target_pct)} Ziel`).join("; ")}. Mindestbeträge, Gebühren und verfügbare Mittel können eine vollständige Angleichung verhindern.`:"Die berechnete Aufteilung liegt innerhalb der festgelegten Zielbänder.";
    replace($("#trade-warnings"),result.warnings.map(warning=>element("li",warning)));
    $("#trade-result").hidden=false;
  },true);
});

if(current)renderDecisions();
