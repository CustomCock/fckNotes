"""Baut aus dem PyInstaller-Ordner ein AppDir für appimagetool (Linux).

Aufruf:  python tools/make_appdir.py dist/<App> AppDir
Danach:  ARCH=x86_64 appimagetool AppDir <App>.AppImage

Aufbau: AppRun startet usr/lib/<App>/<App>; dazu .desktop-Datei und Symbol. Daten legt die App portabel in
„<App>-Daten“ neben der .AppImage-Datei an (siehe notex/paths.py, Umgebungsvariable APPIMAGE).
"""
from __future__ import annotations

import shutil
import stat
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from notex import APP_NAME, EXE_NAME, __version__  # noqa: E402

APPRUN = """#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/usr/lib/{exe}/{exe}" "$@"
"""

DESKTOP = """[Desktop Entry]
Type=Application
Name={name}
Comment=Portabler Explorer und Editor für Notizen, Textdateien und PDFs
Exec={exe} %F
Icon={exe}
Categories=Utility;TextEditor;Office;
MimeType=text/plain;text/markdown;application/pdf;
Terminal=false
X-AppImage-Version={version}
"""


def build(source: Path, target: Path) -> Path:
    if not (source / EXE_NAME).exists():
        raise SystemExit(f"{source / EXE_NAME} fehlt – erst „python build.py“ ausführen")
    shutil.rmtree(target, ignore_errors=True)
    lib = target / "usr" / "lib" / EXE_NAME
    shutil.copytree(source, lib, symlinks=True)
    apprun = target / "AppRun"
    apprun.write_text(APPRUN.format(exe=EXE_NAME), encoding="utf-8")
    apprun.chmod(apprun.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    (target / f"{EXE_NAME}.desktop").write_text(DESKTOP.format(name=APP_NAME, exe=EXE_NAME, version=__version__),
                                                 encoding="utf-8")
    icon = ROOT / "notex" / "assets" / "notex.png"
    shutil.copyfile(icon, target / f"{EXE_NAME}.png")
    shutil.copyfile(icon, target / ".DirIcon")
    return target


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    print(build(Path(sys.argv[1]), Path(sys.argv[2])))
