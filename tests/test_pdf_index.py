"""Block V10: PDF-Index (SQLite) – inkrementell, Hash, Passwort-PDFs, Unicode-Pfade, Groß/Klein, Abbrechen,
Suche über core.search."""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import pytest

from notex.core import pdfindex as X
from notex.core import search as S
from pdffixtures import encrypted, scanned
from pdfhelp import make_pdf


@pytest.fixture
def folder(tmp_path) -> Path:
    root = tmp_path / "daten"
    (root / "unter").mkdir(parents=True)
    (root / ".versteckt").mkdir()
    (root / "a.pdf").write_bytes(make_pdf(["Hallo Welt", "Zweite Seite mit Rechnung 2024"], outline=False))
    (root / "unter" / "B.PDF").write_bytes(make_pdf(["Rechnung Nummer 7"], outline=False))
    (root / "Bericht äöü 日本.pdf").write_bytes(make_pdf(["Groesse und \xc4rger"], outline=False))
    (root / ".versteckt" / "c.pdf").write_bytes(make_pdf(["Rechnung versteckt"], outline=False))
    return root


def run(root: Path, text: str, db: Path, **options) -> S.SearchResult:
    opts = S.SearchOptions(by_name=False, full_text=False, pdf=True, pdf_index=db, **options)
    return S.search(root, text, opts)


def hits(result: S.SearchResult) -> dict[str, list[int]]:
    return {m.relative: [h.page for h in m.hits] for m in result.pdfs}


def test_finds_pages_recursive_uppercase_extension_and_unicode(folder, tmp_path):
    db = tmp_path / "cache" / "pdf-index.sqlite"
    result = run(folder, "rechnung", db)
    assert hits(result) == {"a.pdf": [1], "unter/B.PDF": [0]}                     # versteckte Ordner nicht
    result = run(folder, "ärger", db)                                              # Groß/Klein auch für Umlaute
    assert hits(result) == {"Bericht äöü 日本.pdf": [0]}
    hit = result.pdfs[0].hits[0]
    assert hit.text == "Ärger" and hit.snippet[hit.start:hit.end] == "Ärger"


def test_phrase_regex_whole_word_and_filters(folder, tmp_path):
    db = tmp_path / "idx.sqlite"
    assert hits(run(folder, '"rechnung nummer"', db)) == {"unter/B.PDF": [0]}
    assert hits(run(folder, r"rechnung \d{4}", db, regex=True)) == {"a.pdf": [1]}
    assert hits(run(folder, "rech", db, whole_word=True)) == {}
    assert hits(run(folder, "rechnung path:unter", db)) == {"unter/B.PDF": [0]}
    assert hits(run(folder, "rechnung -path:unter", db)) == {"a.pdf": [1]}
    assert hits(run(folder, "rechnung ext:md", db)) == {}
    assert run(folder, "Rechnung", db, case_sensitive=True).pdfs
    assert not run(folder, "RECHNUNG", db, case_sensitive=True).pdfs


def test_incremental_update_uses_mtime_size_and_hash(folder, tmp_path):
    index = X.PdfIndex(tmp_path / "idx.sqlite")
    files = X.pdf_files(folder)
    first = index.update(files, folder)
    assert first.extracted == 3 and first.reused == 0
    again = index.update(files, folder)
    assert again.extracted == 0 and again.reused == 0                             # nichts geändert
    target = folder / "a.pdf"
    stamp = target.stat().st_mtime + 10
    os.utime(target, (stamp, stamp))                                               # nur berührt
    touched = index.update(files, folder)
    assert touched.extracted == 0 and touched.reused == 1
    target.write_bytes(make_pdf(["Ganz neuer Inhalt"], outline=False))             # wirklich geändert
    os.utime(target, (stamp + 10, stamp + 10))
    changed = index.update(files, folder)
    assert changed.extracted == 1
    query = S.parse_query("neuer")
    assert [m.relative for m in index.search(files, query, folder)] == ["a.pdf"]
    assert not index.search(files, S.parse_query("hallo"), folder)


def test_copy_reuses_text_by_hash(folder, tmp_path):
    index = X.PdfIndex(tmp_path / "idx.sqlite")
    index.update(X.pdf_files(folder), folder)
    (folder / "kopie.pdf").write_bytes((folder / "a.pdf").read_bytes())
    stats = index.update(X.pdf_files(folder), folder)
    assert stats.reused == 1 and stats.extracted == 0
    assert "kopie.pdf" in {m.relative for m in index.search(X.pdf_files(folder), S.parse_query("welt"), folder)}


def test_password_and_scanned_pdfs_are_reported_not_searched(folder, tmp_path):
    (folder / "geheim.pdf").write_bytes(encrypted())
    (folder / "scan.pdf").write_bytes(scanned())
    result = run(folder, "inhalt", tmp_path / "idx.sqlite")
    reasons = dict(result.pdf_skipped)
    assert reasons["geheim.pdf"] == "Passwortschutz" and reasons["scan.pdf"] == "kein Text (Scan?)"
    again = run(folder, "inhalt", tmp_path / "idx.sqlite")                         # auch aus dem Index gemeldet
    assert dict(again.pdf_skipped)["geheim.pdf"] == "Passwortschutz"


def test_too_large_is_skipped(folder, tmp_path):
    index = X.PdfIndex(tmp_path / "idx.sqlite")
    stats = index.update(X.pdf_files(folder), folder, max_bytes=10)
    assert {reason for _p, reason in stats.skipped} == {"zu groß"}


def test_deleted_files_are_pruned_and_old_text_overwritten(folder, tmp_path):
    db = tmp_path / "idx.sqlite"
    run(folder, "welt", db)
    (folder / "a.pdf").unlink()
    assert hits(run(folder, "welt", db)) == {}
    raw = b"".join(p.read_bytes() for p in tmp_path.glob("idx.sqlite*"))
    assert b"Hallo Welt" not in raw                                                 # secure_delete + Checkpoint


def test_forget_and_clear(folder, tmp_path):
    index = X.PdfIndex(tmp_path / "idx.sqlite")
    index.update(X.pdf_files(folder), folder)
    assert index.status(folder / "a.pdf") == "ok"
    index.forget(folder / "a.pdf")
    assert index.status(folder / "a.pdf") is None
    index.clear()
    assert not (tmp_path / "idx.sqlite").exists()


def test_cancel_and_progress(folder, tmp_path):
    seen = []
    cancel = threading.Event()

    def progress(done, total, name):
        seen.append((done, total))
        if done == 1:
            cancel.set()
    opts = S.SearchOptions(by_name=False, full_text=False, pdf=True, pdf_index=tmp_path / "idx.sqlite")
    result = S.search(folder, "rechnung", opts, cancel, on_progress=progress)
    assert result.cancelled and seen[0] == (0, 3)
    result = S.search(folder, "rechnung", opts, on_pdf=lambda m: seen.append(m.relative))
    assert "a.pdf" in seen


def test_combined_with_text_search(folder, tmp_path):
    (folder / "notiz.md").write_text("Rechnung im Text\n", encoding="utf-8")
    opts = S.SearchOptions(by_name=True, full_text=True, pdf=True, pdf_index=tmp_path / "idx.sqlite")
    result = S.search(folder, "rechnung", opts)
    assert [f.relative for f in result.files] == ["notiz.md"] and len(result.pdfs) == 2


def test_regex_timeout_is_reported(folder, tmp_path, monkeypatch):
    def slow(*args, **kwargs):
        raise S.RegexTimeout("zu langsam")
    monkeypatch.setattr(S, "match_spans", slow)
    result = run(folder, "x", tmp_path / "idx.sqlite")
    assert result.timed_out


def test_extract_pages_statuses():
    assert X.extract_pages(make_pdf(["A", "B"], outline=False)) == ("ok", ["A", "B"])
    assert X.extract_pages(encrypted())[0] == "encrypted"
    assert X.extract_pages(b"kein pdf")[0] == "error"


def test_speed_second_search_uses_index(folder, tmp_path):
    db = tmp_path / "idx.sqlite"
    run(folder, "welt", db)
    started = time.perf_counter()
    run(folder, "welt", db)
    assert time.perf_counter() - started < 2.0
