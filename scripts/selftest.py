# -*- coding: utf-8 -*-
"""Offline regression for the audit rules. No network, no NAS needed.

    python scripts/selftest.py        # exits 1 on the first broken expectation
"""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import audit_core as core

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       'fixtures', 'section_sample.xml')

CASES = []


def case(name):
    def wrap(func):
        CASES.append((name, func))
        return func
    return wrap


def fails(cond, message):
    if not cond:
        raise AssertionError(message)


@case('parse: 10 items, fields intact')
def _parse():
    with open(FIXTURE, 'rb') as handle:
        items = core.parse_section(handle.read(), '25')
    fails(len(items) == 10, 'expected 10 items, got %d' % len(items))
    first = items[0]
    fails(first['key'] == '1001', 'ratingKey lost')
    fails(first['section'] == '25', 'section lost')
    fails(len(first['medias']) == 3, 'media count lost')
    fails(first['medias'][0]['parts'][0]['size'] == 4000000000, 'part size')
    return '%d items' % len(items)


@case('classify: one class per anomaly shape')
def _classify():
    with open(FIXTURE, 'rb') as handle:
        items = core.parse_section(handle.read(), '25')
    pairs, dup_guids = core.classify_all(items)
    got = dict((item['key'], code) for item, code in pairs)
    want = {
        '1001': 'SEG',      # 3 chunks
        '1002': 'SEG',      # A/B, durations 135/103 min
        '1003': 'DUP',      # same name, same size
        '1004': 'BADGE',    # C vs UC
        '1005': 'RES',      # 4K vs 1080p
        '1006': 'PARTS',    # one media, two parts
        '1007': 'SINGLE',
        '1008': 'UNMERGED',  # same guid as 1009
        '1009': 'UNMERGED',
        '1010': 'ODD',      # 118 vs 96 min, no A/B naming -> truncated rip
    }
    for key, code in want.items():
        fails(got.get(key) == code,
              'key %s -> %s, want %s' % (key, got.get(key), code))
    fails(len(dup_guids) == 1, 'expected 1 duplicate guid group')
    fails('unm00001' in list(dup_guids)[0], 'wrong guid grouped')
    return '9/9'


@case('summary: counts and sizes')
def _summary():
    with open(FIXTURE, 'rb') as handle:
        items = core.parse_section(handle.read(), '25')
    pairs, _ = core.classify_all(items)
    stats = core.summarize(pairs)
    fails(stats['SEG']['count'] == 2, 'SEG count')
    fails(stats['DUP']['count'] == 1, 'DUP count')
    fails(stats['SINGLE']['count'] == 1, 'SINGLE count')
    fails(stats['ODD']['count'] == 1, 'ODD count')
    fails(stats['PARTS']['count'] == 1, 'PARTS count')
    fails(stats['SEG']['size'] > 0, 'SEG size not accumulated')
    return 'ok'


@case('reclaimable: keeps the bigger copy')
def _reclaim():
    with open(FIXTURE, 'rb') as handle:
        items = core.parse_section(handle.read(), '25')
    pairs, _ = core.classify_all(items)
    dup = [i for i, c in pairs if c == 'DUP'][0]
    saved = core.reclaimable(dup)
    fails(abs(saved - 5400000000) < 1000000,
          'reclaimable %d, want ~5400000000' % saved)
    return '%.2f GB' % core.gb(saved)


@case('hints: A/B and mixed badge are flagged, -C is not a segment')
def _hints():
    with open(FIXTURE, 'rb') as handle:
        items = core.parse_section(handle.read(), '25')
    by_key = dict((i['key'], i) for i in items)
    fails('分段命名' in core.hint_flags(by_key['1002']), 'A/B not flagged')
    fails(core.classify(by_key['1010']) == 'ODD', 'short version not ODD')
    fails('混装版别' in core.hint_flags(by_key['1004']), 'badge mix not flagged')
    fails(core.split_hint('/volume1/x/SSIS-465-C.mp4') is False,
          '-C must not look like a segment')
    fails(core.split_hint('/volume1/x/SSIS-465-U.mp4') is False,
          '-U must not look like a segment')
    fails(core.split_hint('/volume1/x/HUNTA-972-A.mp4') is True,
          '-A must look like a segment')
    fails(core.split_hint('/volume1/x/FC2-PPV-1_2.mp4') is True,
          '_2 must look like a segment')
    fails(core.split_hint('/volume1/x/ABC-123.part2.mp4') is True,
          '.part2 must look like a segment')
    fails(core.split_hint('/volume1/x/SSIS-465.mp4') is False,
          'plain code must not look like a segment')
    return 'ok'


@case('badge_of: only real suffixes')
def _badge():
    fails(core.badge_of('/a/ABF-032-UC.mp4') == 'UC', 'UC')
    fails(core.badge_of('/a/ABF-032.mp4') == '', 'plain')
    fails(core.badge_of('/a/SSIS983C.mp4') == '', 'glued C is not a badge')
    fails(core.badge_of('/a/x-uncensored.mkv') == 'UNCENSORED', 'uncensored')
    fails(core.badge_of('/a/ABF-032_LTX.mp4') == 'LTX', 'underscore LTX')
    fails(core.badge_of('/a/ABF-032__restored.mp4') == 'RESTORED',
          'doubled separator restored')
    fails(core.badge_of('/a/ABF-032-restored.mp4') == 'RESTORED', 'restored')
    fails(core.badge_of('/a/ABF-032_LTX__restored.mp4') == 'RESTORED',
          'LTX then restored')
    fails(core.badge_of('/a/JUR-110_restored_wm.mp4') == 'RESTORED',
          'watermark mark after restored')
    fails(core.badge_of('/a/SONE-521_restored.LTX_wm.mp4') == 'LTX',
          'dot separator + watermark mark')
    fails(core.badge_of('/a/ABP-321_wm.mp4') == '',
          'wm alone is not a badge')
    return 'ok'


def _item_with(*files):
    """Two same-length versions of one item, used by the version-class tests."""
    medias = []
    for path in files:
        medias.append({
            'id': '', 'duration': 7200000, 'bitrate': 0,
            'width': 1920, 'height': 1080, 'videoCodec': 'h264',
            'audioCodec': '', 'container': '',
            'parts': [{'file': path, 'size': 4000000000,
                       'duration': 7200000}],
        })
    return {'key': 'x', 'section': '25', 'title': 'x', 'year': '',
            'guid': '', 'medias': medias}


@case('badge_class: all uncensored spellings collapse to U')
def _badge_class():
    for tag in ('U', 'UNCENSORED', 'LTX', 'RESTORED', 'BASICVSR',
                'BASICVSR++'):
        fails(core.badge_class(tag) == 'U',
              '%s must collapse to U' % tag)
    fails(core.badge_class('C') == 'C', 'C')
    fails(core.badge_class('CH') == 'C', 'CH')
    fails(core.badge_class('UC') == 'UC', 'UC must stay a separate class')
    fails(core.badge_class('') == '', 'no badge')
    return 'ok'


@case('classify: U vs LTX vs restored is NOT a mixed badge')
def _same_version():
    same_class = (
        ('/v/ABP-321-U.mp4', '/v/ABP-321-LTX.mp4'),
        ('/v/ABP-321_U.mp4', '/v/ABP-321__restored.mp4'),
        ('/v/ABP-321-BASICVSR++.mp4', '/v/ABP-321-UNCENSORED.mp4'),
        ('/v/ABP-321-LTX__restored.mp4', '/v/ABP-321-U.mp4'),
        ('/v/ABP-321_restored_wm.mp4', '/v/ABP-321-U.mp4'),
    )
    for left, right in same_class:
        item = _item_with(left, right)
        fails(core.classify(item) != 'BADGE',
              '%s + %s wrongly classified BADGE' % (left, right))
        fails('混装版别' not in core.hint_flags(item),
              '%s + %s wrongly flagged 混装版别' % (left, right))
    different_class = (
        ('/v/ABP-321-C.mp4', '/v/ABP-321-U.mp4'),
        ('/v/ABP-321-UC.mp4', '/v/ABP-321-LTX.mp4'),
        ('/v/ABP-321.mp4', '/v/ABP-321__restored.mp4'),
    )
    for left, right in different_class:
        item = _item_with(left, right)
        fails(core.classify(item) == 'BADGE',
              '%s + %s should still be BADGE' % (left, right))
        fails('混装版别' in core.hint_flags(item),
              '%s + %s missing 混装版别' % (left, right))
    return 'ok'


@case('report: renders every class that occurs')
def _report():
    with open(FIXTURE, 'rb') as handle:
        items = core.parse_section(handle.read(), '25')
    pairs, dup_guids = core.classify_all(items)
    text = core.build_report(pairs, dup_guids,
                             {'title': 't', 'host': 'h', 'section': 's',
                              'date': 'd'})
    for needle in ('## SEG', '## ODD', '## DUP', '## BADGE', '## RES',
                   '## PARTS',
                   '## UNMERGED', '可回收重复空间', '处理建议'):
        fails(needle in text, 'report missing %r' % needle)
    fails(text.count('### ') >= 7, 'too few item blocks (%d)'
          % text.count('### '))
    fails('| `' in text, 'file paths not in table')
    buf = io.StringIO()
    buf.write(text)
    return '%d lines' % text.count('\n')


@case('cli: offline scan runs end to end')
def _cli():
    import plex_audit

    out = os.path.join(os.path.dirname(FIXTURE), '..', '_selftest_out')
    out = os.path.abspath(out)
    rc = plex_audit.main(['scan', '--offline', FIXTURE])
    fails(rc == 0, 'scan returned %s' % rc)
    rc = plex_audit.main(['report', '--offline', FIXTURE, '--out',
                          out + '.md', '--title', 't', '--section', '25'])
    fails(rc == 0, 'report returned %s' % rc)
    fails(os.path.isfile(out + '.md'), 'report file missing')
    fails(os.path.getsize(out + '.md') > 500, 'report file too small')
    os.remove(out + '.md')
    return 'ok'


def main():
    width = max(len(name) for name, _ in CASES)
    failed = 0
    for name, func in CASES:
        try:
            note = func()
        except AssertionError as exc:
            failed += 1
            print('FAIL  %-*s  %s' % (width, name, exc))
        else:
            print('ok    %-*s  %s' % (width, name, note or ''))
    print('\n%d/%d passed' % (len(CASES) - failed, len(CASES)))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
