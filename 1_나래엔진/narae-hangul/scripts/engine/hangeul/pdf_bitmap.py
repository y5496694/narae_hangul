"""Independent PDF bitmap sampling and native printer color quantization.

Windows GDI+ x86 supplies the measured interpolation rounding. Only this
adapter's own source is compiled into a private cache; Hancom is not loaded.
"""
from functools import lru_cache
from io import BytesIO
from pathlib import Path
import hashlib
import math
import os
import struct
import subprocess
import tempfile


MAX_PIXELS = 16_000_000
MAX_ENCODED_BYTES = 32 * 1024 * 1024
_MAGIC = 0x3142524e
_RGB555_RANKS = ((0,4,1,5),(6,2,7,3),(1,5,0,4),(7,3,6,2))
_RGB555_TABLES = tuple(bytes(((min(31,(value+bias)//8)<<3) |
                              (min(31,(value+bias)//8)>>2)) for value in range(256))
                       for bias in range(8))


class BitmapBackendError(ValueError):
    pass


def printer_pixel(value):
    # Source positions include half-HWPUNIT centering (1/200 pt). Convert
    # through integer units so 153.90/.12 cannot fall below its exact tie.
    units=round(abs(value)*200)
    return ((units+12)//24)*(1 if value>=0 else -1)


def image_frame(rect,page_size):
    """Native image matrix and independently rounded clipping edges, in pt."""
    f32=lambda value:struct.unpack('<f',struct.pack('<f',value))[0]
    pw,ph=page_size;paper_height=math.floor(ph)
    sx=f32(math.floor(pw)/max(1,printer_pixel(pw)))
    sy=f32(paper_height/max(1,printer_pixel(ph)))
    x,y,width,height=rect
    qx,qy,qw,qh=map(printer_pixel,rect)
    px=round(f32(qx*sx),3);w=round(f32(qw*sx),3);h=round(f32(qh*sy),3)
    bottom=round(paper_height-f32((qy+qh)*sy),3)
    left=round(f32(qx*sx),3);right=round(f32(printer_pixel(x+width)*sx),3)
    top=round(paper_height-f32(qy*sy),3)
    clip_bottom=round(paper_height-f32(printer_pixel(y+height)*sy),3)
    return {'matrix':[w,0,0,h,px,bottom],
            'rect':[px,paper_height-bottom-h,w,h],
            'clip':[left,paper_height-top,right-left,top-clip_bottom]}


def rgb555_dither(image):
    """Four-by-four ordered conversion, indexed in bottom-up bitmap rows."""
    from PIL import Image
    source=image.convert('RGB')
    width,height=source.size
    if width*height>MAX_PIXELS:raise BitmapBackendError('bitmap bounds')
    data=bytearray(source.tobytes());stride=width*3
    for y in range(height):
        row=data[y*stride:(y+1)*stride]
        ranks=_RGB555_RANKS[(height-1-y)%4]
        for x,bias in enumerate(ranks):
            for channel in range(3):
                first=x*3+channel
                row[first::12]=row[first::12].translate(_RGB555_TABLES[bias])
        if width%4==1:
            # The native converter's final unpaired RGB555 word uses the
            # third column, including widths 65/129/193/257. The other odd
            # widths already end in that column.
            first=(width-1)*3
            row[first:first+3]=data[y*stride+first:y*stride+first+3].translate(_RGB555_TABLES[ranks[2]])
        data[y*stride:(y+1)*stride]=row
    return Image.frombytes('RGB',source.size,bytes(data))


@lru_cache(maxsize=1)
def _windows_worker():
    if os.name!='nt':raise BitmapBackendError('Windows x86 GDI+ is unavailable')
    source=Path(__file__).with_name('windows_bitmap.cs')
    compiler=Path(os.environ.get('WINDIR','C:/Windows'))/'Microsoft.NET/Framework/v4.0.30319/csc.exe'
    if not compiler.is_file():raise BitmapBackendError('Windows .NET x86 compiler is unavailable')
    fingerprint=hashlib.sha256(source.read_bytes()).hexdigest()
    base=Path(os.environ.get('LOCALAPPDATA',tempfile.gettempdir()))/'NaraeHangul/bitmap-cache'
    base.mkdir(parents=True,exist_ok=True)
    target=base/('bitmap-x86-'+fingerprint+'.exe')
    manifest=target.with_suffix('.sha256')
    if target.is_file() and manifest.is_file():
        if hashlib.sha256(target.read_bytes()).hexdigest()==manifest.read_text(encoding='ascii').strip():return target
    # A unique build directory avoids partial executables when independent
    # viewer processes start together. Nothing is compiled in the repository.
    with tempfile.TemporaryDirectory(prefix='building-',dir=base) as directory:
        stage=Path(directory)/'bitmap.exe'
        result=subprocess.run([str(compiler),'/nologo','/target:exe','/platform:x86','/optimize+',
                               '/reference:System.Drawing.dll','/out:'+str(stage),str(source)],
                              capture_output=True,timeout=30,
                              creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if result.returncode or not stage.is_file():raise BitmapBackendError('Windows bitmap adapter compilation failed')
        data=stage.read_bytes();digest=hashlib.sha256(data).hexdigest()
        try:os.replace(stage,target)
        except PermissionError:
            # Another viewer can finish the same build and start the PE32
            # executable before this process installs it. Windows locks an
            # executable in use; reuse the already validated cache instead.
            if target.is_file() and manifest.is_file() and hashlib.sha256(target.read_bytes()).hexdigest()==manifest.read_text(encoding='ascii').strip():
                return target
            raise BitmapBackendError('Windows bitmap cache installation failed')
        checksum=Path(directory)/'checksum';checksum.write_text(digest,encoding='ascii');os.replace(checksum,manifest)
    return target


def scale_rgb(encoded,width,height):
    """GDI+ x86 resampling and JPEG/GIF format round trip, in bounded pipes."""
    from PIL import Image
    width,height=int(width),int(height)
    if not 0<len(encoded)<=MAX_ENCODED_BYTES or min(width,height)<1 or width*height>MAX_PIXELS:
        raise BitmapBackendError('bitmap bounds')
    packet=struct.pack('<4i',_MAGIC,len(encoded),width,height)+encoded
    try:
        result=subprocess.run([str(_windows_worker())],input=packet,capture_output=True,timeout=20,
                              creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    except (OSError,subprocess.SubprocessError) as error:
        raise BitmapBackendError('Windows bitmap adapter failed') from error
    if len(result.stdout)<24:raise BitmapBackendError('incomplete bitmap response')
    magic,status,source_width,source_height,pixel_format,length=struct.unpack('<6i',result.stdout[:24])
    if magic!=_MAGIC or length<0 or len(result.stdout)!=24+length:
        raise BitmapBackendError('invalid bitmap response')
    if status or result.returncode:raise BitmapBackendError('Windows bitmap decoding failed')
    if length!=width*height*3 or min(source_width,source_height)<1 or source_width*source_height>MAX_PIXELS:
        raise BitmapBackendError('invalid bitmap dimensions')
    return Image.frombytes('RGB',(width,height),result.stdout[24:])


def jpeg_quality(width_pt,bitmap_width,page_width_pt):
    """Auto quality uses the 600dpi device width and EMF paper conversion."""
    paper_pixels=max(1,printer_pixel(page_width_pt))
    draw_pixels=max(1,printer_pixel(abs(width_pt)))
    # Native EMF coefficients and image transforms are single precision.
    f32=lambda value:struct.unpack('<f',struct.pack('<f',value))[0]
    points=f32(f32(draw_pixels)*f32(math.floor(page_width_pt)/paper_pixels))
    return max(35,min(75,int(f32(f32(points/bitmap_width)*100))))


def prepare_bitmap(encoded,width_pt,height_pt,page_width_pt,quality_cache=None):
    """Opaque picture path; return pixels and explicit processing facts.

    Source alpha and picture effects have additional native paths. The caller
    retains those paths and reports their limits.
    """
    from PIL import Image
    if len(encoded)>MAX_ENCODED_BYTES:raise BitmapBackendError('encoded bitmap bounds')
    with Image.open(BytesIO(encoded)) as original:
        source_format=original.format
        source_mode=original.mode
        if source_format not in ('BMP','PNG','JPEG','GIF') or original.mode not in ('RGB','RGBA','P','L'):
            raise BitmapBackendError('unsupported source format or bitmap mode')
        if original.width*original.height>MAX_PIXELS:raise BitmapBackendError('source bitmap bounds')
        if original.convert('RGBA').getchannel('A').getextrema()!=(255,255):
            raise BitmapBackendError('source bitmap alpha')
        source_size=original.size
    dpi=500 if source_format=='JPEG' else 96
    width=min(source_size[0],max(1,math.floor(abs(width_pt)*dpi/72+.5)))
    height=min(source_size[1],max(1,math.floor(abs(height_pt)*dpi/72+.5)))
    image=scale_rgb(encoded,width,height)
    grayscale=source_mode=='L'
    if source_format!='GIF' and not grayscale:image=rgb555_dither(image)
    pixel_key=(width,height,hashlib.sha256(image.tobytes()).digest())
    colors=image.getcolors(1025)
    count=len(colors) if colors is not None else 1026
    quality=None
    if width*height*3>=4000 and count>=32:
        quality=jpeg_quality(width_pt,width,page_width_pt)
        if count>1024 and quality>70:quality=int(quality*.7)
    reused=quality_cache is not None and pixel_key in quality_cache
    if reused:quality=quality_cache[pixel_key]
    elif quality_cache is not None:quality_cache[pixel_key]=quality
    if quality is not None:
        stream=BytesIO();image.save(stream,format='JPEG',quality=quality,subsampling=2,
                                    optimize=False,progressive=False)
        # Qt must store these decoded pixels losslessly; re-encoding them
        # through QPrinter's own JPEG defaults adds another lossy generation.
        with Image.open(BytesIO(stream.getvalue())) as jpeg:image=jpeg.convert('RGB')
    return image,{'source_format':source_format,'source_mode':source_mode,'source_size':list(source_size),
                  'bitmap_size':[width,height],'sampling':'Windows GDI+ x86 bilinear','bitmap_dpi':dpi,
                  'intermediate_encoding':('original GIF palette' if source_format=='GIF' and (width,height)==source_size
                                           else 'Windows GDI+ '+source_format if source_format in ('JPEG','GIF') else 'lossless'),
                  'color_quantization':'8-bit grayscale' if grayscale else 'GDI+ GIF palette' if source_format=='GIF' else 'RGB555 bottom-up 4x4 ordered dither',
                  'color_count':count if colors is not None else '>1025',
                  'jpeg_quality':quality,'jpeg_quality_reused':reused,
                  'jpeg_decoded_pixels_stored_losslessly':quality is not None,
                  'whole_page_parity_verified':False}
