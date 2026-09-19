"""Interactive 96-well plate map.

The map shows three things at once: which wells hold data for the current
plate and signal, which wells are already spoken for by another practical
group, and the order in which the current selection was clicked. Reading order
matters because it sets the GuHCl concentration of each condition, so every
selected well carries its position in the series.
"""

from __future__ import annotations

import tkinter as tk
from typing import Callable, Iterable, Optional

from .wells import PLATE_ROWS, indices_to_well

SURFACE = "#ffffff"
EMPTY_FILL = "#eef1f6"
EMPTY_OUTLINE = "#e3e8f0"
EMPTY_TEXT = "#aab3c0"
WELL_OUTLINE = "#cbd6e6"
WELL_TEXT = "#152033"
SELECTED_FILL = "#6957f5"
SELECTED_OUTLINE = "#4735cf"
SELECTED_TEXT = "#ffffff"
HOVER_OUTLINE = "#0f9bb0"
LABEL_TEXT = "#5d6b82"

# Pale fills for wells that already belong to a group, with a stronger edge of
# the same hue. Twelve are enough that neighbouring groups never collide.
GROUP_FILLS = [
    ("#e7f0ff", "#7ea6e8"),
    ("#e6f7ee", "#6cbf94"),
    ("#fdf0e3", "#e0a765"),
    ("#f6ebfb", "#b481d8"),
    ("#e5f6f8", "#67b9c6"),
    ("#fdeaef", "#e089a1"),
    ("#f0f3dd", "#a8b45f"),
    ("#eceaf9", "#8f86d8"),
    ("#e9f4e2", "#88b36a"),
    ("#fbeee7", "#d29274"),
    ("#e6f1f4", "#7fa8b8"),
    ("#f4eef6", "#a98cb6"),
]

COLUMN_LABEL_HEIGHT = 18
ROW_LABEL_WIDTH = 22
CELL_PADDING = 2


def group_palette(names: Iterable[str]) -> dict[str, tuple[str, str]]:
    """Map group names onto stable (fill, outline) colour pairs."""
    return {name: GROUP_FILLS[index % len(GROUP_FILLS)] for index, name in enumerate(names)}


class PlateMap(tk.Canvas):
    """A clickable 8x12 plate grid."""

    def __init__(
        self,
        master: tk.Misc,
        *,
        on_click: Optional[Callable[[str], None]] = None,
        on_drag: Optional[Callable[[str], None]] = None,
        on_right_click: Optional[Callable[[str], None]] = None,
        on_hover: Optional[Callable[[Optional[str]], None]] = None,
        **kwargs: object,
    ) -> None:
        options: dict[str, object] = {
            "background": SURFACE,
            "highlightthickness": 0,
            "borderwidth": 0,
            "width": 560,
            "height": 300,
        }
        options.update(kwargs)
        super().__init__(master, **options)  # type: ignore[arg-type]

        self._on_click = on_click
        self._on_drag = on_drag
        self._on_right_click = on_right_click
        self._on_hover = on_hover

        self.available: set[str] = set()
        self.selected: list[str] = []
        self.group_by_well: dict[str, str] = {}
        self.group_colors: dict[str, tuple[str, str]] = {}
        self.values: dict[str, float] = {}

        self._hover_well: Optional[str] = None
        self._drag_well: Optional[str] = None
        self._redraw_pending = False

        self.bind("<Configure>", lambda _event: self.refresh())
        self.bind("<Button-1>", self._handle_press)
        self.bind("<B1-Motion>", self._handle_drag)
        self.bind("<ButtonRelease-1>", self._handle_release)
        self.bind("<Button-3>", self._handle_right_click)
        self.bind("<Motion>", self._handle_motion)
        self.bind("<Leave>", self._handle_leave)

    # ------------------------------------------------------------------ state

    def set_available(self, wells: Iterable[str]) -> None:
        self.available = {str(well) for well in wells}
        self.schedule_refresh()

    def set_values(self, values: dict[str, float]) -> None:
        self.values = dict(values)
        self.schedule_refresh()

    def set_selected(self, wells: Iterable[str]) -> None:
        self.selected = [str(well) for well in wells]
        self.schedule_refresh()

    def set_groups(self, group_by_well: dict[str, str], names: Iterable[str]) -> None:
        self.group_by_well = dict(group_by_well)
        self.group_colors = group_palette(names)
        self.schedule_refresh()

    def describe(self, well: Optional[str]) -> str:
        """One-line readout for the well under the pointer."""
        if well is None:
            return ""
        parts = [well]
        value = self.values.get(well)
        parts.append(f"{value:,.0f}" if value is not None else "no data")
        group = self.group_by_well.get(well)
        if group:
            parts.append(f"group {group}")
        if well in self.selected:
            parts.append(f"condition {self.selected.index(well) + 1}")
        return "  ·  ".join(parts)

    # --------------------------------------------------------------- geometry

    def _cell_size(self) -> tuple[float, float]:
        width = max(self.winfo_width(), 120)
        height = max(self.winfo_height(), 90)
        return (width - ROW_LABEL_WIDTH) / 12.0, (height - COLUMN_LABEL_HEIGHT) / 8.0

    def well_at(self, x: float, y: float) -> Optional[str]:
        cell_width, cell_height = self._cell_size()
        if x < ROW_LABEL_WIDTH or y < COLUMN_LABEL_HEIGHT:
            return None
        column = int((x - ROW_LABEL_WIDTH) // cell_width)
        row = int((y - COLUMN_LABEL_HEIGHT) // cell_height)
        if not (0 <= row < 8 and 0 <= column < 12):
            return None
        return indices_to_well(row, column)

    # ---------------------------------------------------------------- drawing

    def schedule_refresh(self) -> None:
        """Coalesce bursts of state changes into a single redraw."""
        if self._redraw_pending:
            return
        self._redraw_pending = True
        self.after_idle(self._redraw_now)

    def _redraw_now(self) -> None:
        self._redraw_pending = False
        self.refresh()

    def refresh(self) -> None:
        if not self.winfo_exists():
            return
        self.delete("all")
        cell_width, cell_height = self._cell_size()
        order = {well: index + 1 for index, well in enumerate(self.selected)}
        label_size = max(6, min(10, int(min(cell_width, cell_height) / 3.6)))
        badge_size = max(6, label_size - 1)

        for column in range(12):
            self.create_text(
                ROW_LABEL_WIDTH + cell_width * (column + 0.5),
                COLUMN_LABEL_HEIGHT / 2,
                text=str(column + 1),
                fill=LABEL_TEXT,
                font=("TkDefaultFont", label_size),
            )
        for row, letter in enumerate(PLATE_ROWS):
            self.create_text(
                ROW_LABEL_WIDTH / 2,
                COLUMN_LABEL_HEIGHT + cell_height * (row + 0.5),
                text=letter,
                fill=LABEL_TEXT,
                font=("TkDefaultFont", label_size),
            )

        for row in range(8):
            for column in range(12):
                well = indices_to_well(row, column)
                left = ROW_LABEL_WIDTH + cell_width * column + CELL_PADDING
                top = COLUMN_LABEL_HEIGHT + cell_height * row + CELL_PADDING
                right = ROW_LABEL_WIDTH + cell_width * (column + 1) - CELL_PADDING
                bottom = COLUMN_LABEL_HEIGHT + cell_height * (row + 1) - CELL_PADDING

                position = order.get(well)
                group = self.group_by_well.get(well)
                if position is not None:
                    fill, outline, text_color = SELECTED_FILL, SELECTED_OUTLINE, SELECTED_TEXT
                elif well not in self.available:
                    fill, outline, text_color = EMPTY_FILL, EMPTY_OUTLINE, EMPTY_TEXT
                elif group is not None:
                    fill, outline = self.group_colors.get(group, (SURFACE, WELL_OUTLINE))
                    text_color = WELL_TEXT
                else:
                    fill, outline, text_color = SURFACE, WELL_OUTLINE, WELL_TEXT

                width = 2 if well == self._hover_well else 1
                if well == self._hover_well:
                    outline = HOVER_OUTLINE
                self.create_rectangle(left, top, right, bottom, fill=fill, outline=outline, width=width)
                # A selected well carries its place in the concentration series,
                # so the label moves up to leave room for the number below it.
                stacked = position is not None and cell_height > 26
                self.create_text(
                    (left + right) / 2,
                    (top + bottom) / 2 - (cell_height * 0.16 if stacked else 0),
                    text=well,
                    fill=text_color,
                    font=("TkDefaultFont", label_size),
                )
                if stacked:
                    self.create_text(
                        (left + right) / 2,
                        (top + bottom) / 2 + cell_height * 0.21,
                        text=str(position),
                        fill=SELECTED_TEXT,
                        font=("TkDefaultFont", badge_size, "bold"),
                    )

    # ------------------------------------------------------------------ input

    def _handle_press(self, event: tk.Event) -> None:
        well = self.well_at(event.x, event.y)
        self._drag_well = well
        if well is not None and self._on_click is not None:
            self._on_click(well)

    def _handle_drag(self, event: tk.Event) -> None:
        well = self.well_at(event.x, event.y)
        if well is None or well == self._drag_well:
            return
        self._drag_well = well
        self._set_hover(well)
        if self._on_drag is not None:
            self._on_drag(well)

    def _handle_release(self, _event: tk.Event) -> None:
        self._drag_well = None

    def _handle_right_click(self, event: tk.Event) -> None:
        well = self.well_at(event.x, event.y)
        if well is not None and self._on_right_click is not None:
            self._on_right_click(well)

    def _handle_motion(self, event: tk.Event) -> None:
        self._set_hover(self.well_at(event.x, event.y))

    def _handle_leave(self, _event: tk.Event) -> None:
        self._set_hover(None)

    def _set_hover(self, well: Optional[str]) -> None:
        if well == self._hover_well:
            return
        self._hover_well = well
        self.schedule_refresh()
        if self._on_hover is not None:
            self._on_hover(well)


__all__ = ["PlateMap", "group_palette"]
