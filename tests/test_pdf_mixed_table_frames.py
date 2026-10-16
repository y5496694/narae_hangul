"""Native mixed solid cell borders, including shared and outer edges."""
from pathlib import Path
import os,sys,tempfile,unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'1_나래엔진/narae-hangul/scripts/engine'))
from hangeul.formats import Table,Cell,Paragraph,ParaStyle,save_hwp,save_hwpx
from hangeul.geometry import DocumentModel,Border
from hangeul.viewer import application,Document,compare


class PdfMixedTableFramesTests(unittest.TestCase):
    @staticmethod
    def fixture():
        black=Border('SOLID',.3*72/25.4,'#000000')
        red=Border('SOLID',black.width_pt,'#ee0000');blue=Border('SOLID',black.width_pt,'#0000ee')
        thin=Border('SOLID',.2*72/25.4);thick=Border('SOLID',.4*72/25.4);none=Border('NONE')
        pairs=((red,blue),(blue,red),(thin,thick),(thick,thin),(none,black),(black,none))
        tables=[]
        def base():return [Cell(r,c,width=80,height=30,borders=(black,)*4) for r in range(2) for c in range(2)]
        def side(cell,index,border):
            values=list(cell.borders);values[index]=border;cell.borders=tuple(values)
        for vertical in (True,False):
            for first,second in pairs:
                cells=base()
                if vertical:side(cells[0],1,first);side(cells[1],0,second)
                else:side(cells[0],3,first);side(cells[2],2,second)
                tables.append(Table(2,2,cells,width=160,height=60))
        for edge,positions in ((0,(0,2)),(1,(1,3)),(2,(0,1)),(3,(2,3))):
            for first,second in pairs:
                cells=base();side(cells[positions[0]],edge,first);side(cells[positions[1]],edge,second)
                tables.append(Table(2,2,cells,width=160,height=60))
        cells=[Cell(0,0,1,2,width=160,height=30,borders=(black,)*4),
            Cell(1,0,width=80,height=30,borders=(black,)*4),Cell(1,1,width=80,height=30,borders=(black,)*4)]
        side(cells[0],3,red);side(cells[1],2,blue)
        tables.append(Table(2,2,cells,width=160,height=60,column_widths=[80,80],row_heights=[30,30]))
        cells=[Cell(0,0,2,1,width=80,height=60,borders=(black,)*4),
            Cell(0,1,width=80,height=30,borders=(black,)*4),Cell(1,1,width=80,height=30,borders=(black,)*4)]
        side(cells[0],1,red);side(cells[1],0,blue)
        tables.append(Table(2,2,cells,width=160,height=60,column_widths=[80,80],row_heights=[30,30]))
        cells=[Cell(0,0,2,2,width=160,height=60,borders=(red,blue,thin,thick),background='#99ddff')]
        tables.append(Table(2,2,cells,width=160,height=60,column_widths=[80,80],row_heights=[30,30]))
        cells=base()
        for index,cell in enumerate(cells):
            cell.background='#99ddff' if index%2 else '#ffeecc'
            cell.borders=(red,blue,thin,thick)
        tables.append(Table(2,2,cells,width=160,height=60))
        blocks=[]
        for table in tables:blocks.extend((Paragraph(style=ParaStyle(page_break=bool(blocks))),table))
        return DocumentModel(blocks)

    def test_format_pair_has_identical_complete_pages(self):
        application()
        with tempfile.TemporaryDirectory() as folder:
            hwp=Path(folder)/'mixed.hwp';hwpx=Path(folder)/'mixed.hwpx'
            save_hwp(self.fixture(),hwp);save_hwpx(self.fixture(),hwpx)
            with Document(hwp) as first,Document(hwpx) as second:
                self.assertEqual(40,first.count)
                result=compare(first,second)
                self.assertTrue(result['match'],result['pages'])

    @unittest.skipUnless(os.environ.get('NARAE_RUN_HANCOM_TESTS')=='1','requires installed Hancom')
    def test_complete_native_mixed_border_pages_in_both_formats(self):
        application()
        with tempfile.TemporaryDirectory() as folder:
            for ext,save in (('hwp',save_hwp),('hwpx',save_hwpx)):
                path=Path(folder)/('mixed.'+ext);save(self.fixture(),path);before=path.read_bytes()
                with Document(path) as own,Document(path,'hancom') as native:
                    self.assertEqual(40,own.count)
                    result=compare(own,native)
                    for page in result['pages']:self.assertTrue(page['match'],(ext,page))
                    self.assertTrue(result['match'])
                    self.assertEqual(40,own.details['pdf_table_frames']['processed_grids'])
                    self.assertEqual(0,own.details['pdf_table_frames']['legacy_grids'])
                self.assertEqual(before,path.read_bytes())


if __name__=='__main__':unittest.main()
