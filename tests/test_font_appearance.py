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

    def test_synthetic_italic_uses_upright_metrics_and_preserves_real_faces(self):
        from hangeul.source_layout import SourceLayout,SYNTHETIC_ITALIC_PROPERTY
        from PySide6.QtGui import QFontDatabase
        family=QFontDatabase.families()[0]
        layout=SourceLayout(DocumentModel([]))
        style=TextStyle(font=family,italic=True)
        with patch('hangeul.source_layout.QFontDatabase.styles',return_value=['Regular']), \
             patch('hangeul.source_layout.QFontDatabase.italic',return_value=False):
            fmt=layout._char_format(style)
            self.assertFalse(fmt.fontItalic())
            self.assertEqual(.333333,fmt.property(SYNTHETIC_ITALIC_PROPERTY))
        layout=SourceLayout(DocumentModel([]))
        with patch('hangeul.source_layout.QFontDatabase.styles',return_value=['Regular','Italic']), \
             patch('hangeul.source_layout.QFontDatabase.italic',side_effect=lambda family,face:face=='Italic'):
            fmt=layout._char_format(style)
            self.assertTrue(fmt.fontItalic())
            self.assertIsNone(fmt.property(SYNTHETIC_ITALIC_PROPERTY))

    def test_tracking_is_proportional_and_inline_width_overrides_it(self):
        from dataclasses import replace
        from PySide6.QtGui import QFontDatabase,QFontMetricsF
        from hangeul.source_layout import SourceLayout,TEXT_UNIT_PT
        family=QFontDatabase.families()[0]
        style=TextStyle(font=family,size=12,letter_spacing=16)
        layout=SourceLayout(DocumentModel([Paragraph([Run('A 가',style)])]))
        normal=QFontMetricsF(layout._font(replace(style,letter_spacing=0)),layout._metrics_device)
        spaced=QFontMetricsF(layout._font(style),layout._metrics_device)
        for character in ('A','가'):
            # QTextLayout uses 26.6 fixed-point advances. This bound concerns
            # metric serialization; whole-page pixel comparison stays exact.
            self.assertAlmostEqual(normal.horizontalAdvance(character)*1.16,
                                   spaced.horizontalAdvance(character),delta=1/32)
        command=next(c for c in layout.pages[0] if c.kind=='text')
        text_layout=layout._text_layout(command);line=text_layout.lineAt(0)
        self.assertAlmostEqual(58,line.cursorToX(2)[0]-line.cursorToX(1)[0],delta=1/32)
        target=normal.horizontalAdvance('\u2000')+60
        control=layout._font_with_advance(layout._font(style),'\u2000',target)
        self.assertAlmostEqual(target,QFontMetricsF(control,layout._metrics_device).horizontalAdvance('\u2000'),delta=1/64)

    @unittest.skipUnless(os.environ.get('NARAE_RUN_HANCOM_TESTS')=='1','requires installed Hancom/fonts')
    def test_native_italic_transforms_and_independent_pdf_paths(self):
        from pypdf import PdfReader
        from pypdf.generic import ContentStream
        from hangeul.formats import load_document
        from hangeul.source_layout import SourceLayout,TEXT_UNIT_PT
        families=('Arial','함초롬돋움','한컴바탕','한양신명조','명조')
        blocks=[Paragraph([Run('A',TextStyle(font=family,size=30,italic=italic,
            font_type='HFT' if family in ('명조','한양신명조') else 'TTF',
            language_fonts=(family,)*7))],style=ParaStyle(page_break=i>0 or italic))
            for i,family in enumerate(families) for italic in (False,True)]
        with tempfile.TemporaryDirectory() as folder:
            for ext,save in (('hwp',save_hwp),('hwpx',save_hwpx)):
                source=Path(folder)/('italic.'+ext);save(DocumentModel(blocks),source)
                layout=SourceLayout(load_document(source).model)
                with Document(source,'hancom') as native,Document(source) as own:
                    reference,actual=PdfReader(native.pdf_path),PdfReader(own.pdf_path)
                    self.assertEqual(10,native.count);self.assertEqual(native.count,own.count)
                    for i,family in enumerate(families):
                        normal=ContentStream(actual.pages[i*2].get_contents(),actual).operations
                        italic=ContentStream(actual.pages[i*2+1].get_contents(),actual).operations
                        native_ops=ContentStream(reference.pages[i*2+1].get_contents(),reference).operations
                        matrix=next(vals for vals,op in native_ops if op==b'Tm')
                        if family=='Arial':
                            # The installed font has a real Italic face; native
                            # and independent output must not add another shear.
                            self.assertEqual(0,float(matrix[2]))
                            self.assertIn('Italic',str(next(iter(reference.pages[i*2+1]['/Resources']['/Font'].values())).get_object()['/BaseFont']))
                            continue
                        self.assertAlmostEqual(1/3,float(matrix[2]),places=3)
                        if family=='명조':
                            # Normal text uses a full white native buffer;
                            # styled legacy text retains a tight alpha image.
                            # Compare ink, independent of buffer representation.
                            self.assertTrue(any(op==b'cm' and float(vals[2])==-.333333 for vals,op in italic))
                            from PIL import Image,ImageChops
                            def ink(page):
                                obj=next(ref.get_object() for ref in page['/Resources']['/XObject'].values()
                                         if ref.get_object().get('/Subtype')=='/Image')
                                image=list(page.images)[0].image
                                stencil=obj.get('/Mask')
                                if stencil:
                                    stencil=stencil.get_object();size=(int(stencil['/Width']),int(stencil['/Height']))
                                    mask=Image.frombytes('1',size,stencil.get_data(),'raw','1',(size[0]+7)//8,1).convert('L')
                                    if list(stencil.get('/Decode',[0,1]))==[0,1]:mask=ImageChops.invert(mask)
                                else:mask=ImageChops.invert(image.convert('L'))
                                return mask.crop(mask.getbbox())
                            before,after=ink(actual.pages[i*2]),ink(actual.pages[i*2+1])
                            self.assertEqual(before.size,after.size)
                            self.assertEqual(before.tobytes(),after.tobytes())
                            continue
                        command=next(c for c in layout.pages[i*2] if c.kind=='text')
                        run=layout._text_layout(command).glyphRuns()[0]
                        baseline=command.rect[1]/TEXT_UNIT_PT+run.positions()[0].y()
                        paths=lambda ops:[(op,tuple(map(float,vals))) for vals,op in ops if op in (b'm',b'l',b'c')]
                        before,after=paths(normal),paths(italic)
                        self.assertEqual(len(before),len(after))
                        for (op,points),(slanted_op,slanted) in zip(before,after):
                            self.assertEqual(op,slanted_op)
                            for j in range(0,len(points),2):
                                self.assertAlmostEqual(points[j+1],slanted[j+1],places=5)
                                self.assertAlmostEqual(points[j]+(baseline-points[j+1])*.333333,slanted[j],places=4)

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
