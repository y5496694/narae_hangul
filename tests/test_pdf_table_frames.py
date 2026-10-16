"""Shared solid grids, cumulative cell origins and guarded PDF path tokens."""
from pathlib import Path
import os,sys,tempfile,unittest,zipfile
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'1_나래엔진/narae-hangul/scripts/engine'))
from hangeul.formats import Paragraph,Run,TextStyle,ParaStyle,Table,Cell,save_hwp,save_hwpx,load_document
from hangeul.geometry import DocumentModel,Border
from hangeul.source_layout import Command
from hangeul.pdf_table_frames import prepare,stroke_program
from hangeul.pdf_native_text import PdfNativeText,PATH_MARKER_PREFIX
from hangeul.viewer import application,Document,compare


class PdfTableFramesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):application()

    @staticmethod
    def fixture():
        style=TextStyle(font='Arial',size=13,language_fonts=('Arial',)*7,language_font_types=('TTF',)*7)
        def paragraph(text,page=False,align='LEFT'):
            return Paragraph([Run(text,style)],style=ParaStyle(page_break=page,align=align))
        base=Border();none=Border('NONE');red=Border('SOLID',base.width_pt,'#ee0000');thick=Border('SOLID',1,'#000000')
        blocks=[]
        cases=[(base,base,''),(none,base,''),(base,none,''),(base,thick,''),
            (base,red,''),(red,base,''),(none,none,''),(base,base,'#99ddff')]
        for inner,outer,fill in cases:
            blocks.extend([paragraph('BEFORE',bool(blocks)),Table(1,2,[
                Cell(0,0,blocks=[paragraph('FIRST')],width=100,height=30,borders=(inner,)*4),
                Cell(0,1,blocks=[paragraph('LAST')],width=100,height=30,borders=(inner,)*4,background=fill)],
                width=200,height=30,borders=(outer,)*4),paragraph('AFTER')])
        for rows in (1,2):
            for cols in (1,2,3):
                for align in ('LEFT','CENTER','RIGHT'):
                    for vertical in ('TOP','CENTER'):
                        widths=[63.47,97.83,81.36][:cols];heights=[30,44.21][:rows]
                        border=Border('SOLID',.5 if cols==2 else 1 if cols==3 else .12,'#2939e7')
                        cells=[Cell(r,c,blocks=[paragraph('FIRST',align=align)],width=widths[c],height=heights[r],
                            padding=(2.13,3.27,1.19,2.21),vertical_align=vertical,borders=(border,)*4,
                            background='#99ddff' if (r+c)%2 else '') for r in range(rows) for c in range(cols)]
                        blocks.extend([paragraph('BEFORE',True),Table(rows,cols,cells,width=sum(widths),height=sum(heights),
                            column_widths=widths,row_heights=heights),paragraph('AFTER')])
        return DocumentModel(blocks)

    def test_shared_grid_uses_parent_origin_and_rejects_mixed_borders(self):
        cells=[Cell(0,i,width=100,height=30) for i in range(2)]
        commands=[Command('cell',(56.69+i*100,77.49,100,30),cell) for i,cell in enumerate(cells)]
        table=Table(1,2,cells,width=200,height=30)
        grid,reason=prepare(table,commands,(56.69,56.69),(595.28,841.86))
        self.assertIsNone(reason)
        self.assertEqual([56.61,156.516,256.542],grid['x'])
        self.assertEqual([763.685,733.717],grid['y'])
        self.assertEqual(9,stroke_program(grid).count(' l S'))
        cells[1].borders=(Border('SOLID',1),)*4
        self.assertEqual((None,'mixed-cell-borders'),prepare(table,commands,(56.69,56.69),(595.28,841.86)))
        cells[1].borders=cells[0].borders
        cells[0].col_span=2
        self.assertEqual((None,'split-or-merged-grid'),prepare(table,commands,(56.69,56.69),(595.28,841.86)))

    def test_missing_path_token_preserves_original_pdf(self):
        from pypdf import PdfWriter
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'original.pdf';writer=PdfWriter();writer.add_blank_page(595,841)
            with path.open('wb') as output:writer.write(output)
            writer.close();before=path.read_bytes();fonts=PdfNativeText(None)
            fonts.path_records[PATH_MARKER_PREFIX+b'\0'*6]='0 0 m 1 1 l S'
            with self.assertRaisesRegex(ValueError,'Missing PDF text placement token'):fonts.finish(path)
            self.assertEqual(before,path.read_bytes())
            self.assertEqual(['original.pdf'],[p.name for p in Path(folder).iterdir()])

    @staticmethod
    def width_labels_source(path):
        widths=(.1,.12,.15,.2,.25,.3,.4,.5,.6,.7,1.,1.5,2.,3.,4.,5.)
        labels=[str(v)+' mm' for v in widths]+['0.120 mm','0.12mm',' 0.12 mm','0.12 mm ',
            '0.12  mm','0.12 MM','0.012 cm','.12 mm','0.12000001 mm','2 mm','1 mm','0.176389 mm']
        tables=[Table(1,1,[Cell(0,0,blocks=[Paragraph([Run('BORDER')])],width=60,height=22,
            borders=(Border('SOLID',.3,'#%06x'%(0x112200+i)),)*4)],width=60,height=22) for i in range(len(labels))]
        save_hwpx(DocumentModel(tables),path)
        with zipfile.ZipFile(path) as z:items={n:z.read(n) for n in z.namelist()}
        header=ET.fromstring(items['Contents/header.xml'])
        for element in header.iter():
            if element.tag.rsplit('}',1)[-1] not in ('leftBorder','rightBorder','topBorder','bottomBorder'):continue
            index=int(element.get('color','#000000')[1:],16)-0x112200
            if 0<=index<len(labels):element.set('width',labels[index])
        items['Contents/header.xml']=ET.tostring(header,encoding='UTF-8',xml_declaration=True)
        with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED) as z:
            for name,data in items.items():z.writestr(name,data)
        return [v*72/25.4 for v in widths]+[.1*72/25.4]*12

    def test_hwpx_width_labels_and_canonical_format_round_trip(self):
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'labels.hwpx';expected=self.width_labels_source(source)
            actual=[b.cells[0].borders[0].width_pt for b in load_document(source).model.blocks if isinstance(b,Table)]
            self.assertEqual(expected,actual)
            table=Table(1,1,[Cell(0,0,borders=(Border('SOLID',1),)*4)])
            for ext,save in (('hwp',save_hwp),('hwpx',save_hwpx)):
                path=Path(folder)/('thickness.'+ext);save(DocumentModel([table]),path)
                restored=next(b for b in load_document(path).model.blocks if isinstance(b,Table))
                self.assertEqual(.4*72/25.4,restored.cells[0].borders[0].width_pt)

    @unittest.skipUnless(os.environ.get('NARAE_RUN_HANCOM_TESTS')=='1','requires installed Hancom')
    def test_native_width_label_conversion(self):
        from hangeul.hwp_automation_bridge import HancomBridge
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'labels.hwpx';expected=self.width_labels_source(source)
            with HancomBridge() as bridge:
                bridge.open_doc(source);target=Path(folder)/'native.hwpx'
                bridge.check(bridge.hwp.SaveAs(str(target),'HWPX',''),'normalize border width')
                actual=[b.cells[0].borders[0].width_pt for b in load_document(target).model.blocks if isinstance(b,Table)]
                self.assertEqual(expected,actual)

    @unittest.skipUnless(os.environ.get('NARAE_RUN_HANCOM_TESTS')=='1','requires installed Hancom/fonts')
    def test_complete_grid_pages_in_both_formats(self):
        with tempfile.TemporaryDirectory() as folder:
            for ext,save in (('hwp',save_hwp),('hwpx',save_hwpx)):
                source=Path(folder)/('grids.'+ext);save(self.fixture(),source)
                with Document(source) as own,Document(source,'hancom') as native:
                    self.assertEqual(44,own.count)
                    result=compare(own,native)
                    for index,page in enumerate(result['pages']):self.assertTrue(page['match'],(ext,index+1,page))
                    self.assertTrue(result['match'])
                    self.assertEqual(44,own.details['pdf_table_frames']['processed_grids'])
                    self.assertEqual(0,own.details['pdf_table_frames']['legacy_grids'])
                    for index in range(44):self.assertIn('FIRST',own.text(index))


if __name__=='__main__':unittest.main()
