"""Zen Browser (a Firefox fork), installed as a Flatpak, while it is running."""
import os
import sqlite3
import time

import pytest

from yt_dlp_gui.browsers import detect_browser_profiles
from yt_dlp_gui.cookies import probe_profile

ZEN_FLATPAK = os.path.join('.var', 'app', 'app.zen_browser.zen', '.zen')


def make_running_browser_profile(profile_dir):
    """Create cookies.sqlite the way a running Firefox-family browser leaves it: WAL mode, log not yet merged.

    Returns the open connection; the log is only folded in once it is closed (i.e. the browser quits).
    """
    os.makedirs(profile_dir, exist_ok=True)
    db = sqlite3.connect(os.path.join(profile_dir, 'cookies.sqlite'))
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('PRAGMA wal_autocheckpoint=0')
    db.execute('CREATE TABLE moz_cookies (id INTEGER PRIMARY KEY, originAttributes TEXT NOT NULL DEFAULT "", '
               'name TEXT, value TEXT, host TEXT, path TEXT, expiry INTEGER, isSecure INTEGER)')
    expiry = int(time.time()) + 3600
    db.executemany(
        'INSERT INTO moz_cookies (originAttributes, name, value, host, path, expiry, isSecure) '
        'VALUES (?, ?, "v", ?, "/", ?, 1)',
        [('', 'PREF', '.youtube.com', expiry),
         ('', '__Secure-3PAPISID', '.youtube.com', expiry),
         # Logged in inside a Zen workspace bound to a container:
         ('^userContextId=2', 'LOGIN_INFO', '.youtube.com', expiry),
         ('', 'other', '.example.org', expiry)])
    db.commit()
    return db


@pytest.fixture
def zen_home(tmp_path):
    root = tmp_path / ZEN_FLATPAK
    root.mkdir(parents=True)
    (root / 'profiles.ini').write_text(
        '[Profile0]\nName=Default (release)\nIsRelative=1\nPath=k3j2h1g0.Default (release)\nDefault=1\n\n'
        '[General]\nStartWithLastProfile=1\nVersion=2\n')
    db = make_running_browser_profile(str(root / 'k3j2h1g0.Default (release)'))
    yield tmp_path
    db.close()


def test_zen_flatpak_profile_is_detected(zen_home):
    [profile] = detect_browser_profiles('linux', str(zen_home), {})
    assert profile.label == 'Zen — Default (release)'
    assert profile.browser == 'firefox'  # read with yt-dlp's Firefox cookie reader
    assert profile.profile_path.endswith(os.path.join(ZEN_FLATPAK, 'k3j2h1g0.Default (release)'))


def test_cookies_from_running_zen_are_read_including_unmerged_log(zen_home):
    assert os.path.exists(os.path.join(zen_home, ZEN_FLATPAK, 'k3j2h1g0.Default (release)', 'cookies.sqlite-wal'))
    [profile] = detect_browser_profiles('linux', str(zen_home), {})

    probe = probe_profile(profile, 'https://www.youtube.com/watch?v=dQw4w9WgXcQ')

    assert probe.error is None
    assert probe.site_cookies == 3 and probe.logged_in


def test_native_and_xdg_zen_locations(tmp_path):
    for root in ('.zen', os.path.join('.config', 'zen')):
        profile_dir = tmp_path / root / f'{root[-3:]}.Default (release)'
        profile_dir.mkdir(parents=True)
        (profile_dir / 'cookies.sqlite').touch()
    labels = sorted(p.label for p in detect_browser_profiles('linux', str(tmp_path), {}))
    assert labels == ['Zen — Default (release)', 'Zen — Default (release)']


def test_windows_and_macos_zen_locations(tmp_path):
    (tmp_path / 'Roaming' / 'zen' / 'Profiles' / 'a.Default (release)').mkdir(parents=True)
    (tmp_path / 'Roaming' / 'zen' / 'Profiles' / 'a.Default (release)' / 'cookies.sqlite').touch()
    [win] = detect_browser_profiles('win32', str(tmp_path), {'APPDATA': str(tmp_path / 'Roaming')})
    assert win.label == 'Zen — Default (release)'

    mac_dir = tmp_path / 'Library' / 'Application Support' / 'zen' / 'Profiles' / 'b.Default (release)'
    mac_dir.mkdir(parents=True)
    (mac_dir / 'cookies.sqlite').touch()
    [mac] = detect_browser_profiles('darwin', str(tmp_path), {})
    assert mac.label == 'Zen — Default (release)'
