# -*- coding: utf-8 -*-
"""Plex 多版本条目审计 CLI.

    python plex_audit.py scan   --section 25
    python plex_audit.py report --section 25 --out report.md
    python plex_audit.py dups   --section 25
    python plex_audit.py export --section 25 --format csv --out items.csv

Read-only: nothing is renamed, moved, deleted or rescanned.
"""
import argparse
import glob
import io
import json
import os
import sys
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import audit_core as core
import plex_client as client

CLASS_HELP = ', '.join('%s=%s' % (c, core.CLASS_LABEL[c])
                       for c in core.CLASS_ORDER[:-1])


def dwidth(text):
    """Terminal width of ``text``: CJK glyphs occupy two columns."""
    return sum(2 if unicodedata.east_asian_width(ch) in ('W', 'F') else 1
               for ch in text)


def pad(text, width):
    return text + ' ' * max(0, width - dwidth(text))


def _utf8_stdout():
    """Japanese/Chinese titles on a cp936 console would raise UnicodeError."""
    stream = getattr(sys.stdout, 'reconfigure', None)
    if stream:
        try:
            sys.stdout.reconfigure(errors='replace')
        except (ValueError, OSError):
            pass


def load_items(args):
    """Return (items, section_titles). Live Plex, or --offline XML dump."""
    if args.offline:
        files = []
        if os.path.isdir(args.offline):
            files = sorted(glob.glob(os.path.join(args.offline, '*.xml')))
        elif os.path.isfile(args.offline):
            files = [args.offline]
        if not files:
            raise SystemExit('no XML found at %s' % args.offline)
        items = []
        for path in files:
            section = os.path.splitext(os.path.basename(path))[0]
            with open(path, 'rb') as handle:
                items.extend(core.parse_section(handle.read(), section))
        print('offline: %d file(s), %d items' % (len(files), len(items)))
        return items, {}

    cfg = client.load_config(args.config)
    plex = client.Plex(cfg, token=args.token)
    sections = plex.resolve_sections(args.section)
    if args.save_xml:
        if not os.path.isdir(args.save_xml):
            os.makedirs(args.save_xml)
    items, titles = [], {}
    for section in sections:
        data = plex.section_xml(section)
        if args.save_xml:
            target = os.path.join(args.save_xml, 'section_%s.xml' % section)
            with open(target, 'wb') as handle:
                handle.write(data)
            print('saved %s (%.1f MB)' % (target, len(data) / 1048576.0))
        parsed = core.parse_section(data, section)
        print('section %s: %d movie items' % (section, len(parsed)))
        items.extend(parsed)
        titles[section] = section
    return items, titles


def cmd_scan(pairs, dup_guids, args):
    stats = core.summarize(pairs)
    print()
    print('%s %s %6s %12s' % (pad('class', 9), pad('meaning', 30),
                              'items', 'size'))
    for code in core.CLASS_ORDER:
        slot = stats.get(code)
        if not slot:
            continue
        print('%s %s %6d %9.1f GB'
              % (pad(code, 9), pad(core.CLASS_LABEL[code], 30),
                 slot['count'], core.gb(slot['size'])))
    waste = sum(core.reclaimable(i) for i, c in pairs if c == 'DUP')
    print('\nreclaimable duplicate space: %.1f GB' % core.gb(waste))

    wanted = args.only or ['SEG', 'ODD', 'UNMERGED', 'DUP']
    for code in wanted:
        rows = [(i, c) for i, c in pairs if c == code]
        if not rows:
            continue
        print('\n-- %s (%d) --' % (code, len(rows)))
        for item, _ in rows:
            rows_m = core.media_rows(item)
            durations = '/'.join('%.0f' % (r['duration'] / 60000.0)
                                 for r in rows_m)
            flags = core.hint_flags(item)
            print('  %s %s [%s min]%s'
                  % (pad(item['key'], 7), pad(item['title'][:46], 48),
                     durations, (' !' + ','.join(flags)) if flags else ''))
        if code == 'UNMERGED':
            for guid, group in core.unmerged_groups(dup_guids):
                print('    %s  %s <-> %s'
                      % (guid, group[0]['key'], group[1]['key']))
    return 0


def cmd_report(pairs, dup_guids, args):
    meta = {
        'title': args.title or 'Plex 多版本条目检查报告',
        'host': args.host_note or '',
        'section': args.section_note or '',
        'date': args.date_note or '',
    }
    text = core.build_report(pairs, dup_guids, meta)
    out = args.out or 'plex-multiversion-report.md'
    if os.path.dirname(out) and not os.path.isdir(os.path.dirname(out)):
        os.makedirs(os.path.dirname(out))
    with io.open(out, 'w', encoding='utf-8') as handle:
        handle.write(text)
    print('report -> %s (%d lines)' % (out, text.count('\n') + 1))
    return 0


def cmd_dups(pairs, dup_guids, args):
    total = 0
    print('%s %s %10s' % (pad('key', 8), pad('title', 42), 'copy'))
    for item, code in pairs:
        if code != 'DUP':
            continue
        rows = core.media_rows(item)
        total += core.reclaimable(item)
        print('%s %s %7.2f GB'
              % (pad(item['key'], 8), pad(item['title'][:42], 42),
                 core.gb(min(r['size'] for r in rows))))
        for row in rows:
            print('         %.2f GB  %s' % (core.gb(row['size']), row['file']))
    print('\ntotal reclaimable: %.2f GB (delete one copy of each)' %
          core.gb(total))
    return 0


def cmd_export(pairs, dup_guids, args):
    rows = []
    for item, code in pairs:
        if code == 'SINGLE' and not args.include_single:
            continue
        for n, row in enumerate(core.media_rows(item), 1):
            rows.append({
                'key': item['key'],
                'class': code,
                'title': item['title'],
                'year': item['year'],
                'guid': item['guid'],
                'version': n,
                'resolution': '%dx%d' % (row['width'], row['height']),
                'videoCodec': row['videoCodec'],
                'audioCodec': row['audioCodec'],
                'durationMin': round(row['duration'] / 60000.0, 1),
                'sizeGB': round(core.gb(row['size']), 3),
                'badge': row['badge'],
                'partCount': row['partCount'],
                'files': ' | '.join(row['files']),
                'flags': ','.join(core.hint_flags(item)),
            })
    out = args.out or ('plex-multiversion.%s' % args.format)
    if args.format == 'json':
        with io.open(out, 'w', encoding='utf-8') as handle:
            json.dump(rows, handle, ensure_ascii=False, indent=1)
    else:
        import csv

        with io.open(out, 'w', encoding='utf-8-sig', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            for row in rows:
                writer.writerow(row)
    print('export -> %s (%d rows)' % (out, len(rows)))
    return 0


def main(argv=None):
    _utf8_stdout()
    parser = argparse.ArgumentParser(
        description='Audit a Plex movie section for multi-version anomalies.',
        epilog='classes: ' + CLASS_HELP)
    parser.add_argument('command',
                        choices=('scan', 'report', 'dups', 'export'))
    parser.add_argument('--section', '-s', action='append', default=[],
                        help='section key or title, repeatable')
    parser.add_argument('--config', help='path to config.json')
    parser.add_argument('--token', help='Plex token (else $PLEX_TOKEN / '
                                       'Preferences.xml)')
    parser.add_argument('--offline', help='scan a saved XML dump instead of '
                                         'the server')
    parser.add_argument('--save-xml', help='dump the raw section XML here')
    parser.add_argument('--out', '-o', help='output path')
    parser.add_argument('--format', '-f', default='csv',
                        choices=('csv', 'json'))
    parser.add_argument('--only', action='append', default=[],
                        help='scan: restrict the detail list to a class')
    parser.add_argument('--include-single', action='store_true',
                        help='export: also emit plain single-version items')
    parser.add_argument('--title', help='report: H1 title')
    parser.add_argument('--host-note', help='report: server line')
    parser.add_argument('--section-note', help='report: library line')
    parser.add_argument('--date-note', help='report: date line')
    args = parser.parse_args(argv)

    items, _ = load_items(args)
    if not items:
        raise SystemExit('no movie items found')
    pairs, dup_guids = core.classify_all(items)
    handler = {'scan': cmd_scan, 'report': cmd_report,
               'dups': cmd_dups, 'export': cmd_export}[args.command]
    return handler(pairs, dup_guids, args)


if __name__ == '__main__':
    sys.exit(main())
