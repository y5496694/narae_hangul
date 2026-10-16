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
    occupied=set();vertical={};horizontal={}
    for command in cells:
        cell=command.source;r,c,rs,cs=cell.row,cell.col,cell.row_span,cell.col_span
        if min(r,c)<0 or min(rs,cs)<=0 or r+rs>table.rows or c+cs>table.cols:return None,'invalid-cell-span'
        for row in range(r,r+rs):
            for col in range(c,c+cs):
                if (row,col) in occupied:return None,'overlapping-grid'
                occupied.add((row,col))
        for col in (c,c+cs):vertical.setdefault(col,[]).append((r,r+rs))
        for row in (r,r+rs):horizontal.setdefault(row,[]).append((c,c+cs))
    if len(occupied)!=table.rows*table.cols:return None,'incomplete-grid'
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
    def joined(intervals):
        result=[]
        for first,last in sorted(intervals):
            if result and first<=result[-1][1]:result[-1]=(result[-1][0],max(last,result[-1][1]))
            else:result.append((first,last))
        return result
    return {'x':xx,'y':yy,'border':border,'x_pixels':xp,'scale_x':sx,
        'vertical':[(xx[col],yy[first],yy[last]) for col,intervals in sorted(vertical.items()) for first,last in joined(intervals)],
        'horizontal':[(first,row,last) for row,intervals in sorted(horizontal.items()) for first,last in joined(intervals)]},None


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
    border=grid['border']
    if border.style=='NONE' or border.width_pt<=0:return None
    pixels=max(1,math.floor(border.width_pt/.12+.5))
    f32=lambda v:struct.unpack('<f',struct.pack('<f',v))[0]
    width=round(f32(pixels*grid['scale_x']),3)
    x,y=grid['x'],grid['y'];left,right,top,bottom=x[0],x[-1],y[0],y[-1]
    lines=[(v,first,v,last) for v,first,last in grid['vertical']]
    # Native horizontal rules explicitly extend by half the pen width and
    # use flat caps. Boundary rules are painted again after the shared grid.
    horizontal=lambda a,v,b:(round(f32((grid['x_pixels'][a]-math.floor(pixels/2))*grid['scale_x']),3),y[v],
        round(f32((grid['x_pixels'][b]+math.ceil(pixels/2))*grid['scale_x']),3),y[v])
    lines.extend(horizontal(a,v,b) for a,v,b in grid['horizontal'])
    lines.extend(((right,top,right,bottom),(left,top,left,bottom),horizontal(0,-1,-1),horizontal(0,0,-1)))
    program=[_color(border.color,'RG'),f'{width} w','0 J','0 j','[] 0 d']
    program.extend(f'{a} {b} m {c} {d} l S' for a,b,c,d in lines)
    return '\n'.join(program)
