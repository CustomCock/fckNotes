"""Drucken: Windows-Druckdialog bzw. CUPS unter Linux (beides über QtPrintSupport). „In PDF drucken“ geht immer."""
from __future__ import annotations

import ctypes.util

from notex.platform import LINUX, Availability


def available() -> Availability:
    """Gibt es einen System-Drucker-Dienst? (Drucken in eine PDF-Datei geht unabhängig davon.)"""
    if LINUX and not ctypes.util.find_library("cups"):
        return Availability(False, "CUPS fehlt (libcups). Installieren z. B. mit „sudo apt install cups“ – "
                                   "„In PDF drucken“ geht trotzdem.")
    try:
        from PySide6.QtPrintSupport import QPrinterInfo
    except ImportError:
        return Availability(False, "Qt-Druckunterstützung fehlt in diesem Build – „In PDF drucken“ geht trotzdem.")
    if not QPrinterInfo.availablePrinterNames():
        return Availability(False, "Kein Drucker eingerichtet – „In PDF drucken“ geht trotzdem.")
    return Availability(True)
