"""Render the source document's cached layout without invoking Hancom.

This preserves imported line positions, including compatible text edits. It
does not implement Hancom's reflow algorithm; structural edits invalidate it.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from bisect import bisect_right
import math
import unicodedata
from PySide6.QtCore import Qt, QPointF, QRectF, QSizeF, Signal
from PySide6.QtGui import (QColor, QFont, QFontDatabase, QFontMetricsF, QImage, QPainter, QPen, QTextLayout,
                          QTextCharFormat, QTextOption, QTransform, QPageSize, QPageLayout)
from PySide6.QtGui import QPainterPath
from PySide6.QtWidgets import QGraphicsView, QGraphicsScene, QGraphicsRectItem
from .formats import Paragraph, Table, Drawing, _table_grid
from .font_resolver import InstalledHftResolver,InstalledSfntResolver

SYNTHETIC_BOLD_PROPERTY=0x108001
SCRIPT_OFFSET_PROPERTY=0x108002
TEXT_DPI=600
TEXT_UNIT_PT=72/TEXT_DPI
TEXT_TO_VIEW=96/TEXT_DPI
FONT_FAMILY_ALIASES={'굴림':'Gulim','굴림체':'GulimChe','돋움':'Dotum','돋움체':'DotumChe',
                     '바탕':'Batang','바탕체':'BatangChe','궁서':'Gungsuh','궁서체':'GungsuhChe',
                     '맑은 고딕':'Malgun Gothic','함초롬바탕':'HCR Batang','함초롬돋움':'HCR Dotum',
                     '함초롬바탕 확장':'HCR Batang Ext','함초롬바탕 확장B':'HCR Batang ExtB',
                     '함초롬돋움 확장':'HCR Dotum Ext'}
LANGUAGE_NAMES=('HANGUL','LATIN','HANJA','JAPANESE','OTHER','SYMBOL','USER')


def _language_index(character):
    """Unicode selection for the seven preserved native face declarations.

    Other/symbol assignments outside the explicit scripts remain an inference;
    HWP's historical HChar tables can classify those characters differently.
    """
    code=ord(character)
    if unicodedata.category(character) in {'Cc','Cf'}:return None,False
    if (0x1100<=code<=0x11ff or 0x3130<=code<=0x318f or 0xa960<=code<=0xa97f or
            0xac00<=code<=0xd7a3 or 0xd7b0<=code<=0xd7ff):return 0,False
    if code<128 or unicodedata.name(character,'').startswith('LATIN '):return 1,False
    if (0x3400<=code<=0x4dbf or 0x4e00<=code<=0x9fff or 0xf900<=code<=0xfaff or
            0x20000<=code<=0x323af):return 2,False
    if (0x3040<=code<=0x30ff or 0x31f0<=code<=0x31ff or 0xff66<=code<=0xff9f or
            0x1b000<=code<=0x1b16f):return 3,False
    if unicodedata.category(character)=='Co':return 6,False
    return (5 if unicodedata.category(character).startswith('S') else 4),True


def _print_line_width(width):
    # Independently authored Hancom PDF fixtures, including exact half-pixel
    # values, show half-up rounding and a one-pixel minimum at 600 dpi.
    return max(1,math.floor(width/.12+.5))*.12


def _print_dot_metrics(width):
    pixels=max(1,math.floor(width/.12+.5))
    # Native CIRCLE combines a filled path and its boundary stroke. Its outer
    # diameter grows to the next even pixel; pitch uses the nominal pen width.
    return 2*math.ceil(pixels/2)*.12,2*pixels*.12


@dataclass
class Command:
    kind: str
    rect: tuple
    source: object
    extra: dict = field(default_factory=dict)


def _utf16_slice(text, start, end=None):
    data=text.encode('utf-16le')
    return data[start*2:None if end is None else end*2].decode('utf-16le','replace')


def _segments(paragraph):
    if paragraph.line_segments:
        return paragraph.line_segments
    return []


def _occupies_flow(position):
    return position.inline or (position.flow_with_text and not position.allow_overlap and
                              position.wrap=='TOP_AND_BOTTOM' and position.vert_rel=='PARA' and
                              position.vert_align=='TOP')


def _table_measurements(table, continuation_threshold, segments):
    columns,rows=_table_grid(table)
    columns=list(columns);rows=list(rows)
    widths={id(c):max(1,(c.width or sum(columns[c.col:c.col+c.col_span]))-c.padding[0]-c.padding[1]) for c in table.cells}
    cell_extents={id(c):_content_pages(c.blocks,continuation_threshold,
                 (lambda p,end,width=widths[id(c)]:segments(p,width,end)) if segments else None,
                 segments) for c in table.cells}
    for cell in table.cells:
        extents=cell_extents[id(cell)];extent,count=extents[-1],len(extents)
        if count==1:
            needed=extent+cell.padding[2]+cell.padding[3]
            present=sum(rows[cell.row:cell.row+cell.row_span])
            if needed>present and cell.row_span==1:rows[cell.row]=needed
    return columns,rows,widths,cell_extents


def _content_pages(blocks, continuation_threshold=300, segments=None, reflow=None):
    extents=[0.];previous=-1.;offset=0.;previous_end=0.;anchors={}
    for block in blocks:
        if isinstance(block,Paragraph):
            lines=segments(block,previous_end) if segments else _segments(block)
            if block.source_index>=0:anchors[block.source_index]=lines
            for number,line in enumerate(lines):
                y=line['y_pt']
                if (number and y<=previous+.01) or y+1<previous:
                    if number or previous>continuation_threshold:
                        extents.append(0.);offset=0.;previous_end=0.
                    else:offset=previous_end-y
                previous=y
                extents[-1]=max(extents[-1],y+offset+line['height_pt'])
                previous_end=y+offset+line['height_pt']+line.get('spacing_pt',0)
            for run in block.runs:
                if run.image and run.picture.position.flow_with_text:
                    position=run.picture.position
                    if position.vert_rel=='PARA':
                        line=next((l for l in reversed(lines) if l['text_start']<=(run.source_start or 0)),None)
                        if line:
                            end=line['y_pt']+offset+position.y_pt+run.height*.75
                            if not block.line_segments:end+=position.out_margin[2]+position.out_margin[3]
                            extents[-1]=max(extents[-1],end)
                            if not block.line_segments and _occupies_flow(position):previous_end=max(previous_end,end)
        elif isinstance(block,(Table,Drawing)) and (block.position.inline or block.position.flow_with_text):
            position=block.position
            if not position.inline and position.vert_rel!='PARA':continue
            anchor_lines=anchors.get(block.source_index,[])
            line=block.anchor_segments[0] if block.anchor_segments else (
                anchor_lines[0] if anchor_lines else {'y_pt':previous_end})
            if not block.anchor_segments and not _occupies_flow(position):continue
            top=line['y_pt']+offset+position.y_pt+position.out_margin[2]
            height=block.height
            if not height and isinstance(block,Table):height=sum(_table_grid(block)[1])
            if isinstance(block,Table) and not block.anchor_segments and reflow:
                # New nested tables must contribute their grown cell heights,
                # rather than only their requested minimum row dimensions.
                _,rows,_,_=_table_measurements(block,continuation_threshold,reflow)
                height=max(height,sum(rows))
            end=top+height+position.out_margin[3]
            extents[-1]=max(extents[-1],end)
            previous_end=max(previous_end,end)
    return extents


def _content_extent(blocks, continuation_threshold=300, segments=None, reflow=None):
    extents=_content_pages(blocks,continuation_threshold,segments,reflow)
    return extents[-1],len(extents)


class SourceLayout:
    def __init__(self, model):
        self.model=model
        self._set_page_geometry(model.page)
        self.pages=[[]]
        self._page_layouts=[model.page]
        self._section_page_ranges={}
        self.warnings=[]
        self.font_substitutions={}
        self.glyph_substitutions={}
        self._source_fonts={}
        self._local_font_families={}
        self._hft_kind_substitutions={}
        self._missing_glyphs={}
        self._hft_resolver=InstalledHftResolver()
        self._sfnt_resolver=InstalledSfntResolver()
        self._families=set(QFontDatabase.families())
        self._fonts={}
        self._synthetic_bolds={}
        self._script_styles={}
        self._script_ambiguities={}
        self._script_offsets={}
        self._drawing_limitations={}
        self._metrics_device=QImage(1,1,QImage.Format.Format_RGB32)
        self._metrics_device.setDotsPerMeterX(round(TEXT_DPI/0.0254))
        self._metrics_device.setDotsPerMeterY(round(TEXT_DPI/0.0254))
        self._paragraph_pages={}
        self._paragraph_entries={}
        self._paragraphs={}
        self._source_paragraphs={}
        self._object_anchors={}
        self._paragraph_lines={}
        self._inline_objects={}
        self._reflowed_paragraphs=set()
        self._synthetic_text_paragraphs=set()
        self._reflowed_object_paragraphs=set()
        self._field_page=None
        def collect(blocks):
            anchors={}
            for block in blocks:
                if isinstance(block,Paragraph):
                    key=id(block)
                    self._paragraphs[key]=block
                    self._source_paragraphs.setdefault(block.source_index,[]).append(block)
                    if block.source_index>=0:anchors[block.source_index]=block
                    for run in block.runs:
                        if run.image and run.picture.position.inline:
                            self._inline_objects.setdefault(key,[]).append((run.source_start or 0,run.width*.75,run.picture.position,run.style))
                        styles={self._style_for_character(run.style,c) for c in run.text} or {
                            self._style_for_character(run.style,'0') if run.field else run.style}
                        for style in styles:
                            family=self._font_family(style.font)
                            if style.font_type=='HFT' and style.font not in self._source_fonts:
                                self._hft_kind_substitutions[style.font]=family
                        for character in run.text:
                            style=self._style_for_character(run.style,character)
                            if self._glyph_fallback(style,character):
                                self._missing_glyphs.setdefault(style.font,set()).add(ord(character))
                elif isinstance(block,(Drawing,Table)):
                    if isinstance(block,Drawing):
                        geometry=getattr(block,'geometry',{})
                        if block.kind=='curve' and any(s.get('type')=='CURVE' for s in geometry.get('segments',[])):
                            self._drawing_limitations[id(block)]='곡선 제어점이 없어 선분으로 표시합니다.'
                        elif block.kind=='ellipse' and geometry.get('hasArcPr'):
                            self._drawing_limitations[id(block)]='타원의 호·부채꼴 정보는 전체 타원으로 표시합니다.'
                        elif block.kind not in {'rect','ellipse','line','polygon','curve'}:
                            self._drawing_limitations[id(block)]='지원하지 않는 도형은 외곽 상자로 표시합니다.'
                        if any(block.line_options.get(key,'NORMAL')!='NORMAL' for key in ('headStyle','tailStyle')):
                            self._drawing_limitations[id(block)]='선의 화살표 모양은 표시하지만 한글의 화살표 크기·접합 위치와 동일함을 검증하지 못했습니다.'
                        if block.kind=='rect' and geometry.get('ratio'):
                            self._drawing_limitations[id(block)]='둥근 모서리의 비율은 저장하지만 화면에서는 직각 사각형으로 표시합니다.'
                    anchor=anchors.get(block.source_index)
                    if anchor is not None:
                        self._object_anchors[id(block)]=anchor
                    if block.position.inline and anchor is not None:
                        self._inline_objects.setdefault(id(anchor),[]).append((block.anchor_text_start,block.width,block.position,block.anchor_style))
                    if isinstance(block,Drawing):collect(block.blocks)
                    else:
                        for cell in block.cells:collect(cell.blocks)
        collect(model.blocks)
        for part in model.header_footers:collect(part.blocks)
        for name,characters in self._missing_glyphs.items():
            fallback=self._fallback_font_family(name)
            self.glyph_substitutions[name]={'font':fallback,'distinct_characters':len(characters)}
            self.warnings.append(f"'{name}' HFT의 미지원 문자 {len(characters)}종은 '{fallback}'으로 표시합니다. 해당 글자 모양과 폭은 원본과 다를 수 있습니다.")
        self.warnings.extend(w for w in self._hft_resolver.warnings if w not in self.warnings)
        self.warnings.extend(w for w in self._sfnt_resolver.warnings if w not in self.warnings)
        for name,family in self._hft_kind_substitutions.items():
            self.warnings.append(f"원본 HFT 글꼴 '{name}'은 SFNT 글꼴 '{family}'으로 표시합니다. 같은 이름이어도 원본 HFT의 모양·폭과 동일함을 검증하지 못했습니다.")
        sections=[]
        for block in model.blocks:
            section=getattr(block,'scope',{}).get('section_index',0)
            if not sections or sections[-1][0]!=section:sections.append((section,[]))
            sections[-1][1].append(block)
        for section,blocks in sections:
            page_layout=model.section_pages[section] if 0<=section<len(model.section_pages) else model.page
            self._set_page_geometry(page_layout)
            start=0 if not self._section_page_ranges else len(self.pages)
            self._ensure_page(start)
            self._page_layouts[start]=page_layout
            count=self._build(blocks,(self.left,self.top),start,body=True)
            self._ensure_page(start+count)
            self._section_page_ranges[section]=(start,start+count)
        self._headers_footers()
        self._set_page_geometry(model.page)
        if self._reflowed_paragraphs:
            self.warnings.append(f"줄 배치 캐시가 없는 {len(self._reflowed_paragraphs)}개 문단은 Qt로 다시 배치했습니다. 수정 내용은 표시하지만 해당 문단의 줄바꿈과 높이는 한컴과 다를 수 있습니다.")
        if self._synthetic_text_paragraphs:
            self.warnings.append(f"지원하지 않는 개체의 대체 텍스트가 있는 {len(self._synthetic_text_paragraphs)}개 문단은 원래 개체 대신 대체 텍스트로 표시합니다.")
        if self._reflowed_object_paragraphs:
            self.warnings.append(f"글자 없이 위아래로 공간을 차지하는 그림이 있는 {len(self._reflowed_object_paragraphs)}개 문단은 그림 크기로 쪽 넘김을 다시 계산했습니다. 개별 그림의 배치 캐시가 없어 전체 화면 동일성은 검증하지 못했습니다.")
        if self._script_ambiguities:
            count=sum(len(characters) for characters in self._script_ambiguities.values())
            self.warnings.append(f"기타·기호 문자 {count}종의 언어별 글꼴은 Unicode 분류로 선택했습니다. 한글의 HChar 분류표와 다를 수 있어 해당 문자 모양·폭은 동일함을 검증하지 못했습니다.")
        if self._script_offsets and self._reflowed_paragraphs:
            self.warnings.append('언어별 글자 위치 이동은 원본 기본 크기를 기준으로 표시합니다. 새 줄 배치에서 이동된 글자를 포함한 줄 높이는 한글과 동일함을 검증하지 못했습니다.')
        self.warnings.extend(dict.fromkeys(self._drawing_limitations.values()))

    def _set_page_geometry(self,page_layout):
        self._current_page_layout=page_layout
        self.width,self.height=page_layout.display_size
        self.left=page_layout.left_pt+page_layout.gutter_pt
        self.top=page_layout.top_pt+page_layout.header_pt
        self.bottom=self.height-page_layout.bottom_pt-page_layout.footer_pt
        self.body_width=self.width-self.left-page_layout.right_pt
        self.body_height=self.bottom-self.top

    def _ensure_page(self,page):
        while len(self.pages)<=page:
            self.pages.append([])
            self._page_layouts.append(self._current_page_layout)

    def page_size(self,index):
        return self._page_layouts[index].display_size

    def _add(self,page,command):
        self._ensure_page(page)
        self.pages[page].append(command)

    def _segments_for(self,paragraph,width,start_y=0):
        if paragraph.line_segments:return paragraph.line_segments
        text=self._paragraph_text(paragraph)
        if not text and any(run.image for run in paragraph.runs):text='\u200b'
        if not text:return []
        self._reflowed_paragraphs.add(id(paragraph))
        text=text.replace('\r','\u200b').replace('\n','\u2028').replace('\x0b','\u2028').replace('\ufffc','\u200b')
        available=max(1,width-paragraph.style.left-paragraph.style.right)
        seed=Command('text',(0,0,available,1),paragraph,{'text':text,'start':0,'line':{'baseline_pt':0}})
        layout=self._text_layout(seed)
        option=layout.textOption();option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        layout.setTextOption(option);layout.clearLayout();layout.beginLayout()
        lines=[];y=(0 if paragraph.style.page_break else start_y)+paragraph.style.before
        while True:
            line=layout.createLine()
            if not line.isValid():break
            indent=paragraph.style.indent if not lines else 0
            line.setLineWidth(max(1,available-indent)/TEXT_UNIT_PT)
            height=line.height()*TEXT_UNIT_PT
            if y+height>self.body_height and y>0:y=0
            style=paragraph.style
            pitch=(height*style.line/100 if style.line_type=='PERCENT' else
                   height+style.line if style.line_type=='BETWEEN_LINES' else
                   max(height,style.line) if style.line_type=='AT_LEAST' else style.line)
            lines.append(dict(text_start=line.textStart(),y_pt=y,height_pt=height,text_height_pt=height,
                              baseline_pt=line.ascent()*TEXT_UNIT_PT,spacing_pt=max(0,pitch-height),
                              x_pt=style.left+indent,width_pt=max(1,available-indent),flags=0))
            y+=max(height,pitch)
        layout.endLayout()
        if lines:lines[-1]['spacing_pt']+=paragraph.style.after
        return lines

    def _paragraph_text(self,paragraph):
        text=paragraph.cache_text or ''.join(r.text for r in paragraph.runs)
        literal=''.join(r.text for r in paragraph.runs)
        if paragraph.cache_text and literal.strip() and not any(
                not c.isspace() and unicodedata.category(c) not in {'Cc','Cf'} for c in paragraph.cache_text):
            # Unsupported native controls can have a diagnostic text run but
            # only zero-width controls in their source cache. Keep that marker
            # visible instead of silently displaying a completely blank page.
            self._synthetic_text_paragraphs.add(id(paragraph))
            text=literal
        if self._field_page is None:return text
        page,first=self._field_page
        fields=[r for r in paragraph.runs if r.field and r.field.get('kind')=='auto_number'
                and r.field.get('number_type')=='PAGE']
        if not fields:return text
        if not paragraph.cache_text:
            return ''.join(str(page-first+r.field.get('number',1)) if r in fields else r.text for r in paragraph.runs)
        for run in fields:
            if run.source_start is None:
                # Older imported models lack field offsets. A single line can
                # still display their literal/field sequence without guessing
                # which control in a cached multiline paragraph owns the field.
                if len(paragraph.line_segments)<=1:
                    return ''.join(str(page-first+r.field.get('number',1)) if r in fields else r.text for r in paragraph.runs)
                continue
            value=str(page-first+run.field.get('number',1))
            if len(value)>8:continue
            start=run.source_start
            text=_utf16_slice(text,0,start)+value+'\u200b'*(8-len(value))+_utf16_slice(text,start+8)
        return text

    def _build(self,blocks,origin,page,body=False,continuation_y=None,content_width=None):
        current=page;last=-1.;origin_x,origin_y=origin
        if content_width is None:content_width=self.body_width
        local_offset=0.;previous_end=0.
        if continuation_y is None:continuation_y=self.top
        for block_index,block in enumerate(blocks):
            if isinstance(block,Paragraph):
                lines=self._segments_for(block,content_width,previous_end-local_offset)
                if not lines:continue
                image_only=any(r.image for r in block.runs) and all(r.image or not r.text.strip() for r in block.runs)
                if not block.line_segments and image_only:
                    occupied=max((r.height*.75+r.picture.position.out_margin[2]+r.picture.position.out_margin[3]
                                  for r in block.runs if r.image and r.picture.position.inline),default=0)
                    if lines[0]['y_pt']>0 and lines[0]['y_pt']+occupied>self.body_height+.1:
                        shift=lines[0]['y_pt']
                        lines=[dict(line,y_pt=line['y_pt']-shift) for line in lines]
                self._paragraph_lines[id(block)]=lines
                if body and block.style.page_break and block_index:
                    current+=1;last=-1.;local_offset=0.;previous_end=0.
                entry_page=current
                entry_y=origin_y+previous_end
                self._paragraph_entries[id(block)]=(entry_page,entry_y)
                floating_flow=body and image_only and all(not run.image or (
                    not run.picture.position.inline and run.picture.position.flow_with_text and
                    not run.picture.position.allow_overlap and run.picture.position.wrap=='TOP_AND_BOTTOM' and
                    run.picture.position.vert_rel=='PARA' and run.picture.position.vert_align=='TOP' and
                    abs(run.picture.position.x_pt)<.01 and abs(run.picture.position.y_pt)<.01) for run in block.runs)
                first_image=next((run for run in block.runs if run.image),None)
                flows=not image_only or any(r.image and r.picture.position.inline for r in block.runs)
                for number,line in enumerate(lines):
                    y=line['y_pt']
                    if flows and ((number and y<=last+.01) or y+1<last):
                        if body or number or last>self.body_height*.4:
                            current+=1;local_offset=0.;previous_end=0.
                        else:
                            local_offset=previous_end-y
                    if flows:
                        last=y
                        previous_end=max(previous_end,y+local_offset+line['height_pt']+line.get('spacing_pt',0))
                    self._paragraph_pages[(id(block),number)]=current
                    if not image_only:
                        start=line['text_start']
                        end=lines[number+1]['text_start'] if number+1<len(lines) else None
                        raw_text=_utf16_slice(self._paragraph_text(block),start,end)
                        text=raw_text.replace('\r','').replace('\n','').replace('\x0b','').replace('\ufffc','\u200b')
                        if text.strip('\u200b\u200c\u200d '):
                            actual_y=(continuation_y if current>page else origin_y)+y+local_offset
                            self._add(current,Command('text',(origin_x+line['x_pt'],actual_y,line['width_pt'],line['height_pt']),block,
                                                      {'line':line,'text':text,'start':start,
                                                       'justify_continuation':number+1<len(lines) and not raw_text.endswith(('\n','\x0b'))}))
                for run in block.runs:
                    if not run.image:continue
                    number=next((n for n in range(len(lines)-1,-1,-1) if lines[n]['text_start']<=(run.source_start or 0)),0)
                    image_page=self._paragraph_pages.get((id(block),number),current)
                    position=run.picture.position
                    if floating_flow:
                        self._reflowed_object_paragraphs.add(id(block))
                        if run is first_image:
                            flow_y=max(previous_end,lines[number]['y_pt'])
                        occupied=run.height*.75+position.out_margin[2]+position.out_margin[3]
                        if flow_y+occupied>self.body_height+.1 and flow_y>.1:
                            current+=1;flow_y=0
                        image_page=current
                        if run is first_image:
                            self._paragraph_pages[(id(block),number)]=image_page
                        image_line=dict(lines[number],y_pt=flow_y)
                        self._picture(run,image_line,(origin_x,self.top),image_page,block.style.align,block)
                        flow_y+=occupied;previous_end=flow_y;last=flow_y
                        continue
                    if (body and image_page>entry_page and not block.style.page_break and
                            not position.inline and position.vert_rel=='PAGE' and
                            position.wrap=='SQUARE' and (run.source_start or 0)<=lines[0]['text_start']):
                        # The object control is placed before text exclusion moves
                        # its paragraph to the next page. Confirmed with an owned
                        # native fixture, for both flowWithText values.
                        picture_y=self.top+position.y_pt
                        if entry_y+lines[0]['height_pt']>picture_y and entry_y<picture_y+run.height*.75:
                            image_page=entry_page
                    self._picture(run,lines[number],origin,image_page,block.style.align,block)
                    if not block.line_segments and _occupies_flow(position):
                        image=self.pages[image_page][-1]
                        base_y=continuation_y if image_page>page else origin_y
                        previous_end=max(previous_end,image.rect[1]+image.rect[3]+position.out_margin[3]-base_y)
                        last=max(last,previous_end-local_offset)
            elif isinstance(block,(Table,Drawing)):
                command_counts=[len(commands) for commands in self.pages]
                line=block.anchor_segments[0] if block.anchor_segments else {'y_pt':max(0,last),'x_pt':0,'width_pt':self.body_width}
                anchor=self._object_anchors.get(id(block))
                anchor_page=self._paragraph_pages.get((id(anchor),0),current)
                uncached_flow=not block.anchor_segments and not (anchor and anchor.line_segments) and _occupies_flow(block.position)
                if uncached_flow:
                    anchor_lines=self._paragraph_lines.get(id(anchor),[])
                    line=next((l for l in reversed(anchor_lines) if l['text_start']<=block.anchor_text_start),None)
                    if line is None:line={'y_pt':previous_end-local_offset,'x_pt':0,'width_pt':content_width}
                if isinstance(block,Table) and not block.position.inline:
                    position=block.position
                    entry=self._paragraph_entries.get(id(anchor))
                    exclusion=block.height+position.out_margin[2]+position.out_margin[3]
                    if (entry and entry[0]==anchor_page and position.flow_with_text and
                            position.vert_rel=='PARA' and position.vert_align=='TOP' and
                            position.wrap=='TOP_AND_BOTTOM' and abs(position.y_pt)<.01 and
                            abs(origin_y+line['y_pt']-entry[1]-exclusion)<.1):
                        # The floating table's exclusion moves its empty anchor
                        # line after the table. Position the object from the
                        # preceding flow point, before that self-exclusion.
                        line=dict(line,y_pt=entry[1]-origin_y)
                x,y=self._position(block.position,line,origin,block.width,block.height,anchor_page,anchor.style.align if anchor else None)
                if block.position.inline:
                    inline_x=self._inline_x(anchor,block.anchor_text_start,anchor_page)
                    if inline_x is not None:x=inline_x+block.position.out_margin[0]
                if isinstance(block,Drawing):
                    pad=block.padding
                    content= max(1,block.width-pad[0]-pad[1])
                    extent,count=_content_extent(block.blocks,self.body_height*.4,lambda p,end:self._segments_for(p,content,end),self._segments_for)
                    height=max(block.height,extent+pad[2]+pad[3])
                    self._add(anchor_page,Command('box',(x,y,block.width,height),block))
                    spare=max(0,height-pad[2]-pad[3]-extent) if count==1 else 0
                    shift=spare/2 if block.vertical_align=='CENTER' else spare if block.vertical_align=='BOTTOM' else 0
                    extra=self._build(block.blocks,(x+pad[0],y+pad[2]+shift),anchor_page,content_width=content)
                else:
                    if not block.position.inline:
                        # Native PAGE/PARA fixtures place the table's physical
                        # cell rectangle inside its left/top outer-margin box.
                        # Centre/right and inline alignment need their own rule.
                        if block.position.horz_align=='LEFT':x+=block.position.out_margin[0]
                        if block.position.vert_align=='TOP':y+=block.position.out_margin[2]
                    extra=self._table(block,x,y,anchor_page)
                if body:
                    following=next((b for b in blocks[block_index+1:] if isinstance(b,Paragraph) and b.line_segments and b.source_index!=block.source_index),None)
                    if extra and following and not following.style.page_break and following.line_segments[0]['y_pt']+1<last:
                        extra-=1  # the body cache already records the final page transition
                    current=max(current,anchor_page+extra)
                if uncached_flow:
                    current=max(current,anchor_page+extra)
                    begin=command_counts[current] if current<len(command_counts) else 0
                    physical_end=max((command.rect[1]+command.rect[3] for command in self.pages[current][begin:]),default=y)
                    base_y=continuation_y if current>page else origin_y
                    if current>anchor_page:previous_end=0.;local_offset=0.
                    previous_end=max(previous_end,physical_end+block.position.out_margin[3]-base_y)
                    last=previous_end-local_offset
                if block.position.wrap in {'BEHIND_TEXT','IN_FRONT_OF_TEXT'}:
                    for index,commands in enumerate(self.pages):
                        begin=command_counts[index] if index<len(command_counts) else 0
                        for command in commands[begin:]:
                            command.extra['object_wrap']=block.position.wrap
                            command.extra['object_z_order']=block.position.z_order
        return current-page

    def _position(self,position,line,origin,width,height,page,anchor_align=None):
        x0,y0=origin
        if position.inline:
            # Inline object occupies the same alignment box as the anchor.
            x=x0+line.get('x_pt',0)
            align=anchor_align or getattr(position,'horz_align','LEFT')
            if align=='CENTER':x+=(line.get('width_pt',self.body_width)-width)/2
            elif align=='RIGHT':x+=line.get('width_pt',self.body_width)-width
            y=y0+line.get('y_pt',0)
            y+=position.out_margin[2]
        else:
            xr={'PAPER':0,'PAGE':self.left,'COLUMN':x0,'PARA':x0}.get(position.horz_rel,x0)
            yr={'PAPER':0,'PAGE':self.top,'PARA':y0+line.get('y_pt',0)}.get(position.vert_rel,y0)
            aw=self.width if position.horz_rel=='PAPER' else self.body_width
            ah=self.height if position.vert_rel=='PAPER' else self.body_height
            x=xr+position.x_pt;y=yr+position.y_pt
            if position.horz_align=='CENTER':x+=(aw-width)/2
            elif position.horz_align=='RIGHT':x+=aw-width
            if position.vert_align=='CENTER':y+=(ah-height)/2
            elif position.vert_align=='BOTTOM':y+=ah-height
        return x,y

    def _picture(self,run,line,origin,page,align=None,paragraph=None):
        width,height=run.width*.75,run.height*.75
        x,y=self._position(run.picture.position,line,origin,width,height,page,align)
        if run.picture.position.inline and paragraph is not None:
            inline_x=self._inline_x(paragraph,run.source_start or 0,page)
            if inline_x is not None:x=inline_x+run.picture.position.out_margin[0]
        self._add(page,Command('image',(x,y,width,height),run,
                               {'object_wrap':run.picture.position.wrap,'object_z_order':run.picture.position.z_order}))

    def _inline_x(self,paragraph,start,page):
        if paragraph is None or not 0<=page<len(self.pages):return None
        for command in self.pages[page]:
            if command.kind!='text' or command.source is not paragraph:continue
            offset=start-command.extra['start']
            if 0<=offset<len(command.extra['text'].encode('utf-16le'))//2:
                layout=self._text_layout(command)
                return command.rect[0]+layout.lineAt(0).cursorToX(offset)[0]*TEXT_UNIT_PT
        return None

    def _table(self,table,x,y,page):
        columns,rows,widths,cell_extents=_table_measurements(table,self.body_height*.4,self._segments_for)
        # Cached line positions capture automatic growth of cells beyond their
        # requested minimum height, including continuations to another page.
        col_offsets=[0.]
        for value in columns:col_offsets.append(col_offsets[-1]+value)
        # Floating table cells may extend into the footer distance. Native
        # mixed-paper fixtures keep the final note row on the current sheet
        # while its cell rectangle crosses the paragraph flow frame's bottom.
        table_bottom=self.height-self._current_page_layout.bottom_pt
        table_height=table_bottom-self.top
        row_parts=[];row_page=page;row_y=y
        for row,height in enumerate(rows):
            cells=[c for c in table.cells if c.row==row]
            count=max((len(cell_extents[id(c)]) for c in cells),default=1)
            if count>1:
                heights=[max((cell_extents[id(c)][part]+c.padding[2]+c.padding[3] for c in cells if part<len(cell_extents[id(c)])),default=0) for part in range(count)]
                if row_page==page and table.height>row_y-y:
                    heights[0]=max(heights[0],table.height-(row_y-y))
            else:heights=[height]
            pieces=[]
            for part,part_height in enumerate(heights):
                if part:
                    row_page+=1;row_y=self.top
                elif (table.page_break!='NONE' and row_y+part_height>table_bottom+.1 and row_y>self.top+.1 and part_height<=table_height):
                    row_page+=1;row_y=self.top
                pieces.append((row_page,row_y,part_height));row_y+=part_height
            row_parts.append(pieces)
        final_page=page
        for cell in table.cells:
            cx=x+col_offsets[cell.col]
            width=cell.width or sum(columns[cell.col:cell.col+cell.col_span])
            pieces=row_parts[cell.row]
            page_cell,cy,height=pieces[0]
            if cell.row_span>1 and len(pieces)==1:
                height=sum(p[2] for row in row_parts[cell.row:cell.row+cell.row_span] for p in row if p[0]==page_cell)
            extents=cell_extents[id(cell)]
            if len(pieces)==1:
                self._add(page_cell,Command('cell',(cx,cy,width,height),cell,{'table_group':id(table)}))
                spare=max(0,height-cell.padding[2]-cell.padding[3]-extents[0])
                shift=spare/2 if cell.vertical_align=='CENTER' else spare if cell.vertical_align=='BOTTOM' else 0
                final_page=max(final_page,page_cell+self._build(cell.blocks,(cx+cell.padding[0],cy+cell.padding[2]+shift),page_cell,content_width=widths[id(cell)]))
            else:
                for part_page,part_y,part_height in pieces:
                    self._add(part_page,Command('cell',(cx,part_y,width,part_height),cell,{'table_group':id(table)}))
                final_page=max(final_page,pieces[-1][0],page_cell+self._build(cell.blocks,(cx+cell.padding[0],cy+cell.padding[2]),page_cell,continuation_y=self.top+cell.padding[2],content_width=widths[id(cell)]))
        return final_page-page

    def _headers_footers(self):
        definitions=[]
        for order,part in enumerate(self.model.header_footers):
            anchors=[p for p in self._source_paragraphs.get(part.source_index,[])
                     if p.source_id==part.source_para_id and p.scope.get('kind')=='body']
            scope=getattr(part,'scope',{})
            if scope:
                anchors=[p for p in anchors if p.scope==scope]
            first=self._paragraph_pages.get((id(anchors[0]),0),0) if anchors else 0
            if len(anchors)>1:
                self.warnings.append('머리말·꼬리말의 구역 기준 문단이 중복되어 적용 쪽을 정확히 구분하지 못했습니다.')
            definitions.append((first,order,part))
        for page in range(len(self.pages)):
            for kind in ('header','footer'):
                matching=[definition for definition in definitions if definition[2].kind==kind and definition[0]<=page
                          and (definition[2].page_type!='EVEN' or not (page+1)%2)
                          and (definition[2].page_type!='ODD' or (page+1)%2)]
                if not matching:continue
                first,_,part=max(matching,key=lambda definition:definition[:2])
                self._set_page_geometry(self._page_layouts[page])
                y=self.height-self._current_page_layout.bottom_pt-part.height if part.kind=='footer' else self._current_page_layout.top_pt
                width=part.width or self.body_width
                self._field_page=(page,first)
                try:
                    begin=len(self.pages[page])
                    self._build(part.blocks,(self.left,y),page,continuation_y=y,content_width=width)
                    commands=self.pages[page][begin:]
                    extent=max((c.rect[1]+c.rect[3]-y for c in commands),default=0)
                    spare=max(0,part.height-extent)
                    shift=spare if part.vertical_align=='BOTTOM' else spare/2 if part.vertical_align=='CENTER' else 0
                    if shift:
                        for command in commands:
                            x,cy,cw,ch=command.rect
                            command.rect=(x,cy+shift,cw,ch)
                finally:self._field_page=None

    def _font(self,style,fallback=False):
        key=(style,fallback)
        if key in self._fonts:return self._fonts[key]
        font=QFont(self._fallback_font_family(style.font) if fallback else self._font_family(style.font))
        font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias|QFont.StyleStrategy.NoSubpixelAntialias|QFont.StyleStrategy.ForceOutline)
        font.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
        font.setPointSizeF(style.size)
        font.setBold(style.bold and not self._synthetic_bold(style,fallback));font.setItalic(style.italic)
        font.setUnderline(style.underline);font.setStrikeOut(style.strike)
        font.setStretch(style.stretch)
        font.setKerning(getattr(style,'use_kerning',False))
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing,0)
        if style.letter_spacing:
            nominal=QFontMetricsF(font,self._metrics_device).horizontalAdvance('H')
            font=self._font_with_advance(font,'H',nominal+style.size*style.letter_spacing/100/TEXT_UNIT_PT)
        self._fonts[key]=font
        return font

    def _synthetic_bold(self,style,fallback=False):
        if not style.bold:return False
        family=self._fallback_font_family(style.font) if fallback else self._font_family(style.font)
        if family not in self._synthetic_bolds:
            self._synthetic_bolds[family]=not any(QFontDatabase.bold(family,face) for face in QFontDatabase.styles(family))
        return self._synthetic_bolds[family]

    def _char_format(self,style,fallback=False):
        fmt=QTextCharFormat();fmt.setFont(self._font(style,fallback));fmt.setForeground(QColor(style.color))
        if self._synthetic_bold(style,fallback):fmt.setProperty(SYNTHETIC_BOLD_PROPERTY,style.size*.02/TEXT_UNIT_PT)
        return fmt

    def _font_with_advance(self,font,character,advance):
        """Fit one native control advance without guessing Qt's DPI scaling."""
        result=QFont(font)
        nominal=QFontMetricsF(result,self._metrics_device).horizontalAdvance(character)
        probe=QFont(result)
        probe.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing,result.letterSpacing()+1)
        slope=QFontMetricsF(probe,self._metrics_device).horizontalAdvance(character)-nominal
        if slope>0:
            result.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing,result.letterSpacing()+(advance-nominal)/slope)
        return result

    def _font_family(self,name):
        if name in self._families:return name
        alias=FONT_FAMILY_ALIASES.get(name)
        if alias in self._families:return alias
        if name in self._source_fonts:return self._source_fonts[name]
        if name in self._local_font_families:return self._local_font_families[name]
        if name in self.font_substitutions:return self.font_substitutions[name]
        installed=self._sfnt_resolver.resolve(name)
        if installed:
            self._local_font_families[name]=installed
            self._families.update(QFontDatabase.families())
            self._families.add(installed)
            return installed
        resolved=self._hft_resolver.resolve(name)
        if resolved:
            self._source_fonts[name]=resolved
            self._families.add(resolved)
            self.warnings.append(f"'{name}'은 설치된 HFT를 독립 변환해 표시합니다. 검증 범위는 일반 윤곽 글자이며, 힌팅·스타일과 전체 화면의 동일성은 아직 미통과입니다.")
            return resolved
        fallback=self._fallback_font_family(name)
        self.font_substitutions[name]=fallback
        self.warnings.append(f"원본 글꼴 '{name}'을 사용할 수 없어 '{fallback}'으로 표시합니다. 글자 모양이 원본과 다릅니다.")
        return fallback

    def _fallback_font_family(self,name):
        serif=any(value in name.lower() for value in ('명조','바탕','serif','times','batang'))
        choices=('바탕','Batang','Noto Serif CJK KR') if serif else ('맑은 고딕','Malgun Gothic','Noto Sans CJK KR')
        fallback=next((f for f in choices if f in self._families),QFont().family())
        return fallback

    def _glyph_fallback(self,style,character):
        return (style.font in self._source_fonts and not character.isspace() and
                unicodedata.category(character) not in {'Cc','Cf'} and
                not self._hft_resolver.supports(style.font,character))

    def _style_for_character(self,style,character):
        if not any((style.language_fonts,style.language_ratios,style.language_spacing,
                    style.language_relative_sizes,style.language_offsets)):return style
        index,uncertain=_language_index(character)
        if index is None:return style
        family=(style.language_fonts[index] or style.font) if index<len(style.language_fonts) else style.font
        kind=style.language_font_types[index] if index<len(style.language_font_types) else style.font_type
        if uncertain:
            declarations={tuple(values[i] if i<len(values) else fallback for values,fallback in (
                (style.language_fonts,style.font),(style.language_font_types,style.font_type),
                (style.language_ratios,style.stretch),(style.language_spacing,style.letter_spacing),
                (style.language_relative_sizes,100))) for i in range(7)}
        if uncertain and len(declarations)>1:
            self._script_ambiguities.setdefault((style.font,LANGUAGE_NAMES[index]),set()).add(ord(character))
        if index<len(style.language_offsets) and style.language_offsets[index]:
            self._script_offsets[(style.font,LANGUAGE_NAMES[index])]=style.language_offsets[index]
        key=(style,index)
        if key not in self._script_styles:
            ratio=style.language_ratios[index] if index<len(style.language_ratios) else style.stretch
            spacing=style.language_spacing[index] if index<len(style.language_spacing) else style.letter_spacing
            relative_size=style.language_relative_sizes[index] if index<len(style.language_relative_sizes) else 100
            self._script_styles[key]=replace(style,font=family,font_type=kind,stretch=ratio,letter_spacing=spacing,
                                            size=style.size*relative_size/100)
        return self._script_styles[key]

    def _script_offset(self,style,character):
        index,_=_language_index(character)
        if index is None or index>=len(style.language_offsets):return 0
        # An owned native PDF oracle confirms positive HWP offsets move down,
        # based on the unreduced base font height rather than the script relSz.
        return style.size*style.language_offsets[index]/100

    def _text_layout(self,command):
        paragraph=command.source
        text=command.extra['text'];start=command.extra['start']
        style=next((r.style for r in paragraph.runs if not r.image),paragraph.default_style)
        font=self._font(style)
        text_units=len(text.encode('utf-16le'))//2
        spacers=[]
        for anchor,width,position,anchor_style in self._inline_objects.get(id(paragraph),[]):
            offset=anchor-start
            if 0<=offset<text_units:
                # One visible space carries the entire inline control advance;
                # the remaining seven UTF-16 control slots stay zero-width.
                # This retains cached/run offsets and prevents following text
                # from being painted underneath an inline table or image.
                text=_utf16_slice(text,0,offset)+'\u2000'+_utf16_slice(text,offset+1)
                spacer_font=QFont(self._font(anchor_style or style))
                advance=(width+position.out_margin[0]+position.out_margin[1])/TEXT_UNIT_PT
                spacer_font=self._font_with_advance(spacer_font,'\u2000',advance)
                item=QTextLayout.FormatRange();item.start=offset;item.length=1
                item.format=QTextCharFormat();item.format.setFont(spacer_font)
                spacers.append(item)
        continued=paragraph.style.align=='JUSTIFY' and command.extra.get('justify_continuation',False)
        # A cached source line is not the paragraph's last line merely because
        # we render it separately. Keep another (unlaid-out) word so Qt applies
        # justification to this line while retaining its imported UTF-16 break.
        layout=QTextLayout(text+' X' if continued else text,font,self._metrics_device)
        option=QTextOption()
        option.setWrapMode(QTextOption.WrapMode.WrapAnywhere if continued else QTextOption.WrapMode.NoWrap)
        option.setAlignment({'CENTER':Qt.AlignmentFlag.AlignHCenter,'RIGHT':Qt.AlignmentFlag.AlignRight,'JUSTIFY':Qt.AlignmentFlag.AlignJustify}.get(paragraph.style.align,Qt.AlignmentFlag.AlignLeft))
        layout.setTextOption(option)
        formats=list(spacers)
        for run in paragraph.runs:
            if run.image:continue
            rs=run.source_start if run.source_start is not None else 0
            length=8 if run.field and run.field.get('kind')=='auto_number' else len(run.text.encode('utf-16le'))//2
            first=max(start,rs);last=min(start+text_units,rs+length)
            if last<=first:continue
            groups=[];cursor=first-start
            visible=_utf16_slice(text,first-start,last-start) if run.field else _utf16_slice(run.text,first-rs,last-rs)
            for character in visible:
                effective=self._style_for_character(run.style,character)
                fallback=self._glyph_fallback(effective,character)
                units=len(character.encode('utf-16le'))//2
                native_space=character==' ' and not getattr(effective,'use_font_space',False)
                baseline_offset=self._script_offset(run.style,character)
                if groups and groups[-1][2:]==[fallback,effective,native_space,baseline_offset]:groups[-1][1]+=units
                else:groups.append([cursor,units,fallback,effective,native_space,baseline_offset])
                cursor+=units
            for offset,length,fallback,effective,native_space,baseline_offset in groups:
                item=QTextLayout.FormatRange();item.start=offset;item.length=length
                fmt=self._char_format(effective,fallback)
                if baseline_offset:fmt.setProperty(SCRIPT_OFFSET_PROPERTY,baseline_offset)
                if native_space:
                    # With useFontSpace disabled, native HWP uses a half-em
                    # advance for ordinary spaces, independent of the face's
                    # proportional space glyph. Keep its font and extra spacing.
                    space_font=QFont(self._font(effective,fallback))
                    target=effective.size/TEXT_UNIT_PT*(effective.stretch/200+effective.letter_spacing/100)
                    space_font=self._font_with_advance(space_font,' ',target)
                    fmt.setFont(space_font)
                background=QColor(run.style.background)
                if background.isValid():fmt.setBackground(background)
                if run.style.superscript:fmt.setVerticalAlignment(QTextCharFormat.VerticalAlignment.AlignSuperScript)
                elif run.style.subscript:fmt.setVerticalAlignment(QTextCharFormat.VerticalAlignment.AlignSubScript)
                item.format=fmt;formats.append(item)
        # Synthetic field text (for example the footer page number) has no
        # literal run range. Also retain the paragraph font for cached controls
        # between literal ranges instead of silently omitting their glyphs.
        ranges=[];cursor=0
        for item in sorted(formats,key=lambda value:value.start):
            if item.start>cursor:
                gap=QTextLayout.FormatRange();gap.start=cursor;gap.length=item.start-cursor
                gap.format=self._char_format(style)
                ranges.append(gap)
            ranges.append(item);cursor=max(cursor,item.start+item.length)
        if cursor<text_units:
            gap=QTextLayout.FormatRange();gap.start=cursor;gap.length=text_units-cursor
            gap.format=self._char_format(style)
            ranges.append(gap)
        layout.setFormats(ranges);layout.beginLayout();line=layout.createLine()
        if continued:line.setNumColumns(text_units,command.rect[2]/TEXT_UNIT_PT)
        else:line.setLineWidth(command.rect[2]/TEXT_UNIT_PT)
        baseline=command.extra['line']['baseline_pt']/TEXT_UNIT_PT
        line.setPosition(QPointF(0,baseline-line.ascent()))
        layout.endLayout()
        return layout

    def _draw_text(self,painter,command):
        paragraph=command.source
        if paragraph.style.background and QColor(paragraph.style.background).isValid():
            x,y,width,height=command.rect
            left,right,top,bottom=paragraph.style.border_offsets
            painter.fillRect(QRectF((x-left)/.75,(y-top)/.75,(width+left+right)/.75,(height+top+bottom)/.75),QColor(paragraph.style.background))
        layout=self._text_layout(command)
        # Paint font outlines with grayscale antialiasing. Native screen font
        # rasterizers otherwise add RGB ClearType fringes to image/PDF output.
        painter.save();painter.translate(command.rect[0]/.75,command.rect[1]/.75)
        painter.scale(TEXT_TO_VIEW,TEXT_TO_VIEW)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing,True)
        line=layout.lineAt(0)
        for item in layout.formats():
            fmt=item.format;color=fmt.foreground().color()
            painter.save()
            painter.translate(0,(fmt.property(SCRIPT_OFFSET_PROPERTY) or 0)/TEXT_UNIT_PT)
            first=line.cursorToX(item.start)[0];last=line.cursorToX(item.start+item.length)[0]
            if fmt.background().style()!=Qt.BrushStyle.NoBrush:
                painter.fillRect(QRectF(min(first,last),line.y(),abs(last-first),line.height()),fmt.background())
            stroke=fmt.property(SYNTHETIC_BOLD_PROPERTY) or 0
            painter.setPen(QPen(color,stroke) if stroke else Qt.PenStyle.NoPen);painter.setBrush(color)
            for run in layout.glyphRuns(item.start,item.length):
                raw=run.rawFont();glyphs=run.glyphIndexes();positions=run.positions()
                paths=[raw.pathForGlyph(index) for index in glyphs]
                if any(path.isEmpty() and index for path,index in zip(paths,glyphs)):
                    # Spaces/zero-width controls legitimately have empty paths;
                    # color bitmap glyphs require the regular glyph renderer.
                    indexes=raw.glyphIndexesForString(' \u200b')
                    if any(path.isEmpty() and index not in indexes for path,index in zip(paths,glyphs)):
                        painter.setPen(color);painter.drawGlyphRun(QPointF(),run);painter.setPen(Qt.PenStyle.NoPen);continue
                stretch=fmt.fontStretch()/100 or 1
                for path,position in zip(paths,positions):
                    if stretch!=1:path=QTransform().scale(stretch,1).map(path)
                    path.translate(position);painter.drawPath(path)
            metrics=QFontMetricsF(fmt.font(),self._metrics_device)
            painter.setPen(QPen(color,max(.5/TEXT_TO_VIEW,metrics.lineWidth())))
            baseline=command.extra['line']['baseline_pt']/TEXT_UNIT_PT
            if fmt.fontUnderline():painter.drawLine(QPointF(first,baseline+metrics.underlinePos()),QPointF(last,baseline+metrics.underlinePos()))
            if fmt.fontStrikeOut():painter.drawLine(QPointF(first,baseline-metrics.strikeOutPos()),QPointF(last,baseline-metrics.strikeOutPos()))
            painter.restore()
        painter.restore()

    def _draw_box(self,painter,command,background=True):
        x,y,width,height=(v/.75 for v in command.rect)
        source=command.source
        if background and source.background:painter.fillRect(QRectF(x,y,width,height),QColor(source.background))
        for side,border in enumerate(source.borders):
            if border.style=='NONE' or border.width_pt<=0:continue
            points=[(x,y,x,y+height),(x+width,y,x+width,y+height),(x,y,x+width,y),(x,y+height,x+width,y+height)]
            if border.style=='CIRCLE':
                # HWP CIRCLE consists of separate filled round dots, rather
                # than the rectangular dash used by the DOT pen style.
                # Filled-path bounds alone omit the native boundary stroke.
                x1,y1,x2,y2=points[side]
                diameter,pitch=_print_dot_metrics(border.width_pt)
                diameter/=.75;pitch/=.75
                length=math.hypot(x2-x1,y2-y1)
                painter.save();painter.setPen(Qt.PenStyle.NoPen)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing,True)
                painter.setBrush(QColor(border.color))
                for step in range(int(length/pitch)+1):
                    distance=step*pitch
                    fraction=distance/length if length else 0
                    cx=x1+(x2-x1)*fraction;cy=y1+(y2-y1)*fraction
                    painter.drawEllipse(QRectF(cx-diameter/2,cy-diameter/2,diameter,diameter))
                painter.restore();continue
            pen=QPen(QColor(border.color),_print_line_width(border.width_pt)/.75)
            pen.setStyle({'DASH':Qt.PenStyle.DashLine,'DOT':Qt.PenStyle.DotLine,'DASH_DOT':Qt.PenStyle.DashDotLine,
                          'DASH_DOT_DOT':Qt.PenStyle.DashDotDotLine}.get(border.style,Qt.PenStyle.SolidLine))
            painter.setPen(pen)
            painter.drawLine(QPointF(*points[side][:2]),QPointF(*points[side][2:]))

    def _drawing_path(self,drawing,width,height):
        """Return the shape in local points, before the source anchor offset."""
        geometry=getattr(drawing,'geometry',{})
        shape=drawing.shape
        original_width=shape.original_width_pt or width or 1
        original_height=shape.original_height_pt or height or 1
        path=QPainterPath()
        points=geometry.get('points',[])
        if drawing.kind in ('line','polygon') and points:
            path.moveTo(*points[0])
            for point in points[1:]:path.lineTo(*point)
            if drawing.kind=='polygon':path.closeSubpath()
        elif drawing.kind=='curve' and geometry.get('segments'):
            for number,segment in enumerate(geometry['segments']):
                if not number:path.moveTo(segment['x1'],segment['y1'])
                path.lineTo(segment['x2'],segment['y2'])
        elif drawing.kind=='ellipse':
            center=geometry.get('center',(original_width/2,original_height/2))
            axis1=geometry.get('ax1',(original_width,original_height/2))
            axis2=geometry.get('ax2',(original_width/2,0))
            unit=QPainterPath();unit.addEllipse(QRectF(-1,-1,2,2))
            transform=QTransform(axis1[0]-center[0],axis1[1]-center[1],
                                 axis2[0]-center[0],axis2[1]-center[1],center[0],center[1])
            path=transform.map(unit)
        else:
            if drawing.kind=='rect' and len(shape.rect)==4 and any(x or y for x,y in shape.rect):
                path.moveTo(*shape.rect[0])
                for point in shape.rect[1:]:path.lineTo(*point)
                path.closeSubpath()
            else:path.addRect(QRectF(0,0,original_width,original_height))
        chain=geometry.get('transform_chain',[])
        if chain:
            # OWPML matrices are row-major affine 2x3 matrices. A recorded
            # Trans, Scale, Rotate chain applies Rotate first, then Scale and
            # Trans; translations use HWPUNIT while path points use points.
            for _,values in reversed(chain):
                if len(values)!=6:continue
                a,b,tx,c,d,ty=values
                path=QTransform(a,c,b,d,tx/100,ty/100).map(path)
            horizontal=shape.flip_h and not any(name=='scaMatrix' and values[0]<0 for name,values in chain if len(values)==6)
            vertical=shape.flip_v and not any(name=='scaMatrix' and values[4]<0 for name,values in chain if len(values)==6)
            if horizontal or vertical:
                transform=QTransform();transform.translate(width/2,height/2)
                transform.scale(-1 if horizontal else 1,-1 if vertical else 1)
                transform.translate(-width/2,-height/2);path=transform.map(path)
        else:
            path=QTransform().scale(width/original_width,height/original_height).map(path)
            center=QPointF(width/2,height/2)
            transform=QTransform();transform.translate(center.x(),center.y())
            if shape.rotation:transform.rotate(shape.rotation)
            transform.scale(-1 if shape.flip_h else 1,-1 if shape.flip_v else 1)
            transform.translate(-center.x(),-center.y())
            path=transform.map(path)
        return path

    def _draw_shape(self,painter,command):
        drawing=command.source
        x,y,width,height=command.rect
        path=self._drawing_path(drawing,width,height)
        border=drawing.borders[0]
        color=QColor(border.color)
        color.setAlpha(255-max(0,min(255,drawing.line_options.get('alpha',0))))
        pen=QPen(color,_print_line_width(border.width_pt)/.75)
        pen.setStyle({'NONE':Qt.PenStyle.NoPen,'DASH':Qt.PenStyle.DashLine,'LONG_DASH':Qt.PenStyle.DashLine,
                      'DOT':Qt.PenStyle.DotLine,'CIRCLE':Qt.PenStyle.DotLine,'DASH_DOT':Qt.PenStyle.DashDotLine,
                      'DASH_DOT_DOT':Qt.PenStyle.DashDotDotLine}.get(border.style,Qt.PenStyle.SolidLine))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap if drawing.line_options.get('endCap')=='ROUND' else Qt.PenCapStyle.FlatCap)
        painter.save();painter.translate(x/.75,y/.75)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing,True)
        painter.setPen(pen)
        background=QColor(drawing.background)
        painter.setBrush(background if background.isValid() and drawing.kind!='line' else Qt.BrushStyle.NoBrush)
        painter.drawPath(QTransform().scale(1/.75,1/.75).map(path))
        if drawing.kind=='line' and path.elementCount()>=2:
            first,last=path.elementAt(0),path.elementAt(path.elementCount()-1)
            for end,tip,other in (('head',QPointF(first.x,first.y),QPointF(last.x,last.y)),
                                  ('tail',QPointF(last.x,last.y),QPointF(first.x,first.y))):
                style=drawing.line_options.get(end+'Style','NORMAL')
                if style=='NORMAL':continue
                # Basic arrows use the retained style, size class and fill.
                # Their native attachment/size formula still needs an oracle.
                size=drawing.line_options.get(end+'Sz','MEDIUM_MEDIUM').split('_')
                length=max(.5,border.width_pt)*{'SMALL':4,'MEDIUM':6,'LARGE':8}.get(size[-1],6)
                half=max(.5,border.width_pt)*{'SMALL':2,'MEDIUM':3,'LARGE':4}.get(size[0],3)
                arrow=QPainterPath()
                if style.endswith('CIRCLE'):arrow.addEllipse(QRectF(-length,-half,length,2*half))
                elif style.endswith('BOX'):arrow.addRect(QRectF(-length,-half,length,2*half))
                elif style.endswith('DIAMOND'):
                    arrow.moveTo(0,0);arrow.lineTo(-length/2,-half);arrow.lineTo(-length,0);arrow.lineTo(-length/2,half);arrow.closeSubpath()
                else:
                    arrow.moveTo(0,0);arrow.lineTo(-length,-half)
                    if style=='CONCAVE_ARROW':arrow.lineTo(-length*.7,0)
                    arrow.lineTo(-length,half);arrow.closeSubpath()
                rotation=math.degrees(math.atan2(tip.y()-other.y(),tip.x()-other.x()))
                transform=QTransform();transform.translate(tip.x(),tip.y());transform.rotate(rotation)
                arrow=QTransform().scale(1/.75,1/.75).map(transform.map(arrow))
                filled=bool(drawing.line_options.get(end+'fill',style.startswith('FILLED_') or style in ('ARROW','CONCAVE_ARROW')))
                painter.setBrush(color if filled else Qt.BrushStyle.NoBrush)
                painter.drawPath(arrow)
        painter.restore()

    def _draw_image(self,painter,command):
        run=command.source;picture=run.picture
        image=QImage.fromData(run.image)
        if image.isNull():return
        iw=getattr(picture,'image_width_pt',0) or picture.original_width_pt
        ih=getattr(picture,'image_height_pt',0) or picture.original_height_pt
        left,top,right,bottom=picture.crop
        source=QRectF(0,0,image.width(),image.height())
        if iw>0 and ih>0 and right>left and bottom>top:
            source=QRectF(left/iw*image.width(),top/ih*image.height(),(right-left)/iw*image.width(),(bottom-top)/ih*image.height())
        if picture.flip_h or picture.flip_v:image=image.mirrored(picture.flip_h,picture.flip_v)
        target=QRectF(*(v/.75 for v in command.rect))
        painter.save()
        if picture.rotation:
            center=target.center();painter.translate(center);painter.rotate(picture.rotation);painter.translate(-center)
        painter.drawImage(target,image,source);painter.restore()

    def paint_page(self,painter,index):
        def order(item):
            ordinal,command=item
            rank={'BEHIND_TEXT':-1,'IN_FRONT_OF_TEXT':1}.get(command.extra.get('object_wrap'),0)
            return rank,command.extra.get('object_z_order',0) if rank else ordinal,ordinal
        commands=[command for _,command in sorted(enumerate(self.pages[index]),key=order)]
        tables=set()
        for command in commands:
            if command.kind=='text':self._draw_text(painter,command)
            elif command.kind=='image':self._draw_image(painter,command)
            elif command.kind=='box' and isinstance(command.source,Drawing) and (command.source.kind!='rect' or command.source.shape.rotation
                    or command.source.shape.flip_h or command.source.shape.flip_v or any(values!=[1,0,0,0,1,0]
                        for _,values in command.source.geometry.get('transform_chain',[]))):self._draw_shape(painter,command)
            elif command.kind=='cell' and 'table_group' in command.extra:
                group=command.extra['table_group']
                if group in tables:continue
                tables.add(group)
                cells=[c for c in commands if c.kind=='cell' and c.extra.get('table_group')==group]
                # Paint every cell background before the shared edges. A later
                # neighbour's fill must not erase half of an antialiased border.
                for cell in cells:
                    if cell.source.background:
                        painter.fillRect(QRectF(*(v/.75 for v in cell.rect)),QColor(cell.source.background))
                for cell in cells:self._draw_box(painter,cell,background=False)
            else:self._draw_box(painter,command)

    def render_page(self,index,dpi=96):
        width,height=self.page_size(index)
        image=QImage(math.ceil(width*dpi/72),math.ceil(height*dpi/72),QImage.Format.Format_RGB32)
        image.fill(Qt.GlobalColor.white)
        image.setDotsPerMeterX(round(dpi/0.0254));image.setDotsPerMeterY(round(dpi/0.0254))
        painter=QPainter(image);painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.scale(dpi/96,dpi/96)
        self.paint_page(painter,index);painter.end();return image

    def print_to(self,printer):
        width,height=self.page_size(0)
        # display_size already applies the section's landscape flag. The
        # printer orientation must not rotate those physical dimensions again.
        printer.setPageOrientation(QPageLayout.Orientation.Portrait)
        def printer_page_size(width,height):
            # The native PDF reference exporter truncates the physical paper
            # MediaBox to whole points. ExactMatch avoids Qt rounding A4 back
            # to 842 points after the verified 841-point export height.
            if printer.outputFormat()==printer.OutputFormat.PdfFormat:
                width,height=math.floor(width),math.floor(height)
            return QPageSize(QSizeF(width,height),QPageSize.Unit.Point,'',QPageSize.SizeMatchPolicy.ExactMatch)
        printer.setPageSize(printer_page_size(width,height))
        painter=QPainter()
        if not painter.begin(printer):raise OSError('원본 배치 PDF/인쇄를 시작하지 못했습니다.')
        try:
            for index in range(len(self.pages)):
                if index:
                    width,height=self.page_size(index)
                    printer.setPageSize(printer_page_size(width,height))
                    if not printer.newPage():raise OSError('다음 쪽을 출력하지 못했습니다.')
                painter.save();painter.scale(printer.resolution()/96,printer.resolution()/96)
                self.paint_page(painter,index);painter.restore()
        finally:
            if not painter.end():raise OSError('PDF/인쇄를 완료하지 못했습니다.')
        if printer.outputFormat()==printer.OutputFormat.PdfFormat:
            from .pdf_text import add_source_text
            add_source_text(printer.outputFileName(),self)

    def geometry_report(self):
        return {'pages':len(self.pages),'page_pt':[self.width,self.height],
                'pages_pt':[list(self.page_size(index)) for index in range(len(self.pages))],
                'uses_hancom_runtime':False,'uses_reference_pdf':False,'mode':'source cached line geometry; retained for compatible text edits',
                'text_metrics_dpi':TEXT_DPI,'recomputed_paragraphs':len(self._reflowed_paragraphs),
                'synthetic_text_paragraphs':len(self._synthetic_text_paragraphs),
                'recomputed_object_paragraphs':len(self._reflowed_object_paragraphs),
                'objects':[[{'kind':c.kind,'rect_pt':list(c.rect),'source_index':getattr(c.source,'source_index',None)} for c in commands] for commands in self.pages],
                'warnings':self.warnings,'font_substitutions':self.font_substitutions,
                'glyph_substitutions':self.glyph_substitutions,'hft_fonts':self._hft_resolver.fonts,
                'hft_kind_substitutions':self._hft_kind_substitutions,
                'script_ambiguities':[{'source_font':name,'language':language,'distinct_characters':len(characters)}
                                      for (name,language),characters in self._script_ambiguities.items()],
                'script_baseline_offsets':[{'source_font':name,'language':language,'offset':offset}
                                            for (name,language),offset in self._script_offsets.items()],
                'drawing_limitations':list(dict.fromkeys(self._drawing_limitations.values())),
                'installed_sfnt_fonts':self._sfnt_resolver.fonts}


class SourcePageItem(QGraphicsRectItem):
    def __init__(self,layout,index):
        width,height=layout.page_size(index)
        super().__init__(0,0,width/.75,height/.75)
        self.layout=layout;self.index=index

    def paint(self,painter,option,widget=None):
        painter.fillRect(self.rect(),Qt.GlobalColor.white)
        painter.save();painter.setClipRect(self.rect());self.layout.paint_page(painter,self.index);painter.restore()
        painter.setPen(QPen(QColor('#bdc9d7'),1));painter.drawRect(self.rect())


class SourceDocumentView(QGraphicsView):
    paragraph_edit_requested=Signal(object)

    def __init__(self,parent=None):
        super().__init__(parent);self.scene_model=QGraphicsScene(self);self.setScene(self.scene_model)
        self.layout=None;self._page_tops=[];self.setBackgroundBrush(QColor('#e9eef5'))
        self.setAlignment(Qt.AlignmentFlag.AlignHCenter|Qt.AlignmentFlag.AlignTop)
        self.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

    def set_model(self,model):
        self.scene_model.clear();self.layout=SourceLayout(model)
        self._page_tops=[];top=0.;width=0.
        for index in range(len(self.layout.pages)):
            page_width,page_height=self.layout.page_size(index)
            self._page_tops.append(top)
            item=SourcePageItem(self.layout,index);item.setPos(0,top);self.scene_model.addItem(item)
            top+=page_height/.75+16;width=max(width,page_width/.75)
        self.scene_model.setSceneRect(-16,-16,width+32,top+16)

    def set_zoom(self,factor):self.setTransform(QTransform().scale(factor,factor))

    def paragraph_at(self,point):
        if not self.layout:return None
        index=bisect_right(self._page_tops,point.y())-1
        if not 0<=index<len(self.layout.pages):return None
        y=(point.y()-self._page_tops[index])*.75
        if not 0<=y<=self.layout.page_size(index)[1]:return None
        local=QPointF(point.x()*.75,y)
        for command in reversed(self.layout.pages[index]):
            if command.kind=='text' and QRectF(*command.rect).contains(local):return command.source
        return None

    def mouseDoubleClickEvent(self,event):
        if event.button()==Qt.MouseButton.LeftButton:
            paragraph=self.paragraph_at(self.mapToScene(event.position().toPoint()))
            if paragraph is not None:
                self.paragraph_edit_requested.emit(paragraph);event.accept();return
        super().mouseDoubleClickEvent(event)

    def go_page(self,index):
        if not self.layout:return
        index=max(0,min(index,len(self.layout.pages)-1))
        self.ensureVisible(QRectF(0,self._page_tops[index],self.layout.page_size(index)[0]/.75,40),0,0)

    def visible_page(self):
        if not self.layout:return 0
        y=self.mapToScene(self.viewport().rect().topLeft()).y()
        return max(0,min(len(self.layout.pages)-1,bisect_right(self._page_tops,y)-1))
