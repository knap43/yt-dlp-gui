import http.cookiejar

import pytest

from yt_dlp_gui.browsers import BrowserProfile
from yt_dlp_gui.cookies import base_domain, explain_cookie_load_error, is_cookie_error, probe_profile, rank_probes


@pytest.mark.parametrize('message', [
    "ERROR: [youtube] dQw4w9WgXcQ: Sign in to confirm you’re not a bot. Use --cookies-from-browser or --cookies "
    'for the authentication.',
    'ERROR: [youtube] abc: Sign in to confirm your age. This video may be inappropriate for some users.',
    'ERROR: [instagram] xyz: Requested content is not available, rate-limit reached or login required.',
    "ERROR: [youtube] abc: Join this channel to get access to members-only content like this video",
    'ERROR: [youtube] abc: Private video. Sign in if you\'ve been granted access to this video',
    'ERROR: [vimeo] 1: This video is only available for registered users.',
])
def test_cookie_errors_are_recognised(message):
    assert is_cookie_error(message)


@pytest.mark.parametrize('message', [
    'ERROR: [generic] Unsupported URL: https://example.com',
    'ERROR: unable to download video data: HTTP Error 404: Not Found',
    'ERROR: [youtube] abc: Video unavailable. This video has been removed by the uploader',
    '',
    None,
])
def test_other_errors_are_not(message):
    assert not is_cookie_error(message)


@pytest.mark.parametrize('url, domain', [
    ('https://www.youtube.com/watch?v=x', 'youtube.com'),
    ('https://youtu.be/x', 'youtube.com'),
    ('https://music.youtube.com/watch?v=x', 'youtube.com'),
    ('www.instagram.com/p/abc', 'instagram.com'),
    ('https://www.bbc.co.uk/iplayer', 'bbc.co.uk'),
    ('https://vimeo.com/1', 'vimeo.com'),
])
def test_base_domain(url, domain):
    assert base_domain(url) == domain


def make_cookie(domain, name):
    return http.cookiejar.Cookie(0, name, 'v', None, False, domain, True, domain.startswith('.'), '/', False,
                                 True, None, False, None, None, {})


def make_jar(*cookies):
    jar = http.cookiejar.CookieJar()
    for domain, name in cookies:
        jar.set_cookie(make_cookie(domain, name))
    return jar


def profile(name, last_used=0):
    return BrowserProfile('firefox', '/p/' + name, '/p/' + name + '/cookies.sqlite', name, last_used)


def test_probe_detects_login_cookies():
    jar = make_jar(('.youtube.com', 'LOGIN_INFO'), ('.youtube.com', 'PREF'), ('.example.com', 'x'))
    probe = probe_profile(profile('a'), 'https://youtu.be/x', loader=lambda p: (jar, []))
    assert probe.usable and probe.logged_in and probe.site_cookies == 2


def test_probe_without_site_cookies_is_not_usable():
    probe = probe_profile(profile('a'), 'https://youtube.com/x', loader=lambda p: (make_jar(('.x.com', 'a')), []))
    assert not probe.usable


def test_probe_reports_load_errors_with_hint():
    def loader(p):
        raise PermissionError(13, 'Permission denied')
    probe = probe_profile(profile('a'), 'https://youtube.com/x', loader=loader)
    assert probe.error and not probe.usable
    assert 'Close Firefox completely' in probe.hint


def test_ranking_prefers_logged_in_then_cookie_count_then_recency():
    loaders = {
        'anon-many': make_jar(*[('.youtube.com', f'c{i}') for i in range(10)]),
        'logged-old': make_jar(('.youtube.com', 'SAPISID')),
        'logged-new': make_jar(('.youtube.com', 'SAPISID')),
        'none': make_jar(),
    }
    probes = [probe_profile(profile(name, last_used=2 if name == 'logged-new' else 1), 'https://youtube.com/x',
                            loader=lambda p: (loaders[p.profile_name], []))
              for name in loaders]
    assert [p.profile.profile_name for p in rank_probes(probes)] == ['logged-new', 'logged-old', 'anon-many']


def test_explanations():
    assert 'App-Bound' in explain_cookie_load_error('chrome', 'Failed to decrypt with DPAPI', 'win32')
    assert 'keyring' in explain_cookie_load_error('chromium', 'no keyring found', 'linux')
    assert 'Could not read' in explain_cookie_load_error('firefox', 'weird', 'linux')


def test_real_firefox_profile_is_read_through_yt_dlp(tmp_path):
    """End to end through yt-dlp's own Firefox reader, using a hand-made cookies.sqlite."""
    import sqlite3
    import time

    from yt_dlp_gui.browsers import detect_browser_profiles

    profile_dir = tmp_path / '.mozilla' / 'firefox' / 'abc.default-release'
    profile_dir.mkdir(parents=True)
    db = sqlite3.connect(profile_dir / 'cookies.sqlite')
    db.execute('CREATE TABLE moz_cookies (id INTEGER PRIMARY KEY, originAttributes TEXT NOT NULL DEFAULT "", '
               'name TEXT, value TEXT, host TEXT, path TEXT, expiry INTEGER, isSecure INTEGER)')
    expiry = int(time.time()) + 3600
    db.executemany('INSERT INTO moz_cookies (name, value, host, path, expiry, isSecure) VALUES (?, ?, ?, "/", ?, 1)',
                   [('LOGIN_INFO', 'x', '.youtube.com', expiry), ('PREF', 'y', '.youtube.com', expiry),
                    ('other', 'z', '.example.org', expiry)])
    db.commit()
    db.close()

    [found] = detect_browser_profiles('linux', str(tmp_path), {})
    probe = probe_profile(found, 'https://youtu.be/abc')
    assert probe.error is None
    assert probe.usable and probe.logged_in and probe.site_cookies == 2
