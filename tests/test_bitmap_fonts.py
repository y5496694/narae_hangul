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
from hangeul.viewer import application,Document


class BitmapFontTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):application()

    def test_cell_boundaries_keep_em_origin_and_round_em_before_scaling(self):
        glyph=Glyph('x',42,'bitmap',(1,41,40,40),(('R',3,1,38,40),),{})
        mask,origin=glyph.bitmap_mask(83.333333)
        self.assertEqual((76,80),mask.size)
        self.assertEqual((5,1),origin)
        self.assertEqual((255,255),mask.getextrema())
        with self.assertRaises(HftError):glyph.bitmap_mask(8193)

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
                        # Qt stores binary alpha as an explicit /Mask. Check
                        # the actual PDF payload, not pypdf's RGB image preview.
                        obj=next(ref.get_object() for ref in actual.pages[page]['/Resources']['/XObject'].values()
                                 if ref.get_object().get('/Subtype')=='/Image')
                        self.assertEqual(expected.size,(obj['/Width'],obj['/Height']))
                        stencil=obj['/Mask'].get_object()
                        self.assertEqual([1,0],list(stencil['/Decode']))
                        image=Image.frombytes('1',expected.size,stencil.get_data()).convert('L')
                        self.assertIsNone(ImageChops.difference(expected,image).getbbox(),(ext,family,size,c))


if __name__=='__main__':unittest.main()
