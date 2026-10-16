"""Independent solid table grids at native printer coordinates.

Preflight is deliberately limited to complete, single-page rectangular grids.
Other table styles retain the painter path; a processed grid is not a parity
claim. No Hancom runtime or reference document is read here.
"""
import math
import struct

from .pdf_bitmap import printer_pixel


def prepare(table,cells,origin,page_size):
    from PySide6.QtGui import QColor
    if not table.position.inline:return None,'floating-table'
    if not 0<table.rows*table.cols<=10000 or min(table.rows,table.cols)<=0:return None,'grid-size-bounds'
    if not cells or len(cells)>table.rows*table.cols:return None,'split-grid'
    occupied={}
    for command in cells:
        cell=command.source;r,c,rs,cs=cell.row,cell.col,cell.row_span,cell.col_span
        if min(r,c)<0 or min(rs,cs)<=0 or r+rs>table.rows or c+cs>table.cols:return None,'invalid-cell-span'
        for row in range(r,r+rs):
            for col in range(c,c+cs):
                if (row,col) in occupied:return None,'overlapping-grid'
                occupied[row,col]=cell
    if len(occupied)!=table.rows*table.cols:return None,'incomplete-grid'
    if any(len(c.source.borders)!=4 for c in cells):return None,'invalid-cell-borders'
    borders={border for c in cells for border in c.source.borders};signatures={}
    for border in borders:
        if border.style not in ('SOLID','NONE'):return None,'border-style'
        color=QColor(border.color)
        if not color.isValid() or color.alpha()!=255 or not math.isfinite(border.width_pt):return None,'border-color-or-width'
        signatures[border]=(border.style,border.width_pt,color.name())
    def segments(edges,x_pixels=None):
        result=[]
        for boundary,first,last,side,pen,left,right in sorted(edges,key=lambda v:(v[0],v[1],v[3])):
            if pen[0]=='NONE' or pen[1]<=0:continue
            a=x_pixels[first]-math.floor(left/2) if x_pixels is not None else None
            b=x_pixels[last]+math.ceil(right/2) if x_pixels is not None else None
            if result and result[-1][0]==boundary and result[-1][3]==(pen,) and first<=result[-1][2]:
                previous=result[-1]
                # Only consecutive equal pens coalesce. Retain the first
                # physical start and the farthest physical end; different
                # side widths (including NONE) still affect horizontal caps.
                result[-1]=(boundary,previous[1],max(last,previous[2]),(pen,),previous[4],
                    max(b,previous[5]) if b is not None else None)
            else:result.append((boundary,first,last,(pen,),a,b))
        return result
    vertical=[];horizontal=[]
    for command in cells:
        cell=command.source;r,c,rs,cs=cell.row,cell.col,cell.row_span,cell.col_span
        left,right,top,bottom=(signatures[b] for b in cell.borders)
        vertical.extend(((c,r,r+rs,1,left,0,0),(c+cs,r,r+rs,0,right,0,0)))
        lp=max(1,math.floor(left[1]/.12+.5));rp=max(1,math.floor(right[1]/.12+.5))
        horizontal.extend(((r,c,c+cs,1,top,lp,rp),(r+rs,c,c+cs,0,bottom,lp,rp)))
    xs={};ys={}
    for c in cells:
        color=QColor(c.source.background)
        if color.isValid() and color.alpha()!=255:return None,'translucent-cell-fill'
        x,y,w,h=c.rect
        if not all(math.isfinite(v) for v in c.rect) or min(w,h)<=0:return None,'invalid-cell-rectangle'
        for bounds,key,value in ((xs,c.source.col,x),(xs,c.source.col+c.source.col_span,x+w),
                                 (ys,c.source.row,y),(ys,c.source.row+c.source.row_span,y+h)):
            if key in bounds and abs(bounds[key]-value)>.001:return None,'irregular-grid'
            bounds[key]=value
    for bounds in (xs,ys):
        values=[bounds[i] for i in sorted(bounds)]
        if any(a>=b for a,b in zip(values,values[1:])):return None,'irregular-grid'
    f32=lambda v:struct.unpack('<f',struct.pack('<f',v))[0]
    pw,ph=page_size;sx=f32(math.floor(pw)/printer_pixel(pw));sy=f32(math.floor(ph)/printer_pixel(ph))
    def coordinate_pixel(value,axis):
        # The native grid adds logical offsets to its already rounded parent
        # origin, then rounds the cumulative result to a printer pixel.
        base=origin[axis]
        return math.floor((printer_pixel(base)*12+round((value-base)*100))/12+.5+1e-9)
    # A boundary hidden entirely by merged cells needs no invented position.
    xp=[coordinate_pixel(xs[i],0) if i in xs else None for i in range(table.cols+1)]
    yp=[coordinate_pixel(ys[i],1) if i in ys else None for i in range(table.rows+1)]
    xx=[round(f32(v*sx),3) if v is not None else None for v in xp]
    yy=[round(math.floor(ph)-f32(v*sy),3) if v is not None else None for v in yp]
    vertical=segments(vertical);horizontal=segments(horizontal,xp)
    return {'x':xx,'y':yy,'x_pixels':xp,'scale_x':sx,'cols':table.cols,'rows':table.rows,
        'vertical':vertical,'horizontal':horizontal},None


def _color(value,operator):
    from PySide6.QtGui import QColor
    c=QColor(value)
    return ' '.join(str(round(v/255,4)) for v in (c.red(),c.green(),c.blue()))+' '+operator


def fill_program(grid,cell):
    from PySide6.QtGui import QColor
    color=QColor(cell.source.background)
    if not color.isValid():return None
    if color.alpha()!=255:return None
    x,y=grid['x'],grid['y'];row,col=cell.source.row,cell.source.col
    left,right,top,bottom=x[col],x[col+cell.source.col_span],y[row],y[row+cell.source.row_span]
    return '\n'.join((_color(color,'rg'),f'{left} {top} m {right} {top} l {right} {bottom} l {left} {bottom} l h f'))


def stroke_program(grid):
    f32=lambda v:struct.unpack('<f',struct.pack('<f',v))[0]
    x,y=grid['x'],grid['y'];vertical=grid['vertical'];horizontal=grid['horizontal']
    lines=[('v',*line) for line in vertical]+[('h',*line) for line in horizontal]
    # Conflicting shared edges paint both pens: left then right cell, or
    # upper then lower cell. Outer runs repeat right/down, left/up,
    # bottom/right, top/left, without reversing each individual segment.
    for kind,source,boundary in (('v',vertical,grid['cols']),('v',vertical,0),
                                  ('h',horizontal,grid['rows']),('h',horizontal,0)):
        selected=[line for line in source if line[0]==boundary]
        if boundary==0:selected.reverse()
        lines.extend((kind,*line) for line in selected)
    if not lines:return None
    program=['0 J','0 j','[] 0 d'];previous=None
    for kind,boundary,first,last,pens,start,end in lines:
        for pen in pens:
            _,width,color=pen;pixels=max(1,math.floor(width/.12+.5))
            if pen!=previous:
                program.extend((_color(color,'RG'),f"{round(f32(pixels*grid['scale_x']),3)} w"));previous=pen
            if kind=='v':a,b,c,d=x[boundary],y[first],x[boundary],y[last]
            else:
                a=round(f32(start*grid['scale_x']),3)
                c=round(f32(end*grid['scale_x']),3)
                b=d=y[boundary]
            program.append(f'{a} {b} m {c} {d} l S')
    return '\n'.join(program)
