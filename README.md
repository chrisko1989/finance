# Depotblick 0.2

Lokaler Portfoliomonitor für das besprochene ETF-Musterportfolio: ING-orientierter CSV-Import, kostenlose Tageskurse, Buchungen, nachvollziehbare Renditeberechnung, Sparratenplan und Hinweise ausschließlich im Dashboard. Python ≥3.11, Standardbibliothek, keine Zugangsdaten oder externen Laufzeitpakete. Die Anwendung führt keine Orders aus.

## Vorschau

Die folgenden Screenshots zeigen ausschließlich fiktive Demodaten. Sie sind direkt im Repository gespeichert und auf GitHub sichtbar. Die Anwendung selbst ist damit noch nicht öffentlich gehostet.

![Depotblick: Portfolioübersicht mit fiktiven Demodaten](docs/screenshots/demo-desktop.png)

<details>
<summary>Mobilansicht anzeigen</summary>

<img src="docs/screenshots/demo-mobile.png" alt="Depotblick auf dem Smartphone mit fiktiven Demodaten" width="390">

</details>

## Starten

Aus dem Projektverzeichnis in zwei Terminals:

```bash
python -m finance.server --port 8000
```

```bash
python -m finance.monitor --daemon
```

Der Webdienst bindet an die lokale Loopback-Adresse auf Port 8000. Der zweite Prozess übernimmt die geplanten Prüfungen. Er läuft nur, solange der Rechner bzw. die Cloud-Umgebung läuft. Die Onboarding-Oberfläche bietet keine Webvorschau; für eine interaktive Nutzung die Anwendung auf einem eigenen Rechner starten. Dauerhaftes Hosting ist hier nicht eingerichtet.

Neue Datenbestände sind leer. Die Demo lädt fiktive Daten nur in die Ansicht; sie schreibt nichts ins echte Depot. Standard-Datenverzeichnis: `.data/`; beide Prozesse müssen dasselbe Verzeichnis verwenden. Abweichend jeweils `--data-dir /absoluter/pfad` angeben. Der Scheduler benötigt Unix/Linux (`fcntl`). Webdienst und Einmalberichte funktionieren unabhängig davon.

## Einstieg mit deinem ING-Depot

1. **Daten importieren:** Bestands-CSV auswählen, Spalten und Zahlenformat prüfen, unbekannte Wertpapiere zuordnen, Vorschau bestätigen. Für automatische Bewertungen werden Stückzahlen benötigt; ein reiner Gesamtwert reicht nur für die manuelle Übersicht. Ein echter ING-Export wurde noch nicht bereitgestellt: getestet wurde ein synthetisches ING-ähnliches Format.
2. **Positionen:** Verrechnungsguthaben ergänzen. Alle Ausgangsbestände einschließlich Guthaben brauchen für die automatische Fortschreibung dasselbe Datum. Keine Beträge doppelt erfassen. Das separate Guthaben ist das Konto, aus dem die Buchungen bezahlt werden. Nicht verfügbares Tages-/Festgeld gegebenenfalls als weitere `safe`-Position importieren.
3. **Kurse & Berichte:** Yahoo auswählen, ISINs den tatsächlich passenden Kurssymbolen einschließlich Börsenplatz zuordnen, EUR-Notierung und eine bereinigte World-Referenz prüfen und bestätigen. `EUNL.DE` ist ein mögliches ETF-Referenzsymbol, keine direkte MSCI-Indexreihe. Ein Symbol wird nicht aus einem Fondsnamen erraten.
4. **Kurse abrufen & bewerten:** Erst ein vollständig passender gemeinsamer Kursstand aktualisiert das Depot. Der automatische Renditeverlauf beginnt mit dieser ersten Bewertung; mindestens zwei Bewertungstage sind für Renditen erforderlich. Die abrufbaren zwei Jahre Fondskurse sind keine rekonstruierte persönliche Depothistorie.
5. Neue Buchungen regelmäßig importieren und Bestände mit ING abgleichen. Aktuelle Kurse bestätigen keine aktuellen Stückzahlen oder Bankguthaben. Der Scheduler bewertet ausschließlich die erfassten Bestände.

Eine manuell importierte Depot-Renditehistorie und die automatische Reihe bleiben getrennt. Im Menü „Renditehistorie“ wird gewählt, welche Reihe ausgewertet wird. CSV-Vorlagen in `examples/` enthalten ausschließlich fiktive Beispiele, niemals automatisch echte Daten ersetzen.

## Modell und Prüfregeln

| Baustein | Zielanteil am Gesamtdepot |
|---|---:|
| MSCI World | 17,5 % |
| World Quality | 10 % |
| World Momentum | 10 % |
| World Small Caps | 7,5 % |
| Emerging Markets IMI | 5 % |
| Sicherheit | 50 % |

Die ursprünglichen 35/20/20/15/10 beschreiben den Aktienanteil. Gewichte sind editierbar; vorhandene Nutzereinstellungen bleiben bei Schemaerweiterungen erhalten. Das Modell ist keine Garantie für einen Mehrertrag oder eine Verlustgrenze von 20–30 %. Ein separat vorhandener Notgroschen wird vorausgesetzt.

Standardregeln:

- Aktienquote außerhalb von ±5 Prozentpunkten zum Ziel prüfen.
- Bausteinabweichung **über 5 Prozentpunkte ODER über 25 % relativ zum Ziel** prüfen. Bei Ziel 0 zählt ein positiver Bestand als Abweichung.
- Aktueller Rückgang vom Höchststand: ab 10 % beobachten, ab 15 % analysieren, ab 20 % ausführlicher Portfolio-Check. Eine zusätzliche eigene Rückgangsschwelle ist einstellbar. Kein automatischer Verkauf.
- Kursstand älter als zwei Wochentage oder Bestandsabgleich älter als sieben Wochentage: Daten prüfen. Feiertage werden nicht als besondere Börsentage modelliert.
- Entscheidung: **HALTEN**, **BEOBACHTEN**, **REBALANCING PRÜFEN**. Unvollständige, widersprüchliche oder veraltete Daten haben Vorrang: **DATEN PRÜFEN**.

`AGENTS.md` hält diese Regeln für spätere Arbeiten mit Codex fest. Neue Mittel werden bevorzugt. Der Sparratenplan berücksichtigt den neuen Gesamtdepotwert, Gebühren je ETF-Kauf und Cent-Rundung. Er erstellt weder Verkaufsorders noch Stückzahlen; Steuern und brokerspezifische Mindestgrößen sind nicht enthalten. Beispiel: EM-Bestand 1.600 EUR bei 50.000 EUR Gesamtwert benötigt für 10 % Zielgewicht rund 3.777,78 EUR neue Mittel ausschließlich in EM, nicht nur die bisherige Ziellücke von 3.400 EUR.

## Bestands-CSV

```csv
name,isin,bucket,quantity,price,cost_basis,currency,as_of
Quality,IE00BP3QZ601,quality,10,100,900,EUR,2026-10-22
```

Spaltenzuordnung unterstützt auch ING-ähnliche Überschriften, Semikolon, Dezimalkomma, Tausenderpunkte, UTF-8/Windows-1252 und `sep=;`. Metadaten- und Summenzeilen vorher entfernen. `examples/ing-style.csv` ist kein bestätigter Originalexport.

- Alternativ `value` statt Stückzahl × Kurs. Ein zugeordneter Gesamtwert hat Vorrang; bei vorhandener Stückzahl wird der passende Preis daraus abgeleitet.
- `cost_basis` ist optional: gesamter Einstandswert der Position, nicht Stückpreis. Daraus entstehen Buchgewinne; nach Verkäufen wird der Einstandswert nach Durchschnittskosten reduziert. Keine steuerliche FIFO-Berechnung und keine Gesamtrendite seit Kauf inklusive realisierter Gewinne/Ausschüttungen.
- `bucket`: `world`, `quality`, `momentum`, `small`, `em`, `safe`. Bekannte ISINs werden vorgeschlagen; unbekannte Anlagen erfordern Zuordnung. Keine Live-Verifikation von Fondskosten/Stammdaten.
- Alle Werte in Referenzwährung; keine automatische Devisenumrechnung. Keine Shortpositionen, Kredite, Hebelprodukte oder Anleihenbewertung aus Prozentkursen.
- Identität ist die ISIN; ohne ISIN eine aus Name und Baustein abgeleitete ID. Doppelte Positionen derselben ISIN vor dem Import zusammenfassen. IDs stehen in der Kurszuordnung.
- Ein Import ersetzt den Ausgangsbestand der CSV-Positionen, erhält aber das separate Guthaben. Änderungen an Ausgangsbestand oder Kurszuordnung erfordern bei bestehender automatischer Historie einen neuen Bewertungszeitraum. Die bisherige Reihe wird unter „Neuen Bewertungszeitraum starten“ archiviert und bleibt im JSON-Export erhalten.

## Transaktionen

```csv
id,date,type,instrument_id,quantity,price,amount,fee,currency
einzahlung-1,2026-10-23,DEPOSIT,,0,0,1000,0,EUR
kauf-1,2026-10-23,BUY,IE00BP3QZ601,5,100,500,1,EUR
```

Exakte Spalten, Dezimalpunkte, ISO-Datum, Referenzwährung. Unterstützt: `BUY`, `SELL`, `DEPOSIT`, `WITHDRAWAL`, `DIVIDEND`, `INTEREST`, `FEE`, `TAX`, `SPLIT`. Die Datei ergänzt das Buchungsjournal; sie ersetzt es nicht.

| Typ | Eingabe / Wirkung |
|---|---|
| BUY / SELL | Positive Stückzahl und Preis; `amount = quantity × price`, Gebühr separat. Kauf belastet, Verkauf erhöht das Verrechnungsguthaben. |
| DEPOSIT / WITHDRAWAL | Positiver Betrag, keine Positions-ID; externer Geldfluss. |
| DIVIDEND | Bekannte Positions-ID, positiver Bruttobetrag; `fee` kann eine separat zu berücksichtigende Abzugssumme enthalten. Keine automatische Erfassung. |
| INTEREST | Positiver Betrag, keine Positions-ID, optionaler Abzug in `fee`. |
| FEE / TAX | Positiver Betrag, keine Positions-ID; separate Belastung des Verrechnungskontos. |
| SPLIT | Bekannte Positions-ID; `quantity` ist der Splitfaktor (z. B. 2), Preis/Betrag/Gebühr sind 0. |

Identische Buchungs-ID mit identischem Inhalt wird übersprungen; widersprüchlicher Inhalt wird abgelehnt. Buchungen am Ausgangsdatum und davor gelten als bereits im Ausgangsbestand enthalten. Am selben Tag gilt die importierte Reihenfolge; Einzahlungen vor finanzierungsabhängigen Käufen aufführen. Fehlendes Guthaben und Überverkäufe werden abgelehnt. Neue Anlagen vorher mit Stückzahl 0 im Ausgangsbestand registrieren; bei laufender Historie ist das ein neuer Bewertungszeitraum.

Ein Buchungsimport aktualisiert zunächst das Journal. Erst eine gemeinsame Kursbewertung aktualisiert Werte und Renditen. Buchungen zu einem früheren bereits verketteten Bewertungstag blockieren die weitere Renditeverkettung; den Zeitraum archivieren oder die jüngste Tagesbewertung mit korrigierten Daten erneut berechnen. Am selben Tag ersetzt eine Wiederholung die letzte automatische Bewertung ohne doppelte Geldflüsse. Ausschüttungen, Zinsen und Steuern müssen vollständig erfasst werden; hierfür besteht keine Bankanbindung.

## Kostenlose Tageskurse und CSV-Alternative

Yahoo Finance wird über HTTPS `query1.finance.yahoo.com` abgefragt, maximal zwei Jahre Tagesdaten. Es ist eine öffentliche, inoffizielle Quelle ohne Verfügbarkeitsgarantie. An den Anbieter gehen die konfigurierten Symbole, keine Depotbeträge, Kontodaten oder Buchungen. Kein API-Schlüssel. Die Cloud-Netzwerkfreigabe umfasst diese Domain; TLS-Prüfung bleibt aktiv.

Noch laufende Tageskerzen werden nicht verwendet; heutige Werte frühestens 15 Minuten nach gemeldetem regulärem Börsenschluss. Ein Abruf ist deshalb kein Echtzeit-Alarm. Für die Bewertung wird der jüngste gemeinsame Handelstag aller benötigten Positionen und der Referenz verwendet. Es gibt keine stillen Währungsumrechnungen oder Mischung unterschiedlich datierter Positionskurse. Fällt eine Quelle aus, bleiben die vorherige Bewertung und Historie erhalten; der Fehler wird sichtbar. Es gibt höchstens einen automatischen Wiederholungsversuch je HTTP-5xx-Fehler.

Kursdatei als Alternative:

```csv
symbol,date,close,adjusted_close,currency,source
QUALITY.EXAMPLE,2026-10-22,100,100,EUR,Fiktives Formatbeispiel
WORLD.EXAMPLE,2026-10-22,100,100,EUR,Fiktives Formatbeispiel
```

Optional `split_ratio` (Standard 1). `close` bewertet Positionen, `adjusted_close` dient dem Fonds-/Referenzrenditevergleich. Die Referenz benötigt bereinigte Kurse. Quellenkorrekturen ersetzen gleiche Symbol-/Datumspaare im Kursarchiv. Der Import schreibt zunächst nur Kurse; „Gespeicherte Kurse bewerten“ startet die Bewertung. Sicherheitspositionen ohne Symbol werden als Nutzerwerte fortgeschrieben. Erkannte Splits seit dem Ausgangsdatum brauchen eine passende SPLIT-Buchung; andere Kapitalmaßnahmen müssen manuell geprüft werden.

## Rendite, Risiko und Grenzen

Der automatische Verlauf beginnt mit der ersten erfolgreichen Bewertung. Heutige Stückzahlen werden niemals für frühere persönliche Depotwerte verwendet. Historische bereinigte Fondskurse erlauben einen separaten Fondsvergleich, keine persönliche Rendite und keine statistische Faktorattribution.

Die separat importierbare Renditehistorie erwartet:

```csv
date,total_value,equity_value,net_flow,equity_flow,world_index,safety_index
2026-09-30,100000,50000,0,0,100,100
2026-10-22,103000,52500,1000,1000,102,100.2
```

`total_value` umfasst den Sicherheitsanteil; `equity_value` nur Aktien. `net_flow` ist der externe Nettozufluss seit der vorigen Bewertung; `equity_flow` der Nettozufluss in den Aktienanteil einschließlich Umbuchungen aus dem Sicherheitsanteil. Die erste Beobachtung hat beide Flüsse 0. Interne Käufe sind kein externer Zufluss. Ausschüttungen an das erfasste Verrechnungskonto bleiben innerhalb des Gesamtdepots, verlassen aber den Aktienanteil. Kauf-/Verkaufsgebühren werden wirtschaftlich berücksichtigt; separate Steuern/Gebühren belasten das Gesamtdepot. Steuerliche Ergebnisse werden nicht simuliert.

Intervallrendite: `(Endwert − Geldfluss) / vorheriger Endwert − 1`, anschließend geometrische Verkettung. **Alle Geldflüsse gelten als am Intervallende erfolgt.** Bei tatsächlichen Zwischenzuflüssen sind zusätzliche Bewertungen nötig; sonst ist dies eine Näherung an die zeitgewichtete Rendite. Gilt besonders für mehrtägige Lücken. Einzahlungen werden nicht einfach als Gewinn gerechnet.

Die World-Referenz muss eine konsistente Total-Return-Reihe in Referenzwährung sein. Ein bereinigter ETF ist eine Näherung mit eigenen Kosten. Die gemischte Referenz startet mit den aktuellen Zielgewichten und wird danach ohne Rebalancing gehalten. Zieländerungen ändern diese Vergleichslinie rückwirkend. Die automatische Sicherheitsreferenz ist **unverzinst**; eine importierte `safety_index`-Reihe kann eine andere ausdrücklich benannte Sicherheitsreferenz abbilden. Quellen können nicht fachlich authentifiziert werden.

Kennzahlen:

- Letztes Tagesintervall, letzte sieben Tage, laufender Monat, seit Jahresbeginn, seit Historienbeginn. Ohne geeigneten Ausgangswert wird keine Teilperiode als volle Periode ausgegeben.
- Aktueller/maximaler Drawdown auf der geldflussbereinigten Reihe; nur vorhandene Beobachtungen zählen. Wochen-/Monatsdaten können zwischenzeitliche Verluste übersehen.
- Annualisierte Volatilität erst ab 20 ausreichend dichten Tagesintervallen, Stichprobenstandardabweichung × √252. Keine Annualisierung von Monatswerten als Tageswerte.
- Buchgewinn bei vorhandenem Einstandswert und Fondsvergleich auf gemeinsamen bereinigten Kursdaten.

Noch nicht enthalten: vollständige persönliche Gesamtrendite seit Kauf/XIRR, Durchschau auf einzelne Unternehmen/Länder/Sektoren, automatische Nachrichten-/Makroanalyse, automatische Fondsstammdaten- und Gebührenüberwachung, echtes ING-Buchungsformat oder Broker-Synchronisierung. Der Monitor kann weder Outperformance garantieren noch Verluste begrenzen.

## Hinweise, Ereignisse und Zeitplan

Alle Hinweise bleiben lokal. Ein Prüflauf protokolliert neue Hinweise; gleiche Hinweise werden nicht jedes Mal erneut angelegt. Neue Eskalationsstufen oder Wiederauftreten nach Auflösung erzeugen einen neuen Eintrag. Das Dashboard zeigt die jüngsten 20, gespeichert werden bis zu 100.

Manuell belegte Fondsereignisse lassen sich importieren:

```csv
id,date,instrument_id,type,title,url,status
hinweis-1,2026-10-22,,market,Fiktives Beispiel,https://example.com,resolved
```

Typen: `fund_closure`, `index_change`, `tracking_difference`, `aum_change`, `market`. `open` führt zu einem Prüfhinweis, `resolved` schließt denselben Hinweis. HTTPS-Quelle erforderlich; kein automatischer Abruf oder Wahrheitsnachweis. Tages-/Wochenberichte formulieren nur berechnete Fakten und erfasste Hinweise, keine erfundenen Marktursachen oder Konfidenzwerte.

Der laufende Scheduler verwendet **Europe/Berlin** einschließlich Sommerzeit:

- Mo–Fr 22:15: Kurse holen (wenn Yahoo eingerichtet), gemeinsam bewerten, Tagescheck und Dashboard-Hinweise aktualisieren.
- Sonntag 18:00: Wochenbericht aus dem gespeicherten Stand erstellen.
- Vor geplanten Läufen eine SQLite-Sicherung pro Datum. Abgeschlossene Läufe werden nicht nach jedem Neustart wiederholt. Frühere verpasste Tage werden nicht rückwirkend mit heutigen Beständen befüllt.
- Technische Fehler werden vermerkt; kein endloser Wiederholungsversuch am selben Tag. Ein manueller Prüflauf bleibt möglich.

Einmalige Läufe:

```bash
python -m finance.monitor --update
python -m finance.monitor --mode weekly
python -m finance.report --output .data/latest-report.json --check
```

`monitor --update` aktualisiert Kurse und Werte; ohne `--update` prüft es vorhandene Daten. Berichte unter `.data/reports/latest-daily.{json,md}` bzw. `latest-weekly.{json,md}`. `finance.report` bleibt als reiner JSON-Prüfbericht kompatibel. Exit 0: keine Hinweise; 2: Daten-/Strategiehinweise; 1: Aktualisierungs-/technischer Fehler. Bei leerem Depot ist „DATEN PRÜFEN“ erwartbar und keine abgeschlossene Portfolioüberwachung.

Der Scheduler hat eine Sperre gegen Doppelstarts und einen Heartbeat; das Dashboard zeigt aktiv/inaktiv. Ein grüner Prozessstatus sagt nichts über erfolgreiche Kursversorgung aus. Eine Cloud-Entwicklungsumgebung ist kein zugesicherter 24/7-Betrieb. Nach Wiederherstellung beide Prozesse neu starten. Für einen dauerhaft verfügbaren Linux-Rechner ist `deploy/depotblick-monitor.service.example` als anzupassende, nicht installierte Vorlage beigefügt.

## Daten, Sicherung und Entwicklung

Private Daten liegen in `.data/portfolio.sqlite3`, ignoriert von Git. Änderungen sind transaktional und mit einer Versionsnummer vor Überschreiben durch alte Browseransichten geschützt. Ungültige Importe werden abgelehnt. Schema 2 ergänzt bestehende Daten; Nutzerwerte werden nicht durch Defaults ersetzt. Der JSON-Export enthält Journal, Kurse, Konfiguration und archivierte automatische Historien. Ein JSON-Wiederimport ist nicht implementiert.

Sicherung während des Betriebs mit SQLite-Backup:

```bash
python - <<'PY'
from finance.storage import Store
Store('.data').backup('.data/backups/manuelle-sicherung.sqlite3')
PY
```

Die Sicherungsdatei darf noch nicht existieren. Zur Wiederherstellung beide Prozesse stoppen und eine private Sicherung als `.data/portfolio.sqlite3` zurückkopieren. Dateirechte 0600, Datenverzeichnis 0700 für neu erzeugte Verzeichnisse; keine Datenträgerverschlüsselung. Sicherungen haben aktuell keine automatische Löschfrist und sollten privat nach einer passenden Aufbewahrungsregel verwaltet werden. Keine Depotdateien, `.data`, `.runtime` oder Geheimnisse in Git aufnehmen. Der lokale Dienst hat keine Benutzeranmeldung; nicht unverändert öffentlich betreiben.

```bash
python -m unittest discover -s tests -v
node --check static/app.js
node --check static/expansion.js
python scripts/browser_smoke.py
```

62 Unit-/HTTP-Tests sowie Browserabläufe wurden geprüft; der optionale Browsercheck benötigt Playwright und Chromium, verwendet eine isolierte Datenbank und synthetische Daten. Node/Playwright sind keine Laufzeitabhängigkeiten. Der Live-Abruf von EUNL.DE wurde am 23.10.2026 erfolgreich geprüft (504 abgeschlossene Tageswerte, EUR, bis 22.10.2026); das bestätigt weder die Zuordnung anderer Fonds noch die zukünftige Verfügbarkeit.

Den vorhandenen Checkout verwenden, keine zusätzlichen Git-Worktrees. Das Projekt-Repository ist `chrisko1989/finance`, Hauptbranch `main`. Private Depotdaten und Laufzeitdateien gehören nicht ins Repository. Cloud-Startanweisungen sichern Start, Datenverzeichnis und Funktionsprüfung. Keine Beispieldaten beim Start ins echte Depot übernehmen.
