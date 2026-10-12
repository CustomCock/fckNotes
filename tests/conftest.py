import os

import pytest


@pytest.fixture
def win(tmp_path, monkeypatch):
    """Echtes Hauptfenster (offscreen, alle Module an). Modale Dialoge blockieren nie."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication, QDialog, QMessageBox
    import uihelp
    monkeypatch.setenv("NOTEX_ROOT", str(tmp_path))
    monkeypatch.setattr(QDialog, "exec", lambda self: 0)
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)
    from PySide6.QtWidgets import QInputDialog
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("", False)))   # nie blockieren
    QApplication.instance() or QApplication([])
    window = uihelp.make_window(tmp_path)
    yield window
    uihelp.close_window(window)


# ---- Netzwerk-Wächter (Regel „offline“) ------------------------------------------------------------------------
# PDF-, Diagramm- und Plattform-Tests dürfen keine Verbindung öffnen. Jeder Versuch lässt den Test scheitern –
# auch dann, wenn eine Bibliothek das „heimlich“ täte.
OFFLINE_MODULES = ("test_pdf", "test_diagram", "test_drawio", "test_v0", "test_pdfaudit", "test_pdffont",
                   "test_xfdf", "test_redact")


class NetworkUsed(AssertionError):
    pass


@pytest.fixture(autouse=True)
def _offline_guard(request, monkeypatch):
    name = request.module.__name__.rsplit(".", 1)[-1]
    if not name.startswith(OFFLINE_MODULES):
        yield
        return
    import socket

    def refuse(*args, **kwargs):
        raise NetworkUsed(f"Netzwerkzugriff in einem Offline-Test: {args[:1]}")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    yield
