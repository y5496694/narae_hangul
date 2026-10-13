"""Keep imported native objects while mapping text edits from the Qt surface."""
import copy
import difflib
from dataclasses import replace
from .formats import Paragraph, Run, Table, Drawing, _walk_blocks, _qt_blocks, _block_data

SOURCE_PROPERTY = 0x100006
CHAR_FIELDS = ("font", "size", "bold", "italic", "underline", "strike", "color", "background",
               "superscript", "subscript", "stretch", "letter_spacing")
PARA_FIELDS = ("align", "left", "right", "indent", "before", "after", "line", "line_type", "page_break")


def paragraph_text(paragraph):
    return "".join("\ufffc" if r.image is not None else r.text for r in paragraph.runs)


def editor_text(block, original):
    return block.text().replace("\u2028", "\n")


def visible_paragraphs(blocks):
    for index, block in enumerate(blocks):
        following = blocks[index + 1] if index + 1 < len(blocks) else None
        if isinstance(block, Table):
            for cell in sorted(block.cells, key=lambda c: (c.row, c.col)):
                yield from visible_paragraphs(cell.blocks)
        elif isinstance(block, Drawing):
            yield from visible_paragraphs(block.blocks)
        elif not (not paragraph_text(block) and isinstance(following, (Table, Drawing)) and block.source_index == following.source_index):
            yield block


def bind_source_paragraphs(document, model):
    from PySide6.QtGui import QTextCursor
    ordinals = {id(p):i for i,p in enumerate(p for p in _walk_blocks(model.blocks) if isinstance(p, Paragraph))}
    block = document.begin()
    for paragraph in visible_paragraphs(model.blocks):
        wanted = paragraph_text(paragraph)
        while block.isValid() and not (not wanted and block.text() in ('','\u2028')) and editor_text(block, wanted) != wanted:
            block = block.next()
        if not block.isValid():
            return False
        cursor = QTextCursor(block)
        if not wanted and block.text()=='\u2028':
            # HTML <p><br/></p> imports a line-separator placeholder. Remove
            # only this initial empty-native placeholder before taking the
            # edit baseline; a later user-entered line break remains real.
            cursor.setPosition(block.position())
            cursor.setPosition(block.position()+1,QTextCursor.MoveMode.KeepAnchor)
            cursor.removeSelectedText()
        fmt = block.blockFormat()
        fmt.setProperty(SOURCE_PROPERTY, ordinals[id(paragraph)])
        cursor.setBlockFormat(fmt)
        block = block.next()
    return True


def editor_signature(document):
    blocks = _qt_blocks(document)[0]
    data = [_block_data(b) for b in blocks]
    def visit(value):
        if isinstance(value, dict):
            if value.get("type") == "paragraph":
                for key in PARA_FIELDS:
                    value["style"].pop(key, None)
                runs = []
                for run in value.get("runs", []):
                    if run.get('image') is None and not run.get('field'):
                        continue
                    run.pop("text", None)
                    # Fragment boundaries change during typing and formatting.
                    # Object geometry and non-text payload remain structural.
                    run.pop("style", None)
                    if not runs or run != runs[-1]:
                        runs.append(run)
                value["runs"] = runs
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    visit(data)
    unbound, block = [], document.begin()
    while block.isValid():
        if block.blockFormat().property(SOURCE_PROPERTY) is None:
            unbound.append((block.blockNumber(), block.text()))
        block = block.next()
    bound = {p.source_index:copy.deepcopy(p) for p in _walk_blocks(blocks)
             if isinstance(p, Paragraph) and p.source_index >= 0}
    return data, unbound, bound


def _styles_by_character(paragraph):
    result = []
    for run in paragraph.runs:
        result.extend([run.style] * len(run.text))
    return result


def _style_delta(original, before, after):
    changes = {key:getattr(after,key) for key in CHAR_FIELDS if getattr(before,key)!=getattr(after,key)}
    if "font" in changes:
        # A deliberately selected Qt font uses its TTF declaration. Imported
        # per-script HFT/TTF declarations survive unrelated toolbar changes.
        changes.update(language_fonts=(after.font,)*7, language_font_types=("TTF",)*7, font_type="TTF")
    if 'stretch' in changes:
        changes['language_ratios']=(after.stretch,)*7
    if 'letter_spacing' in changes:
        changes['language_spacing']=(after.letter_spacing,)*7
    return replace(original, **changes) if changes else original


def _merge_styles(paragraph, before, after):
    old_text, new_text = paragraph_text(before), paragraph_text(after)
    old_styles, new_styles = _styles_by_character(before), _styles_by_character(after)
    if any(r.image is not None or r.field for r in paragraph.runs):
        if old_text != new_text or before.runs != after.runs:
            return False
        return True
    if len(new_styles)!=len(new_text):
        return False
    # Map Qt's baseline style to each surviving/inserted character, then apply
    # only the fields the user changed. This retains unexposed native settings.
    origins = [0] * len(new_text)
    for operation, start, end, current_start, current_end in difflib.SequenceMatcher(None, old_text, new_text, autojunk=False).get_opcodes():
        for i in range(current_start,current_end):
            origins[i] = start+i-current_start if operation=="equal" else min(start,len(old_styles)-1)
    result, offset, metrics_changed = [], 0, False
    for run in paragraph.runs:
        if not run.text:
            result.append(run)
            continue
        start = 0
        effective = []
        for i in range(len(run.text)):
            index = offset+i
            baseline = old_styles[origins[index]] if old_styles else before.default_style
            effective.append(_style_delta(run.style,baseline,new_styles[index]))
            if any(getattr(run.style,key)!=getattr(effective[-1],key) for key in ("font","size","bold","italic","stretch","letter_spacing","superscript","subscript")):
                metrics_changed=True
        while start < len(run.text):
            end = start+1
            while end<len(run.text) and effective[end]==effective[start]:
                end+=1
            native_offset = len(run.text[:start].replace("\t","\t"+"\u200b"*7).encode("utf-16le"))//2
            result.append(replace(run,text=run.text[start:end],style=effective[start],
                                  source_start=None if run.source_start is None else run.source_start+native_offset))
            start=end
        offset+=len(run.text)
    paragraph.runs=result
    if metrics_changed:paragraph.line_segments=[]
    return True


def replace_paragraph_text(paragraph, text):
    """Distribute edits across existing runs, retaining their original styles."""
    old = paragraph_text(paragraph)
    if old == text:
        return
    if any(r.image is not None or r.field for r in paragraph.runs):
        raise ValueError("그림·자동 필드를 포함한 문단은 편집 탭에서 수정하세요.")
    if not paragraph.runs:
        paragraph.runs=[Run(text,paragraph.default_style,source_start=0)]
        paragraph.cache_text=text
        paragraph.line_segments=[]
        return
    boundaries, offset = [], 0
    for run in paragraph.runs:
        boundaries.append((offset, offset + len(run.text)))
        offset += len(run.text)
    edits = [[] for _ in paragraph.runs]
    for _, start, end, new_start, new_end in difflib.SequenceMatcher(None, old, text, autojunk=False).get_opcodes():
        if old[start:end] == text[new_start:new_end]:
            continue
        owner = next((i for i, (_, right) in enumerate(boundaries) if start < right), len(boundaries) - 1)
        for i, (left, right) in enumerate(boundaries):
            if max(start, left) < min(end, right):
                edits[i].append((max(start, left) - left, min(end, right) - left, text[new_start:new_end] if i == owner else ""))
            elif i == owner and start == end:
                edits[i].append((start - left, start - left, text[new_start:new_end]))
    cache = paragraph.cache_text.encode("utf-16le")
    cache_patches = []
    for run, changes in zip(paragraph.runs, edits):
        before = run.text
        for left, right, replacement in sorted(changes, reverse=True):
            run.text = run.text[:left] + replacement + run.text[right:]
        if before != run.text and run.source_start is not None:
            begin = run.source_start * 2
            cache_patches.append((begin, begin + len(before.replace("\t", "\t" + "\u200b" * 7).encode("utf-16le")), run.text.replace("\t", "\t" + "\u200b" * 7).encode("utf-16le")))
    for begin, end, replacement in sorted(cache_patches, reverse=True):
        cache = cache[:begin] + replacement + cache[end:]
    paragraph.cache_text = cache.decode("utf-16le") if cache else text
    for run in paragraph.runs:
        if run.source_start is not None:
            original = run.source_start * 2
            run.source_start += sum((len(replacement) - (end - begin)) // 2 for begin, end, replacement in cache_patches if end <= original)
    for segment in paragraph.line_segments:
        original = segment["text_start"] * 2
        segment["text_start"] += sum((len(replacement) - (end - begin)) // 2 for begin, end, replacement in cache_patches if end <= original)


def merge_editor_text(original, baseline_signature, document):
    current_signature = editor_signature(document)
    if current_signature[:2] != baseline_signature[:2]:
        return None
    original_paragraphs = [p for p in _walk_blocks(original.blocks) if isinstance(p, Paragraph)]
    original_texts = {i:paragraph_text(p) for i,p in enumerate(original_paragraphs)}
    texts, block = {}, document.begin()
    while block.isValid():
        index = block.blockFormat().property(SOURCE_PROPERTY)
        if index is not None:
            if index in texts:
                return None
            texts[index] = editor_text(block, original_texts.get(index, ""))
        block = block.next()
    model = copy.deepcopy(original)
    for index, paragraph in enumerate(p for p in _walk_blocks(model.blocks) if isinstance(p, Paragraph)):
        if index in texts:
            try:
                replace_paragraph_text(paragraph, texts[index])
                before, after = baseline_signature[2].get(index), current_signature[2].get(index)
                if before is None or after is None or not _merge_styles(paragraph,before,after):
                    return None
                delta={key:getattr(after.style,key) for key in PARA_FIELDS if getattr(before.style,key)!=getattr(after.style,key)}
                if delta:
                    paragraph.style=replace(paragraph.style,**delta)
                    paragraph.line_segments=[]
            except ValueError:
                return None
    return model
