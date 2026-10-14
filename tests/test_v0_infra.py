"""V0: Speicherbudget fürs Rückgängigmachen, Hintergrundaufgaben, Portable-Ausweichpfad, Plattformschicht."""
import os
from pathlib import Path

import pytest

from notex import paths
from notex.core.tasks import Cancelled, TaskContext
from notex.core.undo import SnapshotStack
from notex.platform import Availability, external, printing, scanner, speech


# ---- Rückgängig mit Budget -------------------------------------------------------------------------------------
def test_snapshot_stack_undo_redo_roundtrip(tmp_path):
    stack = SnapshotStack(budget=10_000, spill_dir=tmp_path)
    stack.push(b"v1", 0)
    stack.push(b"v2", 1)
    assert stack.undo(b"v3", 2) == (b"v2", 1)
    assert stack.undo(b"v2", 1) == (b"v1", 0)
    assert not stack.can_undo and stack.can_redo
    assert stack.redo(b"v1", 0) == (b"v2", 1)
    stack.push(b"anders", 0)                                 # neue Änderung: Wiederholen verfällt
    assert not stack.can_redo


def test_snapshot_stack_spills_oldest_over_budget_and_cleans_up(tmp_path):
    stack = SnapshotStack(budget=2_500, spill_dir=tmp_path)
    for i in range(5):
        stack.push(bytes([i]) * 1000, i)
    assert stack.memory() <= 2_500 and stack.spilled() == 3
    assert len(list(tmp_path.iterdir())) == 3
    data, page = stack.undo(b"x" * 1000, 9)
    assert data == bytes([4]) * 1000 and page == 4
    while stack.can_undo:
        data, page = stack.undo(data, page)
    assert data == bytes([0]) * 1000                         # ausgelagerte Fassungen kommen unverändert zurück
    stack.clear()
    assert list(tmp_path.iterdir()) == []


def test_snapshot_stack_max_steps(tmp_path):
    stack = SnapshotStack(budget=10**9, max_steps=3, spill_dir=tmp_path)
    for i in range(6):
        stack.push(bytes([i]), i)
    assert len(stack) == 3


# ---- Hintergrundaufgaben ---------------------------------------------------------------------------------------
def test_task_context_reports_and_cancels():
    seen = []
    ctx = TaskContext(lambda d, t, text: seen.append((d, t, text)))
    ctx.step(1, 3, "eins")
    assert seen == [(1, 3, "eins")] and not ctx.cancelled
    ctx.cancel()
    with pytest.raises(Cancelled):
        ctx.step(2, 3)


def test_background_task_runs_and_can_be_cancelled(win):
    import threading
    from PySide6.QtCore import QCoreApplication
    from notex.ui.tasks import run_task
    results, cancelled = [], []

    def count(ctx, n):
        for i in range(n):
            ctx.step(i, n, f"{i}")
        return n
    run_task(win, "Zählen", count, 50, on_done=results.append)
    gate = threading.Event()

    def wait(ctx):
        while not gate.wait(0.01):
            ctx.check()
    worker = run_task(win, "Warten", wait, on_cancel=lambda: cancelled.append(True))
    for _ in range(300):
        QCoreApplication.processEvents()
        if results:
            break
        worker.wait(10)
    worker.cancel()
    for _ in range(300):
        QCoreApplication.processEvents()
        if cancelled:
            break
        worker.wait(10)
    assert results == [50] and cancelled == [True]


# ---- Portable-Ausweichpfad -------------------------------------------------------------------------------------
def test_fallback_when_app_folder_not_writable(tmp_path, monkeypatch):
    monkeypatch.delenv("NOTEX_ROOT", raising=False)
    locked = tmp_path / "app"
    locked.mkdir()
    monkeypatch.setattr(paths, "_install_root", lambda: locked)
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    paths._WRITABLE.clear()
    assert paths.app_root() == locked and paths.using_fallback() is None
    paths._WRITABLE[str(locked)] = False                    # wie ein schreibgeschützter Ordner
    root = paths.app_root()
    assert root == paths.fallback_root() and root.is_dir()
    assert root.name == "fckNotes" and paths.using_fallback() == root
    assert paths.cache_dir() == root / "cache"
    paths._WRITABLE.clear()


@pytest.mark.skipif(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0),
                    reason="Schreibschutz per chmod greift unter Windows bzw. als root nicht")
def test_is_writable_detects_read_only_folder(tmp_path):
    folder = tmp_path / "ro"
    folder.mkdir()
    folder.chmod(0o500)
    try:
        assert not paths.is_writable(folder)
    finally:
        folder.chmod(0o700)


# ---- Plattformschicht ------------------------------------------------------------------------------------------
def test_platform_components_report_availability():
    for module in (printing, speech, scanner, external):
        result = module.available()
        assert isinstance(result, Availability)
        assert result.ok or result.hint                        # fehlt etwas, steht dabei, was


def test_speech_hints_name_the_missing_part(monkeypatch):
    from notex import APP_NAME
    monkeypatch.setattr(speech, "_module", lambda: None)              # Qt-Sprachausgabe lädt nicht (libpulse …)
    result = speech.available()
    assert not result.ok and "nicht laden" in result.hint and speech.engines() == []
    assert APP_NAME in speech.LINUX_HINT and "fckNotes" not in speech.LINUX_HINT.replace(APP_NAME, "")


def test_external_refuses_executables(tmp_path):
    for name, content in (("tool.exe", b"MZ\x90"), ("run.sh", b"#!/bin/sh"), ("x.PDF.lnk", b"x"),
                          ("noext", b"\x7fELF...")):
        path = tmp_path / name
        path.write_bytes(content)
        assert external.is_executable(path), name
        assert external.open_path(path) is False
    doc = tmp_path / "bericht.pdf"
    doc.write_bytes(b"%PDF-1.7")
    assert not external.is_executable(doc)
    zipdoc = tmp_path / "brief.docx"
    zipdoc.write_bytes(b"PK\x03\x04rest")
    assert not external.is_executable(zipdoc)
    assert external.is_executable(Path(tmp_path / "gibtsnicht.exe"))


# ---- Linux-AppImage --------------------------------------------------------------------------------------------
def test_make_appdir_layout(tmp_path):
    import importlib.util
    from notex import EXE_NAME
    spec = importlib.util.spec_from_file_location("make_appdir", Path(__file__).resolve().parent.parent / "tools" / "make_appdir.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    source = tmp_path / "dist" / EXE_NAME
    (source / "_internal").mkdir(parents=True)
    (source / EXE_NAME).write_text("#!/bin/sh\n")
    target = module.build(source, tmp_path / "AppDir")
    if os.name != "nt":                                     # Ausführrecht gibt es nur unter Unix (AppImage = Linux)
        assert (target / "AppRun").stat().st_mode & 0o111
    assert (target / "AppRun").read_text(encoding="utf-8").startswith("#!/bin/sh")
    assert (target / "usr" / "lib" / EXE_NAME / EXE_NAME).exists()
    desktop = (target / f"{EXE_NAME}.desktop").read_text(encoding="utf-8")
    assert f"Exec={EXE_NAME}" in desktop and f"Icon={EXE_NAME}" in desktop
    assert (target / f"{EXE_NAME}.png").exists() and (target / ".DirIcon").exists()


def test_appimage_data_lives_next_to_the_image(tmp_path, monkeypatch):
    import sys
    monkeypatch.delenv("NOTEX_ROOT", raising=False)
    monkeypatch.setenv("APPIMAGE", str(tmp_path / "fckNotes-1.18.0.AppImage"))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    paths._WRITABLE.clear()
    assert paths.app_root() == tmp_path / "fckNotes-Daten"
    paths._WRITABLE.clear()
