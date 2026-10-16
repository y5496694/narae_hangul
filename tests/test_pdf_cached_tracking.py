"""Cached device widths precede signed tracking and overflow fitting."""
from pathlib import Path
import hashlib,os,sys,tempfile,unittest
from test_pdf_line_fit import cached_source,text_style

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'1_나래엔진/narae-hangul/scripts/engine'))
from hangeul.formats import Run
from hangeul.viewer import application,Document,compare

def tracking_cases():
    cases=[]
    for family in ('Arial','맑은 고딕','함초롬바탕'):
        for size,spacing,align in ((9.5,-10,'LEFT'),(13,10,'CENTER'),(15,-13,'RIGHT'),(18,-5,'CENTER'),(15,-20,'LEFT')):
            for width in (120,400):
                cases.append(dict(label=f'{family}/{size}/{spacing}/{width}',width=width,size=size,align=align,
                    runs=[Run('AFTER FIRST FONT, BEFORE AFTER',text_style(family,size,spacing))]))
    for width in (400.01,400.02,400.03):
        for align in ('LEFT','CENTER','RIGHT'):
            cases.append(dict(label=f'fractional/{width}/{align}',width=width,size=15,align=align,
                runs=[Run('AFTER FIRST FONT, BEFORE AFTER',text_style('Arial',15,-13))]))
    return cases

class CachedTrackingTests(unittest.TestCase):
    def _native(self,cell):
        application();cases=tracking_cases()
        with tempfile.TemporaryDirectory() as folder:
            for ext in ('hwp','hwpx'):
                path=Path(folder)/('source.'+ext);cached_source(path,cases,cell)
                original=hashlib.sha256(path.read_bytes()).hexdigest()
                with Document(path) as own,Document(path,'hancom') as native:
                    self.assertEqual(len(cases),own.count);self.assertEqual(len(cases),native.count)
                    result=compare(own,native)
                    for page,case in zip(result['pages'],cases):self.assertTrue(page['match'],(ext,cell,case['label'],page))
                    self.assertTrue(result['match']);self.assertEqual(len(cases),own.details['pdf_native_text']['device_centered_lines'])
                self.assertEqual(original,hashlib.sha256(path.read_bytes()).hexdigest())

    @unittest.skipUnless(os.environ.get('NARAE_RUN_HANCOM_TESTS')=='1','requires installed Hancom/fonts')
    def test_cached_tracking_body_in_both_formats(self):self._native(False)

    @unittest.skipUnless(os.environ.get('NARAE_RUN_HANCOM_TESTS')=='1','requires installed Hancom/fonts')
    def test_cached_tracking_cells_in_both_formats(self):self._native(True)

if __name__=='__main__':unittest.main()
