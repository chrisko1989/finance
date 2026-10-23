import copy
import json
import sqlite3
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from finance.analytics import analyze, contribution_plan, parse_events, period_metrics
from finance.core import DEFAULT_CONFIG, ValidationError, inspect_csv, parse_holdings, performance
from finance.ledger import merge_transactions, normalize_state, parse_transactions, replay
from finance.market import parse_quotes, parse_yahoo, revalue, validate_market
from finance.monitor import due_modes, run_once
from finance.reporting import markdown, report
from finance.storage import Store

ISIN = "IE00BP3QZ601"
TX_HEADER = "id,date,type,instrument_id,quantity,price,amount,fee,currency\n"
QUOTE_HEADER = "symbol,date,close,adjusted_close,currency,source\n"


def fixture():
    config = copy.deepcopy(DEFAULT_CONFIG)
    config["targets"] = {k: 50 if k in {"quality", "safe"} else 0 for k in config["targets"]}
    rows = [
        {"name": "Quality", "isin": ISIN, "bucket": "quality", "value": 1000,
         "quantity": 10, "price": 100, "cost_basis": 900, "currency": "EUR", "as_of": "2024-01-02", "source": "csv"},
        {"name": "Cash", "isin": "", "bucket": "safe", "value": 1000,
         "currency": "EUR", "as_of": "2024-01-02", "source": "manual_cash"},
    ]
    state = normalize_state({"config": config, "holdings": rows, "history": [], "revision": 0, "demo": False})
    state["market"] = {"provider": "csv", "symbols": {ISIN: "QUALITY.EXAMPLE"},
                       "benchmark_symbol": "WORLD.EXAMPLE", "benchmark_label": "Fiktive World-TR-Referenz",
                       "confirmed": True, "history_source": "automatic"}
    state["quotes"] = parse_quotes(QUOTE_HEADER + "\n".join(
        f"{symbol},2024-01-0{day},{value},{value},EUR,Fixture"
        for symbol, values in [("QUALITY.EXAMPLE", [100, 110, 100]), ("WORLD.EXAMPLE", [100, 102, 103])]
        for day, value in zip([2, 3, 4], values)))
    return state


def transactions(state, text):
    return parse_transactions(TX_HEADER + text, state)


class LedgerTests(unittest.TestCase):
    def test_quantity_basis_and_old_state_are_preserved(self):
        csv = "name,isin,quantity,price,cost_basis,as_of\nQuality,IE00BP3QZ601,10,100,900,2024-01-02"
        row = parse_holdings(csv, inspect_csv(csv)["mapping"], DEFAULT_CONFIG)[0]
        self.assertEqual((row["quantity"], row["price"], row["cost_basis"]), (10, 100, 900))
        original = {"schema_version": 1, "config": copy.deepcopy(DEFAULT_CONFIG), "holdings": [row], "history": [], "revision": 7}
        original["config"]["bucket_band_relative"] = 18
        migrated = normalize_state(original)
        self.assertNotIn("opening_holdings", original)
        self.assertEqual(migrated["schema_version"], 2)
        self.assertEqual(migrated["revision"], 7)
        self.assertEqual(migrated["config"]["bucket_band_relative"], 18)
        self.assertEqual(migrated["opening_holdings"][0]["value"], 1000)

    def test_idempotence_conflicts_and_cash_flows(self):
        state = fixture()
        incoming = transactions(state, f"deposit,2024-01-03,DEPOSIT,,0,0,500,0,EUR\nbuy,2024-01-03,BUY,{ISIN},2,110,220,1,EUR")
        state["transactions"] = merge_transactions(incoming, incoming)
        self.assertEqual(len(state["transactions"]), 2)
        positions, flows, _ = replay(state, "2024-01-03")
        self.assertEqual(positions[0]["quantity"], 12)
        self.assertEqual(positions[0]["cost_basis"], 1121)
        self.assertEqual(positions[1]["value"], 1279)
        self.assertEqual(flows["2024-01-03"], {"net_flow": 500, "equity_flow": 221})
        conflict = copy.deepcopy(incoming)
        conflict[0]["amount"] = 501
        with self.assertRaisesRegex(ValidationError, "anderem Inhalt"):
            merge_transactions(incoming, conflict)

    def test_snapshot_date_transactions_are_already_included(self):
        state = fixture()
        state["transactions"] = transactions(state, "old,2024-01-02,DEPOSIT,,0,0,999,0,EUR")
        positions, flows, _ = replay(state, "2024-01-03")
        self.assertEqual(positions[1]["value"], 1000)
        self.assertEqual(flows, {})

    def test_negative_cash_oversell_and_mixed_dates_rejected(self):
        for text, error in [("x,2024-01-03,WITHDRAWAL,,0,0,1001,0,EUR", "negativ"),
                            (f"x,2024-01-03,SELL,{ISIN},11,100,1100,0,EUR", "überschreitet")]:
            state = fixture()
            state["transactions"] = transactions(state, text)
            with self.subTest(text=text), self.assertRaisesRegex(ValidationError, error):
                replay(state, "2024-01-03")
        state = fixture()
        state["opening_holdings"][1]["as_of"] = "2024-01-01"
        with self.assertRaisesRegex(ValidationError, "dasselbe"):
            replay(state, "2024-01-03")

    def test_sale_and_dividend_flow_and_average_basis(self):
        state = fixture()
        state["transactions"] = transactions(state, f"sell,2024-01-03,SELL,{ISIN},2,110,220,1,EUR\ndiv,2024-01-03,DIVIDEND,{ISIN},0,0,20,2,EUR")
        positions, flows, _ = replay(state, "2024-01-03")
        self.assertEqual(positions[0]["cost_basis"], 720)
        self.assertEqual(positions[1]["value"], 1237)
        self.assertEqual(flows["2024-01-03"], {"net_flow": 0, "equity_flow": -237})


class MarketTests(unittest.TestCase):
    def test_valuation_flows_repeat_and_no_backcast(self):
        state = fixture()
        revalue(state, today=date(2024, 1, 2))
        state["transactions"] = transactions(state, f"dep,2024-01-03,DEPOSIT,,0,0,500,0,EUR\nbuy,2024-01-03,BUY,{ISIN},2,110,220,1,EUR")
        revalue(state, today=date(2024, 1, 3))
        self.assertEqual(state["automatic_history"][-1]["total_value"], 2599)
        perf = performance(state["automatic_history"], state["config"])
        self.assertAlmostEqual(perf["total_return"], 4.95)
        self.assertAlmostEqual(perf["equity_return"], 9.9)
        original = copy.deepcopy(state)
        revalue(state, today=date(2024, 1, 3))
        self.assertEqual(state, original)
        self.assertEqual(state["history"], [])
        first = fixture()
        revalue(first, today=date(2024, 1, 4))
        self.assertEqual(len(first["automatic_history"]), 1)
        self.assertIsNone(analyze(first, today=date(2024, 1, 4))["performance"])

    def test_shared_date_and_stale_quotes(self):
        state = fixture()
        state["quotes"] = [q for q in state["quotes"] if not(q["symbol"] == "WORLD.EXAMPLE" and q["date"] == "2024-01-04")]
        self.assertEqual(revalue(state, today=date(2024, 1, 4)), "2024-01-03")
        with self.assertRaisesRegex(ValidationError, "zu alt"):
            revalue(state, today=date(2024, 1, 10))

    def test_fx_and_missing_adjusted_reference_rejected(self):
        for key, value, message in [("currency", "USD", "Devisenumrechnung"), ("adjusted_close", None, "bereinigte")]:
            state = fixture()
            for quote in state["quotes"]:
                if quote["symbol"] == "WORLD.EXAMPLE":
                    quote[key] = value
            with self.subTest(key=key), self.assertRaisesRegex(ValidationError, message):
                revalue(state, today=date(2024, 1, 4))

    def test_missing_quantity_and_mapping_rejected(self):
        for missing in ["quantity", "mapping"]:
            state = fixture()
            if missing == "quantity":
                state["opening_holdings"][0]["quantity"] = None
            else:
                state["market"]["symbols"] = {}
            with self.subTest(missing=missing), self.assertRaisesRegex(ValidationError, "Stückzahl"):
                revalue(state, today=date(2024, 1, 4))

    def test_split_requires_matching_ledger_entry(self):
        state = fixture()
        for q in state["quotes"]:
            if q["symbol"] == "QUALITY.EXAMPLE" and q["date"] == "2024-01-03":
                q.update(close=50, split_ratio=2)
        with self.assertRaisesRegex(ValidationError, "Split|split"):
            revalue(state, today=date(2024, 1, 3))
        state["transactions"] = transactions(state, f"split,2024-01-03,SPLIT,{ISIN},2,0,0,0,EUR")
        revalue(state, today=date(2024, 1, 3))
        self.assertEqual(state["holdings"][0]["quantity"], 20)
        self.assertEqual(state["holdings"][0]["value"], 1000)
        self.assertEqual(state["holdings"][0]["cost_basis"], 900)

    def test_changed_baseline_or_mapping_cannot_create_returns(self):
        for change in ["opening", "mapping"]:
            state = fixture()
            revalue(state, today=date(2024, 1, 2))
            if change == "opening":
                state["opening_holdings"][1]["value"] += 100
            else:
                state["market"]["symbols"][ISIN] = "OTHER.EXAMPLE"
            self.assertIn("valuation_changed", {a["code"] for a in analyze(state)["alerts"]})
            with self.assertRaisesRegex(ValidationError, "archivieren"):
                revalue(state, today=date(2024, 1, 3))

    def test_retroactive_transaction_blocks_future_linking_but_same_day_recalculates(self):
        state = fixture()
        revalue(state, today=date(2024, 1, 2))
        revalue(state, today=date(2024, 1, 3))
        state["transactions"] = transactions(state, "late,2024-01-03,DEPOSIT,,0,0,500,0,EUR")
        with self.assertRaisesRegex(ValidationError, "Nachträgliche"):
            revalue(state, today=date(2024, 1, 4))
        revalue(state, today=date(2024, 1, 3))
        self.assertEqual(state["automatic_history"][-1]["net_flow"], 500)
        revalue(state, today=date(2024, 1, 4))
        self.assertEqual(state["automatic_history"][-1]["net_flow"], 0)

    def test_automatic_and_manual_history_remain_separate(self):
        state = fixture()
        manual = [{"date": "2024-01-01", "total_value": 100, "equity_value": 50, "net_flow": 0, "equity_flow": 0, "world_index": 100, "safety_index": 100},
                  {"date": "2024-01-02", "total_value": 110, "equity_value": 60, "net_flow": 0, "equity_flow": 0, "world_index": 120, "safety_index": 100}]
        state["history"] = copy.deepcopy(manual)
        revalue(state, today=date(2024, 1, 2))
        revalue(state, today=date(2024, 1, 3))
        self.assertEqual(state["history"], manual)
        self.assertAlmostEqual(analyze(state)["performance"]["total_return"], 5)
        state["market"]["history_source"] = "imported"
        self.assertAlmostEqual(analyze(state)["performance"]["total_return"], 10)

    def test_unconfirmed_mapping_and_benchmark_change(self):
        state = fixture()
        revalue(state, today=date(2024, 1, 2))
        market = {**state["market"], "benchmark_symbol": "OTHER"}
        with self.assertRaises(ValidationError):
            validate_market(market, state)
        state["market"]["confirmed"] = False
        with self.assertRaisesRegex(ValidationError, "bestätigt"):
            revalue(state, today=date(2024, 1, 3))

    def test_yahoo_filters_unfinished_candles_and_wrong_symbol(self):
        stamps = [int(datetime(2024, 1, d, 9, tzinfo=timezone.utc).timestamp()) for d in [2, 3]]
        end = int(datetime(2024, 1, 3, 17, tzinfo=timezone.utc).timestamp())
        document = {"chart": {"error": None, "result": [{"meta": {"symbol": "EXAMPLE", "currency": "EUR", "exchangeTimezoneName": "Europe/Berlin", "currentTradingPeriod": {"regular": {"end": end}}},
            "timestamp": stamps, "indicators": {"quote": [{"close": [100, 102]}], "adjclose": [{"adjclose": [99, 101]}]}}]}}
        rows = parse_yahoo(document, "EXAMPLE", now=datetime(2024, 1, 3, 16, tzinfo=timezone.utc))
        self.assertEqual([q["date"] for q in rows], ["2024-01-02"])
        self.assertEqual(rows[0]["adjusted_close"], 99)
        self.assertEqual(len(parse_yahoo(document, "EXAMPLE", now=datetime(2024, 1, 3, 18, tzinfo=timezone.utc))), 2)
        with self.assertRaisesRegex(ValidationError, "Symbolkennung"):
            parse_yahoo(document, "WRONG")
        document["chart"]["result"][0]["meta"]["currency"] = "GBp"
        with self.assertRaisesRegex(ValidationError, "Untereinheiten"):
            parse_yahoo(document, "EXAMPLE")


class AnalyticsTests(unittest.TestCase):
    def test_fresh_money_denominator_and_fee_budget(self):
        state = fixture()
        state["holdings"][0]["value"] = 1600
        state["holdings"][1]["value"] = 48400
        state["config"]["targets"]["quality"] = 10
        state["config"]["targets"]["safe"] = 90
        plan = contribution_plan(state, 3777.78)
        quality = next(r for r in plan["rows"] if r["key"] == "quality")
        self.assertAlmostEqual(quality["after_pct"], 10, places=5)
        self.assertAlmostEqual(quality["amount"], 3777.78, places=2)
        for amount in [0, 1, 10, 100, 1000.01]:
            for fee in [0, 1, 20]:
                with self.subTest(amount=amount, fee=fee):
                    p = contribution_plan(fixture(), amount, fee)
                    self.assertAlmostEqual(sum(r["amount"] for r in p["rows"])+p["fees"]+p["unallocated_cash"], amount, places=2)
                    self.assertTrue(all(r["amount"] >= 0 for r in p["rows"]))

    def test_partial_year_is_not_ytd_and_sparse_data_not_daily_volatility(self):
        state = fixture()
        revalue(state, today=date(2024, 1, 2))
        revalue(state, today=date(2024, 1, 3))
        metrics = analyze(state, today=date(2024, 1, 3))
        self.assertIsNone(metrics["periods"]["year"])
        self.assertIsNone(metrics["periods"]["month"])
        self.assertAlmostEqual(metrics["periods"]["day"]["portfolio"], 5)
        points = [{"date": (date(2024, 1, 1)+timedelta(days=i*7)).isoformat(), **{k: i for k in ["portfolio", "equity", "world", "reference"]}} for i in range(25)]
        self.assertIsNone(period_metrics({"points": points})["volatility_annualized"])
        days = [date(2024, 1, 1)+timedelta(days=i) for i in range(40)]
        points = [{"date": day.isoformat(), **{k: (i % 3)/10 for k in ["portfolio", "equity", "world", "reference"]}} for i, day in enumerate(days) if day.weekday() < 5]
        self.assertGreater(period_metrics({"points": points})["volatility_annualized"], 0)

    def test_missing_data_never_suggests_hold(self):
        self.assertEqual(analyze(fixture())["decision"]["label"], "DATEN PRÜFEN")

    def test_absolute_or_relative_drift_rule(self):
        state = fixture()
        state["holdings"][0]["value"] = 1120
        state["holdings"][1]["value"] = 880
        # 6 pp exceeds 5 pp even though 12% relative is below 25%.
        alerts = analyze(state, today=date(2024, 1, 2))["alerts"]
        self.assertIn("drift_quality", {a["code"] for a in alerts})

    def test_drawdown_stages(self):
        for loss, stage in [(11, 10), (16, 15), (21, 20)]:
            state = fixture()
            revalue(state, today=date(2024, 1, 2))
            for quote in state["quotes"]:
                if quote["symbol"] == "QUALITY.EXAMPLE" and quote["date"] == "2024-01-03":
                    quote["close"] = 100-loss*2
            revalue(state, today=date(2024, 1, 3))
            alert = next(a for a in analyze(state, today=date(2024, 1, 3))["alerts"] if a["code"] == "drawdown")
            self.assertEqual(alert["stage"], stage)

    def test_event_sources_and_weekly_report_limits(self):
        state = fixture()
        header = "id,date,instrument_id,type,title,url,status\n"
        for url in ["javascript:alert(1)", "https://user:secret@example.com", "http://example.com"]:
            with self.subTest(url=url), self.assertRaises(ValidationError):
                parse_events(header+f"e,2024-01-02,,market,Example,{url},open", state)
        state["events"] = parse_events(header+"e,2024-01-02,,market,Fiktiver Hinweis,https://example.com,open", state)
        result = markdown(report(state, mode="weekly", today=date(2024, 1, 3)))
        self.assertIn("Fiktiver Hinweis", result)
        self.assertIn("Keine automatische Nachrichten-/Makroanalyse", result)
        self.assertIn("DATEN PRÜFEN", result)


class MonitorTests(unittest.TestCase):
    def test_failed_update_is_atomic_and_notices_deduplicate(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            state = fixture()
            state["market"]["provider"] = "yahoo"
            store.change(0, lambda s: s.update(state))
            before = store.read()
            def unavailable(symbol):
                raise ValidationError("Quelle nicht erreichbar")
            run_once(store, update=True, fetcher=unavailable)
            failed = store.read()
            for key in ["holdings", "quotes", "automatic_history"]:
                self.assertEqual(failed[key], before[key])
            self.assertEqual(failed["monitor"]["last_error"], "Quelle nicht erreichbar")
            run_once(store, update=True, fetcher=unavailable)
            self.assertEqual(store.read()["monitor"]["notices"], failed["monitor"]["notices"])
            self.assertTrue((Path(directory)/"reports/latest-daily.md").exists())

    def test_successful_update_persists_archive_basis_and_report(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            store.change(0, lambda s: s.update(fixture()))
            with patch("finance.monitor.revalue", side_effect=lambda s: revalue(s, today=date(2024, 1, 4))):
                run_once(store, update=True, use_cache=True, mode="weekly")
            state = store.read()
            self.assertEqual(len(state["automatic_history"]), 1)
            self.assertEqual(state["automatic_opening"], state["opening_holdings"])
            self.assertIsNone(state["monitor"]["last_error"])
            self.assertTrue(state["monitor"]["last_success"])
            self.assertEqual((Path(directory)/"reports/latest-weekly.json").stat().st_mode & 0o777, 0o600)

    def test_schedule_uses_berlin_dst_and_does_not_repeat(self):
        self.assertEqual(due_modes(datetime(2024, 7, 1, 20, 14, tzinfo=timezone.utc), {}), [])
        self.assertEqual(due_modes(datetime(2024, 7, 1, 20, 15, tzinfo=timezone.utc), {}), ["daily"])
        self.assertEqual(due_modes(datetime(2024, 1, 2, 21, 15, tzinfo=timezone.utc), {}), ["daily"])
        self.assertEqual(due_modes(datetime(2024, 1, 2, 21, 16, tzinfo=timezone.utc), {"daily": "2024-01-02"}), [])
        self.assertEqual(due_modes(datetime(2024, 7, 7, 16, 0, tzinfo=timezone.utc), {}), ["weekly"])
        self.assertEqual(due_modes(datetime(2024, 7, 6, 22, 0, tzinfo=timezone.utc), {}), [])

    def test_backup_is_restorable_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            store.change(0, lambda s: s.update(fixture()))
            destination = Path(directory)/"backups/test.sqlite3"
            store.backup(destination)
            with sqlite3.connect(destination) as db:
                revision, payload = db.execute("SELECT revision,payload FROM portfolio").fetchone()
            self.assertEqual(revision, store.read()["revision"])
            self.assertEqual(json.loads(payload)["holdings"], store.read()["holdings"])
            self.assertEqual(destination.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(ValidationError):
                store.backup(destination)


if __name__ == "__main__":
    unittest.main()
