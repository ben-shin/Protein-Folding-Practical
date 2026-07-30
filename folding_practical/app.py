"""Tkinter desktop application for the protein-folding practical."""

from __future__ import annotations

import json
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

from .models import choose_best_fit, fit_four_parameter_logistic, fit_two_state_denaturation
from .plate_io import load_plate_csvs
from .project import (
    GroupAssignment,
    build_group_dataframe,
    build_spectrum_dataframe,
    export_group_csv,
    load_group_map_assignments,
)
from .wells import PLATE_ROWS, consecutive_wells, expand_well_spec, well_sort_key


class FoldingPracticalApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Protein Folding Practical")
        self.geometry("1450x900")
        self.minsize(1100, 700)

        self.data = pd.DataFrame()
        self.assignments: dict[str, GroupAssignment] = {}
        self.selected_wells: list[str] = []
        self.well_buttons: dict[str, ttk.Button] = {}
        self.last_fit_rows: list[dict[str, object]] = []

        self.status_var = tk.StringVar(value="Load one or more CLARIOstar CSV files to begin.")
        self.plate_var = tk.StringVar()
        self.measurement_var = tk.StringVar()
        self.wavelength_var = tk.StringVar()
        self.group_name_var = tk.StringVar()
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
        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=8, pady=8)

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

    def _build_data_tab(self) -> None:
        self.data_tab.columnconfigure(0, weight=3)
        self.data_tab.columnconfigure(1, weight=2)
        self.data_tab.rowconfigure(1, weight=1)

        import_bar = ttk.Frame(self.data_tab)
        import_bar.grid(row=0, column=0, columnspan=2, sticky="ew", padx=6, pady=6)
        ttk.Button(import_bar, text="Load CSV files", command=self.load_files).pack(side="left")
        ttk.Button(import_bar, text="Load group map CSV", command=self.load_group_map).pack(side="left", padx=6)
        ttk.Button(import_bar, text="Export tidy data", command=self.export_tidy_data).pack(side="left")
        ttk.Label(import_bar, text="Plate:").pack(side="left", padx=(18, 4))
        self.plate_combo = ttk.Combobox(import_bar, textvariable=self.plate_var, state="readonly", width=24)
        self.plate_combo.pack(side="left")
        self.plate_combo.bind("<<ComboboxSelected>>", self.on_plate_changed)
        ttk.Label(import_bar, text="Signal:").pack(side="left", padx=(18, 4))
        self.measurement_combo = ttk.Combobox(import_bar, textvariable=self.measurement_var, state="readonly", width=34)
        self.measurement_combo.pack(side="left")
        self.measurement_combo.bind("<<ComboboxSelected>>", self.on_measurement_changed)
        ttk.Label(import_bar, text="Wavelength:").pack(side="left", padx=(18, 4))
        self.wavelength_combo = ttk.Combobox(import_bar, textvariable=self.wavelength_var, state="disabled", width=11)
        self.wavelength_combo.pack(side="left")
        self.wavelength_combo.bind("<<ComboboxSelected>>", lambda _event: self.refresh_plate())

        left = ttk.Frame(self.data_tab)
        left.grid(row=1, column=0, sticky="nsew", padx=(6, 3), pady=6)
        left.columnconfigure(0, weight=1)
        left.rowconfigure(1, weight=1)

        ttk.Label(left, text="Plate map — click wells in concentration order").grid(row=0, column=0, sticky="w")
        plate_frame = ttk.Frame(left)
        plate_frame.grid(row=1, column=0, sticky="nsew", pady=(4, 8))
        for column in range(12):
            ttk.Label(plate_frame, text=str(column + 1), anchor="center").grid(row=0, column=column + 1, padx=1, pady=1)
        for row_index, row_letter in enumerate(PLATE_ROWS, start=1):
            ttk.Label(plate_frame, text=row_letter, anchor="center").grid(row=row_index, column=0, padx=3)
            for column in range(1, 13):
                well = f"{row_letter}{column}"
                button = ttk.Button(plate_frame, text=well, width=5, command=lambda current=well: self.toggle_well(current))
                button.grid(row=row_index, column=column, padx=1, pady=2, sticky="nsew")
                self.well_buttons[well] = button

        preview_frame = ttk.LabelFrame(left, text="Imported values for current plate and signal")
        preview_frame.grid(row=2, column=0, sticky="nsew")
        preview_frame.columnconfigure(0, weight=1)
        preview_frame.rowconfigure(0, weight=1)
        self.preview_tree = ttk.Treeview(preview_frame, columns=("well", "value", "source"), show="headings", height=10)
        self.preview_tree.heading("well", text="Well")
        self.preview_tree.heading("value", text="Value")
        self.preview_tree.heading("source", text="Source file")
        self.preview_tree.column("well", width=70, anchor="center")
        self.preview_tree.column("value", width=140, anchor="e")
        self.preview_tree.column("source", width=220)
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
        self.selected_label = ttk.Label(selected_frame, text="None", wraplength=500, justify="left")
        self.selected_label.grid(row=0, column=0, sticky="ew", padx=6, pady=6)
        ttk.Button(selected_frame, text="Clear selection", command=self.clear_selection).grid(row=0, column=1, padx=6, pady=6)

        helper = ttk.LabelFrame(right, text="Consecutive-well helper")
        helper.grid(row=1, column=0, sticky="ew", pady=6)
        ttk.Label(helper, text="Start well").grid(row=0, column=0, padx=4, pady=4)
        ttk.Entry(helper, textvariable=self.start_well_var, width=8).grid(row=0, column=1, padx=4, pady=4)
        ttk.Label(helper, text="Conditions").grid(row=0, column=2, padx=4, pady=4)
        ttk.Spinbox(helper, from_=3, to=96, textvariable=self.count_var, width=7).grid(row=0, column=3, padx=4, pady=4)
        ttk.Label(helper, text="Order").grid(row=0, column=4, padx=4, pady=4)
        ttk.Combobox(helper, textvariable=self.order_var, values=("row-major", "column-major"), state="readonly", width=13).grid(row=0, column=5, padx=4, pady=4)
        ttk.Button(helper, text="Select", command=self.select_consecutive).grid(row=0, column=6, padx=4, pady=4)

        concentration_frame = ttk.LabelFrame(right, text="Group definition")
        concentration_frame.grid(row=2, column=0, sticky="ew", pady=6)
        concentration_frame.columnconfigure(1, weight=1)
        ttk.Label(concentration_frame, text="Group name").grid(row=0, column=0, sticky="w", padx=5, pady=4)
        ttk.Entry(concentration_frame, textvariable=self.group_name_var).grid(row=0, column=1, columnspan=5, sticky="ew", padx=5, pady=4)
        ttk.Label(concentration_frame, text="GuHCl concentrations (M)").grid(row=1, column=0, sticky="nw", padx=5, pady=4)
        concentration_entry = ttk.Entry(concentration_frame, textvariable=self.concentration_var)
        concentration_entry.grid(row=1, column=1, columnspan=5, sticky="ew", padx=5, pady=4)
        ttk.Label(concentration_frame, text="Generate:").grid(row=2, column=0, sticky="w", padx=5, pady=4)
        ttk.Entry(concentration_frame, textvariable=self.conc_start_var, width=8).grid(row=2, column=1, padx=3, pady=4)
        ttk.Label(concentration_frame, text="to").grid(row=2, column=2, padx=3)
        ttk.Entry(concentration_frame, textvariable=self.conc_stop_var, width=8).grid(row=2, column=3, padx=3, pady=4)
        ttk.Spinbox(concentration_frame, from_=3, to=96, textvariable=self.conc_count_var, width=7).grid(row=2, column=4, padx=3, pady=4)
        ttk.Button(concentration_frame, text="Generate list", command=self.generate_concentrations).grid(row=2, column=5, padx=5, pady=4)
        ttk.Button(concentration_frame, text="Add or replace group", command=self.add_group).grid(row=3, column=0, columnspan=6, sticky="ew", padx=5, pady=6)

        action_frame = ttk.Frame(right)
        action_frame.grid(row=3, column=0, sticky="ew", pady=6)
        ttk.Button(action_frame, text="Delete selected group", command=self.delete_group).pack(side="left")
        ttk.Button(action_frame, text="Export all group CSVs", command=self.export_groups).pack(side="left", padx=6)
        ttk.Button(action_frame, text="Save project mapping", command=self.save_project).pack(side="left")
        ttk.Button(action_frame, text="Load project mapping", command=self.load_project).pack(side="left", padx=6)

        groups_frame = ttk.LabelFrame(right, text="Assigned practical groups")
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
            ("wavelength", "λ (nm)", 70),
            ("count", "N", 45),
            ("wells", "Wells", 260),
        ):
            self.group_tree.heading(column, text=title)
            self.group_tree.column(column, width=width)
        self.group_tree.grid(row=0, column=0, sticky="nsew")
        group_scroll = ttk.Scrollbar(groups_frame, orient="vertical", command=self.group_tree.yview)
        group_scroll.grid(row=0, column=1, sticky="ns")
        self.group_tree.configure(yscrollcommand=group_scroll.set)

    def _build_analysis_tab(self) -> None:
        self.analysis_tab.columnconfigure(1, weight=1)
        self.analysis_tab.rowconfigure(0, weight=1)

        controls = ttk.Frame(self.analysis_tab)
        controls.grid(row=0, column=0, sticky="ns", padx=6, pady=6)
        ttk.Label(controls, text="Groups (Ctrl/Shift for multiple)").pack(anchor="w")
        self.analysis_group_list = tk.Listbox(controls, selectmode=tk.EXTENDED, exportselection=False, width=34, height=18)
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
            "best": "Best?",
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

        controls = ttk.Frame(self.spectrum_tab)
        controls.grid(row=0, column=0, sticky="ns", padx=6, pady=6)

        ttk.Label(controls, text="Plate").pack(anchor="w")
        self.spectrum_plate_combo = ttk.Combobox(
            controls,
            textvariable=self.spectrum_plate_var,
            state="readonly",
            width=32,
        )
        self.spectrum_plate_combo.pack(fill="x", pady=(4, 10))
        self.spectrum_plate_combo.bind("<<ComboboxSelected>>", self.on_spectrum_plate_changed)

        ttk.Label(controls, text="Spectrum readout").pack(anchor="w")
        self.spectrum_measurement_combo = ttk.Combobox(
            controls,
            textvariable=self.spectrum_measurement_var,
            state="readonly",
            width=32,
        )
        self.spectrum_measurement_combo.pack(fill="x", pady=(4, 10))
        self.spectrum_measurement_combo.bind("<<ComboboxSelected>>", lambda _event: self.refresh_spectrum_wells())

        ttk.Label(controls, text="Practical group").pack(anchor="w")
        self.spectrum_group_combo = ttk.Combobox(
            controls,
            textvariable=self.spectrum_group_var,
            state="readonly",
            width=32,
        )
        self.spectrum_group_combo.pack(fill="x", pady=(4, 4))
        ttk.Button(controls, text="Select entire group", command=self.use_group_for_spectra).pack(fill="x", pady=(0, 10))

        ttk.Label(controls, text="Wells (for example A1-A4, B2)").pack(anchor="w")
        ttk.Entry(controls, textvariable=self.spectrum_well_spec_var, width=32).pack(fill="x", pady=(4, 4))
        ttk.Button(controls, text="Select wells from entry", command=self.select_spectrum_wells_from_spec).pack(fill="x", pady=2)
        ttk.Button(controls, text="Use plate-map selection", command=self.use_plate_map_selection_for_spectra).pack(fill="x", pady=2)
        ttk.Button(controls, text="Select all available wells", command=self.select_all_spectrum_wells).pack(fill="x", pady=2)
        ttk.Button(controls, text="Clear spectrum selection", command=self.clear_spectrum_wells).pack(fill="x", pady=(2, 8))

        ttk.Label(controls, text="Available wells (Ctrl/Shift for multiple)").pack(anchor="w")
        well_frame = ttk.Frame(controls)
        well_frame.pack(fill="both", expand=True, pady=(4, 10))
        self.spectrum_well_list = tk.Listbox(
            well_frame,
            selectmode=tk.EXTENDED,
            exportselection=False,
            width=32,
            height=20,
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
        ).pack(fill="x", pady=(4, 10))
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

    def load_files(self) -> None:
        paths = filedialog.askopenfilenames(title="Select CLARIOstar CSV files", filetypes=[("CSV files", "*.csv"), ("All files", "*.*")])
        if not paths:
            return
        try:
            imported = load_plate_csvs(paths)
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
        path = filedialog.askopenfilename(
            title="Select group map CSV",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            wavelength = float(self.wavelength_var.get()) if self.wavelength_var.get() else None
            imported = load_group_map_assignments(
                self.data,
                path,
                default_concentrations=self._parse_concentrations(),
                default_measurement=self.measurement_var.get(),
                default_wavelength_nm=wavelength,
                existing_assignments=self.assignments,
            )
            self.assignments.update(imported)
            self.refresh_group_views()
            self.status_var.set(f"Loaded {len(imported)} practical group(s) from {Path(path).name}.")
        except Exception as exc:
            messagebox.showerror("Cannot load group map", str(exc))

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
        for well, button in self.well_buttons.items():
            button.state(["!disabled"] if well in value_by_well else ["disabled"])
            self._refresh_well_button(well)
        for item in self.preview_tree.get_children():
            self.preview_tree.delete(item)
        for row in subset.sort_values(["row", "column"]).itertuples(index=False):
            self.preview_tree.insert("", "end", values=(row.well, f"{row.value:.6g}", row.source_file))
        self.clear_selection()

    def _refresh_well_button(self, well: str) -> None:
        button = self.well_buttons[well]
        if well in self.selected_wells:
            button.state(["pressed"])
        else:
            button.state(["!pressed"])

    def toggle_well(self, well: str) -> None:
        if well in self.selected_wells:
            self.selected_wells.remove(well)
        else:
            self.selected_wells.append(well)
        self._refresh_well_button(well)
        self._update_selected_label()

    def clear_selection(self) -> None:
        prior = list(self.selected_wells)
        self.selected_wells.clear()
        for well in prior:
            self._refresh_well_button(well)
        self._update_selected_label()

    def _update_selected_label(self) -> None:
        self.selected_label.configure(text=", ".join(self.selected_wells) if self.selected_wells else "None")

    def select_consecutive(self) -> None:
        try:
            wells = consecutive_wells(self.start_well_var.get(), int(self.count_var.get()), self.order_var.get())
            available = set(self.current_subset()["well"].astype(str))
            missing = [well for well in wells if well not in available]
            if missing:
                raise ValueError(f"Current plate/signal has no data for: {', '.join(missing)}")
            self.clear_selection()
            self.selected_wells.extend(wells)
            for well in wells:
                self._refresh_well_button(well)
            self._update_selected_label()
            self.conc_count_var.set(len(wells))
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
            self.status_var.set(f"Assigned {len(assignment.wells)} conditions to {assignment.name}.")
        except Exception as exc:
            messagebox.showerror("Cannot add group", str(exc))

    def refresh_group_views(self) -> None:
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
            return
        for name in selected:
            self.assignments.pop(name, None)
        self.refresh_group_views()

    def export_tidy_data(self) -> None:
        if self.data.empty:
            messagebox.showinfo("Nothing to export", "Load data first.")
            return
        path = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV", "*.csv")], initialfile="imported_plate_data_tidy.csv")
        if path:
            self.data.to_csv(path, index=False)
            self.status_var.set(f"Saved tidy data to {path}")

    def export_groups(self) -> None:
        if not self.assignments:
            messagebox.showinfo("Nothing to export", "Assign at least one group first.")
            return
        directory = filedialog.askdirectory(title="Choose output directory")
        if not directory:
            return
        try:
            paths = [export_group_csv(self.data, assignment, directory) for assignment in self.assignments.values()]
            self.status_var.set(f"Exported {len(paths)} group CSV files to {directory}")
        except Exception as exc:
            messagebox.showerror("Export failed", str(exc))

    def save_project(self) -> None:
        if not self.assignments:
            messagebox.showinfo("Nothing to save", "Assign at least one group first.")
            return
        path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON", "*.json")], initialfile="folding_practical_mapping.json")
        if not path:
            return
        payload = {name: asdict(assignment) for name, assignment in self.assignments.items()}
        Path(path).write_text(json.dumps(payload, indent=2), encoding="utf-8")
        self.status_var.set(f"Saved group mapping to {path}")

    def load_project(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("JSON", "*.json"), ("All files", "*.*")])
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
        path = filedialog.asksaveasfilename(
            defaultextension=".png",
            filetypes=[("PNG", "*.png"), ("PDF", "*.pdf"), ("SVG", "*.svg")],
            initialfile="well_spectra.png",
        )
        if path:
            self.spectrum_figure.savefig(path, dpi=300, bbox_inches="tight")
            self.status_var.set(f"Saved spectrum graph to {path}")

    def export_selected_spectra(self) -> None:
        try:
            spectrum = self._current_spectrum_dataframe()
            path = filedialog.asksaveasfilename(
                defaultextension=".csv",
                filetypes=[("CSV", "*.csv")],
                initialfile="selected_well_spectra.csv",
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
                            label=f"{group_name}: {result.model_name}{' (best)' if is_best and len(results) > 1 else ''}",
                        )
                    row = {
                        "group": group_name,
                        "model": result.model_name,
                        "best": bool(is_best),
                        "success": result.success,
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
                "OK" if row.get("success") else row.get("message", "Failed"),
            ),
        )

    def save_graph(self) -> None:
        path = filedialog.asksaveasfilename(defaultextension=".png", filetypes=[("PNG", "*.png"), ("PDF", "*.pdf"), ("SVG", "*.svg")], initialfile="folding_curves.png")
        if path:
            self.figure.savefig(path, dpi=300, bbox_inches="tight")
            self.status_var.set(f"Saved graph to {path}")

    def export_fit_report(self) -> None:
        if not self.last_fit_rows:
            messagebox.showinfo("No fit report", "Run the fitting panel first.")
            return
        path = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV", "*.csv")], initialfile="folding_fit_report.csv")
        if path:
            pd.DataFrame(self.last_fit_rows).to_csv(path, index=False)
            self.status_var.set(f"Saved fit report to {path}")

    def export_detailed_report(self) -> None:
        if not self.last_fit_rows:
            messagebox.showinfo("No fit report", "Run the fitting panel first.")
            return
        path = filedialog.asksaveasfilename(defaultextension=".txt", filetypes=[("Text", "*.txt")], initialfile="folding_fit_report.txt")
        if not path:
            return
        lines = [
            "Protein Folding Practical — Fit Report",
            "",
            "Thermodynamic model: ΔG_unfold([D]) = ΔG°H2O - m[D]",
            "Cm = ΔG°H2O / m",
            "The 4PL logistic model is descriptive and does not independently establish a folding free energy.",
            "AICc comparisons are meaningful only for fits to the same observations and response variable.",
            "",
        ]
        for row in self.last_fit_rows:
            lines.append(f"Group: {row.get('group')}")
            lines.append(f"Model: {row.get('model')}")
            lines.append(f"Best among fitted models: {'yes' if row.get('best') else 'no'}")
            lines.append(f"Status: {'success' if row.get('success') else row.get('message', 'failed')}")
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
