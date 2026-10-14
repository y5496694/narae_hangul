"""Hancom COM bridge with explicit targeting and saved-content verification.

The raw action interface exposes the installed Hancom runtime; it does not
claim that the independent renderer implements every Hancom feature.
"""
from __future__ import annotations
import re
import os
import math
import time
import base64
import hashlib
import zlib
import tempfile
import ctypes
import shutil
from pathlib import Path
import xml.etree.ElementTree as ET


class HancomError(ValueError):
    pass


def integer(value, label):
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100000:
        raise HancomError(f"{label} must be a nonnegative integer")
    return value


def paragraph_xml_text(paragraph):
    return "".join(c.text or "" if c.tag == "CHAR" else "\n" if c.tag == "LINEBREAK" else "\t" if c.tag == "TAB" else ""
                   for text in paragraph.findall("TEXT") for c in text)


def cell_text(cell):
    return "\n".join(paragraph_xml_text(p) for p in cell.findall("./PARALIST/P"))


def snapshot(xml):
    root = ET.fromstring(xml)
    tables = []
    for table in root.findall(".//TABLE"):
        cells = [{"row": int(c.get("RowAddr", 0)), "col": int(c.get("ColAddr", 0)),
                  "row_span": int(c.get("RowSpan", 1)), "col_span": int(c.get("ColSpan", 1)),
                  "text": cell_text(c)} for c in table.findall("./ROW/CELL")]
        tables.append({"rows": int(table.get("RowCount", 0)), "cols": int(table.get("ColCount", 0)),
                       "cells": sorted(cells, key=lambda c: (c["row"], c["col"]))})
    return {"text": "\n".join(paragraph_xml_text(p) for p in root.findall("./BODY//P")), "tables": tables}


def verification_snapshot(xml):
    root = ET.fromstring(xml)
    maps = {}
    binaries = {}
    for entry in root.findall("./TAIL/BINDATASTORAGE/BINDATA"):
        data = base64.b64decode(entry.text or "")
        if len(data) != int(entry.get("Size", len(data))):
            try: data = zlib.decompress(data, -15)
            except zlib.error: pass
        binaries[entry.get("Id")] = hashlib.sha256(data).hexdigest()
    for name, path in (("CharShape", "CHARSHAPELIST/CHARSHAPE"), ("ParaShape", "PARASHAPELIST/PARASHAPE"), ("BorderFill", "BORDERFILLLIST/BORDERFILL"), ("Style", "STYLELIST/STYLE")):
        maps[name] = {e.get("Id"): e for e in root.findall("./HEAD/MAPPINGTABLE/" + path)}
    def canonical(element, ancestors=()):
        attrs = {}
        for name, value in element.attrib.items():
            if name in ("Id", "InstId", "InstID", "Dirty"): continue
            attrs[name] = binaries[value] if name == "BinItem" and value in binaries else canonical(maps[name][value]) if name in maps and value in maps[name] else value
        if element.tag == "SIZE" and ancestors[-2:] == ("TABLE", "SHAPEOBJECT"):
            # Hancom recalculates aggregate table height on load/save. Cell
            # heights, width and all requested cell formatting remain checked.
            attrs.pop("Height", None)
        return {"tag": element.tag, "attributes": attrs,
                "children": [canonical(e, ancestors + (element.tag,)) for e in element if e.tag not in ("LINESegLIST",)],
                "text": element.text or ""}
    return {"content": snapshot(xml), "body": canonical(root.find("BODY")), "embedded_sha256": sorted(binaries.values())}


class HancomBridge:
    def __init__(self, visible=False):
        import pythoncom
        import win32com.client
        import win32process
        # pywin32 caches main-thread initialization, so its helpers do not
        # balance nested bridge lifetimes. Match native initialization and
        # uninitialization calls without tearing down another caller's COM.
        ole32 = ctypes.WinDLL("ole32")
        initialize = ole32.CoInitializeEx
        initialize.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        initialize.restype = ctypes.c_long
        status = initialize(None, pythoncom.COINIT_APARTMENTTHREADED)
        if status & 0xffffffff == 0x80010106: status = initialize(None, pythoncom.COINIT_MULTITHREADED)
        if status < 0: raise HancomError(f"COM initialization failed: {status & 0xffffffff:#x}")
        self._uninitialize = ole32.CoUninitialize
        self._uninitialize.argtypes = []
        self._uninitialize.restype = None
        self._com, self._hwp, self.receipts = pythoncom, None, []
        self._document = self._previous_document = self._window = None
        self._document_id = self._previous_document_id = self._window_handle = None
        self._owns_process = False
        self._old_message_mode = None
        self.visible = bool(visible)
        prior_processes = set(win32process.EnumProcesses())
        try:
            for attempt in range(3):
                try:
                    self._hwp = win32com.client.DispatchEx("HWPFrame.HwpObject")
                    self._window = self._hwp.XHwpWindows.Active_XHwpWindow
                    break
                except pythoncom.com_error as error:
                    if attempt == 2 or error.hresult & 0xffffffff not in (0x80010105, 0x800706ba, 0x800706be): raise
                    self._hwp = None
                    time.sleep(.2)
            process_id = win32process.GetWindowThreadProcessId(self._window.WindowHandle)[1]
            self._owns_process = process_id not in prior_processes
            documents = self._hwp.XHwpDocuments
            current = documents.Active_XHwpDocument
            if self._owns_process and documents.Count == 1 and self._hwp.IsEmpty and not current.Path:
                self._document = current
            else:
                # Hancom can return the SAME application even for DispatchEx.
                # Own a separate tab, never the current user document. Keep
                # the root automation object bound to its original window.
                self._previous_document = current
                self._previous_document_id = current.DocumentID
                prior_ids = {documents.Item(i).DocumentID for i in range(documents.Count)}
                created = documents.Add(True)
                if created is None or created.DocumentID in prior_ids:
                    raise HancomError("Could not create an isolated Hancom document")
                self._document = created
                self._window = self._hwp.XHwpWindows.Active_XHwpWindow
            self._document.SetActive_XHwpDocument()
            self._document_id = self._document.DocumentID
            self._window_handle = self._window.WindowHandle
            if self._owns_process or visible: self._window.Visible = bool(visible)
            configured = os.environ.get("NARAE_HANCOM_SECURITY_MODULE")
            module_names = (configured,) if configured else ("NaraeHangulFilePathChecker", "FilePathCheckerModule")
            self.security_module_registered = False
            for module_name in module_names:
                if self.hwp.RegisterModule("FilePathCheckDLL", module_name):
                    self.security_module_registered = True
                    self.security_module_name = module_name
                    break
            self._old_message_mode = self._hwp.GetMessageBoxMode()
            self._hwp.SetMessageBoxMode(0x00020000)
            # File I/O replaces Hancom's native document objects. Retain only
            # stable identifiers across Open/SaveAs, not their COM wrappers.
            self._document = self._previous_document = self._window = None
        except Exception:
            self.close()
            raise

    def close(self):
        try:
            if self._hwp is not None:
                try:
                    if self._old_message_mode is not None: self._hwp.SetMessageBoxMode(self._old_message_mode)
                except Exception: pass
                try:
                    if self._document_id is not None:
                        # Quit is application-wide. Document collections can
                        # be scoped/cached per controller; never infer that a
                        # count of one means no other user window exists.
                        self.activate_document()
                        active = self._hwp.XHwpDocuments.Active_XHwpDocument
                        active.Close(False)
                        if self._previous_document_id is not None:
                            previous = self._hwp.XHwpDocuments.FindItem(self._previous_document_id)
                            if previous is not None: previous.SetActive_XHwpDocument()
                except Exception: pass
                # Bound the retirement pause; never retry document mutations.
                time.sleep(.1)
        finally:
            self._document = self._previous_document = self._window = self._hwp = None
            if self._com is not None:
                self._uninitialize()
                self._com = None

    def __enter__(self): return self
    def __exit__(self, *args): self.close()

    @property
    def hwp(self):
        return self._hwp

    def activate_document(self):
        if self._document_id is not None and self._hwp.XHwpDocuments.Active_XHwpDocument.DocumentID != self._document_id:
            selected = self._hwp.XHwpDocuments.FindItem(self._document_id)
            if selected is None: raise HancomError("The job's document was closed")
            selected.SetActive_XHwpDocument()

    @hwp.setter
    def hwp(self, value):
        self._hwp = value

    def _refresh_document(self):
        # Document COM objects may become stale after file I/O; refresh from
        # the root interface and refuse any switch into another user window.
        active_window = self._hwp.XHwpWindows.Active_XHwpWindow
        if active_window.WindowHandle != self._window_handle:
            raise HancomError("Hancom switched outside the window owned by this job")
        self._document_id = self._hwp.XHwpDocuments.Active_XHwpDocument.DocumentID

    @staticmethod
    def check(value, label):
        if value is False or value == 0:
            raise HancomError(f"Hancom rejected {label}")
        return value

    def run(self, action): return self.check(self.hwp.HAction.Run(action), action)

    def open_doc(self, path):
        self.activate_document()
        if not self.security_module_registered and not self.visible:
            raise HancomError("Hancom file access module is not registered. Configure an approved FilePathCheckDLL under HKCU/Software/HNC/HwpAutomation/Modules and set NARAE_HANCOM_SECURITY_MODULE to its registry value name. Refusing to hang on a hidden permission dialog.")
        path = Path(path).resolve()
        fmt = "HWPX" if path.suffix.lower() == ".hwpx" else "HWP"
        result = self.check(self.hwp.Open(str(path), fmt, "versionwarning:false"), "Open")
        self._refresh_document()
        return result

    def save_doc(self, path=None):
        self.activate_document()
        if path is None:
            result = self.check(self.hwp.Save(), "Save")
            return result
        path = Path(path).resolve()
        result = self.check(self.hwp.SaveAs(str(path), "HWPX" if path.suffix.lower() == ".hwpx" else "HWP", ""), "SaveAs")
        return result

    def xml(self):
        self.activate_document()
        value = self.hwp.GetTextFile("HWPML2X", "")
        if not isinstance(value, str) or not value: raise HancomError("HWPML verification export failed")
        return value

    def snapshot(self): return snapshot(self.xml())

    def controls(self, kind):
        result, ctrl = [], self.hwp.HeadCtrl
        while ctrl:
            if ctrl.CtrlID == kind: result.append(ctrl)
            ctrl = ctrl.Next
        return result

    def table(self, index):
        integer(index, "table_index")
        tables = self.snapshot()["tables"]
        if index >= len(tables): raise HancomError(f"Unknown table_index: {index}")
        return tables[index]

    @staticmethod
    def cell(table, row, col):
        integer(row, "row"); integer(col, "col")
        for cell in table["cells"]:
            if cell["row"] <= row < cell["row"] + cell["row_span"] and cell["col"] <= col < cell["col"] + cell["col_span"]:
                return cell
        raise HancomError(f"Unknown cell: ({row}, {col})")

    def move_to_cell(self, index, row, col):
        table = self.table(index)
        cell = self.cell(table, row, col)
        controls = self.controls("tbl")
        if len(controls) != len(self.snapshot()["tables"]): raise HancomError("COM/HWPML table inventories disagree")
        ctrl = controls[index]
        self.hwp.HAction.Run("Cancel")
        self.check(self.hwp.SetPosBySet(ctrl.GetAnchorPos(0)), "table anchor")
        self.run("SelectCtrlReverse"); self.run("ShapeObjTableSelCell"); self.run("Cancel")
        for _ in range(table["cells"].index(cell)): self.run("TableRightCell")
        parent = self.hwp.ParentCtrl
        if parent is None or parent.CtrlID != "tbl" or parent.GetCtrlInstID() != ctrl.GetCtrlInstID():
            raise HancomError("Navigation left the requested table")
        return cell

    def target(self, op):
        if "target_table" in op: raise HancomError("COM requires numeric table_index")
        index = integer(op.get("table_index", 0), "table_index")
        table = self.table(index)
        search = op.get("match_text", op.get("find_text"))
        if search is not None:
            if not isinstance(search, str) or not search: raise HancomError("Text selector must be nonempty")
            matches = [c for c in table["cells"] if search in c["text"]]
            if len(matches) != 1: raise HancomError(f"Text selector must match once; found {len(matches)}")
            row, col = matches[0]["row"], matches[0]["col"]
        else:
            row, col = op.get("row", op.get("start_row", 0)), op.get("col", op.get("start_col", 0))
        return index, self.move_to_cell(index, row, col)

    def set_parameters(self, pset, parameters):
        if not isinstance(parameters, dict): raise HancomError("parameters must be an object")
        allowed = set()
        try:
            set_id = {"Table": "ShapeObject", "ParagraphShape": "ParaShape"}.get(str(pset.SetID), str(pset.SetID))
            wrapper = getattr(self.hwp.HParameterSet, "H" + set_id)
            info = wrapper._oleobj_.GetTypeInfo()
            for index in range(info.GetTypeAttr().cFuncs):
                func = info.GetFuncDesc(index)
                if func.invkind in (2, 4, 8):
                    name = info.GetNames(func.memid)[0]
                    if name != "HSet": allowed.update((name, name[0].upper() + name[1:]))
        except AttributeError:
            pass
        for key, value in parameters.items():
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", key): raise HancomError("Invalid parameter name")
            # SetItem accepts invented names and Execute can silently ignore
            # them. Check the runtime's declarations/defaults before writing.
            if key not in allowed and not pset.ItemExist(key): raise HancomError(f"Unknown parameter {pset.SetID}.{key}; inspect hancom_capabilities first")
            if isinstance(value, dict):
                if set(value) != {"$set", "items"}: raise HancomError("Nested parameter requires $set and items")
                self.set_parameters(pset.CreateItemSet(key, value["$set"]), value["items"])
            elif isinstance(value, list):
                array = pset.CreateItemArray(key, len(value))
                for i, item in enumerate(value):
                    if isinstance(item, (dict, list)): raise HancomError("Parameter arrays accept scalar values")
                    array.SetItem(i, item)
            elif value is None or isinstance(value, (str, int, float, bool)):
                if isinstance(value, float) and not math.isfinite(value): raise HancomError("Parameter must be finite")
                self.check(pset.SetItem(key, value), f"parameter {key}")
            else: raise HancomError(f"Unsupported parameter: {key}")

    def execute_action(self, name, parameters=None):
        self.activate_document()
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", name): raise HancomError("Invalid action identifier")
        if parameters is None: return self.run(name)
        if name == "InsertText" and isinstance(parameters.get("Text"), str):
            parameters = dict(parameters)
            # Hancom InsertText discards bare LF. Jobs use LF; the runtime
            # requires CRLF to create paragraph breaks inside the current cell.
            parameters["Text"] = parameters["Text"].replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r\n")
        action = self.hwp.CreateAction(name)
        if action is None: raise HancomError(f"Unknown action: {name}")
        pset = action.CreateSet()
        if pset is None: raise HancomError(f"{name} has no parameter set; omit parameters")
        self.check(action.GetDefault(pset), f"GetDefault {name}")
        self.set_parameters(pset, parameters)
        return self.check(action.Execute(pset), name)

    def replace_cell_text(self, index, cell, text, append=False):
        if not isinstance(text, str): raise HancomError("text must be a string")
        self.move_to_cell(index, cell["row"], cell["col"])
        if append: self.run("MoveListEnd")
        else:
            self.run("MoveListBegin")
            if cell["text"]: self.run("SelectAll"); self.run("Delete")
        if text: self.execute_action("InsertText", {"Text": text})
        expected = (cell["text"] if append else "") + text
        if self.cell(self.table(index), cell["row"], cell["col"])["text"] != expected:
            raise HancomError("Cell text readback failed")

    def color(self, text):
        if not isinstance(text, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", text): raise HancomError("color must be #RRGGBB")
        return self.hwp.RGBColor(*(int(text[i:i+2],16) for i in (1,3,5)))

    def style(self, index, cell, op):
        if set(op) & {"border_fill", "char_pr", "para_pr"}: raise HancomError("HWPX reference IDs are not COM parameter values")
        self.move_to_cell(index, cell["row"], cell["col"]); self.run("TableCellBlock")
        text = dict(op.get("text_style", {}))
        text.update({v: op[k] for k,v in {"size_pt":"size", "font_size_pt":"size", "bold":"bold", "letter_spacing":"letter_spacing", "color":"color"}.items() if k in op})
        params = {}
        for key, value in text.items():
            if key in ("font", "stretch", "letter_spacing"):
                prefix = {"font":"FaceName", "stretch":"Ratio", "letter_spacing":"Spacing"}[key]
                params.update({prefix+lang:value for lang in ("Hangul","Latin","Hanja","Japanese","Other","Symbol","User")})
            elif key == "size": params["Height"] = round(float(value)*100)
            elif key in ("color","background"): params[{"color":"TextColor","background":"ShadeColor"}[key]] = self.color(value)
            elif key in ("bold","italic","underline","strike","superscript","subscript"):
                params[{"bold":"Bold","italic":"Italic","underline":"UnderlineType","strike":"StrikeOutType","superscript":"SuperScript","subscript":"SubScript"}[key]] = int(bool(value))
            else: raise HancomError(f"Unsupported COM text style: {key}")
        if params:
            self.execute_action("CharShape", params)
            if any(self.hwp.CharShape.Item(k) != v for k, v in params.items()): raise HancomError("Character style readback failed")
        para = dict(op.get("paragraph_style", {}))
        if "align" in op: para["align"] = op["align"]
        params = {}
        for key,value in para.items():
            if key == "align": params["AlignType"] = {"JUSTIFY":0,"LEFT":1,"RIGHT":2,"CENTER":3,"DISTRIBUTE":4,"DISTRIBUTE_SPACE":5}[value.upper()]
            elif key == "line_type": params["LineSpacingType"] = {"PERCENT":0,"FIXED":1,"BETWEEN_LINES":2,"AT_LEAST":3}[value.upper()]
            elif key in ("left","right","indent","before","after","line"):
                params[{"left":"LeftMargin","right":"RightMargin","indent":"Indentation","before":"PrevSpacing","after":"NextSpacing","line":"LineSpacing"}[key]] = round(float(value)*(1 if key == "line" else 100))
            else: raise HancomError(f"Unsupported COM paragraph style: {key}")
        if params:
            self.execute_action("ParagraphShape",params)
            if any(self.hwp.ParaShape.Item(k) != v for k, v in params.items()): raise HancomError("Paragraph style readback failed")
        border, params = op.get("border",{}), {}
        for side in ("Left","Right","Top","Bottom"):
            spec = border.get(side.lower(), border)
            if isinstance(spec,str): spec={"type":spec}
            for key,value in spec.items():
                if key == "type": params["BorderType"+side]=self.hwp.HwpLineType(value)
                elif key == "width": params["BorderWidth"+side]=self.hwp.HwpLineWidth(str(value).replace(" ",""))
                elif key == "color": params["BorderColor"+side]=self.color(value)
        if "background" in op:
            params["FillAttr"]={"$set":"DrawFillAttr","items":{"Type":1,"WinBrushFaceColor":self.color(op["background"]),"WinBrushHatchColor":0}}
        if params: self.execute_action("CellBorderFill",params)
        if any(k in op for k in ("vertical_align","padding_mm","width_mm","height_mm")):
            shape = self.hwp.CellShape
            cellset = shape.Item("Cell")
            expected_cell = {}
            if "vertical_align" in op: expected_cell["VertAlign"] = {"TOP":0,"CENTER":1,"BOTTOM":2}[op["vertical_align"].upper()]
            if "padding_mm" in op:
                if len(op["padding_mm"]) != 4: raise HancomError("padding_mm needs four values")
                expected_cell["HasMargin"] = 1
                for side,value in zip(("Left","Right","Top","Bottom"),op["padding_mm"]): expected_cell["Margin"+side] = self.hwp.MiliToHwpUnit(float(value))
            for key,parameter in (("width_mm","Width"),("height_mm","Height")):
                if key in op: expected_cell[parameter] = self.hwp.MiliToHwpUnit(float(op[key]))
            self.set_parameters(cellset, expected_cell)
            shape.SetItem("Cell", cellset)
            self.hwp.CellShape=shape
            if any(self.hwp.CellShape.Item("Cell").Item(k) != v for k, v in expected_cell.items()): raise HancomError("Cell property readback failed")
        self.run("Cancel")

    def merge(self, op):
        index, cell = self.target(op)
        table = self.table(index)
        texts = op.get("find_texts", op.get("cell_texts"))
        if "from_text" in op and "to_text" in op: texts=[op["from_text"],op["to_text"]]
        if texts:
            matches=[]
            for text in texts:
                found=[c for c in table["cells"] if text in c["text"]]
                if len(found)!=1: raise HancomError("Each merge selector must match exactly once")
                matches.extend(found)
            row,col=min(c["row"] for c in matches),min(c["col"] for c in matches)
            er,ec=max(c["row"]+c["row_span"] for c in matches),max(c["col"]+c["col_span"] for c in matches)
        else:
            row,col=cell["row"],cell["col"]
            er,ec=row+integer(op.get("row_span",1),"row_span"),col+integer(op.get("col_span",1),"col_span")
        if not (row<er<=table["rows"] and col<ec<=table["cols"]): raise HancomError("Invalid merge rectangle")
        selected=[c for c in table["cells"] if c["row"]<er and c["row"]+c["row_span"]>row and c["col"]<ec and c["col"]+c["col_span"]>col]
        if any(c["row"]<row or c["col"]<col or c["row"]+c["row_span"]>er or c["col"]+c["col_span"]>ec for c in selected): raise HancomError("Merge cuts through an existing merged cell")
        if len(selected)>1:
            positions={}
            for c in table["cells"]:
                self.move_to_cell(index,c["row"],c["col"]); positions[self.hwp.GetPos()[0]]=c
            self.move_to_cell(index,row,col); self.run("TableCellBlock"); self.run("TableCellBlockExtend")
            for action,axis,goal in (("TableRightCell","col",ec-1),("TableLowerCell","row",er-1)):
                for _ in table["cells"]:
                    current=positions.get(self.hwp.GetPos()[0])
                    if current and current[axis]<=goal<current[axis]+current[axis+"_span"]: break
                    self.run(action)
                else: raise HancomError("Cannot extend merge selection")
            self.run("TableMergeCell"); self.hwp.HAction.Run("Cancel")
        after=self.table(index); merged=self.cell(after,row,col)
        if merged["row_span"]!=er-row or merged["col_span"]!=ec-col or len(after["cells"])!=len(table["cells"])-len(selected)+1: raise HancomError("Merge structure readback failed")
        if "text" in op: self.replace_cell_text(index,merged,op["text"])
        self.style(index,merged,op)

    def apply_operations_on_hwp(self, operations):
        self.activate_document()
        if not isinstance(operations,list): raise HancomError("operations must be an array")
        from .automation import _edit, _keys
        from .geometry import DocumentModel, PageLayout
        for op in operations:
            if not isinstance(op, dict): raise HancomError("operation must be an object")
            if op.get("op") in ("insert_image", "insert_photo"):
                _keys(op, ("op", "table_index", "row", "col", "match_text", "find_text", "image_path", "image_bytes", "width_mm", "height_mm"), "image operation")
            else:
                _edit(DocumentModel([], PageLayout()), [op])
        for number,op in enumerate(operations):
            before=self.snapshot(); before_state=verification_snapshot(self.xml()); kind=op.get("op")
            if kind=="merge_cells": self.merge(op)
            elif kind in ("insert_image", "insert_photo"):
                index, cell = self.target(op)
                self.replace_cell_text(index, cell, "")
                self.move_to_cell(index, cell["row"], cell["col"])
                with tempfile.TemporaryDirectory(prefix="narae-picture-") as temporary:
                    if op.get("image_path"):
                        picture_path = Path(op["image_path"]).resolve()
                    elif isinstance(op.get("image_bytes"), bytes):
                        picture_path = Path(temporary) / "picture.png"
                        picture_path.write_bytes(op["image_bytes"])
                    else: raise HancomError("insert_image requires image_path or image_bytes")
                    if not picture_path.is_file(): raise HancomError("Image file does not exist")
                    picture = self.hwp.InsertPicture(str(picture_path), True, 1, False, False, 0,
                                                     float(op.get("width_mm", 30)), float(op.get("height_mm", 30)))
                    if picture is None: raise HancomError("InsertPicture failed")
                    properties = picture.Properties
                    properties.SetItem("TreatAsChar", 1)
                    picture.Properties = properties
            elif kind in ("set_cell_text","set_cell_style","split_cell"):
                index,cell=self.target(op)
                if kind=="set_cell_text": self.replace_cell_text(index,cell,op.get("text",""),op.get("append",False))
                if kind=="split_cell":
                    rows,cols=integer(op.get("rows",1),"rows"),integer(op.get("cols",1),"cols")
                    if not rows or not cols or rows*cols<=1: raise HancomError("Split must create at least two cells")
                    texts = op.get("texts", [])
                    if texts and (not isinstance(texts, list) or len(texts) != rows * cols or any(not isinstance(t, str) for t in texts)): raise HancomError("Split texts require one string per resulting cell")
                    self.run("TableCellBlock"); self.execute_action("TableSplitCell",{"Rows":rows,"Cols":cols,"Merge":0}); self.hwp.HAction.Run("Cancel")
                    after=self.table(index)
                    if len(after["cells"])!=len(before["tables"][index]["cells"])+rows*cols-1: raise HancomError("Split structure readback failed")
                    if texts:
                        # Splitting inserts logical grid lines. Hancom can
                        # renumber every list, so old list IDs are not stable.
                        original = before["tables"][index]
                        end_row = cell["row"] + cell["row_span"] + after["rows"] - original["rows"]
                        end_col = cell["col"] + cell["col_span"] + after["cols"] - original["cols"]
                        new_cells = [c for c in after["cells"] if cell["row"] <= c["row"] < end_row and cell["col"] <= c["col"] < end_col]
                        if len(new_cells) != len(texts): raise HancomError("Could not identify every split cell")
                        for target, text in zip(new_cells, texts): self.replace_cell_text(index, target, text)
                else:
                    table=self.table(index); scope=op.get("apply_to","cell")
                    scope = {"col": "column", "all": "table"}.get(scope, scope)
                    scopes={"cell":[self.cell(table,cell["row"],cell["col"])],"table":table["cells"],"header":[c for c in table["cells"] if c["row"]==0],"row":[c for c in table["cells"] if c["row"]==cell["row"]],"column":[c for c in table["cells"] if c["col"]==cell["col"]]}
                    if scope not in scopes: raise HancomError("Unknown apply_to")
                    for c in scopes[scope]: self.style(index,c,op)
            elif kind in ("insert_row","delete_row"):
                index=integer(op.get("table_index",0),"table_index"); table=self.table(index); row=integer(op.get("row_index"),"row_index")
                if "target_table" in op: raise HancomError("Use numeric table_index")
                if row>table["rows"] or (kind=="delete_row" and (row==table["rows"] or table["rows"]==1)): raise HancomError("Invalid row_index")
                self.move_to_cell(index,min(row,table["rows"]-1),0)
                if kind=="insert_row": self.execute_action("TableInsertLowerRow" if row==table["rows"] else "TableInsertUpperRow",{"Count":1})
                else: self.execute_action("TableDeleteRow",{"Type":0})
                self.hwp.HAction.Run("Cancel")
                if self.table(index)["rows"]!=table["rows"]+(1 if kind=="insert_row" else -1): raise HancomError("Row count readback failed")
                if "height_mm" in op:
                    for c in self.table(index)["cells"]:
                        if c["row"]==row: self.style(index,c,{"height_mm":op["height_mm"]})
            elif kind in ("modify_table", "modify_image"):
                is_table = kind == "modify_table"
                index = integer(op.get("table_index" if is_table else "image_index", 0), "object_index")
                if is_table:
                    ctrls = self.controls("tbl")
                else:
                    picture_ids = [p.find("SHAPEOBJECT").get("InstId") for p in ET.fromstring(self.xml()).findall(".//PICTURE")]
                    by_id = {c.GetCtrlInstID(): c for c in self.controls("gso")}
                    if any(pid not in by_id for pid in picture_ids): raise HancomError("COM/HWPML picture inventories disagree")
                    ctrls = [by_id[pid] for pid in picture_ids]
                    if "replace_image_path" in op or "replace_image_data" in op: raise HancomError("Use a native picture replacement action or InsertPicture/DeleteCtrl for image data replacement")
                if index >= len(ctrls): raise HancomError("Unknown object_index")
                ctrl = ctrls[index]; params = {}
                if "target_table" in op or "target_image" in op: raise HancomError("Use numeric object indices")
                if "treat_as_char" in op: params["TreatAsChar"]=int(bool(op["treat_as_char"]))
                if "align" in op: params["HorzAlign"]=self.hwp.HAlign(op["align"])
                for key,parameter in (("width_mm","Width"),("height_mm","Height")):
                    if key in op: params[parameter]=self.hwp.MiliToHwpUnit(float(op[key]))
                for key,prefix in (("out_margin_mm","OutsideMargin"),("in_margin_mm","CellMargin")):
                    if key in op:
                        if len(op[key])!=4: raise HancomError("Margins need four values")
                        params.update({prefix+s:self.hwp.MiliToHwpUnit(float(v)) for s,v in zip(("Left","Right","Top","Bottom"),op[key])})
                properties=ctrl.Properties; self.set_parameters(properties,params); ctrl.Properties=properties
                if any(ctrl.Properties.Item(k)!=v for k,v in params.items()): raise HancomError("Table property readback failed")
            else: raise HancomError(f"Unsupported Hancom operation: {kind}; use the raw Hancom action/method interface")
            self.receipts.append({"index":number,"op":kind,"content_changed":before_state!=verification_snapshot(self.xml()),"runtime_checked":True})
        return self.receipts

    def save_verified(self,target):
        expected=verification_snapshot(self.xml())
        self.save_doc(target)
        # Clear can fault in Hancom 2024 when an automation controller is
        # reused across documents. A unique copy forces an actual disk load
        # without relying on Clear or an already-open document cache.
        target = Path(target).resolve()
        handle, name = tempfile.mkstemp(prefix=".narae-readback-", suffix=target.suffix, dir=target.parent)
        os.close(handle)
        readback = Path(name)
        try:
            shutil.copyfile(target, readback)
            self.open_doc(readback)
            if verification_snapshot(self.xml())!=expected: raise HancomError("Saved text, structure or formatting differs from edited Hancom document")
        finally:
            self.open_doc(target)
            readback.unlink(missing_ok=True)
        return {"uses_hancom_runtime":True,"hancom_version":self.hwp.Version,"operation_results":self.receipts,"saved_content_verified":True,
                "verification_scope":"HWPML body with resolved character, paragraph, style and border-fill references", "visual_parity_verified":False}


def patch_hwp_advanced(source_file,target_file,operations):
    source, target = Path(source_file).resolve(), Path(target_file).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    handle, temporary = tempfile.mkstemp(prefix=".narae-hancom-", suffix=target.suffix, dir=target.parent)
    os.close(handle)
    staged = Path(temporary)
    try:
        with HancomBridge() as bridge:
            bridge.open_doc(source); bridge.apply_operations_on_hwp(operations)
            result = bridge.save_verified(staged)
        if hashlib.sha256(source.read_bytes()).hexdigest() != source_hash: raise HancomError("Input changed during Hancom editing")
        os.replace(staged, target)
        return result
    finally:
        staged.unlink(missing_ok=True)
