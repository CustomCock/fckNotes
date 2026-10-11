# Grundlagen (Block V0)

## Eingebettete Schrift
Alles, was fckNotes selbst auf PDF-Seiten schreibt (Text auf der Seite, Diagramme), nutzt die mitgelieferte
**Noto Sans** und bettet nur die benutzten Zeichen ein. Dadurch sieht das Ergebnis in Acrobat, im Browser und in
fckNotes auf Windows wie Linux gleich aus, und auch Ł, Ş, Ω oder Kyrillisch gehen. Zeichen, die die Schrift nicht
kennt (z. B. Emoji, Pfeile), erscheinen als „?“. Formularfelder bleiben bei Helvetica, damit andere Programme beim
Tippen alle Buchstaben haben.

## Rückgängig auch bei großen PDFs
Jeder Bearbeitungsschritt im PDF-Tab lässt sich mit `Ctrl+Z` zurücknehmen und mit `Ctrl+Y` wiederholen. Damit große
Dateien nicht den Arbeitsspeicher füllen, gilt ein Speicherbudget (Einstellungen › Editor › PDF bearbeiten ›
„Speicher für Rückgängig“, Standard 512 MB). Ältere Schritte darüber hinaus werden in den Ordner `cache/undo`
ausgelagert und beim Schließen des Tabs gelöscht.

## Ausweichordner
fckNotes speichert alles im eigenen Ordner. Ist der nicht beschreibbar (z. B. unter „Programme“ oder auf einem
schreibgeschützten Stick), weicht fckNotes auf `%APPDATA%\fckNotes` bzw. `~/.config/fckNotes` aus. Die Statusleiste
zeigt dann „Daten im Ausweichordner“; der Tooltip nennt den genauen Pfad.

## Prüfung nach dem Schwärzen
Nach „Schwärzen anwenden“ sucht fckNotes die geschwärzten Wörter an jedem Ort, an dem ein PDF Text ablegen kann:
Seitentext, Erscheinungsbilder, Anmerkungen, Formularfelder, Lesezeichen, Anhänge (auch deren Inhalt), Ebenen,
Metadaten, Alt-Texte, Skripte – und zuletzt in allen Objekten, Streams und Rohbytes, wo auch frühere
Speicherstände stecken. Jede Fundstelle wird genannt; erst eine leere Liste heißt „weg“.

## Linux als AppImage
Unter Linux gibt es fckNotes zusätzlich als einzelne `.AppImage`-Datei: ausführbar machen (`chmod +x`) und starten.
Die Daten liegen portabel im Ordner `fckNotes-Daten` neben der Datei. Fehlt FUSE, startet
`--appimage-extract-and-run` die App trotzdem.
