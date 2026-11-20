"""Choosing spreads (scans) to redo, shared by ``booktrans.py --redo`` and the run window.

A spec is a comma-separated list of scan names or numbers: ``0010``, ``10``, ``0012-0014``, ``12-14``,
``0010.tif``, ``scan_0012-scan_0014``.  A name matches the scan's file stem; a number matches the number at the end of the stem
(``scan_0010`` is 10).  ``spread_state`` says how far a scan got in a book folder.
"""
import re
from pathlib import Path


class SpreadError(ValueError):
    pass


def _number(stem):
    m = re.search(r'(\d+)$', stem)
    return int(m.group(1)) if m else None


def select(spec, tiffs):
    """Stems of ``tiffs`` (paths) chosen by ``spec``, in scan order.  Raises SpreadError naming every item that
    matches no scan, so nothing is deleted on a typo."""
    stems = [Path(t).stem for t in tiffs]
    chosen, bad = set(), []
    for item in (s.strip() for s in (spec or '').split(',')):
        if not item:
            continue
        name = re.sub(r'\.tiff?$', '', item, flags=re.I)
        if name in stems:
            chosen.add(name)
            continue
        span = _name_range(name, stems)
        if span:
            chosen.update(span)
            continue
        m = re.fullmatch(r'(\d+)\s*(?:-\s*(\d+))?', name)
        if not m:
            bad.append(item)
            continue
        lo = int(m.group(1)); hi = int(m.group(2) or lo)
        if hi < lo:
            bad.append(item)
            continue
        hit = [s for s in stems if _number(s) is not None and lo <= _number(s) <= hi]
        if not hit:
            bad.append(item)
        chosen.update(hit)
    if bad:
        raise SpreadError('no scan matches %s (scans: %s)' % (', '.join(bad), describe(stems)))
    return [s for s in stems if s in chosen]


def _name_range(item, stems):
    """``scan_0010-scan_0012``: the scans from one name to the other, in folder order (names may hold hyphens)."""
    for k in [i for i, c in enumerate(item) if c == '-']:
        a, b = item[:k].strip(), item[k + 1:].strip()
        a, b = (re.sub(r'\.tiff?$', '', x, flags=re.I) for x in (a, b))
        if a in stems and b in stems and stems.index(a) <= stems.index(b):
            return stems[stems.index(a):stems.index(b) + 1]
    return None


def describe(stems):
    """Short list for messages: ``0001-0010`` when the numbers run without gaps, else the first few names."""
    nums = [_number(s) for s in stems]
    if stems and None not in nums and nums == list(range(nums[0], nums[0] + len(nums))):
        return '%s-%s' % (stems[0], stems[-1]) if len(stems) > 1 else stems[0]
    return ', '.join(stems[:6]) + (', ...' if len(stems) > 6 else '')


def compact(stems, all_stems):
    """``stems`` as a spec in scan order, consecutive numbers as ranges: ['0010','0011','0012','0015'] ->
    '0010-0012, 0015' (a range never spans a missing number).  Stems without a number are listed as they are."""
    chosen = [s for s in all_stems if s in set(stems)]
    parts, i = [], 0
    while i < len(chosen):
        j = i
        while (j + 1 < len(chosen) and _number(chosen[j]) is not None and _number(chosen[j + 1]) is not None
               and _number(chosen[j + 1]) == _number(chosen[j]) + 1):
            j += 1
        parts.append(chosen[i] if i == j else '%s-%s' % (chosen[i], chosen[j]))
        i = j + 1
    return ', '.join(parts)


def spread_state(book_dir, stem):
    """'done' (stages 1-3 finished), 'incomplete' (started, not finished) or 'new'."""
    st = Path(book_dir) / 'stages' / stem
    if (st / 's3' / 'page_structure_stage3.json').exists():
        return 'done'
    return 'incomplete' if st.exists() else 'new'
