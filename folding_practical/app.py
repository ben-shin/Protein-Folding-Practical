"""Tkinter desktop application for the protein-folding practical."""

from __future__ import annotations

import json
import re
import traceback
from dataclasses import asdict
from pathlib import Path
from typing import Optional
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk

from . import platemap
from .batch import run_batch_export
from .models import choose_best_fit, fit_four_parameter_logistic, fit_two_state_denaturation
from .plate_io import load_plate_csvs
from .platemap import PlateMap
from .project import (
    GroupAssignment,
    build_group_dataframe,
    build_spectrum_dataframe,
    export_group_csv,
    export_group_spectrum_csv,
    inspect_group_map,
    load_group_map_assignments,
)
from .wells import consecutive_wells, expand_well_spec, well_sort_key

MUTED_TEXT = "#667085"
OK_TEXT = "#0a7a52"
DANGER_TEXT = "#b42318"


class ScrollableColumn(ttk.Frame):
    """A control column that scrolls when the window is too short for it.

    The side panels are packed top to bottom, so on a small screen the last
    buttons in them — Plot, Save, Export — used to be pushed off the bottom
    edge with no way to reach them. Build the panel inside ``interior``
    instead and it stays reachable at any window size.
    """

    def __init__(self, master: tk.Misc, width: int = 300) -> None:
        super().__init__(master)
        background = ttk.Style().lookup("TFrame", "background") or "#ffffff"
        self.canvas = tk.Canvas(
            self,
            width=width,
            highlightthickness=0,
            borderwidth=0,
            background=background,
        )
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.interior = ttk.Frame(self.canvas)
        self._window = self.canvas.create_window((0, 0), window=self.interior, anchor="nw")
        self.canvas.configure(yscrollcommand=self._on_scroll)

        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.scrollbar.grid(row=0, column=1, sticky="ns")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        self._wheel_bound: set[str] = set()
        self.interior.bind("<Configure>", self._on_interior_resized)
        self.canvas.bind("<Configure>", self._on_canvas_resized)

    def _on_interior_resized(self, _event: tk.Event) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        self.canvas.configure(width=self.interior.winfo_reqwidth())
        self._fit_interior()

    def _on_canvas_resized(self, event: tk.Event) -> None:
        self.canvas.itemconfigure(self._window, width=event.width)
        self._fit_interior()

    def _fit_interior(self) -> None:
        """Fill the canvas when there is room to spare, scroll when there is not.

        Without this the panel would always sit at its minimum height, so a
        list inside it could never grow to use a tall window.
        """
        self.canvas.itemconfigure(
            self._window,
            height=max(self.canvas.winfo_height(), self.interior.winfo_reqheight()),
        )

    def _on_scroll(self, first: str, last: str) -> None:
        """Only show the scrollbar when something is actually out of view."""
        if float(first) <= 0.0 and float(last) >= 1.0:
            self.scrollbar.grid_remove()
        else:
            self.scrollbar.grid()
        self.scrollbar.set(first, last)

    def bind_mouse_wheel(self) -> None:
        """Enable wheel scrolling, leaving list and tree widgets their own.

        Safe to call again after more widgets are added to the panel; each one
        is only ever bound once.
        """
        for widget in self._scrollable_children(self):
            name = str(widget)
            if name in self._wheel_bound:
                continue
            self._wheel_bound.add(name)
            widget.bind("<MouseWheel>", self._on_wheel, add="+")
            widget.bind("<Button-4>", self._on_wheel, add="+")
            widget.bind("<Button-5>", self._on_wheel, add="+")

    def _scrollable_children(self, widget: tk.Misc):
        for child in widget.winfo_children():
            if isinstance(child, (tk.Listbox, tk.Text, ttk.Treeview)):
                continue
            yield child
            yield from self._scrollable_children(child)
        if widget is self:
            yield self.canvas

    def _on_wheel(self, event: tk.Event) -> str:
        first, last = self.canvas.yview()
        if first <= 0.0 and last >= 1.0:
            return ""
        if getattr(event, "num", None) == 4:
            steps = -1
        elif getattr(event, "num", None) == 5:
            steps = 1
        else:
            steps = -1 if event.delta > 0 else 1
        self.canvas.yview_scroll(steps, "units")
        return "break"


class FoldingPracticalApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Protein Folding Practical")
        self.geometry("1450x900")
        self.minsize(1100, 700)

        self.data = pd.DataFrame()
        self.assignments: dict[str, GroupAssignment] = {}
        self.selected_wells: list[str] = []
        self.last_fit_rows: list[dict[str, object]] = []
        self.last_directory: Optional[str] = None

        self.status_var = tk.StringVar(value="Step 1 — load the plate reader CSV files, or use Batch export to do everything at once.")
        self.plate_var = tk.StringVar()
        self.measurement_var = tk.StringVar()
        self.wavelength_var = tk.StringVar()
        self.group_name_var = tk.StringVar(value="Group 1")
        self.hover_var = tk.StringVar(value="Hover a well to read its value.")
        self.selection_summary_var = tk.StringVar(value="No wells selected")
        self.block_select_var = tk.BooleanVar(value=True)
        self.concentration_var = tk.StringVar(value="0, 0.4, 0.8, 1.2, 1.6, 2.0, 2.4, 2.8, 3.2, 3.6, 4.0, 4.4, 4.8, 5.2, 5.6, 6.0")
        self.start_well_var = tk.StringVar(value="A1")
        self.count_var = tk.IntVar(value=16)
        self.order_var = tk.StringVar(value="row-major")
        self.conc_start_var = tk.DoubleVar(value=0.0)
        self.conc_stop_var = tk.DoubleVar(value=6.0)
        self.conc_count_var = tk.IntVar(value=16)
        self.temperature_var = tk.DoubleVar(value=298.15)
        self.signal_mode_var = tk.StringVar(value="Raw fluorescence")
        self.fit_mode_var = tk.StringVar(value="Auto compare")
        self.spectrum_plate_var = tk.StringVar()
        self.spectrum_measurement_var = tk.StringVar()
        self.spectrum_group_var = tk.StringVar()
        self.spectrum_well_spec_var = tk.StringVar(value="A1")
        self.spectrum_signal_mode_var = tk.StringVar(value="Raw fluorescence")

        self._build_ui()

    def _build_ui(self) -> None:
        self._build_menu()
        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=8, pady=8)
        self.notebook = notebook

        self.data_tab = ttk.Frame(notebook)
        self.analysis_tab = ttk.Frame(notebook)
        self.spectrum_tab = ttk.Frame(notebook)
        notebook.add(self.data_tab, text="1. Import and assign wells")
        notebook.add(self.analysis_tab, text="2. Denaturation analysis")
        notebook.add(self.spectrum_tab, text="3. Well spectra")

        self._build_data_tab()
        self._build_analysis_tab()
        self._build_spectrum_tab()
        ttk.Label(self, textvariable=self.status_var, anchor="w").pack(fill="x", padx=10, pady=(0, 8))
        self.analysis_controls.bind_mouse_wheel()
        self.spectrum_controls.bind_mouse_wheel()
        self._bind_shortcuts()

    def _build_menu(self) -> None:
        menubar = tk.Menu(self)

        file_menu = tk.Menu(menubar, tearoff=False)
        file_menu.add_command(label="Load plate CSV files", accelerator="Ctrl+O", command=self.load_files)
        file_menu.add_command(label="Load group map CSV", accelerator="Ctrl+G", command=self.load_group_map)
        file_menu.add_separator()
        file_menu.add_command(label="Batch export from files", accelerator="Ctrl+B", command=self.batch_export_wizard)
        file_menu.add_command(label="Export all group CSVs", accelerator="Ctrl+E", command=self.export_groups)
        file_menu.add_command(label="Export spectra CSVs", command=self.export_group_spectra)
        file_menu.add_command(label="Export tidy data", command=self.export_tidy_data)
        file_menu.add_separator()
        file_menu.add_command(label="Save group mapping", accelerator="Ctrl+S", command=self.save_project)
        file_menu.add_command(label="Load group mapping", command=self.load_project)
        file_menu.add_separator()
        file_menu.add_command(label="Quit", command=self.quit_app)
        menubar.add_cascade(label="File", menu=file_menu)

        help_menu = tk.Menu(menubar, tearoff=False)
        help_menu.add_command(label="Quick start", accelerator="F1", command=self.show_quick_start)
        help_menu.add_command(label="About", command=self.show_about)
        menubar.add_cascade(label="Help", menu=help_menu)

        self.configure(menu=menubar)

    def _bind_shortcuts(self) -> None:
        for sequence, command in (
            ("<Control-o>", self.load_files),
            ("<Control-g>", self.load_group_map),
            ("<Control-b>", self.batch_export_wizard),
            ("<Control-e>", self.export_groups),
            ("<Control-s>", self.save_project),
            ("<F1>", self.show_quick_start),
        ):
            self.bind_all(sequence, self._shortcut(command))

    def _shortcut(self, action):
        """Run ``action`` unless a text field has focus.

        Tk already gives entry widgets Control-b and Control-e for cursor
        movement, so typing a group name must never fire an export.
        """

        def handler(event: tk.Event):
            widget = self.focus_get()
            if isinstance(widget, (tk.Entry, tk.Spinbox, tk.Text)):
                return None
            action()
            return "break"

        return handler

    def quit_app(self) -> None:
        closer = getattr(self, "_close_enhanced_app", None)
        if callable(closer):
            closer()
        else:
            self.destroy()

    def show_quick_start(self) -> None:
        messagebox.showinfo(
            "Quick start",
            "Fastest route — Batch export (Ctrl+B)\n"
            "  1. Pick every plate reader CSV.\n"
            "  2. Pick the group map CSV.\n"
            "  3. Pick a folder.\n"
            "Each group gets a denaturation CSV and a wavelength-by-concentration CSV, "
            "and the data stays loaded for the analysis tabs.\n\n"
            "Assigning groups by hand\n"
            "  1. Load CSV files, then choose the plate, signal and wavelength.\n"
            "  2. Leave block selection ticked and click the first well of a group. "
            "That well and the next ones in reading order are taken, wherever the group started.\n"
            "  3. Check the numbers on the wells: they are the concentration order.\n"
            "  4. Name the group and use Add or replace group.\n"
            "  5. Double-click any saved group to load it back and fix it.\n\n"
            "Right click removes one well. Untick block selection to click wells one by one or drag across them.",
        )

    def show_about(self) -> None:
        messagebox.showinfo(
            "About",
            "Protein Folding Practical\n"
            "Imperial College London — Dr. Ernesto Cota\n\n"
            "Questions, bugs and suggestions: benwshin@gmail.com",
        )

    def _build_data_tab(self) -> None:
        self.data_tab.columnconfigure(0, weight=3)
        self.data_tab.columnconfigure(1, weight=2)
        self.data_tab.rowconfigure(1, weight=1)

        import_bar = ttk.Frame(self.data_tab)
        import_bar.grid(row=0, column=0, columnspan=2, sticky="ew", padx=6, pady=6)
        ttk.Button(import_bar, text="Load CSV files", command=self.load_files).pack(side="left")
        ttk.Button(import_bar, text="Load group map CSV", command=self.load_group_map).pack(side="left", padx=6)
        ttk.Button(import_bar, text="Batch export", command=self.batch_export_wizard).pack(side="left")
        ttk.Label(import_bar, text="Plate:").pack(side="left", padx=(18, 4))
        self.plate_combo = ttk.Combobox(import_bar, textvariable=self.plate_var, state="readonly", width=22)
        self.plate_combo.pack(side="left")
        self.plate_combo.bind("<<ComboboxSelected>>", self.on_plate_changed)
        ttk.Label(import_bar, text="Signal:").pack(side="left", padx=(18, 4))
        self.measurement_combo = ttk.Combobox(import_bar, textvariable=self.measurement_var, state="readonly", width=32)
        self.measurement_combo.pack(side="left")
        self.measurement_combo.bind("<<ComboboxSelected>>", self.on_measurement_changed)
        ttk.Label(import_bar, text="Wavelength:").pack(side="left", padx=(18, 4))
        self.wavelength_combo = ttk.Combobox(import_bar, textvariable=self.wavelength_var, state="disabled", width=11)
        self.wavelength_combo.pack(side="left")
        self.wavelength_combo.bind("<<ComboboxSelected>>", lambda _event: self.refresh_plate())

        left = ttk.Frame(self.data_tab)
        left.grid(row=1, column=0, sticky="nsew", padx=(6, 3), pady=6)
        left.columnconfigure(0, weight=1)
        left.rowconfigure(1, weight=3)
        left.rowconfigure(3, weight=2)

        map_header = ttk.Frame(left)
        map_header.grid(row=0, column=0, sticky="ew")
        ttk.Label(map_header, text="Plate map — click wells in concentration order").pack(side="left")
        self._build_plate_legend(map_header)

        self.plate_map = PlateMap(
            left,
            on_click=self.on_well_clicked,
            on_drag=self.on_well_dragged,
            on_right_click=self.on_well_right_clicked,
            on_hover=self.on_well_hover,
        )
        self.plate_map.grid(row=1, column=0, sticky="nsew", pady=(4, 2))
        ttk.Label(left, textvariable=self.hover_var, anchor="w").grid(row=2, column=0, sticky="ew", pady=(0, 6))

        preview_frame = ttk.LabelFrame(left, text="Imported values for current plate and signal")
        preview_frame.grid(row=3, column=0, sticky="nsew")
        preview_frame.columnconfigure(0, weight=1)
        preview_frame.rowconfigure(0, weight=1)
        self.preview_tree = ttk.Treeview(preview_frame, columns=("well", "value", "group", "source"), show="headings", height=8)
        for column, title, width, anchor in (
            ("well", "Well", 70, "center"),
            ("value", "Value", 130, "e"),
            ("group", "Group", 110, "w"),
            ("source", "Source file", 200, "w"),
        ):
            self.preview_tree.heading(column, text=title)
            self.preview_tree.column(column, width=width, anchor=anchor)
        self.preview_tree.grid(row=0, column=0, sticky="nsew")
        preview_scroll = ttk.Scrollbar(preview_frame, orient="vertical", command=self.preview_tree.yview)
        preview_scroll.grid(row=0, column=1, sticky="ns")
        self.preview_tree.configure(yscrollcommand=preview_scroll.set)

        right = ttk.Frame(self.data_tab)
        right.grid(row=1, column=1, sticky="nsew", padx=(3, 6), pady=6)
        right.columnconfigure(0, weight=1)
        right.rowconfigure(4, weight=1)

        selected_frame = ttk.LabelFrame(right, text="Selected wells")
        selected_frame.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        selected_frame.columnconfigure(0, weight=1)
        self.selected_label = ttk.Label(selected_frame, text="None", wraplength=460, justify="left")
        self.selected_label.grid(row=0, column=0, columnspan=3, sticky="ew", padx=6, pady=(6, 2))
        self.selection_summary_label = ttk.Label(selected_frame, textvariable=self.selection_summary_var)
        self.selection_summary_label.grid(row=1, column=0, sticky="w", padx=6, pady=(0, 6))
        ttk.Button(selected_frame, text="Undo last well", command=self.undo_last_well).grid(row=1, column=1, padx=4, pady=(0, 6))
        ttk.Button(selected_frame, text="Clear selection", command=self.clear_selection).grid(row=1, column=2, padx=6, pady=(0, 6))

        helper = ttk.LabelFrame(right, text="Well selection")
        helper.grid(row=1, column=0, sticky="ew", pady=6)
        helper.columnconfigure(6, weight=1)
        ttk.Checkbutton(
            helper,
            text="One click selects a whole block of conditions",
            variable=self.block_select_var,
            command=self.on_block_mode_changed,
        ).grid(row=0, column=0, columnspan=7, sticky="w", padx=5, pady=(4, 2))
        ttk.Label(helper, text="Conditions").grid(row=1, column=0, padx=(5, 2), pady=4)
        ttk.Spinbox(helper, from_=1, to=96, textvariable=self.count_var, width=6).grid(row=1, column=1, padx=2, pady=4)
        ttk.Label(helper, text="Order").grid(row=1, column=2, padx=(10, 2), pady=4)
        ttk.Combobox(
            helper,
            textvariable=self.order_var,
            values=("row-major", "column-major"),
            state="readonly",
            width=13,
        ).grid(row=1, column=3, padx=2, pady=4)
        ttk.Label(helper, text="Start well").grid(row=1, column=4, padx=(10, 2), pady=4)
        ttk.Entry(helper, textvariable=self.start_well_var, width=7).grid(row=1, column=5, padx=2, pady=4)
        ttk.Button(helper, text="Select", command=self.select_consecutive).grid(row=1, column=6, padx=5, pady=4, sticky="e")
        ttk.Label(
            helper,
            text="Left click starts a block, drag or click adds wells, right click removes one.",
            foreground="#667085",
            wraplength=460,
            justify="left",
        ).grid(row=2, column=0, columnspan=7, sticky="w", padx=5, pady=(0, 5))

        concentration_frame = ttk.LabelFrame(right, text="Group definition")
        concentration_frame.grid(row=2, column=0, sticky="ew", pady=6)
        concentration_frame.columnconfigure(1, weight=1)
        ttk.Label(concentration_frame, text="Group name").grid(row=0, column=0, sticky="w", padx=5, pady=4)
        ttk.Entry(concentration_frame, textvariable=self.group_name_var).grid(row=0, column=1, columnspan=5, sticky="ew", padx=5, pady=4)
        ttk.Label(concentration_frame, text="GuHCl concentrations (M)").grid(row=1, column=0, sticky="nw", padx=5, pady=4)
        concentration_entry = ttk.Entry(concentration_frame, textvariable=self.concentration_var)
        concentration_entry.grid(row=1, column=1, columnspan=5, sticky="ew", padx=5, pady=4)
        self.concentration_var.trace_add("write", lambda *_args: self._update_selected_label())
        ttk.Label(concentration_frame, text="Generate:").grid(row=2, column=0, sticky="w", padx=5, pady=4)
        ttk.Entry(concentration_frame, textvariable=self.conc_start_var, width=8).grid(row=2, column=1, padx=3, pady=4)
        ttk.Label(concentration_frame, text="to").grid(row=2, column=2, padx=3)
        ttk.Entry(concentration_frame, textvariable=self.conc_stop_var, width=8).grid(row=2, column=3, padx=3, pady=4)
        ttk.Spinbox(concentration_frame, from_=3, to=96, textvariable=self.conc_count_var, width=7).grid(row=2, column=4, padx=3, pady=4)
        ttk.Button(concentration_frame, text="Generate list", command=self.generate_concentrations).grid(row=2, column=5, padx=5, pady=4)
        ttk.Button(concentration_frame, text="Add or replace group", command=self.add_group).grid(row=3, column=0, columnspan=6, sticky="ew", padx=5, pady=6)

        action_frame = ttk.Frame(right)
        action_frame.grid(row=3, column=0, sticky="ew", pady=6)
        ttk.Button(action_frame, text="Edit selected group", command=self.edit_selected_group).pack(side="left")
        ttk.Button(action_frame, text="Delete selected group", command=self.delete_group).pack(side="left", padx=6)
        ttk.Button(action_frame, text="Export all group CSVs", command=self.export_groups).pack(side="left")
        ttk.Button(action_frame, text="Export spectra CSVs", command=self.export_group_spectra).pack(side="left", padx=6)

        groups_frame = ttk.LabelFrame(right, text="Assigned practical groups — double-click a row to edit it")
        groups_frame.grid(row=4, column=0, sticky="nsew")
        groups_frame.columnconfigure(0, weight=1)
        groups_frame.rowconfigure(0, weight=1)
        self.group_tree = ttk.Treeview(
            groups_frame,
            columns=("group", "plate", "signal", "wavelength", "count", "wells"),
            show="headings",
        )
        for column, title, width in (
            ("group", "Group", 120),
            ("plate", "Plate", 100),
            ("signal", "Signal", 130),
            ("wavelength", "\u03bb (nm)", 70),
            ("count", "N", 45),
            ("wells", "Wells", 260),
        ):
            self.group_tree.heading(column, text=title)
            self.group_tree.column(column, width=width)
        self.group_tree.grid(row=0, column=0, sticky="nsew")
        self.group_tree.bind("<Double-Button-1>", lambda _event: self.edit_selected_group())
        group_scroll = ttk.Scrollbar(groups_frame, orient="vertical", command=self.group_tree.yview)
        group_scroll.grid(row=0, column=1, sticky="ns")
        self.group_tree.configure(yscrollcommand=group_scroll.set)

    def _build_plate_legend(self, parent: ttk.Frame) -> None:
        """Small colour key so the plate map explains itself."""
        legend = ttk.Frame(parent)
        legend.pack(side="right")
        for text, color in (
            ("selected", platemap.SELECTED_FILL),
            ("in another group", platemap.GROUP_FILLS[0][0]),
            ("no data", platemap.EMPTY_FILL),
        ):
            tk.Label(legend, background=color, width=2, relief="solid", borderwidth=1).pack(side="left", padx=(10, 3))
            ttk.Label(legend, text=text).pack(side="left")

    def _build_analysis_tab(self) -> None:
        self.analysis_tab.columnconfigure(1, weight=1)
        self.analysis_tab.rowconfigure(0, weight=1)

        self.analysis_controls = ScrollableColumn(self.analysis_tab)
        self.analysis_controls.grid(row=0, column=0, sticky="ns", padx=6, pady=6)
        controls = self.analysis_controls.interior
        ttk.Label(controls, text="Groups (Ctrl/Shift for multiple)").pack(anchor="w")
        self.analysis_group_list = tk.Listbox(controls, selectmode=tk.EXTENDED, exportselection=False, width=34, height=12)
        self.analysis_group_list.pack(fill="x", pady=(4, 10))

        ttk.Label(controls, text="Fit model").pack(anchor="w")
        ttk.Combobox(
            controls,
            textvariable=self.fit_mode_var,
            state="readonly",
            values=("Auto compare", "Two-state thermodynamic", "4PL logistic", "Fit both"),
            width=30,
        ).pack(fill="x", pady=(4, 10))
        ttk.Label(controls, text="Plot/fitting signal").pack(anchor="w")
        ttk.Combobox(
            controls,
            textvariable=self.signal_mode_var,
            state="readonly",
            values=("Raw fluorescence", "Normalized fluorescence"),
            width=30,
        ).pack(fill="x", pady=(4, 10))
        ttk.Label(controls, text="Temperature (K)").pack(anchor="w")
        ttk.Entry(controls, textvariable=self.temperature_var).pack(fill="x", pady=(4, 10))
        ttk.Button(controls, text="Plot and fit selected groups", command=self.plot_and_fit).pack(fill="x", pady=3)
        ttk.Button(controls, text="Select all groups", command=self.select_all_analysis_groups).pack(fill="x", pady=3)
        ttk.Button(controls, text="Save graph", command=self.save_graph).pack(fill="x", pady=3)
        ttk.Button(controls, text="Export fit report CSV", command=self.export_fit_report).pack(fill="x", pady=3)
        ttk.Button(controls, text="Export detailed report text", command=self.export_detailed_report).pack(fill="x", pady=3)

        plot_area = ttk.Frame(self.analysis_tab)
        plot_area.grid(row=0, column=1, sticky="nsew", padx=6, pady=6)
        plot_area.columnconfigure(0, weight=1)
        plot_area.rowconfigure(0, weight=3)
        plot_area.rowconfigure(1, weight=1)

        self.figure, self.axes = plt.subplots(figsize=(9, 6), constrained_layout=True)
        self.canvas = FigureCanvasTkAgg(self.figure, master=plot_area)
        self.canvas.get_tk_widget().grid(row=0, column=0, sticky="nsew")
        toolbar_frame = ttk.Frame(plot_area)
        toolbar_frame.grid(row=0, column=0, sticky="sw")
        self.toolbar = NavigationToolbar2Tk(self.canvas, toolbar_frame, pack_toolbar=False)
        self.toolbar.update()
        self.toolbar.pack(side="left")

        report_frame = ttk.LabelFrame(plot_area, text="Fit summary")
        report_frame.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
        report_frame.columnconfigure(0, weight=1)
        report_frame.rowconfigure(0, weight=1)
        columns = ("group", "model", "best", "dg_unf", "dg_fold", "m", "cm", "rmse", "r2", "aicc", "status")
        self.report_tree = ttk.Treeview(report_frame, columns=columns, show="headings")
        headings = {
            "group": "Group",
            "model": "Model",
            "best": "Preferred fit?",
            "dg_unf": "ΔG°unfold (kJ/mol)",
            "dg_fold": "ΔG°fold (kJ/mol)",
            "m": "m (kJ/mol/M)",
            "cm": "Cm (M)",
            "rmse": "RMSE",
            "r2": "R²",
            "aicc": "AICc",
            "status": "Status",
        }
        for column in columns:
            self.report_tree.heading(column, text=headings[column])
            self.report_tree.column(column, width=105 if column not in {"group", "status"} else 150)
        self.report_tree.grid(row=0, column=0, sticky="nsew")
        report_scroll = ttk.Scrollbar(report_frame, orient="vertical", command=self.report_tree.yview)
        report_scroll.grid(row=0, column=1, sticky="ns")
        self.report_tree.configure(yscrollcommand=report_scroll.set)

    def _build_spectrum_tab(self) -> None:
        self.spectrum_tab.columnconfigure(1, weight=1)
        self.spectrum_tab.rowconfigure(0, weight=1)

        self.spectrum_controls = ScrollableColumn(self.spectrum_tab)
        self.spectrum_controls.grid(row=0, column=0, sticky="ns", padx=6, pady=6)
        controls = self.spectrum_controls.interior

        ttk.Label(controls, text="Plate").pack(anchor="w")
        self.spectrum_plate_combo = ttk.Combobox(
            controls,
            textvariable=self.spectrum_plate_var,
            state="readonly",
            width=32,
        )
        self.spectrum_plate_combo.pack(fill="x", pady=(4, 8))
        self.spectrum_plate_combo.bind("<<ComboboxSelected>>", self.on_spectrum_plate_changed)

        ttk.Label(controls, text="Spectrum readout").pack(anchor="w")
        self.spectrum_measurement_combo = ttk.Combobox(
            controls,
            textvariable=self.spectrum_measurement_var,
            state="readonly",
            width=32,
        )
        self.spectrum_measurement_combo.pack(fill="x", pady=(4, 8))
        self.spectrum_measurement_combo.bind("<<ComboboxSelected>>", lambda _event: self.refresh_spectrum_wells())

        ttk.Label(controls, text="Practical group").pack(anchor="w")
        group_row = ttk.Frame(controls)
        group_row.pack(fill="x", pady=(4, 8))
        group_row.columnconfigure(0, weight=1)
        self.spectrum_group_combo = ttk.Combobox(
            group_row,
            textvariable=self.spectrum_group_var,
            state="readonly",
            width=18,
        )
        self.spectrum_group_combo.grid(row=0, column=0, sticky="ew")
        ttk.Button(group_row, text="Select entire group", command=self.use_group_for_spectra).grid(
            row=0, column=1, padx=(6, 0)
        )

        ttk.Label(controls, text="Wells (for example A1-A4, B2)").pack(anchor="w")
        well_entry_row = ttk.Frame(controls)
        well_entry_row.pack(fill="x", pady=(4, 4))
        well_entry_row.columnconfigure(0, weight=1)
        ttk.Entry(well_entry_row, textvariable=self.spectrum_well_spec_var).grid(row=0, column=0, sticky="ew")
        ttk.Button(well_entry_row, text="Select", command=self.select_spectrum_wells_from_spec).grid(
            row=0, column=1, padx=(6, 0)
        )

        select_grid = ttk.Frame(controls)
        select_grid.pack(fill="x", pady=(0, 8))
        select_grid.columnconfigure(0, weight=1)
        select_grid.columnconfigure(1, weight=1)
        for index, (text, command) in enumerate(
            (
                ("Use plate-map selection", self.use_plate_map_selection_for_spectra),
                ("Select all wells", self.select_all_spectrum_wells),
                ("Clear selection", self.clear_spectrum_wells),
            )
        ):
            ttk.Button(select_grid, text=text, command=command).grid(
                row=index // 2, column=index % 2, sticky="ew", padx=(0, 3) if index % 2 == 0 else (3, 0), pady=2
            )

        ttk.Label(controls, text="Available wells (Ctrl/Shift for multiple)").pack(anchor="w")
        well_frame = ttk.Frame(controls)
        well_frame.pack(fill="both", expand=True, pady=(4, 8))
        self.spectrum_well_list = tk.Listbox(
            well_frame,
            selectmode=tk.EXTENDED,
            exportselection=False,
            width=32,
            height=6,
        )
        self.spectrum_well_list.pack(side="left", fill="both", expand=True)
        well_scroll = ttk.Scrollbar(well_frame, orient="vertical", command=self.spectrum_well_list.yview)
        well_scroll.pack(side="right", fill="y")
        self.spectrum_well_list.configure(yscrollcommand=well_scroll.set)

        ttk.Label(controls, text="Spectrum scale").pack(anchor="w")
        ttk.Combobox(
            controls,
            textvariable=self.spectrum_signal_mode_var,
            state="readonly",
            values=("Raw fluorescence", "Peak-normalized fluorescence"),
            width=30,
        ).pack(fill="x", pady=(4, 8))
        ttk.Button(controls, text="Plot selected well spectra", command=self.plot_spectra).pack(fill="x", pady=3)
        ttk.Button(controls, text="Save spectrum graph", command=self.save_spectrum_graph).pack(fill="x", pady=3)
        ttk.Button(controls, text="Export selected spectra CSV", command=self.export_selected_spectra).pack(fill="x", pady=3)

        plot_area = ttk.Frame(self.spectrum_tab)
        plot_area.grid(row=0, column=1, sticky="nsew", padx=6, pady=6)
        plot_area.columnconfigure(0, weight=1)
        plot_area.rowconfigure(0, weight=1)

        self.spectrum_figure, self.spectrum_axes = plt.subplots(figsize=(9, 6), constrained_layout=True)
        self.spectrum_canvas = FigureCanvasTkAgg(self.spectrum_figure, master=plot_area)
        self.spectrum_canvas.get_tk_widget().grid(row=0, column=0, sticky="nsew")
        toolbar_frame = ttk.Frame(plot_area)
        toolbar_frame.grid(row=0, column=0, sticky="sw")
        self.spectrum_toolbar = NavigationToolbar2Tk(self.spectrum_canvas, toolbar_frame, pack_toolbar=False)
        self.spectrum_toolbar.update()
        self.spectrum_toolbar.pack(side="left")

    # ---------------------------------------------------------- file dialogs

    def _remember_directory(self, path: object) -> None:
        """Keep every dialog opening where the last one left off."""
        if not path:
            return
        candidate = Path(str(path))
        self.last_directory = str(candidate if candidate.is_dir() else candidate.parent)

    def ask_open_files(self, title: str, filetypes=None) -> tuple[str, ...]:
        paths = filedialog.askopenfilenames(
            title=title,
            initialdir=self.last_directory,
            filetypes=filetypes or [("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if paths:
            self._remember_directory(paths[0])
        return tuple(paths)

    def ask_open_file(self, title: str, filetypes=None) -> str:
        path = filedialog.askopenfilename(
            title=title,
            initialdir=self.last_directory,
            filetypes=filetypes or [("CSV files", "*.csv"), ("All files", "*.*")],
        )
        self._remember_directory(path)
        return path

    def ask_save_file(self, *, defaultextension: str, filetypes, initialfile: str, title: str = "Save") -> str:
        path = filedialog.asksaveasfilename(
            title=title,
            initialdir=self.last_directory,
            defaultextension=defaultextension,
            filetypes=filetypes,
            initialfile=initialfile,
        )
        self._remember_directory(path)
        return path

    def ask_directory(self, title: str) -> str:
        path = filedialog.askdirectory(title=title, initialdir=self.last_directory)
        self._remember_directory(path)
        return path

    def load_files(self) -> None:
        paths = self.ask_open_files("Select plate reader CSV files")
        if not paths:
            return
        try:
            imported = load_plate_csvs(paths, existing_data=self.data)
            existing_plate_ids = set(self.data["plate_id"].astype(str)) if not self.data.empty else set()
            rename_map: dict[str, str] = {}
            for imported_plate_id in dict.fromkeys(imported["plate_id"].astype(str)):
                candidate = imported_plate_id
                suffix = 2
                while candidate in existing_plate_ids:
                    candidate = f"{imported_plate_id}_{suffix}"
                    suffix += 1
                rename_map[imported_plate_id] = candidate
                existing_plate_ids.add(candidate)
            imported["plate_id"] = imported["plate_id"].astype(str).map(rename_map)
            self.data = pd.concat([self.data, imported], ignore_index=True) if not self.data.empty else imported
            plates = list(dict.fromkeys(self.data["plate_id"].astype(str)))
            self.plate_combo["values"] = plates
            if plates:
                self.plate_var.set(rename_map.get(str(imported.iloc[0]["plate_id"]), str(imported.iloc[0]["plate_id"])))
                if self.plate_var.get() not in plates:
                    self.plate_var.set(plates[0])
            self.on_plate_changed()
            self.refresh_spectrum_controls()
            self.status_var.set(f"Loaded {len(paths)} file(s), {len(imported):,} tidy measurement rows.")
        except Exception as exc:
            messagebox.showerror("Import failed", f"{exc}\n\n{traceback.format_exc(limit=1)}")


    def load_group_map(self) -> None:
        path = self.ask_open_file("Select group map CSV")
        if not path:
            return
        try:
            wavelength = float(self.wavelength_var.get()) if self.wavelength_var.get() else None
            failures: dict[str, str] = {}
            imported = load_group_map_assignments(
                self.data,
                path,
                default_concentrations=self._parse_concentrations(),
                default_measurement=self.measurement_var.get(),
                default_wavelength_nm=wavelength,
                existing_assignments=self.assignments,
                failures=failures,
            )
            self.assignments.update(imported)
            self.refresh_group_views()
            self.report_group_map_result(imported, failures, path)
        except Exception as exc:
            messagebox.showerror("Cannot load group map", str(exc))

    def report_group_map_result(self, imported: dict, failures: dict[str, str], path: str) -> None:
        """Say what loaded and, in one place, what did not."""
        summary = f"Loaded {len(imported)} practical group(s) from {Path(path).name}."
        if failures:
            detail = "\n".join(f"{name}: {message}" for name, message in list(failures.items())[:12])
            if len(failures) > 12:
                detail += f"\n... and {len(failures) - 12} more"
            messagebox.showwarning(
                "Some group map rows were skipped",
                f"{summary}\n\n{len(failures)} row(s) could not be read:\n\n{detail}",
            )
            self.status_var.set(f"{summary} {len(failures)} row(s) skipped.")
        else:
            self.status_var.set(summary)

    def on_plate_changed(self, _event: Optional[object] = None) -> None:
        if self.data.empty or not self.plate_var.get():
            self.measurement_combo["values"] = ()
            self.measurement_var.set("")
            self.wavelength_combo["values"] = ()
            self.wavelength_var.set("")
            self.wavelength_combo.configure(state="disabled")
            self.refresh_plate()
            return
        measurements = list(
            dict.fromkeys(
                self.data.loc[self.data["plate_id"] == self.plate_var.get(), "measurement"].astype(str)
            )
        )
        self.measurement_combo["values"] = measurements
        if self.measurement_var.get() not in measurements:
            self.measurement_var.set(measurements[0] if measurements else "")
        self.on_measurement_changed()

    def on_measurement_changed(self, _event: Optional[object] = None) -> None:
        if self.data.empty or not self.plate_var.get() or not self.measurement_var.get():
            self.wavelength_combo["values"] = ()
            self.wavelength_var.set("")
            self.wavelength_combo.configure(state="disabled")
            self.refresh_plate()
            return

        subset = self.data.loc[
            (self.data["plate_id"] == self.plate_var.get())
            & (self.data["measurement"] == self.measurement_var.get())
        ]
        wavelengths = sorted(
            pd.to_numeric(subset.get("wavelength_nm", pd.Series(dtype=float)), errors="coerce")
            .dropna()
            .unique()
            .tolist()
        )
        labels = [f"{float(value):g}" for value in wavelengths]
        self.wavelength_combo["values"] = labels
        if labels:
            self.wavelength_combo.configure(state="readonly")
            preferred = "508" if "508" in labels else labels[0]
            if self.wavelength_var.get() not in labels:
                self.wavelength_var.set(preferred)
        else:
            self.wavelength_var.set("")
            self.wavelength_combo.configure(state="disabled")
        self.refresh_plate()

    def current_subset(self) -> pd.DataFrame:
        if self.data.empty:
            return self.data
        subset = self.data.loc[
            (self.data["plate_id"] == self.plate_var.get())
            & (self.data["measurement"] == self.measurement_var.get())
        ].copy()
        if self.wavelength_var.get() and "wavelength_nm" in subset.columns:
            wavelengths = pd.to_numeric(subset["wavelength_nm"], errors="coerce")
            subset = subset.loc[np.isclose(wavelengths, float(self.wavelength_var.get()), equal_nan=False)].copy()
        return subset

    def refresh_plate(self) -> None:
        subset = self.current_subset()
        value_by_well = subset.groupby("well")["value"].mean().to_dict() if not subset.empty else {}
        self.plate_map.set_values(value_by_well)
        self.plate_map.set_available(value_by_well)
        self.refresh_plate_groups()
        for item in self.preview_tree.get_children():
            self.preview_tree.delete(item)
        group_by_well = self._group_by_well()
        for row in subset.sort_values(["row", "column"]).itertuples(index=False):
            self.preview_tree.insert(
                "",
                "end",
                values=(row.well, f"{row.value:.6g}", group_by_well.get(row.well, ""), row.source_file),
            )
        self.clear_selection()

    def _group_by_well(self) -> dict[str, str]:
        """Which group owns each well on the plate currently being shown."""
        plate_id = self.plate_var.get()
        owners: dict[str, str] = {}
        for name, assignment in self.assignments.items():
            if assignment.plate_id != plate_id:
                continue
            for well in assignment.wells:
                owners.setdefault(well, name)
        return owners

    def refresh_plate_groups(self) -> None:
        self.plate_map.set_groups(self._group_by_well(), list(self.assignments))

    def on_well_clicked(self, well: str) -> None:
        if self.block_select_var.get():
            self.select_block_from(well)
        else:
            self.toggle_well(well)

    def on_well_dragged(self, well: str) -> None:
        """Dragging paints extra wells onto the end of a free-form selection."""
        if self.block_select_var.get() or well in self.selected_wells:
            return
        self.toggle_well(well)

    def on_well_right_clicked(self, well: str) -> None:
        if well in self.selected_wells:
            self.selected_wells.remove(well)
            self._sync_selection()

    def on_well_hover(self, well: Optional[str]) -> None:
        self.hover_var.set(self.plate_map.describe(well) or "Hover a well to read its value.")

    def on_block_mode_changed(self) -> None:
        if self.block_select_var.get():
            self.status_var.set(
                f"Click any well to take it and the next {int(self.count_var.get()) - 1} wells in {self.order_var.get()} order."
            )
        else:
            self.status_var.set("Click wells one at a time, in increasing or decreasing concentration order.")

    def select_block_from(self, start_well: str) -> None:
        """Select ``start_well`` plus the following conditions in reading order.

        Groups that plated with gaps do not start where the map says they
        should, so the block follows the click rather than a fixed layout.
        """
        try:
            count = max(1, int(self.count_var.get()))
        except (tk.TclError, ValueError):
            count = 16
        wells = consecutive_wells(start_well, count, self.order_var.get(), clamp=True)
        self.selected_wells = list(wells)
        self.start_well_var.set(start_well)
        self.conc_count_var.set(len(wells))
        self._sync_selection()

        notes = []
        if len(wells) < count:
            notes.append(f"only {len(wells)} wells remain from {start_well}")
        available = set(self.plate_map.available)
        blank = [well for well in wells if well not in available]
        if blank:
            notes.append(f"no data in {', '.join(blank[:6])}{' ...' if len(blank) > 6 else ''}")
        owners = self._group_by_well()
        taken = sorted({owners[well] for well in wells if well in owners})
        if taken:
            notes.append(f"overlaps {', '.join(taken)}")
        message = f"Selected {len(wells)} wells from {start_well}"
        self.status_var.set(f"{message} — {'; '.join(notes)}" if notes else message)

    def toggle_well(self, well: str) -> None:
        if well in self.selected_wells:
            self.selected_wells.remove(well)
        else:
            self.selected_wells.append(well)
        self._sync_selection()

    def undo_last_well(self) -> None:
        if self.selected_wells:
            removed = self.selected_wells.pop()
            self._sync_selection()
            self.status_var.set(f"Removed {removed} from the selection.")

    def clear_selection(self) -> None:
        self.selected_wells.clear()
        self._sync_selection()

    def _sync_selection(self) -> None:
        self.plate_map.set_selected(self.selected_wells)
        self._update_selected_label()

    def _update_selected_label(self) -> None:
        self.selected_label.configure(text=", ".join(self.selected_wells) if self.selected_wells else "None")
        well_count = len(self.selected_wells)
        try:
            concentration_count = len(self._parse_concentrations())
        except ValueError:
            self.selection_summary_var.set(f"{well_count} wells  ·  concentration list is not numeric")
            self.selection_summary_label.configure(foreground=DANGER_TEXT)
            return
        if well_count == 0:
            self.selection_summary_var.set("No wells selected")
            self.selection_summary_label.configure(foreground=MUTED_TEXT)
        elif well_count == concentration_count:
            self.selection_summary_var.set(f"{well_count} wells  ·  {concentration_count} concentrations  ·  ready")
            self.selection_summary_label.configure(foreground=OK_TEXT)
        else:
            self.selection_summary_var.set(f"{well_count} wells  ·  {concentration_count} concentrations  ·  counts differ")
            self.selection_summary_label.configure(foreground=DANGER_TEXT)

    def select_consecutive(self) -> None:
        try:
            self.select_block_from(self.start_well_var.get())
        except Exception as exc:
            messagebox.showerror("Cannot select wells", str(exc))

    def generate_concentrations(self) -> None:
        try:
            count = int(self.conc_count_var.get())
            values = np.linspace(float(self.conc_start_var.get()), float(self.conc_stop_var.get()), count)
            self.concentration_var.set(", ".join(f"{value:.6g}" for value in values))
        except Exception as exc:
            messagebox.showerror("Cannot generate concentrations", str(exc))

    def _parse_concentrations(self) -> list[float]:
        tokens = [token.strip() for token in self.concentration_var.get().replace(";", ",").split(",") if token.strip()]
        return [float(token) for token in tokens]

    def add_group(self) -> None:
        try:
            if self.data.empty:
                raise ValueError("Load data first")
            assignment = GroupAssignment(
                name=self.group_name_var.get(),
                plate_id=self.plate_var.get(),
                wells=list(self.selected_wells),
                concentrations=self._parse_concentrations(),
                measurement=self.measurement_var.get(),
                wavelength_nm=float(self.wavelength_var.get()) if self.wavelength_var.get() else None,
            )
            build_group_dataframe(self.data, assignment)
            self.assignments[assignment.name] = assignment
            self.refresh_group_views()
            self.clear_selection()
            self.group_name_var.set(self._next_group_name(assignment.name))
            self.status_var.set(
                f"Assigned {len(assignment.wells)} conditions to {assignment.name}. "
                f"Next group is named {self.group_name_var.get()} — rename it if you like."
            )
        except Exception as exc:
            messagebox.showerror("Cannot add group", str(exc))

    def _next_group_name(self, current: str) -> str:
        """Suggest the following group name so adding many groups stays quick."""
        match = re.search(r"^(.*?)(\d+)(\D*)$", current.strip())
        candidate = current.strip()
        for _ in range(200):
            if match:
                prefix, number, suffix = match.group(1), int(match.group(2)), match.group(3)
                number += 1
                candidate = f"{prefix}{number}{suffix}"
                match = re.search(r"^(.*?)(\d+)(\D*)$", candidate)
            else:
                candidate = f"{candidate} 2"
                match = re.search(r"^(.*?)(\d+)(\D*)$", candidate)
            if candidate not in self.assignments:
                return candidate
        return ""

    def refresh_group_views(self) -> None:
        self.refresh_plate_groups()
        for item in self.group_tree.get_children():
            self.group_tree.delete(item)
        for name, assignment in self.assignments.items():
            self.group_tree.insert(
                "",
                "end",
                iid=name,
                values=(
                    name,
                    assignment.plate_id,
                    assignment.measurement,
                    f"{assignment.wavelength_nm:g}" if assignment.wavelength_nm is not None else "",
                    len(assignment.wells),
                    ", ".join(assignment.wells),
                ),
            )
        current_selection = [self.analysis_group_list.get(index) for index in self.analysis_group_list.curselection()]
        self.analysis_group_list.delete(0, tk.END)
        for name in self.assignments:
            self.analysis_group_list.insert(tk.END, name)
        for index, name in enumerate(self.assignments):
            if name in current_selection:
                self.analysis_group_list.selection_set(index)

        group_names = list(self.assignments)
        self.spectrum_group_combo["values"] = group_names
        if self.spectrum_group_var.get() not in group_names:
            self.spectrum_group_var.set(group_names[0] if group_names else "")

    def delete_group(self) -> None:
        selected = self.group_tree.selection()
        if not selected:
            messagebox.showinfo("No group selected", "Select a group in the table first.")
            return
        for name in selected:
            self.assignments.pop(name, None)
        self.refresh_group_views()
        self.status_var.set(f"Deleted {len(selected)} group(s): {', '.join(selected)}.")

    def edit_selected_group(self) -> None:
        """Load a saved group back into the editor so a bad assignment can be redone."""
        selected = self.group_tree.selection()
        if not selected:
            messagebox.showinfo("No group selected", "Select a group in the table first.")
            return
        name = selected[0]
        assignment = self.assignments.get(name)
        if assignment is None:
            return
        try:
            plates = list(self.plate_combo["values"])
            if assignment.plate_id in plates:
                self.plate_var.set(assignment.plate_id)
                self.on_plate_changed()
            measurements = list(self.measurement_combo["values"])
            if assignment.measurement in measurements:
                self.measurement_var.set(assignment.measurement)
                self.on_measurement_changed()
            if assignment.wavelength_nm is not None:
                label = f"{assignment.wavelength_nm:g}"
                if label in list(self.wavelength_combo["values"]):
                    self.wavelength_var.set(label)
                    self.refresh_plate()
            self.group_name_var.set(name)
            self.concentration_var.set(", ".join(f"{value:g}" for value in assignment.concentrations))
            self.conc_count_var.set(len(assignment.concentrations))
            self.selected_wells = list(assignment.wells)
            self.start_well_var.set(assignment.wells[0])
            self._sync_selection()
            self.status_var.set(
                f"Editing {name}. Change the wells or concentrations, then use Add or replace group to save it."
            )
        except Exception as exc:
            messagebox.showerror("Cannot edit group", str(exc))

    def export_tidy_data(self) -> None:
        if self.data.empty:
            messagebox.showinfo("Nothing to export", "Load data first.")
            return
        path = self.ask_save_file(defaultextension=".csv", filetypes=[("CSV", "*.csv")], initialfile="imported_plate_data_tidy.csv", title="Save tidy data")
        if path:
            self.data.to_csv(path, index=False)
            self.status_var.set(f"Saved tidy data to {path}")

    def export_groups(self) -> None:
        if not self.assignments:
            messagebox.showinfo("Nothing to export", "Assign at least one group first.")
            return
        directory = self.ask_directory("Choose output directory")
        if not directory:
            return
        try:
            paths = [export_group_csv(self.data, assignment, directory) for assignment in self.assignments.values()]
            self.status_var.set(f"Exported {len(paths)} group CSV files to {directory}")
        except Exception as exc:
            messagebox.showerror("Export failed", str(exc))

    def export_group_spectra(self) -> None:
        """Write one wavelength-by-concentration CSV per assigned group."""
        if not self.assignments:
            messagebox.showinfo("Nothing to export", "Assign at least one group first.")
            return
        directory = self.ask_directory("Choose output directory for the spectra CSVs")
        if not directory:
            return
        written: list[Path] = []
        failures: dict[str, str] = {}
        for name, assignment in self.assignments.items():
            try:
                written.append(export_group_spectrum_csv(self.data, assignment, directory))
            except Exception as exc:
                failures[name] = str(exc)
        self._report_export(written, failures, directory)

    def _report_export(self, written: list[Path], failures: dict[str, str], directory: str) -> None:
        summary = f"Wrote {len(written)} file(s) to {directory}"
        if failures:
            detail = "\n".join(f"{name}: {message}" for name, message in list(failures.items())[:12])
            if len(failures) > 12:
                detail += f"\n... and {len(failures) - 12} more"
            messagebox.showwarning("Some groups could not be exported", f"{summary}\n\n{detail}")
            self.status_var.set(f"{summary} — {len(failures)} group(s) failed.")
        else:
            self.status_var.set(summary)

    def _batch_concentrations(self, group_map_path: str) -> Optional[list[float]]:
        """Work out the concentration series a batch run should use.

        The group map wins if it carries its own column. Otherwise the list in
        the group panel is used when it is the right length, and failing that
        the Generate range is spread over however many wells a group has.
        """
        info = inspect_group_map(group_map_path)
        if not info["missing_concentrations"]:
            return []
        if not info["same_count"]:
            raise ValueError(
                "The groups in this map do not all have the same number of wells. "
                "Add a concentrations column to the map so each group carries its own series."
            )
        count = int(info["well_count"])
        try:
            current = self._parse_concentrations()
        except ValueError:
            current = []
        if len(current) == count:
            return current
        return [float(value) for value in np.linspace(float(self.conc_start_var.get()), float(self.conc_stop_var.get()), count)]

    def batch_export_wizard(self) -> None:
        """Plate files plus a group map in, one CSV set per group out."""
        plate_paths = self.ask_open_files("Step 1 of 3 — select every plate reader CSV")
        if not plate_paths:
            return
        group_map_path = self.ask_open_file("Step 2 of 3 — select the group map CSV")
        if not group_map_path:
            return
        try:
            concentrations = self._batch_concentrations(group_map_path)
        except Exception as exc:
            messagebox.showerror("Cannot read the group map", str(exc))
            return
        directory = self.ask_directory("Step 3 of 3 — choose the output folder")
        if not directory:
            return
        try:
            self.status_var.set(f"Exporting {len(plate_paths)} plate(s)...")
            self.update_idletasks()
            result = run_batch_export(
                plate_paths,
                group_map_path,
                directory,
                concentrations=concentrations,
                start=float(self.conc_start_var.get()),
                stop=float(self.conc_stop_var.get()),
            )
        except Exception as exc:
            messagebox.showerror("Batch export failed", str(exc))
            return
        self.adopt_batch_result(result)

    def adopt_batch_result(self, result) -> None:
        """Show what the batch wrote, and keep its data loaded for analysis."""
        self.data = result.data
        self.assignments = dict(result.assignments)
        plates = list(dict.fromkeys(self.data["plate_id"].astype(str)))
        self.plate_combo["values"] = plates
        if plates:
            self.plate_var.set(plates[0])
        if result.concentrations:
            self.concentration_var.set(", ".join(f"{value:g}" for value in result.concentrations))
        self.on_plate_changed()
        self.refresh_spectrum_controls()
        self.refresh_group_views()
        written = [*result.spectrum_files.values(), *result.curve_files.values()]
        self._report_export(written, result.failures, str(result.output_directory))
        if not result.failures:
            messagebox.showinfo(
                "Batch export finished",
                f"{result.summary()}\n\nThe plates and groups are loaded, so you can go straight to the "
                "analysis tab.",
            )

    def save_project(self) -> None:
        if not self.assignments:
            messagebox.showinfo("Nothing to save", "Assign at least one group first.")
            return
        path = self.ask_save_file(defaultextension=".json", filetypes=[("JSON", "*.json")], initialfile="folding_practical_mapping.json", title="Save group mapping")
        if not path:
            return
        payload = {name: asdict(assignment) for name, assignment in self.assignments.items()}
        Path(path).write_text(json.dumps(payload, indent=2), encoding="utf-8")
        self.status_var.set(f"Saved group mapping to {path}")

    def load_project(self) -> None:
        path = self.ask_open_file("Load group mapping", [("JSON", "*.json"), ("All files", "*.*")])
        if not path:
            return
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
            self.assignments = {name: GroupAssignment(**values) for name, values in payload.items()}
            self.refresh_group_views()
            self.status_var.set(f"Loaded {len(self.assignments)} group mappings.")
        except Exception as exc:
            messagebox.showerror("Cannot load mapping", str(exc))

    def refresh_spectrum_controls(self) -> None:
        if self.data.empty or "wavelength_nm" not in self.data.columns:
            self.spectrum_plate_combo["values"] = ()
            self.spectrum_plate_var.set("")
            self.spectrum_measurement_combo["values"] = ()
            self.spectrum_measurement_var.set("")
            self.refresh_spectrum_wells()
            return

        wavelengths = pd.to_numeric(self.data["wavelength_nm"], errors="coerce")
        spectral = self.data.loc[wavelengths.notna()]
        plates = list(dict.fromkeys(spectral["plate_id"].astype(str)))
        self.spectrum_plate_combo["values"] = plates
        if self.plate_var.get() in plates:
            self.spectrum_plate_var.set(self.plate_var.get())
        elif self.spectrum_plate_var.get() not in plates:
            self.spectrum_plate_var.set(plates[0] if plates else "")
        self.on_spectrum_plate_changed()

    def on_spectrum_plate_changed(self, _event: Optional[object] = None) -> None:
        if self.data.empty or not self.spectrum_plate_var.get():
            self.spectrum_measurement_combo["values"] = ()
            self.spectrum_measurement_var.set("")
            self.refresh_spectrum_wells()
            return
        wavelengths = pd.to_numeric(self.data.get("wavelength_nm"), errors="coerce")
        subset = self.data.loc[
            (self.data["plate_id"] == self.spectrum_plate_var.get()) & wavelengths.notna()
        ]
        measurements = list(dict.fromkeys(subset["measurement"].astype(str)))
        self.spectrum_measurement_combo["values"] = measurements
        if self.spectrum_measurement_var.get() not in measurements:
            self.spectrum_measurement_var.set(measurements[0] if measurements else "")
        self.refresh_spectrum_wells()

    def refresh_spectrum_wells(self) -> None:
        selected = set(self._selected_spectrum_wells()) if hasattr(self, "spectrum_well_list") else set()
        self.spectrum_well_list.delete(0, tk.END)
        if self.data.empty or not self.spectrum_plate_var.get() or not self.spectrum_measurement_var.get():
            return
        wavelengths = pd.to_numeric(self.data.get("wavelength_nm"), errors="coerce")
        subset = self.data.loc[
            (self.data["plate_id"] == self.spectrum_plate_var.get())
            & (self.data["measurement"] == self.spectrum_measurement_var.get())
            & wavelengths.notna()
        ]
        wells = sorted(subset["well"].astype(str).unique().tolist(), key=well_sort_key)
        for index, well in enumerate(wells):
            self.spectrum_well_list.insert(tk.END, well)
            if well in selected:
                self.spectrum_well_list.selection_set(index)

    def _selected_spectrum_wells(self) -> list[str]:
        return [self.spectrum_well_list.get(index) for index in self.spectrum_well_list.curselection()]

    def select_spectrum_wells_from_spec(self) -> None:
        try:
            requested = expand_well_spec(self.spectrum_well_spec_var.get())
            available = [self.spectrum_well_list.get(index) for index in range(self.spectrum_well_list.size())]
            missing = [well for well in requested if well not in available]
            if missing:
                raise ValueError(f"No spectrum is available for: {', '.join(missing)}")
            self.spectrum_well_list.selection_clear(0, tk.END)
            positions = {well: index for index, well in enumerate(available)}
            for well in requested:
                self.spectrum_well_list.selection_set(positions[well])
                self.spectrum_well_list.see(positions[well])
        except Exception as exc:
            messagebox.showerror("Cannot select spectrum wells", str(exc))

    def use_plate_map_selection_for_spectra(self) -> None:
        if not self.selected_wells:
            messagebox.showinfo("No plate-map wells selected", "Select wells on the plate map first.")
            return
        self.spectrum_plate_var.set(self.plate_var.get())
        self.on_spectrum_plate_changed()
        if self.measurement_var.get() in self.spectrum_measurement_combo["values"]:
            self.spectrum_measurement_var.set(self.measurement_var.get())
            self.refresh_spectrum_wells()
        self.spectrum_well_spec_var.set(", ".join(self.selected_wells))
        self.select_spectrum_wells_from_spec()

    def use_group_for_spectra(self) -> None:
        group_name = self.spectrum_group_var.get()
        if not group_name or group_name not in self.assignments:
            messagebox.showinfo("No group selected", "Select a practical group first.")
            return
        try:
            assignment = self.assignments[group_name]
            available_plates = list(self.spectrum_plate_combo["values"])
            if assignment.plate_id not in available_plates:
                raise ValueError(f"No wavelength scan is loaded for plate {assignment.plate_id!r}")
            self.spectrum_plate_var.set(assignment.plate_id)
            self.on_spectrum_plate_changed()
            available_measurements = list(self.spectrum_measurement_combo["values"])
            if assignment.measurement in available_measurements:
                self.spectrum_measurement_var.set(assignment.measurement)
                self.refresh_spectrum_wells()
            self.spectrum_well_spec_var.set(", ".join(assignment.wells))
            self.select_spectrum_wells_from_spec()
            self.status_var.set(f"Selected all {len(assignment.wells)} wells from {group_name}.")
        except Exception as exc:
            messagebox.showerror("Cannot select group spectra", str(exc))

    def select_all_spectrum_wells(self) -> None:
        self.spectrum_well_list.selection_set(0, tk.END)

    def clear_spectrum_wells(self) -> None:
        self.spectrum_well_list.selection_clear(0, tk.END)

    def _current_spectrum_dataframe(self) -> pd.DataFrame:
        wells = self._selected_spectrum_wells()
        if not wells:
            raise ValueError("Select one or more wells")
        return build_spectrum_dataframe(
            self.data,
            plate_id=self.spectrum_plate_var.get(),
            measurement=self.spectrum_measurement_var.get(),
            wells=wells,
        )

    def plot_spectra(self) -> None:
        try:
            spectrum = self._current_spectrum_dataframe()
            y_column = (
                "raw fluorescence values"
                if self.spectrum_signal_mode_var.get() == "Raw fluorescence"
                else "peak-normalized fluorescence values"
            )
            self.spectrum_axes.clear()
            for well, well_data in spectrum.groupby("well", sort=False):
                self.spectrum_axes.plot(
                    well_data["wavelength_nm"],
                    well_data[y_column],
                    marker="o",
                    markersize=3,
                    label=str(well),
                )
            self.spectrum_axes.set_xlabel("Emission wavelength (nm)")
            self.spectrum_axes.set_ylabel(y_column)
            self.spectrum_axes.set_title(
                f"{self.spectrum_measurement_var.get()} — {self.spectrum_plate_var.get()}"
            )
            self.spectrum_axes.grid(True, alpha=0.25)
            self.spectrum_axes.legend(fontsize="small", ncol=2)
            self.spectrum_canvas.draw()
            self.status_var.set(f"Plotted spectra for {spectrum['well'].nunique()} well(s).")
        except Exception as exc:
            messagebox.showerror("Cannot plot spectra", str(exc))

    def save_spectrum_graph(self) -> None:
        path = self.ask_save_file(
            defaultextension=".png",
            filetypes=[("PNG", "*.png"), ("PDF", "*.pdf"), ("SVG", "*.svg")],
            initialfile="well_spectra.png",
            title="Save spectrum graph",
        )
        if path:
            self.spectrum_figure.savefig(path, dpi=300, bbox_inches="tight")
            self.status_var.set(f"Saved spectrum graph to {path}")

    def export_selected_spectra(self) -> None:
        try:
            spectrum = self._current_spectrum_dataframe()
            path = self.ask_save_file(
                defaultextension=".csv",
                filetypes=[("CSV", "*.csv")],
                initialfile="selected_well_spectra.csv",
                title="Save selected spectra",
            )
            if path:
                spectrum.to_csv(path, index=False)
                self.status_var.set(f"Saved selected spectra to {path}")
        except Exception as exc:
            messagebox.showerror("Cannot export spectra", str(exc))

    def select_all_analysis_groups(self) -> None:
        self.analysis_group_list.selection_set(0, tk.END)

    def _selected_group_names(self) -> list[str]:
        return [self.analysis_group_list.get(index) for index in self.analysis_group_list.curselection()]

    def _fit_models(self, x: np.ndarray, y: np.ndarray) -> list:
        mode = self.fit_mode_var.get()
        results = []
        if mode in {"Auto compare", "Two-state thermodynamic", "Fit both"}:
            results.append(fit_two_state_denaturation(x, y, temperature_k=float(self.temperature_var.get())))
        if mode in {"Auto compare", "4PL logistic", "Fit both"}:
            results.append(fit_four_parameter_logistic(x, y))
        return results

    def plot_and_fit(self) -> None:
        selected_names = self._selected_group_names()
        if not selected_names:
            messagebox.showinfo("No groups selected", "Select one or more groups to plot.")
            return

        self.axes.clear()
        for item in self.report_tree.get_children():
            self.report_tree.delete(item)
        self.last_fit_rows = []
        signal_column = "raw fluorescence values" if self.signal_mode_var.get() == "Raw fluorescence" else "normalized fluorescence values"

        for group_name in selected_names:
            assignment = self.assignments[group_name]
            try:
                group_data = build_group_dataframe(self.data, assignment).sort_values("GuHCl concentration (M)")
                x = group_data["GuHCl concentration (M)"].to_numpy(dtype=float)
                y = group_data[signal_column].to_numpy(dtype=float)
                point_line = self.axes.plot(x, y, marker="o", linestyle="none", label=f"{group_name} data")[0]
                group_color = point_line.get_color()
                results = self._fit_models(x, y)
                best = choose_best_fit(results)
                grid = np.linspace(float(np.min(x)), float(np.max(x)), 300)

                for result in results:
                    is_best = best is result
                    if result.success:
                        linestyle = "-" if is_best else "--"
                        self.axes.plot(
                            grid,
                            result.predict(grid),
                            linestyle=linestyle,
                            color=group_color,
                            alpha=1.0 if is_best else 0.65,
                            label=f"{group_name}: {result.model_name}{' (preferred statistical fit)' if is_best and len(results) > 1 else ''}",
                        )
                    row = {
                        "group": group_name,
                        "model": result.model_name,
                        "best": bool(is_best),
                        "success": result.success,
                        "interpretation_status": result.interpretation_status,
                        "warnings": "; ".join(result.warnings),
                        "diagnostics": result.diagnostics,
                        "message": result.message,
                        **result.parameters,
                        **{f"se_{key}": value for key, value in result.standard_errors.items()},
                        **result.metrics,
                    }
                    self.last_fit_rows.append(row)
                    self._insert_report_row(row)
            except Exception as exc:
                row = {"group": group_name, "model": "Not fitted", "best": False, "success": False, "message": str(exc)}
                self.last_fit_rows.append(row)
                self._insert_report_row(row)

        self.axes.set_xlabel("GuHCl concentration (M)")
        self.axes.set_ylabel(signal_column)
        self.axes.set_title("GFP chemical denaturation")
        self.axes.grid(True, alpha=0.25)
        self.axes.legend(fontsize="small", ncol=1)
        self.canvas.draw()
        self.status_var.set(f"Fitted {len(selected_names)} group(s). Compare AICc only when both models converged.")

    def _insert_report_row(self, row: dict[str, object]) -> None:
        def format_number(key: str, digits: int = 4) -> str:
            value = row.get(key)
            return f"{float(value):.{digits}g}" if value is not None and np.isfinite(float(value)) else ""

        self.report_tree.insert(
            "",
            "end",
            values=(
                row.get("group", ""),
                row.get("model", ""),
                "Yes" if row.get("best") else "",
                format_number("delta_g_h2o_kj_mol"),
                format_number("delta_g_folding_h2o_kj_mol"),
                format_number("m_value_kj_mol_m"),
                format_number("cm_m"),
                format_number("rmse"),
                format_number("r_squared"),
                format_number("aicc"),
                row.get("interpretation_status", "calculation completed") if row.get("success") else row.get("message", "Failed"),
            ),
        )

    def save_graph(self) -> None:
        path = self.ask_save_file(defaultextension=".png", filetypes=[("PNG", "*.png"), ("PDF", "*.pdf"), ("SVG", "*.svg")], initialfile="folding_curves.png", title="Save graph")
        if path:
            self.figure.savefig(path, dpi=300, bbox_inches="tight")
            self.status_var.set(f"Saved graph to {path}")

    def export_fit_report(self) -> None:
        if not self.last_fit_rows:
            messagebox.showinfo("No fit report", "Run the fitting panel first.")
            return
        path = self.ask_save_file(defaultextension=".csv", filetypes=[("CSV", "*.csv")], initialfile="folding_fit_report.csv", title="Save fit report")
        if path:
            pd.DataFrame(self.last_fit_rows).to_csv(path, index=False)
            self.status_var.set(f"Saved fit report to {path}")

    def export_detailed_report(self) -> None:
        if not self.last_fit_rows:
            messagebox.showinfo("No fit report", "Run the fitting panel first.")
            return
        path = self.ask_save_file(defaultextension=".txt", filetypes=[("Text", "*.txt")], initialfile="folding_fit_report.txt", title="Save detailed report")
        if not path:
            return
        lines = [
            "Protein Folding Practical — Fit Report",
            "",
            "Thermodynamic model: ΔG_unfold([D]) = ΔG°H2O - m[D]",
            "Cm = ΔG°H2O / m",
            "The 4PL logistic model is descriptive and does not independently establish a folding free energy.",
            "AICc comparisons are meaningful only for fits to the same observations and response variable.",
            "Equilibrium, reversibility and two-state behaviour require experimental evidence.",
            "Calculation success is separate from scientific interpretation status; inspect all warnings.",
            "AIC/AICc/BIC assume independent Gaussian errors and count estimated residual variance as a parameter.",
            "",
        ]
        for row in self.last_fit_rows:
            lines.append(f"Group: {row.get('group')}")
            lines.append(f"Model: {row.get('model')}")
            lines.append(f"Preferred statistical fit among models compared: {'yes' if row.get('best') else 'no'}")
            lines.append(f"Status: {'success' if row.get('success') else row.get('message', 'failed')}")
            lines.append(f"Interpretation: {row.get('interpretation_status', 'unavailable')}")
            lines.append(f"Warnings: {row.get('warnings', '')}")
            lines.append(f"Diagnostics: {row.get('diagnostics', {})}")
            for key in ("delta_g_h2o_kj_mol", "delta_g_folding_h2o_kj_mol", "m_value_kj_mol_m", "cm_m", "rmse", "r_squared", "aicc", "bic"):
                if key in row:
                    lines.append(f"{key}: {row[key]}")
            lines.append("")
        Path(path).write_text("\n".join(lines), encoding="utf-8")
        self.status_var.set(f"Saved detailed report to {path}")


def main() -> None:
    app = FoldingPracticalApp()
    app.mainloop()


if __name__ == "__main__":
    main()
