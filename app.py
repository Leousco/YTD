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


FONT_CANDIDATES = {
    "win32": ["Segoe UI"],
    "darwin": ["SF Pro Text", "Helvetica Neue"],
}
FONT_CANDIDATES_DEFAULT = ["Inter", "Noto Sans", "Ubuntu", "Cantarell", "DejaVu Sans"]


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("dark")
        installed = set(tkfont.families(self))
        for name in FONT_CANDIDATES.get(sys.platform, FONT_CANDIDATES_DEFAULT):
            if name in installed:
                ctk.ThemeManager.theme["CTkFont"]["family"] = name
                break
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
        self.proc = None
        self.busy = False
        self.info = None
        self.fetch_id = 0
        self.deb = None
        self.last_clip = ""
        self.last_path = None
        self.target_p = 0.0
        self.shown_p = 0.0
        self.pulse_t = 0.0
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
        self.last_pulse = time.perf_counter()
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
        self.after(30, self.poll)
        self.tick()
        self.pulse()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.attributes("-alpha", 0.0)
        self.fade_in()

    def fade_in(self, t0=None):
        t0 = t0 or time.perf_counter()
        a = min(1.0, (time.perf_counter() - t0) / 0.22)
        self.attributes("-alpha", a)
        if a < 1.0:
            self.after(10, lambda: self.fade_in(t0))

    def ui(self, fn, *args):
        self.ui_q.put((fn, args))

    def poll(self):
        pp = self.pending_progress
        if pp is not None:
            self.pending_progress = None
            self._progress(*pp)
        try:
            while True:
                fn, args = self.ui_q.get_nowait()
                fn(*args)
        except queue.Empty:
            pass
        self.after(30, self.poll)

    def font(self, size, bold=False):
        key = (size, bold)
        f = self.font_cache.get(key)
        if f is None:
            f = self.font_cache[key] = ctk.CTkFont(size=size, weight="bold" if bold else "normal")
        return f

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
            font=ctk.CTkFont(size=13, weight="bold"))
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
        self.label(link, "MEDIA LINK", 10, True, C["muted"]).pack(anchor="w", padx=16, pady=(12, 5))
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
        self.url_var.trace_add("write", lambda *_: self.sync_url_hint())
        for text, cmd in (("Paste", self.paste), ("Clear", self.clear)):
            ctk.CTkButton(row, text=text, width=68, height=38, corner_radius=11, fg_color=C["card2"],
                          hover_color=C["border"], border_width=1, border_color=C["border"],
                          command=cmd).pack(side="left", padx=(8, 0))

        self.preview = ctk.CTkFrame(link, fg_color=C["card2"], corner_radius=12, height=42)
        self.preview.pack(fill="x", padx=16, pady=(10, 12))
        self.preview.pack_propagate(False)
        self.info_row = ctk.CTkFrame(self.preview, fg_color="transparent")
        self.info_row.pack(fill="both", expand=True, padx=14)
        self.title_lbl = self.label(self.info_row, "Paste a link to get started", 12, True, anchor="w")
        self.title_lbl.pack(side="left")
        self.meta_lbl = self.label(self.info_row, "Length and estimated size will show here", 12,
                                   color=C["cyan"], anchor="w")
        self.meta_lbl.pack(side="left", padx=(14, 0))
        self.title_full = "Paste a link to get started"
        self.info_row.bind("<Configure>", lambda e: self.queue_fit())

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

        self.label(opts, "SAVE TO", 10, True, C["muted"]).pack(anchor="w", padx=16, pady=(11, 5))
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
                                    hover_color=C["accent_h"], font=ctk.CTkFont(size=15, weight="bold"),
                                    command=self.start_download)
        self.dl_btn.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self.cancel_btn = ctk.CTkButton(act, text="Cancel", height=44, corner_radius=12, fg_color="#2a1f2e",
                                        hover_color=C["red_h"], text_color_disabled="#5a4a5e", state="disabled",
                                        font=ctk.CTkFont(size=15, weight="bold"), command=self.cancel)
        self.cancel_btn.grid(row=0, column=1, sticky="ew", padx=(6, 0))

        prog = self.card(pg)
        prog.pack(fill="x", pady=(9, 12))
        sr = ctk.CTkFrame(prog, fg_color="transparent")
        sr.pack(fill="x", padx=16, pady=(11, 6))
        self.dot = ctk.CTkLabel(sr, text="●", text_color=C["green"], font=ctk.CTkFont(size=14))
        self.dot.pack(side="left")
        self.status_lbl = self.label(sr, "Ready", 14, True, anchor="w")
        self.status_lbl.pack(side="left", padx=8)
        self.pct_lbl = self.label(sr, "0%", 13, True, C["cyan"])
        self.pct_lbl.pack(side="right")
        self.show_btn = ctk.CTkButton(sr, text="Show file", width=84, height=28, corner_radius=10,
                                      fg_color=C["card2"], hover_color=C["border"], state="disabled",
                                      command=lambda: self.last_path and reveal(self.last_path))
        self.show_btn.pack(side="right", padx=12)
        self.bar = ctk.CTkProgressBar(prog, height=8, corner_radius=4, fg_color=C["card2"],
                                      progress_color=C["accent"])
        self.bar.set(0)
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
            self.label(inner, title, 9, True, C["muted"], height=14).pack(anchor="w")
            v = self.label(inner, "—", 15, True, height=20)
            v.pack(anchor="w")
            v.configure(font=ctk.CTkFont(size=13, weight="bold"))
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
        rid = self.render_id
        for w in self.hist_list.winfo_children():
            w.destroy()
        self.count_lbl.configure(text=f"{len(items)} download{'s' if len(items) != 1 else ''}, newest first")
        if not items:
            self.label(self.hist_list, "Nothing here yet. Your downloads will appear in this list.", 13,
                       color=C["muted"]).pack(pady=60)
            return
        ops = []
        last_day = None
        for i, e in enumerate(shown):
            dt = datetime.fromisoformat(e["time"])
            day = self.day_label(dt)
            if day != last_day:
                ops.append((day, None, None))
                last_day = day
            ops.append((None, e, (dt, i == 0 and not q)))
        self.render_chunk(ops, 0, rid)

    def render_chunk(self, ops, start, rid):
        if rid != self.render_id:
            return
        for day, e, extra in ops[start:start + 6]:
            if e is None:
                self.label(self.hist_list, day.upper(), 11, True, C["muted"], anchor="w").pack(
                    anchor="w", pady=(10, 2), padx=4)
            else:
                self.history_row(e, extra[0], extra[1])
        if start + 6 < len(ops):
            self.after(12, lambda: self.render_chunk(ops, start + 6, rid))

    def day_label(self, dt):
        d = (datetime.now().date() - dt.date()).days
        if d == 0:
            return "Today"
        if d == 1:
            return "Yesterday"
        return dt.strftime("%a, %b %d, %Y")

    def history_row(self, e, dt, latest):
        exists = os.path.exists(e["path"])
        row = ctk.CTkFrame(self.hist_list, fg_color=C["card"], corner_radius=14, border_width=1,
                           border_color=C["green"] if latest else C["border"])
        row.pack(fill="x", pady=4, padx=2)
        row.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(row, text=e["format"], width=48, height=48, corner_radius=12,
                     fg_color=C["purple"] if e["format"] == "MP4" else C["accent"], text_color="white",
                     font=self.font(12, True)).grid(row=0, column=0, rowspan=2, padx=12, pady=12)
        tf = ctk.CTkFrame(row, fg_color="transparent")
        tf.grid(row=0, column=1, sticky="w", pady=(12, 0))
        title = e["title"] if len(e["title"]) <= 52 else e["title"][:51] + "…"
        self.label(tf, title, 13, True, C["text"] if exists else C["muted"]).pack(side="left")
        if latest:
            ctk.CTkLabel(tf, text="LATEST", fg_color=C["green"], text_color="#06281c", corner_radius=6,
                         height=18, width=52, font=self.font(10, True)).pack(side="left", padx=8)
        sub = f"{dt.strftime('%I:%M %p').lstrip('0')}  ·  {fmt_bytes(e.get('size'))}  ·  {e.get('quality', '')}"
        if not exists:
            sub += "  ·  file moved or deleted"
        self.label(row, sub, 11, color=C["red_h"] if not exists else C["muted"]).grid(
            row=1, column=1, sticky="w", pady=(0, 12))
        btns = ctk.CTkFrame(row, fg_color="transparent")
        btns.grid(row=0, column=2, rowspan=2, padx=10)
        specs = [("Open", lambda: open_path(e["path"]), exists), ("Folder", lambda: reveal(e["path"]), exists),
                 ("Link", lambda: self.copy_link(e["url"]), True)]
        for text, cmd, ok in specs:
            ctk.CTkButton(btns, text=text, width=56, height=30, corner_radius=9, fg_color=C["card2"],
                          hover_color=C["border"], font=self.font(11),
                          state="normal" if ok else "disabled", command=cmd).pack(side="left", padx=2)
        ctk.CTkButton(btns, text="✕", width=30, height=30, corner_radius=9, fg_color=C["card2"],
                      hover_color=C["red"], font=self.font(11),
                      command=lambda: self.delete_entry(e)).pack(side="left", padx=2)

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
        self.bar.configure(progress_color=accent)
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
        self.fetch_id += 1
        self.info = None
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

    def animate_height(self, widget, target):
        cur = widget.cget("height")
        if abs(cur - target) <= 2:
            widget.configure(height=target)
            return
        widget.configure(height=int(cur + (target - cur) * 0.35))
        self.after(16, lambda: self.animate_height(widget, target))

    def on_url_change(self, *_):
        if self.deb:
            self.after_cancel(self.deb)
        self.deb = self.after(450, self.fetch_info)

    def fetch_info(self):
        url = self.url_var.get().strip()
        self.fetch_id += 1
        fid = self.fetch_id
        self.info = None
        if not YT_RE.search(url):
            self.set_title("Paste a link to get started" if not url else "That doesn't look like a YouTube link")
            self.set_meta("Length and estimated size will show here")
            return
        self.set_title("Fetching details…")
        self.set_meta("")
        threading.Thread(target=self._fetch, args=(url, fid), daemon=True).start()

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
        t = info.get("title", "Untitled")
        self.set_title(t)
        self.refresh_preview_meta()

    def refresh_preview_meta(self):
        if not self.info:
            return
        size = estimate_size(self.info, self.fmt, self.quality_var.get())
        parts = [fmt_dur(self.info.get("duration")), f"~ {fmt_bytes(size)}" if size else ""]
        self.set_meta("  ·  ".join(p for p in parts if p))
    
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
        self.title_full = text
        self.title_lbl.configure(text=text)
        self.queue_fit(10)

    def set_meta(self, text):
        self.meta_lbl.configure(text=text)
        self.queue_fit(10)

    def fit_title(self):
        self.fit_job = None
        text = self.title_full
        avail = self.info_row.winfo_width() - self.meta_lbl.winfo_reqwidth() - 14 - 12
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

    def current_vid(self):
        m = VID_RE.search(self.url_var.get())
        return m.group(1) if m else (self.info or {}).get("id")

    def find_dup(self, fmt):
        vid = self.current_vid()
        if not vid:
            return None
        for e in self.history:
            if e.get("vid") == vid and e["format"] == fmt and os.path.exists(e["path"]):
                return e
        return None

    def set_state(self, kind, text):
        self.dot_color = {"ready": C["green"], "busy": C["accent"], "error": C["red_h"], "done": C["green"],
                          "cancelled": C["accent"]}[kind]
        text_color = {"busy": C["cyan"], "cancelled": C["accent"], "done": C["green"],
                      "error": C["red_h"]}.get(kind, C["text"])
        self.dot.configure(text_color=self.dot_color)
        self.status_lbl.configure(text=text, text_color=text_color)

    def tick(self):
        now = time.perf_counter()
        dt = min(0.05, now - self.last_tick)
        self.last_tick = now
        diff = self.target_p - self.shown_p
        if diff:
            self.shown_p = self.target_p if abs(diff) <= 0.0005 else self.shown_p + diff * (1 - 0.82 ** (dt * 60))
            self.bar.set(max(0, min(1, self.shown_p)))
            self.pct_lbl.configure(text=f"{int(self.shown_p * 100)}%")
        self.after(16, self.tick)

    def pulse(self):
        now = time.perf_counter()
        dt = min(0.1, now - self.last_pulse)
        self.last_pulse = now
        if self.busy:
            self.pulse_t += 6.25 * dt
            k = (math.sin(self.pulse_t) + 1) / 2
            self.dot.configure(text_color=lerp_color(self.active_accent, C["card"], k * 0.8))
        self.after(40, self.pulse)

    def set_busy(self, b):
        self.busy = b
        self.dl_btn.configure(state="disabled" if b else "normal", text="Downloading…" if b else "Download")
        self.cancel_btn.configure(state="normal" if b else "disabled", fg_color=C["red"] if b else "#2a1f2e")

    def start_download(self):
        url = self.url_var.get().strip()
        if not YT_RE.search(url):
            self.url_entry.configure(border_color=C["red"])
            self.after(900, lambda: self.url_entry.configure(border_color=C["border"]))
            self.set_state("error", "Paste a valid YouTube link first")
            return
        dup = self.find_dup("M4A" if self.fmt == "MP3" and self.quality_var.get() == M4A_Q else self.fmt)
        if dup:
            when = datetime.fromisoformat(dup["time"]).strftime("%b %d at %I:%M %p")
            if not messagebox.askyesno("Already downloaded",
                                       f"You already downloaded this as {dup['format']} on {when}:\n\n{dup['title']}\n\nDownload it again?"):
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
        self.set_busy(True)
        self.set_state("busy", "Starting…")
        threading.Thread(target=self._worker, args=(url, self.fmt, self.quality_var.get(), folder,
                                                    self.hevc_var.get()), daemon=True).start()

    def cancel(self):
        self.cancel_flag.set()
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
        self.set_state("busy", "Cancelling…")

    def _worker(self, url, fmt, q, folder, hevc):
        stage = [0]

        def hook(d):
            if self.cancel_flag.is_set():
                raise yt_dlp.utils.DownloadCancelled()
            if d["status"] == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                done = d.get("downloaded_bytes") or 0
                n = len(d.get("info_dict", {}).get("requested_formats") or []) or 1
                frac = (done / total) if total else 0
                p = min(1.0, (stage[0] + frac) / n)
                self.pending_progress = (p, done, total, d.get("speed"), d.get("eta"))
            elif d["status"] == "finished":
                stage[0] += 1

        def pp_hook(d):
            if d["status"] == "started":
                self.ui(self.set_state, "busy", "Processing…")

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
        try:
            self.ui(self.set_state, "busy", "Downloading…")
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                rd = info.get("requested_downloads") or []
                path = rd[0].get("filepath") if rd else None
                if not path or not os.path.exists(path):
                    base = os.path.splitext(ydl.prepare_filename(info))[0]
                    path = base + (".m4a" if original else ".mp3" if fmt == "MP3" else ".mp4")
            if self.cancel_flag.is_set():
                raise yt_dlp.utils.DownloadCancelled()
            if fmt == "MP4" and hevc:
                path = self._to_hevc(path, info.get("duration"))
            entry = {"title": info.get("title", Path(path).stem), "url": url, "vid": info.get("id"),
                     "format": (Path(path).suffix[1:].upper() or "M4A") if original else fmt, "quality": q, "path": os.path.normpath(path),
                     "size": os.path.getsize(path), "time": datetime.now().isoformat(timespec="seconds")}
            self.ui(self._done, entry)
        except yt_dlp.utils.DownloadCancelled:
            self.ui(self._failed, "Cancelled", True)
        except Exception as ex:
            if self.cancel_flag.is_set():
                self.ui(self._failed, "Cancelled", True)
            else:
                msg = str(ex).replace("ERROR: ", "")
                self.ui(self._failed, msg[:90] or "Download failed", False)

    def _to_hevc(self, path, duration=None):
        self.ui(self.set_state, "busy", "Converting to HEVC…")
        self.ui(self._hevc_progress, 0.0)
        out = str(Path(path).with_suffix(".hevc.mp4"))
        total_us = (duration or 0) * 1_000_000
        for name, params in hevc_encoders():
            cmd = ["ffmpeg", "-y", "-v", "error", "-nostats", "-progress", "pipe:1", "-i", path,
                   "-c:v", name, *params, "-tag:v", "hvc1", "-c:a", "copy", out]
            try:
                self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                             text=True, errors="ignore", creationflags=NO_WINDOW)
            except OSError:
                raise RuntimeError("HEVC conversion failed. Is ffmpeg installed?")
            for line in self.proc.stdout:
                if total_us and line.startswith("out_time_us="):
                    try:
                        us = int(line.split("=", 1)[1])
                    except ValueError:
                        continue
                    self.ui(self._hevc_progress, max(0.0, min(0.99, us / total_us)))
            self.proc.wait()
            if self.cancel_flag.is_set():
                if os.path.exists(out):
                    os.remove(out)
                raise yt_dlp.utils.DownloadCancelled()
            if self.proc.returncode == 0:
                os.replace(out, path)
                return path
            if os.path.exists(out):
                os.remove(out)
        raise RuntimeError("HEVC conversion failed. Is ffmpeg installed?")

    def _hevc_progress(self, p):
        self.target_p = p
        self.status_lbl.configure(text=f"Converting to HEVC… {int(p * 100)}%")

    def _progress(self, p, done, total, speed, eta):
        self.target_p = p
        if total:
            self.stat["done"].configure(text=f"{fmt_bytes(done)} / {fmt_bytes(total)}")
            self.stat["left"].configure(text=fmt_bytes(max(0, total - done)))
        if speed:
            self.stat["speed"].configure(text=f"{fmt_bytes(speed)}/s · {fmt_dur(eta) if eta else '—'}")

    def _done(self, entry):
        self.pending_progress = None
        self.history.insert(0, entry)
        del self.history[HISTORY_LIMIT:]
        save_json(HISTORY_FILE, self.history)
        self.last_path = entry["path"]
        self.target_p = 1.0
        self.set_busy(False)
        t = entry["title"]
        self.set_state("done", "Download successful")
        self.show_btn.configure(state="normal")
        self.stat["done"].configure(text=fmt_bytes(entry["size"]))
        self.stat["left"].configure(text="0 B")

    def _failed(self, msg, cancelled):
        self.pending_progress = None
        self.set_busy(False)
        self.target_p = 0.0
        self.set_state("cancelled" if cancelled else "error", "Download cancelled" if cancelled else msg)


if __name__ == "__main__":
    App().mainloop()