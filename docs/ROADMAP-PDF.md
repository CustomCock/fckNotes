# Roadmap: PDF-Funktionen vervollständigen (Blöcke V, W, X)

Stand: Analyse abgeschlossen, **noch kein Code geändert**. Diese Roadmap wartet auf das OK des Besitzers.
Block U bleibt für den KI-Assistenten reserviert. Die Benennung setzt die bestehenden Blöcke fort:

| Block | Priorität | Inhalt | Abstimmung |
|---|---|---|---|
| **V0–V11** | A | Fundament + alle A-Features | Roadmap-OK genügt |
| **W1–W9** | B | alle B-Features | jeden Block vor Start kurz abstimmen |
| **X1–X14** | C | alle C-Features | jeden Block vor Start kurz abstimmen |

---

## 1. Ausgangslage (Analyse der Codebasis)

**Bibliotheken heute**
- **Anzeigen, Text, Suche im Dokument:** QtPdf (PDFium) aus PySide6. Liefert Seitenbilder, Text pro Seite und
  Zeichenrahmen. Schriftgrößen und Seitenobjekte (Bilder, Textobjekte) gibt es darüber **nicht**.
- **Schreiben:** pypdf 6.19.0 (BSD-3, rein Python), exakt gepinnt. Kann Seiten, Anmerkungen, Formulare,
  Lesezeichen, Seitenbeschriftungen, Anhänge, leere Seiten und AES-256-Verschlüsselung (R6, über `cryptography`).
  pypdf schreibt **keine Objekt-Streams** und hat **keine Prüfung** wie `qpdf --check`.
- **Kryptografie:** `cryptography` ist schon da (für `.ntx`).

**Muster, auf denen alles aufbaut**
- `notex/core/pdf*.py` sind Qt-frei: Bytes rein, Bytes raus. Getestet wird mit `tests/pdfhelp.py`, das die
  Test-PDFs zur Laufzeit erzeugt.
- `PdfPage` (`notex/ui/pdf_view.py`) hält das PDF als Bytes im Speicher. `_run(funktion, …)` erzeugt eine neue
  Fassung und legt die alte auf den Rückgängig-Stapel (`MAX_UNDO = 40` ganze Fassungen). Gespeichert wird erst
  mit `Ctrl+S`; vor dem ersten Überschreiben landet das Original im Papierkorb (send2trash).
- **Anmerkungen** (`pdfannot.py`) haben ein eigenes Erscheinungsbild (`/AP`) mit **Helvetica, WinAnsi,
  nicht eingebettet**. Das ist ein Widerspruch zu Regel 6 und wird in V0 gelöst.
- **Formulare** (`pdfforms.py`): ausfüllen, Text-, Kästchen- und Unterschriftsfelder anlegen, einbrennen.
- **Eingefügtes** (`pdfobjects.py`, `ui/pdf_objects.py`): auswählen, verschieben, Größe ändern, löschen.
- **Diagramme** (`pdfdiagram.py`, `core/diagram/`): Stempel-Anmerkung mit Vektorbild und Modell.
- **Schwärzen** (`pdfredact.py`): betroffene Seiten werden als Bild neu erzeugt. `leftovers()` prüft heute
  Seitentext, Anmerkungen, Feldwerte, Lesezeichen, Info und XMP. **Es fehlen:** Anhänge, Ebenen-Namen,
  Struktur-Tags/Alt-Texte, rohe Stream-Suche und frühere Speicherstände.
- **Passwort-PDFs** lassen sich heute nur ansehen (`edit_block_reason`).
- **Plattform:** `notex/paths.py` legt alles in den App-Ordner. Einen Ausweichpfad gibt es nicht (Regel 1 fehlt).
  Die CI testet bereits auf `ubuntu-latest` und `windows-latest` (`tests.yml`). Das Release baut eine
  Windows-ZIP und ein Linux-tar.gz (auf `ubuntu-22.04` wegen älterem glibc), **noch kein AppImage**.
- **Rückgängig bei großen PDFs:** 40 ganze Fassungen eines 80-MB-PDFs wären über 3 GB. Ab 256 MB ist Bearbeiten
  heute gesperrt. Für die geforderten Tests mit über 500 Seiten braucht der Stapel ein **Speicherbudget**.

---

## 2. Was die vorhandenen Bibliotheken können

| Feature | pypdf + QtPdf reicht? | Was fehlt |
|---|---|---|
| A1 Ordnersuche | 🟡 | Text-Extraktion außerhalb der UI und ohne Qt (Worker-Thread, Tests) |
| A2 Lesezeichen | ✅ (Überschriften-Vorschlag 🟡) | Schriftgröße je Zeichen für „aus Überschriften“ |
| A3 Anhänge | ✅ | – |
| A4 Drucken | ✅ QtPrintSupport ist im PySide6-Wheel | Linux braucht `libcups` vom System |
| A5 Vorlesen | ✅ QtTextToSpeech ist im PySide6-Wheel | Linux braucht speech-dispatcher vom System |
| A6 Seitenwerkzeuge | ✅ | weiße Ränder erkennen: Seitenbild von PDFium |
| A7 Bilder | 🟡 | Bilder im Inhalt finden, dekodieren, ersetzen (Bildbibliothek) |
| A8 Links | ✅ | – |
| A9 Wasserzeichen usw. | 🟡 | **eingebettete Schrift** (Untermenge bilden) |
| A10 Kommentieren | ✅ (Schrift wie A9) | – |
| A11 Formulare | ✅ | – |
| A12 Passwort | ✅ AES-256 R6 schreiben, RC4/AES-128 lesen | – |
| A13 Muster-Schwärzen | ✅ (Treffer über PDFium-Text) | – |
| A14 Komprimieren/Konvertieren | 🟡 | Objekt-Streams, Bilder neu kodieren |
| B1 OCR | ❌ | OCR-Engine |
| B2 Schwärzen ohne Raster | ❌ | Content-Stream-Interpreter mit Glyphpositionen |
| B3 Vergleichen | 🟡 | Text mit Positionen (A1-Basis), Pixelvergleich |
| B4 Signaturen | ❌ | PAdES/CMS-Bibliothek |
| B5 Ebenen | ✅ (Lesen/Schreiben der OCGs); Anzeige 🟡 | QtPdf blendet Ebenen nicht um, siehe Risiko W2 |
| B6 Formate/Prüfregeln | ✅ (nativ in Python) | – |
| B7 Stapel | ✅ (Kette der vorhandenen Kernfunktionen) | – |
| B8 PDF/A | 🟡 | voll nur mit Ghostscript (AGPL), siehe W9 |
| B9 Scans verbessern | 🟡 | Bildbibliothek |
| B10 Korrekturzeichen | ✅ | – |
| C1 Text ändern | ❌ | baut auf B2 auf, dazu Schrift-Neusatz |
| C4 Office | 🟡 | DOCX-Schreiber, LibreOffice (optional, extern) |
| C9 Ausgabevorschau | ❌ | PDFium kann keine Farbauszüge, siehe X13 |
| C12 Zertifikatsverschlüsselung | ❌ | Public-Key-Security-Handler (pyHanko) |

---

## 3. Vorgeschlagene Abhängigkeiten

Größen: Wheel-Größe ≈ Zuwachs der ZIP. Im Ordner entpackt etwa das Doppelte. Ich messe vor jedem Block nach dem
Build nach und melde die echte Zahl.

| Paket | Lizenz | Win / Linux (Wheel) | Wofür | Block |
|---|---|---|---|---|
| **pypdfium2** | Apache-2.0 / BSD-3 (PDFium) | 3,9 / 3,7 MB | Text mit Positionen **und Schriftgröße** ohne Qt (Worker, Tests), Seitenobjekte (Bilder, Textobjekte), Rendern im Hintergrund | V0 |
| **fontTools** | MIT | 2,5 / 5,4 MB | Untermengen eingebetteter Schriften (Type0/CIDFontType2 + ToUnicode) | V0 |
| **Noto Sans** (Regular, Bold, Italic) | SIL OFL 1.1 | ≈ 1,5 MB | mitgelieferte Schrift (Regel 6) | V0 |
| **Pillow** | MIT-CMU | 7,2 / 6,3 MB | PDF-Bilder dekodieren/neu kodieren, Scans verbessern | V7 |
| **pikepdf** (bringt **lxml** mit) | MPL-2.0 (+ BSD-3) | 3,8 + 4,0 / 2,4 + 4,9 MB | Objekt-Streams, Reparatur kaputter PDFs, `qpdf --check` in Tests | V0 (Test), V9 (Laufzeit) |
| **pyHanko** + certvalidator, asn1crypto, oscrypto, aiohttp, tzlocal, uritools, certifi | MIT / Apache-2.0 | ≈ 3–4 MB (lxml schon da) | PAdES-Signaturen, Prüfung, Zertifikatsverschlüsselung | W8, X10 |
| **Tesseract 5** (Programm + deu/eng) | Apache-2.0 | Win ≈ 25–35 MB (ZIP), Linux ≈ 8–12 MB | OCR | W5 – **braucht dein OK (> 20 MB)** |

**Summe ohne Tesseract:** etwa +25 MB pro Plattform, verteilt auf mehrere Blöcke. Die ZIP wächst von ≈ 95 MB
auf ≈ 120 MB. Kein einzelner Block liegt über 20 MB, außer W5 (OCR).

**Bewusst nicht vorgeschlagen**
- **PyMuPDF/MuPDF** (AGPL-3.0, 20–25 MB): könnte B2, C1, OCR-Anbindung und Konvertierung in einem Paket. AGPL
  verlangt aber, dass das **ganze ausgelieferte Programm** unter AGPL-Bedingungen steht, also mit Quellcode und
  Hinweisen. Der fckNotes-Code bliebe MIT, das Release-Paket nicht mehr. Für reinen Eigengebrauch wäre das kein
  Problem, für ein MIT-Projekt ist es ein Bruch. Empfehlung: nicht nehmen. Falls B2/C1 mit eigener Lösung
  scheitern, frage ich vorher, ob wir doch umsteigen.
- **Ghostscript** (AGPL, ≈ 40 MB): wäre der Weg zu vollständiger PDF/A- und PDF/X-Konvertierung und zu
  Farbauszügen. Gleiches Lizenzproblem plus Größe. Empfehlung: nur optional **erkennen**, wenn installiert.
- **python-docx** (+ lxml): für C4 reicht ein kleiner eigener DOCX-Schreiber (zip + XML). Spart die Abhängigkeit.
- **numpy:** Gerade richten und Pixelvergleich gehen auf verkleinerten Bildern mit Pillow bzw. QImage.

**OCR-Alternative für Windows:** `Windows.Media.Ocr` ist eingebaut und offline (Pakete `winrt-*`, MIT,
≈ 1–2 MB). Die Sprachen hängen an den installierten Windows-Sprachpaketen, und Linux braucht trotzdem
Tesseract. Empfehlung: Tesseract auf beiden Systemen für gleiche Ergebnisse. Entscheidung bei W5.

---

## 4. Querschnitt (gilt für alle Blöcke)

- **Plattformschicht** `notex/platform/` mit `printing`, `speech`, `scanner`, `open_external`. Jede Komponente
  hat `available() -> (bool, Hinweis)`. Die UI graut aus und zeigt den Hinweis; nichts stürzt ab.
- **Hintergrundaufgaben:** eine gemeinsame Basis (`QThread`/Worker) mit Fortschritt und Abbrechen. Die
  bestehenden Worker (Suche, Scanner) machen es schon vor.
- **Netzwerk-Wächter:** ein Test, der Socket-Verbindungen während aller PDF-Tests verbietet, plus eine statische
  Prüfung, dass `notex/core/pdf*`, `notex/core/pdfx/*` und `notex/platform/*` kein `socket`, `urllib`, `http`,
  `requests` oder `aiohttp` importieren. Einzige Ausnahme: der RFC-3161-Zeitstempel in W8, getrennt in einem Modul
  und standardmäßig aus.
- **Fixtures** in `tests/fixtures/`: ein Skript `make_fixtures.py` erzeugt sie reproduzierbar. Kleine Dateien
  werden eingecheckt, die große (über 500 Seiten) entsteht beim Test und kommt nicht ins Repo. Dabei: kaputte
  (abgeschnittene, falsche xref), verschlüsselte (R6 AES-256, RC4/AES-128 nur zum Lesen), gescannte
  (nur Bild) und die bestehenden Formular/Arbeitsblatt-PDFs.
- **Prüfung im fremden Viewer:** `assert_valid_pdf()` ruft `pikepdf.Pdf.check()` auf. Das ist dieselbe Bibliothek
  wie `qpdf --check`. In der CI läuft zusätzlich das echte `qpdf --check` (Linux: apt, Windows: Chocolatey). Die
  manuelle Prüfanleitung kommt nach `docs/TESTPLAN-PDF.md`.
- **Schwärzen-Prüfung** (Regel 8): `notex/core/pdfaudit.py` mit einem **Register der Textablageorte**: Seitentext,
  Anmerkungen (`/Contents`, `/RC`, `/T`, Antworten, Erscheinungsbilder), Feldwerte (`/V`, `/DV`, `/Opt`, `/TU`),
  Lesezeichen, Anhänge (Namen, Beschreibungen, Inhalt), Ebenen-Namen, Info, XMP, Struktur-Tags (`/Alt`,
  `/ActualText`, `/E`, `/T`), Benannte Ziele, Seitenbeschriftungen, Rohsuche in allen **dekomprimierten
  Streams** und in den **Rohbytes** (frühere Speicherstände). Jedes neue Feature, das einen neuen Ablageort
  schafft, trägt sich dort ein. Ein Test stellt sicher, dass es eingetragen ist.
- **Eingebettete Schrift** (Regel 6): `notex/core/pdffont.py` bildet Untermengen von Noto Sans, schreibt
  Type0/CIDFontType2 mit ToUnicode und liefert Breiten für Umbruch und Zentrierung. **Bestehende Freitexte,
  Stempel und Formularfeld-Erscheinungsbilder** stellen in V0 darauf um. Ergebnis: echtes Unicode statt WinAnsi
  (z. B. Ł, Ş, Ω, →), auf Windows und Linux identisch.
- **Rückgängig-Budget:** Der Stapel wird nach Gesamtgröße begrenzt (Standard 512 MB, einstellbar), nicht nur
  nach Anzahl. Was nicht mehr in den Speicher passt, lagert er in eine Cache-Datei im Portable-Ordner aus.
- **Portable-Ausweichpfad** (Regel 1): `paths.py` prüft, ob der App-Ordner beschreibbar ist, und weicht sonst
  auf `%APPDATA%\fckNotes` bzw. `$XDG_CONFIG_HOME/fckNotes` aus. Die Statusleiste zeigt dann „Daten liegen in …“.
- **Passwörter** (Regel 7): Sie stehen nur in lokalen Variablen und nie im Log. Ehrlicher Hinweis: Python kann
  Zeichenketten nicht zuverlässig aus dem Speicher löschen. Ich halte die Lebensdauer so kurz wie möglich und
  speichere sie nirgends. Bei A12 muss das Passwort leben, bis gespeichert wird, weil pypdf es zum erneuten
  Verschlüsseln braucht.
- **Pro Block:** Kern-Tests (Qt-frei), UI-Tests, Command Palette, README-Tastenkürzel, Nutzer-Doku
  `docs/pdf/<feature>.md` (2–5 Sätze je Funktion), CHANGELOG, PROGRESS.md, ein Commit (Conventional Commits).

---

## 5. Blöcke Priorität A (V0–V11)

### V0 – Fundament (keine sichtbare neue Funktion, macht alles Weitere sauber)
- **Umfang:** Plattformschicht, Hintergrundbasis, Netzwerk-Wächter, Fixtures + `assert_valid_pdf`, `pdfaudit.py`
  (Regel 8, bestehendes Schwärzen hängt sich dran), `pdffont.py` + Umstellung der bestehenden Erscheinungsbilder,
  Rückgängig-Budget, Portable-Ausweichpfad. Dazu der Build: **Linux-AppImage** zusätzlich zum tar.gz und der
  Release-Workflow mit beiden Artefakten.
- **Dateien:** `notex/paths.py`, neu `notex/platform/*`, `notex/core/pdfaudit.py`, `notex/core/pdffont.py`,
  `notex/core/pdfannot.py`, `notex/core/pdfforms.py`, `notex/core/pdfredact.py`, `notex/ui/pdf_view.py`,
  `build.py`, `.github/workflows/*.yml`, `tests/fixtures/*`.
- **Abhängigkeiten:** pypdfium2, fontTools, Noto Sans; pikepdf nur als Test-Abhängigkeit (`requirements-dev.txt`).
- **Plattform:** AppImage-Bau auf `ubuntu-22.04` (glibc), getestet zusätzlich auf `ubuntu-latest`. Dazu ein
  Hinweis, dass AppImage FUSE braucht (Ausweichen: `--appimage-extract-and-run`, steht in der Doku).
- **Risiken:** Die Schrift-Umstellung ändert das Aussehen bestehender Anmerkungen leicht (Noto statt Helvetica).
  Alte, von fckNotes erzeugte Anmerkungen bleiben, wie sie sind, bis man sie bearbeitet.
- **Tests:** Schrift (Untermenge, ToUnicode, Text wieder extrahierbar), Audit findet Wörter an jedem Ablageort
  (je eine Fixture pro Ort), Budget, Ausweichpfad (schreibgeschützter Ordner simuliert), Netzwerk-Wächter.

### V1 – Seiten und Lesezeichen (A6, A2)
- **Umfang:**
  - A6: leere Seite (Größe wie Nachbarseite, A4 oder Letter), Seiten ersetzen in einem Schritt, Zuschneiden
    (aufziehen + mm-Eingabe; aktuelle Seite, Bereich, gerade/ungerade, alle; weiße Ränder automatisch, nur
    CropBox), Seitenbeschriftungen (Stil, Präfix, Startwert).
  - A2: Lesezeichen-Leiste bearbeitbar (anlegen auf Seite/Position, umbenennen, löschen, ziehen, verschachteln),
    „aus Überschriften erzeugen“ mit Vorschau (Schriftgröße über pypdfium2).
- **Dateien:** `pdfpages.py`, neu `pdfoutline.py`, `pdf_view.py` (Lesezeichen-Leiste), `pdf_edit.py` (Dialoge).
- **Abhängigkeiten:** keine neuen.
- **Risiken:** „Weiße Ränder“ bei Scans mit grauem Rand → Schwellwert einstellbar. Zuschneiden ist kein Schwärzen;
  das steht in der Doku und im Dialog.
- **Tests:** CropBox gesetzt und Inhalt unverändert (Text noch extrahierbar), Beschriftungen gelesen wie
  geschrieben, Lesezeichen-Baum Rundlauf, große Fixture (über 500 Seiten) für Ersetzen/Einfügen.

### V2 – Anhänge und Links (A3, A8)
- **Umfang:**
  - A3: Liste aller Anhänge (Dokument + Datei-Anmerkungen), extrahieren, hinzufügen, entfernen. Öffnen nur nach
    Bestätigung mit Typ und Warnung, über `open_external` der Plattformschicht. Ausführbare Dateien lassen sich
    nur speichern, nie öffnen.
  - A8: Link-Werkzeug (Rechteck aufziehen; Ziel: Seite, Lesezeichen, URL, Datei; Rahmen sichtbar/unsichtbar),
    bearbeiten, löschen. Links werden auch als Objekte (T1) auswählbar.
- **Dateien:** neu `pdfattach.py`, `pdflinks.py`, `pdfobjects.py`, `pdf_view.py`, `pdf_edit.py`.
- **Risiken:** Ablageort Anhänge ins Audit-Register. URL-Links werden **nicht angeklickt**; das macht erst X2
  (C3), dort mit Bestätigung.
- **Tests:** Rundlauf Anhang (Bytes identisch), Entfernen auch aus dem Namensbaum, Audit findet Text in Anhängen,
  Link-Ziele aller Arten.

### V3 – Kommentieren I: Stift, Formen, Stempel (A10, erster Teil)
- **Umfang:**
  - Freihand-Stift (Farbe, Stärke, Deckkraft, Glättung) und Radierer, der ganze Striche entfernt.
  - Formen als echte Anmerkungen: Rechteck, Ellipse, Linie, Pfeil, Polygon, Wolke (`/BE /S /C`). Einstellbar sind
    Rand, Füllung, Deckkraft und Linienstil (durchgezogen, gestrichelt, gepunktet).
  - Stempel: Genehmigt, Entwurf, Vertraulich, Erledigt, Geprüft. Eigene Stempel aus Text oder Bild, optional mit
    Datum und Benutzername; sie liegen in `<portable>/stamps/`.
- **Dateien:** `pdfannot.py` (Ink, Square, Circle, Line, Polygon, Stamp), neu `pdfstamps.py`, `pdf_view.py`
  (Werkzeuge), `pdf_objects.py` (verschieben/skalieren wie bisher).
- **Risiken:** Wolkenrand und gestrichelte Linien sehen in jedem Viewer nur dann gleich aus, wenn das eigene
  Erscheinungsbild gezeichnet wird. Das mache ich immer.
- **Tests:** Erscheinungsbild parsebar, Deckkraft über ExtGState, `assert_valid_pdf`, Radierer entfernt nur ganze
  Ink-Striche.

### V4 – Kommentieren II: Liste, Antworten, Status, Export, XFDF (A10, zweiter Teil)
- **Umfang:**
  - Seitenleiste „Kommentare“: sortieren und filtern nach Typ, Seite, Autor, Datum und Status; ein Klick springt
    zur Stelle.
  - Antworten (`/IRT`, `/RT /R`) und Status (offen/angenommen/abgelehnt/erledigt über `/State` + `/StateModel`).
  - Zusammenfassung als Markdown, CSV und PDF (Seite + Kommentar + zitierter markierter Text).
  - XFDF exportieren und importieren.
- **Dateien:** neu `pdfcomments.py`, `xfdf.py`, neue Seitenleiste `ui/pdf_comments.py`.
- **Risiken:** XFDF ist XML von außen. Es gelten dieselben Regeln wie beim draw.io-Import (kein DOCTYPE/ENTITY,
  Größenlimit).
- **Tests:** Rundlauf XFDF (eigene Datei und eine Acrobat-ähnliche), Threads bleiben verbunden, CSV-Export.

### V5 – Formulare (A11)
- **Umfang:**
  - Neue Feldtypen: Optionsfeld-Gruppe, Dropdown, Listenfeld, Schaltfläche (nur „Formular zurücksetzen“ und
    „Seite anspringen“).
  - Eigenschaften: Name, Pflicht, nur lesen, Standardwert, Optionen, Schrift/Größe, Ausrichtung.
  - Tab-Reihenfolge: Nummern einblenden, per Ziehen ändern, „nach Zeilen“/„nach Spalten“.
  - Daten als JSON, XFDF und FDF exportieren/importieren; „Antworten sammeln“ → CSV.
- **Dateien:** `pdfforms.py`, neu `pdfformdata.py`, `ui/pdf_objects.py`, `pdf_edit.py` (FieldDialog erweitern).
- **Risiken:** Viewer lesen die Tab-Reihenfolge aus `/Tabs` der Seite und der Reihenfolge in `/Annots`.
  Ich setze beides passend zueinander.
- **Tests:** jedes Feld in pypdf gelesen wie geschrieben, Optionsgruppe genau ein Wert, FDF/XFDF/JSON-Rundlauf,
  Sammeln aus drei ausgefüllten Kopien.

### V6 – Schutz: Passwort/Rechte und Muster-Schwärzen (A12, A13)
- **Umfang:**
  - A12: Öffnen-Passwort setzen, ändern, entfernen; Rechte-Passwort mit Rechten. Passwort-PDFs nach Eingabe
    **voll bearbeiten**. Beim Speichern fragen, ob der Schutz bleibt. Schreiben nur AES-256 (R6).
  - A13: Muster (E-Mail, Telefon DE/international, IBAN mit Mod-97, BIC, Kreditkarte mit Luhn, Datum, Steuer-ID mit
    Prüfziffer, PLZ, IP, URL) und eigene Regex (gespeichert). Vorschau: Treffer markiert und als Liste, einzeln
    abwählbar, dann anwenden. Danach automatisch die erweiterte Prüfung.
- **Dateien:** neu `pdfsecurity.py`, `redact_patterns.py`; `pdf_view.py` (Passwort im Bearbeiten-Modus),
  `pdf_edit.py` (RedactDialog erweitern).
- **Risiken:** Rechte-Einschränkungen sind nur ein Hinweis an Viewer und kein echter Schutz; das steht in Dialog
  und Doku. Lebensdauer des Passworts siehe Querschnitt.
- **Tests:** R6 lesbar mit falschem/richtigem Passwort, RC4/AES-128-Fixture nur lesbar, nie geschrieben;
  Validierer mit echten Prüfziffer-Beispielen und Fehltreffern.

### V7 – Bilder (A7)
- **Umfang:**
  - Bilder (PNG, JPG, WebP) frei einfügen, verschieben, skalieren (mit/ohne Seitenverhältnis), drehen.
    Umsetzung als Bild-Objekt wie die Unterschrift, einbrennbar.
  - Vorhandene Bilder im Seiteninhalt finden (pypdfium2), auswählen, ersetzen, zuschneiden (Clip), löschen und
    als Datei exportieren (JPEG/JPX unverändert, sonst PNG).
- **Dateien:** neu `pdfimages.py`, `pdfobjects.py`, `pdf_view.py`.
- **Abhängigkeiten:** **Pillow** (+7 MB).
- **Risiken:** Löschen aus dem Inhalt verlangt Content-Stream-Bearbeitung. Mehrfach verwendete Bilder (dasselbe
  XObject auf vielen Seiten) betreffen alle Stellen; ich frage nach. Bildmasken, SMask und Inline-Bilder: Inline-
  Bilder nur exportieren, nicht ersetzen (wird klar angezeigt).
- **Tests:** Rundlauf Export, Ersetzen ändert nur das gewählte Bild, Clip-Rechteck.

### V8 – Wasserzeichen, Kopf-/Fußzeilen, Seitenzahlen, Hintergrund (A9)
- **Umfang:** Wasserzeichen (Text oder Bild; Deckkraft, Drehung, Position, Größe; vor/hinter dem Inhalt;
  Seitenbereich), Kopf-/Fußzeilen links/mitte/rechts mit `{seite}`, `{seiten}`, `{datum}`, `{dateiname}`,
  `{titel}`, Hintergrund als Farbe oder Bild. Alles kommt in eine **eigene Ebene (OCG)** mit fckNotes-Kennung,
  damit „Entfernen“ und „Aktualisieren“ sauber gehen. Vorlagen werden gespeichert.
- **Dateien:** neu `pdfstamp_layers.py` (gemeinsame Basis, später auch für Bates in X6).
- **Risiken:** Das Entfernen findet nur von fckNotes erzeugte Ebenen. Fremde Wasserzeichen (z. B. von Acrobat)
  erkenne ich, wenn sie als `/Artifact /Watermark` markiert sind; sonst nicht. Das steht ehrlich in der Doku.
- **Tests:** Hinzufügen → Entfernen ergibt wieder den alten Seiteninhalt, Platzhalter, Ebene sichtbar in Viewern.

### V9 – Komprimieren und Konvertieren (A14)
- **Umfang:** Profile (Hohe Qualität, Standard, Klein, Eigene: Bild-DPI, JPEG-Qualität, Ungenutztes entfernen,
  Objekt-Streams) mit Vorher/Nachher-Größe; Bilder → PDF (Reihenfolge, Größe wie Bild oder A4); PDF → PNG/JPG
  (DPI, Bereich); PDF → `.txt` und Markdown.
- **Abhängigkeiten:** **pikepdf** wird Laufzeit-Abhängigkeit (+4 MB, lxml +4–5 MB). Damit gibt es Objekt-Streams
  und die Reparatur kaputter Dateien.
- **Risiken:** Neu kodierte Bilder sind verlustbehaftet; das Profil zeigt das an. Komprimieren darf keine
  Schwärzung rückgängig machen (läuft über das Audit).
- **Tests:** Größe sinkt, `assert_valid_pdf`, Text bleibt extrahierbar, große Fixture im Hintergrund abbrechbar.

### V10 – Ordnerweite PDF-Suche (A1)
- **Umfang:** In die bestehende Suche integriert (Checkbox „PDF-Inhalte durchsuchen“): rekursiv, case-insensitive,
  ganze Wörter, Regex. Treffer mit Datei, Seite und Ausschnitt; ein Klick öffnet die Seite mit Markierung.
  Index als SQLite in `<portable>/cache/pdf-index.sqlite` (Änderungsdatum + Größe + Hash, inkrementell).
  Passwort-PDFs werden übersprungen und als „nicht durchsucht“ markiert.
- **Dateien:** neu `pdfindex.py`; `search.py`, `ui/search_worker.py`, `ui/search_results.py`.
- **Abhängigkeiten:** keine neuen (pypdfium2 aus V0, sqlite3 aus der Standardbibliothek).
- **Risiken:** Erstindex großer Ordner dauert; läuft im Hintergrund mit Fortschritt. Gescannte PDFs werden erst
  mit OCR (W5) durchsuchbar.
- **Tests:** Index-Aktualisierung bei Änderung, Unicode-Pfade, Groß/Klein unter Linux, Abbrechen.

### V11 – Drucken und Vorlesen (A4, A5)
- **Umfang:**
  - A4: Seitenbereich, Kopien, Hoch/Quer automatisch, „An Seite anpassen“/Originalgröße, mit/ohne
    Anmerkungen/Formularinhalte. Nativer Druckdialog (Windows) bzw. CUPS (Linux), dazu „In PDF drucken“.
  - A5: ab Cursor, Seitenanfang oder Markierung; Pause, Stopp, Geschwindigkeit, Stimme; der aktuelle Satz wird
    hervorgehoben.
- **Dateien:** `notex/platform/printing.py`, `notex/platform/speech.py`, `pdf_view.py`.
- **Abhängigkeiten:** keine neuen. QtPrintSupport und QtTextToSpeech sind im PySide6-Wheel, sie müssen nur im
  Build mitgenommen werden (Plugins `printsupport`, `texttospeech`).
- **Plattform:** Linux braucht `libcups` bzw. speech-dispatcher vom System. Fehlt etwas, wird ausgegraut mit
  Hinweis. Windows: SAPI/WinRT über Qt.
- **Risiken:** Satz-Hervorhebung geht zuverlässig, weil ich Satz für Satz einreihe. Wort-Hervorhebung bietet nicht
  jede Engine, deshalb verspreche ich sie nicht.
- **Tests:** Druck in PDF (Seitenzahl, Bereich, mit/ohne Anmerkungen), Satz-Zerlegung, Ausgrauen ohne Engine
  (simuliert).

---

## 6. Blöcke Priorität B (W1–W9) – je Block vorher abstimmen

### W1 – Korrekturzeichen (B10)
Caret für „Text einfügen“, Durchstreichen + Ersatztext für „Text ersetzen“, Durchstreichen für „Text löschen“ (mit
`/IT`-Kennungen wie Acrobat). Erscheinen in Liste und Export (V4). Klein, keine neuen Abhängigkeiten.

### W2 – Ebenen (B5)
Liste der OCGs; ein-/ausblenden, für Druck/Export berücksichtigen, umbenennen, zusammenführen/einbrennen.
**Risiko:** QtPdf zeigt den Standardzustand der Ebenen, schaltet sie aber nicht live um. Lösung: für die Anzeige
eine Kopie mit geändertem `/OCProperties /D` rendern (wie heute die Anzeige-Kopie für Formulare). Ebenen-Namen
kommen ins Audit-Register.

### W3 – Formular-Formate und Prüfregeln (B6)
Datum, Zahl, Währung, Prozent, Telefon, PLZ, eigene Maske; Pflicht, Wertebereich, Regex, Länge. Nativ in Python,
ungültige Eingaben am Feld rot markiert. Zusätzlich schreibe ich die Standard-`/AA`-Format-Aktionen **optional** für
fremde Viewer (Standard: aus); fckNotes führt sie nie aus.

### W4 – Schwärzen ohne Rasterisierung (B2)
- **Umfang:** eigener Content-Stream-Interpreter (Grafikzustand, CTM, Textmatrix, Schriftbreiten aus `/Widths`
  bzw. `/W`, Type0/CID, Form-XObjects rekursiv). Glyphen unter dem Balken werden aus `TJ`/`Tj` entfernt (Abstand
  bleibt erhalten), Vektorpfade ganz im Balken entfernt, teilweise mit Clip-Ausschnitt; Bilder nur im Bereich
  geschwärzt (Pillow). Rest der Seite bleibt Text.
- **Sicherung:** Nach dem Entfernen prüft fckNotes mit PDFium, ob im Balken noch Zeichen stehen, und durchsucht
  die rohen Streams. Bleibt etwas, fällt **diese Seite** automatisch auf „Seite als Bild“ zurück, und der Bericht
  sagt das. Type3-Schriften und unbekannte Kodierungen lösen den Rückfall direkt aus.
- **Risiken:** Das ist der aufwendigste Block. Ligaturen, gedrehter Text und überlappende Zeichen werden getestet;
  sicherheitshalber gilt immer „im Zweifel rastern“.
- **Tests:** wie im Auftrag (Textextraktion + Rohsuche im dekomprimierten PDF), plus Fixtures mit gedrehtem Text,
  Ligaturen, Form-XObjects, CID-Schriften.

### W5 – Scans verbessern und OCR (B9, B1)
- **Umfang:**
  - B9: gerade richten (automatisch über Projektionsprofil auf verkleinertem Bild, manuell), Ränder entfernen,
    entrauschen, Kontrast/Schwellwert, drehen nach erkannter Textausrichtung (Tesseract OSD). Vorschau vor dem
    Anwenden.
  - B1: Tesseract liefert TSV/hOCR; fckNotes legt unsichtbaren Text (Render-Modus 3, eingebettete Schrift aus V0)
    genau über die Wörter. Optionen: nur Seiten ohne Text, alle, erzwingen.
- **Abhängigkeiten:** **Tesseract 5 mit deu+eng**. Größe Windows ≈ 25–35 MB (ZIP), Linux ≈ 8–12 MB.
  **→ Hier brauche ich dein OK** (Regel 5), dazu die Wahl tessdata_fast (klein) oder tessdata_best (genauer,
  +20 MB).
- **Tests:** gescannte Fixture → Text durchsuchbar, kopierbar und schwärzbar; abbrechen mitten im Lauf.

### W6 – PDFs vergleichen (B3)
Nebeneinander mit synchronem Scrollen, Text-Diff (Wortebene, Positionen aus pypdfium2) mit farbiger Markierung und
Änderungsliste, Pixel-Diff pro Seite mit Toleranz, Bericht als PDF/Markdown.

### W7 – Stapelverarbeitung (B7)
Aktionskette aus den Kernfunktionen (OCR, Komprimieren, Wasserzeichen, Muster-Schwärzen, Metadaten entfernen,
Speichern in Ordner), als Vorlage gespeichert; Dateiauswahl oder Ordner (rekursiv optional); Protokoll pro Datei.
Originale werden nie überschrieben: Ausgabe immer in einen Zielordner.

### W8 – Digitale Signaturen (B4)
- **Umfang:** Signieren mit `.p12`/`.pfx` (PAdES B-B), sichtbar im bestehenden Unterschriftsfeld; Prüfen
  (Integrität, Kette, Gültigkeit, Zeitpunkt) mit klarer Anzeige gültig/ungültig/unbekannte Kette.
- **Abhängigkeiten:** **pyHanko**-Paket (≈ 3–4 MB).
- **Netzwerk:** RFC-3161-Zeitstempel ist die **einzige** Netzwerk-Ausnahme, standardmäßig aus, mit eigenem
  Bestätigungsdialog. Die Prüfung läuft offline (`allow_fetching=False`); ohne OCSP/CRL heißt es
  „Sperrstatus nicht geprüft (offline)“.
- **Risiken:**
  - Nach dem Signieren muss jede weitere Änderung als **inkrementelles Update** gespeichert werden, sonst ist die
    Signatur kaputt. fckNotes schreibt heute immer komplett neu. Deshalb warnt fckNotes bei signierten PDFs vor
    dem Bearbeiten, und Speichern hängt an.
  - Das Audit (Regel 8) muss frühere Speicherstände prüfen. Schwärzen in einem signierten PDF entfernt die
    Signatur zwangsläufig; das wird angesagt.
  - Der Windows-Zertifikatsspeicher braucht CNG über ctypes. Das schlage ich als eigenen Nachschritt vor, nicht
    im ersten Wurf.

### W9 – PDF/A (B8)
- **Ehrlich:** Eine vollständige Umwandlung beliebiger PDFs nach PDF/A (Schriften nachträglich einbetten,
  Farbräume umrechnen, Transparenz) kann zuverlässig nur Ghostscript oder ein kommerzielles Werkzeug.
- **Vorschlag:**
  1. Prüfung mit verständlicher Fehlerliste (eigene Regeln für 2b/3b: Schriften eingebettet, keine
     Verschlüsselung, kein JavaScript, OutputIntent, XMP-Kennung, keine externen Inhalte …).
  2. Export: Erfüllt das Dokument die Regeln, setzt fckNotes OutputIntent (sRGB-ICC, frei), XMP-Kennung und
     entfernt Verbotenes. Sonst wird angeboten, die betroffenen Seiten als Bild (+ OCR-Textebene) neu zu erzeugen.
  3. veraPDF wird erkannt und genutzt, wenn installiert.

---

## 7. Blöcke Priorität C (X1–X14) – je Block vorher abstimmen

| Block | Feature | Kern | Abhängigkeit / Risiko |
|---|---|---|---|
| X1 | C14 XFA | Warnung vor der Umwandlung, Original bleibt | – |
| X2 | C3 Links anklicken | intern immer; extern/Datei als Einstellung (Standard aus), Bestätigung mit voller URL und markierter Domain, nie ausführbare Dateien | – |
| X3 | C10 Portfolios | erkennen, Liste, öffnen (PDF im Tab), extrahieren, hinzufügen | baut auf V2 auf |
| X4 | C13 Multimedia | RichMedia/Screen/3D erkennen, Platzhalter mit Typ/Größe, extrahieren, nach Bestätigung im System-Player | – |
| X5 | C7 Gemeinsame Prüfung | mehrere XFDF zusammenführen, Duplikate erkennen | baut auf V4 auf |
| X6 | C8 Bates | Präfix/Suffix/Start/Stellen, über mehrere Dateien mit Protokoll | baut auf V8 auf |
| X7 | C2 Berechnete Felder | Summe, Produkt, Ø, Min/Max, einfache Ausdrücke (eigener sicherer Parser, kein `eval`) | optionaler Acrobat-Export, Standard aus |
| X8 | C6 Lesemodus | Lesereihenfolge aus pypdfium2, frei umbrechende Ansicht | Mehrspalten-Layouts nur „so gut wie möglich“ |
| X9 | C11 Barrierefreiheit | Prüfbericht, Tag-Baum, Alt-Texte und Sprache bearbeiten | Alt-Texte ins Audit-Register |
| X10 | C12 Zertifikatsverschlüsselung | Empfänger-Zertifikate `.cer`/`.pem`, öffnen mit eigener `.p12` | pyHanko (aus W8) |
| X11 | C5 Scanner | Windows WIA über PowerShell/COM (keine Abhängigkeit), Linux `scanimage` (SANE); danach optional W5 | fehlt SANE → ausgegraut |
| X12 | C4 Office | PDF → DOCX (eigener kleiner Schreiber), HTML, Markdown best effort; Word/Excel/PowerPoint → PDF nur mit gefundenem `soffice`; HTML-Datei → PDF nur lokal | ohne LibreOffice ausgegraut |
| X13 | C9 Druckvorstufe light | Preflight-Bericht (Schriften, Bild-DPI, Farbräume, Transparenz, Boxen) | **Ausgabevorschau/Farbauszüge geht mit PDFium nicht.** Vorschlag: nur wenn Ghostscript installiert ist (erkannt, nicht mitgeliefert). **PDF/X-4-Export: Vorschlag nicht umsetzen** – braucht Farbmanagement mit CMYK-Profil und Output-Intent-Prüfung; ohne Ghostscript nicht verlässlich. |
| X14 | C1 Text ändern | Zeile/Absatz anklicken, mit derselben Schrift ersetzen, wenn alle Glyphen da sind; sonst entfernen (W4) und mit Noto neu setzen + Hinweis „Schrift ersetzt“; Umbruch nur im Absatzrahmen | baut auf W4 auf. **Ehrlich:** Eingebettete Schriften sind fast immer Untermengen, „dieselbe Schrift“ klappt daher oft nur bei Korrekturen mit vorhandenen Buchstaben. Schwierigster Block, deshalb zuletzt. |

---

## 8. Abweichungen und offene Punkte (bitte entscheiden)

1. **Release-Build Linux auf `ubuntu-22.04`** statt `ubuntu-latest`: Der Build auf dem älteren System läuft auch
   auf neueren Distributionen, umgekehrt nicht. Tests laufen weiter auf `ubuntu-latest`. Einverstanden?
2. **Tesseract-Größe** (W5): Ja/Nein und fast oder best. Alternative: Windows-OCR, siehe Abschnitt 3.
3. **Gesamtwachstum** ohne OCR ≈ +25 MB (ZIP ≈ 120 MB), mit OCR ≈ +35–60 MB. Einverstanden?
4. **PDF/X-4 und Farbauszüge** (X13): nicht umsetzen bzw. nur mit installiertem Ghostscript.
5. **PDF/A** (W9): Prüfung + Export für konforme Quellen + Bild-Rückfall statt voller Umwandlung.
6. **Schrift-Umstellung** in V0 ändert das Aussehen neu erzeugter Anmerkungen (Noto Sans statt Helvetica).
7. **Windows-Zertifikatsspeicher** (W8): als Nachschritt nach `.p12`/`.pfx`.

## 9. Reihenfolge

V0 → V1 → V2 → V3 → V4 → V5 → V6 → V7 → V8 → V9 → V10 → V11, danach W1 … W9 und X1 … X14 jeweils nach Abstimmung.
Jeder Block endet mit Tests (Windows + Linux grün), Doku, CHANGELOG, PROGRESS.md, einem Commit und einer kurzen
Statusmeldung.
