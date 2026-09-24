"""Tkinter front end."""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from . import __version__
from .browsers import BrowserProfile, detect_browser_profiles, find_profile
from .downloader import PRESETS, PRESETS_BY_KEY, CookieChoice, DownloadJob, Downloader, ffmpeg_available
from .settings import Settings, default_download_dir

AUTO_LABEL = 'Automatic (recommended)'
NONE_LABEL = "Don't use cookies"
FILE_LABEL = 'cookies.txt file…'


class App:
    def __init__(self, root: tk.Tk, settings: Settings | None = None):
        self.root = root
        self.settings = settings or Settings()
        self.events: queue.Queue = queue.Queue()
        self.worker: threading.Thread | None = None
        self.downloader: Downloader | None = None
        self.profiles: list[BrowserProfile] = []
        self.cookie_file: str | None = self.settings.get('cookie_file')

        root.title('yt-dlp GUI')
        root.minsize(640, 480)
        self._build()
        self.refresh_browsers()
        self._restore()
        root.protocol('WM_DELETE_WINDOW', self.on_close)
        root.after(100, self._drain_events)

    # -- layout --------------------------------------------------------------------------------

    def _build(self):
        pad = {'padx': 8, 'pady': 4}
        main = ttk.Frame(self.root, padding=8)
        main.pack(fill='both', expand=True)
        main.columnconfigure(1, weight=1)

        ttk.Label(main, text='Video URL(s):').grid(row=0, column=0, sticky='nw', **pad)
        url_frame = ttk.Frame(main)
        url_frame.grid(row=0, column=1, sticky='ew', **pad)
        url_frame.columnconfigure(0, weight=1)
        self.url_text = tk.Text(url_frame, height=3, wrap='none', undo=True)
        self.url_text.grid(row=0, column=0, sticky='ew')
        ttk.Button(url_frame, text='Paste', command=self.paste_url).grid(row=0, column=1, sticky='n', padx=(6, 0))
        ttk.Label(main, text='One link per line. Playlists and channels work too.',
                  foreground='gray').grid(row=1, column=1, sticky='w', padx=8)

        ttk.Label(main, text='Save to:').grid(row=2, column=0, sticky='w', **pad)
        dir_frame = ttk.Frame(main)
        dir_frame.grid(row=2, column=1, sticky='ew', **pad)
        dir_frame.columnconfigure(0, weight=1)
        self.output_var = tk.StringVar()
        ttk.Entry(dir_frame, textvariable=self.output_var).grid(row=0, column=0, sticky='ew')
        ttk.Button(dir_frame, text='Browse…', command=self.choose_dir).grid(row=0, column=1, padx=(6, 0))
        ttk.Button(dir_frame, text='Open', command=self.open_dir).grid(row=0, column=2, padx=(6, 0))

        ttk.Label(main, text='Format:').grid(row=3, column=0, sticky='w', **pad)
        fmt_frame = ttk.Frame(main)
        fmt_frame.grid(row=3, column=1, sticky='ew', **pad)
        self.preset_var = tk.StringVar()
        ttk.Combobox(fmt_frame, textvariable=self.preset_var, state='readonly', width=32,
                     values=[p.label for p in PRESETS]).pack(side='left')
        self.playlist_var = tk.BooleanVar()
        ttk.Checkbutton(fmt_frame, text='Download entire playlist',
                        variable=self.playlist_var).pack(side='left', padx=(12, 0))

        ttk.Label(main, text='Cookies:').grid(row=4, column=0, sticky='w', **pad)
        cookie_frame = ttk.Frame(main)
        cookie_frame.grid(row=4, column=1, sticky='ew', **pad)
        cookie_frame.columnconfigure(0, weight=1)
        main.bind('<Configure>', lambda e: self.cookie_info.configure(wraplength=max(300, e.width - 140)))
        self.cookie_var = tk.StringVar()
        self.cookie_box = ttk.Combobox(cookie_frame, textvariable=self.cookie_var, state='readonly')
        self.cookie_box.grid(row=0, column=0, sticky='ew')
        self.cookie_box.bind('<<ComboboxSelected>>', self.on_cookie_selected)
        ttk.Button(cookie_frame, text='Re-detect browsers',
                   command=self.refresh_browsers).grid(row=0, column=1, padx=(6, 0))
        self.cookie_info = ttk.Label(main, foreground='gray', wraplength=560, justify='left')
        self.cookie_info.grid(row=5, column=1, sticky='w', padx=8)

        buttons = ttk.Frame(main)
        buttons.grid(row=6, column=0, columnspan=2, sticky='ew', **pad)
        self.download_btn = ttk.Button(buttons, text='Download', command=self.start_download)
        self.download_btn.pack(side='left')
        self.cancel_btn = ttk.Button(buttons, text='Cancel', command=self.cancel_download, state='disabled')
        self.cancel_btn.pack(side='left', padx=(6, 0))
        self.status_var = tk.StringVar(value='Ready')
        ttk.Label(buttons, textvariable=self.status_var).pack(side='left', padx=(12, 0))

        self.progress = ttk.Progressbar(main, maximum=1.0)
        self.progress.grid(row=7, column=0, columnspan=2, sticky='ew', **pad)
        self.progress_var = tk.StringVar()
        ttk.Label(main, textvariable=self.progress_var).grid(row=8, column=0, columnspan=2, sticky='w', padx=8)

        self.log = ScrolledText(main, height=12, state='disabled', wrap='word')
        self.log.grid(row=9, column=0, columnspan=2, sticky='nsew', **pad)
        self.log.tag_configure('warning', foreground='#b36b00')
        self.log.tag_configure('error', foreground='#c62828')
        main.rowconfigure(9, weight=1)

        self.url_text.bind('<Control-Return>', lambda e: (self.start_download(), 'break')[1])
        self.url_text.focus_set()

    def _restore(self):
        self.output_var.set(self.settings.get('output_dir') or default_download_dir())
        preset = PRESETS_BY_KEY.get(self.settings.get('preset'), PRESETS[0])
        self.preset_var.set(preset.label)
        self.playlist_var.set(bool(self.settings.get('playlist', False)))
        mode = self.settings.get('cookie_mode', 'auto')
        if mode == 'none':
            self.cookie_var.set(NONE_LABEL)
        elif mode == 'file' and self.cookie_file and os.path.isfile(self.cookie_file):
            self.cookie_var.set(self._file_label())
        elif mode == 'browser' and (profile := find_profile(self.profiles, self.settings.get('cookie_profile'))):
            self.cookie_var.set(profile.label)
        else:
            self.cookie_var.set(AUTO_LABEL)
        self._update_cookie_info()
        if not ffmpeg_available():
            self.append_log('warning', 'FFmpeg was not found. Downloads will still work, but separate video and '
                            'audio streams cannot be merged and MP3 conversion is unavailable. '
                            'Get it from https://ffmpeg.org/download.html')

    def _save(self):
        choice = self.cookie_choice()
        self.settings.data.update({
            'output_dir': self.output_var.get(),
            'preset': self._preset_key(),
            'playlist': self.playlist_var.get(),
            'cookie_mode': choice.mode,
            'cookie_profile': choice.profile.to_dict() if choice.profile else None,
            'cookie_file': self.cookie_file,
        })
        self.settings.save()

    # -- cookies -------------------------------------------------------------------------------

    def _file_label(self) -> str:
        return f'cookies.txt: {os.path.basename(self.cookie_file or "")}'

    def refresh_browsers(self):
        current = self.cookie_var.get()
        self.profiles = detect_browser_profiles()
        values = [AUTO_LABEL, NONE_LABEL] + [p.label for p in self.profiles]
        if self.cookie_file:
            values.append(self._file_label())
        values.append(FILE_LABEL)
        self.cookie_box['values'] = values
        if current and current not in values:
            self.cookie_var.set(AUTO_LABEL)
        self._update_cookie_info()

    def _update_cookie_info(self):
        if self.cookie_var.get() == AUTO_LABEL:
            if self.profiles:
                names = sorted({p.label.split(' — ')[0] for p in self.profiles})
                text = (f'Tries without cookies first, then your browsers if the site asks you to sign in. '
                        f'Found: {", ".join(names)}.')
            else:
                text = 'No browsers with saved cookies found; sign-in-only videos will need a cookies.txt file.'
        elif self.cookie_var.get() == NONE_LABEL:
            text = 'Videos that need a signed-in account will fail.'
        else:
            text = 'Tip: if reading cookies fails, close that browser completely and try again.'
        self.cookie_info.configure(text=text)

    def on_cookie_selected(self, _event=None):
        if self.cookie_var.get() == FILE_LABEL:
            path = filedialog.askopenfilename(
                title='Choose a cookies.txt file (Netscape format)',
                filetypes=[('Cookie files', '*.txt'), ('All files', '*.*')])
            if path:
                self.cookie_file = path
                self.refresh_browsers()
                self.cookie_var.set(self._file_label())
            else:
                self.cookie_var.set(AUTO_LABEL)
        self._update_cookie_info()

    def cookie_choice(self) -> CookieChoice:
        label = self.cookie_var.get()
        if label == NONE_LABEL:
            return CookieChoice('none')
        if self.cookie_file and label == self._file_label():
            return CookieChoice('file', file=self.cookie_file)
        for profile in self.profiles:
            if profile.label == label:
                return CookieChoice('browser', profile=profile)
        return CookieChoice('auto')

    # -- actions -------------------------------------------------------------------------------

    def _preset_key(self) -> str:
        for preset in PRESETS:
            if preset.label == self.preset_var.get():
                return preset.key
        return PRESETS[0].key

    def paste_url(self):
        try:
            text = self.root.clipboard_get().strip()
        except tk.TclError:
            return
        if text:
            existing = self.url_text.get('1.0', 'end').strip()
            self.url_text.insert('end', ('\n' if existing else '') + text)

    def choose_dir(self):
        path = filedialog.askdirectory(initialdir=self.output_var.get() or default_download_dir())
        if path:
            self.output_var.set(path)

    def open_dir(self):
        path = self.output_var.get()
        if not os.path.isdir(path):
            return
        try:
            if sys.platform == 'win32':
                os.startfile(path)  # noqa: S606 - opening a folder the user chose
            elif sys.platform == 'darwin':
                subprocess.Popen(['open', path])
            else:
                subprocess.Popen(['xdg-open', path])
        except OSError as e:
            messagebox.showerror('Could not open folder', str(e))

    def start_download(self):
        if self.worker and self.worker.is_alive():
            return
        urls = [line.strip() for line in self.url_text.get('1.0', 'end').splitlines() if line.strip()]
        if not urls:
            messagebox.showinfo('No URL', 'Paste a video link first.')
            return
        output_dir = self.output_var.get().strip() or default_download_dir()
        try:
            os.makedirs(output_dir, exist_ok=True)
        except OSError as e:
            messagebox.showerror('Cannot use folder', f'{output_dir}\n\n{e}')
            return
        self._save()

        job = DownloadJob(urls=urls, output_dir=output_dir, preset=self._preset_key(),
                          cookies=self.cookie_choice(), playlist=self.playlist_var.get())
        self.downloader = Downloader(self._post_event, self.settings)
        self.worker = threading.Thread(target=self.downloader.run, args=(job,), daemon=True)
        self.download_btn.configure(state='disabled')
        self.cancel_btn.configure(state='normal')
        self.progress.configure(mode='determinate', value=0)
        self.progress_var.set('')
        self.append_log('info', f'Starting {len(urls)} download(s) into {output_dir}')
        self.worker.start()

    def cancel_download(self):
        if self.downloader:
            self.downloader.cancel()
            self.status_var.set('Cancelling…')
            self.cancel_btn.configure(state='disabled')

    def on_close(self):
        if self.worker and self.worker.is_alive():
            if not messagebox.askyesno('Quit?', 'A download is still running. Stop it and quit?'):
                return
            self.downloader.cancel()
        self._save()
        self.root.destroy()

    # -- worker → UI events ---------------------------------------------------------------------

    def _post_event(self, kind, *args):
        self.events.put((kind, args))

    def _drain_events(self):
        try:
            while True:
                kind, args = self.events.get_nowait()
                self._handle_event(kind, args)
        except queue.Empty:
            pass
        self.root.after(100, self._drain_events)

    def _handle_event(self, kind, args):
        if kind == 'log':
            self.append_log(*args)
        elif kind == 'status':
            self.status_var.set(args[0])
        elif kind == 'progress':
            fraction, text = args
            if fraction is None:
                if str(self.progress['mode']) != 'indeterminate':
                    self.progress.configure(mode='indeterminate')
                    self.progress.start(15)
            else:
                if str(self.progress['mode']) != 'determinate':
                    self.progress.stop()
                    self.progress.configure(mode='determinate')
                self.progress.configure(value=fraction)
            self.progress_var.set(text)
        elif kind == 'cookies':
            self.cookie_info.configure(text=args[0])
        elif kind == 'needs_login':
            title, message = args
            messagebox.showwarning(title, message, parent=self.root)
        elif kind == 'done':
            succeeded, failed = args
            self.progress.stop()
            self.progress.configure(mode='determinate', value=1.0 if succeeded and not failed else 0)
            self.download_btn.configure(state='normal')
            self.cancel_btn.configure(state='disabled')
            if self.status_var.get() != 'Cancelled':
                summary = f'Done: {succeeded} succeeded' + (f', {failed} failed' if failed else '')
                self.status_var.set(summary)
                self.append_log('error' if failed else 'info', summary)

    def append_log(self, level, text):
        self.log.configure(state='normal')
        self.log.insert('end', text.rstrip() + '\n', (level,) if level in ('warning', 'error') else ())
        self.log.see('end')
        self.log.configure(state='disabled')


def main():
    try:
        import yt_dlp  # noqa: F401
    except ImportError:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror('yt-dlp is missing', 'Install it with:\n\n    pip install -U yt-dlp')
        return
    root = tk.Tk()
    try:
        ttk.Style(root).theme_use('clam' if sys.platform.startswith('linux') else ttk.Style(root).theme_use())
    except tk.TclError:
        pass
    App(root)
    root.title(f'yt-dlp GUI {__version__}')
    root.mainloop()


if __name__ == '__main__':
    main()
