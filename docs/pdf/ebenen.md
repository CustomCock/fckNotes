# Wasserzeichen, Kopf-/Fußzeilen, Seitenzahlen, Hintergrund (Block V8)

## Grundprinzip
Alles kommt in eine **eigene Ebene** des PDFs („fckNotes: Wasserzeichen“ usw.), die auch Acrobat und andere Programme
im Ebenen-Bedienfeld zeigen. Deshalb lässt es sich später wieder **entfernen** oder **aktualisieren**, ohne den
eigentlichen Seiteninhalt anzufassen – nach dem Entfernen ist der Seiteninhalt genau wie vorher. Pro Art gibt es eine
Ebene; wer ein neues Wasserzeichen setzt, ersetzt das alte. Text wird in der eingebetteten Noto Sans geschrieben und
bleibt durchsuchbar.

## Wasserzeichen
Ebenen-Symbol › „Wasserzeichen …“ (oder Palette): Text (auch mehrzeilig) oder Bild, Schriftgröße, fett, Farbe,
Deckkraft, Drehung (gegen den Uhrzeigersinn, 45° = steigende Diagonale), Position (neun Stellen), bei Bildern die
Größe in Prozent der Seitenbreite, darüber oder hinter dem Inhalt, und ein Seitenbereich. Die Vorschau rechts zeigt
eine Seite genau so, wie sie danach aussieht.

## Kopf-/Fußzeile und Seitenzahlen
Sechs Felder (oben/unten, links/Mitte/rechts) mit Platzhaltern: `{seite}`, `{seiten}`, `{datum}`, `{dateiname}`,
`{titel}`. „Seitenzahl“ setzt fertige Muster wie „Seite {seite} von {seiten}“. Mit Seitenbereich und „Erste Nummer“
beginnt die Zählung z. B. erst nach dem Deckblatt; `{seiten}` ist dann die letzte vergebene Nummer. Abstand zum Rand
und seitlich in Millimetern.

## Hintergrund
Eine Farbe oder ein Bild (gestreckt oder eingepasst) hinter dem Inhalt, mit Deckkraft und Seitenbereich. Seiten, die
selbst einen weißen Hintergrund malen (z. B. Scans), verdecken ihn.

## Aktualisieren, Entfernen, Vorlagen
„Ebenen aktualisieren“ erzeugt alle Ebenen aus ihrer gespeicherten Einstellung neu – nach dem Einfügen oder Löschen
von Seiten stimmen Seitenzahlen und „alle Seiten“ wieder, das Datum ist neu. „Ebenen entfernen“ nimmt eine Art oder
alle heraus. Einstellungen lassen sich als **Vorlage** speichern (Bilder landen im Ordner `stamps/` neben dem
Programm) und später wieder laden. Hinzufügen und Aktualisieren laufen im Hintergrund mit Fortschritt und Abbrechen;
alles ist ein Rückgängig-Schritt.

## Fremde Wasserzeichen
„Fremde Wasserzeichen entfernen …“ findet Wasserzeichen anderer Programme (z. B. Acrobat), wenn sie als Wasserzeichen
markiert sind (`/Artifact /Watermark` oder Wasserzeichen-Anmerkung), und entfernt sie nach Rückfrage. Nicht markierte
Wasserzeichen sind vom übrigen Inhalt nicht zu unterscheiden und werden **nicht** erkannt.

## Schwärzen
Die gespeicherte Einstellung einer Ebene enthält ihren Text. Die Prüfung nach dem Schwärzen kennt diesen Ablageort
(„Vorlagen von Wasserzeichen, Kopf-/Fußzeilen und Hintergrund“) – steht ein geschwärzter Begriff in einer Kopfzeile,
zuerst die Ebene entfernen oder ändern.
