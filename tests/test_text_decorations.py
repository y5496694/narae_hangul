"""HWP character decoration enums, with installed Hancom as an oracle."""
from pathlib import Path
import os,struct,sys,tempfile,unittest
from dataclasses import replace

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'1_나래엔진/narae-hangul/scripts/engine'))
from hangeul.formats import (Paragraph,ParaStyle,Run,TextStyle,DocumentModel,save_hwp,save_hwpx,load_document,STRIKE_SHAPES,
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
    def test_strike_shapes_and_colors_round_trip_in_both_formats(self):
        paragraphs=[Paragraph([Run('가나 Aa0',TextStyle(font='맑은 고딕',size=13,strike=True,
            strike_shape=shape,strike_color='#e72d11'))]) for shape in STRIKE_SHAPES]
        with tempfile.TemporaryDirectory() as folder:
            for ext,save in (('hwp',save_hwp),('hwpx',save_hwpx)):
                source=Path(folder)/('shapes.'+ext);save(DocumentModel(paragraphs),source)
                for shape,paragraph in zip(STRIKE_SHAPES,load_document(source).model.blocks):
                    self.assertEqual((True,shape,'#e72d11'),(paragraph.runs[0].style.strike,
                        paragraph.runs[0].style.strike_shape,paragraph.runs[0].style.strike_color))

    def test_shape_and_color_survive_source_preserving_edits(self):
        from hangeul.hwp_native import patch_hwp_source
        from hangeul.hwpx_native import patch_hwpx_source
        with tempfile.TemporaryDirectory() as folder:
            for ext,save,patch in (('hwp',save_hwp,patch_hwp_source),('hwpx',save_hwpx,patch_hwpx_source)):
                source=Path(folder)/('original.'+ext)
                save(DocumentModel([Paragraph([Run('한글',TextStyle(strike=True,strike_shape='3D',strike_color='#2255ee'))])]),source)
                for changes in ({'size':14},{'strike_shape':'SOLID','strike_color':'#ee2255'}):
                    model=load_document(source).model;run=model.blocks[0].runs[0]
                    expected=replace(run.style,**changes);run.style=expected
                    target=Path(folder)/(next(iter(changes))+'.'+ext)
                    self.assertTrue(patch(model,target),getattr(model,'_hwp_preservation_error',None))
                    actual=load_document(target).model.blocks[0].runs[0].style
                    self.assertEqual((expected.strike_shape,expected.strike_color),(actual.strike_shape,actual.strike_color))

    def test_legacy_middle_line_preserves_its_own_shape_and_color(self):
        from hangeul.hwp_native import patch_hwp_source
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'legacy.hwp';underline_fixture(source,2,True)
            cfb=CompoundFile(source.read_bytes());streams={name:cfb.read(name) for name in cfb.streams}
            records=_records(_inflate(streams['DocInfo']))
            for record in records:
                if record.tag!=21:continue
                data=bytearray(record.data);flags=struct.unpack_from('<I',data,46)[0]
                struct.pack_into('<I',data,46,(flags&~((15<<4)|(15<<26)))|(15<<4))
                struct.pack_into('<I',data,56,0xff);struct.pack_into('<I',data,70,0xff0000)
                record.data=bytes(data)
            streams['DocInfo']=_deflate(b''.join(_record(r.tag,r.level,r.data) for r in records));source.write_bytes(write_compound(streams,cfb))
            model=load_document(source).model;run=model.blocks[0].runs[0]
            self.assertEqual(('3D','#ff0000'),(run.style.strike_shape,run.style.strike_color))
            run.style=replace(run.style,underline=True)
            target=Path(folder)/'modern.hwp';self.assertTrue(patch_hwp_source(model,target))
            style=load_document(target).model.blocks[0].runs[0].style
            self.assertEqual((True,True,'3D','#ff0000'),(style.underline,style.strike,style.strike_shape,style.strike_color))

    def test_older_session_metadata_keeps_unmodeled_strike_fields(self):
        from hangeul.hwp_native import patch_hwp_source
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'older.hwp'
            save_hwp(DocumentModel([Paragraph([Run('한글',TextStyle(strike=True,strike_shape='3D',strike_color='#2255ee'))])]),source)
            model=load_document(source).model
            def strip_new_fields(value):
                if isinstance(value,dict):
                    value.pop('strike_shape',None);value.pop('strike_color',None)
                    for child in value.values():strip_new_fields(child)
                elif isinstance(value,list):
                    for child in value:strip_new_fields(child)
            strip_new_fields(model._hwp_source['baseline'])
            model._hwp_source['char_styles']={i:replace(s,strike_shape='SOLID',strike_color='')
                for i,s in model._hwp_source['char_styles'].items()}
            run=model.blocks[0].runs[0];run.style=replace(run.style,size=14)
            target=Path(folder)/'edited.hwp';self.assertTrue(patch_hwp_source(model,target))
            actual=load_document(target).model.blocks[0].runs[0].style
            self.assertEqual((14,'3D','#2255ee'),(actual.size,actual.strike_shape,actual.strike_color))

    @staticmethod
    def invisible_strike_model(strike=True):
        paragraphs=[]
        for family in ('맑은 고딕','함초롬돋움','휴먼명조','굴림','한양신명조','HCI Poppy'):
            kind='HFT' if family in ('한양신명조','HCI Poppy') else 'TTF'
            for size in (5,13):
                for shape in STRIKE_SHAPES[13:]:
                    style=TextStyle(font=family,size=size,color='#2939e7',strike=strike,
                        strike_shape=shape,strike_color='#e72d11',font_type=kind,
                        language_fonts=(family,)*7,language_font_types=(kind,)*7)
                    text='AbZ01!?' if family=='HCI Poppy' else '가나A a0漢'
                    paragraphs.append(Paragraph([Run(text,style)],style=ParaStyle(page_break=bool(paragraphs))))
        return DocumentModel(paragraphs)

    @unittest.skipUnless(os.environ.get('NARAE_RUN_HANCOM_TESTS')=='1','requires installed Hancom/fonts')
    def test_all_shape_encodings_survive_native_format_conversion(self):
        from hangeul.hwp_automation_bridge import HancomBridge
        paragraphs=[Paragraph([Run('가나 Aa0',TextStyle(font='맑은 고딕',size=13,strike=True,
            strike_shape=shape,strike_color='#e72d11'))],style=ParaStyle(page_break=bool(index)))
            for index,shape in enumerate(STRIKE_SHAPES)]
        with tempfile.TemporaryDirectory() as folder:
            with HancomBridge() as bridge:
                for ext,save,target_ext in (('hwp',save_hwp,'hwpx'),('hwpx',save_hwpx,'hwp')):
                    source=Path(folder)/('shapes.'+ext);save(DocumentModel(paragraphs),source)
                    bridge.open_doc(source)
                    target=Path(folder)/('native-from-'+ext+'.'+target_ext)
                    bridge.check(bridge.hwp.SaveAs(str(target),target_ext.upper(),''),'native shape conversion')
                    converted=[p.runs[0].style for p in load_document(target).model.blocks if isinstance(p,Paragraph)]
                    self.assertEqual(list(STRIKE_SHAPES),[style.strike_shape for style in converted])
                    self.assertTrue(all(style.strike and style.strike_color.lower()=='#e72d11' for style in converted),
                        (ext,[(style.strike,style.strike_color) for style in converted]))

    @unittest.skipUnless(os.environ.get('NARAE_RUN_HANCOM_TESTS')=='1','requires installed Hancom/fonts')
    def test_invisible_strike_shapes_match_native_complete_pages_in_both_formats(self):
        application()
        with tempfile.TemporaryDirectory() as folder:
            for ext,save in (('hwp',save_hwp),('hwpx',save_hwpx)):
                normal=Path(folder)/('normal.'+ext);source=Path(folder)/('invisible.'+ext)
                save(self.invisible_strike_model(False),normal);save(self.invisible_strike_model(),source)
                with Document(normal,'hancom') as plain,Document(source,'hancom') as native,Document(source) as own:
                    self.assertTrue(compare(plain,native)['match'])
                    result=compare(own,native)
                    for index,page in enumerate(result['pages']):self.assertTrue(page['match'],(ext,index+1,page))
                    self.assertEqual(36,own.count);self.assertEqual(0,own.details['pdf_native_text']['legacy_lines'])
                    self.assertEqual(252,own.details['pdf_native_text']['suppressed_strike_glyphs'])

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
                with Document(converted,'hancom') as native_hwpx:
                    result=compare(native,native_hwpx)
                    self.assertTrue(result['match'],result['pages'])
            with Document(modern) as normal,Document(legacy) as own:
                self.assertTrue(compare(own,normal)['match'])
                with Document(converted) as own_hwpx:self.assertTrue(compare(own,own_hwpx)['match'])
            # This verifies two encodings of the same decoration. Complete
            # independent/native styled-text pixel parity remains separate.


if __name__=='__main__':unittest.main()
