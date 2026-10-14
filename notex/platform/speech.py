"""Sprachausgabe (offline): Windows SAPI/WinRT, Linux speech-dispatcher – über QtTextToSpeech."""
from __future__ import annotations

from notex import APP_NAME
from notex.platform import LINUX, Availability

LINUX_HINT = ("Keine Sprachausgabe gefunden. Installieren z. B. mit „sudo apt install speech-dispatcher espeak-ng“ "
              f"und {APP_NAME} neu starten.")
LOAD_HINT = ("Die Qt-Sprachausgabe lässt sich nicht laden" +
             (" – meist fehlt die PulseAudio-Bibliothek („sudo apt install libpulse0“)." if LINUX else "."))


def _module():
    try:
        from PySide6 import QtTextToSpeech
    except ImportError:
        return None
    return QtTextToSpeech


def engines() -> list[str]:
    module = _module()
    if module is None:
        return []
    return [name for name in module.QTextToSpeech.availableEngines() if name != "mock"]


def available() -> Availability:
    if _module() is None:
        return Availability(False, LOAD_HINT)
    if engines():
        return Availability(True)
    return Availability(False, LINUX_HINT if LINUX else "Keine Sprachausgabe gefunden (Windows-Sprachausgabe fehlt).")
