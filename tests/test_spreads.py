"""Spreads to redo: names, numbers and ranges; compact specs; how far a scan got."""
import pytest

import spreads


STEMS = ['0008', '0009', '0010', '0011', '0012', '0014']


def test_names_numbers_and_ranges():
    assert spreads.select('0010', STEMS) == ['0010']
    assert spreads.select('10, 12-14', STEMS) == ['0010', '0012', '0014']
    assert spreads.select('0012-0014,0008.tif', STEMS) == ['0008', '0012', '0014']      # in scan order
    assert spreads.select('', STEMS) == []
    assert spreads.select('3', ['scan_0003', 'scan_0004']) == ['scan_0003']           # the number at the end


def test_unknown_items_are_all_named():
    with pytest.raises(spreads.SpreadError) as e:
        spreads.select('0010, 0013, x, 14-12', STEMS)
    assert '0013' in str(e.value) and 'x' in str(e.value) and '14-12' in str(e.value) and '0008-' not in str(e.value)


def test_compact_round_trips():
    spec = spreads.compact(['0008', '0009', '0010', '0012', '0014'], STEMS)
    assert spec == '0008-0010, 0012, 0014'          # 0014 follows 0012 in the folder, but 0013 is missing - shown as is
    assert spreads.select(spec, STEMS) == ['0008', '0009', '0010', '0012', '0014']


def test_spread_state(tmp_path):
    (tmp_path / 'stages' / '0009' / 's3').mkdir(parents=True)
    (tmp_path / 'stages' / '0009' / 's3' / 'page_structure_stage3.json').write_text('{}')
    (tmp_path / 'stages' / '0010' / 's1').mkdir(parents=True)
    assert [spreads.spread_state(tmp_path, s) for s in ('0009', '0010', '0011')] == ['done', 'incomplete', 'new']


def test_name_ranges():
    stems = ['scan_0001', 'scan_0002', 'scan_0003', 'a-b']
    assert spreads.select('scan_0001-scan_0002', stems) == ['scan_0001', 'scan_0002']
    assert spreads.select('a-b', stems) == ['a-b']                             # a hyphen in a name
    assert spreads.select(spreads.compact(['scan_0002', 'scan_0003'], stems), stems) == ['scan_0002', 'scan_0003']
