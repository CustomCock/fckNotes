"""Regel „offline“ und „PDF gültig“: Die PDF-Module enthalten keinen Netzwerk-Code, und was sie schreiben, besteht
die Prüfung wie `qpdf --check` (pikepdf/libqpdf; unter Linux in der CI zusätzlich das echte qpdf)."""
import ast
import io
import shutil
import subprocess
from pathlib import Path

import pytest
from pypdf import PdfReader

from notex.core import pdfannot as A, pdfdiagram, pdfforms, pdfpages, pdfredact
from notex.core.diagram import templates
from notex.core.diagram.model import Diagram
from pdffixtures import PASSWORD, assert_valid_pdf, big, broken, encrypted, scanned
from pdfhelp import make_form_pdf, make_pdf

ROOT = Path(__file__).resolve().parent.parent / "notex"
NETWORK_MODULES = {"socket", "ssl", "urllib", "http", "requests", "aiohttp", "httpx", "ftplib", "smtplib",
                   "telnetlib", "xmlrpc", "websocket", "websockets"}
PURE_STRING_MODULES = {"urllib.parse"}          # nur Zeichenketten (quote/unquote), kein Netzwerk
OFFLINE_FILES = sorted([*ROOT.glob("core/pdf*.py"), ROOT / "core/xfdf.py", ROOT / "core/redact_patterns.py", *ROOT.glob("core/diagram/*.py"),
                        *ROOT.glob("platform/*.py"),
                        *ROOT.glob("ui/pdf*.py"), *ROOT.glob("ui/diagram*.py"), ROOT / "core/undo.py",
                        ROOT / "core/tasks.py", ROOT / "ui/tasks.py"])


@pytest.mark.parametrize("path", OFFLINE_FILES, ids=lambda p: str(p.relative_to(ROOT)))
def test_pdf_modules_import_no_network_code(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module]
        for name in names:
            if name in PURE_STRING_MODULES:
                continue
            assert name.split(".")[0] not in NETWORK_MODULES, f"{path.name} importiert {name}"


def test_guard_blocks_network_in_offline_tests():
    import socket
    from conftest import NetworkUsed
    with pytest.raises(NetworkUsed):
        socket.create_connection(("example.org", 80))


def _outputs():
    doc = make_pdf(["Eins", "Zwei", "Drei"])
    d = Diagram(10, 10)
    templates.apply(d, "class")
    d.fit()
    yield "Text auf der Seite", A.add_text(doc, 0, (72, 100, 300, 110), "Grüße aus Łódź", 12, border=True)
    yield "Notiz", A.add_note(doc, 1, 50, 50, "Notiz")
    yield "Markierung", A.add_markup(doc, 0, "highlight", [(72, 60, 200, 90)])
    yield "Diagramm", pdfdiagram.add_diagram(doc, 0, (20, 300), d)
    yield "Seiten umsortiert", pdfpages.rearrange(doc, [2, 0, 1], {0: 90})
    yield "Zusammengefügt", pdfpages.merge([doc, doc])
    yield "Felder", pdfforms.add_fields(doc, [{"page": 0, "rect": (72, 200, 250, 220), "name": "Name", "kind": "text"},
                                              {"page": 0, "rect": (72, 240, 86, 254), "name": "OK", "kind": "checkbox"}])
    yield "Ausgefüllt", pdfforms.fill(make_form_pdf(), {"Ort": "Łódź", "Zustimmung": True})
    yield "Eingebrannt", pdfforms.flatten(A.add_text(doc, 0, (72, 100, 300, 110), "fest", 12))
    yield "Unterschrift", pdfforms.add_strokes(doc, 0, (300, 600, 450, 650), [[(0, 0), (10, 5), (20, 0)]])


@pytest.mark.parametrize("label,data", list(_outputs()), ids=[label for label, _d in _outputs()])
def test_outputs_pass_qpdf_style_check(label, data):
    assert_valid_pdf(data)


@pytest.mark.skipif(shutil.which("qpdf") is None, reason="qpdf-Programm nicht installiert (CI unter Linux hat es)")
def test_outputs_pass_real_qpdf_check(tmp_path):
    for label, data in _outputs():
        path = tmp_path / "out.pdf"
        path.write_bytes(data)
        result = subprocess.run(["qpdf", "--check", str(path)], capture_output=True, text=True)
        assert result.returncode == 0, (label, result.stdout, result.stderr)


# ---- Sonderfälle aus der Definition of Done --------------------------------------------------------------------
def test_broken_pdf_is_rejected_cleanly():
    with pytest.raises(pdfpages.PdfEditError):
        pdfpages.page_count(broken())


@pytest.mark.parametrize("algorithm", ["AES-256", "AES-128", "RC4-128"])
def test_encrypted_fixtures_are_readable_with_password(algorithm):
    reader = PdfReader(io.BytesIO(encrypted(algorithm)))
    assert reader.is_encrypted
    assert reader.decrypt(PASSWORD)
    assert "Verschlüsselter Inhalt" in reader.pages[0].extract_text()


def test_scanned_fixture_has_no_text_but_an_image():
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(scanned(2))
    assert len(doc) == 2
    assert doc[0].get_textpage().get_text_range().strip() == ""
    assert_valid_pdf(scanned(1))


def test_big_fixture_over_500_pages_stays_editable():
    data = big(520)
    assert pdfpages.page_count(data) == 520
    rotated = pdfpages.rotate(data, [0, 519], 90)
    assert pdfpages.page_count(rotated) == 520
    assert_valid_pdf(rotated)


def test_committed_fixtures_exist():
    folder = Path(__file__).resolve().parent / "fixtures" / "pdf"
    names = {p.name for p in folder.glob("*.pdf")}
    assert {"kaputt.pdf", "verschluesselt_aes256.pdf", "verschluesselt_rc4.pdf", "gescannt.pdf"} <= names


def test_redaction_output_is_valid():
    from notex.core.pdfredact import PageImage
    image = PageImage(595, 842, 10, 10, bytes([255]) * 300)
    assert_valid_pdf(pdfredact.redact_pages(make_pdf(["Geheim"]), {0: image}))
