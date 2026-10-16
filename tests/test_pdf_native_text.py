"""Native PDF font/placement oracles; source fonts and documents stay local."""
from pathlib import Path
import os
import sys
import tempfile
import unittest
from dataclasses import replace

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'1_나래엔진/narae-hangul/scripts/engine'))
from hangeul.formats import Paragraph,ParaStyle,Run,TextStyle,Drawing,Table,Cell,save_hwp,save_hwpx
from hangeul.geometry import DocumentModel,Border
from hangeul.viewer import application,Document,compare
from hangeul.pdf_native_text import PdfNativeText,MARKER_PREFIX


def style(family,size=13,**values):
    kind='HFT' if family in ('한양신명조','한양중고딕','HCI Poppy','HCI Hollyhock') else 'TTF'
    return TextStyle(font=family,size=size,font_type=kind,language_fonts=(family,)*7,
                     language_font_types=(kind,)*7,**values)


def matrix_sequence(page,reader,own=False):
    from pypdf.generic import ContentStream
    from hangeul.pdf_native_text import _multiply
    current=(1,0,0,1,0,0);stack=[];font='';sequence=[]
    for values,operator in ContentStream(page.get_contents(),reader).operations:
        if operator==b'q':stack.append(current)
        elif operator==b'Q':current=stack.pop()
        elif operator==b'cm':current=_multiply(tuple(map(float,values)),current)
        elif operator==b'Tf':font=str(values[0])
        elif operator in (b'Tj',b'TJ') and (not own or font.startswith('/NaraeNative')):
            value=(round(current[0],9),round(abs(current[3]),9))
            if not sequence or sequence[-1]!=value:sequence.append(value)
    return sequence


class NativePdfTextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):application()

    def test_missing_placement_token_preserves_existing_pdf(self):
        from pypdf import PdfWriter
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'existing.pdf';writer=PdfWriter();writer.add_blank_page(595,841)
            with path.open('wb') as output:writer.write(output)
            writer.close();before=path.read_bytes()
            fonts=PdfNativeText(None);fonts.records[MARKER_PREFIX+b'\0'*6]=([], (595.28,841.86),6)
            with self.assertRaisesRegex(ValueError,'Missing PDF text placement token'):fonts.finish(path)
            self.assertEqual(before,path.read_bytes())
            self.assertEqual(['existing.pdf'],[p.name for p in Path(folder).iterdir()])

    def test_on_paper_rgb_image_is_not_a_text_placement_token(self):
        from pypdf import PdfWriter
        from pypdf.generic import DictionaryObject,DecodedStreamObject,NameObject,NumberObject
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'image.pdf';writer=PdfWriter();page=writer.add_blank_page(595,841)
            marker=MARKER_PREFIX+b'\0'*6;image=DecodedStreamObject();image.set_data(marker)
            image.update({NameObject(k):v for k,v in {
                '/Type':NameObject('/XObject'),'/Subtype':NameObject('/Image'),
                '/Width':NumberObject(4),'/Height':NumberObject(1),
                '/ColorSpace':NameObject('/DeviceRGB'),'/BitsPerComponent':NumberObject(8)}.items()})
            page[NameObject('/Resources')]=DictionaryObject({NameObject('/XObject'):
                DictionaryObject({NameObject('/Image'):writer._add_object(image)})})
            content=DecodedStreamObject();content.set_data(b'q 4 0 0 1 50 50 cm /Image Do Q')
            page[NameObject('/Contents')]=writer._add_object(content)
            with path.open('wb') as output:writer.write(output)
            writer.close();before=path.read_bytes()
            fonts=PdfNativeText(None);fonts.records[marker]=([], (595.28,841.86),6)
            with self.assertRaisesRegex(ValueError,'Missing PDF text placement token'):fonts.finish(path)
            self.assertEqual(before,path.read_bytes())

    def test_native_reference_prints_before_reading_metadata(self):
        from unittest.mock import patch
        from hangeul.viewer import native_export
        class Settings(dict):
            def SetItem(self,key,value):self[key]=value
        class App:
            printed=False
            @property
            def Version(self):
                if not self.printed:raise AssertionError('Version query changed pre-print layout')
                return 'test'
            @property
            def PageCount(self):
                if not self.printed:raise AssertionError('PageCount changed pre-print layout')
                return 1
            def CreateAction(self,name):
                self.action=name;return self
            def CreateSet(self):return Settings()
            def GetDefault(self,settings):return True
            def Execute(self,settings):
                Path(settings['FileName']).write_bytes(b'%PDF-test')
                self.printed=True;return True
        app=App()
        class Bridge:
            hwp=app
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def open_doc(self,path):self.input=Path(path)
            def check(self,value,label):
                if not value:raise AssertionError(label)
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'source.hwp';source.write_bytes(b'original')
            with patch('hangeul.hwp_automation_bridge.HancomBridge',Bridge):
                result=native_export(source,Path(folder)/'reference.pdf')
            self.assertEqual(1,result['hancom_page_count'])
            self.assertIsNone(result['hancom_page_count_before_export'])
            self.assertEqual('print-before-count',result['hancom_pagination'])
            self.assertTrue(result['hancom_metadata_after_export'])
            self.assertEqual('test',result['hancom_version'])
            self.assertEqual(b'original',source.read_bytes())

    @staticmethod
    def font_pages():
        result=[]
        def add(family,size,text,align='LEFT',**settings):
            result.append(Paragraph([Run(text,style(family,size,**settings))],style=ParaStyle(align=align)))
        for family in ('Arial','맑은 고딕','함초롬돋움','한컴바탕','바탕','바탕체','굴림',
                       '한양신명조','한양중고딕','HCI Poppy','HCI Hollyhock'):
            for size in (5,8,10,12,13,18):
                add(family,size,'AbZ01!?' if family.startswith('HCI ') else '가나A a0漢')
        for family in ('바탕체','한양신명조'):
            for color in ('#ff0000','#2939e7','#777777'):add(family,12,'가나A a0漢',color=color)
            for align in ('CENTER','RIGHT'):
                for size in (5,13):add(family,size,'가나A a0漢',align)
            for spacing in (-16,-7,7,16):add(family,13,'가나A a0漢',letter_spacing=spacing)
            add(family,13,'가나A a0漢',use_font_space=True)
        result.append(Paragraph([Run('가나다',style('한양신명조')),Run(' Aa0 ',style('Arial')),Run('漢字',style('바탕체'))]))
        result.append(Paragraph([Run('가나다',style('바탕')),Run(' Aa0 ',style('HCI Poppy')),Run('漢字',style('한양중고딕'))]))
        # Rare Hangul invokes native HFT composition, including fallback
        # transitions within a line and logical advances before printer em.
        add('한양신명조',8,''.join(chr(0xac00+i) for i in range(260)))
        add('한양중고딕',8,''.join(chr(0xac00+i) for i in range(260)))
        # More than 256 glyphs from the SAME HFT require several encodings.
        ks=''.join(bytes((lead,trail)).decode('euc_kr') for lead in range(0xb0,0xc9) for trail in range(0xa1,0xff))
        add('한양신명조',5,ks[:300])
        for family in ('HCI Poppy','한양신명조','Arial'):
            for spaces in (False,True):add(family,13,'A!A !?',use_font_space=spaces)
        for index,paragraph in enumerate(result):paragraph.style=replace(paragraph.style,page_break=index>0)
        return result

    @unittest.skipUnless(os.environ.get('NARAE_RUN_HANCOM_TESTS')=='1','requires installed Hancom/fonts')
    def test_complete_ttf_ttc_hft_pages_and_text_in_both_formats(self):
        paragraphs=self.font_pages()
        with tempfile.TemporaryDirectory() as folder:
            for extension,save in (('hwp',save_hwp),('hwpx',save_hwpx)):
                source=Path(folder)/('native-fonts.'+extension);save(DocumentModel(paragraphs),source)
                with Document(source,'hancom') as native,Document(source) as own:
                    self.assertEqual(len(paragraphs),native.count);self.assertEqual(native.count,own.count)
                    result=compare(own,native)
                    for index,(page,paragraph) in enumerate(zip(result['pages'],paragraphs)):
                        expected=''.join(run.text for run in paragraph.runs)
                        self.assertTrue(page['match'],(extension,index+1,paragraph.runs[0].style,page))
                        self.assertEqual(''.join(expected.split()),''.join(own.text(index).split()),(extension,index+1))
                    self.assertTrue(result['match'])
                    self.assertEqual(0,own.details['pdf_native_text']['legacy_lines'])
                    self.assertEqual(6,own.details['pdf_native_text']['number_precision'])

    @unittest.skipUnless(os.environ.get('NARAE_RUN_HANCOM_TESTS')=='1','requires installed Hancom/fonts')
    def test_native_precision_changes_at_filled_cell_and_survives_page_change(self):
        from pypdf import PdfReader
        model=self.precision_model()
        with tempfile.TemporaryDirectory() as folder:
            for extension,save in (('hwp',save_hwp),('hwpx',save_hwpx)):
                source=Path(folder)/('precision.'+extension);save(model,source)
                with Document(source,'hancom') as native,Document(source) as own:
                    self.assertEqual(2,native.count);self.assertEqual(native.count,own.count)
                    reference,actual=PdfReader(native.pdf_path),PdfReader(own.pdf_path)
                    expected=[(.119935,.119869),(.12,.12)]
                    self.assertEqual(expected,matrix_sequence(reference.pages[0],reference))
                    self.assertEqual(expected,matrix_sequence(actual.pages[0],actual,True))
                    self.assertEqual([(.12,.12)],matrix_sequence(actual.pages[1],actual,True))
                    # Grid paths and cell text share the rounded parent origin;
                    # both complete pages must match, including table edges.
                    self.assertTrue(compare(own,native)['match'])
                    self.assertEqual(3,own.details['pdf_native_text']['number_precision'])

    @staticmethod
    def precision_model():
        def paragraph(text):return Paragraph([Run(text,style('Arial'))])
        cells=[Cell(0,0,blocks=[paragraph('FIRSTCELL')],width=100,height=30),
               Cell(0,1,blocks=[paragraph('LASTCELL')],width=100,height=30,background='#ffffff')]
        second=Paragraph([Run('가나A a0漢',style('바탕체')),Run(' Aa0 ',style('한양신명조'))],style=ParaStyle(page_break=True))
        return DocumentModel([paragraph('BEFORE'),Table(1,2,cells,width=200,height=30),paragraph('AFTER'),second])


if __name__=='__main__':unittest.main()
