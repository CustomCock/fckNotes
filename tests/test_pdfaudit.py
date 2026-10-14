"""Regel 8: Die Prüfung „Wörter noch vorhanden?“ findet ein Wort an jedem Ablageort – und meldet nichts, wo nichts ist."""
import io
import logging

import pytest
from pypdf import PdfWriter

from notex.core import pdfaudit, pdfredact
from pdffixtures import assert_valid_pdf, audit_doc
from pdfhelp import make_pdf

logging.getLogger("pypdf").setLevel(logging.ERROR)
SECRET = "Quastenflosser"


def test_every_location_has_a_fixture():
    """Wer einen neuen Ablageort einträgt, muss hier eine Fixture ergänzen (pdffixtures.audit_doc)."""
    for location in pdfaudit.LOCATIONS:
        audit_doc(location, SECRET)                         # ValueError, falls ein Ort keine Fixture hat


@pytest.mark.parametrize("location", list(pdfaudit.LOCATIONS))
def test_secret_is_found_at_its_location(location):
    data = audit_doc(location, SECRET)
    if location != "raw":
        assert_valid_pdf(data)
    findings = pdfaudit.audit(data, [SECRET.lower()])
    assert findings, location
    assert location in {f.location for f in findings}, (location, findings)


@pytest.mark.parametrize("location", [loc for loc in pdfaudit.LOCATIONS if loc not in pdfaudit.GENERIC])
def test_specific_finding_suppresses_rest_search(location):
    findings = pdfaudit.audit(audit_doc(location, SECRET), [SECRET])
    assert not {f.location for f in findings} & set(pdfaudit.GENERIC)


def test_clean_document_reports_nothing_and_whitespace_is_normalized():
    clean = make_pdf(["Nichts zu sehen"], outline=False, title="")
    assert pdfaudit.audit(clean, [SECRET]) == []
    assert pdfaudit.audit(clean, ["", "  "]) == []
    spaced = make_pdf(["Max   Muster"], outline=False, title="")
    assert pdfaudit.report(spaced, ["max muster"]) == ["„max muster“ auf Seite 1"]


def test_old_revision_found_in_raw_bytes_only():
    data = audit_doc("raw", SECRET)
    reader_meta = pdfaudit.open_reader(data).metadata
    assert SECRET not in str(reader_meta)                    # die aktuelle Fassung zeigt es nicht mehr …
    report = pdfaudit.report(data, [SECRET])
    assert report == [f"„{SECRET}“ in den Rohdaten der Datei (auch frühere Speicherstände)"]   # … die Rohdaten schon


def test_compressed_old_revision_is_inflated():
    writer = PdfWriter(clone_from=io.BytesIO(make_pdf([SECRET], outline=False, title="")))
    for page in writer.pages:
        page.compress_content_streams()
    first = io.BytesIO()
    writer.write(first)
    later = PdfWriter(io.BytesIO(first.getvalue()), incremental=True)
    from pypdf.generic import DecodedStreamObject, NameObject
    clean = DecodedStreamObject()
    clean.set_data(b"BT /F1 24 Tf 72 700 Td (sauber) Tj ET")
    later.pages[0][NameObject("/Contents")] = later._add_object(clean)
    out = io.BytesIO()
    later.write(out)
    assert SECRET.encode() not in out.getvalue()             # nur komprimiert vorhanden
    report = pdfaudit.report(out.getvalue(), [SECRET])
    assert report and "in der Datei" in report[0]                 # alter, unerreichbarer Stream wird gefunden


def test_leftovers_uses_the_audit():
    assert pdfredact.leftovers(audit_doc("attachment", SECRET), [SECRET]) == [f"„{SECRET}“ im Anhang „bericht.txt“"]
    assert pdfredact.leftovers(audit_doc("layer", SECRET), [SECRET]) == [f"„{SECRET}“ in einem Ebenen-Namen"]
