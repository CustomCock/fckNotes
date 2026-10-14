# Seiten und Lesezeichen (Block V1)

Alle Funktionen gibt es im Bearbeiten-Modus (Stift im PDF-Tab), mit der Maus über Knöpfe und Rechtsklick in der
Seitenleiste und mit der Tastatur über die Command Palette (`Ctrl+Shift+P`, „PDF: …“). Jede Änderung ist ein
Rückgängig-Schritt (`Ctrl+Z` / `Ctrl+Y`), gespeichert wird erst mit `Ctrl+S`.

## Leere Seite
Rechtsklick auf eine Miniatur › „Leere Seite davor“ bzw. „dahinter“, oder Palette › „PDF: Leere Seite einfügen …“.
Die Größe richtet sich nach der Nachbarseite; im Dialog lässt sich stattdessen A4 oder Letter wählen.

## Seiten ersetzen
Palette › „PDF: Seiten ersetzen …“ (oder Rechtsklick › „Ersetzen durch PDF …“): Seiten X bis Y angeben, ein anderes
PDF wählen und optional dessen Seiten (`1-3`). Die alten Seiten werden in einem Schritt durch die neuen ersetzt,
Lesezeichen und Seitenbeschriftungen der übrigen Seiten bleiben.

## Zuschneiden
Knopf „Zuschneiden“ öffnet den Dialog: Ränder oben/unten/links/rechts in mm, gilt für die aktuelle Seite, einen
Bereich, gerade, ungerade oder alle Seiten. „Weiße Ränder erkennen“ misst die Ränder der aktuellen Seite (Schwelle
einstellbar, für Scans mit grauem Rand); „Zurücksetzen“ zeigt wieder die ganze Seite. Mit dem Werkzeug
„Zuschneiden“ zieht man den sichtbaren Bereich direkt auf der Seite auf. Zuschneiden blendet nur aus – der Inhalt
bleibt in der Datei. **Für vertrauliche Stellen „Schwärzen“ verwenden.**

## Seitenbeschriftungen
Palette › „PDF: Seitenbeschriftungen …“: Abschnitte anlegen, die ab einer Seite gelten – Stil (1 2 3, i ii iii,
I II III, a b c, A B C oder nur Präfix), Präfix (z. B. „A-“) und Startwert. Die Vorschau zeigt die ersten
Beschriftungen. Acrobat und andere Programme zeigen diese Nummern statt der reinen Seitenzahl.

## Lesezeichen
Der Lesezeichen-Knopf öffnet die Lesezeichen-Leiste. `Ctrl+Shift+B` legt ein Lesezeichen auf der aktuellen Seite an
der sichtbaren Position an; `F2` benennt um, `Entf` löscht, Ziehen verschiebt und verschachtelt, die Pfeil-Knöpfe
rücken ein und aus. Ein Klick springt zur Stelle. Lesezeichen, die auf eine Webadresse zeigen, behalten ihre Aktion
unverändert (fckNotes öffnet sie nicht).

## Lesezeichen aus Überschriften
Knopf „Überschriften“ in der Leiste oder Palette › „PDF: Lesezeichen aus Überschriften …“: fckNotes sucht Zeilen mit
deutlich größerer Schrift als der Fließtext und schlägt sie in bis zu drei Ebenen vor. In der Vorschau lässt sich
jede einzeln abwählen; danach die vorhandenen Lesezeichen ersetzen oder die neuen anhängen. Bei gescannten PDFs ohne
Text findet die Suche nichts (Texterkennung kommt mit Block W).
