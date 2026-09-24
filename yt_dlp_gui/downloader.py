"""Runs yt-dlp and transparently retries with browser cookies when a site demands a sign-in."""

from __future__ import annotations

import functools
import inspect
import os
import shutil
import threading
from dataclasses import dataclass, field
from typing import Callable

from .browsers import BrowserProfile, detect_browser_profiles, find_profile
from .cookies import CookieProbe, base_domain, is_cookie_error, probe_profile, rank_probes
from .settings import Settings


@dataclass(frozen=True)
class Preset:
    key: str
    label: str
    format: str
    format_without_ffmpeg: str | None
    merge_output_format: str | None = None
    audio_codec: str | None = None


PRESETS = [
    Preset('best', 'Best quality (video + audio)', 'bv*+ba/b', 'b'),
    Preset('mp4', 'Best MP4 (plays everywhere)',
           'bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/bv*+ba/b', 'b[ext=mp4]/b', merge_output_format='mp4'),
    Preset('1080', 'Up to 1080p', 'bv*[height<=1080]+ba/b[height<=1080]/bv*+ba/b', 'b[height<=1080]/b'),
    Preset('720', 'Up to 720p', 'bv*[height<=720]+ba/b[height<=720]/bv*+ba/b', 'b[height<=720]/b'),
    Preset('480', 'Up to 480p', 'bv*[height<=480]+ba/b[height<=480]/bv*+ba/b', 'b[height<=480]/b'),
    Preset('m4a', 'Audio only (M4A)', 'ba[ext=m4a]/ba/b', 'ba[ext=m4a]/ba/b'),
    Preset('mp3', 'Audio only (MP3)', 'ba/b', None, audio_codec='mp3'),
]
PRESETS_BY_KEY = {p.key: p for p in PRESETS}


def ffmpeg_available() -> bool:
    return shutil.which('ffmpeg') is not None


@dataclass
class CookieChoice:
    mode: str = 'auto'                  # 'auto' | 'none' | 'browser' | 'file'
    profile: BrowserProfile | None = None
    file: str | None = None


@dataclass
class DownloadJob:
    urls: list[str]
    output_dir: str
    preset: str = 'best'
    cookies: CookieChoice = field(default_factory=CookieChoice)
    playlist: bool = False


@dataclass
class AttemptResult:
    ok: bool
    errors: list[str] = field(default_factory=list)

    @property
    def error(self) -> str:
        return '\n'.join(self.errors)


class Cancelled(Exception):
    pass


class _Logger:
    """Forwards yt-dlp's log output to the GUI and remembers any errors."""

    def __init__(self, emit, soften_cookie_errors=False):
        self._emit = emit
        self._soften_cookie_errors = soften_cookie_errors
        self.errors: list[str] = []

    def debug(self, message):
        if not message.startswith('[debug] '):
            self._emit('log', 'info', message)

    def info(self, message):
        self._emit('log', 'info', message)

    def warning(self, message):
        self._emit('log', 'warning', message)

    def error(self, message):
        self.errors.append(message)
        # In automatic mode a sign-in error is expected and handled, so don't show it in alarming red.
        level = 'warning' if self._soften_cookie_errors and is_cookie_error(message) else 'error'
        self._emit('log', level, message)


def _supports_jar_injection(ydl) -> bool:
    return isinstance(inspect.getattr_static(type(ydl), 'cookiejar', None), functools.cached_property)


def _default_ydl_factory(params):
    from yt_dlp import YoutubeDL
    return YoutubeDL(params)


class Downloader:
    """Download a list of URLs, one at a time, reporting progress through `emit(kind, *args)`.

    Event kinds: ('log', level, text), ('status', text), ('progress', fraction_or_None, text),
    ('cookies', text), ('needs_login', title, message), ('done', succeeded, failed).
    """

    def __init__(self, emit: Callable, settings: Settings, *, ydl_factory=None,
                 detect_profiles=detect_browser_profiles, probe=probe_profile, has_ffmpeg=None):
        self._emit = emit
        self._settings = settings
        self._ydl_factory = ydl_factory or _default_ydl_factory
        self._detect_profiles = detect_profiles
        self._probe = probe
        self._has_ffmpeg = ffmpeg_available() if has_ffmpeg is None else has_ffmpeg
        self._cancel = threading.Event()

    def cancel(self):
        self._cancel.set()

    # -- public entry point -------------------------------------------------------------------

    def run(self, job: DownloadJob) -> bool:
        succeeded = failed = 0
        try:
            preset = PRESETS_BY_KEY.get(job.preset, PRESETS[0])
            if preset.format_without_ffmpeg is None and not self._has_ffmpeg:
                self._emit('log', 'error', f'"{preset.label}" needs FFmpeg, which was not found on this computer. '
                           'Install FFmpeg (https://ffmpeg.org/download.html) or pick another format.')
                failed = len(job.urls)
                return False
            if not self._has_ffmpeg:
                self._emit('log', 'warning', 'FFmpeg was not found, so video and audio cannot be merged; '
                           'downloading the best single-file format instead.')
            for index, url in enumerate(job.urls, 1):
                if self._cancel.is_set():
                    raise Cancelled
                if len(job.urls) > 1:
                    self._emit('log', 'info', f'--- [{index}/{len(job.urls)}] {url}')
                if self._download_url(url, job, preset):
                    succeeded += 1
                else:
                    failed += 1
        except Cancelled:
            self._emit('log', 'warning', 'Download cancelled.')
            self._emit('status', 'Cancelled')
            return False
        finally:
            self._emit('done', succeeded, failed)
        return failed == 0

    # -- cookie strategy ----------------------------------------------------------------------

    def _download_url(self, url: str, job: DownloadJob, preset: Preset) -> bool:
        domain = base_domain(url)
        choice = job.cookies

        if choice.mode == 'none':
            result = self._attempt(url, job, preset)
            if not result.ok and is_cookie_error(result.error):
                self._report_login_needed(domain, [], manual=True)
            return result.ok

        if choice.mode == 'file':
            self._emit('cookies', f'Using cookies from {os.path.basename(choice.file or "")}')
            result = self._attempt(url, job, preset, cookie_file=choice.file)
            if not result.ok and is_cookie_error(result.error):
                self._report_login_needed(domain, [], manual=True)
            return result.ok

        if choice.mode == 'browser' and choice.profile:
            probe = self._probe_profile(choice.profile, url)
            if probe.error:
                self._emit('log', 'error', probe.summary(domain))
                self._report_login_needed(domain, [probe], manual=True)
                return False
            self._emit('cookies', f'Using cookies from {probe.profile.label}')
            result = self._attempt(url, job, preset, probe=probe)
            if not result.ok and is_cookie_error(result.error):
                self._report_login_needed(domain, [probe], manual=True)
            return result.ok

        return self._download_url_auto(url, job, preset, domain)

    def _download_url_auto(self, url: str, job: DownloadJob, preset: Preset, domain: str) -> bool:
        profiles = self._detect_profiles()
        tried: set[BrowserProfile] = set()

        # 1. Start with whatever worked for this site last time, or with no cookies at all.
        first_probe = None
        remembered = find_profile(profiles, self._settings.remembered_cookie_profile(domain))
        if remembered:
            probe = self._probe_profile(remembered, url)
            tried.add(remembered)
            if probe.usable:
                first_probe = probe
                self._emit('cookies', f'Using cookies from {probe.profile.label} (worked last time)')
        if first_probe is None:
            self._emit('cookies', 'No cookies needed so far')

        result = self._attempt(url, job, preset, probe=first_probe)
        if result.ok or not is_cookie_error(result.error):
            return result.ok

        # 2. The site wants a signed-in session: look through every browser profile we can find.
        self._emit('log', 'warning', f'{domain} requires you to be signed in. '
                   'Looking for a browser where you are logged in…')
        self._emit('status', 'Looking for browser cookies…')
        if not profiles:
            self._report_login_needed(domain, [])
            return False

        probes = []
        for profile in profiles:
            if self._cancel.is_set():
                raise Cancelled
            if profile in tried:
                continue
            probe = self._probe_profile(profile, url)
            probes.append(probe)
            level = 'warning' if probe.error else 'info'
            self._emit('log', level, '  ' + probe.summary(domain))
            if probe.hint and probe.error:
                self._emit('log', 'warning', '    → ' + probe.hint)

        for probe in rank_probes(probes):
            if self._cancel.is_set():
                raise Cancelled
            self._emit('cookies', f'Trying cookies from {probe.profile.label}')
            self._emit('log', 'info', f'Retrying with cookies from {probe.profile.label}…')
            result = self._attempt(url, job, preset, probe=probe)
            if result.ok:
                self._settings.remember_cookie_profile(domain, probe.profile.to_dict())
                self._emit('cookies', f'Using cookies from {probe.profile.label}')
                self._emit('log', 'info', f'Success! {probe.profile.label} will be used for {domain} from now on.')
                return True
            if not is_cookie_error(result.error):
                # The cookies got us past the sign-in wall but something else went wrong.
                self._settings.remember_cookie_profile(domain, probe.profile.to_dict())
                return False

        if first_probe is not None:
            # What worked before doesn't anymore; don't keep trying it first.
            self._settings.remember_cookie_profile(domain, None)
        self._report_login_needed(domain, probes + ([first_probe] if first_probe else []))
        return False

    def _probe_profile(self, profile: BrowserProfile, url: str) -> CookieProbe:
        self._emit('status', f'Reading cookies from {profile.label}…')
        return self._probe(profile, url)

    def _report_login_needed(self, domain: str, probes: list[CookieProbe], manual: bool = False):
        lines = [f'{domain} only lets signed-in users download this.', '']
        blocked = [p for p in probes if p.error]
        if manual:
            lines.append('The cookies you selected did not work. Switch the Cookies option to '
                         '"Automatic" to let the app search all of your browsers, or:')
        elif not probes:
            lines.append('No browser with saved cookies was found on this computer. To fix this:')
        else:
            lines.append('None of your browsers seem to be signed in to this site. To fix this:')
        lines += [
            f'  1. Open {domain} in your browser (Firefox works most reliably) and sign in.',
            '  2. Watch the video there once to make sure your account can play it.',
            '  3. Close the browser completely, then press Download again.',
        ]
        hints = []
        for probe in blocked:
            if probe.hint and probe.hint not in hints:
                hints.append(probe.hint)
        if hints:
            lines += ['', 'Problems reading some browsers:'] + [f'  • {h}' for h in hints]
        lines += ['', 'Alternatively, export a cookies.txt file with a browser extension such as '
                  '"Get cookies.txt LOCALLY" and choose it under Cookies → "cookies.txt file…".']
        message = '\n'.join(lines)
        self._emit('log', 'error', message)
        self._emit('needs_login', 'Sign-in required', message)

    # -- a single yt-dlp run --------------------------------------------------------------------

    def _build_params(self, job: DownloadJob, preset: Preset, logger: _Logger) -> dict:
        fmt = preset.format if self._has_ffmpeg else preset.format_without_ffmpeg
        params = {
            'format': fmt,
            'outtmpl': {'default': os.path.join(job.output_dir, '%(title)s [%(id)s].%(ext)s')},
            'noplaylist': not job.playlist,
            'ignoreerrors': 'only_download',
            'windowsfilenames': True,
            'logger': logger,
            'noprogress': True,
            'progress_hooks': [self._progress_hook],
            'postprocessor_hooks': [self._postprocessor_hook],
        }
        if self._has_ffmpeg and preset.merge_output_format:
            params['merge_output_format'] = preset.merge_output_format
        if preset.audio_codec:
            params['postprocessors'] = [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': preset.audio_codec,
                'preferredquality': '0',
            }]
        return params

    def _attempt(self, url: str, job: DownloadJob, preset: Preset, *,
                 probe: CookieProbe | None = None, cookie_file: str | None = None) -> AttemptResult:
        from yt_dlp.utils import DownloadCancelled

        logger = _Logger(self._emit, soften_cookie_errors=job.cookies.mode == 'auto')
        params = self._build_params(job, preset, logger)
        if cookie_file:
            params['cookiefile'] = cookie_file
        self._emit('status', 'Fetching video information…')
        self._emit('progress', None, '')

        try:
            ydl = self._ydl_factory(params)
            if probe is not None:
                if _supports_jar_injection(ydl):
                    # Reuse the cookies we already decrypted, instead of making yt-dlp read them again.
                    ydl.__dict__['cookiejar'] = probe.jar
                else:
                    if hasattr(ydl, 'close'):
                        ydl.close()
                    params['cookiesfrombrowser'] = (probe.profile.browser, probe.profile.profile_path, None, None)
                    ydl = self._ydl_factory(params)
            with ydl:
                retcode = ydl.download([url])
        except DownloadCancelled:
            raise Cancelled
        except Exception as e:  # DownloadError and anything unexpected from extractors
            message = str(e) or type(e).__name__
            if message not in logger.errors:
                logger.error(message)
            return AttemptResult(False, logger.errors)
        if self._cancel.is_set():
            raise Cancelled
        ok = retcode == 0 and not logger.errors
        if ok:
            self._emit('progress', 1.0, 'Finished')
            self._emit('status', 'Finished')
        return AttemptResult(ok, logger.errors)

    # -- yt-dlp hooks (called on the worker thread) --------------------------------------------

    def _progress_hook(self, d: dict):
        from yt_dlp.utils import DownloadCancelled

        if self._cancel.is_set():
            raise DownloadCancelled('Cancelled by user')
        info = d.get('info_dict') or {}
        title = info.get('title') or os.path.basename(d.get('filename') or '')
        prefix = ''
        if info.get('playlist_index') and info.get('n_entries'):
            prefix = f'[{info["playlist_index"]}/{info["n_entries"]}] '
        if d.get('status') == 'downloading':
            total = d.get('total_bytes') or d.get('total_bytes_estimate')
            done = d.get('downloaded_bytes') or 0
            fraction = done / total if total else None
            parts = []
            if fraction is not None:
                parts.append(f'{fraction * 100:5.1f}% of {_human_size(total)}')
            else:
                parts.append(_human_size(done))
            if d.get('speed'):
                parts.append(f'{_human_size(d["speed"])}/s')
            if d.get('eta') is not None:
                parts.append(f'ETA {_human_time(d["eta"])}')
            self._emit('status', f'{prefix}Downloading {title}')
            self._emit('progress', fraction, ' · '.join(parts))
        elif d.get('status') == 'finished':
            self._emit('progress', 1.0, f'Downloaded {os.path.basename(d.get("filename") or "")}')

    def _postprocessor_hook(self, d: dict):
        from yt_dlp.utils import DownloadCancelled

        if self._cancel.is_set():
            raise DownloadCancelled('Cancelled by user')
        if d.get('status') == 'started':
            names = {'Merger': 'Merging video and audio…', 'ExtractAudio': 'Converting audio…',
                     'FFmpegExtractAudio': 'Converting audio…', 'MoveFiles': 'Finishing…'}
            name = d.get('postprocessor') or ''
            self._emit('status', names.get(name, f'Post-processing ({name})…'))


def _human_size(num) -> str:
    num = float(num or 0)
    for unit in ('B', 'KiB', 'MiB', 'GiB'):
        if num < 1024 or unit == 'GiB':
            return f'{num:.0f} {unit}' if unit == 'B' else f'{num:.1f} {unit}'
        num /= 1024
    return f'{num:.1f} GiB'


def _human_time(seconds) -> str:
    seconds = int(seconds or 0)
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f'{hours}:{minutes:02d}:{secs:02d}' if hours else f'{minutes}:{secs:02d}'

