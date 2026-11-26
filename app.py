import ctypes
import json
import math
import os
import queue
import re
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox
from tkinter import font as tkfont

import customtkinter as ctk
import yt_dlp
from PIL import Image, ImageDraw

CONFIG_DIR = Path.home() / ".streamgrab"
HISTORY_FILE = CONFIG_DIR / "history.json"
HISTORY_LIMIT = 10
SETTINGS_FILE = CONFIG_DIR / "settings.json"

C = {
    "bg": "#0a0c16", "card": "#11142a", "card2": "#1a1e3a", "border": "#252b4d",
    "text": "#f1f3fb", "muted": "#8a90b0", "accent": "#f97316", "accent_h": "#fb8b3d",
    "purple": "#8b6cff", "purple_h": "#a18bff", "cyan": "#22d3ee", "green": "#34d399",
    "red": "#ef4444", "red_h": "#f87171",
}

YT_RE = re.compile(r"(youtube\.com|youtu\.be|music\.youtube\.com)", re.I)
VID_RE = re.compile(r"(?:v=|youtu\.be/|shorts/|embed/)([\w-]{11})")
URL_RE = re.compile(r"https?://[^\s<>\"']+")
BATCH_LIMIT = 20      # most links accepted from one paste
MAX_WORKERS = 2       # simultaneous downloads; more than this thrashes bandwidth
PREVIEW_ROWS = 6      # rows shown before the batch list starts scrolling
ROW_H = 34
MP4_Q = ["Best available", "1080p", "720p", "480p", "360p"]
M4A_Q = "M4A · Original"
MP3_Q = [M4A_Q, "128 kbps", "192 kbps", "256 kbps", "320 kbps"]
MP3_DEFAULT = "128 kbps"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
HEVC_HW = (
    ("hevc_nvenc", ["-preset", "p5", "-rc", "vbr", "-cq", "28", "-b:v", "0"]),
    ("hevc_qsv", ["-preset", "medium", "-global_quality", "27"]),
    ("hevc_amf", ["-quality", "balanced", "-rc", "cqp", "-qp_i", "28", "-qp_p", "28"]),
    ("hevc_videotoolbox", ["-q:v", "55"]),
)
HEVC_SW = ("libx265", ["-crf", "26", "-preset", "faster"])
HEVC_CHOICE = []


def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path, data):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def parse_urls(text):
    """Pull unique YouTube links out of arbitrary pasted text."""
    urls = []
    for raw in URL_RE.findall(text or ""):
        url = raw.rstrip(").,;]}'\"")
        if url in urls or not YT_RE.search(url):
            continue
        urls.append(url)
        if len(urls) >= BATCH_LIMIT:
            break
    return urls


def vid_of(url):
    m = VID_RE.search(url or "")
    return m.group(1) if m else None


def fmt_bytes(n):
    if not n:
        return "—"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def fmt_dur(s):
    if not s:
        return "—"
    s = int(s)
    h, r = divmod(s, 3600)
    m, sec = divmod(r, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def lerp_color(a, b, t):
    a = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{int(x + (y - x) * t):02x}" for x, y in zip(a, b))


def open_path(p):
    if sys.platform == "win32":
        os.startfile(p)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", p])
    else:
        subprocess.Popen(["xdg-open", p])


def reveal(p):
    p = os.path.normpath(p)
    if sys.platform == "win32":
        subprocess.Popen(f'explorer /select,"{p}"')
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", p])
    else:
        subprocess.Popen(["xdg-open", os.path.dirname(p)])


def ffmpeg_works(args):
    try:
        return subprocess.run(["ffmpeg", "-v", "error", *args], stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, creationflags=NO_WINDOW, timeout=30).returncode == 0
    except Exception:
        return False


def hevc_encoders():
    if HEVC_CHOICE:
        return HEVC_CHOICE[0]
    try:
        listing = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True,
                                 errors="ignore", creationflags=NO_WINDOW, timeout=30).stdout
    except Exception:
        listing = ""
    found = []
    for name, params in HEVC_HW:
        if name in listing and ffmpeg_works(["-f", "lavfi", "-i", "color=c=black:s=640x360:r=30:d=0.5",
                                             "-c:v", name, *params, "-f", "null", "-"]):
            found.append((name, params))
            break
    found.append(HEVC_SW)
    HEVC_CHOICE.append(found)
    return found


def estimate_size(info, fmt, q):
    dur = info.get("duration") or 0

    def fs(f):
        return f.get("filesize") or f.get("filesize_approx") or (f.get("tbr") or 0) * 1000 / 8 * dur

    if fmt == "MP3" and q == M4A_Q:
        fl = info.get("formats") or []
        auds = [f for f in fl if f.get("acodec") not in (None, "none") and f.get("vcodec") in (None, "none")]
        auds = [f for f in auds if f.get("ext") == "m4a"] or auds
        a = max(auds, key=fs, default=None)
        return (fs(a) if a else 0) or None
    if fmt == "MP3":
        kb = int(re.match(r"\d+", q).group())
        return dur * kb * 1000 / 8

    m = re.match(r"\d+", q)
    h = int(m.group()) if m else 99999
    fl = info.get("formats") or []
    vids = [f for f in fl if f.get("vcodec") not in (None, "none") and f.get("acodec") in (None, "none")
            and (f.get("height") or 0) <= h]
    auds = [f for f in fl if f.get("acodec") not in (None, "none") and f.get("vcodec") in (None, "none")]
    vids = [f for f in vids if f.get("ext") == "mp4"] or vids
    auds = [f for f in auds if f.get("ext") == "m4a"] or auds
    v = max(vids, key=lambda f: ((f.get("height") or 0), fs(f)), default=None)
    a = max(auds, key=fs, default=None)
    total = (fs(v) if v else 0) + (fs(a) if a else 0)
    return total or None


def _bez(p0, p1, p2, p3, n=40):
    pts = []
    for i in range(n + 1):
        t = i / n
        u = 1 - t
        pts.append((u ** 3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t ** 3 * p3[0],
                    u ** 3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t ** 3 * p3[1]))
    return pts


def _fit(layer, px, fill_ratio, dx=0.0):
    box = layer.getchannel("A").getbbox()
    layer = layer.crop(box)
    w, h = layer.size
    s = px * fill_ratio / max(w, h)
    layer = layer.resize((max(1, round(w * s)), max(1, round(h * s))), Image.LANCZOS)
    out = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    out.paste(layer, ((px - layer.width) // 2 + round(layer.width * dx), (px - layer.height) // 2), layer)
    return out


def note_image(px=96, color="#ffffff", fill_ratio=0.62):
    k = 8
    S = 100 * k
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    cx, cy, rx, ry, ang = 36, 74, 17, 12.5, math.radians(-22)
    head = []
    for i in range(120):
        t = 2 * math.pi * i / 120
        x, y = rx * math.cos(t), ry * math.sin(t)
        head.append(((cx + x * math.cos(ang) - y * math.sin(ang)) * k, (cy + x * math.sin(ang) + y * math.cos(ang)) * k))
    d.polygon(head, fill=color)
    d.rectangle([49 * k, 14 * k, 55.5 * k, 75 * k], fill=color)
    outer = _bez((55.5, 14), (60, 36), (83, 40), (80, 68))
    inner = _bez((80, 68), (76, 50), (62, 48), (55.5, 40))
    d.polygon([(x * k, y * k) for x, y in outer + inner], fill=color)
    return _fit(img, px, fill_ratio)


def play_image(px=96, color="#ffffff", fill_ratio=0.5):
    k = 8
    S = 100 * k
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pts = [(24 * k, 14 * k), (24 * k, 86 * k), (86 * k, 50 * k)]
    d.polygon(pts, fill=color)
    d.line(pts + [pts[0], pts[1]], fill=color, width=10 * k, joint="curve")
    return _fit(img, px, fill_ratio, dx=0.04)


def search_image(px=72, color="#8a90b0"):
    k = 8
    S = px * k
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    w = S * 0.11
    cx, cy, r = S * 0.42, S * 0.42, S * 0.28
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=color, width=round(w))
    a = math.radians(45)
    x0, y0 = cx + (r + w * 0.2) * math.cos(a), cy + (r + w * 0.2) * math.sin(a)
    x1, y1 = S * 0.88, S * 0.88
    d.line([x0, y0, x1, y1], fill=color, width=round(w * 1.1))
    for x, y in ((x1, y1),):
        rr = w * 1.1 / 2
        d.ellipse([x - rr, y - rr, x + rr, y + rr], fill=color)
    return img.resize((px, px), Image.LANCZOS)


FONT_STACKS = {
    "win32": [("Segoe UI", "Segoe UI Semibold"), ("Calibri", "Calibri")],
    "darwin": [("SF Pro Text", "SF Pro Text"), ("Helvetica Neue", "Helvetica Neue")],
}
FONT_STACKS_DEFAULT = [("Inter", "Inter"), ("Noto Sans", "Noto Sans"),
                       ("Cantarell", "Cantarell"), ("Ubuntu", "Ubuntu"), ("DejaVu Sans", "DejaVu Sans")]


def pick_fonts(root):
    """Prefer a UI family that ships a real semibold face.

    Tk fakes bold by smearing the weight normal face, which is what makes
    small labels look chunky and pixelated. Using an actual semibold family
    keeps emphasis crisp at 10-11px.
    """
    installed = set(tkfont.families(root))
    for body, semibold in FONT_STACKS.get(sys.platform, FONT_STACKS_DEFAULT):
        if body in installed:
            return body, (semibold if semibold in installed else None)
    return "TkDefaultFont", None


def clear_layered_style(window):
    """Drop WS_EX_LAYERED after the fade so Windows re-enables ClearType.

    Tk's -alpha attribute makes the toplevel a layered window, and subpixel
    text antialiasing is disabled on layered surfaces, so text stays soft
    even at alpha 1.0.
    """
    if sys.platform != "win32":
        return
    GWL_EXSTYLE = -20
    WS_EX_LAYERED = 0x00080000
    SWP = 0x0001 | 0x0002 | 0x0004 | 0x0020
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.GetParent(window.winfo_id()) or window.winfo_id()
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        if style & WS_EX_LAYERED:
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style & ~WS_EX_LAYERED)
            user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, SWP)
    except Exception:
        pass


class SmoothBar(tk.Canvas):
    """Pill progress bar drawn as two canvas lines.

    A frame update is a single coords() call instead of the full canvas
    re-draw a CTkProgressBar does on every set().
    """

    def __init__(self, parent, height=8, track="#1a1e3a", accent="#f97316", bg="#11142a"):
        super().__init__(parent, height=height, highlightthickness=0, bd=0, bg=bg)
        self._h = height
        self._p = -1.0
        self._track = self.create_line(0, 0, 1, 0, width=height, capstyle="round", fill=track)
        self._fill = self.create_line(0, 0, 1, 0, width=height, capstyle="round", fill=accent,
                                      state="hidden")
        self.bind("<Configure>", lambda e: self._draw())

    def set(self, p):
        p = 0.0 if p < 0 else (1.0 if p > 1 else float(p))
        if abs(p - self._p) < 0.0008:
            return
        self._p = p
        self._draw()

    def set_color(self, color):
        self.itemconfigure(self._fill, fill=color)

    def _draw(self):
        w = self.winfo_width()
        if w <= 1:
            return
        y = r = self._h / 2
        self.coords(self._track, r, y, w - r, y)
        if self._p <= 0.001:
            self.itemconfigure(self._fill, state="hidden")
            return
        self.itemconfigure(self._fill, state="normal")
        self.coords(self._fill, r, y, max(r + 0.5, r + (w - self._h) * self._p), y)


class App(ctk.CTk):
    FRAME_MS = 16
    IDLE_MS = 60

    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("dark")
        self.font_family, self.font_semibold = pick_fonts(self)
        ctk.ThemeManager.theme["CTkFont"]["family"] = self.font_family
        self.title("Streamgrab")
        self.geometry("780x680")
        self.minsize(640, 560)
        self.configure(fg_color=C["bg"])

        self.settings = load_json(SETTINGS_FILE, {})
        stored = load_json(HISTORY_FILE, [])
        self.history = stored[:HISTORY_LIMIT]
        if len(stored) > HISTORY_LIMIT:
            save_json(HISTORY_FILE, self.history)
        self.ui_q = queue.Queue()
        self.cancel_flag = threading.Event()
        self.procs = set()
        self.busy = False
        self.info = None
        self.batch_urls = []
        self.field_urls = []
        self.urls = []
        self.batch_infos = {}
        self.batch_status = {}
        self.batch_rows = []
        self.batch_open = False
        self.job = None
        self._urls_sig = None
        self._batch_sig = None
        self.fetch_id = 0
        self.deb = None
        self.last_clip = ""
        self.last_path = None
        self.target_p = 0.0
        self.shown_p = 0.0
        self.pulse_t = 0.0
        self._pulse_acc = 0.0
        self._pct = -1
        self._state_key = None
        self._frame_job = None
        self.dot_color = C["green"]
        self.active_accent = C["accent"]
        self.fmt = self.settings.get("format", "MP3")

        self.url_var = ctk.StringVar()
        self.url_var.trace_add("write", self.on_url_change)
        self.folder_var = ctk.StringVar(value=self.settings.get("folder", str(Path.home() / "Downloads")))
        self.quality_var = ctk.StringVar()
        self.hevc_var = ctk.BooleanVar(value=self.settings.get("hevc", False))
        self.search_var = ctk.StringVar()
        self.search_var.trace_add("write", lambda *_: self.schedule_search())
        self.search_job = None
        self.hist_sig = None
        self.render_id = 0
        self.font_cache = {}
        self.fit_job = None
        self.pending_progress = None
        self.last_tick = time.perf_counter()
        self.icon_note = ctk.CTkImage(note_image(96), size=(48, 48))
        self.icon_play = ctk.CTkImage(play_image(96), size=(48, 48))
        self.icon_search = ctk.CTkImage(search_image(72), size=(18, 18))
        self.title_full = ""
        self.build_header()
        self.build_download_page()
        self.build_history_page()
        self.set_format(self.fmt, initial=True)
        self.show_page("Download")

        self.bind("<FocusIn>", self.on_focus)
        self.bind("<Return>", lambda e: self.fetch_info() if self.focus_get() else None)
        self.after(self.FRAME_MS, self._frame)
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.attributes("-alpha", 0.0)
        self.fade_in()
        if self.hevc_var.get():
            threading.Thread(target=hevc_encoders, daemon=True).start()

    def fade_in(self, t0=None):
        t0 = t0 or time.perf_counter()
        a = min(1.0, (time.perf_counter() - t0) / 0.18)
        self.attributes("-alpha", a)
        if a < 1.0:
            self.after(10, lambda: self.fade_in(t0))
        else:
            clear_layered_style(self)

    def ui(self, fn, *args):
        self.ui_q.put((fn, args))

    def _frame(self):
        """Single update clock for the whole app.

        Runs at FRAME_MS while something is animating and backs off to IDLE_MS
        when idle, so a quiet window costs ~16 wakeups a second instead of the
        ~120 the old poll/tick/pulse loops were spending.
        """
        self._frame_job = None
        now = time.perf_counter()
        dt = min(0.05, now - self.last_tick)
        self.last_tick = now
        active = False

        pp = self.pending_progress
        if pp is not None:
            self.pending_progress = None
            self._progress(*pp)
            active = True

        try:
            while True:
                fn, args = self.ui_q.get_nowait()
                fn(*args)
                active = True
        except queue.Empty:
            pass

        diff = self.target_p - self.shown_p
        if diff:
            if abs(diff) <= 0.0006:
                self.shown_p = self.target_p
            else:
                self.shown_p += diff * (1 - 0.82 ** (dt * 60))
                active = True
            self._paint_progress()

        if self.busy:
            active = True
            self.pulse_t += 6.25 * dt
            self._pulse_acc += dt
            if self._pulse_acc >= 0.04:
                self._pulse_acc = 0.0
                k = (math.sin(self.pulse_t) + 1) / 2
                self.dot.configure(text_color=lerp_color(self.active_accent, C["card"], k * 0.8))

        self._frame_job = self.after(self.FRAME_MS if active else self.IDLE_MS, self._frame)

    def _paint_progress(self):
        p = self.shown_p
        p = 0.0 if p < 0 else (1.0 if p > 1 else p)
        self.bar.set(p)
        pct = int(p * 100)
        if pct != self._pct:
            self._pct = pct
            self.pct_lbl.configure(text=f"{pct}%")

    def font(self, size, bold=False):
        key = (size, bold)
        f = self.font_cache.get(key)
        if f is None:
            if bold and self.font_semibold:
                f = ctk.CTkFont(family=self.font_semibold, size=size)
            else:
                f = ctk.CTkFont(family=self.font_family, size=size,
                                weight="bold" if bold else "normal")
            self.font_cache[key] = f
        return f

    def _set_text(self, widget, text):
        """Skip redundant configure() calls, which cost a full redraw each."""
        if widget.cget("text") == text:
            return
        widget.configure(text=text)

    def label(self, parent, text, size=12, bold=False, color=None, **kw):
        return ctk.CTkLabel(parent, text=text, text_color=color or C["text"],
                            font=self.font(size, bold), **kw)

    def card(self, parent, **kw):
        return ctk.CTkFrame(parent, fg_color=C["card"], corner_radius=18, border_width=1,
                            border_color=C["border"], **kw)

    def build_header(self):
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x", padx=24, pady=(20, 8))
        logo = ctk.CTkLabel(head, text="", image=self.icon_note, width=48, height=48, corner_radius=14,
                            fg_color=C["accent"])
        logo.pack(side="left")
        self.logo = logo
        box = ctk.CTkFrame(head, fg_color="transparent")
        box.pack(side="left", padx=14)
        self.label(box, "Streamgrab", 22, True).pack(anchor="w")
        self.label(box, "Download YouTube video or audio in a few clicks", 12, color=C["muted"]).pack(anchor="w")
        self.tabs = ctk.CTkSegmentedButton(
            head, values=["Download", "History"], command=self.show_page, height=36,
            fg_color=C["card"], selected_color=C["accent"], selected_hover_color=C["accent_h"],
            unselected_color=C["card"], unselected_hover_color=C["card2"], corner_radius=12,
            font=self.font(13, True))
        self.tabs.pack(side="right")
        self.tabs.set("Download")
        self.pages = ctk.CTkFrame(self, fg_color="transparent")
        self.pages.pack(fill="both", expand=True, padx=24, pady=(4, 20))

    def show_page(self, name):
        for p in (self.dl_page, self.hist_page):
            p.pack_forget()
        if name == "Download":
            self.dl_page.pack(fill="both", expand=True)
        else:
            self.hist_page.pack(fill="both", expand=True)
            self.render_history()
        self.tabs.set(name)

    def build_download_page(self):
        pg = self.dl_page = ctk.CTkFrame(self.pages, fg_color="transparent")

        link = self.card(pg)
        link.pack(fill="x")
        self.label(link, "MEDIA LINK", 11, True, C["muted"]).pack(anchor="w", padx=16, pady=(12, 5))
        row = ctk.CTkFrame(link, fg_color="transparent")
        row.pack(fill="x", padx=20)
        self.url_entry = ctk.CTkEntry(row, textvariable=self.url_var, height=38, corner_radius=11,
                                      fg_color=C["card2"], border_color=C["border"], border_width=1,
                                      placeholder_text="Paste a YouTube or YouTube Music URL",
                                      font=ctk.CTkFont(size=13))
        self.url_entry.pack(side="left", fill="x", expand=True)
        self.url_hint = ctk.CTkLabel(self.url_entry, text="Paste a YouTube video or music link", text_color=C["muted"],
                             fg_color="transparent", font=ctk.CTkFont(size=13), height=20)
        self.url_hint.place(x=12, rely=0.5, anchor="w")
        self.url_hint.bind("<Button-1>", lambda e: self.url_entry.focus_set())
        for text, cmd in (("Paste", self.paste), ("Add", self.add_to_batch), ("Clear", self.clear)):
            btn = ctk.CTkButton(row, text=text, width=58 if text == "Add" else 68, height=38,
                                corner_radius=11, fg_color=C["card2"], hover_color=C["border"],
                                border_width=1, border_color=C["border"], command=cmd)
            btn.pack(side="left", padx=(8, 0))
            if text == "Add":
                self.add_btn = btn
        self._sync_add_btn()

        self.preview = ctk.CTkFrame(link, fg_color=C["card2"], corner_radius=12)
        self.preview.pack(fill="x", padx=16, pady=(10, 12))
        self.info_row = ctk.CTkFrame(self.preview, fg_color="transparent", height=42)
        # inset by more than the preview's corner radius, otherwise this child
        # paints its background straight over the rounded corners
        self.info_row.pack(fill="x", padx=14)
        self.info_row.pack_propagate(False)
        self.title_lbl = self.label(self.info_row, "Paste a link to get started", 12, True, anchor="w")
        self.title_lbl.pack(side="left")
        self.meta_lbl = self.label(self.info_row, "Length and estimated size will show here", 12,
                                   color=C["cyan"], anchor="w")
        self.meta_lbl.pack(side="left", padx=(14, 0))
        self.info_right = ctk.CTkFrame(self.info_row, fg_color="transparent")
        self.chevron = ctk.CTkButton(self.info_right, text="▾", width=26, height=26, corner_radius=8,
                                     fg_color="transparent", hover_color=C["border"],
                                     text_color=C["muted"], font=self.font(13, True),
                                     command=self.toggle_batch)
        self.chevron.pack()
        self._chev_shown = False
        self._batch_shown = False
        self.title_full = "Paste a link to get started"
        self.info_row.bind("<Configure>", lambda e: self.queue_fit())

        self.batch_box = ctk.CTkFrame(self.preview, fg_color="transparent")
        self.batch_scroll = ctk.CTkScrollableFrame(
            self.batch_box, fg_color="transparent", height=ROW_H * PREVIEW_ROWS,
            scrollbar_fg_color=C["card2"], scrollbar_button_color="#69749a",
            scrollbar_button_hover_color="#aab5dc")
        self.batch_scroll.pack(fill="both", expand=True)

        opts = self.card(pg)
        opts.pack(fill="x", pady=9)
        top = ctk.CTkFrame(opts, fg_color="transparent")
        top.pack(fill="x", padx=16, pady=(12, 0))
        left = ctk.CTkFrame(top, fg_color="transparent")
        left.pack(side="left", fill="x", expand=True)
        self.label(left, "FORMAT", 11, True, C["muted"]).pack(anchor="w", pady=(0, 6))
        fr = ctk.CTkFrame(left, fg_color="transparent")
        fr.pack(anchor="w")
        self.fmt_btns = {}
        for f, txt in (("MP4", "MP4 · Video"), ("MP3", "MP3 · Audio")):
            b = ctk.CTkButton(fr, text=txt, width=136, height=36, corner_radius=10, border_width=1,
                              font=ctk.CTkFont(size=13, weight="bold"), command=lambda f=f: self.set_format(f))
            b.pack(side="left", padx=(0, 10))
            self.fmt_btns[f] = b
        right = ctk.CTkFrame(top, fg_color="transparent")
        right.pack(side="right")
        self.label(right, "QUALITY", 11, True, C["muted"]).pack(anchor="w", pady=(0, 6))
        self.quality_menu = ctk.CTkOptionMenu(
            right, variable=self.quality_var, values=MP3_Q, width=180, height=36, corner_radius=10,
            fg_color=C["card2"], button_color=C["border"], button_hover_color=C["purple"],
            dropdown_fg_color=C["card2"], dropdown_hover_color=C["border"], command=lambda _: self.after_quality())
        self.quality_menu.pack()

        self.label(opts, "SAVE TO", 11, True, C["muted"]).pack(anchor="w", padx=16, pady=(11, 5))
        sr = ctk.CTkFrame(opts, fg_color="transparent")
        sr.pack(fill="x", padx=20)
        ctk.CTkEntry(sr, textvariable=self.folder_var, height=36, corner_radius=10, fg_color=C["card2"],
                     border_color=C["border"], border_width=1).pack(side="left", fill="x", expand=True)
        ctk.CTkButton(sr, text="Browse", width=76, height=36, corner_radius=10, fg_color=C["card2"],
                      hover_color=C["border"], border_width=1, border_color=C["border"],
                      command=self.browse).pack(side="left", padx=(8, 0))
        ctk.CTkButton(sr, text="Open", width=58, height=36, corner_radius=10, fg_color=C["card2"],
                      hover_color=C["border"], border_width=1, border_color=C["border"],
                      command=lambda: open_path(self.folder_var.get()) if os.path.isdir(self.folder_var.get()) else None
                      ).pack(side="left", padx=(8, 0))
        self.hevc_sw = ctk.CTkSwitch(opts, text="Convert to HEVC (H.265) after download · smaller file, slower",
                                     variable=self.hevc_var, progress_color=C["purple"], text_color=C["muted"],
                                     font=ctk.CTkFont(size=12), command=self.save_settings)
        self.hevc_sw.pack(anchor="w", padx=16, pady=(10, 12))

        act = ctk.CTkFrame(pg, fg_color="transparent")
        act.pack(fill="x")
        act.grid_columnconfigure((0, 1), weight=1, uniform="a")
        self.dl_btn = ctk.CTkButton(act, text="Download", height=44, corner_radius=12, fg_color=C["accent"],
                                    hover_color=C["accent_h"], font=self.font(15, True),
                                    command=self.start_download)
        self.dl_btn.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self.cancel_btn = ctk.CTkButton(act, text="Cancel", height=44, corner_radius=12, fg_color="#2a1f2e",
                                        hover_color=C["red_h"], text_color_disabled="#5a4a5e", state="disabled",
                                        font=self.font(15, True), command=self.cancel)
        self.cancel_btn.grid(row=0, column=1, sticky="ew", padx=(6, 0))

        prog = self.card(pg)
        prog.pack(fill="x", pady=(9, 12))
        sr = ctk.CTkFrame(prog, fg_color="transparent")
        sr.pack(fill="x", padx=16, pady=(11, 6))
        self.dot = ctk.CTkLabel(sr, text="●", text_color=C["green"], font=ctk.CTkFont(size=14))
        self.dot.pack(side="left")
        self.status_lbl = self.label(sr, "Ready", 15, True, anchor="w")
        self.status_lbl.pack(side="left", padx=8)
        self.pct_lbl = self.label(sr, "0%", 13, True, C["cyan"])
        self.pct_lbl.pack(side="right")
        self.show_btn = ctk.CTkButton(sr, text="Show file", width=84, height=28, corner_radius=10,
                                      fg_color=C["card2"], hover_color=C["border"], state="disabled",
                                      command=lambda: self.last_path and reveal(self.last_path))
        self.show_btn.pack(side="right", padx=12)
        self.bar = SmoothBar(prog, height=8, track=C["card2"], accent=C["accent"], bg=C["card"])
        self.bar.pack(fill="x", padx=16)
        stats = ctk.CTkFrame(prog, fg_color="transparent")
        stats.pack(fill="x", padx=11, pady=(10, 12))
        stats.grid_columnconfigure((0, 1, 2), weight=1, uniform="s")
        self.stat = {}
        for i, (key, title, col) in enumerate((("done", "DOWNLOADED", C["purple"]),
                                              ("left", "REMAINING", C["cyan"]),
                                              ("speed", "SPEED · ETA", C["green"]))):
            box = ctk.CTkFrame(stats, fg_color=C["card2"], corner_radius=10, height=60)
            box.pack_propagate(False)
            box.grid(row=0, column=i, sticky="ew", padx=5)
            ctk.CTkFrame(box, width=3, height=2, fg_color=col, corner_radius=2).pack(side="left", fill="y", padx=(10, 0), pady=9)
            inner = ctk.CTkFrame(box, fg_color="transparent")
            inner.pack(side="left", padx=8, pady=9)
            self.label(inner, title, 10, True, C["muted"], height=15).pack(anchor="w")
            v = self.label(inner, "—", 15, True, height=20)
            v.pack(anchor="w")
            v.configure(font=self.font(14, True))
            self.stat[key] = v

    def build_history_page(self):
        pg = self.hist_page = ctk.CTkFrame(self.pages, fg_color="transparent")
        bar = ctk.CTkFrame(pg, fg_color="transparent")
        bar.pack(fill="x", pady=(0, 10))
        search_box = ctk.CTkFrame(bar, height=42, corner_radius=12, fg_color=C["card"], border_width=1,
                                  border_color=C["border"])
        search_box.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(search_box, text="", image=self.icon_search, fg_color="transparent",
                     width=18, height=18).pack(side="left", padx=(14, 0))
        ctk.CTkEntry(search_box, textvariable=self.search_var, height=38, corner_radius=10,
                     fg_color=C["card"], border_width=0, placeholder_text_color=C["muted"],
                     placeholder_text="Search downloads").pack(side="left", fill="both", expand=True,
                                                               padx=(4, 6), pady=2)
        ctk.CTkButton(bar, text="Clear all", width=90, height=42, corner_radius=12, fg_color=C["card"],
                      hover_color=C["red"], border_width=1, border_color=C["border"],
                      command=self.clear_history).pack(side="left", padx=(8, 0))
        self.count_lbl = self.label(pg, "", 12, color=C["muted"], anchor="w")
        self.count_lbl.pack(anchor="w", pady=(0, 6))
        self.hist_list = ctk.CTkScrollableFrame(
            pg, fg_color="transparent", scrollbar_fg_color=C["card"],
            scrollbar_button_color="#69749a", scrollbar_button_hover_color="#aab5dc")
        self.hist_list.pack(fill="both", expand=True)
        self._slots = []

    def schedule_search(self):
        if self.search_job:
            self.after_cancel(self.search_job)
        self.search_job = self.after(150, self.render_history)

    def render_history(self):
        self.search_job = None
        q = self.search_var.get().lower().strip()
        items = [e for e in self.history if q in e["title"].lower()]
        shown = items[:HISTORY_LIMIT]
        sig = (q, len(items), datetime.now().date(),
               tuple((e["time"], e["path"], os.path.exists(e["path"])) for e in shown))
        if sig == self.hist_sig:
            return
        self.hist_sig = sig
        self.render_id += 1
        self._set_text(self.count_lbl,
                       f"{len(items)} download{'s' if len(items) != 1 else ''}, newest first")
        ops = []
        last_day = None
        for i, e in enumerate(shown):
            dt = datetime.fromisoformat(e["time"])
            day = self.day_label(dt)
            if day != last_day:
                ops.append(("day", day, None, False))
                last_day = day
            ops.append(("row", e, dt, i == 0 and not q))
        if not ops:
            ops.append(("empty", None, None, False))
        self._sync_slots(ops, self.render_id, 0)

    def _sync_slots(self, ops, rid, start):
        """Reconcile the list in place: reuse row widgets, only update props.

        The old version destroyed and rebuilt every child on each render, which
        reconstructed ~10 widgets per row on every search keystroke.
        """
        if rid != self.render_id:
            return
        end = min(len(ops), start + 8)
        for i in range(start, end):
            kind, payload, dt, latest = ops[i]
            slot = self._slots[i] if i < len(self._slots) else None
            if slot is None or slot["kind"] != kind:
                if slot is not None:
                    slot["w"].destroy()
                slot = self._build_slot(kind)
                if i < len(self._slots):
                    self._slots[i] = slot
                else:
                    self._slots.append(slot)
            self._fill_slot(slot, payload, dt, latest)
            if not slot["packed"]:
                self._pack_slot(i)
        if end < len(ops):
            self.after(1, lambda: self._sync_slots(ops, rid, end))
            return
        for i in range(end, len(self._slots)):
            slot = self._slots[i]
            if slot["packed"]:
                slot["w"].pack_forget()
                slot["packed"] = False

    def _pack_slot(self, i):
        slot = self._slots[i]
        anchor = None
        for j in range(i + 1, len(self._slots)):
            if self._slots[j]["packed"]:
                anchor = self._slots[j]["w"]
                break
        if slot["kind"] == "day":
            kw = {"anchor": "w", "pady": (10, 2), "padx": 4}
        elif slot["kind"] == "row":
            kw = {"fill": "x", "pady": 4, "padx": 2}
        else:
            kw = {"pady": 60}
        if anchor is not None:
            kw["before"] = anchor
        slot["w"].pack(**kw)
        slot["packed"] = True

    def _build_slot(self, kind):
        if kind == "day":
            w = self.label(self.hist_list, "", 11, True, C["muted"], anchor="w")
            return {"kind": "day", "w": w, "packed": False, "cache": {}}
        if kind == "empty":
            w = self.label(self.hist_list, "Nothing here yet. Your downloads will appear in this list.",
                           13, color=C["muted"])
            return {"kind": "empty", "w": w, "packed": False, "cache": {}}
        w = ctk.CTkFrame(self.hist_list, fg_color=C["card"], corner_radius=14, border_width=1,
                         border_color=C["border"])
        w.grid_columnconfigure(1, weight=1)
        badge = ctk.CTkLabel(w, text="", width=48, height=48, corner_radius=12, fg_color=C["accent"],
                             text_color="white", font=self.font(12, True))
        badge.grid(row=0, column=0, rowspan=2, padx=12, pady=12)
        tf = ctk.CTkFrame(w, fg_color="transparent")
        tf.grid(row=0, column=1, sticky="w", pady=(12, 0))
        title = self.label(tf, "", 13, True, anchor="w")
        title.pack(side="left")
        pill = ctk.CTkLabel(tf, text="LATEST", fg_color=C["green"], text_color="#06281c",
                            corner_radius=6, height=18, width=52, font=self.font(10, True))
        sub = self.label(w, "", 11, color=C["muted"])
        sub.grid(row=1, column=1, sticky="w", pady=(0, 12))
        btns = ctk.CTkFrame(w, fg_color="transparent")
        btns.grid(row=0, column=2, rowspan=2, padx=10)
        ref = {"kind": "row", "w": w, "packed": False, "cache": {}, "entry": None,
               "badge": badge, "title": title, "pill": pill, "pill_on": False, "sub": sub,
               "btns": []}
        for text, action, needs_file in (("Open", "open", True), ("Folder", "folder", True),
                                         ("Link", "link", False)):
            b = ctk.CTkButton(btns, text=text, width=56, height=30, corner_radius=9,
                              fg_color=C["card2"], hover_color=C["border"], font=self.font(11),
                              command=lambda a=action, r=ref: self._row_action(a, r))
            b.pack(side="left", padx=2)
            ref["btns"].append((b, needs_file))
        ctk.CTkButton(btns, text="✕", width=30, height=30, corner_radius=9, fg_color=C["card2"],
                      hover_color=C["red"], font=self.font(11),
                      command=lambda r=ref: self._row_action("delete", r)).pack(side="left", padx=2)
        return ref

    def _cfg(self, widget, cache, key, **kw):
        if cache.get(key) == kw:
            return
        cache[key] = kw
        widget.configure(**kw)

    def _fill_slot(self, slot, payload, dt, latest):
        kind = slot["kind"]
        if kind == "empty":
            return
        cache = slot["cache"]
        if kind == "day":
            self._cfg(slot["w"], cache, "text", text=payload.upper())
            return
        e = payload
        slot["entry"] = e
        exists = os.path.exists(e["path"])
        title = e["title"] if len(e["title"]) <= 52 else e["title"][:51] + "…"
        self._cfg(slot["badge"], cache, "badge", text=e["format"],
                  fg_color=C["purple"] if e["format"] == "MP4" else C["accent"])
        self._cfg(slot["title"], cache, "title", text=title,
                  text_color=C["text"] if exists else C["muted"])
        self._cfg(slot["w"], cache, "border", border_color=C["green"] if latest else C["border"])
        sub = (f"{dt.strftime('%I:%M %p').lstrip('0')}  ·  {fmt_bytes(e.get('size'))}  ·  "
               f"{e.get('quality', '')}")
        if not exists:
            sub += "  ·  file moved or deleted"
        self._cfg(slot["sub"], cache, "sub", text=sub,
                  text_color=C["red_h"] if not exists else C["muted"])
        if latest != slot["pill_on"]:
            slot["pill_on"] = latest
            if latest:
                slot["pill"].pack(side="left", padx=8)
            else:
                slot["pill"].pack_forget()
        for button, needs_file in slot["btns"]:
            button.configure(state="normal" if (exists or not needs_file) else "disabled")

    def _row_action(self, action, ref):
        e = ref["entry"]
        if not e:
            return
        if action == "open":
            open_path(e["path"])
        elif action == "folder":
            reveal(e["path"])
        elif action == "link":
            self.copy_link(e["url"])
        else:
            self.delete_entry(e)

    def day_label(self, dt):
        d = (datetime.now().date() - dt.date()).days
        if d == 0:
            return "Today"
        if d == 1:
            return "Yesterday"
        return dt.strftime("%a, %b %d, %Y")

    def copy_link(self, url):
        self.clipboard_clear()
        self.clipboard_append(url)
        self.set_state("ready", "Link copied to clipboard")

    def delete_entry(self, e):
        self.history = [h for h in self.history if h is not e]
        save_json(HISTORY_FILE, self.history)
        self.render_history()

    def clear_history(self):
        if self.history and messagebox.askyesno("Clear history", "Remove all entries from the history log?\nYour files are not deleted."):
            self.history = []
            save_json(HISTORY_FILE, self.history)
            self.render_history()

    def set_format(self, f, initial=False):
        self.fmt = f
        accent = C["purple"] if f == "MP4" else C["accent"]
        accent_hover = C["purple_h"] if f == "MP4" else C["accent_h"]
        self.active_accent = accent
        self.logo.configure(image=self.icon_play if f == "MP4" else self.icon_note, fg_color=accent)
        self.tabs.configure(selected_color=accent, selected_hover_color=accent_hover)
        self.dl_btn.configure(fg_color=accent, hover_color=accent_hover)
        self.bar.set_color(accent)
        for k, b in self.fmt_btns.items():
            on = k == f
            col = C["purple"] if k == "MP4" else C["accent"]
            hov = C["purple_h"] if k == "MP4" else C["accent_h"]
            b.configure(fg_color=col if on else C["card2"], hover_color=hov if on else C["border"],
                        border_color=col if on else C["border"], text_color="white" if on else C["muted"])
        vals = MP4_Q if f == "MP4" else MP3_Q
        self.quality_menu.configure(values=vals)
        saved = self.settings.get(f"quality_{f}")
        self.quality_var.set(saved if saved in vals else (MP3_DEFAULT if f == "MP3" else vals[0]))
        if f == "MP3":
            self.hevc_var.set(False)
        self.hevc_sw.configure(state="normal" if f == "MP4" else "disabled")
        if not initial:
            self.save_settings()
        self.refresh_preview_meta()

    def after_quality(self):
        self.save_settings()
        self.refresh_preview_meta()

    def save_settings(self):
        self.settings.update(folder=self.folder_var.get(), format=self.fmt, hevc=self.hevc_var.get())
        self.settings[f"quality_{self.fmt}"] = self.quality_var.get()
        save_json(SETTINGS_FILE, self.settings)

    def on_close(self):
        self.save_settings()
        self.cancel_flag.set()
        self.destroy()

    def browse(self):
        d = filedialog.askdirectory(initialdir=self.folder_var.get())
        if d:
            self.folder_var.set(d)
            self.save_settings()

    def paste(self):
        try:
            self.url_var.set(self.clipboard_get().strip())
        except Exception:
            pass

    def clear(self):
        self.url_var.set("")
        self.batch_urls = []
        self.field_urls = []
        self._refresh_urls()
        self._urls_sig = None
        self.fetch_id += 1
        self.info = None
        self._reset_batch()
        self._apply_batch_layout()
        self._sync_add_btn()
        self.set_title("Paste a link to get started")
        self.set_meta("Length and estimated size will show here")
        if self.deb:
            self.after_cancel(self.deb)
            self.deb = None
        if not self.busy:
            self.target_p = self.shown_p = 0.0
            self.bar.set(0)
            self.pct_lbl.configure(text="0%")
            for value in self.stat.values():
                value.configure(text="—")
            self.show_btn.configure(state="disabled")
            self.set_state("ready", "Ready")

    def on_focus(self, e):
        if e.widget is not self or self.url_var.get().strip() or self.busy:
            return
        try:
            clip = self.clipboard_get().strip()
        except Exception:
            return
        if YT_RE.search(clip) and clip != self.last_clip:
            self.last_clip = clip
            self.url_var.set(clip)

    def on_url_change(self, *_):
        self.sync_url_hint()
        self.field_urls = parse_urls(self.url_var.get())
        self._sync_add_btn()
        if self.deb:
            self.after_cancel(self.deb)
        self.deb = self.after(450, self.fetch_info)

    def fetch_info(self):
        text = self.url_var.get().strip()
        self.field_urls = parse_urls(text)
        self._refresh_urls()
        sig = tuple(self.urls)
        if sig == self._urls_sig:
            self._sync_add_btn()
            return
        self._urls_sig = sig
        self.fetch_id += 1
        fid = self.fetch_id
        self.info = None
        self._reset_batch()
        if not self.urls:
            self.set_title("Paste a link to get started" if not text else "That doesn't look like a YouTube link")
            self.set_meta("Length and estimated size will show here")
            self._apply_batch_layout()
            self._sync_add_btn()
            return
        if len(self.urls) == 1:
            self.set_title("Fetching details…")
            self.set_meta("")
            threading.Thread(target=self._fetch, args=(self.urls[0], fid), daemon=True).start()
        else:
            self.refresh_preview_meta()
            missing = [u for u in self.urls if u not in self.batch_infos]
            if missing:
                threading.Thread(target=self._fetch_batch, args=(fid, missing), daemon=True).start()
        self._apply_batch_layout()
        self._sync_add_btn()

    def add_to_batch(self):
        """Commit the link in the field into the batch, then clear the field."""
        if self.busy:
            return
        parsed = parse_urls(self.url_var.get())
        if not parsed:
            self.url_entry.configure(border_color=C["red"])
            self.after(900, lambda: self.url_entry.configure(border_color=C["border"]))
            self.set_state("error", "Paste a valid YouTube link first")
            return
        fresh = [u for u in parsed if u not in self.batch_urls]
        if not fresh:
            self.set_state("error", "That link is already in the batch")
            return
        room = BATCH_LIMIT - len(self.batch_urls)
        if room <= 0:
            self.set_state("error", f"Batch is full ({BATCH_LIMIT} links)")
            return
        added = fresh[:room]
        self.batch_urls.extend(added)
        self.url_var.set("")
        self.field_urls = []
        self._refresh_urls()
        self.fetch_id += 1
        fid = self.fetch_id
        self._urls_sig = tuple(self.urls)
        self._reset_batch()
        if len(self.urls) == 1:
            self.set_title("Fetching details…")
            self.set_meta("")
            threading.Thread(target=self._fetch, args=(self.urls[0], fid), daemon=True).start()
        else:
            self.refresh_preview_meta()
            missing = [u for u in added if u not in self.batch_infos]
            if missing:
                threading.Thread(target=self._fetch_batch, args=(fid, missing), daemon=True).start()
        self._apply_batch_layout()
        self._sync_add_btn()
        self.set_state("ready", f"Added · {len(self.urls)} in batch")
        self.url_entry.focus_set()

    def _fetch(self, url, fid):
        try:
            with yt_dlp.YoutubeDL({"quiet": True, "noplaylist": True, "skip_download": True,
                                   "no_warnings": True}) as ydl:
                info = ydl.extract_info(url, download=False)
            self.ui(self._fetched, fid, info)
        except Exception:
            self.ui(self._fetched, fid, None)

    def _fetched(self, fid, info):
        if fid != self.fetch_id:
            return
        if not info:
            self.set_title("Couldn't read that link")
            self.set_meta("Check the URL and your connection")
            return
        self.info = info
        if len(self.urls) == 1:
            self.batch_infos[self.urls[0]] = info
        t = info.get("title", "Untitled")
        self.set_title(t)
        self.refresh_preview_meta()

    def _fetch_batch(self, fid, urls):
        """Read details for a batch, three at a time so we don't hammer YouTube."""
        gate = threading.Semaphore(3)

        def one(url):
            with gate:
                if fid != self.fetch_id or self.cancel_flag.is_set():
                    return
                try:
                    with yt_dlp.YoutubeDL({"quiet": True, "noplaylist": True, "skip_download": True,
                                           "no_warnings": True}) as ydl:
                        info = ydl.extract_info(url, download=False)
                except Exception:
                    info = None
                self.ui(self._batch_fetched, fid, url, info)

        for url in urls:
            threading.Thread(target=one, args=(url,), daemon=True).start()

    def _batch_fetched(self, fid, url, info):
        if fid != self.fetch_id:
            return
        if info:
            self.batch_infos[url] = info
        if url in self.urls:
            self._fill_batch_row(self.urls.index(url))
        self.refresh_preview_meta()

    def refresh_preview_meta(self):
        n = len(self.urls)
        if n > 1:
            known = [self.batch_infos[u] for u in self.urls if u in self.batch_infos]
            dur = sum(i.get("duration") or 0 for i in known)
            size = sum(estimate_size(i, self.fmt, self.quality_var.get()) or 0 for i in known)
            parts = []
            if dur:
                parts.append(fmt_dur(dur))
            if size:
                parts.append(f"~ {fmt_bytes(size)}")
            if len(known) < n:
                parts.append(f"reading {len(known)}/{n}…")
            self.set_title(f"{n} Files")
            self.set_meta("  ·  ".join(parts) if parts else "Reading details…")
            if self.batch_rows:
                self._render_batch_list()
            return
        if not self.info:
            return
        size = estimate_size(self.info, self.fmt, self.quality_var.get())
        parts = [fmt_dur(self.info.get("duration")), f"~ {fmt_bytes(size)}" if size else ""]
        self.set_meta("  ·  ".join(p for p in parts if p))

    def _refresh_urls(self):
        """The list is the committed batch plus whatever is staged in the field."""
        combined = list(self.batch_urls)
        for url in self.field_urls:
            if url not in combined:
                combined.append(url)
        self.urls = combined

    def _reset_batch(self):
        self.batch_infos = {u: self.batch_infos[u] for u in self.urls if u in self.batch_infos}
        self.batch_status = {}
        self.batch_rows = []
        self._batch_sig = None
        for w in self.batch_scroll.winfo_children():
            w.destroy()

    def _sync_add_btn(self):
        btn = getattr(self, "add_btn", None)
        if btn is None:
            return
        on = bool(self.field_urls) and not self.busy and len(self.batch_urls) < BATCH_LIMIT
        btn.configure(state="normal" if on else "disabled")

    def toggle_batch(self):
        if len(self.urls) < 2:
            return
        self.batch_open = not self.batch_open
        self._apply_batch_layout()

    def _apply_batch_layout(self):
        """Show the chevron and file list only when more than one link is loaded."""
        multi = len(self.urls) > 1
        if not multi:
            self.batch_open = False
            if self._chev_shown:
                self.info_right.pack_forget()
                self._chev_shown = False
            if self._batch_shown:
                self.batch_box.pack_forget()
                self._batch_shown = False
            self.queue_fit(10)
            return
        if not self._chev_shown:
            self.info_right.pack(side="right")
            self._chev_shown = True
        self.chevron.configure(text="▴" if self.batch_open else "▾")
        if self.batch_open:
            self._render_batch_list()
            rows = max(1, min(len(self.urls), PREVIEW_ROWS))
            self.batch_scroll.configure(height=rows * ROW_H)
            if not self._batch_shown:
                self.batch_box.pack(fill="x", padx=14, pady=(0, 10))
                self._batch_shown = True
        elif self._batch_shown:
            self.batch_box.pack_forget()
            self._batch_shown = False
        self.queue_fit(10)

    def _render_batch_list(self):
        sig = tuple(self.urls)
        if sig != self._batch_sig:
            for w in self.batch_scroll.winfo_children():
                w.destroy()
            self.batch_rows = [self._make_batch_row(i) for i in range(len(self.urls))]
            self._batch_sig = sig
        for i in range(len(self.urls)):
            self._fill_batch_row(i)

    def _make_batch_row(self, i):
        row = ctk.CTkFrame(self.batch_scroll, fg_color="transparent", height=ROW_H)
        row.pack(fill="x", pady=1)
        row.pack_propagate(False)
        num = self.label(row, f"{i + 1}", 11, True, C["muted"], width=18)
        num.pack(side="left", padx=(2, 6))
        name = self.label(row, "", 12, anchor="w")
        name.pack(side="left")
        meta = self.label(row, "", 11, color=C["muted"], anchor="e")
        meta.pack(side="right", padx=(8, 6))
        return {"name": name, "meta": meta}

    def _fill_batch_row(self, i):
        if i >= len(self.batch_rows) or i >= len(self.urls):
            return
        row = self.batch_rows[i]
        url = self.urls[i]
        info = self.batch_infos.get(url)
        status = self.batch_status.get(i, "")
        if info:
            title = info.get("title") or "Untitled"
            size = estimate_size(info, self.fmt, self.quality_var.get())
            detail = "  ·  ".join(p for p in (fmt_dur(info.get("duration")),
                                             f"~ {fmt_bytes(size)}" if size else "") if p)
        else:
            title = vid_of(url) or url
            detail = ""
        if status:
            detail = f"{status}  ·  {detail}" if detail else status
        elif not detail:
            detail = "Reading…"
        if len(title) > 52:
            title = title[:51] + "…"
        self._set_text(row["name"], title)
        self._set_text(row["meta"], detail)

    def _update_batch_row(self, i, status):
        self.batch_status[i] = status
        if self.batch_rows and i < len(self.batch_rows):
            self._fill_batch_row(i)
    
    def sync_url_hint(self):
        if self.url_var.get():
            self.url_hint.place_forget()
        else:
            self.url_hint.place(x=12, rely=0.5, anchor="w")

    def queue_fit(self, delay=30):
        if self.fit_job:
            self.after_cancel(self.fit_job)
        self.fit_job = self.after(delay, self.fit_title)

    def set_title(self, text):
        if text == self.title_full:
            return
        self.title_full = text
        self._set_text(self.title_lbl, text)
        self.queue_fit(10)

    def set_meta(self, text):
        if self.meta_lbl.cget("text") == text:
            return
        self.meta_lbl.configure(text=text)
        self.queue_fit(10)

    def fit_title(self):
        self.fit_job = None
        text = self.title_full
        reserve = 0
        if self._chev_shown:
            reserve = self.info_right.winfo_reqwidth() + 10
        avail = self.info_row.winfo_width() - self.meta_lbl.winfo_reqwidth() - 14 - 12 - reserve
        if avail <= 40 or not text:
            return
        font = self.title_lbl.cget("font")
        if font.measure(text) > avail:
            lo, hi = 1, len(text)
            while lo < hi:
                mid = (lo + hi + 1) // 2
                if font.measure(text[:mid].rstrip() + "…") <= avail:
                    lo = mid
                else:
                    hi = mid - 1
            text = text[:lo].rstrip() + "…"
        if self.title_lbl.cget("text") != text:
            self.title_lbl.configure(text=text)

    def _find_dups(self, urls, fmt):
        """Existing downloads that match any of these links."""
        by_vid = {e.get("vid"): e for e in self.history
                  if e["format"] == fmt and os.path.exists(e["path"])}
        found = []
        for url in urls:
            entry = by_vid.get(vid_of(url))
            if entry is not None:
                found.append((url, entry))
        return found

    def set_state(self, kind, text):
        key = (kind, text)
        if key == self._state_key:
            return
        self._state_key = key
        self.dot_color = {"ready": C["green"], "busy": C["accent"], "error": C["red_h"], "done": C["green"],
                          "cancelled": C["accent"]}[kind]
        text_color = {"busy": C["cyan"], "cancelled": C["accent"], "done": C["green"],
                      "error": C["red_h"]}.get(kind, C["text"])
        self.dot.configure(text_color=self.dot_color)
        self.status_lbl.configure(text=text, text_color=text_color)

    def set_busy(self, b):
        self.busy = b
        self.dl_btn.configure(state="disabled" if b else "normal", text="Downloading…" if b else "Download")
        self.cancel_btn.configure(state="normal" if b else "disabled", fg_color=C["red"] if b else "#2a1f2e")
        self._sync_add_btn()

    def start_download(self):
        urls = list(self.urls)
        if not urls:
            self.url_entry.configure(border_color=C["red"])
            self.after(900, lambda: self.url_entry.configure(border_color=C["border"]))
            self.set_state("error", "Paste a valid YouTube link first")
            return
        key = "M4A" if self.fmt == "MP3" and self.quality_var.get() == M4A_Q else self.fmt
        dups = self._find_dups(urls, key)
        if dups:
            if len(urls) == 1:
                entry = dups[0][1]
                when = datetime.fromisoformat(entry["time"]).strftime("%b %d at %I:%M %p")
                if not messagebox.askyesno(
                        "Already downloaded",
                        f"You already downloaded this as {entry['format']} on {when}:\n\n"
                        f"{entry['title']}\n\nDownload it again?"):
                    return
            elif not messagebox.askyesno(
                    "Already downloaded",
                    f"{len(dups)} of these {len(urls)} links are already downloaded, for example:\n\n"
                    f"{dups[0][1]['title']}\n\nDownload them all again?"):
                return
        folder = self.folder_var.get()
        os.makedirs(folder, exist_ok=True)
        self.save_settings()
        self.cancel_flag.clear()
        self.target_p = self.shown_p = 0.0
        self.bar.set(0)
        self.pct_lbl.configure(text="0%")
        self.show_btn.configure(state="disabled")
        for v in self.stat.values():
            v.configure(text="—")
        self.batch_status = {}
        self.set_busy(True)
        self.set_state("busy", "Starting…")
        self._start_job(urls, self.fmt, self.quality_var.get(), folder, self.hevc_var.get())

    def _start_job(self, urls, fmt, q, folder, hevc):
        n = len(urls)
        job = {"n": n, "fmt": fmt, "q": q, "folder": folder, "hevc": hevc,
               "lock": threading.Lock(), "frac": [0.0] * n, "done_b": [0] * n,
               "total_b": [0] * n, "speed": [0] * n, "eta": [0] * n,
               "started": 0, "ok": 0, "fail": 0, "finished": 0, "closed": False}
        self.job = job
        work = queue.Queue()
        for i, url in enumerate(urls):
            work.put((i, url))
        for _ in range(min(MAX_WORKERS, n)):
            threading.Thread(target=self._batch_loop, args=(work, job), daemon=True).start()

    def cancel(self):
        self.cancel_flag.set()
        for proc in list(self.procs):
            if proc.poll() is None:
                proc.terminate()
        job = self.job
        if job is not None:
            job["closed"] = True
            self.job = None
        self.set_state("busy", "Cancelling…")
        self.ui(self._cancelled_final)

    def _cancelled_final(self):
        if self.job is not None:
            return
        self.pending_progress = None
        self.set_busy(False)
        self.target_p = 0.0
        self.set_state("cancelled", "Download cancelled")

    def _batch_loop(self, work, job):
        while not self.cancel_flag.is_set() and not job["closed"]:
            try:
                idx, url = work.get_nowait()
            except queue.Empty:
                return
            self.ui(self._file_started, job, idx, url)
            try:
                entry = self._download_one(idx, url, job)
                self.ui(self._file_done, job, idx, entry)
            except yt_dlp.utils.DownloadCancelled:
                self.ui(self._file_failed, job, idx, "Cancelled", True)
            except Exception as ex:
                if self.cancel_flag.is_set():
                    self.ui(self._file_failed, job, idx, "Cancelled", True)
                else:
                    msg = str(ex).replace("ERROR: ", "")
                    self.ui(self._file_failed, job, idx, msg[:90] or "Download failed", False)

    def _job_progress(self, job, idx, frac, done, total, speed, eta):
        """Worker thread: fold one file's progress into the whole batch."""
        with job["lock"]:
            if job["closed"]:
                return
            job["frac"][idx] = frac
            job["done_b"][idx] = done
            job["total_b"][idx] = total
            job["speed"][idx] = speed or 0
            job["eta"][idx] = eta or 0
            n = job["n"]
            p = sum(job["frac"]) / n if n else 0.0
            p = 0.0 if p < 0 else (1.0 if p > 1 else p)
            done_b = sum(job["done_b"])
            total_b = sum(job["total_b"])
            speed_b = sum(job["speed"])
            etas = [e for e in job["eta"] if e]
            eta_b = max(etas) if etas else None
        self.pending_progress = (p, done_b, total_b, speed_b, eta_b)

    def _file_started(self, job, idx, url):
        if job["closed"]:
            return
        job["started"] += 1
        if job["n"] == 1:
            self.set_state("busy", "Downloading…")
        else:
            self.set_state("busy", f"Downloading {min(job['started'], job['n'])} of {job['n']}…")
            self._update_batch_row(idx, "Downloading…")

    def _job_processing(self, job, idx):
        if job["closed"]:
            return
        if job["n"] == 1:
            self.set_state("busy", "Processing…")
        else:
            self.set_state("busy", f"Processing {idx + 1} of {job['n']}…")

    def _batch_hevc(self, job, idx, p):
        if job["closed"]:
            return
        self.set_state("busy", f"Converting {idx + 1} of {job['n']} to HEVC… {int(p * 100)}%")

    def _file_done(self, job, idx, entry):
        if job["closed"]:
            return
        with job["lock"]:
            job["frac"][idx] = 1.0
            job["ok"] += 1
            job["finished"] += 1
        if job["n"] == 1:
            self._done(entry)
            return
        self._record_entry(entry)
        self._update_batch_row(idx, "Done")
        self._finish_batch(job)

    def _file_failed(self, job, idx, msg, cancelled):
        if job["closed"]:
            return
        with job["lock"]:
            job["frac"][idx] = 1.0
            job["fail"] += 1
            job["finished"] += 1
        if job["n"] == 1:
            self._failed(msg, cancelled)
            return
        self._update_batch_row(idx, "Cancelled" if cancelled else msg)
        self._finish_batch(job)

    def _finish_batch(self, job):
        if job["finished"] < job["n"]:
            return
        self.pending_progress = None
        self.job = None
        self.set_busy(False)
        ok, fail, n = job["ok"], job["fail"], job["n"]
        if self.cancel_flag.is_set():
            self.target_p = 0.0
            self.set_state("cancelled", f"Cancelled · {ok} of {n} finished" if ok else "Download cancelled")
        else:
            self.target_p = 1.0
            if fail:
                self.set_state("error", f"{ok} of {n} completed · {fail} failed")
            else:
                self.set_state("done", f"{n} downloads complete")
        self.show_btn.configure(state="normal" if self.last_path else "disabled")
        self._set_text(self.stat["left"], "0 B")
        if self.batch_open:
            self._render_batch_list()

    def _download_one(self, idx, url, job):
        fmt, q, folder, hevc = job["fmt"], job["q"], job["folder"], job["hevc"]
        stage = [0]

        def hook(d):
            if self.cancel_flag.is_set() or job["closed"]:
                raise yt_dlp.utils.DownloadCancelled()
            if d["status"] == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                done = d.get("downloaded_bytes") or 0
                parts = len(d.get("info_dict", {}).get("requested_formats") or []) or 1
                frac = (done / total) if total else 0
                self._job_progress(job, idx, min(1.0, (stage[0] + frac) / parts), done, total,
                                   d.get("speed"), d.get("eta"))
            elif d["status"] == "finished":
                stage[0] += 1

        def pp_hook(d):
            if d["status"] == "started":
                self.ui(self._job_processing, job, idx)

        opts = {"outtmpl": os.path.join(folder, "%(title)s.%(ext)s"), "noplaylist": True, "quiet": True,
                "no_warnings": True, "progress_hooks": [hook], "postprocessor_hooks": [pp_hook],
                "windowsfilenames": True}
        original = fmt == "MP3" and q == M4A_Q
        if original:
            opts["format"] = "bestaudio[ext=m4a]/bestaudio"
        elif fmt == "MP3":
            opts["format"] = "bestaudio/best"
            opts["postprocessors"] = [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3",
                                       "preferredquality": re.match(r"\d+", q).group()}]
        else:
            m = re.match(r"\d+", q)
            cap = f"[height<={m.group()}]" if m else ""
            opts["format"] = f"bestvideo{cap}[ext=mp4]+bestaudio[ext=m4a]/best{cap}[ext=mp4]/best{cap}"
            opts["merge_output_format"] = "mp4"
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            rd = info.get("requested_downloads") or []
            path = rd[0].get("filepath") if rd else None
            if not path or not os.path.exists(path):
                base = os.path.splitext(ydl.prepare_filename(info))[0]
                path = base + (".m4a" if original else ".mp3" if fmt == "MP3" else ".mp4")
        if self.cancel_flag.is_set() or job["closed"]:
            raise yt_dlp.utils.DownloadCancelled()
        if fmt == "MP4" and hevc:
            if job["n"] == 1:
                path = self._to_hevc(path, info.get("duration"))
            else:
                path = self._to_hevc(path, info.get("duration"),
                                     lambda p, i=idx: self.ui(self._batch_hevc, job, i, p))
        return {"title": info.get("title", Path(path).stem), "url": url, "vid": info.get("id"),
                "format": (Path(path).suffix[1:].upper() or "M4A") if original else fmt,
                "quality": q, "path": os.path.normpath(path), "size": os.path.getsize(path),
                "time": datetime.now().isoformat(timespec="seconds")}

    def _to_hevc(self, path, duration=None, report=None):
        if report is None:
            self.ui(self.set_state, "busy", "Converting to HEVC…")
            report = lambda p: self.ui(self._hevc_progress, p)
        report(0.0)
        out = str(Path(path).with_suffix(".hevc.mp4"))
        total_us = (duration or 0) * 1_000_000
        for name, params in hevc_encoders():
            cmd = ["ffmpeg", "-y", "-v", "error", "-nostats", "-progress", "pipe:1", "-i", path,
                   "-c:v", name, *params, "-tag:v", "hvc1", "-c:a", "copy", out]
            try:
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                        text=True, errors="ignore", creationflags=NO_WINDOW)
            except OSError:
                raise RuntimeError("HEVC conversion failed. Is ffmpeg installed?")
            self.procs.add(proc)
            try:
                for line in proc.stdout:
                    if total_us and line.startswith("out_time_us="):
                        try:
                            us = int(line.split("=", 1)[1])
                        except ValueError:
                            continue
                        report(max(0.0, min(0.99, us / total_us)))
                proc.wait()
            finally:
                self.procs.discard(proc)
            if self.cancel_flag.is_set():
                if os.path.exists(out):
                    os.remove(out)
                raise yt_dlp.utils.DownloadCancelled()
            if proc.returncode == 0:
                os.replace(out, path)
                return path
            if os.path.exists(out):
                os.remove(out)
        raise RuntimeError("HEVC conversion failed. Is ffmpeg installed?")

    def _hevc_progress(self, p):
        self.target_p = p
        self._set_text(self.status_lbl, f"Converting to HEVC… {int(p * 100)}%")

    def _progress(self, p, done, total, speed, eta):
        self.target_p = p
        if total:
            self._set_text(self.stat["done"], f"{fmt_bytes(done)} / {fmt_bytes(total)}")
            self._set_text(self.stat["left"], fmt_bytes(max(0, total - done)))
        if speed:
            self._set_text(self.stat["speed"],
                           f"{fmt_bytes(speed)}/s · {fmt_dur(eta) if eta else '—'}")

    def _record_entry(self, entry):
        self.history.insert(0, entry)
        del self.history[HISTORY_LIMIT:]
        save_json(HISTORY_FILE, self.history)
        self.last_path = entry["path"]

    def _done(self, entry):
        self.pending_progress = None
        self.job = None
        self._record_entry(entry)
        self.target_p = 1.0
        self.set_busy(False)
        self.set_state("done", "Download successful")
        self.show_btn.configure(state="normal")
        self.stat["done"].configure(text=fmt_bytes(entry["size"]))
        self.stat["left"].configure(text="0 B")

    def _failed(self, msg, cancelled):
        self.pending_progress = None
        self.job = None
        self.set_busy(False)
        self.target_p = 0.0
        self.set_state("cancelled" if cancelled else "error", "Download cancelled" if cancelled else msg)


if __name__ == "__main__":
    App().mainloop()