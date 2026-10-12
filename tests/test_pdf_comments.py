"""V4 (A10, Teil 2): Kommentare als Threads – Antworten, Status, markierter Text, Filter, Zusammenfassung."""
import csv
import io
from datetime import datetime, timedelta, timezone

import pytest
from pypdf import PdfReader

from notex.core import pdfannot as A, pdfcomments as C, pdfdraw, pdfobjects
from notex.core.pdfpages import PdfEditError
from pdffixtures import assert_valid_pdf
from pdfhelp import make_pdf


def _doc():
    data = make_pdf(["Die Biene sammelt Nektar", "Seite zwei"], size=(600, 800), outline=False)
    data = A.add_note(data, 1, 100, 100, "Bitte prüfen")
    words = [(73, 82, 351, 101)]                                 # Zeile „Die Biene sammelt Nektar“ auf Seite 1
    return A.add_markup(data, 0, "highlight", words, contents="Wichtig")


def test_list_with_quoted_text_and_order():
    comments = C.list_comments(_doc())
    assert [(c.page, c.kind) for c in comments] == [(0, "highlight"), (1, "note")]
    mark = comments[0]
    assert "Biene" in mark.quoted and mark.contents == "Wichtig" and mark.status == "open"
    assert mark.label == "Markierung" and mark.status_label == "offen" and mark.date is not None


def test_replies_threads_and_nested_replies_stay_connected():
    data = _doc()
    data = C.add_reply(data, 0, 0, "Sehe ich auch so", "Mia")
    data = C.add_reply(data, 0, 0, "Danke!", "Jo")
    reply_index = len(PdfReader(io.BytesIO(data)).pages[0]["/Annots"]) - 1
    data = C.add_reply(data, 0, reply_index, "Antwort auf die Antwort", "Mia")   # hängt am Ursprung
    assert_valid_pdf(data)
    comments = C.list_comments(data)
    assert len(comments) == 2                                    # Antworten sind keine eigenen Kommentare
    mark = comments[0]
    assert [r.contents for r in mark.replies] == ["Sehe ich auch so", "Danke!", "Antwort auf die Antwort"]
    assert [r.author for r in mark.replies] == ["Mia", "Jo", "Mia"]
    assert mark.replies[0].parent_nm == mark.nm
    annot = PdfReader(io.BytesIO(data)).pages[0]["/Annots"][1].get_object()
    assert annot["/RT"] == "/R" and annot["/IRT"].get_object()["/Subtype"] == "/Highlight"
    assert int(annot["/F"]) & C.FLAG_NOVIEW
    assert len(A.list_annotations(data, 0)) == 1 and len(pdfobjects.list_objects(data, 0)) == 1
    assert C.find(comments, mark.replies[1].nm).contents == "Danke!"


def test_status_latest_wins_and_is_hidden():
    data = C.set_status(_doc(), 0, 0, "accepted", "Mia")
    data = C.set_status(data, 0, 0, "done", "Jo")
    mark = C.list_comments(data)[0]
    assert (mark.status, mark.status_by, mark.status_label) == ("done", "Jo", "erledigt")
    entries = [a.get_object() for a in PdfReader(io.BytesIO(data)).pages[0]["/Annots"]]
    states = [e for e in entries if "/State" in e]
    assert [str(e["/State"]) for e in states] == ["/Accepted", "/Completed"]
    assert all(e["/StateModel"] == "/Review" and int(e["/F"]) & C.FLAG_HIDDEN for e in states)
    with pytest.raises(PdfEditError):
        C.set_status(data, 0, 0, "vielleicht")


def test_deleting_comment_removes_whole_thread():
    data = C.add_reply(_doc(), 0, 0, "Antwort", "Mia")
    data = C.set_status(data, 0, 0, "rejected", "Jo")
    out = A.delete_annotation(data, 0, 0)
    assert PdfReader(io.BytesIO(out)).pages[0].get("/Annots") is None
    assert [c.kind for c in C.list_comments(out)] == ["note"]
    assert_valid_pdf(out)


def test_eraser_removes_ink_thread():
    data = pdfdraw.add_ink(_doc(), 1, [[(50, 300), (200, 300)]])
    ink = next(o for o in pdfobjects.list_objects(data, 1) if o.kind == "ink")
    data = C.add_reply(data, 1, ink.index, "Was heißt das?", "Jo")
    out, removed = pdfdraw.erase_at(data, 1, [(120, 300)])
    assert removed == 1 and [c.kind for c in C.list_comments(out)] == ["highlight", "note"]
    assert all("/IRT" not in a.get_object() for a in PdfReader(io.BytesIO(out)).pages[1]["/Annots"])


def test_invalid_targets():
    with pytest.raises(PdfEditError):
        C.add_reply(_doc(), 0, 0, "   ")
    with pytest.raises(PdfEditError):
        C.add_reply(_doc(), 0, 9, "x")


def test_filter_and_sort():
    data = C.add_reply(_doc(), 0, 0, "Nektar ist süß", "Mia")
    data = C.set_status(data, 1, 0, "done", "Jo")
    comments = C.list_comments(data)
    assert [c.kind for c in C.filter_comments(comments, status="done")] == ["note"]
    assert [c.kind for c in C.filter_comments(comments, kind="highlight")] == ["highlight"]
    assert [c.kind for c in C.filter_comments(comments, page=1)] == ["note"]
    assert [c.kind for c in C.filter_comments(comments, text="süß")] == ["highlight"]   # sucht auch in Antworten
    assert [c.kind for c in C.filter_comments(comments, sort="page", descending=True)] == ["note", "highlight"]
    future = datetime.now(timezone.utc) + timedelta(days=1)
    assert C.filter_comments(comments, since=future) == []


@pytest.mark.parametrize("raw, expected", [
    ("D:20261011183000Z", datetime(2026, 10, 11, 18, 30, tzinfo=timezone.utc)),
    ("D:20261011183000+02'00'", datetime(2026, 10, 11, 16, 30, tzinfo=timezone.utc)),
    ("D:2026", datetime(2026, 1, 1, tzinfo=timezone.utc)),
    ("20261011", datetime(2026, 10, 11, tzinfo=timezone.utc)),
])
def test_parse_date(raw, expected):
    assert C.parse_date(raw) == expected


@pytest.mark.parametrize("raw", ["", "gestern", "D:20261399"])
def test_parse_bad_dates(raw):
    assert C.parse_date(raw) is None


def test_markdown_and_csv_summary():
    data = C.add_reply(_doc(), 0, 0, "Sehe ich auch so", "Mia")
    data = C.set_status(data, 0, 0, "accepted", "Jo")
    comments = C.list_comments(data)
    md = C.summary_markdown(comments, "aufsatz.pdf")
    assert md.startswith("# Kommentare – aufsatz.pdf")
    assert "## Seite 1" in md and "## Seite 2" in md and "angenommen" in md
    assert "> Die Biene sammelt Nektar" in md and "*Antwort Mia" in md and "Bitte prüfen" in md
    rows = list(csv.reader(io.StringIO(C.summary_csv(comments)), delimiter=";"))
    assert rows[0] == C.CSV_HEADER
    assert rows[1][1:3] == ["1", "Markierung"] and rows[1][5] == "angenommen" and "Biene" in rows[1][7]
    assert rows[2][2] == "Antwort" and rows[2][8] == "1" and rows[2][6] == "Sehe ich auch so"
    assert rows[3][2] == "Notiz"
    assert C.summary_markdown([]).endswith("Keine Kommentare.\n")


def test_csv_quotes_correctly():
    data = A.add_note(make_pdf(["x"], outline=False), 0, 10, 10, 'Zeile 1; "Zitat"\nZeile 2')
    rows = list(csv.reader(io.StringIO(C.summary_csv(C.list_comments(data))), delimiter=";"))
    assert rows[1][6] == 'Zeile 1; "Zitat"\nZeile 2'


@pytest.mark.parametrize("text", ["=HYPERLINK(\"http://x\")", "+1+1", "-2", "@SUM(A1)"])
def test_csv_neutralizes_formulas(text):
    data = A.add_note(make_pdf(["x"], outline=False), 0, 10, 10, text)
    data = C.add_reply(data, 0, 0, text, "=Autor")
    rows = list(csv.reader(io.StringIO(C.summary_csv(C.list_comments(data))), delimiter=";"))
    assert rows[1][6] == "'" + text and rows[2][6] == "'" + text and rows[2][3] == "'=Autor"
