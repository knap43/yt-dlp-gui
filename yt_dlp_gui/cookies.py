"""Recognise "you need to be signed in" errors and load cookies from browser profiles."""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from urllib.parse import urlparse

from .browsers import BROWSER_NAMES, BrowserProfile

# Phrases yt-dlp (and the sites it talks to) use when a request only works for a signed-in user.
_COOKIE_ERROR_PATTERNS = [
    r'--cookies',                      # yt-dlp's own hint: "Use --cookies-from-browser or --cookies ..."
    r'sign in to confirm',             # YouTube: "Sign in to confirm you're not a bot" / "... your age"
    r'not a bot',
    r'confirm your age',
    r'age[- ]restricted',
    r'inappropriate for some users',
    r'login required',
    r'log ?in required',
    r'requires? (?:a )?(?:log ?in|login|sign[- ]?in|authentication|account)',
    r'only available (?:for|to) (?:registered|logged[- ]in|signed[- ]in)',
    r'available to this channel\'s members',
    r'members[- ]only',
    r'join this channel to get access',
    r'private video',
    r'you need to log ?in',
    r'please (?:log|sign) ?in',
    r'cookies? (?:are|is) (?:needed|required)',
]
_COOKIE_ERROR_RE = re.compile('|'.join(_COOKIE_ERROR_PATTERNS), re.IGNORECASE)

# Cookies that indicate an actual logged-in session on popular sites.
_LOGIN_COOKIES = {
    'youtube.com': {'LOGIN_INFO', 'SAPISID', '__Secure-3PAPISID', 'SID', '__Secure-3PSID'},
    'google.com': {'SAPISID', '__Secure-3PAPISID', 'SID', '__Secure-3PSID'},
    'instagram.com': {'sessionid'},
    'facebook.com': {'c_user', 'xs'},
    'twitter.com': {'auth_token'},
    'x.com': {'auth_token'},
    'tiktok.com': {'sessionid', 'sid_tt'},
    'reddit.com': {'reddit_session', 'token_v2'},
    'twitch.tv': {'auth-token'},
    'vimeo.com': {'vimeo'},
    'patreon.com': {'session_id'},
    'nicovideo.jp': {'user_session'},
    'bilibili.com': {'SESSDATA'},
}

# Other hosts whose cookies live under a different domain.
_DOMAIN_ALIASES = {
    'youtu.be': 'youtube.com',
    'youtube-nocookie.com': 'youtube.com',
    'music.youtube.com': 'youtube.com',
    'fb.watch': 'facebook.com',
    'vm.tiktok.com': 'tiktok.com',
    'redd.it': 'reddit.com',
    'b23.tv': 'bilibili.com',
}

_TWO_PART_SUFFIXES = {'co', 'com', 'net', 'org', 'gov', 'ac', 'edu', 'ne', 'or', 'go'}


def is_cookie_error(message: str | None) -> bool:
    """True if an error message says the content needs a signed-in session."""
    return bool(message and _COOKIE_ERROR_RE.search(message))


def base_domain(url: str) -> str:
    """Best-effort registrable domain of a URL: "https://m.youtube.com/x" -> "youtube.com"."""
    if '://' not in url:
        url = 'https://' + url
    host = (urlparse(url).hostname or '').lower().rstrip('.')
    for alias, target in _DOMAIN_ALIASES.items():
        if host == alias or host.endswith('.' + alias):
            return target
    parts = host.split('.')
    if len(parts) >= 3 and parts[-2] in _TWO_PART_SUFFIXES and len(parts[-1]) == 2:
        return '.'.join(parts[-3:])
    return '.'.join(parts[-2:])


def _domain_matches(cookie_domain: str, domain: str) -> bool:
    cookie_domain = cookie_domain.lstrip('.').lower()
    return cookie_domain == domain or cookie_domain.endswith('.' + domain)


@dataclass
class CookieProbe:
    """Result of loading one browser profile's cookies."""
    profile: BrowserProfile
    jar: object = None
    error: str | None = None
    hint: str | None = None
    warnings: list[str] = field(default_factory=list)
    site_cookies: int = 0
    logged_in: bool = False

    @property
    def usable(self) -> bool:
        return self.jar is not None and self.site_cookies > 0

    def summary(self, domain: str) -> str:
        if self.error:
            return f'{self.profile.label}: could not read cookies ({self.error})'
        if not self.site_cookies:
            return f'{self.profile.label}: no cookies for {domain}'
        state = 'signed in' if self.logged_in else 'not obviously signed in'
        return f'{self.profile.label}: {self.site_cookies} cookies for {domain}, {state}'


class _CapturingLogger:
    """Stand-in for yt-dlp's cookie logger that records messages instead of printing them."""

    def __init__(self):
        self.messages: list[str] = []

    def debug(self, message):
        pass

    def info(self, message):
        pass

    def warning(self, message, only_once=False, **kwargs):
        self.messages.append(str(message))

    def error(self, message, **kwargs):
        self.messages.append(str(message))

    def progress_bar(self):
        return None


def explain_cookie_load_error(browser: str, message: str, platform: str | None = None) -> str:
    """Turn a cookie extraction failure into a plain-language suggestion."""
    platform = platform or sys.platform
    name = BROWSER_NAMES.get(browser, browser)
    lowered = message.lower()
    if 'could not copy' in lowered or 'database is locked' in lowered or 'permission denied' in lowered \
            or 'errno 13' in lowered:
        return (f'{name} is locking its cookie file. Close {name} completely (including any '
                f'background process in the system tray) and try again.')
    if 'dpapi' in lowered or 'app-bound' in lowered or 'v20' in lowered:
        return (f'{name} encrypts its cookies in a way yt-dlp cannot read while it is running '
                f'(App-Bound Encryption). Close {name} fully and retry, or sign in with Firefox instead.')
    if 'keyring' in lowered or 'secretstorage' in lowered or 'kwallet' in lowered:
        return (f'The system keyring that protects {name}\'s cookies could not be unlocked. '
                f'Unlock your keyring (or log in to the desktop session) and try again.')
    if 'failed to decrypt' in lowered or 'possibly the key is wrong' in lowered:
        return f'Some {name} cookies could not be decrypted. Signing in with Firefox usually avoids this.'
    if platform == 'darwin' and ('keychain' in lowered or 'safe storage' in lowered):
        return f'macOS denied access to the {name} Safe Storage key. Click "Always Allow" when asked.'
    if 'full disk access' in lowered or 'operation not permitted' in lowered:
        return 'Grant this app (or your terminal) "Full Disk Access" in System Settings to read Safari cookies.'
    if 'sqlite' in lowered:
        return 'Python was built without SQLite support, which is needed to read browser cookies.'
    return f'Could not read {name}\'s cookies.'


def load_profile_cookies(profile: BrowserProfile) -> tuple[object, list[str]]:
    """Load a profile's cookies with yt-dlp. Returns (cookie jar, warnings). Raises on failure."""
    from yt_dlp.cookies import extract_cookies_from_browser

    logger = _CapturingLogger()
    profile_arg = None if profile.browser == 'safari' and not profile.profile_path else profile.profile_path
    jar = extract_cookies_from_browser(profile.browser, profile_arg, logger)
    return jar, logger.messages


def probe_profile(profile: BrowserProfile, url: str, loader=load_profile_cookies) -> CookieProbe:
    """Load a profile's cookies and check whether they're relevant to `url`."""
    probe = CookieProbe(profile)
    domain = base_domain(url)
    try:
        probe.jar, probe.warnings = loader(profile)
    except Exception as e:  # yt-dlp raises a wide variety of errors here
        message = str(e) or type(e).__name__
        probe.error = message
        probe.hint = explain_cookie_load_error(profile.browser, message)
        return probe

    login_names = _LOGIN_COOKIES.get(domain, set())
    for cookie in probe.jar:
        if _domain_matches(cookie.domain, domain):
            probe.site_cookies += 1
            if cookie.name in login_names:
                probe.logged_in = True
    if not login_names and probe.site_cookies:
        # Unknown site: any cookies at all is the best evidence we have.
        probe.logged_in = True
    if probe.warnings and not probe.site_cookies:
        probe.hint = explain_cookie_load_error(profile.browser, ' '.join(probe.warnings))
    return probe


def rank_probes(probes: list[CookieProbe]) -> list[CookieProbe]:
    """Most promising first: signed-in profiles, then those with the most site cookies, then newest."""
    usable = [p for p in probes if p.usable]
    return sorted(usable, key=lambda p: (p.logged_in, p.site_cookies, p.profile.last_used), reverse=True)
