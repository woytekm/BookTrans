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
