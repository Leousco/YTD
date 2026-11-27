"""Download engine extracted from the original Streamgrab Tk application."""
import json
import os
import re
import statistics
import subprocess
import sys
import threading
from urllib.parse import urlsplit
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import yt_dlp

CONFIG_DIR = Path.home() / ".streamgrab"
HISTORY_FILE = CONFIG_DIR / "history.json"
SETTINGS_FILE = CONFIG_DIR / "settings.json"
HISTORY_LIMIT = 10
BATCH_LIMIT = 20
MAX_WORKERS = 2
HEVC_FALLBACK_RATIO = 0.55
M4A_Q = "M4A \ufffd Original"
MP3_Q = [M4A_Q, "128 kbps", "192 kbps", "256 kbps", "320 kbps"]
MP4_Q = ["Best available", "1080p", "720p", "480p", "360p"]
VID_RE = re.compile(r"(?:v=|youtu\.be/|shorts/|embed/)([\w-]{11})")
URL_RE = re.compile(r"https?://[^\s<>\"']+", re.I)
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
HEVC_HW = (("hevc_nvenc", ["-preset", "p5", "-rc", "vbr", "-cq", "28", "-b:v", "0"]),
           ("hevc_qsv", ["-preset", "medium", "-global_quality", "27"]),
           ("hevc_amf", ["-quality", "balanced", "-rc", "cqp", "-qp_i", "28", "-qp_p", "28"]),
           ("hevc_videotoolbox", ["-q:v", "55"]))
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
    urls = []
    for raw in URL_RE.findall(text or ""):
        url = raw.rstrip(").,;]}'\"")
        try:
            parsed = urlsplit(url)
            host = parsed.hostname
        except ValueError:
            parsed = None
            host = None
        if url not in urls and parsed and parsed.scheme in ("http", "https") and host:
            urls.append(url)
            if len(urls) >= BATCH_LIMIT:
                break
    return urls


def vid_of(url):
    m = VID_RE.search(url or "")
    return m.group(1) if m else url


def error_message(ex):
    message = str(ex).replace("ERROR: ", "").strip()
    if re.search(r"\bdrm\b|digital rights management", message, re.I):
        return "DRM-protected media is not supported."
    return message[:180] or "Download failed"


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


def estimate_size(info, fmt, q, hevc=False, hevc_ratio=HEVC_FALLBACK_RATIO):
    dur = info.get("duration") or 0
    formats = info.get("formats") or []
    def finish(size):
        if size is not None and fmt == "MP4" and hevc:
            return size * hevc_ratio
        return size
    def fs(f):
        return f.get("filesize") or f.get("filesize_approx") or (f.get("tbr") or 0) * 1000 / 8 * dur
    def audio_fs(f):
        return f.get("filesize") or f.get("filesize_approx") or (f.get("abr") or f.get("tbr") or 0) * 1000 / 8 * dur
    def info_fs():
        return info.get("filesize") or info.get("filesize_approx") or None
    if fmt == "MP3" and q == M4A_Q:
        auds = [f for f in formats if f.get("acodec") not in (None, "none") and f.get("vcodec") in (None, "none")]
        auds = [f for f in auds if f.get("ext") == "m4a"] or auds
        if not auds:
            auds = [f for f in formats if f.get("acodec") not in (None, "none")]
        a = max(auds, key=audio_fs, default=None)
        return finish((audio_fs(a) if a else 0) or info_fs())
    if fmt == "MP3":
        return finish(dur * int(re.match(r"\d+", q).group()) * 1000 / 8 or info_fs())
    m = re.match(r"\d+", q)
    h = int(m.group()) if m else 99999
    vids = [f for f in formats if f.get("vcodec") not in (None, "none") and f.get("acodec") in (None, "none") and (f.get("height") or 0) <= h]
    auds = [f for f in formats if f.get("acodec") not in (None, "none") and f.get("vcodec") in (None, "none")]
    vids = [f for f in vids if f.get("ext") == "mp4"] or vids
    auds = [f for f in auds if f.get("ext") == "m4a"] or auds
    v = max(vids, key=lambda f: ((f.get("height") or 0), fs(f)), default=None)
    a = max(auds, key=fs, default=None)
    candidates = []
    if v and a and fs(v) and fs(a):
        candidates.append(((v.get("height") or 0), fs(v) + fs(a)))
    combined = [f for f in formats if f.get("vcodec") not in (None, "none") and f.get("acodec") not in (None, "none") and (f.get("height") or 0) <= h]
    candidates.extend(((f.get("height") or 0, fs(f)) for f in combined if fs(f)))
    size = max(candidates, key=lambda candidate: (candidate[0], candidate[1]))[1] if candidates else info_fs()
    return finish(size)


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
        if name in listing and ffmpeg_works(["-f", "lavfi", "-i", "color=c=black:s=640x360:r=30:d=0.5", "-c:v", name, *params, "-f", "null", "-"]):
            found.append((name, params)); break
    found.append(HEVC_SW)
    HEVC_CHOICE.append(found)
    return found


class DownloadCancelled(Exception):
    pass


class DownloadManager:
    """Threaded two-worker downloader that publishes serializable job snapshots."""
    def __init__(self):
        self.lock = threading.RLock()
        self.changed = threading.Event()
        self.cancel_event = threading.Event()
        self.procs = set()
        self.job = None
        self.history = load_json(HISTORY_FILE, [])[:HISTORY_LIMIT]
        self.settings = load_json(SETTINGS_FILE, {})
        if len(load_json(HISTORY_FILE, [])) > HISTORY_LIMIT:
            save_json(HISTORY_FILE, self.history)
        self.last_path = None

    def settings_update(self, values):
        with self.lock:
            self.settings.update(values)
            save_json(SETTINGS_FILE, self.settings)
            return dict(self.settings)

    def hevc_ratio(self):
        with self.lock:
            ratios = self.settings.get("hevc_ratios", [])
            if not isinstance(ratios, list):
                ratios = []
            valid = [r for r in ratios if isinstance(r, (int, float)) and not isinstance(r, bool) and 0.2 <= r <= 1.2]
            return statistics.median(valid) if len(valid) >= 3 else HEVC_FALLBACK_RATIO

    def get_history(self, q=""):
        with self.lock:
            return [dict(e, exists=os.path.exists(e.get("path", ""))) for e in self.history if q.lower() in e.get("title", "").lower()]

    def delete_history(self, entry_id=None):
        with self.lock:
            if entry_id is None: self.history = []
            else: self.history = [e for i, e in enumerate(self.history) if str(i) != str(entry_id) and e.get("time") != entry_id]
            save_json(HISTORY_FILE, self.history)

    def info(self, urls, fmt, quality, hevc=False):
        if quality == "M4A · Original": quality = M4A_Q
        urls = parse_urls("\n".join(urls))
        if not urls: raise ValueError("No valid HTTP(S) URLs")
        hevc_est = fmt == "MP4" and hevc
        hevc_ratio = self.hevc_ratio() if hevc_est else HEVC_FALLBACK_RATIO
        def fetch(url):
            with yt_dlp.YoutubeDL({"quiet": True, "noplaylist": True, "skip_download": True, "no_warnings": True}) as ydl:
                return ydl.extract_info(url, download=False)
        results = [None] * len(urls)
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = {pool.submit(fetch, u): i for i, u in enumerate(urls)}
            for future in as_completed(futures):
                i = futures[future]; url = urls[i]
                try:
                    d = future.result()
                    size = estimate_size(d, fmt, quality, hevc=hevc_est, hevc_ratio=hevc_ratio)
                    results[i] = {"url": url, "id": d.get("id"), "title": d.get("title", "Untitled"), "duration": d.get("duration"), "size": size}
                    if hevc_est and size is not None: results[i]["hevc_est"] = True
                except Exception as ex: results[i] = {"url": url, "error": error_message(ex)}
        return results

    def duplicates(self, urls, fmt):
        keyfmt = "M4A" if fmt == "MP3" else fmt
        by_vid = {e.get("vid"): e for e in self.history if e.get("format") == keyfmt and os.path.exists(e.get("path", ""))}
        return [dict(url=u, entry=e) for u in urls if (e := by_vid.get(vid_of(u))) is not None]

    def start(self, urls, fmt, quality, folder, hevc, force=False):
        if quality == "M4A · Original": quality = M4A_Q
        urls = parse_urls("\n".join(urls))
        if not urls: raise ValueError("No valid HTTP(S) URLs")
        if len(urls) > BATCH_LIMIT: raise ValueError("At most 20 links can be downloaded at once")
        if fmt not in ("MP3", "MP4"): raise ValueError("Invalid format")
        if quality not in (MP3_Q if fmt == "MP3" else MP4_Q): raise ValueError("Invalid quality")
        if hevc and fmt != "MP4": raise ValueError("HEVC is only available for MP4")
        folder = os.path.abspath(os.path.expanduser(folder))
        os.makedirs(folder, exist_ok=True)
        dups = self.duplicates(urls, fmt)
        if dups and not force: return {"duplicates": dups}
        with self.lock:
            if self.job and not self.job["finished"]: raise ValueError("A download is already running")
            self.cancel_event.clear()
            job = {"id": os.urandom(8).hex(), "n": len(urls), "urls": urls, "fmt": fmt, "q": quality, "folder": folder, "hevc": hevc,
                   "frac": [0.0]*len(urls), "done_b": [0]*len(urls), "total_b": [0]*len(urls), "speed": [0]*len(urls), "eta": [0]*len(urls),
                   "files": [{"url": u, "status": "Reading", "title": vid_of(u) or u, "duration": None, "size": None} for u in urls],
                   "started": 0, "ok": 0, "fail": 0, "finished": False, "status": "Starting", "error": None, "last_path": None, "updated": 0}
            self.job = job
            self.changed.set()
        threading.Thread(target=self._run, args=(job,), daemon=True).start()
        return {"job_id": job["id"]}

    def snapshot(self):
        with self.lock:
            if not self.job: return {"status": "Ready", "percent": 0, "files": []}
            j = self.job
            total = sum(j["total_b"]); done = sum(j["done_b"]); speed = sum(j["speed"]); eta = max(j["eta"], default=0)
            return {"job_id": j["id"], "status": j["status"], "percent": round(sum(j["frac"])/j["n"]*100, 1), "downloaded": done, "total": total,
                    "remaining": max(0,total-done), "speed": speed, "eta": eta or None, "files": [dict(f) for f in j["files"]], "last_path": j["last_path"], "error": j["error"], "finished": j["finished"], "ok": j["ok"], "fail": j["fail"]}

    def cancel(self):
        self.cancel_event.set()
        self.changed.set()
        with self.lock:
            if self.job and not self.job["finished"]:
                self.job["status"] = "Cancelled"
                for p in list(self.procs):
                    if p.poll() is None: p.terminate()
                return True
        return False

    def shutdown(self):
        self.cancel()

    def _run(self, job):
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = [pool.submit(self._one, job, i, u) for i,u in enumerate(job["urls"])]
            for future in as_completed(futures):
                pass
        with self.lock:
            if self.cancel_event.is_set(): job["status"] = "Cancelled"
            elif job["fail"]: job["status"] = f"{job['ok']} of {job['n']} completed · {job['fail']} failed"
            else: job["status"] = "Download successful" if job["n"] == 1 else f"{job['n']} downloads complete"
            job["finished"] = True
            self.changed.set()

    def _one(self, j, idx, url):
        stage = [0]
        def update(**kw):
            with self.lock:
                if self.job is j: j.update(kw); j["updated"] += 1
        def hook(d):
            if self.cancel_event.is_set(): raise yt_dlp.utils.DownloadCancelled()
            if d["status"] == "downloading":
                total=d.get("total_bytes") or d.get("total_bytes_estimate") or 0; done=d.get("downloaded_bytes") or 0
                parts=len(d.get("info_dict",{}).get("requested_formats") or []) or 1
                with self.lock:
                    j["frac"][idx]=min(1,(stage[0]+(done/total if total else 0))/parts); j["done_b"][idx]=done; j["total_b"][idx]=total; j["speed"][idx]=d.get("speed") or 0; j["eta"][idx]=d.get("eta") or 0; j["files"][idx]["status"]="Downloading"
                    self.changed.set()
            elif d["status"] == "finished":
                stage[0] += 1
                with self.lock: j["files"][idx]["status"]="Processing"
                self.changed.set()
        try:
            with self.lock:
                j["started"] += 1; j["status"] = "Downloading" if j["n"] == 1 else f"Downloading {min(j['started'],j['n'])} of {j['n']}"
                j["files"][idx]["status"]="Downloading"
                self.changed.set()
            fmt,q,folder,hevc=j["fmt"],j["q"],j["folder"],j["hevc"]
            original=fmt=="MP3" and q==M4A_Q
            opts={"outtmpl":os.path.join(folder,"%(title)s.%(ext)s"),"noplaylist":True,"quiet":True,"no_warnings":True,"progress_hooks":[hook],"windowsfilenames":True}
            if original: opts["format"]="bestaudio[ext=m4a]/bestaudio"
            elif fmt=="MP3":
                opts["format"]="bestaudio/best"; opts["postprocessors"]=[{"key":"FFmpegExtractAudio","preferredcodec":"mp3","preferredquality":re.match(r"\d+",q).group()}]
            else:
                m=re.match(r"\d+",q); cap=f"[height<={m.group()}]" if m else ""; opts["format"]=f"bestvideo{cap}[ext=mp4]+bestaudio[ext=m4a]/best{cap}[ext=mp4]/best{cap}"; opts["merge_output_format"]="mp4"
            with yt_dlp.YoutubeDL(opts) as ydl:
                info=ydl.extract_info(url,download=True); rd=info.get("requested_downloads") or []; path=rd[0].get("filepath") if rd else None
                if not path or not os.path.exists(path): path=os.path.splitext(ydl.prepare_filename(info))[0]+(".m4a" if original else ".mp3" if fmt=="MP3" else ".mp4")
            if self.cancel_event.is_set(): raise yt_dlp.utils.DownloadCancelled()
            if fmt=="MP4" and hevc:
                original_size=os.path.getsize(path)
                path=self._to_hevc(path,info.get("duration"),j,idx)
                final_size=os.path.getsize(path)
                if original_size > 0:
                    with self.lock:
                        ratios=self.settings.get("hevc_ratios",[])
                        if not isinstance(ratios,list): ratios=[]
                        self.settings_update({"hevc_ratios":(ratios+[final_size/original_size])[-10:]})
            entry={"title":info.get("title",Path(path).stem),"url":url,"vid":vid_of(url),"format":(Path(path).suffix[1:].upper() or "M4A") if original else fmt,"quality":q,"path":os.path.normpath(path),"size":os.path.getsize(path),"time":datetime.now().isoformat(timespec="seconds")}
            with self.lock:
                self.history.insert(0,entry); del self.history[HISTORY_LIMIT:]; save_json(HISTORY_FILE,self.history); j["ok"]+=1; j["files"][idx].update(status="Done",title=entry["title"]); j["last_path"]=entry["path"]; j["frac"][idx]=1
                self.changed.set()
        except (yt_dlp.utils.DownloadCancelled, DownloadCancelled):
            with self.lock: j["fail"]+=1; j["files"][idx]["status"]="Cancelled"; j["frac"][idx]=0; self.changed.set()
        except Exception as ex:
            with self.lock: j["fail"]+=1; j["files"][idx]["status"]=error_message(ex)[:120]; j["frac"][idx]=1; self.changed.set()

    def _to_hevc(self,path,duration,j,idx):
        out=str(Path(path).with_suffix(".hevc.mp4")); total=(duration or 0)*1_000_000
        for name,params in hevc_encoders():
            cmd=["ffmpeg","-y","-v","error","-nostats","-progress","pipe:1","-i",path,"-c:v",name,*params,"-tag:v","hvc1","-c:a","copy",out]
            try: p=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,errors="ignore",creationflags=NO_WINDOW)
            except OSError: raise RuntimeError("HEVC conversion failed. Is ffmpeg installed?")
            with self.lock: self.procs.add(p); j["status"]="Converting to HEVC"
            try:
                for line in p.stdout:
                    if self.cancel_event.is_set(): p.terminate(); break
                    if total and line.startswith("out_time_us="):
                        try: frac=max(0,min(.99,int(line.split("=",1)[1])/total))
                        except ValueError: continue
                        with self.lock: j["frac"][idx]=frac; j["status"]=f"Converting to HEVC · {int(frac*100)}%"; self.changed.set()
                p.wait()
            finally:
                with self.lock: self.procs.discard(p)
            if self.cancel_event.is_set():
                if os.path.exists(out): os.remove(out)
                raise yt_dlp.utils.DownloadCancelled()
            if p.returncode==0: os.replace(out,path); return path
            if os.path.exists(out): os.remove(out)
        raise RuntimeError("HEVC conversion failed. Is ffmpeg installed?")
