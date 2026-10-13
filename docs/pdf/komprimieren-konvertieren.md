# Komprimieren, Reparieren, Konvertieren (Block V9)

## Komprimieren
Symbol „Komprimieren“ in der Bearbeiten-Leiste (oder Palette): Profil wählen – **Hohe Qualität** (300 dpi, JPEG 90),
**Standard** (150 dpi, JPEG 80), **Klein** (96 dpi, JPEG 60), **Nur verlustfrei** oder eigene Werte. Bilder mit mehr
Auflösung als nötig werden verkleinert (gemessen an der größten Stelle, an der sie gezeichnet werden), Fotos als
JPEG neu kodiert; Strichzeichnungen und Text bleiben verlustfrei. Der Dialog sagt vorher, ob das Ergebnis
verlustbehaftet ist. Danach zeigt fckNotes Vorher/Nachher-Größe und fragt, ob es übernommen werden soll – das ist ein
Rückgängig-Schritt, gespeichert wird erst mit Ctrl+S.

Dabei fallen auch frühere Speicherstände in der Datei und ungenutzte Objekte weg; „Objekt-Streams“ packen die
Struktur zusätzlich. Wurde im selben Tab geschwärzt, prüft fckNotes das Ergebnis noch einmal und verwirft es, falls
ein geschwärzter Begriff wieder auffindbar wäre. Bilder, die nur in Anmerkungen stecken (z. B. eingefügte Bilder),
JPEG 2000, JBIG2 und Masken bleiben unverändert.

## Reparieren
Lässt sich ein PDF nicht öffnen (abgeschnitten, kaputte Querverweise), versucht fckNotes beim Öffnen automatisch eine
Reparatur. Der Tab zeigt dann die reparierte Fassung als ungespeicherte Änderung; mit Ctrl+S wird sie gespeichert
und das Original landet im Papierkorb. „PDF reparieren“ (Exportieren-Menü oder Palette) schreibt ein geöffnetes PDF
auch von Hand neu.

## Seiten als Bilder
Exportieren-Symbol › „Seiten als Bilder“: PNG (verlustfrei) oder JPG mit Qualität, Auflösung 36–1200 dpi,
Seitenbereich und Zielordner. Die Dateien heißen `<PDF-Name>-S<Seite>.png`; vorhandene Dateien werden nie
überschrieben, es kommt „(2)“ dazu. Läuft im Hintergrund mit Fortschritt und Abbrechen.

## Als Text oder Markdown
„Als Text (.txt)“ speichert den Text Seite für Seite (mit „--- Seite n ---“). „Als Markdown (.md)“ erkennt
Überschriften an der Schriftgröße, fügt Zeilen zu Absätzen zusammen, setzt getrennte Wörter wieder zusammen und macht
Aufzählungen und nummerierte Listen zu Markdown-Listen; Seitenwechsel stehen als unsichtbarer Kommentar
`<!-- Seite n -->` darin. Die Datei öffnet sich danach gleich als Notiz. Tabellen und mehrspaltiger Satz werden nicht
nachgebaut – der Text kommt in Lesereihenfolge des PDFs.

## Bilder zu PDF
Palette › „Bilder zu PDF …“ (auch ohne geöffnetes PDF): Bilder wählen, Reihenfolge per Ziehen oder Hoch/Runter,
Seitengröße „wie das Bild“ (aus der Bildauflösung, sonst 150 dpi), A4 oder Letter – hoch oder quer je nach Bild – und
ein Rand. JPEGs werden unverändert übernommen, Handyfotos stehen aufrecht. Das neue PDF öffnet sich gleich; eine
vorhandene Datei gleichen Namens kommt vorher in den Papierkorb.
