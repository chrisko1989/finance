"""Optional public quote adapter, CSV fallback and atomic valuation preparation."""
import copy
import json
import math
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from .core import ValidationError, iso_date, number, read_csv, weekdays_since
from .ledger import replay, valuation_fingerprint, ledger_fingerprint


def validate_market(data, state):
    if not isinstance(data, dict) or data.get("provider") not in {"csv", "yahoo"}:
        raise ValidationError("Kursquelle muss csv oder yahoo sein.")
    known = {h["id"] for h in state["opening_holdings"] if h["id"] != "CASH"}
    mapping = data.get("symbols", {})
    if not isinstance(mapping, dict) or set(mapping) - known:
        raise ValidationError("Symbolzuordnung enthält unbekannte Positionen.")
    symbols = {k: str(v).strip().upper() for k, v in mapping.items() if str(v).strip()}
    benchmark = str(data.get("benchmark_symbol", "")).strip().upper()
    for symbol in [*symbols.values(), benchmark]:
        if symbol and not re.fullmatch(r"[A-Z0-9^][A-Z0-9.^=_-]{0,31}", symbol):
            raise ValidationError("Ungültiges Kurssymbol.")
    if len(set(symbols.values())) != len(symbols):
        raise ValidationError("Dasselbe Kurssymbol ist mehreren Positionen zugeordnet.")
    label = str(data.get("benchmark_label", "")).strip()
    if len(label) > 150 or (benchmark and not label):
        raise ValidationError("Eine benannte Referenz ist erforderlich, z. B. MSCI-World-ETF statt Index.")
    history_source = data.get("history_source", "automatic")
    if history_source not in {"automatic", "imported"}:
        raise ValidationError("Historienquelle muss automatic oder imported sein.")
    if state["automatic_history"] and benchmark != state["market"]["benchmark_symbol"]:
        raise ValidationError("Die Referenz einer bestehenden automatischen Historie darf nicht ausgetauscht werden. Separaten Datenbestand verwenden.")
    return {"provider": data["provider"], "symbols": symbols, "benchmark_symbol": benchmark, "benchmark_label": label, "confirmed": data.get("confirmed") is True, "history_source": history_source}


def validate_quote(row):
    symbol = str(row.get("symbol", "")).strip().upper()
    if not re.fullmatch(r"[A-Z0-9^][A-Z0-9.^=_-]{0,31}", symbol):
        raise ValidationError("Kurszeile: ungültiges Symbol.")
    close = number(row.get("close"), "Schlusskurs")
    adjusted = number(row["adjusted_close"], "Bereinigter Schlusskurs") if row.get("adjusted_close") not in {None, ""} else None
    ratio = number(row.get("split_ratio") or 1, "Splitfaktor")
    if close <= 0 or (adjusted is not None and adjusted <= 0) or ratio <= 0:
        raise ValidationError("Kurse und Splitfaktor müssen positiv sein.")
    currency = str(row.get("currency", ""))
    if currency not in {"EUR", "USD", "GBP", "CHF"}:
        raise ValidationError("Kurswährung muss EUR, USD, GBP oder CHF sein; Untereinheiten werden nicht automatisch umgerechnet.")
    source = str(row.get("source", "")).strip()
    if not source or len(source) > 160:
        raise ValidationError("Eine kurze Quellenbezeichnung ist erforderlich.")
    return {"symbol": symbol, "date": iso_date(row.get("date", "")), "close": close, "adjusted_close": adjusted, "currency": currency, "source": source, "split_ratio": ratio}


def parse_quotes(text):
    fields, rows = read_csv(text)
    required = {"symbol", "date", "close", "adjusted_close", "currency", "source"}
    if not required <= set(fields) or set(fields) - required - {"split_ratio"}:
        raise ValidationError("Kursspalten: symbol,date,close,adjusted_close,currency,source; optional split_ratio.")
    result = [validate_quote(r) for r in rows]
    if len({(r["symbol"], r["date"]) for r in result}) != len(result):
        raise ValidationError("Doppelte Kursdaten für dasselbe Symbol und Datum.")
    return result


def merge_quotes(existing, incoming):
    quotes = {(r["symbol"], r["date"]): r for r in existing}
    quotes.update({(r["symbol"], r["date"]): r for r in incoming})
    result = sorted(quotes.values(), key=lambda r: (r["date"], r["symbol"]))
    if len(result) > 60000:
        raise ValidationError("Kursarchiv überschreitet 60.000 Beobachtungen. Datenbestand vor Erweiterung prüfen.")
    return result


def parse_yahoo(document, requested_symbol, *, now=None):
    now = now or datetime.now(timezone.utc)
    try:
        chart = document["chart"]
        if chart.get("error") or not chart.get("result"):
            raise ValidationError("Yahoo lieferte keine gültige Kursreihe.")
        result = chart["result"][0]
        meta = result["meta"]
        if meta["symbol"].upper() != requested_symbol.upper():
            raise ValidationError("Die gelieferte Symbolkennung stimmt nicht mit der Anfrage überein.")
        currency = meta["currency"]
        zone = ZoneInfo(meta.get("exchangeTimezoneName", "UTC"))
        today = now.astimezone(zone).date()
        close = result["indicators"]["quote"][0]["close"]
        adjusted = result["indicators"].get("adjclose", [{}])[0].get("adjclose", [])
        regular_end = meta.get("currentTradingPeriod", {}).get("regular", {}).get("end")
        splits = {}
        for split in result.get("events", {}).get("splits", {}).values():
            split_day = datetime.fromtimestamp(split["date"], timezone.utc).astimezone(zone).date().isoformat()
            splits[split_day] = split["numerator"] / split["denominator"]
        rows = []
        for i, stamp in enumerate(result["timestamp"]):
            day = datetime.fromtimestamp(stamp, timezone.utc).astimezone(zone).date()
            if day > today or (day == today and (regular_end is None or now.timestamp() < regular_end + 900)):
                continue  # No still-forming daily candle.
            if i >= len(close) or close[i] is None:
                continue
            rows.append(validate_quote({"symbol": requested_symbol, "date": day.isoformat(), "close": close[i], "adjusted_close": adjusted[i] if i < len(adjusted) else None, "currency": currency, "source": "Yahoo Finance (öffentliche Kursabfrage)", "split_ratio": splits.get(day.isoformat(), 1)}))
    except (KeyError, IndexError, TypeError, ZeroDivisionError, ValueError) as exc:
        if isinstance(exc, ValidationError):
            raise
        raise ValidationError("Nicht unterstütztes Yahoo-Antwortformat.") from None
    if not rows:
        raise ValidationError("Keine abgeschlossenen Handelstage verfügbar.")
    return rows


def fetch_yahoo(symbol):
    if not re.fullmatch(r"[A-Z0-9^][A-Z0-9.^=_-]{0,31}", symbol):
        raise ValidationError("Ungültiges Symbol.")
    url = "https://query1.finance.yahoo.com/v8/finance/chart/" + urllib.parse.quote(symbol, safe="") + "?range=2y&interval=1d&events=div%2Csplits"
    request = urllib.request.Request(url, headers={"User-Agent": "Depotblick/0.2", "Accept": "application/json"})
    for attempt in range(2):
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                raw = response.read(4_000_001)
                if len(raw) > 4_000_000:
                    raise ValidationError("Kursantwort überschreitet die erlaubte Größe.")
                document = json.loads(raw)
            return parse_yahoo(document, symbol)
        except urllib.error.HTTPError as exc:
            if exc.code >= 500 and attempt == 0:
                time.sleep(0.5)
                continue
            raise ValidationError(f"Kursabruf {symbol}: HTTP {exc.code}. Keine Aktualisierung; Quelle oder Netzwerkfreigabe prüfen.") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise ValidationError(f"Kursabruf {symbol} nicht erreichbar. Netzwerkfreigabe für query1.finance.yahoo.com prüfen; gespeicherte Bewertungen bleiben erhalten.") from None
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise ValidationError("Kursanbieter lieferte keine gültigen JSON-Daten.") from None


def collect_quotes(state, fetcher=fetch_yahoo):
    market = state["market"]
    if market["provider"] != "yahoo" or not market["confirmed"]:
        raise ValidationError("Yahoo aktivieren und Symbolzuordnung bestätigen, bevor Kurse abgerufen werden.")
    symbols = sorted(set(market["symbols"].values()) | {market["benchmark_symbol"]})
    if "" in symbols or len(symbols) > 30:
        raise ValidationError("Eine Referenz und höchstens 30 eindeutige Kurssymbole sind erforderlich.")
    result = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        for rows in pool.map(fetcher, symbols):
            result.extend(rows)
    return result


def revalue(state, *, today=None):
    today = today or date.today()
    market = state["market"]
    if not market["confirmed"] or not market["benchmark_symbol"]:
        raise ValidationError("Symbolzuordnung und benannte Total-Return-Referenz müssen bestätigt sein.")
    baseline = valuation_fingerprint(state)
    if state["automatic_history"] and state.get("automatic_baseline") != baseline:
        raise ValidationError("Ausgangsbestand oder Kurszuordnung wurde geändert. Alten Bewertungszeitraum archivieren und einen neuen starten; sonst könnten Änderungen als Rendite erscheinen.")
    projected, _, anchor = replay(state, today.isoformat())
    mapped = market["symbols"]
    required = {market["benchmark_symbol"]}
    for row in projected:
        if row["id"] == "CASH" or (row["bucket"] == "safe" and row["id"] not in mapped):
            continue
        if row["quantity"] is None or row["id"] not in mapped:
            raise ValidationError(f"{row['name']}: Stückzahl oder bestätigtes Kurssymbol fehlt.")
        required.add(mapped[row["id"]])
    by_symbol = {symbol: {} for symbol in required}
    for quote in state["quotes"]:
        if quote["symbol"] in required and anchor <= quote["date"] <= today.isoformat():
            if quote["currency"] != state["config"]["currency"]:
                raise ValidationError("Kurs- und Referenzwährung weichen ab. Keine automatische Devisenumrechnung.")
            by_symbol[quote["symbol"]][quote["date"]] = quote
    common = set.intersection(*(set(values) for values in by_symbol.values()))
    if not common:
        raise ValidationError("Kein gemeinsamer Bewertungstag für alle Positionen und die Referenz vorhanden.")
    day = max(common)
    if weekdays_since(date.fromisoformat(day), today) > state["config"]["stale_weekdays"]:
        raise ValidationError("Gemeinsamer Kursstand ist zu alt. Bewertungen werden nicht als aktuell ausgegeben.")
    positions, flows, anchor = replay(state, day)
    for position in positions:
        symbol = mapped.get(position["id"])
        if symbol:
            for quote in state["quotes"]:
                if quote["symbol"] == symbol and anchor < quote["date"] <= day and quote.get("split_ratio", 1) != 1:
                    matching = [t for t in state["transactions"] if t["type"] == "SPLIT" and t["instrument_id"] == position["id"] and t["date"] == quote["date"] and math.isclose(t["quantity"], quote["split_ratio"])]
                    if not matching:
                        raise ValidationError("Ein Aktiensplit benötigt eine passende SPLIT-Buchung oder einen neuen Ausgangsbestand.")
            quote = by_symbol[symbol][day]
            position["price"] = quote["close"]
            position["value"] = position["quantity"] * quote["close"]
            position["valuation_source"] = quote["source"]
        else:
            position["valuation_source"] = "Fortgeschriebener Nutzerbestand; kein Bankabruf"
        position["as_of"] = day
        position["position_as_of"] = anchor
    benchmark = by_symbol[market["benchmark_symbol"]][day]
    if benchmark["adjusted_close"] is None:
        raise ValidationError("Für die Referenz fehlen ausschüttungsbereinigte Kurse.")
    total = sum(p["value"] for p in positions)
    equity = sum(p["value"] for p in positions if p["bucket"] != "safe")
    if total <= 0 or equity <= 0:
        raise ValidationError("Für diesen Renditevergleich ist ein positiver Gesamt- und Aktienbestand erforderlich.")
    history = state["automatic_history"]
    if history and history[-1]["date"] > day:
        raise ValidationError("Kursstand liegt vor der letzten gespeicherten Bewertung.")
    if history and history[-1]["date"] == day and history[-1].get("origin") != "generated":
        raise ValidationError("An diesem Tag besteht bereits eine importierte Bewertung. Sie wird nicht automatisch überschrieben.")
    previous = history[-2] if history and history[-1]["date"] == day and len(history) > 1 else history[-1] if history and history[-1]["date"] != day else None
    if previous and previous.get("ledger_hash") != ledger_fingerprint(state, previous["date"]):
        raise ValidationError("Nachträgliche Buchungen verändern einen früheren Bewertungstag. Bisherigen Zeitraum archivieren und neu beginnen, damit Geldflüsse nicht als Rendite erscheinen.")
    selected = [v for d, v in flows.items() if previous and previous["date"] < d <= day]
    reference = benchmark["adjusted_close"]
    if previous:
        past = next((q for q in state["quotes"] if q["symbol"] == market["benchmark_symbol"] and q["date"] == previous["date"]), None)
        if not past or not past["adjusted_close"]:
            raise ValidationError("Referenzkurs am letzten Bewertungstag fehlt; keine unbelegte Verkettung.")
        reference = previous["world_index"] * benchmark["adjusted_close"] / past["adjusted_close"]
    row = {"date": day, "total_value": total, "equity_value": equity, "net_flow": sum(v["net_flow"] for v in selected), "equity_flow": sum(v["equity_flow"] for v in selected), "world_index": reference, "safety_index": previous["safety_index"] if previous else 100, "origin": "generated", "ledger_hash": ledger_fingerprint(state, day)}
    if history and history[-1]["date"] == day:
        history[-1] = row
    else:
        history.append(row)
    if len(history) > 10000:
        raise ValidationError("Historie überschreitet 10.000 Bewertungen.")
    state["holdings"] = positions
    state["automatic_baseline"] = baseline
    state["automatic_opening"] = copy.deepcopy(state["opening_holdings"])
    return day
