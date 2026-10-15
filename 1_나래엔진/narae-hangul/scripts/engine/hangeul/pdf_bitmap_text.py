"""Independent normal HFT bitmap text buffers and EMF/PDF placement.

Installed glyph masks come from the bounded HFT reader. This module mirrors
the observed text buffers, integer advances and bitmap guard pixels; it does
not execute Hancom code or carry captured document/font data.
"""
import math

from PySide6.QtCore import Qt,QRectF
from PySide6.QtGui import QImage,QPainter

from .pdf_bitmap import image_frame,printer_pixel


MAX_LINE_PIXELS=16_000_000
BITBLT_WIDTH=2047
BITMAP_BYTES=0xf800


def _round(value):
    # Source dimensions have at most half-HWPUNIT precision. Suppress only
    # arithmetic noise at an exact half device pixel, far below that unit.
    return math.floor(value+.5+1e-9)


def draw_line(owner,painter,command,layout):
    """Return processing facts, or None if this line has no bitmap font.

    Preflight the entire line before painting. Mixed unsupported fonts/styles
    retain the existing renderer and are reported, without a partial repaint.
    """
    from .source_layout import (BITMAP_FONT_PROPERTY,BITMAP_TRACKING_PROPERTY,
        SYNTHETIC_BOLD_PROPERTY,SYNTHETIC_ITALIC_PROPERTY,SCRIPT_OFFSET_PROPERTY,
        BITMAP_SPACE_PROPERTY,_language_index)
    formats=layout.formats()
    if not any(f.format.property(BITMAP_FONT_PROPERTY) for f in formats):return None
    def legacy(reason):return {'handled':False,'reason':reason}
    if command.source.style.align=='JUSTIFY' and command.extra.get('justify_continuation'):
        return legacy('justified-continuation')
    line=layout.lineAt(0);cursor=0;pen=0;groups=[];group=None;remaining=set()
    text=layout.text();last_visible=len(text.rstrip('\u200b'))-1
    for character_index,character in enumerate(text):
        length=len(character.encode('utf-16le'))//2
        fmt=next((f.format for f in formats if f.start<=cursor<f.start+f.length),None)
        if character=='\u200b':cursor+=length;continue
        if fmt is None:return legacy('missing-format')
        family=fmt.property(BITMAP_FONT_PROPERTY)
        if not family:return legacy('mixed-outline-or-fallback')
        if (fmt.property(SYNTHETIC_BOLD_PROPERTY) or fmt.property(SYNTHETIC_ITALIC_PROPERTY)
            or fmt.font().bold() or fmt.font().italic() or fmt.fontStretch()!=100
            or fmt.fontUnderline() or fmt.fontStrikeOut() or fmt.property(SCRIPT_OFFSET_PROPERTY)):
            return legacy('style-or-script-offset')
        if fmt.background().style()!=Qt.BrushStyle.NoBrush:return legacy('character-background')
        size=fmt.font().pointSizeF();em=_round(size/.12)
        if not 0<em<=8192:return legacy('font-size-bounds')
        color=fmt.foreground().color();script,uncertain=_language_index(character)
        if uncertain or script is None:return legacy('unverified-character-script')
        tracking_percent=fmt.property(BITMAP_TRACKING_PROPERTY) or 0
        tracking=1+tracking_percent/100
        if tracking<=0:return legacy('nonpositive-tracking')
        space=character.isspace();key=(family,size,color.rgba(),script,tracking_percent)
        if group is None or (not space and key!=group['key']):
            group={'key':key,'start':pen,'parts':[],'em':em,'bearing':None}
            groups.append(group)
        terminal=character_index==last_visible and not command.extra.get('justify_continuation')
        glyph_tracking=1 if terminal else tracking
        if not space:
            bitmap=owner._bitmap_image(family,character,size,color)
            if bitmap is None:return legacy('unavailable-bitmap-glyph')
            ink,left,top,_=bitmap;glyph=owner._hft_resolver.bitmap_glyph(family,character,em)
            if group['bearing'] is None:
                group['bearing']=math.floor(glyph.bounds[0]*em/glyph.units_per_em)
            group['parts'].append((pen,left,top,ink))
            # Native font measurements occupy a 4-HWPUNIT (.04pt) grid.
            # Device widths are differences of rounded cumulative *absolute*
            # logical positions, rather than individually rounded advances.
            advance_hwp=owner._hft_resolver.bitmap_advance(family,character,_round(size*100))
            if advance_hwp is None:return legacy('unavailable-bitmap-advance')
            delta=_round(_round(advance_hwp/4)*glyph_tracking)*4
        else:
            fixed=fmt.property(BITMAP_SPACE_PROPERTY)
            if fixed is not None:
                # Default half-em spaces truncate to the same logical grid;
                # explicit font spaces use the font's normal rounding instead.
                target=size*100/2*glyph_tracking
                delta=math.floor(target/4+1e-9)*4
            else:
                advance_hwp=owner._hft_resolver.bitmap_advance(family,character,_round(size*100))
                if advance_hwp is None:return legacy('unavailable-bitmap-advance')
                delta=_round(_round(advance_hwp/4)*glyph_tracking)*4
            if group['bearing'] is None:group['bearing']=0
        pen+=delta;group['end']=pen;cursor+=length
        if space:group=None
    factor={'CENTER':.5,'RIGHT':1}.get(command.source.style.align,0)
    width_hwp=math.floor(command.rect[2]*100/4+1e-9)*4
    alignment=(width_hwp-pen)*factor
    plans=[];total_pixels=0
    for group in groups:
        if not group['parts']:continue
        em=group['em'];ascent=(em*85+50)//100;height=ascent+ascent//2
        start=_round((alignment+group['start'])/12)
        width=_round((alignment+group['end'])/12)-start+height//2
        aligned=(width+15)//16*16
        if width<=0 or height<=0:return legacy('nonpositive-buffer')
        total_pixels+=aligned*height
        if total_pixels>MAX_LINE_PIXELS:return legacy('line-buffer-bounds')
        plans.append((group,start,width,aligned,height))
    chunks=0;painter.save()
    try:
        painter.setRenderHint(QPainter.RenderHint.LosslessImageRendering,True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform,False)
        for group,start,width,aligned,height in plans:
            em=group['em'];bearing=group['bearing']
            surface=QImage(aligned,height,QImage.Format.Format_RGB888);surface.fill(0xffffff)
            image_painter=QPainter(surface)
            for char_start,left,top,ink in group['parts']:
                image_painter.drawImage(_round((alignment+char_start)/12)-start+left-bearing,
                                        (em*20+50)//100+top,ink)
            image_painter.end()
            x=printer_pixel(command.rect[0])+start+bearing
            y=printer_pixel(command.rect[1])+_round(command.extra['line']['baseline_pt']/.12)-em
            max_height=max(1,BITMAP_BYTES//(aligned//8));allocated_height=min(max_height,height)
            stripe=QImage(aligned,allocated_height,QImage.Format.Format_RGB888);stripe.fill(0xffffff)
            for yy in range(0,height,max_height):
                hh=min(max_height,height-yy)
                # The native DDB is reused. A short last SetBitmapBits write
                # retains earlier rows below hh, including captured guards.
                image_painter=QPainter(stripe)
                image_painter.drawImage(0,0,surface.copy(0,yy,aligned,hh));image_painter.end()
                for xx in range(0,width,BITBLT_WIDTH):
                    ww=min(BITBLT_WIDTH,width-xx)
                    left=max(0,xx-1);right=min(aligned,xx+ww+2)
                    piece=stripe.copy(left,0,right-left,min(allocated_height,hh+2))
                    frame=image_frame(((x+xx)*.12,(y+yy)*.12,ww*.12,hh*.12),
                                      (owner._pdf_page_width,owner._pdf_page_height))
                    painter.drawImage(QRectF(*(v/.75 for v in frame['rect'])),piece);chunks+=1
    finally:painter.restore()
    return {'handled':True,'groups':len(plans),'image_chunks':chunks,'remaining':sorted(remaining)}
