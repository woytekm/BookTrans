"""Footnote classification in Stage 2: small lines at the foot of a column, wherever they start, one block per note."""
import stage2


def line(y, text, h=12, x1=72, x2=642):
    return {'bbox': [x1, y, x2, y + h + 3], 'glyph_height': h, 'type': 'body_line', 'text': text}


def body_and_notes(gap=22):
    body = [line(764 + i * 22, 'Fließtext Zeile %d mit genug Wörtern hier' % i, h=14) for i in range(9)]
    top = body[-1]['bbox'][3] + gap
    notes = [line(top, '1 Hinweis auf das anliegende Schrifttumsverzeichnis: die Zahl'),
             line(top + 19, 'die laufende Nummer des Verzeichnisses, des Belegwerkes.'),
             line(top + 38, '2 ... Wir haben verstattet und verstatten gnädig'),
             line(top + 57, 'der Pfarrkirche d. hl. Jakobus ... erbauen könne.“')]
    return body, notes


def test_small_lines_at_the_foot_are_footnotes_above_the_old_bottom_band():
    """Scan 0010 p71: the notes start at 76 % of the page height; the old rule wanted 82 %."""
    body, notes = body_and_notes()
    stage2.mark_foot_notes(body + notes, 14.0, 1310)
    assert all(l['type'] == 'footnote_or_footer' for l in notes)
    assert all(l['type'] == 'body_line' for l in body)


def test_small_text_with_normal_leading_or_body_text_below_is_not_a_footnote():
    body, notes = body_and_notes(gap=6)                  # continues the text above
    stage2.mark_foot_notes(body + notes, 14.0, 1310)
    assert all(l['type'] == 'body_line' for l in notes)
    body, notes = body_and_notes()
    after = [line(notes[-1]['bbox'][3] + 20, 'Und weiter geht der Fließtext auf dieser Seite', h=14)]
    stage2.mark_foot_notes(body + notes + after, 14.0, 1310)
    assert all(l['type'] == 'body_line' for l in notes)  # a small passage inside the text
    _, notes = body_and_notes()
    stage2.mark_foot_notes(notes, 14.0, 2400)            # small print alone in the upper half: text, not notes
    assert all(l['type'] == 'body_line' for l in notes)


def test_a_speck_does_not_start_a_footnote():
    body, _ = body_and_notes()
    speck = line(1100, '|', h=10, x1=65, x2=67)
    stage2.mark_foot_notes(body + [speck], 14.0, 1310)
    assert speck['type'] == 'body_line'


def test_each_note_is_its_own_block():
    _, notes = body_and_notes()
    parts = stage2.split_notes(notes)
    assert [len(p) for p in parts] == [2, 2]
    one = [{'text': 'Vgl. Lutsch, Verzeichnis der'}, {'text': '1298 erwähnt.'}]   # no sentence end before "1298"
    assert len(stage2.split_notes(one)) == 1
    marks = [{'text': 'Erste Anmerkung.'}, {'text': '2) Zweite'}, {'text': 'Ende.'}, {'text': '³ Dritte'},
             {'text': 'Schluss.'}, {'text': '* Stern'}]
    assert [len(p) for p in stage2.split_notes(marks)] == [1, 2, 2, 1]


def test_a_note_under_the_last_paragraph_counts_anywhere_on_the_page():
    """Page 70: the chapter ends half way down; a two-line "Literatur:" note in smaller type follows the last
    paragraph at 46 % of the page height.  It was set as body text and flowed into the paragraph above."""
    body, _ = body_and_notes()
    note = [line(980, 'Literatur: Romanische Portale in Niederösterreich von Richard Kurt'),
            line(998, 'und französische Kunst im Mittelalter von Richard Hamann 1923.')]
    stage2.mark_foot_notes(body + note, 14.0, 2400)      # 980 / 2400 = 41 %
    assert all(l['type'] == 'footnote_or_footer' for l in note)
