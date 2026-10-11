"""Ermittelt, wo die App liegt – und damit, wo data/ und config.json hingehören.

Grundprinzip der portablen App: alles liegt relativ zum App-Ordner, nichts in der Registry.
Einzige Ausnahme: Ist der App-Ordner nicht beschreibbar, weicht die App auf %APPDATA% bzw. $XDG_CONFIG_HOME aus und
zeigt das an (`using_fallback`).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _install_root() -> Path:
    """Der Ordner, in dem die App liegt (gebaut: Ordner der EXE; Dev-Modus: Projektordner).

    AppImage: Das Programm läuft aus einem schreibgeschützten Abbild. Portabel heißt dort: ein Ordner
    `<App>-Daten` neben der .AppImage-Datei (lässt er sich nicht anlegen, greift der Ausweichpfad)."""
    appimage = os.environ.get("APPIMAGE")
    if appimage and getattr(sys, "frozen", False):
        from notex import APP_NAME
        folder = Path(appimage).resolve().parent / f"{APP_NAME}-Daten"
        try:
            folder.mkdir(exist_ok=True)
        except OSError:
            pass
        return folder
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def fallback_root() -> Path:
    """Ausweichort, wenn der App-Ordner nicht beschreibbar ist: %APPDATA%\\<App> bzw. $XDG_CONFIG_HOME/<App>."""
    from notex import APP_NAME
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / APP_NAME


_WRITABLE: dict[str, bool] = {}


def is_writable(folder: Path) -> bool:
    """Lässt sich im Ordner eine Datei anlegen? (Echt ausprobiert – Rechte-Bits lügen unter Windows.)"""
    key = str(folder)
    if key not in _WRITABLE:
        probe = folder / f".fcknotes-write-test-{os.getpid()}"
        try:
            probe.write_bytes(b"")
            probe.unlink()
            _WRITABLE[key] = True
        except OSError:
            _WRITABLE[key] = False
    return _WRITABLE[key]


def app_root() -> Path:
    """Der Ordner für alles Veränderliche (data/, config.json, Verlauf, Vorlagen, Stempel, Caches).

    - NOTEX_ROOT (Umgebungsvariable): nur für Tests und Screenshots.
    - Normal (portabel): der App-Ordner.
    - Ist der App-Ordner nicht beschreibbar (z. B. unter „Programme“ oder auf einem schreibgeschützten Stick):
      `fallback_root()`. Die App zeigt das in der Statusleiste an (`using_fallback`).
    """
    override = os.environ.get("NOTEX_ROOT")
    if override:
        return Path(override).resolve()
    base = _install_root()
    if is_writable(base):
        return base
    fallback = fallback_root()
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def using_fallback() -> Path | None:
    """Ausweichort, falls er gerade benutzt wird – sonst None."""
    if os.environ.get("NOTEX_ROOT"):
        return None
    return None if is_writable(_install_root()) else fallback_root()


def cache_dir() -> Path:
    """Caches (PDF-Suchindex, ausgelagerte Rückgängig-Schritte …) – darf jederzeit gelöscht werden."""
    path = app_root() / "cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def stamps_dir() -> Path:
    """Eigene PDF-Stempel (stamps.json + Bilder), portabel neben der App."""
    return app_root() / "stamps"


def data_dir() -> Path:
    """data/ neben der App. Wird angelegt, falls es fehlt."""
    path = app_root() / "data"
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_path() -> Path:
    return app_root() / "config.json"
