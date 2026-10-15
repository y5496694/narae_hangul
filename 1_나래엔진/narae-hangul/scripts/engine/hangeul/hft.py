"""Bounded, independent HFT experiment based on read-only REA evidence.

No native DLL is imported. Fonts are read from the user's installation.
Implemented: group inventory, direct/KS outline glyphs and direct bitmap glyphs.
Implemented Hangul bitmap composition for map kind 2 and Full Jaso kind 3.
Map kind 4 offsets and other vendor variations remain incomplete.
"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import hashlib
import re
import struct
MAX_FONT_BYTES = 32 * 1024 * 1024

class HftError(ValueError):
    pass

@dataclass(frozen=True)
class Glyph:
    character: str
    units_per_em: int
    kind: str
    bounds: tuple[int,int,int,int]
    operations: tuple
    provenance: dict

    def bitmap_mask(self, em_pixels):
        """Independent monochrome raster, with its origin relative to the em.

        Enlargement expands integer pixel cell boundaries; reduction splats
        every set source bit with OR composition. The requested printer em
        is rounded to the nearest integer. Returns a Pillow L mask and
        (left, top) in full-em pixels.
        """
        import math
        from PIL import Image,ImageDraw
        if not self.kind.startswith('bitmap'):raise HftError('Not a bitmap glyph')
        if not 0<float(em_pixels)<=8192:raise HftError('Bitmap em exceeds rendering limit')
        em=max(1,math.floor(float(em_pixels)+.5));scale=em/self.units_per_em
        rectangles=[]
        for op,x,y,w,h in self.operations:
            if op!='R':raise HftError('Invalid bitmap operation')
            left=math.floor(x*scale+1e-9);right=math.floor((x+w)*scale+1e-9)
            top=math.floor((self.units_per_em-y-h)*scale+1e-9)
            bottom=math.floor((self.units_per_em-y)*scale+1e-9)
            if em<self.units_per_em:
                # Reduction splats every set source bit and ORs collisions.
                # Preserve strokes whose scaled cell boundaries collapse.
                right=math.floor((x+w-1)*scale+1e-9)+1
                bottom=math.floor((self.units_per_em-y-1)*scale+1e-9)+1
            if right>left and bottom>top:rectangles.append((left,top,right,bottom))
        if not rectangles:return Image.new('L',(1,1)),(0,0)
        left=min(r[0] for r in rectangles);top=min(r[1] for r in rectangles)
        right=max(r[2] for r in rectangles);bottom=max(r[3] for r in rectangles)
        if (right-left)*(bottom-top)>16_777_216:raise HftError('Bitmap surface exceeds limit')
        image=Image.new('L',(right-left,bottom-top));draw=ImageDraw.Draw(image)
        for x,y,r,b in rectangles:draw.rectangle((x-left,y-top,r-left-1,b-top-1),fill=255)
        return image,(left,top)

    def painter_path(self, em_size=1000, native_pdf_quantization=False):
        """Qt path; outlines apply observed 0.15em native baseline adjustment.

        native_pdf_quantization=True exactly reproduces sampled normal Hanyang
        Type3 coordinates before scaling to the requested em_size. Bitmap
        baseline/strike selection is still an explicit experimental limitation.
        """
        from PySide6.QtGui import QPainterPath
        from PySide6.QtCore import Qt
        p=QPainterPath();p.setFillRule(Qt.FillRule.WindingFill)
        import math
        scale=float(em_size)/self.units_per_em
        shift=self.provenance.get('baseline_shift_em',0)*self.units_per_em
        def point(value,is_y):
            value=value-(shift if is_y else 0)
            if native_pdf_quantization:value=math.trunc(value*1000/self.units_per_em)*float(em_size)/1000
            else:value*=scale
            return -value if is_y else value
        for op,*args in self.operations:
            if op=='M':p.moveTo(point(args[0],False),point(args[1],True))
            elif op=='L':p.lineTo(point(args[0],False),point(args[1],True))
            elif op=='C':p.cubicTo(*(point(v,bool(i%2)) for i,v in enumerate(args)))
            elif op=='Z':p.closeSubpath()
            elif op=='R':p.addRect(args[0]*scale,-(args[1]+args[3])*scale,args[2]*scale,args[3]*scale)
            else:raise HftError(f'Unsupported path operator {op}')
        return p

class HftFont:
    def __init__(self,path):
        self.path=Path(path)
        if not self.path.is_file() or self.path.stat().st_size > MAX_FONT_BYTES:
            raise HftError('HFT must be a regular file of at most 32 MiB')
        with self.path.open('rb') as stream:self.data=stream.read(MAX_FONT_BYTES+1)
        if len(self.data)>MAX_FONT_BYTES:raise HftError('HFT exceeds 32 MiB limit')
        if len(self.data)<512 or not self.data.startswith(b'Han Unified Font File 1.0\x1a\x04\x03\x02\x01'):
            raise HftError('Unsupported HFT magic/endian marker')
        if self.u32(32)!=1 or self.u32(36)!=len(self.data):raise HftError('Unsupported header/version/length')
        checksum=self.u16(42);header=bytearray(self.data[:512]);header[42:44]=b'\0\0'
        if sum(header)&65535!=checksum:raise HftError('Header checksum mismatch')
        self.sha256=hashlib.sha256(self.data).hexdigest()
        self.unicode_tables={}
        self.mapping_sources=[]
        # REA LoadUnicodeTbl: general symbols are indexed by Unicode; the
        # CJK table starts at U+3400. Tables belong to this installation and
        # are read as data, never copied into a distribution or executed.
        base=self.path.resolve().parent.parent.parent
        code_dir=next((p for p in (base/'Bin'/'Code',base/'Code') if (p/'u300_gen.dat').is_file()),None)
        if code_dir is not None:
            for name,count in (('gen',0x4000),('cjk',0x6f00)):
                path=code_dir/('u300_'+name+'.dat')
                if not path.is_file():continue
                if path.stat().st_size>128*1024:raise HftError('Unicode mapping table exceeds limit')
                with path.open('rb') as stream:data=stream.read(128*1024+1)
                if len(data)>128*1024:raise HftError('Unicode mapping table exceeds limit')
                header=re.search(rb'Data count : (\d+) *\r?\n',data[:640])
                if b'Unicode version : 3.00' not in data[:640] or not header or int(header[1])!=count or len(data)-header.end()!=count*2:
                    raise HftError('Invalid installed Unicode mapping table')
                self.unicode_tables[name]=struct.unpack('<'+'H'*count,data[header.end():])
                self.mapping_sources.append({'path':str(path),'sha256':hashlib.sha256(data).hexdigest()})
        self.family=self.data[44:108].split(b'\0')[0].decode('johab','replace')
        self.vendor_hint=self.data[308:340].split(b'\0')[0].decode('ascii','replace')
        self.outline_count=self.u16(422);self.bitmap_count=self.u16(424)
        if self.outline_count+self.bitmap_count>4096:raise HftError('Too many HFT groups')
        self.groups=[];self.unknown_records=[];offset=self.u32(430)
        self.font_em=self.u16(378)
        if self.font_em==0:raise HftError('Invalid zero font em')
        self.width_records=[];width_offset=self.u32(426)
        for _ in range(self.u16(420)):
            size,first,last,kind=self.unpack('<IHHH',width_offset)
            if size<12:raise HftError('Invalid width block')
            self.slice(width_offset,size)
            self.width_records.append({'offset':width_offset,'size':size,'first':first,'last':last,'kind':kind})
            width_offset+=size
        for index in range(self.outline_count+self.bitmap_count):
            size,tag,kind,count,delta=self.unpack('<IHHHI',offset)
            if size<14 or not 1<=count<=256:raise HftError('Invalid component group')
            self.slice(offset,size)
            children=[];sub=offset+delta
            for _ in range(count):
                sz,flags,first,last,num,em,x,y,w,h=self.unpack('<IHHHHHhhhh',sub)
                if sz<22 or em==0 or num==0 or sub<offset+14 or sub+sz>offset+size:
                    self.unknown_records.append({'offset':sub,'group_offset':offset,'reason':'Unresolved record boundary/size'})
                    break
                payload=sub+22;mapping=None
                if flags&15 in (1,2,4):
                    length,tableid=self.unpack('<HH',payload)
                    if length<4:raise HftError('Invalid character map length')
                    if payload+length>sub+sz:raise HftError('Character map exceeds glyph record')
                    mapping={'length':length,'table_id':tableid,'data':self.slice(payload,length)}
                    payload+=length
                elif flags&15 not in (0,3):raise HftError('Unsupported glyph map kind')
                children.append({'offset':sub,'size':sz,'flags':flags,'first':first,'last':last,'count':num,'em':em,
                                 'bounds':(x,y,w,h),'mapping':mapping,'payload':payload})
                sub+=sz
            self.groups.append({'offset':offset,'size':size,'strike':tag,'kind':kind,'children':children})
            offset+=size

    def advance(self,character,em_size=1.0,style_bits=0):
        """Advance scaled to requested em; supported constant/per-code/KS widths.

        Native normal width comes from FACE_NAME script's own font file, not glyph
        bounds. Script selection remains the caller's responsibility.
        """
        code=self.hchar(character);width=self.font_em
        for r in self.width_records:
            if not r['first']<=code<=r['last']:continue
            if r['kind']==0:width=self.unpack('<h',r['offset']+10)[0]
            elif r['kind']==1:
                if code<32768:index=code-r['first']
                else:
                    encoded=character.encode('euc_kr')
                    if len(encoded)!=2 or not 0xb0<=encoded[0]<=0xc8:raise HftError('Unsupported KS/Jaso width mapping')
                    index=(encoded[0]-0xb0)*94+encoded[1]-0xa1
                width_offset=r['offset']+10+index*2
                if width_offset+2>r['offset']+r['size']:raise HftError('Width index exceeds width record')
                width=self.unpack('<h',width_offset)[0]
            else:raise HftError(f'Width map kind {r["kind"]} not implemented')
            break
        if style_bits&2:width+=(self.font_em+10)//20
        if style_bits&16:width+=(self.font_em+8)>>4
        if style_bits&96:width=width*16/25
        return width*float(em_size)/self.font_em

    def slice(self,offset,length):
        if not isinstance(offset,int) or not isinstance(length,int) or offset<0 or length<0 or offset+length>len(self.data):
            raise HftError('Out-of-bounds HFT read')
        return self.data[offset:offset+length]
    def unpack(self,fmt,offset):return struct.unpack(fmt,self.slice(offset,struct.calcsize(fmt)))
    def u16(self,offset):return self.unpack('<H',offset)[0]
    def u32(self,offset):return self.unpack('<I',offset)[0]

    def hchar(self,character):
        if len(character)!=1:raise HftError('One Unicode character required')
        code=ord(character)
        if code<128:return code
        if not 0xac00<=code<=0xd7a3:
            table=self.unicode_tables.get('gen')
            if code<0x4000 and table is not None:
                value=table[code]
                if value!=0x3441:return value
            table=self.unicode_tables.get('cjk')
            index=code-0x3400 if 0x3400<=code<=0x9fff else code-0x8c00 if 0xf900<=code<=0xfaff else -1
            if table is not None and 0<=index<len(table) and table[index]!=0x3441:
                return table[index]
            raise HftError('Unicode→HChar mapping unavailable')
        try:b=character.encode('johab')
        except UnicodeEncodeError as exc:raise HftError('Unicode→HChar mapping incomplete') from exc
        if len(b)!=2:raise HftError('Unsupported HChar width')
        return int.from_bytes(b,'big')

    def glyph_index(self,record,character):
        code=self.hchar(character)
        if not record['first']<=code<=record['last']:return None
        kind=record['flags']&15
        if kind==0:return code-record['first']
        if kind==1:
            mapping=record['mapping']
            if mapping['table_id']==65535:
                # Native HncGetKSTblPtr: KS X1001 Hangul block, 25 rows of 94.
                try:encoded=character.encode('euc_kr')
                except UnicodeEncodeError:return None
                if len(encoded)!=2 or not 0xb0<=encoded[0]<=0xc8 or not 0xa1<=encoded[1]<=0xfe:return None
                return (encoded[0]-0xb0)*94+encoded[1]-0xa1
            codes=struct.unpack('<'+'H'*((mapping['length']-4)//2),mapping['data'][4:])
            try:return codes.index(code)
            except ValueError:return None
        if kind==2:return self._composite_indices(record,code)
        if kind==3:
            if (code&0x83c0)!=0x8000 or 0xe829<=code<=0xe83f:return None
            if 0x20<=code&255<=0x3f and code>>8 in (0xec,0xf0,0xf4,0xf8,0xfc):return None
            return ((code>>5)&0x3e0)|((code&0x20)<<5)|(code&0x1f)
        raise HftError(f'Glyph map kind {kind} not implemented')

    def _composite_indices(self,record,code):
        # Exact static field-index tables observed via REA read_bytes at 0x100c6e20.
        leading=(20,0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,21,22,23,24,25,26,27,28,29,30,31)
        vowel=(-1,-1,0,1,2,3,4,5,-1,-1,6,7,8,9,10,11,-1,22,12,13,14,15,16,17,23,24,18,19,20,21,25,26)
        trailing=(28,0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,29,17,18,19,20,21,22,23,24,25,26,27,30,31)
        jaso=(leading[(code>>10)&31],vowel[(code>>5)&31],trailing[code&31])
        data=record['mapping']['data']
        if len(data)<300 or min(jaso)<0 or any(jaso[i]>data[4+i] for i in range(3)):return None
        dims=tuple(data[200:203]);variants=tuple(data[204+32*i+jaso[i]] for i in range(3))
        if any(variants[i]>=dims[i] for i in range(3)):return None
        matrix_index=(dims[1]*variants[0]+variants[1])*dims[2]+variants[2]
        matrix_size=dims[0]*dims[1]*dims[2]
        total=0;component_bases=[]
        for script in range(3):
            sizes=struct.unpack_from('<32H',data,8+script*64)
            offsets=[]
            for value in sizes[:data[4+script]+1]:offsets.append(total);total+=value
            component_bases.append(offsets)
        if total!=record['count']:raise HftError('Unsupported composite font count/flag variant')
        result=[]
        for script in range(3):
            if jaso[script]:
                offset=300+script*matrix_size+matrix_index
                if offset>=len(data):raise HftError('Truncated component variation matrix')
                result.append(component_bases[script][jaso[script]]+data[offset])
        return tuple(result)

    def glyph(self,character,*,strike=None,prefer_outline=True):
        groups=[g for g in self.groups if g['kind']==int(prefer_outline) and (strike is None or g['strike']==strike)]
        if not groups and prefer_outline:
            groups=[g for g in self.groups if g['kind']==0 and (strike is None or g['strike']==strike)]
        # Printer output selects the largest available bitmap strike. Smaller
        # strikes are retained for explicit diagnostic requests.
        if groups and not groups[0]['kind']:groups.sort(key=lambda g:g['strike'],reverse=True)
        for g in groups:
            for record in g['children']:
                try:index=self.glyph_index(record,character)
                except HftError:continue
                if index is None:continue
                if isinstance(index,tuple):
                    if g['kind'] or not index or any(not 0<=i<record['count'] for i in index):continue
                    parts=[self._bitmap(g,record,i,character) for i in index]
                    return Glyph(character,parts[0].units_per_em,'bitmap-composite',parts[0].bounds,
                                 tuple(op for p in parts for op in p.operations),
                                 {'font_sha256':self.sha256,'component_indices':index,'parts':[p.provenance for p in parts],
                                  'composition':'OR of same-origin packed monochrome masks','decoder':'independent-python'})
                if not 0<=index<record['count']:continue
                if g['kind']:
                    return self._outline(g,record,index,character)
                return self._bitmap(g,record,index,character)
        raise HftError('Glyph/strike unavailable in implemented mapping subset')

    @staticmethod
    def decode_hanyang(data):
        key=0xe696;out=bytearray()
        for value in data:
            out.append((key>>8)^value)
            key=((key+value)*-0x38c2+0xc863)&65535
        return bytes(out)

    def _outline(self,group,r,index,character):
        base=r['payload'];relative=self.u32(base+index*4)
        if relative==0:raise HftError('Missing glyph index')
        offset=base+relative;bounds=r['bounds']
        if base+index*4+4>r['offset']+r['size'] or offset+2>r['offset']+r['size']:
            raise HftError('Outline index exceeds glyph record')
        if not r['flags']&16:
            bounds=self.unpack('<hhhh',offset);offset+=8
        length=self.u16(offset);offset+=2
        if not 1<=length<=8192:raise HftError('Invalid contour byte length')
        if offset+length>r['offset']+r['size']:raise HftError('Outline program exceeds glyph record')
        data=self.slice(offset,length)
        if self.vendor_hint.startswith('Hanyang outline font for HWP 2.1'):
            data=self.decode_hanyang(data)
        elif self.vendor_hint and not self.vendor_hint.startswith('Human Font for HWP 2.1'):
            raise HftError(f'Unsupported outline vendor {self.vendor_hint!r}')
        operations=decode_contours(data)
        return Glyph(character,r['em'],'outline',tuple(bounds),operations,
                     {'font_sha256':self.sha256,'group_offset':group['offset'],'record_offset':r['offset'],
                      'glyph_index':index,'data_offset':offset,'data_bytes':length,'decoder':'independent-python',
                      'baseline_shift_em':0 if r['flags']&32 else 0.15,
                      'native_evidence':['FUN_100ab220','FUN_100abc00','FUN_100ad2c0','FUN_10026b70','FUN_10029c50']})

    def _bitmap(self,group,r,index,character):
        bounds=r['bounds'];base=r['payload']
        if r['flags']&16:
            x,y,w,h=bounds;row=(w+7)//8;offset=base+index*row*h
        else:
            relative=self.u32(base+index*4)
            if relative==0:raise HftError('Missing bitmap glyph index')
            offset=base+relative;bounds=self.unpack('<hhhh',offset);offset+=8
            x,y,w,h=bounds;row=(w+7)//8
        if not 1<=w<=4096 or not 1<=h<=4096:raise HftError('Invalid bitmap size')
        if offset+row*h>r['offset']+r['size']:raise HftError('Bitmap data exceeds glyph record')
        data=self.slice(offset,row*h);operations=[]
        # Packed rows are big-endian bits. Coalesce adjacent pixels into row rectangles.
        for yy in range(h):
            start=None
            for xx in range(w+1):
                black=xx<w and bool(data[yy*row+xx//8]&(128>>(xx%8)))
                if black and start is None:start=xx
                if not black and start is not None:
                    operations.append(('R',x+start,y-yy-1,xx-start,1));start=None
        return Glyph(character,r['em'],'bitmap',tuple(bounds),tuple(operations),
                     {'font_sha256':self.sha256,'group_offset':group['offset'],'record_offset':r['offset'],
                      'glyph_index':index,'data_offset':offset,'data_bytes':len(data),'decoder':'independent-python',
                      'native_evidence':['FUN_100abc00','FUN_100ac080','FUN_100ac550']})

def decode_contours(data):
    """Native compact M/L/C grammar; hint records are skipped, not applied."""
    offset=0;x=y=0;origin=(0,0);active=False;operations=[]
    def byte():
        nonlocal offset
        if offset>=len(data):raise HftError('Truncated contour program')
        value=data[offset];offset+=1;return value
    def number():
        first=byte();value=first if first<128 else first-256
        if value==-128:
            low=byte();high=byte();v=low|(high<<8);return v if v<32768 else v-65536
        if value>=124:return value*256+byte()-0x7b84
        if value<=-124:return value*256-byte()+0x7b84
        return value
    for _ in range(20000):
        op=byte()
        if op in (0,4):
            if active:operations.append(('Z',));active=False;x,y=origin
            if op==0:return tuple(operations)
        elif op in (1,2,3,5,6,7):
            dx=number() if op&1 else 0;dy=number() if op&2 else 0
            x+=dx;y+=dy
            if op<4:
                if active:operations.append(('Z',))
                operations.append(('M',x,y));origin=(x,y);active=False
            else:operations.append(('L',x,y));active=True
        elif op in (9,10,11):
            dx=number() if op&1 else 0;dy=number() if op&2 else 0
            c1=(x+dx,y+dy);c2=(c1[0]+number(),c1[1]+number())
            dx=number() if op&2 else 0;dy=number() if op&1 else 0
            x=c2[0]+dx;y=c2[1]+dy;operations.append(('C',*c1,*c2,x,y));active=True
        elif op==32:
            count=byte()
            for _ in range(count):byte();byte()
            byte();other=byte()
            for _ in range(other):byte();byte()
        elif op==33:
            count=byte()
            for _ in range(count):
                length=byte()
                for _ in range(length):byte()
        elif op in (34,35):byte()
        elif op in (64,66):number();number()
        elif op in (65,67):
            for _ in range(6):number()
        elif op==68:pass
        else:raise HftError(f'Unsupported contour opcode {op:#x} at {offset-1}')
    raise HftError('Contour operation limit reached')
