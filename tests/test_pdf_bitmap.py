"""Source pixels and independent PDF payloads, without reference realignment."""
from io import BytesIO
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'1_나래엔진/narae-hangul/scripts/engine'))
from PIL import Image
from hangeul.pdf_bitmap import rgb555_dither,scale_rgb,BitmapBackendError,image_frame,printer_pixel


def pattern(seed=0):
    return Image.frombytes('RGB',(257,193),bytes(v for y in range(193) for x in range(257)
        for v in ((x*13+y*7+seed)%256,(x*3+y*17+seed*3)%256,(x*11+y*19+seed*5)%256)))


class PdfBitmapTests(unittest.TestCase):
    def test_ordered_rgb555_uses_bottom_up_rows_and_preserves_input(self):
        image=Image.new('RGB',(4,4),(100,100,100));before=image.tobytes()
        result=rgb555_dither(image)
        self.assertEqual([[107,99,107,99],[99,107,99,107],[107,99,107,99],[99,107,99,107]],
                         [[result.getpixel((x,y))[0] for x in range(4)] for y in range(4)])
        self.assertEqual(before,image.tobytes())
        for value in (0,255):
            image=Image.new('RGB',(5,3),(value,)*3)
            self.assertEqual(image.tobytes(),rgb555_dither(image).tobytes())
        odd=rgb555_dither(Image.new('RGB',(5,4),(7,7,7)))
        self.assertEqual([8,0,8,8],[odd.getpixel((4,y))[0] for y in range(4)])

    def test_scale_bounds_are_checked_before_worker_execution(self):
        for data,width,height in ((b'',1,1),(b'x',0,1),(b'x',4001,4001)):
            with self.assertRaises(BitmapBackendError):scale_rgb(data,width,height)

    def test_printer_frame_rounds_size_and_clipping_edges_independently(self):
        self.assertEqual(1283,printer_pixel(153.89999999999998))
        frame=image_frame((61.78,104.855,227.93,142.62),(595.28,841.89))
        self.assertEqual([227.758,0,0,142.524,61.767,593.711],frame['matrix'])
        self.assertAlmostEqual(104.765,frame['clip'][1])
        self.assertAlmostEqual(142.405,frame['clip'][3])
        self.assertLess(frame['clip'][3],frame['rect'][3])

    @unittest.skipUnless(os.name=='nt','requires Windows x86 GDI+')
    def test_x86_sampling_and_quantization_match_captured_synthetic_oracle(self):
        stream=BytesIO();pattern().save(stream,format='BMP')
        image=scale_rgb(stream.getvalue(),64,48)
        # Measured from the native 32-bit GDI+ call and pre-JPEG bitmap,
        # respectively. x64 GDI+ differs even with the same source/settings.
        self.assertEqual('aeee05ef07a36fac9c3ed1c19e9c347759b02a74704258d8a741ce6b5861c0e7',
                         hashlib.sha256(image.tobytes()).hexdigest())
        self.assertEqual('6005448007302ae3e8ec7545ab06d21ae4cdf3cd31b9f9cb07b13eea19fdce93',
                         hashlib.sha256(rgb555_dither(image).tobytes()).hexdigest())

    @unittest.skipUnless(os.environ.get('NARAE_RUN_HANCOM_TESTS')=='1','requires installed Hancom')
    def test_bmp_and_opaque_png_pdf_bitmap_pixels_equal_native_in_both_formats(self):
        from pypdf import PdfReader
        from hangeul.formats import Paragraph,ParaStyle,Run,save_hwp,save_hwpx
        from hangeul.geometry import DocumentModel,PictureGeometry
        from hangeul.viewer import application,Document,compare
        application();cases=[];blocks=[]
        for kind in ('BMP','PNG','RGBA'):
            for width,height in ((64,48),(96,72)):
                stream=BytesIO();image=pattern(len(blocks)*7)
                if kind=='RGBA':image=image.convert('RGBA')
                image.save(stream,format='PNG' if kind=='RGBA' else kind)
                picture=PictureGeometry(original_width_pt=192.75,original_height_pt=144.75,
                    image_width_pt=192.75,image_height_pt=144.75,crop=(0,0,192.75,144.75))
                run=Run(image=stream.getvalue(),image_ext='bmp' if kind=='BMP' else 'png',
                        width=width,height=height,picture=picture)
                blocks.append(Paragraph([run],style=ParaStyle(page_break=bool(blocks))))
                cases.append((kind,width,height))
        with tempfile.TemporaryDirectory() as folder:
            for ext,save in (('hwp',save_hwp),('hwpx',save_hwpx)):
                path=Path(folder)/('bitmap.'+ext);save(DocumentModel(blocks),path)
                with Document(path) as own,Document(path,'hancom') as native:
                    self.assertEqual(len(cases),native.count);self.assertEqual(native.count,own.count)
                    actual,reference=PdfReader(own.pdf_path),PdfReader(native.pdf_path)
                    records=own.details['pdf_bitmaps']
                    self.assertEqual(len(cases),sum(p['status']=='prepared' for p in records))
                    for page,case in enumerate(cases):
                        objects=lambda reader:[ref.get_object() for ref in reader.pages[page]['/Resources']['/XObject'].values()
                                               if ref.get_object().get('/Subtype')=='/Image']
                        ours=objects(actual);theirs=objects(reference)
                        self.assertEqual(1,len(ours),(ext,case));self.assertEqual(1,len(theirs),(ext,case))
                        a,b=ours[0],theirs[0]
                        self.assertEqual(case[1:],(a['/Width'],a['/Height']))
                        self.assertEqual(case[1:],(b['/Width'],b['/Height']))
                        # page.images can re-encode JPEG with its own defaults.
                        # Read the native DCT stream directly for this oracle.
                        with Image.open(BytesIO(b.get_data())) as image:native_rgb=image.convert('RGB').tobytes()
                        self.assertEqual(native_rgb,a.get_data(),(ext,case))
                    self.assertTrue(compare(own,native)['match'],ext)

    @unittest.skipUnless(os.environ.get('NARAE_RUN_HANCOM_TESTS')=='1','requires installed Hancom')
    def test_jpeg_gif_indexed_and_odd_width_whole_pages_equal_native(self):
        from pypdf import PdfReader
        from hangeul.formats import Paragraph,ParaStyle,Run,save_hwp,save_hwpx
        from hangeul.geometry import DocumentModel,PictureGeometry
        from hangeul.viewer import application,Document,compare
        application();cases=[];blocks=[]
        source=pattern()
        for kind,mode,sizes in (
            ('JPEG','RGB',((40,30),(64,48),(96,72),(257,193))),
            ('GIF','P',((64,48),(96,72),(257,193))),
            ('BMP','RGB',tuple((w,48) for w in (63,65,67,129,257))),
            ('PNG','P',((64,48),)),('BMP','P',((64,48),)),
            ('PNG','L',((64,48),)),('JPEG','L',((64,48),)),
        ):
            image=source.quantize(colors=128) if mode=='P' else source.convert(mode)
            stream=BytesIO();image.save(stream,format=kind)
            for width,height in sizes:
                picture=PictureGeometry(original_width_pt=192.75,original_height_pt=144.75,
                    image_width_pt=192.75,image_height_pt=144.75,crop=(0,0,192.75,144.75))
                run=Run(image=stream.getvalue(),image_ext=kind.lower(),width=width,height=height,picture=picture)
                blocks.append(Paragraph([run],style=ParaStyle(page_break=bool(blocks))))
                cases.append((kind,mode,width,height))
        with tempfile.TemporaryDirectory() as folder:
            for ext,save in (('hwp',save_hwp),('hwpx',save_hwpx)):
                path=Path(folder)/('formats.'+ext);save(DocumentModel(blocks),path)
                with Document(path) as own,Document(path,'hancom') as native:
                    self.assertEqual(len(cases),native.count);self.assertEqual(native.count,own.count)
                    records=own.details['pdf_bitmaps']
                    self.assertEqual(len(cases),sum(p['status']=='prepared' for p in records))
                    actual,reference=PdfReader(own.pdf_path),PdfReader(native.pdf_path)
                    for n,case in enumerate(cases):
                        page=reference.pages[n]
                        native_image=next(o.get_object() for o in page['/Resources']['/XObject'].values()
                                          if o.get_object().get('/Subtype')=='/Image')
                        if native_image.get('/Filter')=='/DCTDecode':
                            with Image.open(BytesIO(native_image.get_data())) as image:expected=image.convert('RGB')
                        else:expected=page.images[0].image.convert('RGB')
                        ours=next(o.get_object() for o in actual.pages[n]['/Resources']['/XObject'].values()
                                  if o.get_object().get('/Subtype')=='/Image')
                        self.assertEqual(expected.size,(ours['/Width'],ours['/Height']),(ext,case))
                        self.assertEqual(hashlib.sha256(expected.tobytes()).hexdigest(),
                                         hashlib.sha256(ours.get_data()).hexdigest(),(ext,case))
                    self.assertTrue(compare(own,native)['match'],ext)


if __name__=='__main__':unittest.main()
