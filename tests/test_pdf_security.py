"""V6 (A12): Passwortschutz – AES-256 (R6) mit falschem/richtigem Passwort, RC4/AES-128 nur lesbar und nie
geschrieben, Rechte, Rechte-Passwort, Passwörter nie in Ausgaben."""
import io

import pytest
from pypdf import PdfReader

from notex.core import pdfsecurity as S
from notex.core.pdfpages import PdfEditError, open_reader
from pdffixtures import PASSWORD, assert_valid_pdf, encrypted
from pdfhelp import make_pdf, texts


def _plain():
    return make_pdf(["Geheimer Inhalt"], outline=False)


def _encrypt_info(data):
    enc = PdfReader(io.BytesIO(data)).trailer["/Encrypt"].get_object()
    return int(enc["/V"]), int(enc["/R"]), int(enc["/P"]) & 0xFFFFFFFF


def test_protect_writes_aes256_r6_only():
    data = S.protect(_plain(), "auf-gehts", "chef-passwort")
    assert _encrypt_info(data)[:2] == (5, 6)
    info = S.inspect(data)
    assert info.encrypted and info.algorithm == "AES-256" and info.revision == 6 and info.needs_password
    with pytest.raises(PdfEditError):
        open_reader(data)                                              # ohne Entsperren nicht bearbeitbar


def test_wrong_and_right_password():
    data = S.protect(_plain(), "auf-gehts", "chef-passwort")
    with pytest.raises(PdfEditError, match="Falsches Passwort"):
        S.unlock(data, "falsch")
    plain, prot = S.unlock(data, "auf-gehts")
    assert texts(plain) == ["Geheimer Inhalt"] and not S.inspect(plain).encrypted
    assert prot.unlocked_as == "user" and prot.user_password == "auf-gehts" and prot.owner_password is None
    plain2, owner = S.unlock(data, "chef-passwort")
    assert owner.unlocked_as == "owner" and owner.user_password is None and owner.can_edit
    assert_valid_pdf(plain2)


@pytest.mark.parametrize("algorithm, label", [("RC4-128", "RC4 128 Bit"), ("AES-128", "AES-128")])
def test_legacy_encryption_is_read_but_never_written(algorithm, label):
    data = encrypted(algorithm)
    info = S.inspect(data)
    assert info.algorithm == label and not info.writable_as_is
    plain, prot = S.unlock(data, PASSWORD)
    assert texts(plain) == ["Verschlüsselter Inhalt"]
    again = S.reapply(plain, prot)
    assert _encrypt_info(again)[:2] == (5, 6)                          # beim Speichern immer AES-256
    assert texts(S.unlock(again, PASSWORD)[0]) == ["Verschlüsselter Inhalt"]
    assert b"/V 2" not in again and b"/V 4" not in again


def test_permissions_roundtrip_and_respected():
    rights = S.Permissions(print=True, print_high=False, modify=False, copy=False, annotate=True, fill_forms=True,
                           accessibility=True, assemble=False)
    data = S.protect(_plain(), "", "chef-passwort", rights)
    info = S.inspect(data)
    assert not info.needs_password and info.permissions == rights
    assert set(info.permissions.denied()) == {"Drucken in hoher Qualität", "Inhalt ändern",
                                              "Text und Bilder kopieren", "Seiten einfügen, drehen, löschen"}
    plain, prot = S.unlock(data, "")
    assert prot.unlocked_as == "user" and not prot.can_edit             # Rechte verbieten Ändern
    _plain2, owner = S.unlock(data, "chef-passwort")
    assert owner.can_edit and owner.user_password == ""                  # öffnet ohne Passwort


def test_flags_bits():
    assert S.Permissions().flags() & 0b11 == 0
    assert S.Permissions(print=False).flags() & (1 << 2) == 0
    assert S.Permissions.from_flags(S.Permissions(copy=False).flags()) == S.Permissions(copy=False)


def test_reapply_needs_known_user_password():
    data = S.protect(_plain(), "auf-gehts", "chef-passwort")
    _plain2, owner = S.unlock(data, "chef-passwort")
    with pytest.raises(PdfEditError, match="Öffnen-Passwort"):
        S.reapply(_plain2, owner)
    fixed = S.with_passwords(owner, user_password="neu")
    again = S.reapply(_plain2, fixed)
    assert texts(S.unlock(again, "neu")[0]) == ["Geheimer Inhalt"]
    assert S.unlock(again, "chef-passwort")[1].unlocked_as == "owner"   # Rechte-Passwort bleibt


@pytest.mark.parametrize("user, owner, rights", [("", None, None), ("gleich", "gleich", None)])
def test_invalid_protection(user, owner, rights):
    with pytest.raises(PdfEditError):
        S.protect(_plain(), user, owner, rights)


def test_already_encrypted_rejected_and_unencrypted_unlock_is_noop():
    data = S.protect(_plain(), "x")
    with pytest.raises(PdfEditError):
        S.protect(data, "y")
    plain = _plain()
    assert S.unlock(plain, "")[0] == plain


def test_random_owner_password_when_none_given():
    one, two = S.protect(_plain(), "x"), S.protect(_plain(), "x")
    assert S.unlock(one, "x")[1].unlocked_as == "user"
    assert one != two                                                     # zufälliges Rechte-Passwort, Salt


def test_passwords_never_in_repr():
    prot = S.Protection("streng-geheim", "noch-geheimer")
    assert "geheim" not in repr(prot) and "geheim" not in str(prot)
