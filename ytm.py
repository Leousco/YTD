import tkinter as tk
from tkinter import messagebox
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

def start_download():
    url = url_entry.get().strip()
    
    if not url:
        messagebox.showerror("Error", "Please enter a valid YouTube URL!")
        return

    status_label.config(text="Downloading... Please wait ⏳", fg="blue")
    root.update()

    ydl_opts = {
        'format': 'bestaudio/best',
        'js_runtimes': {'node': {}},
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '320',
        }],
        'outtmpl': r'C:\Users\cjvil\Documents\Project\YTD\Music\%(title)s.%(ext)s',
    }

    try:
        with YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        status_label.config(text="✓ Music Download Completed Successfully!", fg="green")
        messagebox.成功("Success", "Download completed successfully!")
    except DownloadError:
        status_label.config(text="❌ Music Download Failed!", fg="red")
        messagebox.showerror("Error", "Download failed! Check URL or network.")

# --- UI Setup ---
root = tk.Tk()
root.title("YTD Music Downloader")
root.geometry("450x220")
root.resizable(False, False)

# Title Label
title_label = tk.Label(root, text="YouTube Music Downloader", font=("Arial", 14, "bold"))
title_label.pack(pady=10)

# URL Input Field
url_label = tk.Label(root, text="Paste YouTube Music URL:", font=("Arial", 10))
url_label.pack(anchor="w", padx=30)

url_entry = tk.Entry(root, width=50, font=("Arial", 10))
url_entry.pack(pady=5, padx=30)

# Download Button
download_btn = tk.Button(root, text="Download MP3", font=("Arial", 10, "bold"), bg="#4CAF50", fg="white", command=start_download)
download_btn.pack(pady=10)

# Status Label
status_label = tk.Label(root, text="", font=("Arial", 9))
status_label.pack(pady=5)

root.mainloop()