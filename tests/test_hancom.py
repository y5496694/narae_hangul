"""Portable regression checks plus opt-in tests against installed Hancom.

python -m unittest discover -s tests -v
NARAE_RUN_HANCOM_TESTS=1 enables real COM tests (approved access module required).
"""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "1_나래엔진/narae-hangul/scripts/engine"))
from hangeul.hwp_automation_bridge import HancomBridge, HancomError, snapshot
from hangeul.automation import run_job
from hangeul.formats import load_document, Table


class PortableRegressionTests(unittest.TestCase):
    def test_false_action_cannot_report_success(self):
        class Actions:
            def Run(self, name): return False
        class Hwp:
            HAction = Actions()
        bridge = object.__new__(HancomBridge)
        bridge.hwp = Hwp()
        with self.assertRaises(HancomError): bridge.run("TableInsertUpperRow")

    def test_unknown_parameter_is_rejected_before_execution(self):
        class PSet:
            SetID = "ParaShape"
            def ItemExist(self, name): return False
            def SetItem(self, name, value): raise AssertionError("must not write invented parameter")
        class Hwp:
            HParameterSet = object()
        bridge = object.__new__(HancomBridge)
        bridge.hwp = Hwp()
        with self.assertRaises(HancomError): bridge.set_parameters(PSet(), {"Align": 2})

    def test_mixed_hwpx_job_keeps_both_edits(self):
        with tempfile.TemporaryDirectory() as folder:
            run_job({"action":"create","format":"hwpx","output":"in.hwpx","blocks":[
                {"type":"paragraph","text":"original title"},
                {"type":"table","columns_mm":[30,30],"rows":[["A","B"],["C","D"]]}]}, folder)
            result = run_job({"action":"edit","input":"in.hwpx","output":"out.hwpx","operations":[
                {"op":"replace_text","find":"original","replace":"updated","expected":1},
                {"op":"set_cell_text","table_index":0,"row":0,"col":1,"text":"changed cell"}]},folder)
            texts = [p["text"] for p in result["paragraphs"]]
            self.assertIn("updated title",texts)
            self.assertIn("changed cell",texts)

    def test_invalid_target_leaves_existing_output_and_input_intact(self):
        with tempfile.TemporaryDirectory() as folder:
            run_job({"action":"create","format":"hwpx","output":"in.hwpx","blocks":[
                {"type":"table","columns_mm":[30],"rows":[["A"]]}]},folder)
            source=Path(folder)/"in.hwpx"; target=Path(folder)/"out.hwpx"
            before=source.read_bytes(); target.write_bytes(b"existing output")
            with self.assertRaises(ValueError):
                run_job({"action":"edit","input":"in.hwpx","output":"out.hwpx","overwrite_output":True,
                         "operations":[{"op":"set_cell_text","table_index":99,"row":0,"col":0,"text":"X"}]},folder)
            self.assertEqual(before,source.read_bytes())
            self.assertEqual(b"existing output",target.read_bytes())

    def test_negative_index_is_not_python_last_item(self):
        with tempfile.TemporaryDirectory() as folder:
            run_job({"action":"create","format":"hwpx","output":"in.hwpx","blocks":[
                {"type":"table","columns_mm":[30],"rows":[["A"]]}]},folder)
            with self.assertRaises(ValueError):
                run_job({"action":"edit","input":"in.hwpx","output":"out.hwpx",
                         "operations":[{"op":"set_cell_text","table_index":-1,"row":0,"col":0,"text":"X"}]},folder)


@unittest.skipUnless(os.name=="nt" and os.environ.get("NARAE_RUN_HANCOM_TESTS")=="1", "real Hancom tests are opt-in")
class NativeRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory(prefix="narae-hancom-tests-")
        cls.folder=Path(cls.temporary.name)
        job=json.loads((ROOT/"3_형식템플릿/11_회의록_협의록_표준양식/meeting_minutes.json").read_text(encoding="utf-8-sig"))
        job.update(output=str(cls.folder/"fixture.hwpx"),preview={},overwrite_output=True)
        run_job(job,cls.folder)
        with HancomBridge() as bridge:
            bridge.open_doc(cls.folder/"fixture.hwpx")
            bridge.save_verified(cls.folder/"fixture.hwp")
        cls.source=cls.folder/"fixture.hwp"
        cls.source_hash=hashlib.sha256(cls.source.read_bytes()).hexdigest()

    @classmethod
    def tearDownClass(cls): cls.temporary.cleanup()

    def check_operation(self,operation):
        with HancomBridge() as bridge:
            bridge.open_doc(self.source)
            before=bridge.snapshot()
            bridge.apply_operations_on_hwp([operation])
            after=bridge.snapshot()
            receipt=bridge.save_verified(self.folder/"result.hwp")
            self.assertTrue(receipt["saved_content_verified"])
            for index,table in enumerate(before["tables"]):
                if index!=operation.get("table_index",0): self.assertEqual(table,after["tables"][index])
        self.assertEqual(self.source_hash,hashlib.sha256(self.source.read_bytes()).hexdigest())
        return load_document(self.folder/"result.hwp").model

    def test_cell_in_each_table(self):
        for index in range(3):
            with self.subTest(table_index=index):
                self.check_operation({"op":"set_cell_text","table_index":index,"row":0,"col":1,"text":"시험\n두 번째 줄"})

    def test_other_document_and_com_lifetime_are_preserved(self):
        for first_close in ("second", "first"):
            with self.subTest(first_close=first_close):
                first=HancomBridge(); second=None
                try:
                    first.execute_action("InsertText",{"Text":"NARAE_SESSION_SENTINEL"})
                    saved=self.folder/("session-"+first_close+".hwp")
                    first.save_doc(saved)
                    second=HancomBridge()
                    self.assertNotIn("NARAE_SESSION_SENTINEL",second.xml())
                    second.open_doc(saved)
                    second.execute_action("InsertText",{"Text":"NARAE_SECOND_SESSION"})
                    if first_close=="second":
                        second.close();second=None
                        self.assertIn("NARAE_SESSION_SENTINEL",first.xml())
                        self.assertNotIn("NARAE_SECOND_SESSION",first.xml())
                    else:
                        first.close();first=None
                        self.assertIn("NARAE_SECOND_SESSION",second.xml())
                finally:
                    if second is not None: second.close()
                    if first is not None: first.close()

    def test_clear_and_append(self):
        self.check_operation({"op":"set_cell_text","table_index":0,"row":0,"col":1,"text":""})
        self.check_operation({"op":"set_cell_text","table_index":0,"row":0,"col":1,"text":" 추가","append":True})

    def test_merge_with_and_without_text(self):
        for text in (None,"병합 시험"):
            with self.subTest(text=text):
                op={"op":"merge_cells","table_index":0,"start_row":0,"start_col":0,"row_span":1,"col_span":2}
                if text is not None: op["text"]=text
                self.check_operation(op)

    def test_rectangle_merge(self):
        self.check_operation({"op":"merge_cells","table_index":1,"start_row":0,"start_col":0,"row_span":2,"col_span":2,"text":"직사각형"})

    def test_insert_append_delete_row(self):
        for kind,row in (("insert_row",0),("insert_row",2),("insert_row",4),("delete_row",2)):
            with self.subTest(kind=kind,row=row): self.check_operation({"op":kind,"table_index":1,"row_index":row})

    def test_split(self):
        self.check_operation({"op":"split_cell","table_index":1,"row":0,"col":0,"rows":1,"cols":2,"texts":["왼쪽","오른쪽"]})

    def test_split_two_rows_and_columns(self):
        self.check_operation({"op":"split_cell","table_index":1,"row":0,"col":0,"rows":2,"cols":2,"texts":["A","B","C","D"]})

    def test_saved_formatting_matches_requested_values(self):
        model=self.check_operation({"op":"set_cell_style","table_index":1,"row":0,"col":0,"font_size_pt":15,
                                   "bold":True,"align":"RIGHT","background":"#FFEEEE","vertical_align":"BOTTOM","padding_mm":[2,2,1,1]})
        table=[b for b in model.blocks if isinstance(b,Table)][1]
        cell=next(c for c in table.cells if c.row==0 and c.col==0)
        self.assertEqual("RIGHT",cell.blocks[0].style.align)
        self.assertEqual("BOTTOM",cell.vertical_align)
        self.assertEqual("#ffeeee",cell.background.lower())
        self.assertTrue(cell.blocks[0].runs[0].style.bold)
        self.assertEqual(15,cell.blocks[0].runs[0].style.size)
        for expected,actual in zip((2,2,1,1),cell.padding): self.assertAlmostEqual(expected,actual*25.4/72,places=2)

    def test_table_properties(self):
        self.check_operation({"op":"modify_table","table_index":1,"treat_as_char":True,"out_margin_mm":[1,1,2,2]})

    def test_cli_job_mixed_edits(self):
        result=run_job({"action":"edit","input":str(self.source),"output":str(self.folder/"mixed.hwp"),"overwrite_output":True,
                        "operations":[{"op":"replace_text","find":"회의록","replace":"검증기록","expected":1},
                                      {"op":"set_cell_text","table_index":1,"row":0,"col":1,"text":"혼합 편집"}]},self.folder)
        self.assertTrue(result["uses_hancom_runtime"])
        texts=[p["text"] for p in result["paragraphs"]]
        self.assertTrue(any("검증기록" in t for t in texts))
        self.assertIn("혼합 편집",texts)

    def test_raw_actions_fields_and_nested_sets(self):
        result=run_job({"action":"hancom","output":str(self.folder/"raw.hwp"),"overwrite_output":True,"operations":[
            {"op":"hancom_action","action":"PageSetup","parameters":{"PageDef":{"$set":"PageDef","items":{"LeftMargin":5669,"RightMargin":5669}}}},
            {"op":"hancom_action","action":"InsertText","parameters":{"Text":"본문 검증"}},
            {"op":"hancom_method","name":"CreateField","args":["","","test_field"]},
            {"op":"hancom_method","name":"PutFieldText","args":["test_field","누름틀 검증"]}],
            "verify":[{"kind":"text_contains","text":"본문 검증"},{"kind":"field_text","field":"test_field","expected":"누름틀 검증"}]},self.folder)
        self.assertTrue(result["requested_effects_verified"])
        page=load_document(self.folder/"raw.hwp").model.page
        self.assertAlmostEqual(20,page.left_pt*25.4/72,places=2)

    def test_raw_image_object_references_and_resize(self):
        from PySide6.QtGui import QImage
        image=QImage(24,24,QImage.Format.Format_RGB32); image.fill(0x336699)
        image_path=self.folder/"picture.png"; self.assertTrue(image.save(str(image_path)))
        picture=self.folder/"picture.hwp"
        run_job({"action":"hancom","output":str(picture),"operations":[
            {"op":"hancom_method","name":"InsertPicture","args":[str(image_path),True,1,False,False,0,20,20],"store":"picture"},
            {"op":"hancom_get","object":"picture","name":"Properties","store":"props"},
            {"op":"hancom_method","object":"props","name":"SetItem","args":["TreatAsChar",True]},
            {"op":"hancom_set","object":"picture","name":"Properties","value":{"$ref":"props"}}],
            "verify":[{"kind":"control_count","ctrl_id":"gso","expected":1}]},self.folder)
        output=self.folder/"resized.hwp"
        run_job({"action":"hancom","input":str(picture),"output":str(output),"operations":[
            {"op":"modify_image","image_index":0,"width_mm":30,"height_mm":25,"treat_as_char":True}],
            "verify":[{"kind":"control_count","ctrl_id":"gso","expected":1}]},self.folder)
        from hangeul.automation import _walk_model
        from hangeul.formats import Paragraph
        runs=[r for _,p in _walk_model(load_document(output).model) if isinstance(p,Paragraph) for r in p.runs if r.image]
        self.assertEqual(1,len(runs))
        self.assertEqual(image_path.read_bytes(),runs[0].image)
        self.assertAlmostEqual(30,runs[0].width*25.4/96,places=2)
        self.assertAlmostEqual(25,runs[0].height*25.4/96,places=2)

    def test_equation_action(self):
        result=run_job({"action":"hancom","output":str(self.folder/"equation.hwp"),"operations":[
            {"op":"hancom_action","action":"EquationCreate","parameters":{"String":"a^2+b^2=c^2","BaseUnit":1000}}],
            "verify":[{"kind":"control_count","ctrl_id":"eqed","expected":1}]},self.folder)
        self.assertTrue(result["requested_effects_verified"])

    def test_natural_commander_uses_checked_native_route(self):
        from hangeul.table_ai_tools import execute_ai_table_command
        result=execute_ai_table_command(self.source,"2열 전체 글자색 #FF0000으로 해줘",output_path=self.folder/"commander.hwp")
        self.assertTrue(result["success"],result)
        self.assertTrue(result["saved_content_verified"])
        table=[b for b in load_document(self.folder/"commander.hwp").model.blocks if isinstance(b,Table)][0]
        for cell in table.cells:
            if cell.col==1:
                for block in cell.blocks:
                    for run in block.runs:
                        if run.text: self.assertEqual("#ff0000",run.style.color.lower())

    def test_bad_action_and_parameter_preserve_existing_output(self):
        for op in ({"op":"hancom_action","action":"NoSuchActionNarae"},
                   {"op":"hancom_action","action":"ParagraphShape","parameters":{"Align":2}}):
            target=self.folder/"failed.hwp"; target.write_bytes(b"existing")
            with self.assertRaises(ValueError): run_job({"action":"hancom","output":str(target),"overwrite_output":True,"operations":[op]},self.folder)
            self.assertEqual(b"existing",target.read_bytes())


if __name__=="__main__": unittest.main()
