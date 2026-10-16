"""Independent stepped cell fills from document parameters, at 600 dpi.

The radial band geometry and integer interpolation follow observed drawing
calls, not pixels or paths copied from a reference PDF. Other fill kinds and
source alpha keep a painter fallback with an explicit diagnostic.
"""
import math
import struct


def _f32(value):
    return struct.unpack('<f',struct.pack('<f',value))[0]


def _muldiv(a,b,c):
    value=a*b
    return (1 if value>=0 else -1)*((abs(value)+c//2)//c)


def _rgb(value):
    return tuple(int(value[i:i+2],16) for i in (1,3,5))


def _color(g,index):
    start,end=map(_rgb,(g.colors[0],g.colors[-1]))
    return tuple(a+math.trunc((b-a)*index/(g.step-1)) if g.step>1 else a for a,b in zip(start,end))


def radial_bands(radius,step,center):
    """Yield outer-to-inner (color index, integer radius) pairs.

    Each side of the midpoint rounds independently. Odd step counts use a
    doubled iteration grid and retain both bands adjacent to the midpoint.
    """
    if step<=0:return
    middle=_muldiv(radius,center,100);outer=radius-middle
    odd=step&1;count=step*2 if odd else step;half=count//2
    for i in range(count-1,-1,-1):
        if odd and i&1 and i not in (half,half-1):continue
        q=1 if not odd or i in (half,half-1) else 2
        r=(middle+_muldiv(outer,i-half+q,half) if i>=half else _muldiv(middle,i+q,half))
        yield (i//2 if odd else i),r


def cell_program(grid,cell):
    """Return a physical PDF program and a fallback reason, if unsupported."""
    from .formats import _validate_gradient
    from .pdf_table_frames import clip_program
    g=cell.gradient
    try:_validate_gradient(g)
    except (ValueError,TypeError):return None,'invalid-gradient'
    if g.alpha:return None,'gradient-alpha'
    if len(g.colors)!=2:return None,'gradient-color-stops'
    if g.kind!='RADIAL':return None,'gradient-'+g.kind.lower()
    row,col=cell.row,cell.col
    left,right=grid['x_pixels'][col],grid['x_pixels'][col+cell.col_span]
    top,bottom=grid['y_pixels'][row],grid['y_pixels'][row+cell.row_span]
    x=left-1+math.trunc((right-left+2)*g.center_x/100)
    y=top-1+math.trunc((bottom-top+2)*g.center_y/100)
    dx=max(abs(x-left+1),abs(right+1-x));dy=max(abs(y-top+1),abs(bottom+1-y))
    radius=math.floor(math.hypot(dx,dy)+.5)
    def point(x,y):
        # The printer path first stores each control point as REAL, applies
        # REAL page scaling, then subtracts from the REAL paper height.
        xx=_f32(_f32(x)*grid['scale_x'])
        yy=_f32(grid['page_height']-_f32(_f32(y)*grid['scale_y']))
        return f'{round(xx,3)} {round(yy,3)}'
    def color(rgb):return ' '.join(str(round(v/255,4)) for v in rgb)+' rg'
    program=[clip_program(grid,cell)]
    if not g.step or g.colors[0].lower()==g.colors[1].lower():
        program.append(color(_rgb(g.colors[0]) if g.colors[0].lower()==g.colors[1].lower() else (255,255,255)))
        xx,yy=grid['x'],grid['y'];l,r,t,b=xx[col],xx[col+cell.col_span],yy[row],yy[row+cell.row_span]
        program.append(f'{l} {t} m {r} {t} l {r} {b} l {l} {b} l h f*')
        return '\n'.join(program),None
    x-=.5;y-=.5;kappa=4/3*math.tan(math.pi/8)
    for index,radius in radial_bands(radius,g.step,g.step_center):
        r=radius-.5
        if r<=0:continue
        d=kappa*r;program.extend((color(_color(g,index)),point(x+r,y)+' m'))
        for values in ((x+r,y-d,x+d,y-r,x,y-r),(x-d,y-r,x-r,y-d,x-r,y),
                       (x-r,y+d,x-d,y+r,x,y+r),(x+d,y+r,x+r,y+d,x+r,y)):
            program.append(' '.join(point(values[j],values[j+1]) for j in (0,2,4))+' c')
        program.append('f*')
    return '\n'.join(program),None


def paint_fallback(painter,rect,g):
    """Visible Qt fallback; never counted as verified native fill geometry."""
    from PySide6.QtCore import QPointF,QRectF
    from PySide6.QtGui import QColor,QLinearGradient,QRadialGradient,QConicalGradient
    cx=rect.left()+rect.width()*g.center_x/100;cy=rect.top()+rect.height()*g.center_y/100
    center=QPointF(cx,cy);radius=max(math.hypot(cx-x,cy-y) for x in (rect.left(),rect.right()) for y in (rect.top(),rect.bottom()))
    if g.kind=='RADIAL':brush=QRadialGradient(center,max(.01,radius))
    elif g.kind=='CONICAL':brush=QConicalGradient(center,g.angle)
    else:
        angle=math.radians(g.angle);dx=math.cos(angle)*rect.width()/2;dy=-math.sin(angle)*rect.height()/2
        brush=QLinearGradient(QPointF(rect.center().x()-dx,rect.center().y()-dy),QPointF(rect.center().x()+dx,rect.center().y()+dy))
    for i,value in enumerate(g.colors):
        position=g.positions[i]/100 if len(g.positions)==len(g.colors) else i/(len(g.colors)-1)
        color=QColor(value);color.setAlpha(255-g.alpha);brush.setColorAt(position,color)
    painter.save()
    try:
        if g.kind=='SQUARE':
            painter.setClipRect(rect)
            from PySide6.QtCore import Qt
            painter.setPen(Qt.PenStyle.NoPen)
            for i in range(99,-1,-1):
                t=(i+1)/100
                a,b=map(QColor,(g.colors[0],g.colors[-1]));color=QColor(*[round(a.getRgb()[j]+(b.getRgb()[j]-a.getRgb()[j])*i/99) for j in range(3)])
                color.setAlpha(255-g.alpha);painter.setBrush(color)
                painter.drawRect(QRectF(cx-radius*t,cy-radius*t,2*radius*t,2*radius*t))
        else:painter.fillRect(rect,brush)
    finally:painter.restore()
