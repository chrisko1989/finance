import copy
import http.client
import json
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from finance.core import DEFAULT_CONFIG
from finance.server import make_server
from finance.storage import Store


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)
        self.server = make_server(self.store, port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.tmp.cleanup()

    def request(self, path, data=None, headers=None):
        client = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        hdrs = {"Content-Type": "application/json", **(headers or {})}
        client.request("GET" if data is None else "POST", path, body=None if data is None else json.dumps(data), headers=hdrs)
        response = client.getresponse()
        body = response.read()
        content_type = response.getheader("Content-Type")
        status = response.status
        client.close()
        return status, json.loads(body) if "json" in content_type else body

    def holding(self):
        return {"name": "Quality", "isin": "IE00BP3QZ601", "bucket": "quality", "value": 1000, "currency": "EUR", "as_of": "2024-01-01"}

    def test_health_static_and_empty_state(self):
        self.assertEqual(self.request("/api/health")[1]["status"], "ok")
        self.assertIn(b"Depotblick", self.request("/")[1])
        self.assertEqual(self.request("/api/state")[1]["state"]["holdings"], [])

    def test_demo_is_isolated(self):
        self.assertTrue(self.request("/api/demo")[1]["analysis"]["demo"])
        self.assertEqual(self.request("/api/state")[1]["state"]["revision"], 0)
        self.assertEqual(self.store.read()["holdings"], [])

    def test_preview_commit_and_persistence(self):
        csv = "name,value,isin,as_of\nQuality,1000,IE00BP3QZ601,2024-01-01"
        status, inspection = self.request("/api/inspect", {"csv": csv})
        self.assertEqual(status, 200)
        status, preview = self.request("/api/preview", {"csv": csv, "mapping": inspection["mapping"]})
        self.assertEqual(status, 200)
        self.assertEqual(self.store.read()["holdings"], [])
        status, saved = self.request("/api/holdings", {"holdings": preview["holdings"], "revision": 0})
        self.assertEqual(status, 200)
        self.assertEqual(saved["analysis"]["total"], 1000)
        self.assertEqual(Store(self.tmp.name).read()["holdings"][0]["bucket"], "quality")

    def test_repeat_import_replaces_and_preserves_manual_cash(self):
        self.assertEqual(self.request("/api/cash", {"amount": 1000, "as_of": "2024-01-01", "revision": 0})[0], 200)
        payload = {"holdings": [self.holding()], "revision": 1}
        self.assertEqual(self.request("/api/holdings", payload)[0], 200)
        payload["revision"] = 2
        status, saved = self.request("/api/holdings", payload)
        self.assertEqual(status, 200)
        self.assertEqual(len(saved["state"]["holdings"]), 2)
        self.assertEqual(saved["analysis"]["total"], 2000)

    def test_ing_duplicate_currency_header_preview_and_commit(self):
        csv = (Path(__file__).resolve().parent.parent / "examples/ing-holdings.csv").read_text()
        status, inspection = self.request("/api/inspect", {"csv": csv})
        self.assertEqual(status, 200)
        self.assertEqual(inspection["mapping"]["currency"], "Währung (Kurswert)")
        request = {"csv": csv, "mapping": inspection["mapping"], "german": True}
        self.assertEqual(self.request("/api/preview", request)[0], 400)
        status, preview = self.request("/api/preview", {**request, "valuation_date": "2024-01-02"})
        self.assertEqual(status, 200)
        self.assertEqual(self.store.read()["revision"], 0)
        status, saved = self.request("/api/holdings", {"holdings": preview["holdings"], "revision": 0})
        self.assertEqual(status, 200)
        self.assertEqual(saved["analysis"]["total"], 20000)
        self.assertEqual(saved["state"]["holdings"][0]["cost_basis"], 9000)
        self.assertEqual(saved["state"]["holdings"][0]["quantity"], 100)

    def test_invalid_import_leaves_prior_data_intact(self):
        row = self.holding()
        row["bucket"] = ""
        self.assertEqual(self.request("/api/holdings", {"holdings": [row], "revision": 0})[0], 400)
        self.assertEqual(self.store.read()["revision"], 0)

    def test_stale_revision_does_not_overwrite(self):
        payload = {"holdings": [self.holding()], "revision": 0}
        self.assertEqual(self.request("/api/holdings", payload)[0], 200)
        self.assertEqual(self.request("/api/holdings", payload)[0], 409)
        self.assertEqual(self.store.read()["revision"], 1)

    def test_currency_change_blocked_with_existing_data(self):
        self.request("/api/holdings", {"holdings": [self.holding()], "revision": 0})
        config = copy.deepcopy(DEFAULT_CONFIG)
        config["currency"] = "USD"
        self.assertEqual(self.request("/api/config", {"config": config, "revision": 1})[0], 400)
        self.assertEqual(self.store.read()["config"]["currency"], "EUR")

    def test_cross_site_posts_and_rebinding_rejected(self):
        self.assertEqual(self.request("/api/cash", {}, {"Origin": "https://unrelated.example"})[0], 403)
        self.assertEqual(self.request("/api/state", headers={"Host": "unrelated.example"})[0], 403)
        self.assertEqual(self.request("/api/cash", {}, {"Content-Type": "text/plain"})[0], 415)

    def test_no_static_path_traversal(self):
        self.assertEqual(self.request("/../finance/storage.py")[0], 404)
        self.assertEqual(self.request("/.data/portfolio.sqlite3")[0], 404)

    def test_history_import_and_export(self):
        csv = (Path(__file__).resolve().parent.parent / "examples/history.csv").read_text()
        status, data = self.request("/api/history", {"csv": csv, "currency": "EUR", "revision": 0})
        self.assertEqual(status, 200)
        self.assertEqual(data["analysis"]["performance"]["observations"], 3)
        self.assertAlmostEqual(data["analysis"]["performance"]["total_return"], (1.02*104000/103000-1)*100)
        self.assertEqual(self.request("/api/export")[1]["schema_version"], 2)

    def test_scheduled_report_exit_status_and_atomic_file(self):
        path = Path(self.tmp.name) / "report.json"
        process = subprocess.run([sys.executable, "-m", "finance.report", "--data-dir", self.tmp.name, "--output", str(path), "--check"], capture_output=True, text=True)
        self.assertEqual(process.returncode, 2, process.stderr)
        report = json.loads(path.read_text())
        self.assertIn("no_holdings", {a["code"] for a in report["alerts"]})
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_transactions_require_baseline_and_are_idempotent(self):
        csv = "id,date,type,instrument_id,quantity,price,amount,fee,currency\ndep,2024-01-02,DEPOSIT,,0,0,500,0,EUR"
        self.assertEqual(self.request("/api/transactions/preview", {"csv": csv})[0], 400)
        self.assertEqual(self.request("/api/transactions", {"csv": csv, "revision": 0})[0], 400)
        self.request("/api/holdings", {"holdings": [self.holding()], "revision": 0})
        status, preview = self.request("/api/transactions/preview", {"csv": csv})
        self.assertEqual((status, preview["added"]), (200, 1))
        self.assertEqual(self.store.read()["revision"], 1)
        self.assertEqual(self.request("/api/transactions", {"csv": csv, "revision": 1})[0], 200)
        self.assertEqual(self.request("/api/transactions", {"csv": csv, "revision": 2})[0], 200)
        self.assertEqual(len(self.store.read()["transactions"]), 1)

    def test_quotes_config_reports_and_currency_protection(self):
        csv = "symbol,date,close,adjusted_close,currency,source\nWORLD,2024-01-01,100,100,EUR,Fixture"
        self.assertEqual(self.request("/api/quotes", {"csv": csv, "revision": 0})[0], 200)
        config = {**DEFAULT_CONFIG, "currency": "USD"}
        self.assertEqual(self.request("/api/config", {"config": config, "revision": 1})[0], 400)
        self.assertEqual(self.request("/api/market", {"market": {"provider": "csv", "symbols": {"unknown": "X"}}, "revision": 1})[0], 400)
        status, data = self.request("/api/report?mode=weekly")
        self.assertEqual(status, 200)
        self.assertIn("Wochenbericht", data["markdown"])
        self.assertEqual(data["report"]["decision"]["label"], "DATEN PRÜFEN")
        self.assertEqual(self.request("/api/report?mode=invalid")[0], 400)

    def test_history_restart_archives_original_baseline(self):
        from test_expansion import fixture
        from finance.market import revalue
        from datetime import date
        state = fixture()
        revalue(state, today=date(2024, 1, 2))
        self.store.change(0, lambda s: s.update(state))
        status, data = self.request("/api/history/restart", {"revision": 1})
        self.assertEqual(status, 200)
        self.assertEqual(data["state"]["automatic_history"], [])
        archive = data["state"]["history_archives"][0]
        self.assertEqual(archive["opening_holdings"], state["opening_holdings"])
        self.assertEqual(archive["history"], state["automatic_history"])

    def test_monitor_failure_is_reported_without_overwriting_holdings(self):
        self.request("/api/holdings", {"holdings": [self.holding()], "revision": 0})
        before = self.store.read()["holdings"]
        status, data = self.request("/api/monitor", {"update": True, "use_cache": True, "revision": 1})
        self.assertEqual(status, 200)
        self.assertEqual(data["state"]["holdings"], before)
        self.assertIsNotNone(data["analysis"]["monitor"]["last_error"])
        self.assertEqual(data["analysis"]["decision"]["label"], "DATEN PRÜFEN")

    def test_add_and_remove_custom_bucket_preserves_existing_targets(self):
        status, data = self.request("/api/buckets", {"label": "Europa-Aktien", "asset_class": "equity", "revision": 0})
        self.assertEqual(status, 200)
        key = next(iter(data["state"]["config"]["custom_buckets"]))
        self.assertEqual(data["buckets"][key], "Europa-Aktien")
        self.assertEqual(data["state"]["config"]["targets"][key], 0)
        self.assertEqual(data["state"]["config"]["targets"]["safe"], 50)
        self.assertFalse(data["state"]["config"]["targets_confirmed"])
        self.assertEqual(self.request("/api/buckets/remove", {"key": "stocks", "revision": 1})[0], 400)
        status, removed = self.request("/api/buckets/remove", {"key": key, "revision": 1})
        self.assertEqual(status, 200)
        self.assertNotIn(key, removed["buckets"])

    def test_used_custom_bucket_cannot_be_removed_or_reclassified(self):
        _, data = self.request("/api/buckets", {"label": "Anleihen", "asset_class": "non_equity", "revision": 0})
        key = next(iter(data["state"]["config"]["custom_buckets"]))
        self.assertEqual(self.request("/api/holdings", {"holdings": [{**self.holding(), "bucket": key}], "revision": 1})[0], 200)
        self.assertEqual(self.request("/api/buckets/remove", {"key": key, "revision": 2})[0], 400)
        config = copy.deepcopy(self.store.read()["config"])
        config["custom_buckets"][key]["asset_class"] = "equity"
        self.assertEqual(self.request("/api/config", {"config": config, "revision": 2})[0], 400)
        self.assertEqual(self.store.read()["revision"], 2)

    def test_custom_bucket_import_and_new_stock_category(self):
        _, data = self.request("/api/buckets", {"label": "Europa-Aktien", "asset_class": "equity", "revision": 0})
        key = next(iter(data["state"]["config"]["custom_buckets"]))
        csv = f"name,value,bucket,as_of\nExample,1000,{key},2024-01-01"
        _, inspection = self.request("/api/inspect", {"csv": csv})
        _, preview = self.request("/api/preview", {"csv": csv, "mapping": inspection["mapping"]})
        self.assertEqual(preview["holdings"][0]["bucket"], key)
        self.assertEqual(self.request("/api/holdings", {"holdings": [{**self.holding(), "bucket": "stocks"}], "revision": 1})[0], 200)

    def test_recommendation_endpoint_is_read_only_and_blocks_missing_data(self):
        before = self.store.read()
        status, data = self.request("/api/recommendations", {"amount": 500, "fee": 1, "allow_sales": True})
        self.assertEqual(status, 200)
        self.assertEqual(data["status"], "blocked")
        self.assertEqual(data["rows"], [])
        self.assertEqual(self.store.read(), before)
        self.assertEqual(self.request("/api/recommendations", {"allow_sales": "yes"})[0], 400)
        self.assertIn(b"trade-form", self.request("/decisions.js")[1])


if __name__ == "__main__":
    unittest.main()
