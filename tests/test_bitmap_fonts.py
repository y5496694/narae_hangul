"""Installed bitmap fonts: native payload oracles, with no font assets."""
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'1_나래엔진/narae-hangul/scripts/engine'))
from hangeul.hft import Glyph,HftError
from hangeul.font_resolver import InstalledHftResolver
from hangeul.formats import Paragraph,ParaStyle,Run,TextStyle,save_hwp,save_hwpx
from hangeul.geometry import DocumentModel
from hangeul.viewer import application,Document,compare


class BitmapFontTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):application()

    def test_cell_boundaries_keep_em_origin_and_round_em_before_scaling(self):
        glyph=Glyph('x',42,'bitmap',(1,41,40,40),(('R',3,1,38,40),),{})
        mask,origin=glyph.bitmap_mask(83.333333)
        self.assertEqual((76,80),mask.size)
        self.assertEqual((5,1),origin)
        self.assertEqual((255,255),mask.getextrema())
        # An isolated stroke must survive reduction even when both of its
        # scaled cell edges coincide. Native reduction ORs source bits.
        thin=Glyph('x',42,'bitmap',(1,41,1,1),(('R',1,40,1,1),),{})
        reduced,origin=thin.bitmap_mask(33)
        self.assertEqual(((1,1),(0,0),b'\xff'),(reduced.size,origin,reduced.tobytes()))
        with self.assertRaises(HftError):glyph.bitmap_mask(8193)

    @staticmethod
    def word_cases():
        cases=[]
        def add(family,size,text,**style):
            cases.append(dict(family=family,size=size,text=text,**style))
        for size in (5,8,10):
            for text in ('漢','漢漢漢','가나A a0漢','가나漢','가漢가漢','가나A a0'):
                add('명조',size,text)
            for text in ('가나다','Aa0A a','가나다 가나다 '):add('고딕',size,text)
        for size in (12,18,24,36):add('명조',size,'가나A a0漢')
        add('고딕',12,'가나A a0')
        add('명조',12,'가나다라마바사아자차카타파하'*4)
        add('고딕',12,'가나다라마바사'*12)
        for size in (5,12):
            for align in ('CENTER','RIGHT'):add('명조',size,'가나A a0漢',align=align)
        for color in ('#ff0000','#2939e7','#777777'):add('명조',12,'가나A a0漢',color=color)
        for spacing in (-16,-7,7,16):add('명조',13,'가나A a0漢',letter_spacing=spacing)
        add('명조',13,'가나A a0漢',use_font_space=True)
        for size in (2,3,4,4.5,5,5.5,6,7,8,9,10,11,12,13,17):add('명조',size,'A'*12)
        add('명조',100,'가나다')
        return cases

    @unittest.skipUnless(os.environ.get('NARAE_RUN_HANCOM_TESTS')=='1','requires installed Hancom/fonts')
    def test_native_complete_word_pages_in_both_formats(self):
        cases=self.word_cases();blocks=[]
        self.assertEqual(62,len(cases))
        for case in cases:
            family=case['family']
            text_style=TextStyle(font=family,font_type='HFT',size=case['size'],
                language_fonts=(family,)*7,language_font_types=('HFT',)*7,
                **{key:case[key] for key in ('color','letter_spacing','use_font_space') if key in case})
            blocks.append(Paragraph([Run(case['text'],text_style)],
                style=ParaStyle(page_break=bool(blocks),align=case.get('align','LEFT'))))
        with tempfile.TemporaryDirectory() as folder:
            for ext,save in (('hwp',save_hwp),('hwpx',save_hwpx)):
                source=Path(folder)/('words.'+ext);save(DocumentModel(blocks),source)
                with Document(source,'hancom') as native,Document(source) as own:
                    self.assertEqual(len(cases),native.count);self.assertEqual(native.count,own.count)
                    result=compare(own,native)
                    for page,case in zip(result['pages'],cases):
                        self.assertTrue(page['match'],(ext,case,page))
                    self.assertTrue(result['match'])
                    facts=own.details['pdf_bitmap_text']
                    self.assertEqual(0,facts['legacy_lines'])
                    self.assertFalse(facts['remaining'])

    @unittest.skipUnless(os.environ.get('NARAE_RUN_HANCOM_TESTS')=='1','requires installed Hancom/fonts')
    def test_native_bitmap_masks_and_independent_pdf_payloads_in_both_formats(self):
        from PIL import Image,ImageChops
        from pypdf import PdfReader
        cases=[(family,size,c) for family in ('명조','고딕')
               for size in (5,8,10,12,18,24,36,50) for c in '가나다Aa0漢']
        blocks=[Paragraph([Run(c,TextStyle(font=family,font_type='HFT',size=size,
            language_fonts=(family,)*7,language_font_types=('HFT',)*7))],
            style=ParaStyle(page_break=i>0)) for i,(family,size,c) in enumerate(cases)]
        resolver=InstalledHftResolver()
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            for ext,save in (('hwp',save_hwp),('hwpx',save_hwpx)):
                source=root/('bitmap.'+ext);save(DocumentModel(blocks),source)
                with Document(source,'hancom') as native,Document(source) as own:
                    self.assertEqual(len(cases),native.count);self.assertEqual(native.count,own.count)
                    report=own.report()['layout']
                    self.assertFalse(report['font_substitutions'])
                    self.assertEqual({'고딕'},set(report['glyph_substitutions']))
                    self.assertEqual(1,report['glyph_substitutions']['고딕']['distinct_characters'])
                    actual,reference=PdfReader(own.pdf_path),PdfReader(native.pdf_path)
                    for page,(family,size,c) in enumerate(cases):
                        glyph=resolver.bitmap_glyph(family,c)
                        if glyph is None:
                            # Installed 고딕 has no Hanja face; preserve and
                            # diagnose the configured fallback, not a bitmap.
                            self.assertEqual([],list(reference.pages[page].images))
                            continue
                        mask,_=glyph.bitmap_mask(size*600/72)
                        native_images=list(reference.pages[page].images)
                        own_images=list(actual.pages[page].images)
                        self.assertEqual(1,len(native_images),(ext,family,size,c))
                        self.assertEqual(1,len(own_images),(ext,family,size,c))
                        ink=ImageChops.invert(native_images[0].image.convert('L'))
                        native_mask=ink.crop(ink.getbbox())
                        expected=mask.crop(mask.getbbox())
                        self.assertEqual(expected.size,native_mask.size,(ext,family,size,c))
                        self.assertIsNone(ImageChops.difference(expected,native_mask).getbbox(),(ext,family,size,c))
                        # Native EMF adds guards around its full text buffer.
                        # Compare the RGB payload, including those white pixels.
                        obj=next(ref.get_object() for ref in actual.pages[page]['/Resources']['/XObject'].values()
                                 if ref.get_object().get('/Subtype')=='/Image')
                        native_obj=next(ref.get_object() for ref in reference.pages[page]['/Resources']['/XObject'].values()
                                        if ref.get_object().get('/Subtype')=='/Image')
                        self.assertEqual((native_obj['/Width'],native_obj['/Height']),(obj['/Width'],obj['/Height']))
                        self.assertEqual(native_obj.get_data(),obj.get_data(),(ext,family,size,c))
                    comparison=compare(own,native)
                    for page,(family,size,c) in enumerate(cases):
                        if resolver.bitmap_glyph(family,c) is not None:
                            self.assertTrue(comparison['pages'][page]['match'],(ext,family,size,c))


if __name__=='__main__':unittest.main()
