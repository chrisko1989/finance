"""Descriptive market context from completed reference prices, never forecasts."""
import math
import statistics
from datetime import date

from .core import weekdays_since


def market_context(state, *, today=None):
    today = today or date.today()
    market = state["market"]
    result = {
        "status": "unavailable", "label": "Marktlage noch nicht auswertbar",
        "benchmark": market["benchmark_label"] or "Keine benannte Referenz",
        "symbol": market["benchmark_symbol"], "as_of": None, "start": None,
        "observations": 0, "source": None, "metrics": {}, "signals": [],
        "limitations": [
            "Beschreibung vergangener bereinigter Referenzkurse; keine Kursprognose oder Bewertung einzelner Unternehmen.",
            "World-Referenz bildet Länder-, Branchen- und Einzelaktienrisiken nur eingeschränkt ab.",
            "Keine Nachrichten-, Zins-, Bewertungs- oder Bilanzdaten angebunden. Schwellen sind Prüfregeln, keine getestete Timing-Strategie.",
        ],
    }
    if not market["confirmed"] or not market["benchmark_symbol"] or not market["benchmark_label"]:
        result["reason"] = "Zuerst eine geprüfte, benannte Referenz festlegen und Kursdaten laden."
        return result
    quotes = sorted((q for q in state["quotes"] if q["symbol"] == market["benchmark_symbol"]
                     and q["currency"] == state["config"]["currency"] and q["adjusted_close"]
                     and q["date"] <= today.isoformat()), key=lambda q: q["date"])
    if not quotes:
        result["reason"] = "Keine bereinigten Kurse der Referenz in Depotwährung verfügbar."
        return result
    recent = quotes[-252:]
    result.update(as_of=recent[-1]["date"], start=recent[0]["date"], observations=len(recent), source=recent[-1]["source"])
    if weekdays_since(date.fromisoformat(recent[-1]["date"]), today) > state["config"]["stale_weekdays"]:
        result["reason"] = "Referenzkurse sind veraltet; daraus wird keine aktuelle Markteinschätzung abgeleitet."
        return result
    gaps = [(date.fromisoformat(b["date"])-date.fromisoformat(a["date"])).days for a, b in zip(recent, recent[1:])]
    # Permit long holiday weekends; weekly series still fail the median-gap check.
    if len(recent) < 21 or not gaps or max(gaps) > 7 or statistics.median(gaps) > 1:
        result["reason"] = "Mindestens 21 ausreichend dichte Tageswerte erforderlich; Datenlücken zuerst prüfen."
        return result
    prices = [q["adjusted_close"] for q in recent]
    returns = [b/a-1 for a, b in zip(prices[-21:], prices[-20:])]
    metrics = {
        "return20_pct": (prices[-1]/prices[-21]-1)*100,
        "volatility20_pct": statistics.stdev(returns)*math.sqrt(252)*100,
        "sma200_gap_pct": (prices[-1]/statistics.mean(prices[-200:])-1)*100 if len(prices) >= 200 else None,
        "drawdown252_pct": (prices[-1]/max(prices)-1)*100 if len(prices) >= 252 else None,
    }
    signals = []
    if metrics["sma200_gap_pct"] is not None and metrics["sma200_gap_pct"] < 0:
        signals.append({"code": "below_sma200", "text": "Bereinigter Referenzkurs liegt unter seinem 200-Tage-Durchschnitt."})
    if metrics["volatility20_pct"] >= 25:
        signals.append({"code": "high_volatility", "text": "Annualisierte Schwankung der letzten 20 Tagesrenditen beträgt mindestens 25 %."})
    if metrics["drawdown252_pct"] is not None and metrics["drawdown252_pct"] <= -15:
        signals.append({"code": "market_drawdown", "text": "Bereinigter Referenzkurs liegt mindestens 15 % unter seinem Hoch der letzten 252 Beobachtungen."})
    result.update(status="complete" if len(prices) >= 252 else "partial", metrics=metrics, signals=signals)
    result["label"] = "Marktrisiken prüfen" if signals else "Keine Markt-Prüfschwelle ausgelöst"
    result["reason"] = ("Vor größeren Käufen oder Umschichtungen Risikobudget prüfen; eine Aufteilung neuer Mittel in Tranchen abwägen. Daraus folgt kein automatischer Verkauf."
                        if signals else "Die berechneten Schwellen liefern derzeit keinen zusätzlichen Risikohinweis. Das ist keine Kaufempfehlung.")
    if len(prices) < 252:
        result["reason"] += " Der Datenzeitraum reicht noch nicht für alle Kennzahlen; fehlende Werte bleiben offen."
    return result
