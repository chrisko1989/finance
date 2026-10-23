# Depotblick — Anlage- und Entwicklungsregeln

## Ziel und Portfolio

Langfristig einen möglichen Mehrertrag des Aktienanteils gegenüber einer klar benannten MSCI-World-Total-Return-Referenz prüfen. Keine Rendite- oder Verlustgarantie. Das 50/50-Muster für langfristige Anlagen: World 17,5 %, Quality 10 %, Momentum 10 %, Small Caps 7,5 %, EM IMI 5 %, Sicherheit 50 %. Die ursprünglichen 35/20/20/15/10 sind die Zusammensetzung innerhalb des Aktienanteils. Gespeicherte Nutzerziele haben Vorrang vor Defaults. Persönliche Risikoprofile und Depotdaten gehören nicht in veröffentlichte Projektdateien.

## Entscheidungsregeln

- Tagesbewegungen oder Schlagzeilen allein begründen keinen Trade. Unterperformance ist allein kein Bruch der Anlagethese.
- Neue Einzahlungen beim Rebalancing bevorzugen. Ihr Effekt auf den Gesamtdepotwert muss mitgerechnet werden.
- Einzelaktien und weitere Aktien-ETFs zählen zur Aktienquote. Eigene Bausteine haben eine ausdrückliche Anlageart; andere Anlagen sind nicht automatisch sicher. Bestehende Gewichte bei Erweiterungen erhalten, neue Kategorien zunächst mit 0 % anlegen.
- Konkrete Kauf-/Verkaufsbeträge nur mit vollständigen Daten und ausdrücklich bestätigten Zielgewichten. Verkäufe sind im Plan standardmäßig ausgeschaltet; Gebühren, Mindestbeträge und verfügbare Mittel berücksichtigen. Keine automatische Auswahl einzelner Wertpapiere innerhalb eines Bausteins.
- Marktlage aus bereinigten Referenzkursen separat beschreiben: 200-Tage-Mittel, 20-Tages-Schwankung, Hoch der letzten 252 Beobachtungen. Prüfregeln sind keine getestete Timing-Strategie und ändern keine Zielgewichte. Fehlende oder veraltete Daten offenlegen; keine Nachrichten-/Fundamentalanalyse erfinden.
- Abweichungen je Baustein über 5 Prozentpunkte ODER über 25 % relativ zum Ziel prüfen. Bestehende, ausdrücklich geänderte Nutzerschwellen respektieren.
- Rückgänge ab 10 / 15 / 20 % abgestuft beobachten / analysieren / ausführlich prüfen. Niemals automatisch verkaufen.
- Entscheidung: HALTEN, BEOBACHTEN oder REBALANCING PRÜFEN. Fehlende, veraltete oder widersprüchliche Daten führen zu DATEN PRÜFEN, niemals zu einer beruhigenden Anlageentscheidung.
- Jede Einschätzung nennt Zeitraum, Bewertungsstand, Vergleichsquelle, Gründe und Grenzen. Keine erfundenen Konfidenzwerte oder Kausalgeschichten. Quellen und Hypothesen unterscheiden.
- Vor einem tatsächlichen Trade Kosten, Steuern und Risikobudget prüfen. Steuerliche FIFO-Ergebnisse nicht aus vereinfachten Buchgewinnen ableiten.
- Keine Orderausführung, kein Brokerlogin, keine Veröffentlichung von Depotdaten und kein Versand an externe Empfänger ohne entsprechenden Nutzerauftrag.

## Daten und Betrieb

- ING; EUR als Referenzwährung. Kostenloser Kursadapter als Einstieg. Benachrichtigungen vorerst ausschließlich im Dashboard.
- Yahoo ist eine optionale, nicht garantierte Datenquelle; einen MSCI-World-ETF als Näherung ausdrücklich als ETF-Referenz benennen. Ein Kurs-/ISIN-Mapping nie aus dem Namen erraten.
- Keine historische Rendite aus heutigen Stückzahlen rückrechnen. Transaktionen nur nach dem jeweiligen Ausgangsstand anwenden, keine Doppelerfassung.
- Sicherheitsbeträge und Transaktionen stammen aus Nutzereingaben, nicht aus einer Bankverbindung. Datenalter und Abgleichdatum offenlegen.
- Reale Daten nur in ignorierten Datenverzeichnissen speichern. Schemaänderungen müssen bestehende Daten erhalten. Tests und Demo nutzen isolierte Datenbanken.
- Bestehenden Checkout verwenden; Cloud-Aufgaben sind isoliert. Keine Git-Worktrees ohne ausdrücklichen Auftrag.

## Entwicklung und Prüfung

Python >=3.11, Standardbibliothek; statisches JavaScript. Aus dem Repository: `python -m unittest discover -s tests -v`, `node --check static/app.js`, `node --check static/expansion.js`, `node --check static/decisions.js`. Zusätzlich `python scripts/browser_smoke.py`, wenn Playwright und Chromium vorhanden sind. Lokale Sockets können eine unterstützte Sandbox-Freigabe benötigen. Netzwerkfehler nicht durch Abschalten von TLS-Prüfungen umgehen. Keine Live-Datenprüfung behaupten, wenn nur Fixtures geprüft wurden.
