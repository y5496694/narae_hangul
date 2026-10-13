"""Exact Unicode text for PDFs whose visible type is rendered as outlines.

Qt's PDF glyph maps omit supplementary Unicode characters. A separate,
invisible CID text layer retains the original text and selection positions.
Its embedded font contains only two blank glyphs, created here from scratch;
no installed font or native document asset is copied into this layer.
"""
from __future__ import annotations

from io import BytesIO
import os
from pathlib import Path
import tempfile
import unicodedata

from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from pypdf import PdfReader, PdfWriter
from pypdf.errors import PyPdfError
from pypdf.generic import (ArrayObject, DecodedStreamObject, DictionaryObject,
                          FloatObject, NameObject, NumberObject, TextStringObject)


def _blank_font():
    builder=FontBuilder(1000,isTTF=True)
    names=['.notdef','blank']
    builder.setupGlyphOrder(names);builder.setupCharacterMap({32:'blank'})
    builder.setupGlyf({name:TTGlyphPen(None).glyph() for name in names})
    builder.setupHorizontalMetrics({name:(500,0) for name in names})
    builder.setupHorizontalHeader(ascent=800,descent=-200)
    builder.setupNameTable({'familyName':'Narae Source Text','styleName':'Regular',
                           'uniqueFontIdentifier':'NaraeSourceText',
                           'fullName':'Narae Source Text','psName':'NaraeSourceText'})
    builder.setupOS2(sTypoAscender=800,sTypoDescender=-200,
                    usWinAscent=800,usWinDescent=200)
    builder.setupPost();builder.setupMaxp()
    output=BytesIO();builder.save(output)
    return output.getvalue()


def _stream(writer,data):
    stream=DecodedStreamObject();stream.set_data(data)
    return writer._add_object(stream.flate_encode())


def _semantic_font(writer,characters,descriptor):
    # Each occurrence owns its width, including native spaces, font stretch,
    # letter spacing, justification and combining characters of zero advance.
    widths=[FloatObject(character[3]/character[4]*1000) for character in characters]
    descendant=DictionaryObject({
        NameObject('/Type'):NameObject('/Font'),
        NameObject('/Subtype'):NameObject('/CIDFontType2'),
        NameObject('/BaseFont'):NameObject('/NaraeSourceText'),
        NameObject('/CIDSystemInfo'):DictionaryObject({
            NameObject('/Registry'):TextStringObject('Adobe'),
            NameObject('/Ordering'):TextStringObject('Identity'),
            NameObject('/Supplement'):NumberObject(0)}),
        NameObject('/FontDescriptor'):descriptor,
        NameObject('/DW'):NumberObject(500),
        NameObject('/W'):ArrayObject([NumberObject(1),ArrayObject(widths)]),
        NameObject('/CIDToGIDMap'):_stream(writer,b'\0\0'+b'\0\1'*len(characters))})
    cmap=['/CIDInit /ProcSet findresource begin','12 dict begin','begincmap',
          '/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def',
          '/CMapName /NaraeSourceText def','/CMapType 2 def',
          '1 begincodespacerange','<0000> <FFFF>','endcodespacerange']
    # The PDF CMap grammar limits each bfchar group to 100 mappings.
    for start in range(0,len(characters),100):
        group=characters[start:start+100]
        cmap.append(f'{len(group)} beginbfchar')
        cmap.extend(f'<{start+i+1:04X}> <{item[0].encode("utf-16-be").hex().upper()}>'
                    for i,item in enumerate(group))
        cmap.append('endbfchar')
    cmap.extend(['endcmap','CMapName currentdict /CMap defineresource pop','end','end'])
    return writer._add_object(DictionaryObject({
        NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type0'),
        NameObject('/BaseFont'):NameObject('/NaraeSourceText'),
        NameObject('/Encoding'):NameObject('/Identity-H'),
        NameObject('/DescendantFonts'):ArrayObject([writer._add_object(descendant)]),
        NameObject('/ToUnicode'):_stream(writer,('\n'.join(cmap)+'\n').encode('ascii'))}))


def _page_characters(layout,index):
    # Import lazily: source_layout calls this helper after its painter closes.
    from .source_layout import TEXT_UNIT_PT, SCRIPT_OFFSET_PROPERTY
    characters=[]
    for command in layout.pages[index]:
        if command.kind!='text':continue
        text_layout=layout._text_layout(command);line=text_layout.lineAt(0)
        formats=text_layout.formats();cursor=0
        for character in command.extra['text']:
            units=len(character.encode('utf-16-le'))//2
            first=line.cursorToX(cursor)[0];last=line.cursorToX(cursor+units)[0]
            start=cursor;cursor+=units
            if character=='\ufffc' or unicodedata.category(character) in {'Cc','Cf'}:continue
            fmt=next((item.format for item in formats if item.start<=start<item.start+item.length),None)
            offset=(fmt.property(SCRIPT_OFFSET_PROPERTY) or 0) if fmt else 0
            runs=text_layout.glyphRuns(start,units)
            raw=next((run.rawFont() for run in runs if run.rawFont().isValid()),None)
            position=next((run.positions()[0] for run in runs if run.positions()),None)
            size=raw.pixelSize()*TEXT_UNIT_PT if raw is not None else 10
            baseline=(position.y()*TEXT_UNIT_PT if position is not None
                      else command.extra['line']['baseline_pt'])
            characters.append((character,command.rect[0]+min(first,last)*TEXT_UNIT_PT,
                               command.rect[1]+baseline+offset,abs(last-first)*TEXT_UNIT_PT,
                               max(.01,size)))
    return characters


def add_source_text(path,layout):
    """Atomically add searchable text after QPainter.end(); return if added.

    A copy-restricted HWP distribution view must retain its visual-only PDF.
    The caller's existing temporary export remains safe if processing fails.
    """
    if int(getattr(layout.model,'_hwp_source',{}).get('distribution_options',0))&1:return False
    pages=[_page_characters(layout,index) for index in range(len(layout.pages))]
    if not any(pages):return False
    path=Path(path);temporary=None
    try:
        reader=PdfReader(BytesIO(path.read_bytes()))
        if len(reader.pages)!=len(pages):raise ValueError('PDF 쪽 수와 원본 배치가 다릅니다.')
        writer=PdfWriter();writer.clone_document_from_reader(reader)
        font_data=_blank_font();font_file=DecodedStreamObject();font_file.set_data(font_data)
        font_file[NameObject('/Length1')]=NumberObject(len(font_data))
        descriptor=writer._add_object(DictionaryObject({
            NameObject('/Type'):NameObject('/FontDescriptor'),
            NameObject('/FontName'):NameObject('/NaraeSourceText'),NameObject('/Flags'):NumberObject(4),
            NameObject('/FontBBox'):ArrayObject([NumberObject(v) for v in (0,-200,1000,800)]),
            NameObject('/ItalicAngle'):NumberObject(0),NameObject('/Ascent'):NumberObject(800),
            NameObject('/Descent'):NumberObject(-200),NameObject('/CapHeight'):NumberObject(800),
            NameObject('/StemV'):NumberObject(0),NameObject('/FontFile2'):writer._add_object(font_file)}))
        for page,characters in zip(writer.pages,pages):
            if not characters:continue
            resources=page['/Resources'].get_object()
            fonts=resources.setdefault(NameObject('/Font'),DictionaryObject()).get_object()
            content=['q','BT','3 Tr']
            height=float(page.mediabox.top)
            for start in range(0,len(characters),65535):
                group=characters[start:start+65535]
                name=NameObject(f'/NaraeSourceText{start//65535}')
                fonts[name]=_semantic_font(writer,group,descriptor)
                for cid,(_,x,baseline,_,size) in enumerate(group,1):
                    content.extend([f'{name} {size:.6f} Tf',
                                    f'1 0 0 1 {x:.6f} {height-baseline:.6f} Tm <{cid:04X}> Tj'])
            content.extend(['ET','Q'])
            # Qt establishes a page-wide top-left transform outside q/Q.
            # Balance it so the new layer can use ordinary PDF point units.
            original=page.get_contents().get_data()
            page[NameObject('/Contents')]=_stream(writer,b'q\n'+original+b'\nQ\n'+
                                                ('\n'.join(content)+'\n').encode('ascii'))
        writer.compress_identical_objects(remove_duplicates=False,remove_unreferenced=True)
        fd,name=tempfile.mkstemp(prefix='.narae-text-',suffix='.pdf',dir=path.parent)
        os.close(fd);temporary=Path(name)
        with temporary.open('wb') as output:writer.write(output)
        writer.close();reader.close()
        os.replace(temporary,path)
        return True
    except (OSError,ValueError,PyPdfError) as exc:
        raise OSError(f'PDF 검색용 텍스트를 저장하지 못했습니다: {exc}') from exc
    finally:
        if temporary:
            try:temporary.unlink(missing_ok=True)
            except OSError:pass
