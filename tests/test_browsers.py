import json
import os

from yt_dlp_gui.browsers import detect_browser_profiles, find_profile


def touch(path, mtime=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, 'wb').close()
    if mtime is not None:
        os.utime(path, (mtime, mtime))


def test_linux_profiles_are_found_and_sorted_by_last_use(tmp_path):
    home = str(tmp_path)
    ff_root = tmp_path / '.mozilla' / 'firefox'
    touch(str(ff_root / 'abc123.default-release' / 'cookies.sqlite'), 1000)
    (ff_root / 'profiles.ini').write_text('[Profile0]\nName=Work\nPath=abc123.default-release\n')
    chrome = tmp_path / '.config' / 'google-chrome'
    touch(str(chrome / 'Default' / 'Network' / 'Cookies'), 3000)
    touch(str(chrome / 'Profile 1' / 'Cookies'), 2000)
    (chrome / 'Local State').write_text(json.dumps(
        {'profile': {'info_cache': {'Default': {'name': 'Me'}, 'Profile 1': {'name': 'Gaming'}}}}))
    touch(str(tmp_path / 'snap' / 'chromium' / 'common' / 'chromium' / 'Default' / 'Cookies'), 500)

    profiles = detect_browser_profiles('linux', home, {})

    assert [p.label for p in profiles] == ['Chrome — Me', 'Chrome — Gaming', 'Firefox — Work', 'Chromium — Default']
    assert profiles[0].profile_path == str(chrome / 'Default')


def test_firefox_name_falls_back_to_folder_suffix(tmp_path):
    touch(str(tmp_path / '.mozilla' / 'firefox' / 'x9.dev-edition-default' / 'cookies.sqlite'))
    [profile] = detect_browser_profiles('linux', str(tmp_path), {})
    assert profile.label == 'Firefox — dev-edition-default'


def test_windows_paths_use_appdata(tmp_path):
    local, roaming = tmp_path / 'Local', tmp_path / 'Roaming'
    touch(str(local / 'Microsoft' / 'Edge' / 'User Data' / 'Default' / 'Network' / 'Cookies'))
    touch(str(roaming / 'Opera Software' / 'Opera Stable' / 'Network' / 'Cookies'))
    profiles = detect_browser_profiles('win32', str(tmp_path), {'LOCALAPPDATA': str(local), 'APPDATA': str(roaming)})
    assert sorted(p.browser for p in profiles) == ['edge', 'opera']
    opera = next(p for p in profiles if p.browser == 'opera')
    assert opera.profile_path == str(roaming / 'Opera Software' / 'Opera Stable')
    assert opera.label == 'Opera'


def test_no_browsers(tmp_path):
    assert detect_browser_profiles('linux', str(tmp_path), {}) == []


def test_find_profile_round_trip(tmp_path):
    touch(str(tmp_path / '.mozilla' / 'firefox' / 'a.default' / 'cookies.sqlite'))
    profiles = detect_browser_profiles('linux', str(tmp_path), {})
    assert find_profile(profiles, profiles[0].to_dict()) == profiles[0]
    assert find_profile(profiles, {'browser': 'chrome', 'profile_path': 'nope'}) is None
    assert find_profile(profiles, None) is None
