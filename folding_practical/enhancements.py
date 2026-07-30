"""Visual and performance upgrades for the desktop app."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable, Optional
import tkinter as tk
from tkinter import filedialog, font as tkfont, messagebox, ttk


BACKGROUND = "#f4f7fb"
SURFACE = "#ffffff"
HEADER = "#15243b"
TEXT = "#172033"
MUTED = "#667085"
ACCENT = "#2563eb"
ACCENT_DARK = "#1d4ed8"
BORDER = "#dbe3ef"
DANGER = "#b42318"

PRIMARY_ACTIONS = {
    "Load CSV files",
    "Add or replace group",
    "Plot and fit selected groups",
    "Plot selected well spectra",
    "Export all group CSVs",
}

BUSY_ACTIONS = {
    "Load CSV files",
    "Load group map CSV",
    "Plot and fit selected groups",
    "Plot selected well spectra",
}


def _walk_widgets(widget: tk.Misc):
    for child in widget.winfo_children():
        yield child
        yield from _walk_widgets(child)


def _configure_theme(app: tk.Tk) -> None:
    style = ttk.Style(app)
    if "clam" in style.theme_names():
        style.theme_use("clam")

    app.configure(background=BACKGROUND)

    default_font = tkfont.nametofont("TkDefaultFont")
    text_font = tkfont.nametofont("TkTextFont")
    heading_font = tkfont.nametofont("TkHeadingFont")
    default_font.configure(size=10)
    text_font.configure(size=10)
    heading_font.configure(size=10, weight="bold")

    style.configure("TFrame", background=BACKGROUND)
    style.configure("Surface.TFrame", background=SURFACE)
    style.configure("Header.TFrame", background=HEADER)
    style.configure("Status.TFrame", background=SURFACE)

    style.configure("TLabel", background=BACKGROUND, foreground=TEXT)
    style.configure("HeaderTitle.TLabel", background=HEADER, foreground="#ffffff", font=(default_font.actual("family"), 17, "bold"))
    style.configure("HeaderSubtitle.TLabel", background=HEADER, foreground="#cbd5e1")
    style.configure("Status.TLabel", background=SURFACE, foreground=MUTED, padding=(4, 0))

    style.configure("TLabelframe", background=BACKGROUND, bordercolor=BORDER, relief="solid", borderwidth=1)
    style.configure("TLabelframe.Label", background=BACKGROUND, foreground=TEXT, font=(default_font.actual("family"), 10, "bold"))

    style.configure("TNotebook", background=BACKGROUND, borderwidth=0)
    style.configure("TNotebook.Tab", padding=(18, 10), font=(default_font.actual("family"), 10, "bold"))
    style.map(
        "TNotebook.Tab",
        background=[("selected", SURFACE), ("!selected", "#e8eef7")],
        foreground=[("selected", ACCENT_DARK), ("!selected", MUTED)],
    )

    style.configure("TButton", padding=(10, 7), background="#e9eef6", foreground=TEXT, bordercolor="#cbd5e1")
    style.map(
        "TButton",
        background=[("pressed", ACCENT_DARK), ("active", "#dce5f2")],
        foreground=[("pressed", "#ffffff")],
    )
    style.configure("Primary.TButton", padding=(12, 8), background=ACCENT, foreground="#ffffff", bordercolor=ACCENT)
    style.map(
        "Primary.TButton",
        background=[("pressed", ACCENT_DARK), ("active", ACCENT_DARK), ("disabled", "#9fb7e7")],
        foreground=[("disabled", "#eef2ff"), ("!disabled", "#ffffff")],
    )
    style.configure("Danger.TButton", foreground=DANGER)

    style.configure("TEntry", fieldbackground=SURFACE, foreground=TEXT, padding=6, bordercolor=BORDER)
    style.configure("TCombobox", fieldbackground=SURFACE, foreground=TEXT, padding=5, bordercolor=BORDER)
    style.configure("TSpinbox", fieldbackground=SURFACE, foreground=TEXT, padding=5, bordercolor=BORDER)

    style.configure("Treeview", background=SURFACE, fieldbackground=SURFACE, foreground=TEXT, rowheight=28, bordercolor=BORDER)
    style.configure("Treeview.Heading", background="#e8eef7", foreground=TEXT, relief="flat", font=(default_font.actual("family"), 9, "bold"))
    style.map("Treeview", background=[("selected", ACCENT)], foreground=[("selected", "#ffffff")])

    style.configure("Horizontal.TProgressbar", background=ACCENT, troughcolor="#e8eef7", bordercolor="#e8eef7")


def _style_axes(axes: Any) -> None:
    axes.set_facecolor(SURFACE)
    axes.tick_params(colors=TEXT, labelsize=9)
    for spine in axes.spines.values():
        spine.set_color("#cbd5e1")
    axes.xaxis.label.set_color(TEXT)
    axes.yaxis.label.set_color(TEXT)
    axes.title.set_color(TEXT)
    axes.grid(True, color="#dbe3ef", alpha=0.65, linewidth=0.8)


def _decorate_ui(app: tk.Tk) -> None:
    notebook = next((child for child in app.winfo_children() if isinstance(child, ttk.Notebook)), None)

    if notebook is not None:
        header = ttk.Frame(app, style="Header.TFrame", padding=(22, 14))
        title_block = ttk.Frame(header, style="Header.TFrame")
        title_block.pack(side="left", fill="x", expand=True)
        ttk.Label(title_block, text="Protein Folding Practical", style="HeaderTitle.TLabel").pack(anchor="w")
        ttk.Label(
            title_block,
            text="Import plate reads, assign groups, fit denaturation curves, and inspect spectra.",
            style="HeaderSubtitle.TLabel",
        ).pack(anchor="w", pady=(3, 0))
        ttk.Label(header, text="GFP · GuHCl", style="HeaderSubtitle.TLabel").pack(side="right", padx=(20, 0))
        header.pack(fill="x", before=notebook)

    old_status = None
    for child in app.winfo_children():
        if isinstance(child, ttk.Label):
            try:
                if child.cget("textvariable") == str(app.status_var):
                    old_status = child
                    break
            except tk.TclError:
                pass
    if old_status is not None:
        old_status.pack_forget()

    status_bar = ttk.Frame(app, style="Status.TFrame", padding=(12, 8))
    ttk.Label(status_bar, textvariable=app.status_var, style="Status.TLabel", anchor="w").pack(side="left", fill="x", expand=True)
    app.busy_progress = ttk.Progressbar(status_bar, mode="indeterminate", length=150, style="Horizontal.TProgressbar")
    app.busy_progress.pack(side="right", padx=(12, 0))
    status_bar.pack(side="bottom", fill="x")

    app._busy_buttons = []
    for widget in _walk_widgets(app):
        if isinstance(widget, ttk.Button):
            text = str(widget.cget("text"))
            if text in PRIMARY_ACTIONS:
                widget.configure(style="Primary.TButton")
            elif text.startswith("Delete"):
                widget.configure(style="Danger.TButton")
            if text in BUSY_ACTIONS:
                app._busy_buttons.append(widget)
        elif isinstance(widget, tk.Listbox):
            widget.configure(
                background=SURFACE,
                foreground=TEXT,
                selectbackground=ACCENT,
                selectforeground="#ffffff",
                highlightthickness=1,
                highlightbackground=BORDER,
                relief="flat",
                borderwidth=0,
            )

    for figure, axes, empty_text in (
        (app.figure, app.axes, "Select one or more groups, then run the fit."),
        (app.spectrum_figure, app.spectrum_axes, "Select wells or a practical group, then plot spectra."),
    ):
        figure.patch.set_facecolor(SURFACE)
        axes.clear()
        _style_axes(axes)
        axes.text(0.5, 0.5, empty_text, ha="center", va="center", transform=axes.transAxes, color=MUTED)
        axes.set_xticks([])
        axes.set_yticks([])

    app.canvas.draw_idle()
    app.spectrum_canvas.draw_idle()


def _begin_task(app: tk.Tk, message: str) -> bool:
    if getattr(app, "_task_active", False):
        messagebox.showinfo("Task in progress", "Let the current task finish first.")
        return False
    app._task_active = True
    app.status_var.set(message)
    app.configure(cursor="watch")
    app.busy_progress.start(12)
    for button in getattr(app, "_busy_buttons", []):
        button.state(["disabled"])
    app.update_idletasks()
    return True


def _end_task(app: tk.Tk) -> None:
    app._task_active = False
    app.configure(cursor="")
    app.busy_progress.stop()
    for button in getattr(app, "_busy_buttons", []):
        button.state(["!disabled"])


def _run_background(
    app: tk.Tk,
    work: Callable[[], Any],
    on_success: Callable[[Any], None],
    error_title: str,
    message: str,
) -> None:
    if not _begin_task(app, message):
        return

    future = app._executor.submit(work)

    def poll() -> None:
        if not app.winfo_exists():
            return
        if not future.done():
            app.after(50, poll)
            return
        try:
            result = future.result()
            on_success(result)
        except Exception as exc:
            messagebox.showerror(error_title, str(exc))
            app.status_var.set(str(exc))
        finally:
            _end_task(app)

    app.after(50, poll)


def install(app_module: Any) -> None:
    """Apply the upgrades to the current app module."""
    app_class = app_module.FoldingPracticalApp
    if getattr(app_class, "_enhancements_installed", False):
        return

    original_init = app_class.__init__
    original_build_ui = app_class._build_ui

    def enhanced_init(self: tk.Tk) -> None:
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="folding-practical")
        self._task_active = False
        original_init(self)
        self.protocol("WM_DELETE_WINDOW", self._close_enhanced_app)

    def enhanced_build_ui(self: tk.Tk) -> None:
        _configure_theme(self)
        original_build_ui(self)
        _decorate_ui(self)

    def close_enhanced_app(self: tk.Tk) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
        self.destroy()

    def load_files_async(self: tk.Tk) -> None:
        paths = filedialog.askopenfilenames(
            title="Select CLARIOstar CSV files",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if not paths:
            return

        existing_plate_ids = set(self.data["plate_id"].astype(str)) if not self.data.empty else set()

        def work():
            imported = app_module.load_plate_csvs(paths)
            rename_map = {}
            reserved = set(existing_plate_ids)
            for imported_plate_id in dict.fromkeys(imported["plate_id"].astype(str)):
                candidate = imported_plate_id
                suffix = 2
                while candidate in reserved:
                    candidate = f"{imported_plate_id}_{suffix}"
                    suffix += 1
                rename_map[imported_plate_id] = candidate
                reserved.add(candidate)
            imported = imported.copy()
            imported["plate_id"] = imported["plate_id"].astype(str).map(rename_map)
            return imported

        def finish(imported):
            self.data = app_module.pd.concat([self.data, imported], ignore_index=True) if not self.data.empty else imported
            plates = list(dict.fromkeys(self.data["plate_id"].astype(str)))
            self.plate_combo["values"] = plates
            if not imported.empty:
                self.plate_var.set(str(imported.iloc[0]["plate_id"]))
            elif plates:
                self.plate_var.set(plates[0])
            self.on_plate_changed()
            self.refresh_spectrum_controls()
            self.status_var.set(f"Loaded {len(paths)} file(s) with {len(imported):,} measurements.")

        _run_background(self, work, finish, "Import failed", f"Loading {len(paths)} plate file(s)...")

    def plot_and_fit_async(self: tk.Tk) -> None:
        selected_names = self._selected_group_names()
        if not selected_names:
            messagebox.showinfo("No groups selected", "Select one or more groups to plot.")
            return

        signal_column = (
            "raw fluorescence values"
            if self.signal_mode_var.get() == "Raw fluorescence"
            else "normalized fluorescence values"
        )
        fit_mode = self.fit_mode_var.get()
        temperature = float(self.temperature_var.get())
        assignments = {name: self.assignments[name] for name in selected_names}
        data = self.data

        def work():
            bundles = []
            for group_name in selected_names:
                assignment = assignments[group_name]
                try:
                    group_data = app_module.build_group_dataframe(data, assignment).sort_values("GuHCl concentration (M)")
                    x = group_data["GuHCl concentration (M)"].to_numpy(dtype=float)
                    y = group_data[signal_column].to_numpy(dtype=float)
                    results = []
                    if fit_mode in {"Auto compare", "Two-state thermodynamic", "Fit both"}:
                        results.append(app_module.fit_two_state_denaturation(x, y, temperature_k=temperature))
                    if fit_mode in {"Auto compare", "4PL logistic", "Fit both"}:
                        results.append(app_module.fit_four_parameter_logistic(x, y))
                    best = app_module.choose_best_fit(results)
                    grid = app_module.np.linspace(float(app_module.np.min(x)), float(app_module.np.max(x)), 180)
                    curves = []
                    rows = []
                    for result in results:
                        is_best = best is result
                        prediction = result.predict(grid) if result.success else None
                        curves.append((result, is_best, prediction))
                        rows.append(
                            {
                                "group": group_name,
                                "model": result.model_name,
                                "best": bool(is_best),
                                "success": result.success,
                                "message": result.message,
                                **result.parameters,
                                **{f"se_{key}": value for key, value in result.standard_errors.items()},
                                **result.metrics,
                            }
                        )
                    bundles.append({"group": group_name, "x": x, "y": y, "grid": grid, "curves": curves, "rows": rows})
                except Exception as exc:
                    bundles.append(
                        {
                            "group": group_name,
                            "x": None,
                            "y": None,
                            "grid": None,
                            "curves": [],
                            "rows": [
                                {
                                    "group": group_name,
                                    "model": "Not fitted",
                                    "best": False,
                                    "success": False,
                                    "message": str(exc),
                                }
                            ],
                        }
                    )
            return bundles

        def finish(bundles):
            self.axes.clear()
            _style_axes(self.axes)
            for item in self.report_tree.get_children():
                self.report_tree.delete(item)
            self.last_fit_rows = []

            for bundle in bundles:
                group_name = bundle["group"]
                x = bundle["x"]
                y = bundle["y"]
                if x is not None:
                    point_line = self.axes.plot(
                        x,
                        y,
                        marker="o",
                        markersize=5,
                        linestyle="none",
                        label="_nolegend_",
                    )[0]
                    group_color = point_line.get_color()
                    successful = [curve for curve in bundle["curves"] if curve[0].success]
                    for result, is_best, prediction in bundle["curves"]:
                        if not result.success:
                            continue
                        label = f"{group_name} — {result.model_name}" if is_best or len(successful) == 1 else "_nolegend_"
                        self.axes.plot(
                            bundle["grid"],
                            prediction,
                            linestyle="-" if is_best else "--",
                            linewidth=2.0 if is_best else 1.4,
                            color=group_color,
                            alpha=1.0 if is_best else 0.55,
                            label=label,
                        )
                    if not successful:
                        point_line.set_label(group_name)

                for row in bundle["rows"]:
                    self.last_fit_rows.append(row)
                    self._insert_report_row(row)

            self.axes.set_xlabel("GuHCl concentration (M)")
            self.axes.set_ylabel(signal_column)
            self.axes.set_title("GFP chemical denaturation", loc="left", pad=12, fontweight="bold")
            self.axes.margins(x=0.03)
            handles, labels = self.axes.get_legend_handles_labels()
            if handles:
                self.axes.legend(
                    handles,
                    labels,
                    fontsize="small",
                    ncol=2 if len(handles) > 8 else 1,
                    frameon=False,
                )
            self.canvas.draw_idle()
            self.status_var.set(f"Fitted {len(selected_names)} group(s). Compare AICc only when both models converged.")

        _run_background(self, work, finish, "Fit failed", f"Fitting {len(selected_names)} group(s)...")

    def plot_spectra_async(self: tk.Tk) -> None:
        wells = self._selected_spectrum_wells()
        if not wells:
            messagebox.showinfo("No wells selected", "Select one or more wells to plot.")
            return

        plate_id = self.spectrum_plate_var.get()
        measurement = self.spectrum_measurement_var.get()
        signal_mode = self.spectrum_signal_mode_var.get()
        data = self.data

        def work():
            spectrum = app_module.build_spectrum_dataframe(
                data,
                plate_id=plate_id,
                measurement=measurement,
                wells=wells,
            )
            y_column = (
                "raw fluorescence values"
                if signal_mode == "Raw fluorescence"
                else "peak-normalized fluorescence values"
            )
            series = []
            for well, well_data in spectrum.groupby("well", sort=False):
                series.append(
                    (
                        str(well),
                        well_data["wavelength_nm"].to_numpy(dtype=float),
                        well_data[y_column].to_numpy(dtype=float),
                    )
                )
            return spectrum, y_column, series

        def finish(payload):
            spectrum, y_column, series = payload
            self.spectrum_axes.clear()
            _style_axes(self.spectrum_axes)
            count = len(series)
            marker = None if count > 24 else "o"
            markevery = 4 if count > 12 else None
            for well, wavelengths, values in series:
                self.spectrum_axes.plot(
                    wavelengths,
                    values,
                    marker=marker,
                    markevery=markevery,
                    markersize=3,
                    linewidth=1.4,
                    alpha=0.85,
                    label=well,
                )
            self.spectrum_axes.set_xlabel("Emission wavelength (nm)")
            self.spectrum_axes.set_ylabel(y_column)
            self.spectrum_axes.set_title(f"{measurement} — {plate_id}", loc="left", pad=12, fontweight="bold")
            self.spectrum_axes.margins(x=0.02)
            if count <= 24:
                self.spectrum_axes.legend(fontsize="small", ncol=2, frameon=False)
            self.spectrum_canvas.draw_idle()
            extra = " The legend was hidden for speed." if count > 24 else ""
            self.status_var.set(f"Plotted spectra for {spectrum['well'].nunique()} well(s).{extra}")

        _run_background(self, work, finish, "Cannot plot spectra", f"Preparing {len(wells)} spectra...")

    app_class.__init__ = enhanced_init
    app_class._build_ui = enhanced_build_ui
    app_class._close_enhanced_app = close_enhanced_app
    app_class.load_files = load_files_async
    app_class.plot_and_fit = plot_and_fit_async
    app_class.plot_spectra = plot_spectra_async
    app_class._enhancements_installed = True
