"""Versioned portfolio state and an explicit, idempotent transaction ledger."""
import copy
import hashlib
import json
import math
import re

from .core import DEFAULT_CONFIG, ValidationError, iso_date, number, read_csv, is_equity


def instrument_id(row):
    if row.get("source") == "manual_cash":
        return "CASH"
    if row.get("isin"):
        return row["isin"].upper()
    return "LOCAL-" + hashlib.sha256((row["name"] + "|" + row["bucket"]).encode()).hexdigest()[:12]


def normalize_holdings(rows):
    result, seen = [], set()
    for original in rows:
        row = copy.deepcopy(original)
        row["id"] = instrument_id(row)
        if row["id"] in seen:
            raise ValidationError("Doppelte Position: Bestände derselben ISIN vor dem Import zusammenfassen.")
        seen.add(row["id"])
        for key in ["quantity", "price", "cost_basis"]:
            row.setdefault(key, None)
        result.append(row)
    return result


def normalize_state(state):
    state = copy.deepcopy(state)
    state["schema_version"] = 2
    for key, value in DEFAULT_CONFIG.items():
        state["config"].setdefault(key, copy.deepcopy(value))
    for key in ["stocks", "other_equity"]:
        state["config"]["targets"].setdefault(key, 0)
    state["holdings"] = normalize_holdings(state["holdings"])
    state.setdefault("opening_holdings", copy.deepcopy(state["holdings"]))
    state["opening_holdings"] = normalize_holdings(state["opening_holdings"])
    state.setdefault("transactions", [])
    state.setdefault("quotes", [])
    state.setdefault("automatic_history", [])
    state.setdefault("market", {"provider": "yahoo", "symbols": {}, "benchmark_symbol": "", "benchmark_label": "", "confirmed": False})
    state["market"].setdefault("history_source", "imported" if state["history"] else "automatic")
    state.setdefault("monitor", {"last_run": None, "last_success": None, "last_error": None, "signatures": [], "notices": []})
    state.setdefault("events", [])
    return state


def valuation_fingerprint(state):
    basis = {"opening": state["opening_holdings"], "symbols": state["market"]["symbols"],
             "benchmark": state["market"]["benchmark_symbol"], "currency": state["config"]["currency"]}
    return hashlib.sha256(json.dumps(basis, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def ledger_fingerprint(state, until):
    anchor = max((h["as_of"] for h in state["opening_holdings"]), default=until)
    rows = [t for t in state["transactions"] if anchor < t["date"] <= until]
    return hashlib.sha256(json.dumps(rows, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


TX_FIELDS = {"id", "date", "type", "instrument_id", "quantity", "price", "amount", "fee", "currency"}
TX_TYPES = {"BUY", "SELL", "DEPOSIT", "WITHDRAWAL", "DIVIDEND", "INTEREST", "FEE", "TAX", "SPLIT"}


def parse_transactions(text, state):
    fields, rows = read_csv(text)
    if set(fields) != TX_FIELDS:
        raise ValidationError("Transaktionsspalten: " + ", ".join(sorted(TX_FIELDS)))
    known = {h["id"] for h in state["opening_holdings"]}
    parsed, seen = [], set()
    for row in rows:
        tx = {k: row[k] for k in ["id", "date", "type", "instrument_id", "currency"]}
        if not re.fullmatch(r"[\w.-]{1,80}", tx["id"]) or tx["id"] in seen:
            raise ValidationError("Transaktions-IDs müssen eindeutig sein (Buchstaben, Zahlen, Punkt, Bindestrich).")
        seen.add(tx["id"])
        tx["date"] = iso_date(tx["date"])
        tx["type"] = tx["type"].upper()
        if tx["type"] not in TX_TYPES or tx["currency"] != state["config"]["currency"]:
            raise ValidationError("Nicht unterstützter Transaktionstyp oder abweichende Währung.")
        for key in ["quantity", "price", "amount", "fee"]:
            tx[key] = number(row[key] or 0, key)
            if tx[key] < 0:
                raise ValidationError("Transaktionszahlen sind nichtnegativ; der Typ bestimmt die Richtung.")
        if tx["type"] in {"BUY", "SELL", "DIVIDEND", "SPLIT"}:
            if tx["instrument_id"] not in known or tx["instrument_id"] == "CASH":
                raise ValidationError("Transaktion benötigt eine bekannte Position. Neue Anlagen zuerst mit Bestand 0 importieren.")
        elif tx["instrument_id"]:
            raise ValidationError("Bei Geldkontobuchungen instrument_id leer lassen.")
        if tx["type"] in {"BUY", "SELL"}:
            gross = tx["quantity"] * tx["price"]
            if gross <= 0 or not math.isclose(gross, tx["amount"], abs_tol=0.02, rel_tol=0):
                raise ValidationError("Kauf/Verkauf: Betrag muss Stückzahl × Preis sein; Gebühr separat erfassen.")
            if tx["type"] == "SELL" and tx["fee"] > gross:
                raise ValidationError("Verkaufsgebühr darf den Erlös nicht übersteigen.")
        elif tx["type"] == "SPLIT":
            if tx["quantity"] <= 0 or tx["amount"] or tx["price"] or tx["fee"]:
                raise ValidationError("SPLIT: quantity ist der Faktor (z. B. 2); Preis, Betrag und Gebühr sind 0.")
        else:
            if tx["quantity"] or tx["price"] or tx["amount"] <= 0 or tx["fee"] > tx["amount"]:
                raise ValidationError("Geldbuchungen benötigen einen positiven Betrag; Stückzahl und Preis bleiben 0.")
            if tx["type"] not in {"DIVIDEND", "INTEREST"} and tx["fee"]:
                raise ValidationError("Gebühren für diese Buchung als eigene FEE-Transaktion erfassen.")
        parsed.append(tx)
    return parsed


def merge_transactions(existing, incoming):
    result = {tx["id"]: copy.deepcopy(tx) for tx in existing}
    for tx in incoming:
        if tx["id"] in result and result[tx["id"]] != tx:
            raise ValidationError(f"Transaktion {tx['id']} existiert mit anderem Inhalt. Kein stilles Überschreiben.")
        result[tx["id"]] = tx
    # Stable order retains the explicitly imported order within one day.
    return sorted(result.values(), key=lambda tx: tx["date"])


def replay(state, until):
    """Replay only events after the opening snapshot, never today's units backward."""
    rows = copy.deepcopy(state["opening_holdings"])
    if not rows:
        raise ValidationError("Zuerst einen Ausgangsbestand importieren.")
    dates = {h["as_of"] for h in rows}
    if len(dates) != 1:
        raise ValidationError("Für Transaktionsfortschreibung müssen alle Ausgangsbestände dasselbe Bewertungsdatum haben.")
    anchor = next(iter(dates))
    if until < anchor:
        raise ValidationError("Bewertung liegt vor dem Ausgangsbestand.")
    positions = {h["id"]: h for h in rows}
    cash = positions.get("CASH")
    if not cash:
        cash = {"id": "CASH", "name": "Verrechnungskonto (Transaktionen)", "isin": "", "bucket": "safe", "value": 0.0, "currency": state["config"]["currency"], "as_of": anchor, "source": "manual_cash", "quantity": None, "price": None, "cost_basis": None}
    flows = {}
    for tx in state["transactions"]:
        if not anchor < tx["date"] <= until:
            continue
        kind, amount, fee = tx["type"], tx["amount"], tx["fee"]
        flow = flows.setdefault(tx["date"], {"net_flow": 0.0, "equity_flow": 0.0})
        position = positions.get(tx["instrument_id"])
        if kind in {"BUY", "SELL", "SPLIT", "DIVIDEND"} and position is None:
            raise ValidationError("Transaktion nach dem Ausgangsstand verweist auf eine fehlende Position.")
        equity = position is not None and is_equity(position["bucket"], state["config"])
        if kind in {"BUY", "SELL", "SPLIT"}:
            if position is None:
                raise ValidationError("Transaktion nach dem Ausgangsstand verweist auf eine fehlende Position.")
            if position["quantity"] is None:
                raise ValidationError(f"{position['name']}: Stückzahl im Ausgangsbestand fehlt.")
            quantity = position["quantity"]
            if kind == "SPLIT":
                position["quantity"] *= tx["quantity"]
                if position["price"] is not None:
                    position["price"] /= tx["quantity"]
                continue
            if kind == "BUY":
                position["quantity"] += tx["quantity"]
                cash["value"] -= amount + fee
                if position["cost_basis"] is not None:
                    position["cost_basis"] += amount + fee
                if equity:
                    flow["equity_flow"] += amount + fee
            else:
                if tx["quantity"] > quantity + 1e-9:
                    raise ValidationError("Verkauf überschreitet den vorhandenen Bestand.")
                position["quantity"] = max(0, quantity - tx["quantity"])
                cash["value"] += amount - fee
                if position["cost_basis"] is not None:
                    position["cost_basis"] *= position["quantity"] / quantity
                if equity:
                    flow["equity_flow"] -= amount - fee
        elif kind == "DEPOSIT":
            cash["value"] += amount
            flow["net_flow"] += amount
        elif kind == "WITHDRAWAL":
            cash["value"] -= amount
            flow["net_flow"] -= amount
        elif kind in {"DIVIDEND", "INTEREST"}:
            cash["value"] += amount - fee
            if equity:
                flow["equity_flow"] -= amount - fee
        else:
            cash["value"] -= amount
        if cash["value"] < -0.005:
            raise ValidationError("Verrechnungskonto würde negativ. Ausgangsguthaben, Transaktionsreihenfolge und Einzahlungen prüfen.")
    if cash["value"] or "CASH" in positions:
        positions["CASH"] = cash
    return list(positions.values()), flows, anchor
