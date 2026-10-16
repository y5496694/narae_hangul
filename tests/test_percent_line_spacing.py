"""Percent line gaps retain native logical precision, including overlap."""
from pathlib import Path
import hashlib,os,sys,tempfile,unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'1_나래엔진/narae-hangul/scripts/engine'))
from hangeul.formats import Paragraph,ParaStyle,Run,TextStyle,save_hwp,save_hwpx
from hangeul.geometry import DocumentModel
from hangeul.viewer import application,Document,compare

TEXT='가나다라\n마바사아\n자차카타\n파하가나\n다라마바사\n아자차카'


def spacing_cases():
    result=[(size,ratio) for size in (10.01,10.02,10.03,10.04,11,11.03,13.13)
            for ratio in (101,125,149,150,151,160)]
    result.extend((size,ratio) for size in (11,11.03) for ratio in (0,50,75,90,99,100))
    return result


def paragraph(size,ratio,text=TEXT,page_break=False):
    font=TextStyle(font='맑은 고딕',size=size)
    return Paragraph([Run(text,font)],default_style=font,end_style=font,
        style=ParaStyle(line=ratio,page_break=page_break))


class PercentLineSpacingTests(unittest.TestCase):
    def test_zero_percent_overlap_stays_on_the_same_page(self):
        application()
        with tempfile.TemporaryDirectory() as folder:
            for ext,save in (('hwp',save_hwp),('hwpx',save_hwpx)):
                path=Path(folder)/('source.'+ext)
                save(DocumentModel([paragraph(11,0)]),path)
                with Document(path) as own:self.assertEqual(1,own.count,ext)

    @unittest.skipUnless(os.environ.get('NARAE_RUN_HANCOM_TESTS')=='1','requires installed Hancom/fonts')
    def test_fractional_and_overlapping_percent_gaps_in_both_formats(self):
        application();cases=spacing_cases()
        with tempfile.TemporaryDirectory() as folder:
            for ext,save in (('hwp',save_hwp),('hwpx',save_hwpx)):
                source=Path(folder)/('source.'+ext)
                save(DocumentModel([paragraph(size,ratio,page_break=bool(i))
                    for i,(size,ratio) in enumerate(cases)]),source)
                sha=hashlib.sha256(source.read_bytes()).hexdigest()
                with Document(source) as own,Document(source,'hancom') as native:
                    self.assertEqual(len(cases),own.count);self.assertEqual(len(cases),native.count)
                    result=compare(own,native)
                    for case,page in zip(cases,result['pages']):
                        self.assertTrue(page['match'],(ext,case,page))
                    self.assertTrue(result['match'])
                self.assertEqual(sha,hashlib.sha256(source.read_bytes()).hexdigest())

    @unittest.skipUnless(os.environ.get('NARAE_RUN_HANCOM_TESTS')=='1','requires installed Hancom/fonts')
    def test_percent_gap_accumulation_and_page_breaks(self):
        application()
        with tempfile.TemporaryDirectory() as folder:
            for ext,save in (('hwp',save_hwp),('hwpx',save_hwpx)):
                source=Path(folder)/('source.'+ext)
                save(DocumentModel([paragraph(11,150,'\n'.join(['가나다라']*160))]),source)
                with Document(source) as own,Document(source,'hancom') as native:
                    self.assertEqual(4,own.count);self.assertEqual(4,native.count)
                    self.assertTrue(compare(own,native)['match'],ext)


if __name__=='__main__':unittest.main()
