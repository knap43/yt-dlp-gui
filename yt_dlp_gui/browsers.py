"""Discover which browsers (and which of their profiles) exist on this machine.

yt-dlp can read cookies from these browsers, but on its own it only looks in
each browser's default location and silently picks the most recently used
profile.  Here we enumerate *every* profile we can find (including Snap and
Flatpak installs on Linux) so that the downloader can try them one by one.
"""

from __future__ import annotations

import configparser
import glob
import json
import os
import sys
from dataclasses import dataclass

BROWSER_NAMES = {
    'firefox': 'Firefox',
    'chrome': 'Chrome',
    'chromium': 'Chromium',
    'edge': 'Edge',
    'brave': 'Brave',
    'opera': 'Opera',
    'vivaldi': 'Vivaldi',
    'whale': 'Whale',
    'safari': 'Safari',
}

# Browsers whose data directory *is* the profile (no "Default"/"Profile 1" subfolders).
_BROWSERS_WITHOUT_PROFILES = {'opera'}


@dataclass(frozen=True)
class BrowserProfile:
    browser: str          # yt-dlp browser key, e.g. "chrome"
    profile_path: str     # value passed to yt-dlp as the profile
    cookie_db: str        # the cookie database file we found
    profile_name: str     # human-friendly profile name
    last_used: float      # mtime of the cookie database

    @property
    def label(self) -> str:
        name = BROWSER_NAMES.get(self.browser, self.browser.title())
        return f'{name} — {self.profile_name}' if self.profile_name else name

    def to_dict(self) -> dict:
        return {'browser': self.browser, 'profile_path': self.profile_path}


def _chromium_dirs(platform: str, home: str, env: dict) -> dict[str, list[str]]:
    if platform in ('win32', 'cygwin'):
        local = env.get('LOCALAPPDATA') or os.path.join(home, 'AppData', 'Local')
        roaming = env.get('APPDATA') or os.path.join(home, 'AppData', 'Roaming')
        return {
            'chrome': [os.path.join(local, 'Google', 'Chrome', 'User Data')],
            'chromium': [os.path.join(local, 'Chromium', 'User Data')],
            'edge': [os.path.join(local, 'Microsoft', 'Edge', 'User Data')],
            'brave': [os.path.join(local, 'BraveSoftware', 'Brave-Browser', 'User Data')],
            'opera': [os.path.join(roaming, 'Opera Software', 'Opera Stable')],
            'vivaldi': [os.path.join(local, 'Vivaldi', 'User Data')],
            'whale': [os.path.join(local, 'Naver', 'Naver Whale', 'User Data')],
        }
    if platform == 'darwin':
        support = os.path.join(home, 'Library', 'Application Support')
        return {
            'chrome': [os.path.join(support, 'Google', 'Chrome')],
            'chromium': [os.path.join(support, 'Chromium')],
            'edge': [os.path.join(support, 'Microsoft Edge')],
            'brave': [os.path.join(support, 'BraveSoftware', 'Brave-Browser')],
            'opera': [os.path.join(support, 'com.operasoftware.Opera')],
            'vivaldi': [os.path.join(support, 'Vivaldi')],
            'whale': [os.path.join(support, 'Naver', 'Whale')],
        }
    config = env.get('XDG_CONFIG_HOME') or os.path.join(home, '.config')
    flatpak = os.path.join(home, '.var', 'app')
    snap = os.path.join(home, 'snap')
    return {
        'chrome': [
            os.path.join(config, 'google-chrome'),
            os.path.join(flatpak, 'com.google.Chrome', 'config', 'google-chrome'),
        ],
        'chromium': [
            os.path.join(config, 'chromium'),
            os.path.join(snap, 'chromium', 'common', 'chromium'),
            os.path.join(flatpak, 'org.chromium.Chromium', 'config', 'chromium'),
        ],
        'edge': [
            os.path.join(config, 'microsoft-edge'),
            os.path.join(flatpak, 'com.microsoft.Edge', 'config', 'microsoft-edge'),
        ],
        'brave': [
            os.path.join(config, 'BraveSoftware', 'Brave-Browser'),
            os.path.join(snap, 'brave', 'current', '.config', 'BraveSoftware', 'Brave-Browser'),
            os.path.join(flatpak, 'com.brave.Browser', 'config', 'BraveSoftware', 'Brave-Browser'),
        ],
        'opera': [os.path.join(config, 'opera')],
        'vivaldi': [os.path.join(config, 'vivaldi')],
        'whale': [os.path.join(config, 'naver-whale')],
    }


def _firefox_dirs(platform: str, home: str, env: dict) -> list[str]:
    if platform in ('win32', 'cygwin'):
        roaming = env.get('APPDATA') or os.path.join(home, 'AppData', 'Roaming')
        local = env.get('LOCALAPPDATA') or os.path.join(home, 'AppData', 'Local')
        return [
            os.path.join(roaming, 'Mozilla', 'Firefox', 'Profiles'),
            os.path.join(local, 'Packages', 'Mozilla.Firefox_n80bbvh6b1yt2', 'LocalCache',
                         'Roaming', 'Mozilla', 'Firefox', 'Profiles'),
        ]
    if platform == 'darwin':
        return [os.path.join(home, 'Library', 'Application Support', 'Firefox', 'Profiles')]
    config = env.get('XDG_CONFIG_HOME') or os.path.join(home, '.config')
    return [
        os.path.join(config, 'mozilla', 'firefox'),
        os.path.join(home, '.mozilla', 'firefox'),
        os.path.join(home, '.var', 'app', 'org.mozilla.firefox', 'config', 'mozilla', 'firefox'),
        os.path.join(home, '.var', 'app', 'org.mozilla.firefox', '.mozilla', 'firefox'),
        os.path.join(home, 'snap', 'firefox', 'common', '.mozilla', 'firefox'),
    ]


def _mtime(path: str) -> float:
    try:
        return os.stat(path).st_mtime
    except OSError:
        return 0.0


def _chromium_profile_names(browser_dir: str) -> dict[str, str]:
    """Map profile directory names ("Profile 1") to the names shown in the browser UI."""
    try:
        with open(os.path.join(browser_dir, 'Local State'), encoding='utf-8') as f:
            info_cache = json.load(f)['profile']['info_cache']
        return {key: value.get('name') or key for key, value in info_cache.items()}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {}


def _find_chromium_profiles(browser: str, browser_dir: str) -> list[BrowserProfile]:
    if not os.path.isdir(browser_dir):
        return []
    if browser in _BROWSERS_WITHOUT_PROFILES:
        profile_dirs = [browser_dir]
    else:
        profile_dirs = [
            path for path in glob.glob(os.path.join(glob.escape(browser_dir), '*'))
            if os.path.isdir(path)
        ]
    names = _chromium_profile_names(browser_dir)
    profiles = []
    for profile_dir in profile_dirs:
        for candidate in (os.path.join(profile_dir, 'Network', 'Cookies'),
                          os.path.join(profile_dir, 'Cookies')):
            if os.path.isfile(candidate):
                dir_name = os.path.basename(profile_dir)
                name = '' if profile_dir == browser_dir else names.get(dir_name, dir_name)
                profiles.append(BrowserProfile(
                    browser, profile_dir, candidate, name, _mtime(candidate)))
                break
    return profiles


def _firefox_profile_names(root: str) -> dict[str, str]:
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read(os.path.join(root, 'profiles.ini'), encoding='utf-8')
    except (configparser.Error, OSError, UnicodeDecodeError):
        return {}
    names = {}
    for section in parser.sections():
        path, name = parser.get(section, 'Path', fallback=None), parser.get(section, 'Name', fallback=None)
        if path and name:
            names[os.path.normcase(os.path.basename(path.rstrip('/\\')))] = name
    return names


def _find_firefox_profiles(root: str) -> list[BrowserProfile]:
    if not os.path.isdir(root):
        return []
    names = _firefox_profile_names(root)
    profiles = []
    escaped = glob.escape(root)
    for pattern in ('*', os.path.join('Profiles', '*')):
        for cookie_db in glob.glob(os.path.join(escaped, pattern, 'cookies.sqlite')):
            profile_dir = os.path.dirname(cookie_db)
            dir_name = os.path.basename(profile_dir)
            # Profile folders look like "x1y2z3.default-release"; the part after the dot is readable.
            name = names.get(os.path.normcase(dir_name)) or dir_name.partition('.')[2] or dir_name
            profiles.append(BrowserProfile('firefox', profile_dir, cookie_db, name, _mtime(cookie_db)))
    return profiles


def _find_safari_profiles(home: str) -> list[BrowserProfile]:
    for cookie_db in (
        os.path.join(home, 'Library', 'Containers', 'com.apple.Safari', 'Data', 'Library',
                     'Cookies', 'Cookies.binarycookies'),
        os.path.join(home, 'Library', 'Cookies', 'Cookies.binarycookies'),
    ):
        if os.path.isfile(cookie_db):
            return [BrowserProfile('safari', cookie_db, cookie_db, '', _mtime(cookie_db))]
    return []


def detect_browser_profiles(platform: str | None = None, home: str | None = None,
                            env: dict | None = None) -> list[BrowserProfile]:
    """Return every browser profile with a cookie database, most recently used first."""
    platform = platform or sys.platform
    home = home or os.path.expanduser('~')
    env = os.environ if env is None else env

    profiles: list[BrowserProfile] = []
    for root in _firefox_dirs(platform, home, env):
        profiles.extend(_find_firefox_profiles(root))
    for browser, dirs in _chromium_dirs(platform, home, env).items():
        for browser_dir in dirs:
            profiles.extend(_find_chromium_profiles(browser, browser_dir))
    if platform == 'darwin':
        profiles.extend(_find_safari_profiles(home))

    unique = {}
    for profile in profiles:
        key = (profile.browser, os.path.normcase(os.path.realpath(profile.cookie_db)))
        unique.setdefault(key, profile)
    return sorted(unique.values(), key=lambda p: p.last_used, reverse=True)


def find_profile(profiles: list[BrowserProfile], data: dict | None) -> BrowserProfile | None:
    """Look up a profile previously serialised with `BrowserProfile.to_dict`."""
    if not data:
        return None
    for profile in profiles:
        if profile.browser == data.get('browser') and os.path.normcase(profile.profile_path) == \
                os.path.normcase(data.get('profile_path') or ''):
            return profile
    return None
