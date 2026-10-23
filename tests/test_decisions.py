import copy
import unittest
from datetime import date, timedelta

from finance.analytics import analyze, contribution_plan
from finance.core import DEFAULT_CONFIG, ValidationError, bucket_labels, is_equity, validate_config, validate_holdings
from finance.ledger import normalize_state, replay
from finance.market import revalue
from finance.recommendations import trade_plan
from finance.signals import market_context
from test_expansion import fixture, transactions, ISIN


def ready_state(price=200):
    state = fixture()
    state["config"]["targets_confirmed"] = True
    for q in state["quotes"]:
        if q["symbol"] == "QUALITY.EXAMPLE" and q["date"] == "2024-01-03":
            q["close"] = q["adjusted_close"] = price
    revalue(state, today=date(2024, 1, 2))
    revalue(state, today=date(2024, 1, 3))
    return state


def market_quotes(count=252, end=date(2024, 1, 3), final=100):
    days, day = [], end
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day -= timedelta(days=1)
    days.reverse()
    return [{"symbol": "WORLD.EXAMPLE", "date": d.isoformat(), "close": final if i == count-1 else 100,
             "adjusted_close": final if i == count-1 else 100, "currency": "EUR", "source": "Fiktiver Test", "split_ratio": 1}
            for i, d in enumerate(days)]


class BucketTests(unittest.TestCase):
    def test_legacy_config_migration_preserves_allocation_and_history(self):
        old = fixture()
        for key in ["stocks", "other_equity"]:
            old["config"]["targets"].pop(key)
        old["config"].pop("custom_buckets")
        old["config"].pop("targets_confirmed")
        snapshot = copy.deepcopy(old)
        new = normalize_state(old)
        self.assertEqual(old, snapshot)
        self.assertEqual(new["holdings"], old["holdings"])
        self.assertEqual(new["history"], old["history"])
        self.assertEqual(new["config"]["targets"]["stocks"], 0)
        self.assertEqual(new["config"]["targets"]["safe"], 50)
        self.assertFalse(new["config"]["targets_confirmed"])
        self.assertEqual(sum(validate_config(old["config"])["targets"].values()), 100)

    def test_custom_labels_and_classes_validation(self):
        for definition in [{"label": "MSCI World", "asset_class": "equity"},
                           {"label": "Gold", "asset_class": "unknown"},
                           {"label": "", "asset_class": "non_equity"}]:
            config = copy.deepcopy(DEFAULT_CONFIG)
            config["custom_buckets"] = {"custom_gold": definition}
            config["targets"]["custom_gold"] = 0
            with self.subTest(definition=definition), self.assertRaises(ValidationError):
                validate_config(config)
        config["custom_buckets"]["custom_gold"] = {"label": "Gold", "asset_class": "non_equity"}
        checked = validate_config(config)
        self.assertEqual(bucket_labels(checked)["custom_gold"], "Gold")
        self.assertFalse(is_equity("custom_gold", checked))
        self.assertTrue(is_equity("stocks", checked))
        self.assertTrue(is_equity("other_equity", checked))

    def test_custom_non_equity_value_is_not_counted_as_stock(self):
        state = fixture()
        state["config"]["custom_buckets"] = {"custom_bonds": {"label": "Anleihen", "asset_class": "non_equity"}}
        state["config"]["targets"].update(quality=30, safe=50, custom_bonds=20)
        row = {**state["holdings"][0], "name": "Anleihen", "isin": "BONDS", "bucket": "custom_bonds", "value": 500}
        state["holdings"].append(row)
        result = analyze(state, today=date(2024, 1, 2))
        self.assertEqual(result["total"], 2500)
        self.assertEqual(result["equity_pct"], 40)
        self.assertEqual(result["equity_target"], 30)
        self.assertEqual(validate_holdings([row], state["config"])[0]["bucket"], "custom_bonds")
        plan = contribution_plan(state, 100)
        self.assertIn("custom_bonds", {r["key"] for r in plan["rows"]})

    def test_custom_equity_transactions_and_automatic_valuation(self):
        for asset_class, expected_flow, expected_equity in [("equity", 100, 2200), ("non_equity", 0, 1100)]:
            state = fixture()
            key = "custom_test"
            state["config"]["custom_buckets"] = {key: {"label": "Eigener Fonds", "asset_class": asset_class}}
            state["config"]["targets"][key] = 0
            added = {**state["opening_holdings"][0], "id": "CUSTOM", "isin": "CUSTOM", "name": "Custom", "bucket": key}
            state["opening_holdings"].append(added)
            state["market"]["symbols"]["CUSTOM"] = "CUSTOM.EXAMPLE"
            state["quotes"].extend({**q, "symbol": "CUSTOM.EXAMPLE", "close": 100} for q in list(state["quotes"]) if q["symbol"] == "QUALITY.EXAMPLE")
            state["transactions"] = transactions(state, "buy,2024-01-03,BUY,CUSTOM,1,100,100,0,EUR")
            _, flows, _ = replay(state, "2024-01-03")
            self.assertEqual(flows["2024-01-03"]["equity_flow"], expected_flow)
            revalue(state, today=date(2024, 1, 3))
            self.assertEqual(state["automatic_history"][-1]["equity_value"], expected_equity)


class RecommendationTests(unittest.TestCase):
    def test_unconfirmed_or_stale_data_blocks_concrete_amounts(self):
        state = ready_state()
        state["config"]["targets_confirmed"] = False
        result = trade_plan(state, allow_sales=True, today=date(2024, 1, 3))
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["rows"], [])
        state["config"]["targets_confirmed"] = True
        result = trade_plan(state, allow_sales=True, today=date(2024, 2, 1))
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["rows"], [])

    def test_new_cash_resolves_overweight_without_selling(self):
        result = trade_plan(ready_state(), amount=1000, fee=1, allow_sales=True, today=date(2024, 1, 3))
        self.assertFalse(any(r["action"] == "sell" for r in result["rows"]))
        self.assertEqual(result["estimated_fees"], 0)
        self.assertEqual(result["after_total"], 4000)
        self.assertEqual(result["remaining_deviations"], [])

    def test_sales_are_explicit_and_budget_covers_purchases_and_fees(self):
        state = ready_state()
        state["config"]["targets"].update(quality=20, world=30)
        before = copy.deepcopy(state)
        off = trade_plan(state, fee=1, today=date(2024, 1, 3))
        self.assertFalse(any(r["action"] == "sell" for r in off["rows"]))
        self.assertTrue(off["remaining_deviations"])
        result = trade_plan(state, fee=1, allow_sales=True, today=date(2024, 1, 3))
        buys = sum(r["amount"] for r in result["rows"] if r["action"] == "buy")
        sells = sum(r["amount"] for r in result["rows"] if r["action"] == "sell")
        reserve = sum(r["amount"] for r in result["rows"] if r["action"] == "reserve")
        self.assertGreater(buys, 0)
        self.assertGreater(sells, 0)
        self.assertAlmostEqual(sells, buys+reserve+result["estimated_fees"], places=2)
        self.assertEqual(result["after_total"], 3000-result["estimated_fees"])
        self.assertEqual(state, before)

    def test_minimum_purchase_keeps_small_budget_as_cash_without_fee(self):
        state = ready_state()
        result = trade_plan(state, amount=10, fee=5, minimum=25, today=date(2024, 1, 3))
        self.assertFalse(any(r["action"] == "buy" for r in result["rows"]))
        self.assertEqual(result["estimated_fees"], 0)
        self.assertEqual(result["after_total"], 3010)

    def test_zero_target_does_not_sell_before_explicit_confirmation(self):
        state = ready_state()
        state["config"]["targets"].update(quality=0, safe=100)
        state["config"]["targets_confirmed"] = False
        self.assertEqual(trade_plan(state, allow_sales=True, today=date(2024, 1, 3))["rows"], [])
        state["config"]["targets_confirmed"] = True
        result = trade_plan(state, allow_sales=True, today=date(2024, 1, 3))
        sale = next(r for r in result["rows"] if r["action"] == "sell")
        self.assertEqual(sale["amount"], 2000)
        self.assertEqual(sale["after_pct"], 0)

    def test_only_recorded_cash_can_fund_reserve_reduction(self):
        state = ready_state(price=50)
        # A security in the safety bucket is not available transaction cash.
        for rows in [state["holdings"], state["opening_holdings"]]:
            rows[1].update(source="csv", isin="SAFEFUND", name="Safety ETF")
        state["automatic_history"] = []
        state["history"] = [{"date": "2024-01-02", "total_value": 1500, "equity_value": 500, "net_flow": 0, "equity_flow": 0, "world_index": 100, "safety_index": 100},
                            {"date": "2024-01-03", "total_value": 1500, "equity_value": 500, "net_flow": 0, "equity_flow": 0, "world_index": 100, "safety_index": 100}]
        state["market"]["history_source"] = "imported"
        result = trade_plan(state, allow_sales=True, today=date(2024, 1, 3))
        self.assertEqual(result["status"], "no_change")
        self.assertFalse(any(r["action"] in {"buy", "use_cash", "sell"} for r in result["rows"]))
        self.assertTrue(any("Verrechnungsguthaben" in message for message in result["warnings"]))

    def test_market_stress_alone_does_not_trigger_sale(self):
        state = fixture()
        state["config"]["targets_confirmed"] = True
        state["quotes"] = [q for q in state["quotes"] if q["symbol"] != "WORLD.EXAMPLE"]+market_quotes(final=70)
        revalue(state, today=date(2024, 1, 2))
        revalue(state, today=date(2024, 1, 3))
        before = copy.deepcopy(state["config"])
        result = trade_plan(state, allow_sales=True, today=date(2024, 1, 3))
        self.assertEqual(result["status"], "no_change")
        self.assertTrue(result["market_context"]["signals"])
        self.assertEqual(state["config"], before)

    def test_invalid_inputs(self):
        for arguments in [{"amount": -1}, {"fee": -1}, {"minimum": -1}, {"allow_sales": "yes"}]:
            with self.subTest(arguments=arguments), self.assertRaises(ValidationError):
                trade_plan(ready_state(), **arguments)

    def test_budget_conservation_across_fees_targets_and_minimums(self):
        for price in [25, 100, 500]:
            for target in [0, 10, 80]:
                for amount in [0, 10, 1000]:
                    for fee in [0, 1, 100]:
                        for minimum in [0, 25, 500]:
                            state = ready_state(price)
                            state["config"]["targets"].update(quality=target, safe=100-target)
                            result = trade_plan(state, amount, fee, minimum, True, today=date(2024, 1, 3))
                            self.assertNotEqual(result["status"], "blocked")
                            totals = {kind: sum(r["amount"] for r in result["rows"] if r["action"] == kind) for kind in ["buy", "sell", "reserve", "use_cash"]}
                            self.assertAlmostEqual(amount+totals["sell"]+totals["use_cash"], totals["buy"]+totals["reserve"]+result["estimated_fees"], places=2)
                            self.assertAlmostEqual(result["after_total"], price*10+1000+amount-result["estimated_fees"], places=2)
                            self.assertEqual(sum(r["fee"] for r in result["rows"]), result["estimated_fees"])
                            self.assertTrue(all(r["amount"] >= minimum for r in result["rows"] if r["action"] in {"buy", "sell"}))

    def test_full_exit_can_leave_only_cash(self):
        state = fixture()
        state["opening_holdings"][1]["value"] = 0
        state["config"]["targets"].update(quality=0, safe=100)
        state["config"]["targets_confirmed"] = True
        revalue(state, today=date(2024, 1, 2))
        revalue(state, today=date(2024, 1, 3))
        result = trade_plan(state, fee=1, allow_sales=True, today=date(2024, 1, 3))
        self.assertEqual(result["after_total"], 1099)
        self.assertEqual(next(r for r in result["rows"] if r["action"] == "sell")["amount"], 1100)
        self.assertEqual(next(r for r in result["rows"] if r["action"] == "reserve")["amount"], 1099)


class MarketContextTests(unittest.TestCase):
    def test_full_history_metrics_and_stress_thresholds(self):
        state = fixture()
        state["quotes"] = market_quotes()
        result = market_context(state, today=date(2024, 1, 3))
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["signals"], [])
        self.assertEqual(result["metrics"]["volatility20_pct"], 0)
        state["quotes"] = market_quotes(final=70)
        result = market_context(state, today=date(2024, 1, 3))
        self.assertEqual({s["code"] for s in result["signals"]}, {"below_sma200", "high_volatility", "market_drawdown"})
        self.assertAlmostEqual(result["metrics"]["drawdown252_pct"], -30)
        self.assertAlmostEqual(result["metrics"]["return20_pct"], -30)

    def test_partial_history_does_not_invent_year_high_or_sma(self):
        state = fixture()
        state["quotes"] = market_quotes(count=30)
        result = market_context(state, today=date(2024, 1, 3))
        self.assertEqual(result["status"], "partial")
        self.assertIsNone(result["metrics"]["sma200_gap_pct"])
        self.assertIsNone(result["metrics"]["drawdown252_pct"])

    def test_daily_series_can_include_a_long_holiday_weekend(self):
        state = fixture()
        state["quotes"] = market_quotes(count=40)
        # Monday and Tuesday closure following Friday creates a five-day gap.
        state["quotes"] = [q for q in state["quotes"] if q["date"] not in {"2023-12-25", "2023-12-26"}]
        result = market_context(state, today=date(2024, 1, 3))
        self.assertEqual(result["status"], "partial")
        self.assertIn("volatility20_pct", result["metrics"])

    def test_stale_sparse_wrong_currency_or_unconfirmed_has_no_current_signals(self):
        for condition in ["stale", "sparse", "currency", "confirmation"]:
            state = fixture()
            state["quotes"] = market_quotes()
            today = date(2024, 1, 3)
            if condition == "stale":
                today = date(2024, 2, 1)
            elif condition == "sparse":
                state["quotes"] = state["quotes"][::5]
            elif condition == "currency":
                for q in state["quotes"]:
                    q["currency"] = "USD"
            else:
                state["market"]["confirmed"] = False
            result = market_context(state, today=today)
            with self.subTest(condition=condition):
                self.assertEqual(result["status"], "unavailable")
                self.assertEqual(result["signals"], [])
                self.assertEqual(result["metrics"], {})


if __name__ == "__main__":
    unittest.main()
