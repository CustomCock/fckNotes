"""Schreibt die kleinen Test-PDFs nach tests/fixtures/pdf/ (reproduzierbar). Große Dateien (über 500 Seiten)
entstehen nur im Test (`pdffixtures.big`) und landen nicht im Repository.

Aufruf (aus dem Projektordner):  python tests/fixtures/make_fixtures.py
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent.parent))

import pdffixtures as F  # noqa: E402
from pdfhelp import make_form_pdf, make_pdf, make_worksheet_pdf  # noqa: E402

FILES = {
    "einfach.pdf": lambda: make_pdf(["Erste Seite", "Zweite Seite", "Dritte Seite"]),
    "formular.pdf": make_form_pdf,
    "arbeitsblatt.pdf": make_worksheet_pdf,
    "kaputt.pdf": F.broken,
    "verschluesselt_aes256.pdf": lambda: F.encrypted("AES-256"),
    "verschluesselt_aes128.pdf": lambda: F.encrypted("AES-128"),
    "verschluesselt_rc4.pdf": lambda: F.encrypted("RC4-128"),
    "gescannt.pdf": lambda: F.scanned(2),
}


def main() -> None:
    out = HERE / "pdf"
    out.mkdir(exist_ok=True)
    for name, build in FILES.items():
        (out / name).write_bytes(build())
        print(f"{name}: {(out / name).stat().st_size} Bytes")


if __name__ == "__main__":
    main()
