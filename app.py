import os
import sys
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

# Try using CustomTkinter if installed for a sleek, modern appearance;
# fallback to standard Tkinter + ttk if CustomTkinter is missing.
try:
    import customtkinter as ctk
    ctk.set_appearance_mode("Dark")
    ctk.set_default_color_theme("blue")
    USE_CTK = True
except ImportError:
    USE_CTK = False


class YTDownloaderApp:
    def __init__(self, root):
        self.root = root
        self.root.title("YouTube Media Downloader")
        self.root.geometry("620x580")
        self.root.minsize(550, 500)

        # Default save directory (creates 'Downloads' relative to script path if default)
        self.download_dir = os.path.join(os.getcwd(), "Downloads")
        if not os.path.exists(self.download_dir):
            os.makedirs(self.download_dir, exist_ok=True)

        # Active state variables
        self.downloading = False

        # Build GUI Layout
        self._setup_ui()

    def _setup_ui(self):
        """Builds all visual components of the desktop application."""
        
        # Configure grid expansion
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        # Main Container
        if USE_CTK:
            main_frame = ctk.CTkFrame(self.root, corner_radius=15)
            main_frame.grid(row=0, column=0, padx=20, pady=20, sticky="nsew")
            main_frame.columnconfigure(1, weight=1)
        else:
            main_frame = ttk.Frame(self.root, padding=20)
            main_frame.grid(row=0, column=0, sticky="nsew")
            main_frame.columnconfigure(1, weight=1)

        row_idx = 0

        # --- Header Title ---
        title_text = "🎬 YouTube Media Downloader"
        if USE_CTK:
            title_label = ctk.CTkLabel(main_frame, text=title_text, font=ctk.CTkFont(size=22, weight="bold"))
            title_label.grid(row=row_idx, column=0, columnspan=3, pady=(10, 20))
        else:
            title_label = ttk.Label(main_frame, text=title_text, font=("Helvetica", 16, "bold"))
            title_label.grid(row=row_idx, column=0, columnspan=3, pady=(10, 20))

        row_idx += 1

        # --- URL Input Row ---
        if USE_CTK:
            ctk.CTkLabel(main_frame, text="URL:", font=ctk.CTkFont(weight="bold")).grid(row=row_idx, column=0, sticky="w", padx=5, pady=5)
            self.url_entry = ctk.CTkEntry(main_frame, placeholder_text="Paste YouTube or YT Music URL here...")
            self.url_entry.grid(row=row_idx, column=1, sticky="ew", padx=5, pady=5)
            
            btn_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
            btn_frame.grid(row=row_idx, column=2, sticky="e", padx=5, pady=5)
            
            paste_btn = ctk.CTkButton(btn_frame, text="📋 Paste", width=65, command=self._paste_url)
            paste_btn.pack(side="left", padx=2)
            clear_btn = ctk.CTkButton(btn_frame, text="❌ Clear", width=65, fg_color="#D32F2F", hover_color="#9A0007", command=self._clear_url)
            clear_btn.pack(side="left", padx=2)
        else:
            ttk.Label(main_frame, text="URL:").grid(row=row_idx, column=0, sticky="w", padx=5, pady=5)
            self.url_entry = ttk.Entry(main_frame)
            self.url_entry.grid(row=row_idx, column=1, sticky="ew", padx=5, pady=5)

            btn_frame = ttk.Frame(main_frame)
            btn_frame.grid(row=row_idx, column=2, sticky="e", padx=5, pady=5)

            ttk.Button(btn_frame, text="Paste", command=self._paste_url, width=6).pack(side="left", padx=2)
            ttk.Button(btn_frame, text="Clear", command=self._clear_url, width=6).pack(side="left", padx=2)

        row_idx += 1

        # --- Format Selection (MP4 / MP3) ---
        self.format_var = tk.StringVar(value="MP4 (Video)")
        
        if USE_CTK:
            ctk.CTkLabel(main_frame, text="Format:", font=ctk.CTkFont(weight="bold")).grid(row=row_idx, column=0, sticky="w", padx=5, pady=10)
            self.format_dropdown = ctk.CTkOptionMenu(
                main_frame, 
                values=["MP4 (Video)", "MP3 (Audio)"],
                variable=self.format_var,
                command=self._on_format_change
            )
            self.format_dropdown.grid(row=row_idx, column=1, columnspan=2, sticky="w", padx=5, pady=10)
        else:
            ttk.Label(main_frame, text="Format:").grid(row=row_idx, column=0, sticky="w", padx=5, pady=10)
            self.format_dropdown = ttk.OptionMenu(
                main_frame, 
                self.format_var, 
                "MP4 (Video)", 
                "MP4 (Video)", "MP3 (Audio)", 
                command=lambda _: self._on_format_change(self.format_var.get())
            )
            self.format_dropdown.grid(row=row_idx, column=1, columnspan=2, sticky="w", padx=5, pady=10)

        row_idx += 1

        # --- Quality / Resolution Selection ---
        self.video_qualities = [
            "Best / 1080p Max",
            "720p",
            "480p",
            "360p",
            "Highest Available (4K/1440p)"
        ]
        self.audio_qualities = [
            "320 kbps (Best)",
            "256 kbps",
            "192 kbps",
            "128 kbps"
        ]
        
        self.quality_var = tk.StringVar(value=self.video_qualities[0])

        if USE_CTK:
            ctk.CTkLabel(main_frame, text="Quality:", font=ctk.CTkFont(weight="bold")).grid(row=row_idx, column=0, sticky="w", padx=5, pady=10)
            self.quality_dropdown = ctk.CTkOptionMenu(
                main_frame, 
                values=self.video_qualities,
                variable=self.quality_var
            )
            self.quality_dropdown.grid(row=row_idx, column=1, columnspan=2, sticky="w", padx=5, pady=10)
        else:
            ttk.Label(main_frame, text="Quality:").grid(row=row_idx, column=0, sticky="w", padx=5, pady=10)
            self.quality_dropdown = ttk.OptionMenu(
                main_frame, 
                self.quality_var, 
                self.video_qualities[0], 
                *self.video_qualities
            )
            self.quality_dropdown.grid(row=row_idx, column=1, columnspan=2, sticky="w", padx=5, pady=10)

        row_idx += 1

        # --- Save Directory Selection ---
        if USE_CTK:
            ctk.CTkLabel(main_frame, text="Save To:", font=ctk.CTkFont(weight="bold")).grid(row=row_idx, column=0, sticky="w", padx=5, pady=10)
            self.folder_label = ctk.CTkEntry(main_frame)
            self.folder_label.insert(0, self.download_dir)
            self.folder_label.configure(state="disabled")
            self.folder_label.grid(row=row_idx, column=1, sticky="ew", padx=5, pady=10)
            
            browse_btn = ctk.CTkButton(main_frame, text="📁 Browse...", width=90, command=self._browse_folder)
            browse_btn.grid(row=row_idx, column=2, sticky="e", padx=5, pady=10)
        else:
            ttk.Label(main_frame, text="Save To:").grid(row=row_idx, column=0, sticky="w", padx=5, pady=10)
            self.folder_label = ttk.Entry(main_frame)
            self.folder_label.insert(0, self.download_dir)
            self.folder_label.configure(state="disabled")
            self.folder_label.grid(row=row_idx, column=1, sticky="ew", padx=5, pady=10)
            
            ttk.Button(main_frame, text="Browse...", command=self._browse_folder).grid(row=row_idx, column=2, sticky="e", padx=5, pady=10)

        row_idx += 1

        # --- Download Button ---
        if USE_CTK:
            self.download_btn = ctk.CTkButton(
                main_frame, 
                text="📥 Download Now", 
                font=ctk.CTkFont(size=15, weight="bold"),
                height=40,
                fg_color="#2E7D32", 
                hover_color="#1B5E20",
                command=self._start_download_thread
            )
            self.download_btn.grid(row=row_idx, column=0, columnspan=3, pady=15, sticky="ew")
        else:
            self.download_btn = ttk.Button(main_frame, text="Download Now", command=self._start_download_thread)
            self.download_btn.grid(row=row_idx, column=0, columnspan=3, pady=15, sticky="ew")

        row_idx += 1

        # --- Progress Bar & Status Text ---
        if USE_CTK:
            self.status_label = ctk.CTkLabel(main_frame, text="Ready", font=ctk.CTkFont(size=12))
            self.status_label.grid(row=row_idx, column=0, columnspan=3, sticky="w", padx=5)
            row_idx += 1
            self.progress_bar = ctk.CTkProgressBar(main_frame)
            self.progress_bar.set(0.0)
            self.progress_bar.grid(row=row_idx, column=0, columnspan=3, sticky="ew", padx=5, pady=(2, 10))
        else:
            self.status_label = ttk.Label(main_frame, text="Ready")
            self.status_label.grid(row=row_idx, column=0, columnspan=3, sticky="w", padx=5)
            row_idx += 1
            self.progress_bar = ttk.Progressbar(main_frame, orient="horizontal", mode="determinate")
            self.progress_bar.grid(row=row_idx, column=0, columnspan=3, sticky="ew", padx=5, pady=(2, 10))

        row_idx += 1

        # --- Log Output Console Area ---
        if USE_CTK:
            ctk.CTkLabel(main_frame, text="Logs Output:", font=ctk.CTkFont(weight="bold")).grid(row=row_idx, column=0, sticky="w", padx=5)
            row_idx += 1
            self.log_textbox = ctk.CTkTextbox(main_frame, height=120)
            self.log_textbox.grid(row=row_idx, column=0, columnspan=3, sticky="nsew", padx=5, pady=5)
            main_frame.rowconfigure(row_idx, weight=1)
        else:
            ttk.Label(main_frame, text="Logs Output:").grid(row=row_idx, column=0, sticky="w", padx=5)
            row_idx += 1
            self.log_textbox = tk.Text(main_frame, height=8)
            self.log_textbox.grid(row=row_idx, column=0, columnspan=3, sticky="nsew", padx=5, pady=5)
            main_frame.rowconfigure(row_idx, weight=1)

    def _paste_url(self):
        """Pastes current clipboard content into the URL box."""
        try:
            clipboard_text = self.root.clipboard_get()
            self.url_entry.delete(0, tk.END)
            self.url_entry.insert(0, clipboard_text.strip())
        except tk.TclError:
            pass  # Empty clipboard or non-text data

    def _clear_url(self):
        """Clears the URL entry field."""
        self.url_entry.delete(0, tk.END)

    def _browse_folder(self):
        """Opens directory picker dialog to select output path."""
        selected_dir = filedialog.askdirectory(initialdir=self.download_dir)
        if selected_dir:
            self.download_dir = selected_dir
            self.folder_label.configure(state="normal")
            self.folder_label.delete(0, tk.END)
            self.folder_label.insert(0, self.download_dir)
            self.folder_label.configure(state="disabled")

    def _on_format_change(self, selected_format):
        """Dynamically updates the quality options based on format chosen (MP4 or MP3)."""
        if "MP3" in selected_format:
            options = self.audio_qualities
        else:
            options = self.video_qualities

        self.quality_var.set(options[0])

        if USE_CTK:
            self.quality_dropdown.configure(values=options)
        else:
            # Re-populate TTK OptionMenu
            menu = self.quality_dropdown["menu"]
            menu.delete(0, "end")
            for item in options:
                menu.add_command(label=item, command=lambda value=item: self.quality_var.set(value))

    def _append_log(self, text):
        """Appends log text into the log output console safely."""
        if USE_CTK:
            self.log_textbox.insert(tk.END, text + "\n")
            self.log_textbox.see(tk.END)
        else:
            self.log_textbox.insert(tk.END, text + "\n")
            self.log_textbox.see(tk.END)

    def _progress_hook(self, d):
        """Hook called continuously by yt-dlp to report download progress."""
        if d['status'] == 'downloading':
            total_bytes = d.get('total_bytes') or d.get('total_bytes_estimate', 0)
            downloaded_bytes = d.get('downloaded_bytes', 0)
            
            if total_bytes > 0:
                percentage = downloaded_bytes / total_bytes
                percent_str = f"{percentage * 100:.1f}%"
                
                # Update progress bar & label safely on the main thread UI
                if USE_CTK:
                    self.progress_bar.set(percentage)
                else:
                    self.progress_bar['value'] = percentage * 100

                self.status_label.configure(text=f"Downloading... {percent_str}")
                
        elif d['status'] == 'finished':
            self.status_label.configure(text="Processing/Converting file... ⏳")
            self._append_log("👍 Stream downloaded. Processing file...")

    def _start_download_thread(self):
        """Spawns a background thread for downloading so GUI doesn't freeze."""
        url = self.url_entry.get().strip()

        if not url:
            messagebox.showerror("Error", "Please enter or paste a valid YouTube URL!")
            return

        if self.downloading:
            messagebox.showwarning("In Progress", "A download is already running!")
            return

        self.downloading = True
        self.download_btn.configure(state="disabled")
        self.status_label.configure(text="Starting download... ⏳")
        if USE_CTK:
            self.progress_bar.set(0.0)
        else:
            self.progress_bar['value'] = 0

        # Run background thread
        thread = threading.Thread(target=self._run_download, args=(url,), daemon=True)
        thread.start()

    def _run_download(self, url):
        """Executes yt-dlp download logic (runs off the main UI thread)."""
        fmt = self.format_var.get()
        quality = self.quality_var.get()

        self._append_log(f"--- Starting Download ---")
        self._append_log(f"URL: {url}")
        self._append_log(f"Format: {fmt} | Quality: {quality}")

        # Base yt-dlp options
        ydl_opts = {
            'outtmpl': os.path.join(self.download_dir, '%(title)s.%(ext)s'),
            'progress_hooks': [self._progress_hook],
            'js_runtimes': {'node': {}},  # Node.js runtime for YouTube JS challenges
        }

        # Configure Audio vs Video download options
        if "MP3" in fmt:
            bitrate = quality.split()[0]  # Extracts '320', '256', etc.
            ydl_opts.update({
                'format': 'bestaudio/best',
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': bitrate,
                }],
            })
        else:
            # Video configuration
            if "1080p" in quality:
                format_str = 'bestvideo[height<=1080]+bestaudio/best'
            elif "720p" in quality:
                format_str = 'bestvideo[height<=720]+bestaudio/best'
            elif "480p" in quality:
                format_str = 'bestvideo[height<=480]+bestaudio/best'
            elif "360p" in quality:
                format_str = 'bestvideo[height<=360]+bestaudio/best'
            else:  # Highest Available (4K/1440p)
                format_str = 'bestvideo+bestaudio/best'

            ydl_opts.update({
                'format': format_str,
            })

        # Execute Download
        try:
            with YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])

            self.status_label.configure(text="✓ Download Completed Successfully!")
            self._append_log("✔️ Finished successfully!")
            messagebox.showinfo("Success", f"Download finished successfully!\nSaved to:\n{self.download_dir}")

        except DownloadError as e:
            self.status_label.configure(text="❌ Download Failed!")
            self._append_log(f"❌ Download Error: {str(e)}")
            messagebox.showerror("Download Error", f"Failed to download video.\n\nError details:\n{str(e)}")
        except Exception as e:
            self.status_label.configure(text="❌ Unexpected Error!")
            self._append_log(f"❌ Error: {str(e)}")
            messagebox.showerror("Error", f"An unexpected error occurred:\n{str(e)}")
        finally:
            self.downloading = False
            self.download_btn.configure(state="normal")


def main():
    if USE_CTK:
        root = ctk.CTk()
    else:
        root = tk.Tk()

    app = YTDownloaderApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()