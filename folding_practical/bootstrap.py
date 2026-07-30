"""Responsive launcher for the desktop app."""

from __future__ import annotations

from queue import Empty, Queue
from threading import Thread
import tkinter as tk
from tkinter import ttk


def _center(window: tk.Tk, width: int, height: int) -> None:
    window.update_idletasks()
    x = max(0, (window.winfo_screenwidth() - width) // 2)
    y = max(0, (window.winfo_screenheight() - height) // 2)
    window.geometry(f"{width}x{height}+{x}+{y}")


def main() -> None:
    splash = tk.Tk()
    splash.title("Protein Folding Practical")
    splash.resizable(False, False)
    splash.configure(background="#15243b")
    _center(splash, 430, 190)

    panel = tk.Frame(splash, background="#15243b", padx=28, pady=24)
    panel.pack(fill="both", expand=True)
    tk.Label(
        panel,
        text="Protein Folding Practical",
        background="#15243b",
        foreground="#ffffff",
        font=("TkDefaultFont", 17, "bold"),
    ).pack(anchor="w")
    tk.Label(
        panel,
        text="Loading analysis tools...",
        background="#15243b",
        foreground="#cbd5e1",
        font=("TkDefaultFont", 10),
    ).pack(anchor="w", pady=(8, 18))
    progress = ttk.Progressbar(panel, mode="indeterminate", length=370)
    progress.pack(fill="x")
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
