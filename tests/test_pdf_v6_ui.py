"""V6 in der Oberfläche: Passwort-PDFs nach Eingabe voll bearbeiten, beim Speichern Schutz behalten/entfernen
(immer AES-256), Rechte respektieren, Schutz-Dialog, Schwärzen nach Muster mit Vorschau."""
import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QDialog, QInputDialog  # noqa: E402

from notex.core import pdfsecurity, redact_patterns  # noqa: E402
from notex.core.pdfpages import PdfEditError  # noqa: E402
from pdffixtures import PASSWORD, assert_valid_pdf, encrypted  # noqa: E402
from pdfhelp import make_pdf, texts  # noqa: E402


def _answers(monkeypatch, *answers):
    queue = list(answers)
    asked = []

    def get_text(*args, **kwargs):
        asked.append(args[2] if len(args) > 2 else "")
        return (queue.pop(0), True) if queue else ("", False)
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(get_text))
    return asked


def _open(win, name, data):
    path = win.root / name
    path.write_bytes(data)
    return win.tabs.open_viewer(path, "pdf")


def test_open_with_password_retry_edit_and_keep_protection(win, monkeypatch):
    asked = _answers(monkeypatch, "falsch", PASSWORD)
    tab = _open(win, "geheim.pdf", encrypted("AES-256"))
    assert len(asked) == 2 and "Falsches Passwort" in asked[1]
    assert not tab.error and tab.data is not None and not tab.edit_block_reason()
    assert texts(tab.data) == ["Verschlüsselter Inhalt"] and tab.protection is not None
    tab.set_editing(True)
    assert tab.rotate_pages(90, [0])
    monkeypatch.setattr(type(tab), "_ask_keep_protection", lambda self: "keep")
    assert tab.save()
    saved = tab.path.read_bytes()
    info = pdfsecurity.inspect(saved)
    assert info.encrypted and info.revision == 6
    assert texts(pdfsecurity.unlock(saved, PASSWORD)[0]) == ["Verschlüsselter Inhalt"]
    assert PASSWORD.encode() not in saved


def test_cancel_password_keeps_tab_read_only(win, monkeypatch):
    _answers(monkeypatch)
    tab = _open(win, "zu.pdf", encrypted("AES-256"))
    assert tab.error.startswith("Passwortgeschützt") and tab.edit_block_reason()


def test_legacy_rc4_is_saved_as_aes256_or_without_protection(win, monkeypatch):
    _answers(monkeypatch, PASSWORD)
    tab = _open(win, "alt.pdf", encrypted("RC4-128"))
    tab.set_editing(True)
    tab.rotate_pages(90, [0])
    monkeypatch.setattr(type(tab), "_ask_keep_protection", lambda self: "keep")
    target = win.root / "neu.pdf"
    assert tab.save_as(target)
    assert pdfsecurity.inspect(target.read_bytes()).revision == 6
    tab.rotate_pages(90, [0])
    tab._keep_protection = False
    assert tab.save()
    assert not pdfsecurity.inspect(tab.path.read_bytes()).encrypted
    assert_valid_pdf(tab.path.read_bytes())


def test_cancel_on_save_question_writes_nothing(win, monkeypatch):
    _answers(monkeypatch, PASSWORD)
    tab = _open(win, "geheim.pdf", encrypted("AES-256"))
    before = tab.path.read_bytes()
    tab.set_editing(True)
    tab.rotate_pages(90, [0])
    monkeypatch.setattr(type(tab), "_ask_keep_protection", lambda self: "cancel")
    assert not tab.save() and tab.path.read_bytes() == before and tab.is_dirty


def test_rights_are_respected_until_owner_password(win, monkeypatch):
    rights = pdfsecurity.Permissions(modify=False, copy=False)
    data = pdfsecurity.protect(make_pdf(["Nur lesen"], outline=False), "", "chef", rights)
    asked = _answers(monkeypatch)
    tab = _open(win, "rechte.pdf", data)
    assert asked == [] and not tab.error                                  # öffnet ohne Passwort
    assert "Rechte-Passwort" in tab.edit_block_reason()
    assert not tab.unlock_rights("falsch")
    assert tab.unlock_rights("chef") and not tab.edit_block_reason()
    tab.set_editing(True)
    tab.rotate_pages(90, [0])
    tab._keep_protection = True
    assert tab.save()
    saved = tab.path.read_bytes()
    assert pdfsecurity.inspect(saved).permissions == rights
    assert pdfsecurity.unlock(saved, "chef")[1].unlocked_as == "owner"


def test_set_and_remove_protection(win, tmp_path):
    tab = _open(win, "offen.pdf", make_pdf(["Offen"], outline=False))
    assert tab.edit_protection(pdfsecurity.Protection("neu-pw", None, pdfsecurity.Permissions(print=False)))
    assert tab.is_dirty and tab.save()
    saved = tab.path.read_bytes()
    assert pdfsecurity.inspect(saved).needs_password
    plain, prot = pdfsecurity.unlock(saved, "neu-pw")
    assert not prot.permissions.print and texts(plain) == ["Offen"]
    assert tab.edit_protection(None) and tab.save()
    assert not pdfsecurity.inspect(tab.path.read_bytes()).encrypted


def test_protection_dialog_validates(win):
    from notex.ui.pdf_security_ui import ProtectionDialog
    tab = _open(win, "offen.pdf", make_pdf(["Offen"], outline=False))
    dialog = ProtectionDialog(tab, None)
    dialog.open_on.setChecked(True)
    dialog.open_pw.setText("a")
    dialog.open_pw2.setText("b")
    with pytest.raises(PdfEditError, match="stimmen nicht"):
        dialog.protection()
    dialog.open_pw2.setText("a")
    dialog.rights_on.setChecked(True)
    dialog.boxes["copy"].setChecked(False)
    dialog.rights_pw.setText("a")
    dialog.rights_pw2.setText("a")
    with pytest.raises(PdfEditError, match="unterscheiden"):
        dialog.protection()
    dialog.rights_pw.setText("chef")
    dialog.rights_pw2.setText("chef")
    prot = dialog.protection()
    assert prot.user_password == "a" and prot.owner_password == "chef" and not prot.permissions.copy
    assert "chef" not in repr(prot)
    dialog._remove()
    assert dialog.protection() is None


def test_redact_by_pattern_with_preview(win, monkeypatch):
    tab = _open(win, "kunde.pdf", make_pdf(["Mail max.muster@example.org", "IBAN DE89 3704 0044 0532 0130 00"],
                                           size=(600, 800), outline=False))
    applied = []
    monkeypatch.setattr(type(tab), "apply_redactions", lambda self, *a, **k: applied.append(True) or True)
    count = tab.redact_patterns(["email", "iban"], choose=lambda hits: [h for h in hits if h.key == "iban"],
                                apply_now=True)
    assert count == 1 and applied == [True]
    assert [(p, round(r[0])) for p, r in tab.redactions] == [(1, round(tab.redactions[0][1][0]))]
    assert tab.redact_terms == ["DE89 3704 0044 0532 0130 00"] and tab.redact_apply.isVisible() is not None


def test_pattern_dialog_custom_patterns_saved(win, monkeypatch):
    from notex.ui.pdf_security_ui import PatternRedactDialog
    tab = _open(win, "kunde.pdf", make_pdf(["Akte AZ-2026/12"], outline=False))
    tab.set_editing(True)

    def setup(dialog):
        for box in dialog.boxes.values():
            box.setChecked(False)
        dialog.name.setText("Aktenzeichen")
        dialog.regex.setText("([")
        dialog._add_clicked()
        assert "Ungültig" in dialog.error.text()
        dialog.regex.setText(r"AZ-\d{4}/\d+")
        dialog._add_clicked()
    monkeypatch.setattr(PatternRedactDialog, "exec", lambda self: (setup(self), QDialog.DialogCode.Accepted)[-1])
    from notex.ui.pdf_security_ui import PatternHitsDialog
    monkeypatch.setattr(PatternHitsDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    monkeypatch.setattr("notex.ui.tasks.TaskDialog.show", lambda self: None)
    tab.redact_patterns()                                               # läuft im Hintergrund
    from PySide6.QtCore import QCoreApplication
    for _ in range(200):
        QCoreApplication.processEvents()
        if tab.redactions:
            break
    assert win.config["pdf_redact_patterns"] == [{"name": "Aktenzeichen", "regex": r"AZ-\d{4}/\d+", "on": True}]
    assert tab.redact_terms == ["AZ-2026/12"]


def test_palette_entries(win):
    for key in ("pdf:protect", "pdf:unlock_rights", "pdf:redact_patterns"):
        assert win.registry.get(key) is not None, key


def test_pattern_labels_cover_all():
    assert all(redact_patterns.label_of(k) for k in redact_patterns.PATTERNS)
