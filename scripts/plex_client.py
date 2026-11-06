# -*- coding: utf-8 -*-
"""Minimal Plex client for the audit skill: config, token, section listing.

Only read-only endpoints are used. The token never gets written anywhere; it
comes from --token, $PLEX_TOKEN, or the server's own Preferences.xml.
"""
import io
import json
import os
import re
import urllib.request

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_NAME = 'config.json'

DEFAULTS = {
    'plex_url': 'http://127.0.0.1:32400',
    'prefs_path': '',
    'sections': [],
    'timeout': 180,
}


class ConfigError(Exception):
    pass


def _host_of(url):
    match = re.match(r'https?://([^:/]+)', url or '')
    return match.group(1) if match else '127.0.0.1'


def load_config(path=None):
    """--config > $PLEX_AUDIT_CONFIG > <skill>/config.json > ./.plex-audit.json"""
    candidates = []
    if path:
        candidates.append(path)
    env = os.environ.get('PLEX_AUDIT_CONFIG')
    if env:
        candidates.append(env)
    candidates.append(os.path.join(SKILL_DIR, CONFIG_NAME))
    candidates.append(os.path.join(os.getcwd(), '.plex-audit.json'))

    cfg = dict(DEFAULTS)
    used = None
    for candidate in candidates:
        if candidate and os.path.isfile(candidate):
            with io.open(candidate, encoding='utf-8-sig') as handle:
                cfg.update(json.load(handle))
            used = candidate
            break
    cfg['_config_file'] = used

    # Preferences.xml lives on the same server; derive the SMB path when the
    # config leaves it empty so a fresh checkout works after only setting
    # plex_url.
    if not cfg.get('prefs_path'):
        cfg['prefs_path'] = ('//%s/Plex/Library/Application Support/'
                             'Plex Media Server/Preferences.xml'
                             % _host_of(cfg.get('plex_url')))
    return cfg


def read_token(prefs_path):
    try:
        with open(prefs_path, 'rb') as handle:
            raw = handle.read().decode('utf-8', 'replace')
    except (OSError, IOError) as exc:
        raise ConfigError('cannot read %s (%s)' % (prefs_path, exc))
    match = re.search(r'PlexOnlineToken="([^"]+)"', raw)
    if not match:
        raise ConfigError('no PlexOnlineToken in %s' % prefs_path)
    return match.group(1)


class Plex(object):
    def __init__(self, cfg, token=None):
        self.cfg = cfg
        self.url = (cfg.get('plex_url') or '').rstrip('/')
        self.timeout = cfg.get('timeout') or 180
        self.token = (token or os.environ.get('PLEX_TOKEN')
                      or read_token(cfg.get('prefs_path')))
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}))

    def fetch(self, path, timeout=None):
        request = urllib.request.Request(
            self.url + path, headers={'X-Plex-Token': self.token})
        return self._opener.open(
            request, timeout=timeout or self.timeout).read()

    def sections(self):
        import xml.etree.ElementTree as ET

        root = ET.fromstring(self.fetch('/library/sections'))
        out = []
        for node in root.findall('Directory'):
            out.append({
                'key': node.get('key') or '',
                'type': node.get('type') or '',
                'title': node.get('title') or '',
                'paths': [loc.get('path') for loc in node.findall('Location')],
            })
        return out

    def resolve_sections(self, wanted=None):
        """Accept keys or titles; fall back to the config list, else all."""
        keys = [str(k) for k in (wanted or self.cfg.get('sections') or [])]
        if not keys:
            return [s['key'] for s in self.sections() if s['type'] == 'movie']
        known = self.sections()
        resolved = []
        for want in keys:
            for section in known:
                if want == section['key'] or want == section['title']:
                    resolved.append(section['key'])
                    break
            else:
                resolved.append(want)
        return resolved

    def section_xml(self, section):
        return self.fetch('/library/sections/%s/all?includeMedia=1' % section)

    def identity(self):
        return self.fetch('/identity').decode('utf-8', 'replace')
