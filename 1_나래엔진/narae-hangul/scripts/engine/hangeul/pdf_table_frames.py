"""Independent solid table grids at native printer coordinates.

Preflight is deliberately limited to complete, unmerged, single-page grids.
Other table styles retain the painter path; a processed grid is not a parity
claim. No Hancom runtime or reference document is read here.
"""
import math
import struct

from .pdf_bitmap import printer_pixel


def prepare(table,cells,origin,page_size):
    from PySide6.QtGui import QColor
    if not table.position.inline:return None,'floating-table'
    if not cells or len(cells)>10000 or len(cells)!=table.rows*table.cols:return None,'split-or-merged-grid'
    positions={(c.source.row,c.source.col) for c in cells}
    if len(positions)!=len(cells) or any(c.source.row_span!=1 or c.source.col_span!=1 for c in cells):
        return None,'split-or-merged-grid'
    borders={border for c in cells for border in c.source.borders}
    if len(borders)!=1:return None,'mixed-cell-borders'
    border=next(iter(borders))
    if border.style not in ('SOLID','NONE'):return None,'border-style'
    color=QColor(border.color)
    if not color.isValid() or color.alpha()!=255 or not math.isfinite(border.width_pt):return None,'border-color-or-width'
    xs={};ys={}
    for c in cells:
        color=QColor(c.source.background)
        if color.isValid() and color.alpha()!=255:return None,'translucent-cell-fill'
        x,y,w,h=c.rect
        if not all(math.isfinite(v) for v in c.rect) or min(w,h)<=0:return None,'invalid-cell-rectangle'
        for bounds,key,value in ((xs,c.source.col,(x,x+w)),(ys,c.source.row,(y,y+h))):
            if key in bounds and any(abs(a-b)>.001 for a,b in zip(bounds[key],value)):return None,'irregular-grid'
            bounds[key]=value
    if set(xs)!=set(range(table.cols)) or set(ys)!=set(range(table.rows)):return None,'incomplete-grid'
    for bounds in (xs,ys):
        if any(abs(bounds[i][1]-bounds[i+1][0])>.001 for i in range(len(bounds)-1)):return None,'irregular-grid'
    f32=lambda v:struct.unpack('<f',struct.pack('<f',v))[0]
    pw,ph=page_size;sx=f32(math.floor(pw)/printer_pixel(pw));sy=f32(math.floor(ph)/printer_pixel(ph))
    def coordinate(value,axis):
        # The native grid adds logical offsets to its already rounded parent
        # origin, then rounds the cumulative result to a printer pixel.
        base=origin[axis]
        pixel=math.floor((printer_pixel(base)*12+round((value-base)*100))/12+.5+1e-9)
        return round(f32(pixel*sx),3) if axis==0 else round(math.floor(ph)-f32(pixel*sy),3)
    xx=[coordinate(xs[i][0],0) for i in range(table.cols)]+[coordinate(xs[table.cols-1][1],0)]
    yy=[coordinate(ys[i][0],1) for i in range(table.rows)]+[coordinate(ys[table.rows-1][1],1)]
    return {'x':xx,'y':yy,'border':border},None


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
    left,right,top,bottom=x[col],x[col+1],y[row],y[row+1]
    return '\n'.join((_color(color,'rg'),f'{left} {top} m {right} {top} l {right} {bottom} l {left} {bottom} l h f'))


def stroke_program(grid):
    border=grid['border']
    if border.style=='NONE' or border.width_pt<=0:return None
    pixels=max(1,math.floor(border.width_pt/.12+.5));width=pixels*.12
    x,y=grid['x'],grid['y'];left,right,top,bottom=x[0],x[-1],y[0],y[-1]
    lines=[(v,top,v,bottom) for v in x]
    # Native horizontal rules explicitly extend by half the pen width and
    # use flat caps. Boundary rules are painted again after the shared grid.
    horizontal=lambda v:(round(left-math.floor(pixels/2)*.12,3),v,round(right+math.ceil(pixels/2)*.12,3),v)
    lines.extend(horizontal(v) for v in y)
    lines.extend(((right,top,right,bottom),(left,top,left,bottom),horizontal(bottom),horizontal(top)))
    program=[_color(border.color,'RG'),f'{width} w','0 J','0 j','[] 0 d']
    program.extend(f'{a} {b} m {c} {d} l S' for a,b,c,d in lines)
    return '\n'.join(program)
