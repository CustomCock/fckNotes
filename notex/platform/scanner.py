"""Scanner: Windows über WIA (PowerShell/COM, ohne Zusatzpaket), Linux über SANE (`scanimage`)."""
from __future__ import annotations

import shutil

from notex.platform import WINDOWS, Availability


def available() -> Availability:
    if WINDOWS:
        if shutil.which("powershell") or shutil.which("powershell.exe"):
            return Availability(True)
        return Availability(False, "PowerShell fehlt – ohne sie erreicht fckNotes die Windows-Scanschnittstelle (WIA) nicht.")
    if shutil.which("scanimage"):
        return Availability(True)
    return Availability(False, "SANE fehlt. Installieren z. B. mit „sudo apt install sane-utils“.")
