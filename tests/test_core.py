import copy
import unittest
from datetime import date

from finance.core import DEFAULT_CONFIG, ValidationError, analyze, demo_state, inspect_csv, number, parse_history, parse_holdings, performance, validate_config, weekdays_since


HEADER = "date,total_value,equity_value,net_flow,equity_flow,world_index,safety_index\n"


class PerformanceTests(unittest.TestCase):
    def test_deposit_is_not_a_return(self):
        history = parse_history(HEADER + "2024-01-01,1000,500,0,0,100,100\n2024-01-02,1500,1000,500,500,100,100")
        result = performance(history, DEFAULT_CONFIG)
        self.assertAlmostEqual(result["total_return"], 0)
        self.assertAlmostEqual(result["equity_return"], 0)

    def test_withdrawal_is_not_a_loss(self):
        history = parse_history(HEADER + "2024-01-01,1000,500,0,0,100,100\n2024-01-02,800,300,-200,-200,100,100")
        result = performance(history, DEFAULT_CONFIG)
        self.assertAlmostEqual(result["total_return"], 0)
        self.assertAlmostEqual(result["max_drawdown"], 0)

    def test_internal_transfer_is_flow_only_in_equity_sleeve(self):
        history = parse_history(HEADER + "2024-01-01,1000,500,0,0,100,100\n2024-01-02,1000,750,0,250,100,100")
        result = performance(history, DEFAULT_CONFIG)
        self.assertAlmostEqual(result["total_return"], 0)
        self.assertAlmostEqual(result["equity_return"], 0)

    def test_geometric_linking_and_cashflow_adjusted_drawdown(self):
        history = parse_history(HEADER + "2024-01-01,1000,500,0,0,100,100\n2024-01-02,1200,650,100,100,110,100\n2024-01-03,1080,585,0,0,99,100")
        result = performance(history, DEFAULT_CONFIG)
        self.assertAlmostEqual(result["total_return"], -1)
        self.assertAlmostEqual(result["max_drawdown"], -10)

    def test_reference_is_initial_weight_buy_and_hold(self):
        history = parse_history(HEADER + "2024-01-01,1000,500,0,0,100,100\n2024-01-02,1100,600,0,0,120,102")
        result = performance(history, DEFAULT_CONFIG)
        self.assertAlmostEqual(result["reference_return"], 11)
        self.assertAlmostEqual(result["world_return"], 20)
        self.assertAlmostEqual(result["excess_total_pp"], -1)

    def test_history_is_sorted(self):
        history = parse_history(HEADER + "2024-01-02,1000,500,0,0,100,100\n2024-01-01,1000,500,0,0,100,100")
        self.assertEqual(history[0]["date"], "2024-01-01")

    def test_reject_invalid_histories(self):
        cases = [
            "2024-01-01,1000,500,0,0,100,100",  # insufficient observations
            "2024-01-01,1000,500,0,0,100,100\n2024-01-01,1000,500,0,0,100,100",
            "2024-01-01,1000,500,10,0,100,100\n2024-01-02,1000,500,0,0,100,100",
            "2024-01-01,1000,500,0,0,100,100\n2024-01-02,1000,1500,0,0,100,100",
            "2024-01-01,1000,500,0,0,100,100\n2024-01-02,1000,0,0,0,100,100",
            "2024-01-01,1000,500,0,0,100,100\n2024-01-02,1000,500,0,0,0,100",
            "2024-01-01,1000,500,0,0,100,100\n2024-01-02,1000,500,700,0,100,100",
        ]
        for csv in cases:
            with self.subTest(csv=csv), self.assertRaises(ValidationError):
                parse_history(HEADER + csv)


class ImportTests(unittest.TestCase):
    def test_ing_style_decimal_comma_and_isin_assignment(self):
        text = "\ufeffWertpapierbezeichnung;ISIN;Stück/Nominale;Kurs;Währung;Kursdatum\nQualität;IE00BP3QZ601;10,5;1.234,56;EUR;01.02.2024"
        mapping = inspect_csv(text)["mapping"]
        rows = parse_holdings(text, mapping, DEFAULT_CONFIG, german=True)
        self.assertAlmostEqual(rows[0]["value"], 12962.88)
        self.assertEqual(rows[0]["bucket"], "quality")
        self.assertEqual(rows[0]["as_of"], "2024-02-01")

    def test_market_value_takes_precedence_over_quote(self):
        text = "name,value,quantity,price,currency,as_of\nWorld,123,2,50,EUR,2024-01-01"
        rows = parse_holdings(text, inspect_csv(text)["mapping"], DEFAULT_CONFIG)
        self.assertEqual(rows[0]["value"], 123)
        self.assertEqual(rows[0]["bucket"], "")

    def test_reject_mixed_currency(self):
        text = "name,value,currency,as_of\nWorld,123,USD,2024-01-01"
        with self.assertRaisesRegex(ValidationError, "umrechnen"):
            parse_holdings(text, inspect_csv(text)["mapping"], DEFAULT_CONFIG)

    def test_require_explicit_date(self):
        text = "name,value\nWorld,123"
        mapping = inspect_csv(text)["mapping"]
        with self.assertRaises(ValidationError):
            parse_holdings(text, mapping, DEFAULT_CONFIG)
        self.assertEqual(parse_holdings(text, mapping, DEFAULT_CONFIG, valuation_date="2024-01-01")[0]["as_of"], "2024-01-01")

    def test_reject_negative_quantity_even_with_negative_price(self):
        text = "name,quantity,price,as_of\nWorld,-2,-50,2024-01-01"
        with self.assertRaises(ValidationError):
            parse_holdings(text, inspect_csv(text)["mapping"], DEFAULT_CONFIG)

    def test_reject_bad_headers_and_row_lengths(self):
        for text in ["name,name\na,b", "name,value\na,1,2", "name,value\na", "name,value\n"]:
            with self.subTest(text=text), self.assertRaises(ValidationError):
                inspect_csv(text)

    def test_reject_non_finite_and_boolean_numbers(self):
        for value in ["NaN", "Infinity", "-Infinity", "1e1000", True, None]:
            with self.subTest(value=value), self.assertRaises(ValidationError):
                number(value)

    def test_unknown_isin_is_not_assumed_world(self):
        text = "name,value,isin,as_of\nUnknown,100,XX0000000000,2024-01-01"
        result = parse_holdings(text, inspect_csv(text)["mapping"], DEFAULT_CONFIG)
        self.assertEqual(result[0]["bucket"], "")


class AlertTests(unittest.TestCase):
    def state(self):
        state = demo_state()
        state["history"] = []
        state["holdings"] = [{"name": key, "bucket": key, "value": weight*1000, "as_of": "2024-01-05"} for key, weight in DEFAULT_CONFIG["targets"].items()]
        return state

    def test_exact_targets_have_no_drift(self):
        result = analyze(self.state(), today=date(2024,1,5))
        self.assertFalse(any(a["code"].startswith("drift") or a["code"] == "equity_band" for a in result["alerts"]))
        self.assertAlmostEqual(result["equity_pct"], 50)

    def test_equity_band_and_relative_threshold(self):
        state = self.state()
        state["holdings"][2]["value"] += 10000
        state["holdings"][-1]["value"] -= 10000
        codes = {a["code"] for a in analyze(state, today=date(2024,1,5))["alerts"]}
        self.assertIn("equity_band", codes)
        self.assertIn("drift_momentum", codes)

    def test_zero_total_is_not_a_zero_return(self):
        state = self.state()
        state["holdings"] = []
        result = analyze(state)
        self.assertIsNone(result["equity_pct"])
        self.assertIsNone(result["performance"])

    def test_weekends_do_not_stale_quotes(self):
        self.assertEqual(weekdays_since(date(2024,1,5), date(2024,1,8)), 1)
        self.assertEqual(weekdays_since(date(2024,1,5), date(2024,1,10)), 3)
        self.assertNotIn("stale", {a["code"] for a in analyze(self.state(), today=date(2024,1,8))["alerts"]})
        self.assertIn("stale", {a["code"] for a in analyze(self.state(), today=date(2024,1,10))["alerts"]})

    def test_historical_drawdown_does_not_claim_current_quotes(self):
        state = self.state()
        state["history"] = parse_history(HEADER + "2024-01-01,1000,500,0,0,100,100\n2024-01-02,800,300,0,0,60,100")
        codes = {a["code"] for a in analyze(state, today=date(2024,2,1))["alerts"]}
        self.assertIn("drawdown", codes)
        self.assertIn("history_stale", codes)

    def test_invalid_targets_and_fractional_staleness(self):
        config = copy.deepcopy(DEFAULT_CONFIG)
        config["targets"]["safe"] = 49
        with self.assertRaises(ValidationError):
            validate_config(config)
        config = copy.deepcopy(DEFAULT_CONFIG)
        config["stale_weekdays"] = 1.5
        with self.assertRaises(ValidationError):
            validate_config(config)

    def test_demo_does_not_mutate_default_config(self):
        first = demo_state()
        first["config"]["targets"]["safe"] = 0
        self.assertEqual(demo_state()["config"]["targets"]["safe"], 50)


if __name__ == "__main__":
    unittest.main()
