"""Decision rules, period returns and contribution planning without trade execution."""
import copy
import math
import statistics
from datetime import date, timedelta
from decimal import Decimal, ROUND_FLOOR
from urllib.parse import urlsplit

from .core import ValidationError, analyze as basic_analyze, iso_date, number, read_csv, weekdays_since, bucket_labels
from .ledger import normalize_state, valuation_fingerprint, ledger_fingerprint
from .signals import market_context


def selected_history(state):
    return state["automatic_history"] if state["market"]["history_source"] == "automatic" else state["history"]


def period_metrics(perf):
    if not perf:
        return {"periods": {}, "volatility_annualized": None, "volatility_observations": 0}
    points = perf["points"]
    end = date.fromisoformat(points[-1]["date"])
    boundaries = {"week": end-timedelta(days=7), "month": end.replace(day=1)-timedelta(days=1), "year": date(end.year,1,1)-timedelta(days=1)}
    result = {}
    def compare(start):
        item = {"start": start["date"], "end": points[-1]["date"]}
        for key in ["portfolio", "equity", "world", "reference"]:
            item[key] = ((1+points[-1][key]/100)/(1+start[key]/100)-1)*100 if start[key] > -100 else None
        return item
    for key, boundary in boundaries.items():
        eligible = [p for p in points if date.fromisoformat(p["date"]) <= boundary]
        # Do not silently label a partial year or a much older quote as YTD.
        start = eligible[-1] if eligible and (boundary-date.fromisoformat(eligible[-1]["date"])).days <= 4 else None
        result[key] = compare(start) if start else None
    result["since_start"] = compare(points[0])
    gaps = [(date.fromisoformat(b["date"])-date.fromisoformat(a["date"])).days for a,b in zip(points,points[1:])]
    result["day"] = compare(points[-2]) if len(points)>1 and weekdays_since(date.fromisoformat(points[-2]["date"]),end) <= 1 else None
    returns = [(1+b["portfolio"]/100)/(1+a["portfolio"]/100)-1 for a,b in zip(points,points[1:]) if a["portfolio"] > -100]
    daily = len(returns) >= 20 and len(returns) == len(gaps) and max(gaps) <= 4 and statistics.median(gaps) <= 1
    volatility = statistics.stdev(returns)*math.sqrt(252)*100 if daily else None
    return {"periods": result, "volatility_annualized": volatility, "volatility_observations": len(returns) if daily else 0}


def contribution_plan(state, amount, fee_per_purchase=0, *, allow_empty=False):
    """Allocate a fixed fresh-cash budget to deficits including its denominator effect."""
    amount = number(amount, "Sparrate")
    fee = number(fee_per_purchase, "Gebühr je Kauf")
    if not 0 <= amount <= 1e9 or not 0 <= fee <= 10000:
        raise ValidationError("Ungültiger Sparbetrag oder Gebühr.")
    cents = int((Decimal(str(amount))*100).quantize(Decimal("1")))
    fee_cents = int((Decimal(str(fee))*100).quantize(Decimal("1")))
    amount, fee = cents/100, fee_cents/100
    buckets = bucket_labels(state["config"])
    values = {k: sum(h["value"] for h in state["holdings"] if h["bucket"] == k) for k in buckets}
    total = sum(values.values())
    if total <= 0 and not allow_empty:
        raise ValidationError("Für eine Sparratenplanung zuerst Bestände erfassen.")
    targets = state["config"]["targets"]
    weights = {k: max(0, (total+amount)*targets.get(k,0)/100-values[k]) for k in buckets}
    active = {k for k,v in weights.items() if v>0}
    # Fees can make very small ETF purchases uneconomic; exclude those rows.
    for _ in range(len(buckets)+1):
        fees = fee_cents*len(active-{"safe"})
        budget = max(0,cents-fees)
        gaps = {k:max(0,(total+budget/100)*targets[k]/100-values[k]) for k in active}
        denom = sum(gaps.values())
        allocation = {k:(budget*gaps[k]/denom if denom else 0) for k in active}
        remove = {k for k in active if k != "safe" and allocation[k] <= fee_cents}
        if fees>cents:
            remove |= active-{"safe"}
        if not remove:
            break
        active -= remove
    if not active or not sum(allocation.get(k,0) for k in active):
        return {"amount": amount, "fees": 0, "unallocated_cash": cents/100, "rows": [], "note": "Sparrate für diese Gebühren zu klein; als Guthaben behalten."}
    rounded = {k:int(Decimal(str(allocation[k])).to_integral_value(rounding=ROUND_FLOOR)) for k in active}
    remainder = budget-sum(rounded.values())
    order = sorted(active,key=lambda k:(-(allocation[k]-rounded[k]),k))
    for key in order[:remainder]:
        rounded[key]+=1
    after_total=total+budget/100
    rows=[{"key":k,"label":buckets[k],"amount":rounded.get(k,0)/100,"fee":fee if k in active and k!="safe" else 0,"before_pct":values[k]/total*100 if total else 0,"after_pct":(values[k]+rounded.get(k,0)/100)/after_total*100,"target_pct":targets.get(k,0)} for k in buckets]
    return {"amount": cents/100, "fees": fees/100, "unallocated_cash": 0, "rows": rows, "note": "Nur neue Mittel; keine Verkäufe. Gebühren aus dem Budget. Cent-Beträge, keine Order und keine Stückzahlrundung. Keine Steuerberechnung."}


def fund_comparisons(state):
    market = state["market"]
    benchmark={q["date"]:q for q in state["quotes"] if q["symbol"]==market["benchmark_symbol"] and q["adjusted_close"] and q["currency"]==state["config"]["currency"]}
    result=[]
    for holding in state["holdings"]:
        symbol=market["symbols"].get(holding["id"])
        quotes={q["date"]:q for q in state["quotes"] if q["symbol"]==symbol and q["adjusted_close"] and q["currency"]==state["config"]["currency"]}
        dates=sorted(set(quotes)&set(benchmark))
        if len(dates)<2:
            continue
        start,end=dates[0],dates[-1]
        fund=(quotes[end]["adjusted_close"]/quotes[start]["adjusted_close"]-1)*100
        world=(benchmark[end]["adjusted_close"]/benchmark[start]["adjusted_close"]-1)*100
        result.append({"id":holding["id"],"label":holding["name"],"bucket":holding["bucket"],"start":start,"end":end,"return_pct":fund,"benchmark_pct":world,"difference_pp":fund-world})
    return result


def parse_events(text, state):
    fields,rows=read_csv(text)
    expected={"id","date","instrument_id","type","title","url","status"}
    if set(fields)!=expected:
        raise ValidationError("Ereignisspalten: id,date,instrument_id,type,title,url,status")
    ids={h["id"] for h in state["opening_holdings"]}
    seen=set()
    for row in rows:
        row["date"]=iso_date(row["date"])
        parsed=urlsplit(row["url"])
        if not row["id"] or row["id"] in seen or len(row["id"])>80 or not row["title"] or len(row["title"])>300:
            raise ValidationError("Ereignis benötigt eine eindeutige ID und einen kurzen Titel.")
        if row["instrument_id"] and row["instrument_id"] not in ids:
            raise ValidationError("Ereignisposition ist unbekannt.")
        if row["type"] not in {"fund_closure","index_change","tracking_difference","aum_change","market"} or row["status"] not in {"open","resolved"}:
            raise ValidationError("Ungültiger Ereignistyp oder Status.")
        if parsed.scheme!="https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValidationError("Eine HTTPS-Quellenadresse ohne Zugangsdaten ist erforderlich.")
        seen.add(row["id"])
    return rows


def analyze(state, *, today=None):
    state=normalize_state(state)
    today=today or date.today()
    history=selected_history(state)
    result=basic_analyze({**state,"history":history},today=today)
    result.update(period_metrics(result["performance"]))
    alerts=result["alerts"]
    context=market_context(state,today=today)
    result["market_context"]=context
    if context["signals"]:
        alerts.append({"code":"market_risk","level":"review","title":"Marktlage: zusätzliche Risikohinweise","detail":" ".join(s["text"] for s in context["signals"])+" Keine automatische Handelsentscheidung."})
    if state["holdings"] and not state["config"].get("targets_confirmed"):
        alerts.append({"code":"targets_unconfirmed","level":"review","title":"Zielgewichte für Vorschläge bestätigen","detail":"Neue Bausteine starten mit 0 %. Erst die gesamte Zielaufteilung bewusst festlegen und bestätigen; daraus wird vorher kein Verkaufsvorschlag abgeleitet."})
    if state["automatic_history"]:
        last_auto=state["automatic_history"][-1]
        if state.get("automatic_baseline") != valuation_fingerprint(state) or last_auto.get("ledger_hash") != ledger_fingerprint(state,last_auto["date"]):
            alerts.append({"code":"valuation_changed","level":"data","title":"Bewertungsgrundlage wurde geändert","detail":"Bestand, Kurszuordnung oder frühere Buchungen passen nicht mehr zur gespeicherten automatischen Historie. Neu bewerten; bei nachträglichen Änderungen den bisherigen Zeitraum archivieren."})
    if state["monitor"].get("last_error"):
        alerts.insert(0,{"code":"update_failed","level":"data","title":"Letzte Aktualisierung fehlgeschlagen","detail":state["monitor"]["last_error"]})
    if state["holdings"]:
        oldest=state.get("reconciled_at") or min(h["as_of"] for h in state["opening_holdings"])
        if weekdays_since(date.fromisoformat(oldest),today)>state["config"]["reconcile_weekdays"]:
            alerts.append({"code":"reconcile","level":"data","title":"Depotbestand mit ING abgleichen","detail":f"Ausgangsbestand vom {oldest}. Aktuelle Kurse bestätigen keine aktuellen Stückzahlen oder Bankguthaben."})
        last=min(h["as_of"] for h in state["holdings"])
        if any(t["date"]>last for t in state["transactions"]):
            alerts.append({"code":"transactions_pending","level":"data","title":"Transaktionen noch nicht bewertet","detail":"Neuere Buchungen sind gespeichert; erst nach einer gemeinsamen Kursbewertung fließen sie in den Depotwert ein."})
    if result["performance"] and not state["market"]["benchmark_label"]:
        alerts.append({"code":"reference_name","level":"data","title":"Referenzquelle benennen","detail":"Die importierte Referenz ist noch nicht eindeutig bezeichnet. Index und ETF-Näherung unterscheiden."})
    open_events=[e for e in state["events"] if e["status"]=="open"]
    for event in open_events:
        alerts.append({"code":"event_"+event["id"],"level":"review","title":event["title"],"detail":f"Erfasster Hinweis vom {event['date']}; Quelle manuell prüfen. Keine automatisch bestätigte Ursache.","url":event["url"]})
    data_issues=[a for a in alerts if a["level"]=="data"]
    drift=[a for a in alerts if a["code"].startswith("drift_") or a["code"]=="equity_band"]
    if data_issues:
        decision="DATEN PRÜFEN"
        reason="Die Datenlage reicht für eine verlässliche Anlageentscheidung nicht aus."
    elif drift:
        decision="REBALANCING PRÜFEN"
        reason="Mindestens eine Allokationsgrenze ist überschritten. Neue Mittel bevorzugen; kein automatischer Handel."
    elif alerts:
        decision="BEOBACHTEN"
        reason="Ein Risikohinweis oder erfasstes Ereignis benötigt Prüfung; daraus folgt kein Verkaufssignal."
    else:
        decision="HALTEN"
        reason="Die vorhandenen Daten lösen keine der festgelegten Prüfregeln aus."
    result["decision"]={"label":decision,"reason":reason,"data_complete":not data_issues,"evidence":[{"code":a["code"],"title":a["title"]} for a in alerts],"limitations":["Keine Orderausführung; keine bestätigte Marktursachenanalyse.","Sicherheitsanlagen können ebenfalls verlieren; keine Verlustobergrenze."]}
    result["fund_comparisons"]=fund_comparisons(state)
    result["position_returns"]=[{"id":h["id"],"label":h["name"],"cost_basis":h.get("cost_basis"),"unrealized_gain":h["value"]-h["cost_basis"] if h.get("cost_basis") is not None else None,"unrealized_return_pct":(h["value"]/h["cost_basis"]-1)*100 if h.get("cost_basis") else None} for h in state["holdings"]]
    result["benchmark_label"]=state["market"]["benchmark_label"] or "Importierte Referenz (Quelle nicht benannt)"
    result["history_source"]=state["market"]["history_source"]
    result["open_events"]=open_events
    result["monitor"]=state["monitor"]
    return result
