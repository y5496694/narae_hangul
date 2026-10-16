"""Transparent GIF printer oracles; generated documents stay in temp folders."""
from io import BytesIO
from pathlib import Path
import hashlib,os,sys,tempfile,unittest,zlib

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'1_나래엔진/narae-hangul/scripts/engine'))
from PIL import Image
from hangeul.formats import Paragraph,ParaStyle,Run,TextStyle,save_hwp,save_hwpx
from hangeul.geometry import DocumentModel,PictureGeometry
from hangeul.pdf_bitmap import prepare_alpha_gif,BitmapBackendError
from hangeul.viewer import application,Document,compare,export


def gif_cases():
    cases=[]
    specs=[('small',64,48,16,12,255,(21,87,149)),
           ('reuse',64,48,256,192,255,(21,87,149)),
           ('big',257,193,48,36,255,(255,255,255)),
           ('flat',64,48,96,72,2,(0,0,0)),
           ('black',64,48,96,72,255,(0,0,0)),
           ('animated',48,32,64,48,255,(255,255,255))]
    for label,w,h,dw,dh,colors,hidden in specs:
        rgb=Image.frombytes('RGB',(w,h),bytes(v for y in range(h) for x in range(w)
            for v in ((x*13+y*7)%256,(x*3+y*17)%256,(x*11+y*19)%256)))
        quantized=rgb.quantize(colors=colors)
        palette=list(hidden)+quantized.getpalette()[:colors*3]
        palette+=[0]*(768-len(palette))
        image=Image.new('P',(w,h));image.putpalette(palette)
        source=quantized.tobytes()
        image.frombytes(bytes(0 if (x//5+y//7)%3==0 else source[y*w+x]+1
            for y in range(h) for x in range(w)))
        output=BytesIO();extra={}
        if label=='animated':
            extra=dict(save_all=True,append_images=[image.transpose(Image.Transpose.FLIP_LEFT_RIGHT)],
                       duration=100,loop=0,disposal=2)
        image.save(output,format='GIF',transparency=0,optimize=False,**extra)
        cases.append(dict(label=label,encoded=output.getvalue(),display=(dw,dh)))
    for count,w,h,dw,dh in ((255,17,17,64,64),(256,17,17,64,64),(256,64,48,384,288)):
        image=Image.new('P',(w,h));palette=[255,255,255]
        for i in range(1,256):palette.extend(((i*37)%256,(i*71)%256,(i*113)%256))
        image.putpalette(palette);image.frombytes(bytes(i%count for i in range(w*h)))
        output=BytesIO();image.save(output,format='GIF',transparency=0,optimize=False)
        cases.append(dict(label=f'colors-{count}-{w}',encoded=output.getvalue(),display=(dw,dh)))
    return cases


def fixture(cases):
    blocks=[]
    for case in cases:
        encoded=case['encoded']
        with Image.open(BytesIO(encoded)) as image:w,h=image.size
        dw,dh=case['display'];style=case.get('style',TextStyle())
        picture=PictureGeometry(original_width_pt=w*.75,original_height_pt=h*.75,
            image_width_pt=w*.75,image_height_pt=h*.75,crop=(0,0,w*.75,h*.75))
        blocks.append(Paragraph([Run(image=encoded,image_ext='gif',width=dw,height=dh,
            picture=picture,style=style)],default_style=style,end_style=style,
            style=ParaStyle(page_break=bool(blocks))))
    return DocumentModel(blocks)


class SourceAlphaTests(unittest.TestCase):
    def test_mask_matte_animation_and_per_placement_quality(self):
        cases=gif_cases();qualities=[]
        for case in cases:
            payload,facts=prepare_alpha_gif(case['encoded'],case['display'][0]*.75,595.28)
            with Image.open(BytesIO(case['encoded'])) as original:rgba=original.convert('RGBA')
            self.assertEqual(rgba.size,(payload['width'],payload['height']))
            mask=Image.frombytes('1',rgba.size,zlib.decompress(payload['mask'])).convert('L')
            self.assertEqual(rgba.getchannel('A').point(lambda v:255-v).tobytes(),mask.tobytes())
            self.assertFalse(facts['jpeg_quality_reused'])
            self.assertEqual(0,facts['source_frame_index'])
            qualities.append(facts['jpeg_quality'])
            if case['label'].startswith('colors-255'):
                self.assertEqual('FlateDecode',payload['filter'])
                rgb=Image.frombytes('RGB',rgba.size,zlib.decompress(payload['color']))
                source=rgba.convert('RGB').tobytes();actual=rgb.tobytes()
                for i,a in enumerate(rgba.getchannel('A').tobytes()):
                    self.assertEqual(source[i*3:i*3+3] if a else b'\xff'*3,actual[i*3:i*3+3])
        self.assertEqual((35,75),tuple(qualities[:2]))

    def test_transparent_empty_and_unsupported_format(self):
        stream=BytesIO();Image.new('P',(4,3)).save(stream,format='GIF',transparency=0)
        payload,facts=prepare_alpha_gif(stream.getvalue(),12,595.28)
        self.assertIsNone(payload);self.assertTrue(facts['source_alpha_omitted'])
        stream=BytesIO();Image.new('RGBA',(4,3),(0,0,0,128)).save(stream,format='PNG')
        with self.assertRaisesRegex(BitmapBackendError,'another native path'):
            prepare_alpha_gif(stream.getvalue(),12,595.28)
        with self.assertRaisesRegex(BitmapBackendError,'bounds'):prepare_alpha_gif(b'',12,595.28)

    def test_independent_pdf_contains_hard_masks_without_tokens(self):
        from pypdf import PdfReader
        application()
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'source.hwpx';save_hwpx(fixture(gif_cases()),source)
            with Document(source) as own:
                export(own,pdf=Path(folder)/'output.pdf')
                self.assertFalse(own.details['image_limitations'])
                self.assertTrue(all(r['status']=='prepared' for r in own.details['pdf_bitmaps']))
            pdf=PdfReader(Path(folder)/'output.pdf')
            for page in pdf.pages:
                images=[r.get_object() for r in page['/Resources']['/XObject'].values()]
                masked=[o for o in images if '/Mask' in o]
                self.assertEqual(1,len(masked))
                self.assertEqual(1,masked[0]['/Mask']['/BitsPerComponent'])
                self.assertTrue(masked[0]['/Mask']['/ImageMask'])
                self.assertFalse(any((o.get('/Width'),o.get('/Height'))==(4,1) for o in images))

    def native_check(self,cases):
        application()
        with tempfile.TemporaryDirectory() as folder:
            for ext,save in (('hwp',save_hwp),('hwpx',save_hwpx)):
                source=Path(folder)/('source.'+ext);save(fixture(cases),source)
                sha=hashlib.sha256(source.read_bytes()).hexdigest()
                with Document(source) as own,Document(source,'hancom') as native:
                    self.assertEqual(len(cases),own.count);self.assertEqual(len(cases),native.count)
                    result=compare(own,native)
                    for case,page in zip(cases,result['pages']):
                        self.assertTrue(page['match'],(ext,case['label'],page))
                    self.assertTrue(result['match'])
                self.assertEqual(sha,hashlib.sha256(source.read_bytes()).hexdigest())

    @unittest.skipUnless(os.environ.get('NARAE_RUN_HANCOM_TESTS')=='1','requires installed Hancom')
    def test_transparent_gif_complete_pages_in_both_formats(self):
        self.native_check(gif_cases())

    @unittest.skipUnless(os.environ.get('NARAE_RUN_HANCOM_TESTS')=='1','requires installed Hancom/fonts')
    def test_small_inline_picture_baselines_in_both_formats(self):
        encoded=gif_cases()[0]['encoded'];cases=[]
        for family in ('맑은 고딕','함초롬바탕'):
            for size in (7,11,18):
                for height in (3,6,9,12):
                    cases.append(dict(label=f'{family}/{size}/{height}',encoded=encoded,
                        display=(height*4/3,height*4/3),style=TextStyle(font=family,size=size)))
        self.native_check(cases)


if __name__=='__main__':unittest.main()
