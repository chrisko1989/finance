"""Validation and deterministic portfolio calculations; independent of the UI."""
from __future__ import annotations

import copy
import csv
import io
import math
import re
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation


BUCKETS = {
    "world": "MSCI World",
    "quality": "World Quality",
    "momentum": "World Momentum",
    "small": "World Small Caps",
    "em": "Emerging Markets IMI",
    "safe": "Sicherheitsanteil",
}
KNOWN_ISINS = {
    "IE00BP3QZ601": "quality",
    "IE00BP3QZ825": "momentum",
    "IE00BCBJG560": "small",
    "IE00BKM4GZ66": "em",
}
DEFAULT_CONFIG = {
    "currency": "EUR",
    "targets": {"world": 17.5, "quality": 10, "momentum": 10, "small": 7.5, "em": 5, "safe": 50},
    "equity_band_pp": 5,
    "bucket_band_relative": 25,
    "bucket_band_pp": 5,
    "drawdown_alert_pct": 15,
    "stale_weekdays": 2,
    "reconcile_weekdays": 7,
}


class ValidationError(ValueError):
    pass


def number(value, field="Zahl", *, german=False):
    if isinstance(value, bool) or value is None:
        raise ValidationError(f"{field}: gültige Zahl erforderlich.")
    text = str(value).strip().replace("\u00a0", "").replace(" ", "")
    if german:
        text = text.replace(".", "").replace(",", ".")
    try:
        result = float(Decimal(text))
    except (InvalidOperation, ValueError, OverflowError):
        raise ValidationError(f"{field}: ungültige Zahl {str(value)[:40]!r}.") from None
    if not math.isfinite(result) or abs(result) > 1e15:
        raise ValidationError(f"{field}: Zahl liegt außerhalb des zulässigen Bereichs.")
    return result


def iso_date(value, field="Datum", *, today=None):
    text = str(value).strip()
    if re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", text):
        day, month, year = text.split(".")
        text = f"{year}-{month}-{day}"
    try:
        parsed = date.fromisoformat(text)
    except ValueError:
        raise ValidationError(f"{field}: Datum als JJJJ-MM-TT oder TT.MM.JJJJ erforderlich.") from None
    if parsed > (today or date.today()):
        raise ValidationError(f"{field}: Datum liegt in der Zukunft.")
    return parsed.isoformat()


def validate_config(data):
    if not isinstance(data, dict):
        raise ValidationError("Einstellungen müssen ein Objekt sein.")
    config = copy.deepcopy(DEFAULT_CONFIG)
    currency = str(data.get("currency", config["currency"])).upper()
    if currency not in {"EUR", "USD", "CHF", "GBP"}:
        raise ValidationError("Referenzwährung muss EUR, USD, CHF oder GBP sein.")
    config["currency"] = currency
    targets = data.get("targets", config["targets"])
    if not isinstance(targets, dict) or set(targets) != set(BUCKETS):
        raise ValidationError("Zielgewichte müssen alle sechs Bausteine enthalten.")
    config["targets"] = {k: number(v, BUCKETS[k]) for k, v in targets.items()}
    if any(v < 0 or v > 100 for v in config["targets"].values()) or not math.isclose(sum(config["targets"].values()), 100, abs_tol=1e-6):
        raise ValidationError("Zielgewichte müssen nichtnegativ sein und zusammen 100 % ergeben.")
    for key, low, high in [("equity_band_pp", 1, 25), ("bucket_band_relative", 1, 100), ("bucket_band_pp", 0.1, 25), ("drawdown_alert_pct", 1, 90), ("stale_weekdays", 0, 30), ("reconcile_weekdays", 1, 90)]:
        config[key] = number(data.get(key, config[key]), key)
        if not low <= config[key] <= high:
            raise ValidationError(f"{key}: Wert muss zwischen {low} und {high} liegen.")
    if not config["stale_weekdays"].is_integer():
        raise ValidationError("Die Datenalter-Grenze muss eine ganze Anzahl Tage sein.")
    return config


ING_HOLDINGS_FIELDS = [
    "ISIN", "Wertpapiername", "Stück/Nominale", "Einheitskennzeichen",
    "Einstandskurs", "Währung", "Einstandswert", "Währung",
    "Bewertungskurs", "Währung", "Zeit", "Handelsplatz", "Kurswert", "Währung",
    "Gewinn/Verlust", "Währung", "Gewinn/Verlust (%)",
]
ING_UNIQUE_FIELDS = [
    f"Währung ({ING_HOLDINGS_FIELDS[i-1]})" if name == "Währung" else name
    for i, name in enumerate(ING_HOLDINGS_FIELDS)
]


def read_csv(text, *, ing_holdings=False):
    if not isinstance(text, str) or not text.strip():
        raise ValidationError("Die CSV-Datei ist leer.")
    if len(text) > 2_000_000:
        raise ValidationError("Die CSV-Datei darf höchstens 2 MB groß sein.")
    text = text.lstrip("\ufeff")
    # Some broker exports prefix CSV with a delimiter hint or title lines.
    lines = text.splitlines()
    if lines and lines[0].lower().startswith("sep="):
        delimiter = lines.pop(0)[4:5]
        text = "\n".join(lines)
    else:
        first = next((line for line in lines if line.strip()), "")
        delimiter = ";" if first.count(";") > first.count(",") else ","
    if delimiter not in {";", ",", "\t"}:
        raise ValidationError("Nicht unterstütztes CSV-Trennzeichen.")
    try:
        reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
        fields = [str(h).strip() for h in (reader.fieldnames or [])]
        if ing_holdings and [h.casefold() for h in fields] == [h.casefold() for h in ING_HOLDINGS_FIELDS]:
            # Rename before DictReader reads a row: duplicate keys would discard currencies.
            fields = ING_UNIQUE_FIELDS.copy()
        if not fields or any(not h for h in fields) or len(set(fields)) != len(fields):
            raise ValidationError("Die erste CSV-Zeile muss eindeutige, nicht leere Spaltennamen enthalten.")
        reader.fieldnames = fields
        rows = []
        for line, row in enumerate(reader, 2):
            if None in row or any(v is None for v in row.values()):
                raise ValidationError(f"CSV-Zeile {line}: Spaltenanzahl stimmt nicht mit der Kopfzeile überein.")
            if any(v.strip() for v in row.values()):
                rows.append({k: v.strip() for k, v in row.items()})
        if len(rows) > 10000:
            raise ValidationError("Höchstens 10.000 Zeilen je Import erlaubt.")
    except csv.Error as exc:
        raise ValidationError(f"CSV konnte nicht gelesen werden: {exc}") from None
    if not rows:
        raise ValidationError("Die CSV-Datei enthält keine Datenzeilen.")
    return fields, rows


ALIASES = {
    "name": ["name", "wertpapierbezeichnung", "wertpapiername", "bezeichnung", "wertpapier", "name / isin"],
    "isin": ["isin"],
    "quantity": ["quantity", "stück/nominale", "stück / nominale", "stück", "anzahl", "bestand", "nominale"],
    "price": ["price", "kurs", "bewertungskurs", "kurs aktuell", "aktueller kurs"],
    "value": ["value", "kurswert", "wert", "gesamtwert", "kurswert in eur"],
    "currency": ["currency", "währung", "währung kurswert", "währung (kurswert)", "kurswährung"],
    "as_of": ["as_of", "kursdatum", "datum", "stand"],
    "bucket": ["bucket", "baustein"],
    "cost_basis": ["cost_basis", "einstandswert", "kaufwert", "einstandswert in eur"],
}


def inspect_csv(text):
    fields, rows = read_csv(text, ing_holdings=True)
    mapping = {}
    for field, aliases in ALIASES.items():
        mapping[field] = next((h for h in fields if h.casefold() in aliases), "")
    notices = []
    if fields == ING_UNIQUE_FIELDS:
        notices = [
            "ING-Spaltenformat erkannt. Die fünf Währungsspalten werden nach ihrem zugehörigen Wert benannt; für den Gesamtwert gilt Währung (Kurswert).",
            "Zeit wird nicht automatisch als Bewertungsdatum verwendet. Bitte das Datum des Bestandsstands unten ausdrücklich angeben und die Kursstände prüfen.",
            "Einstandswert ist der gesamte Einstandsbetrag. Gewinn/Verlust-Spalten werden nicht als Depot-Renditehistorie importiert.",
        ]
    return {"fields": fields, "mapping": mapping, "sample": rows[:5], "row_count": len(rows), "notices": notices}


def parse_holdings(text, mapping, config, *, german=False, valuation_date=None):
    fields, rows = read_csv(text, ing_holdings=True)
    if not isinstance(mapping, dict):
        raise ValidationError("Spaltenzuordnung fehlt.")
    if any(v and v not in fields for v in mapping.values()):
        raise ValidationError("Eine zugeordnete Spalte fehlt in der CSV-Datei.")
    if not mapping.get("name"):
        raise ValidationError("Bitte die Spalte für die Wertpapierbezeichnung auswählen.")
    if not mapping.get("value") and not (mapping.get("quantity") and mapping.get("price")):
        raise ValidationError("Entweder Gesamtwert oder Stückzahl und Kurs zuordnen.")
    result = []
    for i, row in enumerate(rows, 2):
        def get(key, fallback=""):
            return row.get(mapping.get(key, ""), "") or fallback
        name = get("name")
        if not name or len(name) > 200:
            raise ValidationError(f"Zeile {i}: Name fehlt oder ist zu lang.")
        currency = get("currency", config["currency"]).upper()
        if fields == ING_UNIQUE_FIELDS:
            used_fields = ["value"] if mapping.get("value") else ["price"]
            if mapping.get("cost_basis") and get("cost_basis"):
                used_fields.append("cost_basis")
            for field in used_fields:
                column = mapping.get(field, "")
                currency_column = f"Währung ({column})"
                if currency_column in row and row[currency_column].upper() != config["currency"]:
                    raise ValidationError(f"Zeile {i}: {currency_column} muss {config['currency']} sein. Keine automatische Devisenumrechnung; optionalen Einstandswert gegebenenfalls nicht zuordnen.")
            if mapping.get("quantity") and row["Einheitskennzeichen"].upper() not in {"ST", "ST.", "STK", "STK.", "STÜCK", "STUECK", "STÜCKE"}:
                raise ValidationError(f"Zeile {i}: Einheitskennzeichen kennzeichnet keinen unterstützten Stückbestand. Nominalbestände nur mit Gesamtwert und ohne Stückzahl zuordnen.")
        if currency != config["currency"]:
            raise ValidationError(f"Zeile {i}: {currency} kann nicht mit {config['currency']} summiert werden. Kurswert zuerst in Referenzwährung umrechnen; keine automatische Devisenumrechnung.")
        isin = get("isin").upper()
        bucket = get("bucket", KNOWN_ISINS.get(isin, ""))
        if mapping.get("value"):
            value = number(get("value"), f"Zeile {i}, Gesamtwert", german=german)
        else:
            quantity = number(get("quantity"), f"Zeile {i}, Stückzahl", german=german)
            price = number(get("price"), f"Zeile {i}, Kurs", german=german)
            if quantity < 0 or price < 0:
                raise ValidationError(f"Zeile {i}: Stückzahl und Kurs dürfen nicht negativ sein.")
            value = quantity * price
        if value < 0 or value > 1e12:
            raise ValidationError(f"Zeile {i}: Nur nichtnegative Bestände bis 1 Billion werden unterstützt.")
        as_of = iso_date(get("as_of", valuation_date or ""), f"Zeile {i}, Bewertungsdatum")
        quantity = number(get("quantity"), f"Zeile {i}, Stückzahl", german=german) if mapping.get("quantity") else None
        if quantity is not None and (quantity < 0 or (quantity == 0 and value > 0)):
            raise ValidationError(f"Zeile {i}: Stückzahl passt nicht zum Bestandswert.")
        cost = number(get("cost_basis"), f"Zeile {i}, Einstandswert", german=german) if mapping.get("cost_basis") and get("cost_basis") else None
        if cost is not None and cost < 0:
            raise ValidationError("Einstandswert darf nicht negativ sein.")
        result.append({"name": name, "isin": isin, "bucket": bucket if bucket in BUCKETS else "", "value": value, "currency": currency, "as_of": as_of, "source": "csv", "quantity": quantity, "price": value / quantity if quantity else None, "cost_basis": cost})
    return result


def validate_holdings(rows, config):
    if not isinstance(rows, list) or len(rows) > 10000:
        raise ValidationError("Ungültige Bestandsliste.")
    result = []
    for i, row in enumerate(rows, 1):
        if not isinstance(row, dict) or row.get("bucket") not in BUCKETS:
            raise ValidationError(f"Position {i}: Bitte einen Baustein auswählen.")
        name = str(row.get("name", "")).strip()
        isin = str(row.get("isin", "")).strip().upper()
        if not name or len(name) > 200 or len(isin) > 30:
            raise ValidationError(f"Position {i}: Ungültige Bezeichnung oder ISIN.")
        value = number(row.get("value"), f"Position {i}, Wert")
        if not 0 <= value <= 1e12:
            raise ValidationError("Bestandswerte müssen zwischen 0 und 1 Billion liegen.")
        if row.get("currency") != config["currency"]:
            raise ValidationError("Alle Bestände müssen in der Referenzwährung bewertet sein.")
        quantity = number(row["quantity"], "Stückzahl") if row.get("quantity") is not None else None
        cost = number(row["cost_basis"], "Einstandswert") if row.get("cost_basis") is not None else None
        if quantity is not None and (quantity < 0 or (quantity == 0 and value > 0)) or cost is not None and cost < 0:
            raise ValidationError("Stückzahl oder Einstandswert ungültig.")
        result.append({"name": name, "isin": isin, "bucket": row["bucket"], "value": value, "currency": config["currency"], "as_of": iso_date(row.get("as_of", "")), "source": "csv", "quantity": quantity, "price": value / quantity if quantity else None, "cost_basis": cost})
    return result


HISTORY_FIELDS = ["date", "total_value", "equity_value", "net_flow", "equity_flow", "world_index", "safety_index"]


def parse_history(text):
    fields, rows = read_csv(text)
    if set(fields) != set(HISTORY_FIELDS):
        raise ValidationError("Historie benötigt genau diese Spalten: " + ", ".join(HISTORY_FIELDS))
    result = []
    seen = set()
    for i, row in enumerate(rows, 2):
        record = {"date": iso_date(row["date"], f"Zeile {i}, Datum")}
        if record["date"] in seen:
            raise ValidationError("Doppelte Bewertungstage in der Historie.")
        seen.add(record["date"])
        for key in HISTORY_FIELDS[1:]:
            record[key] = number(row[key], f"Zeile {i}, {key}")
        if record["total_value"] <= 0 or not 0 <= record["equity_value"] <= record["total_value"]:
            raise ValidationError(f"Zeile {i}: Gesamtwert muss positiv sein; Aktienwert muss zwischen 0 und Gesamtwert liegen.")
        if record["world_index"] <= 0 or record["safety_index"] <= 0:
            raise ValidationError("Referenzstände müssen positiv sein.")
        if record["total_value"] - record["net_flow"] < 0 or record["equity_value"] - record["equity_flow"] < 0:
            raise ValidationError("Der Wert vor dem Geldfluss darf nicht negativ sein.")
        if record["total_value"] - record["equity_value"] - (record["net_flow"] - record["equity_flow"]) < 0:
            raise ValidationError("Der Sicherheitswert vor dem Geldfluss darf nicht negativ sein.")
        result.append(record)
    result.sort(key=lambda r: r["date"])
    if len(result) < 2:
        raise ValidationError("Mindestens zwei Bewertungstage sind für einen Renditevergleich erforderlich.")
    if result[0]["net_flow"] or result[0]["equity_flow"]:
        raise ValidationError("Am ersten Bewertungstag müssen beide Geldflüsse 0 sein; dieser Tag ist der Startwert.")
    if any(r["equity_value"] <= 0 for r in result):
        raise ValidationError("Für die Aktienrendite ist an jedem Bewertungstag ein positiver Aktienbestand erforderlich.")
    return result


def performance(history, config):
    if len(history) < 2:
        return None
    first = history[0]
    mix_equity = (100 - config["targets"]["safe"]) / 100
    total_factor = equity_factor = peak = 1.0
    max_drawdown = 0.0
    points = []
    for i, row in enumerate(history):
        if i:
            prev = history[i - 1]
            total_factor *= (row["total_value"] - row["net_flow"]) / prev["total_value"]
            equity_factor *= (row["equity_value"] - row["equity_flow"]) / prev["equity_value"]
        world = row["world_index"] / first["world_index"]
        safety = row["safety_index"] / first["safety_index"]
        mix = mix_equity * world + (1 - mix_equity) * safety
        peak = max(peak, total_factor)
        drawdown = (total_factor / peak - 1) * 100
        max_drawdown = min(max_drawdown, drawdown)
        points.append({"date": row["date"], "portfolio": (total_factor - 1) * 100, "equity": (equity_factor - 1) * 100, "world": (world - 1) * 100, "reference": (mix - 1) * 100, "drawdown": drawdown})
    latest = points[-1]
    if not all(math.isfinite(p[k]) for p in points for k in ["portfolio", "equity", "world", "reference", "drawdown"]):
        raise ValidationError("Die Historie ergibt nicht darstellbare Renditen. Bitte Werte und Geldflüsse prüfen.")
    return {"points": points, "total_return": latest["portfolio"], "equity_return": latest["equity"], "world_return": latest["world"], "reference_return": latest["reference"], "excess_equity_pp": latest["equity"] - latest["world"], "excess_total_pp": latest["portfolio"] - latest["reference"], "max_drawdown": max_drawdown, "current_drawdown": latest["drawdown"], "start": first["date"], "end": history[-1]["date"], "observations": len(history)}


def weekdays_since(start, end):
    days = max((end - start).days, 0)
    weeks, extra = divmod(days, 7)
    return weeks * 5 + sum((start + timedelta(days=i + 1)).weekday() < 5 for i in range(extra))


def analyze(state, *, today=None):
    today = today or date.today()
    config = state["config"]
    holdings = state["holdings"]
    values = {key: sum(h["value"] for h in holdings if h["bucket"] == key) for key in BUCKETS}
    total = sum(values.values())
    alerts = []
    allocations = []
    for key, label in BUCKETS.items():
        actual = values[key] / total * 100 if total else None
        target = config["targets"][key]
        delta = actual - target if actual is not None else None
        outside = delta is not None and (abs(delta) > config.get("bucket_band_pp", 5) or abs(delta) > max(target * config["bucket_band_relative"] / 100, 0.01))
        allocations.append({"key": key, "label": label, "value": values[key], "target_pct": target, "actual_pct": actual, "delta_pp": delta, "target_gap": total * target / 100 - values[key], "outside_band": outside})
        if outside:
            alerts.append({"code": f"drift_{key}", "level": "review", "title": f"{label}: Gewicht prüfen", "detail": f"Ist {actual:.1f} %, Ziel {target:.1f} %. Beim nächsten Rebalancing prüfen; Kosten und Steuern berücksichtigen."})
    equity_pct = (total - values["safe"]) / total * 100 if total else None
    equity_target = 100 - config["targets"]["safe"]
    if equity_pct is not None and abs(equity_pct - equity_target) > config["equity_band_pp"]:
        alerts.insert(0, {"code": "equity_band", "level": "review", "title": "Aktienquote außerhalb des Zielbands", "detail": f"Aktuell {equity_pct:.1f} %, Ziel {equity_target:.1f} % ± {config['equity_band_pp']:g} Prozentpunkte. Sind Sicherheitsanlagen vollständig erfasst?"})
    stale = [h["name"] for h in holdings if h["value"] > 0 and weekdays_since(date.fromisoformat(h["as_of"]), today) > config["stale_weekdays"]]
    if stale:
        alerts.insert(0, {"code": "stale", "level": "data", "title": "Bewertungen aktualisieren", "detail": f"{len(stale)} Position(en) älter als {config['stale_weekdays']:g} Wochentage. Feiertage werden nicht berücksichtigt; der gespeicherte Stand ist veraltet."})
    history = state.get("history", [])
    perf = performance(history, config)
    if history and holdings and all(h["as_of"] == history[-1]["date"] for h in holdings):
        if not math.isclose(history[-1]["total_value"], total, abs_tol=0.01, rel_tol=0):
            alerts.append({"code": "history_mismatch", "level": "data", "title": "Bestände und Historie stimmen nicht überein", "detail": "Für denselben Bewertungstag ergeben sich unterschiedliche Gesamtwerte. Bestandsimport und Historie getrennt prüfen."})
    if perf and weekdays_since(date.fromisoformat(perf["end"]), today) > config["stale_weekdays"]:
        alerts.append({"code": "history_stale", "level": "data", "title": "Renditehistorie ist veraltet", "detail": f"Letzter Bewertungstag: {perf['end']}. Der Rückgang berücksichtigt nur gespeicherte Beobachtungen."})
    if perf and perf["current_drawdown"] <= -10:
        loss = -perf["current_drawdown"]
        stage = "Ausführlicher Portfolio-Check" if loss >= 20 else "Ursachenanalyse" if loss >= 15 else "Beobachten"
        alerts.append({"code": "drawdown", "level": "review", "stage": 20 if loss >= 20 else 15 if loss >= 15 else 10, "title": stage, "detail": f"Rückgang {loss:.1f} %. Prüfung des Risikobudgets; kein automatisches Verkaufssignal."})
    if perf and perf["current_drawdown"] <= -config["drawdown_alert_pct"] and config["drawdown_alert_pct"] not in {10, 15, 20}:
        alerts.append({"code": "drawdown_custom", "level": "review", "title": "Eigene Rückgangsschwelle erreicht", "detail": "Risikobudget prüfen; kein automatisches Verkaufssignal."})
    if not holdings:
        alerts.append({"code": "no_holdings", "level": "data", "title": "Noch keine Depotdaten", "detail": "Bestände importieren und Sicherheitsanlagen ergänzen. Demo-Daten sind separat verfügbar."})
    if not perf:
        alerts.append({"code": "no_history", "level": "data", "title": "Renditevergleich noch offen", "detail": "Mindestens zwei Bewertungen mit Geldflüssen und Referenzständen werden benötigt: automatisch aufbauen oder als Historie importieren. Ein einzelner Depotstand genügt nicht."})
    return {"total": total, "equity_pct": equity_pct, "equity_target": equity_target, "allocations": allocations, "performance": perf, "alerts": alerts, "oldest_valuation": min((h["as_of"] for h in holdings), default=None), "latest_valuation": max((h["as_of"] for h in holdings), default=None), "as_of": today.isoformat(), "demo": state.get("demo", False)}


def demo_state():
    today = date.today()
    config = copy.deepcopy(DEFAULT_CONFIG)
    values = [18600, 11000, 11600, 6900, 4900, 47000]
    holdings = [{"name": BUCKETS[key] + " · Beispiel", "isin": next((i for i, b in KNOWN_ISINS.items() if b == key), ""), "bucket": key, "value": val, "currency": "EUR", "as_of": today.isoformat(), "source": "demo"} for key, val in zip(BUCKETS, values)]
    history = []
    for i in range(53):
        stock = 50000 * (1 + 0.06 * i / 52 + 0.04 * math.sin(i / 5))
        safety = 50000 * (1 + 0.015 * i / 52)
        history.append({"date": (today - timedelta(weeks=52-i)).isoformat(), "total_value": stock + safety, "equity_value": stock, "net_flow": 0, "equity_flow": 0, "world_index": 100 * (1 + 0.065 * i / 52 + 0.03 * math.sin(i / 6)), "safety_index": 100 * (1 + 0.015 * i / 52)})
    # The latest snapshot and history must agree even in the fictional example.
    stock_total = sum(values[:-1])
    history[-1]["equity_value"] = stock_total
    history[-1]["total_value"] = sum(values)
    return {"config": config, "holdings": holdings, "history": history, "demo": True}
