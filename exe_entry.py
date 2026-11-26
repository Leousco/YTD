"""Windowed entry point for the packaged Streamgrab executable."""
import ctypes
import traceback

from engine import CONFIG_DIR
from server import main


def run():
    try:
        main()
    except Exception:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        log = CONFIG_DIR / "startup.log"
        log.write_text(traceback.format_exc(), encoding="utf-8")
        message = f"Streamgrab could not start. Details were saved to:\n{log}"
        ctypes.windll.user32.MessageBoxW(0, message, "Streamgrab", 0x10)


if __name__ == "__main__":
    run()
