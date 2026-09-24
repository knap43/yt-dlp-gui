import functools
import http.cookiejar

import pytest
from yt_dlp.utils import DownloadError

from yt_dlp_gui.browsers import BrowserProfile
from yt_dlp_gui.cookies import CookieProbe
from yt_dlp_gui.downloader import CookieChoice, DownloadJob, Downloader
from yt_dlp_gui.settings import Settings

BOT_ERROR = "ERROR: [youtube] x: Sign in to confirm you're not a bot. Use --cookies-from-browser or --cookies"
URL = 'https://www.youtube.com/watch?v=x'


def profile(name, last_used=0):
    return BrowserProfile('firefox', '/p/' + name, '/p/' + name + '/cookies.sqlite', name, last_used)


class FakeYDL:
    """Mimics the parts of yt_dlp.YoutubeDL the downloader touches."""
    calls = []

    def __init__(self, params, behaviour):
        self.params = params
        self.behaviour = behaviour

    @functools.cached_property
    def cookiejar(self):
        return None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def download(self, urls):
        FakeYDL.calls.append(self.cookiejar)
        outcome = self.behaviour(self.cookiejar)
        if outcome == 'ok':
            return 0
        raise DownloadError(outcome)


def make_downloader(tmp_path, behaviour, probes, profiles=None):
    events = []
    FakeYDL.calls = []
    settings = Settings(str(tmp_path / 'settings.json'))
    profiles = profiles if profiles is not None else [p.profile for p in probes.values()]

    def probe(p, url):
        return probes[p.profile_name]

    downloader = Downloader(lambda *e: events.append(e), settings,
                            ydl_factory=lambda params: FakeYDL(params, behaviour),
                            detect_profiles=lambda: profiles, probe=probe, has_ffmpeg=True)
    return downloader, events, settings


def job(tmp_path, cookies=None):
    return DownloadJob([URL], str(tmp_path), cookies=cookies or CookieChoice('auto'))


def usable(name, last_used=0, logged_in=True):
    return CookieProbe(profile(name, last_used), jar=name, site_cookies=3, logged_in=logged_in)


def test_no_cookies_needed(tmp_path):
    downloader, events, _ = make_downloader(tmp_path, lambda jar: 'ok', {'a': usable('a')})
    assert downloader.run(job(tmp_path))
    assert FakeYDL.calls == [None]
    assert ('done', 1, 0) in events


def test_auto_retries_with_the_signed_in_browser_and_remembers_it(tmp_path):
    probes = {'anon': usable('anon', 5, logged_in=False), 'me': usable('me', 1),
              'broken': CookieProbe(profile('broken'), error='locked', hint='close it')}
    downloader, events, settings = make_downloader(
        tmp_path, lambda jar: 'ok' if jar == 'me' else BOT_ERROR, probes)

    assert downloader.run(job(tmp_path))
    assert FakeYDL.calls == [None, 'me']
    assert settings.remembered_cookie_profile('youtube.com') == profile('me').to_dict()
    assert not any(e[0] == 'needs_login' for e in events)

    # Next time the remembered profile is used straight away.
    downloader, events, _ = make_downloader(
        tmp_path, lambda jar: 'ok' if jar == 'me' else BOT_ERROR, probes)
    assert downloader.run(job(tmp_path))
    assert FakeYDL.calls == ['me']


def test_auto_reports_when_no_browser_is_signed_in(tmp_path):
    probes = {'a': usable('a'), 'b': CookieProbe(profile('b'), error='x', hint='Close Chrome completely')}
    downloader, events, _ = make_downloader(tmp_path, lambda jar: BOT_ERROR, probes)
    assert not downloader.run(job(tmp_path))
    assert FakeYDL.calls == [None, 'a']
    [(_, title, message)] = [e for e in events if e[0] == 'needs_login']
    assert 'youtube.com' in message and 'Close Chrome completely' in message


def test_non_cookie_errors_do_not_trigger_browser_search(tmp_path):
    downloader, events, _ = make_downloader(tmp_path, lambda jar: 'ERROR: Unsupported URL', {'a': usable('a')})
    assert not downloader.run(job(tmp_path))
    assert FakeYDL.calls == [None]


def test_manual_browser_choice(tmp_path):
    probes = {'a': usable('a'), 'b': usable('b')}
    downloader, events, _ = make_downloader(tmp_path, lambda jar: 'ok', probes)
    assert downloader.run(job(tmp_path, CookieChoice('browser', profile=profile('b'))))
    assert FakeYDL.calls == ['b']


def test_cookie_file_is_passed_to_yt_dlp(tmp_path):
    seen = {}

    def factory(params):
        seen.update(params)
        return FakeYDL(params, lambda jar: 'ok')
    downloader = Downloader(lambda *e: None, Settings(str(tmp_path / 's.json')), ydl_factory=factory,
                            detect_profiles=lambda: [], has_ffmpeg=True)
    assert downloader.run(job(tmp_path, CookieChoice('file', file='/tmp/cookies.txt')))
    assert seen['cookiefile'] == '/tmp/cookies.txt'


def test_mp3_without_ffmpeg_fails_early(tmp_path):
    downloader = Downloader(lambda *e: None, Settings(str(tmp_path / 's.json')),
                            ydl_factory=lambda p: pytest.fail('should not run'), has_ffmpeg=False)
    assert not downloader.run(DownloadJob([URL], str(tmp_path), preset='mp3'))


def test_real_youtubedl_accepts_injected_cookie_jar(tmp_path):
    """Guard the one yt-dlp internal we rely on: cookiejar is a cached_property we can pre-fill."""
    from yt_dlp import YoutubeDL

    from yt_dlp_gui.downloader import _supports_jar_injection
    jar = http.cookiejar.CookieJar()
    with YoutubeDL({'quiet': True}) as ydl:
        assert _supports_jar_injection(ydl)
        ydl.__dict__['cookiejar'] = jar
        assert ydl.cookiejar is jar
