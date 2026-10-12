"""Passwortschutz für PDFs (Qt-frei, Block V6): prüfen, entsperren, schützen.

- Lesen: RC4 (40/128 Bit), AES-128 und AES-256 – mit Öffnen- (Benutzer-) oder Rechte- (Besitzer-)Passwort.
- Schreiben: **nur AES-256 (R6)**. Ein altes RC4-/AES-128-PDF bekommt beim Speichern mit Schutz AES-256.
- Rechte (Drucken, Ändern, Kopieren …) sind laut PDF-Norm nur eine Bitte an den Betrachter, kein echter Schutz:
  Wer das Öffnen-Passwort kennt, kann den Inhalt lesen; ein Programm kann die Rechte ignorieren. fckNotes hält sich
  daran: Wurde nur mit dem Öffnen-Passwort entsperrt und verbietet das PDF Änderungen, bleibt es nur lesbar, bis das
  Rechte-Passwort eingegeben ist.

Passwörter: nur in Variablen, nie im Log, nirgends gespeichert. Python kann Zeichenketten nicht zuverlässig aus dem
Speicher löschen – `Protection` lebt deshalb nur so lange, bis der Tab das PDF wieder verschlüsselt gespeichert oder
den Schutz entfernt hat.
"""
from __future__ import annotations

import io
import secrets
from dataclasses import dataclass, field, replace

from pypdf import PdfReader, PdfWriter
from pypdf.errors import PdfReadError

from notex.core.pdfpages import PdfEditError

# Rechte-Bits aus /P (PDF 32000-1, Tabelle 22; Bit 1 = 1)
BITS = {"print": 3, "modify": 4, "copy": 5, "annotate": 6, "fill_forms": 9, "accessibility": 10, "assemble": 11,
        "print_high": 12}
LABELS = {"print": "Drucken", "print_high": "Drucken in hoher Qualität", "modify": "Inhalt ändern",
          "copy": "Text und Bilder kopieren", "annotate": "Kommentieren", "fill_forms": "Formulare ausfüllen",
          "accessibility": "Text für Bildschirmleser freigeben", "assemble": "Seiten einfügen, drehen, löschen"}
ALGORITHMS = {(1, 2): "RC4 40 Bit", (2, 3): "RC4 128 Bit", (4, 4): "AES-128 (oder RC4 128 Bit)", (5, 5): "AES-256 (R5)",
              (5, 6): "AES-256"}
MIN_PASSWORD = 1


@dataclass(frozen=True)
class Permissions:
    print: bool = True
    print_high: bool = True
    modify: bool = True
    copy: bool = True
    annotate: bool = True
    fill_forms: bool = True
    accessibility: bool = True
    assemble: bool = True

    def flags(self) -> int:
        """/P als vorzeichenlose 32-Bit-Zahl: reservierte Bits gesetzt, verbotene Bits gelöscht."""
        value = 0xFFFFFFFF & ~0b11                        # Bits 1–2 müssen 0 sein
        for name, bit in BITS.items():
            if not getattr(self, name):
                value &= ~(1 << (bit - 1))
        return value

    @classmethod
    def from_flags(cls, value: int) -> "Permissions":
        value = int(value) & 0xFFFFFFFF
        return cls(**{name: bool(value & (1 << (bit - 1))) for name, bit in BITS.items()})

    def denied(self) -> list[str]:
        return [LABELS[name] for name in LABELS if not getattr(self, name)]


@dataclass(frozen=True)
class SecurityInfo:
    encrypted: bool
    algorithm: str = ""
    revision: int = 0
    permissions: Permissions = field(default_factory=Permissions)
    needs_password: bool = False                          # False = öffnet ohne Passwort (nur Rechte-Schutz)

    @property
    def writable_as_is(self) -> bool:
        return self.revision == 6


@dataclass(frozen=True)
class Protection:
    """Schutz, mit dem der Tab beim Speichern wieder verschlüsselt. `user_password` "" = öffnet ohne Passwort."""
    user_password: str | None = None                      # None = unbekannt (mit Rechte-Passwort entsperrt)
    owner_password: str | None = None                     # None = unbekannt (mit Öffnen-Passwort entsperrt)
    permissions: Permissions = field(default_factory=Permissions)
    algorithm: str = "AES-256"
    unlocked_as: str = "user"                            # user | owner

    @property
    def can_edit(self) -> bool:
        return self.unlocked_as == "owner" or self.permissions.modify

    def __repr__(self) -> str:                            # Passwörter nie in Ausgaben/Logs
        return (f"Protection(user={'gesetzt' if self.user_password else 'leer/unbekannt'}, "
                f"owner={'bekannt' if self.owner_password else 'unbekannt'}, unlocked_as={self.unlocked_as})")

    __str__ = __repr__


def _reader(data: bytes) -> PdfReader:
    try:
        return PdfReader(io.BytesIO(data))
    except (PdfReadError, ValueError, KeyError, TypeError) as error:
        raise PdfEditError(f"Kein gültiges PDF ({error})") from error


def inspect(data: bytes) -> SecurityInfo:
    """Ob und wie ein PDF verschlüsselt ist – ohne Passwort."""
    reader = _reader(data)
    if not reader.is_encrypted:
        return SecurityInfo(False)
    encrypt = reader.trailer["/Encrypt"].get_object()
    version, revision = int(encrypt.get("/V", 0)), int(encrypt.get("/R", 0))
    algorithm = ALGORITHMS.get((version, revision), f"V{version}/R{revision}")
    if version == 4:
        cf = encrypt.get("/CF")
        method = ""
        if cf is not None:
            for sub in cf.get_object().values():
                method = str(sub.get_object().get("/CFM", ""))
        algorithm = "AES-128" if method == "/AESV2" else "RC4 128 Bit"
    needs = True
    try:
        needs = not _reader(data).decrypt("")
    except Exception:                                      # noqa: BLE001
        pass
    return SecurityInfo(True, algorithm, revision, Permissions.from_flags(int(encrypt.get("/P", -1))), needs)


def unlock(data: bytes, password: str) -> tuple[bytes, Protection]:
    """Entsperren: liefert das PDF unverschlüsselt (nur im Speicher) und den Schutz zum späteren Wiederherstellen."""
    info = inspect(data)
    if not info.encrypted:
        return data, Protection(unlocked_as="owner")
    reader = _reader(data)
    try:
        result = int(reader.decrypt(password))
    except (NotImplementedError, PdfReadError) as error:
        raise PdfEditError(f"Diese Verschlüsselung wird nicht unterstützt ({error})") from error
    if result == 0:
        raise PdfEditError("Falsches Passwort")
    if result == 2:                                       # Rechte-Passwort: Öffnen-Passwort ist evtl. leer
        user = "" if not info.needs_password else None    # Öffnen-Passwort unbekannt → beim Speichern neu fragen
        protection = Protection(user, password, info.permissions, info.algorithm, "owner")
    else:
        protection = Protection(password, None, info.permissions, info.algorithm, "user")
    try:
        writer = PdfWriter(clone_from=reader)
        buffer = io.BytesIO()
        writer.write(buffer)
    except Exception as error:                            # noqa: BLE001 – kaputte Verschlüsselung: verständlich melden
        raise PdfEditError(f"Entschlüsseln fehlgeschlagen ({error})") from error
    plain = buffer.getvalue()
    if _reader(plain).is_encrypted:
        raise PdfEditError("Entschlüsseln fehlgeschlagen")
    return plain, protection


def protect(data: bytes, user_password: str = "", owner_password: str | None = None,
            permissions: Permissions | None = None) -> bytes:
    """Mit AES-256 (R6) schützen. `user_password` "" = öffnet ohne Passwort (dann wirken nur die Rechte).
    `owner_password` None/"" = zufällig (niemand kann die Rechte später aufheben – außer neu speichern ohne Schutz)."""
    if user_password is None:
        raise PdfEditError("Öffnen-Passwort fehlt")
    reader = _reader(data)
    if reader.is_encrypted:
        raise PdfEditError("Das PDF ist schon verschlüsselt – erst entsperren")
    permissions = permissions or Permissions()
    if not user_password and permissions == Permissions():
        raise PdfEditError("Ohne Öffnen-Passwort und ohne eingeschränkte Rechte gibt es nichts zu schützen")
    owner = owner_password or secrets.token_urlsafe(24)
    if owner == user_password:
        raise PdfEditError("Öffnen- und Rechte-Passwort müssen sich unterscheiden")
    writer = PdfWriter(clone_from=reader)
    writer.encrypt(user_password, owner, permissions_flag=permissions.flags(), algorithm="AES-256")
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def reapply(data: bytes, protection: Protection) -> bytes:
    """Schutz beim Speichern wiederherstellen – immer als AES-256, auch wenn das Original RC4/AES-128 war."""
    if protection.user_password is None:
        raise PdfEditError("Das Öffnen-Passwort ist unbekannt (mit dem Rechte-Passwort entsperrt) – bitte neu setzen")
    return protect(data, protection.user_password, protection.owner_password, protection.permissions)


def with_passwords(protection: Protection, user_password: str | None = None,
                   owner_password: str | None = None, permissions: Permissions | None = None) -> Protection:
    return replace(protection,
                   user_password=protection.user_password if user_password is None else user_password,
                   owner_password=protection.owner_password if owner_password is None else owner_password,
                   permissions=protection.permissions if permissions is None else permissions,
                   algorithm="AES-256", unlocked_as="owner")
