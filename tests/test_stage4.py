from stage4 import join_pair, dehyphenate, continues, link_pages, build_units


def test_join_rules():
    assert join_pair('Spitz-', 'bogen gewölbt') == ('Spitzbogen gewölbt', 'removed_hyphen')
    assert join_pair('Gra-', 'Nit') == ('Gra-Nit', 'kept_hyphen_uppercase')
    assert join_pair('Werk-', 'und Form-') == ('Werk- und Form-', 'suspended_compound')


def test_dehyphenate_inside_paragraph():
    text, joins = dehyphenate(['Haupteingängen der Kir-', 'chen, den Portalen.'])
    assert text == 'Haupteingängen der Kirchen, den Portalen.' and len(joins) == 1


def test_continues_rules():
    assert continues({'text': 'Werk- und Form-'}, {'text': 'steinen'})
    assert continues({'text': 'Niederschlesien'}, {'text': 'und die'})
    assert not continues({'text': 'Ende des Satzes.'}, {'text': 'und weiter'})
    assert not continues({'text': 'ohne Punkt'}, {'text': 'Neuer Absatz'})


def _page(n, *texts):
    regs = [{'type': 'paragraph', 'translation_status': 'translate', 'object_id': 'p%d-r%d' % (n, i),
             'bbox': [0, 100 * i, 10, 100 * i + 50], 'text': t, 'lines': [{'text': t}]}
            for i, t in enumerate(texts)]
    return {'page': n, 'input_index': 1, 'regions': regs}


def test_link_and_units_across_pages():
    pages = link_pages([_page(1, 'Erster Absatz.', 'Der Spitz-'), _page(2, 'bogen ist hoch.', 'Neu.')])
    assert pages[1]['regions'][0]['continues_from'] == 'p1-r1'
    units = build_units(pages)
    assert [u['text'] for u in units] == ['Erster Absatz.', 'Der Spitzbogen ist hoch.', 'Neu.']
    assert units[1]['dehyphenated'] and units[1]['joins'][-1]['across_page']


# ---------------------------------------------------------------- OCR region errors (first real book, page 62)
def _reg(oid, lines, x1=200, x2=800, typ='paragraph', gh=14):
    """A region from (text, x1, x2) lines; tops 23 px apart."""
    ls = []
    for i, ln in enumerate(lines):
        t, a, b = ln if isinstance(ln, tuple) else (ln, x1, x2)
        ls.append({'text': t, 'bbox': [a, 100 + 23 * i, b, 114 + 23 * i], 'glyph_height': gh})
    return {'type': typ, 'translation_status': 'translate', 'object_id': oid, 'text': '\n'.join(l['text'] for l in ls),
            'bbox': [min(l['bbox'][0] for l in ls), 100, max(l['bbox'][2] for l in ls), 114 + 23 * (len(ls) - 1)],
            'lines': ls}


def test_noise_regions_are_excluded_and_marks_left_out_of_the_text():
    page = {'page': 1, 'input_index': 1, 'regions': [
        _reg('a', ['Bei der im Jahre 1900']),
        _reg('n', [('|', 896, 899)]),                                     # a page-edge strip
        _reg('num', [('62', 228, 247)]),                                  # a page number
        _reg('b', [(')', 790, 797), 'erfolgten Restauration der Kirche sind die Stücke erneuert worden.'])]}
    for r in page['regions'][2:]:
        for ln in r['lines']:
            ln['bbox'][1] += 100; ln['bbox'][3] += 100
        r['bbox'][1] += 100; r['bbox'][3] += 100
    link_pages([page])
    st = {r['object_id']: r['translation_status'] for r in page['regions']}
    assert st['n'] == 'exclude' and st['num'] == 'exclude' and st['a'] == st['b'] == 'translate'
    units = build_units([page])
    assert [u['text'] for u in units] == ['Bei der im Jahre 1900 erfolgten Restauration der Kirche sind die Stücke erneuert worden.']


def test_a_paragraph_split_on_a_page_is_joined_by_its_full_last_line():
    a = _reg('a', ['wechseln drei durch Schaftringe gegürtete Säulen mit tiefen'])  # full last line, no sentence end
    b = _reg('b', [('Hohlkehlen ab, die Gewändepfosten fehlen.', 200, 600)])
    b['bbox'][1] += 30
    c = _reg('c', [('Ende des Absatzes ohne Punkt', 200, 500)])              # short last line: a real paragraph end
    c['bbox'][1] += 60
    d = _reg('d', ['Neuer Absatz beginnt hier.'])
    d['bbox'][1] += 90
    page = {'page': 1, 'input_index': 1, 'regions': [a, b, c, d]}
    link_pages([page])
    assert a.get('continues_to') == 'b' and not c.get('continues_to')
    units = build_units([page])
    assert units[0]['text'].startswith('wechseln') and units[0]['text'].endswith('fehlen.') and len(units) == 3


def test_across_pages_a_capital_letter_still_stops_the_link():
    p1 = {'page': 1, 'input_index': 1, 'regions': [_reg('a', ['an den inneren Gewändesäulen'])]}
    p2 = {'page': 2, 'input_index': 1, 'regions': [_reg('b', ['Eine eigenartige Portalanlage besitzt die Kirche.'])]}
    link_pages([p1, p2])
    assert not p1['regions'][0].get('continues_to')                      # pages may be missing between them


def test_a_body_size_caption_is_a_body_paragraph_for_linking():
    p1 = {'page': 1, 'input_index': 1, 'regions': [_reg('a', ['Das Raudener Portal hat dagegen mit seinen'])]}
    big = _reg('cap', ['gedrungenen Verhältnissen trotz seiner', 'spitzbogigen Überwölbung durchaus', 'romanischen Charakter. An die Frühgotik',
                       'gemahnt lediglich die Basis.'], typ='caption')
    small = _reg('fig', [('46 KIRCHE IN KALKAU', 200, 400)], typ='caption', gh=8)
    p2 = {'page': 2, 'input_index': 1, 'regions': [small, big]}
    link_pages([p1, p2])
    assert p1['regions'][0].get('continues_to') == 'cap'
