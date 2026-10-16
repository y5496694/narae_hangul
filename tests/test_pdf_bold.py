"""Real and emulated bold text against complete native pages."""
from pathlib import Path
from dataclasses import replace
import hashlib,os,sys,tempfile,unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'1_나래엔진/narae-hangul/scripts/engine'))
from hangeul.formats import Paragraph,ParaStyle,Run,save_hwp,save_hwpx
from hangeul.geometry import DocumentModel
from hangeul.viewer import application,Document,compare
from test_pdf_native_text import style


def cases():
    result=[]
    for family in ('Arial','휴먼명조','함초롬바탕','한양신명조','HCI Poppy'):
        text='AFTER FIRST FONT' if family in ('Arial','HCI Poppy') else '가나다 라마바사 아자차'
        for size in (8,13,24):
            result.append((family+'/'+str(size),[Run(text,style(family,size,bold=True))]))
        normal=style(family,13);bold=replace(normal,bold=True,color='#c63726')
        result.append((family+'/mixed',[Run(text+' ',normal),Run(text+' ',bold),Run(text,normal)]))
    for family in ('Arial','휴먼명조','함초롬바탕'):
        text='AFTER FIRST FONT' if family=='Arial' else '가나다 라마바사 아자차'
        for stretch,tracking in [(95,-13),(105,10)]:
            result.append((family+f'/{stretch}/{tracking}',[Run(text,style(family,13,bold=True,stretch=stretch,
                letter_spacing=tracking,language_ratios=(stretch,)*7,language_spacing=(tracking,)*7))]))
    return result


class PdfBoldTests(unittest.TestCase):
    def _native(self,cached):
        from hangeul.hwp_automation_bridge import HancomBridge
        application();values=cases()
        model=DocumentModel([Paragraph(runs,style=ParaStyle(page_break=index>0,align='CENTER'))
            for index,(_,runs) in enumerate(values)])
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            for ext,save in [('hwp',save_hwp),('hwpx',save_hwpx)]:
                source=root/('source.'+ext);save(model,source);before=hashlib.sha256(source.read_bytes()).hexdigest()
                if cached:
                    target=root/('cached.'+ext);reference=root/(ext+'-native.pdf')
                    with HancomBridge() as bridge:
                        bridge.open_doc(source);action=bridge.hwp.CreateAction('PrintToPDFEx');settings=action.CreateSet()
                        bridge.check(action.GetDefault(settings),'defaults')
                        for key,value in dict(Range=6,RangeCustom='',RangeIncludeLinkedDoc=0,NumCopy=1,
                            PrintMethod=0,PrintToFile=1,FileName=str(reference),ReverseOrder=0,
                            EvenOddPageType=0,ZoomX=100,ZoomY=100,PrintWithoutBlank=0).items():settings.SetItem(key,value)
                        bridge.check(action.Execute(settings),'print')
                        bridge.check(bridge.hwp.SaveAs(str(target),ext.upper(),''),'cache')
                    native=Document(reference)
                else:target=source;native=Document(source,'hancom')
                with native,Document(target) as own:
                    self.assertEqual(len(values),native.count);self.assertEqual(native.count,own.count)
                    result=compare(own,native)
                    for page,case in zip(result['pages'],values):self.assertTrue(page['match'],(ext,cached,case[0],page))
                    self.assertTrue(result['match'])
                    facts=own.details['pdf_native_text'];self.assertEqual(0,facts['legacy_lines'])
                    self.assertGreater(facts['synthetic_bold_ttf_glyphs'],0);self.assertGreater(facts['synthetic_bold_hft_glyphs'],0)
                    self.assertFalse(facts['whole_document_parity_verified'])
                self.assertEqual(before,hashlib.sha256(source.read_bytes()).hexdigest())

    @unittest.skipUnless(os.environ.get('NARAE_RUN_HANCOM_TESTS')=='1','requires installed Hancom/fonts')
    def test_uncached_bold_in_both_formats(self):self._native(False)

    @unittest.skipUnless(os.environ.get('NARAE_RUN_HANCOM_TESTS')=='1','requires installed Hancom/fonts')
    def test_saved_bold_in_both_formats(self):self._native(True)


if __name__=='__main__':unittest.main()
