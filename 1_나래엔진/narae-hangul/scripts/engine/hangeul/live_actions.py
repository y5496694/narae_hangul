"""Precise live Hancom actions for verified document targets.

Use action-created sets, avoiding the mismatched installed HParameterSet getters.
"""
import json
from pathlib import Path
import re
import sys
import time
import pythoncom
from win32com.client import dynamic


def application_for(file):
    target = str(Path(file).resolve()).lower()
    rot, context = pythoncom.GetRunningObjectTable(), pythoncom.CreateBindCtx(0)
    matches = []
    for moniker in rot:
        if not moniker.GetDisplayName(context, None).startswith("!HwpObject."):
            continue
        app = dynamic.Dispatch(rot.GetObject(moniker).QueryInterface(pythoncom.IID_IDispatch))
        for index in range(app.XHwpDocuments.Count):
            doc = app.XHwpDocuments.Item(index)
            if str(Path(str(doc.FullName)).resolve()).lower() == target:
                matches.append((app, doc))
    if len(matches) != 1:
        raise ValueError("한글 문서를 하나로 식별할 수 없습니다. 중복으로 열린 사본을 확인하세요.")
    app, doc = matches[0]
    doc.SetActive_XHwpDocument()
    if str(Path(str(app.XHwpDocuments.Active_XHwpDocument.FullName)).resolve()).lower() != target:
        raise ValueError("현재 한글 문서가 요청한 파일과 다릅니다.")
    return app, doc


def apply(app, name, values):
    action = app.CreateAction(name)
    params = action.CreateSet()
    for key, value in values.items():
        params.SetItem(key, value)
    if not action.Execute(params):
        raise ValueError("한글 동작 실패: " + name)


def selected_text(app):
    # GetTextFile exports to memory; tracked documents otherwise display a
    # conversion warning even for a selection. Restore the user's mode promptly.
    previous = app.GetMessageBoxMode()
    try:
        app.SetMessageBoxMode(0x00010000)
        return app.GetTextFile("TEXT", "saveblock").rstrip("\r\n")
    finally:
        app.SetMessageBoxMode(previous)


def select_paragraph(app, target):
    previous = app.GetMessageBoxMode()
    try:
        # RepeatFind can ask whether to wrap at the document end even with
        # IgnoreMessage. Refuse wrap prompts in this hidden native operation.
        app.SetMessageBoxMode(0x00020000)
        return _select_paragraph(app, target)
    finally:
        app.SetMessageBoxMode(previous)


def _select_paragraph(app, target):
    value = target["text"].strip()
    if not value or len(value) > 10000:
        raise ValueError("글자 서식을 적용할 문단 내용을 먼저 읽으세요.")
    app.HAction.Run("Cancel")
    cached=target.get('position')
    if cached and len(cached)==6:
        try:
            app.SetPos(cached[0],cached[1],0);app.HAction.Run('MoveSelParaEnd')
            whole=selected_text(app).strip()
            app.SetPos(cached[0],cached[1],cached[2]);app.SelectText(cached[1],cached[2],cached[4],cached[5])
            if whole==value and selected_text(app)==value:return (True,*cached)
        except (pythoncom.com_error,ValueError):
            pass
        app.HAction.Run('Cancel')
    app.MovePos(2, 0, 0)
    action = app.CreateAction("RepeatFind")
    params = action.CreateSet()
    action.GetDefault(params)
    for key, item in {"FindString": value, "Direction": 0, "IgnoreMessage": 1, "FindType": 1}.items():
        params.SetItem(key, item)
    positions, seen = [], set()
    while len(seen) < 10000 and action.Execute(params):
        selected = tuple(app.GetSelectedPos())
        if len(selected) != 7 or not selected[0] or selected[1] != selected[4] or selected[2] != selected[5]:
            raise ValueError("한글이 정확한 문단 범위를 선택하지 못했습니다.")
        if selected in seen:
            break
        seen.add(selected)
        # Find may also match part of a longer paragraph. Only whole paragraphs
        # from the CVD line are eligible; restore the search selection afterward.
        app.SetPos(selected[1], selected[2], 0)
        app.HAction.Run("MoveSelParaEnd")
        paragraph = selected_text(app)
        app.SetPos(selected[1], selected[2], selected[3])
        app.SelectText(selected[2], selected[3], selected[5], selected[6])
        if paragraph.strip() == value:
            positions.append(selected)
            if len(positions)>target['occurrences']:
                raise ValueError('같은 내용의 문단이 여러 개입니다. CVD scope 또는 ID 기반 파일 편집을 사용하세요.')
    if len(positions) != target["occurrences"] or len(positions) <= target["occurrence"]:
        raise ValueError("읽은 문단과 실제 한글의 후보 수가 다릅니다. 원본 CVD patch 또는 scope 서식을 사용하세요.")
    selected = positions[target["occurrence"]]
    app.SetPos(selected[1], selected[2], selected[3])
    if not app.SelectText(selected[2], selected[3], selected[5], selected[6]):
        raise ValueError("대상 문단 선택 실패")
    actual = selected_text(app)
    if actual != value:
        raise ValueError("선택된 문단의 내용이 읽은 내용과 다릅니다.")
    return selected


def style(app, step, target):
    char, para = {}, {}
    allowed = {"name", "target_line_id", "font_size", "font_family", "bold", "italic", "underline", "text_color", "align"}
    if set(step) - allowed:
        raise ValueError("지원하지 않는 글자 서식 인자: " + ", ".join(sorted(set(step) - allowed)))
    if "font_size" in step:
        size = float(step["font_size"])
        if not 1 <= size <= 400:
            raise ValueError("글자 크기는 1~400pt로 지정하세요.")
        char["Height"] = round(size * 100)
    if "font_family" in step:
        family = str(step["font_family"]).strip()
        if not family or len(family) > 200:
            raise ValueError("글꼴 이름을 확인하세요.")
        for script in ("Hangul", "Latin", "Hanja", "Japanese", "Other", "Symbol", "User"):
            char["FaceName" + script] = family
            char["FontType" + script] = int(app.FontType("TTF"))
    for field, key in (("bold", "Bold"), ("italic", "Italic"), ("underline", "UnderlineType")):
        if field in step:
            if not isinstance(step[field], bool):
                raise ValueError(field + "는 true/false로 지정하세요.")
            char[key] = int(step[field])
    if "text_color" in step:
        value = str(step["text_color"])
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            raise ValueError("글자색은 #RRGGBB로 지정하세요.")
        char["TextColor"] = int(value[1:3], 16) | int(value[3:5], 16) << 8 | int(value[5:7], 16) << 16
    if "align" in step:
        align = {"justify": 0, "left": 1, "right": 2, "center": 3, "distribute": 4, "distribute_space": 5}.get(str(step["align"]).lower())
        if align is None:
            raise ValueError("문단 정렬을 확인하세요.")
        para["AlignType"] = align
    if not char and not para:
        raise ValueError("변경할 서식을 지정하세요.")
    selected = select_paragraph(app, target)
    if char:
        apply(app, "CharShape", char)
    if para:
        apply(app, "ParagraphShape", para)
    # Execute can return success even when a font from another font category
    # was ignored. Observe the applied values before claiming it was changed.
    for action_name, values in (("CharShape", char), ("ParagraphShape", para)):
        if not values:
            continue
        action = app.CreateAction(action_name)
        actual = action.CreateSet()
        action.GetDefault(actual)
        for key,value in values.items():
            if key.startswith("FontType") or key.startswith("FaceName") and key != "FaceNameHangul":
                continue
            if actual.Item(key) != value:
                raise ValueError("한글이 요청한 서식을 반영하지 않았습니다: " + key)
    return {"selection": selected[1:], "properties": {**char, **para}}
