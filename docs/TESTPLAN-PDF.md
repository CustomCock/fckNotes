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

## V5 – Formulare
1. Je ein Feld anlegen: Optionsgruppe (3 Optionen), Dropdown, Listenfeld, Schaltfläche „Zurücksetzen“ und
   „Seite 2“. Speichern, in Acrobat/Firefox ausfüllen: Optionsgruppe erlaubt genau eine Wahl, Dropdown klappt auf,
   „Zurücksetzen“ leert/setzt Standardwerte, „Seite 2“ springt.
2. Pflichtfeld und Tooltip setzen: Acrobat markiert das Feld rot umrandet und zeigt den Tooltip.
3. Tab-Reihenfolge per Ziehen ändern, speichern: In Acrobat springt die Tab-Taste in dieser Reihenfolge; dann „nach
   Spalten“ – ebenso.
4. Daten als FDF und XFDF exportieren, in Acrobat importieren („Formulardaten importieren“) – und umgekehrt eine
   Acrobat-FDF in fckNotes einlesen.
5. Drei ausgefüllte Kopien → „Antworten sammeln“: CSV öffnet in Excel mit einer Zeile je Datei.

## V6 – Passwortschutz und Muster-Schwärzen
1. `tests/fixtures/pdf/` AES-256-Fixture mit Passwort öffnen, eine Seite drehen, speichern mit „Schutz beibehalten“:
   Acrobat fragt nach demselben Passwort; Dokumenteigenschaften zeigen AES-256.
2. RC4-Fixture öffnen, speichern mit Schutz: Acrobat zeigt danach AES-256.
3. In Acrobat ein PDF mit „Ändern verboten“ und Rechte-Passwort erstellen; in fckNotes öffnen: nur lesbar, nach
   Rechte-Passwort bearbeitbar; gespeichert gelten die Rechte in Acrobat weiter.
4. Schutz entfernen, speichern: öffnet überall ohne Passwort.
5. Ein Dokument mit E-Mail, IBAN, Telefon und Steuer-ID: „Nach Mustern schwärzen“ findet alle, eine
   falsche IBAN (Prüfziffer) nicht; nach dem Schwärzen findet die Suche in Acrobat nichts mehr.

## V7 – Bilder
1. `tests/fixtures/pdf/bilder.pdf` öffnen, Werkzeug „Bilder im Seiteninhalt“: fünf Rahmen auf Seite 1. Das große
   JPEG exportieren und mit einem Hex-Vergleich prüfen: identisch mit dem eingebetteten Stream.
2. Das obere JPEG links halb zuschneiden, speichern unter: in Acrobat ist die rechte Hälfte weg, die linke steht an
   derselben Stelle; das zweite JPEG auf Seite 1 und das auf Seite 2 sind unverändert.
3. Ein Handyfoto (Hochformat, EXIF-Drehung) einfügen: steht in Acrobat und im Browser aufrecht.
4. Ein PNG mit Transparenz einfügen, auf 30° drehen, Deckkraft 50 %, einbrennen: in Acrobat gleich, durchsichtig.
5. „Überall ersetzen“ auf dem JPEG (3×): alle drei Stellen zeigen in Acrobat das neue Bild; Dateigröße passt.
6. Ein Bild löschen, speichern, mit `qpdf --show-xref` bzw. der Suche nach dem Bild-Stream prüfen: weg.

## V8 – Wasserzeichen, Kopf-/Fußzeilen, Hintergrund
1. Ein mehrseitiges PDF: Wasserzeichen „ENTWURF“, Fußzeile „Seite {seite} von {seiten}“, Hintergrund hellgelb.
   In Acrobat erscheinen drei Ebenen „fckNotes: …“ im Ebenen-Bedienfeld; ausblenden blendet jeweils genau das aus.
2. Drucken (Acrobat, Browser): Wasserzeichen und Fußzeile sind auf dem Papier.
3. Seite einfügen, „Ebenen aktualisieren“: die neue Seite hat Fußzeile und Wasserzeichen, die Zählung stimmt.
4. „Ebenen entfernen › Alle“, speichern: Acrobat zeigt keine Ebenen mehr, der Inhalt ist unverändert.
5. In Acrobat ein Wasserzeichen hinzufügen, in fckNotes „Fremde Wasserzeichen entfernen“: es ist weg.
6. Text des Wasserzeichens in Acrobat suchen: wird gefunden; Bildschirmleser liest es nicht vor (Artefakt).

## V9 – Komprimieren und Konvertieren
1. Ein PDF mit Fotos (z. B. Handy-Scan) mit „Standard“ komprimieren: Vorher/Nachher stimmt mit der Dateigröße nach
   dem Speichern überein; in Acrobat sind Fotos noch gut lesbar, Text bleibt durchsuchbar.
2. Dasselbe mit „Nur verlustfrei“: Bilder in Acrobat (Bildeigenschaften) unverändert.
3. Ein abgeschnittenes PDF öffnen: wird repariert angezeigt, nach dem Speichern öffnet Acrobat es ohne Warnung.
4. Seiten als PNG mit 300 dpi: Bildgröße passt zur Seitengröße (A4 ≈ 2480 × 3508 px).
5. Ein Artikel-PDF als Markdown: Überschriften, Absätze und Listen stimmen; die Notiz öffnet sich.
6. Drei Handyfotos (hoch und quer) zu einem A4-PDF: Ausrichtung je Seite passend, Fotos aufrecht.
7. Windows-Build: Komprimieren funktioniert in der gepackten Anwendung (pikepdf wird mitgeliefert).
