"""AI Natural Language Command Parser and Automation Executor for Hancom Table Operations.

Interprets natural language instructions such as:
- "'항목A'와 '항목B'가 있는 셀을 병합해서 '통합 항목'이라고 적어줘"
- "'비고' 셀을 2행 1열로 분할하고 각각 '비고1', '비고2'를 적어줘"
- "'비고' 셀 내용을 '해당 사항 없음'으로 변경하고 글자 굵게 해줘"
- "1행 1열 셀에 '추진 개요'라고 적고 배경색 #F0F4F8로 지정해줘"
- "헤더 행 전체 글자 굵게 하고 가운데 정렬해줘"
- "'비고' 셀 테두리 없애줘"
- "표를 가운데 정렬하고 글자처럼 취급해줘"
- "그림을 가운데 정렬하고 글자처럼 취급해줘"
and applies them directly to HWP/HWPX documents conforming to Hancom 2024 specifications (REA).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from .hwpx_advanced import apply_advanced_hwpx_operations
from .hwp_automation_bridge import patch_hwp_advanced

def _extract_style_properties(cmd: str) -> dict[str, Any]:
    """Extract styling parameters (bold, font_size, align, background, border) from text."""
    props: dict[str, Any] = {}

    if re.search(r"(굵게|볼드|진하게|두껍게)", cmd):
        props["bold"] = True

    if re.search(r"(가운데|중앙)\s*(?:정렬|맞춤)?", cmd):
        props["align"] = "CENTER"
    elif re.search(r"(왼쪽|좌측)\s*(?:정렬|맞춤)?", cmd):
        props["align"] = "LEFT"
    elif re.search(r"(오른쪽|우측)\s*(?:정렬|맞춤)?", cmd):
        props["align"] = "RIGHT"

    size_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:pt|포인트)", cmd)
    if size_match:
        props["font_size_pt"] = float(size_match.group(1))

    bg_match = re.search(r"배경(?:색)?(?:은|\s*:\s*|\s+)?(#[0-9a-fA-F]{6})", cmd)
    if bg_match:
        props["background"] = bg_match.group(1)
    else:
        # Check if generic hex color appears with background keyword
        hex_match = re.search(r"(#[0-9a-fA-F]{6})", cmd)
        if hex_match and "배경" in cmd:
            props["background"] = hex_match.group(1)
        elif hex_match and ("글자" in cmd or "색" in cmd):
            props["color"] = hex_match.group(1)

    # Border rules
    if re.search(r"테두리.*?(?:없애|투명|제거|없음|숨겨)", cmd) or re.search(r"(?:외곽선|테두리)\s*없음", cmd):
        props["border"] = {"type": "NONE"}
    elif re.search(r"하단.*?(?:이중선|이중실선)", cmd):
        props["border"] = {"bottom": {"type": "DOUBLE_SLIM", "width": "0.5 mm"}}
    elif re.search(r"상단.*?(?:이중선|이중실선)", cmd):
        props["border"] = {"top": {"type": "DOUBLE_SLIM", "width": "0.5 mm"}}
    elif re.search(r"(?:좌우|양쪽).*?(?:투명|없애|제거)", cmd):
        props["border"] = {"left": "NONE", "right": "NONE"}
    elif re.search(r"(?:이중선|이중실선)", cmd):
        props["border"] = {"type": "DOUBLE_SLIM", "width": "0.5 mm"}
    elif re.search(r"(?:실선|기본선)", cmd):
        props["border"] = {"type": "SOLID", "width": "0.12 mm"}

    return props

def parse_single_command(cmd: str) -> dict[str, Any] | None:
    """Parse a single sentence into a structured operation dict."""
    cmd = cmd.strip()
    if not cmd:
        return None

    styles = _extract_style_properties(cmd)

    # 1. Modify Table properties (Align, Treat as char, Width)
    if re.search(r"^표\s*(?:를|가|\s)?.*?(?:정렬|취급|너비|폭|여백)", cmd):
        op: dict[str, Any] = {"op": "modify_table"}
        if "align" in styles:
            op["align"] = styles["align"]
        if re.search(r"글자처럼\s*취급", cmd):
            op["treat_as_char"] = True
        w_match = re.search(r"너비\s*(\d+(?:\.\d+)?)\s*(?:mm)?", cmd)
        if w_match:
            op["width_mm"] = float(w_match.group(1))
        return op

    # 2. Modify Image properties (Align, Treat as char, Width, Height)
    if re.search(r"^(?:그림|이미지|사진)\s*(?:를|가|\s)?.*?(?:정렬|취급|너비|폭|크기)", cmd):
        op = {"op": "modify_image"}
        if "align" in styles:
            op["align"] = styles["align"]
        if re.search(r"글자처럼\s*취급", cmd):
            op["treat_as_char"] = True
        w_match = re.search(r"너비\s*(\d+(?:\.\d+)?)\s*(?:mm)?", cmd)
        if w_match:
            op["width_mm"] = float(w_match.group(1))
        h_match = re.search(r"높이\s*(\d+(?:\.\d+)?)\s*(?:mm)?", cmd)
        if h_match:
            op["height_mm"] = float(h_match.group(1))
        return op

    # 3. Row Insert / Delete
    if re.search(r"행\s*(?:추가|삽입)", cmd):
        row_idx_match = re.search(r"(\d+)번째\s*행", cmd)
        row_idx = int(row_idx_match.group(1)) - 1 if row_idx_match else 9999
        return {"op": "insert_row", "row_index": row_idx}

    if re.search(r"(\d+)번째\s*행\s*(?:삭제|제거)", cmd):
        del_match = re.search(r"(\d+)번째\s*행", cmd)
        if del_match:
            return {"op": "delete_row", "row_index": int(del_match.group(1)) - 1}

    # 4. Cell Merge
    merge_match = re.search(r"(?:['\"]([^'\"]+)['\"]|(\S+))\s*(?:와|과|,)\s*(?:['\"]([^'\"]+)['\"]|(\S+))\s*(?:가|이|\s)*(?:있는\s*)?셀(?:을|를)?\s*(?:병합|합치)", cmd)
    if merge_match:
        from_text = (merge_match.group(1) or merge_match.group(2)).strip()
        to_text = (merge_match.group(3) or merge_match.group(4)).strip()

        text_match = re.search(r"['\"]([^'\"]+)['\"](?:\s*(?:이?라고|으로)?\s*(?:적어|입력|써|변경|기입))", cmd)
        replacement_text = text_match.group(1) if text_match else None

        op = {
            "op": "merge_cells",
            "from_text": from_text,
            "to_text": to_text,
            "text": replacement_text,
        }
        op.update(styles)
        return op

    # Coordinate-based merge: "1행 1열부터 1행 3열까지 병합"
    coord_merge = re.search(r"(\d+)행\s*(\d+)열(?:부터|\s*(?:과|와))\s*(\d+)행\s*(\d+)열.*?(?:병합|합치)", cmd)
    if coord_merge:
        r1, c1, r2, c2 = (int(coord_merge.group(i)) for i in range(1, 5))
        sr = min(r1, r2) - 1 if min(r1, r2) > 0 else 0
        sc = min(c1, c2) - 1 if min(c1, c2) > 0 else 0
        er = max(r1, r2) - 1 if max(r1, r2) > 0 else 0
        ec = max(c1, c2) - 1 if max(c1, c2) > 0 else 0

        text_match = re.search(r"['\"]([^'\"]+)['\"]", cmd)
        op = {
            "op": "merge_cells",
            "start_row": sr,
            "start_col": sc,
            "row_span": er - sr + 1,
            "col_span": ec - sc + 1,
            "text": text_match.group(1) if text_match else None
        }
        op.update(styles)
        return op

    # 5. Cell Split: "'A' 셀을 N행 M열로 분할"
    split_match = re.search(r"(?:['\"]([^'\"]+)['\"]|(\S+))\s*(?:가|이|\s)*(?:있는\s*)?셀(?:을|를)?\s*(?:(\d+)행)?\s*(?:(\d+)열)?(?:\s*개)?(?:\s*으?로)?\s*(?:분할|나눠|쪼개)", cmd)
    if split_match:
        target_text = (split_match.group(1) or split_match.group(2)).strip()
        rows_str = split_match.group(3)
        cols_str = split_match.group(4)

        rows = int(rows_str) if rows_str else 1
        cols = int(cols_str) if cols_str else 1
        if rows == 1 and cols == 1:
            if "행" in cmd and not cols_str:
                rows = 2
            elif "열" in cmd and not rows_str:
                cols = 2
            else:
                rows = 2

        texts = re.findall(r"['\"]([^'\"]+)['\"]", cmd)
        sub_texts = [t for t in texts if t != target_text]

        return {
            "op": "split_cell",
            "match_text": target_text,
            "rows": rows,
            "cols": cols,
            "texts": sub_texts if sub_texts else None
        }

    # 6. Set Cell Text (내용 지정 / 내용 변경 / 텍스트 입력)
    # Examples:
    # - "'비고' 셀 내용 '특이사항 없음'으로 변경해줘"
    # - "'구분' 셀에 '항목'이라고 적어줘"
    # - "1행 1열 셀에 '제목' 입력해줘"
    text_set_match = re.search(r"['\"]([^'\"]+)['\"](?:\s*(?:이?라고|으로)?\s*(?:적어|입력|써|변경|지정|바꿔|채워))", cmd)
    if text_set_match:
        new_text = text_set_match.group(1)
        # Check coordinate target
        coord_target = re.search(r"(\d+)행\s*(\d+)열", cmd)
        # Check text target
        cell_target = re.search(r"['\"]([^'\"]+)['\"]\s*(?:있는\s*)?셀", cmd)

        op = {"op": "set_cell_text", "text": new_text}
        op.update(styles)

        if coord_target:
            op["row"] = int(coord_target.group(1)) - 1
            op["col"] = int(coord_target.group(2)) - 1
            return op
        elif cell_target and cell_target.group(1) != new_text:
            op["match_text"] = cell_target.group(1)
            return op
        else:
            # Fallback text match from preceding word
            pre_match = re.search(r"(\S+)\s*셀", cmd)
            if pre_match and pre_match.group(1) != "있는":
                op["match_text"] = pre_match.group(1).strip("'\"")
                return op

    # 7. Set Cell Style (헤더 행, 특정 행/열, 단일 셀 스타일링)
    # Examples:
    # - "헤더 행 전체 글자 굵게 하고 가운데 정렬"
    # - "1행 전체 배경색 #F0F4F8로 해줘"
    # - "'비고' 셀 글자 굵게 해줘"
    if styles:
        op = {"op": "set_cell_style"}
        op.update(styles)

        if re.search(r"(?:헤더|제목행|첫\s*번째\s*행)", cmd):
            op["apply_to"] = "header"
            return op

        row_all = re.search(r"(\d+)행\s*(?:전체)?", cmd)
        if row_all and "열" not in cmd:
            op["apply_to"] = "row"
            op["row"] = int(row_all.group(1)) - 1
            return op

        col_all = re.search(r"(\d+)열\s*(?:전체)?", cmd)
        if col_all and "행" not in cmd:
            op["apply_to"] = "col"
            op["col"] = int(col_all.group(1)) - 1
            return op

        if re.search(r"표\s*(?:전체|모든\s*셀)", cmd):
            op["apply_to"] = "all"
            return op

        # Coordinate single cell
        coord = re.search(r"(\d+)행\s*(\d+)열", cmd)
        if coord:
            op["row"] = int(coord.group(1)) - 1
            op["col"] = int(coord.group(2)) - 1
            return op

        # Named cell match
        named_cell = re.search(r"['\"]?([^'\"\s]+)['\"]?\s*(?:있는\s*)?셀", cmd)
        if named_cell:
            op["match_text"] = named_cell.group(1).strip("'\"")
            return op

    return None

def parse_ai_command(command: str) -> list[dict[str, Any]]:
    """Parse natural language command (including multiple joined clauses) into structured operations."""
    cmd = command.strip()
    if not cmd:
        return []

    # Split compound clauses by semicolon, newline, or Korean conjunctions
    clauses = re.split(r"[;\n]|\s*(?:그리고|한\s*뒤|다음에|하고\s*나서)\s*", cmd)
    operations: list[dict[str, Any]] = []

    for clause in clauses:
        clause = clause.strip()
        if not clause:
            continue
        op = parse_single_command(clause)
        if op:
            operations.append(op)

    # Fallback to entire string if clause splitting yielded nothing
    if not operations:
        single = parse_single_command(cmd)
        if single:
            operations.append(single)

    return operations

def execute_ai_table_command(
    file_path: str | Path,
    command: str | None = None,
    operations: list[dict[str, Any]] | None = None,
    output_path: str | Path | None = None
) -> dict[str, Any]:
    """Execute natural language command or explicit JSON operations on target HWP/HWPX file."""
    src = Path(file_path).resolve()
    dst = Path(output_path).resolve() if output_path else src

    if not src.is_file():
        return {"success": False, "error": f"File not found: {src}"}

    ops = operations if operations is not None else (parse_ai_command(command) if command else [])
    if not ops:
        return {
            "success": False,
            "error": f"Could not parse table action from command: '{command}'. Expected commands like 'A와 B 셀을 병합해서 C 적어줘', '비고 셀 내용 OOO으로 변경해줘', or '헤더 행 굵게 해줘'"
        }

def apply_native_hwp_operations(src_path: Path, ops: list[dict[str, Any]], dst_path: Path) -> bool:
    """Execute advanced table and object operations directly on native HWP 5.0 documents without COM."""
    from .formats import load_document, save_hwp, Table, Cell, Paragraph, Run, TextStyle, ParaStyle
    from .geometry import Border, PictureGeometry
    from .automation import _walk_model, paragraph_text

    loaded = load_document(src_path)
    model = loaded.model
    if not model:
        return False

    tables = [b for _, b in _walk_model(model) if isinstance(b, Table)]

    for op in ops:
        kind = op.get("op")
        tbl_idx = op.get("table_index", 0)

        # Target table resolution
        tbl = None
        if op.get("target_table"):
            m = re.search(r"(\d+)", op["target_table"])
            if m and int(m.group(1)) < len(tables):
                tbl = tables[int(m.group(1))]
        if tbl is None and tbl_idx < len(tables):
            tbl = tables[tbl_idx]

        # Content match fallback if search_text given
        if tbl is None or op.get("match_text"):
            search = op.get("match_text") or op.get("find_text")
            if search:
                for candidate in tables:
                    for c in candidate.cells:
                        c_txt = " ".join([paragraph_text(b) for b in c.blocks if isinstance(b, Paragraph)])
                        if search in c_txt:
                            tbl = candidate
                            break
                    if tbl is candidate:
                        break

        if tbl is None and tables:
            tbl = tables[0]

        if tbl is None:
            continue

        if kind == "set_cell_text":
            target_cell = None
            search = op.get("match_text") or op.get("find_text")
            if search:
                for c in tbl.cells:
                    c_txt = " ".join([paragraph_text(b) for b in c.blocks if isinstance(b, Paragraph)])
                    if search in c_txt:
                        target_cell = c
                        break
            elif op.get("row") is not None and op.get("col") is not None:
                for c in tbl.cells:
                    if c.row == op["row"] and c.col == op["col"]:
                        target_cell = c
                        break

            if target_cell:
                align_val = op.get("align", "CENTER")
                bold_val = op.get("bold", False)
                font_size = op.get("font_size_pt", 10.0)
                txt = op.get("text", "")
                p_style = ParaStyle(align=align_val)
                t_style = TextStyle(bold=bold_val, size=font_size)
                if op.get("append", False):
                    target_cell.blocks.append(Paragraph([Run(txt, style=t_style)], style=p_style))
                else:
                    target_cell.blocks = [Paragraph([Run(txt, style=t_style)], style=p_style)]

                if op.get("background"):
                    target_cell.background = op["background"]

        elif kind == "set_cell_style":
            apply_to = str(op.get("apply_to", "cell")).lower()
            target_cells = []
            if apply_to == "all":
                target_cells = list(tbl.cells)
            elif apply_to == "header":
                target_cells = [c for c in tbl.cells if c.row == 0]
            elif apply_to == "row" and op.get("row") is not None:
                target_cells = [c for c in tbl.cells if c.row == op["row"]]
            elif apply_to == "col" and op.get("col") is not None:
                target_cells = [c for c in tbl.cells if c.col == op["col"]]
            else:
                search = op.get("match_text") or op.get("find_text")
                if search:
                    for c in tbl.cells:
                        c_txt = " ".join([paragraph_text(b) for b in c.blocks if isinstance(b, Paragraph)])
                        if search in c_txt:
                            target_cells.append(c)
                            break
                elif op.get("row") is not None and op.get("col") is not None:
                    target_cells = [c for c in tbl.cells if c.row == op["row"] and c.col == op["col"]]

            border_spec = op.get("border")
            for c in target_cells:
                if border_spec:
                    b_type = str(border_spec.get("type", "SOLID")).upper()
                    if b_type == "NONE":
                        c.borders = (Border(style="NONE", width_pt=0, color="#000000"),) * 4
                    elif b_type == "DOUBLE_SLIM":
                        c.borders = (Border(style="DOUBLE_SLIM", width_pt=0.5 * 72 / 25.4, color=border_spec.get("color", "#000000")),) * 4
                    elif b_type == "SOLID":
                        c.borders = (Border(style="SOLID", width_pt=0.12 * 72 / 25.4, color=border_spec.get("color", "#000000")),) * 4

                if op.get("background"):
                    c.background = op["background"]

                if op.get("bold") is not None or op.get("font_size_pt") or op.get("align"):
                    for b in c.blocks:
                        if isinstance(b, Paragraph):
                            if op.get("align"):
                                b.style.align = op["align"]
                            for r in b.runs:
                                if op.get("bold") is not None:
                                    r.style.bold = op["bold"]
                                if op.get("font_size_pt"):
                                    r.style.size = op["font_size_pt"]

        elif kind == "delete_row":
            del_idx = op.get("row_index", tbl.rows - 1)
            if 0 <= del_idx < tbl.rows:
                tbl.cells = [c for c in tbl.cells if c.row != del_idx]
                for c in tbl.cells:
                    if c.row > del_idx:
                        c.row -= 1
                tbl.rows -= 1
                if tbl.row_heights and len(tbl.row_heights) > tbl.rows:
                    tbl.row_heights = tbl.row_heights[:tbl.rows]

        elif kind == "insert_row":
            pos = op.get("row_index", tbl.rows)
            ref_row = min(pos - 1, tbl.rows - 1) if tbl.rows > 0 else 0
            ref_cells = [c for c in tbl.cells if c.row == ref_row]
            new_h = op.get("height_mm", 10) * 72 / 25.4
            for c in tbl.cells:
                if c.row >= pos:
                    c.row += 1
            for rc in ref_cells:
                new_c = Cell(
                    row=pos, col=rc.col, row_span=1, col_span=rc.col_span,
                    blocks=[Paragraph([Run("")], style=ParaStyle(align="CENTER"))],
                    width=rc.width, height=new_h, background=rc.background,
                    padding=rc.padding, borders=rc.borders, vertical_align="CENTER"
                )
                tbl.cells.append(new_c)
            tbl.rows += 1
            tbl.row_heights = list(tbl.row_heights) + [new_h]

        elif kind in ("insert_image", "insert_photo"):
            target_cell = None
            search = op.get("match_text")
            if search:
                for c in tbl.cells:
                    c_txt = " ".join([paragraph_text(b) for b in c.blocks if isinstance(b, Paragraph)])
                    if search in c_txt:
                        target_cell = c
                        break
            elif op.get("row") is not None and op.get("col") is not None:
                for c in tbl.cells:
                    if c.row == op["row"] and c.col == op["col"]:
                        target_cell = c
                        break

            img_bytes = op.get("image_bytes")
            img_path = op.get("image_path")
            if img_path and Path(img_path).is_file():
                img_bytes = Path(img_path).read_bytes()

            if target_cell and img_bytes:
                w_mm = op.get("width_mm", target_cell.width * 25.4 / 72 * 0.9)
                h_mm = op.get("height_mm", target_cell.height * 25.4 / 72 * 0.9)
                w_pt = w_mm * 72 / 25.4
                h_pt = h_mm * 72 / 25.4
                p_run = Run(
                    image=img_bytes, image_ext="png",
                    width=w_pt * 96 / 72, height=h_pt * 96 / 72,
                    picture=PictureGeometry(original_width_pt=w_pt, original_height_pt=h_pt)
                )
                target_cell.blocks = [Paragraph([p_run], style=ParaStyle(align="CENTER", line=100, before=0, after=0))]

    save_hwp(model, dst_path)
    return True

def execute_ai_table_command(
    file_path: str | Path,
    command: str | None = None,
    operations: list[dict[str, Any]] | None = None,
    output_path: str | Path | None = None
) -> dict[str, Any]:
    """Execute natural language command or explicit JSON operations on target HWP/HWPX file."""
    src = Path(file_path).resolve()
    dst = Path(output_path).resolve() if output_path else src

    if not src.is_file():
        return {"success": False, "error": f"File not found: {src}"}

    ops = operations if operations is not None else (parse_ai_command(command) if command else [])
    if not ops:
        return {
            "success": False,
            "error": f"Could not parse table action from command: '{command}'. Expected commands like 'A와 B 셀을 병합해서 C 적어줘', '비고 셀 내용 OOO으로 변경해줘', or '헤더 행 굵게 해줘'"
        }

    ext = src.suffix.lower()
    try:
        # HWP source editing must not reconstruct a reduced model: it can
        # discard fields, controls, or unsupported formatting. Prefer the
        # checked native Hancom route for both formats when it is installed.
        use_hancom = ext == ".hwp"
        if ext == ".hwpx" and sys.platform == "win32":
            try:
                import pythoncom
            except ImportError:
                pass
            else:
                try:
                    pythoncom.CLSIDFromProgID("HWPFrame.HwpObject")
                    use_hancom = True
                except pythoncom.com_error:
                    pass
        if use_hancom:
            report = patch_hwp_advanced(src, dst, ops)
            return {"success": True, "format": ext[1:], "file": str(dst), "command": command,
                    "operations_applied": ops, **report}
        if ext == ".hwpx":
            hwpx_bytes = src.read_bytes()
            result_bytes = apply_advanced_hwpx_operations(hwpx_bytes, ops)
            dst.write_bytes(result_bytes)
            return {
                "success": True,
                "format": "hwpx",
                "file": str(dst),
                "command": command,
                "operations_applied": ops
            }
        else:
            return {"success": False, "error": f"Unsupported file extension: {ext}"}
    except Exception as e:
        return {"success": False, "error": str(e), "operations_attempted": ops}

def main():
    parser = argparse.ArgumentParser(description="Hancom Table & Object AI Natural Language Commander")
    parser.add_argument("--file", required=True, help="Path to HWP or HWPX file")
    parser.add_argument("--command", help="Natural language command (e.g. 'A와 B 셀을 병합하고 C 적어줘')")
    parser.add_argument("--ops-json", help="Direct JSON string or path to JSON file containing operations array")
    parser.add_argument("--output", help="Output file path (defaults to overwrite source)")
    args = parser.parse_args()

    ops = None
    if args.ops_json:
        if Path(args.ops_json).is_file():
            ops = json.loads(Path(args.ops_json).read_text(encoding="utf-8"))
        else:
            ops = json.loads(args.ops_json)

    res = execute_ai_table_command(args.file, command=args.command, operations=ops, output_path=args.output)
    print(json.dumps(res, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
