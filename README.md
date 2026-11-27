# Streamgrab

Streamgrab is a Windows desktop app for downloading video or audio from any
HTTP(S) site supported by yt-dlp as MP4 video or MP3 audio. DRM-protected
services are not supported. The desktop UI is React + Tailwind; FastAPI keeps
download work in Python and streams progress over Server-Sent Events.

## Requirements

- Python 3.10 or newer only for source/debug runs
- FFmpeg on `PATH` for MP3 extraction and optional HEVC conversion
- Node.js 20.19+ only when developing or rebuilding the frontend

## Run

Double-click **Streamgrab.exe** to launch the app. It bundles Python, the
backend, and the built web UI; FFmpeg must still be installed and available on
`PATH`. A second launch exits quietly. The app binds only to `127.0.0.1` on a
free port and opens in a native window when pywebview is available, otherwise
it falls back to an Edge or Chrome app window.

For a source/debug run, install Python packages with
`python -m pip install -r requirements.txt`, then use **Run Streamgrab.bat**
to launch with a visible console. **Streamgrab.vbs** remains available as a
Python-based silent launcher.

To rebuild the executable, install `requirements-build.txt` and double-click
**Build Streamgrab.exe.bat**. The resulting `Streamgrab.exe` is written next
to the script. The bundled web assets are committed under `web/dist`, so
building the executable does not require Node unless the UI source has changed.

The web bundle is committed under `web/dist`, so end users do not need Node.
The launchers run from their own directory and the project can be moved freely.

## Frontend development

Start a development API in one terminal from the project root:

```sh
python -m uvicorn server:app --host 127.0.0.1 --port 8765
```

Then from `web/`:

```sh
npm install
npm run dev
```

Vite serves the frontend on port 5173 and proxies `/api` to that development
server. Create the shipped bundle with:

```sh
npm run build
```

The backend serves `web/dist` directly. No Node process is needed after build.

## API examples

The server prints no console window in the silent launcher. For API development,
run `python server.py`; its built-in window launcher opens the app. The service
exposes these local endpoints:

```sh
curl -X POST http://127.0.0.1:PORT/api/info -H "Content-Type: application/json" \
  -d '{"urls":["https://www.youtube.com/watch?v=dQw4w9WgXcQ"],"format":"MP3","quality":"128 kbps"}'
curl -X POST http://127.0.0.1:PORT/api/download -H "Content-Type: application/json" \
  -d '{"urls":["https://www.youtube.com/watch?v=dQw4w9WgXcQ"],"format":"MP3","quality":"128 kbps","folder":"C:/Users/you/Downloads","hevc":false}'
curl -N http://127.0.0.1:PORT/api/events
curl -X POST http://127.0.0.1:PORT/api/cancel
curl http://127.0.0.1:PORT/api/history
```

`/api/events` emits JSON snapshots as SSE events while a job changes and sends
keepalives when idle. The API validates HTTP(S) URLs, formats, qualities,
configured folder access, and history file actions. URL support follows yt-dlp;
DRM-protected services are not supported. The API has no generic open-path
endpoint.

## Features

- MP4 video with Best available, 1080p, 720p, 480p, and 360p caps
- MP3 at 128, 192, 256, or 320 kbps, or original M4A audio
- Optional hardware-probed HEVC conversion, with libx265 fallback
- Size previews adjust for HEVC using a ratio learned from your previous conversions.
- Up to 20 deduplicated links and two concurrent downloads
- Duplicate detection by video ID (or URL for other sites) and output format
- Download history, search, per-format quality settings, and HEVC preference
- Cancel support and per-file batch state

Settings and the last 10 history entries retain the original JSON formats in
`~/.streamgrab/settings.json` and `~/.streamgrab/history.json`. Removing
history never removes the files.

## Project files

- `exe_entry.py` — windowed executable entry point
- `requirements-build.txt` — PyInstaller build dependency
- `Build Streamgrab.exe.bat` — rebuilds the standalone executable

- `server.py` — local API, window launcher, and single-instance lock
- `engine.py` — downloader and persistent settings/history
- `web/` — Vite, React, TypeScript, and Tailwind sources
- `web/dist/` — built UI served by FastAPI
- `legacy/app_tk.py` — original Tk UI retained for reference
