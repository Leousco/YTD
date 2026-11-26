# Streamgrab

A small Windows desktop app for downloading YouTube and YouTube Music links as
MP4 video or MP3 audio. Built with [customtkinter](https://github.com/TomSchimansky/CustomTkinter)
and [yt-dlp](https://github.com/yt-dlp/yt-dlp).

## Requirements

- Python 3.10 or newer (developed on 3.14; the launchers use `py -3`)
- `yt-dlp`, `customtkinter` and `Pillow` (see below)
- FFmpeg on `PATH` — needed for MP3 extraction and for the optional HEVC
  conversion. Without it, audio downloads and HEVC conversion will fail.

## Install

```bat
python -m pip install yt-dlp customtkinter pillow
```

FFmpeg is not a pip package. On Windows the simplest install is:

```bat
winget install Gyan.FFmpeg
```

Or download a build from <https://www.gyan.dev/ffmpeg/builds/>, unzip it, and add
its `bin` folder to your `PATH`. Open a new terminal and check with `ffmpeg -version`.

## Run

- Double-click **`Run Streamgrab.bat`** — starts the app and keeps a console window
  open so startup errors stay visible.
- Double-click **`Streamgrab.vbs`** — starts the app silently, with no console window.

Both launchers just run `app.py` from their own folder, so the repo can live anywhere.

## Usage

1. Paste a YouTube / YouTube Music link into the **Media link** field. Links copied
   to the clipboard are picked up automatically when the window gains focus.
2. Pick **MP4 · Video** or **MP3 · Audio**, then a quality. The estimated length and
   file size appear next to the title.
3. Choose a save folder and press **Download**. **Cancel** stops the active job.
4. Press **Add** to stage the current link and paste another — up to 20 links can be
   queued at once, and the chevron in the preview row expands the per-file list.
5. The **History** tab lists the last 10 downloads.

## Data and settings

Settings and history are stored as JSON in `~/.streamgrab/`:

| File | Contents |
| --- | --- |
| `settings.json` | Last folder, format, per-format quality, HEVC toggle |
| `history.json` | Last 10 downloads (capped on startup) |

Deleting a history entry or clearing the log never deletes the downloaded files.

## Files

| File | Purpose |
| --- | --- |
| `app.py` | The entire application: UI, downloads, history |
| `Run Streamgrab.bat` | Console launcher |
| `Streamgrab.vbs` | Silent launcher |

## Notes

- Two downloads run at a time; more than that tends to split bandwidth rather than
  speed things up.
- HEVC (H.265) conversion happens after the download and is much slower than the
  download itself. It requires FFmpeg, and is only offered for MP4.
- Window size is set in `App.__init__` via `self.geometry("780x680")`.

## TODO

- Better visuals
- Optimization: make the app faster and smoother
