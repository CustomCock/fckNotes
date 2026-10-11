"""Sprachausgabe (offline): Windows SAPI/WinRT, Linux speech-dispatcher – über QtTextToSpeech."""
from __future__ import annotations

from notex.platform import LINUX, Availability

LINUX_HINT = ("Keine Sprachausgabe gefunden. Installieren z. B. mit „sudo apt install speech-dispatcher espeak-ng“ "
              "und fckNotes neu starten.")


def engines() -> list[str]:
    try:
        from PySide6.QtTextToSpeech import QTextToSpeech
    except ImportError:
        return []
    return [name for name in QTextToSpeech.availableEngines() if name != "mock"]


def available() -> Availability:
    if engines():
        return Availability(True)
    return Availability(False, LINUX_HINT if LINUX else "Keine Sprachausgabe gefunden (Windows-Sprachausgabe fehlt).")
