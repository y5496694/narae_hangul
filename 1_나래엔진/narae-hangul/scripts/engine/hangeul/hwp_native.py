"""Patch editable text and styles in the original binary HWP 5 container.

Unsupported records are retained as bytes. Edits that cannot be represented
without changing their structure use the ordinary export writer instead.
"""
from __future__ import annotations

from difflib import SequenceMatcher
from bisect import bisect_right
import math
import re
import struct

from .cfb import CompoundFile, write_compound


_EXTENDED = {1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 12, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23}


def _paragraphs(model):
    from .formats import Paragraph, _walk_blocks
    blocks = list(model.blocks)
    for part in model.header_footers:
        blocks.extend(part.blocks)
    return [block for block in _walk_blocks(blocks) if isinstance(block, Paragraph)]


def _json(value):
    # Normalize serialized tuples without copying large immutable image/base64
    # strings through a second complete JSON buffer.
    if isinstance(value, dict):
        return {key: _json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json(item) for item in value]
    return value


def _migrate_baseline(data):
    """Fill additive model defaults in snapshots written by older releases."""
    from dataclasses import asdict
    from .formats import TextStyle
    data = _json(data)
    defaults = _json(asdict(TextStyle()))
    def visit(value):
        if isinstance(value, dict):
            if value.get("type") == "paragraph":
                for key in ("default_style", "end_style"):
                    if isinstance(value.get(key), dict):
                        value[key] = {**defaults, **value[key]}
                for run in value.get("runs", []):
                    run["style"] = {**defaults, **run.get("style", {})}
            elif value.get("type") == "drawing":
                value.setdefault("geometry", {})
                if isinstance(value.get("anchor_style"), dict):
                    value["anchor_style"] = {**defaults, **value["anchor_style"]}
            elif value.get("type") == "table" and isinstance(value.get("anchor_style"), dict):
                value["anchor_style"] = {**defaults, **value["anchor_style"]}
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    for part in data.get("header_footers", []):
        part.setdefault("scope", {})
    visit(data)
    return data


def _baseline_paragraphs(data):
    """Read paragraph metadata without decoding every embedded image again."""
    from .formats import _block_from_data
    result = []
    def visit(blocks):
        for block in blocks:
            if block.get("type") == "paragraph":
                paragraph = {**block, "runs": [{**run, "image": None} for run in block.get("runs", [])]}
                result.append(_block_from_data(paragraph))
            elif block.get("type") == "table":
                for cell in block.get("cells", []):
                    visit(cell.get("blocks", []))
            else:
                visit(block.get("blocks", []))
    visit(data.get("blocks", []))
    for part in data.get("header_footers", []):
        visit(part.get("blocks", []))
    return result


def _structure(data):
    """Compare objects and structure, allowing plain-text/style edits."""
    data = _json(data)
    def visit(value):
        if isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, dict):
            if value.get("type") == "paragraph":
                value.pop("cache_text", None)
                value.pop("line_segments", None)
                for key in ("style", "default_style", "end_style"):
                    value.pop(key, None)
                runs = []
                for run in value.get("runs", []):
                    run.pop("source_start", None)
                    if run.get("image") is not None or run.get("field") is not None:
                        runs.append(run)
                    elif run.get("text"):
                        run["text"] = ""
                        run.pop("style", None)
                        if not runs or runs[-1] != run:
                            runs.append(run)
                if not runs:
                    from .formats import Run
                    from dataclasses import asdict
                    run = asdict(Run())
                    run.pop("style", None)
                    run.pop("source_start", None)
                    runs = [_json(run)]
                value["runs"] = runs
            for item in value.values():
                visit(item)
    visit(data)
    return data


class _DocInfoEditor:
    """Append cloned style records; never renumber existing references."""
    def __init__(self, raw, source):
        self.raw = raw
        self.records, self.trailing = _record_slices(raw)
        self.mapping = next((i for i, r in enumerate(self.records) if r[0] == 17), None)
        if self.mapping is None or len(self.records[self.mapping][2]) < 56:
            raise ValueError("원본 HWP ID 매핑을 확인할 수 없습니다.")
        self.counts = list(struct.unpack_from("<14I", self.records[self.mapping][2]))
        self.chars = [r[2] for r in self.records if r[0] == 21]
        self.paras = [r[2] for r in self.records if r[0] == 25]
        self.original_char_count, self.original_para_count = len(self.chars), len(self.paras)
        faces = [r[2] for r in self.records if r[0] == 19]
        if sum(self.counts[1:8]) != len(faces) or self.counts[9] != len(self.chars) or self.counts[13] != len(self.paras):
            raise ValueError("원본 HWP 글꼴·서식 ID 개수가 일치하지 않습니다.")
        self.fonts, self.added_fonts, offset = [], [[] for _ in range(7)], 0
        for count in self.counts[1:8]:
            language = []
            for data in faces[offset:offset + count]:
                if len(data) < 3:
                    raise ValueError("원본 HWP 글꼴 이름이 손상되었습니다.")
                length = struct.unpack_from("<H", data, 1)[0]
                name = data[3:3 + length * 2].decode("utf-16le")
                language.append((name, ("UNKNOWN", "TTF", "HFT", "UNKNOWN_3")[data[0] & 3]))
            self.fonts.append(language)
            offset += count
        self.char_styles = source.get("char_styles", {})
        self.unmodeled_character_fields = set()
        def missing_flags(value):
            if isinstance(value, dict):
                if "font" in value and "bold" in value:
                    self.unmodeled_character_fields.update(key for key in ("use_font_space", "use_kerning") if key not in value)
                for child in value.values():
                    missing_flags(child)
            elif isinstance(value, (list, tuple)):
                for child in value:
                    missing_flags(child)
        missing_flags(source.get("baseline", {}))
        self.char_cache, self.para_cache = {}, {}

    @staticmethod
    def _color(value):
        from .formats import _rgb
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            raise ValueError("HWP 글자 색은 RGB 색상이어야 합니다.")
        return _rgb(value)

    def _font(self, language, name, kind):
        from .formats import _wstr
        pair = (name, kind)
        if pair not in self.fonts[language]:
            if kind not in ("UNKNOWN", "TTF", "HFT", "UNKNOWN_3") or not name or len(self.fonts[language]) >= 65536:
                raise ValueError("새 HWP 글꼴을 선언할 수 없습니다.")
            self.added_fonts[language].append(bytes([("UNKNOWN", "TTF", "HFT", "UNKNOWN_3").index(kind)]) + _wstr(name))
            self.fonts[language].append(pair)
        return self.fonts[language].index(pair)

    def character(self, ident, old, new):
        if old == new:
            return ident
        key = (ident, old, new)
        if key in self.char_cache:
            return self.char_cache[key]
        if ident >= self.original_char_count or len(self.chars[ident]) < 66:
            raise ValueError("원본 HWP 글자 모양을 확인할 수 없습니다.")
        data = bytearray(self.chars[ident])
        flags, = struct.unpack_from("<I", data, 46)
        for attribute, mask, enabled in (("italic", 1, 1), ("bold", 2, 2), ("underline", 12, 4),
            ("strike", 7 << 18, 1 << 18), ("superscript", 1 << 15, 1 << 15), ("subscript", 1 << 16, 1 << 16),
            ("use_font_space", 1 << 25, 1 << 25), ("use_kerning", 1 << 30, 1 << 30)):
            if attribute in self.unmodeled_character_fields:
                continue  # old sessions never exposed these native flags
            if getattr(old, attribute) != getattr(new, attribute):
                flags = (flags & ~mask) | (enabled if getattr(new, attribute) else 0)
        struct.pack_into("<I", data, 46, flags)
        if old.size != new.size:
            if not math.isfinite(new.size) or not 0 < new.size <= 4096:
                raise ValueError("HWP 글자 크기가 허용 범위를 벗어났습니다.")
            struct.pack_into("<i", data, 42, round(new.size * 100))
        if old.stretch != new.stretch and (not new.language_ratios or new.language_ratios == old.language_ratios):
            if not 1 <= new.stretch <= 255:
                raise ValueError("HWP 장평이 허용 범위를 벗어났습니다.")
            data[14:21] = bytes([new.stretch] * 7)
        if old.letter_spacing != new.letter_spacing and (not new.language_spacing or new.language_spacing == old.language_spacing):
            if not -128 <= new.letter_spacing <= 127:
                raise ValueError("HWP 자간이 허용 범위를 벗어났습니다.")
            data[21:28] = struct.pack("<7b", *([new.letter_spacing] * 7))
        for attribute, position, signed in (("language_ratios", 14, False), ("language_spacing", 21, True),
            ("language_relative_sizes", 28, False), ("language_offsets", 35, True)):
            values = getattr(new, attribute)
            if len(values) not in (0, 7):
                raise ValueError("HWP 언어별 글자 서식은 7개 언어 값을 가져야 합니다.")
            # An empty tuple denotes unmodeled native data in older sessions.
            if values and values != getattr(old, attribute):
                if any(not isinstance(value, int) or not (-128 <= value <= 127 if signed else 0 <= value <= 255) for value in values):
                    raise ValueError("HWP 언어별 글자 서식 값이 허용 범위를 벗어났습니다.")
                data[position:position + 7] = struct.pack("<7b", *values) if signed else bytes(values)
        for attribute, position in (("color", 52), ("background", 60)):
            if getattr(old, attribute) != getattr(new, attribute):
                value = getattr(new, attribute)
                struct.pack_into("<I", data, position, 0xFFFFFFFF if attribute == "background" and not value else self._color(value))
        names = new.language_fonts if len(new.language_fonts) == 7 else (new.font,) * 7
        kinds = new.language_font_types if len(new.language_font_types) == 7 else (new.font_type,) * 7
        # A toolbar family change applies to every script unless script-specific
        # families were changed explicitly at the same time.
        if new.font != old.font and new.language_fonts == old.language_fonts:
            names = (new.font,) * 7
        if new.font_type != old.font_type and new.language_font_types == old.language_font_types:
            kinds = (new.font_type,) * 7
        old_names = old.language_fonts if len(old.language_fonts) == 7 else (old.font,) * 7
        old_kinds = old.language_font_types if len(old.language_font_types) == 7 else (old.font_type,) * 7
        for language, (name, kind, old_name, old_kind) in enumerate(zip(names, kinds, old_names, old_kinds)):
            if (name, kind) != (old_name, old_kind):
                struct.pack_into("<H", data, language * 2, self._font(language, name, kind))
        raw = bytes(data)
        if raw in self.chars:
            result = self.chars.index(raw)
        else:
            result = len(self.chars)
            self.chars.append(raw)
        self.char_cache[key] = result
        return result

    def paragraph(self, ident, old, new):
        if old == new:
            return ident
        key = (ident, old, new)
        if key in self.para_cache:
            return self.para_cache[key]
        if ident >= self.original_para_count or len(self.paras[ident]) < 28:
            raise ValueError("원본 HWP 문단 모양을 확인할 수 없습니다.")
        if old.background != new.background or old.borders != new.borders:
            raise ValueError("HWP 문단 테두리·배경 변경은 원본 보존 저장으로 처리할 수 없습니다.")
        data = bytearray(self.paras[ident])
        flags, = struct.unpack_from("<I", data)
        for attribute, shift, mask, options in (("align", 2, 7, ("JUSTIFY", "LEFT", "RIGHT", "CENTER", "DISTRIBUTE", "DISTRIBUTE_SPACE")),
            ("break_latin", 5, 3, ("KEEP_WORD", "HYPHENATION", "BREAK_WORD")), ("break_nonlatin", 7, 1, ("BREAK_WORD", "KEEP_WORD")),
            ("vertical_align", 20, 3, ("BASELINE", "TOP", "CENTER", "BOTTOM"))):
            if getattr(old, attribute) != getattr(new, attribute):
                if getattr(new, attribute) not in options:
                    raise ValueError("지원하지 않는 HWP 문단 서식 값입니다.")
                flags = (flags & ~(mask << shift)) | (options.index(getattr(new, attribute)) << shift)
        for attribute, shift in (("snap_to_grid", 8), ("widow_orphan", 16), ("keep_with_next", 17), ("keep_lines", 18),
            ("page_break", 19), ("font_line_height", 22), ("border_connect", 28), ("border_ignore_margin", 29)):
            if getattr(old, attribute) != getattr(new, attribute):
                flags = (flags & ~(1 << shift)) | (int(getattr(new, attribute)) << shift)
        if old.condense != new.condense:
            if not 0 <= new.condense <= 127:
                raise ValueError("HWP 공백 최소값이 허용 범위를 벗어났습니다.")
            flags = (flags & ~(127 << 9)) | (new.condense << 9)
        for attribute, position in (("left", 4), ("right", 8), ("indent", 12), ("before", 16), ("after", 20)):
            if getattr(old, attribute) != getattr(new, attribute):
                struct.pack_into("<i", data, position, round(getattr(new, attribute) * 200))
        if old.line != new.line or old.line_type != new.line_type:
            options = ("PERCENT", "FIXED", "BETWEEN_LINES", "AT_LEAST")
            if new.line_type not in options or not math.isfinite(new.line) or new.line < 0:
                raise ValueError("HWP 줄 간격이 허용 범위를 벗어났습니다.")
            kind = options.index(new.line_type)
            flags = (flags & ~3) | kind
            line = round(new.line if kind == 0 else new.line * 100)
            struct.pack_into("<i", data, 24, line)
            if len(data) >= 54:
                prop3, = struct.unpack_from("<I", data, 46)
                struct.pack_into("<Ii", data, 46, (prop3 & ~3) | kind, line)
        if any(getattr(old, a) != getattr(new, a) for a in ("line_wrap", "auto_spacing_eng", "auto_spacing_num")):
            if len(data) < 46 or new.line_wrap not in ("BREAK", "SQUEEZE", "KEEP"):
                raise ValueError("이 HWP 버전의 문단 자동 간격은 변경할 수 없습니다.")
            prop2, = struct.unpack_from("<I", data, 42)
            for attribute, mask, value in (("line_wrap", 3, ("BREAK", "SQUEEZE", "KEEP").index(new.line_wrap)),
                ("auto_spacing_eng", 16, int(new.auto_spacing_eng) << 4), ("auto_spacing_num", 32, int(new.auto_spacing_num) << 5)):
                if getattr(old, attribute) != getattr(new, attribute):
                    prop2 = (prop2 & ~mask) | value
            struct.pack_into("<I", data, 42, prop2)
        if old.border_offsets != new.border_offsets:
            if len(data) < 42 or len(new.border_offsets) != 4:
                raise ValueError("HWP 문단 테두리 간격을 변경할 수 없습니다.")
            struct.pack_into("<4h", data, 34, *(round(v * 100) for v in new.border_offsets))
        struct.pack_into("<I", data, 0, flags)
        raw = bytes(data)
        if raw in self.paras:
            result = self.paras.index(raw)
        else:
            result = len(self.paras)
            if result >= 65536:
                raise ValueError("HWP 문단 모양 ID가 허용 범위를 벗어났습니다.")
            self.paras.append(raw)
        self.para_cache[key] = result
        return result

    def bytes(self):
        from .formats import _record
        if not any(self.added_fonts) and len(self.chars) == self.original_char_count and len(self.paras) == self.original_para_count:
            return self.raw
        mapping = bytearray(self.records[self.mapping][2])
        for language in range(7):
            struct.pack_into("<I", mapping, 4 + language * 4, len(self.fonts[language]))
        struct.pack_into("<I", mapping, 36, len(self.chars))
        struct.pack_into("<I", mapping, 52, len(self.paras))
        face_indices = [i for i, r in enumerate(self.records) if r[0] == 19]
        font_after, font_before, offset = {}, {}, 0
        for language, count in enumerate(self.counts[1:8]):
            addition = b"".join(_record(19, 1, d) for d in self.added_fonts[language])
            if addition:
                if count or offset == len(face_indices):
                    target = face_indices[offset + count - 1] if face_indices else self.mapping
                    font_after[target] = font_after.get(target, b"") + addition
                else:
                    target = face_indices[offset]
                    font_before[target] = font_before.get(target, b"") + addition
            offset += count
        last_char = max(i for i, r in enumerate(self.records) if r[0] == 21)
        last_para = max(i for i, r in enumerate(self.records) if r[0] == 25)
        result = bytearray()
        for i, (tag, level, data, start, end) in enumerate(self.records):
            result.extend(font_before.get(i, b""))
            result.extend(_record(tag, level, bytes(mapping)) if i == self.mapping else self.raw[start:end])
            result.extend(font_after.get(i, b""))
            if i == last_char:
                result.extend(b"".join(_record(21, level, d) for d in self.chars[self.original_char_count:]))
            if i == last_para:
                result.extend(b"".join(_record(25, level, d) for d in self.paras[self.original_para_count:]))
        return bytes(result) + self.trailing


def _record_slices(data):
    records, position = [], 0
    while position + 4 <= len(data):
        start = position
        header, = struct.unpack_from("<I", data, position)
        position += 4
        size = header >> 20
        if size == 4095:
            if position + 4 > len(data):
                raise ValueError("HWP 레코드 헤더가 손상되었습니다.")
            size, = struct.unpack_from("<I", data, position)
            position += 4
        end = position + size
        if end > len(data):
            raise ValueError("HWP 레코드 길이가 올바르지 않습니다.")
        records.append((header & 1023, (header >> 10) & 1023, data[position:end], start, end))
        position = end
    return records, data[position:]


def _tokens(data):
    result, position = [], 0
    while position + 2 <= len(data):
        unit, = struct.unpack_from("<H", data, position)
        start = position // 2
        if unit in _EXTENDED:
            length = 16
            char = "\t" if unit == 9 else None
        elif unit < 32:
            length = 2
            char = {10: "\n", 24: "-", 30: "\u00a0", 31: "\u3000"}.get(unit)
        else:
            length = 4 if 0xD800 <= unit <= 0xDBFF and position + 4 <= len(data) else 2
            char = data[position:position + length].decode("utf-16le", "replace")
        if position + length > len(data):
            raise ValueError("HWP 본문 제어문자가 손상되었습니다.")
        if char is not None:
            result.append((char, start, (position + length) // 2))
        position += length
    return result


def _encode_text(text):
    result = bytearray()
    for character in text:
        if character == "\t":
            result.extend(struct.pack("<H", 9) + bytes(12) + struct.pack("<H", 9))
        elif ord(character) < 32 and character != "\n":
            raise ValueError("문단 구조를 바꾸는 제어문자는 원본 보존 저장으로 편집할 수 없습니다.")
        else:
            result.extend(character.encode("utf-16le"))
    return bytes(result)


def _style_at(paragraph, position):
    offset = 0
    for run in paragraph.runs:
        if offset <= position < offset + len(run.text):
            return run.style
        offset += len(run.text)
    return paragraph.end_style or paragraph.default_style


def _patch_paragraph(records, index, old_paragraph, new_paragraph, docinfo):
    from .formats import _record
    tag, level, header, _, _ = records[index]
    if tag != 66 or len(header) < 18:
        raise ValueError("원본 HWP 문단 위치를 확인할 수 없습니다.")
    stop = index + 1
    while stop < len(records) and records[stop][1] > level:
        stop += 1
    children = [i for i in range(index + 1, stop) if records[i][1] == level + 1]
    text_ids = [i for i in children if records[i][0] == 67]
    shape_ids = [i for i in children if records[i][0] == 68]
    native = b"".join(records[i][2] for i in text_ids) or b"\r\0"
    visible = _tokens(native)
    old_text = "".join(token[0] for token in visible)
    shapes = b"".join(records[i][2] for i in shape_ids)
    if len(shapes) % 8:
        raise ValueError("원본 HWP 글자 모양 위치가 손상되었습니다.")
    shape_positions = [struct.unpack_from("<II", shapes, offset) for offset in range(0, len(shapes), 8)]
    shape_positions = sorted(shape_positions) or [(0, 0)]
    if shape_positions[0][0] != 0:
        raise ValueError("원본 HWP 첫 글자 모양 위치가 0이 아닙니다.")
    native_shape_starts = [start for start, _ in shape_positions]

    def shape_at(position):
        return shape_positions[max(0, bisect_right(native_shape_starts, position) - 1)][1]

    new_text = "".join(run.text for run in new_paragraph.runs)
    edits = []
    insertion_bias = {}
    template_ids = []
    for operation, start, end, current_start, current_end in SequenceMatcher(None, old_text, new_text, autojunk=False).get_opcodes():
        if operation == "equal":
            template_ids.extend(shape_at(token[1]) for token in visible[start:end])
            continue
        native_start = visible[start][1] if start < len(visible) else visible[-1][2] if visible else len(native) // 2 - int(native.endswith(b"\r\0"))
        native_end = visible[end - 1][2] if end > start else native_start
        if end > start:
            if sum(token[2] - token[1] for token in visible[start:end]) != native_end - native_start:
                raise ValueError("도형·필드 제어문자를 가로지르는 편집은 원본 보존 저장으로 처리할 수 없습니다.")
        elif any(position == native_start and position > 0 for position, _ in shape_positions):
            inserted_styles = {_style_at(new_paragraph, position) for position in range(current_start, current_end)}
            preceding = _style_at(old_paragraph, start - 1)
            following = _style_at(old_paragraph, start)
            insertion_bias[native_start] = len(inserted_styles) == 1 and next(iter(inserted_styles)) == preceding and preceding != following
        template_position = native_start - 1 if insertion_bias.get(native_start) else native_start
        template_ids.extend([shape_at(template_position)] * (current_end - current_start))
        edits.append((native_start, native_end, _encode_text(new_text[current_start:current_end])))
    updated, cursor = bytearray(), 0
    for start, end, text in edits:
        updated.extend(native[cursor:start * 2])
        updated.extend(text)
        cursor = end * 2
    updated.extend(native[cursor:])
    if "".join(token[0] for token in _tokens(updated)) != new_text:
        raise ValueError("원본 HWP 텍스트 편집 위치가 일치하지 않습니다.")

    def offset(position, after_insert=True):
        if position > len(native) // 2:
            return position  # opaque/sentinel positions are not text offsets
        difference = 0
        for start, end, text in edits:
            length = len(text) // 2
            if position < start:
                break
            if position < end:
                return start + difference + min(position - start, length)
            if start == end == position and not after_insert:
                break
            difference += length - (end - start)
        return position + difference

    shifted = {}
    for position, shape in shape_positions:
        shifted[0 if position == 0 else offset(position, insertion_bias.get(position, True))] = shape
    # Overlay visible character intervals while retaining the source styles of
    # hidden control units, anchors and paragraph-break glyphs.
    old_char_styles = docinfo.char_styles
    new_char_styles = [run.style for run in new_paragraph.runs for _ in run.text]
    overlays = []
    updated_tokens = _tokens(updated)
    if old_paragraph.default_style != new_paragraph.default_style and updated_tokens:
        prefix_end = updated_tokens[0][1]
        if prefix_end:
            default_id = docinfo.character(shape_positions[0][1], old_paragraph.default_style, new_paragraph.default_style)
            overlays.append((0, prefix_end, default_id))
        elif new_char_styles[0] != new_paragraph.default_style:
            raise ValueError("HWP 첫 글자 모양과 문단 기본 글자 모양이 서로 다릅니다.")
    for text_position, (token, template_id) in enumerate(zip(updated_tokens, template_ids)):
        character, start, end = token
        old_style = old_char_styles.get(template_id)
        if old_style is None:
            # Older serialized sessions are loaded again from native bytes.
            old_style = _style_at(old_paragraph, min(text_position, max(0, len(old_text) - 1)))
        target_id = docinfo.character(template_id, old_style, new_char_styles[text_position])
        if overlays and overlays[-1][1] == start and overlays[-1][2] == target_id:
            overlays[-1] = (overlays[-1][0], end, target_id)
        else:
            overlays.append((start, end, target_id))
    original_points = sorted(shifted)
    points = sorted(set(original_points) | {p for start, end, _ in overlays for p in (start, end)})
    overlay_index, original_index, overlaid = 0, 0, {}
    for point in points:
        while original_index + 1 < len(original_points) and original_points[original_index + 1] <= point:
            original_index += 1
        while overlay_index < len(overlays) and overlays[overlay_index][1] <= point:
            overlay_index += 1
        ident = shifted[original_points[original_index]]
        if overlay_index < len(overlays) and overlays[overlay_index][0] <= point < overlays[overlay_index][1]:
            ident = overlays[overlay_index][2]
        overlaid[point] = ident
    shifted = overlaid
    if not visible and not new_text:
        template_id = shape_positions[0][1]
        shifted[0] = docinfo.character(template_id, old_paragraph.default_style, new_paragraph.default_style)
    if new_paragraph.end_style != old_paragraph.end_style and updated.endswith(b"\r\0"):
        end = len(updated) // 2 - 1
        template_id = shape_at(len(native) // 2 - 1)
        shifted[end] = docinfo.character(template_id, old_paragraph.end_style or old_paragraph.default_style,
            new_paragraph.end_style or new_paragraph.default_style)
    # Native paragraph headers require one shape at zero. Keep meaningful
    # native IDs even when they project to the same visible style.
    if 0 not in shifted:
        shifted[0] = shape_positions[0][1]
    compact = {}
    for position, ident in sorted(shifted.items()):
        if not compact or next(reversed(compact.values())) != ident:
            compact[position] = ident
    shifted = compact
    shape_data = b"".join(struct.pack("<II", position, shape) for position, shape in sorted(shifted.items()))
    header = bytearray(header)
    count, mask = struct.unpack_from("<II", header)
    struct.pack_into("<I", header, 0, (count & 0x80000000) | (len(updated) // 2))
    # Keep masks for every opaque control; text editing only changes TAB/LF.
    mask &= ~((1 << 9) | (1 << 10))
    for character, _, _ in _tokens(updated):
        if character in ("\t", "\n"):
            mask |= 1 << ord(character)
    struct.pack_into("<I", header, 4, mask)
    para_id = struct.unpack_from("<H", header, 8)[0]
    struct.pack_into("<H", header, 8, docinfo.paragraph(para_id, old_paragraph.style, new_paragraph.style))
    if old_paragraph.style.page_break != new_paragraph.style.page_break:
        header[11] = (header[11] & ~4) | (4 if new_paragraph.style.page_break else 0)
    struct.pack_into("<H", header, 12, len(shifted))
    struct.pack_into("<H", header, 16, 0)  # invalidate cached line geometry
    replacements = {index: _record(66, level, bytes(header))}
    after = {}
    if text_ids:
        replacements[text_ids[0]] = _record(67, level + 1, bytes(updated))
        replacements.update({i: b"" for i in text_ids[1:]})
    else:
        after[index] = _record(67, level + 1, bytes(updated))
    if shape_ids:
        replacements[shape_ids[0]] = _record(68, level + 1, shape_data)
        replacements.update({i: b"" for i in shape_ids[1:]})
    else:
        after[index] = after.get(index, b"") + _record(68, level + 1, shape_data)
    for i in children:
        child_tag, child_level, data, _, _ = records[i]
        if child_tag == 69:
            replacements[i] = b""
        elif child_tag == 70:
            if len(data) % 12:
                raise ValueError("원본 HWP 범위 태그 위치가 손상되었습니다.")
            ranges = bytearray(data)
            for position in range(0, len(ranges), 12):
                start, end = struct.unpack_from("<II", ranges, position)
                struct.pack_into("<II", ranges, position, offset(start, False), offset(end))
            replacements[i] = _record(child_tag, child_level, bytes(ranges))
    return replacements, after


def patch_hwp_source(model, path):
    """Write original bytes or native text/style patches; False is safe refusal."""
    from .formats import DocumentModel, _atomic_write, _deflate, _inflate, model_to_data
    source = getattr(model, "_hwp_source", None)
    if not isinstance(model, DocumentModel) or not source:
        return False
    baseline = _migrate_baseline(source["baseline"])
    current = model_to_data(model)
    if _json(current) == _json(baseline):
        _atomic_write(path, source["data"])
        return True
    if source.get("read_only"):
        model._hwp_preservation_error = "배포용 HWP는 원본의 편집 제한을 유지하는 읽기 전용 문서입니다."
        return False
    if source.get("format_version") == "3.0":
        from .hwp3 import patch_hwp3_source
        return patch_hwp3_source(model, path)
    if _structure(current) != _structure(baseline):
        model._hwp_preservation_error = "문단·표·개체 구조 또는 개체 서식이 변경되었습니다."
        return False
    original = CompoundFile(source["data"])
    compressed = bool(struct.unpack_from("<I", original.read("FileHeader"), 36)[0] & 1)
    before, after = _baseline_paragraphs(baseline), _paragraphs(model)
    if len(before) != len(after) or len(source["paragraphs"]) != len(before):
        model._hwp_preservation_error = "원본 HWP 문단 구조가 변경되었습니다."
        return False
    sections = {}
    try:
        raw_info = original.read("DocInfo")
        raw_info = _inflate(raw_info) if compressed else raw_info
        docinfo = _DocInfoEditor(raw_info, source)
        for old, new, location in zip(before, after, source["paragraphs"]):
            old_text = "".join(run.text for run in old.runs)
            new_text = "".join(run.text for run in new.runs)
            if (old_text == new_text and old.style == new.style and old.default_style == new.default_style
                and old.end_style == new.end_style and [(r.text, r.style) for r in old.runs] == [(r.text, r.style) for r in new.runs]):
                continue
            if location is None:
                raise ValueError("원본 HWP 제어문자에서 가져온 대체 본문은 직접 편집 저장할 수 없습니다.")
            name, index = location
            if name not in sections:
                raw = original.read(name)
                raw = _inflate(raw) if compressed else raw
                records, trailing = _record_slices(raw)
                sections[name] = (raw, records, trailing, {}, {})
            raw, records, trailing, changes, additions = sections[name]
            replacements, inserted = _patch_paragraph(records, index, old, new, docinfo)
            changes.update(replacements)
            additions.update(inserted)
    except (ValueError, UnicodeError, struct.error, OverflowError) as error:
        model._hwp_preservation_error = str(error)
        return False
    if not sections:
        _atomic_write(path, source["data"])
        return True
    streams = {name: original.read(name) for name in original.streams}
    updated_info = docinfo.bytes()
    if updated_info != raw_info:
        streams["DocInfo"] = _deflate(updated_info) if compressed else updated_info
    for name, (raw, records, trailing, changes, additions) in sections.items():
        body = b"".join(changes.get(i, raw[record[3]:record[4]]) + additions.get(i, b"") for i, record in enumerate(records)) + trailing
        streams[name] = _deflate(body) if compressed else body
    if "PrvText" in streams and any("".join(r.text for r in old.runs) != "".join(r.text for r in new.runs) for old, new in zip(before, after)):
        streams["PrvText"] = "\r\n".join("".join(run.text for run in paragraph.runs) for paragraph in after).encode("utf-16le")
    _atomic_write(path, write_compound(streams, template=original))
    return True
