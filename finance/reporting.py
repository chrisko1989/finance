"""Human-readable reports built only from calculated facts and explicit sources."""
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .analytics import analyze


def atomic_write(path, text):
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd,temporary=tempfile.mkstemp(dir=path.parent,prefix=".depotblick-")
    try:
        with os.fdopen(fd,"w",encoding="utf-8") as handle:
            handle.write(text)
        os.replace(temporary,path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def report(state, *, mode="daily", today=None):
    analysis=analyze(state,today=today)
    return {"generated_at":datetime.now(timezone.utc).isoformat(),"mode":mode,"revision":state.get("revision"),"currency":state["config"]["currency"],**analysis}


def markdown(data):
    def fmt(value,suffix=""):
        return "nicht verfügbar" if value is None else f"{value:.2f}{suffix}"
    decision=data["decision"]
    title="Wochenbericht" if data["mode"]=="weekly" else "Tagescheck"
    lines=[f"# Depotblick · {title}","",f"**{decision['label']}**", "",decision["reason"],"",f"Erstellt: {data['generated_at']}; Bewertungsstand: {data['oldest_valuation'] or 'fehlt'} bis {data['latest_valuation'] or 'fehlt'}.",f"Erfasster Depotwert: {fmt(data['total'])} {data['currency']}. Aktienquote: {fmt(data['equity_pct'],' %')}.",f"Referenz: {data['benchmark_label']}. Historie: {data['history_source']}.","","| Zeitraum | Depot | Aktienanteil | World-Referenz | Gemischte Referenz |","|---|---:|---:|---:|---:|"]
    for key,label in [("day","Letztes Tagesintervall"),("week","Letzte 7 Tage"),("month","Laufender Monat"),("year","Seit Jahresbeginn"),("since_start","Seit Historienbeginn")]:
        row=data["periods"].get(key)
        lines.append("| "+label+" | "+" | ".join(fmt(row[k]," %") if row else "nicht verfügbar" for k in ["portfolio","equity","world","reference"])+" |")
    perf=data["performance"]
    lines += ["",f"Maximaler Rückgang: {fmt(perf['max_drawdown'] if perf else None,' %')}; annualisierte Volatilität: {fmt(data['volatility_annualized'],' %')}.","","## Prüfhinweise",""]
    lines += [f"- {a['title']}: {a['detail']}" for a in data["alerts"]] or ["Keine Prüfgrenze überschritten."]
    if data["mode"]=="weekly":
        lines += ["","## Allokation","","| Baustein | Ist | Ziel | Differenz zum Zielbetrag |","|---|---:|---:|---:|"]
        for row in data["allocations"]:
            lines.append(f"| {row['label']} | {fmt(row['actual_pct'],' %')} | {fmt(row['target_pct'],' %')} | {fmt(row['target_gap'])} {data['currency']} |")
        lines += ["","Zielbetragsdifferenzen gelten bei unverändertem Depotwert. Frische Einzahlungen verändern den Nenner; dafür den Sparratenplan verwenden.","","## Fondsvergleich",""]
        for row in data["fund_comparisons"]:
            label=row["label"].replace("\n"," ")
            lines.append(f"- {label}: {fmt(row['return_pct'],' %')}, Referenz {fmt(row['benchmark_pct'],' %')}; {row['start']} bis {row['end']}. Fondskursvergleich, keine persönliche Depotrendite.")
        if not data["fund_comparisons"]:
            lines.append("Keine ausreichenden gemeinsamen bereinigten Kursreihen vorhanden.")
        lines += ["","## Ereignisse und Marktumfeld",""]
        for event in data["open_events"]:
            lines.append(f"- {event['date']}: {event['title']} — erfasste Quelle: {event['url']} (nicht automatisch verifiziert).")
        lines += ["Keine automatische Nachrichten-/Makroanalyse angebunden. Aus Kursbewegungen wird keine Marktursache abgeleitet."]
    lines += ["","## Grenzen","","Geldflüsse werden am Intervallende berücksichtigt. Bei anderen Buchungszeitpunkten sind zusätzliche Bewertungen erforderlich. Volatilität nur bei ausreichend dichter Historie; Jahresrendite nur mit passendem Ausgangswert. Buchgewinne sind keine steuerliche Gewinnermittlung. Kein Verkaufsautomatismus und keine Renditegarantie.",""]
    return "\n".join(lines)


def save_reports(directory,state,mode="daily"):
    result=report(state,mode=mode)
    root=Path(directory)/"reports"
    atomic_write(root/f"latest-{mode}.json",json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+"\n")
    atomic_write(root/f"latest-{mode}.md",markdown(result))
    return result
