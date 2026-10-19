import os
import threading
import tkinter as tk
from tkinter import filedialog, messagebox
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError, DownloadCancelled

import customtkinter as ctk

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")
ctk.ThemeManager.theme["CTkFont"]["family"] = "Segoe UI"

BG = "#0B0D14"
SURFACE = "#141824"
SURFACE_ALT = "#1B2132"
BORDER = "#262D42"
VIDEO = "#7C5CFF"
VIDEO_HOVER = "#6849F0"
AUDIO = "#F97316"
AUDIO_HOVER = "#EA580C"
CYAN = "#22D3EE"
TEXT = "#EDEFF7"
MUTED = "#8A93AD"
DANGER = "#F0506E"
DANGER_HOVER = "#D13A58"
SUCCESS = "#34D399"
WARN = "#FBBF24"


class YTDownloaderApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Streamgrab")
        self.root.geometry("720x650")
        self.root.minsize(640, 560)
        self.root.configure(fg_color=BG)

        self.download_dir = os.path.join(os.getcwd(), "Downloads")
        os.makedirs(self.download_dir, exist_ok=True)

        self.downloading = False
        self.cancel_event = threading.Event()
        self._estimate_job = None
        self._estimate_token = 0

        self.video_qualities = [
            "Best / 1080p Max",
            "720p",
            "480p",
            "360p",
            "Highest Available (4K/1440p)",
        ]
        self.audio_qualities = [
            "320 kbps (Best)",
            "256 kbps",
            "192 kbps",
            "128 kbps",
        ]

        self.format_var = tk.StringVar(value="MP4 (Video)")
        self.quality_var = tk.StringVar(value=self.video_qualities[0])

        self._is_fullscreen = False
        self._last_size = (0, 0)

        self._setup_ui()
        self._apply_mode()

        self.root.bind("<F11>", self._toggle_fullscreen)
        self.root.bind("<Escape>", self._exit_fullscreen)
        self.root.bind("<Configure>", self._on_resize)
        # self.root.after(50, self._maximize) # Auto Full Screen on Startup

    def _maximize(self):
        try:
            self.root.state("zoomed")
        except tk.TclError:
            try:
                self.root.attributes("-zoomed", True)
            except tk.TclError:
                w, h = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
                self.root.geometry(f"{w}x{h}+0+0")

    def _toggle_fullscreen(self, _event=None):
        self._is_fullscreen = not self._is_fullscreen
        self.root.attributes("-fullscreen", self._is_fullscreen)
        if not self._is_fullscreen:
            self.root.after(50, self._maximize)

    def _exit_fullscreen(self, _event=None):
        if self._is_fullscreen:
            self._toggle_fullscreen()

    def _on_resize(self, event):
        if event.widget is not self.root:
            return
        size = (event.width, event.height)
        if size == self._last_size:
            return
        self._last_size = size
        width, height = size
        value_size = min(15 + max(0, width - 720) // 60, 26)
        pad = min(8 + max(0, height - 640) // 22, 34)
        self.stat_font.configure(size=value_size)
        for inner in self._stat_inners:
            inner.pack_configure(pady=pad)

    def _card(self, parent, **grid):
        card = ctk.CTkFrame(
            parent,
            height=1,
            fg_color=SURFACE,
            corner_radius=16,
            border_width=1,
            border_color=BORDER,
        )
        card.grid(sticky="ew", **grid)
        return card

    def _caption(self, parent, text):
        return ctk.CTkLabel(
            parent,
            text=text.upper(),
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=MUTED,
        )

    def _stat_tile(self, parent, column, title, accent):
        tile = ctk.CTkFrame(parent, height=1, fg_color=SURFACE_ALT, corner_radius=12)
        tile.grid(
            row=0,
            column=column,
            sticky="nsew",
            padx=(0 if column == 0 else 5, 0 if column == 2 else 5),
        )
        bar = ctk.CTkFrame(tile, fg_color=accent, width=3, height=1, corner_radius=2)
        bar.pack(side="left", fill="y", padx=(10, 0), pady=10)
        inner = ctk.CTkFrame(tile, fg_color="transparent", height=1)
        inner.pack(side="left", fill="both", expand=True, padx=10, pady=8)
        self._stat_inners.append(inner)
        self._caption(inner, title).pack(anchor="w")
        value = ctk.CTkLabel(
            inner,
            text="—",
            font=self.stat_font,
            text_color=TEXT,
            anchor="w",
        )
        value.pack(anchor="w")
        return value

    def _setup_ui(self):
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        page = ctk.CTkFrame(self.root, fg_color="transparent")
        page.grid(row=0, column=0, sticky="nsew", padx=22, pady=16)
        page.columnconfigure(0, weight=1)
        page.rowconfigure(4, weight=1)

        header = ctk.CTkFrame(page, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        header.columnconfigure(1, weight=1)

        self.badge = ctk.CTkFrame(header, fg_color=VIDEO, width=52, height=52, corner_radius=14)
        self.badge.grid(row=0, column=0, rowspan=2, padx=(0, 12))
        self.badge.grid_propagate(False)
        self.icon_canvas = tk.Canvas(
            self.badge, width=36, height=36, bg=VIDEO, highlightthickness=0, bd=0
        )
        self.icon_canvas.place(relx=0.5, rely=0.5, anchor="center")

        ctk.CTkLabel(
            header,
            text="Streamgrab",
            font=ctk.CTkFont(size=22, weight="bold"),
            text_color=TEXT,
            anchor="w",
        ).grid(row=0, column=1, sticky="w")
        ctk.CTkLabel(
            header,
            text="Download YouTube video or audio in a few clicks",
            font=ctk.CTkFont(size=11),
            text_color=MUTED,
            anchor="w",
        ).grid(row=1, column=1, sticky="w")

        source = self._card(page, row=1, column=0, pady=(0, 10))
        source.columnconfigure(0, weight=1)
        self._caption(source, "Video link").grid(row=0, column=0, sticky="w", padx=16, pady=(12, 5))

        url_row = ctk.CTkFrame(source, fg_color="transparent", height=38)
        url_row.grid(row=1, column=0, sticky="ew", padx=16)
        url_row.columnconfigure(0, weight=1)

        self.url_entry = ctk.CTkEntry(
            url_row,
            placeholder_text="Paste a YouTube or YouTube Music URL",
            height=38,
            corner_radius=10,
            fg_color=SURFACE_ALT,
            border_color=BORDER,
            text_color=TEXT,
        )
        self.url_entry.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.url_entry.bind("<KeyRelease>", lambda e: self._schedule_estimate())

        ctk.CTkButton(
            url_row,
            text="Paste",
            width=66,
            height=38,
            corner_radius=10,
            fg_color=SURFACE_ALT,
            hover_color=BORDER,
            border_width=1,
            border_color=BORDER,
            text_color=TEXT,
            command=self._paste_url,
        ).grid(row=0, column=1, padx=(0, 8))
        ctk.CTkButton(
            url_row,
            text="Clear",
            width=66,
            height=38,
            corner_radius=10,
            fg_color=SURFACE_ALT,
            hover_color=DANGER,
            border_width=1,
            border_color=BORDER,
            text_color=TEXT,
            command=self._clear_url,
        ).grid(row=0, column=2)

        estimate_row = ctk.CTkFrame(source, fg_color="transparent", height=20)
        estimate_row.grid(row=2, column=0, sticky="ew", padx=16, pady=(8, 12))
        ctk.CTkLabel(
            estimate_row,
            text="Estimated size:",
            font=ctk.CTkFont(size=12),
            text_color=MUTED,
        ).pack(side="left")
        self.estimate_value = ctk.CTkLabel(
            estimate_row,
            text="—",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=CYAN,
        )
        self.estimate_value.pack(side="left", padx=(6, 8))
        self.estimate_info = ctk.CTkLabel(
            estimate_row,
            text="Paste a link to see the file size",
            font=ctk.CTkFont(size=12),
            text_color=MUTED,
            anchor="w",
        )
        self.estimate_info.pack(side="left", fill="x", expand=True)

        options = self._card(page, row=2, column=0, pady=(0, 10))
        options.columnconfigure(0, weight=1)
        options.columnconfigure(1, weight=1)

        self._caption(options, "Format").grid(row=0, column=0, sticky="w", padx=16, pady=(12, 5))
        self._caption(options, "Quality").grid(row=0, column=1, sticky="w", padx=16, pady=(12, 5))

        toggle = ctk.CTkFrame(options, fg_color="transparent", height=38)
        toggle.grid(row=1, column=0, sticky="ew", padx=16)
        toggle.columnconfigure(0, weight=1, uniform="fmt")
        toggle.columnconfigure(1, weight=1, uniform="fmt")

        self.video_btn = ctk.CTkButton(
            toggle,
            text="MP4 · Video",
            height=38,
            corner_radius=10,
            border_width=1,
            font=ctk.CTkFont(size=13, weight="bold"),
            command=lambda: self._select_format("MP4 (Video)"),
        )
        self.video_btn.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self.audio_btn = ctk.CTkButton(
            toggle,
            text="MP3 · Audio",
            height=38,
            corner_radius=10,
            border_width=1,
            font=ctk.CTkFont(size=13, weight="bold"),
            command=lambda: self._select_format("MP3 (Audio)"),
        )
        self.audio_btn.grid(row=0, column=1, sticky="ew", padx=(4, 0))

        self.quality_dropdown = ctk.CTkOptionMenu(
            options,
            values=self.video_qualities,
            variable=self.quality_var,
            command=lambda _: self._schedule_estimate(),
            height=38,
            corner_radius=10,
            fg_color=SURFACE_ALT,
            button_color=BORDER,
            dropdown_fg_color=SURFACE_ALT,
            text_color=TEXT,
            anchor="w",
        )
        self.quality_dropdown.grid(row=1, column=1, sticky="ew", padx=16)

        self._caption(options, "Save to").grid(row=2, column=0, columnspan=2, sticky="w", padx=16, pady=(12, 5))

        dest_row = ctk.CTkFrame(options, fg_color="transparent", height=36)
        dest_row.grid(row=3, column=0, columnspan=2, sticky="ew", padx=16, pady=(0, 14))
        dest_row.columnconfigure(0, weight=1)

        self.folder_label = ctk.CTkEntry(
            dest_row,
            height=36,
            corner_radius=10,
            fg_color=SURFACE_ALT,
            border_color=BORDER,
            text_color=MUTED,
        )
        self.folder_label.insert(0, self.download_dir)
        self.folder_label.configure(state="disabled")
        self.folder_label.grid(row=0, column=0, sticky="ew", padx=(0, 8))

        ctk.CTkButton(
            dest_row,
            text="Browse",
            width=90,
            height=36,
            corner_radius=10,
            fg_color=SURFACE_ALT,
            hover_color=BORDER,
            border_width=1,
            border_color=BORDER,
            text_color=TEXT,
            command=self._browse_folder,
        ).grid(row=0, column=1)

        button_row = ctk.CTkFrame(page, fg_color="transparent", height=46)
        button_row.grid(row=3, column=0, sticky="ew", pady=(0, 10))
        button_row.columnconfigure(0, weight=1, uniform="btns")
        button_row.columnconfigure(1, weight=1, uniform="btns")

        self.download_btn = ctk.CTkButton(
            button_row,
            text="Download",
            font=ctk.CTkFont(size=15, weight="bold"),
            height=46,
            corner_radius=14,
            text_color_disabled="#D5D8E6",
            command=self._start_download_thread,
        )
        self.download_btn.grid(row=0, column=0, sticky="ew", padx=(0, 5))

        self.cancel_btn = ctk.CTkButton(
            button_row,
            text="Cancel",
            font=ctk.CTkFont(size=15, weight="bold"),
            height=46,
            corner_radius=14,
            text_color_disabled="#F6D3DB",
            command=self._cancel_download,
        )
        self.cancel_btn.grid(row=0, column=1, sticky="ew", padx=(5, 0))
        self._set_cancel_idle()

        progress_card = self._card(page, row=4, column=0)
        progress_card.grid_configure(sticky="new")
        progress_card.columnconfigure(0, weight=1)

        top = ctk.CTkFrame(progress_card, fg_color="transparent", height=20)
        top.grid(row=0, column=0, sticky="ew", padx=16, pady=(12, 6))
        top.columnconfigure(0, weight=1)

        self.status_label = ctk.CTkLabel(
            top,
            text="Ready",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=TEXT,
            anchor="w",
        )
        self.status_label.grid(row=0, column=0, sticky="w")

        self.percent_label = ctk.CTkLabel(
            top,
            text="0%",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=CYAN,
        )
        self.percent_label.grid(row=0, column=1, sticky="e")

        self.progress_bar = ctk.CTkProgressBar(
            progress_card,
            height=8,
            corner_radius=4,
            fg_color=SURFACE_ALT,
        )
        self.progress_bar.set(0.0)
        self.progress_bar.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 10))

        self.stat_font = ctk.CTkFont(size=15, weight="bold")
        self._stat_inners = []

        stats = ctk.CTkFrame(progress_card, height=1, fg_color="transparent")
        stats.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 14))
        stats.rowconfigure(0, weight=1)
        for col in range(3):
            stats.columnconfigure(col, weight=1, uniform="stat")

        self.downloaded_value = self._stat_tile(stats, 0, "Downloaded", VIDEO)
        self.remaining_value = self._stat_tile(stats, 1, "Remaining", CYAN)
        self.speed_value = self._stat_tile(stats, 2, "Speed · ETA", SUCCESS)

    def _ui(self, func, *args):
        self.root.after(0, lambda: func(*args))

    def _mode_colors(self):
        if "MP3" in self.format_var.get():
            return AUDIO, AUDIO_HOVER
        return VIDEO, VIDEO_HOVER

    def _apply_mode(self):
        color, hover = self._mode_colors()
        is_audio = "MP3" in self.format_var.get()

        self._style_toggle(self.video_btn, VIDEO, VIDEO_HOVER, not is_audio)
        self._style_toggle(self.audio_btn, AUDIO, AUDIO_HOVER, is_audio)

        self.download_btn.configure(fg_color=color, hover_color=hover)
        self.progress_bar.configure(progress_color=color)
        self.badge.configure(fg_color=color)
        self._draw_icon(is_audio, color)
        self.quality_dropdown.configure(button_hover_color=color, dropdown_hover_color=color)

    def _draw_icon(self, is_audio, bg):
        c = self.icon_canvas
        c.configure(bg=bg)
        c.delete("all")
        w = dict(fill="white", outline="white")
        line = dict(fill="white", width=2.4, capstyle="round", joinstyle="round")
        if is_audio:
            c.create_oval(4, 17, 12, 25, **w)
            c.create_oval(17, 14, 25, 22, **w)
            c.create_line(11, 21, 11, 5, 24, 3, 24, 18, **line)
            c.create_line(11, 10, 24, 8, **line)
        else:
            c.create_polygon(
                5, 4, 23, 4, 24, 5, 24, 23, 23, 24, 5, 24, 4, 23, 4, 5,
                fill="", outline="white", width=2.4, joinstyle="round",
            )
            c.create_line(9, 4, 9, 24, **line)
            c.create_line(19, 4, 19, 24, **line)
            for y in (9, 14, 19):
                c.create_line(4, y, 9, y, **line)
                c.create_line(19, y, 24, y, **line)
        c.scale("all", 0, 0, 36 / 28, 36 / 28)

    def _set_cancel_idle(self):
        self.cancel_btn.configure(
            state="disabled",
            text="Cancel",
            fg_color="#2A1E2C",
            text_color_disabled="#7C5E6B",
        )

    def _set_cancel_active(self):
        self.cancel_btn.configure(
            state="normal",
            text="Cancel",
            fg_color=DANGER,
            hover_color=DANGER_HOVER,
        )

    def _style_toggle(self, button, color, hover, active):
        if active:
            button.configure(
                fg_color=color,
                hover_color=hover,
                border_color=color,
                text_color="white",
            )
        else:
            button.configure(
                fg_color=SURFACE_ALT,
                hover_color=BORDER,
                border_color=color,
                text_color=color,
            )

    def _select_format(self, selected_format):
        if self.downloading:
            return
        self.format_var.set(selected_format)
        options = self.audio_qualities if "MP3" in selected_format else self.video_qualities
        self.quality_var.set(options[0])
        self.quality_dropdown.configure(values=options)
        self._apply_mode()
        self._schedule_estimate()

    def _set_status(self, text, color=TEXT):
        self.status_label.configure(text=text, text_color=color)

    def _paste_url(self):
        try:
            clipboard_text = self.root.clipboard_get()
            self.url_entry.delete(0, tk.END)
            self.url_entry.insert(0, clipboard_text.strip())
        except tk.TclError:
            return
        self._schedule_estimate()

    def _clear_url(self):
        self.url_entry.delete(0, tk.END)
        self._schedule_estimate()

    def _browse_folder(self):
        selected_dir = filedialog.askdirectory(initialdir=self.download_dir)
        if selected_dir:
            self.download_dir = selected_dir
            self.folder_label.configure(state="normal")
            self.folder_label.delete(0, tk.END)
            self.folder_label.insert(0, self.download_dir)
            self.folder_label.configure(state="disabled")

    def _format_size(self, size_bytes):
        if size_bytes is None:
            return "Unknown"
        size_bytes = float(size_bytes)
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if size_bytes < 1024 or unit == "TB":
                return f"{size_bytes:.2f} {unit}"
            size_bytes /= 1024

    def _format_eta(self, seconds):
        if seconds is None:
            return "--"
        seconds = int(seconds)
        minutes, secs = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours}h {minutes:02d}m"
        if minutes:
            return f"{minutes}m {secs:02d}s"
        return f"{secs}s"

    def _format_duration(self, seconds):
        if not seconds:
            return ""
        seconds = int(seconds)
        minutes, secs = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours}:{minutes:02d}:{secs:02d}"
        return f"{minutes}:{secs:02d}"

    def _video_format_string(self, quality):
        if "1080p" in quality:
            return "bestvideo[height<=1080]+bestaudio/best"
        if "720p" in quality:
            return "bestvideo[height<=720]+bestaudio/best"
        if "480p" in quality:
            return "bestvideo[height<=480]+bestaudio/best"
        if "360p" in quality:
            return "bestvideo[height<=360]+bestaudio/best"
        return "bestvideo+bestaudio/best"

    def _schedule_estimate(self):
        if self._estimate_job is not None:
            self.root.after_cancel(self._estimate_job)
        self._estimate_job = self.root.after(600, self._start_estimate)

    def _start_estimate(self):
        self._estimate_job = None
        url = self.url_entry.get().strip()
        self._estimate_token += 1
        token = self._estimate_token

        if not url:
            self.estimate_value.configure(text="—")
            self.estimate_info.configure(text="Paste a link to see the file size")
            return

        if self.downloading:
            return

        self.estimate_value.configure(text="Calculating...")
        self.estimate_info.configure(text="")

        fmt = self.format_var.get()
        quality = self.quality_var.get()
        thread = threading.Thread(
            target=self._estimate_worker,
            args=(url, fmt, quality, token),
            daemon=True,
        )
        thread.start()

    def _estimate_worker(self, url, fmt, quality, token):
        is_audio = "MP3" in fmt
        opts = {
            "format": "bestaudio/best" if is_audio else self._video_format_string(quality),
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "skip_download": True,
            "js_runtimes": {"node": {}},
        }

        try:
            with YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)

            title = info.get("title") or ""
            duration = info.get("duration")

            if is_audio:
                bitrate = int(quality.split()[0])
                size = duration * bitrate * 125 if duration else None
            else:
                formats = info.get("requested_formats") or [info]
                size = self._sum_sizes(formats, duration)

            self._ui(self._show_estimate, token, size, title, duration, False)
        except Exception:
            self._ui(self._show_estimate, token, None, "", None, True)

    def _sum_sizes(self, formats, duration):
        total = 0
        for fmt in formats:
            size = fmt.get("filesize") or fmt.get("filesize_approx")
            if not size and fmt.get("tbr") and duration:
                size = fmt["tbr"] * 125 * duration
            if not size:
                return None
            total += size
        return total

    def _show_estimate(self, token, size, title, duration, failed):
        if token != self._estimate_token:
            return

        if failed:
            self.estimate_value.configure(text="Unavailable")
            self.estimate_info.configure(text="Could not read this link")
            return

        if size:
            self.estimate_value.configure(text=f"≈ {self._format_size(size)}")
        else:
            self.estimate_value.configure(text="Unknown")

        short_title = title if len(title) <= 46 else title[:43] + "..."
        parts = [p for p in (self._format_duration(duration), short_title) if p]
        self.estimate_info.configure(text="  ·  ".join(parts))

    def _reset_stats(self):
        self.progress_bar.set(0.0)
        self.percent_label.configure(text="0%")
        self.downloaded_value.configure(text="—")
        self.remaining_value.configure(text="—")
        self.speed_value.configure(text="—")

    def _progress_hook(self, d):
        status = d.get("status")

        if status == "downloading":
            if self.cancel_event.is_set():
                raise DownloadCancelled()

            total_bytes = d.get("total_bytes") or d.get("total_bytes_estimate")
            downloaded_bytes = d.get("downloaded_bytes", 0) or 0
            speed = d.get("speed")
            eta = d.get("eta")

            if total_bytes and total_bytes > 0:
                fraction = min(downloaded_bytes / total_bytes, 1.0)
                remaining_bytes = max(total_bytes - downloaded_bytes, 0)
                remaining_text = self._format_size(remaining_bytes)
                percent_text = f"{fraction * 100:.1f}%"
            else:
                fraction = None
                remaining_text = "Unknown"
                percent_text = "..."

            speed_text = f"{self._format_size(speed)}/s" if speed else "--"
            speed_line = f"{speed_text} · {self._format_eta(eta)}"
            downloaded_text = self._format_size(downloaded_bytes)

            self._ui(
                self._update_progress,
                fraction,
                percent_text,
                downloaded_text,
                remaining_text,
                speed_line,
            )

        elif status == "finished":
            self._ui(self._on_stream_finished, d.get("downloaded_bytes", 0))

    def _update_progress(self, fraction, percent_text, downloaded_text, remaining_text, speed_line):
        if not self.downloading:
            return
        if fraction is not None:
            self.progress_bar.set(fraction)
        self.percent_label.configure(text=percent_text)
        if not self.cancel_event.is_set():
            self._set_status("Downloading")
        self.downloaded_value.configure(text=downloaded_text)
        self.remaining_value.configure(text=remaining_text)
        self.speed_value.configure(text=speed_line)

    def _on_stream_finished(self, downloaded_bytes):
        if not self.downloading:
            return
        self.progress_bar.set(1.0)
        self.percent_label.configure(text="100%")
        self._set_status("Processing file...")
        self.downloaded_value.configure(text=self._format_size(downloaded_bytes))
        self.remaining_value.configure(text="0.00 B")
        self.speed_value.configure(text="—")

    def _start_download_thread(self):
        url = self.url_entry.get().strip()

        if not url:
            self._set_status("Paste a video link first", DANGER)
            return

        if self.downloading:
            return

        self.downloading = True
        self.cancel_event.clear()
        self.download_btn.configure(state="disabled", text="Downloading...")
        self._set_cancel_active()
        self._set_status("Starting download...")
        self._reset_stats()

        thread = threading.Thread(target=self._run_download, args=(url,), daemon=True)
        thread.start()

    def _cancel_download(self):
        if not self.downloading or self.cancel_event.is_set():
            return
        if not messagebox.askyesno(
            "Cancel download?",
            "Are you sure you want to cancel the current download?",
            parent=self.root,
        ):
            return
        if not self.downloading or self.cancel_event.is_set():
            return
        self.cancel_event.set()
        self.cancel_btn.configure(state="disabled", text="Cancelling...")
        self._set_status("Cancelling...", WARN)

    def _run_download(self, url):
        fmt = self.format_var.get()
        quality = self.quality_var.get()
        download_dir = self.download_dir

        ydl_opts = {
            "outtmpl": os.path.join(download_dir, "%(title)s.%(ext)s"),
            "progress_hooks": [self._progress_hook],
            "noplaylist": True,
            "js_runtimes": {"node": {}},
        }

        if "MP3" in fmt:
            bitrate = quality.split()[0]
            ydl_opts.update({
                "format": "bestaudio/best",
                "postprocessors": [{
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": bitrate,
                }],
            })
        else:
            ydl_opts.update({"format": self._video_format_string(quality)})

        try:
            with YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])

            self._ui(self._on_success, download_dir)

        except DownloadCancelled:
            self._ui(self._on_cancelled)
        except DownloadError as e:
            if self.cancel_event.is_set():
                self._ui(self._on_cancelled)
            else:
                self._ui(self._on_failure, "Download failed", f"Download Error: {e}")
        except Exception as e:
            if self.cancel_event.is_set():
                self._ui(self._on_cancelled)
            else:
                self._ui(self._on_failure, "Unexpected error", f"Error: {e}")

    def _finish_state(self):
        self.downloading = False
        self.download_btn.configure(state="normal", text="Download")
        self._set_cancel_idle()

    def _on_success(self, download_dir):
        self._finish_state()
        self._set_status("Download completed", SUCCESS)
        self.progress_bar.set(1.0)
        self.percent_label.configure(text="100%")
        self.remaining_value.configure(text="0.00 B")
        self.speed_value.configure(text="—")

    def _on_cancelled(self):
        self._finish_state()
        self._set_status("Download cancelled", WARN)
        self.progress_bar.set(0.0)
        self.percent_label.configure(text="0%")
        self.remaining_value.configure(text="—")
        self.speed_value.configure(text="—")

    def _on_failure(self, status_text, log_text):
        self._finish_state()
        print(log_text)
        detail = log_text.splitlines()[0] if log_text else ""
        if len(detail) > 70:
            detail = detail[:67] + "..."
        self._set_status(f"{status_text}: {detail}" if detail else status_text, DANGER)


def main():
    root = ctk.CTk()
    YTDownloaderApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()