"""Apply supported edits to native OWPML without rewriting unrelated XML."""
import difflib
import html
import io
import json
import re
import zipfile
from xml.parsers import expat


LANGUAGES = ("HANGUL", "LATIN", "HANJA", "JAPANESE", "OTHER", "SYMBOL", "USER")
LANG_ATTRS = ("hangul", "latin", "hanja", "japanese", "other", "symbol", "user")
PARA_FIELDS = {"align", "left", "right", "indent", "before", "after", "line", "line_type", "page_break"}


def _tag_end(raw, begin):
    # A '>' inside an attribute is legal XML and must not terminate the tag.
    quote = None
    for offset in range(begin, len(raw)):
        char = raw[offset]
        if quote:
            if char == quote:
                quote = None
        elif char in (34, 39):
            quote = char
        elif char == 62:
            return offset + 1
    raise ValueError("XML 태그가 끝나지 않았습니다.")


def _xml_nodes(raw, namespaces=True):
    """Use validated byte offsets; original prefixes and unknown nodes survive."""
    raw.decode("utf-8-sig")  # Other encodings are preserved only for no-op saves.
    parser = expat.ParserCreate(namespace_separator="|" if namespaces else None)
    stack, nodes = [], []
    def start(name, attrs):
        begin = parser.CurrentByteIndex
        content = _tag_end(raw, begin)
        opening = raw[begin:content]
        node = dict(name=name.rsplit("|", 1)[-1].rsplit(":", 1)[-1], attrs=attrs, begin=begin,
                    content=content, empty=opening.rstrip().endswith(b"/>"), children=[],
                    qname=re.match(rb"<([^\s/>]+)", opening).group(1), index=len(nodes))
        if stack:
            stack[-1]["children"].append(node)
            node["parent_index"] = stack[-1]["index"]
        nodes.append(node)
        stack.append(node)
    def end(name):
        node = stack.pop()
        node["close"] = node["content"] if node["empty"] else parser.CurrentByteIndex
        node["end"] = node["content"] if node["empty"] else _tag_end(raw, node["close"])
    def forbidden(*args):
        raise ValueError("외부 엔터티·DTD를 포함한 XML은 수정하지 않습니다.")
    parser.StartElementHandler, parser.EndElementHandler = start, end
    parser.StartDoctypeDeclHandler, parser.EntityDeclHandler = forbidden, forbidden
    parser.Parse(raw, True)
    return nodes


def _patches(raw, patches):
    stop = len(raw)
    for begin, end, replacement in sorted(patches, key=lambda p: (p[0], p[1]), reverse=True):
        if end > stop or begin > end:
            raise ValueError("겹치는 XML 수정 위치입니다.")
        raw = raw[:begin] + replacement + raw[end:]
        stop = begin
    return raw


def _attributes(opening, changes):
    for name, value in changes.items():
        pattern = rb"(\s" + re.escape(name.encode("ascii")) + rb"\s*=\s*)([\"'])(.*?)\2"
        match = re.search(pattern, opening, re.DOTALL)
        escaped = html.escape(str(value), quote=True).encode("utf-8")
        if match:
            opening = opening[:match.start(3)] + escaped + opening[match.end(3):]
        else:
            end = len(opening.rstrip()) - (2 if opening.rstrip().endswith(b"/>") else 1)
            opening = opening[:end] + b" " + name.encode("ascii") + b'="' + escaped + b'"' + opening[end:]
    return opening


def _prefix(node):
    return node["qname"].rsplit(b":", 1)[0] + b":" if b":" in node["qname"] else b""


def _element(prefix, name, attrs=None):
    return _attributes(b"<" + prefix + name.encode("ascii") + b"/>", attrs or {})


def _path_nodes(node, path):
    if not path:
        return [node]
    result = []
    for child in node["children"]:
        if child["name"] == path[0]:
            result.extend(_path_nodes(child, path[1:]))
        elif child["name"] in ("switch", "case", "default"):
            result.extend(_path_nodes(child, path))
    return result


def _alter(raw, changes, toggles=None):
    """Clone one property record and patch only explicitly changed fields."""
    nodes = _xml_nodes(raw, namespaces=False)
    root = nodes[0]
    edits, additions = [], []
    for path, attrs in changes:
        matches = _path_nodes(root, path)
        if not matches:
            if len(path) != 1:
                raise ValueError("원본 문단 속성의 필수 하위 요소가 없습니다.")
            additions.append(_element(_prefix(root), path[0], attrs))
        for node in matches:
            values = dict(attrs)
            parent = nodes[node["parent_index"]] if "parent_index" in node else None
            case = None
            while parent:
                if parent["name"] == "case":
                    case = parent
                    break
                parent = nodes[parent["parent_index"]] if "parent_index" in parent else None
            if "value" in values and case is not None:
                required = next((v for k, v in case["attrs"].items() if k.endswith("required-namespace")), "")
                if required != "http://www.hancom.co.kr/hwpml/2016/HwpUnitChar":
                    raise ValueError("미지원 버전별 단위의 속성은 변경하지 않습니다.")
                if path[0] == "margin" or values.get("type") != "PERCENT":
                    values["value"] = round(values["value"] / 2)
                if path[0] == "lineSpacing":
                    values["unit"] = "CHAR" if values.get("type") != "PERCENT" else "HWPUNIT"
            elif path and path[0] == "lineSpacing" and "value" in values:
                values["unit"] = "HWPUNIT"
            edits.append((node["begin"], node["content"], _attributes(raw[node["begin"]:node["content"]], values)))
    for local, enabled in (toggles or {}).items():
        existing = [n for n in root["children"] if n["name"] == local]
        if not enabled:
            edits.extend((n["begin"], n["end"], b"") for n in existing)
        elif not existing:
            additions.append(_element(_prefix(root), local))
    if additions:
        if root["empty"]:
            raise ValueError("빈 속성 레코드에 하위 요소를 추가할 수 없습니다.")
        edits.append((root["close"], root["close"], b"".join(additions)))
    return _patches(raw, edits)


class _References:
    def __init__(self, raw):
        self.raw, self.nodes = raw, _xml_nodes(raw)
        self.additions, self.cache, self.next_ids = {}, {}, {}

    def _record(self, name, ident):
        matches = [n for n in self.nodes if n["name"] == name and n["attrs"].get("id") == str(ident)]
        if len(matches) > 1:
            raise ValueError("중복된 속성 ID의 스타일은 변경하지 않습니다.")
        return matches[0] if matches else None

    def _allocate(self, collection, child):
        parent = next((n for n in self.nodes if n["name"] == collection), None)
        if parent is None or parent["empty"]:
            raise ValueError("원본 스타일 참조 목록이 없습니다.")
        if collection not in self.next_ids:
            self.next_ids[collection] = max((int(n["attrs"].get("id", "0")) for n in parent["children"] if n["name"] == child), default=-1) + 1
        ident = self.next_ids[collection]
        self.next_ids[collection] += 1
        return parent, ident

    def _font(self, lang, family, kind):
        key = ("font", lang, family, kind)
        if key in self.cache:
            return self.cache[key]
        parent = next((n for n in self.nodes if n["name"] == "fontface" and n["attrs"].get("lang") == lang), None)
        if parent is None or parent["empty"] or kind not in ("TTF", "HFT"):
            raise ValueError("원본의 언어별 글꼴 목록을 수정할 수 없습니다.")
        fonts = [n for n in parent["children"] if n["name"] == "font"]
        existing = next((n for n in fonts if n["attrs"].get("face") == family and n["attrs"].get("type", "TTF") == kind), None)
        if existing:
            return existing["attrs"]["id"]
        count = len(self.additions.get(id(parent), ()))
        ident = max((int(n["attrs"].get("id", "0")) for n in fonts), default=-1) + 1 + count
        font = _element(_prefix(parent), "font", {"id": ident, "face": family, "type": kind, "isEmbedded": 0})
        self.additions.setdefault(id(parent), []).append((parent, font))
        self.cache[key] = str(ident)
        return str(ident)

    def char(self, ident, before, after):
        if before == after:
            return str(ident)
        key = ("char", str(ident), before, after)
        if key in self.cache:
            return self.cache[key]
        source = self._record("charPr", ident)
        if source is None:
            raise ValueError("글자 속성 참조가 없습니다.")
        parent, new_id = self._allocate("charProperties", "charPr")
        changes, attrs, toggles = [], {"id": new_id}, {}
        for field, attr, value in (("size", "height", round(after.size * 100)), ("color", "textColor", after.color), ("background", "shadeColor", after.background or "none")):
            if getattr(before, field) != getattr(after, field):
                attrs[attr] = value
        for field, attr in (("use_font_space", "useFontSpace"), ("use_kerning", "useKerning")):
            if getattr(before, field) != getattr(after, field):
                attrs[attr] = int(getattr(after, field))
        changes.append(((), attrs))
        for field, local in (("bold", "bold"), ("italic", "italic"), ("superscript", "supscript"), ("subscript", "subscript")):
            if getattr(before, field) != getattr(after, field):
                toggles[local] = getattr(after, field)
        for field, local, attr, enabled, disabled in (("underline", "underline", "type", "BOTTOM", "NONE"), ("strike", "strikeout", "shape", "SOLID", "NONE")):
            if getattr(before, field) != getattr(after, field):
                child = next((n for n in source["children"] if n["name"] == local), None)
                values = {attr: enabled if getattr(after, field) else disabled}
                if child is None:
                    values.update(color=after.color)
                    if local == "underline":
                        values["shape"] = "SOLID"
                changes.append(((local,), values))
        for field, local, array, fallback in (("stretch", "ratio", "language_ratios", after.stretch),
                ("letter_spacing", "spacing", "language_spacing", after.letter_spacing),
                (None, "relSz", "language_relative_sizes", 100), (None, "offset", "language_offsets", 0)):
            values = getattr(after,array)
            if getattr(before,array) != values or field and getattr(before,field) != getattr(after,field):
                if len(values) != 7 or field and getattr(before,array) == values:
                    values = (fallback,) * 7
                changes.append(((local,), dict(zip(LANG_ATTRS,values))))
        if any(getattr(before, f) != getattr(after, f) for f in ("font", "language_fonts", "font_type", "language_font_types")):
            names = after.language_fonts if len(after.language_fonts) == 7 else (after.font,) * 7
            kinds = after.language_font_types if len(after.language_font_types) == 7 else (after.font_type,) * 7
            if before.font != after.font and after.language_fonts == before.language_fonts:
                names = (after.font,) * 7
            if before.font_type != after.font_type and after.language_font_types == before.language_font_types:
                kinds = (after.font_type,) * 7
            changes.append((("fontRef",), {attr: self._font(lang, family, kind) for lang, attr, family, kind in zip(LANGUAGES, LANG_ATTRS, names, kinds)}))
        cloned = _alter(self.raw[source["begin"]:source["end"]], changes, toggles)
        self.additions.setdefault(id(parent), []).append((parent, cloned))
        self.cache[key] = str(new_id)
        return str(new_id)

    def para(self, ident, before, after):
        if before == after:
            return str(ident)
        key = ("para", str(ident), before, after)
        if key in self.cache:
            return self.cache[key]
        source = self._record("paraPr", ident)
        if source is None:
            raise ValueError("문단 속성 참조가 없습니다.")
        parent, new_id = self._allocate("paraProperties", "paraPr")
        changes = [((), {"id": new_id})]
        if before.align != after.align:
            changes.append((("align",), {"horizontal": after.align}))
        for field, local in (("left", "left"), ("right", "right"), ("indent", "intent"), ("before", "prev"), ("after", "next")):
            if getattr(before, field) != getattr(after, field):
                changes.append((("margin", local), {"value": round(getattr(after, field) * 200)}))
        if (before.line, before.line_type) != (after.line, after.line_type):
            changes.append((("lineSpacing",), {"type": after.line_type, "value": round(after.line if after.line_type == "PERCENT" else after.line * 100)}))
        if before.page_break != after.page_break:
            changes.append((("breakSetting",), {"pageBreakBefore": int(after.page_break)}))
        cloned = _alter(self.raw[source["begin"]:source["end"]], changes)
        self.additions.setdefault(id(parent), []).append((parent, cloned))
        self.cache[key] = str(new_id)
        return str(new_id)

    def finish(self):
        edits = []
        for entries in self.additions.values():
            parent = entries[0][0]
            attr = "fontCnt" if parent["name"] == "fontface" else "itemCnt"
            child = {"fontface": "font", "charProperties": "charPr", "paraProperties": "paraPr"}[parent["name"]]
            count = sum(n["name"] == child for n in parent["children"]) + len(entries)
            edits.append((parent["begin"], parent["content"], _attributes(self.raw[parent["begin"]:parent["content"]], {attr: count})))
            edits.append((parent["close"], parent["close"], b"".join(raw for _, raw in entries)))
        return _patches(self.raw, edits)


def _editable_signature(data):
    result = json.loads(json.dumps(data, ensure_ascii=False))
    def visit(value):
        if isinstance(value, dict):
            if value.get("type") == "paragraph":
                for key in ("cache_text", "line_segments"):
                    value.pop(key, None)
                for field in PARA_FIELDS:
                    value.get("style", {}).pop(field, None)
                runs = []
                for run in value.get("runs", []):
                    if run.get("image") is None and run.get("field") is None:
                        for key in ("text", "source_start", "style"):
                            run.pop(key, None)
                        if runs and run == runs[-1]:
                            continue
                    runs.append(run)
                value["runs"] = runs
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    visit(result)
    return result


def _text_tokens(raw, node):
    """Retain unchanged entities and native inline control attributes verbatim."""
    tokens, text, offset = [], [], node["content"]
    for child in node["children"] + [None]:
        end = child["begin"] if child else node["close"]
        chunk = raw[offset:end].decode("utf-8")
        for match in re.finditer(r"\r\n|\r|&(?:#[0-9]+|#x[0-9a-fA-F]+|amp|lt|gt|quot|apos);|[^&<]|[&<]", chunk):
            lexical = match.group()
            if lexical in ("&", "<"):
                raise ValueError("복잡한 텍스트 XML은 변경하지 않습니다.")
            char = "\n" if lexical in ("\r", "\r\n") else html.unescape(lexical)
            if len(char) != 1:
                raise ValueError("텍스트 엔터티를 안전하게 대응할 수 없습니다.")
            text.append(char)
            tokens.append(lexical.encode("utf-8"))
        if child:
            char = {"lineBreak": "\n", "tab": "\t", "nbSpace": "\u00a0", "fwSpace": "\u3000"}.get(child["name"])
            if char is None or not child["empty"]:
                raise ValueError("미지원 인라인 요소를 포함한 텍스트는 변경하지 않습니다.")
            text.append(char)
            tokens.append(raw[child["begin"]:child["end"]])
            offset = child["end"]
    return "".join(text), tokens


def _edited_tokens(raw, node, before, after):
    text, original = _text_tokens(raw, node)
    if text != before:
        raise ValueError("원본 텍스트와 모델 텍스트가 일치하지 않습니다.")
    tokens, prefix = [], _prefix(node)
    for tag, a0, a1, b0, b1 in difflib.SequenceMatcher(None, before, after, autojunk=False).get_opcodes():
        if tag == "equal":
            tokens.extend(original[a0:a1])
        else:
            for char in after[b0:b1]:
                tokens.append(_element(prefix, "lineBreak" if char == "\n" else "tab") if char in ("\n", "\t") else html.escape(char, quote=False).encode("utf-8"))
    return tokens


def _boundary(opcodes, offset, total):
    if offset == total:
        return opcodes[-1][4] if opcodes else 0
    for tag, a0, a1, b0, b1 in opcodes:
        if a0 == offset:
            return b0
        if a0 < offset < a1:
            return b0 + offset - a0 if tag == "equal" else b1
    raise ValueError("텍스트 경계를 대응할 수 없습니다.")


def _style_parts(runs, left, right):
    result, offset = [], 0
    for run in runs:
        end = offset + len(run.text)
        a, b = max(offset, left), min(end, right)
        if a < b:
            piece = run.text[a-offset:b-offset]
            if result and result[-1][1] == run.style:
                result[-1] = (result[-1][0] + piece, run.style)
            else:
                result.append((piece, run.style))
        offset = end
    return result


def _visible_styles(runs):
    result = []
    for run in runs:
        # Language fallback tuples may be populated while re-reading a font ref;
        # all explicitly modeled visible properties must still match exactly.
        style = {k: v for k, v in run.style.__dict__.items() if k not in ("language_fonts", "language_font_types", "language_ratios", "language_spacing", "language_relative_sizes", "language_offsets")}
        if not run.text:
            continue
        if result and result[-1][1] == style:
            result[-1] = (result[-1][0] + run.text, style)
        else:
            result.append((run.text, style))
    return result


def patch_hwpx_source(model, path):
    from .formats import model_to_data, model_from_data, _walk_blocks, Paragraph, _atomic_write, _load_hwpx
    source = getattr(model, "_hwpx_source", None)
    if source is None:
        return False
    baseline = model_from_data(source["baseline"])
    baseline_data = model_to_data(baseline)
    current = model_to_data(model)
    if json.dumps(current, sort_keys=True, ensure_ascii=False) == json.dumps(baseline_data, sort_keys=True, ensure_ascii=False):
        _atomic_write(path, source["data"])
        return True
    if _editable_signature(current) != _editable_signature(baseline_data):
        return False
    def paras(m):
        return [p for p in _walk_blocks(m.blocks + [b for part in m.header_footers for b in part.blocks]) if isinstance(p, Paragraph)]
    old_paras, new_paras = paras(baseline), paras(model)
    if len(old_paras) != len(new_paras):
        return False
    changes = {}
    for old, new in zip(old_paras, new_paras):
        if old.style != new.style or [(r.text, r.style) for r in old.runs] != [(r.text, r.style) for r in new.runs]:
            location = source["paragraphs"].get(str(old.source_index))
            if location is None:
                return False
            changes.setdefault(location[0], []).append((location[1], old, new))
    if not changes:
        return False
    modified = {}
    try:
        with zipfile.ZipFile(io.BytesIO(source["data"])) as zin:
            if len(zin.namelist()) != len(set(zin.namelist())):
                return False
            refs = _References(zin.read("Contents/header.xml"))
            for name, edits in changes.items():
                raw = zin.read(name)
                paragraphs = [n for n in _xml_nodes(raw) if n["name"] == "p"]
                patches = []
                for index, old, new in edits:
                    p = paragraphs[index]
                    if old.style != new.style:
                        ident = refs.para(p["attrs"].get("paraPrIDRef", "0"), old.style, new.style)
                        attrs = {"paraPrIDRef": ident}
                        if old.style.page_break != new.style.page_break:
                            attrs["pageBreak"] = int(new.style.page_break)
                        patches.append((p["begin"], p["content"], _attributes(raw[p["begin"]:p["content"]], attrs)))
                    text_changed = [(r.text, r.style) for r in old.runs] != [(r.text, r.style) for r in new.runs]
                    if text_changed:
                        if any(r.image is not None or r.field is not None for r in old.runs + new.runs):
                            return False
                        texts = [(run, t) for run in p["children"] if run["name"] == "run" for t in run["children"] if t["name"] == "t"]
                        if len(texts) != len(old.runs):
                            return False
                        before, after = "".join(r.text for r in old.runs), "".join(r.text for r in new.runs)
                        opcodes = difflib.SequenceMatcher(None, before, after, autojunk=False).get_opcodes()
                        offset = 0
                        for run_index, ((run, t), original) in enumerate(zip(texts, old.runs)):
                            left = 0 if not before and len(texts) == 1 else _boundary(opcodes, offset, len(before))
                            right = len(after) if not before and len(texts) == 1 else _boundary(opcodes, offset + len(original.text), len(before))
                            offset += len(original.text)
                            parts = _style_parts(new.runs, left, right) or [("", original.style)]
                            if not original.text and len(old.runs) == len(new.runs):
                                # An empty text run can carry an explicit insertion style.
                                parts = [(text, new.runs[run_index].style) for text, _ in parts]
                            replacement_text = "".join(text for text, _ in parts)
                            if replacement_text == original.text and len(parts) == 1 and parts[0][1] == original.style:
                                continue
                            tokens = _edited_tokens(raw, t, original.text, replacement_text)
                            opening, closing = raw[t["begin"]:t["content"]], raw[t["close"]:t["end"]]
                            if t["empty"]:
                                opening = opening.rstrip()[:-2] + b">"
                                closing = b"</" + t["qname"] + b">"
                            if len(parts) == 1 and parts[0][1] == original.style:
                                replacement = opening + b"".join(tokens) + closing
                            else:
                                # Original wrappers retain control siblings and paragraph-mark style.
                                run_open = raw[run["begin"]:run["content"]]
                                run_close = b"</" + run["qname"] + b">"
                                replacement, token_offset = run_close, 0
                                for text, style in parts:
                                    ident = refs.char(run["attrs"].get("charPrIDRef", "0"), original.style, style)
                                    replacement += _attributes(run_open, {"charPrIDRef": ident}) + opening + b"".join(tokens[token_offset:token_offset+len(text)]) + closing + run_close
                                    token_offset += len(text)
                                replacement += run_open
                            patches.append((t["begin"], t["end"], replacement))
                    for lines in (n for n in p["children"] if n["name"] == "linesegarray"):
                        patches.append((lines["begin"], lines["end"], b""))
                modified[name] = _patches(raw, patches)
                _xml_nodes(modified[name])
            header = refs.finish()
            if header != refs.raw:
                _xml_nodes(header)
                modified["Contents/header.xml"] = header
            output = io.BytesIO()
            with zipfile.ZipFile(output, "w") as zout:
                zout.comment = zin.comment
                for info in zin.infolist():
                    zout.writestr(info, modified.get(info.filename, zin.read(info)))
        restored = {p.source_index: p for p in paras(_load_hwpx(io.BytesIO(output.getvalue())).model)}
        for edits in changes.values():
            for _, old, new in edits:
                actual = restored.get(old.source_index)
                if actual is None or actual.style != new.style or _visible_styles(actual.runs) != _visible_styles(new.runs):
                    return False
        _atomic_write(path, output.getvalue())
    except (ValueError, UnicodeError, expat.ExpatError, KeyError, IndexError, OverflowError):
        return False
    return True


"""Module extending hwpx_native with native cell merge capabilities."""
import io
import re
import zipfile
from xml.etree import ElementTree as ET

def patch_hwpx_merge_cells(hwpx_bytes: bytes, merge_ops: list) -> bytes:
    with zipfile.ZipFile(io.BytesIO(hwpx_bytes), "r") as zin:
        entries = {info.filename: (info, zin.read(info.filename)) for info in zin.infolist()}

    sec0_info, sec0_bytes = entries["Contents/section0.xml"]

    ET.register_namespace("hp", "http://www.hancom.co.kr/hwpml/2011/paragraph")
    ET.register_namespace("hs", "http://www.hancom.co.kr/hwpml/2011/section")
    ET.register_namespace("hc", "http://www.hancom.co.kr/hwpml/2011/core")
    ET.register_namespace("hh", "http://www.hancom.co.kr/hwpml/2011/head")

    root = ET.fromstring(sec0_bytes)
    HP = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"
    tables = root.findall(f".//{HP}tbl")

    prv_replacements = []

    for op in merge_ops:
        tbl_idx = op.get("table_index", 0)
        target_table_id = op.get("target_table")
        tbl = None
        if target_table_id:
            # Match body index if needed or fallback to index
            match = re.search(r"body/(\d+)", target_table_id)
            if match:
                # find table matching or fallback
                pass
        if tbl is None and tbl_idx < len(tables):
            tbl = tables[tbl_idx]
        if tbl is None:
            raise ValueError(f"Table not found for merge operation: {op}")

        trs = tbl.findall(f"{HP}tr")
        start_row = op["start_row"]
        start_col = op["start_col"]
        row_span = op.get("row_span", 1)
        col_span = op.get("col_span", 1)
        text = op.get("text", "")
        border_fill = op.get("border_fill")

        target_rows = range(start_row, start_row + row_span)
        target_cols = range(start_col, start_col + col_span)

        # 1. Collect dimensions and locate start_tc
        start_tc = None
        row_heights = {}
        col_widths = {}

        for r_idx in target_rows:
            tr = trs[r_idx]
            for tc in tr.findall(f"{HP}tc"):
                addr = tc.find(f"{HP}cellAddr")
                if addr is not None:
                    r = int(addr.get("rowAddr"))
                    c = int(addr.get("colAddr"))
                    if r in target_rows and c in target_cols:
                        sz = tc.find(f"{HP}cellSz")
                        if sz is not None:
                            w = int(sz.get("width"))
                            h = int(sz.get("height"))
                            row_heights[r] = max(row_heights.get(r, 0), h)
                            col_widths[c] = max(col_widths.get(c, 0), w)
                        if r == start_row and c == start_col:
                            start_tc = tc

        if start_tc is None:
            raise ValueError(f"Start cell ({start_row}, {start_col}) not found")

        total_height = sum(row_heights.values())
        total_width = sum(col_widths.values())

        # Update start_tc
        if border_fill:
            start_tc.set("borderFillIDRef", str(border_fill))

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

        # Update text if specified
        if text is not None:
            subList = start_tc.find(f"{HP}subList")
            if subList is not None:
                subList.set("vertAlign", "CENTER")
                for p in list(subList.findall(f"{HP}p")):
                    subList.remove(p)
            else:
                subList = ET.SubElement(start_tc, f"{HP}subList", {
                    "id": "", "textDirection": "HORIZONTAL", "lineWrap": "BREAK", "vertAlign": "CENTER",
                    "linkListIDRef": "0", "linkListNextIDRef": "0", "textWidth": "0", "textHeight": "0",
                    "hasTextRef": "0", "hasNumRef": "0"
                })

            para_pr = op.get("para_pr", "19")
            char_pr = op.get("char_pr", "8")
            new_p = ET.SubElement(subList, f"{HP}p", {
                "id": "2147483648", "paraPrIDRef": str(para_pr), "styleIDRef": "0",
                "pageBreak": "0", "columnBreak": "0", "merged": "0"
            })
            new_run = ET.SubElement(new_p, f"{HP}run", {"charPrIDRef": str(char_pr)})
            new_t = ET.SubElement(new_run, f"{HP}t")
            new_t.text = text

        # 2. Remove other merged cells
        for r_idx in target_rows:
            tr = trs[r_idx]
            for tc in list(tr.findall(f"{HP}tc")):
                addr = tc.find(f"{HP}cellAddr")
                if addr is not None:
                    r = int(addr.get("rowAddr"))
                    c = int(addr.get("colAddr"))
                    if r in target_rows and c in target_cols:
                        if not (r == start_row and c == start_col):
                            tr.remove(tc)

    new_sec0_xml = ET.tostring(root, encoding="utf-8", xml_declaration=True)

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
            else:
                zout.writestr(new_info, data)

    return out_stream.getvalue()

