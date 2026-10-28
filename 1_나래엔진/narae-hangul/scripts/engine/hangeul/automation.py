"""JSON automation for native HWP writing and conservative source editing.

No desktop UI or Hancom runtime is required. Original documents are always
patched through their native preservation writer; reconstruction is never an
implicit fallback. Dimensions in jobs are millimetres, font sizes are points.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, replace
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unicodedata

from .formats import (Cell, Drawing, Paragraph, ParaStyle, Run, Table, TextStyle,
                      load_document, save_hwp, save_hwpx)
from .geometry import DocumentModel, PageLayout, PictureGeometry
from .native_edit import CHAR_FIELDS, PARA_FIELDS, paragraph_text, replace_paragraph_text

SCHEMA = 1
_APP = None
_LIMITATIONS = [
    "한컴과 동일한 전체 기능·쪽 나눔·픽셀 배치는 아직 검증되지 않았습니다.",
    "독립 보존 편집에는 지원 범위가 있습니다. 고급 HWP 표·그림 편집에는 Windows 한컴 COM이 필요합니다.",
    "HWP 3.0 원본은 표현 가능한 텍스트만 보존 편집하며 서식 변경은 거부합니다.",
]


def _object(value, label):
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _keys(value, allowed, label):
    _object(value, label)
    unknown = set(value) - set(allowed)
    if unknown:
        raise ValueError(f"Unsupported {label} keys: {', '.join(sorted(unknown))}")


def _number(value, label, minimum=0, maximum=10000):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{label} must be a finite number")
    if not minimum <= value <= maximum:
        raise ValueError(f"{label} must be between {minimum} and {maximum}")
    return float(value)


def _integer(value, label, minimum=0, maximum=10000):
    number = _number(value, label, minimum, maximum)
    if number != int(number):
        raise ValueError(f"{label} must be an integer")
    return int(number)


def _text(value, label="text"):
    if not isinstance(value, str):
        raise ValueError(f"{label} must be text")
    if any(unicodedata.category(character) == "Cc" and character not in "\t\n" for character in value):
        raise ValueError(f"{label} contains unsupported control characters")
    return value


def _mm(value, label):
    return _number(value, label) * 72 / 25.4


def _path(value, base, label):
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a nonempty path")
    path = Path(value).expanduser()
    return (base / path).resolve() if not path.is_absolute() else path.resolve()


def _bools(value, names, label):
    for name in names:
        if name in value and not isinstance(value[name], bool):
            raise ValueError(f"{label}.{name} must be true or false")


def _color(value, label, empty=False):
    import re
    if not (empty and value == "") and not re.fullmatch(r"#[0-9a-fA-F]{6}", _text(value, label)):
        raise ValueError(f"{label} must be #RRGGBB")
    return value


def _style(value, original=None, paragraph=False):
    value = dict(_object(value, "paragraph_style" if paragraph else "text_style"))
    allowed = PARA_FIELDS if paragraph else CHAR_FIELDS
    _keys(value, allowed, "paragraph_style" if paragraph else "text_style")
    if paragraph:
        for key in ("left", "right", "indent", "before", "after", "line"):
            if key in value:
                _number(value[key], key, -1000 if key == "indent" else 0)
        if "align" in value:
            value["align"] = _text(value["align"]).upper()
            if value["align"] not in ("LEFT", "RIGHT", "CENTER", "JUSTIFY", "DISTRIBUTE", "DISTRIBUTE_SPACE"):
                raise ValueError("Unsupported paragraph alignment")
        if "line_type" in value:
            value["line_type"] = _text(value["line_type"]).upper()
            if value["line_type"] not in ("PERCENT", "FIXED", "BETWEEN_LINES", "AT_LEAST"):
                raise ValueError("Unsupported line_type")
        _bools(value, ("page_break",), "paragraph_style")
        return replace(original or ParaStyle(), **value)
    _bools(value, ("bold", "italic", "underline", "strike", "superscript", "subscript"), "text_style")
    if "font" in value and not _text(value["font"], "font").strip():
        raise ValueError("font must not be empty")
    if "size" in value:
        _number(value["size"], "size", 1, 1000)
    if "stretch" in value:
        value["stretch"] = _integer(value["stretch"], "stretch", 1, 1000)
    if "letter_spacing" in value:
        value["letter_spacing"] = _integer(value["letter_spacing"], "letter_spacing", -50, 50)
    for key in ("color", "background"):
        if key in value:
            _color(value[key], key, key == "background")
    if "font" in value:
        value.update(language_fonts=(value["font"],) * 7, language_font_types=("TTF",) * 7, font_type="TTF")
    if "stretch" in value:
        value["language_ratios"] = (value["stretch"],) * 7
    if "letter_spacing" in value:
        value["language_spacing"] = (value["letter_spacing"],) * 7
    return replace(original or TextStyle(), **value)


def _paragraph(spec):
    _keys(spec, ("type", "text", "runs", "text_style", "paragraph_style"), "paragraph")
    default = _style(spec.get("text_style", {}))
    style = _style(spec.get("paragraph_style", {}), paragraph=True)
    if "runs" in spec and "text" in spec:
        raise ValueError("paragraph accepts text or runs, not both")
    if "runs" in spec:
        if not isinstance(spec["runs"], list):
            raise ValueError("runs must be an array")
        runs = []
        for item in spec["runs"]:
            _keys(item, ("text", "text_style"), "run")
            runs.append(Run(_text(item.get("text", "")), _style(item.get("text_style", {}), default)))
    else:
        runs = [Run(_text(spec.get("text", "")), default)]
    return Paragraph(runs, style, default_style=default)


def _image(spec, base):
    _keys(spec, ("type", "path", "width_mm", "height_mm", "paragraph_style"), "image")
    from PySide6.QtGui import QImage
    path = _path(spec.get("path"), base, "image.path")
    if path.stat().st_size > 32 * 1024 * 1024:
        raise ValueError("Image exceeds 32 MB")
    data = path.read_bytes()
    image = QImage.fromData(data)
    if image.isNull() or image.width() * image.height() > 100_000_000:
        raise ValueError("Image is unreadable or exceeds 100 million pixels")
    ext = path.suffix.lower().lstrip(".")
    if ext not in ("png", "jpg", "jpeg", "bmp", "gif"):
        raise ValueError("Use PNG, JPEG, BMP or GIF raster images")
    if "width_mm" not in spec:
        raise ValueError("image.width_mm is required")
    width = _mm(spec["width_mm"], "width_mm")
    height = _mm(spec["height_mm"], "height_mm") if "height_mm" in spec else width * image.height() / image.width()
    if width <= 0 or height <= 0:
        raise ValueError("Image dimensions must be positive")
    picture = PictureGeometry(original_width_pt=width, original_height_pt=height)
    return Paragraph([Run(image=data, image_ext=ext, width=width * 96 / 72,
                          height=height * 96 / 72, picture=picture)],
                     _style(spec.get("paragraph_style", {}), paragraph=True))


def _table(spec, base):
    _keys(spec, ("type", "columns_mm", "rows", "row_heights_mm", "repeat_header", "background"), "table")
    widths, rows = spec.get("columns_mm"), spec.get("rows")
    if not isinstance(widths, list) or not 1 <= len(widths) <= 200:
        raise ValueError("columns_mm must contain 1 to 200 column widths")
    widths = [_mm(v, "columns_mm") for v in widths]
    if any(v <= 0 for v in widths):
        raise ValueError("Column widths must be positive")
    if not isinstance(rows, list) or not 1 <= len(rows) <= 1000:
        raise ValueError("rows must contain 1 to 1000 rows")
    heights = spec.get("row_heights_mm", [8] * len(rows))
    if not isinstance(heights, list) or len(heights) != len(rows):
        raise ValueError("row_heights_mm must match rows")
    heights = [_mm(v, "row_heights_mm") for v in heights]
    if any(v <= 0 for v in heights):
        raise ValueError("Row heights must be positive")
    _bools(spec, ("repeat_header",), "table")
    occupied, cells = set(), []
    for row, items in enumerate(rows):
        if not isinstance(items, list):
            raise ValueError("Each table row must be an array")
        col = 0
        for item in items:
            if isinstance(item, str):
                item = {"text": item}
            _keys(item, ("text", "blocks", "text_style", "paragraph_style", "row_span", "col_span", "background", "header", "padding_mm", "vertical_align"), "cell")
            while (row, col) in occupied:
                col += 1
            row_span = _integer(item.get("row_span", 1), "row_span", 1, len(rows))
            col_span = _integer(item.get("col_span", 1), "col_span", 1, len(widths))
            covered = {(r, c) for r in range(row, row + row_span) for c in range(col, col + col_span)}
            if row + row_span > len(rows) or col + col_span > len(widths) or covered & occupied:
                raise ValueError("Overlapping or out-of-bounds table cells")
            occupied.update(covered)
            if "blocks" in item:
                if any(key in item for key in ("text", "text_style", "paragraph_style")):
                    raise ValueError("cell accepts blocks or text/styles, not both")
                blocks = _blocks(item["blocks"], base)
            else:
                blocks = [_paragraph({key: item[key] for key in ("text", "text_style", "paragraph_style") if key in item})]
            _bools(item, ("header",), "cell")
            padding = item.get("padding_mm", [1, 1, .5, .5])
            if not isinstance(padding, list) or len(padding) != 4:
                raise ValueError("padding_mm must contain left, right, top, bottom")
            vertical = _text(item.get("vertical_align", "CENTER"), "vertical_align").upper()
            if vertical not in ("TOP", "CENTER", "BOTTOM"):
                raise ValueError("Unsupported cell vertical_align")
            cells.append(Cell(row, col, row_span, col_span, blocks, sum(widths[col:col + col_span]),
                              _color(item.get("background", ""), "cell.background", True), sum(heights[row:row + row_span]),
                              tuple(_mm(v, "padding_mm") for v in padding), vertical_align=vertical,
                              header=item.get("header", False)))
            col += col_span
        for col in range(len(widths)):
            if (row, col) not in occupied:
                cells.append(Cell(row, col, blocks=[Paragraph([Run("")])], width=widths[col], height=heights[row]))
                occupied.add((row, col))
    return Table(len(rows), len(widths), cells, sum(widths), sum(heights),
                 column_widths=widths, row_heights=heights,
                 background=_color(spec.get("background", ""), "table.background", True), repeat_header=spec.get("repeat_header", False))


def _blocks(value, base):
    if not isinstance(value, list) or not value:
        raise ValueError("blocks must be a nonempty array")
    result = []
    for spec in value:
        _object(spec, "block")
        kind = spec.get("type", "paragraph")
        if kind == "paragraph":
            result.append(_paragraph(spec))
        elif kind == "table":
            result.append(_table(spec, base))
        elif kind == "image":
            result.append(_image(spec, base))
        else:
            raise ValueError(f"Unsupported block type: {kind}")
    return result


def _model(job, base):
    spec = job.get("page", {})
    _keys(spec, ("width_mm", "height_mm", "margins_mm", "landscape"), "page")
    _bools(spec, ("landscape",), "page")
    margins = spec.get("margins_mm", [20, 20, 20, 20])
    if not isinstance(margins, list) or len(margins) != 4:
        raise ValueError("margins_mm must contain left, right, top, bottom")
    page = PageLayout(width_pt=_mm(spec.get("width_mm", 210), "width_mm"),
                      height_pt=_mm(spec.get("height_mm", 297), "height_mm"),
                      left_pt=_mm(margins[0], "margins_mm"), right_pt=_mm(margins[1], "margins_mm"),
                      top_pt=_mm(margins[2], "margins_mm"), bottom_pt=_mm(margins[3], "margins_mm"),
                      landscape=spec.get("landscape", False))
    width, height = page.display_size
    if width <= page.left_pt + page.right_pt or height <= page.top_pt + page.bottom_pt:
        raise ValueError("Page margins consume the page")
    return DocumentModel(_blocks(job.get("blocks"), base), page)


def _walk(blocks, prefix="body"):
    for index, block in enumerate(blocks):
        path = f"{prefix}/{index}"
        yield path, block
        if isinstance(block, Table):
            for cell in sorted(block.cells, key=lambda c: (c.row, c.col)):
                yield from _walk(cell.blocks, f"{path}/cell/{cell.row}/{cell.col}")
        elif isinstance(block, Drawing):
            yield from _walk(block.blocks, f"{path}/drawing")


def _walk_model(model):
    yield from _walk(model.blocks)
    for index, part in enumerate(model.header_footers):
        yield from _walk(part.blocks, f"{part.kind.lower()}/{index}")


def _inventory(model, include_text=True):
    paragraphs, tables, images = [], [], []
    for ident, block in _walk_model(model):
        if isinstance(block, Paragraph):
            item = {"id": ident, "text_style": asdict(block.default_style), "paragraph_style": asdict(block.style),
                    "editable_text": not any(r.image is not None or r.field is not None for r in block.runs)}
            if include_text:
                item["text"] = paragraph_text(block)
                item["runs"] = [{"text": r.text, "text_style": asdict(r.style), "image": r.image is not None, "field": r.field is not None} for r in block.runs]
            paragraphs.append(item)
            for index, run in enumerate(block.runs):
                if run.image is not None:
                    images.append({"id": f"{ident}/run/{index}", "format": run.image_ext,
                                   "width_mm": round(run.width * 25.4 / 96, 4), "height_mm": round(run.height * 25.4 / 96, 4),
                                   "sha256": hashlib.sha256(run.image).hexdigest()})
        elif isinstance(block, Table):
            tables.append({"id": ident, "rows": block.rows, "columns": block.cols,
                           "cells": [{"row": c.row, "column": c.col, "row_span": c.row_span, "col_span": c.col_span,
                                      "paragraph_ids": [p for p, b in _walk(c.blocks, f"{ident}/cell/{c.row}/{c.col}") if isinstance(b, Paragraph)]}
                                     for c in sorted(block.cells, key=lambda c: (c.row, c.col))]})
    return {"paragraphs": paragraphs, "tables": tables, "images": images,
            "counts": {"paragraphs": len(paragraphs), "tables": len(tables), "images": len(images)}}


def _format(paragraph, operation):
    text_style, paragraph_style = operation.get("text_style", {}), operation.get("paragraph_style", {})
    if not text_style and not paragraph_style:
        raise ValueError("format requires text_style or paragraph_style")
    if any(run.image is not None or run.field is not None for run in paragraph.runs):
        raise ValueError("Formatting object/field paragraphs is outside conservative automation")
    text = paragraph_text(paragraph)
    start = _integer(operation.get("start", 0), "start", 0, len(text))
    end = _integer(operation.get("end", len(text)), "end", start, len(text))
    if paragraph_style:
        paragraph.style = _style(paragraph_style, paragraph.style, paragraph=True)
    if text_style:
        runs, offset = [], 0
        for run in paragraph.runs:
            left, right = max(start - offset, 0), min(end - offset, len(run.text))
            if left < right:
                for a, b, modified in ((0, left, False), (left, right, True), (right, len(run.text), False)):
                    if a < b:
                        native = len(run.text[:a].replace("\t", "\t" + "\u200b" * 7).encode("utf-16le")) // 2
                        runs.append(replace(run, text=run.text[a:b], style=_style(text_style, run.style) if modified else run.style,
                                            source_start=None if run.source_start is None else run.source_start + native))
            else:
                runs.append(run)
            offset += len(run.text)
        paragraph.runs = runs
        if start == 0 and end == len(text):
            paragraph.default_style = _style(text_style, paragraph.default_style)
            if paragraph.end_style:
                paragraph.end_style = _style(text_style, paragraph.end_style)
    paragraph.line_segments = []


def _replace_exact(paragraph, find, replacement):
    """Apply known match intervals, retaining untouched characters' styles.

    A general diff can align repeated letters to the wrong style run. A literal
    replacement supplies the edit ranges, so its surviving styles are exact.
    """
    old = paragraph_text(paragraph)
    if not old.count(find) or find == replacement:
        return
    if any(run.image is not None or run.field is not None for run in paragraph.runs):
        raise ValueError("Object/field paragraphs cannot be replaced by automation")
    originals = [replace(run) for run in paragraph.runs]
    runs = []
    def append_range(start, end):
        offset = 0
        for run in originals:
            left, right = max(0, start - offset), min(len(run.text), end - offset)
            if left < right:
                runs.append(replace(run, text=run.text[left:right], source_start=None))
            offset += len(run.text)
    cursor = 0
    while True:
        start = old.find(find, cursor)
        if start < 0:
            append_range(cursor, len(old))
            break
        append_range(cursor, start)
        offset, owner = 0, originals[-1] if originals else Run(style=paragraph.default_style)
        for run in originals:
            if start < offset + len(run.text):
                owner = run
                break
            offset += len(run.text)
        if replacement:
            runs.append(replace(owner, text=replacement, source_start=None))
        cursor = start + len(find)
    # Retain the engine's native cache/control-offset update. The replacement
    # ranges above determine the exposed style runs, independently of its diff.
    replace_paragraph_text(paragraph, old.replace(find, replacement))
    paragraph.runs = runs or [Run("", paragraph.default_style)]


def _edit(model, operations):
    if not isinstance(operations, list):
        raise ValueError("operations must be an array; use [] for a byte-exact copy")
    paragraphs = {ident: block for ident, block in _walk_model(model) if isinstance(block, Paragraph)}
    for operation in operations:
        _object(operation, "operation")
        allowed = {"set_text": ("op", "target", "text"),
                   "replace_text": ("op", "target", "find", "replace", "expected"),
                   "format": ("op", "target", "text_style", "paragraph_style", "start", "end"),
                   "merge_cells": ("op", "table_index", "target_table", "start_row", "start_col", "row_span", "col_span", "text", "border_fill", "para_pr", "char_pr", "vertical_align", "match_text", "from_text", "to_text", "cell_texts", "find_texts", "font_size_pt", "size_pt", "bold", "letter_spacing", "color", "align", "background", "border", "paragraph_style", "text_style"),
                   "split_cell": ("op", "table_index", "target_table", "row", "col", "rows", "cols", "match_text", "find_text", "texts"),
                   "set_cell_text": ("op", "table_index", "target_table", "row", "col", "match_text", "find_text", "text", "append", "font_size_pt", "size_pt", "bold", "letter_spacing", "color", "align", "background", "border", "vertical_align", "paragraph_style", "text_style"),
                   "set_cell_style": ("op", "table_index", "target_table", "row", "col", "apply_to", "border_fill", "vertical_align", "padding_mm", "width_mm", "height_mm", "match_text", "find_text", "border", "background", "font_size_pt", "size_pt", "bold", "letter_spacing", "color", "align", "paragraph_style", "text_style"),
                   "modify_table": ("op", "table_index", "target_table", "treat_as_char", "align", "width_mm", "height_mm", "out_margin_mm", "in_margin_mm"),
                   "modify_image": ("op", "image_index", "target_image", "treat_as_char", "align", "width_mm", "height_mm", "out_margin_mm", "replace_image_path", "replace_image_data"),
                   "insert_row": ("op", "table_index", "target_table", "row_index", "height_mm"),
                   "delete_row": ("op", "table_index", "target_table", "row_index")}
        if operation.get("op") not in allowed:
            raise ValueError(f"Unsupported edit operation: {operation.get('op')}")
        _keys(operation, allowed[operation["op"]], "operation")
        kind, target = operation.get("op"), operation.get("target", "*")
        if kind in ("merge_cells", "split_cell", "set_cell_text", "set_cell_style", "modify_table", "modify_image", "insert_row", "delete_row"):
            for key in ("table_index", "image_index", "row", "col", "row_index", "start_row", "start_col", "row_span", "col_span", "rows", "cols"):
                if key in operation:
                    _integer(operation[key], key, 1 if key in ("row_span", "col_span", "rows", "cols") else 0)
            for key in ("text", "match_text", "find_text", "from_text", "to_text"):
                if key in operation: _text(operation[key], key)
            _bools(operation, ("append", "bold", "treat_as_char"), "operation")
            if "text_style" in operation: _style(operation["text_style"])
            if "paragraph_style" in operation: _style(operation["paragraph_style"], paragraph=True)
            for key in ("width_mm", "height_mm", "font_size_pt", "size_pt"):
                if key in operation: _number(operation[key], key, .01)
            for key in ("padding_mm", "out_margin_mm", "in_margin_mm"):
                if key in operation:
                    if not isinstance(operation[key], list) or len(operation[key]) != 4: raise ValueError(f"{key} requires four values")
                    for value in operation[key]: _number(value, key)
            continue
        if target == "*" and kind == "replace_text":
            selected = [p for p in paragraphs.values() if not any(r.image is not None or r.field is not None for r in p.runs)]
        elif target in paragraphs:
            selected = [paragraphs[target]]
        else:
            raise ValueError(f"Unknown paragraph target: {target}; inspect the input first")
        if kind == "set_text":
            replace_paragraph_text(selected[0], _text(operation.get("text")))
        elif kind == "replace_text":
            find, replacement = _text(operation.get("find"), "find"), _text(operation.get("replace"), "replace")
            if not find:
                raise ValueError("find must not be empty")
            if "expected" not in operation:
                raise ValueError("replace_text requires an exact expected match count")
            expected = _integer(operation["expected"], "expected", 1, 1000000)
            count = sum(paragraph_text(p).count(find) for p in selected)
            if count != expected:
                raise ValueError(f"Expected {expected} replacements, found {count}")
            for paragraph in selected:
                _replace_exact(paragraph, find, replacement)
        elif kind == "format":
            _format(selected[0], operation)
        else:
            raise ValueError(f"Unsupported edit operation: {kind}")


def _qt():
    global _APP
    if os.name != "nt" and not os.environ.get("DISPLAY"):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    _APP = QApplication.instance() or QApplication([])
    return _APP


def _sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for part in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(part)
    return digest.hexdigest()


def _stage(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=".narae-ai-", suffix=path.suffix, dir=path.parent)
    os.close(handle)
    return Path(temporary)


def _destinations(job, base, source):
    result = {}
    if job["action"] in ("create", "edit"):
        result["document"] = _path(job.get("output"), base, "output")
    preview = job.get("preview", {})
    _keys(preview, ("png", "pdf", "page", "dpi"), "preview")
    for kind in ("png", "pdf"):
        if kind in preview:
            result[kind] = _path(preview[kind], base, f"preview.{kind}")
    if len(set(result.values())) != len(result):
        raise ValueError("Output artifact paths must be distinct")
    if job["action"] == "render" and not result:
        raise ValueError("render requires preview.png or preview.pdf")
    for name, path in result.items():
        if source is not None and path == source:
            if name != "document" or not job.get("overwrite_source", False):
                raise ValueError("Writing over input requires overwrite_source:true for the document")
        elif path.exists() and not job.get("overwrite_output", False):
            raise ValueError(f"Output exists: {path}; choose another path or overwrite_output:true")
    return result


def _render(model, preview, staged):
    if not any(key in staged for key in ("png", "pdf")):
        return None
    _qt()
    from .source_layout import SourceLayout
    from PySide6.QtPrintSupport import QPrinter
    layout = SourceLayout(model)
    if "png" in staged:
        page = _integer(preview.get("page", 1), "preview.page", 1, len(layout.pages))
        dpi = _integer(preview.get("dpi", 96), "preview.dpi", 24, 600)
        if not layout.render_page(page - 1, dpi).save(str(staged["png"]), "PNG"):
            raise OSError("Could not write PNG preview")
    if "pdf" in staged:
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        printer.setResolution(600)
        printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
        printer.setOutputFileName(str(staged["pdf"]))
        printer.setFullPage(True)
        layout.print_to(printer)
    return layout.geometry_report()


def _equivalent(first, second, label):
    if isinstance(first, (int, float)) and not isinstance(first, bool):
        if not isinstance(second, (int, float)) or abs(first - second) > .010001:
            raise ValueError(f"Readback verification failed: {label}")
    elif first != second:
        raise ValueError(f"Readback verification failed: {label}")


def _verify_readback(expected, actual, creation=False):
    """Check modeled content before committing; this is not visual parity."""
    def paragraphs(model):
        return [block for _, block in _walk_model(model) if isinstance(block, Paragraph)
                and (not creation or paragraph_text(block))]
    before, after = paragraphs(expected), paragraphs(actual)
    if len(before) != len(after):
        raise ValueError("Readback verification failed: paragraph count")
    for index, (old, new) in enumerate(zip(before, after)):
        _equivalent(paragraph_text(old), paragraph_text(new), f"paragraph {index} text")
        for name in PARA_FIELDS:
            _equivalent(getattr(old.style, name), getattr(new.style, name), f"paragraph {index}.{name}")
        # Native writers may join adjacent equal style runs. Compare each
        # overlapping range rather than relying on fragment boundaries.
        left = [(run.text, run.style) for run in old.runs if run.text]
        right = [(run.text, run.style) for run in new.runs if run.text]
        li = ri = lo = ro = 0
        while li < len(left) and ri < len(right):
            count = min(len(left[li][0]) - lo, len(right[ri][0]) - ro)
            _equivalent(left[li][0][lo:lo + count], right[ri][0][ro:ro + count], f"paragraph {index} run text")
            for name in CHAR_FIELDS:
                _equivalent(getattr(left[li][1], name), getattr(right[ri][1], name), f"paragraph {index}.{name}")
            lo += count
            ro += count
            if lo == len(left[li][0]):
                li, lo = li + 1, 0
            if ro == len(right[ri][0]):
                ri, ro = ri + 1, 0
        if li != len(left) or ri != len(right):
            raise ValueError("Readback verification failed: text run length")
    def tables(model):
        return [block for _, block in _walk_model(model) if isinstance(block, Table)]
    before_tables, after_tables = tables(expected), tables(actual)
    if len(before_tables) != len(after_tables):
        raise ValueError("Readback verification failed: table count")
    for old, new in zip(before_tables, after_tables):
        signature = lambda table: (table.rows, table.cols, sorted((c.row, c.col, c.row_span, c.col_span) for c in table.cells))
        _equivalent(signature(old), signature(new), "table cell spans")
    def pictures(model):
        return [run for _, block in _walk_model(model) if isinstance(block, Paragraph) for run in block.runs if run.image is not None]
    old_pictures, new_pictures = pictures(expected), pictures(actual)
    if len(old_pictures) != len(new_pictures):
        raise ValueError("Readback verification failed: image count")
    for old, new in zip(old_pictures, new_pictures):
        _equivalent(hashlib.sha256(old.image).hexdigest(), hashlib.sha256(new.image).hexdigest(), "image SHA-256")
        _equivalent(old.width * .75, new.width * .75, "image width")
        _equivalent(old.height * .75, new.height * .75, "image height")
    for key in ("width_pt", "height_pt", "left_pt", "right_pt", "top_pt", "bottom_pt", "landscape"):
        _equivalent(getattr(expected.page, key), getattr(actual.page, key), f"page.{key}")


def _run_job(job, base_dir=None):
    """Execute one JSON job. Raise on refusal; never reconstruct imports."""
    if isinstance(job, dict) and job.get("action") in ("live-read", "live-edit"):
        from .job_safety import live_job
        return live_job(job,Path(base_dir or Path.cwd()).resolve())
    if isinstance(job, dict) and job.get("action") in ("hancom", "hancom_capabilities"):
        if job.get("schema", SCHEMA) != SCHEMA:
            raise ValueError("Unsupported automation schema")
        from .hancom import run_hancom_job
        return run_hancom_job(job, Path(base_dir or Path.cwd()).resolve())
    _keys(job, ("schema", "action", "input", "output", "format", "page", "blocks", "operations", "preview",
                "overwrite_output", "overwrite_source", "expected_input_sha256"), "job")
    if job.get("schema", SCHEMA) != SCHEMA:
        raise ValueError("Unsupported automation schema")
    _bools(job, ("overwrite_output", "overwrite_source"), "job")
    action = job.get("action")
    if action not in ("create", "inspect", "edit", "render"):
        raise ValueError("action must be create, inspect, edit or render")
    common = ("schema", "action", "preview", "overwrite_output")
    allowed = {"create": common + ("output", "format", "page", "blocks"),
               "edit": common + ("input", "output", "format", "operations", "overwrite_source", "expected_input_sha256"),
               "inspect": common + ("input", "expected_input_sha256"),
               "render": common + ("input", "expected_input_sha256")}
    _keys(job, allowed[action], action + " job")
    base = Path(base_dir or Path.cwd()).resolve()
    source = None if action == "create" else _path(job.get("input"), base, "input")
    destinations = _destinations(job, base, source)
    operations = job.get("operations", [])
    from . import __version__
    result = {"schema": SCHEMA, "application": "NaraeHangul", "display_name": "나래 한글", "version": __version__, "action": action, "success": True,
              "uses_hancom_runtime": False, "zero_difference_verified": False,
              "limitations": list(_LIMITATIONS), "warnings": [], "artifacts": {}}
    if action == "create":
        _qt()
        model = _model(job, base)
        source_format = job.get("format", "hwp").lower()
        if source_format not in ("hwp", "hwpx"):
            raise ValueError("New documents support hwp or hwpx")
        result["preservation"] = "new_native_document"
    else:
        initial_hash = _sha(source)
        if "expected_input_sha256" in job and job["expected_input_sha256"].lower() != initial_hash:
            raise ValueError("Input SHA-256 has changed since inspection")
        from .revision import revision_state
        revision = revision_state(source)
        if revision and action == 'edit':raise ValueError(revision['guidance'])
        if revision and action == 'inspect':
            return {**result,'input':str(source),'input_sha256':initial_hash,'input_unchanged':True,
                    'format':source.suffix[1:].lower(),'paragraphs':[],'tables':[],'images':[],
                    'counts':{'paragraphs':0,'tables':0},'textDetailsLoaded':False,'revisionState':revision,
                    'warnings':[revision['guidance']]}
        loaded = load_document(source)
        if not loaded.model or loaded.source_format not in ("hwp", "hwpx"):
            raise ValueError("Automation requires an actual native HWP/HWPX document")
        model, source_format = loaded.model, loaded.source_format
        result.update(input=str(source), input_sha256=initial_hash, warnings=list(loaded.warnings))
        native = getattr(model, "_hwp_source", {})
        result["read_only"] = bool(native.get("read_only") or native.get("legacy_protected"))
        result["format_version"] = native.get("format_version", "5.0" if source_format == "hwp" else "HWPX")
        restrictions = native.get("distribution_options", 0)
        if restrictions & 2 and any(key in destinations for key in ("png", "pdf")):
            raise ValueError("Original HWP printing restriction prevents preview export")
        if action == "edit":
            operations = job.get("operations")
            if result["read_only"] and operations:
                raise ValueError("Original document is protected against editing")
            _edit(model, operations)
            result["preservation"] = "native_source_patch"
        else:
            result["preservation"] = "read_only_inspection"
    result["format"] = source_format
    if action != "create" and job.get("format", source_format).lower() != source_format:
        raise ValueError("Native editing preserves the actual source format; conversion is not supported")
    staged = {}
    try:
        for name, path in destinations.items():
            staged[name] = _stage(path)
        if "document" in staged:
            if action == "create":
                result["warnings"].extend((save_hwp if source_format == "hwp" else save_hwpx)(model, staged["document"]))
            else:
                from .hwp_native import patch_hwp_source
                from .hwpx_native import patch_hwpx_source, patch_hwpx_merge_cells
                adv_ops = [op for op in (operations or []) if op.get("op") in ("merge_cells", "split_cell", "set_cell_text", "set_cell_style", "modify_table", "modify_image", "insert_row", "delete_row")]
                if adv_ops:
                    # Preserve ordinary text/format edits in a mixed job before
                    # applying structural edits. Previously these were dropped.
                    if not (patch_hwp_source if source_format == "hwp" else patch_hwpx_source)(model, staged["document"]):
                        raise ValueError("Native preservation refused before advanced editing")
                    native_readback = load_document(staged["document"])
                    _verify_readback(model, native_readback.model)
                if adv_ops and source_format == "hwpx":
                    from .hwpx_advanced import apply_advanced_hwpx_operations
                    source_bytes = staged["document"].read_bytes()
                    patched_bytes = apply_advanced_hwpx_operations(source_bytes, adv_ops)
                    staged["document"].write_bytes(patched_bytes)
                elif adv_ops and source_format == "hwp":
                    from .hwp_automation_bridge import patch_hwp_advanced
                    result.update(patch_hwp_advanced(staged["document"], staged["document"], adv_ops))
                    result["preservation"] = "hancom_runtime_save"
                else:
                    if not (patch_hwp_source if source_format == "hwp" else patch_hwpx_source)(model, staged["document"]):
                        raise ValueError("Native preservation refused: " + getattr(model, "_hwp_preservation_error", "unsupported text/style or structural change"))
            reopened = load_document(staged["document"])
            if reopened.source_format != source_format or not reopened.model:
                raise ValueError("Output native format could not be verified")
            if not (operations and any(op.get("op") in ("merge_cells", "split_cell", "set_cell_text", "set_cell_style", "modify_table", "modify_image", "insert_row", "delete_row") for op in operations)):
                _verify_readback(model, reopened.model, action == "create")
            model = reopened.model
            result["modeled_content_verified"] = not bool(action == "edit" and adv_ops) or result.get("saved_content_verified", False)
            result["output_sha256"] = _sha(staged["document"])
            if action == "edit":
                result["byte_exact_copy"] = result["output_sha256"] == result["input_sha256"]
        geometry = _render(model, job.get("preview", {}), staged)
        if geometry:
            result["layout"] = geometry
        native = getattr(model, "_hwp_source", {})
        result.update(_inventory(model, not (native.get("distribution_options", 0) & 1)))
        if source is not None and _sha(source) != result["input_sha256"]:
            raise ValueError("Input changed while processing; outputs were not committed")
        for name, target in destinations.items():
            if target != source and target.exists() and not job.get("overwrite_output", False):
                raise ValueError(f"Output appeared while processing: {target}")
        if "document" in destinations and destinations["document"] == source:
            backup = source.with_name(source.name + ".bak")
            if backup.exists():
                handle, backup = tempfile.mkstemp(prefix=source.name + ".backup-", suffix=source.suffix, dir=source.parent)
                os.close(handle)
            shutil.copy2(source, backup)
            result["artifacts"]["backup"] = str(Path(backup))
        for name, target in destinations.items():
            os.replace(staged[name], target)
            result["artifacts"][name] = str(target)
        result["input_unchanged"] = source is None or _sha(source) == result["input_sha256"]
        return result
    finally:
        for path in staged.values():
            path.unlink(missing_ok=True)


def run_job(job, base_dir=None):
    from .job_safety import guarded_job
    base=Path(base_dir or Path.cwd()).resolve()
    with guarded_job(job,base) as state:
        result=_run_job(job,base)
    result.update(state)
    return result


def main(argv=None):
    """CLI and frozen-executable entry; --result works without a console."""
    parser = argparse.ArgumentParser(description="나래 한글 native document automation")
    parser.add_argument("--automation", required=True, metavar="JOB.json")
    parser.add_argument("--result", metavar="RESULT.json")
    args = parser.parse_args(argv)
    job_path = Path(args.automation).resolve()
    result_path = Path(args.result).resolve() if args.result else None
    reserved = [job_path]
    try:
        job = _object(json.loads(job_path.read_text(encoding="utf-8-sig")), "job")
        for key in ("input", "output"):
            if key in job:
                reserved.append(_path(job[key], job_path.parent, key))
        preview = _object(job.get("preview", {}), "preview")
        for key in ("png", "pdf"):
            if key in preview:
                reserved.append(_path(preview[key], job_path.parent, key))
        if job_path in reserved[1:]:
            raise ValueError("Job JSON must be separate from document and preview paths")
        if result_path in reserved:
            raise ValueError("Result JSON must be separate from input, job and output artifacts")
        result, code = run_job(job, job_path.parent), 0
    except Exception as error:
        result = {"schema": SCHEMA, "application": "NaraeHangul", "display_name": "나래 한글", "success": False,
                  "error": str(error), "error_type": type(error).__name__}
        code = 1
    serialized = json.dumps(result, ensure_ascii=False, indent=2)
    if result_path:
        if result_path not in reserved:
            result_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = _stage(result_path)
            try:
                temporary.write_text(serialized, encoding="utf-8")
                os.replace(temporary, result_path)
            finally:
                temporary.unlink(missing_ok=True)
    if sys.stdout is not None:
        try:
            print(serialized)
        except UnicodeEncodeError:
            sys.stdout.buffer.write((serialized + "\n").encode("utf-8"))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
