# Manuelle Prüfanleitung PDF (Definition of Done)

Automatisch prüft die Testsuite jedes erzeugte PDF wie `qpdf --check` (pikepdf/libqpdf; unter Linux in der CI auch das
echte `qpdf`). Diese Anleitung ergänzt das um den Blick in **fremde Programme**. Pro Block nach dem Merge einmal auf
Windows und Linux durchgehen.

## Vorbereitung
- Testdateien: `tests/fixtures/pdf/` (neu erzeugen mit `python tests/fixtures/make_fixtures.py`).
- Fremde Programme: mindestens eines von Adobe Acrobat Reader, Firefox/Chrome (eingebauter Viewer),
  SumatraPDF (Windows) oder Okular/Evince (Linux).

## V0 – Grundlagen
1. In `einfach.pdf` einen Text mit „Grüße aus Łódź – Ωmega“ auf die Seite setzen, speichern.
   - In einem fremden Programm öffnen: Text sichtbar, gleiche Schrift (Noto Sans), keine Fehlermeldung.
   - Text markieren und kopieren: kommt korrekt in die Zwischenablage.
2. Ein Klassendiagramm einfügen, speichern, im fremden Programm öffnen: Text und Linien wie im Editor.
3. `formular.pdf` ausfüllen (Feld „Ort“ = „Łódź“), speichern, im fremden Programm weitertippen: alle Zeichen
   erscheinen (Formularfelder bleiben bewusst bei Helvetica).
4. Eine große Datei (über 500 Seiten, z. B. mit `python -c "import sys; sys.path[:0]=['tests']; import pdffixtures as F; open('gross.pdf','wb').write(F.big())"`)
   öffnen, 20× drehen und zurücknehmen: Die Oberfläche bleibt flüssig, `cache/undo` füllt sich und ist nach dem
   Schließen des Tabs leer.
5. Den App-Ordner schreibschützen und starten: Statusleiste zeigt „Daten im Ausweichordner“ mit Pfad im Tooltip.
6. Linux: AppImage starten; neben der Datei entsteht `fckNotes-Daten/`.
7. Schwärzen: eine Datei mit Anhang erzeugen
   (`python -c "import sys; sys.path[:0]=['tests','.']; import pdffixtures as F; open('anhang.pdf','wb').write(F.audit_doc('attachment','Zweite'))"`),
   in fckNotes ein Wort auf der Seite schwärzen und als Suchbegriff „Zweite“ vormerken – die Prüfung meldet den
   Anhang („„Zweite“ im Anhang „bericht.txt““).

## V1 – Seiten und Lesezeichen
1. `einfach.pdf`: hinter Seite 1 eine leere A4-Seite einfügen, Seite 2–3 durch eine Seite aus einem anderen PDF
   ersetzen, speichern. Im fremden Programm: Seitenzahl und Reihenfolge stimmen, keine Fehlermeldung.
2. Alle Seiten 20 mm zuschneiden, speichern. Im fremden Programm: Ränder ausgeblendet; Text im ausgeblendeten Rand
   lässt sich dort per Suche weiterhin finden (Zuschneiden ist kein Schwärzen). „Zuschnitt aufheben“ zeigt alles.
3. Einen Scan mit grauem Rand: „Weiße Ränder erkennen“ mit Schwelle 245 und 200 vergleichen.
4. Seitenbeschriftungen „i, ii“ für Seite 1–2 und „1 …“ ab Seite 3 setzen, speichern. Acrobat/Okular/Firefox zeigen
   „i“, „ii“, „1“ … in der Seitenanzeige. Danach eine Seite drehen und eine löschen: Beschriftungen bleiben passend.
5. Lesezeichen anlegen (`Ctrl+Shift+B`), umbenennen, verschachteln, speichern: Der Baum erscheint im fremden
   Programm gleich, ein Klick springt an die gespeicherte Position.
6. Ein PDF mit großen Überschriften: „Aus Überschriften“ – Vorschau prüfen, einzelne abwählen, übernehmen.
7. Große Datei (über 500 Seiten): Seite 300 durch ein anderes PDF ersetzen und rückgängig machen – flüssig.

## V2 – Anhänge und Links
1. Eine Datei (z. B. CSV) anhängen, speichern. Im fremden Programm (Acrobat: Büroklammer-Leiste, Firefox:
   Anhänge-Leiste): Anhang da, Inhalt beim Speichern identisch.
2. Einen Anhang entfernen, speichern: im fremden Programm weg, `qpdf --check` ohne Fehler.
3. Ein PDF mit einer `.exe` als Anhang (z. B. aus `tests/test_pdf_attach.py` erzeugt): „Öffnen“ ist ausgegraut,
   „Speichern unter“ funktioniert.
4. Links anlegen: Seite (mit Höhe), Lesezeichen, Webadresse, anderes PDF; mit und ohne Rahmen. Im fremden Programm:
   Rahmen wie gewählt, Klick springt bzw. fragt nach dem Öffnen der Webadresse.
5. In fckNotes im Lesemodus: Seiten-Link springt, Webadresse öffnet **nichts** (nur Hinweis), Rechtsklick kopiert.
6. Einen Link verschieben und vergrößern, speichern: im fremden Programm an der neuen Stelle klickbar.
7. Linux ohne `xdg-open`: „Öffnen“ ist ausgegraut, Tooltip nennt das fehlende Paket.

## V3 – Stift, Formen, Stempel
1. Mit dem Stift schreiben (mit und ohne Glättung, Deckkraft 50 %), speichern. Im fremden Programm: gleiche Form,
   halbdurchsichtig, als Anmerkung auswählbar/löschbar.
2. Radierer über einen von zwei Strichen ziehen: genau dieser Strich verschwindet ganz.
3. Je eine Form anlegen (Rechteck gestrichelt, Ellipse gepunktet, Pfeil, Polygon, Wolke gefüllt), speichern. Im
   fremden Programm: Wolkenrand, Strichelung und Füllung wie in fckNotes.
4. Eine Form vergrößern und verschieben, speichern: Strichstärke unverändert, Form an der neuen Stelle.
5. Alle fünf Standardstempel setzen, einen mit Datum und Namen; einen eigenen Stempel aus Text und einen aus einem
   PNG mit Transparenz anlegen und setzen. Im fremden Programm: Text in Noto Sans, Bild mit Transparenz.
6. Auf einer gedrehten Seite einen Stempel setzen: steht aufrecht.
7. Fenster auf etwa 1000 px Breite verkleinern: Die Bearbeiten-Leiste bricht in zwei Zeilen um, nichts ist abgeschnitten.

## V4 – Kommentare, Antworten, Status, XFDF
1. Ein PDF mit Kommentaren aus Acrobat öffnen: Die Liste zeigt alle Kommentare mit Antworten und Status wie in
   Acrobat; Klick springt zur Stelle.
2. Auf einen Kommentar antworten und den Status „angenommen“ setzen, speichern. In Acrobat: Antwort im Faden,
   Status-Häkchen; die Antwort erscheint nicht als eigenes Symbol auf der Seite.
3. In Acrobat „Kommentare exportieren“ (XFDF), in fckNotes in eine saubere Kopie importieren: alle Kommentare da,
   Antworten verbunden. Erneut importieren: keine Doppelten.
4. In fckNotes als XFDF exportieren und in Acrobat importieren: Kommentare erscheinen an derselben Stelle.
5. Zusammenfassung als CSV in Excel/LibreOffice öffnen: Umlaute korrekt, Spalten getrennt, ein Kommentar
   „=1+1“ steht als Text da.
6. Zusammenfassung als PDF: je Seite die Kommentare mit markiertem Text.
