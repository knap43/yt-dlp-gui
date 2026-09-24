# yt-dlp-gui

A simple desktop window for [yt-dlp](https://github.com/yt-dlp/yt-dlp): paste a link, pick a format, press **Download**.

Its main trick is **automatic cookie handling**. Many videos (and, increasingly, YouTube in general) refuse to
download unless you are signed in, and yt-dlp answers with errors such as:

```
ERROR: [youtube] …: Sign in to confirm you're not a bot. Use --cookies-from-browser or --cookies for the authentication.
```

With the Cookies option left on **Automatic**, the app deals with this for you:

1. It first tries the download without any cookies (or with the browser that worked last time for that site).
2. If the site says you need to be signed in, it finds **every browser profile** on your computer — Firefox, Chrome,
   Edge, Brave, Opera, Vivaldi, Chromium, Whale and Safari, including Snap and Flatpak installs on Linux.
3. It reads each profile's cookies and checks which ones are actually **signed in** to that site
   (e.g. has YouTube's login cookies), then retries the download with the best candidate, and the next, and so on.
4. The profile that works is remembered per site, so next time it's used straight away.
5. If nothing works, it tells you exactly why — for example *"Chrome is locking its cookie file, close it
   completely"*, or *"none of your browsers are signed in to youtube.com"* — and what to do about it.

You can also pick a specific browser profile, turn cookies off, or point it to a `cookies.txt` file.

## Install and run

You need Python 3.10 or newer (with Tk, which the python.org installers include).

```sh
pip install -U yt-dlp            # or: pip install .   (installs a `yt-dlp-gui` command)
python -m yt_dlp_gui             # from this folder
```

On Windows you can also just double-click `run.pyw`. On Debian/Ubuntu, install Tk with `sudo apt install python3-tk`.

**Optional but recommended:** install [FFmpeg](https://ffmpeg.org/download.html) and make sure it is on your `PATH`.
Without it the best video and audio streams can't be merged, so you get the best *single-file* format instead,
and MP3 conversion is unavailable. For YouTube, current yt-dlp versions also want a JavaScript runtime such as
[Deno](https://deno.com/); yt-dlp will warn in the log if it is missing.

Keep yt-dlp up to date (`pip install -U yt-dlp`): sites change often, and an outdated yt-dlp is the most common
cause of failures that have nothing to do with cookies.

## Cookie tips

- **Firefox is the most reliable** source of cookies. Sign in to the site there once, and you're set.
- **Chrome / Edge on Windows** lock their cookie database while running and use "App-Bound Encryption", which
  yt-dlp often cannot read. Close the browser completely (including the tray icon) and try again, or use Firefox.
- **Linux**: Chromium-based browsers keep the cookie key in your desktop keyring, which must be unlocked.
- **macOS**: you may be asked to allow access to "Chrome Safe Storage" — choose *Always Allow*. Safari needs the app
  (or your terminal) to have *Full Disk Access*.
- Cookies are read locally and handed to yt-dlp in memory; they are sent only to the site you are downloading
  from, exactly as your browser would, and this app never saves them.
  The only thing remembered is *which* browser profile worked for which site
  (in `~/.config/yt-dlp-gui/settings.json`, `%APPDATA%\yt-dlp-gui\settings.json` on Windows, or
  `~/Library/Application Support/yt-dlp-gui/settings.json` on macOS).

## Development

```sh
pip install -e .[test]
pytest
```

The code lives in `yt_dlp_gui/`:

| File            | What it does                                                                  |
|-----------------|-------------------------------------------------------------------------------|
| `browsers.py`   | Finds browser profiles and their cookie databases on Windows, macOS and Linux |
| `cookies.py`    | Recognises "sign in required" errors, reads and ranks profiles' cookies       |
| `downloader.py` | Runs yt-dlp on a worker thread and implements the automatic retry strategy    |
| `gui.py`        | The Tkinter window                                                            |
| `settings.py`   | Remembers your folder, format and which browser worked for each site         |
