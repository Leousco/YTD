from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

url = 'https://youtu.be/DiAT-mS0rns' # YT Video url

ydl_opts = {
    'format': 'bestvideo[height<=720]+bestaudio/best',
    'outtmpl': r'C:\Users\cjvil\Documents\Project\YTD\Video\%(title)s.%(ext)s',
}

try:
    with YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])
    print("✔️  Video Download Completed Successfully!")
except DownloadError:
    print("❌ Video Download Failed!")