"""HWP character decoration enums, with installed Hancom as an oracle."""
from pathlib import Path
import os,struct,sys,tempfile,unittest
from dataclasses import replace

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'1_나래엔진/narae-hangul/scripts/engine'))
from hangeul.formats import (Paragraph,Run,TextStyle,DocumentModel,save_hwp,save_hwpx,load_document,
    CompoundFile,write_compound,_records,_record,_inflate,_deflate)
from hangeul.viewer import application,Document,compare


def underline_fixture(path,value,strike=False):
    save_hwp(DocumentModel([Paragraph([Run('가나 Aa0',TextStyle(font='맑은 고딕',size=13))])]),path)
    cfb=CompoundFile(path.read_bytes());streams={name:cfb.read(name) for name in cfb.streams}
    compressed=struct.unpack_from('<I',streams['FileHeader'],36)[0]&1
    records=_records(_inflate(streams['DocInfo']) if compressed else streams['DocInfo'])
    for record in records:
        if record.tag!=21:continue
        data=bytearray(record.data);flags=struct.unpack_from('<I',data,46)[0]
        struct.pack_into('<I',data,46,(flags&~(12|(7<<18)))|(value<<2)|(int(strike)<<18));record.data=bytes(data)
    raw=b''.join(_record(r.tag,r.level,r.data) for r in records)
    streams['DocInfo']=_deflate(raw) if compressed else raw
    path.write_bytes(write_compound(streams,cfb))


class TextDecorationTests(unittest.TestCase):
    def test_legacy_middle_line_maps_to_strikeout(self):
        with tempfile.TemporaryDirectory() as folder:
            for value,enabled,strike in ((0,False,False),(1,True,False),(2,False,True),(3,True,False)):
                path=Path(folder)/f'underline-{value}.hwp';underline_fixture(path,value)
                style=load_document(path).model.blocks[0].runs[0].style
                self.assertEqual(enabled,style.underline,value)
                self.assertEqual(strike,style.strike,value)

    def test_preserved_edits_normalize_legacy_strike_only_when_needed(self):
        from hangeul.hwp_native import patch_hwp_source
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'legacy.hwp';underline_fixture(source,2)
            for changes,expected in (({'strike':False},(False,False)),
                                     ({'underline':True},(True,True)),({'size':14},(False,True))):
                model=load_document(source).model;run=model.blocks[0].runs[0]
                run.style=replace(run.style,**changes);output=Path(folder)/(next(iter(changes))+'.hwp')
                self.assertTrue(patch_hwp_source(model,output),getattr(model,'_hwp_preservation_error',None))
                style=load_document(output).model.blocks[0].runs[0].style
                self.assertEqual(expected,(style.underline,style.strike))

    @unittest.skipUnless(os.environ.get('NARAE_RUN_HANCOM_TESTS')=='1','requires installed Hancom/fonts')
    def test_legacy_and_modern_strike_forms_have_same_full_pages(self):
        application()
        with tempfile.TemporaryDirectory() as folder:
            modern=Path(folder)/'modern.hwp';legacy=Path(folder)/'legacy.hwp'
            underline_fixture(modern,0,True);underline_fixture(legacy,2)
            converted=Path(folder)/'legacy.hwpx';save_hwpx(load_document(legacy).model,converted)
            with Document(modern,'hancom') as normal,Document(legacy,'hancom') as native:
                self.assertTrue(compare(normal,native)['match'])
                with Document(converted,'hancom') as native_hwpx:self.assertTrue(compare(native,native_hwpx)['match'])
            with Document(modern) as normal,Document(legacy) as own:
                self.assertTrue(compare(own,normal)['match'])
                with Document(converted) as own_hwpx:self.assertTrue(compare(own,own_hwpx)['match'])
            # This verifies two encodings of the same decoration. Complete
            # independent/native styled-text pixel parity remains separate.


if __name__=='__main__':unittest.main()
