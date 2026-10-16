"""Verified installed TrueType device widths for cached PDF text.

Only Windows GDI and the selected installed font are read. No office runtime,
reference PDF, captured glyph or font-specific adjustment is used here.
"""
import math
import sys


def centered_advances(text,logical,device):
    """Center device glyphs in logical advances, retaining endpoint carry."""
    if len(text)!=len(logical) or len(text)!=len(device):raise ValueError('device-width-count')
    result=list(logical);carry=0
    for i,(character,base,width) in enumerate(zip(text,logical,device)):
        if character==' ':
            if i:result[i-1]+=carry
            carry=0
            continue
        difference=base-width
        if not difference:continue
        half=math.trunc(difference/2);remainder=difference-half*2
        if i==0:
            carry=half+remainder
            result[i]=width+half+(carry if len(text)==1 else 0)
        else:
            result[i-1]+=half
            result[i]=width+half+remainder+(carry if i==len(text)-1 else 0)
    return result


def tracked_advances(text,advances,spacing):
    """Apply tracking after device centering, with signed nearest rounding."""
    if len(text)!=len(advances):raise ValueError('tracking-width-count')
    terminal=len(text.rstrip(' '))-1;result=[]
    for i,advance in enumerate(advances):
        numerator=math.trunc(advance/4)*spacing
        tracking=(abs(numerator)+50)//100*4
        if numerator<0:tracking=-tracking
        result.append(advance+(tracking if i!=terminal else 0))
    return result


def installed_widths(family,em,characters,tables):
    """Return widths in 1/100 point units after verifying the selected face."""
    if sys.platform!='win32':raise ValueError('device-metrics-platform')
    if not 0<em<=8192 or len(characters)>20000 or any(not 0<=ord(c)<65536 for c in characters):
        raise ValueError('device-metrics-bounds')
    import ctypes as c
    from ctypes import wintypes as w
    gdi=c.WinDLL('gdi32',use_last_error=True)
    gdi.CreateCompatibleDC.argtypes=[w.HDC];gdi.CreateCompatibleDC.restype=w.HDC
    gdi.CreateFontW.argtypes=[c.c_int]*5+[w.DWORD]*8+[w.LPCWSTR];gdi.CreateFontW.restype=w.HFONT
    gdi.SelectObject.argtypes=[w.HDC,w.HGDIOBJ];gdi.SelectObject.restype=w.HGDIOBJ
    gdi.GetFontData.argtypes=[w.HDC,w.DWORD,w.DWORD,c.c_void_p,w.DWORD];gdi.GetFontData.restype=w.DWORD
    gdi.GetCharWidth32W.argtypes=[w.HDC,w.UINT,w.UINT,c.POINTER(c.c_int)];gdi.GetCharWidth32W.restype=w.BOOL
    gdi.DeleteObject.argtypes=[w.HGDIOBJ];gdi.DeleteDC.argtypes=[w.HDC]
    dc=gdi.CreateCompatibleDC(None);font=None;previous=None
    if not dc:raise ValueError('device-context-unavailable')
    try:
        font=gdi.CreateFontW(-em,0,0,0,0,0,0,0,1,7,0,0,0,family)
        if not font:raise ValueError('device-font-unavailable')
        previous=gdi.SelectObject(dc,font)
        if not previous or previous==c.c_void_p(-1).value:raise ValueError('device-font-selection')
        for tag,expected in tables.items():
            if not expected or len(expected)>2*1024*1024:raise ValueError('device-font-table-bounds')
            table=int.from_bytes(tag.encode('ascii'),'little')
            if gdi.GetFontData(dc,table,0,None,0)!=len(expected):raise ValueError('unverified-device-font')
            buffer=c.create_string_buffer(len(expected))
            if gdi.GetFontData(dc,table,0,buffer,len(expected))!=len(expected) or buffer.raw!=expected:
                raise ValueError('unverified-device-font')
        result={}
        for character in set(characters):
            width=c.c_int()
            if not gdi.GetCharWidth32W(dc,ord(character),ord(character),c.byref(width)):
                raise ValueError('device-width-unavailable')
            result[character]=width.value*12
        return result
    finally:
        if previous and previous!=c.c_void_p(-1).value:gdi.SelectObject(dc,previous)
        if font:gdi.DeleteObject(font)
        gdi.DeleteDC(dc)
