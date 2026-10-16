"""Inline table flow and the native meaning of HWPX paragraph margins."""
from dataclasses import replace
from pathlib import Path
import os,sys,tempfile,unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'1_나래엔진/narae-hangul/scripts/engine'))
from hangeul.formats import (Paragraph,ParaStyle,Run,TextStyle,Table,Cell,Border,
    DocumentModel,save_hwp,save_hwpx,load_document)
from hangeul.geometry import ObjectPosition
from hangeul.viewer import application,Document,compare


class InlineSpacingTests(unittest.TestCase):
    def test_direct_hwpx_margins_survive_preserved_edits(self):
        from hangeul.hwpx_native import patch_hwpx_source
        before=ParaStyle(left=17,right=7,indent=-4,before=2,after=3)
        after=replace(before,left=21,right=9,indent=-6,before=4,after=5)
        fields=('left','right','indent','before','after')
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'original.hwpx'
            save_hwpx(DocumentModel([Paragraph([Run('문단 여백')],style=before)]),source)
            model=load_document(source).model
            self.assertEqual(tuple(getattr(before,k) for k in fields),
                tuple(getattr(model.blocks[0].style,k) for k in fields))
            model.blocks[0].style=replace(model.blocks[0].style,**{k:getattr(after,k) for k in fields})
            target=Path(folder)/'edited.hwpx'
            self.assertTrue(patch_hwpx_source(model,target))
            actual=load_document(target).model.blocks[0].style
            self.assertEqual(tuple(getattr(after,k) for k in fields),tuple(getattr(actual,k) for k in fields))

    @staticmethod
    def fixture():
        blocks=[];index=0
        for affects in (False,True):
            for line in (100,150,160,200):
                for after in (0,3):
                    anchor=Paragraph(style=ParaStyle(line=line,after=after,page_break=bool(index)),
                        default_style=TextStyle(size=15),source_index=index)
                    table=Table(1,1,[Cell(0,0,blocks=[Paragraph(default_style=TextStyle(size=11))],
                        width=100,height=44,padding=(0,0,0,0),borders=(Border('NONE'),)*4)],
                        width=100,height=44,row_heights=[44],column_widths=[100],
                        borders=(Border('NONE'),)*4,padding=(0,0,0,0),source_index=index,
                        position=ObjectPosition(affect_line_spacing=affects))
                    blocks.extend([anchor,table,Paragraph([Run('A가',TextStyle(font='맑은 고딕',size=13))])])
                    index+=1
        return DocumentModel(blocks)

    @unittest.skipUnless(os.environ.get('NARAE_RUN_HANCOM_TESTS')=='1','requires installed Hancom/fonts')
    def test_inline_table_spacing_matches_native_complete_pages(self):
        application()
        with tempfile.TemporaryDirectory() as folder:
            for ext,save in (('hwp',save_hwp),('hwpx',save_hwpx)):
                source=Path(folder)/('inline.'+ext);save(self.fixture(),source)
                with Document(source) as own,Document(source,'hancom') as native:
                    self.assertEqual(16,own.count)
                    result=compare(own,native)
                    self.assertTrue(result['match'],(ext,result['pages']))

    @unittest.skipUnless(os.environ.get('NARAE_RUN_HANCOM_TESTS')=='1','requires installed Hancom')
    def test_direct_and_native_default_margin_edits_have_same_native_units(self):
        from hangeul.hwp_automation_bridge import HancomBridge
        from hangeul.hwpx_native import patch_hwpx_source
        before=ParaStyle(left=17,right=7,indent=-4,before=2,after=3)
        after=replace(before,left=21,right=9,indent=-6,before=4,after=5)
        fields=('left','right','indent','before','after')
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'direct.hwpx'
            save_hwpx(DocumentModel([Paragraph([Run('문단 여백')],style=before)]),source)
            with HancomBridge() as bridge:
                native=Path(folder)/'native-default.hwpx';bridge.open_doc(source)
                bridge.check(bridge.hwp.SaveAs(str(native),'HWPX',''),'native margin conversion')
                for label,path in (('direct',source),('versioned',native)):
                    model=load_document(path).model
                    self.assertEqual(tuple(getattr(before,k) for k in fields),
                        tuple(getattr(model.blocks[0].style,k) for k in fields),label)
                    model.blocks[0].style=replace(model.blocks[0].style,**{k:getattr(after,k) for k in fields})
                    edited=Path(folder)/(label+'-edited.hwpx')
                    self.assertTrue(patch_hwpx_source(model,edited))
                    bridge.open_doc(edited);converted=Path(folder)/(label+'-verified.hwpx')
                    bridge.check(bridge.hwp.SaveAs(str(converted),'HWPX',''),'verify edited native margins')
                    actual=load_document(converted).model.blocks[0].style
                    self.assertEqual(tuple(getattr(after,k) for k in fields),
                        tuple(getattr(actual,k) for k in fields),label)


if __name__=='__main__':unittest.main()
