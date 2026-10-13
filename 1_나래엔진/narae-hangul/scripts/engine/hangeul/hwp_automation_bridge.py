"""Hancom Office 2024 COM Automation Bridge for native HWP documents.

Engineered directly from Hancom Office 2024 runtime specifications (REA analysis):
- HWPFrame.HwpObject (CLSID {5E6A8276-CF1C-42B8-BCED-319548B02AF6}, version 13.0)
- Table Actions: TableMergeCell, TableSplitCell, TableCellBlock, TableCellBlockExtend
- Border & Fill: CellBorderFill action with HCellBorderFill parameter set
- Typography: CharShape (Height: 1pt=100, Spacing: -50~50%, Ratio: 50~200%, Bold, TextColor)
- Paragraph Shape: ParagraphShape (Align: 0=Left, 1=Right, 2=Center, 3=Justify, LineSpacing: 160%)
- LineType: None=0, Solid=1, Dash=2, Dot=3, DashDot=4, DashDotDot=5, Circle=7, DoubleSlim=8
- LineWidth: 0.1mm=0, 0.12mm=1, 0.15mm=2, 0.2mm=3, 0.25mm=4, 0.3mm=5, 0.4mm=6, 0.5mm=7, 0.6mm=8, 0.7mm=9, 1.0mm=10
"""
from __future__ import annotations

import os
from pathlib import Path
import re
from typing import Any

LINE_TYPES = {
    "NONE": 0, "SOLID": 1, "DASH": 2, "DOT": 3, "DASHDOT": 4, "DASH_DOT": 4,
    "DASHDOTDOT": 5, "DASH_DOT_DOT": 5, "CIRCLE": 7, "DOUBLESLIM": 8, "DOUBLE_SLIM": 8,
    "SLIMTHICK": 9, "THICKSLIM": 10, "SLIMTHICKSLIM": 11
}

LINE_WIDTHS = {
    "0.1MM": 0, "0.12MM": 1, "0.15MM": 2, "0.2MM": 3, "0.25MM": 4, "0.3MM": 5,
    "0.4MM": 6, "0.5MM": 7, "0.6MM": 8, "0.7MM": 9, "1.0MM": 10, "1.5MM": 11,
    "2.0MM": 12, "3.0MM": 13, "4.0MM": 14, "5.0MM": 15
}

def parse_color_hex(hex_str: str) -> tuple[int, int, int]:
    """Parse #RRGGBB into (r, g, b) components."""
    hex_str = hex_str.lstrip("#")
    if len(hex_str) == 6:
        return int(hex_str[0:2], 16), int(hex_str[2:4], 16), int(hex_str[4:6], 16)
    return 0, 0, 0

def hex_to_hwp_color(hwp: Any, hex_str: str) -> int:
    r, g, b = parse_color_hex(hex_str)
    return hwp.RGBColor(r, g, b)

class HancomBridge:
    def __init__(self, visible: bool = False):
        import win32com.client
        self.hwp = win32com.client.Dispatch("HWPFrame.HwpObject")
        self.hwp.RegisterModule("FilePathCheckDLL", "FilePathCheckerModule")
        if visible:
            self.hwp.XHwpWindows.Item(0).Visible = True
        else:
            self.hwp.SetMessageBoxMode(0x00020000) # suppress dialog popups

    def close(self):
        try:
            self.hwp.Quit()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def open_doc(self, file_path: str | Path) -> bool:
        abs_path = str(Path(file_path).resolve())
        return self.hwp.Open(abs_path, "HWP", "versionwarning:false")

    def save_doc(self, file_path: str | Path | None = None) -> bool:
        if file_path:
            abs_path = str(Path(file_path).resolve())
            return self.hwp.SaveAs(abs_path)
        return self.hwp.Save()

    def find_and_move_to_text(self, text: str) -> bool:
        """Find text in document and move selection there."""
        self.hwp.InitScan()
        self.hwp.ReleaseScan()
        self.hwp.MovePos(2) # move to top (doc start)

        act = self.hwp.CreateAction("RepeatFind")
        pset = self.hwp.CreateSet("FindReplace")
        act.GetDefault(pset)
        pset.SetItem("FindString", text)
        pset.SetItem("Direction", 0) # forward
        pset.SetItem("MatchCase", 0)
        pset.SetItem("SeveralWords", 0)
        pset.SetItem("UseWildCards", 0)
        pset.SetItem("WholeWordOnly", 0)
        pset.SetItem("AutoSpell", 0)
        return act.Execute(pset)

    def apply_operations_on_hwp(self, operations: list[dict]) -> None:
        """Execute table operations on the active HWP document."""
        for op in operations:
            kind = op.get("op")

            if kind == "merge_cells":
                search_texts = op.get("find_texts") or op.get("cell_texts") or []
                if op.get("match_text"):
                    search_texts.append(op["match_text"])
                if op.get("from_text") and op.get("to_text"):
                    search_texts.extend([op["from_text"], op["to_text"]])

                if search_texts and len(search_texts) >= 2:
                    # Find first cell text
                    t1 = search_texts[0]
                    t2 = search_texts[1]
                    if self.find_and_move_to_text(t1):
                        self.hwp.HAction.Run("TableCellBlock") # F5 block start
                        self.hwp.HAction.Run("TableCellBlockExtend") # F5 extend
                        # Search second text to extend selection
                        self.find_and_move_to_text(t2)
                        self.hwp.HAction.Run("TableMergeCell") # Merge selected cells
                elif op.get("start_row") is not None and op.get("start_col") is not None:
                    # Move to table start and navigate
                    self.hwp.MovePos(2)
                    self.hwp.HAction.Run("TableCellBlock")
                    self.hwp.HAction.Run("TableCellBlockExtend")
                    col_span = op.get("col_span", 1)
                    row_span = op.get("row_span", 1)
                    for _ in range(col_span - 1):
                        self.hwp.HAction.Run("TableRightCell")
                    for _ in range(row_span - 1):
                        self.hwp.HAction.Run("TableDownCell")
                    self.hwp.HAction.Run("TableMergeCell")

                # If text replacement specified
                if op.get("text") is not None:
                    self.hwp.HAction.Run("SelectAll")
                    self.hwp.HAction.Run("Delete")
                    self.hwp.InsertText(op["text"])

                # Apply character shape
                if any(k in op for k in ("font_size_pt", "size_pt", "bold", "letter_spacing", "color")):
                    self._apply_char_shape(op)

                # Apply paragraph alignment
                if op.get("align"):
                    self._apply_para_align(op["align"])

                # Apply cell border/fill
                if op.get("border") or op.get("background"):
                    self._apply_cell_border_fill(op.get("border"), op.get("background"))

            elif kind == "split_cell":
                search_text = op.get("match_text") or op.get("find_text")
                if search_text:
                    self.find_and_move_to_text(search_text)

                self.hwp.HAction.Run("TableCellBlock")
                split_act = self.hwp.CreateAction("TableSplitCell")
                pset = self.hwp.CreateSet("TableSplitCell")
                split_act.GetDefault(pset)
                pset.SetItem("Rows", op.get("rows", 1))
                pset.SetItem("Cols", op.get("cols", 1))
                pset.SetItem("Merge", 0)
                split_act.Execute(pset)

                # If texts specified for sub-cells
                texts = op.get("texts") or []
                if texts:
                    self.hwp.InsertText(texts[0])

            elif kind == "set_cell_text":
                search_text = op.get("match_text") or op.get("find_text")
                if search_text:
                    if self.find_and_move_to_text(search_text):
                        self.hwp.HAction.Run("TableCellBlock")
                elif op.get("row") is not None and op.get("col") is not None:
                    self.hwp.MovePos(2)
                    self.hwp.HAction.Run("TableCellBlock")
                    for _ in range(op.get("col", 0)):
                        self.hwp.HAction.Run("TableRightCell")
                    for _ in range(op.get("row", 0)):
                        self.hwp.HAction.Run("TableDownCell")

                # Clear and insert new text
                if not op.get("append", False):
                    self.hwp.HAction.Run("SelectAll")
                    self.hwp.HAction.Run("Delete")
                self.hwp.InsertText(op.get("text", ""))

                if any(k in op for k in ("font_size_pt", "size_pt", "bold", "letter_spacing", "color")):
                    self._apply_char_shape(op)
                if op.get("align"):
                    self._apply_para_align(op["align"])
                if op.get("border") or op.get("background"):
                    self._apply_cell_border_fill(op.get("border"), op.get("background"))

            elif kind == "set_cell_style":
                search_text = op.get("match_text")
                if search_text:
                    self.find_and_move_to_text(search_text)
                    self.hwp.HAction.Run("TableCellBlock")
                elif op.get("row") is not None and op.get("col") is not None:
                    self.hwp.MovePos(2)
                    self.hwp.HAction.Run("TableCellBlock")
                    for _ in range(op.get("col", 0)):
                        self.hwp.HAction.Run("TableRightCell")
                    for _ in range(op.get("row", 0)):
                        self.hwp.HAction.Run("TableDownCell")

                if op.get("border") or op.get("background"):
                    self._apply_cell_border_fill(op.get("border"), op.get("background"))

                if any(k in op for k in ("font_size_pt", "size_pt", "bold", "letter_spacing", "color")):
                    self._apply_char_shape(op)

                if op.get("align"):
                    self._apply_para_align(op["align"])

            elif kind == "modify_table":
                # Hancom table property adjustment
                pass

    def _apply_char_shape(self, op: dict):
        act = self.hwp.CreateAction("CharShape")
        pset = self.hwp.CreateSet("CharShape")
        act.GetDefault(pset)

        size_pt = op.get("font_size_pt", op.get("size_pt"))
        if size_pt is not None:
            pset.SetItem("Height", round(float(size_pt) * 100))

        if op.get("bold") is not None:
            pset.SetItem("Bold", 1 if op["bold"] else 0)

        if op.get("letter_spacing") is not None:
            spacing = max(-50, min(50, int(op["letter_spacing"])))
            pset.SetItem("Spacing", spacing)

        if op.get("stretch") or op.get("ratio"):
            ratio = max(50, min(200, int(op.get("stretch") or op.get("ratio"))))
            pset.SetItem("Ratio", ratio)

        if op.get("color"):
            pset.SetItem("TextColor", hex_to_hwp_color(self.hwp, op["color"]))

        act.Execute(pset)

    def _apply_para_align(self, align_str: str):
        align_map = {"LEFT": 0, "RIGHT": 1, "CENTER": 2, "JUSTIFY": 3, "DISTRIBUTE": 4}
        val = align_map.get(align_str.upper(), 3)
        act = self.hwp.CreateAction("ParagraphShape")
        pset = self.hwp.CreateSet("ParagraphShape")
        act.GetDefault(pset)
        pset.SetItem("Align", val)
        act.Execute(pset)

    def _apply_cell_border_fill(self, border_spec: dict | None, bg_color: str | None):
        act = self.hwp.CreateAction("CellBorderFill")
        pset = self.hwp.CreateSet("CellBorderFill")
        act.GetDefault(pset)

        if border_spec:
            has_sides = any(k in border_spec for k in ("left", "right", "top", "bottom"))
            base_type_name = str(border_spec.get("type", "SOLID")).upper()
            base_type = LINE_TYPES.get(base_type_name, 1)

            w_str = str(border_spec.get("width", "0.12mm")).upper().replace(" ", "")
            if not w_str.endswith("MM"):
                w_str += "MM"
            base_width = LINE_WIDTHS.get(w_str, 1)
            base_color = hex_to_hwp_color(self.hwp, border_spec.get("color", "#000000"))

            for side in ("Left", "Right", "Top", "Bottom"):
                side_key = side.lower()
                side_spec = border_spec.get(side_key) if has_sides else None
                if side_spec:
                    if isinstance(side_spec, str):
                        s_type = LINE_TYPES.get(side_spec.upper(), base_type)
                        s_width = base_width
                        s_color = base_color
                    elif isinstance(side_spec, dict):
                        s_type = LINE_TYPES.get(str(side_spec.get("type", base_type_name)).upper(), base_type)
                        sw_str = str(side_spec.get("width", w_str)).upper().replace(" ", "")
                        if not sw_str.endswith("MM"):
                            sw_str += "MM"
                        s_width = LINE_WIDTHS.get(sw_str, base_width)
                        s_color = hex_to_hwp_color(self.hwp, side_spec.get("color", "#000000")) if "color" in side_spec else base_color
                    else:
                        s_type, s_width, s_color = base_type, base_width, base_color
                else:
                    s_type, s_width, s_color = base_type, base_width, base_color

                pset.SetItem(f"BorderType{side}", s_type)
                pset.SetItem(f"BorderWidth{side}", s_width)
                pset.SetItem(f"BorderColor{side}", s_color)

        if bg_color and bg_color.startswith("#"):
            pset.SetItem("FillAttr", 1)
            pset.SetItem("FaceColor", hex_to_hwp_color(self.hwp, bg_color))

        act.Execute(pset)

def patch_hwp_advanced(source_file: str | Path, target_file: str | Path, operations: list[dict]) -> bool:
    """Open HWP source, apply advanced Hancom operations, and save to target."""
    with HancomBridge() as bridge:
        if not bridge.open_doc(source_file):
            return False
        bridge.apply_operations_on_hwp(operations)
        return bridge.save_doc(target_file)
