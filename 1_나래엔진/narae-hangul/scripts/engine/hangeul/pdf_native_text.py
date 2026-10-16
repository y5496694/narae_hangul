"""Independent HFT Type3 / installed TrueType PDF text at printer coordinates.

Qt supplies shaping and an off-paper placement token. After painting, replace
only that token with source font programs, retaining the page's drawing order.
No Hancom library, reference PDF or captured glyph is used by this module.
"""
from io import BytesIO
from pathlib import Path
import hashlib
import math
import os
import struct
import tempfile
import unicodedata

from .font_resolver import ALIASES,LATIN_ONLY,HFT_HANGUL_FALLBACKS,MAX_SFNT_BYTES,_inside,_read_bounded,installed_default_fonts
from .hft import HftError
from .hft_cff import normalized_operations
from .pdf_bitmap import printer_pixel


MAX_FONTS=64
MAX_SOURCE_BYTES=256*1024*1024
MAX_CHARACTERS=1_000_000
MAX_LINES=100_000
MARKER_PREFIX=b'NARAE\x02'
PATH_MARKER_PREFIX=b'NARAE\x03'


def _round(value):return math.floor(value+.5+1e-9)


def _multiply(a,b):
    x,y,z,w,t,u=a;A,B,C,D,E,F=b
    return (x*A+y*C,x*B+y*D,z*A+w*C,z*B+w*D,t*A+u*C+E,t*B+u*D+F)


def _inverse(matrix):
    a,b,c,d,e,f=matrix;det=a*d-b*c
    if not math.isfinite(det) or abs(det)<1e-20:raise ValueError('Singular PDF text placement')
    return (d/det,-b/det,-c/det,a/det,(c*f-d*e)/det,(b*e-a*f)/det)


def _numbers(values):return ' '.join(f'{value:.15f}'.rstrip('0').rstrip('.') if value else '0' for value in values)


class PdfNativeText:
    def __init__(self,owner):
        self.owner=owner
        self.hft_sources={};self.sfnt_sources={};self.fonts={};self.records={};self.path_records={};self.record_clips={}
        self.collection_index=None
        self.script_defaults={};self.requested_fonts={};self.hanja_fonts={}
        self.source_bytes=0;self.characters=0;self.device_widths={}
        self.precision=6
        self.facts={'processed_lines':0,'legacy_lines':0,'glyphs':0,'fonts':[],
                    'legacy_reasons':{},'number_precision':6,'glyph_fallbacks':{},'suppressed_strike_glyphs':0,'stretched_glyphs':0,
                    'device_centered_lines':0,'device_metric_fallbacks':{},'fitted_lines':0,
                    'condensed_word_gaps':0,'condensed_character_gaps':0,'cell_clipped_lines':0,'cell_clipped_painter_lines':0,'whole_document_parity_verified':False}

    def _center_cached_line(self,command,text,parts,metrics):
        # A saved, single-line paragraph uses device-centered widths. Uncached
        # paragraphs retain logical widths; a line cache is not a page-number
        # heuristic. Tracking follows device centering of untracked widths.
        if not command.source.line_segments:return parts,None
        text=text.replace('\u200b','')
        reason=None
        if len(command.source.line_segments)!=1:reason='multiline-paragraph'
        elif len(metrics)!=len(text) or any(ord(c)>=65536 or c=='!' for c in text):reason='control-or-postprocessed-character'
        elif len({item[:4] for item in metrics})!=1:reason='mixed-font-metrics'
        elif metrics:
            key,em,stretch,tracking,*_=metrics[0]
            if self.fonts[key]['kind']!='ttf':return parts,None
            if stretch!=100:reason='stretch'
        if reason or not metrics:
            if reason:
                facts=self.facts['device_metric_fallbacks'];facts[reason]=facts.get(reason,0)+1
            return parts,None
        from .pdf_device_metrics import centered_advances,installed_widths,tracked_advances
        key,em,*_=metrics[0];source=self.fonts[key];identity=(key,em)
        widths=self.device_widths.setdefault(identity,{})
        missing=''.join(set(text)-widths.keys())
        try:
            if missing:
                # Loaded fontTools tables may recompile timestamps or names;
                # GDI identity must be checked against original source bytes.
                tables={tag:source['source'].reader[tag] for tag in ('head','maxp','name')}
                widths.update(installed_widths(source['family'],em,missing,tables))
            advances=[item[5] for item in metrics]
            # Each word's measurement run includes its trailing spaces.
            # Spaces flush the word's endpoint carry, then their paragraph
            # width is restored by character postprocessing.
            first=0
            while first<len(text):
                if text[first]==' ':first+=1;continue
                last=first+1
                while last<len(text) and text[last]!=' ' and parts[last][6]==parts[first][6]:last+=1
                while last<len(text) and text[last]==' ':last+=1
                advances[first:last]=centered_advances(text[first:last],advances[first:last],
                    [widths[c] for c in text[first:last]])
                first=last
            advances=[item[5] if character==' ' else advance
                      for character,item,advance in zip(text,metrics,advances)]
            advances=tracked_advances(text,advances,metrics[0][3])
        except (OSError,ValueError) as error:
            reason=str(error) if isinstance(error,ValueError) else 'device-metrics-error'
            facts=self.facts['device_metric_fallbacks'];facts[reason]=facts.get(reason,0)+1
            return parts,None
        pen=0;alignment=0;result=[]
        for character,part,advance in zip(text,parts,advances):
            result.append((pen,*part[1:]));pen+=advance
            if character!=' ':alignment=pen
        self.facts['device_centered_lines']+=1
        return result,alignment

    def _fit_saved_line(self,command,text,parts,metrics,alignment):
        if not command.source.line_segments:return parts,False
        from .pdf_line_fit import condense_line
        text=text.replace('\u200b','')
        if not metrics or len(text)!=len(metrics):return parts,False
        # The existing alignment width excludes trailing ordinary spaces.
        # Device-centered lines may have a different final advance, so use
        # that actual width instead of rebuilding it from source metrics.
        end=len(text.rstrip(' '));total=alignment+sum(m[4] for m in metrics[end:])
        advances=[parts[i+1][0]-part[0] if i+1<len(parts) else total-part[0] for i,part in enumerate(parts)]
        scope=command.source.scope;all_gaps=command.source.style.line_wrap=='SQUEEZE'
        while scope:
            all_gaps=all_gaps or scope.get('line_wrap','BREAK')!='BREAK'
            scope=scope.get('parent')
        result=condense_line(text,advances,[m[5] for m in metrics],round(command.extra['line']['width_pt']*100),all_gaps)
        if result is None:return parts,False
        pen=0;fitted=[]
        for part,advance in zip(parts,result['advances']):fitted.append((pen,*part[1:]));pen+=advance
        self.facts['fitted_lines']+=1
        if result['word_adjustment']:self.facts['condensed_word_gaps']+=result['word_gaps']
        if result['character_adjustment']:self.facts['condensed_character_gaps']+=result['character_gaps']
        return fitted,True

    def _hanja_fallback(self,font,character,raw,index):
        from PySide6.QtGui import QRawFont,QFont
        from .source_layout import _language_index
        if _language_index(character)[0]!=2:return raw,index
        key=(font.family(),font.styleName(),font.weight())
        if key not in self.requested_fonts:self.requested_fonts[key]=QRawFont.fromFont(font)
        if self.requested_fonts[key].supportsCharacter(ord(character)):return raw,index
        if 'Hanja' not in self.script_defaults:self.script_defaults['Hanja']=installed_default_fonts('Hanja')[0]
        for family in self.script_defaults['Hanja']:
            if family not in self.hanja_fonts:
                candidate=QFont(font);candidate.setFamily(self.owner._font_family(family))
                self.hanja_fonts[family]=QRawFont.fromFont(candidate)
            candidate=self.hanja_fonts[family]
            if candidate.supportsCharacter(ord(character)):
                target=candidate.familyName();label=font.family()+' → '+target
                self.facts['glyph_fallbacks'][label]=self.facts['glyph_fallbacks'].get(label,0)+1
                return candidate,candidate.glyphIndexesForString(character)[0]
        return raw,index

    def observe_command(self,command):
        # Native polygon/Bezier serialization leaves fixed precision(3) on
        # its page stream. q/Q and page changes do not restore this C++ state.
        # Straight LineTo borders and images use other serialization paths.
        from PySide6.QtGui import QColor
        if self.precision==3:return
        source=command.source;change=False
        if command.kind=='box' and getattr(source,'kind',None)!='line':
            change=QColor(getattr(source,'background','')).isValid() or any(
                border.style!='NONE' and border.width_pt>0 for border in getattr(source,'borders',()))
        elif command.kind=='cell':change=QColor(source.background).isValid()
        elif command.kind=='text':
            change=QColor(source.style.background).isValid() or any(QColor(run.style.background).isValid() for run in source.runs)
        if change:self.precision=3;self.facts['number_precision']=3

    def _hft(self,family,character):
        family=ALIASES.get(family,family)
        if family not in self.hft_sources:self.hft_sources[family]=self.owner._hft_resolver._sources(family)[0]
        sources=self.hft_sources[family]
        if character==' ' and family not in LATIN_ONLY:sources=sources[1:2]
        for source in sources:
            try:glyph=source.glyph(character)
            except (HftError,UnicodeError):continue
            if glyph.kind!='outline':continue
            fallback=HFT_HANGUL_FALLBACKS.get(family)
            script_fallback=bool(fallback and source.path.name==fallback[1])
            # One native face shares its 256-code Type3 cache across script
            # files. Splitting Hangul/Latin/Hanja changes where text matrices
            # reset when that cache fills, causing small visible differences.
            key=('hft',fallback[0] if script_fallback else family)
            record=self.fonts.get(key)
            if record is None:
                if len(self.fonts)>=MAX_FONTS:raise ValueError('font-count-bounds')
                record={'kind':'hft','family':family,'sha256':source.sha256,'source':source,'glyphs':{},
                        'script_fallback':script_fallback,'source_hashes':set()}
                self.fonts[key]=record
            record['source_hashes'].add(source.sha256)
            record['glyphs'].setdefault(character,(glyph,math.trunc(source.advance(character,1000))))
            return key,character,source.advance(character,1)
        raise ValueError('unavailable-outline-glyph')

    def _sfnt_candidates(self,family):
        for path,directory in self.owner._sfnt_resolver._index().get(family,[])[:16]:
            yield path,directory,None
        if self.collection_index is None:
            from fontTools.ttLib import TTCollection,TTLibError
            result={}
            for directory in self.owner._sfnt_resolver._directories:
                if not directory.is_absolute() or str(directory).startswith('\\\\') or not directory.is_dir():continue
                directory=directory.resolve()
                for path in directory.iterdir():
                    if path.suffix.lower() not in {'.ttc','.otc'}:continue
                    try:
                        path=path.resolve(strict=True)
                        if not _inside(path,directory) or path.stat().st_size>MAX_SFNT_BYTES:continue
                        with path.open('rb') as source:header=source.read(12)
                        if len(header)!=12 or header[:4]!=b'ttcf' or not 0<struct.unpack('>I',header[8:])[0]<=256:continue
                        collection=TTCollection(path,lazy=True)
                        try:
                            for face,font in enumerate(collection.fonts):
                                for record in font['name'].names:
                                    if record.nameID not in (1,16):continue
                                    value=record.toUnicode().strip()
                                    if value and not any(ord(c)<32 for c in value):
                                        candidate=(path,directory,face)
                                        if candidate not in result.setdefault(value,[]):result[value].append(candidate)
                        finally:collection.close()
                    except (OSError,ValueError,KeyError,TTLibError,struct.error,UnicodeError):continue
            self.collection_index=result
        yield from self.collection_index.get(family,[])[:16]

    def _sfnt(self,raw,character,index):
        from fontTools.ttLib import TTFont,TTLibError
        tables={tag:bytes(raw.fontTable(tag)) for tag in ('head','maxp','name')}
        if not all(tables.values()):raise ValueError('unsupported-sfnt-tables')
        identity=hashlib.sha256(b''.join(tables.values())).hexdigest()
        record=self.sfnt_sources.get(identity)
        if record is None:
            record=False
            for path,directory,face in self._sfnt_candidates(raw.familyName()):
                try:
                    path=path.resolve(strict=True)
                    if not _inside(path,directory):continue
                    data=_read_bounded(path,MAX_SFNT_BYTES)
                    source=TTFont(BytesIO(data),fontNumber=face if face is not None else -1,lazy=True)
                    if 'glyf' not in source or any(source.getTableData(tag)!=value for tag,value in tables.items()):
                        source.close();continue
                    flags=source['OS/2'].fsType if 'OS/2' in source else 0
                    if flags&0x202:
                        source.close();continue
                    if self.source_bytes+len(data)>MAX_SOURCE_BYTES or len(self.fonts)>=MAX_FONTS:
                        source.close();raise ValueError('font-source-bounds')
                    checksum=hashlib.sha256(data).hexdigest();key=('ttf',checksum,face)
                    record={'kind':'ttf','family':raw.familyName(),'sha256':checksum,'data':data,
                            'source':source,'glyphs':{},'no_subsetting':bool(flags&0x100),'face':face,'key':key}
                    self.fonts[key]=record;self.source_bytes+=len(data)
                    break
                except (OSError,ValueError,KeyError,TTLibError,struct.error):continue
            self.sfnt_sources[identity]=record
        if not record:raise ValueError('unverified-sfnt-source')
        source=record['source'];cmap=source.getBestCmap() or {};glyph=cmap.get(ord(character))
        if glyph is None or source.getGlyphID(glyph)!=index:raise ValueError('complex-shaping-or-glyph-map')
        advance=source['hmtx'].metrics[glyph][0]/source['head'].unitsPerEm
        record['glyphs'].setdefault(index,(glyph,math.trunc(advance*1000)))
        return record['key'],index,advance

    def draw_line(self,painter,command,layout):
        """Preflight the whole line; unsupported lines retain the old painter."""
        from PySide6.QtCore import Qt,QRectF
        from PySide6.QtGui import QImage,QPainter
        from fontTools.ttLib import TTLibError
        from .source_layout import (BITMAP_FONT_PROPERTY,OUTLINE_FONT_PROPERTY,FONT_FALLBACK_PROPERTY,_language_index,
            BITMAP_TRACKING_PROPERTY,BITMAP_SPACE_PROPERTY,SCRIPT_OFFSET_PROPERTY,
            SYNTHETIC_BOLD_PROPERTY,SYNTHETIC_ITALIC_PROPERTY,STRIKE_SHAPE_PROPERTY)
        from .formats import INVISIBLE_STRIKE_SHAPES
        formats=layout.formats()
        if any(item.format.property(BITMAP_FONT_PROPERTY) for item in formats):return None
        def legacy(reason):
            self.facts['legacy_lines']+=1
            reasons=self.facts['legacy_reasons'];reasons[reason]=reasons.get(reason,0)+1
            return False
        if command.source.style.align=='JUSTIFY' and command.extra.get('justify_continuation'):
            return legacy('justified-continuation')
        if len(self.records)>=MAX_LINES:return legacy('line-count-bounds')
        text=layout.text();cursor=0;pen=0;alignment_pen=0;parts=[];metrics=[];terminal=len(text.rstrip('\u200b '))-1
        group_number=0;current_group=None;hft_fallback_mode=False;suppressed_strike=0;stretched=0
        if len(text)+self.characters>MAX_CHARACTERS:return legacy('character-count-bounds')
        for position,character in enumerate(text):
            units=len(character.encode('utf-16le'))//2
            fmt=next((item.format for item in formats if item.start<=cursor<item.start+item.length),None)
            start=cursor;cursor+=units
            if character=='\u200b':continue
            if character=='\u2000' or unicodedata.category(character) in {'Cc','Cf'}:
                return legacy('inline-object-or-control-space')
            if fmt is None:return legacy('missing-format')
            font=fmt.font();size=font.pointSizeF();em=_round(size/.12)
            if not 0<em<=8192:return legacy('font-size-bounds')
            if (font.bold() or font.italic() or font.underline() or font.strikeOut()
                or fmt.property(SYNTHETIC_BOLD_PROPERTY) or fmt.property(SYNTHETIC_ITALIC_PROPERTY)
                or fmt.property(SCRIPT_OFFSET_PROPERTY)):
                return legacy('style-or-script-offset')
            if font.kerning():return legacy('kerning')
            if fmt.background().style()!=Qt.BrushStyle.NoBrush:return legacy('character-background')
            color=fmt.foreground().color()
            if color.alpha()!=255:return legacy('translucent-text-color')
            tracking_percent=fmt.property(BITMAP_TRACKING_PROPERTY) or 0
            tracking=tracking_percent/100+1
            if tracking<=0:return legacy('nonpositive-tracking')
            if position==terminal and not command.extra.get('justify_continuation'):tracking=1
            try:
                family=fmt.property(OUTLINE_FONT_PROPERTY)
                if family:key,glyph,advance=self._hft(family,character)
                else:
                    runs=layout.glyphRuns(start,units)
                    if len(runs)!=1 or len(runs[0].glyphIndexes())!=1:return legacy('complex-shaping')
                    raw,index=self._hanja_fallback(font,character,runs[0].rawFont(),runs[0].glyphIndexes()[0])
                    key,glyph,advance=self._sfnt(raw,character,index)
            except (OSError,ValueError,HftError,UnicodeError,KeyError,struct.error,TTLibError) as error:
                return legacy(str(error) if isinstance(error,ValueError) else 'font-source-error')
            stretch=font.stretch();scale=float(stretch)
            if not 1<=stretch<=500:return legacy('stretch-bounds')
            source=self.fonts[key]
            if stretch!=100 and source['kind']=='ttf':
                sfnt=source['source'];units=sfnt['head'].unitsPerEm
                average=_round(sfnt['OS/2'].xAvgCharWidth*em/units) if 'OS/2' in sfnt else 0
                if average<=0:return legacy('unavailable-average-character-width')
                f32=lambda value:struct.unpack('<f',struct.pack('<f',value))[0]
                # The requested GDI width uses logical size before printer
                # em rounding; its PDF ratio uses the rounded base average.
                width=_round(sfnt['OS/2'].xAvgCharWidth/units*size/.12*stretch/100)
                scale=f32(f32(width/average)*100)
                if scale<=0:return legacy('nonpositive-stretched-font-width')
            if character=='!' or character==' ' and (fmt.property(BITMAP_SPACE_PROPERTY) is not None or advance==0):
                # Native text layout gives U+0021 a half-em advance even
                # with font spaces enabled (HFT and TrueType alike).
                # Quantize the base half-em before stretch/tracking. Reversing
                # this order changes 11pt/50% and 18pt/95% by four HWP units.
                delta=_round(math.floor(size*100/8+1e-9)*stretch/100*tracking)*4
                untracked_delta=_round(math.floor(size*100/8+1e-9)*stretch/100)*4
            else:
                logical_em=math.trunc(_round(size*100)/4)
                width=(math.floor(advance*logical_em*stretch/100+1e-9) if stretch!=100
                       else _round(advance*logical_em))
                delta=_round(width*tracking)*4
                untracked_delta=width*4
            if self.fonts[key].get('script_fallback'):hft_fallback_mode=True
            rgb=(color.red(),color.green(),color.blue());fallback=fmt.property(FONT_FALLBACK_PROPERTY) or hft_fallback_mode
            group_key=(key,em,rgb,_language_index(character)[0],tracking_percent,scale)
            if fallback or current_group!=group_key:group_number+=1
            current_group=group_key
            parts.append((pen,key,glyph,em,rgb,scale,group_number))
            metrics.append((key,em,stretch,tracking_percent,delta,untracked_delta))
            if stretch!=100:stretched+=1
            if fmt.property(STRIKE_SHAPE_PROPERTY) in INVISIBLE_STRIKE_SHAPES:suppressed_strike+=1
            if character.isspace() or fallback:current_group=None
            pen+=delta
            if character!=' ':alignment_pen=pen
        if not parts:
            self.facts['processed_lines']+=1;return True
        parts,centered_alignment=self._center_cached_line(command,text,parts,metrics)
        if centered_alignment is not None:alignment_pen=centered_alignment
        parts,fitted=self._fit_saved_line(command,text,parts,metrics,alignment_pen)
        factor={'CENTER':.5,'RIGHT':1}.get(command.source.style.align,0)
        if command.source.line_segments:
            # Saved frame widths already include paragraph margins; rounding
            # them to four units again changes the native alignment offset.
            alignment=0 if fitted else math.trunc((round(command.rect[2]*100)-alignment_pen)*factor)
        else:alignment=(math.floor(command.rect[2]*100/4+1e-9)*4-alignment_pen)*factor
        x=printer_pixel(command.rect[0]);line=command.extra['line'];offset=line.get('y_pt',0)
        y=_round((printer_pixel(command.rect[1]-offset)*12+_round(offset*100)+_round(line['baseline_pt']*100))/12)
        origin=command.extra.get('table_text_origin')
        if origin is not None and self.owner._active_pdf_grids.get(origin[0]):
            ox,oy=origin[1]
            x_units=printer_pixel(ox)*12+round((command.rect[0]-ox)*200)/2
            y_units=printer_pixel(oy)*12+round((command.rect[1]-oy)*200)/2+_round(line['baseline_pt']*100)
            y=_round(y_units/12)
            glyphs=[(key,glyph,em,_round((x_units+alignment+start)/12),y,color,scale,group) for start,key,glyph,em,color,scale,group in parts]
        else:
            glyphs=[(key,glyph,em,x+_round((alignment+start)/12),y,color,scale,group) for start,key,glyph,em,color,scale,group in parts]
        marker=MARKER_PREFIX+len(self.records).to_bytes(6,'big')
        self.records[marker]=(glyphs,(self.owner._pdf_page_width,self.owner._pdf_page_height),self.precision)
        cell=command.extra.get('table_text_cell')
        if cell is not None and origin is not None and self.owner._active_pdf_grids.get(origin[0]):
            self.record_clips[marker]=(self.owner._active_pdf_grids[origin[0]],cell)
            self.facts['cell_clipped_lines']+=1
        image=QImage(marker,4,1,12,QImage.Format.Format_RGB888).copy()
        painter.save()
        try:
            painter.setRenderHint(QPainter.RenderHint.LosslessImageRendering,True)
            painter.drawImage(QRectF(-100,-100,1,1),image)
        finally:painter.restore()
        self.characters+=len(parts);self.facts['processed_lines']+=1;self.facts['glyphs']+=len(parts)
        self.facts['suppressed_strike_glyphs']+=suppressed_strike
        self.facts['stretched_glyphs']+=stretched
        return True

    def draw_path(self,painter,program):
        """Place an independently built path in physical PDF coordinates."""
        from PySide6.QtCore import QRectF
        from PySide6.QtGui import QImage,QPainter
        if not program:return
        marker=PATH_MARKER_PREFIX+len(self.path_records).to_bytes(6,'big')
        self.path_records[marker]=program
        image=QImage(marker,4,1,12,QImage.Format.Format_RGB888).copy()
        painter.save()
        try:
            painter.setRenderHint(QPainter.RenderHint.LosslessImageRendering,True)
            painter.drawImage(QRectF(-100,-100,1,1),image)
        finally:painter.restore()

    def finish(self,path):
        """Replace every placement token once, then atomically save the PDF."""
        if not self.records and not self.path_records:return
        from pypdf import PdfReader,PdfWriter
        from pypdf.generic import (ArrayObject,DictionaryObject,DecodedStreamObject,
                                  NameObject,NumberObject,FloatObject,ContentStream,TextStringObject)
        from fontTools.ttLib import TTFont
        from fontTools.subset import Subsetter,Options
        class MatrixFloat(FloatObject):
            # Replacing a Qt block may require division by its .75 scale.
            # Keep enough digits that PDFium sees the native six-digit CTM;
            # pypdf's ordinary eight-digit writer loses that cancellation.
            def myrepr(self):return _numbers([float(self)])
        path=Path(path);reader=PdfReader(BytesIO(path.read_bytes()));writer=PdfWriter()
        writer.clone_document_from_reader(reader);temporary=None
        # A line can fail preflight after some glyphs were inspected. Only
        # actually painted glyphs consume codes, in first drawing order.
        used={}
        for glyphs,_,_ in self.records.values():
            for key,glyph,*_ in glyphs:used.setdefault(key,{}).setdefault(glyph,self.fonts[key]['glyphs'][glyph])
        for key,glyphs in used.items():self.fonts[key]['glyphs']=glyphs
        name=NameObject;num=NumberObject
        array=lambda values:ArrayObject([FloatObject(value) for value in values])
        def stream(data):
            obj=DecodedStreamObject();obj.set_data(data);return writer._add_object(obj.flate_encode())
        def empty_cmap(codes,digits):
            maximum='FF' if digits==2 else 'FFFF'
            lines=['/CIDInit /ProcSet findresource begin','12 dict begin','begincmap',
                   '/CMapName /NaraeVisual def','/CMapType 2 def','1 begincodespacerange',
                   f'<{"0"*digits}> <{maximum}>','endcodespacerange']
            for start in range(0,len(codes),100):
                group=codes[start:start+100];lines.append(f'{len(group)} beginbfchar')
                lines.extend(f'<{code:0{digits}X}> <>' for code in group);lines.append('endbfchar')
            lines.extend(['endcmap','CMapName currentdict /CMap defineresource pop','end','end'])
            return stream(('\n'.join(lines)+'\n').encode('ascii'))
        built={};locations={};font_number=0
        def build_fonts(key):
            nonlocal font_number
            if key in built:return
            source=self.fonts[key];items=list(source['glyphs'].items());created=[]
            if source['kind']=='hft':
                units=source['source'].font_em
                bottom=math.trunc(-math.floor(units/5)*1000/units)
                for start in range(0,len(items),256):
                    group=items[start:start+256];charprocs=DictionaryObject();widths=[];differences=[num(0)]
                    resource=f'/NaraeNative{font_number}';font_number+=1
                    for code,(character,(glyph,advance)) in enumerate(group):
                        program=[f'{advance} 0 0 {bottom} 1000 1000 d1']
                        for op,*args in normalized_operations(glyph):
                            program.append(' '.join(map(str,args))+(' ' if args else '')+{'M':'m','L':'l','C':'c','Z':'h'}[op])
                        program.append('f');glyph_name=name(f'/NaraeGlyph{code}')
                        charprocs[glyph_name]=stream(('\n'.join(program)+'\n').encode('ascii'))
                        differences.append(glyph_name);widths.append(advance);locations[key,character]=(resource,code,2)
                    obj=DictionaryObject({name('/Type'):name('/Font'),name('/Subtype'):name('/Type3'),
                        name('/FontBBox'):array([0,bottom,1000,1000]),name('/FontMatrix'):array([.001,0,0,.001,0,0]),
                        name('/FirstChar'):num(0),name('/LastChar'):num(len(group)-1),name('/Widths'):array(widths),
                        name('/Encoding'):DictionaryObject({name('/Type'):name('/Encoding'),name('/Differences'):ArrayObject(differences)}),
                        name('/CharProcs'):charprocs,name('/Resources'):DictionaryObject(),
                        name('/ToUnicode'):empty_cmap(list(range(len(group))),2)})
                    created.append((resource,writer._add_object(obj)))
            else:
                font=TTFont(BytesIO(source['data']),fontNumber=source['face'] if source['face'] is not None else -1,
                            recalcBBoxes=False,recalcTimestamp=False)
                head=font['head'];units=head.unitsPerEm
                bbox=[math.trunc(v*1000/units) for v in (head.xMin,head.yMin,head.xMax,head.yMax)]
                os2=font.get('OS/2');ascent=math.trunc((getattr(os2,'sTypoAscender',font['hhea'].ascent))*1000/units)
                descent=math.trunc((getattr(os2,'sTypoDescender',font['hhea'].descent))*1000/units)
                resource=f'/NaraeNative{font_number}';font_number+=1
                base=name('/NARAEF+Native'+hashlib.sha256(repr(key).encode('ascii')).hexdigest()[:12])
                if not source['no_subsetting']:
                    options=Options();options.hinting=True
                    # These optional vendor tables have no PDF glyph role.
                    options.drop_tables.extend(['meta','MERG','HJCT'])
                    subset=Subsetter(options);subset.populate(glyphs=[glyph[0] for _,glyph in items]);subset.subset(font)
                    for tag in list(font.keys()):
                        if tag not in {'GlyphOrder','head','hhea','maxp','hmtx','fpgm','prep','cvt ','loca','glyf'}:del font[tag]
                    output=BytesIO();font.save(output);data=output.getvalue()
                else:
                    output=BytesIO();font.save(output);data=output.getvalue()
                cidmap=bytearray((len(items)+1)*2);widths=[]
                for code,(original,(glyph,advance)) in enumerate(items,1):
                    cidmap[code*2:code*2+2]=font.getGlyphID(glyph).to_bytes(2,'big')
                    widths.append(advance);locations[key,original]=(resource,code,4)
                font.close();font_file=DecodedStreamObject();font_file.set_data(data)
                font_file[name('/Length1')]=num(len(data))
                descriptor=writer._add_object(DictionaryObject({name('/Type'):name('/FontDescriptor'),
                    name('/FontName'):base,name('/Flags'):num(32),name('/FontBBox'):array(bbox),
                    name('/ItalicAngle'):num(0),name('/Ascent'):num(ascent),name('/Descent'):num(descent),
                    name('/CapHeight'):num(ascent),name('/StemV'):num(0),name('/FontFile2'):writer._add_object(font_file.flate_encode())}))
                descendant=writer._add_object(DictionaryObject({name('/Type'):name('/Font'),name('/Subtype'):name('/CIDFontType2'),
                    name('/BaseFont'):base,name('/CIDSystemInfo'):DictionaryObject({name('/Registry'):TextStringObject('Adobe'),
                        name('/Ordering'):TextStringObject('Identity'),name('/Supplement'):num(0)}),
                    name('/FontDescriptor'):descriptor,name('/DW'):num(1000),
                    name('/W'):ArrayObject([num(1),array(widths)]),name('/CIDToGIDMap'):stream(bytes(cidmap))}))
                obj=DictionaryObject({name('/Type'):name('/Font'),name('/Subtype'):name('/Type0'),name('/BaseFont'):base,
                    name('/Encoding'):name('/Identity-H'),name('/DescendantFonts'):ArrayObject([descendant]),
                    name('/ToUnicode'):empty_cmap(list(range(1,len(items)+1)),4)})
                created.append((resource,writer._add_object(obj)))
            built[key]=created
            self.facts['fonts'].append({'family':source['family'],'kind':source['kind'],'source_sha256':source['sha256'],
                'source_sha256s':sorted(source.get('source_hashes',{source['sha256']})),
                'glyphs':len(items),'preserves_truetype_hinting':source['kind']=='ttf'})
        seen=set()
        try:
            for page in writer.pages:
                content=ContentStream(page.get_contents(),writer);ops=content.operations
                stack=[];ctm=(1,0,0,1,0,0);replacements={}
                resources=page['/Resources'].get_object();font_resources=resources.setdefault(name('/Font'),DictionaryObject()).get_object()
                for i,(values,operator) in enumerate(ops):
                    if operator==b'q':stack.append((i,ctm))
                    elif operator==b'Q':_,ctm=stack.pop()
                    elif operator==b'cm':ctm=_multiply(tuple(map(float,values)),ctm)
                    elif operator==b'Do':
                        obj=resources['/XObject'][values[0]].get_object()
                        if obj.get('/Subtype')!='/Image' or obj.get('/Width')!=4 or obj.get('/Height')!=1:continue
                        marker=obj.get_data();record=self.records.get(marker)
                        path_program=self.path_records.get(marker)
                        if record is None and path_program is None:continue
                        # A document image can contain the same twelve bytes.
                        # Only the off-paper RGB token belongs to this writer.
                        xs=[ctm[4],ctm[0]+ctm[4],ctm[2]+ctm[4],ctm[0]+ctm[2]+ctm[4]]
                        if (obj.get('/ColorSpace')!='/DeviceRGB' or obj.get('/BitsPerComponent')!=8
                            or max(xs)>=float(page.mediabox.left)):continue
                        if marker in seen:raise ValueError('Duplicate PDF text placement token')
                        seen.add(marker);start,parent=stack[-1]
                        if i+1>=len(ops) or ops[i+1][1]!=b'Q' or any(op not in (b'cm',b'gs') for _,op in ops[start+1:i]):
                            raise ValueError('Unexpected PDF text placement block')
                        if path_program is not None:
                            replacement=DecodedStreamObject()
                            # PDF device matrices use single precision. Invert
                            # that matrix so its product is an actual identity,
                            # rather than a nearby scale at antialiased corners.
                            device_parent=tuple(struct.unpack('<f',struct.pack('<f',v))[0] for v in parent)
                            replacement.set_data(('q\n'+_numbers(_inverse(device_parent))+' cm\n'+path_program+'\nQ\n').encode('ascii'))
                            chunk=ContentStream(replacement,writer).operations
                            for values,operator in chunk:
                                if operator==b'cm':
                                    for index,value in enumerate(values):values[index]=MatrixFloat(value)
                            replacements[start]=(i+2,chunk)
                            continue
                        glyphs,(pw,ph),precision=record;f32=lambda value:struct.unpack('<f',struct.pack('<f',value))[0]
                        sx=round(f32(math.floor(pw)/printer_pixel(pw)),precision);sy=round(f32(math.floor(ph)/printer_pixel(ph)),precision)
                        relative=_multiply((sx,0,0,-sy,0,math.floor(ph)),_inverse(parent))
                        clip=self.record_clips.get(marker)
                        if clip:
                            from .pdf_table_frames import clip_program
                            # Keep the original glyph CTM untouched. Resetting
                            # and rebuilding it changes unhinted HFT edges.
                            program=['q',clip_program(*clip,_inverse(parent)),_numbers(relative)+' cm','BT','0 Tr']
                        else:program=['q',_numbers(relative)+' cm','BT','0 Tr']
                        for key,glyph,em,x,y,color,scale,group in glyphs:
                            build_fonts(key)
                            for resource,ref in built[key]:
                                if name(resource) in font_resources and font_resources.get(name(resource))!=ref:
                                    raise ValueError('PDF native font resource collision')
                                font_resources[name(resource)]=ref
                            resource,code,digits=locations[key,glyph]
                        groups=[]
                        for item in glyphs:
                            if not groups or groups[-1][0][-1]!=item[-1]:groups.append([])
                            groups[-1].append(item)
                        for group in groups:
                            key,glyph,em,x,y,color,scale,_=group[0]
                            resource,_,_=locations[key,glyph]
                            program.extend([_numbers([v/255 for v in color])+' rg',f'{resource} {em} Tf',
                                _numbers([round(scale,precision)])+' Tz',f'1 0 0 -1 {x} {y} Tm'])
                            items=[]
                            for offset,item in enumerate(group):
                                key,glyph,_,gx,_,_,_,_=item
                                current_resource,code,digits=locations[key,glyph]
                                if current_resource!=resource:
                                    # Type3's 256-code boundary starts a new text
                                    # matrix, keeping its cumulative device phase.
                                    if items:program.append('['+' '.join(items)+'] TJ');items=[]
                                    resource=current_resource;program.extend([f'{resource} {em} Tf',f'1 0 0 -1 {gx} {y} Tm'])
                                items.append(f'<{code:0{digits}X}>')
                                if offset+1<len(group) and locations[group[offset+1][0],group[offset+1][1]][0]==resource:
                                    width=self.fonts[key]['glyphs'][glyph][1]
                                    delta=group[offset+1][3]-gx
                                    natural=struct.unpack('<f',struct.pack('<f',width*em/1000))[0]
                                    # Native tests equality before horizontal
                                    # scaling. A matching device advance skips
                                    # correction even when Tz changes its ink.
                                    adjustment=(_round((width-delta*1000/em/(scale/100))*10)/10
                                        if delta and delta!=natural else 0)
                                    if adjustment:items.append(f'{adjustment:.1f}')
                            if items:program.append('['+' '.join(items)+'] TJ')
                        program.extend(['ET','Q']);replacement=DecodedStreamObject()
                        replacement.set_data(('\n'.join(program)+'\n').encode('ascii'))
                        chunk=ContentStream(replacement,writer).operations
                        for values,operator in chunk:
                            if operator==b'cm':
                                for index,value in enumerate(values):values[index]=MatrixFloat(value)
                        replacements[start]=(i+2,chunk)
                if not replacements:continue
                new=[];index=0
                while index<len(ops):
                    if index in replacements:index,chunk=replacements[index];new.extend(chunk)
                    else:new.append(ops[index]);index+=1
                content.operations=new;page[name('/Contents')]=stream(content.get_data())
            if len(seen)!=len(self.records)+len(self.path_records):raise ValueError('Missing PDF text placement token')
            writer.compress_identical_objects(remove_duplicates=False,remove_unreferenced=True)
            fd,filename=tempfile.mkstemp(prefix='.narae-font-',suffix='.pdf',dir=path.parent)
            os.close(fd);temporary=Path(filename)
            with temporary.open('wb') as output:writer.write(output)
            writer.close();reader.close();os.replace(temporary,path)
        finally:
            writer.close();reader.close()
            if temporary:temporary.unlink(missing_ok=True)

    def close(self):
        for source in self.fonts.values():
            if source['kind']=='ttf':source['source'].close()
