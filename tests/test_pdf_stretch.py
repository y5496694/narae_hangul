"""Printer font widths, horizontal scaling and native PDF text corrections."""
from pathlib import Path
import os,sys,tempfile,unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'1_나래엔진/narae-hangul/scripts/engine'))
from hangeul.formats import Paragraph,ParaStyle,Run,TextStyle,save_hwp,save_hwpx
from hangeul.geometry import DocumentModel
from hangeul.viewer import application,Document,compare


def text_style(family,size,ratio,**values):
    kind='HFT' if family in ('한양신명조','HCI Poppy') else 'TTF'
    return TextStyle(font=family,size=size,stretch=ratio,font_type=kind,language_fonts=(family,)*7,
        language_font_types=(kind,)*7,language_ratios=(ratio,)*7,**values)


class PdfStretchTests(unittest.TestCase):
    @staticmethod
    def paragraphs():
        paragraphs=[]
        def add(family,size,ratio,align='LEFT',**values):
            text='AbZ a0!?' if family=='HCI Poppy' else '가나A a0!?漢'
            paragraphs.append(Paragraph([Run(text,text_style(family,size,ratio,**values))],
                style=ParaStyle(align=align,page_break=bool(paragraphs))))
        for family in ('맑은 고딕','함초롬바탕','휴먼명조','굴림체','HY울릉도B','한양신명조','HCI Poppy'):
            for size in (5,11,13,18):
                for ratio in (50,95,105,150):add(family,size,ratio)
        for family in ('맑은 고딕','함초롬바탕','한양신명조','HCI Poppy'):
            for ratio in (95,105):
                for tracking in (-16,16):
                    for align in ('CENTER','RIGHT'):
                        for spaces in (False,True):
                            add(family,13,ratio,align,letter_spacing=tracking,language_spacing=(tracking,)*7,
                                use_font_space=spaces,color='#2939e7')
        return paragraphs

    @unittest.skipUnless(os.environ.get('NARAE_RUN_HANCOM_TESTS')=='1','requires installed Hancom/fonts')
    def test_complete_stretched_pages_and_text_in_both_formats(self):
        application();paragraphs=self.paragraphs()
        with tempfile.TemporaryDirectory() as folder:
            for ext,save in (('hwp',save_hwp),('hwpx',save_hwpx)):
                source=Path(folder)/('stretched.'+ext);save(DocumentModel(paragraphs),source)
                with Document(source,'hancom') as native,Document(source) as own:
                    self.assertEqual(176,own.count)
                    result=compare(own,native)
                    for index,(page,paragraph) in enumerate(zip(result['pages'],paragraphs)):
                        style=paragraph.runs[0].style
                        self.assertTrue(page['match'],(ext,index+1,style.font,style.size,style.stretch,
                            style.letter_spacing,style.use_font_space,paragraph.style.align,page))
                        expected=''.join(run.text for run in paragraph.runs)
                        self.assertEqual(''.join(expected.split()),''.join(own.text(index).split()))
                    self.assertTrue(result['match'])
                    self.assertEqual(0,own.details['pdf_native_text']['legacy_lines'])
                    self.assertGreater(own.details['pdf_native_text']['stretched_glyphs'],1000)


if __name__=='__main__':unittest.main()
