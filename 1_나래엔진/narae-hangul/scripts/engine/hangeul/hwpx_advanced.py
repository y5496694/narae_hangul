"""Advanced table and object editing capabilities for native HWPX (OWPML) matching Hancom Office 2024 behavior.

Analyzed and verified against Hancom Office 2024 specifications (REA analysis):
- HWPUNIT: 1 inch = 7200 HWPUNIT (1 mm = 283.465 HWPUNIT, 1 pt = 100 HWPUNIT)
- BorderFill: line types (SOLID, NONE, DASH, DOT, DASH_DOT, DASH_DOT_DOT, DOUBLE_SLIM, etc.)
- Line widths: 0.1 mm, 0.12 mm, 0.15 mm, 0.2 mm, 0.25 mm, 0.3 mm, 0.4 mm, 0.5 mm, 0.6 mm, 0.7 mm, 1.0 mm, 1.5 mm, 2.0 mm, 3.0 mm, 4.0 mm, 5.0 mm
- Character shape: height (pt * 100), spacing (-50% ~ 50%), ratio (50% ~ 200%), bold, italic, underline, textColor
- Paragraph shape: align (LEFT, CENTER, RIGHT, JUSTIFY, DISTRIBUTE), line spacing (PERCENT, 160%)
"""
from __future__ import annotations

import io
import re
import zipfile
import xml.etree.ElementTree as ET
from typing import Any

HP = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"
HS = "{http://www.hancom.co.kr/hwpml/2011/section}"
HC = "{http://www.hancom.co.kr/hwpml/2011/core}"
HH = "{http://www.hancom.co.kr/hwpml/2011/head}"

def _register_namespaces():
    ET.register_namespace("hp", "http://www.hancom.co.kr/hwpml/2011/paragraph")
    ET.register_namespace("hs", "http://www.hancom.co.kr/hwpml/2011/section")
    ET.register_namespace("hc", "http://www.hancom.co.kr/hwpml/2011/core")
    ET.register_namespace("hh", "http://www.hancom.co.kr/hwpml/2011/head")

def _get_cell_text(tc: ET.Element) -> str:
    """Extract all text within a table cell."""
    texts = []
    for t_node in tc.findall(f".//{HP}t"):
        if t_node.text:
            texts.append(t_node.text)
    return " ".join(texts).strip()

def _find_table(root: ET.Element, op: dict) -> tuple[int, ET.Element]:
    tables = root.findall(f".//{HP}tbl")
    target = op.get("target_table")
    idx = op.get("table_index", 0)
    if target:
        match = re.search(r"body/(\d+)", target)
        if match:
            t_idx = int(match.group(1))
            if t_idx < len(tables):
                return t_idx, tables[t_idx]
    if idx < len(tables):
        return idx, tables[idx]
    raise ValueError(f"Table index {idx} out of range (found {len(tables)} tables)")

def _parse_side_border(side_spec: Any, default_type: str = "SOLID", default_width: str = "0.12 mm", default_color: str = "#000000") -> tuple[str, str, str]:
    if not side_spec:
        return default_type, default_width, default_color
    if isinstance(side_spec, str):
        val = side_spec.strip().upper()
        if val in ("NONE", "SOLID", "DASH", "DOT", "DASH_DOT", "DASH_DOT_DOT", "DOUBLE_SLIM", "SLIM_THICK", "THICK_SLIM", "CIRCLE"):
            return val, default_width, default_color
        elif val.endswith("MM") or val.replace(".", "", 1).isdigit():
            w = f"{val} mm" if not val.endswith("MM") else val
            return default_type, w, default_color
        elif val.startswith("#"):
            return default_type, default_width, val
    elif isinstance(side_spec, dict):
        b_type = str(side_spec.get("type", default_type)).upper()
        b_width = side_spec.get("width", default_width)
        if isinstance(b_width, (int, float)):
            b_width = f"{b_width} mm"
        b_color = str(side_spec.get("color", default_color))
        return b_type, b_width, b_color
    return default_type, default_width, default_color

def _ensure_border_fill(header_root: ET.Element, border_spec: dict | None, bg_color: str | None) -> str:
    """Find or create borderFill in header.xml conforming to Hancom 2024 spec."""
    border_fills = header_root.find(f".//{HH}borderFills")
    if border_fills is None:
        return "1"

    # Find highest existing id
    max_id = 0
    for bf in border_fills.findall(f"{HH}borderFill"):
        try:
            bid = int(bf.get("id", "0"))
            max_id = max(max_id, bid)
        except ValueError:
            pass

    new_id = str(max_id + 1)
    new_bf = ET.SubElement(border_fills, f"{HH}borderFill", {
        "id": new_id,
        "threeD": "0",
        "shadow": "0",
        "centerLine": "NONE",
        "breakCellSeparateLine": "0"
    })
    ET.SubElement(new_bf, f"{HH}slash", {"type": "NONE", "Crooked": "0", "isCounter": "0"})
    ET.SubElement(new_bf, f"{HH}backSlash", {"type": "NONE", "Crooked": "0", "isCounter": "0"})

    # Check for individual side specs vs uniform spec
    has_sides = border_spec and any(k in border_spec for k in ("left", "right", "top", "bottom"))
    base_type = str(border_spec.get("type", "SOLID") if border_spec else "SOLID").upper()
    base_width = border_spec.get("width", "0.12 mm") if border_spec else "0.12 mm"
    if isinstance(base_width, (int, float)):
        base_width = f"{base_width} mm"
    base_color = str(border_spec.get("color", "#000000") if border_spec else "#000000")

    sides_map = {
        "leftBorder": border_spec.get("left") if has_sides else None,
        "rightBorder": border_spec.get("right") if has_sides else None,
        "topBorder": border_spec.get("top") if has_sides else None,
        "bottomBorder": border_spec.get("bottom") if has_sides else None,
    }

    for side, side_data in sides_map.items():
        s_type, s_width, s_color = _parse_side_border(side_data, base_type, base_width, base_color)
        ET.SubElement(new_bf, f"{HH}{side}", {"type": s_type, "width": s_width, "color": s_color})

    ET.SubElement(new_bf, f"{HH}diagonal", {"type": "SOLID", "width": "0.1 mm", "color": "#000000"})

    if bg_color and bg_color.startswith("#"):
        fill_info = ET.SubElement(new_bf, f"{HC}fillInfo")
        ET.SubElement(fill_info, f"{HC}winBrush", {
            "faceColor": bg_color,
            "hatchColor": "#FF000000",
            "alpha": "0"
        })

    # update borderFills itemCnt
    border_fills.set("itemCnt", str(len(border_fills.findall(f"{HH}borderFill"))))
    return new_id

def _ensure_char_pr(header_root: ET.Element, text_style: dict | None) -> str:
    """Find or create charPr in header.xml matching Hancom 2024 spec."""
    if not text_style:
        return "8" # default Hancom body charPr ID
    char_shapes = header_root.find(f".//{HH}charShapes")
    if char_shapes is None:
        return "8"

    max_id = 0
    for cp in char_shapes.findall(f"{HH}charPr"):
        try:
            cid = int(cp.get("id", "0"))
            max_id = max(max_id, cid)
        except ValueError:
            pass

    new_id = str(max_id + 1)
    size_pt = text_style.get("size", text_style.get("size_pt", 10.0))
    height_val = str(round(float(size_pt) * 100))
    color_val = text_style.get("color", "#000000")

    char_attrib = {
        "id": new_id,
        "height": height_val,
        "textColor": color_val,
        "shadeColor": "none",
        "useFontSpace": "0",
        "useKerning": "0",
        "symMark": "NONE",
        "borderFillIDRef": "1"
    }
    new_cp = ET.SubElement(char_shapes, f"{HH}charPr", char_attrib)
    ET.SubElement(new_cp, f"{HH}fontRef", {
        "hangul": "0", "latin": "0", "hanja": "0", "japanese": "0", "other": "0", "symbol": "0", "user": "0"
    })

    ratio_val = str(text_style.get("stretch", text_style.get("ratio", 100)))
    ET.SubElement(new_cp, f"{HH}ratio", {
        "hangul": ratio_val, "latin": ratio_val, "hanja": ratio_val, "japanese": ratio_val,
        "other": ratio_val, "symbol": ratio_val, "user": ratio_val
    })

    spacing_val = str(text_style.get("letter_spacing", 0))
    ET.SubElement(new_cp, f"{HH}spacing", {
        "hangul": spacing_val, "latin": spacing_val, "hanja": spacing_val, "japanese": spacing_val,
        "other": spacing_val, "symbol": spacing_val, "user": spacing_val
    })

    ET.SubElement(new_cp, f"{HH}relSz", {
        "hangul": "100", "latin": "100", "hanja": "100", "japanese": "100",
        "other": "100", "symbol": "100", "user": "100"
    })
    ET.SubElement(new_cp, f"{HH}offset", {
        "hangul": "0", "latin": "0", "hanja": "0", "japanese": "0",
        "other": "0", "symbol": "0", "user": "0"
    })

    if text_style.get("bold"):
        ET.SubElement(new_cp, f"{HH}bold")
    if text_style.get("italic"):
        ET.SubElement(new_cp, f"{HH}italic")
    if text_style.get("underline"):
        ET.SubElement(new_cp, f"{HH}underline", {"type": "BOTTOM", "shape": "SOLID", "color": color_val})

    char_shapes.set("itemCnt", str(len(char_shapes.findall(f"{HH}charPr"))))
    return new_id

def _ensure_para_pr(header_root: ET.Element, para_style: dict | None) -> str:
    """Find or create paraPr in header.xml matching Hancom 2024 spec."""
    if not para_style:
        return "19" # default Hancom body paraPr ID
    para_shapes = header_root.find(f".//{HH}paraShapes")
    if para_shapes is None:
        return "19"

    max_id = 0
    for pp in para_shapes.findall(f"{HH}paraPr"):
        try:
            pid = int(pp.get("id", "0"))
            max_id = max(max_id, pid)
        except ValueError:
            pass

    new_id = str(max_id + 1)
    new_pp = ET.SubElement(para_shapes, f"{HH}paraPr", {
        "id": new_id,
        "tabPrIDRef": "0",
        "condense": "0",
        "fontLineHeight": "0",
        "snapToGrid": "1",
        "suppressLineNumbers": "0",
        "checked": "0"
    })
    align_val = para_style.get("align", "JUSTIFY").upper()
    if align_val not in ("LEFT", "CENTER", "RIGHT", "JUSTIFY", "DISTRIBUTE"):
        align_val = "JUSTIFY"
    ET.SubElement(new_pp, f"{HH}align", {"horizontal": align_val, "vertical": "BASELINE"})
    ET.SubElement(new_pp, f"{HH}heading", {"type": "NONE", "idRef": "0", "level": "0"})
    ET.SubElement(new_pp, f"{HH}breakSetting", {
        "breakLatinWord": "KEEP_WORD", "breakNonLatinWord": "WORD",
        "widowOrphan": "0", "keepWithNext": "0", "keepLines": "0", "pageBreakBefore": "0"
    })
    ET.SubElement(new_pp, f"{HH}autoSpacing", {"eAsianEng": "0", "eAsianNum": "0"})
    ET.SubElement(new_pp, f"{HH}margin", {
        "intent": "0", "left": "0", "right": "0", "prev": "0", "next": "0"
    })
    line_spacing = str(round(para_style.get("line_spacing", para_style.get("line", 160))))
    ET.SubElement(new_pp, f"{HH}lineSpacing", {"type": "PERCENT", "value": line_spacing, "unit": "CHAR"})

    para_shapes.set("itemCnt", str(len(para_shapes.findall(f"{HH}paraPr"))))
    return new_id


def _apply_style_to_tc(tc: ET.Element, header_root: ET.Element | None, op: dict) -> None:
    """Apply borders, backgrounds, margins, sizing, typography and paragraph alignments to a cell."""
    # Border & Background
    border_spec = op.get("border")
    bg_color = op.get("background")
    if (border_spec or bg_color) and header_root is not None:
        new_bf_id = _ensure_border_fill(header_root, border_spec, bg_color)
        tc.set("borderFillIDRef", new_bf_id)
    elif "border_fill" in op:
        tc.set("borderFillIDRef", str(op["border_fill"]))

    if "vertical_align" in op:
        subList = tc.find(f"{HP}subList")
        if subList is not None:
            subList.set("vertAlign", op["vertical_align"].upper())

    if "padding_mm" in op:
        pad = op["padding_mm"]
        margin = tc.find(f"{HP}cellMargin")
        if margin is None:
            margin = ET.SubElement(tc, f"{HP}cellMargin")
        margin.set("left", str(round(pad[0] * 283.465)))
        margin.set("right", str(round(pad[1] * 283.465)))
        margin.set("top", str(round(pad[2] * 283.465)))
        margin.set("bottom", str(round(pad[3] * 283.465)))
        tc.set("hasMargin", "1")

    if "width_mm" in op or "height_mm" in op:
        sz = tc.find(f"{HP}cellSz")
        if sz is not None:
            if "width_mm" in op:
                sz.set("width", str(round(op["width_mm"] * 283.465)))
            if "height_mm" in op:
                sz.set("height", str(round(op["height_mm"] * 283.465)))

    # Typography (Character Shape)
    if header_root is not None and (op.get("text_style") or any(k in op for k in ("font_size_pt", "size_pt", "bold", "letter_spacing", "color"))):
        char_pr_id = _ensure_char_pr(header_root, op.get("text_style") or {
            "size": op.get("font_size_pt", op.get("size_pt", 10.0)),
            "bold": op.get("bold", False),
            "letter_spacing": op.get("letter_spacing", 0),
            "color": op.get("color", "#000000")
        })
        for run in tc.findall(f".//{HP}run"):
            run.set("charPrIDRef", char_pr_id)

    # Paragraph Shape (Alignment)
    if header_root is not None and (op.get("paragraph_style") or op.get("align")):
        para_pr_id = _ensure_para_pr(header_root, op.get("paragraph_style") or {"align": op["align"]})
        for p in tc.findall(f".//{HP}p"):
            p.set("paraPrIDRef", para_pr_id)


def _set_tc_text(tc: ET.Element, header_root: ET.Element | None, text: str, op: dict) -> None:
    """Set or append text content in a cell with proper paragraph and character styling."""
    subList = tc.find(f"{HP}subList")
    vert_align = op.get("vertical_align", "CENTER").upper()
    if subList is None:
        subList = ET.SubElement(tc, f"{HP}subList", {
            "id": "", "textDirection": "HORIZONTAL", "lineWrap": "BREAK", "vertAlign": vert_align,
            "linkListIDRef": "0", "linkListNextIDRef": "0", "textWidth": "0", "textHeight": "0",
            "hasTextRef": "0", "hasNumRef": "0"
        })
    else:
        subList.set("vertAlign", vert_align)

    para_pr_id = "19"
    char_pr_id = "8"
    if header_root is not None:
        if op.get("paragraph_style"):
            para_pr_id = _ensure_para_pr(header_root, op["paragraph_style"])
        elif op.get("align"):
            para_pr_id = _ensure_para_pr(header_root, {"align": op["align"]})

        if op.get("text_style"):
            char_pr_id = _ensure_char_pr(header_root, op["text_style"])
        elif any(k in op for k in ("font_size_pt", "size_pt", "bold", "letter_spacing", "color")):
            char_pr_id = _ensure_char_pr(header_root, {
                "size": op.get("font_size_pt", op.get("size_pt", 10.0)),
                "bold": op.get("bold", False),
                "letter_spacing": op.get("letter_spacing", 0),
                "color": op.get("color", "#000000")
            })

    append_mode = op.get("append", False)
    if not append_mode:
        for p in list(subList.findall(f"{HP}p")):
            subList.remove(p)

    # Support multiple lines separated by \n
    lines = text.split("\n")
    for line in lines:
        new_p = ET.SubElement(subList, f"{HP}p", {
            "id": "2147483648", "paraPrIDRef": str(para_pr_id), "styleIDRef": "0",
            "pageBreak": "0", "columnBreak": "0", "merged": "0"
        })
        new_run = ET.SubElement(new_p, f"{HP}run", {"charPrIDRef": str(char_pr_id)})
        new_t = ET.SubElement(new_run, f"{HP}t")
        new_t.text = line

    # Also apply border, background, or cell dimensions if present
    _apply_style_to_tc(tc, header_root, op)


def apply_advanced_hwpx_operations(hwpx_bytes: bytes, operations: list) -> bytes:
    """Execute high-level table/cell editing commands on HWPX matching Hancom 2024 native spec."""
    _register_namespaces()

    with zipfile.ZipFile(io.BytesIO(hwpx_bytes), "r") as zin:
        entries = {info.filename: (info, zin.read(info.filename)) for info in zin.infolist()}

    sec0_info, sec0_bytes = entries["Contents/section0.xml"]
    root = ET.fromstring(sec0_bytes)

    header_info, header_bytes = entries.get("Contents/header.xml", (None, None))
    header_root = ET.fromstring(header_bytes) if header_bytes else None

    for op in operations:
        kind = op.get("op")

        if kind == "merge_cells":
            _, tbl = _find_table(root, op)
            trs = tbl.findall(f"{HP}tr")

            # Content-based cell resolution if text search provided
            start_row = op.get("start_row")
            start_col = op.get("start_col")
            row_span = op.get("row_span", 1)
            col_span = op.get("col_span", 1)

            # Look up by text if match_text / find_texts / text_contains specified
            search_texts = op.get("find_texts") or op.get("cell_texts") or []
            if op.get("match_text"):
                search_texts.append(op["match_text"])
            if op.get("from_text") and op.get("to_text"):
                search_texts.extend([op["from_text"], op["to_text"]])

            if search_texts:
                matched_coords = []
                for r_idx, tr in enumerate(trs):
                    for tc in tr.findall(f"{HP}tc"):
                        addr = tc.find(f"{HP}cellAddr")
                        c_text = _get_cell_text(tc)
                        if addr is not None:
                            r = int(addr.get("rowAddr"))
                            c = int(addr.get("colAddr"))
                            for st in search_texts:
                                if st and (st == c_text or st in c_text):
                                    matched_coords.append((r, c))
                                    break
                if matched_coords:
                    min_r = min(r for r, c in matched_coords)
                    max_r = max(r for r, c in matched_coords)
                    min_c = min(c for r, c in matched_coords)
                    max_c = max(c for r, c in matched_coords)
                    start_row = min_r
                    start_col = min_c
                    row_span = max_r - min_r + 1
                    col_span = max_c - min_c + 1

            if start_row is None or start_col is None:
                raise ValueError("merge_cells requires valid coordinates or matching cell texts")

            target_rows = range(start_row, start_row + row_span)
            target_cols = range(start_col, start_col + col_span)

            start_tc = None
            row_heights = {}
            col_widths = {}

            for r_idx in target_rows:
                if r_idx >= len(trs):
                    continue
                tr = trs[r_idx]
                for tc in tr.findall(f"{HP}tc"):
                    addr = tc.find(f"{HP}cellAddr")
                    if addr is not None:
                        r = int(addr.get("rowAddr"))
                        c = int(addr.get("colAddr"))
                        if r in target_rows and c in target_cols:
                            sz = tc.find(f"{HP}cellSz")
                            if sz is not None:
                                w = int(sz.get("width", "0"))
                                h = int(sz.get("height", "0"))
                                row_heights[r] = max(row_heights.get(r, 0), h)
                                col_widths[c] = max(col_widths.get(c, 0), w)
                            if r == start_row and c == start_col:
                                start_tc = tc

            if start_tc is None:
                raise ValueError(f"Start cell ({start_row}, {start_col}) not found")

            total_height = sum(row_heights.values())
            total_width = sum(col_widths.values())

            # Border / background fill
            border_spec = op.get("border")
            bg_color = op.get("background")
            if (border_spec or bg_color) and header_root is not None:
                new_bf_id = _ensure_border_fill(header_root, border_spec, bg_color)
                start_tc.set("borderFillIDRef", new_bf_id)
            elif op.get("border_fill"):
                start_tc.set("borderFillIDRef", str(op["border_fill"]))

            cellSpan = start_tc.find(f"{HP}cellSpan")
            if cellSpan is None:
                cellSpan = ET.SubElement(start_tc, f"{HP}cellSpan")
            cellSpan.set("colSpan", str(col_span))
            cellSpan.set("rowSpan", str(row_span))

            cellSz = start_tc.find(f"{HP}cellSz")
            if cellSz is None:
                cellSz = ET.SubElement(start_tc, f"{HP}cellSz")
            if total_width > 0:
                cellSz.set("width", str(total_width))
            if total_height > 0:
                cellSz.set("height", str(total_height))

            # Set text and typography
            text = op.get("text")
            if text is not None:
                subList = start_tc.find(f"{HP}subList")
                vert_align = op.get("vertical_align", "CENTER").upper()
                if subList is not None:
                    subList.set("vertAlign", vert_align)
                    for p in list(subList.findall(f"{HP}p")):
                        subList.remove(p)
                else:
                    subList = ET.SubElement(start_tc, f"{HP}subList", {
                        "id": "", "textDirection": "HORIZONTAL", "lineWrap": "BREAK", "vertAlign": vert_align,
                        "linkListIDRef": "0", "linkListNextIDRef": "0", "textWidth": "0", "textHeight": "0",
                        "hasTextRef": "0", "hasNumRef": "0"
                    })

                para_pr_id = "19"
                char_pr_id = "8"
                if header_root is not None:
                    if op.get("paragraph_style"):
                        para_pr_id = _ensure_para_pr(header_root, op["paragraph_style"])
                    elif op.get("align"):
                        para_pr_id = _ensure_para_pr(header_root, {"align": op["align"]})

                    if op.get("text_style"):
                        char_pr_id = _ensure_char_pr(header_root, op["text_style"])
                    elif any(k in op for k in ("font_size_pt", "size_pt", "bold", "letter_spacing", "color")):
                        char_pr_id = _ensure_char_pr(header_root, {
                            "size": op.get("font_size_pt", op.get("size_pt", 10.0)),
                            "bold": op.get("bold", False),
                            "letter_spacing": op.get("letter_spacing", 0),
                            "color": op.get("color", "#000000")
                        })

                new_p = ET.SubElement(subList, f"{HP}p", {
                    "id": "2147483648", "paraPrIDRef": str(para_pr_id), "styleIDRef": "0",
                    "pageBreak": "0", "columnBreak": "0", "merged": "0"
                })
                new_run = ET.SubElement(new_p, f"{HP}run", {"charPrIDRef": str(char_pr_id)})
                new_t = ET.SubElement(new_run, f"{HP}t")
                new_t.text = text

            # Remove subsumed cells from table
            for r_idx in target_rows:
                if r_idx >= len(trs):
                    continue
                tr = trs[r_idx]
                for tc in list(tr.findall(f"{HP}tc")):
                    addr = tc.find(f"{HP}cellAddr")
                    if addr is not None:
                        r = int(addr.get("rowAddr"))
                        c = int(addr.get("colAddr"))
                        if r in target_rows and c in target_cols:
                            if not (r == start_row and c == start_col):
                                tr.remove(tc)

        elif kind == "split_cell":
            _, tbl = _find_table(root, op)
            trs = tbl.findall(f"{HP}tr")

            # Resolve cell position by text or coordinates
            target_row = op.get("row")
            target_col = op.get("col")
            search_text = op.get("match_text") or op.get("find_text")

            if search_text:
                for r_idx, tr in enumerate(trs):
                    for tc in tr.findall(f"{HP}tc"):
                        addr = tc.find(f"{HP}cellAddr")
                        if addr is not None and search_text in _get_cell_text(tc):
                            target_row = int(addr.get("rowAddr"))
                            target_col = int(addr.get("colAddr"))
                            break
                    if target_row is not None:
                        break

            if target_row is None or target_col is None:
                raise ValueError("split_cell requires row/col or match_text finding target cell")

            split_rows = op.get("rows", 1)
            split_cols = op.get("cols", 1)
            if split_rows < 1 or split_cols < 1 or (split_rows == 1 and split_cols == 1):
                raise ValueError("split_cell requires at least 2 rows or 2 cols")

            tr = trs[target_row]
            target_tc = None
            for tc in tr.findall(f"{HP}tc"):
                addr = tc.find(f"{HP}cellAddr")
                if addr is not None and int(addr.get("colAddr")) == target_col:
                    target_tc = tc
                    break

            if target_tc is None:
                raise ValueError(f"Cell at ({target_row}, {target_col}) not found for split")

            # Original dimensions
            sz = target_tc.find(f"{HP}cellSz")
            orig_w = int(sz.get("width", "3000")) if sz is not None else 3000
            orig_h = int(sz.get("height", "2267")) if sz is not None else 2267

            sub_w = orig_w // split_cols
            sub_h = orig_h // split_rows

            target_attribs = dict(target_tc.attrib)
            texts_list = op.get("texts") or []

            # 1. Column split within same row
            if split_cols > 1 and split_rows == 1:
                tc_index = list(tr).index(target_tc)
                sz.set("width", str(sub_w))

                # If initial text provided for first sub-cell
                if texts_list:
                    t_elem = target_tc.find(f".//{HP}t")
                    if t_elem is not None:
                        t_elem.text = texts_list[0]

                # Create additional cells to the right
                for c_offset in range(1, split_cols):
                    new_tc = ET.Element(f"{HP}tc", target_attribs)
                    ET.SubElement(new_tc, f"{HP}cellAddr", {
                        "colAddr": str(target_col + c_offset),
                        "rowAddr": str(target_row)
                    })
                    ET.SubElement(new_tc, f"{HP}cellSpan", {"colSpan": "1", "rowSpan": "1"})
                    ET.SubElement(new_tc, f"{HP}cellSz", {"width": str(sub_w), "height": str(orig_h)})

                    subList = ET.SubElement(new_tc, f"{HP}subList", {
                        "id": "", "textDirection": "HORIZONTAL", "lineWrap": "BREAK", "vertAlign": "CENTER",
                        "linkListIDRef": "0", "linkListNextIDRef": "0", "textWidth": "0", "textHeight": "0",
                        "hasTextRef": "0", "hasNumRef": "0"
                    })
                    p = ET.SubElement(subList, f"{HP}p", {
                        "id": "2147483648", "paraPrIDRef": "19", "styleIDRef": "0",
                        "pageBreak": "0", "columnBreak": "0", "merged": "0"
                    })
                    run = ET.SubElement(p, f"{HP}run", {"charPrIDRef": "8"})
                    t = ET.SubElement(run, f"{HP}t")
                    if c_offset < len(texts_list):
                        t.text = texts_list[c_offset]

                    tr.insert(tc_index + c_offset, new_tc)

                # Re-index trailing column addresses in this row
                col_shift = split_cols - 1
                for tc in list(tr)[tc_index + split_cols:]:
                    addr = tc.find(f"{HP}cellAddr")
                    if addr is not None:
                        old_c = int(addr.get("colAddr"))
                        addr.set("colAddr", str(old_c + col_shift))

            # 2. Row split: adjust height of first cell and create sub-cells in subsequent rows
            elif split_rows > 1:
                sz.set("height", str(sub_h))
                if texts_list:
                    t_elem = target_tc.find(f".//{HP}t")
                    if t_elem is not None:
                        t_elem.text = texts_list[0]

                # Insert new rows or cells into existing rows
                for r_offset in range(1, split_rows):
                    insert_r_idx = target_row + r_offset
                    if insert_r_idx < len(trs):
                        dest_tr = trs[insert_r_idx]
                    else:
                        dest_tr = ET.SubElement(tbl, f"{HP}tr")
                        trs.append(dest_tr)

                    new_tc = ET.SubElement(dest_tr, f"{HP}tc", target_attribs)
                    ET.SubElement(new_tc, f"{HP}cellAddr", {
                        "colAddr": str(target_col),
                        "rowAddr": str(insert_r_idx)
                    })
                    ET.SubElement(new_tc, f"{HP}cellSpan", {"colSpan": "1", "rowSpan": "1"})
                    ET.SubElement(new_tc, f"{HP}cellSz", {"width": str(orig_w), "height": str(sub_h)})

                    subList = ET.SubElement(new_tc, f"{HP}subList", {
                        "id": "", "textDirection": "HORIZONTAL", "lineWrap": "BREAK", "vertAlign": "CENTER",
                        "linkListIDRef": "0", "linkListNextIDRef": "0", "textWidth": "0", "textHeight": "0",
                        "hasTextRef": "0", "hasNumRef": "0"
                    })
                    p = ET.SubElement(subList, f"{HP}p", {
                        "id": "2147483648", "paraPrIDRef": "19", "styleIDRef": "0",
                        "pageBreak": "0", "columnBreak": "0", "merged": "0"
                    })
                    run = ET.SubElement(p, f"{HP}run", {"charPrIDRef": "8"})
                    t = ET.SubElement(run, f"{HP}t")
                    if r_offset < len(texts_list):
                        t.text = texts_list[r_offset]

        elif kind == "set_cell_text":
            _, tbl = _find_table(root, op)
            trs = tbl.findall(f"{HP}tr")
            row, col = op.get("row"), op.get("col")
            search_text = op.get("match_text") or op.get("find_text")

            target_tc = None
            if search_text:
                for r_idx, tr in enumerate(trs):
                    for tc in tr.findall(f"{HP}tc"):
                        if search_text in _get_cell_text(tc):
                            target_tc = tc
                            row, col = r_idx, int(tc.find(f"{HP}cellAddr").get("colAddr"))
                            break
                    if target_tc is not None:
                        break
            elif row is not None and col is not None and 0 <= row < len(trs):
                tr = trs[row]
                for tc in tr.findall(f"{HP}tc"):
                    addr = tc.find(f"{HP}cellAddr")
                    if addr is not None and int(addr.get("colAddr")) == col:
                        target_tc = tc
                        break

            if target_tc is not None:
                text_to_set = op.get("text", "")
                _set_tc_text(target_tc, header_root, text_to_set, op)
            else:
                raise ValueError(f"Target cell for set_cell_text not found (row={row}, col={col}, match_text={search_text})")

        elif kind == "set_cell_style":
            _, tbl = _find_table(root, op)
            trs = tbl.findall(f"{HP}tr")
            apply_to = str(op.get("apply_to", "cell")).lower()
            target_cells = []

            if apply_to == "header" and trs:
                # Apply to all cells in first row (row 0)
                target_cells.extend(trs[0].findall(f"{HP}tc"))
            elif apply_to == "row" and op.get("row") is not None:
                r_idx = op["row"]
                if 0 <= r_idx < len(trs):
                    target_cells.extend(trs[r_idx].findall(f"{HP}tc"))
            elif apply_to == "col" and op.get("col") is not None:
                c_idx = op["col"]
                for tr in trs:
                    for tc in tr.findall(f"{HP}tc"):
                        addr = tc.find(f"{HP}cellAddr")
                        if addr is not None and int(addr.get("colAddr")) == c_idx:
                            target_cells.append(tc)
            elif apply_to == "all":
                for tr in trs:
                    target_cells.extend(tr.findall(f"{HP}tc"))
            else:
                # Target single cell by coordinates or text
                row, col = op.get("row"), op.get("col")
                search_text = op.get("match_text") or op.get("find_text")
                if search_text:
                    for r_i, tr in enumerate(trs):
                        for tc in tr.findall(f"{HP}tc"):
                            if search_text in _get_cell_text(tc):
                                target_cells.append(tc)
                                break
                        if target_cells:
                            break
                elif row is not None and col is not None and 0 <= row < len(trs):
                    tr = trs[row]
                    for tc in tr.findall(f"{HP}tc"):
                        addr = tc.find(f"{HP}cellAddr")
                        if addr is not None and int(addr.get("colAddr")) == col:
                            target_cells.append(tc)
                            break

            for tc in target_cells:
                _apply_style_to_tc(tc, header_root, op)

        elif kind == "modify_table":
            _, tbl = _find_table(root, op)

            # Table positioning (treatAsChar, horzAlign)
            pos = tbl.find(f"{HP}pos")
            if pos is not None:
                if "treat_as_char" in op:
                    tac_val = "1" if op["treat_as_char"] in (True, 1, "1", "true", "True") else "0"
                    pos.set("treatAsChar", tac_val)
                if "align" in op:
                    align_val = op["align"].upper()
                    pos.set("horzAlign", align_val)

            # Table size
            sz = tbl.find(f"{HP}sz")
            if sz is not None:
                if "width_mm" in op:
                    sz.set("width", str(round(op["width_mm"] * 283.465)))
                if "height_mm" in op:
                    sz.set("height", str(round(op["height_mm"] * 283.465)))

            # Table margins
            if "out_margin_mm" in op:
                om = op["out_margin_mm"]
                outMargin = tbl.find(f"{HP}outMargin")
                if outMargin is None:
                    outMargin = ET.SubElement(tbl, f"{HP}outMargin")
                outMargin.set("left", str(round(om[0] * 283.465)))
                outMargin.set("right", str(round(om[1] * 283.465)))
                outMargin.set("top", str(round(om[2] * 283.465)))
                outMargin.set("bottom", str(round(om[3] * 283.465)))

            if "in_margin_mm" in op:
                im = op["in_margin_mm"]
                inMargin = tbl.find(f"{HP}inMargin")
                if inMargin is None:
                    inMargin = ET.SubElement(tbl, f"{HP}inMargin")
                inMargin.set("left", str(round(im[0] * 283.465)))
                inMargin.set("right", str(round(im[1] * 283.465)))
                inMargin.set("top", str(round(im[2] * 283.465)))
                inMargin.set("bottom", str(round(im[3] * 283.465)))

        elif kind == "modify_image":
            pics = root.findall(f".//{HP}pic")
            img_idx = op.get("image_index", 0)
            if not pics or img_idx >= len(pics):
                raise ValueError(f"Image index {img_idx} out of range (found {len(pics)} pictures)")
            pic = pics[img_idx]

            # Positioning
            pos = pic.find(f"{HP}pos")
            if pos is not None:
                if "treat_as_char" in op:
                    tac_val = "1" if op["treat_as_char"] in (True, 1, "1", "true", "True") else "0"
                    pos.set("treatAsChar", tac_val)
                if "align" in op:
                    pos.set("horzAlign", op["align"].upper())

            # Dimensions
            sz = pic.find(f"{HP}sz")
            if sz is not None:
                if "width_mm" in op:
                    sz.set("width", str(round(op["width_mm"] * 283.465)))
                if "height_mm" in op:
                    sz.set("height", str(round(op["height_mm"] * 283.465)))

            # Margin
            if "out_margin_mm" in op:
                om = op["out_margin_mm"]
                outMargin = pic.find(f"{HP}outMargin")
                if outMargin is None:
                    outMargin = ET.SubElement(pic, f"{HP}outMargin")
                outMargin.set("left", str(round(om[0] * 283.465)))
                outMargin.set("right", str(round(om[1] * 283.465)))
                outMargin.set("top", str(round(om[2] * 283.465)))
                outMargin.set("bottom", str(round(om[3] * 283.465)))

            # Replace image binary data if requested
            replace_path = op.get("replace_image_path")
            replace_data = op.get("replace_image_data")
            if replace_path or replace_data:
                new_img_bytes = Path(replace_path).read_bytes() if replace_path else replace_data
                img_node = pic.find(f".//{HP}img")
                if img_node is not None:
                    bin_ref = img_node.get("binaryItemIDRef")
                    # Locate entry in zip entries matching bin_ref
                    for entry_name, (info, data) in list(entries.items()):
                        if bin_ref and bin_ref in entry_name:
                            entries[entry_name] = (info, new_img_bytes)
                            break

        elif kind == "insert_row":
            _, tbl = _find_table(root, op)
            trs = tbl.findall(f"{HP}tr")
            pos = op.get("row_index", len(trs))
            ref_tr = trs[min(pos, len(trs) - 1)]
            new_tr = ET.Element(f"{HP}tr")
            height = op.get("height_mm", 8)
            height_hwp = round(height * 283.465)

            col_idx = 0
            for ref_tc in ref_tr.findall(f"{HP}tc"):
                new_tc = ET.SubElement(new_tr, f"{HP}tc", dict(ref_tc.attrib))
                subList = ET.SubElement(new_tc, f"{HP}subList", {
                    "id": "", "textDirection": "HORIZONTAL", "lineWrap": "BREAK", "vertAlign": "CENTER",
                    "linkListIDRef": "0", "linkListNextIDRef": "0", "textWidth": "0", "textHeight": "0",
                    "hasTextRef": "0", "hasNumRef": "0"
                })
                p = ET.SubElement(subList, f"{HP}p", {
                    "id": "2147483648", "paraPrIDRef": "19", "styleIDRef": "0",
                    "pageBreak": "0", "columnBreak": "0", "merged": "0"
                })
                ET.SubElement(p, f"{HP}run", {"charPrIDRef": "8"})

                ET.SubElement(new_tc, f"{HP}cellAddr", {"colAddr": str(col_idx), "rowAddr": str(pos)})
                ET.SubElement(new_tc, f"{HP}cellSpan", {"colSpan": "1", "rowSpan": "1"})
                ref_sz = ref_tc.find(f"{HP}cellSz")
                w_str = ref_sz.get("width") if ref_sz is not None else "3000"
                ET.SubElement(new_tc, f"{HP}cellSz", {"width": w_str, "height": str(height_hwp)})
                ref_m = ref_tc.find(f"{HP}cellMargin")
                if ref_m is not None:
                    ET.SubElement(new_tc, f"{HP}cellMargin", dict(ref_m.attrib))
                col_idx += 1

            tbl.insert(list(tbl).index(ref_tr) + (1 if pos >= len(trs) else 0), new_tr)
            all_trs = tbl.findall(f"{HP}tr")
            for r_i, r_tr in enumerate(all_trs):
                for tc in r_tr.findall(f"{HP}tc"):
                    addr = tc.find(f"{HP}cellAddr")
                    if addr is not None:
                        addr.set("rowAddr", str(r_i))

        elif kind == "delete_row":
            _, tbl = _find_table(root, op)
            trs = tbl.findall(f"{HP}tr")
            target_idx = op["row_index"]
            if 0 <= target_idx < len(trs):
                tbl.remove(trs[target_idx])
                all_trs = tbl.findall(f"{HP}tr")
                for r_i, r_tr in enumerate(all_trs):
                    for tc in r_tr.findall(f"{HP}tc"):
                        addr = tc.find(f"{HP}cellAddr")
                        if addr is not None:
                            addr.set("rowAddr", str(r_i))

    new_sec0_xml = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    new_header_xml = ET.tostring(header_root, encoding="utf-8", xml_declaration=True) if header_root is not None else None

    out_stream = io.BytesIO()
    with zipfile.ZipFile(out_stream, "w") as zout:
        if "mimetype" in entries:
            minfo, mdata = entries["mimetype"]
            new_info = zipfile.ZipInfo("mimetype")
            new_info.compress_type = zipfile.ZIP_STORED
            zout.writestr(new_info, mdata)

        for fname, (info, data) in entries.items():
            if fname == "mimetype":
                continue
            new_info = zipfile.ZipInfo(fname)
            new_info.compress_type = info.compress_type
            new_info.comment = info.comment

            if fname == "Contents/section0.xml":
                zout.writestr(new_info, new_sec0_xml)
            elif fname == "Contents/header.xml" and new_header_xml is not None:
                zout.writestr(new_info, new_header_xml)
            else:
                zout.writestr(new_info, data)

    return out_stream.getvalue()
