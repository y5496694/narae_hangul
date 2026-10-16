"""Continuation-line tracking does not extend the visible alignment width."""
from pathlib import Path
import hashlib,os,sys,tempfile,unittest
from test_pdf_line_fit import text_style

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'1_나래엔진/narae-hangul/scripts/engine'))
from hangeul.formats import Paragraph,Run,ParaStyle,save_hwp,save_hwpx
from hangeul.geometry import DocumentModel
from hangeul.viewer import application,Document,compare


def paragraphs():
    result=[]
    for family in ('함초롬바탕','맑은 고딕'):
        text='가나다라마바사아자차카타파하'*9
        for size,stretch,tracking,align in ((11,105,-5,'CENTER'),(13,100,10,'RIGHT'),(15,95,-13,'RIGHT'),(18,105,16,'CENTER')):
            result.append(Paragraph([Run(text.rstrip(),text_style(family,size,tracking,stretch))],
                style=ParaStyle(align=align,page_break=bool(result))))
    return result


class ContinuedAlignmentTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('NARAE_RUN_HANCOM_TESTS')=='1','requires installed Hancom/fonts')
    def test_uncached_continuation_lines_in_both_formats(self):
        application();blocks=paragraphs()
        with tempfile.TemporaryDirectory() as folder:
            for ext,save in (('hwp',save_hwp),('hwpx',save_hwpx)):
                source=Path(folder)/('source.'+ext);save(DocumentModel(blocks),source)
                before=hashlib.sha256(source.read_bytes()).hexdigest()
                with Document(source) as own,Document(source,'hancom') as native:
                    self.assertEqual(len(blocks),own.count)
                    result=compare(own,native)
                    for page,block in zip(result['pages'],blocks):
                        style=block.runs[0].style
                        self.assertTrue(page['match'],(ext,style.font,style.size,style.stretch,style.letter_spacing,block.style.align,page))
                    self.assertTrue(result['match'])
                    self.assertGreater(own.details['pdf_native_text']['continuation_tracking_aligned_lines'],0)
                    self.assertGreater(own.details['positive_tracking_wrap_lines'],0)
                    if ext=='hwp':self.assertGreater(own.details['nonbreaking_control_prefixes'],0)
                self.assertEqual(before,hashlib.sha256(source.read_bytes()).hexdigest())


if __name__=='__main__':unittest.main()
