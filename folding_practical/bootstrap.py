"""Responsive launcher for the desktop app."""

from __future__ import annotations

from queue import Empty, Queue
from threading import Thread
import tkinter as tk
from tkinter import ttk


BACKGROUND = "#071426"
ACCENT = "#6957f5"
ACCENT_CYAN = "#24c8d8"


def _center(window: tk.Tk, width: int, height: int) -> None:
    window.update_idletasks()
    x = max(0, (window.winfo_screenwidth() - width) // 2)
    y = max(0, (window.winfo_screenheight() - height) // 2)
    window.geometry(f"{width}x{height}+{x}+{y}")


def main() -> None:
    splash = tk.Tk()
    splash.title("Protein Folding Practical")
    splash.resizable(False, False)
    splash.configure(background=BACKGROUND)
    _center(splash, 470, 230)

    canvas = tk.Canvas(splash, background=BACKGROUND, highlightthickness=0, borderwidth=0)
    canvas.pack(fill="both", expand=True)
    canvas.create_oval(350, -70, 515, 95, fill="#243f83", outline="")
    canvas.create_oval(390, 35, 485, 130, fill="#156b87", outline="")
    canvas.create_line(355, 36, 430, 78, fill="#82ddf0", width=2)
    canvas.create_oval(348, 28, 364, 44, fill="#b7f4fb", outline="")
    canvas.create_oval(423, 70, 441, 88, fill="#b7f4fb", outline="")
    canvas.create_rectangle(0, 0, 7, 230, fill=ACCENT, outline="")
    canvas.create_text(30, 44, text="Protein Folding Practical", fill="#ffffff", anchor="w", font=("TkDefaultFont", 19, "bold"))
    canvas.create_text(30, 78, text="Loading plate analysis tools...", fill="#c8d5ea", anchor="w", font=("TkDefaultFont", 10))
    canvas.create_text(30, 199, text="Designed by Ben Shin", fill="#7f91ad", anchor="w", font=("TkDefaultFont", 8))

    style = ttk.Style(splash)
    if "clam" in style.theme_names():
        style.theme_use("clam")
    style.configure("Splash.Horizontal.TProgressbar", troughcolor="#162844", background=ACCENT_CYAN, bordercolor="#162844")
    progress = ttk.Progressbar(splash, mode="indeterminate", length=390, style="Splash.Horizontal.TProgressbar")
    progress.place(x=30, y=126, width=390, height=12)
    progress.start(12)

    result_queue = Queue()

    def load_modules() -> None:
        try:
            from . import app as app_module
            from .enhancements import install

            install(app_module)
            result_queue.put((app_module, None))
        except Exception as exc:
            result_queue.put((None, exc))

    Thread(target=load_modules, daemon=True).start()

    def poll() -> None:
        try:
            app_module, error = result_queue.get_nowait()
        except Empty:
            splash.after(50, poll)
            return

        progress.stop()
        splash.destroy()
        if error is not None:
            raise error
        app_module.main()

    splash.after(50, poll)
    splash.mainloop()


if __name__ == "__main__":
    main()
