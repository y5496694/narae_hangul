"""Cached TrueType widths against native PDFs, with verified GDI faces."""
from pathlib import Path
import os,sys,tempfile,unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'1_나래엔진/narae-hangul/scripts/engine'))
from hangeul.pdf_device_metrics import centered_advances,installed_widths
from hangeul.formats import Paragraph,Run,TextStyle,ParaStyle,load_document,save_hwpx
from hangeul.geometry import DocumentModel
from hangeul.viewer import application,Document,compare


class PdfDeviceMetricsTests(unittest.TestCase):
    def test_device_centering_preserves_observed_internal_positions(self):
        self.assertEqual([868,790,802,868,940],centered_advances('AFTER',
            [868,796,796,868,940],[864,792,804,864,936]))
        self.assertEqual([796],centered_advances('T',[796],[804]))
        with self.assertRaisesRegex(ValueError,'device-width-count'):centered_advances('AB',[1],[1])

    @unittest.skipUnless(sys.platform=='win32','requires Windows GDI')
    def test_font_identity_is_checked_before_using_device_widths(self):
        with self.assertRaisesRegex(ValueError,'unverified-device-font'):
            installed_widths('Arial',108,'AFTER',{'head':b'wrong source font'})

    @unittest.skipUnless(os.environ.get('NARAE_RUN_HANCOM_TESTS')=='1','requires installed Hancom/fonts')
    def test_cached_single_line_pages_in_both_formats(self):
        from hangeul.hwp_automation_bridge import HancomBridge
        application();blocks=[]
        for family in ('Arial','맑은 고딕','함초롬바탕'):
            for size,align in ((9.5,'LEFT'),(13,'CENTER'),(18,'RIGHT')):
                style=TextStyle(font=family,size=size,language_fonts=(family,)*7,language_font_types=('TTF',)*7)
                blocks.append(Paragraph([Run('AFTER FIRST AFT',style)],style=ParaStyle(align=align)))
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'source.hwpx';save_hwpx(DocumentModel(blocks),source)
            with HancomBridge() as bridge:
                bridge.open_doc(source)
                self.assertEqual(1,int(bridge.hwp.PageCount))
                for ext,kind in (('hwp','HWP'),('hwpx','HWPX')):
                    bridge.check(bridge.hwp.SaveAs(str(Path(folder)/('cached.'+ext)),kind,''),'cache native line metrics')
            for ext in ('hwp','hwpx'):
                path=Path(folder)/('cached.'+ext);model=load_document(path).model
                self.assertEqual(9,sum(bool(p.line_segments) for p in model.blocks if isinstance(p,Paragraph)))
                with Document(path) as own,Document(path,'hancom') as native:
                    self.assertEqual(1,own.count)
                    result=compare(own,native)
                    self.assertTrue(result['match'],(ext,result['pages']))
                    self.assertEqual(9,own.details['pdf_native_text']['device_centered_lines'])
                    self.assertIn('AFTER',own.text(0))

    @unittest.skipUnless(os.environ.get('NARAE_RUN_HANCOM_TESTS')=='1','requires installed Hancom/fonts')
    def test_cached_and_uncached_table_pages_in_both_formats(self):
        from hangeul.hwp_automation_bridge import HancomBridge
        from test_pdf_table_frames import PdfTableFramesTests
        application()
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'source.hwpx';save_hwpx(PdfTableFramesTests.fixture(),source)
            before=source.read_bytes()
            with HancomBridge() as bridge:
                bridge.open_doc(source)
                self.assertEqual(44,int(bridge.hwp.PageCount))
                for ext,kind in (('hwp','HWP'),('hwpx','HWPX')):
                    bridge.check(bridge.hwp.SaveAs(str(Path(folder)/('cached.'+ext)),kind,''),'cache native table text')
            self.assertEqual(before,source.read_bytes())
            for ext in ('hwp','hwpx'):
                with Document(Path(folder)/('cached.'+ext)) as own,Document(Path(folder)/('cached.'+ext),'hancom') as native:
                    self.assertEqual(44,own.count)
                    result=compare(own,native)
                    for page in result['pages']:self.assertTrue(page['match'],(ext,page))
                    self.assertTrue(result['match'])
                    self.assertGreater(own.details['pdf_native_text']['device_centered_lines'],0)


if __name__=='__main__':unittest.main()
