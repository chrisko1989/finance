"""Small local HTTP application, using only Python's standard library."""
from __future__ import annotations

import argparse
import copy
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

from .core import BUCKETS, ValidationError, demo_state, inspect_csv, iso_date, number, parse_history, parse_holdings, validate_config, validate_holdings
from .analytics import analyze, contribution_plan, parse_events
from .ledger import normalize_state, normalize_holdings, parse_transactions, merge_transactions, replay
from .market import validate_market, parse_quotes, merge_quotes
from .monitor import run_once, scheduler_status
from .reporting import report, markdown
from .storage import ConflictError, Store

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
MAX_REQUEST = 2_500_000


def present(state, directory=None):
    state = normalize_state(state)
    analysis = analyze(state)
    analysis["scheduler"] = scheduler_status(directory) if directory else {"running": False}
    return {"state": state, "analysis": analysis, "buckets": BUCKETS}


def make_server(store, port=8000):
    class Handler(BaseHTTPRequestHandler):
        server_version = "FinanceMonitor/0.2"

        def log_message(self, format, *args):
            # Do not put private financial data or request bodies in logs.
            return

        def respond(self, status, body, content_type="application/json; charset=utf-8"):
            if not isinstance(body, bytes):
                body = json.dumps(body, ensure_ascii=False, allow_nan=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
            self.end_headers()
            self.wfile.write(body)

        def allowed_request(self):
            host = self.headers.get("Host", "")
            allowed = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
            if host not in allowed:
                self.respond(403, {"error": "Dieser Dienst ist nur für lokalen Zugriff vorgesehen."})
                return False
            origin = self.headers.get("Origin")
            if origin and origin != f"http://{host}":
                self.respond(403, {"error": "Anfragen von einer anderen Webseite sind nicht erlaubt."})
                return False
            return True

        def do_GET(self):
            if not self.allowed_request():
                return
            path = urlsplit(self.path).path
            if path == "/api/health":
                self.respond(200, {"status": "ok", "version": "0.2.0"})
            elif path == "/api/state":
                self.respond(200, present(store.read(), store.directory))
            elif path == "/api/demo":
                self.respond(200, present(demo_state()))
            elif path == "/api/export":
                self.respond(200, {**store.read(), "schema_version": 2})
            elif path == "/api/report":
                mode = parse_qs(urlsplit(self.path).query).get("mode", ["daily"])[0]
                if mode not in {"daily", "weekly"}:
                    self.respond(400, {"error": "Unbekannter Berichtstyp."})
                    return
                result = report(store.read(), mode=mode)
                self.respond(200, {"report": result, "markdown": markdown(result)})
            elif path in {"/", "/app.js", "/expansion.js", "/style.css"}:
                filename = {"/": "index.html", "/app.js": "app.js", "/expansion.js": "expansion.js", "/style.css": "style.css"}[path]
                content_type = {"/": "text/html; charset=utf-8", "/app.js": "text/javascript; charset=utf-8", "/expansion.js": "text/javascript; charset=utf-8", "/style.css": "text/css; charset=utf-8"}[path]
                self.respond(200, (STATIC / filename).read_bytes(), content_type)
            elif path in {"/examples/holdings.csv", "/examples/history.csv", "/examples/ing-style.csv", "/examples/transactions.csv", "/examples/quotes.csv", "/examples/events.csv"}:
                self.respond(200, (ROOT / path.lstrip("/")).read_bytes(), "text/csv; charset=utf-8")
            else:
                self.respond(404, {"error": "Nicht gefunden."})

        def do_POST(self):
            if not self.allowed_request():
                return
            if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
                self.respond(415, {"error": "application/json erforderlich."})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self.respond(400, {"error": "Ungültige Datenlänge."})
                return
            if not 0 < length <= MAX_REQUEST or self.headers.get("Transfer-Encoding"):
                self.respond(413, {"error": "Anfrage fehlt oder ist zu groß (max. 2,5 MB)."})
                return
            try:
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise ValidationError("JSON-Objekt erforderlich.")
                path = urlsplit(self.path).path
                if path == "/api/inspect":
                    self.respond(200, inspect_csv(data.get("csv")))
                    return
                if path == "/api/preview":
                    rows = parse_holdings(data.get("csv"), data.get("mapping"), store.read()["config"], german=data.get("german", False), valuation_date=data.get("valuation_date"))
                    self.respond(200, {"holdings": rows})
                    return
                if path == "/api/contributions":
                    self.respond(200, contribution_plan(store.read(), data.get("amount"), data.get("fee", 0)))
                    return
                if path == "/api/monitor":
                    if data.get("revision") != store.read()["revision"]:
                        raise ConflictError("Das Depot wurde inzwischen geändert. Bitte neu laden.")
                    run_once(store, update=data.get("update") is True, mode="weekly" if data.get("mode") == "weekly" else "daily", use_cache=data.get("use_cache") is True)
                    self.respond(200, present(store.read(), store.directory))
                    return
                if path == "/api/transactions/preview":
                    state = store.read()
                    if not state["opening_holdings"]:
                        raise ValidationError("Zuerst einen Ausgangsbestand importieren.")
                    rows = parse_transactions(data.get("csv"), state)
                    merged = merge_transactions(state["transactions"], rows)
                    candidate = {**state, "transactions": merged}
                    replay(candidate, max([h["as_of"] for h in state["opening_holdings"]]+[t["date"] for t in merged]))
                    self.respond(200, {"transactions": rows, "added": len(merged)-len(state["transactions"]), "existing": len(rows)-(len(merged)-len(state["transactions"]))})
                    return

                def mutate(state):
                    if path == "/api/holdings":
                        new_rows = validate_holdings(data.get("holdings"), state["config"])
                        if not new_rows:
                            raise ValidationError("Ein Bestandsimport muss mindestens eine Position enthalten.")
                        cash = [h for h in state["holdings"] if h.get("source") == "manual_cash"]
                        state["holdings"] = normalize_holdings(new_rows + cash)
                        state["opening_holdings"] = copy.deepcopy(state["holdings"])
                        state["reconciled_at"] = min(h["as_of"] for h in state["opening_holdings"])
                        known = {h["id"] for h in state["holdings"]}
                        state["market"]["symbols"] = {k:v for k,v in state["market"]["symbols"].items() if k in known}
                    elif path == "/api/cash":
                        amount = number(data.get("amount"), "Sicherheitsbetrag")
                        if not 0 <= amount <= 1e12:
                            raise ValidationError("Sicherheitsbetrag muss zwischen 0 und 1 Billion liegen.")
                        day = iso_date(data.get("as_of", ""))
                        # Cash edits are explicit opening balances, not inferred cash flows.
                        state["holdings"] = [h for h in state["holdings"] if h.get("source") != "manual_cash"]
                        state["opening_holdings"] = [h for h in state["opening_holdings"] if h.get("source") != "manual_cash"]
                        if amount:
                            state["holdings"].append({"name": "Separater Sicherheitsbetrag", "isin": "", "bucket": "safe", "value": amount, "currency": state["config"]["currency"], "as_of": day, "source": "manual_cash"})
                            state["opening_holdings"].append(copy.deepcopy(state["holdings"][-1]))
                        state["holdings"] = normalize_holdings(state["holdings"])
                        state["opening_holdings"] = normalize_holdings(state["opening_holdings"])
                    elif path == "/api/history":
                        if data.get("currency") != state["config"]["currency"]:
                            raise ValidationError("Währung der Historie stimmt nicht mit der Referenzwährung überein.")
                        state["history"] = parse_history(data.get("csv"))
                        state["market"]["history_source"] = "imported"
                        analyze(state)  # Reject non-finite chained returns before committing.
                    elif path == "/api/config":
                        config = validate_config(data.get("config"))
                        if config["currency"] != state["config"]["currency"] and any(state[k] for k in ["holdings", "history", "automatic_history", "quotes", "transactions"]):
                            raise ValidationError("Referenzwährung kann bei bestehenden Daten nicht geändert werden. Es findet keine automatische Devisenumrechnung statt.")
                        state["config"] = config
                    elif path == "/api/market":
                        state["market"] = validate_market(data.get("market"), state)
                    elif path == "/api/quotes":
                        state["quotes"] = merge_quotes(state["quotes"], parse_quotes(data.get("csv")))
                    elif path == "/api/transactions":
                        if not state["opening_holdings"]:
                            raise ValidationError("Zuerst einen Ausgangsbestand importieren.")
                        rows = parse_transactions(data.get("csv"), state)
                        state["transactions"] = merge_transactions(state["transactions"], rows)
                        replay(state, max([h["as_of"] for h in state["opening_holdings"]]+[t["date"] for t in state["transactions"]]))
                    elif path == "/api/events":
                        events = parse_events(data.get("csv"), state)
                        known = {e["id"]:e for e in state["events"]}
                        known.update({e["id"]:e for e in events})
                        state["events"] = list(known.values())
                    elif path == "/api/history/restart":
                        if state["automatic_history"]:
                            state.setdefault("history_archives", []).append({"history":state["automatic_history"], "benchmark":state["market"]["benchmark_label"], "opening_holdings":state.get("automatic_opening",[]), "transactions":copy.deepcopy(state["transactions"])})
                        state["automatic_history"] = []
                        state.pop("automatic_baseline", None)
                        state.pop("automatic_opening", None)
                    elif path == "/api/reconcile":
                        if not state["holdings"]:
                            raise ValidationError("Zuerst einen Bestand importieren.")
                        state["reconciled_at"] = iso_date(data.get("date", ""))
                    else:
                        raise LookupError

                state = store.change(data.get("revision"), mutate)
                self.respond(200, present(state, store.directory))
            except ConflictError as exc:
                self.respond(409, {"error": str(exc)})
            except (ValidationError, json.JSONDecodeError, UnicodeDecodeError) as exc:
                self.respond(400, {"error": str(exc)})
            except LookupError:
                self.respond(404, {"error": "Nicht gefunden."})
            except Exception:
                self.respond(500, {"error": "Interner Fehler. Die Änderung wurde möglicherweise gespeichert; bitte neu laden und prüfen."})

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main():
    parser = argparse.ArgumentParser(description="Lokaler ETF-Portfoliomonitor")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--data-dir", type=Path, default=ROOT / ".data")
    args = parser.parse_args()
    server = make_server(Store(args.data_dir), args.port)
    print(f"Finance Monitor läuft auf lokalem Port {server.server_port}. Daten: {args.data_dir}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
