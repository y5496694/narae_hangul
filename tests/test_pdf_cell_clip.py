"""Physical cell clips keep overhanging glyph ink inside its own cell."""
from pathlib import Path
import hashlib,os,sys,tempfile,unittest
from test_pdf_line_fit import cached_source,text_style

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'1_나래엔진/narae-hangul/scripts/engine'))
from hangeul.formats import Run
from hangeul.geometry import Border
from hangeul.viewer import application,Document,compare

def clip_cases():
    cases=[];pens=(Border('NONE'),Border('SOLID',.12),Border('SOLID',1,'#ee0000'))
    for family in ('Arial','맑은 고딕','함초롬바탕','한양신명조'):
        for size in (13,18):
            for text in ('jff jjf jAf','가나다 라마바사아자(차카타파) 하가'):
                index=len(cases)
                cases.append(dict(label=f'{family}/{size}/{index}',width=25 if text.startswith('j') else 120,size=size,align='LEFT',
                    runs=[Run(text,text_style(family,size))],padding=(0,0,0,0) if index%2==0 else (3,3,0,0),
                    borders=(pens[index%3],)*4,background='#99ddff' if index%3==1 else ''))
    return cases

class PdfCellClipTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('NARAE_RUN_HANCOM_TESTS')=='1','requires installed Hancom/fonts')
    def test_cached_bitmap_fit_body_and_cells_in_both_formats(self):
        application();cases=[]
        for family in ('명조','고딕'):
            for size in (12,18):
                for width in (110,240):
                    cases.append(dict(label=f'{family}/{size}/{width}',width=width,size=size,align='CENTER' if size==12 else 'RIGHT',
                        runs=[Run('가나다 라마바사아자',text_style(family,size))],padding=(0,0,0,0)))
        with tempfile.TemporaryDirectory() as folder:
            for cell in (False,True):
                for ext in ('hwp','hwpx'):
                    path=Path(folder)/('source.'+ext);cached_source(path,cases,cell)
                    original=hashlib.sha256(path.read_bytes()).hexdigest()
                    with Document(path) as own,Document(path,'hancom') as native:
                        self.assertEqual(len(cases),own.count);self.assertEqual(len(cases),native.count)
                        result=compare(own,native)
                        for page,case in zip(result['pages'],cases):self.assertTrue(page['match'],(ext,cell,case['label'],page))
                        self.assertTrue(result['match']);self.assertEqual(4,own.details['pdf_bitmap_text']['fitted_lines'])
                    self.assertEqual(original,hashlib.sha256(path.read_bytes()).hexdigest())

    @unittest.skipUnless(os.environ.get('NARAE_RUN_HANCOM_TESTS')=='1','requires installed Hancom/fonts')
    def test_bitmap_cell_text_in_both_formats(self):
        application();cases=[]
        for family in ('명조','고딕'):
            for size in (12,18):
                for text in ('jff jjf jAf','가나다 라마바사아자'):
                    cases.append(dict(label=f'{family}/{size}/{text}',width=100,size=size,align='LEFT',
                        runs=[Run(text,text_style(family,size))],padding=(0,0,0,0)))
        with tempfile.TemporaryDirectory() as folder:
            for ext in ('hwp','hwpx'):
                path=Path(folder)/('source.'+ext);cached_source(path,cases,True)
                original=hashlib.sha256(path.read_bytes()).hexdigest()
                with Document(path) as own,Document(path,'hancom') as native:
                    self.assertEqual(len(cases),own.count);self.assertEqual(len(cases),native.count)
                    result=compare(own,native)
                    for page,case in zip(result['pages'],cases):self.assertTrue(page['match'],(ext,case['label'],page))
                    self.assertTrue(result['match']);self.assertEqual(len(cases),own.details['pdf_native_text']['cell_clipped_painter_lines'])
                self.assertEqual(original,hashlib.sha256(path.read_bytes()).hexdigest())

    @unittest.skipUnless(os.environ.get('NARAE_RUN_HANCOM_TESTS')=='1','requires installed Hancom/fonts')
    def test_overhanging_cell_text_in_both_formats(self):
        application();cases=clip_cases()
        with tempfile.TemporaryDirectory() as folder:
            for ext in ('hwp','hwpx'):
                path=Path(folder)/('source.'+ext);cached_source(path,cases,True)
                original=hashlib.sha256(path.read_bytes()).hexdigest()
                with Document(path) as own,Document(path,'hancom') as native:
                    self.assertEqual(len(cases),own.count);self.assertEqual(len(cases),native.count)
                    result=compare(own,native)
                    for page,case in zip(result['pages'],cases):self.assertTrue(page['match'],(ext,case['label'],page))
                    self.assertTrue(result['match']);self.assertEqual(len(cases),own.details['pdf_native_text']['cell_clipped_lines'])
                    self.assertFalse(own.details['pdf_native_text']['whole_document_parity_verified'])
                self.assertEqual(original,hashlib.sha256(path.read_bytes()).hexdigest())

if __name__=='__main__':unittest.main()
