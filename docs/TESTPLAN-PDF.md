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
