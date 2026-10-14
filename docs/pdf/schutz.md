# Passwortschutz und Schwärzen nach Muster (Block V6)

## Passwort-PDFs bearbeiten
Beim Öffnen fragt fckNotes nach dem Passwort; danach lässt sich das PDF wie jedes andere bearbeiten. Beim ersten
Speichern kommt die Frage, ob der Schutz bleibt – mit „Schutz beibehalten“ wird mit denselben Passwörtern wieder
verschlüsselt, immer mit AES-256 (ältere RC4- oder AES-128-Dateien werden dabei modernisiert). fckNotes merkt sich
keine Passwörter: Sie gelten nur, solange der Tab offen ist.

## Rechte
Ein PDF kann Drucken, Ändern, Kopieren usw. verbieten. Wurde es nur mit dem Öffnen-Passwort (oder ohne Passwort)
geöffnet und verbietet es Änderungen, bleibt es in fckNotes nur lesbar, bis das Rechte-Passwort eingegeben ist
(Palette › „PDF: Rechte-Passwort eingeben“). Wichtig: Rechte sind laut PDF-Norm nur eine Bitte an Programme, kein
echter Schutz – wer das PDF öffnen kann, kann den Inhalt grundsätzlich lesen.

## Schutz setzen, ändern, entfernen
Das Schloss-Symbol (oder Palette › „PDF: Passwortschutz …“) öffnet den Dialog: Öffnen-Passwort, Rechte mit
Rechte-Passwort (leer = zufällig, dann lassen sich die Rechte später nicht mehr ändern) oder „Schutz entfernen“. Der
neue Schutz gilt beim nächsten Speichern.

## Nach Mustern schwärzen
Das Regex-Symbol sucht personenbezogene Daten: E-Mail, Telefon (Deutschland/international), IBAN, BIC, Kreditkarte,
Datum, Steuer-ID, PLZ, IP-Adresse, Webadresse – IBAN, Kreditkarte und Steuer-ID werden über ihre Prüfziffern
bestätigt, ein „31.02.“ gilt nicht als Datum. Eigene reguläre Ausdrücke (z. B. Aktenzeichen) werden gespeichert. Die
Vorschau zeigt jeden Treffer mit Seite; abgewählte bleiben stehen. Danach werden die Treffer vorgemerkt (oder gleich
geschwärzt), und nach dem Schwärzen prüft fckNotes wie immer, ob die Wörter noch irgendwo in der Datei stehen.
