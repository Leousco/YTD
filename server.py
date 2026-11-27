"""Local Streamgrab API and desktop window launcher."""
import asyncio
import ctypes
import json
import os
import socket
import sys
import threading
import time
import urllib.parse
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from engine import DownloadManager, CONFIG_DIR, load_json, save_json

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "web" / "dist"
manager = DownloadManager()
server = None
closing = threading.Event()
_instance_socket = None


class InfoRequest(BaseModel):
    urls: list[str]
    format: str = "MP3"
    quality: str = "128 kbps"
    hevc: bool = False

class DownloadRequest(BaseModel):
    urls: list[str]
    format: str = "MP3"
    quality: str = "128 kbps"
    folder: str
    hevc: bool = False
    force: bool = False

class SettingsRequest(BaseModel):
    settings: dict

class PathRequest(BaseModel):
    path: str


@asynccontextmanager
async def lifespan(app):
    yield
    closing.set()
    manager.shutdown()


app = FastAPI(title="Streamgrab", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["null"], allow_methods=["GET", "POST", "PUT", "DELETE"], allow_headers=["*"])


@app.get("/api/health")
def health(): return {"ok": True}

@app.post("/api/info")
def info(req: InfoRequest):
    try: return {"items": manager.info(req.urls, req.format, req.quality, req.hevc)}
    except ValueError as ex: raise HTTPException(400, str(ex))

@app.post("/api/download")
def download(req: DownloadRequest):
    try: return manager.start(req.urls, req.format, req.quality, req.folder, req.hevc, req.force)
    except (ValueError, OSError) as ex: raise HTTPException(400, str(ex))

@app.post("/api/cancel")
def cancel(): return {"cancelled": manager.cancel()}

@app.get("/api/events")
async def events():
    async def stream():
        last = None
        while not closing.is_set():
            manager.changed.clear()
            state = manager.snapshot()
            payload = json.dumps(state, ensure_ascii=False)
            if payload != last:
                yield f"data: {payload}\n\n"
                last = payload
            else:
                pass
            await asyncio.to_thread(manager.changed.wait, 15)
            if last is not None and closing.is_set(): break
            if not manager.changed.is_set(): yield ": keepalive\n\n"
    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control":"no-cache", "X-Accel-Buffering":"no"})

@app.get("/api/history")
def history(q: str = ""):
    return {"items": manager.get_history(q)}

@app.delete("/api/history")
def clear_history():
    manager.delete_history(); return {"ok": True}

@app.delete("/api/history/{entry_id}")
def delete_history(entry_id: str):
    manager.delete_history(entry_id); return {"ok": True}

@app.get("/api/settings")
def settings():
    values = {"folder": str(Path.home() / "Downloads"), **manager.settings}
    for key in ("quality_MP3", "quality_MP4"):
        if values.get(key) == "M4A \ufffd Original": values[key] = "M4A · Original"
    return values

@app.put("/api/settings")
def put_settings(req: SettingsRequest):
    allowed = {"folder", "format", "hevc", "quality_MP3", "quality_MP4"}
    values = {k:v for k,v in req.settings.items() if k in allowed}
    for key in ("quality_MP3", "quality_MP4"):
        if values.get(key) == "M4A · Original": values[key] = "M4A \ufffd Original"
    return manager.settings_update(values)

def _allowed_file(path):
    p = Path(path).expanduser().resolve()
    if not any(os.path.normcase(str(p)) == os.path.normcase(str(Path(e["path"]).resolve())) for e in manager.history if e.get("path")):
        raise HTTPException(403, "File is not in download history")
    if not p.is_file(): raise HTTPException(404, "File not found")
    return p

@app.post("/api/open-file")
def open_file(req: PathRequest):
    p = _allowed_file(req.path)
    if sys.platform == "win32": os.startfile(str(p))
    elif sys.platform == "darwin": import subprocess; subprocess.Popen(["open",str(p)])
    else: import subprocess; subprocess.Popen(["xdg-open",str(p)])
    return {"ok":True}

@app.post("/api/reveal-file")
def reveal_file(req: PathRequest):
    p = _allowed_file(req.path)
    if sys.platform == "win32":
        import subprocess
        subprocess.Popen(["explorer", f"/select,{p}"])
    elif sys.platform == "darwin": import subprocess; subprocess.Popen(["open","-R",str(p)])
    else: import subprocess; subprocess.Popen(["xdg-open",str(p.parent)])
    return {"ok":True}

@app.post("/api/history-folder")
def history_folder(req: PathRequest):
    p = _allowed_file(req.path)
    if sys.platform == "win32": os.startfile(str(p.parent))
    elif sys.platform == "darwin": import subprocess; subprocess.Popen(["open",str(p.parent)])
    else: import subprocess; subprocess.Popen(["xdg-open",str(p.parent)])
    return {"ok":True}

@app.post("/api/open-folder")
def open_folder(req: PathRequest):
    p = Path(req.path).expanduser().resolve()
    configured = Path(manager.settings.get("folder", str(Path.home()/"Downloads"))).expanduser().resolve()
    if os.path.normcase(str(p)) != os.path.normcase(str(configured)) or not p.is_dir(): raise HTTPException(403,"Folder is not the configured download folder")
    if sys.platform == "win32": os.startfile(str(p))
    elif sys.platform == "darwin": import subprocess; subprocess.Popen(["open",str(p)])
    else: import subprocess; subprocess.Popen(["xdg-open",str(p)])
    return {"ok":True}

@app.post("/api/pick-folder")
def pick_folder():
    try:
        import tkinter as tk
        from tkinter import filedialog
        root=tk.Tk(); root.withdraw(); root.attributes("-topmost",True)
        result=filedialog.askdirectory(initialdir=manager.settings.get("folder", str(Path.home()/"Downloads")))
        root.destroy()
        return {"folder":result or None}
    except Exception as ex: raise HTTPException(500,str(ex))

if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")
    @app.get("/{path:path}")
    def frontend(path: str):
        candidate = (DIST / path).resolve()
        if candidate.is_file() and DIST.resolve() in candidate.parents: return FileResponse(candidate)
        return FileResponse(DIST / "index.html")


def acquire_instance():
    global _instance_socket
    s=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
    try: s.bind(("127.0.0.1",47831)); s.listen(1); _instance_socket=s; return True
    except OSError:
        try:
            with socket.create_connection(("127.0.0.1",47831),timeout=.4): pass
        except OSError: pass
        return False

def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1",0)); return s.getsockname()[1]

def launch_window(url):
    try:
        import webview
        webview.create_window("Streamgrab",url,width=780,height=680,min_size=(640,560))
        webview.start()
    except ImportError:
        import shutil, subprocess
        candidates = [shutil.which("msedge"), shutil.which("chrome"), shutil.which("google-chrome")]
        if sys.platform == "win32":
            candidates.extend([r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                               r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
                               str(Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/Edge/Application/msedge.exe"),
                               r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                               str(Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe")])
        browser = next((p for p in candidates if p and Path(p).is_file()), None)
        if browser:
            profile = CONFIG_DIR / "browser-profile"
            proc = subprocess.Popen([browser, f"--user-data-dir={profile}", "--no-first-run", f"--app={url}"],
                                    creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
            proc.wait()
        else:
            raise RuntimeError("Install pywebview or Microsoft Edge/Google Chrome to open Streamgrab.")
    finally:
        closing.set()
        if server: server.should_exit=True

def main():
    global server
    if not acquire_instance(): return
    port=free_port(); url=f"http://127.0.0.1:{port}"
    # A windowed PyInstaller executable has no stdout/stderr streams. Letting
    # Uvicorn configure its default console formatter then raises AttributeError.
    config=uvicorn.Config(app,host="127.0.0.1",port=port,log_level="warning",access_log=False,log_config=None)
    server=uvicorn.Server(config)
    thread=threading.Thread(target=server.run,daemon=True); thread.start()
    for _ in range(100):
        try:
            with socket.create_connection(("127.0.0.1",port),timeout=.1): break
        except OSError: time.sleep(.05)
    launch_window(url)
    manager.shutdown()
    server.should_exit=True; thread.join(timeout=5)

if __name__ == "__main__": main()
