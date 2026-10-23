"""Cash-funded allocation proposals; no security selection, orders or forecasts."""
from decimal import Decimal, ROUND_FLOOR

from .analytics import analyze, contribution_plan
from .core import ValidationError, bucket_labels, number, outside_band
from .ledger import normalize_state


def cents(value):
    return int((Decimal(str(value))*100).quantize(Decimal("1")))


def trade_plan(state, amount=0, fee=0, minimum=25, allow_sales=False, *, today=None):
    state = normalize_state(state)
    amount, fee, minimum = (number(value, name) for value, name in
                            [(amount, "Neue Einzahlung"), (fee, "Gebühr"), (minimum, "Mindestbetrag")])
    if not 0 <= amount <= 1e9 or not 0 <= fee <= 10000 or not 0 <= minimum <= 1e9 or not isinstance(allow_sales, bool):
        raise ValidationError("Ungültige Planungswerte.")
    fresh, fee_cents, min_cents = cents(amount), cents(fee), cents(minimum)
    analysis = analyze(state, today=today)
    reasons = [a["title"] for a in analysis["alerts"] if a["level"] == "data"]
    if not state["config"]["targets_confirmed"]:
        reasons.append("Zielgewichte unter Strategie & Regeln festlegen und ausdrücklich für Vorschläge bestätigen.")
    if state.get("demo"):
        reasons.append("Demo verlassen; Vorschläge werden nur für den eigenen Datenbestand berechnet.")
    result = {
        "status": "blocked" if reasons else "ready", "reasons": reasons, "rows": [],
        "valuation_date": analysis["oldest_valuation"], "fresh_cash": fresh/100,
        "estimated_fees": 0, "after_total": None, "remaining_deviations": [],
        "market_context": analysis["market_context"],
        "warnings": [
            "Regelbasierte Vorschläge zur Zielaufteilung; keine Aussage, ob eine Aktie günstig oder teuer ist.",
            "Beträge je Baustein, keine Stückzahlen oder automatische Auswahl innerhalb mehrerer Wertpapiere.",
            "Eine feste Gebührenannahme je Kauf-/Verkaufszeile. Mehrere Orders, Spreads, Steuern und Stückrundung verändern das Ergebnis.",
            "Verkaufserlöse stehen in dieser Rechnung sofort zur Verfügung; beim Broker Abwicklung und verfügbares Guthaben prüfen.",
        ],
    }
    if reasons:
        return result
    config = state["config"]
    labels = bucket_labels(config)
    values = {k: sum(cents(h["value"]) for h in state["holdings"] if h["bucket"] == k) for k in labels}
    total = sum(values.values())
    if total <= 0:
        result.update(status="blocked", reasons=["Positiver Depotwert erforderlich."])
        return result
    targets = config["targets"]

    def allocate(base, budget):
        synthetic = {"config": config, "holdings": [{"bucket": k, "value": v/100} for k, v in base.items()]}
        plan = contribution_plan(synthetic, budget/100, fee_cents/100, allow_empty=True)
        buys = {r["key"]: cents(r["amount"]) for r in plan["rows"]
                if r["key"] != "safe" and cents(r["amount"]) >= min_cents and cents(r["amount"]) > fee_cents}
        fees = len(buys)*fee_cents
        cash = budget-sum(buys.values())-fees
        if cash < 0:
            raise ValidationError("Plan überschreitet verfügbares Guthaben; Eingaben prüfen.")
        after = dict(base)
        for key, value in buys.items():
            after[key] += value
        after["safe"] += cash
        return after, buys, fees

    # Always try new cash first. A sale is considered only if a band still fails.
    first_after, _, _ = allocate(values, fresh)
    first_total = sum(first_after.values())
    base, budget, sell_fees = dict(values), fresh, 0
    sales = {}
    released = 0
    if allow_sales:
        for key in labels:
            actual = first_after[key]/first_total*100
            if actual <= targets[key] or not outside_band(actual, targets[key], config):
                continue
            desired = Decimal(total+fresh)*Decimal(str(targets[key]))/100
            excess = max(0, int((Decimal(values[key])-desired).to_integral_value(rounding=ROUND_FLOOR)))
            if key == "safe":
                available = sum(cents(h["value"]) for h in state["holdings"] if h.get("source") == "manual_cash")
                released = min(excess, available)
                if released < min_cents:
                    released = 0
                base[key] -= released
                budget += released
                if excess > released:
                    result["warnings"].append("Nicht das gesamte Übergewicht im Sicherheitsanteil ist als verfügbares Verrechnungsguthaben erfasst. Daraus wird kein automatischer Wertpapierverkauf abgeleitet.")
            elif excess >= min_cents and excess > fee_cents:
                sales[key] = excess
                base[key] -= excess
                budget += excess-fee_cents
                sell_fees += fee_cents
    after, buys, buy_fees = allocate(base, budget)
    after_total = sum(after.values())
    if after_total <= 0 or after_total != total+fresh-sell_fees-buy_fees:
        raise ValidationError("Plan kann nicht konsistent finanziert werden.")
    for key, gross in sales.items():
        result["rows"].append({"key": key, "action": "sell", "label": labels[key], "amount": gross/100,
                               "fee": fee_cents/100, "reason": "Übergewicht bleibt trotz geplanter Einzahlung außerhalb des Zielbands; Reduzierung prüfen. Steuerwirkung vor Verkauf klären."})
    for key, gross in buys.items():
        result["rows"].append({"key": key, "action": "buy", "label": labels[key], "amount": gross/100,
                               "fee": fee_cents/100, "reason": "Untergewicht gegenüber deiner bestätigten Zielaufteilung; neue Mittel und gegebenenfalls geplante Erlöse zuweisen."})
    cash_change = after["safe"]-values["safe"]
    if cash_change:
        result["rows"].append({"key": "safe", "action": "reserve" if cash_change > 0 else "use_cash",
                               "label": labels["safe"], "amount": abs(cash_change)/100, "fee": 0,
                               "reason": "Änderung des Verrechnungsguthabens; Zielreserve, Mindestbetrag und Gebühren berücksichtigt. Kein Wertpapierauftrag."})
    for row in result["rows"]:
        key = row["key"]
        row.update(before_pct=values[key]/total*100, after_pct=after[key]/after_total*100, target_pct=targets[key])
        row["positions"] = [h["name"] for h in state["holdings"] if h["bucket"] == key and h["value"] > 0]
    for key in labels:
        actual = after[key]/after_total*100
        if outside_band(actual, targets[key], config):
            result["remaining_deviations"].append({"key": key, "label": labels[key], "after_pct": actual, "target_pct": targets[key]})
    result.update(estimated_fees=(sell_fees+buy_fees)/100, after_total=after_total/100,
                  status="ready" if result["rows"] else "no_change")
    if not allow_sales and result["remaining_deviations"]:
        result["warnings"].append("Restabweichungen bleiben bestehen. Verkäufe und Einsatz vorhandenen Guthabens sind für diese Rechnung ausgeschaltet.")
    if analysis["market_context"]["signals"]:
        result["warnings"].append(analysis["market_context"]["reason"])
    elif analysis["market_context"]["status"] != "complete":
        result["warnings"].append("Marktlage nur teilweise oder nicht auswertbar. Der Vorschlag basiert auf deiner Zielaufteilung, nicht auf einem Marktsignal.")
    return result
