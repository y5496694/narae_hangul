"""Independent HWP 3.0 reader and conservative original-format text patches.

Binary structures follow Hancom's public HWP 3.0/HWPML revision 1.2 tables.
No office runtime, converted intermediate document or copied filter code is used.
본 제품은 한컴의 HWP 문서 파일(.hwp) 공개 문서를 참고하여 개발하였습니다.
Reference: https://cdn.hancom.com/link/docs/한글문서파일형식3.0_HWPML_revision1.2.pdf
"""
from __future__ import annotations

import struct
import zlib
import base64
from difflib import SequenceMatcher

SIGNATURE = b"HWP Document File V3.00 \x1a\x01\x02\x03\x04\x05"
UNIT = 72 / 1800
PALETTE = ("#000000", "#0000ff", "#00ff00", "#00ffff", "#ff0000", "#ff00ff", "#ffff00", "#ffffff")
FIXED_CONTROLS = {7: 84, 8: 96, 9: 8, 18: 8, 19: 8, 20: 8, 21: 8, 22: 24, 23: 10, 24: 6, 25: 6, 26: 246, 28: 64}


def encode_hwp3_source(source):
    """Encode legacy-only binary caches; the caller encodes common data."""
    result = dict(source)
    for key in ("body", "tail"):
        result[key] = base64.b64encode(source[key]).decode("ascii")
    return result


def decode_hwp3_source(source):
    """Restore legacy caches and integer paragraph identities from JSON."""
    result = dict(source)
    for key in ("body", "tail"):
        result[key] = base64.b64decode(source[key], validate=True)
    result["records"] = {int(key):value for key,value in source["records"].items()}
    return result


def decode_hchar(value):
    """HWP's internal Johab Hangul and KS X 1001 Hanja indexing."""
    if value < 128:
        return chr(value)
    if value in (0x81, 0x82):
        return "“" if value == 0x81 else "”"
    if value >= 0x8000:
        try:
            return value.to_bytes(2, "big").decode("johab")
        except UnicodeError:
            return "\u25a1"
    if 0x4000 <= value < 0x4000 + 4888:
        index = value - 0x4000
        return bytes([0xCA + index // 94, 0xA1 + index % 94]).decode("euc_kr", "replace")
    if 0x3400 <= value < 0x37C0:
        index = value - 0x3400
        row, column = 0xA1 + index // 96, 0xA0 + index % 96
        if row == 0xAA:
            row += 2
        try:
            return bytes([row, column]).decode("euc_kr")
        except UnicodeError:
            return "\u25a1"
    # Some HWP97 documents contain direct Unicode punctuation in this range.
    if 0x2000 <= value <= 0x206F:
        return chr(value)
    return "\u25a1"


def encode_hchar(character):
    value = ord(character)
    if 32 <= value < 128:
        return struct.pack("<H", value)
    if character in ("“", "”"):
        return struct.pack("<H", 0x81 if character == "“" else 0x82)
    if 0x2000 <= value <= 0x206F:
        return struct.pack("<H", value)
    if 0xAC00 <= value <= 0xD7A3:
        encoded = character.encode("johab")
        return encoded[::-1]
    encoded = character.encode("euc_kr")
    if len(encoded) == 2 and 0xCA <= encoded[0] <= 0xFD:
        return struct.pack("<H", 0x4000 + (encoded[0] - 0xCA) * 94 + encoded[1] - 0xA1)
    raise ValueError("이 글자는 HWP 3.0 원본 문자 코드로 보존 저장할 수 없습니다.")


class _Reader:
    def __init__(self, data, page):
        self.data, self.position, self.page = data, 0, page
        self.fonts, self.parts, self.records, self.pictures = [], [], {}, []
        self.controls = {}
        self.unsupported = set()

    def take(self, length):
        if length < 0 or self.position + length > len(self.data):
            raise ValueError("HWP 3.0 자료 길이가 파일 범위를 벗어났습니다.")
        value = self.data[self.position:self.position + length]
        self.position += length
        return value

    def word(self):
        return struct.unpack("<H", self.take(2))[0]

    def prefix(self):
        for _ in range(7):
            count = self.word()
            if count > 1024:
                raise ValueError("HWP 3.0 글꼴 개수가 올바르지 않습니다.")
            self.fonts.append([self.take(40).split(b"\0", 1)[0].decode("johab", "replace") for _ in range(count)])
        count = self.word()
        if count > 4096:
            raise ValueError("HWP 3.0 스타일 개수가 올바르지 않습니다.")
        self.take(count * 238)

    def character_style(self, data):
        from .formats import TextStyle
        fonts = tuple(self.fonts[lang][data[2 + lang]] if data[2 + lang] < len(self.fonts[lang]) else "맑은 고딕" for lang in range(7))
        flags, shade = data[26], data[25]
        color = PALETTE[data[24] & 7]
        background = ""
        if shade:
            base = PALETTE[data[23] & 7]
            channels = [int(base[i:i + 2], 16) for i in (1, 3, 5)]
            background = "#" + "".join("%02x" % round(255 + (v - 255) * min(100, shade) / 100) for v in channels)
        return TextStyle(fonts[0], struct.unpack_from("<H", data)[0] * UNIT,
            bold=bool(flags & 2), italic=bool(flags & 1), underline=bool(flags & 4), color=color,
            background=background, superscript=bool(flags & 32), subscript=bool(flags & 64),
            stretch=data[9] or 100, letter_spacing=struct.unpack_from("<b", data, 16)[0],
            language_fonts=fonts, font_type="HFT", language_font_types=("HFT",) * 7, use_font_space=bool(flags & 128),
            language_ratios=tuple(data[9:16]), language_spacing=struct.unpack_from("<7b", data, 16))

    def paragraph_style(self, data, flags):
        from .formats import ParaStyle
        left, right, indent, line, after = struct.unpack_from("<HHhHH", data)
        return ParaStyle(align={0: "LEFT", 1: "RIGHT", 2: "CENTER", 3: "JUSTIFY", 4: "DISTRIBUTE", 5: "DISTRIBUTE_SPACE"}.get(data[11], "LEFT"),
            left=left * UNIT, right=right * UNIT, indent=indent * UNIT, after=after * UNIT,
            before=struct.unpack_from("<H", data, 183)[0] * UNIT,
            line=(line & 0x7FFF) * UNIT if line & 0x8000 else line,
            line_type="FIXED" if line & 0x8000 else "PERCENT", page_break=bool(flags & 2), widow_orphan=bool(flags & 64))

    def position_for(self, data):
        from .geometry import ObjectPosition
        x, y = struct.unpack_from("<hh", data, 10)
        actual_x, actual_y = struct.unpack_from("<HH", data, 62)
        padding = tuple(value * UNIT for value in struct.unpack_from("<4H", data, 18))
        return ObjectPosition(inline=data[8] == 0,
            horz_rel=("CHAR", "PARA", "PAGE", "PAPER")[min(data[8], 3)],
            vert_rel=("CHAR", "PARA", "PAGE", "PAPER")[min(data[8], 3)],
            horz_align={-1: "LEFT", -2: "RIGHT", -3: "CENTER"}.get(x, "LEFT"),
            vert_align={-1: "TOP", -2: "BOTTOM", -3: "CENTER"}.get(y, "TOP"),
            x_pt=(x if x >= 0 else actual_x) * UNIT, y_pt=(y if y >= 0 else actual_y) * UNIT,
            wrap=("TOP_AND_BOTTOM", "IN_FRONT_OF_TEXT", "SQUARE")[min(data[9], 2)], out_margin=padding)

    def paragraph_list(self, scope=None, depth=0):
        from .formats import Paragraph, Run
        if depth > 64:
            raise ValueError("HWP 3.0 문단 중첩 깊이가 허용 범위를 초과했습니다.")
        scope = dict(scope or {"kind": "body", "section_index": 0})
        blocks, previous = [], bytes(187)
        while True:
            begin = self.position
            header = self.take(43)
            count, lines = struct.unpack_from("<HH", header, 1)
            if count == 0:
                return blocks
            if not header[0]:
                previous = self.take(187)
            line_start = self.position
            line_data = self.take(lines * 14)
            default = self.character_style(header[12:43])
            styles, current = [], default
            if header[5]:
                for _ in range(count):
                    if self.take(1) != b"\x01":
                        current = self.character_style(self.take(31))
                    styles.append(current)
            else:
                styles = [default] * count
            paragraph = Paragraph(style=self.paragraph_style(previous, header[6]), default_style=default,
                end_style=styles[-1], source_index=begin, source_id=begin, scope=dict(scope))
            paragraph.scope["legacy_line_hints"] = [dict(text_start=struct.unpack_from("<H", line_data, i)[0],
                height_pt=struct.unpack_from("<H", line_data, i + 4)[0] * UNIT,
                boundary_flags=struct.unpack_from("<H", line_data, i + 12)[0]) for i in range(0, len(line_data), 14)]
            self.records[begin] = {"header": begin, "tokens": [], "lines": (line_start, lines), "count": count}
            blocks.append(paragraph)
            cache, logical = [], 0
            while logical < count:
                start = self.position
                code = self.word()
                style = styles[logical]
                if code >= 32:
                    character = decode_hchar(code)
                    source_start = len(cache)
                    if paragraph.runs and paragraph.runs[-1].style == style and paragraph.runs[-1].image is None and paragraph.runs[-1].field is None and paragraph.runs[-1].source_start + len(paragraph.runs[-1].text) == source_start:
                        paragraph.runs[-1].text += character
                    else:
                        paragraph.runs.append(Run(character, style, source_start=source_start))
                    self.records[begin]["tokens"].append((character, start, code))
                    cache.extend(character)
                    if character == "\u25a1":
                        self.unsupported.add("character_mapping")
                    logical += 1
                    continue
                self.controls[code] = self.controls.get(code, 0) + 1
                if code in (13, 30, 31):
                    if code != 13:
                        character = "\u00a0" if code == 30 else "\u3000"
                        paragraph.runs.append(Run(character, style, source_start=len(cache)))
                        self.records[begin]["tokens"].append((character, start, code))
                        cache.append(character)
                    logical += 1
                elif code in (0, 1, 2, 3, 4, 5, 6, 12, 27, 29):
                    size = struct.unpack("<I", self.take(4))[0]
                    if self.word() != code:
                        raise ValueError("HWP 3.0 특수 문자 식별 정보가 일치하지 않습니다.")
                    self.take(size)
                    logical += 4
                    cache.extend("\u200b" * 4)
                    self.unsupported.add("advanced_fields")
                elif code in FIXED_CONTROLS:
                    length = FIXED_CONTROLS[code]
                    tail = self.take(length - 2)
                    if struct.unpack_from("<H", tail, len(tail) - 2)[0] != code:
                        raise ValueError("HWP 3.0 특수 문자 끝 식별 정보가 일치하지 않습니다.")
                    if code == 9:
                        paragraph.runs.append(Run("\t", style, source_start=len(cache)))
                        self.records[begin]["tokens"].append(("\t", start, code))
                        cache.extend("\t" + "\u200b" * 3)
                    elif code in (18, 19):
                        kind, number = struct.unpack_from("<HH", tail)
                        paragraph.runs.append(Run("", style, source_start=len(cache), field={"kind": "auto_number" if code == 18 else "new_number",
                            "number_type": ("PAGE", "FOOTNOTE", "ENDNOTE", "PICTURE", "TABLE", "EQUATION")[min(kind, 5)], "number": number}))
                        cache.extend("\u200b" * (length // 2))
                    else:
                        cache.extend("\u200b" * (length // 2))
                        if code not in (20, 21, 25):
                            self.unsupported.add("advanced_fields")
                    logical += length // 2
                elif code in (10, 11, 14, 15, 16, 17):
                    self.take(4)
                    if self.word() != code:
                        raise ValueError("HWP 3.0 개체 식별 정보가 일치하지 않습니다.")
                    anchor = len(cache)
                    cache.extend("\u200b" * 4)
                    logical += 4
                    self.object_control(code, paragraph, blocks, anchor, style, depth)
                else:
                    raise ValueError("지원하지 않는 HWP 3.0 특수 문자 코드입니다: %d" % code)
            if logical != count:
                raise ValueError("HWP 3.0 문단의 문자 수가 특수 문자 길이와 일치하지 않습니다.")
            paragraph.cache_text = "".join(cache)

    def object_control(self, code, paragraph, blocks, anchor, style, depth):
        from .formats import Cell, Drawing, HeaderFooter, Paragraph, Run, Table
        from .geometry import Border
        if code == 10:
            data = self.take(84)
            kind, count = struct.unpack_from("<HH", data, 78)
            if count > 10000:
                raise ValueError("HWP 3.0 표 셀 개수가 올바르지 않습니다.")
            cells = [self.take(27) for _ in range(count)]
            width, height = (value * UNIT for value in struct.unpack_from("<HH", data, 42))
            position = self.position_for(data)
            padding = tuple(value * UNIT for value in struct.unpack_from("<4H", data, 34))
            # Cell rectangles provide the exact grid, including merged spans.
            x_points = sorted({v for cell in cells for v in (struct.unpack_from("<H", cell, 4)[0], struct.unpack_from("<H", cell, 4)[0] + struct.unpack_from("<H", cell, 8)[0])})
            y_points = sorted({v for cell in cells for v in (struct.unpack_from("<H", cell, 6)[0], struct.unpack_from("<H", cell, 6)[0] + struct.unpack_from("<H", cell, 10)[0])})
            obj = Table(max(1, len(y_points) - 1), max(1, len(x_points) - 1), width=width, height=height,
                position=position, padding=padding, column_widths=[(b - a) * UNIT for a, b in zip(x_points, x_points[1:])],
                row_heights=[(b - a) * UNIT for a, b in zip(y_points, y_points[1:])],
                source_index=paragraph.source_index, source_para_id=paragraph.source_id, scope=dict(paragraph.scope), anchor_text_start=anchor, anchor_style=style)
            for raw in cells:
                x, y, w, h = struct.unpack_from("<4H", raw, 4)
                row, col = y_points.index(y), x_points.index(x)
                borders = tuple(Border(("NONE", "SOLID", "SOLID", "DOT", "DOUBLE_SLIM")[min(v, 4)], .8 if v == 2 else .4) for v in raw[20:24])
                cell_scope = {"kind": "cell", "width_pt": w * UNIT, "height_pt": h * UNIT, "row": row, "col": col,
                    "section_index": 0, "parent": paragraph.scope}
                content = self.paragraph_list(cell_scope, depth + 1)
                obj.cells.append(Cell(row, col, row_span=y_points.index(y + h) - row, col_span=x_points.index(x + w) - col,
                    blocks=content, width=w * UNIT, height=h * UNIT, padding=padding, borders=borders,
                    vertical_align="CENTER" if raw[19] else "TOP"))
            caption = self.paragraph_list({"kind": "caption", "section_index": 0, "parent": paragraph.scope}, depth + 1)
            if kind == 0:
                blocks.append(obj)
            elif kind in (1, 3):
                blocks.append(Drawing("rect", blocks=[item for cell in obj.cells for item in cell.blocks], width=width, height=height,
                    position=position, padding=padding, source_index=paragraph.source_index, source_para_id=paragraph.source_id,
                    scope=dict(paragraph.scope), anchor_text_start=anchor, anchor_style=style))
            else:
                self.unsupported.add("equations")
                blocks.append(obj)
            blocks.extend(caption)
        elif code == 11:
            data = self.take(348)
            additional = self.take(struct.unpack_from("<I", data)[0])
            caption = self.paragraph_list({"kind": "caption", "section_index": 0, "parent": paragraph.scope}, depth + 1)
            width, height = (value * UNIT for value in struct.unpack_from("<HH", data, 42))
            name = data[83:339].split(b"\0", 1)[0].decode("johab", "replace")
            run = Run("[HWP 3.0 그림]", style, width=width / .75, height=height / .75, source_start=anchor)
            run.picture.position = self.position_for(data)
            if data[74] == 2:
                self.pictures.append((run, name))
            else:
                self.unsupported.add("drawing_objects" if data[74] == 3 else "linked_or_ole_pictures")
            paragraph.runs.append(run)
            blocks.extend(caption)
        elif code == 14:
            data = self.take(84)
            points = tuple((value * UNIT) for value in struct.unpack_from("<4H", data, 70))
            blocks.append(Drawing("line", width=struct.unpack_from("<H", data, 42)[0] * UNIT,
                height=struct.unpack_from("<H", data, 44)[0] * UNIT, position=self.position_for(data),
                source_index=paragraph.source_index, source_para_id=paragraph.source_id, scope=dict(paragraph.scope),
                geometry={"points": [[points[0], points[1]], [points[2], points[3]]]}))
        elif code in (15, 16, 17):
            data = self.take({15: 8, 16: 10, 17: 14}[code])
            kind = "header" if code == 16 and not data[8] else "footer" if code == 16 else "footnote" if code == 17 else "comment"
            content = self.paragraph_list({"kind": kind, "section_index": 0, "parent": paragraph.scope}, depth + 1)
            if code == 16:
                self.parts.append(HeaderFooter(kind, content, page_type=("BOTH", "EVEN", "ODD")[min(data[9], 2)],
                    width=self.page.display_size[0] - self.page.left_pt - self.page.right_pt,
                    source_index=paragraph.source_index, source_para_id=paragraph.source_id, scope=dict(paragraph.scope)))
            else:
                blocks.extend(content)
                self.unsupported.add("notes")

    def additional_images(self):
        images = {}
        while self.position + 8 <= len(self.data):
            ident, size = struct.unpack("<II", self.take(8))
            if ident == 0 and size == 0:
                break
            payload = self.take(size)
            if ident == 1 and size >= 32:
                name = payload[:16].split(b"\0", 1)[0].decode("ascii", "replace")
                kind = payload[16:32].split(b"\0", 1)[0].decode("ascii", "replace").lower()
                if not kind:
                    kind = "gif" if payload[32:38] in (b"GIF87a", b"GIF89a") else "jpg" if payload[32:35] == b"\xff\xd8\xff" else "png" if payload[32:40] == b"\x89PNG\r\n\x1a\n" else "bmp"
                images[name] = (kind, payload[32:])
        for run, name in self.pictures:
            image = images.get(name) or images.get(name.rsplit("\\", 1)[-1])
            if image:
                run.image_ext, run.image = image
                run.text = ""
            else:
                self.unsupported.add("missing_embedded_picture")


def load_hwp3(path):
    from .formats import LoadedDocument, _wrap_html, model_to_data
    from .geometry import DocumentModel, PageLayout
    from .cfb import MAX_BYTES
    data = path.read_bytes()
    if len(data) < 1166 or not data.startswith(SIGNATURE):
        raise ValueError("HWP 3.0 고정 헤더가 불완전합니다.")
    info = data[30:158]
    if struct.unpack_from("<H", info, 96)[0]:
        raise ValueError("암호가 있는 HWP 3.0 문서는 문서 암호가 필요합니다.")
    beginning = 1166 + struct.unpack_from("<H", info, 126)[0]
    compressed = bool(info[124])
    tail = b""
    if compressed:
        inflater = zlib.decompressobj(-15)
        body = inflater.decompress(data[beginning:], MAX_BYTES + 1)
        if len(body) > MAX_BYTES or inflater.unconsumed_tail or not inflater.eof:
            raise ValueError("HWP 3.0 압축 본문이 불완전하거나 지원 크기를 초과했습니다.")
        trailer = inflater.unused_data
        if len(trailer) < 8:
            raise ValueError("HWP 3.0 압축 본문의 검사합이 없습니다.")
        crc, length = struct.unpack_from("<II", trailer)
        if crc != zlib.crc32(body) or length != len(body) & 0xFFFFFFFF:
            raise ValueError("HWP 3.0 압축 본문의 검사합 또는 길이가 일치하지 않습니다.")
        tail = trailer[8:]
    else:
        body = data[beginning:]
    values = struct.unpack_from("<9H", info, 6)
    page = PageLayout(width_pt=values[1] * UNIT, height_pt=values[0] * UNIT,
        top_pt=values[2] * UNIT, bottom_pt=values[3] * UNIT, left_pt=values[4] * UNIT,
        right_pt=values[5] * UNIT, header_pt=values[6] * UNIT, footer_pt=values[7] * UNIT, gutter_pt=values[8] * UNIT,
        landscape=bool(info[5]))
    reader = _Reader(body, page)
    reader.prefix()
    blocks = reader.paragraph_list()
    reader.additional_images()
    model = DocumentModel(blocks, page, [page], reader.parts)
    from .hwp_native import _paragraphs
    model._hwp_source = {"data": data, "baseline": model_to_data(model), "format_version": "3.0",
        "body": body, "body_begin": beginning, "tail": tail, "compressed": compressed,
        "records": reader.records, "paragraphs": [("HWP3/Body", p.source_index) for p in _paragraphs(model)],
        "legacy_protected": bool(struct.unpack_from("<I", info, 24)[0])}
    warnings = ["HWP 3.0 본문·표·글상자와 원본 문자·문단 서식을 읽었습니다. 구형 줄 위치 캐시가 없어 줄바꿈·쪽 나눔은 다시 계산합니다."]
    if reader.unsupported:
        warnings.append("HWP 3.0의 일부 개체·문자는 표시 기능이 완전하지 않습니다: " + ", ".join(sorted(reader.unsupported)))
    metadata = {"version": "3.0", "legacy": True, "compressed": compressed, "sections": 1,
        "page": page.__dict__.copy(), "unsupported_features": sorted(reader.unsupported),
        "legacy_controls": reader.controls, "paragraphs": len(reader.records)}
    if model._hwp_source["legacy_protected"]:
        metadata.update(read_only=True, read_only_reason="legacy_editing_protection")
        model._hwp_source["read_only_reason"] = "legacy_editing_protection"
    return LoadedDocument(_wrap_html(blocks), warnings, metadata, str(path), "hwp", model)


def patch_hwp3_source(model, path):
    """Patch ordinary text in HWP3 while retaining opaque controls/styles."""
    from .formats import Run, _atomic_write, _deflate, model_to_data
    from .hwp_native import _baseline_paragraphs, _json, _migrate_baseline, _paragraphs
    source = model._hwp_source
    migrated_baseline = _migrate_baseline(source["baseline"])
    current = model_to_data(model)
    if _json(current) == migrated_baseline:
        _atomic_write(path, source["data"])
        return True
    if source.get("legacy_protected"):
        model._hwp_preservation_error = "편집 보호가 있는 HWP 3.0 문서입니다."
        return False
    old, new = _baseline_paragraphs(migrated_baseline), _paragraphs(model)
    empty_paragraphs = set()
    for before, after in zip(old, new):
        if not before.runs and after.runs:
            expected = Run(after.runs[0].text, before.default_style, source_start=0)
            if len(after.runs) != 1 or after.runs[0] != expected:
                model._hwp_preservation_error = "빈 HWP 3.0 문단의 원본 글자 모양을 변경할 수 없습니다."
                return False
            empty_paragraphs.add(before.source_index)
    normalized = _json(current)
    baseline = migrated_baseline
    def without_text(value):
        if isinstance(value, dict):
            if value.get("type") == "paragraph":
                value.pop("cache_text", None)
                value.pop("line_segments", None)
                if value.get("source_index") in empty_paragraphs:
                    value["runs"] = []
                for run in value.get("runs", []):
                    run["text"] = ""
                    run.pop("source_start", None)
            for item in value.values():
                without_text(item)
        elif isinstance(value, list):
            for item in value:
                without_text(item)
    without_text(normalized)
    without_text(baseline)
    if normalized != baseline or len(old) != len(new):
        model._hwp_preservation_error = "HWP 3.0의 구조·서식 변경은 원본 보존 저장으로 처리할 수 없습니다."
        return False
    patches = []
    body = source["body"]
    try:
        for before, after in zip(old, new):
            old_text, new_text = "".join(r.text for r in before.runs), "".join(r.text for r in after.runs)
            if old_text == new_text:
                continue
            if any(r.image is not None or r.field for r in after.runs):
                raise ValueError("HWP 3.0 개체·자동 번호 문단의 직접 편집은 지원하지 않습니다.")
            record = source["records"][before.source_index]
            tokens = record["tokens"]
            if len(tokens) != len(old_text) or not tokens and record["count"] != 1:
                raise ValueError("이 HWP 3.0 문단의 원본 문자 위치를 확인할 수 없습니다.")
            logical_indices = []
            for run in before.runs:
                logical = run.source_start
                for character in run.text:
                    if logical is None:
                        raise ValueError("HWP 3.0 원본 글자 모양 위치가 없습니다.")
                    logical_indices.append(logical)
                    logical += 4 if character == "\t" else 1
            if len(logical_indices) != len(tokens):
                raise ValueError("HWP 3.0 표시 문자와 원본 문자 위치가 일치하지 않습니다.")
            header_start = record["header"]
            header = bytearray(body[header_start:header_start + 43])
            line_start, line_count = record["lines"]
            shape_start = line_start + line_count * 14
            shape_end = shape_start
            raw_styles, current_style = [], bytes(header[12:43])
            for _ in range(record["count"]):
                if header[5]:
                    flag = body[shape_end]
                    shape_end += 1
                    if flag != 1:
                        current_style = body[shape_end:shape_end + 31]
                        shape_end += 31
                raw_styles.append(current_style)
            if not tokens and body[shape_end:shape_end + 2] != b"\r\0":
                raise ValueError("빈 HWP 3.0 문단에 원본 제어문자가 있어 편집할 수 없습니다.")
            edits = []
            for operation, start, end, new_start, new_end in SequenceMatcher(None, old_text, new_text, autojunk=False).get_opcodes():
                if operation == "equal":
                    continue
                if tokens:
                    first_position = tokens[start][1] if start < len(tokens) else tokens[-1][1] + (8 if tokens[-1][2] == 9 else 2)
                    native_start = logical_indices[start] if start < len(tokens) else logical_indices[-1] + (4 if tokens[-1][2] == 9 else 1)
                else:
                    first_position, native_start = shape_end, 0
                last_position = tokens[end - 1][1] + (8 if tokens[end - 1][2] == 9 else 2) if end > start else first_position
                native_end = logical_indices[end - 1] + (4 if tokens[end - 1][2] == 9 else 1) if end > start else native_start
                if end > start and sum(8 if token[2] == 9 else 2 for token in tokens[start:end]) != last_position - first_position:
                    raise ValueError("HWP 3.0 개체·필드 제어문자를 가로지르는 편집은 처리할 수 없습니다.")
                encoded, new_units = bytearray(), 0
                for character in new_text[new_start:new_end]:
                    if character == "\t":
                        encoded.extend(struct.pack("<4H", 9, 0, 0, 9))
                        new_units += 4
                    elif character in ("\n", "\r"):
                        raise ValueError("HWP 3.0 문단 구조를 바꾸는 편집은 지원하지 않습니다.")
                    else:
                        encoded.extend(encode_hchar(character))
                        new_units += 1
                template = raw_styles[min(native_start, len(raw_styles) - 1)] if tokens else bytes(header[12:43])
                edits.append((native_start, native_end, new_units, template))
                patches.append((first_position, last_position, bytes(encoded)))
            updated_styles = list(raw_styles)
            for start, end, units, template in reversed(edits):
                updated_styles[start:end] = [template] * units
            if not 1 <= len(updated_styles) <= 65535:
                raise ValueError("HWP 3.0 문단의 문자 수가 허용 범위를 벗어났습니다.")
            header[5] = 1
            struct.pack_into("<H", header, 1, len(updated_styles))
            mask = struct.unpack_from("<I", header, 7)[0]
            if "\t" in new_text:
                mask |= 1 << 9
            else:
                mask &= ~(1 << 9)
            struct.pack_into("<I", header, 7, mask)
            shape_data, previous = bytearray(), None
            for raw in updated_styles:
                shape_data.extend(b"\x01" if raw == previous else b"\x00" + raw)
                previous = raw
            patches.extend(((header_start, header_start + 43, bytes(header)), (shape_start, shape_end, bytes(shape_data))))
            line_data = bytearray(body[line_start:line_start + line_count * 14])
            for i in range(0, len(line_data), 14):
                old_position = struct.unpack_from("<H", line_data, i)[0]
                delta, mapped = 0, None
                for start, end, units, _ in edits:
                    if old_position < start:
                        break
                    if start == end == old_position:
                        # A line beginning at the insertion point also contains
                        # the new text; especially the first line must start at 0.
                        mapped = start + delta
                        break
                    if old_position < end:
                        mapped = start + delta + min(old_position - start, units)
                        break
                    delta += units - (end - start)
                new_position = old_position + delta if mapped is None else mapped
                struct.pack_into("<H", line_data, i, min(new_position, len(updated_styles) - 1))
            patches.append((line_start, line_start + line_count * 14, bytes(line_data)))
        patches.sort()
        if any(first[1] > second[0] for first, second in zip(patches, patches[1:])):
            raise ValueError("HWP 3.0 원본 편집 영역이 겹칩니다.")
    except (ValueError, UnicodeError, struct.error, IndexError) as error:
        model._hwp_preservation_error = str(error)
        return False
    updated = bytearray(body)
    for start, end, replacement in reversed(patches):
        updated[start:end] = replacement
    prefix = bytearray(source["data"][:source["body_begin"]])
    # The documented external-converter bit asks native readers to realign
    # changed text; opaque section/page/object data remains intact.
    converter_flag = struct.unpack_from("<H", prefix, 30 + 28)[0]
    struct.pack_into("<H", prefix, 30 + 28, converter_flag | 1)
    packed = _deflate(bytes(updated)) + struct.pack("<II", zlib.crc32(updated), len(updated) & 0xFFFFFFFF) if source["compressed"] else bytes(updated)
    data = bytes(prefix) + packed + source["tail"]
    _atomic_write(path, data)
    return True
