# Drucken und Vorlesen (Block V11)

## Drucken
Der **Drucker-Knopf** oben im PDF-Tab (oder Palette › „PDF: Drucken …“) öffnet den Druckdialog. Oben wählst du
einen Drucker oder **„In PDF-Datei speichern“**; „Druckereinstellungen …“ öffnet den Dialog des Systems für
Duplex, Fach und Ähnliches. Darunter: alle Seiten, die aktuelle Seite oder ein Bereich („1-3, 5, 8-“), Kopien
(sortiert 1 2 3, 1 2 3 oder ungeordnet 1 1, 2 2), Größe („An Seite anpassen“, „Nur zu große verkleinern“,
„Originalgröße“), Papier (A4, Letter, A3 oder wie die PDF-Seite) und „Hoch/Quer automatisch nach der Seite“.

**Kommentare, Stempel und Zeichnungen** sowie **Formularfelder mit Inhalt** lassen sich einzeln weglassen. Gedruckt
wird nur, was das PDF selbst zum Drucken vorsieht – ausgeblendete Anmerkungen und Notiz-Popups nie, Links nie.

„In PDF-Datei speichern“ schreibt ein neues **Vektor-PDF** (Text bleibt scharf und durchsuchbar) auf dem gewählten
Papier. Das Original bleibt unverändert; denselben Dateinamen lehnt fckNotes ab, eine vorhandene andere Datei kommt
vorher in den Papierkorb. Auf einen echten Drucker geht jede Seite als Bild mit bis zu 300 dpi – das klappt mit jedem
Treiber gleich; der Fortschritt lässt sich abbrechen.

Fehlt unter Linux CUPS oder ist kein Drucker eingerichtet, steht das im Dialog – „In PDF-Datei speichern“ geht
trotzdem.

## Vorlesen
**`F8`** liest vor: in einer Notiz ab dem Satz unter dem Cursor, im PDF ab dem Anfang der aktuellen Seite (oder nur
die Markierung, wenn Text markiert ist; auch über den **Lautsprecher-Knopf** im PDF-Tab). Palette › „Vorlesen:
Markierung“ bzw. „Vorlesen: ab Anfang der Notiz“ wählen den Bereich ausdrücklich.

Der gerade gelesene Satz ist hinterlegt und bleibt im Bild – im PDF springt die Ansicht mit, auch über Seiten
hinweg. Dein Cursor und deine Auswahl bleiben, wo sie waren. Unten erscheint die **Vorlese-Leiste**: Satz zurück,
Pause/Weiter (auch `F8`), Satz vor (`←`/`→`), Stopp (`Shift+F8` oder `Esc`), Tempo und Stimme. Tempo und Stimme
merkt sich fckNotes.

Vorgelesen wird **offline** mit der Sprachausgabe des Systems: unter Windows die eingebauten Stimmen (SAPI/WinRT),
unter Linux speech-dispatcher (z. B. `sudo apt install speech-dispatcher espeak-ng`). Ist keine da, sagt fckNotes,
was fehlt. Sätze werden an Satzzeichen, Absätzen, Überschriften und Listenpunkten getrennt, nicht nach
Abkürzungen wie „z. B.“ oder Datumsangaben wie „3. Oktober“. Hervorgehoben wird der ganze Satz – einzelne Wörter
nicht, weil das nicht jede Sprachausgabe meldet.
