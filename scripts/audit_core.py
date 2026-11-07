# -*- coding: utf-8 -*-
"""Pure functions: parse a Plex section listing, classify version anomalies.

No network, no filesystem. Everything here is exercised by selftest.py, so a
rule change is visible before it ever touches a real library.

Vocabulary
----------
Media   : one Plex "version" of a movie (resolution / codec / container).
Part    : one physical file inside a Media. Several Parts = one movie split
          across files, which Plex plays back seamlessly.
SEG     : several Media whose files are actually consecutive segments of one
          movie. Plex treats each as a separate version, so the user picks one
          and only ever sees that slice.
"""
import os
import re

# Same badge rule the MetaTube plug-in applies to the file stem. Tool marks
# may follow the badge ("_wm", ".LTX"), so the badge need not be the last
# token; a dot is a valid separator. WM alone is not a badge.
_BADGE_TOKEN = (r'(UNCENSORED|UC|C|CH|U|LTX|RESTORED|'
                r'BASICVSR\+\+|BASICVSR)')
_TOOL_TOKEN = (r'(?:WM|UNCENSORED|UC|C|CH|U|LTX|RESTORED|'
               r'BASICVSR\+\+|BASICVSR)')
# Capture the LAST badge token in the chain (see utils.TRAILING_BADGE_TAG).
TAG_RE = re.compile(
    r'[-_.\s]+(?:' + _TOOL_TOKEN + r'[-_.\s]+)*' + _BADGE_TOKEN +
    r'(?:[-_.\s]+' + _TOOL_TOKEN + r')*$',
    re.IGNORECASE)

# Semantic version classes, matching utils.get_badge_type(). All of the
# uncensored spellings map to u.png (or uc.png with a subtitle) and to the
# same "无码破解" genre, so they are ONE version, not different versions.
UNCENSORED_TAGS = frozenset(('U', 'UNCENSORED', 'LTX', 'RESTORED',
                             'BASICVSR', 'BASICVSR++'))
SUBTITLE_TAGS = frozenset(('C', 'CH'))

# Basename patterns that suggest "this file is a piece of a bigger movie".
# A/B/D only - a trailing C or U is a badge (-C 中文字幕 / -U 无码), not a
# segment marker, and must not raise the hint.
SPLIT_HINT_RES = (
    re.compile(r'[-_ .](part|pt|cd|disc|disk)[ _.-]?[0-9]{1,2}$', re.I),
    re.compile(r'[-_ .][ABD]$', re.I),
    re.compile(r'_[0-9]{1,2}$'),
)

# A "version" that is <90% of the longest one is very unlikely to be the same
# complete movie; combined with >=3 media it is the strongest SEG signal.
SEG_RATIO = 0.90
# Same file copied twice: identical basename, sizes within 1%.
DUP_SIZE_TOL = 0.01

CLASS_ORDER = ('SEG', 'ODD', 'UNMERGED', 'DUP', 'BADGE', 'RES', 'PARTS',
               'SINGLE')

CLASS_LABEL = {
    'SEG': '分段文件被当成多版本',
    'ODD': '时长异常（疑似残缺/剪辑版）',
    'UNMERGED': '同片多条目未合并',
    'DUP': '同文件重复（两份拷贝）',
    'BADGE': '版别变体（C/UC/无码破解 U 的角标不同）',
    'RES': '分辨率/编码变体',
    'PARTS': '单版本多 Part（正常分段）',
    'SINGLE': '普通单版本',
}


def gb(num):
    return num / 1073741824.0


def badge_of(path):
    """Literal suffix token, kept for the report's 版别 column."""
    stem = os.path.splitext(os.path.basename(path or ''))[0]
    match = TAG_RE.search(stem)
    return match.group(1).upper() if match else ''


def badge_class(tag):
    """Collapse spelling variants onto the version class Plex actually shows.

    -U / -LTX / _LTX / __restored / -BasicVSR++ all mean 无码破解 and share
    u.png. Treating them as distinct made two copies of the same uncensored
    release look like a "混装版别" anomaly. UC (uc.png) and C/CH (zimu.png)
    stay separate because those really do render different badges.
    """
    tag = (tag or '').upper()
    if tag in UNCENSORED_TAGS:
        return 'U'
    if tag in SUBTITLE_TAGS:
        return 'C'
    return tag


def split_hint(path):
    """True when the basename looks like one segment of a multi-part release."""
    stem = os.path.splitext(os.path.basename(path or ''))[0]
    for pattern in SPLIT_HINT_RES:
        if pattern.search(stem):
            return True
    return False


# ----------------------------------------------------------------- parsing
def _int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def parse_section(xml_bytes, section=''):
    """XML from /library/sections/<n>/all?includeMedia=1 -> list of items."""
    import xml.etree.ElementTree as ET

    root = ET.fromstring(xml_bytes)
    items = []
    for node in root:
        if node.get('type') not in (None, 'movie'):
            continue
        medias = []
        for media in node.findall('Media'):
            parts = []
            for part in media.findall('Part'):
                parts.append({
                    'file': part.get('file') or '',
                    'size': _int(part.get('size')),
                    'duration': _int(part.get('duration')),
                })
            medias.append({
                'id': media.get('id') or '',
                'duration': _int(media.get('duration')),
                'bitrate': _int(media.get('bitrate')),
                'width': _int(media.get('width')),
                'height': _int(media.get('height')),
                'videoCodec': media.get('videoCodec') or '',
                'audioCodec': media.get('audioCodec') or '',
                'container': media.get('container') or '',
                'parts': parts,
            })
        if not medias:
            continue
        items.append({
            'key': node.get('ratingKey') or '',
            'title': node.get('title') or '',
            'year': node.get('year') or '',
            'guid': node.get('guid') or '',
            'section': str(section),
            'addedAt': _int(node.get('addedAt')),
            'updatedAt': _int(node.get('updatedAt')),
            'medias': medias,
        })
    return items


# ------------------------------------------------------------ item metrics
def media_rows(item):
    """Flatten an item into comparable per-version rows."""
    rows = []
    for media in item['medias']:
        parts = media['parts']
        first = parts[0]['file'] if parts else ''
        rows.append({
            'size': sum(p['size'] for p in parts),
            'duration': media['duration'],
            'width': media['width'],
            'height': media['height'],
            'videoCodec': media['videoCodec'],
            'audioCodec': media['audioCodec'],
            'container': media['container'],
            'partCount': len(parts),
            'files': [p['file'] for p in parts],
            'file': first,
            'basename': os.path.basename(first).lower(),
            'badge': badge_of(first),
        })
    return rows


def item_size(item):
    return sum(p['size'] for m in item['medias'] for p in m['parts'])


def item_duration(item):
    """Longest Media - the closest thing to "real runtime" for a split item."""
    return max([m['duration'] for m in item['medias']] or [0])


def classify(item):
    """Return one class code. Order of the tests matters; keep it stable."""
    rows = media_rows(item)
    if len(rows) <= 1:
        return 'PARTS' if (rows and rows[0]['partCount'] > 1) else 'SINGLE'

    durations = [r['duration'] for r in rows if r['duration']]
    if len(rows) >= 3:
        return 'SEG'
    if len(durations) == len(rows) and durations:
        if min(durations) / float(max(durations)) < SEG_RATIO:
            # A 20%+ shorter "version" is either a segment of one movie or a
            # truncated/edited file. The basename tells them apart: HUNTA-972-A
            # is a segment, SONE-357.mp4 next to SONE-357-C.mp4 is a bad rip.
            hinted = any(split_hint(r['basename']) for r in rows)
            return 'SEG' if hinted else 'ODD'

    same_name = len(set(r['basename'] for r in rows)) == 1
    sizes = [r['size'] for r in rows]
    if same_name and max(sizes) > 0:
        spread = (max(sizes) - min(sizes)) / float(max(sizes))
        if spread < DUP_SIZE_TOL:
            return 'DUP'

    if len(set(badge_class(r['badge']) for r in rows)) > 1:
        return 'BADGE'
    return 'RES'


def reclaimable(item):
    """Bytes that could be freed by keeping one copy of a duplicated file."""
    rows = media_rows(item)
    if len(rows) < 2:
        return 0
    return sum(r['size'] for r in rows) - max(r['size'] for r in rows)


def hint_flags(item):
    """Extra warning flags that do not change the class."""
    rows = media_rows(item)
    flags = []
    if len(rows) > 1 and any(split_hint(r['basename']) for r in rows):
        flags.append('分段命名')
    if len(set(badge_class(r['badge']) for r in rows)) > 1:
        flags.append('混装版别')
    if len(rows) > 1:
        longest = max(r['duration'] for r in rows) or 0
        for r in rows:
            if longest and r['duration'] and r['duration'] / float(longest) < 0.8:
                flags.append('时长偏短')
                break
    return flags


# ------------------------------------------------------------ whole library
def classify_all(items):
    """[(item, code)] - UNMERGED replaces the per-item code when GUID repeats."""
    pairs = [(item, classify(item)) for item in items]

    groups = {}
    for item, _ in pairs:
        guid = (item.get('guid') or '').split('?')[0]
        if guid:
            groups.setdefault(guid, []).append(item)
    dup_guids = dict((g, v) for g, v in groups.items() if len(v) > 1)

    out = []
    for item, code in pairs:
        guid = (item.get('guid') or '').split('?')[0]
        out.append((item, 'UNMERGED' if guid in dup_guids else code))
    return out, dup_guids


def summarize(pairs):
    """Class -> {count, size} plus totals."""
    stats = {}
    for item, code in pairs:
        slot = stats.setdefault(code, {'count': 0, 'size': 0})
        slot['count'] += 1
        slot['size'] += item_size(item)
    return stats


def unmerged_groups(dup_guids):
    return sorted(dup_guids.items(), key=lambda kv: kv[0])


# ------------------------------------------------------------------ report
def _cell(text):
    return (text or '').replace('|', '\\|')


def build_report(pairs, dup_guids, meta):
    """Markdown report. ``meta`` carries title/section/host/date strings."""
    stats = summarize(pairs)
    lines = []
    add = lines.append

    add('# %s' % meta.get('title', 'Plex 多版本条目检查报告'))
    add('')
    for label in ('host', 'section', 'total', 'date'):
        if meta.get(label):
            add('- %s：%s' % (label, meta[label]))
    add('')

    add('## 一、概览')
    add('')
    add('| 类别 | 条目数 | 涉及容量 |')
    add('|---|---|---|')
    order = ('SEG', 'ODD', 'DUP', 'BADGE', 'RES', 'PARTS')
    for code in order:
        slot = stats.get(code)
        if not slot:
            continue
        add('| %s %s | %d | %.1f GB |'
            % (code, CLASS_LABEL[code], slot['count'], gb(slot['size'])))
    if dup_guids:
        add('| UNMERGED %s | %d 对 | — |'
            % (CLASS_LABEL['UNMERGED'], len(dup_guids)))
    add('')

    waste = sum(reclaimable(item) for item, code in pairs if code == 'DUP')
    add('可回收重复空间：**%.2f GB**。' % gb(waste))
    add('')

    # ---------------------------------------------------------- per class
    for code in ('SEG', 'ODD', 'DUP', 'BADGE', 'RES', 'PARTS'):
        rows = [(i, c) for i, c in pairs if c == code]
        if not rows:
            continue
        add('## %s · %s（%d）' % (code, CLASS_LABEL[code], len(rows)))
        add('')
        for item, _ in rows:
            flags = hint_flags(item)
            suffix = ('　⚠ ' + '/'.join(flags)) if flags else ''
            add('### %s（%s） · key=%s%s'
                % (_cell(item['title']), item['year'], item['key'], suffix))
            add('')
            add('| # | 分辨率 | 编码 | 时长 | 容量 | 版别 | Part | 文件 |')
            add('|---|---|---|---|---|---|---|---|')
            for n, row in enumerate(media_rows(item), 1):
                add('| %d | %dx%d | %s | %.1f min | %.2f GB | %s | %d | `%s` |'
                    % (n, row['width'], row['height'], row['videoCodec'],
                       row['duration'] / 60000.0, gb(row['size']),
                       row['badge'] or '-', row['partCount'],
                       _cell(row['file'])))
                for extra in row['files'][1:]:
                    add('| | | | | | | | 　└ `%s` |' % _cell(extra))
            add('')

    if dup_guids:
        add('## UNMERGED · %s（%d 对）' % (CLASS_LABEL['UNMERGED'],
                                          len(dup_guids)))
        add('')
        add('| GUID | key A | key B | 文件 A | 文件 B |')
        add('|---|---|---|---|---|')
        for guid, group in unmerged_groups(dup_guids):
            first = group[0]
            second = group[1]
            fa = media_rows(first)[0]['file'] if media_rows(first) else ''
            fb = media_rows(second)[0]['file'] if media_rows(second) else ''
            add('| %s | %s | %s | `%s` | `%s` |'
                % (_cell(guid), first['key'], second['key'],
                   _cell(fa), _cell(fb)))
        add('')

    add('## 处理建议')
    add('')
    add('1. **SEG**：把同一部片的分段文件放进独立目录并改成 `-part1/-part2`'
        '（或 `-CD1/-CD2`），让 Plex 识别为同一个 Media 的多个 Part；'
        '特典/花絮不要和主片放同一目录。')
    add('2. **UNMERGED**：把文件收进同一目录，按 `片名 - 1080p.mp4` / '
        '`片名 - 4K.mp4` 命名，Plex 才会合并成一个多版本条目。')
    add('3. **ODD**：两个版本时长差 20% 以上又不像分段，通常是残缺/剪辑版。'
        '先确认短的那个是否能完整播放，再决定删掉还是重新下载。')
    add('4. **DUP**：同名同体积的两份文件删掉任一份即可，不影响播放。')
    add('5. **BADGE / RES**：正常多版本，不要动。注意一个 movie 只有一张海报和'
        '一个角标，同条目内混装不同角标类别（`-C` 中文字幕 / `-UC` 中文无码 / '
        '`-U` 无码破解）时角标必然只能对应其中一个版本。`-U`、`-LTX`、`_LTX`、'
        '`__restored`、`-BasicVSR++` 同属「无码破解」、共用 `u.png`，不算混装版别。')
    add('6. **PARTS**：正常。只有画质明显偏低（如 720p 以下）才考虑换源。')
    add('')
    return '\n'.join(lines)
