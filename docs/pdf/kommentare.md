# Kommentare: Liste, Antworten, Status, Export (Block V4)

## Kommentar-Seitenleiste
Die Sprechblase oben im PDF-Tab (oder `Ctrl+Shift+M`) zeigt alle Kommentare: Markierungen mit dem markierten Text,
Notizen, Text auf der Seite, Zeichnungen, Formen und Stempel – darunter jeweils die Antworten. Das Suchfeld sucht in
Kommentaren, markiertem Text und Antworten; Auswahllisten filtern nach Art, Autor und Status, „nur diese Seite“ nach
der aktuellen Seite. Sortiert wird nach Seite, Datum, Autor, Art oder Status. Ein Klick springt zur Stelle.

## Antworten und Status
„Antworten“ (Knopf oder `R`) hängt eine Antwort an den Kommentar; Antworten auf Antworten landen im selben Faden.
Der Status (offen, angenommen, abgelehnt, erledigt, abgebrochen) wird wie in Acrobat als eigener, unsichtbarer
Eintrag gespeichert – der jüngste gilt. Als Name steht der aus Einstellungen › Editor › PDF bearbeiten › „Name für
Stempel“ dabei (leer = Anmeldename). Löschen entfernt den Kommentar samt Antworten und Status.

## Zusammenfassung
Der Export-Knopf erzeugt eine Zusammenfassung je Seite mit Art, Autor, Datum, Status, markiertem Text und Antworten –
als Markdown, als CSV (Semikolon, öffnet direkt in Excel) oder als PDF. Zellen, die Excel als Formel lesen würde
(beginnen mit =, +, -, @), werden mit einem Apostroph entschärft.

## XFDF
„Kommentare als XFDF“ speichert alle Kommentare in der Austauschdatei, die Acrobat und andere Programme lesen;
„Importieren“ übernimmt sie in ein PDF. Kommentare mit gleichem Namen werden nicht verdoppelt, sondern aktualisiert;
Datei-Anhänge, Klänge und Schwärz-Vormerkungen aus XFDF werden bewusst nicht übernommen (die Meldung nennt sie).
XFDF-Dateien mit eigenen XML-Entities werden aus Sicherheitsgründen abgelehnt.
