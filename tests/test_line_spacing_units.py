"""Point/character line units and source-preserving edits against Hancom."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import hashlib,os,struct,sys,tempfile,unittest,zipfile,zlib
from xml.etree import ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'1_나래엔진/narae-hangul/scripts/engine'))
from hangeul.formats import Paragraph,ParaStyle,Run,TextStyle,save_hwp,save_hwpx,load_document,_records,_record
from hangeul.cfb import CompoundFile,write_compound
from hangeul.geometry import DocumentModel
from hangeul.viewer import application,Document,compare

TEXT='가나다라\n마바사아\n자차카타\n파하가나\n다라마바사\n아자차카'


def point_model():
    blocks=[]
    for kind in ('FIXED','BETWEEN_LINES','AT_LEAST'):
        for value in (0,3.01,3.02,3.03,16.01,16.02,16.03,20):
            style=TextStyle(font='맑은 고딕',size=11.03)
            blocks.append(Paragraph([Run(TEXT,style)],default_style=style,end_style=style,
                style=ParaStyle(line_type=kind,line=value,page_break=bool(blocks))))
    return DocumentModel(blocks)


def character_source(path,normal_size=11):
    cases=[];blocks=[]
    for base in (11,16,20):
        for size in (8,14):
            for kind in ('FIXED','BETWEEN_LINES','AT_LEAST'):
                for value in (100,151,200):
                    font=TextStyle(font='맑은 고딕',size=size)
                    blocks.append(Paragraph([Run(TEXT,font)],default_style=font,end_style=font,
                        style=ParaStyle(line_type=kind,line=value/100,page_break=bool(blocks))))
                    cases.append((base,value,kind))
    save_hwpx(DocumentModel(blocks),path)
    with zipfile.ZipFile(path) as archive:items={n:archive.read(n) for n in archive.namelist()}
    head=ET.fromstring(items['Contents/header.xml']);section=ET.fromstring(items['Contents/section0.xml'])
    for line in head.findall('.//{*}lineSpacing'):
        if line.get('type')!='PERCENT':line.set('unit','CHAR')
    char_parent=head.find('.//{*}charProperties');style_parent=head.find('.//{*}styles')
    base_char=char_parent[0];base_style=style_parent[0];style_ids={11:'0'}
    base_char.set('height',str(normal_size*100))
    for base in (16,20):
        char=deepcopy(base_char);char_id=str(len(char_parent));char.set('id',char_id);char.set('height',str(base*100));char_parent.append(char)
        style=deepcopy(base_style);style_id=str(len(style_parent));style.set('id',style_id);style.set('name',f'Base{base}');style.set('engName',f'Base{base}');style.set('charPrIDRef',char_id);style_parent.append(style);style_ids[base]=style_id
    char_parent.set('itemCnt',str(len(char_parent)));style_parent.set('itemCnt',str(len(style_parent)))
    for paragraph,case in zip(list(section),cases):paragraph.set('styleIDRef',style_ids[case[0]])
    items['Contents/header.xml']=ET.tostring(head,encoding='UTF-8',xml_declaration=True)
    items['Contents/section0.xml']=ET.tostring(section,encoding='UTF-8',xml_declaration=True)
    with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED) as archive:
        for name,data in items.items():archive.writestr(name,data)
    return cases


def legacy_source(path):
    model=point_model();model.blocks=model.blocks[:8];save_hwp(model,path)
    cfb=CompoundFile(path.read_bytes());streams={n:cfb.read(n) for n in cfb.streams}
    header=bytearray(streams['FileHeader']);struct.pack_into('<I',header,32,0x05000107);streams['FileHeader']=bytes(header)
    data=zlib.decompress(streams['DocInfo'],-15)
    data=b''.join(_record(r.tag,r.level,r.data[:46] if r.tag==25 else r.data) for r in _records(data))
    streams['DocInfo']=zlib.compress(data)[2:-4];path.write_bytes(write_compound(streams,cfb))


class LineSpacingUnitTests(unittest.TestCase):
    def assert_native(self,path,count):
        sha=hashlib.sha256(path.read_bytes()).hexdigest()
        with Document(path) as own,Document(path,'hancom') as native:
            self.assertEqual(count,own.count);self.assertEqual(count,native.count)
            result=compare(own,native)
            self.assertTrue(result['match'],(path.name,[(p['page'],p['changed_pixels']) for p in result['pages'] if not p['match']]))
        self.assertEqual(sha,hashlib.sha256(path.read_bytes()).hexdigest())

    def test_point_model_round_trip_and_source_preserving_edits(self):
        from hangeul.hwp_native import patch_hwp_source
        from hangeul.hwpx_native import patch_hwpx_source
        with tempfile.TemporaryDirectory() as folder:
            expected=[(p.style.line_type,p.style.line) for p in point_model().blocks]
            for ext,save,patch in (('hwp',save_hwp,patch_hwp_source),('hwpx',save_hwpx,patch_hwpx_source)):
                path=Path(folder)/('source.'+ext);save(point_model(),path);model=load_document(path).model
                self.assertEqual(expected,[(p.style.line_type,p.style.line) for p in model.blocks])
                for p in model.blocks:p.style=replace(p.style,line_type='FIXED',line=7.17)
                target=Path(folder)/('edited.'+ext);self.assertTrue(patch(model,target))
                self.assertTrue(all((p.style.line_type,p.style.line)==('FIXED',7.17) for p in load_document(target).model.blocks))

    def test_absolute_point_hwp_and_hwpx_pixels_are_identical(self):
        application()
        with tempfile.TemporaryDirectory() as folder:
            hwp=Path(folder)/'source.hwp';hwpx=Path(folder)/'source.hwpx'
            save_hwp(point_model(),hwp);save_hwpx(point_model(),hwpx)
            with Document(hwp) as first,Document(hwpx) as second:
                self.assertEqual(24,first.count)
                self.assertTrue(compare(first,second)['match'])

    @unittest.skipUnless(os.environ.get('NARAE_RUN_HANCOM_TESTS')=='1','requires installed Hancom/fonts')
    def test_absolute_spacing_modes_and_overlap_in_both_formats(self):
        application()
        with tempfile.TemporaryDirectory() as folder:
            for ext,save in (('hwp',save_hwp),('hwpx',save_hwpx)):
                path=Path(folder)/('source.'+ext);save(point_model(),path);self.assert_native(path,24)

    @unittest.skipUnless(os.environ.get('NARAE_RUN_HANCOM_TESTS')=='1','requires installed Hancom/fonts')
    def test_character_units_use_paragraph_style_across_native_formats(self):
        from hangeul.hwp_automation_bridge import HancomBridge
        application()
        with tempfile.TemporaryDirectory() as folder:
            for normal_size in (11,20):
                directory=Path(folder)/str(normal_size);directory.mkdir()
                source=directory/'source.hwpx';cases=character_source(source,normal_size)
                with HancomBridge() as bridge:
                    bridge.open_doc(source)
                    for ext in ('hwp','hwpx'):
                        bridge.check(bridge.hwp.SaveAs(str(directory/('native.'+ext)),ext.upper(),''),'convert character units')
                for path in (source,directory/'native.hwp',directory/'native.hwpx'):
                    model=load_document(path).model
                    for p,(_,value,kind) in zip(model.blocks,cases):
                        self.assertEqual(kind,p.style.line_type)
                        self.assertAlmostEqual(int(normal_size*value/2+.5)/100,p.style.line)
                    self.assert_native(path,54)

    @unittest.skipUnless(os.environ.get('NARAE_RUN_HANCOM_TESTS')=='1','requires installed Hancom/fonts')
    def test_preserved_direct_and_versioned_spacing_edits(self):
        from hangeul.hwp_automation_bridge import HancomBridge
        from hangeul.hwp_native import patch_hwp_source
        from hangeul.hwpx_native import patch_hwpx_source
        application()
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'direct.hwpx';save_hwpx(point_model(),source)
            with HancomBridge() as bridge:
                bridge.open_doc(source)
                for ext in ('hwp','hwpx'):
                    bridge.check(bridge.hwp.SaveAs(str(Path(folder)/('versioned.'+ext)),ext.upper(),''),'native units')
            for original in (source,Path(folder)/'versioned.hwp',Path(folder)/'versioned.hwpx'):
                model=load_document(original).model
                for i,p in enumerate(model.blocks):
                    kind,value=(('PERCENT',150),('FIXED',6.01),('BETWEEN_LINES',1.03),('AT_LEAST',16.03))[i%4]
                    p.style=replace(p.style,line_type=kind,line=value)
                target=original.with_stem(original.stem+'-edited')
                patch=patch_hwp_source if original.suffix=='.hwp' else patch_hwpx_source
                self.assertTrue(patch(model,target))
                self.assertEqual([(p.style.line_type,p.style.line) for p in model.blocks],
                    [(p.style.line_type,p.style.line) for p in load_document(target).model.blocks])
                self.assert_native(target,24)

    @unittest.skipUnless(os.environ.get('NARAE_RUN_HANCOM_TESTS')=='1','requires installed Hancom/fonts')
    def test_preserved_character_unit_edits_have_requested_point_values(self):
        from hangeul.hwp_automation_bridge import HancomBridge
        from hangeul.hwp_native import patch_hwp_source
        from hangeul.hwpx_native import patch_hwpx_source
        application()
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'direct.hwpx';character_source(source,20)
            with HancomBridge() as bridge:
                bridge.open_doc(source)
                for ext in ('hwp','hwpx'):
                    bridge.check(bridge.hwp.SaveAs(str(Path(folder)/('versioned.'+ext)),ext.upper(),''),'native character units')
            for original in (source,Path(folder)/'versioned.hwp',Path(folder)/'versioned.hwpx'):
                model=load_document(original).model
                for i,p in enumerate(model.blocks):
                    kind,value=(('PERCENT',125),('FIXED',6.01),('BETWEEN_LINES',1.03),('AT_LEAST',16.03))[i%4]
                    p.style=replace(p.style,line_type=kind,line=value)
                target=original.with_stem(original.stem+'-edited')
                patch=patch_hwp_source if original.suffix=='.hwp' else patch_hwpx_source
                self.assertTrue(patch(model,target))
                self.assertEqual([(p.style.line_type,p.style.line) for p in model.blocks],
                    [(p.style.line_type,p.style.line) for p in load_document(target).model.blocks])
                self.assert_native(target,54)

    @unittest.skipUnless(os.environ.get('NARAE_RUN_HANCOM_TESTS')=='1','requires installed Hancom/fonts')
    def test_legacy_hwp_spacing_fields(self):
        application()
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'legacy.hwp';legacy_source(path);self.assert_native(path,8)


if __name__=='__main__':unittest.main()
