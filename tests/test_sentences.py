"""Block V11: Satz-Zerlegung fürs Vorlesen (Abkürzungen, Zahlen, Listen, Absätze, lange Sätze)."""
from notex.core import sentences as S


def parts(text: str) -> list[str]:
    return [text[s:e] for s, e in S.split(text)]


def test_basic_sentences():
    assert parts("Das ist ein Satz. Und noch einer! Fragen? Ja.") == [
        "Das ist ein Satz.", "Und noch einer!", "Fragen?", "Ja."]


def test_abbreviations_initials_ordinals_numbers():
    assert parts("Wir treffen uns z. B. am 3. Oktober bei Dr. Meier. Danach weiter.") == [
        "Wir treffen uns z. B. am 3. Oktober bei Dr. Meier.", "Danach weiter."]
    assert parts("A. Meier und B. Schulz kamen. Gut so.") == ["A. Meier und B. Schulz kamen.", "Gut so."]
    assert parts("Der Wert ist 3.5 Prozent. Siehe www.example.org dazu.") == [
        "Der Wert ist 3.5 Prozent.", "Siehe www.example.org dazu."]
    assert parts("Das kostet ca. 5 Euro usw. und mehr.") == ["Das kostet ca. 5 Euro usw. und mehr."]


def test_quotes_and_ellipsis():
    assert parts("Er sagte: „Komm her!“ Dann ging er. Und dann…") == [
        "Er sagte: „Komm her!“", "Dann ging er.", "Und dann…"]


def test_headings_lists_paragraphs():
    text = "Überschrift\nDer Text darunter. Zweiter Satz.\n\n- Punkt eins\n- Punkt zwei\n1. Erstens\n2. Zweitens"
    assert parts(text) == ["Überschrift", "Der Text darunter.", "Zweiter Satz.", "- Punkt eins", "- Punkt zwei",
                           "1. Erstens", "2. Zweitens"]


def test_wrapped_lines_stay_one_sentence():
    text = "Dieser Satz geht über\nzwei Zeilen und endet hier. Danach kommt noch einer."
    assert parts(text) == ["Dieser Satz geht über\nzwei Zeilen und endet hier.", "Danach kommt noch einer."]


def test_long_sentences_are_split_and_positions_exact():
    text = ("Wort, " * 120).strip()
    spans = S.split(text)
    assert len(spans) > 1 and all(e - s <= S.MAX_SENTENCE for s, e in spans)
    assert all(not text[s].isspace() and not text[e - 1].isspace() for s, e in spans)
    assert "".join(text[s:e] for s, e in spans).replace(" ", "") == text.replace(" ", "")


def test_sentence_at_and_empty():
    text = "Eins. Zwei. Drei."
    spans = S.split(text)
    assert S.sentence_at(spans, 0) == 0 and S.sentence_at(spans, text.index("Zwei")) == 1
    assert S.sentence_at(spans, len(text)) == 2
    assert S.split("") == [] and S.split("   \n\n ") == []
