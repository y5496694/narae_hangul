"""Continued justification against complete native pages in both formats."""
from pathlib import Path
import hashlib,os,sys,tempfile,unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'1_나래엔진/narae-hangul/scripts/engine'))
from hangeul.formats import Paragraph,Run,ParaStyle,save_hwp,save_hwpx
from hangeul.geometry import DocumentModel
from hangeul.viewer import application,Document,compare
from hangeul.pdf_line_fit import expand_line
from test_pdf_line_fit import text_style


def cases(leading=True):
    result=[('korean','휴먼명조','가나다라마바사아자차카타파하'*9,0,100),
            ('latin','Arial','ABCDEFGHIJKLMNOPQRSTUVWXYZ'*8,0,100),
            ('words','휴먼명조','가나다 라마바사 아자차 카타파하 '*9,0,100)]
    if leading:result.append(('leading','휴먼명조','   가나다  라마바사\u3000아자차 카타파하 '*7,0,100))
    result.extend([('shortwords','휴먼명조','가 나 다 라 마 바 사 아 자 차 '*10,0,100),
                   ('tracking-plus','함초롬바탕','가나다 라마바사 아자차 카타파하 '*9,10,105),
                   ('tracking-minus','함초롬바탕','가나다 라마바사 아자차 카타파하 '*9,-15,95),
                   ('hft','한양신명조','가나다 라마바사 아자차 카타파하 '*9,0,100)])
    for family in ('명조','고딕'):
        for label,text,tracking in [('korean','가나다라마바사아자차카타파하'*9,0),
                                    ('words','가나다 라마바사 아자차 카타파하 '*9,0),
                                    ('latin','ABCDEFGHIJKLMNOPQRSTUVWXYZ '*8,0),
                                    ('tracking','가나다 라마바사 아자차 카타파하 '*9,-15),
                                    ('tracking-plus','가나다 라마바사 아자차 카타파하 '*9,10)]:
            result.append((family+'/'+label,family,text,tracking,100))
    return result


def fixture(values):
    return DocumentModel([Paragraph([Run(text,text_style(family,13,tracking,stretch))],
        style=ParaStyle(align='JUSTIFY',page_break=index>0))
        for index,(_,family,text,tracking,stretch) in enumerate(values)])


class PdfJustificationTests(unittest.TestCase):
    def test_outer_spaces_and_integer_remainder(self):
        result=expand_line('  가 나\u3000다  ',[6,6,13,6,13,13,13,6,6],[6,6,13,6,13,13,13,6,6],101)
        self.assertEqual((15,0,2),(result['word_adjustment'],result['character_adjustment'],result['word_gaps']))
        self.assertEqual([6,6],result['advances'][:2]);self.assertEqual([6,6],result['advances'][-2:])
        self.assertEqual(100,sum(result['advances'][:-2]))
        self.assertIsNone(expand_line('   ',[6]*3,[6]*3,100))
        self.assertIsNone(expand_line('가 나',[13,6,13],[13,6,13],31))

    def _native(self,cached):
        from hangeul.hwp_automation_bridge import HancomBridge
        # Cached coverage includes leading/consecutive/wide spaces. Qt's
        # uncached consecutive trailing-space reflow still has a separately
        # preserved failing page; do not claim that case passed uncached.
        application();values=cases(leading=cached)
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            for ext,save in (('hwp',save_hwp),('hwpx',save_hwpx)):
                source=root/('source.'+ext);save(fixture(values),source)
                before=hashlib.sha256(source.read_bytes()).hexdigest()
                if cached:
                    reference=root/(ext+'-reference.pdf');target=root/(ext+'-cached.'+ext)
                    with HancomBridge() as bridge:
                        bridge.open_doc(source)
                        action=bridge.hwp.CreateAction('PrintToPDFEx');settings=action.CreateSet()
                        bridge.check(action.GetDefault(settings),'print defaults')
                        for key,value in dict(Range=6,RangeCustom='',RangeIncludeLinkedDoc=0,NumCopy=1,
                            PrintMethod=0,PrintToFile=1,FileName=str(reference),ReverseOrder=0,
                            EvenOddPageType=0,ZoomX=100,ZoomY=100,PrintWithoutBlank=0).items():settings.SetItem(key,value)
                        bridge.check(action.Execute(settings),'print reference')
                        bridge.check(bridge.hwp.SaveAs(str(target),ext.upper(),''),'save line cache')
                    native=Document(reference)
                else:target=source;native=Document(source,'hancom')
                with native,Document(target) as own:
                    self.assertEqual(len(values),native.count);self.assertEqual(native.count,own.count)
                    result=compare(own,native)
                    for page,case in zip(result['pages'],values):self.assertTrue(page['match'],(ext,cached,case[0],page))
                    self.assertTrue(result['match'])
                    for kind in ('pdf_native_text','pdf_bitmap_text'):
                        facts=own.details[kind];self.assertGreater(facts['justified_lines'],0)
                        self.assertGreater(facts['expanded_word_gaps'],0);self.assertGreater(facts['expanded_character_gaps'],0)
                        self.assertFalse(facts['whole_document_parity_verified'])
                    self.assertNotIn('justified-continuation',own.details['pdf_native_text']['legacy_reasons'])
                self.assertEqual(before,hashlib.sha256(source.read_bytes()).hexdigest())

    @unittest.skipUnless(os.environ.get('NARAE_RUN_HANCOM_TESTS')=='1','requires installed Hancom/fonts')
    def test_cached_continuation_lines_in_both_formats(self):self._native(True)

    @unittest.skipUnless(os.environ.get('NARAE_RUN_HANCOM_TESTS')=='1','requires installed Hancom/fonts')
    def test_uncached_continuation_lines_in_both_formats(self):self._native(False)


if __name__=='__main__':unittest.main()
