"""Installed font mappings and native outline/advance oracles; no font assets."""
import math
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'1_나래엔진/narae-hangul/scripts/engine'))
from hangeul.font_resolver import InstalledHftResolver,installed_default_fonts
from hangeul.hft import HftFont,HftError
from hangeul.hft_cff import normalized_operations
from hangeul.formats import Paragraph,ParaStyle,Run,TextStyle,save_hwp,save_hwpx
from hangeul.geometry import DocumentModel
from hangeul.viewer import application,Document


class FontAppearanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):application()

    def test_unmapped_symbols_do_not_silently_use_johab_byte_codes(self):
        font=object.__new__(HftFont);font.unicode_tables={}
        self.assertEqual(65,font.hchar('A'))
        self.assertEqual(0x8861,font.hchar('가'))
        for character in ('①','漢','😀'):
            with self.assertRaises(HftError):font.hchar(character)

    def test_missing_font_order_comes_from_fontdb(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);config=root/'Bin/FontDB/fontinfo.dat'
            config.parent.mkdir(parents=True)
            config.write_text('[Default Fonts ENU]\nHangul=English,T\n'
                              '[Default Fonts KOR]\nHangul=Native,T,Outline,H,Second,T\n',encoding='utf-16')
            with patch('hangeul.font_resolver._installed_directories',return_value=[root/'Shared/Fonts']):
                families,evidence=installed_default_fonts()
            self.assertEqual(('Native','Second'),families)
            self.assertEqual('Default Fonts KOR',evidence['section'])
            self.assertEqual(64,len(evidence['sha256']))

    @unittest.skipUnless(os.environ.get('NARAE_RUN_HANCOM_TESTS')=='1','requires installed Hancom/fonts')
    def test_native_hft_outlines_and_cff_advances_in_both_formats(self):
        from fontTools.ttLib import TTFont
        from pypdf import PdfReader
        from pypdf.generic import ContentStream
        cases=[(family,c) for family,text in (
            ('한양신명조','①②☆…「」漢字國語あいうABC가나다'),
            ('한양중고딕','①②☆…「」漢字國語あいうABC가나다'),
            ('HCI Poppy','AaBZ01!?'),('HCI Hollyhock','AaBZ01!?')) for c in text]
        blocks=[Paragraph([Run(c,TextStyle(font=family,font_type='HFT',size=20,
            language_fonts=(family,)*7,language_font_types=('HFT',)*7))],
            style=ParaStyle(page_break=i>0)) for i,(family,c) in enumerate(cases)]
        resolver=InstalledHftResolver()
        for family in dict(cases):self.assertIsNotNone(resolver.resolve(family),family)
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            for ext,save in (('hwp',save_hwp),('hwpx',save_hwpx)):
                source=root/('script.'+ext);save(DocumentModel(blocks),source)
                with Document(source,'hancom') as document:
                    self.assertEqual(len(cases),document.count)
                    reader=PdfReader(document.pdf_path)
                    for page,(family,c) in enumerate(cases):
                        fonts,_,_=resolver._sources(family)
                        selected=None
                        for font in fonts:
                            try:glyph=font.glyph(c)
                            except (HftError,UnicodeError):continue
                            if glyph.kind=='outline':selected=font,glyph;break
                        self.assertIsNotNone(selected,(family,c))
                        font,glyph=selected
                        ours=[({'M':b'm','L':b'l','C':b'c'}[op],tuple(vals))
                              for op,*vals in normalized_operations(glyph) if op!='Z']
                        matches=[]
                        for ref in reader.pages[page]['/Resources']['/Font'].values():
                            native=ref.get_object()
                            if native['/Subtype']!='/Type3':continue
                            for proc in native['/CharProcs'].values():
                                operations=ContentStream(proc.get_object(),reader).operations
                                path=[(op,tuple(float(v) for v in vals)) for vals,op in operations
                                      if op in (b'm',b'l',b'c')]
                                if path==ours:
                                    matches.extend(float(vals[0]) for vals,op in operations if op==b'd1')
                        self.assertIn(math.trunc(font.advance(c,1000)),matches,(ext,family,c))
                        self.assertTrue(resolver.supports(family,c),(family,c))
                        with TTFont(resolver.fonts[family]['cache_path']) as cff:
                            name=cff.getBestCmap()[ord(c)]
                            self.assertEqual(math.trunc(font.advance(c,1000)),cff['hmtx'].metrics[name][0])

    @unittest.skipUnless(os.environ.get('NARAE_RUN_HANCOM_TESTS')=='1','requires installed Hancom/fonts')
    def test_unknown_serif_and_gothic_use_native_default_face(self):
        from pypdf import PdfReader
        from hangeul.source_layout import SourceLayout
        names=('Narae Missing 명조 2026','Narae Missing 고딕 2026')
        model=DocumentModel([Paragraph([Run('한글 ABC',TextStyle(font=name))],
            style=ParaStyle(page_break=i>0)) for i,name in enumerate(names)])
        layout=SourceLayout(model)
        self.assertTrue(layout.geometry_report()['font_fallback_config'])
        self.assertEqual(['한컴바탕','한컴바탕'],[layout.font_substitutions[name] for name in names])
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'missing.hwpx';save_hwpx(model,source)
            with Document(source,'hancom') as native:
                for page in PdfReader(native.pdf_path).pages:
                    self.assertTrue(any('Haansoft Batang' in str(ref.get_object().get('/BaseFont'))
                                        for ref in page['/Resources']['/Font'].values()))


if __name__=='__main__':unittest.main()
