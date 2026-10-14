# Bilder (Block V7)

## Bild einfügen
Werkzeug „Bild einfügen“ (Bild-mit-Plus-Symbol) oder Palette › „PDF: Bild einfügen“: auf die Seite klicken setzt das
Bild in natürlicher Größe (höchstens 60 % der Seite), ein aufgezogener Rahmen passt es mit Seitenverhältnis ein –
mit gedrückter Shift-Taste füllt es den Rahmen ganz. Erlaubt sind PNG, JPG, WebP, GIF, BMP und TIFF. JPEGs werden
unverändert übernommen, alles andere verlustfrei; Transparenz bleibt erhalten. Handyfotos mit Drehung im EXIF
erscheinen aufrecht (dafür wird das Foto einmal als JPEG mit Qualität 95 neu gespeichert).

## Verschieben, Größe, Drehen, Deckkraft
Ein eingefügtes Bild ist ein Objekt wie Text oder Unterschrift: anklicken, ziehen, an den Griffen skalieren (Ecken
behalten das Seitenverhältnis, Shift an einer Ecke oder die Kanten verzerren). `R` dreht um 90° nach rechts,
`Shift+R` nach links; Doppelklick öffnet „Drehung und Deckkraft“ für beliebige Winkel und Durchsichtigkeit. Schräg
gedrehte Bilder behalten beim Skalieren immer ihr Seitenverhältnis. Rechtsklick: exportieren, ersetzen, löschen;
„Einbrennen“ macht das Bild wie alle Objekte zu festem Seiteninhalt.

## Bilder im Seiteninhalt
Das Werkzeug „Bilder im Seiteninhalt“ (Bild-Symbol) umrandet alle Bilder, die zum Seiteninhalt gehören (Scans,
Fotos, Logos aus anderen Programmen); ein Klick wählt eines aus. Rechtsklick bietet Exportieren, Ersetzen,
Zuschneiden und Löschen, `Entf` löscht nach Rückfrage. Der Knopf „Bilder im Dokument“ listet alle Bilder mit Seite,
Pixelgröße, Format, Auflösung und wie oft sie vorkommen – alles auch per Tastatur bedienbar.

## Exportieren
JPEG- und JPEG-2000-Bilder werden Byte für Byte so gespeichert, wie sie im PDF stecken; alle anderen als PNG (mit
Transparenz). „Alle exportieren“ läuft im Hintergrund mit Fortschritt und Abbrechen und überschreibt nie eine
vorhandene Datei, sondern hängt „(2)“ an. Exportieren geht auch im Lesemodus.

## Ersetzen, Zuschneiden, Löschen
Ersetzen geht nur an dieser Stelle (neues Bild eingepasst oder gestreckt) oder überall, wo dasselbe Bild vorkommt –
der Dialog zeigt, wie oft. Zuschneiden (Ränder in Prozent oder Rahmen auf der Seite aufziehen) entfernt die Pixel
außerhalb wirklich aus der Datei; der sichtbare Teil bleibt genau an seinem Platz, andere Verwendungen desselben Bilds
bleiben unverändert. Löschen nimmt das Bild von dieser Stelle; kommt es sonst nirgends vor, verschwindet es ganz aus
der Datei.

## Grenzen
Inline-Bilder (kleine, direkt im Seiteninhalt stehende Bilder) lassen sich nur exportieren und löschen. Bilder in
Gruppen (Form-XObjects, etwa eingebrannte Objekte) lassen sich exportieren und überall ersetzen, aber nicht einzeln
zuschneiden oder löschen. Maskenbilder (einfarbige Stempel-Masken) lassen sich nicht zuschneiden. Beim Zuschneiden
von JPEGs wird der Ausschnitt neu als JPEG (Qualität 95) gespeichert; exotische Farbräume (CMYK, Lab) werden dabei
zu RGB.
