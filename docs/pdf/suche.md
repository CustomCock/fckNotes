# PDF-Inhalte ordnerweit durchsuchen (Block V10)

## Einschalten
In der Seitenleiste (Ctrl+Shift+F) gibt es neben „Name“ und „Volltext“ den Chip **„PDF“**. Ist er an, durchsucht die
Suche zusätzlich den Text aller PDFs im Notizordner und seinen Unterordnern (versteckte Ordner ausgenommen; Endung
`.pdf` in beliebiger Schreibweise). Es gelten dieselben Regeln wie für Textdateien: mehrere Wörter müssen in einer
Zeile stehen, „Phrase“ in Anführungszeichen, `ext:` und `path:`/`-path:` als Filter, „Wort“ für ganze Wörter, „.*“
für reguläre Ausdrücke; Groß- und Kleinschreibung zählen nicht (auch bei Umlauten).

## Treffer
Treffer erscheinen unter „PDF-Inhalte“ mit Datei, Seite („S. 3“) und dem Ausschnitt der Zeile. Ein Klick öffnet das
PDF auf dieser Seite und markiert den gefundenen Begriff – die Suchleiste des PDFs ist dann schon gefüllt, Enter
springt zum nächsten Treffer.

## Index
Damit die Suche schnell bleibt, liest fckNotes den Text jedes PDFs nur einmal und legt ihn im Index
`cache/pdf-index.sqlite` neben dem Programm ab. Beim nächsten Suchen werden nur geänderte Dateien neu gelesen
(Änderungsdatum und Größe, bei Abweichung ein Prüfwert; kopierte oder nur „berührte“ Dateien werden nicht neu
gelesen). Der erste Durchlauf großer Ordner dauert etwas – die Seitenleiste zeigt „PDF-Index 12 / 340 …“, eine neue
Eingabe bricht ab. Gelöschte PDFs verschwinden aus dem Index, und wenn du ein PDF in fckNotes speicherst (z. B. nach
dem Schwärzen), wird sein alter Text sofort aus dem Index genommen und dabei überschrieben. Palette › „Suche:
PDF-Suchindex löschen“ entfernt den ganzen Index.

## Nicht durchsucht
Passwortgeschützte PDFs, PDFs ohne Text (Scans – durchsuchbar erst mit Texterkennung) und sehr große Dateien
(über 300 MB) werden nicht gelesen. Sie stehen am Ende der Trefferliste unter „… PDF(s) nicht durchsucht“ mit Grund.
