"""Optional end-to-end UI check; requires Playwright and an installed Chromium."""
import json
import shutil
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from finance.server import make_server
from finance.storage import Store
from playwright.sync_api import expect, sync_playwright


def main():
    chromium = shutil.which("chromium") or shutil.which("google-chrome")
    if not chromium:
        raise SystemExit("Chromium ist für den optionalen Browsertest erforderlich.")
    screenshots = ROOT / ".runtime"
    screenshots.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="finance-browser-") as data:
        store = Store(data)
        server = make_server(store, 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(executable_path=chromium, headless=True, args=["--no-sandbox"])
                context = browser.new_context(viewport={"width": 1440, "height": 1080}, locale="de-DE", timezone_id="Europe/Berlin")
                page = context.new_page()
                errors, violations = [], []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("console", lambda msg: violations.append(msg.text) if "Content Security Policy" in msg.text else None)
                page.goto(f"http://127.0.0.1:{server.server_port}")
                expect(page.locator("#data-status")).to_have_text("Noch keine Depotdaten")
                expect(page.locator("#metric-total")).to_have_text("—")
                page.locator("#demo-toggle").click()
                expect(page.locator("#demo-banner")).to_be_visible()
                expect(page.locator("#performance-chart")).to_be_visible()
                page.locator("#chart-mode").select_option("equity")
                expect(page.locator("#chart-detail")).to_contain_text("Aktienanteil")
                page.locator("#chart-mode").select_option("total")
                page.screenshot(path=str(screenshots / "demo-desktop.png"), full_page=True)
                page.set_viewport_size({"width": 390, "height": 844})
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "Mobile layout overflows horizontally"
                page.screenshot(path=str(screenshots / "demo-mobile.png"), full_page=True)
                page.locator('.nav[data-page="import"]').click()
                expect(page.locator("#holdings-file")).to_be_disabled()
                page.locator("#demo-toggle").click()
                expect(page.locator("#metric-total")).to_have_text("—")
                assert store.read()["holdings"] == [], "Demo changed real data"
                page.set_viewport_size({"width": 1440, "height": 1080})
                page.locator('.nav[data-page="import"]').click()
                page.locator("#holdings-file").set_input_files(ROOT / "examples/ing-holdings.csv")
                expect(page.locator("#mapping-panel")).to_be_visible()
                expect(page.locator("#number-format")).to_have_value("german")
                expect(page.locator("#map-name")).to_have_value("Wertpapiername")
                expect(page.locator("#map-currency")).to_have_value("Währung (Kurswert)")
                expect(page.locator("#map-cost_basis")).to_have_value("Einstandswert")
                expect(page.locator("#map-as_of")).to_have_value("")
                expect(page.locator("#import-summary")).to_contain_text("ING-Spaltenformat erkannt")
                page.locator("#preview-import").click()
                expect(page.locator("#error")).to_contain_text("Datum")
                page.locator("#valuation-date").fill("2026-10-22")
                page.locator("#preview-import").click()
                expect(page.locator("#preview-table tr")).to_have_count(2)
                expect(page.locator("#preview-table select").first).to_have_value("quality")
                page.locator("#commit-import").click()
                expect(page.locator("#page-holdings")).to_be_visible()
                expect(page.locator("#holdings-table tr")).to_have_count(2)
                page.locator("#cash-amount").fill("20000")
                page.locator("#cash-date").fill("2026-10-22")
                page.get_by_role("button", name="Betrag speichern").click()
                expect(page.locator("#holdings-table tr")).to_have_count(3)
                assert store.read()["holdings"][-1]["value"] == 20000
                page.locator('.nav[data-page="settings"]').click()
                page.locator("#equity-band").fill("6")
                page.get_by_role("button", name="Strategie speichern").click()
                expect(page.locator("#toast")).to_contain_text("gespeichert")
                assert store.read()["config"]["equity_band_pp"] == 6
                page.locator('.nav[data-page="import"]').click()
                page.locator("#history-file").set_input_files(ROOT / "examples/history.csv")
                page.locator("#history-confirm").check()
                page.locator("#import-history").click()
                expect(page.locator("#page-overview")).to_be_visible()
                expect(page.locator("#performance-chart")).to_be_visible()
                expect(page.locator("#metric-return")).to_have_text("+3,0 %")
                page.reload()
                expect(page.locator("#metric-equity")).to_have_text("50,0 %")
                assert len(store.read()["history"]) == 3
                page.locator('.nav[data-page="holdings"]').click()
                with page.expect_download() as download:
                    page.locator("#export").click()
                exported = json.loads(Path(download.value.path()).read_text())
                assert len(exported["holdings"]) == 3
                assert exported["config"]["equity_band_pp"] == 6
                assert exported["holdings"][0]["cost_basis"] == 9000
                page.locator('.nav[data-page="planning"]').click()
                page.locator("#contribution-amount").fill("1000")
                page.locator("#contribution-fee").fill("1")
                page.locator('#contribution-form button').click()
                expect(page.locator("#contribution-table tr")).to_have_count(6)
                expect(page.locator("#contribution-note")).to_contain_text("Gebühren gesamt")
                page.locator('.nav[data-page="automation"]').click()
                page.locator("#market-provider").select_option("csv")
                page.locator("#benchmark-symbol").fill("WORLD.EXAMPLE")
                page.locator("#benchmark-label").fill("Fiktive World-ETF-Referenz, EUR, bereinigt")
                page.locator("#history-source").select_option("automatic")
                page.locator('[data-instrument="IE00BP3QZ601"]').fill("QUALITY.EXAMPLE")
                page.locator('[data-instrument="IE00BP3QZ825"]').fill("MOMENTUM.EXAMPLE")
                page.locator("#market-confirmed").check()
                page.locator("#market-form button").click()
                expect(page.locator("#toast")).to_contain_text("Kurszuordnung gespeichert")
                quote_header = "symbol,date,close,adjusted_close,currency,source\n"
                baseline_quotes = quote_header + "\n".join(f"{s}.EXAMPLE,2026-10-22,100,100,EUR,Browsertest" for s in ["QUALITY", "MOMENTUM", "WORLD"])
                page.locator("#quotes-file").set_input_files({"name": "quotes.csv", "mimeType": "text/csv", "buffer": baseline_quotes.encode()})
                page.locator("#quotes-import").click()
                expect(page.locator("#quote-count")).to_contain_text("3 Kursbeobachtungen")
                page.locator("#revalue-cache").click()
                expect(page.locator("#quote-count")).to_contain_text("1 automatische Depotbewertungen")
                page.locator('.nav[data-page="holdings"]').click()
                page.locator("#transactions-file").set_input_files(ROOT / "examples/transactions.csv")
                page.locator("#transactions-preview").click()
                expect(page.locator("#transactions-summary")).to_contain_text("2 neue Buchungen")
                page.locator("#transactions-commit").click()
                expect(page.locator("#transaction-count")).to_contain_text("2 Buchungen gespeichert")
                page.locator('.nav[data-page="automation"]').click()
                new_quotes = quote_header + "\n".join(f"{s}.EXAMPLE,2026-10-23,{v},{v},EUR,Browsertest" for s, v in [("QUALITY", 101), ("MOMENTUM", 102), ("WORLD", 100.5)])
                page.locator("#quotes-file").set_input_files({"name": "quotes.csv", "mimeType": "text/csv", "buffer": new_quotes.encode()})
                page.locator("#quotes-import").click()
                expect(page.locator("#quote-count")).to_contain_text("6 Kursbeobachtungen")
                page.locator("#revalue-cache").click()
                expect(page.locator("#quote-count")).to_contain_text("2 automatische Depotbewertungen")
                assert store.read()["automatic_history"][-1]["total_value"] == 41304
                page.locator("#report-mode").select_option("weekly")
                expect(page.locator("#report-text")).to_contain_text("Wochenbericht")
                page.locator("#run-report").click()
                expect(page.locator("#toast")).to_contain_text("Prüfung abgeschlossen")
                with page.expect_download() as report_download:
                    page.locator("#download-report").click()
                assert "Fondsvergleich" in Path(report_download.value.path()).read_text()
                page.locator("#events-file").set_input_files(ROOT / "examples/events.csv")
                page.locator("#events-import").click()
                expect(page.locator("#toast")).to_contain_text("Ereignishinweise gespeichert")
                page.evaluate("window.scrollTo(0, 0)")
                page.screenshot(path=str(screenshots / "automation-desktop.png"))
                page.set_viewport_size({"width": 390, "height": 844})
                page.locator('.nav[data-page="planning"]').click()
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "New mobile layout overflows horizontally"
                page.screenshot(path=str(screenshots / "planning-mobile.png"), full_page=True)
                page.reload()
                assert len(store.read()["automatic_history"]) == 2
                assert len(store.read()["history"]) == 3
                assert not errors, errors
                assert not violations, violations
                context.close()
                browser.close()
                print("Browser checks passed: empty state, isolated demo, desktop/mobile layout, ING-style import, cash, settings, separate histories, contributions, quote configuration/import/valuation, transactions, weekly report/download, events, persistence, export, CSP and JS errors.")
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    main()
