"""Saved stretched text uses the same integer font width for device metrics."""
from pathlib import Path
import hashlib,os,sys,tempfile,unittest
from test_pdf_line_fit import cached_source,text_style

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'1_나래엔진/narae-hangul/scripts/engine'))
from hangeul.formats import Run
from hangeul.viewer import application,Document,compare


def stretch_cases():
    cases=[]
    for family in ('Arial','맑은 고딕','함초롬바탕','HY울릉도B'):
        for size,ratio,tracking,align,width in (
            (9.5,95,10,'RIGHT',120),(18,105,-5,'CENTER',120),
            (11,50,-16,'LEFT',400),(13,150,16,'RIGHT',400),
            (13,95,0,'CENTER',400),(13,105,-13,'RIGHT',400)):
            cases.append(dict(label=f'{family}/{size}/{ratio}/{tracking}/{align}',width=width,size=size,align=align,
                runs=[Run('AFTER FIRST FONT, BEFORE AFTER',text_style(family,size,tracking,ratio))]))
    return cases


class CachedStretchTests(unittest.TestCase):
    def _native(self,cell):
        application();cases=stretch_cases()
        with tempfile.TemporaryDirectory() as folder:
            for ext in ('hwp','hwpx'):
                path=Path(folder)/('source.'+ext);cached_source(path,cases,cell)
                before=hashlib.sha256(path.read_bytes()).hexdigest()
                with Document(path) as own,Document(path,'hancom') as native:
                    self.assertEqual(len(cases),own.count);self.assertEqual(len(cases),native.count)
                    result=compare(own,native)
                    for page,case in zip(result['pages'],cases):
                        self.assertTrue(page['match'],(ext,cell,case['label'],page))
                    self.assertTrue(result['match'])
                    self.assertEqual(len(cases),own.details['pdf_native_text']['device_centered_stretched_lines'])
                self.assertEqual(before,hashlib.sha256(path.read_bytes()).hexdigest())

    @unittest.skipUnless(os.environ.get('NARAE_RUN_HANCOM_TESTS')=='1','requires installed Hancom/fonts')
    def test_cached_stretch_body_in_both_formats(self):self._native(False)

    @unittest.skipUnless(os.environ.get('NARAE_RUN_HANCOM_TESTS')=='1','requires installed Hancom/fonts')
    def test_cached_stretch_cells_in_both_formats(self):self._native(True)


if __name__=='__main__':unittest.main()
