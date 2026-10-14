"""V6 (A13): Muster-Schwärzen – Prüfer mit echten Prüfziffer-Beispielen und Fehltreffern, eigene Ausdrücke mit
Zeitlimit, Treffer mit Rechtecken im PDF."""
import pytest

from notex.core import redact_patterns as R
from notex.core.pdfpages import PdfEditError
from pdfhelp import make_pdf


@pytest.mark.parametrize("value, ok", [
    ("DE89 3704 0044 0532 0130 00", True), ("GB82 WEST 1234 5698 7654 32", True), ("CH9300762011623852957", True),
    ("DE89 3704 0044 0532 0130 01", False), ("DE89 3704 0044 0532 0130", False), ("XX00 0000 0000 0000", False),
])
def test_iban(value, ok):
    assert R.iban_valid(value) is ok


@pytest.mark.parametrize("value, ok", [
    ("4111 1111 1111 1111", True), ("5555555555554444", True), ("378282246310005", True),
    ("4111111111111112", False), ("0000000000000000", False), ("1234", False),
])
def test_luhn(value, ok):
    assert R.luhn_valid(value) is ok


@pytest.mark.parametrize("value, ok", [
    ("86 095 742 719", True), ("47036892816", True), ("65929970489", True),
    ("86095742718", False), ("12345678901", False), ("06095742719", False), ("11111111111", False),
])
def test_tax_id(value, ok):
    assert R.tax_id_valid(value) is ok


@pytest.mark.parametrize("value, ok", [("COBADEFFXXX", True), ("DEUTDEFF", True), ("VERTRAULICH", False),
                                       ("ABCDXX12", False), ("COBADE00", False)])
def test_bic(value, ok):
    assert R.bic_valid(value) is ok


@pytest.mark.parametrize("value, ok", [("29.02.2024", True), ("24.12.26", True), ("2026-10-12", True),
                                       ("31.02.2026", False), ("29.02.2023", False), ("2026-13-01", False)])
def test_date(value, ok):
    assert R.date_valid(value) is ok


TEXT = ("Kontakt: max.muster@example.org, Tel. 030 1234567 oder +44 20 7946 0958. "
        "IBAN DE89 3704 0044 0532 0130 00 (BIC COBADEFFXXX), Karte 4111 1111 1111 1111, geboren 29.02.2024 / "
        "31.02.2026. Steuer-ID 86 095 742 719, PLZ 10115 Berlin, Server 192.168.0.10 und ::1, "
        "Web https://example.org/x. VERTRAULICH 1234567890123456, Version 999.1.1.1")


def test_find_all_patterns_without_false_positives():
    found = {(m.key, m.text) for m in R.find(TEXT, list(R.PATTERNS))}
    assert found == {("email", "max.muster@example.org"), ("phone_de", "030 1234567"),
                     ("phone_intl", "+44 20 7946 0958"), ("iban", "DE89 3704 0044 0532 0130 00"),
                     ("bic", "COBADEFFXXX"), ("card", "4111 1111 1111 1111"), ("date", "29.02.2024"),
                     ("tax_id", "86 095 742 719"), ("plz", "10115"), ("ip", "192.168.0.10"), ("ip", "::1"),
                     ("url", "https://example.org/x")}


def test_only_selected_patterns_and_no_overlap():
    matches = R.find(TEXT, ["iban", "card"])
    assert [m.key for m in matches] == ["iban", "card"]
    positions = [(m.start, m.end) for m in R.find(TEXT, list(R.PATTERNS))]
    assert all(a[1] <= b[0] for a, b in zip(positions, positions[1:]))


def test_custom_expressions():
    matches = R.find("Akte AZ-2026/123 und AZ-2025/7", [], {"Aktenzeichen": r"AZ-\d{4}/\d+"})
    assert [m.text for m in matches] == ["AZ-2026/123", "AZ-2025/7"] and matches[0].key == "custom:Aktenzeichen"
    assert R.label_of(matches[0].key) == "Eigenes Muster „Aktenzeichen“"
    with pytest.raises(PdfEditError, match="Ungültiger"):
        R.find("x", [], {"kaputt": "(["})
    with pytest.raises(PdfEditError):
        R.compile_custom("   ")


def test_catastrophic_regex_hits_time_limit(monkeypatch):
    pytest.importorskip("regex")
    monkeypatch.setattr(R, "MATCH_TIMEOUT", 0.05)
    with pytest.raises(PdfEditError, match="zu langsam"):
        R.find("a" * 40 + "!", [], {"böse": r"(a|aa)+$"})


def test_find_hits_in_pdf_returns_rects_per_line():
    data = make_pdf(["Mail an max.muster@example.org bitte", "IBAN DE89 3704 0044 0532 0130 00"],
                    size=(600, 800), outline=False)
    hits = R.find_hits(data, ["email", "iban"])
    assert [(h.page, h.key, h.text) for h in hits] == [(0, "email", "max.muster@example.org"),
                                                        (1, "iban", "DE89 3704 0044 0532 0130 00")]
    (rect,) = hits[0].rects
    x0, y0, x1, y1 = rect
    assert 0 < x0 < x1 < 600 and 0 < y0 < y1 < 800 and x1 - x0 > 100
    assert hits[0].label == "E-Mail-Adresse"
