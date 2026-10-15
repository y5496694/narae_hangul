"""Independent HWP 5 and HWPX document interchange.

본 제품은 한컴의 HWP 문서 파일(.hwp) 공개 문서를 참고하여 개발하였습니다.
References: https://www.store.hancom.com/support/downloadCenter/hwpOwpml
HWP 5.0 revision 1.3, sections 3/4; OWPML namespace model:
https://github.com/hancom-io/hwpx-owpml-model
Implementation is original Python; no Hancom runtime or copied templates.
"""
from __future__ import annotations

import base64
import html
import io
import mimetypes
import os
import re
import struct
import tempfile
import zipfile
import zlib
from dataclasses import dataclass, field, asdict
from pathlib import Path
import xml.etree.ElementTree as ET

from .cfb import CompoundFile, MAGIC, MAX_BYTES, MAX_FILE_BYTES, write_compound
from .geometry import (PageLayout, Border, BorderFill, ObjectPosition, PictureGeometry, DocumentModel,
    PAGE_PROPERTY, TABLE_PROPERTY, CELL_PROPERTY, PICTURE_PROPERTY, FONT_PROPERTY, borders_from_data,
    position_from_data, picture_from_data)

ATTRIBUTION = "본 제품은 한컴의 HWP 문서 파일(.hwp) 공개 문서를 참고하여 개발하였습니다."
NS = {p: "http://www.hancom.co.kr/hwpml/2011/" + n for p, n in
      (("hp", "paragraph"), ("hh", "head"), ("hs", "section"), ("hc", "core"), ("hv", "version"))}
NS.update(opf="http://www.idpf.org/2007/opf/", ocf="urn:oasis:names:tc:opendocument:xmlns:container",
          hpf="http://www.hancom.co.kr/schema/2011/hpf", dc="http://purl.org/dc/elements/1.1/")
for _p, _u in NS.items():
    ET.register_namespace(_p, _u)


@dataclass
class LoadedDocument:
    html: str
    warnings: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    source_path: str | None = None
    source_format: str = ""
    model: DocumentModel | None = None


@dataclass(frozen=True)
class TextStyle:
    font: str = "맑은 고딕"
    size: float = 11.0
    bold: bool = False
    italic: bool = False
    underline: bool = False
    strike: bool = False
    color: str = "#000000"
    background: str = ""
    superscript: bool = False
    subscript: bool = False
    stretch: int = 100
    letter_spacing: int = 0  # percent of the character's nominal em width
    language_fonts: tuple = ()  # HANGUL/LATIN/HANJA/JAPANESE/OTHER/SYMBOL/USER
    font_type: str = "TTF"
    language_font_types: tuple = ()  # preserve HFT/TTF for each script independently
    use_font_space: bool = False
    use_kerning: bool = False
    language_ratios: tuple = ()
    language_spacing: tuple = ()
    language_relative_sizes: tuple = ()
    language_offsets: tuple = ()


@dataclass(frozen=True)
class ParaStyle:
    align: str = "LEFT"
    left: float = 0
    right: float = 0
    indent: float = 0
    before: float = 0
    after: float = 0
    line: float = 160
    line_type: str = "PERCENT"
    page_break: bool = False
    break_latin: str = "KEEP_WORD"
    break_nonlatin: str = "BREAK_WORD"
    vertical_align: str = "BASELINE"
    condense: int = 0
    snap_to_grid: bool = False
    font_line_height: bool = False
    widow_orphan: bool = False
    keep_with_next: bool = False
    keep_lines: bool = False
    line_wrap: str = "BREAK"
    auto_spacing_eng: bool = False
    auto_spacing_num: bool = False
    background: str = ""
    borders: tuple = field(default_factory=lambda:(Border("NONE"),)*4)
    border_offsets: tuple = (0,0,0,0)
    border_connect: bool = False
    border_ignore_margin: bool = False


@dataclass
class Run:
    text: str = ""
    style: TextStyle = field(default_factory=TextStyle)
    image: bytes | None = None
    image_ext: str = "png"
    width: float = 0
    height: float = 0
    picture: PictureGeometry = field(default_factory=PictureGeometry)
    source_start: int | None = None  # original UTF-16 code-unit offset
    field: dict | None = None


@dataclass
class Paragraph:
    runs: list[Run] = field(default_factory=list)
    style: ParaStyle = field(default_factory=ParaStyle)
    source_id: int = 0
    line_segments: list = field(default_factory=list)
    source_index: int = -1
    cache_text: str = ""  # preserves original UTF-16 offsets with zero-width controls
    scope: dict = field(default_factory=lambda: {"kind": "body"})
    default_style: TextStyle = field(default_factory=TextStyle)
    end_style: TextStyle | None = None  # paragraph-break glyph affects final line metrics


@dataclass
class Cell:
    row: int
    col: int
    row_span: int = 1
    col_span: int = 1
    blocks: list = field(default_factory=list)
    width: float = 0
    background: str = ""
    height: float = 22
    padding: tuple = (3, 3, 1.5, 1.5)  # left, right, top, bottom; points
    borders: tuple = field(default_factory=lambda: (Border(),) * 4)
    vertical_align: str = "CENTER"
    header: bool = False
    padding_inherited: tuple = (False, False, False, False)
    native_flags: int = 0  # retain native list flags whose modern semantics vary


@dataclass
class Table:
    rows: int
    cols: int
    cells: list[Cell] = field(default_factory=list)
    width: float = 480
    height: float = 0
    column_widths: list = field(default_factory=list)
    row_heights: list = field(default_factory=list)
    padding: tuple = (3, 3, 1.5, 1.5)
    borders: tuple = field(default_factory=lambda: (Border(),) * 4)
    background: str = ""
    cell_spacing: float = 0
    page_break: str = "CELL"
    repeat_header: bool = False
    position: ObjectPosition = field(default_factory=ObjectPosition)
    anchor_segments: list = field(default_factory=list)
    source_para_id: int = 0
    source_index: int = -1
    anchor_text_start: int = 0
    scope: dict = field(default_factory=lambda: {"kind": "body"})
    anchor_style: TextStyle | None = None


@dataclass
class Drawing:
    """Native drawing geometry and optional text; positions use points."""
    kind: str = "rect"
    blocks: list = field(default_factory=list)
    width: float = 0
    height: float = 0
    position: ObjectPosition = field(default_factory=ObjectPosition)
    padding: tuple = (0, 0, 0, 0)
    borders: tuple = field(default_factory=lambda: (Border("NONE"),) * 4)
    background: str = ""
    anchor_segments: list = field(default_factory=list)
    source_para_id: int = 0
    source_index: int = -1
    anchor_text_start: int = 0
    scope: dict = field(default_factory=lambda: {"kind": "body"})
    shape: PictureGeometry = field(default_factory=PictureGeometry)
    vertical_align: str = "CENTER"
    line_options: dict = field(default_factory=dict)  # caps/arrows/fill flags of the shape outline
    anchor_style: TextStyle | None = None
    geometry: dict = field(default_factory=dict)


@dataclass
class HeaderFooter:
    kind: str
    blocks: list = field(default_factory=list)
    page_type: str = "BOTH"
    width: float = 0
    height: float = 0
    source_para_id: int = 0
    source_index: int = -1
    vertical_align: str = "TOP"
    scope: dict = field(default_factory=dict)


def _local(e):
    return e.tag.split("}")[-1]


def _child(e, name):
    return next((x for x in e if _local(x) == name), None) if e is not None else None


def _desc(e, name):
    return next((x for x in e.iter() if _local(x) == name), None)


def _attr(e, name, default=""):
    return e.get(name, default) if e is not None else default


def _num(value, default=0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(value, default=0):
    return int(_num(value, default))


def _warning(warnings, value):
    if value not in warnings:
        warnings.append(value)


def _colorref(value):
    return "#%02x%02x%02x" % (value & 255, (value >> 8) & 255, (value >> 16) & 255)


def _rgb(value):
    v = value.lstrip("#")
    if len(v) == 3:
        v = "".join(c * 2 for c in v)
    try:
        r, g, b = bytes.fromhex(v[:6])
        return r | (g << 8) | (b << 16)
    except ValueError:
        return 0


def _page_metadata(metadata):
    page = metadata.get("page")
    if page:
        layout = PageLayout(**page)
        metadata["page_mm"] = [round(v * 25.4 / 72, 3) for v in layout.display_size]
        metadata["margin_mm"] = round(page.get("left_pt", 56.69) * 25.4 / 72, 3)
        metadata["margins_mm"] = {k: round(page.get(k + "_pt", 0) * 25.4 / 72, 3) for k in ("left", "right", "top", "bottom", "header", "footer", "gutter")}


def _page_layout(document):
    if isinstance(document, DocumentModel):
        return document.page
    if isinstance(document, str):
        return PageLayout()
    saved = document.rootFrame().frameFormat().property(PAGE_PROPERTY)
    if isinstance(saved, dict):
        return PageLayout(**saved)
    size = document.pageSize()
    if size.width() <= 0 or size.height() <= 0:
        return PageLayout()
    margin = document.documentMargin() * .75
    return PageLayout(size.width() * .75, size.height() * .75, margin, margin, margin, margin,
                      landscape=size.width() > size.height())


def _page_settings(document):
    p = _page_layout(document)
    return round(p.width_pt * 100), round(p.height_pt * 100), round(p.left_pt * 100)


def _table_grid(t):
    """Infer an exact column/row grid from unmerged cells when available."""
    columns = list(t.column_widths) if len(t.column_widths) == t.cols else [0.0] * t.cols
    rows = list(t.row_heights) if len(t.row_heights) == t.rows else [0.0] * t.rows
    for c in t.cells:
        if c.col_span == 1 and c.width > 0 and 0 <= c.col < t.cols:
            columns[c.col] = max(columns[c.col], c.width)
        if c.row_span == 1 and c.height > 0 and 0 <= c.row < t.rows:
            rows[c.row] = max(rows[c.row], c.height)
    for c in t.cells:
        if c.col_span > 1 and c.width > 0:
            unknown = [i for i in range(c.col, min(t.cols, c.col + c.col_span)) if not columns[i]]
            residual = c.width - sum(columns[c.col:c.col + c.col_span])
            for i in unknown:
                columns[i] = max(1, residual / len(unknown))
    unknown = [i for i, w in enumerate(columns) if w <= 0]
    residual = t.width - sum(columns)
    for i in unknown:
        columns[i] = max(1, residual / len(unknown))
    rows = [r or 22 for r in rows]
    t.column_widths, t.row_heights = columns, rows
    if not t.height:
        t.height = sum(rows)
    return columns, rows


def _qt_tables_images(document):
    from PySide6.QtGui import QTextTable
    tables, images = [], []
    def frame(fr):
        it = fr.begin()
        while not it.atEnd():
            child, block = it.currentFrame(), it.currentBlock()
            if child:
                if isinstance(child, QTextTable):
                    tables.append(child)
                frame(child)
            elif block.isValid():
                fi = block.begin()
                while not fi.atEnd():
                    fragment = fi.fragment()
                    if fragment.isValid() and fragment.charFormat().isImageFormat():
                        images.append((fragment.position(), fragment.length(), fragment.charFormat().toImageFormat()))
                    fi += 1
            it += 1
    frame(document.rootFrame())
    return tables, images


def export_geometry(document):
    """Return JSON-safe geometry for a .narae file; dimensions are points."""
    tables, images = _qt_tables_images(document)
    result = {"version": 1, "page": asdict(_page_layout(document)), "tables": [], "images": []}
    for table in tables:
        saved = table.format().property(TABLE_PROPERTY) or {}
        cells, seen = [], set()
        for row in range(table.rows()):
            for col in range(table.columns()):
                cell = table.cellAt(row, col)
                key = (cell.row(), cell.column())
                if key not in seen:
                    seen.add(key)
                    cells.append({"row": key[0], "col": key[1], "geometry": cell.format().property(CELL_PROPERTY) or {}})
        result["tables"].append({"geometry": saved, "cells": cells})
    for _, _, fmt in images:
        result["images"].append({"picture": fmt.property(PICTURE_PROPERTY) or {}, "width_px": fmt.width(), "height_px": fmt.height()})
    result["header_footers"] = [dict(kind=p.kind, page_type=p.page_type, width=p.width, height=p.height,
        source_para_id=p.source_para_id, source_index=p.source_index, vertical_align=p.vertical_align,
        blocks=[_block_data(b) for b in p.blocks]) for p in getattr(document, "_hangeul_header_footers", [])]
    result["font_styles"] = document.rootFrame().frameFormat().property(FONT_PROPERTY) or []
    return result


def apply_geometry(document, data):
    """Apply saved geometry to Qt without changing its text or image payloads.

    Exact cell heights, cropping and floating positions are retained as custom
    properties. Qt renders its supported subset; the file writers use all data.
    """
    from PySide6.QtCore import QSizeF, Qt
    from PySide6.QtGui import QTextCursor, QTextLength, QTextFrameFormat, QColor, QBrush
    if not isinstance(data, dict):
        return
    document._hangeul_header_footers = [HeaderFooter(**{**p, "blocks": [_block_from_data(b) for b in p.get("blocks", [])]}) for p in data.get("header_footers", [])]
    page = PageLayout(**data.get("page", {}))
    document.setPageSize(QSizeF(*(v / .75 for v in page.display_size)))
    fmt = document.rootFrame().frameFormat()
    for side in ("Left", "Right", "Top", "Bottom"):
        getattr(fmt, "set" + side + "Margin")(getattr(page, side.lower() + "_pt") / .75)
    fmt.setProperty(PAGE_PROPERTY, asdict(page))
    fmt.setProperty(FONT_PROPERTY,data.get("font_styles",[]))
    document.rootFrame().setFrameFormat(fmt)
    tables, images = _qt_tables_images(document)
    border_styles = {"NONE": QTextFrameFormat.BorderStyle.BorderStyle_None, "SOLID": QTextFrameFormat.BorderStyle.BorderStyle_Solid,
                     "DASH": QTextFrameFormat.BorderStyle.BorderStyle_Dashed, "DOT": QTextFrameFormat.BorderStyle.BorderStyle_Dotted,
                     "DOUBLE_SLIM": QTextFrameFormat.BorderStyle.BorderStyle_Double}
    for table, item in zip(tables, data.get("tables", [])):
        geometry = item.get("geometry", {})
        tf = table.format()
        tf.setProperty(TABLE_PROPERTY, geometry)
        if geometry.get("width"):
            tf.setWidth(QTextLength(QTextLength.Type.FixedLength, geometry["width"] / .75))
        if geometry.get("column_widths"):
            tf.setColumnWidthConstraints([QTextLength(QTextLength.Type.FixedLength, v / .75) for v in geometry["column_widths"]])
        tf.setCellSpacing(geometry.get("cell_spacing", 0) / .75)
        table.setFormat(tf)
        for item in item.get("cells", []):
            cell = table.cellAt(item["row"], item["col"])
            if not cell.isValid():
                continue
            g = item.get("geometry", {})
            cf = cell.format().toTableCellFormat()
            cf.setProperty(CELL_PROPERTY, g)
            for side, value in zip(("Left", "Right", "Top", "Bottom"), g.get("padding", ())):
                getattr(cf, "set" + side + "Padding")(value / .75)
            for side, border in zip(("Left", "Right", "Top", "Bottom"), g.get("borders", ())):
                getattr(cf, "set" + side + "Border")(border.get("width_pt", 0) / .75)
                getattr(cf, "set" + side + "BorderStyle")(border_styles.get(border.get("style"), border_styles["SOLID"]))
                getattr(cf, "set" + side + "BorderBrush")(QBrush(QColor(border.get("color", "#000000"))))
            cell.setFormat(cf)
    for (position, length, fmt), g in zip(images, data.get("images", [])):
        fmt.setProperty(PICTURE_PROPERTY, g.get("picture", g))
        if g.get("width_px"):
            fmt.setWidth(g["width_px"])
        if g.get("height_px"):
            fmt.setHeight(g["height_px"])
        cursor = QTextCursor(document)
        cursor.setPosition(position)
        cursor.setPosition(position + length, QTextCursor.MoveMode.KeepAnchor)
        cursor.setCharFormat(fmt)


def bind_geometry(document, model):
    """Bind LoadedDocument.model after QTextDocument.setHtml()."""
    if model is None:
        return
    tables = []
    pictures = []
    for block in _walk_blocks(model.blocks):
        if isinstance(block, Table):
            _table_grid(block)
            values = asdict(block)
            values.pop("cells")
            cells = []
            for cell in block.cells:
                g = asdict(cell)
                for key in ("row", "col", "row_span", "col_span", "blocks"):
                    g.pop(key)
                cells.append({"row": cell.row, "col": cell.col, "geometry": g})
            tables.append({"geometry": values, "cells": cells})
        elif isinstance(block, Paragraph):
            pictures.extend({"picture": asdict(run.picture), "width_px": run.width, "height_px": run.height} for run in block.runs if run.image)
    styles = _text_styles(model.blocks)
    apply_geometry(document, {"page": asdict(model.page), "tables": tables, "images": pictures,
        "font_styles":[asdict(style) for style in styles]})
    document._hangeul_header_footers = model.header_footers


_BORDER_STYLES = ("NONE", "SOLID", "DASH", "DOT", "DASH_DOT", "DASH_DOT_DOT", "LONG_DASH", "CIRCLE", "DOUBLE_SLIM", "SLIM_THICK", "THICK_SLIM", "SLIM_THICK_SLIM", "WAVE", "DOUBLE_WAVE", "THICK_3D", "THICK_3D_REVERSE", "SOLID_3D", "SOLID_3D_REVERSE")
_BORDER_WIDTHS = (.1, .12, .15, .2, .25, .3, .4, .5, .6, .7, 1, 1.5, 2, 3, 4, 5)
_ARROW_STYLES = ("NORMAL", "ARROW", "CONCAVE_ARROW", "EMPTY_DIAMOND", "EMPTY_CIRCLE", "EMPTY_BOX", "FILLED_DIAMOND", "FILLED_CIRCLE", "FILLED_BOX")
_ARROW_SIZES = ("SMALL_SMALL", "SMALL_MEDIUM", "SMALL_LARGE", "MEDIUM_SMALL", "MEDIUM_MEDIUM", "MEDIUM_LARGE", "LARGE_SMALL", "LARGE_MEDIUM", "LARGE_LARGE")


def _line_options_hwp(flags, alpha=0):
    return dict(endCap="ROUND" if (flags >> 6) & 15 == 0 else "FLAT",
        headStyle=_ARROW_STYLES[min((flags >> 10) & 63, 8)], tailStyle=_ARROW_STYLES[min((flags >> 16) & 63, 8)],
        headfill=int(bool(flags & (1 << 30))), tailfill=int(bool(flags & (1 << 31))),
        headSz=_ARROW_SIZES[min((flags >> 22) & 15, 8)], tailSz=_ARROW_SIZES[min((flags >> 26) & 15, 8)],
        outlineStyle="NORMAL", alpha=alpha)


def _line_flags_hwp(style, options):
    index = _BORDER_STYLES.index(style) if style in _BORDER_STYLES else 1
    flag = index | int(options.get("endCap", "FLAT") != "ROUND") << 6
    for key, shift, values in (("headStyle", 10, _ARROW_STYLES), ("tailStyle", 16, _ARROW_STYLES), ("headSz", 22, _ARROW_SIZES), ("tailSz", 26, _ARROW_SIZES)):
        value = options.get(key,values[0])
        flag |= (values.index(value) if value in values else 0) << shift
    return flag | int(bool(options.get("headfill",1))) << 30 | int(bool(options.get("tailfill",1))) << 31


def _dimension_pt(value):
    match = re.fullmatch(r"\s*([\d.]+)\s*(mm|cm|pt|in)?\s*", value or "")
    if not match:
        return .283465
    return float(match[1]) * {"mm": 72 / 25.4, "cm": 720 / 25.4, "in": 72, "pt": 1, None: 1}[match[2]]


def _position_xml(e):
    pos = _child(e, "pos")
    margin = _child(e, "outMargin")
    return ObjectPosition(_attr(pos, "treatAsChar", "1") == "1", _attr(pos, "affectLSpacing", "1") == "1",
        _attr(pos, "flowWithText", "1") == "1", _attr(pos, "allowOverlap") == "1", _attr(pos, "holdAnchorAndSO") == "1",
        _attr(pos, "vertRelTo", "PARA"), _attr(pos, "horzRelTo", "PARA"), _attr(pos, "vertAlign", "TOP"), _attr(pos, "horzAlign", "LEFT"),
        _signed32(_int(_attr(pos, "horzOffset"))) / 100, _signed32(_int(_attr(pos, "vertOffset"))) / 100, e.get("textWrap", "TOP_AND_BOTTOM"),
        e.get("textFlow", "BOTH_SIDES"), tuple(_num(_attr(margin, n)) / 100 for n in ("left", "right", "top", "bottom")), _int(e.get("zOrder")))


def _position_hwp(d):
    if len(d) < 40:
        return ObjectPosition()
    prop, y, x, _, _, z = struct.unpack_from("<IiiIIi", d, 4)
    margin = struct.unpack_from("<4h", d, 28)
    return ObjectPosition(bool(prop & 1), bool(prop & 4), bool(prop & (1 << 13)), bool(prop & (1 << 14)), False,
        ("PAPER", "PAGE", "PARA", "PARA")[(prop >> 3) & 3], ("PAPER", "PAGE", "COLUMN", "PARA")[(prop >> 8) & 3],
        ("TOP", "CENTER", "BOTTOM", "INSIDE", "OUTSIDE", "TOP", "TOP", "TOP")[(prop >> 5) & 7],
        ("LEFT", "CENTER", "RIGHT", "INSIDE", "OUTSIDE", "LEFT", "LEFT", "LEFT")[(prop >> 10) & 7],
        x / 100, y / 100, ("SQUARE", "TOP_AND_BOTTOM", "BEHIND_TEXT", "IN_FRONT_OF_TEXT", "TIGHT", "THROUGH", "SQUARE", "SQUARE")[(prop >> 21) & 7],
        ("BOTH_SIDES", "LEFT_ONLY", "RIGHT_ONLY", "LARGEST_ONLY")[(prop >> 24) & 3], tuple(v / 100 for v in margin), z)


def _object_flags(p, numbering):
    return (int(p.inline) | int(p.affect_line_spacing) << 2 | {"PAPER": 0, "PAGE": 1, "PARA": 2}.get(p.vert_rel, 2) << 3 |
        {"TOP": 0, "CENTER": 1, "BOTTOM": 2, "INSIDE": 3, "OUTSIDE": 4}.get(p.vert_align, 0) << 5 |
        {"PAPER": 0, "PAGE": 1, "COLUMN": 2, "PARA": 3}.get(p.horz_rel, 3) << 8 |
        {"LEFT": 0, "CENTER": 1, "RIGHT": 2, "INSIDE": 3, "OUTSIDE": 4}.get(p.horz_align, 0) << 10 |
        int(p.flow_with_text) << 13 | int(p.allow_overlap) << 14 | 4 << 15 | 2 << 18 |
        {"SQUARE": 0, "TOP_AND_BOTTOM": 1, "BEHIND_TEXT": 2, "IN_FRONT_OF_TEXT": 3}.get(p.wrap, 1) << 21 |
        {"BOTH_SIDES": 0, "LEFT_ONLY": 1, "RIGHT_ONLY": 2, "LARGEST_ONLY": 3}.get(p.text_flow, 0) << 24 | numbering << 26)


def _signed32(value):
    return value - 4294967296 if value >= 2147483648 else value


def _write_position_xml(e, p):
    _element(e, "hp", "pos", treatAsChar=int(p.inline), affectLSpacing=int(p.affect_line_spacing), flowWithText=int(p.flow_with_text),
        allowOverlap=int(p.allow_overlap), holdAnchorAndSO=int(p.hold_anchor), vertRelTo=p.vert_rel, horzRelTo=p.horz_rel,
        vertAlign=p.vert_align, horzAlign=p.horz_align, vertOffset=round(p.y_pt * 100), horzOffset=round(p.x_pt * 100))
    _element(e, "hp", "outMargin", **{k: round(v * 100) for k, v in zip(("left", "right", "top", "bottom"), p.out_margin)})


def _style_css(s: TextStyle):
    css = [f"font-family:'{html.escape(s.font, quote=True)}'", f"font-size:{s.size:g}pt", f"color:{s.color}"]
    if s.bold:
        css.append("font-weight:700")
    if s.italic:
        css.append("font-style:italic")
    decorations = (["underline"] if s.underline else []) + (["line-through"] if s.strike else [])
    if decorations:
        css.append("text-decoration:" + " ".join(decorations))
    if s.background and s.background.lower() not in ("none", "#ffffffff"):
        css.append("background-color:" + s.background)
    if s.superscript:
        css.append("vertical-align:super")
    if s.subscript:
        css.append("vertical-align:sub")
    if s.stretch != 100:
        css.append(f"font-stretch:{s.stretch}%")
    if s.letter_spacing:
        css.append(f"letter-spacing:{s.size * s.letter_spacing / 100:g}pt")
    return ";".join(css)


def _blocks_html(blocks):
    out = []
    for index, b in enumerate(blocks):
        following = blocks[index + 1] if index + 1 < len(blocks) else None
        if isinstance(b, Paragraph) and not any(r.text or r.image for r in b.runs) and isinstance(following, (Table, Drawing)) and b.source_index >= 0 and b.source_index == following.source_index:
            # Keep the structural anchor in the model without adding a blank
            # editable line above its table/text box.
            continue
        if isinstance(b, Drawing):
            out.append(_blocks_html(b.blocks))
        elif isinstance(b, Table):
            out.append('<table border="1" cellspacing="0" cellpadding="4" style="border-collapse:collapse;width:100%">')
            for row in range(b.rows):
                out.append("<tr>")
                for c in sorted((c for c in b.cells if c.row == row), key=lambda c: c.col):
                    bg = f' bgcolor="{html.escape(c.background)}"' if c.background else ""
                    out.append(f'<td rowspan="{c.row_span}" colspan="{c.col_span}"{bg}>')
                    out.append(_blocks_html(c.blocks) or "<p><br/></p>")
                    out.append("</td>")
                out.append("</tr>")
            out.append("</table>")
        else:
            s = b.style
            align = {"JUSTIFY": "justify", "CENTER": "center", "RIGHT": "right"}.get(s.align, "left")
            # Qt's HTML importer otherwise collapses literal tabs and repeated
            # spaces, including TAB controls imported from HWP/HWPX.
            css = f"white-space:pre-wrap;text-align:{align};margin-left:{s.left:g}pt;margin-right:{s.right:g}pt;text-indent:{s.indent:g}pt;margin-top:{s.before:g}pt;margin-bottom:{s.after:g}pt"
            if s.page_break:
                css += ";page-break-before:always"
            if s.background:
                css += ";background-color:"+html.escape(s.background)
            if s.line_type == "PERCENT":
                css += f";line-height:{s.line:g}%"
            out.append(f'<p style="{css}">')
            for r in b.runs:
                if r.image:
                    mime = mimetypes.types_map.get("." + r.image_ext, "image/png")
                    src = "data:" + mime + ";base64," + base64.b64encode(r.image).decode()
                    # QTextDocument's HTML attribute parser accepts integers;
                    # fractional values become zero and expand large bitmaps.
                    attrs = f' width="{max(1, round(r.width))}"' if r.width else ""
                    attrs += f' height="{max(1, round(r.height))}"' if r.height else ""
                    out.append(f'<img src="{src}"{attrs}/>')
                else:
                    text = html.escape(r.text).replace("\n", "<br/>").replace("\t", "&#9;")
                    out.append(f'<span style="{_style_css(r.style)}">{text}</span>')
            if not b.runs or all(not r.text and not r.image for r in b.runs):
                out.append(f'<span style="{_style_css(b.default_style)}"><br/></span>')
            out.append("</p>")
    return "".join(out)


def _wrap_html(blocks):
    return '<!DOCTYPE html><html><head><meta charset="utf-8"/></head><body>' + _blocks_html(blocks) + "</body></html>"


def _safe_xml(data):
    if len(data) > 32 * 1024 * 1024:
        raise ValueError("XML 부품이 지원 크기를 초과했습니다.")
    if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise ValueError("외부 엔터티를 포함한 XML은 지원하지 않습니다.")
    return ET.fromstring(data)


def _inflate(data):
    obj = zlib.decompressobj(-15)
    result = obj.decompress(data, MAX_BYTES + 1)
    if len(result) > MAX_BYTES or obj.unconsumed_tail:
        raise ValueError("압축된 문서가 지원 크기를 초과했습니다.")
    if not obj.eof:
        raise ValueError("문서 압축 스트림이 손상되었습니다.")
    return result


def load_document(path) -> LoadedDocument:
    path = Path(path)
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("512 MB보다 큰 문서는 지원하지 않습니다.")
    with path.open("rb") as source:
        sig = source.read(32)
    if sig[:8] == MAGIC:
        return _load_hwp(path)
    if sig.startswith(b"HWP Document File V3.00"):
        from .hwp3 import load_hwp3
        return load_hwp3(path)
    if zipfile.is_zipfile(path):
        return _load_hwpx(path)
    if path.suffix.lower() in (".html", ".htm"):
        return LoadedDocument(path.read_text(encoding="utf-8-sig"), source_path=str(path), source_format="html")
    if path.suffix.lower() in (".txt", ".md"):
        raw = path.read_bytes()
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("cp949")
        return LoadedDocument(_wrap_html([Paragraph([Run(x)]) for x in text.splitlines()]), source_path=str(path), source_format="txt")
    raise ValueError("지원하지 않는 파일입니다. HWP 5, HWPX, HTML, TXT를 선택하세요.")


def _load_hwpx(path):
    warnings, metadata = [], {}
    header_footers = []
    native_paragraphs, element_locations = {}, {}
    native_bytes = path.getvalue() if isinstance(path, io.BytesIO) else Path(path).read_bytes()
    with zipfile.ZipFile(io.BytesIO(native_bytes)) as z:
        if sum(i.file_size for i in z.infolist()) > MAX_BYTES:
            raise ValueError("HWPX 압축 해제 크기가 256 MB를 초과합니다.")
        names = z.namelist()
        if "Contents/header.xml" not in names:
            raise ValueError("HWPX 문서 머리말 부품이 없습니다.")
        head = _safe_xml(z.read("Contents/header.xml"))
        fonts, language_fonts, font_types, language_font_types = {}, {}, {}, {}
        for face in head.iter():
            if _local(face) == "fontface":
                lang_fonts = language_fonts.setdefault(face.get("lang"), {})
                lang_types = language_font_types.setdefault(face.get("lang"), {})
                for f in face:
                    if _local(f) == "font":
                        lang_fonts[f.get("id", "0")] = f.get("face", "맑은 고딕")
                        lang_types[f.get("id","0")] = f.get("type","TTF")
                        if face.get("lang") == "HANGUL":
                            fonts[f.get("id", "0")] = f.get("face", "맑은 고딕")
                            font_types[f.get("id","0")] = f.get("type","TTF")
        chars = {}
        paras = {}
        borders = {}
        for e in head.iter():
            if _local(e) == "charPr":
                font = _attr(_child(e, "fontRef"), "hangul", "0")
                underline = _attr(_child(e, "underline"), "type", "NONE") != "NONE"
                strike = _attr(_child(e, "strikeout"), "shape", "NONE") != "NONE"
                chars[e.get("id", "0")] = TextStyle(fonts.get(font, "맑은 고딕"), _num(e.get("height"), 1100) / 100,
                    _child(e, "bold") is not None, _child(e, "italic") is not None, underline, strike,
                    e.get("textColor", "#000000"), "" if e.get("shadeColor", "").lower() in ("none", "#ffffffff") else e.get("shadeColor", ""), _child(e, "supscript") is not None,
                    _child(e, "subscript") is not None, _int(_attr(_child(e, "ratio"), "hangul"), 100),
                    _int(_attr(_child(e, "spacing"), "hangul")),
                    tuple(language_fonts.get(lang, {}).get(_attr(_child(e, "fontRef"), attr, "0"), fonts.get(font, "맑은 고딕"))
                        for lang, attr in zip(("HANGUL", "LATIN", "HANJA", "JAPANESE", "OTHER", "SYMBOL", "USER"), ("hangul", "latin", "hanja", "japanese", "other", "symbol", "user"))),
                    font_type=font_types.get(font,"TTF"),
                    language_font_types=tuple(language_font_types.get(lang,{}).get(_attr(_child(e,"fontRef"),attr,"0"),font_types.get(font,"TTF"))
                        for lang,attr in zip(("HANGUL","LATIN","HANJA","JAPANESE","OTHER","SYMBOL","USER"),("hangul","latin","hanja","japanese","other","symbol","user"))),
                    use_font_space=e.get("useFontSpace") == "1", use_kerning=e.get("useKerning") == "1",
                    language_ratios=tuple(_int(_attr(_child(e,"ratio"),name),100) for name in ("hangul","latin","hanja","japanese","other","symbol","user")),
                    language_spacing=tuple(_int(_attr(_child(e,"spacing"),name)) for name in ("hangul","latin","hanja","japanese","other","symbol","user")),
                    language_relative_sizes=tuple(_int(_attr(_child(e,"relSz"),name),100) for name in ("hangul","latin","hanja","japanese","other","symbol","user")),
                    language_offsets=tuple(_int(_attr(_child(e,"offset"),name)) for name in ("hangul","latin","hanja","japanese","other","symbol","user")))
            elif _local(e) == "paraPr":
                # Modern files provide version-specific switch cases. The
                # default expresses canonical HWP units; CHAR cases may be half.
                def native_property(name):
                    direct = _child(e, name)
                    if direct is not None:
                        return direct
                    for default in (x for x in e.iter() if _local(x) == "default"):
                        value = _desc(default, name)
                        if value is not None:
                            return value
                    return _desc(e, name)
                margin = native_property("margin")
                # Paragraph margins use twice the native HWPUNIT measure. The
                # HwpUnitChar case carries already-halved compatibility values.
                in_default = any(margin is x for default in e.iter() if _local(default) == "default" for x in default.iter())
                in_case = not in_default and any(margin is x for case in e.iter() if _local(case) == "case" for x in case.iter())
                margin_divisor = 100 if in_case else 200
                vals = {n: _num(_attr(_child(margin, n), "value")) / margin_divisor if margin is not None else 0 for n in ("left", "right", "intent", "prev", "next")}
                line = native_property("lineSpacing")
                line_type = _attr(line, "type", "PERCENT")
                breaking = _child(e,"breakSetting")
                auto = _child(e,"autoSpacing")
                border = _child(e,"border")
                fill = borders.get(_attr(border,"borderFillIDRef"),BorderFill((Border("NONE"),)*4))
                paras[e.get("id", "0")] = ParaStyle(_attr(_child(e, "align"), "horizontal", "LEFT"),
                    vals["left"], vals["right"], vals["intent"], vals["prev"], vals["next"], _num(_attr(line, "value"), 160) / (1 if line_type == "PERCENT" else 100),
                    _attr(line, "type", "PERCENT"), _attr(breaking, "pageBreakBefore") == "1",
                    _attr(breaking,"breakLatinWord","KEEP_WORD"),_attr(breaking,"breakNonLatinWord","BREAK_WORD"),
                    _attr(_child(e,"align"),"vertical","BASELINE"),_int(e.get("condense")),e.get("snapToGrid")=="1",e.get("fontLineHeight")=="1",
                    _attr(breaking,"widowOrphan")=="1",_attr(breaking,"keepWithNext")=="1",_attr(breaking,"keepLines")=="1",
                    _attr(breaking,"lineWrap","BREAK"),_attr(auto,"eAsianEng")=="1",_attr(auto,"eAsianNum")=="1",
                    fill.background if fill.background != "none" else "",fill.borders,
                    tuple(_num(_attr(border,"offset"+k))/100 for k in ("Left","Right","Top","Bottom")),
                    _attr(border,"connect")=="1",_attr(border,"ignoreMargin")=="1")
            elif _local(e) == "borderFill":
                fill = _desc(e, "winBrush")
                sides = tuple(Border(_attr(_child(e, n), "type", "NONE"), _dimension_pt(_attr(_child(e, n), "width")),
                    _attr(_child(e, n), "color", "#000000")) for n in ("leftBorder", "rightBorder", "topBorder", "bottomBorder"))
                borders[e.get("id")] = BorderFill(sides, fill.get("faceColor", "") if fill is not None else "")
        bindata = {}
        if "Contents/content.hpf" in names:
            package = _safe_xml(z.read("Contents/content.hpf"))
            for e in package.iter():
                if _local(e) == "item":
                    href = e.get("href", "").lstrip("/")
                    if href not in names and "Contents/" + href in names:
                        href = "Contents/" + href
                    bindata[e.get("id")] = href
                elif _local(e) in ("title", "language"):
                    metadata[_local(e)] = e.text or ""
        def picture(e):
            img = _desc(e, "img")
            ref = _attr(img, "binaryItemIDRef")
            name = bindata.get(ref)
            if not name:
                name = next((n for n in names if n.startswith("BinData/") and Path(n).stem == ref), "")
            if name not in names:
                _warning(warnings, "일부 그림의 바이너리 데이터를 찾지 못했습니다.")
                return Run("[그림]")
            data = z.read(name)
            sz = _child(e, "sz")
            original, clip, flip, rotation = _child(e, "orgSz"), _child(e, "imgClip"), _child(e, "flip"), _child(e, "rotationInfo")
            rect = _child(e, "imgRect")
            pg = PictureGeometry(_position_xml(e), _num(_attr(original, "width")) / 100, _num(_attr(original, "height")) / 100,
                tuple(_num(_attr(clip, n)) / 100 for n in ("left", "top", "right", "bottom")),
                tuple((_num(_attr(_child(rect, n), "x")) / 100, _num(_attr(_child(rect, n), "y")) / 100) for n in ("pt0", "pt1", "pt2", "pt3")),
                _num(_attr(rotation, "angle")), _attr(flip, "horizontal") == "1", _attr(flip, "vertical") == "1",
                _int(_attr(img, "bright")), _int(_attr(img, "contrast")), _attr(img, "effect", "REAL_PIC"), _int(_attr(img, "alpha")),
                tuple(_num(_attr(_child(e, "inMargin"), n)) / 100 for n in ("left", "right", "top", "bottom")))
            render = _child(e, "renderingInfo")
            if render is not None:
                pg.transforms = {_local(m): [_num(m.get("e" + str(i))) for i in range(1, 7)] for m in render}
            dims = _child(e, "imgDim")
            pg.image_width_pt, pg.image_height_pt = _num(_attr(dims, "dimwidth")) / 100, _num(_attr(dims, "dimheight")) / 100
            current = _child(e,"curSz")
            if current is not None:
                pg.current_width_pt,pg.current_height_pt = _num(current.get("width"))/100,_num(current.get("height"))/100
            offset = _child(e,"offset")
            pg.offset_x_pt,pg.offset_y_pt = _signed32(_attr(offset,"x"))/100,_signed32(_attr(offset,"y"))/100
            if rotation is not None:
                pg.rotation_center_x_pt,pg.rotation_center_y_pt = _num(rotation.get("centerX"))/100,_num(rotation.get("centerY"))/100
                pg.rotate_image = rotation.get("rotateimage","1") == "1"
            if not pg.position.inline or pg.rotation or pg.crop != (0, 0, pg.image_width_pt or pg.original_width_pt, pg.image_height_pt or pg.original_height_pt):
                _warning(warnings, "그림의 부유 위치·자르기·회전은 저장 데이터에 보존하지만 편집 화면에서는 본문 흐름으로 표시합니다.")
            return Run(image=data, image_ext=Path(name).suffix.lstrip(".") or "png",
                       width=_num(_attr(sz, "width")) / 75, height=_num(_attr(sz, "height")) / 75, picture=pg)
        def table(e, owner=None):
            rows, cols = _int(e.get("rowCnt"), 1), _int(e.get("colCnt"), 1)
            if rows * cols > 100000:
                raise ValueError("표의 셀 개수가 너무 많습니다.")
            fill = borders.get(e.get("borderFillIDRef"), BorderFill())
            t = Table(rows, cols, width=_num(_attr(_child(e, "sz"), "width"), 48000) / 100,
                height=_num(_attr(_child(e, "sz"), "height")) / 100, borders=fill.borders, background=fill.background,
                padding=tuple(_num(_attr(_child(e, "inMargin"), n)) / 100 for n in ("left", "right", "top", "bottom")),
                cell_spacing=_num(e.get("cellSpacing")) / 100, page_break=e.get("pageBreak", "CELL"), repeat_header=e.get("repeatHeader") == "1",
                position=_position_xml(e))
            if owner is not None:
                t.anchor_segments, t.source_para_id, t.source_index = owner.line_segments, owner.source_id, owner.source_index
                t.scope = dict(owner.scope)
            for row, tr in enumerate(x for x in e if _local(x) == "tr"):
                for col, tc in enumerate(x for x in tr if _local(x) == "tc"):
                    addr, span, size = _child(tc, "cellAddr"), _child(tc, "cellSpan"), _child(tc, "cellSz")
                    sub = _child(tc, "subList")
                    fill = borders.get(tc.get("borderFillIDRef"), BorderFill())
                    raw_margins = tuple(_int(_attr(_child(tc, "cellMargin"), n), -1) for n in ("left", "right", "top", "bottom"))
                    inherited = tuple(tc.get("hasMargin") == "0" or value in (-1, 65535, 4294967295) for value in raw_margins)
                    t.cells.append(Cell(_int(_attr(addr, "rowAddr"), row), _int(_attr(addr, "colAddr"), col),
                        max(1, _int(_attr(span, "rowSpan"), 1)), max(1, _int(_attr(span, "colSpan"), 1)),
                        parse_blocks(sub, {"kind": "cell", "table_source_index": t.source_index, "row": _int(_attr(addr, "rowAddr"), row), "col": _int(_attr(addr, "colAddr"), col), "parent": t.scope}) if sub is not None else [], _num(_attr(size, "width")) / 100,
                        fill.background, height=_num(_attr(size, "height")) / 100,
                        padding=tuple(t.padding[i] if inherited[i] else raw_margins[i] / 100 for i in range(4)),
                        borders=fill.borders, vertical_align=_attr(sub, "vertAlign", "CENTER"), header=tc.get("header") == "1", padding_inherited=inherited))
            _table_grid(t)
            return t
        def drawing(e, owner):
            size, org, line, text = _child(e, "sz"), _child(e, "orgSz"), _child(e, "lineShape"), _child(e, "drawText")
            border = Border(_attr(line, "style", "NONE"), _num(_attr(line, "width")) / 100, _attr(line, "color", "#000000"))
            fill = _desc(e, "winBrush")
            d = Drawing(kind=_local(e), width=_num(_attr(size, "width")) / 100, height=_num(_attr(size, "height")) / 100, position=_position_xml(e),
                padding=tuple(_num(_attr(_child(text, "textMargin"), n)) / 100 for n in ("left", "right", "top", "bottom")),
                borders=(border,) * 4, background=_attr(fill, "faceColor"), anchor_segments=owner.line_segments,
                source_para_id=owner.source_id, source_index=owner.source_index, scope=dict(owner.scope))
            if line is not None:
                d.line_options = {k: (_int(line.get(k)) if k in ("headfill","tailfill","alpha") else line.get(k)) for k in
                    ("endCap","headStyle","tailStyle","headfill","tailfill","headSz","tailSz","outlineStyle","alpha") if line.get(k) is not None}
            d.shape.original_width_pt, d.shape.original_height_pt = _num(_attr(org, "width")) / 100, _num(_attr(org, "height")) / 100
            current = _child(e,"curSz")
            if current is not None:
                d.shape.current_width_pt,d.shape.current_height_pt = _num(current.get("width"))/100,_num(current.get("height"))/100
            offset,rotation = _child(e,"offset"),_child(e,"rotationInfo")
            d.shape.offset_x_pt,d.shape.offset_y_pt = _signed32(_int(_attr(offset,"x")))/100,_signed32(_int(_attr(offset,"y")))/100
            flip = _child(e, "flip")
            d.shape.flip_h, d.shape.flip_v = _attr(flip, "horizontal") == "1", _attr(flip, "vertical") == "1"
            if rotation is not None:
                d.shape.rotation = _num(rotation.get("angle"))
                d.shape.rotation_center_x_pt,d.shape.rotation_center_y_pt = _num(rotation.get("centerX"))/100,_num(rotation.get("centerY"))/100
                d.shape.rotate_image = rotation.get("rotateimage","0") == "1"
            render = _child(e, "renderingInfo")
            if render is not None:
                d.shape.transforms = {_local(m): [_num(m.get("e" + str(i))) for i in range(1, 7)] for m in render}
                d.geometry["transform_chain"] = [(_local(m), [_num(m.get("e"+str(i))) for i in range(1,7)]) for m in render]
            def point(element):
                return (_signed32(_int(_attr(element, "x"))) / 100, _signed32(_int(_attr(element, "y"))) / 100)
            if d.kind == "rect":
                if all(_child(e, name) is not None for name in ("pt0", "pt1", "pt2", "pt3")):
                    d.shape.rect = tuple(point(_child(e, name)) for name in ("pt0", "pt1", "pt2", "pt3"))
                d.geometry["ratio"] = _int(e.get("ratio"))
            elif d.kind == "line":
                d.geometry["points"] = [point(_child(e, name)) for name in ("startPt", "endPt")]
                d.geometry["isReverseHV"] = e.get("isReverseHV") == "1"
            elif d.kind == "polygon":
                d.geometry["points"] = [point(n) for n in e if _local(n) == "pt"]
            elif d.kind in ("ellipse", "arc"):
                for name in ("center", "ax1", "ax2", "start1", "end1", "start2", "end2"):
                    if _child(e, name) is not None:
                        d.geometry[name] = point(_child(e, name))
                d.geometry.update(hasArcPr=e.get("hasArcPr") == "1", arcType=e.get("arcType", "NORMAL"), type=e.get("type", "NORMAL"), intervalDirty=e.get("intervalDirty") == "1")
            elif d.kind == "curve":
                d.geometry["segments"] = [{"type": n.get("type", "LINE"), **{key: _signed32(_int(n.get(key))) / 100 for key in ("x1", "y1", "x2", "y2")}} for n in e if _local(n) == "seg"]
            sub = _child(text, "subList") if text is not None else None
            d.vertical_align = _attr(sub, "vertAlign", "CENTER")
            if sub is not None:
                d.blocks = parse_blocks(sub, {"kind": "drawing", "source_index": d.source_index, "width_pt": d.width, "height_pt": d.height,
                    "position": asdict(d.position), "padding": d.padding, "parent": d.scope})
            if d.blocks:
                _warning(warnings, "그리기 개체의 텍스트는 편집 화면에서 본문으로 표시합니다. 원본 배치 화면에서는 도형의 좌표와 모양을 사용합니다.")
            if d.kind in ("curve", "arc") or d.geometry.get("hasArcPr"):
                _warning(warnings, "곡선·원호의 보간과 구역 도형은 화면 동일성이 검증되지 않았습니다. 원본 보존 저장에서는 해당 XML을 유지합니다.")
            return d
        para_counter = [0]
        def paragraph(e, scope=None):
            style = paras.get(e.get("paraPrIDRef", "0"), ParaStyle())
            if e.get("pageBreak") == "1":
                style = ParaStyle(**{**style.__dict__, "page_break": True})
            p = Paragraph(style=style)
            p.source_index = para_counter[0]
            para_counter[0] += 1
            if id(e) in element_locations:
                native_paragraphs[str(p.source_index)] = element_locations[id(e)]
            p.scope = dict(scope or {"kind": "body"})
            p.source_id = _int(e.get("id"))
            first_run = next((r for r in e if _local(r) == "run"), None)
            p.default_style = chars.get(_attr(first_run,"charPrIDRef","0"),TextStyle())
            last_run = next((r for r in reversed(list(e)) if _local(r) == "run"), None)
            if last_run is not None:
                # A native empty trailing hp:run carries the paragraph mark's
                # font independently of the visible text's last font.
                p.end_style = chars.get(last_run.get("charPrIDRef", "0"), TextStyle())
            layout = _child(e, "linesegarray")
            if layout is not None:
                for segment in layout:
                    p.line_segments.append({k: _int(segment.get(v)) if k in ("text_start", "flags") else _num(segment.get(v)) / 100
                        for k, v in (("text_start", "textpos"), ("y_pt", "vertpos"), ("height_pt", "vertsize"), ("text_height_pt", "textheight"),
                        ("baseline_pt", "baseline"), ("spacing_pt", "spacing"), ("x_pt", "horzpos"), ("width_pt", "horzsize"), ("flags", "flags"))})
            blocks = [p]
            cache = []
            for run in e:
                if _local(run) != "run":
                    continue
                ts = chars.get(run.get("charPrIDRef", "0"), TextStyle())
                for x in run:
                    name = _local(x)
                    if name == "t":
                        text = x.text or ""
                        for child in x:
                            n = _local(child)
                            text += {"lineBreak": "\n", "tab": "\t", "nbSpace": "\u00a0", "fwSpace": "\u3000"}.get(n, "") + (child.tail or "")
                        p.runs.append(Run(text, ts, source_start=len("".join(cache).encode("utf-16le")) // 2))
                        cache.append(text.replace("\t", "\t" + "\u200b" * 7))
                    elif name == "tbl":
                        t = table(x, p)
                        t.anchor_style = ts
                        t.anchor_text_start = len("".join(cache).encode("utf-16le")) // 2
                        blocks.append(t)
                        cache.append("\ufffc" + "\u200b" * 7)
                    elif name == "pic":
                        image_run = picture(x)
                        image_run.style = ts
                        image_run.source_start = len("".join(cache).encode("utf-16le")) // 2
                        p.runs.append(image_run)
                        cache.append("\ufffc" + "\u200b" * 7)
                    elif name in ("rect", "ellipse", "line", "polygon", "curve", "arc"):
                        d = drawing(x, p)
                        d.anchor_style = ts
                        d.anchor_text_start = len("".join(cache).encode("utf-16le")) // 2
                        blocks.append(d)
                        cache.append("\ufffc" + "\u200b" * 7)
                    elif name == "equation":
                        script = _desc(x, "script")
                        p.runs.append(Run("[수식: " + (script.text or "" if script is not None else "") + "]", ts))
                        _warning(warnings, "수식은 편집 가능한 텍스트로 가져옵니다. 수식 배치는 보존되지 않습니다.")
                    elif name in ("container", "ole", "chart", "textart", "video"):
                        cache.append("\ufffc" + "\u200b" * 7)
                        sub = _desc(x, "subList")
                        if sub is not None:
                            blocks.extend(parse_blocks(sub, dict(p.scope)))
                        _warning(warnings, "도형·차트·OLE·글맵시·동영상의 원래 배치는 지원하지 않습니다. 글상자 텍스트는 가능한 범위에서 가져옵니다.")
                    elif name == "ctrl":
                        control_start = len("".join(cache).encode("utf-16le")) // 2
                        cache.append("\u200b" * (8 * len(x)))
                        for ctrl_index, ctrl in enumerate(x):
                            if _local(ctrl) in ("header", "footer"):
                                sub = _desc(ctrl, "subList")
                                part = HeaderFooter(_local(ctrl), parse_blocks(sub, {**p.scope, "kind": _local(ctrl)}) if sub is not None else [], ctrl.get("applyPageType", "BOTH"),
                                    _num(_attr(sub, "textWidth")) / 100, _num(_attr(sub, "textHeight")) / 100, p.source_id,
                                    p.source_index, _attr(sub,"vertAlign","TOP"), scope=dict(p.scope))
                                for block in _walk_blocks(part.blocks):
                                    if isinstance(block, Paragraph):
                                        block.scope = {**block.scope, "kind": part.kind,
                                            **{key: p.scope[key] for key in ("section_index",) if key in p.scope}}
                                header_footers.append(part)
                                _warning(warnings, "머리말·꼬리말은 문서 모델에 보존하지만 본문 편집 화면에서는 반복 표시하지 않습니다.")
                            elif _local(ctrl) in ("footNote", "endNote"):
                                blocks.extend(parse_blocks(ctrl, dict(p.scope)))
                                _warning(warnings, "각주·미주를 본문 텍스트로 가져옵니다.")
                            elif _local(ctrl) == "autoNum":
                                p.runs.append(Run("", ts, source_start=control_start + 8 * ctrl_index, field={"kind": "auto_number", "number_type": ctrl.get("numType", "PAGE"), "number": _int(ctrl.get("num"), 1)}))
                            elif _local(ctrl) == "newNum":
                                p.runs.append(Run("", ts, source_start=control_start + 8 * ctrl_index, field={"kind": "new_number", "number_type": ctrl.get("numType", "PAGE"), "number": _int(ctrl.get("num"),1)}))
                                if ctrl.get("numType","PAGE") == "PAGE":
                                    metadata["page_start_number"] = _int(ctrl.get("num"),1)
                            elif _local(ctrl) in ("fieldBegin", "fieldEnd", "bookmark"):
                                _warning(warnings, "필드·책갈피의 표시 텍스트만 가져옵니다. 자동 갱신 기능은 보존되지 않습니다.")
                    elif name == "secPr":
                        cache.append("\u200b" * 8)
                        page = _child(x, "pagePr")
                        if page is not None:
                            values = {"width_pt": _num(page.get("width")) / 100, "height_pt": _num(page.get("height")) / 100,
                                **{k + "_pt": _num(_attr(_child(page, "margin"), k)) / 100 for k in ("left", "right", "top", "bottom", "header", "footer", "gutter")},
                                "landscape": page.get("landscape") == "NARROWLY", "gutter_type": page.get("gutterType", "LEFT_ONLY")}
                            metadata.setdefault("page", values)
                            metadata.setdefault("section_pages", []).append(values)
            p.cache_text = "".join(cache)
            return blocks
        def parse_blocks(parent, scope=None):
            blocks = []
            for e in parent:
                if _local(e) == "p":
                    blocks.extend(paragraph(e, scope))
                elif _local(e) in ("subList", "header", "footer", "footNote", "endNote"):
                    blocks.extend(parse_blocks(e, scope))
            return blocks
        blocks = []
        sections = sorted((n for n in names if re.fullmatch(r"Contents/section\d+\.xml", n)), key=lambda n: _int(re.search(r"\d+", n).group()))
        if not sections:
            raise ValueError("HWPX 본문 섹션이 없습니다.")
        for section_index, name in enumerate(sections):
            root = _safe_xml(z.read(name))
            element_locations = {id(e): (name, i) for i, e in enumerate(x for x in root.iter() if _local(x) == "p")}
            blocks.extend(parse_blocks(root, {"kind": "body", "section_index": section_index}))
        metadata["sections"] = len(sections)
        if len(sections) > 1:
            _warning(warnings, "여러 구역을 하나의 편집 본문으로 가져옵니다. 구역별 용지와 다단 설정은 보존되지 않습니다.")
        metadata["tables"] = sum(isinstance(x, Table) for x in blocks)
        metadata["paragraphs"] = sum(isinstance(x, Paragraph) for x in blocks)
    _warning(warnings, "한컴과 글꼴·줄바꿈·쪽 나눔이 다를 수 있습니다. 지원되는 글자·글자 서식·문단 서식 수정은 원본 XML에 반영하며, 그 범위를 벗어난 변경은 재구성 내보내기가 필요할 수 있습니다.")
    _page_metadata(metadata)
    model = DocumentModel(blocks, PageLayout(**metadata.get("page", {})), [PageLayout(**p) for p in metadata.get("section_pages", [])], header_footers)
    model._hwpx_source = {"data": native_bytes, "baseline": model_to_data(model), "paragraphs": native_paragraphs}
    return LoadedDocument(_wrap_html(blocks), warnings, metadata, str(path), "hwpx", model)


@dataclass
class Record:
    tag: int
    level: int
    data: bytes
    children: list = field(default_factory=list)
    index: int = -1


def _records(data):
    pos = 0
    records = []
    while pos + 4 <= len(data):
        h, = struct.unpack_from("<I", data, pos)
        pos += 4
        tag, level, size = h & 1023, (h >> 10) & 1023, h >> 20
        if size == 4095:
            if pos + 4 > len(data):
                raise ValueError("HWP 레코드 헤더가 손상되었습니다.")
            size, = struct.unpack_from("<I", data, pos)
            pos += 4
        if pos + size > len(data):
            raise ValueError("HWP 레코드 길이가 올바르지 않습니다.")
        records.append(Record(tag, level, data[pos:pos + size], index=len(records)))
        pos += size
    # Some older files append zero bytes after their last record.
    if data[pos:].strip(b"\0"):
        raise ValueError("HWP 레코드 끝에 불완전한 데이터가 있습니다.")
    return records


def _record_tree(records):
    root = Record(0, -1, b"")
    stack = [root]
    for r in records:
        while stack[-1].level >= r.level:
            stack.pop()
        stack[-1].children.append(r)
        stack.append(r)
    return root


def _hwp_text(data):
    """Return text with original UTF-16 offsets for character-style mapping."""
    chars = []
    pos = 0
    extended = {1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 12, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23}
    while pos + 2 <= len(data):
        unit = struct.unpack_from("<H", data, pos)[0]
        start = pos // 2
        if unit < 32:
            if unit == 9:
                chars.append((start, "\t"))
            elif unit == 10:
                chars.append((start, "\n"))
            elif unit == 24:
                chars.append((start, "-"))
            elif unit == 30:
                chars.append((start, "\u00a0"))
            elif unit == 31:
                chars.append((start, "\u3000"))
            pos += 16 if unit in extended else 2
        elif 0xD800 <= unit <= 0xDBFF and pos + 4 <= len(data):
            chars.append((start, data[pos:pos + 4].decode("utf-16le", "replace")))
            pos += 4
        else:
            chars.append((start, chr(unit)))
            pos += 2
    return chars


def _hwp_cache_text(data):
    chars, controls = [], []
    pos = 0
    extended = {1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 12, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23}
    while pos + 2 <= len(data):
        value = struct.unpack_from("<H", data, pos)[0]
        if value in extended:
            chars.append(("\t" if value == 9 else "\ufffc" if value == 11 else "\u200b") + "\u200b" * 7)
            if value == 11:
                controls.append((pos // 2, data[pos + 2:pos + 6][::-1]))
            pos += 16
        elif value < 32:
            chars.append({10: "\n", 24: "-", 30: "\u00a0", 31: "\u3000"}.get(value, "\u200b"))
            pos += 2
        else:
            if 0xD800 <= value <= 0xDBFF and pos + 4 <= len(data):
                chars.append(data[pos:pos+4].decode("utf-16le", "replace"))
                pos += 4
            else:
                chars.append(chr(value))
                pos += 2
    return "".join(chars), controls


def paragraph_cache_text(paragraph, start=0, end=None):
    """Slice source text by UTF-16 offset; omit control placeholders."""
    encoded = paragraph.cache_text.encode("utf-16le")
    stop = len(encoded) if end is None else end * 2
    return encoded[start * 2:stop].decode("utf-16le", "replace").replace("\u200b", "").replace("\ufffc", "")


def _load_hwp(path):
    original_bytes = path.read_bytes()
    cfb = CompoundFile(original_bytes)
    if "FileHeader" not in cfb.streams:
        raise ValueError("HWP FileHeader 스트림이 없습니다.")
    header = cfb.read("FileHeader")
    if len(header) < 40 or not header.startswith(b"HWP Document File"):
        raise ValueError("HWP 5 문서가 아닙니다. HWP 3 문서는 지원하지 않습니다.")
    version = struct.unpack_from("<I", header, 32)[0]
    flags = struct.unpack_from("<I", header, 36)[0]
    if flags & (2 | 16 | 256 | 512):
        raise ValueError("암호·DRM 보호 문서는 암호 또는 보호 해제가 필요합니다.")
    warnings, metadata = [], {"version": ".".join(str((version >> s) & 255) for s in (24, 16, 8, 0)), "compressed": bool(flags & 1)}
    header_footers = []
    paragraph_locations = {}
    distribution = bool(flags & 4)
    distribution_options = 0
    def read(name):
        nonlocal distribution_options
        raw = cfb.read(name)
        if distribution and name.startswith("ViewText/"):
            from .hwp_distribution import decode_view_stream
            raw, options = decode_view_stream(raw)
            distribution_options |= options
        return _inflate(raw) if flags & 1 else raw
    info = _records(read("DocInfo"))
    fonts, font_types, chars, paras, borders, binaries = [], [], {}, {}, {}, {}
    mappings = next((r.data for r in info if r.tag == 17), b"")
    font_count = struct.unpack_from("<I", mappings, 4)[0] if len(mappings) >= 8 else 0
    font_counts = struct.unpack_from("<7I", mappings, 4) if len(mappings) >= 32 else (font_count,) * 7
    for r in info:
        d = r.data
        if r.tag == 19 and len(d) >= 3:
            length = struct.unpack_from("<H", d, 1)[0]
            fonts.append(d[3:3 + length * 2].decode("utf-16le", "replace"))
            font_types.append(("UNKNOWN","TTF","HFT","UNKNOWN_3")[d[0] & 3])
        elif r.tag == 21 and len(d) >= 66:
            fid, = struct.unpack_from("<H", d)
            size, prop = struct.unpack_from("<iI", d, 42)
            color, _, shade = struct.unpack_from("<III", d, 52)
            chars[len(chars)] = TextStyle(fonts[fid] if fid < min(font_count or len(fonts), len(fonts)) else "맑은 고딕",
                max(1, size / 100), bool(prop & 2), bool(prop & 1), bool(prop & 12), bool(prop & (7 << 18)),
                _colorref(color), "" if shade == 0xFFFFFFFF else _colorref(shade), bool(prop & (1 << 15)), bool(prop & (1 << 16)), d[14], struct.unpack_from("<b", d, 21)[0],
                tuple(fonts[index] if index < len(fonts) else "맑은 고딕" for index in
                    (sum(font_counts[:lang]) + struct.unpack_from("<H", d, lang * 2)[0] for lang in range(7))),
                font_type=font_types[fid] if fid < len(font_types) else "TTF",
                language_font_types=tuple(font_types[index] if index<len(font_types) else "TTF" for index in
                    (sum(font_counts[:lang])+struct.unpack_from("<H",d,lang*2)[0] for lang in range(7))),
                use_font_space=bool(prop & (1 << 25)), use_kerning=bool(prop & (1 << 30)),
                language_ratios=tuple(d[14:21]), language_spacing=struct.unpack_from("<7b", d, 21),
                language_relative_sizes=tuple(d[28:35]), language_offsets=struct.unpack_from("<7b", d, 35))
        elif r.tag == 25 and len(d) >= 28:
            prop, left, right, indent, before, after, line = struct.unpack_from("<I6i", d)
            line_type = prop & 3
            if len(d) >= 54:
                line_type = struct.unpack_from("<I", d, 46)[0] & 3
                line = struct.unpack_from("<i", d, 50)[0]
            prop2 = struct.unpack_from("<I",d,42)[0] if len(d)>=46 else 0
            fill_id = struct.unpack_from("<H",d,32)[0] if len(d)>=34 else 1
            fill = borders.get(fill_id,BorderFill((Border("NONE"),)*4))
            paras[len(paras)] = ParaStyle({0: "JUSTIFY", 1: "LEFT", 2: "RIGHT", 3: "CENTER",4:"DISTRIBUTE",5:"DISTRIBUTE_SPACE"}.get((prop >> 2) & 7, "JUSTIFY"),
                left / 200, right / 200, indent / 200, before / 200, after / 200, line if not line_type else line / 100,
                {0: "PERCENT", 1: "FIXED", 2: "BETWEEN_LINES",3:"AT_LEAST"}.get(line_type, "PERCENT"), bool(prop & (1 << 19)),
                {0:"KEEP_WORD",1:"HYPHENATION",2:"BREAK_WORD"}.get((prop>>5)&3,"KEEP_WORD"),
                "KEEP_WORD" if prop & (1<<7) else "BREAK_WORD",  # native XML cross-checked
                {0:"BASELINE",1:"TOP",2:"CENTER",3:"BOTTOM"}[(prop>>20)&3],(prop>>9)&127,
                bool(prop & (1<<8)),bool(prop & (1<<22)),bool(prop & (1<<16)),bool(prop & (1<<17)),bool(prop & (1<<18)),
                {0:"BREAK",1:"SQUEEZE",2:"KEEP"}.get(prop2&3,"BREAK"),bool(prop2 & 16),bool(prop2 & 32),
                fill.background,fill.borders,tuple(v/100 for v in struct.unpack_from("<4h",d,34)) if len(d)>=42 else (0,)*4,
                bool(prop & (1<<28)),bool(prop & (1<<29)))
        elif r.tag == 20:
            color = ""
            if len(d) >= 44 and struct.unpack_from("<I", d, 32)[0] & 1:
                raw_color = struct.unpack_from("<I", d, 36)[0]
                color = "" if raw_color == 0xFFFFFFFF else _colorref(raw_color)
            sides = []
            for offset in range(2, 26, 6):
                if len(d) >= offset + 6:
                    style, width, rgb = struct.unpack_from("<BBI", d, offset)
                    sides.append(Border(_BORDER_STYLES[min(style, len(_BORDER_STYLES) - 1)], _BORDER_WIDTHS[min(width, 15)] * 72 / 25.4, _colorref(rgb)))
            borders[len(borders) + 1] = BorderFill(tuple(sides) if len(sides) == 4 else (Border(),) * 4, color)
        elif r.tag == 18 and len(d) >= 6:
            prop, = struct.unpack_from("<H", d)
            if prop & 15 == 1:
                bid, length = struct.unpack_from("<HH", d, 2)
                ext = d[6:6 + length * 2].decode("utf-16le", "replace")
                binaries[bid] = (prop, ext)
    def nodes(node):
        for x in node.children:
            yield x
            yield from nodes(x)
    def picture(ctrl, node):
        if len(node.data) < 73:
            _warning(warnings, "일부 HWP 그림 레코드가 불완전하여 읽지 못했습니다.")
            return Run("[그림]")
        bid, = struct.unpack_from("<H", node.data, 71)
        prop, ext = binaries.get(bid, (0, ""))
        name = next((n for n in cfb.streams if n.lower().startswith("bindata/bin%04x." % bid)), "")
        if not name:
            _warning(warnings, "일부 HWP 그림 데이터를 찾지 못했습니다.")
            return Run("[그림]")
        raw = cfb.read(name)
        mode = (prop >> 4) & 3
        if mode == 1 or (mode == 0 and flags & 1):
            try:
                raw = _inflate(raw)
            except zlib.error:
                # Native documents may store already-compressed image formats
                # verbatim even with a general BinData compression attribute.
                if not raw.startswith((b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n", b"GIF87a", b"GIF89a", b"BM", b"II*\0", b"MM\0*")):
                    raise
        width, height = struct.unpack_from("<II", ctrl.data, 16) if len(ctrl.data) >= 24 else (15000, 11250)
        d = node.data
        corners = struct.unpack_from("<8i", d, 12)
        pg = PictureGeometry(position=_position_hwp(ctrl.data), rect=tuple((corners[2 * i] / 100, corners[2 * i + 1] / 100) for i in range(4)),
            crop=tuple(v / 100 for v in struct.unpack_from("<4i", d, 44)), in_margin=tuple(v / 100 for v in struct.unpack_from("<4h", d, 60)),
            # Hancom's HWP export stores contrast then brightness here. The
            # published table labels the opposite order; native HWP/HWPX pairs
            # with independently varied settings establish the stored order.
            contrast=struct.unpack_from("<b", d, 68)[0], brightness=struct.unpack_from("<b", d, 69)[0],
            effect={0:"REAL_PIC",1:"GRAY_SCALE",2:"BLACK_WHITE",4:"PATTERN8x8"}.get(d[70],f"UNSUPPORTED_{d[70]}"),
            alpha=d[90] if len(d) > 90 else 0)
        pg.border_opacity = d[73] if len(d) > 73 else 0
        if len(d) >= 90:
            pg.image_width_pt, pg.image_height_pt = (v / 100 for v in struct.unpack_from("<2I", d, 82))
        component = next((x for x in nodes(ctrl) if x.tag == 76), None)
        if component is not None and len(component.data) >= 50:
            s = component.data
            offset = 8 if s[:4] == s[4:8] else 4
            pg.original_width_pt, pg.original_height_pt = (v / 100 for v in struct.unpack_from("<2I", s, offset + 12))
            pg.current_width_pt,pg.current_height_pt = (v/100 for v in struct.unpack_from("<2I",s,offset+20))
            flags_shape = struct.unpack_from("<I", s, offset + 28)[0]
            pg.native_flags = flags_shape
            pg.rotate_image = bool(flags_shape & (1<<19))
            pg.offset_x_pt,pg.offset_y_pt = (v/100 for v in struct.unpack_from("<ii",s,offset))
            pg.flip_h, pg.flip_v = bool(flags_shape & 1), bool(flags_shape & 2)
            pg.rotation = struct.unpack_from("<H", s, offset + 32)[0]
            pg.rotation_center_x_pt,pg.rotation_center_y_pt = (v/100 for v in struct.unpack_from("<ii",s,offset+34))
            start = offset + 44
            if len(s) >= start + 144:
                pg.transforms = {name: list(struct.unpack_from("<6d", s, start + 48 * i)) for i, name in enumerate(("transMatrix", "scaMatrix", "rotMatrix"))}
        if not pg.original_width_pt:
            pg.original_width_pt, pg.original_height_pt = max(x for x, _ in pg.rect), max(y for _, y in pg.rect)
        if not pg.position.inline or pg.rotation or pg.crop != (0, 0, pg.image_width_pt or pg.original_width_pt, pg.image_height_pt or pg.original_height_pt):
            _warning(warnings, "그림의 부유 위치·자르기·회전은 저장 데이터에 보존하지만 편집 화면에서는 본문 흐름으로 표시합니다.")
        return Run(image=raw, image_ext=ext or Path(name).suffix.lstrip("."), width=width / 75, height=height / 75, picture=pg)
    def paragraph(node, scope=None):
        d = node.data
        pid = struct.unpack_from("<H", d, 8)[0] if len(d) >= 10 else 0
        ps = paras.get(pid, ParaStyle())
        if len(d) > 11 and d[11] & 4:
            ps = ParaStyle(**{**ps.__dict__, "page_break": True})
        text = b"".join(x.data for x in node.children if x.tag == 67)
        shape_data = b"".join(x.data for x in node.children if x.tag == 68)
        shapes = [struct.unpack_from("<II", shape_data, i) for i in range(0, len(shape_data) - 7, 8)] or [(0, 0)]
        shapes.sort()
        p = Paragraph(style=ps)
        paragraph_locations[id(p)] = (name, node.index)
        p.default_style = chars.get(shapes[0][1], TextStyle())
        end_position = len(text) // 2 - 1 if text.endswith(b"\r\0") else max(0, (struct.unpack_from("<I",d)[0] & 0x7fffffff) - 1)
        end_shape = next((cid for pos,cid in reversed(shapes) if pos <= end_position),shapes[0][1])
        p.end_style = chars.get(end_shape, TextStyle())
        p.scope = dict(scope or {"kind": "body"})
        p.scope["section_index"] = section_index
        p.source_index = node.index
        p.cache_text, source_controls = _hwp_cache_text(text)
        available_controls = list(source_controls)
        p.source_id = struct.unpack_from("<I", d, 18)[0] if len(d) >= 22 else 0
        layout = b"".join(x.data for x in node.children if x.tag == 69)
        for offset in range(0, len(layout) - 35, 36):
            values = struct.unpack_from("<8iI", layout, offset)
            p.line_segments.append(dict(zip(("text_start", "y_pt", "height_pt", "text_height_pt", "baseline_pt", "spacing_pt", "x_pt", "width_pt", "flags"),
                (v if i in (0, 8) else v / 100 for i, v in enumerate(values)))))
        shape_index = 0
        for pos, ch in _hwp_text(text):
            while shape_index + 1 < len(shapes) and pos >= shapes[shape_index + 1][0]:
                shape_index += 1
            style = chars.get(shapes[shape_index][1], TextStyle())
            if p.runs and p.runs[-1].style == style and not p.runs[-1].image and p.runs[-1].source_start + len(p.runs[-1].text.encode("utf-16le")) // 2 == pos:
                p.runs[-1].text += ch
            else:
                p.runs.append(Run(ch, style, source_start=pos))
        blocks = [p]
        for ctrl in (x for x in node.children if x.tag == 71):
            ident = ctrl.data[:4][::-1]
            match = next((c for c in available_controls if c[1] == ident), None)
            anchor_start = match[0] if match else 0
            if match:
                available_controls.remove(match)
            if ident == b"tbl ":
                desc = next((x for x in ctrl.children if x.tag == 77), None)
                if desc is None or len(desc.data) < 8:
                    _warning(warnings, "일부 표의 셀 구조를 읽지 못했습니다.")
                    continue
                rows, cols = struct.unpack_from("<HH", desc.data, 4)
                if not rows or not cols or rows * cols > 100000:
                    _warning(warnings, "지원 범위를 벗어나는 표를 건너뛰었습니다.")
                    continue
                fill_id = struct.unpack_from("<H", desc.data, 18 + 2 * rows)[0] if len(desc.data) >= 20 + 2 * rows else 2
                fill = borders.get(fill_id, BorderFill())
                prop = struct.unpack_from("<I", desc.data)[0]
                width, height = struct.unpack_from("<II", ctrl.data, 16) if len(ctrl.data) >= 24 else (48000, rows * 2200)
                table = Table(rows, cols, width=width / 100, height=height / 100, borders=fill.borders, background=fill.background,
                    padding=tuple(v / 100 for v in struct.unpack_from("<4H", desc.data, 10)) if len(desc.data) >= 18 else (3, 3, 1.5, 1.5),
                    cell_spacing=struct.unpack_from("<H", desc.data, 8)[0] / 100 if len(desc.data) >= 10 else 0,
                    page_break={0: "NONE", 1: "TABLE", 2: "CELL"}.get(prop & 3, "CELL"), repeat_header=bool(prop & 4), position=_position_hwp(ctrl.data))
                table.anchor_segments, table.source_para_id, table.source_index = p.line_segments, p.source_id, p.source_index
                anchor_shape = next((cid for pos,cid in reversed(shapes) if pos <= anchor_start),shapes[0][1])
                table.anchor_style = chars.get(anchor_shape,TextStyle())
                table.anchor_text_start, table.scope = anchor_start, dict(p.scope)
                current = None
                for x in ctrl.children:
                    if x.tag == 72 and len(x.data) >= 32:
                        # Modern HWP stores a 32-bit list count (8-byte header).
                        # Older public-spec layouts used a 16-bit count.
                        offset = 8 if len(x.data) >= 34 else 6
                        col, row, cs, rs = struct.unpack_from("<4H", x.data, offset)
                        width, height = struct.unpack_from("<II", x.data, offset + 8)
                        bid = struct.unpack_from("<H", x.data, offset + 24)[0]
                        fill = borders.get(bid, BorderFill())
                        common = struct.unpack_from("<I", x.data, offset - 4)[0]
                        raw_margins = struct.unpack_from("<4H", x.data, offset + 16)
                        # Native cell layout chooses the table's margins when
                        # hasMargin (bit 16) is clear, even if stored cell values
                        # are ordinary numbers rather than the 0xffff sentinel.
                        inherited = tuple(not common & (1 << 16) or value == 65535 for value in raw_margins)
                        current = Cell(row, col, max(1, rs), max(1, cs), width=width / 100, background=fill.background, height=height / 100,
                            padding=tuple(table.padding[i] if inherited[i] else raw_margins[i] / 100 for i in range(4)), borders=fill.borders,
                            vertical_align={0: "TOP", 1: "CENTER", 2: "BOTTOM"}.get((common >> 5) & 3, "CENTER"), padding_inherited=inherited,
                            native_flags=common)
                        table.cells.append(current)
                        cell_scope = {"kind": "cell", "table_source_index": table.source_index, "table_source_para_id": table.source_para_id,
                            "row": row, "col": col, "row_span": rs, "col_span": cs, "width_pt": width / 100, "height_pt": height / 100,
                            "padding": current.padding, "parent": p.scope}
                        for sub in x.children:
                            if sub.tag == 66:
                                current.blocks.extend(paragraph(sub, cell_scope))
                    elif x.tag == 66 and current is not None:
                        current.blocks.extend(paragraph(x, cell_scope))
                _table_grid(table)
                blocks.append(table)
            elif ident == b"secd":
                page = next((x for x in ctrl.children if x.tag == 73), None)
                if page is not None and len(page.data) >= 36:
                    vals = struct.unpack_from("<9I", page.data)
                    values = dict(zip(("width_pt", "height_pt", "left_pt", "right_pt", "top_pt", "bottom_pt", "header_pt", "footer_pt", "gutter_pt"), (v / 100 for v in vals)))
                    pageprop = struct.unpack_from("<I", page.data, 36)[0] if len(page.data) >= 40 else 0
                    values.update(landscape=bool(pageprop & 1), gutter_type={0: "LEFT_ONLY", 1: "LEFT_RIGHT", 2: "TOP_BOTTOM"}.get((pageprop >> 1) & 3, "LEFT_ONLY"))
                    metadata.setdefault("page", values)
                    metadata.setdefault("section_pages", []).append(values)
            elif ident == b"cold":
                if len(ctrl.data) >= 6 and (struct.unpack_from("<H", ctrl.data, 4)[0] >> 2) & 255 > 1:
                    _warning(warnings, "다단 조판을 단일 본문으로 가져옵니다.")
            elif ident in (b"head", b"foot"):
                kind = "header" if ident == b"head" else "footer"
                prop = struct.unpack_from("<I", ctrl.data, 4)[0] if len(ctrl.data) >= 8 else 0
                part = HeaderFooter(kind, page_type={0: "BOTH", 1: "EVEN", 2: "ODD"}.get(prop & 3, "BOTH"),
                    source_para_id=p.source_id, source_index=p.source_index)
                part.scope = dict(p.scope)
                list_header = next((x for x in nodes(ctrl) if x.tag == 72), None)
                if list_header is not None and len(list_header.data) >= 16:
                    part.width, part.height = (v / 100 for v in struct.unpack_from("<II", list_header.data, 8))
                    flags = struct.unpack_from("<I", list_header.data, 4)[0]
                    part.vertical_align = {0:"TOP",1:"CENTER",2:"BOTTOM"}.get((flags >> 5) & 3,"TOP")
                def part_text(parent):
                    for child in parent.children:
                        if child.tag == 66:
                            part.blocks.extend(paragraph(child, {"kind": kind, "width_pt": part.width, "height_pt": part.height}))
                        else:
                            part_text(child)
                part_text(ctrl)
                header_footers.append(part)
                _warning(warnings, "머리말·꼬리말은 문서 모델에 보존하지만 본문 편집 화면에서는 반복 표시하지 않습니다.")
            elif ident == b"atno":
                prop, number = struct.unpack_from("<II", ctrl.data, 4) if len(ctrl.data) >= 12 else (0, 1)
                field_shape = next((cid for pos, cid in reversed(shapes) if pos <= anchor_start), shapes[0][1])
                p.runs.append(Run("", chars.get(field_shape, TextStyle()), source_start=anchor_start,
                    field={"kind": "auto_number", "number_type": ("PAGE", "FOOTNOTE", "ENDNOTE", "PICTURE", "TABLE", "EQUATION")[min(prop & 15, 5)], "number": number}))
            elif ident == b"nwno":
                if len(ctrl.data) >= 10:
                    prop, number = struct.unpack_from("<IH", ctrl.data, 4)
                    metadata["page_start_number"] = number if prop & 15 == 0 else metadata.get("page_start_number", 1)
                    p.runs.append(Run("",p.default_style,source_start=anchor_start,
                        field={"kind":"new_number","number_type":("PAGE","FOOTNOTE","ENDNOTE","PICTURE","TABLE","EQUATION")[min(prop & 15,5)],"number":number}))
            elif ident == b"gso ":
                component = next((x for x in ctrl.children if x.tag == 76), None)
                kind = component.data[:4][::-1] if component is not None else b""
                if kind == b"$pic":
                    image_record = next((x for x in nodes(ctrl) if x.tag == 85), None)
                    if image_record is not None:
                        r = picture(ctrl, image_record)
                        image_shape = next((cid for pos, cid in reversed(shapes) if pos <= anchor_start), shapes[0][1])
                        r.style = chars.get(image_shape, TextStyle())
                        r.source_start = anchor_start
                        p.runs.append(r)
                        p.runs.sort(key=lambda r: r.source_start or 0)
                elif kind == b"$rec":
                    width, height = struct.unpack_from("<II", ctrl.data, 16)
                    drawing = Drawing(width=width / 100, height=height / 100, position=_position_hwp(ctrl.data),
                        anchor_segments=p.line_segments, source_para_id=p.source_id, source_index=p.source_index, anchor_text_start=anchor_start, scope=dict(p.scope))
                    anchor_shape = next((cid for pos,cid in reversed(shapes) if pos <= anchor_start),shapes[0][1])
                    drawing.anchor_style = chars.get(anchor_shape,TextStyle())
                    shape = component.data
                    if len(shape) >= 209:
                        color, thickness, line_flags = struct.unpack_from("<III", shape, 196)
                        border = Border(_BORDER_STYLES[min(line_flags & 63, len(_BORDER_STYLES) - 1)], thickness / 100, _colorref(color))
                        drawing.borders = (border,) * 4
                        drawing.line_options = _line_options_hwp(line_flags,shape[208])
                        if len(shape) >= 217 and struct.unpack_from("<I", shape, 209)[0] & 1:
                            drawing.background = _colorref(struct.unpack_from("<I", shape, 213)[0])
                    if len(shape) >= 196:
                        drawing.shape.original_width_pt, drawing.shape.original_height_pt = (v / 100 for v in struct.unpack_from("<II", shape, 20))
                        drawing.shape.current_width_pt,drawing.shape.current_height_pt = (v/100 for v in struct.unpack_from("<II",shape,28))
                        drawing.shape.offset_x_pt,drawing.shape.offset_y_pt = (v/100 for v in struct.unpack_from("<ii",shape,8))
                        drawing.shape.native_flags = struct.unpack_from("<I",shape,36)[0]
                        drawing.shape.rotate_image = bool(drawing.shape.native_flags & (1<<19))
                        drawing.shape.rotation_center_x_pt,drawing.shape.rotation_center_y_pt = (v/100 for v in struct.unpack_from("<ii",shape,42))
                        drawing.shape.transforms = {name: list(struct.unpack_from("<6d", shape, 52 + 48 * i)) for i, name in enumerate(("transMatrix", "scaMatrix", "rotMatrix"))}
                        drawing.shape.rotation = struct.unpack_from("<H", shape, 40)[0]
                    # A text-bearing shape can contain nested drawings before
                    # its own rectangle record. Coordinates belong to this
                    # component, never to the first recursive descendant.
                    rect = next((x for x in component.children if x.tag == 79), None)
                    if rect is not None and len(rect.data) >= 33:
                        coords = struct.unpack_from("<8i", rect.data, 1)
                        drawing.shape.rect = tuple((coords[i*2]/100, coords[i*2+1]/100) for i in range(4))
                    list_header = next((x for x in nodes(ctrl) if x.tag == 72), None)
                    if list_header is not None and len(list_header.data) >= 16:
                        drawing.padding = tuple(v / 100 for v in struct.unpack_from("<4h", list_header.data, 8))
                        drawing.vertical_align = {0: "TOP", 1: "CENTER", 2: "BOTTOM"}.get((struct.unpack_from("<I", list_header.data, 4)[0] >> 5) & 3, "CENTER")
                    drawing_scope = {"kind": "drawing", "source_index": drawing.source_index, "width_pt": drawing.width, "height_pt": drawing.height,
                        "position": asdict(drawing.position), "padding": drawing.padding, "parent": p.scope}
                    def drawing_text(parent):
                        for child in parent.children:
                            if child.tag == 66:
                                drawing.blocks.extend(paragraph(child, drawing_scope))
                            else:
                                drawing_text(child)
                    drawing_text(ctrl)
                    blocks.append(drawing)
                    _warning(warnings, "글상자 사각형의 원래 좌표와 테두리는 문서 모델에 유지하지만 편집 화면에서는 본문으로 표시합니다.")
                else:
                    _warning(warnings, "일부 그리기 개체의 원래 모양과 기능은 지원하지 않습니다.")
            else:
                for x in nodes(ctrl):
                    if x.tag == 85:
                        blocks.append(Paragraph([picture(ctrl, x)], scope=dict(p.scope)))
                    elif x.tag == 88:
                        length = struct.unpack_from("<H", x.data, 4)[0] if len(x.data) >= 6 else 0
                        formula = x.data[6:6 + length * 2].decode("utf-16le", "replace")
                        blocks.append(Paragraph([Run("[수식: " + formula + "]")], scope=dict(p.scope)))
                        _warning(warnings, "수식은 본문 텍스트로 가져옵니다. 수식 배치와 자동 계산은 지원하지 않습니다.")
                def text_nodes(parent):
                    for x in parent.children:
                        if x.tag == 66:
                            blocks.extend(paragraph(x))
                        else:
                            text_nodes(x)
                text_nodes(ctrl)
                _warning(warnings, "일부 도형·그림·필드·주석의 기능과 원래 배치가 보존되지 않습니다.")
        return blocks or [p]
    blocks = []
    section_prefix = "ViewText" if distribution else "BodyText"
    sections = sorted((n for n in cfb.streams if re.fullmatch(section_prefix + r"/Section\d+", n)), key=lambda n: _int(re.search(r"\d+", n).group()))
    if not sections:
        raise ValueError("HWP 본문 스트림이 없습니다.")
    for section_index, name in enumerate(sections):
        tree = _record_tree(_records(read(name)))
        for node in tree.children:
            if node.tag == 66:
                blocks.extend(paragraph(node))
    metadata.update(sections=len(sections), tables=sum(isinstance(x, Table) for x in blocks), paragraphs=sum(isinstance(x, Paragraph) for x in blocks))
    if len(sections) > 1:
        _warning(warnings, "편집 화면은 여러 구역을 하나의 본문으로 표시합니다. 원본 보존 저장은 구역 스트림을 유지합니다.")
    if distribution:
        metadata.update(read_only=True, distribution=True, distribution_options=distribution_options)
        _warning(warnings, "배포용 HWP의 읽기 전용 본문입니다. 원본의 편집·복사·인쇄 제한을 유지합니다.")
    _warning(warnings, "한컴과 글꼴·줄바꿈·쪽 나눔이 다를 수 있습니다. 원본 보존 저장은 기존 HWP 레코드와 스트림을 유지하며, 지원 범위를 벗어난 편집은 재생성 내보내기가 필요합니다.")
    _page_metadata(metadata)
    model = DocumentModel(blocks, PageLayout(**metadata.get("page", {})), [PageLayout(**p) for p in metadata.get("section_pages", [])], header_footers)
    from .hwp_native import _paragraphs
    model._hwp_source = {"data": original_bytes, "baseline": model_to_data(model),
        "paragraphs": [paragraph_locations.get(id(p)) for p in _paragraphs(model)],
        "char_styles": chars, "para_styles": paras, "read_only": distribution,
        "distribution_options": distribution_options if distribution else 0}
    return LoadedDocument(_wrap_html(blocks), warnings, metadata, str(path), "hwp", model)


def _qt_blocks(document_or_html):
    from PySide6.QtCore import QBuffer, QIODevice, Qt, QUrl
    from PySide6.QtGui import QTextDocument, QTextTable, QTextFormat, QImage
    document = document_or_html
    if isinstance(document, DocumentModel):
        return document.blocks, []
    if isinstance(document, str):
        document = QTextDocument()
        document.setHtml(document_or_html)
    warnings = []
    def native_letter_spacing(font,cf,size):
        value=font.letterSpacing()
        if not cf.hasProperty(QTextFormat.Property.FontLetterSpacing) and value==0:
            return 0
        return round(value-100) if font.letterSpacingType()==font.SpacingType.PercentageSpacing else round(value*.75/size*100)
    declared_fonts=document.rootFrame().frameFormat().property(FONT_PROPERTY) or []
    fonts_by_family={}
    for declared in declared_fonts:
        fonts_by_family.setdefault(declared.get('font'),[]).append(declared)
    font_metadata_cache={}
    def font_metadata(font,size,bold,italic):
        # HTML cannot express Hancom's HFT/TTF distinction. The geometry JSON
        # retains imported declarations; changing to a new face uses Qt's TTF.
        key=(font,size,bold,italic)
        if key in font_metadata_cache:return font_metadata_cache[key]
        candidates=fonts_by_family.get(font,[])
        if not candidates:
            font_metadata_cache[key]={}
            return {}
        distance=lambda s:(int(bool(s.get("bold"))!=bold)+int(bool(s.get("italic"))!=italic),abs(s.get("size",11)-size))
        selected=min(candidates,key=distance)
        equally_close=[s for s in candidates if distance(s)==distance(selected)]
        if len({(s.get("font_type","TTF"),tuple(s.get("language_fonts",())),tuple(s.get("language_font_types",()))) for s in equally_close})>1:
            _warning(warnings,"같은 이름·모양의 글꼴 선언 종류가 여러 개여서 편집 후 일부 언어별 글꼴 선택이 달라질 수 있습니다.")
        result={"font_type":selected.get("font_type","TTF"),"language_fonts":tuple(selected.get("language_fonts",())),
            "language_font_types":tuple(selected.get("language_font_types",())),
            "use_font_space":bool(selected.get("use_font_space",False)), "use_kerning":bool(selected.get("use_kerning",False)),
            **{key:tuple(selected.get(key,())) for key in ("language_ratios","language_spacing","language_relative_sizes","language_offsets")}}
        font_metadata_cache[key]=result
        return result
    def block(b):
        f = b.blockFormat()
        align = f.alignment()
        alignment = "CENTER" if align & Qt.AlignmentFlag.AlignHCenter else "RIGHT" if align & Qt.AlignmentFlag.AlignRight else "JUSTIFY" if align & Qt.AlignmentFlag.AlignJustify else "LEFT"
        line = f.lineHeight()
        line_type = "PERCENT"
        if f.lineHeightType() == 2:
            line_type = "FIXED"
        elif f.lineHeightType() == 3:
            line_type = "BETWEEN_LINES"
        ps = ParaStyle(alignment, f.leftMargin() * .75, f.rightMargin() * .75, f.textIndent() * .75,
                       f.topMargin() * .75, f.bottomMargin() * .75, line or 160, line_type,
                       bool(f.pageBreakPolicy() & QTextFormat.PageBreakFlag.PageBreak_AlwaysBefore))
        p = Paragraph(style=ps, source_index=_int(f.property(0x100006), -1))
        cf_default = b.charFormat()
        base_font = cf_default.font()
        default_family=base_font.family() or document.defaultFont().family() or "맑은 고딕"
        default_size=cf_default.fontPointSize() or document.defaultFont().pointSizeF() or 11
        p.default_style = TextStyle(default_family,default_size,cf_default.fontWeight() >= 600,
            cf_default.fontItalic(),cf_default.fontUnderline(),cf_default.fontStrikeOut(),stretch=base_font.stretch() or 100,
            letter_spacing=native_letter_spacing(base_font,cf_default,default_size),
            **font_metadata(default_family,default_size,cf_default.fontWeight()>=600,cf_default.fontItalic()))
        it = b.begin()
        while not it.atEnd():
            frag = it.fragment()
            if frag.isValid():
                cf = frag.charFormat()
                if cf.isImageFormat():
                    image_format = cf.toImageFormat()
                    name = image_format.name()
                    resource = document.resource(QTextDocument.ResourceType.ImageResource, QUrl(name))
                    image = resource if isinstance(resource, QImage) else QImage()
                    if image.isNull() and name.startswith("data:"):
                        try:
                            image = QImage.fromData(base64.b64decode(name.split(",", 1)[1]))
                        except (ValueError, IndexError):
                            pass
                    if image.isNull() and QUrl(name).isLocalFile():
                        image.load(QUrl(name).toLocalFile())
                    if image.isNull() and ":" not in name and len(name) < 1024:
                        try:
                            if Path(name).is_file():
                                image.load(name)
                        except OSError:
                            pass
                    if image.isNull():
                        _warning(warnings, "일부 그림을 읽지 못해 그림 표시 텍스트로 저장했습니다.")
                        p.runs.append(Run("[그림]"))
                    else:
                        buffer = QBuffer()
                        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
                        image.save(buffer, "PNG")
                        pg = picture_from_data(image_format.property(PICTURE_PROPERTY))
                        p.runs.append(Run(image=bytes(buffer.data()), width=image_format.width() or image.width(), height=image_format.height() or image.height(), picture=pg))
                        if not pg.position.inline or pg.rotation:
                            _warning(warnings, "그림의 부유 위치·회전은 저장 데이터에 유지하지만 편집 화면의 배치와 다를 수 있습니다.")
                else:
                    if cf.isAnchor() and cf.anchorHref():
                        _warning(warnings, "하이퍼링크의 표시 텍스트는 저장하지만 연결 주소는 저장하지 않습니다.")
                    font = cf.font()
                    size = cf.fontPointSize() or font.pointSizeF() or document.defaultFont().pointSizeF()
                    if size <= 0:
                        size = 11
                    fg = cf.foreground().color().name() if cf.foreground().style() != Qt.BrushStyle.NoBrush else "#000000"
                    bg = cf.background().color().name() if cf.background().style() != Qt.BrushStyle.NoBrush else ""
                    va = cf.verticalAlignment()
                    family=font.family() or document.defaultFont().family() or "맑은 고딕"
                    ts = TextStyle(family, size, cf.fontWeight() >= 600,
                        cf.fontItalic(), cf.fontUnderline(), cf.fontStrikeOut(), fg, bg,
                        va == cf.VerticalAlignment.AlignSuperScript, va == cf.VerticalAlignment.AlignSubScript, font.stretch() or 100,
                        native_letter_spacing(font,cf,size),
                        **font_metadata(family,size,cf.fontWeight()>=600,cf.fontItalic()))
                    p.runs.append(Run(frag.text().replace("\u2028", "\n"), ts))
            it += 1
        if b.textList():
            _warning(warnings, "자동 목록 번호는 문단 텍스트로 저장합니다. 저장 후 자동 번호 기능은 유지되지 않습니다.")
            lst = b.textList()
            prefix = lst.itemText(b) + " "
            p.runs.insert(0, Run(prefix, p.runs[0].style if p.runs else TextStyle()))
        return p
    def frame(fr):
        result = []
        it = fr.begin()
        while not it.atEnd():
            child = it.currentFrame()
            b = it.currentBlock()
            if isinstance(child, QTextTable):
                page = _page_layout(document)
                available = max(72, page.display_size[0] - page.left_pt - page.right_pt - page.gutter_pt)
                tf = child.format()
                g = dict(tf.property(TABLE_PROPERTY) or {})
                if "position" in g:
                    g["position"] = position_from_data(g["position"])
                if "borders" in g:
                    g["borders"] = borders_from_data(g["borders"])
                if g.get("anchor_style") is not None:
                    g["anchor_style"] = _text_style_from_data(g["anchor_style"])
                g.pop("rows", None)
                g.pop("cols", None)
                t = Table(child.rows(), child.columns(), **g)
                width = tf.width()
                t.width = width.rawValue() * .75 if width.type().value == 1 else available * width.rawValue() / 100 if width.type().value == 2 else g.get("width", available)
                constraints = tf.columnWidthConstraints()
                if len(constraints) == t.cols:
                    t.column_widths = [v.rawValue() * .75 if v.type().value == 1 else t.width * v.rawValue() / 100 for v in constraints]
                if len(t.column_widths) != t.cols:
                    t.column_widths = [t.width / t.cols] * t.cols
                t.cell_spacing = tf.cellSpacing() * .75
                seen = set()
                for row in range(t.rows):
                    for col in range(t.cols):
                        c = child.cellAt(row, col)
                        key = (c.row(), c.column())
                        if key in seen:
                            continue
                        seen.add(key)
                        cell_blocks = []
                        ci = c.begin()
                        while not ci.atEnd():
                            subframe = ci.currentFrame()
                            cb = ci.currentBlock()
                            if subframe:
                                cell_blocks.extend(frame(subframe))
                            elif cb.isValid():
                                cell_blocks.append(block(cb))
                            ci += 1
                        cformat = c.format().toTableCellFormat()
                        background = cformat.background().color().name() if cformat.background().style() != Qt.BrushStyle.NoBrush else ""
                        cg = dict(cformat.property(CELL_PROPERTY) or {})
                        for key in ("row", "col", "row_span", "col_span", "blocks", "width", "background"):
                            cg.pop(key, None)
                        if "borders" in cg:
                            cg["borders"] = borders_from_data(cg["borders"])
                        if "padding" in cg:
                            cg["padding"] = tuple(cg["padding"])
                        cell = Cell(c.row(), c.column(), c.rowSpan(), c.columnSpan(), cell_blocks,
                            sum(t.column_widths[c.column():c.column() + c.columnSpan()]), background, **cg)
                        # Qt supports individual padding; exact heights and
                        # native border variants remain in the custom data.
                        current_padding = tuple(getattr(cformat, side.lower() + "Padding")() * .75 for side in ("Left", "Right", "Top", "Bottom")) if cg else (tf.cellPadding() * .75,) * 4
                        if any(abs(a-b) > .01 for a,b in zip(cell.padding, current_padding)):
                            cell.padding_inherited = (False,) * 4
                        cell.padding = current_padding
                        t.cells.append(cell)
                _table_grid(t)
                if any(c.height > 0 for c in t.cells):
                    _warning(warnings, "표의 지정 높이와 쪽 경계 분할은 파일에 유지하지만 편집 화면의 자동 행 높이·쪽 나눔은 한컴과 다를 수 있습니다.")
                result.append(t)
            elif child:
                result.extend(frame(child))
            elif b.isValid():
                result.append(block(b))
            it += 1
        return result
    return frame(document.rootFrame()), warnings


def _atomic_write(path, data):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="." + target.name + ".", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, target)
    except BaseException:
        try:
            os.unlink(name)
        except OSError:
            pass
        raise


def _element(parent, prefix, node_name, **attrs):
    return ET.SubElement(parent, "{" + NS[prefix] + "}" + node_name, {k: str(v) for k, v in attrs.items()})


def _xml(root):
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _walk_blocks(blocks):
    for b in blocks:
        yield b
        if isinstance(b, Table):
            for c in b.cells:
                yield from _walk_blocks(c.blocks)
        elif isinstance(b, Drawing):
            yield from _walk_blocks(b.blocks)


def _border_fills(blocks):
    values = [BorderFill((Border("NONE"),) * 4), BorderFill()]
    for block in _walk_blocks(blocks):
        if isinstance(block,Paragraph):
            fill = BorderFill(tuple(block.style.borders),block.style.background)
            if fill not in values:
                values.append(fill)
        if isinstance(block, (Table, Drawing)):
            for obj in [block] + (block.cells if isinstance(block, Table) else []):
                fill = BorderFill(tuple(obj.borders), obj.background)
                if fill not in values:
                    values.append(fill)
    return values


def _text_styles(blocks):
    all_blocks = list(_walk_blocks(blocks))
    paragraphs = [b for b in all_blocks if isinstance(b, Paragraph)]
    return list(dict.fromkeys([b.default_style for b in paragraphs]
        + [r.style for b in paragraphs for r in b.runs]
        + [b.end_style for b in paragraphs if b.end_style is not None]
        + [b.anchor_style for b in all_blocks if isinstance(b,(Table,Drawing)) and b.anchor_style is not None]))


def _text_style_from_data(data):
    if isinstance(data,TextStyle):
        return data
    values = dict(data)
    for key in ("language_fonts", "language_font_types", "language_ratios", "language_spacing", "language_relative_sizes", "language_offsets"):
        values[key] = tuple(values.get(key,()))
    return TextStyle(**values)


def _font_pairs(style):
    names = style.language_fonts if len(style.language_fonts)==7 else (style.font,)*7
    types = style.language_font_types if len(style.language_font_types)==7 else (style.font_type,)*7
    return tuple(zip(names,types))


def _paragraph_count(blocks):
    count, i = 0, 0
    while i < len(blocks):
        count += 1
        if isinstance(blocks[i], Paragraph) and i + 1 < len(blocks) and isinstance(blocks[i+1], (Table, Drawing)) and blocks[i].source_index >= 0 and blocks[i].source_index == blocks[i+1].source_index:
            i += 1
        i += 1
    return count


def _current_size(shape, width, height, preserve=False):
    """A native transformed size may differ from requested size by one unit."""
    if preserve and shape.current_width_pt is not None and shape.current_height_pt is not None:
        return round(shape.current_width_pt*100),round(shape.current_height_pt*100)
    return width,height


def _rotation_center(shape,width,height,preserve=False):
    if preserve and shape.rotation_center_x_pt is not None and shape.rotation_center_y_pt is not None:
        return round(shape.rotation_center_x_pt*100),round(shape.rotation_center_y_pt*100)
    return width//2,height//2


def _signed32(value):
    return (_int(value)+(1<<31)) % (1<<32) - (1<<31)


def _block_data(block):
    result = asdict(block)
    result["type"] = "paragraph" if isinstance(block, Paragraph) else "table" if isinstance(block, Table) else "drawing"
    if isinstance(block, Paragraph):
        for r in result["runs"]:
            if r["image"] is not None:
                r["image"] = base64.b64encode(r["image"]).decode("ascii")
    elif isinstance(block, Table):
        for c, actual in zip(result["cells"], block.cells):
            c["blocks"] = [_block_data(b) for b in actual.blocks]
    else:
        result["blocks"] = [_block_data(b) for b in block.blocks]
    return result


def _block_from_data(data):
    data = dict(data)
    kind = data.pop("type")
    if kind not in ("paragraph", "table", "drawing"):
        raise ValueError("지원하지 않는 문서 모델 블록입니다.")
    if kind == "paragraph":
        runs = []
        for r in data.pop("runs", []):
            r = dict(r)
            style = dict(r.pop("style", {}))
            for key in ("language_fonts", "language_font_types", "language_ratios", "language_spacing", "language_relative_sizes", "language_offsets"):
                style[key] = tuple(style.get(key,()))
            r["picture"] = picture_from_data(r.get("picture"))
            if r.get("image") is not None:
                r["image"] = base64.b64decode(r["image"],validate=True)
            runs.append(Run(style=TextStyle(**style), **r))
        default = dict(data.pop("default_style", {}))
        for key in ("language_fonts", "language_font_types", "language_ratios", "language_spacing", "language_relative_sizes", "language_offsets"):
            default[key] = tuple(default.get(key,()))
        end = data.pop("end_style", None)
        if end is not None:
            end = dict(end)
            for key in ("language_fonts", "language_font_types", "language_ratios", "language_spacing", "language_relative_sizes", "language_offsets"):
                end[key] = tuple(end.get(key,()))
            end = TextStyle(**end)
        paragraph_style = dict(data.pop("style",{}))
        if "borders" in paragraph_style:
            paragraph_style["borders"] = borders_from_data(paragraph_style["borders"])
        if "border_offsets" in paragraph_style:
            paragraph_style["border_offsets"] = tuple(paragraph_style["border_offsets"])
        return Paragraph(runs=runs, style=ParaStyle(**paragraph_style), default_style=TextStyle(**default), end_style=end, **data)
    data["position"] = position_from_data(data.get("position"))
    if data.get("anchor_style") is not None:
        data["anchor_style"] = _text_style_from_data(data["anchor_style"])
    data["borders"] = borders_from_data(data.get("borders", ())) or (Border(),) * 4
    if kind == "table":
        cells = []
        for c in data.pop("cells", []):
            c = dict(c)
            c["borders"] = borders_from_data(c.get("borders", ())) or (Border(),) * 4
            c["blocks"] = [_block_from_data(b) for b in c.get("blocks", [])]
            cells.append(Cell(**c))
        return Table(cells=cells, **data)
    data["blocks"] = [_block_from_data(b) for b in data.get("blocks", [])]
    data["shape"] = picture_from_data(data.get("shape"))
    return Drawing(**data)


def _document_parts(document):
    return document.header_footers if isinstance(document, DocumentModel) else getattr(document, "_hangeul_header_footers", [])


def model_to_data(model):
    """Serialize an imported source model, including bitmap bytes and caches."""
    if not isinstance(model, DocumentModel):
        raise ValueError("문서 모델이 아닙니다.")
    return {"version":1,"blocks":[_block_data(b) for b in model.blocks], "page":asdict(model.page),
        "section_pages":[asdict(p) for p in model.section_pages],
        "header_footers":[{**asdict(p),"blocks":[_block_data(b) for b in p.blocks]} for p in model.header_footers]}


def model_from_data(data):
    """Decode only the declared model types; malformed structures are rejected."""
    if not isinstance(data,dict) or data.get("version") != 1:
        raise ValueError("지원하지 않는 문서 모델 버전입니다.")
    try:
        if set(data)-{"version","blocks","page","section_pages","header_footers"}:
            raise ValueError("문서 모델에 알 수 없는 필드가 있습니다.")
        blocks=[_block_from_data(b) for b in data.get("blocks",[])]
        page=PageLayout(**data.get("page",{}))
        parts=[HeaderFooter(**{**p,"blocks":[_block_from_data(b) for b in p.get("blocks",[])]}) for p in data.get("header_footers",[])]
        if page.width_pt <= 0 or page.height_pt <= 0:
            raise ValueError("용지 크기가 올바르지 않습니다.")
        for block in _walk_blocks(blocks):
            if isinstance(block,Table) and (block.rows <= 0 or block.cols <= 0 or block.rows*block.cols > 100000):
                raise ValueError("표의 크기가 올바르지 않습니다.")
        return DocumentModel(blocks,page,[PageLayout(**p) for p in data.get("section_pages",[])],parts)
    except (TypeError,KeyError,AttributeError,OverflowError,RecursionError) as error:
        raise ValueError("문서 모델이 손상되었습니다.") from error


def save_hwpx(document_or_html, path) -> list[str]:
    from .hwpx_native import patch_hwpx_source
    if isinstance(document_or_html, DocumentModel) and patch_hwpx_source(document_or_html, path):
        return []
    blocks, warnings = _qt_blocks(document_or_html)
    if getattr(document_or_html, "_hwpx_source", None):
        _warning(warnings, "구조·서식 변경으로 HWPX를 재구성했습니다. 원본의 미지원 요소는 보존되지 않을 수 있습니다.")
    document_parts = _document_parts(document_or_html)
    style_blocks = blocks + [b for part in document_parts for b in part.blocks]
    page_width, page_height, page_margin = _page_settings(document_or_html)
    page_layout = _page_layout(document_or_html)
    chars = list(dict.fromkeys([TextStyle()] + _text_styles(style_blocks)))
    paras = list(dict.fromkeys([ParaStyle()] + [b.style for b in _walk_blocks(style_blocks) if isinstance(b, Paragraph)]))
    fonts = list(dict.fromkeys(face for s in chars for face in _font_pairs(s)))
    border_fills = _border_fills(style_blocks)
    head = ET.Element("{" + NS["hh"] + "}head", version="1.5", secCnt="1")
    _element(head, "hh", "beginNum", page=1, footnote=1, endnote=1, pic=1, tbl=1, equation=1)
    refs = _element(head, "hh", "refList")
    fontfaces = _element(refs, "hh", "fontfaces", itemCnt=7)
    languages = ("HANGUL", "LATIN", "HANJA", "JAPANESE", "OTHER", "SYMBOL", "USER")
    langattrs = ("hangul", "latin", "hanja", "japanese", "other", "symbol", "user")
    for lang in languages:
        face = _element(fontfaces, "hh", "fontface", lang=lang, fontCnt=len(fonts))
        for i, (font,kind) in enumerate(fonts):
            _element(face, "hh", "font", id=i, face=font, type=kind if kind in ("TTF","HFT") else "TTF", isEmbedded=0)
    borders = _element(refs, "hh", "borderFills", itemCnt=len(border_fills))
    for i, fill in enumerate(border_fills, 1):
        bf = _element(borders, "hh", "borderFill", id=i, threeD=0, shadow=0, centerLine="NONE", breakCellSeparateLine=0)
        for n in ("slash", "backSlash"):
            _element(bf, "hh", n, type="NONE", Crooked=0, isCounter=0)
        for n, border in zip(("leftBorder", "rightBorder", "topBorder", "bottomBorder"), fill.borders):
            _element(bf, "hh", n, type=border.style, width=f"{border.width_pt * 25.4 / 72:g} mm", color=border.color)
        _element(bf, "hh", "diagonal", type="NONE", width="0.1 mm", color="#000000")
        if fill.background:
            brush = _element(bf, "hc", "fillBrush")
            _element(brush, "hc", "winBrush", faceColor=fill.background, hatchColor="#000000", alpha=0)
    charprops = _element(refs, "hh", "charProperties", itemCnt=len(chars))
    for i, s in enumerate(chars):
        cp = _element(charprops, "hh", "charPr", id=i, height=round(s.size * 100), textColor=s.color,
                      shadeColor=s.background or "none", useFontSpace=int(s.use_font_space), useKerning=int(s.use_kerning), symMark="NONE", borderFillIDRef=1)
        _element(cp, "hh", "fontRef", **{k: fonts.index(_font_pairs(s)[j]) for j, k in enumerate(langattrs)})
        for name, values, fallback in (("ratio", s.language_ratios, s.stretch), ("spacing", s.language_spacing, s.letter_spacing),
                ("relSz", s.language_relative_sizes, 100), ("offset", s.language_offsets, 0)):
            values = values if len(values) == 7 else (fallback,) * 7
            _element(cp, "hh", name, **dict(zip(langattrs,values)))
        for name, enabled in (("bold", s.bold), ("italic", s.italic), ("supscript", s.superscript), ("subscript", s.subscript)):
            if enabled:
                _element(cp, "hh", name)
        _element(cp, "hh", "underline", type="BOTTOM" if s.underline else "NONE", shape="SOLID", color=s.color)
        _element(cp, "hh", "strikeout", shape="SOLID" if s.strike else "NONE", color=s.color)
        _element(cp, "hh", "outline", type="NONE")
        _element(cp, "hh", "shadow", type="NONE", color="#C0C0C0", offsetX=10, offsetY=10)
    tabs = _element(refs, "hh", "tabProperties", itemCnt=1)
    _element(tabs, "hh", "tabPr", id=0, autoTabLeft=0, autoTabRight=0)
    para_properties = _element(refs, "hh", "paraProperties", itemCnt=len(paras))
    for i, s in enumerate(paras):
        pp = _element(para_properties, "hh", "paraPr", id=i, tabPrIDRef=0, condense=s.condense, fontLineHeight=int(s.font_line_height), snapToGrid=int(s.snap_to_grid), suppressLineNumbers=0, checked=0, textDir="AUTO")
        _element(pp, "hh", "align", horizontal=s.align, vertical=s.vertical_align)
        _element(pp, "hh", "heading", type="NONE", idRef=0, level=0)
        _element(pp, "hh", "breakSetting", breakLatinWord=s.break_latin, breakNonLatinWord=s.break_nonlatin, widowOrphan=int(s.widow_orphan), keepWithNext=int(s.keep_with_next), keepLines=int(s.keep_lines), pageBreakBefore=int(s.page_break), lineWrap=s.line_wrap)
        _element(pp, "hh", "autoSpacing", eAsianEng=int(s.auto_spacing_eng), eAsianNum=int(s.auto_spacing_num))
        margin = _element(pp, "hh", "margin")
        for name, value in (("intent", s.indent), ("left", s.left), ("right", s.right), ("prev", s.before), ("next", s.after)):
            _element(margin, "hc", name, value=round(value * 200), unit="HWPUNIT")
        _element(pp, "hh", "lineSpacing", type=s.line_type, value=round(s.line if s.line_type == "PERCENT" else s.line * 100), unit="HWPUNIT")
        _element(pp,"hh","border",borderFillIDRef=border_fills.index(BorderFill(tuple(s.borders),s.background))+1,
            **{"offset"+k:round(v*100) for k,v in zip(("Left","Right","Top","Bottom"),s.border_offsets)},
            connect=int(s.border_connect),ignoreMargin=int(s.border_ignore_margin))
    styles = _element(refs, "hh", "styles", itemCnt=1)
    _element(styles, "hh", "style", id=0, type="PARA", name="바탕글", engName="Normal", paraPrIDRef=0, charPrIDRef=0, nextStyleIDRef=0, langID=1042, lockForm=0)
    _element(head, "hh", "compatibleDocument", targetProgram="HWP201X")
    section = ET.Element("{" + NS["hs"] + "}sec")
    image_parts = {}
    counter = [1]
    first = [True]
    written_parts = set()
    def section_properties(run):
        sec = _element(run, "hp", "secPr", id="", textDirection="HORIZONTAL", spaceColumns=1134, tabStop=8000,
                       tabStopVal=4000, tabStopUnit="HWPUNIT", outlineShapeIDRef=0, memoShapeIDRef=0, textVerticalWidthHead=0, masterPageCnt=0)
        _element(sec, "hp", "grid", lineGrid=0, charGrid=0, wonggojiFormat=0)
        _element(sec, "hp", "startNum", pageStartsOn="BOTH", page=0, pic=0, tbl=0, equation=0)
        _element(sec, "hp", "visibility", hideFirstHeader=0, hideFirstFooter=0, hideFirstMasterPage=0, border="SHOW_ALL", fill="SHOW_ALL", hideFirstPageNum=0, hideFirstEmptyLine=0, showLineNumber=0)
        page = _element(sec, "hp", "pagePr", landscape="NARROWLY" if page_layout.landscape else "WIDELY", width=page_width, height=page_height, gutterType=page_layout.gutter_type)
        _element(page, "hp", "margin", **{k: round(getattr(page_layout, k + "_pt") * 100) for k in ("header", "footer", "gutter", "left", "right", "top", "bottom")})
        ctrl = _element(run, "hp", "ctrl")
        _element(ctrl, "hp", "colPr", id="", type="NEWSPAPER", layout="LEFT", colCount=1, sameSz=1, sameGap=0)
    def new_p(parent, ps, default_style=None):
        p = _element(parent, "hp", "p", id=counter[0], paraPrIDRef=paras.index(ps), styleIDRef=0,
                     pageBreak=int(ps.page_break), columnBreak=0, merged=0)
        counter[0] += 1
        if first[0]:
            section_properties(_element(p, "hp", "run", charPrIDRef=chars.index(default_style or TextStyle())))
            first[0] = False
        return p
    def write_header_footer(anchor, part):
        ctrl = _element(_element(anchor, "hp", "run", charPrIDRef=0), "hp", "ctrl")
        element = _element(ctrl,"hp",part.kind,id=counter[0],applyPageType=part.page_type)
        counter[0] += 1
        sub = _element(element,"hp","subList",id="",textDirection="HORIZONTAL",lineWrap="BREAK",vertAlign=part.vertical_align,linkListIDRef=0,linkListNextIDRef=0,
            textWidth=round(part.width*100),textHeight=round(part.height*100),hasTextRef=0,hasNumRef=0)
        write_blocks(sub,part.blocks or [Paragraph()],False)
    def write_table(parent, table, anchor=None, default_style=None):
        _table_grid(table)
        run = _element(anchor if anchor is not None else new_p(parent, ParaStyle(),table.anchor_style), "hp", "run", charPrIDRef=chars.index(table.anchor_style or default_style or TextStyle()))
        tbl = _element(run, "hp", "tbl", id=counter[0], zOrder=table.position.z_order, numberingType="TABLE", textWrap=table.position.wrap, textFlow=table.position.text_flow, lock=0, dropcapstyle="None", pageBreak=table.page_break, repeatHeader=int(table.repeat_header), rowCnt=table.rows, colCnt=table.cols, cellSpacing=round(table.cell_spacing * 100), borderFillIDRef=border_fills.index(BorderFill(tuple(table.borders), table.background)) + 1, noAdjust=0)
        width = round(table.width * 100)
        _element(tbl, "hp", "sz", width=width, widthRelTo="ABSOLUTE", height=round(table.height * 100), heightRelTo="ABSOLUTE", protect=0)
        _write_position_xml(tbl, table.position)
        _element(tbl, "hp", "inMargin", **{k: round(v * 100) for k, v in zip(("left", "right", "top", "bottom"), table.padding)})
        for row in range(table.rows):
            tr = _element(tbl, "hp", "tr")
            for c in sorted((x for x in table.cells if x.row == row), key=lambda c: c.col):
                bid = border_fills.index(BorderFill(tuple(c.borders), c.background)) + 1
                tc = _element(tr, "hp", "tc", name="", header=int(c.header), hasMargin=int(not all(c.padding_inherited)), protect=0, editable=0, dirty=0, borderFillIDRef=bid)
                sub = _element(tc, "hp", "subList", id="", textDirection="HORIZONTAL", lineWrap="BREAK", vertAlign=c.vertical_align, linkListIDRef=0, linkListNextIDRef=0, textWidth=0, textHeight=0, hasTextRef=0, hasNumRef=0)
                write_blocks(sub, c.blocks or [Paragraph()])
                _element(tc, "hp", "cellAddr", colAddr=c.col, rowAddr=c.row)
                _element(tc, "hp", "cellSpan", colSpan=c.col_span, rowSpan=c.row_span)
                _element(tc, "hp", "cellSz", width=round(c.width * 100) or round(sum(table.column_widths[c.col:c.col+c.col_span]) * 100), height=round(c.height * 100))
                _element(tc, "hp", "cellMargin", **{k: 4294967295 if inherited else round(v * 100) for k, v, inherited in zip(("left", "right", "top", "bottom"), c.padding, c.padding_inherited)})
    def write_picture(run, r):
        ident = "image" + str(len(image_parts) + 1)
        image_parts[ident] = (r.image_ext, r.image)
        width, height = round((r.width or 200) * 75), round((r.height or 150) * 75)
        pg = r.picture
        ow, oh = round(pg.original_width_pt * 100) or width, round(pg.original_height_pt * 100) or height
        cw,ch = _current_size(pg,width,height,r.source_start is not None)
        cx,cy = _rotation_center(pg,cw,ch,r.source_start is not None)
        pic = _element(run, "hp", "pic", id=counter[0], zOrder=pg.position.z_order, numberingType="PICTURE", textWrap=pg.position.wrap, textFlow=pg.position.text_flow, lock=0, dropcapstyle="None", href="", groupLevel=0, instid=counter[0], reverse=0)
        _element(pic, "hp", "offset", x=round(pg.offset_x_pt*100) & 0xffffffff, y=round(pg.offset_y_pt*100) & 0xffffffff)
        _element(pic, "hp", "orgSz", width=ow, height=oh)
        _element(pic, "hp", "curSz", width=cw, height=ch)
        _element(pic, "hp", "flip", horizontal=int(pg.flip_h), vertical=int(pg.flip_v))
        _element(pic, "hp", "rotationInfo", angle=pg.rotation, centerX=cx, centerY=cy, rotateimage=int(pg.rotate_image))
        render = _element(pic, "hp", "renderingInfo")
        for n in ("transMatrix", "scaMatrix", "rotMatrix"):
            _element(render, "hc", n, **{f"e{i+1}": value for i, value in enumerate(pg.transforms.get(n, [1, 0, 0, 0, 1, 0]))})
        _element(pic, "hp", "lineShape", color="#000000", width=0, style="NONE", endCap="FLAT", headStyle="NORMAL", tailStyle="NORMAL", headfill=0, tailfill=0, headSz="SMALL_SMALL", tailSz="SMALL_SMALL", outlineStyle="NORMAL", alpha=0)
        rect = _element(pic, "hp", "imgRect")
        points = pg.rect or ((0, 0), (ow / 100, 0), (ow / 100, oh / 100), (0, oh / 100))
        for n, (x, y) in zip(("pt0", "pt1", "pt2", "pt3"), points):
            x, y = round(x * 100), round(y * 100)
            _element(rect, "hc", n, x=x, y=y)
        iw, ih = round(pg.image_width_pt * 100) or ow, round(pg.image_height_pt * 100) or oh
        crop = pg.crop if pg.crop[2] and pg.crop[3] else (0, 0, iw / 100, ih / 100)
        _element(pic, "hp", "imgClip", **{k: round(v * 100) for k, v in zip(("left", "top", "right", "bottom"), crop)})
        _element(pic, "hp", "inMargin", **{k: round(v * 100) for k, v in zip(("left", "right", "top", "bottom"), pg.in_margin)})
        _element(pic, "hp", "imgDim", dimwidth=iw, dimheight=ih)
        _element(pic, "hc", "img", binaryItemIDRef=ident, bright=pg.brightness, contrast=pg.contrast, effect=pg.effect, alpha=pg.alpha)
        _element(pic, "hp", "sz", width=width, widthRelTo="ABSOLUTE", height=height, heightRelTo="ABSOLUTE", protect=0)
        _write_position_xml(pic, pg.position)
        _element(pic, "hp", "shapeComment").text = "독립 편집기 삽입 그림"
        counter[0] += 1
    def write_drawing(parent, drawing, anchor=None, default_style=None):
        run = _element(anchor if anchor is not None else new_p(parent, ParaStyle(),drawing.anchor_style), "hp", "run", charPrIDRef=chars.index(drawing.anchor_style or default_style or TextStyle()))
        d, pg = drawing, drawing.shape
        w, h = round(d.width * 100), round(d.height * 100)
        ow, oh = round(pg.original_width_pt * 100) or w, round(pg.original_height_pt * 100) or h
        cw,ch = _current_size(pg,w,h,d.source_index>=0)
        cx,cy = _rotation_center(pg,cw,ch,d.source_index>=0)
        if d.kind not in ("rect", "line", "ellipse", "polygon", "curve", "arc"):
            raise ValueError("이 그리기 개체는 HWPX로 재구성할 수 없습니다.")
        kind_attrs = {"ratio": d.geometry.get("ratio", 0)} if d.kind == "rect" else {}
        if d.kind == "line":
            kind_attrs["isReverseHV"] = int(d.geometry.get("isReverseHV", False))
        elif d.kind == "ellipse":
            kind_attrs.update(intervalDirty=int(d.geometry.get("intervalDirty", False)), hasArcPr=int(d.geometry.get("hasArcPr", False)), arcType=d.geometry.get("arcType", "NORMAL"))
        elif d.kind == "arc":
            kind_attrs["type"] = d.geometry.get("type", "NORMAL")
        rect = _element(run, "hp", d.kind, id=counter[0], zOrder=d.position.z_order, numberingType="PICTURE", textWrap=d.position.wrap,
            textFlow=d.position.text_flow, lock=0, dropcapstyle="None", href="", groupLevel=0, instid=counter[0], **kind_attrs)
        counter[0] += 1
        _element(rect, "hp", "offset", x=round(pg.offset_x_pt*100) & 0xffffffff, y=round(pg.offset_y_pt*100) & 0xffffffff)
        _element(rect, "hp", "orgSz", width=ow, height=oh)
        _element(rect, "hp", "curSz", width=cw, height=ch)
        _element(rect, "hp", "flip", horizontal=int(pg.flip_h), vertical=int(pg.flip_v))
        _element(rect, "hp", "rotationInfo", angle=pg.rotation, centerX=cx, centerY=cy, rotateimage=int(pg.rotate_image))
        render = _element(rect, "hp", "renderingInfo")
        chain = d.geometry.get("transform_chain") or [(name, pg.transforms.get(name, [1,0,0,0,1,0])) for name in ("transMatrix", "scaMatrix", "rotMatrix")]
        for name, values in chain:
            if name not in ("transMatrix", "scaMatrix", "rotMatrix") or len(values) != 6:
                raise ValueError("지원하지 않는 도형 변환 행렬입니다.")
            _element(render, "hc", name, **{f"e{i+1}": v for i,v in enumerate(values)})
        border = d.borders[0]
        options = dict(endCap="FLAT",headStyle="NORMAL",tailStyle="NORMAL",headfill=1,tailfill=1,
            headSz="SMALL_SMALL",tailSz="SMALL_SMALL",outlineStyle="NORMAL",alpha=0)
        options.update(d.line_options)
        _element(rect, "hp", "lineShape", color=border.color, width=round(border.width_pt*100), style=border.style, **options)
        if d.background:
            brush = _element(rect, "hc", "fillBrush")
            _element(brush, "hc", "winBrush", faceColor=d.background, hatchColor="#000000", alpha=0)
        _element(rect, "hp", "shadow", type="NONE", color="#000000", offsetX=0, offsetY=0, alpha=0)
        if d.blocks:
            text = _element(rect, "hp", "drawText", lastWidth=w, name="", editable=0)
            sub = _element(text, "hp", "subList", id="", textDirection="HORIZONTAL", lineWrap="BREAK", vertAlign=d.vertical_align, linkListIDRef=0, linkListNextIDRef=0,
                textWidth=0, textHeight=0, hasTextRef=0, hasNumRef=0)
            write_blocks(sub, d.blocks)
            _element(text, "hp", "textMargin", **{k: round(v*100) for k,v in zip(("left","right","top","bottom"),d.padding)})
        def write_point(name, point):
            x, y = point
            _element(rect, "hc", name, x=round(x*100), y=round(y*100))
        if d.kind == "rect":
            points = pg.rect or ((0,0),(ow/100,0),(ow/100,oh/100),(0,oh/100))
            for name, point in zip(("pt0","pt1","pt2","pt3"), points):
                write_point(name, point)
        elif d.kind == "line":
            points = d.geometry.get("points") or ((0,0),(ow/100,oh/100))
            if len(points) != 2:
                raise ValueError("직선에는 시작점과 끝점이 필요합니다.")
            for name, point in zip(("startPt", "endPt"), points):
                write_point(name, point)
        elif d.kind == "polygon":
            points = d.geometry.get("points", ())
            if len(points) < 3:
                raise ValueError("다각형에는 세 개 이상의 꼭짓점이 필요합니다.")
            for point in points:
                write_point("pt", point)
        elif d.kind in ("ellipse", "arc"):
            defaults = {"center":(ow/200,oh/200), "ax1":(ow/100,oh/200), "ax2":(ow/200,0)}
            for name in ("center", "ax1", "ax2", "start1", "end1", "start2", "end2"):
                if name in d.geometry or name in defaults or d.kind == "ellipse":
                    write_point(name, d.geometry.get(name, defaults.get(name, (0,0))))
        elif d.kind == "curve":
            segments = d.geometry.get("segments", ())
            if not segments:
                raise ValueError("곡선의 선분 데이터가 없습니다.")
            for segment in segments:
                _element(rect, "hp", "seg", type=segment.get("type", "LINE"), **{key: round(segment[key]*100) for key in ("x1", "y1", "x2", "y2")})
        _element(rect, "hp", "sz", width=w, widthRelTo="ABSOLUTE", height=h, heightRelTo="ABSOLUTE", protect=0)
        _write_position_xml(rect,d.position)
    def write_blocks(parent, bs, include_parts=True):
        index = 0
        while index < len(bs):
            b = bs[index]
            if isinstance(b, Table):
                write_table(parent, b)
            elif isinstance(b, Drawing):
                write_drawing(parent,b)
            else:
                p = new_p(parent, b.style, b.default_style)
                if include_parts:
                    for j,part in enumerate(document_parts):
                        if j not in written_parts and (part.source_index == b.source_index or (b.source_index < 0 and not written_parts)):
                            written_parts.add(j)
                            write_header_footer(p,part)
                attached = []
                next_index = index + 1
                while next_index < len(bs) and isinstance(bs[next_index],(Table,Drawing)) and b.source_index >= 0 and b.source_index == bs[next_index].source_index:
                    attached.append(bs[next_index])
                    next_index += 1
                index = next_index - 1
                ordered = list(b.runs) + attached
                if not ordered:
                    ordered = [Run(style=b.default_style)]
                if attached:
                    ordered.sort(key=lambda item: item.anchor_text_start if isinstance(item,(Table,Drawing)) else item.source_start or 0)
                for r in ordered:
                    if isinstance(r,(Table,Drawing)):
                        (write_table if isinstance(r,Table) else write_drawing)(parent,r,p,b.default_style)
                        continue
                    run = _element(p, "hp", "run", charPrIDRef=chars.index(r.style))
                    if r.image:
                        write_picture(run, r)
                    elif r.field and r.field.get("kind") == "auto_number":
                        field = _element(_element(run,"hp","ctrl"),"hp","autoNum",num=r.field.get("number",1),numType=r.field.get("number_type","PAGE"))
                        _element(field,"hp","autoNumFormat",type="DIGIT",userChar="",prefixChar="",suffixChar="",supscript=0)
                    elif r.field and r.field.get("kind") == "new_number":
                        _element(_element(run,"hp","ctrl"),"hp","newNum",num=r.field.get("number",1),numType=r.field.get("number_type","PAGE"))
                    else:
                        t = _element(run, "hp", "t")
                        pieces = re.split(r"([\n\t])", r.text)
                        t.text = pieces[0]
                        last = None
                        for part in pieces[1:]:
                            if part in ("\n", "\t"):
                                last = _element(t, "hp", "lineBreak" if part == "\n" else "tab")
                            elif last is not None:
                                last.tail = part
                if b.end_style is not None:
                    _element(p, "hp", "run", charPrIDRef=chars.index(b.end_style))
            index += 1
    write_blocks(section, blocks or [Paragraph()])
    package = ET.Element("{" + NS["opf"] + "}package", version="", id="", **{"unique-identifier": ""})
    meta = _element(package, "opf", "metadata")
    _element(meta, "opf", "title").text = Path(path).stem
    _element(meta, "opf", "language").text = "ko"
    _element(meta, "opf", "meta", name="creator", content="text").text = "독립 한글 편집기"
    manifest = _element(package, "opf", "manifest")
    for ident, href in (("header", "Contents/header.xml"), ("section0", "Contents/section0.xml"), ("settings", "settings.xml")):
        _element(manifest, "opf", "item", id=ident, href=href, **{"media-type": "application/xml"})
    for ident, (ext, _) in image_parts.items():
        _element(manifest, "opf", "item", id=ident, href="BinData/" + ident + "." + ext, isEmbeded=1, **{"media-type": mimetypes.types_map.get("." + ext, "image/png")})
    spine = _element(package, "opf", "spine")
    _element(spine, "opf", "itemref", idref="header", linear="yes")
    _element(spine, "opf", "itemref", idref="section0", linear="yes")
    container = ET.Element("{" + NS["ocf"] + "}container")
    roots = _element(container, "ocf", "rootfiles")
    _element(roots, "ocf", "rootfile", **{"full-path": "Contents/content.hpf", "media-type": "application/hwpml-package+xml"})
    version = ET.Element("{" + NS["hv"] + "}HCFVersion", tagetApplication="WORDPROCESSOR", major="5", minor="1", micro="1", buildNumber="0", os="1", xmlVersion="1.5", application="Independent Hangul Editor", appVersion="0.1")
    settings = b'<?xml version="1.0" encoding="UTF-8"?><ha:HWPApplicationSetting xmlns:ha="http://www.hancom.co.kr/hwpml/2011/app"/>'
    result = io.BytesIO()
    with zipfile.ZipFile(result, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("mimetype", "application/hwp+zip", compress_type=zipfile.ZIP_STORED)
        for name, data in (("version.xml", _xml(version)), ("Contents/header.xml", _xml(head)), ("Contents/section0.xml", _xml(section)),
            ("Contents/content.hpf", _xml(package)), ("META-INF/container.xml", _xml(container)), ("settings.xml", settings)):
            z.writestr(name, data)
        z.writestr("Preview/PrvText.txt", "\n".join("".join(r.text for r in b.runs) for b in _walk_blocks(blocks) if isinstance(b, Paragraph)))
        for ident, (ext, data) in image_parts.items():
            z.writestr("BinData/" + ident + "." + ext, data)
    _atomic_write(path, result.getvalue())
    _warning(warnings, "HWPX는 지원하는 본문·글자 서식·표·그림·글상자·머리말·꼬리말로 새로 생성했습니다. 일부 고급 조판·필드·변경 이력은 보존되지 않습니다.")
    return warnings


def _record(tag, level, data):
    size = len(data)
    prefix = struct.pack("<I", tag | (level << 10) | (min(size, 4095) << 20))
    return prefix + (struct.pack("<I", size) if size >= 4095 else b"") + data


def _wstr(value):
    data = value.encode("utf-16le")
    return struct.pack("<H", len(data) // 2) + data


def _deflate(data):
    z = zlib.compressobj(6, zlib.DEFLATED, -15)
    return z.compress(data) + z.flush()


def save_hwp(document_or_html, path) -> list[str]:
    """Create original HWP 5 records, raster pictures and CFB without COM."""
    from .hwp_native import patch_hwp_source
    if patch_hwp_source(document_or_html, path):
        return []
    if getattr(document_or_html, "_hwp_source", {}).get("read_only"):
        raise ValueError("배포용 HWP는 읽기 전용 문서입니다. 편집 제한을 해제한 원본이 필요합니다.")
    if getattr(document_or_html, "_hwp_source", {}).get("legacy_protected"):
        raise ValueError("HWP 3.0 편집 보호 문서입니다. 보호를 해제한 원본이 필요합니다.")
    blocks, warnings = _qt_blocks(document_or_html)
    if getattr(document_or_html, "_hwp_source", None):
        _warning(warnings, "원본 HWP 보존 저장 범위를 벗어나 지원 서식으로 재생성합니다. "
            + getattr(document_or_html, "_hwp_preservation_error", ""))
    document_parts = _document_parts(document_or_html)
    style_blocks = blocks + [b for part in document_parts for b in part.blocks]
    if any(isinstance(block, Drawing) and block.kind != "rect" for block in _walk_blocks(style_blocks)):
        raise ValueError("HWP로 새로 생성하는 비사각형 도형은 아직 지원하지 않습니다. 원본 형식 보존 저장을 사용하세요.")
    page_width, page_height, page_margin = _page_settings(document_or_html)
    page_layout = _page_layout(document_or_html)
    chars = list(dict.fromkeys([TextStyle()] + _text_styles(style_blocks)))
    paras = list(dict.fromkeys([ParaStyle()] + [b.style for b in _walk_blocks(style_blocks) if isinstance(b, Paragraph)]))
    fonts = list(dict.fromkeys(face for s in chars for face in _font_pairs(s)))
    border_fills = _border_fills(style_blocks)
    images = [r for b in _walk_blocks(blocks) if isinstance(b, Paragraph) for r in b.runs if r.image]
    image_ids = {id(r): i + 1 for i, r in enumerate(images)}
    docinfo = bytearray()
    docinfo.extend(_record(16, 0, struct.pack("<7H3I", 1, 1, 1, 1, 1, 1, 1, 0, 0, 0)))
    counts = [len(images)] + [len(fonts)] * 7 + [len(border_fills), len(chars), 1, 0, 0, len(paras), 1, 0, 0, 0]
    docinfo.extend(_record(17, 0, struct.pack("<18I", *counts)))
    for i, r in enumerate(images, 1):
        docinfo.extend(_record(18, 1, struct.pack("<HH", 0x11, i) + _wstr(r.image_ext)))
    for _ in range(7):
        for font,kind in fonts:
            docinfo.extend(_record(19,1,bytes([{"UNKNOWN":0,"TTF":1,"HFT":2,"UNKNOWN_3":3}.get(kind,1)])+_wstr(font)))
    for fill in border_fills:
        bf = bytearray(struct.pack("<H", 0))
        # HWP serializes border style/width/color as four interleaved tuples.
        for border in fill.borders:
            style = _BORDER_STYLES.index(border.style) if border.style in _BORDER_STYLES else 1
            mm = border.width_pt * 25.4 / 72
            thickness = min(range(16), key=lambda i: abs(_BORDER_WIDTHS[i] - mm))
            bf.extend(struct.pack("<BBI", style, thickness, _rgb(border.color)))
        bf.extend(struct.pack("<BBI", 0, 0, 0))
        if fill.background:
            bf.extend(struct.pack("<IIIi", 1, _rgb(fill.background), 0xFFFFFFFF, -1))
            bf.extend(struct.pack("<IB", 0, 0))
        else:
            bf.extend(struct.pack("<II", 0, 0))
        docinfo.extend(_record(20, 1, bytes(bf)))
    for s in chars:
        flags = int(s.italic) | (int(s.bold) << 1) | (int(s.underline) << 2) | (int(s.strike) << 18) | (int(s.superscript) << 15) | (int(s.subscript) << 16)
        flags |= int(s.use_font_space) << 25 | int(s.use_kerning) << 30
        d = struct.pack("<7H", *(fonts.index(face) for face in _font_pairs(s)))
        for values, fallback, signed in ((s.language_ratios, s.stretch, False), (s.language_spacing, s.letter_spacing, True),
            (s.language_relative_sizes, 100, False), (s.language_offsets, 0, True)):
            if len(values) not in (0, 7):
                raise ValueError("HWP 언어별 글자 서식은 7개 언어 값을 가져야 합니다.")
            values = values or (fallback,) * 7
            if any(not isinstance(value, int) or not (-128 <= value <= 127 if signed else 0 <= value <= 255) for value in values):
                raise ValueError("HWP 언어별 글자 서식 값이 허용 범위를 벗어났습니다.")
            d += struct.pack("<7b", *values) if signed else bytes(values)
        d += struct.pack("<iIbbIIIIHI", round(s.size * 100), flags, 0, 0, _rgb(s.color), _rgb(s.color),
                         _rgb(s.background) if s.background else 0xFFFFFFFF, 0x00C0C0C0, 1, _rgb(s.color))
        docinfo.extend(_record(21, 1, d))
    docinfo.extend(_record(22, 1, struct.pack("<II", 0, 0)))
    for s in paras:
        alignment = {"JUSTIFY": 0, "LEFT": 1, "RIGHT": 2, "CENTER": 3,"DISTRIBUTE":4,"DISTRIBUTE_SPACE":5}.get(s.align, 1)
        line_type = {"PERCENT": 0, "FIXED": 1, "BETWEEN_LINES": 2,"AT_LEAST":3}.get(s.line_type, 0)
        flags = (alignment << 2) | ({"KEEP_WORD":0,"HYPHENATION":1,"BREAK_WORD":2}.get(s.break_latin,0)<<5)
        flags |= int(s.break_nonlatin=="KEEP_WORD")<<7 | int(s.snap_to_grid)<<8 | min(127,max(0,s.condense))<<9
        flags |= int(s.widow_orphan)<<16 | int(s.keep_with_next)<<17 | int(s.keep_lines)<<18 | int(s.page_break)<<19
        flags |= {"BASELINE":0,"TOP":1,"CENTER":2,"BOTTOM":3}.get(s.vertical_align,0)<<20 | int(s.font_line_height)<<22
        flags |= int(s.border_connect)<<28 | int(s.border_ignore_margin)<<29
        prop2 = {"BREAK":0,"SQUEEZE":1,"KEEP":2}.get(s.line_wrap,0) | int(s.auto_spacing_eng)<<4 | int(s.auto_spacing_num)<<5
        line = round(s.line if not line_type else s.line * 100)
        d = struct.pack("<I6i3H4hIII", flags, round(s.left * 200), round(s.right * 200), round(s.indent * 200),
            round(s.before * 200), round(s.after * 200), line, 0, 0, border_fills.index(BorderFill(tuple(s.borders),s.background))+1,
            *(round(v*100) for v in s.border_offsets),prop2,line_type,line)
        d += struct.pack("<I", 0)
        docinfo.extend(_record(25, 1, d))
    style = _wstr("바탕글") + _wstr("Normal") + struct.pack("<BBhHHH", 0, 0, 1042, 0, 0, 0)
    docinfo.extend(_record(26, 1, style))
    docinfo.extend(_record(30, 0, struct.pack("<I", 0)))
    docinfo.extend(_record(31, 1, bytes(20)))
    body = bytearray()
    first = [True]
    written_parts = set()
    instance = [1]
    def control_char(code, ident):
        return struct.pack("<H", code) + ident[::-1].encode("ascii") + bytes(8) + struct.pack("<H", code)
    def section_controls(level):
        sec = b"dces" + struct.pack("<I3Hi6H", 0, 1134, 0, 0, 8000, 0, 0, 0, 0, 0, 1042)
        body.extend(_record(71, level + 1, sec))
        pageflags = int(page_layout.landscape) | {"LEFT_ONLY": 0, "LEFT_RIGHT": 1, "TOP_BOTTOM": 2}.get(page_layout.gutter_type, 0) << 1
        page = struct.pack("<10I", page_width, page_height, *(round(getattr(page_layout, k + "_pt") * 100) for k in ("left", "right", "top", "bottom", "header", "footer", "gutter")), pageflags)
        body.extend(_record(73, level + 2, page))
        footnote = struct.pack("<I4HI3HBBI", 0, 0, 0, ord(")"), 1, 0xFFFFFFFF, 850, 567, 283, 1, 1, 0)
        body.extend(_record(74, level + 2, footnote))
        body.extend(_record(74, level + 2, footnote))
        for _ in range(3):
            body.extend(_record(75, level + 2, struct.pack("<I4hH", 1, 1417, 1417, 1417, 1417, 1)))
        body.extend(_record(71, level + 1, b"dloc" + struct.pack("<HHHBBI", 0x1004, 0, 0, 0, 0, 0)))
    def emit_paragraph(p, level=0, last=False, table=None, cell_width=0, drawing=None):
        initial = first[0]
        first[0] = False
        text = bytearray()
        masks = 0
        if initial:
            text.extend(control_char(2, "secd"))
            text.extend(control_char(2, "cold"))
            masks |= 1 << 2
        shapes = []
        parts = []
        if level == 0:
            for j,part in enumerate(document_parts):
                if j not in written_parts and (part.source_index == p.source_index or (p.source_index < 0 and initial)):
                    written_parts.add(j)
                    text.extend(control_char(16,"head" if part.kind == "header" else "foot"))
                    masks |= 1 << 16
                    parts.append(part)
        if table:
            shapes.append((len(text) // 2, chars.index(table.anchor_style or p.default_style)))
            text.extend(control_char(11, "tbl "))
            masks |= 1 << 11
        if drawing:
            shapes.append((len(text) // 2, chars.index(drawing.anchor_style or p.default_style)))
            text.extend(control_char(11, "gso "))
            masks |= 1 << 11
        # Qt represents a visually empty <p><br/></p> as a soft line break.
        # HWP's empty-paragraph representation is one paragraph-break unit,
        # with no PARA_TEXT record; a LF+CR trailer after a table is rejected
        # by Hancom even though its record lengths and CFB container are valid.
        empty = not table and not drawing and not any(r.image or r.field or r.text.replace("\n", "").replace("\u2028", "") for r in p.runs)
        runs = [] if empty else p.runs
        pictures = []
        fields = []
        for r in runs:
            shapes.append((len(text) // 2, chars.index(r.style)))
            if r.image:
                masks |= 1 << 11
                text.extend(control_char(11, "gso "))
                pictures.append(r)
            elif r.field and r.field.get("kind") in ("auto_number","new_number"):
                code, ident = (18,"atno") if r.field["kind"] == "auto_number" else (21,"nwno")
                text.extend(control_char(code,ident))
                masks |= 1 << code
                fields.append(r.field)
            else:
                for ch in r.text:
                    if ch == "\t":
                        masks |= 1 << 9
                        text.extend(struct.pack("<H", 9) + bytes(12) + struct.pack("<H", 9))
                    else:
                        if ch == "\n":
                            masks |= 1 << 10
                        text.extend(ch.encode("utf-16le"))
        if not shapes:
            shapes = [(0, chars.index(p.default_style))]
        elif shapes[0][0]:
            shapes.insert(0, (0, shapes[0][1]))
        if p.end_style is not None:
            end = (len(text) // 2, chars.index(p.end_style))
            if shapes[-1][0] == end[0]:
                shapes[-1] = end
            elif shapes[-1][1] != end[1]:
                shapes.append(end)
        text.extend(b"\r\0")
        nchar = len(text) // 2 | (0x80000000 if last else 0)
        header = struct.pack("<IIHBBHHHIH", nchar, masks, paras.index(p.style), 0, 4 if p.style.page_break else 0, len(shapes), 0, 0, instance[0], 0)
        instance[0] += 1
        body.extend(_record(66, level, header))
        if text != b"\r\0":
            body.extend(_record(67, level + 1, bytes(text)))
        body.extend(_record(68, level + 1, b"".join(struct.pack("<II", *s) for s in shapes)))
        if initial:
            section_controls(level)
        for part in parts:
            emit_header_footer(part,level)
        if table:
            emit_table(table, level)
        if drawing:
            emit_drawing(drawing, level)
        for r in pictures:
            emit_picture(r, level)
        for field in fields:
            kind = {"PAGE":0,"FOOTNOTE":1,"ENDNOTE":2,"PICTURE":3,"TABLE":4,"EQUATION":5}.get(field.get("number_type"),0)
            data = b"onta"+struct.pack("<IIHH",kind,field.get("number",1),0,0) if field["kind"] == "auto_number" else b"onwn"+struct.pack("<IH",kind,field.get("number",1))
            body.extend(_record(71,level+1,data))
    def emit_header_footer(part,level):
        ident = b"daeh" if part.kind == "header" else b"toof"
        body.extend(_record(71,level+1,ident+struct.pack("<II",{"BOTH":0,"EVEN":1,"ODD":2}.get(part.page_type,0),1)))
        flags = {"TOP":0,"CENTER":1,"BOTTOM":2}.get(part.vertical_align,0)<<5
        header = struct.pack("<IIII",_paragraph_count(part.blocks),flags,round(part.width*100),round(part.height*100))+bytes(18)
        body.extend(_record(72,level+2,header))
        emit_blocks(part.blocks or [Paragraph()],level+2)
    def object_header(ident, position, width, height, numbering):
        value = ident[::-1].encode("ascii") + struct.pack("<IiiIIi4hIIH", _object_flags(position, numbering),
            round(position.y_pt * 100), round(position.x_pt * 100), width, height, position.z_order,
            *(round(v * 100) for v in position.out_margin), instance[0], 0, 0)
        instance[0] += 1
        return value + bytes(2)
    def emit_picture(r, level):
        pg = r.picture
        w, h = round((r.width or 200) * 75), round((r.height or 150) * 75)
        ow, oh = round(pg.original_width_pt * 100) or w, round(pg.original_height_pt * 100) or h
        iw, ih = round(pg.image_width_pt * 100) or ow, round(pg.image_height_pt * 100) or oh
        cw,ch = _current_size(pg,w,h,r.source_start is not None)
        cx,cy = _rotation_center(pg,cw,ch,r.source_start is not None)
        body.extend(_record(71, level + 1, object_header("gso ", pg.position, w, h, 1)))
        componentflags = pg.native_flags & ~(3 | (1<<19)) if r.source_start is not None else 0
        componentflags |= int(pg.flip_h) | int(pg.flip_v)<<1 | int(pg.rotate_image)<<19
        component = b"cip$cip$" + struct.pack("<iiHH4IIHiiH", round(pg.offset_x_pt*100),round(pg.offset_y_pt*100),0,1,ow,oh,cw,ch,
            componentflags,round(pg.rotation),cx,cy,1)
        for name in ("transMatrix", "scaMatrix", "rotMatrix"):
            component += struct.pack("<6d", *pg.transforms.get(name, [1, 0, 0, 0, 1, 0]))
        body.extend(_record(76, level + 2, component))
        points = pg.rect or ((0, 0), (ow / 100, 0), (ow / 100, oh / 100), (0, oh / 100))
        crop = pg.crop if pg.crop[2] and pg.crop[3] else (0, 0, iw / 100, ih / 100)
        data = struct.pack("<IiI", 0, 0, 0) + struct.pack("<8i", *(round(v * 100) for point in points for v in point))
        data += struct.pack("<4i4hbbBHBI", *(round(v * 100) for v in crop), *(round(v * 100) for v in pg.in_margin),
            pg.contrast, pg.brightness, {"REAL_PIC": 0, "GRAY_SCALE": 1, "BLACK_WHITE": 2, "PATTERN8x8": 4}.get(pg.effect, 0),
            image_ids[id(r)], pg.border_opacity, instance[0])
        data += struct.pack("<IIIB", 0, iw, ih, pg.alpha)
        instance[0] += 1
        body.extend(_record(85, level + 3, data))
    def emit_drawing(d, level):
        w, h = round(d.width * 100), round(d.height * 100)
        shape = d.shape
        ow, oh = round(shape.original_width_pt * 100) or w, round(shape.original_height_pt * 100) or h
        cw,ch = _current_size(shape,w,h,d.source_index>=0)
        cx,cy = _rotation_center(shape,cw,ch,d.source_index>=0)
        body.extend(_record(71, level + 1, object_header("gso ", d.position, w, h, 1)))
        flags = (shape.native_flags if d.source_index>=0 else 0) | (1<<24)
        component = b"cer$cer$" + struct.pack("<iiHH4IIHiiH",round(shape.offset_x_pt*100),round(shape.offset_y_pt*100),0,1,ow,oh,cw,ch,flags,round(shape.rotation),cx,cy,1)
        for name in ("transMatrix", "scaMatrix", "rotMatrix"):
            component += struct.pack("<6d", *shape.transforms.get(name, [1, 0, 0, 0, 1, 0]))
        border = d.borders[0]
        component += struct.pack("<IIIB", _rgb(border.color), round(border.width_pt * 100), _line_flags_hwp(border.style,d.line_options), d.line_options.get("alpha",0))
        if d.background:
            component += struct.pack("<IIIiIB", 1, _rgb(d.background), 0xFFFFFFFF, -1, 0, 0)
        else:
            component += struct.pack("<II", 0, 0)
        component += bytes(16) + struct.pack("<IH", instance[0], 0)
        instance[0] += 1
        body.extend(_record(76, level + 2, component))
        paragraphs = d.blocks or [Paragraph()]
        flags = {"TOP":0,"CENTER":1,"BOTTOM":2}.get(d.vertical_align,1)<<5
        list_header = struct.pack("<II4hI", _paragraph_count(paragraphs), flags, *(round(v * 100) for v in d.padding), w) + bytes(13)
        body.extend(_record(72, level + 3, list_header))
        emit_blocks(paragraphs, level + 3, w)
        points = shape.rect or ((0, 0), (ow / 100, 0), (ow / 100, oh / 100), (0, oh / 100))
        body.extend(_record(79, level + 3, struct.pack("<B8i", 0, *(round(v * 100) for p in points for v in p))))
    def emit_table(t, level):
        _table_grid(t)
        w = round(t.width * 100)
        height = round(t.height * 100)
        body.extend(_record(71, level + 1, object_header("tbl ", t.position, w, height, 2)))
        row_sizes = [sum(1 for c in t.cells if c.row == row) for row in range(t.rows)]
        tableflags = {"NONE": 0, "TABLE": 1, "CELL": 2}.get(t.page_break, 2) | int(t.repeat_header) << 2
        tabledata = struct.pack("<I3H4H", tableflags, t.rows, t.cols, round(t.cell_spacing * 100), *(round(v * 100) for v in t.padding))
        tabledata += struct.pack("<%dH" % t.rows, *row_sizes) + struct.pack("<HH", border_fills.index(BorderFill(tuple(t.borders), t.background)) + 1, 0)
        body.extend(_record(77, level + 2, tabledata))
        for c in sorted(t.cells, key=lambda c: (c.row, c.col)):
            cell_blocks = c.blocks or [Paragraph()]
            bid = border_fills.index(BorderFill(tuple(c.borders), c.background)) + 1
            flags = c.native_flags & ~((3 << 5) | (1 << 16))
            flags |= {"TOP": 0, "CENTER": 1, "BOTTOM": 2}.get(c.vertical_align, 1) << 5
            flags |= int(not all(c.padding_inherited)) << 16
            d = struct.pack("<II4HII4HH", _paragraph_count(cell_blocks), flags, c.col, c.row, c.col_span, c.row_span,
                round(c.width * 100) or w * c.col_span // max(1, t.cols), round(c.height * 100), *(65535 if inherited else round(v * 100) for v, inherited in zip(c.padding, c.padding_inherited)), bid)
            # Observed modern HWP appends the cell's desired width followed
            # by reserved layout fields and an editable-state byte.
            d += struct.pack("<IIIB", round(c.width * 100) or w * c.col_span // max(1, t.cols), 0, 0, 0)
            body.extend(_record(72, level + 2, d))
            emit_blocks(cell_blocks, level + 2, round(c.width * 100) or w * c.col_span // max(1, t.cols))
    def emit_blocks(bs, level=0, cell_width=0):
        index = 0
        while index < len(bs):
            b = bs[index]
            following = bs[index + 1] if index + 1 < len(bs) else None
            if isinstance(b, Paragraph) and isinstance(following, (Table, Drawing)) and b.source_index >= 0 and b.source_index == following.source_index:
                emit_paragraph(b, level, index + 2 == len(bs), table=following if isinstance(following, Table) else None,
                    drawing=following if isinstance(following, Drawing) else None, cell_width=cell_width)
                index += 2
                continue
            if isinstance(b, Table):
                emit_paragraph(Paragraph(), level, index == len(bs) - 1, table=b, cell_width=cell_width)
            elif isinstance(b, Drawing):
                emit_paragraph(Paragraph(), level, index == len(bs) - 1, drawing=b, cell_width=cell_width)
            else:
                emit_paragraph(b, level, index == len(bs) - 1, cell_width=cell_width)
            index += 1
    emit_blocks(blocks or [Paragraph()])
    file_header = bytearray(256)
    file_header[:len(b"HWP Document File")] = b"HWP Document File"
    struct.pack_into("<II", file_header, 32, 0x05000302, 1)
    preview = "\n".join("".join(r.text for r in b.runs) for b in _walk_blocks(blocks) if isinstance(b, Paragraph)).encode("utf-16le")
    streams = {"FileHeader": bytes(file_header), "DocInfo": _deflate(bytes(docinfo)), "BodyText/Section0": _deflate(bytes(body)), "PrvText": preview}
    for i, r in enumerate(images, 1):
        streams[f"BinData/BIN{i:04X}.{r.image_ext}"] = _deflate(r.image)
    compound = write_compound(streams)
    _atomic_write(path, compound)
    _warning(warnings, "HWP는 지원하는 본문·글자 서식·표·그림·글상자·머리말·꼬리말로 새로 생성했습니다. 일부 고급 조판·필드·변경 이력은 보존되지 않습니다.")
    return warnings
