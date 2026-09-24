"""Small JSON settings file stored in the platform's usual config location."""

from __future__ import annotations

import json
import os
import sys
import threading


def default_settings_path() -> str:
    if sys.platform in ('win32', 'cygwin'):
        base = os.environ.get('APPDATA') or os.path.expanduser('~')
    elif sys.platform == 'darwin':
        base = os.path.expanduser('~/Library/Application Support')
    else:
        base = os.environ.get('XDG_CONFIG_HOME') or os.path.expanduser('~/.config')
    return os.path.join(base, 'yt-dlp-gui', 'settings.json')


def default_download_dir() -> str:
    downloads = os.path.join(os.path.expanduser('~'), 'Downloads')
    return downloads if os.path.isdir(downloads) else os.path.expanduser('~')


class Settings:
    def __init__(self, path: str | None = None):
        self.path = path or default_settings_path()
        self._lock = threading.Lock()
        self.data: dict = {}
        try:
            with open(self.path, encoding='utf-8') as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                self.data = loaded
        except (OSError, ValueError):
            pass

    def get(self, key, default=None):
        with self._lock:
            return self.data.get(key, default)

    def set(self, key, value):
        with self._lock:
            self.data[key] = value
        self.save()

    def save(self):
        with self._lock:
            payload = json.dumps(self.data, indent=2)
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                f.write(payload)
            os.replace(tmp, self.path)
        except OSError:
            pass  # Settings are a convenience; never let them break a download.

    # Which browser profile worked last time for a given site.
    def remembered_cookie_profile(self, domain: str) -> dict | None:
        return (self.get('cookie_profiles') or {}).get(domain)

    def remember_cookie_profile(self, domain: str, profile: dict | None):
        with self._lock:
            profiles = dict(self.data.get('cookie_profiles') or {})
            if profile is None:
                profiles.pop(domain, None)
            else:
                profiles[domain] = profile
            self.data['cookie_profiles'] = profiles
        self.save()
