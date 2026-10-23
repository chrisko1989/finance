# Depotblick 0.3: Start and validate

Use the existing checkout at /workspace/finance. Read AGENTS.md and README.md. Each cloud task is isolated; do not create a Git worktree unless explicitly requested. Preserve user files, settings and portfolio data.

## Runtime and persistence
Python 3.11 or newer; standard library only. No dependency installation, frontend build, secrets or bank credentials. Node is only used for optional JS syntax checks. The project repository is chrisko1989/finance, main branch. A Git checkout contains source code, not private portfolio data. Cloud snapshot restoration has not independently been verified.

Private data: /workspace/finance/.data/portfolio.sqlite3. Never seed it with demo or example files. Alternative data directories must be passed consistently to both processes using --data-dir. Schema 2 preserves user data and adds the ledger, quotes, separate automatic history and local notices. Before manual migrations, use Store.backup; do not copy an actively modified database unsafely.

## Start both processes
First inspect whether the expected app and scheduler already run. Verify identity through the health endpoint and scheduler heartbeat. Do not kill unrelated processes or start duplicate workers.
Start each command in a managed long-lived execution session from /workspace/finance:

```bash
python -m finance.server --port 8000
```

```bash
python -m finance.monitor --daemon
```

The server binds only to 127.0.0.1. The Unix scheduler locks its data directory and records a heartbeat. Local sockets and network access can require supported sandbox escalation. Do not weaken TLS or application security to bypass restrictions.

The scheduler uses Europe/Berlin: weekdays 22:15 quote update, valuation and daily report; Sunday 18:00 weekly report. Notices remain solely in the dashboard. A successful process start does not mean usable portfolio data exists. No permanent 24/7 hosting has been configured. Processes must be restarted after environment restoration; old missed dates are not backfilled with current holdings.

## Readiness checks
Use internal local HTTP requests; do not create localhost preview links in onboarding. Run:

```bash
python - <<'PY'
import json, urllib.request
for path in ['health', 'state', 'demo', 'report?mode=weekly']:
    with urllib.request.urlopen('http://127.0.0.1:8000/api/' + path, timeout=5) as response:
        data = json.load(response)
    if path == 'health':
        assert data['status'] == 'ok' and data['version'] == '0.3.0'
    elif path == 'state':
        assert data['state']['demo'] is False
        assert isinstance(data['analysis']['alerts'], list)
        assert data['analysis']['scheduler']['running']
    elif path == 'demo':
        assert data['analysis']['demo'] is True
        assert len(data['state']['holdings']) == 6
        assert data['analysis']['performance']['observations'] == 53
    else:
        assert data['report']['mode'] == 'weekly'
print('Application, dashboard state, demo and scheduler verified.')
PY
```

Do not print holdings or account information merely for readiness. The real depot was empty at setup; future user imports must survive restarts. Demo data is fictional and read-only.

## Validation after relevant changes
```bash
python -m unittest discover -s tests -v
node --check static/app.js
node --check static/expansion.js
node --check static/decisions.js
python scripts/browser_smoke.py
```

92 unit/HTTP tests and isolated browser workflows passed, including the supplied ING header layout with repeated currency columns and an explicit valuation date. Playwright and Chromium are available on the prepared cloud machine, but are not app runtime dependencies. Browser checks use a temporary database and cover demo isolation, responsive layout, ING-style import, quantities, cash, settings, separate histories, contributions, quotes, transactions, reports, events, persistence, export, CSP and JS errors.

## Quotes, reports and financial assumptions
The user chose a free quote source, ING/EUR and dashboard-only notices. The custom network allowlist includes query1.finance.yahoo.com. Yahoo's public HTTPS endpoint needs no key. Only configured ticker symbols leave the application. On 2026-10-23 a live EUNL.DE fetch and isolated monitor run passed: 504 completed EUR daily observations through 2026-10-22 with adjusted closes. This does not verify other ETF mappings or guarantee availability. Avoid repetitive live calls during routine tests.

Before real valuation, import positions with quantities and a common opening date, record available transaction cash, explicitly verify ticker/exchange/currency mappings and name a total-return reference. EUNL.DE is an ETF proxy, not the MSCI index itself. No ticker guessing, bank synchronization, automatic trades or external notifications. Yahoo uses completed daily candles, not real-time trading alerts. CSV quotes remain available as a fallback.

Automatic personal return history starts at the first actual valuation; never backcast current units. Manual and automatic histories remain separate. Transactions are idempotent by ID and replay only after the opening snapshot. Changed opening holdings, mappings or retroactive ledger entries must not create false returns; archive the old valuation period when required. Explicit SPLIT and dividend entries are the user's responsibility. All cash flows use end-of-interval timing; README explains approximation limits. Automatic safety reference is unremunerated. No FX conversion.

Defaults: World 17.5%, Quality 10%, Momentum 10%, Small 7.5%, EM IMI 5%, safety 50%; editable, no guaranteed outperformance or drawdown cap. Rule defaults: bucket drift >5 percentage points OR >25% relative; drawdown stages 10/15/20%; missing/stale/inconsistent data means DATEN PRÜFEN. Existing user settings take priority.

```bash
python -m finance.monitor --mode weekly
python -m finance.monitor --update
```

The first checks stored data and saves local JSON/Markdown reports. The second additionally updates and values configured positions. Exit 0: no notices; 2: data/strategy notices; 1: update/technical failure. Missing initial holdings are expected data notices, not completed portfolio monitoring. The legacy finance.report JSON CLI remains supported.

The user-supplied ING header layout is supported and tested with synthetic rows (examples/ing-holdings.csv); a complete authentic export has not been tested. The five currency columns retain their association with cost, quote and market value. Zeit is not automatically treated as a valuation date. Full purchase-to-date total return/XIRR, holdings look-through, automatic news/macro analysis and fund metadata monitoring remain outside this version. Manually sourced events must be labelled as such; never infer market causes or promise the portfolio beats MSCI World.

## Custom allocations, proposals and market context (0.3)
The built-in stocks and other_equity buckets start with 0% targets; older allocation weights remain unchanged. Up to 20 custom buckets carry an explicit equity/non_equity class. Count only equity classes toward equity values, targets and ledger flows. Used or archived categories cannot be removed or reclassified. Mixed funds with variable equity shares are not decomposed. The non-equity part of the automatic reference remains unremunerated; it is not a model of bond or gold risks.

Before concrete trade proposals, targets_confirmed must be explicitly true and data checks must pass. Adding a category clears confirmation. New contributions are considered first. Sales and use of existing transaction cash require the planner option, default off. Suggestions are amounts per bucket, with estimated fees, minimum sizes and residual deviations; no stock selection, quantities, taxes, spreads or orders. Planning is read-only.

The market check uses adjusted daily benchmark prices: 20-return volatility, SMA200 distance and 252-observation drawdown. Thresholds (25% volatility, below SMA200, -15% drawdown) are descriptive heuristics, not a validated timing strategy. Market stress alone never creates a sale or changes targets. Missing/stale prices must remain unavailable. No news, company valuations or fundamental data. A live EUNL.DE market check passed on 2026-10-23 with 252 observations through 2026-10-23; no personal holdings were used or saved.
