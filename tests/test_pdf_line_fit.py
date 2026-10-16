"""Saved overflowing lines against freshly printed, whole-page native PDFs."""
from pathlib import Path
import hashlib,os,struct,sys,tempfile,unittest,zipfile,zlib
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'1_나래엔진/narae-hangul/scripts/engine'))
from hangeul.formats import Paragraph,Run,TextStyle,ParaStyle,Table,Cell,save_hwp,save_hwpx,load_document,_records,_record
from hangeul.cfb import CompoundFile,write_compound
from hangeul.geometry import DocumentModel,Border
from hangeul.viewer import application,Document,compare

TEXT='가나다 라마바사아자(차카타파) 하가'

def text_style(family,size=15,tracking=0,stretch=100,font_space=False):
    kind='HFT' if family in ('한양신명조','명조','고딕') else 'TTF'
    return TextStyle(font=family,size=size,font_type=kind,letter_spacing=tracking,stretch=stretch,
        language_fonts=(family,)*7,language_font_types=(kind,)*7,language_spacing=(tracking,)*7,
        language_ratios=(stretch,)*7,use_font_space=font_space)

def line_cases():
    cases=[]
    def add(label,runs,width=120,size=15,align='CENTER'):
        cases.append(dict(label=label,runs=runs,width=width,size=size,align=align))
    for width in (230,225,220,215,210,205.45,200,195,180,140):
        add('width/'+str(width),[Run(TEXT,text_style('HY울릉도B',tracking=-13))],width)
    for family in ('맑은 고딕','함초롬바탕','HY울릉도B','한양신명조'):
        for size,tracking,stretch,align in ((15,-13,100,'LEFT'),(13,0,100,'CENTER'),(9.5,10,95,'RIGHT'),(18,-5,105,'CENTER')):
            add(f'{family}/{size}/{tracking}/{stretch}',[Run(TEXT,text_style(family,size,tracking,stretch))],size=size,align=align)
    add('Arial/device-widths',[Run('AFTER FIRST FONT, BEFORE AFTER',text_style('Arial',13))],size=13)
    for text in ('  AFTER FIRST FONT AFTER','AFTER FIRST FONT AFTER  ','  AFTER FIRST FONT AFTER  ',
                 '가나다\u3000라마바사아자\u3000차카타파하','가나다  라마바사아자  차카타파하','  가나다 라마바사아자 차카타파하  ',
                 'AFTERFIRSTFONTBEFOREAFTER','가나다라마바사아자차카타파하가나다라','AFTER FIRST FONT! AFTER'):
        add('spaces/'+repr(text),[Run(text,text_style('맑은 고딕',13))],size=13)
    for align in ('LEFT','CENTER','RIGHT'):
        add('mixed/'+align,[Run('AFTER FIRST ',text_style('Arial')),Run('가나다 라마바사아자',text_style('맑은 고딕',tracking=-13))],align=align)
    for family in ('Arial','맑은 고딕'):
        add('font-space/'+family,[Run('AFTER FIRST FONT AFTER',text_style(family,13,font_space=True))],size=13)
    for wrap in ('SQUEEZE','KEEP'):
        for text in ('  AFTER FIRST FONT AFTER  ','\u3000가나다 라마바사아자 차카타파하\u3000'):
            add('wrap/'+wrap+'/'+repr(text),[Run(text,text_style('맑은 고딕',13))],size=13)
            cases[-1]['wrap']=wrap
    add('small-leading-space',[Run('  ',text_style('맑은 고딕',5)),Run(TEXT,text_style('맑은 고딕'))])
    return cases

def _line_data(case,cell):
    size=round(case['size']*100)
    # A text frame is quantized to four HWPUNIT before paragraph margins.
    width=round(case['width']*100)//4*4 if cell else round(case['width']*100)-2
    return (0,0,size,size,round(size*.85),round(size*.6),0,width,393216)

def cached_source(path,cases,cell):
    blocks=[];none=(Border('NONE'),)*4
    for case in cases:
        p=Paragraph(case['runs'],style=ParaStyle(align=case['align'],right=0 if cell else 481.9-case['width'],
            page_break=bool(blocks) if not cell else False,break_latin='KEEP_WORD',break_nonlatin='KEEP_WORD',line_wrap='BREAK' if cell else case.get('wrap','BREAK')))
        if not cell:blocks.append(p)
        else:
            padding=case.get('padding',(3,3,0,0));width=case['width']+padding[0]+padding[1]
            blocks.extend((Paragraph(style=ParaStyle(page_break=bool(blocks))),
                Table(1,1,[Cell(0,0,blocks=[p],width=width,height=50,padding=padding,borders=case.get('borders',none),background=case.get('background',''),vertical_align='TOP',native_flags={'SQUEEZE':8,'KEEP':16}.get(case.get('wrap'),0))],width=width,height=50,borders=none)))
    model=DocumentModel(blocks)
    if path.suffix=='.hwpx':
        save_hwpx(model,path)
        with zipfile.ZipFile(path) as z:items={n:z.read(n) for n in z.namelist()}
        section=ET.fromstring(items['Contents/section0.xml'])
        paragraphs=[p for p in section.iter() if p.tag.rsplit('}',1)[-1]=='p' and any(
            e.tag.rsplit('}',1)[-1]=='t' and e.text for run in p for e in run)]
        assert len(paragraphs)==len(cases)
        names=('textpos','vertpos','vertsize','textheight','baseline','spacing','horzpos','horzsize','flags')
        for p,case in zip(paragraphs,cases):
            ns=p.tag.rsplit('}',1)[0]+'}';array=ET.SubElement(p,ns+'linesegarray')
            ET.SubElement(array,ns+'lineseg',dict(zip(names,map(str,_line_data(case,cell)))))
        items['Contents/section0.xml']=ET.tostring(section,encoding='UTF-8',xml_declaration=True)
        with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED) as z:
            for name,data in items.items():z.writestr(name,data)
    else:
        save_hwp(model,path);cfb=CompoundFile(path.read_bytes());streams={n:cfb.read(n) for n in cfb.streams}
        compressed=bool(struct.unpack_from('<I',streams['FileHeader'],36)[0]&1)
        raw=streams['BodyText/Section0'];records=_records(zlib.decompress(raw,-15) if compressed else raw)
        out=[];case_index=0;header_index=0;visible=False
        for record in records:
            if record.tag==66:header_index=len(out);visible=False
            elif record.tag==67:
                text=record.data.decode('utf-16le')
                visible=case_index<len(cases) and text.endswith(''.join(r.text for r in cases[case_index]['runs'])+'\r')
            out.append(_record(record.tag,record.level,record.data))
            if record.tag==68 and visible:
                header=bytearray(out[header_index]);struct.pack_into('<H',header,4+16,1);out[header_index]=bytes(header)
                out.append(_record(69,record.level,struct.pack('<8iI',*_line_data(cases[case_index],cell))));case_index+=1
        assert case_index==len(cases)
        raw=b''.join(out);streams['BodyText/Section0']=zlib.compress(raw)[2:-4] if compressed else raw
        path.write_bytes(write_compound(streams,cfb))

class PdfLineFitTests(unittest.TestCase):
    def test_cell_wrap_modes_survive_both_format_roundtrips(self):
        cases=[c for c in line_cases() if c['label'].startswith('wrap/')]
        with tempfile.TemporaryDirectory() as folder:
            for ext in ('hwp','hwpx'):
                source=Path(folder)/('source.'+ext);cached_source(source,cases,True)
                model=load_document(source).model
                for target_ext,save in (('hwp',save_hwp),('hwpx',save_hwpx)):
                    target=Path(folder)/('roundtrip.'+target_ext);save(model,target)
                    cells=[b.cells[0] for b in load_document(target).model.blocks if isinstance(b,Table)]
                    self.assertEqual([c['wrap'] for c in cases],[c.blocks[0].scope['line_wrap'] for c in cells])
                    self.assertEqual([8,8,16,16],[c.native_flags&24 for c in cells])

    def test_both_fixture_formats_retain_saved_frame_widths(self):
        cases=line_cases()[:10]
        with tempfile.TemporaryDirectory() as folder:
            for cell in (False,True):
                for ext in ('hwp','hwpx'):
                    path=Path(folder)/('source.'+ext);cached_source(path,cases,cell)
                    model=load_document(path).model
                    paragraphs=[p for block in model.blocks for p in ([block] if isinstance(block,Paragraph) else [p for c in block.cells for p in c.blocks]) if p.line_segments]
                    self.assertEqual(len(cases),len(paragraphs))
                    self.assertEqual([_line_data(c,cell)[7] for c in cases],[round(p.line_segments[0]['width_pt']*100) for p in paragraphs])

    def _native(self,cell):
        application();cases=line_cases()
        with tempfile.TemporaryDirectory() as folder:
            for ext in ('hwp','hwpx'):
                source=Path(folder)/('source.'+ext);cached_source(source,cases,cell)
                original=hashlib.sha256(source.read_bytes()).hexdigest()
                with Document(source) as own,Document(source,'hancom') as native:
                    self.assertEqual(len(cases),own.count);self.assertEqual(len(cases),native.count)
                    result=compare(own,native)
                    for page,case in zip(result['pages'],cases):self.assertTrue(page['match'],(ext,cell,case['label'],page))
                    self.assertTrue(result['match'])
                    facts=own.details['pdf_native_text'];self.assertGreater(facts['fitted_lines'],25)
                    self.assertGreater(facts['condensed_word_gaps'],0);self.assertGreater(facts['condensed_character_gaps'],0)
                    self.assertFalse(facts['whole_document_parity_verified'])
                self.assertEqual(original,hashlib.sha256(source.read_bytes()).hexdigest())

    @unittest.skipUnless(os.environ.get('NARAE_RUN_HANCOM_TESTS')=='1','requires installed Hancom/fonts')
    def test_cached_body_lines_in_both_formats(self):self._native(False)

    @unittest.skipUnless(os.environ.get('NARAE_RUN_HANCOM_TESTS')=='1','requires installed Hancom/fonts')
    def test_cached_cell_lines_in_both_formats(self):self._native(True)

if __name__=='__main__':unittest.main()
