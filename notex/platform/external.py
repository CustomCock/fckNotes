"""Dateien mit dem System-Programm öffnen („start“ bzw. „xdg-open“) – nie ausführbare Dateien.

Wer hier etwas öffnet (Anhang, Multimedia), hat vorher in der Oberfläche bestätigt. `is_executable` ist die
zweite Sperre: Programme, Skripte und Verknüpfungen werden auch nach Bestätigung nicht geöffnet, nur gespeichert.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from notex.platform import WINDOWS, Availability

# Endungen, die unter Windows (oder per Doppelklick unter Linux) Code ausführen
EXECUTABLE_SUFFIXES = {
    ".exe", ".com", ".bat", ".cmd", ".msi", ".msp", ".scr", ".pif", ".cpl", ".dll", ".sys", ".ps1", ".psm1",
    ".vbs", ".vbe", ".js", ".jse", ".wsf", ".wsh", ".hta", ".lnk", ".url", ".reg", ".jar", ".appref-ms",
    ".msc", ".gadget", ".application", ".sh", ".run", ".appimage", ".desktop", ".py", ".pyw", ".pl", ".rb",
    ".deb", ".rpm", ".apk", ".app", ".command", ".iso", ".img", ".vhd", ".vhdx", ".chm", ".xll", ".docm", ".xlsm",
    ".pptm", ".dotm", ".xlam", ".ppam",
}
MAGIC = (b"MZ", b"\x7fELF", b"#!", b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"PK\x03\x04")


def is_executable(path: Path) -> bool:
    """Programm, Skript, Makro-Dokument oder Verknüpfung? (Endung, Ausführbar-Bit oder Dateikopf)."""
    path = Path(path)
    if any(s.lower() in EXECUTABLE_SUFFIXES for s in path.suffixes):
        return True
    try:
        if not WINDOWS and os.access(path, os.X_OK) and path.is_file():
            return True
        head = path.read_bytes()[:4] if path.is_file() else b""
    except OSError:
        return True                                     # im Zweifel nicht öffnen
    if head.startswith(b"PK\x03\x04"):
        return path.suffix.lower() in (".jar", ".apk", ".docm", ".xlsm", ".pptm")
    return any(head.startswith(m) for m in MAGIC if m != b"PK\x03\x04")


def available() -> Availability:
    if WINDOWS or shutil.which("xdg-open"):
        return Availability(True)
    return Availability(False, "xdg-open fehlt (Paket xdg-utils).")


def open_path(path: Path) -> bool:
    """Datei im zugehörigen System-Programm öffnen. Ausführbares wird verweigert (False)."""
    path = Path(path)
    if is_executable(path) or not path.is_file():
        return False
    if WINDOWS:
        os.startfile(str(path))                         # noqa: S606 – nur Dokumente, Ausführbares oben abgelehnt
        return True
    if not shutil.which("xdg-open"):
        return False
    subprocess.Popen(["xdg-open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)
    return True
