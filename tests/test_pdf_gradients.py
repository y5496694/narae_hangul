"""Gradient preservation and complete independent format-pair checks.

These portable checks do not establish Hancom parity. Native observations,
including the retained failed first runs, are recorded in validation.md.
"""
from pathlib import Path
from dataclasses import replace
import json,struct,sys,tempfile,unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'1_나래엔진/narae-hangul/scripts/engine'))
from hangeul.formats import (Table,Cell,Paragraph,ParaStyle,save_hwp,save_hwpx,
    load_document,model_to_data,model_from_data,_gradient_hwp)
from hangeul.geometry import DocumentModel,Border,GradientFill
from hangeul.viewer import application,Document,compare


def fixture():
    base=GradientFill('RADIAL',90,0,0,50,('#cddddd','#ffffff'))
    gradients=[replace(base,center_x=x,center_y=y) for x,y in ((0,0),(50,50),(100,100),(0,100),(100,0),(17,63))]
    gradients += [replace(base,step=n) for n in (0,1,2,3,49,51,100)]
    gradients += [replace(base,step_center=n) for n in (0,17,83,100)]
    gradients += [replace(base,colors=c) for c in (('#ffffff','#112233'),('#000000','#ff0000'),('#227788','#227788'))]
    none=Border('NONE');side=Border('SOLID',.4*72/25.4,'#000080');bottom=Border('SOLID',.5*72/25.4,'#000080')
    blocks=[]
    for g in gradients:
        blocks.extend((Paragraph(style=ParaStyle(page_break=bool(blocks))),Table(1,1,[Cell(0,0,width=172.37,height=40.73,
            padding=(0,)*4,borders=(none,side,none,bottom),gradient=g)],width=172.37,height=40.73)))
    return DocumentModel(blocks)


class PdfGradientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):application()

    def test_gradient_survives_both_formats_and_json(self):
        g=GradientFill('RADIAL',90,17,63,51,('#224466','#88aacc','#ffffff'),(0,40,100),83,37)
        model=DocumentModel([Table(1,1,[Cell(0,0,width=150,height=35,gradient=g)],width=150,height=35,gradient=g)])
        restored=model_from_data(json.loads(json.dumps(model_to_data(model))))
        self.assertEqual(g,restored.blocks[0].gradient)
        self.assertEqual(g,restored.blocks[0].cells[0].gradient)
        with tempfile.TemporaryDirectory() as folder:
            for ext,save in (('hwp',save_hwp),('hwpx',save_hwpx)):
                p=Path(folder)/('source.'+ext);save(restored,p)
                table=next(t for t in load_document(p).model.blocks if isinstance(t,Table))
                self.assertEqual(g,table.gradient);self.assertEqual(g,table.cells[0].gradient)

    def test_truncated_and_oversized_records_do_not_read_past_border_fill(self):
        header=bytes(32)+struct.pack('<I',4)+struct.pack('<B5i',2,90,0,0,50,2)
        self.assertIsNone(_gradient_hwp(header))
        self.assertIsNone(_gradient_hwp(header+struct.pack('<III',0x112233,0xffffff,1000000)))
        self.assertIsNone(_gradient_hwp(bytes(32)+struct.pack('<I',4)+struct.pack('<B5i',2,90,0,0,50,1000000)))

    def test_complete_independent_format_pair(self):
        with tempfile.TemporaryDirectory() as folder:
            first=Path(folder)/'source.hwp';second=Path(folder)/'source.hwpx'
            save_hwp(fixture(),first);save_hwpx(fixture(),second)
            with Document(first) as a,Document(second) as b:
                self.assertEqual(20,a.count);self.assertEqual(a.count,b.count)
                result=compare(a,b);self.assertTrue(result['match'],result['pages'])
                self.assertEqual(20,a.details['pdf_gradients']['processed_radial_fills'])
                self.assertFalse(a.details['pdf_gradients']['whole_document_parity_verified'])

    def test_unsupported_fill_keeps_visible_fallback_and_reason(self):
        for kind,alpha,reason in (('LINEAR',0,'gradient-linear'),('RADIAL',37,'gradient-alpha')):
            g=GradientFill(kind,90,50,50,50,('#112233','#ffffff'),alpha=alpha)
            model=DocumentModel([Table(1,1,[Cell(0,0,width=150,height=35,gradient=g)],width=150,height=35)])
            with tempfile.TemporaryDirectory() as folder:
                source=Path(folder)/'source.hwpx';save_hwpx(model,source)
                with Document(source) as d:
                    self.assertEqual({reason:1},d.details['pdf_gradients']['fallback_reasons'])
                    self.assertEqual(1,d.details['pdf_gradients']['fallback_fills'])


if __name__=='__main__':unittest.main()
