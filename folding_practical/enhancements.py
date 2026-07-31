"""Visual, plotting, and performance upgrades for the desktop app."""

from __future__ import annotations

import csv
import math
import os
import re
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Optional
import tkinter as tk
from tkinter import colorchooser, filedialog, font as tkfont, messagebox, ttk


BACKGROUND = "#edf2f8"
SURFACE = "#ffffff"
HEADER_START = "#071426"
HEADER_END = "#1d2f5f"
TEXT = "#152033"
MUTED = "#667085"
ACCENT = "#6957f5"
ACCENT_DARK = "#5040d6"
ACCENT_CYAN = "#24c8d8"
BORDER = "#d7e0ec"
DANGER = "#b42318"
DARK_PLOT = "#101828"

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

LINE_STYLES = {
    "Solid": "-",
    "Dashed": "--",
    "Dash-dot": "-.",
    "Dotted": ":",
    "None": "none",
}

MARKERS = {
    "Auto": "auto",
    "None": None,
    "Circle": "o",
    "Square": "s",
    "Triangle": "^",
    "Diamond": "D",
    "Plus": "+",
    "Cross": "x",
}

LEGEND_LOCATIONS = (
    "Auto",
    "Off",
    "Best",
    "Upper right",
    "Upper left",
    "Lower left",
    "Lower right",
    "Center left",
    "Center right",
    "Upper center",
    "Lower center",
    "Center",
    "Outside right",
    "Below plot",
)

PALETTES = (
    "Modern",
    "Ocean",
    "Sunset",
    "Viridis",
    "Plasma",
    "Tab10",
    "Grayscale",
)

MODERN_COLORS = [
    "#6957f5",
    "#24a7d8",
    "#10a881",
    "#f28c28",
    "#e34f7a",
    "#8d62d9",
    "#3a7bd5",
    "#cf6b32",
]
OCEAN_COLORS = ["#123b7a", "#146c94", "#19a7ce", "#5bd1d7", "#2a9d8f", "#4361ee"]
SUNSET_COLORS = ["#7b2cbf", "#c44585", "#e85d75", "#f28482", "#f6bd60", "#ee6c4d"]

_GROUP_MAP_ALIASES = {
    "wells": {"wells", "wellrange", "wellranges", "wellspec", "wellspecification"},
    "concentrations": {
        "concentrations",
        "guhcl",
        "guhclconcentrations",
        "guhclconcentrationsm",
    },
}


class _SafeFormat(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def _walk_widgets(widget: tk.Misc):
    for child in widget.winfo_children():
        yield child
        yield from _walk_widgets(child)


def _find_button(app: tk.Misc, text: str):
    for widget in _walk_widgets(app):
        if isinstance(widget, ttk.Button) and str(widget.cget("text")) == text:
            return widget
    return None


def _normalize_label(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value).strip().lower())


def _find_alias(columns: list[str], field: str):
    aliases = _GROUP_MAP_ALIASES[field]
    for column in columns:
        if _normalize_label(column) in aliases:
            return column
    return None


def _parse_concentration_order(text: str, expected_count: int) -> list[float]:
    tokens = [token for token in re.split(r"[,;|\s]+", text.strip()) if token]
    values = [float(token) for token in tokens]
    if len(values) != expected_count:
        raise ValueError(f"Enter exactly {expected_count} concentrations")
    if not all(math.isfinite(value) for value in values):
        raise ValueError("All concentrations must be finite numbers")
    return values


def _inspect_group_map(path: str, expand_well_spec: Callable[[str], list[str]]) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        columns = list(reader.fieldnames or [])
        rows = list(reader)

    if not rows:
        raise ValueError("The group map is empty")

    wells_column = _find_alias(columns, "wells")
    if wells_column is None:
        raise ValueError("The group map is missing the well ranges column")

    concentration_column = _find_alias(columns, "concentrations")
    counts = [len(expand_well_spec(str(row.get(wells_column, "")))) for row in rows]
    missing_concentrations = concentration_column is None or any(
        not str(row.get(concentration_column, "")).strip() for row in rows
    )
    unique_counts = sorted(set(counts))
    return {
        "group_count": len(rows),
        "counts": counts,
        "same_count": len(unique_counts) == 1,
        "well_count": unique_counts[0] if len(unique_counts) == 1 else None,
        "missing_concentrations": missing_concentrations,
    }


def _default_plot_style(kind: str) -> dict[str, Any]:
    if kind == "denaturation":
        return {
            "title": "GFP chemical denaturation",
            "xlabel": "GuHCl concentration (M)",
            "ylabel": "",
            "palette": "Modern",
            "line_style": "Solid",
            "secondary_line_style": "Dashed",
            "data_line_style": "None",
            "line_width": 2.2,
            "marker": "Circle",
            "marker_size": 5.5,
            "alpha": 0.95,
            "font_family": "DejaVu Sans",
            "font_size": 10,
            "title_size": 14,
            "legend": "Best",
            "grid": True,
            "background": "Light",
            "colors": {},
        }
    return {
        "title": "{measurement} — {plate}",
        "xlabel": "Emission wavelength (nm)",
        "ylabel": "",
        "palette": "Modern",
        "line_style": "Solid",
        "secondary_line_style": "Dashed",
        "data_line_style": "Solid",
        "line_width": 1.6,
        "marker": "Auto",
        "marker_size": 3.2,
        "alpha": 0.86,
        "font_family": "DejaVu Sans",
        "font_size": 10,
        "title_size": 14,
        "legend": "Auto",
        "grid": True,
        "background": "Light",
        "colors": {},
    }


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
    style.configure("Status.TFrame", background=SURFACE)

    style.configure("TLabel", background=BACKGROUND, foreground=TEXT)
    style.configure("Status.TLabel", background=SURFACE, foreground=MUTED, padding=(4, 0))

    style.configure("TLabelframe", background=SURFACE, bordercolor=BORDER, relief="solid", borderwidth=1)
    style.configure(
        "TLabelframe.Label",
        background=SURFACE,
        foreground=TEXT,
        font=(default_font.actual("family"), 10, "bold"),
    )

    style.configure("TNotebook", background=BACKGROUND, borderwidth=0)
    style.configure("TNotebook.Tab", padding=(20, 11), font=(default_font.actual("family"), 10, "bold"))
    style.map(
        "TNotebook.Tab",
        background=[("selected", SURFACE), ("!selected", "#dde6f2")],
        foreground=[("selected", ACCENT_DARK), ("!selected", MUTED)],
    )

    style.configure(
        "TButton",
        padding=(10, 7),
        background="#e8edf6",
        foreground=TEXT,
        bordercolor="#c8d3e2",
        focusthickness=1,
        focuscolor=ACCENT,
    )
    style.map(
        "TButton",
        background=[("pressed", ACCENT_DARK), ("active", "#dce4f0")],
        foreground=[("pressed", "#ffffff")],
    )
    style.configure(
        "Primary.TButton",
        padding=(12, 8),
        background=ACCENT,
        foreground="#ffffff",
        bordercolor=ACCENT,
    )
    style.map(
        "Primary.TButton",
        background=[("pressed", ACCENT_DARK), ("active", ACCENT_DARK), ("disabled", "#aaa2ee")],
        foreground=[("disabled", "#f3f0ff"), ("!disabled", "#ffffff")],
    )
    style.configure("Accent.TButton", foreground=ACCENT_DARK, background="#eeeaff", bordercolor="#cfc8ff")
    style.map("Accent.TButton", background=[("active", "#e2dcff"), ("pressed", ACCENT_DARK)], foreground=[("pressed", "#ffffff")])
    style.configure("Danger.TButton", foreground=DANGER)

    style.configure("TEntry", fieldbackground=SURFACE, foreground=TEXT, padding=6, bordercolor=BORDER)
    style.configure("TCombobox", fieldbackground=SURFACE, foreground=TEXT, padding=5, bordercolor=BORDER)
    style.configure("TSpinbox", fieldbackground=SURFACE, foreground=TEXT, padding=5, bordercolor=BORDER)
    style.configure("TCheckbutton", background=BACKGROUND, foreground=TEXT)

    style.configure(
        "Treeview",
        background=SURFACE,
        fieldbackground=SURFACE,
        foreground=TEXT,
        rowheight=29,
        bordercolor=BORDER,
    )
    style.configure(
        "Treeview.Heading",
        background="#e5ebf4",
        foreground=TEXT,
        relief="flat",
        font=(default_font.actual("family"), 9, "bold"),
        padding=(5, 7),
    )
    style.map("Treeview", background=[("selected", ACCENT)], foreground=[("selected", "#ffffff")])

    style.configure("Horizontal.TProgressbar", background=ACCENT_CYAN, troughcolor="#e8edf5", bordercolor="#e8edf5")


def _draw_header(canvas: tk.Canvas) -> None:
    width = max(canvas.winfo_width(), 1)
    height = max(canvas.winfo_height(), 1)
    canvas.delete("all")

    start = tuple(int(HEADER_START[index : index + 2], 16) for index in (1, 3, 5))
    end = tuple(int(HEADER_END[index : index + 2], 16) for index in (1, 3, 5))
    steps = max(width // 8, 1)
    for step in range(steps):
        ratio = step / max(steps - 1, 1)
        color = "#" + "".join(f"{round(start[i] + (end[i] - start[i]) * ratio):02x}" for i in range(3))
        x0 = step * width / steps
        x1 = (step + 1) * width / steps + 1
        canvas.create_rectangle(x0, 0, x1, height, fill=color, outline=color)

    canvas.create_oval(width - 210, -80, width - 40, 90, fill="#243f83", outline="")
    canvas.create_oval(width - 125, 18, width - 30, 113, fill="#156b87", outline="")
    canvas.create_line(width - 185, 22, width - 83, 67, fill="#82ddf0", width=2)
    canvas.create_oval(width - 193, 14, width - 177, 30, fill="#b7f4fb", outline="")
    canvas.create_oval(width - 92, 58, width - 74, 76, fill="#b7f4fb", outline="")

    canvas.create_text(24, 25, text="Protein Folding Practical", fill="#ffffff", anchor="w", font=("TkDefaultFont", 18, "bold"))
    canvas.create_text(
        24,
        53,
        text="Plate assignment, denaturation fitting, and wavelength-resolved fluorescence.",
        fill="#c8d5ea",
        anchor="w",
        font=("TkDefaultFont", 10),
    )
    canvas.create_text(width - 36, height - 18, text="GFP  ·  GuHCl", fill="#dff9fc", anchor="e", font=("TkDefaultFont", 10, "bold"))


def _plot_colors(background: str) -> tuple[str, str, str, str]:
    if background == "Dark":
        return DARK_PLOT, "#e8eef7", "#98a2b3", "#344054"
    if background == "White":
        return "#ffffff", TEXT, "#667085", "#d0d5dd"
    return "#f8fafc", TEXT, "#667085", "#d9e2ec"


def _style_axes(axes: Any, plot_style: Optional[dict[str, Any]] = None) -> None:
    style = plot_style or {"background": "Light", "grid": True, "font_size": 10, "font_family": "DejaVu Sans"}
    face, foreground, muted, grid_color = _plot_colors(str(style.get("background", "Light")))
    axes.set_facecolor(face)
    axes.figure.patch.set_facecolor(face)
    axes.tick_params(colors=foreground, labelsize=max(int(style.get("font_size", 10)) - 1, 7))
    for spine in axes.spines.values():
        spine.set_color(grid_color)
    axes.xaxis.label.set_color(foreground)
    axes.yaxis.label.set_color(foreground)
    axes.title.set_color(foreground)
    axes.grid(bool(style.get("grid", True)), color=grid_color, alpha=0.7, linewidth=0.8)
    for label in list(axes.get_xticklabels()) + list(axes.get_yticklabels()):
        label.set_fontfamily(str(style.get("font_family", "DejaVu Sans")))


def _palette_colors(app_module: Any, palette: str, count: int) -> list[str]:
    if count <= 0:
        return []
    if palette == "Modern":
        return [MODERN_COLORS[index % len(MODERN_COLORS)] for index in range(count)]
    if palette == "Ocean":
        return [OCEAN_COLORS[index % len(OCEAN_COLORS)] for index in range(count)]
    if palette == "Sunset":
        return [SUNSET_COLORS[index % len(SUNSET_COLORS)] for index in range(count)]
    if palette == "Grayscale":
        cmap = app_module.plt.get_cmap("Greys")
        return [cmap(0.3 + 0.6 * index / max(count - 1, 1)) for index in range(count)]
    cmap_name = {"Viridis": "viridis", "Plasma": "plasma", "Tab10": "tab10"}.get(palette, "tab10")
    cmap = app_module.plt.get_cmap(cmap_name)
    if palette == "Tab10":
        return [cmap(index % 10) for index in range(count)]
    return [cmap(index / max(count - 1, 1)) for index in range(count)]


def _series_colors(app_module: Any, names: list[str], plot_style: dict[str, Any]) -> dict[str, Any]:
    base = _palette_colors(app_module, str(plot_style.get("palette", "Modern")), len(names))
    overrides = dict(plot_style.get("colors", {}))
    return {name: overrides.get(name, base[index]) for index, name in enumerate(names)}


def _marker_for(style: dict[str, Any], count: int):
    marker_name = str(style.get("marker", "Circle"))
    marker = MARKERS.get(marker_name, "o")
    if marker == "auto":
        return None if count > 24 else "o"
    return marker


def _format_title(template: str, **values: Any) -> str:
    try:
        return str(template).format_map(_SafeFormat(values))
    except (KeyError, ValueError):
        return str(template)


def _apply_text_style(axes: Any, style: dict[str, Any], title: str, xlabel: str, ylabel: str) -> None:
    family = str(style.get("font_family", "DejaVu Sans"))
    font_size = int(style.get("font_size", 10))
    title_size = int(style.get("title_size", 14))
    axes.set_title(title, loc="left", pad=12, fontweight="bold", fontfamily=family, fontsize=title_size)
    axes.set_xlabel(xlabel, fontfamily=family, fontsize=font_size)
    axes.set_ylabel(ylabel, fontfamily=family, fontsize=font_size)


def _add_legend(axes: Any, style: dict[str, Any], count: int) -> None:
    choice = str(style.get("legend", "Auto"))
    if choice == "Off" or (choice == "Auto" and count > 24):
        return
    if choice == "Auto":
        choice = "Best"

    mapping = {
        "Best": "best",
        "Upper right": "upper right",
        "Upper left": "upper left",
        "Lower left": "lower left",
        "Lower right": "lower right",
        "Center left": "center left",
        "Center right": "center right",
        "Upper center": "upper center",
        "Lower center": "lower center",
        "Center": "center",
    }
    kwargs: dict[str, Any] = {
        "fontsize": max(int(style.get("font_size", 10)) - 1, 7),
        "frameon": False,
        "prop": {"family": str(style.get("font_family", "DejaVu Sans"))},
    }
    if choice == "Outside right":
        kwargs.update(loc="center left", bbox_to_anchor=(1.02, 0.5))
    elif choice == "Below plot":
        kwargs.update(loc="upper center", bbox_to_anchor=(0.5, -0.15), ncol=max(1, min(4, count)))
    else:
        kwargs["loc"] = mapping.get(choice, "best")
        kwargs["ncol"] = 2 if count > 8 else 1
    handles, labels = axes.get_legend_handles_labels()
    if handles:
        axes.legend(handles, labels, **kwargs)


def _sortable_value(value: str):
    cleaned = str(value).strip()
    if not cleaned:
        return (2, "")
    if cleaned.lower() in {"yes", "true"}:
        return (0, 1.0)
    if cleaned.lower() in {"no", "false"}:
        return (0, 0.0)
    try:
        return (0, float(cleaned.replace(",", "")))
    except ValueError:
        return (1, cleaned.lower())


def _sort_tree(tree: ttk.Treeview, column: str, reverse: Optional[bool] = None) -> None:
    current_column = getattr(tree, "_sort_column", None)
    current_reverse = bool(getattr(tree, "_sort_reverse", False))
    if reverse is None:
        reverse = not current_reverse if current_column == column else False

    items = list(tree.get_children(""))
    items.sort(key=lambda item: _sortable_value(tree.set(item, column)), reverse=reverse)
    for index, item in enumerate(items):
        tree.move(item, "", index)

    tree._sort_column = column
    tree._sort_reverse = reverse
    base_headings = getattr(tree, "_base_headings", {})
    for name, base in base_headings.items():
        suffix = ""
        if name == column:
            suffix = " ▼" if reverse else " ▲"
        tree.heading(name, text=base + suffix)


def _make_tree_sortable(tree: ttk.Treeview) -> None:
    columns = list(tree.cget("columns"))
    tree._base_headings = {column: str(tree.heading(column, "text")) for column in columns}
    tree._sort_column = None
    tree._sort_reverse = False
    for column in columns:
        tree.heading(column, command=lambda current=column: _sort_tree(tree, current))


def _reapply_tree_sort(tree: ttk.Treeview) -> None:
    column = getattr(tree, "_sort_column", None)
    if column:
        _sort_tree(tree, column, bool(getattr(tree, "_sort_reverse", False)))


def _ask_concentration_order(app: tk.Tk, group_count: int, well_count: int, default_values: list[float]) -> Optional[list[float]]:
    dialog = tk.Toplevel(app)
    dialog.title("GuHCl concentration order")
    dialog.transient(app)
    dialog.grab_set()
    dialog.resizable(False, False)
    dialog.configure(background=BACKGROUND)

    result: dict[str, Any] = {"values": None}
    text_var = tk.StringVar(value=", ".join(f"{value:g}" for value in default_values))
    error_var = tk.StringVar()

    body = ttk.LabelFrame(dialog, text="Concentration order", padding=16)
    body.grid(row=0, column=0, sticky="nsew", padx=14, pady=14)
    ttk.Label(
        body,
        text=(
            f"The map contains {group_count} groups with {well_count} wells each.\n"
            "Enter the GuHCl concentrations in the same order as the wells in every range."
        ),
        justify="left",
        wraplength=520,
    ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 10))
    entry = ttk.Entry(body, textvariable=text_var, width=72)
    entry.grid(row=1, column=0, columnspan=3, sticky="ew")
    ttk.Label(body, textvariable=error_var, foreground=DANGER).grid(row=2, column=0, columnspan=3, sticky="w", pady=(6, 2))

    def evenly_spaced() -> None:
        values = [index * 6.0 / max(well_count - 1, 1) for index in range(well_count)]
        text_var.set(", ".join(f"{value:.6g}" for value in values))

    def reverse_order() -> None:
        try:
            values = _parse_concentration_order(text_var.get(), well_count)
            text_var.set(", ".join(f"{value:g}" for value in reversed(values)))
            error_var.set("")
        except Exception as exc:
            error_var.set(str(exc))

    def accept() -> None:
        try:
            result["values"] = _parse_concentration_order(text_var.get(), well_count)
            dialog.destroy()
        except Exception as exc:
            error_var.set(str(exc))

    ttk.Button(body, text="Generate 0–6 M", command=evenly_spaced).grid(row=3, column=0, sticky="w", pady=(8, 0))
    ttk.Button(body, text="Reverse order", command=reverse_order).grid(row=3, column=1, sticky="w", padx=6, pady=(8, 0))
    ttk.Button(body, text="Use this order", command=accept, style="Primary.TButton").grid(row=3, column=2, sticky="e", pady=(8, 0))

    dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
    dialog.bind("<Return>", lambda _event: accept())
    entry.focus_set()
    dialog.update_idletasks()
    x = app.winfo_rootx() + max((app.winfo_width() - dialog.winfo_reqwidth()) // 2, 0)
    y = app.winfo_rooty() + max((app.winfo_height() - dialog.winfo_reqheight()) // 3, 0)
    dialog.geometry(f"+{x}+{y}")
    app.wait_window(dialog)
    return result["values"]


def _series_names_for_style(app: tk.Tk, kind: str) -> list[str]:
    if kind == "denaturation":
        selected = app._selected_group_names()
        return selected or list(app.assignments)
    selected = app._selected_spectrum_wells()
    if selected:
        return selected
    if hasattr(app, "spectrum_well_list"):
        return [app.spectrum_well_list.get(index) for index in range(app.spectrum_well_list.size())]
    return []


def _open_style_dialog(app: tk.Tk, app_module: Any, kind: str) -> None:
    style_key = "denaturation_style" if kind == "denaturation" else "spectrum_style"
    current = dict(getattr(app, style_key))
    current["colors"] = dict(current.get("colors", {}))
    series_names = _series_names_for_style(app, kind)

    dialog = tk.Toplevel(app)
    dialog.title("Denaturation plot appearance" if kind == "denaturation" else "Spectrum plot appearance")
    dialog.transient(app)
    dialog.geometry("700x700")
    dialog.minsize(640, 620)
    dialog.configure(background=BACKGROUND)

    notebook = ttk.Notebook(dialog)
    notebook.pack(fill="both", expand=True, padx=12, pady=12)
    line_tab = ttk.Frame(notebook, padding=14)
    text_tab = ttk.Frame(notebook, padding=14)
    notebook.add(line_tab, text="Lines and colors")
    notebook.add(text_tab, text="Text and legend")

    palette_var = tk.StringVar(value=str(current["palette"]))
    line_style_var = tk.StringVar(value=str(current["line_style"]))
    secondary_style_var = tk.StringVar(value=str(current["secondary_line_style"]))
    data_line_var = tk.StringVar(value=str(current["data_line_style"]))
    width_var = tk.DoubleVar(value=float(current["line_width"]))
    marker_var = tk.StringVar(value=str(current["marker"]))
    marker_size_var = tk.DoubleVar(value=float(current["marker_size"]))
    alpha_var = tk.DoubleVar(value=float(current["alpha"]))
    background_var = tk.StringVar(value=str(current["background"]))
    grid_var = tk.BooleanVar(value=bool(current["grid"]))

    title_var = tk.StringVar(value=str(current["title"]))
    xlabel_var = tk.StringVar(value=str(current["xlabel"]))
    ylabel_var = tk.StringVar(value=str(current["ylabel"]))
    font_var = tk.StringVar(value=str(current["font_family"]))
    font_size_var = tk.IntVar(value=int(current["font_size"]))
    title_size_var = tk.IntVar(value=int(current["title_size"]))
    legend_var = tk.StringVar(value=str(current["legend"]))

    line_tab.columnconfigure(1, weight=1)
    row = 0
    for label, variable, values in (
        ("Color palette", palette_var, PALETTES),
        ("Main line style", line_style_var, tuple(LINE_STYLES)),
        ("Second model style", secondary_style_var, tuple(LINE_STYLES)),
        ("Data point line", data_line_var, tuple(LINE_STYLES)),
        ("Marker", marker_var, tuple(MARKERS)),
        ("Plot background", background_var, ("Light", "White", "Dark")),
    ):
        ttk.Label(line_tab, text=label).grid(row=row, column=0, sticky="w", padx=(0, 10), pady=5)
        ttk.Combobox(line_tab, textvariable=variable, values=values, state="readonly").grid(row=row, column=1, sticky="ew", pady=5)
        row += 1

    for label, variable, start, stop, step in (
        ("Line width", width_var, 0.5, 6.0, 0.1),
        ("Marker size", marker_size_var, 1.0, 14.0, 0.5),
        ("Opacity", alpha_var, 0.1, 1.0, 0.05),
    ):
        ttk.Label(line_tab, text=label).grid(row=row, column=0, sticky="w", padx=(0, 10), pady=5)
        ttk.Spinbox(line_tab, from_=start, to=stop, increment=step, textvariable=variable).grid(row=row, column=1, sticky="ew", pady=5)
        row += 1

    ttk.Checkbutton(line_tab, text="Show grid", variable=grid_var).grid(row=row, column=0, columnspan=2, sticky="w", pady=(7, 10))
    row += 1

    colors_frame = ttk.LabelFrame(line_tab, text="Individual series colors", padding=10)
    colors_frame.grid(row=row, column=0, columnspan=2, sticky="nsew", pady=(4, 0))
    colors_frame.columnconfigure(0, weight=1)
    colors_frame.rowconfigure(0, weight=1)
    line_tab.rowconfigure(row, weight=1)
    color_list = tk.Listbox(
        colors_frame,
        exportselection=False,
        background=SURFACE,
        foreground=TEXT,
        selectbackground=ACCENT,
        selectforeground="#ffffff",
        highlightthickness=1,
        highlightbackground=BORDER,
        relief="flat",
        height=5,
    )
    color_list.grid(row=0, column=0, rowspan=3, sticky="nsew")
    for name in series_names:
        color_list.insert(tk.END, name)
    if series_names:
        color_list.selection_set(0)
    color_preview = tk.Label(colors_frame, text="Color", width=14, relief="solid", borderwidth=1)
    color_preview.grid(row=0, column=1, padx=(10, 0), sticky="ew")

    def selected_series() -> Optional[str]:
        selection = color_list.curselection()
        return color_list.get(selection[0]) if selection else None

    def update_preview(_event: Any = None) -> None:
        name = selected_series()
        color = current["colors"].get(name, "#d9e2ec") if name else "#d9e2ec"
        color_preview.configure(background=color, foreground="#ffffff" if color != "#d9e2ec" else TEXT)

    def choose_color() -> None:
        name = selected_series()
        if not name:
            return
        initial = current["colors"].get(name, "#6957f5")
        chosen = colorchooser.askcolor(initialcolor=initial, parent=dialog)[1]
        if chosen:
            current["colors"][name] = chosen
            update_preview()

    def reset_color() -> None:
        name = selected_series()
        if name:
            current["colors"].pop(name, None)
            update_preview()

    color_list.bind("<<ListboxSelect>>", update_preview)
    ttk.Button(colors_frame, text="Choose color", command=choose_color).grid(row=1, column=1, padx=(10, 0), pady=(8, 4), sticky="ew")
    ttk.Button(colors_frame, text="Use palette", command=reset_color).grid(row=2, column=1, padx=(10, 0), sticky="ew")
    update_preview()

    text_tab.columnconfigure(1, weight=1)
    ttk.Label(text_tab, text="Title").grid(row=0, column=0, sticky="w", padx=(0, 10), pady=6)
    ttk.Entry(text_tab, textvariable=title_var).grid(row=0, column=1, sticky="ew", pady=6)
    if kind == "spectrum":
        ttk.Label(text_tab, text="You can use {plate}, {measurement}, and {count}.", foreground=MUTED).grid(row=1, column=1, sticky="w")
        start_row = 2
    else:
        start_row = 1
    ttk.Label(text_tab, text="X-axis label").grid(row=start_row, column=0, sticky="w", padx=(0, 10), pady=6)
    ttk.Entry(text_tab, textvariable=xlabel_var).grid(row=start_row, column=1, sticky="ew", pady=6)
    ttk.Label(text_tab, text="Y-axis label").grid(row=start_row + 1, column=0, sticky="w", padx=(0, 10), pady=6)
    ttk.Entry(text_tab, textvariable=ylabel_var).grid(row=start_row + 1, column=1, sticky="ew", pady=6)

    fonts = sorted(set(tkfont.families(app)))
    common = [name for name in ("DejaVu Sans", "Arial", "Calibri", "Helvetica", "Times New Roman") if name in fonts]
    font_values = common + [name for name in fonts if name not in common]
    ttk.Label(text_tab, text="Font").grid(row=start_row + 2, column=0, sticky="w", padx=(0, 10), pady=6)
    ttk.Combobox(text_tab, textvariable=font_var, values=font_values, state="readonly").grid(row=start_row + 2, column=1, sticky="ew", pady=6)
    ttk.Label(text_tab, text="Base font size").grid(row=start_row + 3, column=0, sticky="w", padx=(0, 10), pady=6)
    ttk.Spinbox(text_tab, from_=7, to=24, textvariable=font_size_var).grid(row=start_row + 3, column=1, sticky="ew", pady=6)
    ttk.Label(text_tab, text="Title size").grid(row=start_row + 4, column=0, sticky="w", padx=(0, 10), pady=6)
    ttk.Spinbox(text_tab, from_=9, to=32, textvariable=title_size_var).grid(row=start_row + 4, column=1, sticky="ew", pady=6)
    ttk.Label(text_tab, text="Legend").grid(row=start_row + 5, column=0, sticky="w", padx=(0, 10), pady=6)
    ttk.Combobox(text_tab, textvariable=legend_var, values=LEGEND_LOCATIONS, state="readonly").grid(row=start_row + 5, column=1, sticky="ew", pady=6)

    footer = ttk.Frame(dialog)
    footer.pack(fill="x", padx=12, pady=(0, 12))

    def collect() -> dict[str, Any]:
        width = float(width_var.get())
        marker_size = float(marker_size_var.get())
        alpha = float(alpha_var.get())
        if width <= 0 or marker_size <= 0:
            raise ValueError("Line width and marker size must be greater than zero")
        if not 0 < alpha <= 1:
            raise ValueError("Opacity must be between 0 and 1")
        updated = {
            "title": title_var.get(),
            "xlabel": xlabel_var.get(),
            "ylabel": ylabel_var.get(),
            "palette": palette_var.get(),
            "line_style": line_style_var.get(),
            "secondary_line_style": secondary_style_var.get(),
            "data_line_style": data_line_var.get(),
            "line_width": width,
            "marker": marker_var.get(),
            "marker_size": marker_size,
            "alpha": alpha,
            "font_family": font_var.get(),
            "font_size": int(font_size_var.get()),
            "title_size": int(title_size_var.get()),
            "legend": legend_var.get(),
            "grid": bool(grid_var.get()),
            "background": background_var.get(),
            "colors": dict(current["colors"]),
        }
        return updated

    def apply(close: bool = False) -> None:
        try:
            updated = collect()
            setattr(app, style_key, updated)
            if kind == "denaturation" and getattr(app, "_last_fit_bundles", None):
                app._render_denaturation()
            if kind == "spectrum" and getattr(app, "_last_spectrum_payload", None):
                app._render_spectra()
            app.status_var.set("Updated plot appearance.")
            if close:
                dialog.destroy()
        except Exception as exc:
            messagebox.showerror("Cannot apply appearance", str(exc), parent=dialog)

    def reset() -> None:
        setattr(app, style_key, _default_plot_style(kind))
        dialog.destroy()
        _open_style_dialog(app, app_module, kind)

    ttk.Button(footer, text="Reset", command=reset).pack(side="left")
    ttk.Button(footer, text="Apply", command=lambda: apply(False)).pack(side="right", padx=(6, 0))
    ttk.Button(footer, text="Apply and close", command=lambda: apply(True), style="Primary.TButton").pack(side="right")


def _decorate_ui(app: tk.Tk, app_module: Any) -> None:
    notebook = next((child for child in app.winfo_children() if isinstance(child, ttk.Notebook)), None)

    if notebook is not None:
        header = tk.Canvas(app, height=82, highlightthickness=0, borderwidth=0, background=HEADER_START)
        header.bind("<Configure>", lambda _event: _draw_header(header))
        header.pack(fill="x", before=notebook)
        app.after_idle(lambda: _draw_header(header))

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

    status_bar = ttk.Frame(app, style="Status.TFrame", padding=(12, 7))
    ttk.Label(status_bar, textvariable=app.status_var, style="Status.TLabel", anchor="w").pack(side="left", fill="x", expand=True)
    app.busy_progress = ttk.Progressbar(status_bar, mode="indeterminate", length=120, style="Horizontal.TProgressbar")
    credit = tk.Label(
        status_bar,
        text="Designed by Ben Shin (http://ben-shin.github.io/)",
        background=SURFACE,
        foreground="#7a8291",
        font=("TkDefaultFont", 8),
        cursor="hand2",
    )
    credit.pack(side="right", padx=(14, 0))
    credit.bind("<Button-1>", lambda _event: webbrowser.open("http://ben-shin.github.io/"))
    status_bar.pack(side="bottom", fill="x")

    analysis_plot = _find_button(app, "Plot and fit selected groups")
    if analysis_plot is not None:
        analysis_style_button = ttk.Button(
            analysis_plot.master,
            text="Customize plot appearance",
            command=lambda: _open_style_dialog(app, app_module, "denaturation"),
            style="Accent.TButton",
        )
        save_graph = _find_button(app, "Save graph")
        if save_graph is not None and save_graph.master == analysis_plot.master:
            analysis_style_button.pack(fill="x", pady=3, before=save_graph)
        else:
            analysis_style_button.pack(fill="x", pady=3)

    spectrum_plot = _find_button(app, "Plot selected well spectra")
    if spectrum_plot is not None:
        spectrum_style_button = ttk.Button(
            spectrum_plot.master,
            text="Customize plot appearance",
            command=lambda: _open_style_dialog(app, app_module, "spectrum"),
            style="Accent.TButton",
        )
        save_spectrum = _find_button(app, "Save spectrum graph")
        if save_spectrum is not None and save_spectrum.master == spectrum_plot.master:
            spectrum_style_button.pack(fill="x", pady=3, before=save_spectrum)
        else:
            spectrum_style_button.pack(fill="x", pady=3)

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

    _make_tree_sortable(app.report_tree)

    for figure, axes, empty_text, plot_style in (
        (app.figure, app.axes, "Select one or more groups, then run the fit.", app.denaturation_style),
        (app.spectrum_figure, app.spectrum_axes, "Select wells or a practical group, then plot spectra.", app.spectrum_style),
    ):
        axes.clear()
        _style_axes(axes, plot_style)
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
    app.busy_progress.pack(side="right", padx=(12, 0))
    app.busy_progress.start(12)
    for button in getattr(app, "_busy_buttons", []):
        button.state(["disabled"])
    app.update_idletasks()
    return True


def _end_task(app: tk.Tk) -> None:
    app._task_active = False
    app.configure(cursor="")
    app.busy_progress.stop()
    app.busy_progress.pack_forget()
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
        if getattr(app, "_closing", False):
            return
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
        self._closing = False
        self.denaturation_style = _default_plot_style("denaturation")
        self.spectrum_style = _default_plot_style("spectrum")
        self._last_fit_bundles = None
        self._last_fit_signal_column = ""
        self._last_spectrum_payload = None
        original_init(self)
        self.protocol("WM_DELETE_WINDOW", self._close_enhanced_app)

    def enhanced_build_ui(self: tk.Tk) -> None:
        _configure_theme(self)
        original_build_ui(self)
        _decorate_ui(self, app_module)

    def close_enhanced_app(self: tk.Tk) -> None:
        if self._closing:
            return
        self._closing = True

        try:
            self._executor.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass

        try:
            self.quit()
            self.destroy()
        finally:
            # ThreadPoolExecutor workers are non-daemon threads. A running
            # NumPy/SciPy task can otherwise keep the detached process alive
            # after the Tk window has closed.
            os._exit(0)

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

    def load_group_map_with_prompt(self: tk.Tk) -> None:
        path = filedialog.askopenfilename(
            title="Select group map CSV",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if not path:
            return
        if self.data.empty:
            messagebox.showinfo("Load plate data first", "Load the plate CSV files before the group map.")
            return

        try:
            info = _inspect_group_map(path, app_module.expand_well_spec)
            try:
                default_concentrations = self._parse_concentrations()
            except Exception:
                default_concentrations = []

            if info["missing_concentrations"]:
                if not info["same_count"]:
                    raise ValueError(
                        "The groups have different numbers of wells. Add a concentrations column to the group map for each row."
                    )
                count = int(info["well_count"])
                if len(default_concentrations) != count:
                    start = float(self.conc_start_var.get())
                    stop = float(self.conc_stop_var.get())
                    default_concentrations = app_module.np.linspace(start, stop, count).tolist()
                chosen = _ask_concentration_order(self, int(info["group_count"]), count, default_concentrations)
                if chosen is None:
                    return
                default_concentrations = chosen
                self.concentration_var.set(", ".join(f"{value:g}" for value in chosen))

            wavelength = float(self.wavelength_var.get()) if self.wavelength_var.get() else None
            data = self.data
            default_measurement = self.measurement_var.get()
            existing = dict(self.assignments)

            def work():
                return app_module.load_group_map_assignments(
                    data,
                    path,
                    default_concentrations=default_concentrations,
                    default_measurement=default_measurement,
                    default_wavelength_nm=wavelength,
                    existing_assignments=existing,
                )

            def finish(imported):
                self.assignments.update(imported)
                self.refresh_group_views()
                self.status_var.set(f"Loaded {len(imported)} practical group(s) from {Path(path).name}.")

            _run_background(self, work, finish, "Cannot load group map", f"Loading {Path(path).name}...")
        except Exception as exc:
            messagebox.showerror("Cannot load group map", str(exc))

    def render_denaturation(self: tk.Tk) -> None:
        bundles = self._last_fit_bundles
        if not bundles:
            return
        style = self.denaturation_style
        self.axes.clear()
        _style_axes(self.axes, style)
        for item in self.report_tree.get_children():
            self.report_tree.delete(item)
        self.last_fit_rows = []

        group_names = [bundle["group"] for bundle in bundles]
        colors = _series_colors(app_module, group_names, style)
        marker = _marker_for(style, len(group_names))
        data_line = LINE_STYLES.get(str(style.get("data_line_style", "None")), "none")
        main_line = LINE_STYLES.get(str(style.get("line_style", "Solid")), "-")
        second_line = LINE_STYLES.get(str(style.get("secondary_line_style", "Dashed")), "--")
        width = float(style.get("line_width", 2.2))
        alpha = float(style.get("alpha", 0.95))

        for bundle in bundles:
            group_name = bundle["group"]
            x = bundle["x"]
            y = bundle["y"]
            group_color = colors[group_name]
            if x is not None:
                point_line = self.axes.plot(
                    x,
                    y,
                    marker=marker,
                    markersize=float(style.get("marker_size", 5.5)),
                    linestyle=data_line,
                    linewidth=max(width * 0.7, 0.5),
                    color=group_color,
                    alpha=alpha,
                    label="_nolegend_",
                )[0]
                successful = [curve for curve in bundle["curves"] if curve[0].success]
                for result, is_best, prediction in bundle["curves"]:
                    if not result.success:
                        continue
                    label = f"{group_name} — {result.model_name}" if is_best or len(successful) == 1 else "_nolegend_"
                    self.axes.plot(
                        bundle["grid"],
                        prediction,
                        linestyle=main_line if is_best else second_line,
                        linewidth=width if is_best else max(width * 0.72, 0.5),
                        color=group_color,
                        alpha=alpha if is_best else max(alpha * 0.58, 0.2),
                        label=label,
                    )
                if not successful:
                    point_line.set_label(group_name)

            for row in bundle["rows"]:
                self.last_fit_rows.append(row)
                self._insert_report_row(row)

        signal_column = self._last_fit_signal_column
        title = _format_title(str(style.get("title", "GFP chemical denaturation")), count=len(group_names))
        ylabel = str(style.get("ylabel", "")).strip() or signal_column
        _apply_text_style(
            self.axes,
            style,
            title,
            str(style.get("xlabel", "GuHCl concentration (M)")),
            ylabel,
        )
        self.axes.margins(x=0.03)
        _add_legend(self.axes, style, len(group_names))
        _reapply_tree_sort(self.report_tree)
        self.canvas.draw_idle()

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
            self._last_fit_bundles = bundles
            self._last_fit_signal_column = signal_column
            self._render_denaturation()
            self.status_var.set(f"Fitted {len(selected_names)} group(s). Click a summary header to sort it.")

        _run_background(self, work, finish, "Fit failed", f"Fitting {len(selected_names)} group(s)...")

    def render_spectra(self: tk.Tk) -> None:
        payload = self._last_spectrum_payload
        if not payload:
            return
        spectrum, y_column, series, measurement, plate_id = payload
        style = self.spectrum_style
        self.spectrum_axes.clear()
        _style_axes(self.spectrum_axes, style)
        names = [item[0] for item in series]
        colors = _series_colors(app_module, names, style)
        count = len(series)
        marker = _marker_for(style, count)
        markevery = 4 if count > 12 and marker is not None else None
        line_style = LINE_STYLES.get(str(style.get("line_style", "Solid")), "-")
        for well, wavelengths, values in series:
            self.spectrum_axes.plot(
                wavelengths,
                values,
                marker=marker,
                markevery=markevery,
                markersize=float(style.get("marker_size", 3.2)),
                linewidth=float(style.get("line_width", 1.6)),
                linestyle=line_style,
                color=colors[well],
                alpha=float(style.get("alpha", 0.86)),
                label=well,
            )
        title = _format_title(
            str(style.get("title", "{measurement} — {plate}")),
            measurement=measurement,
            plate=plate_id,
            count=count,
        )
        ylabel = str(style.get("ylabel", "")).strip() or y_column
        _apply_text_style(
            self.spectrum_axes,
            style,
            title,
            str(style.get("xlabel", "Emission wavelength (nm)")),
            ylabel,
        )
        self.spectrum_axes.margins(x=0.02)
        _add_legend(self.spectrum_axes, style, count)
        self.spectrum_canvas.draw_idle()

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
            return spectrum, y_column, series, measurement, plate_id

        def finish(payload):
            self._last_spectrum_payload = payload
            self._render_spectra()
            spectrum = payload[0]
            self.status_var.set(f"Plotted spectra for {spectrum['well'].nunique()} well(s).")

        _run_background(self, work, finish, "Cannot plot spectra", f"Preparing {len(wells)} spectra...")

    app_class.__init__ = enhanced_init
    app_class._build_ui = enhanced_build_ui
    app_class._close_enhanced_app = close_enhanced_app
    app_class.load_files = load_files_async
    app_class.load_group_map = load_group_map_with_prompt
    app_class.plot_and_fit = plot_and_fit_async
    app_class.plot_spectra = plot_spectra_async
    app_class._render_denaturation = render_denaturation
    app_class._render_spectra = render_spectra
    app_class._enhancements_installed = True
