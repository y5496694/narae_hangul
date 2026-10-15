"""Native-created property order and visible picture-tone regressions."""
from io import BytesIO
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "1_나래엔진/narae-hangul/scripts/engine"))
from PIL import Image, ImageDraw
from PySide6.QtGui import QColor, QImage
from hangeul.formats import Paragraph, ParaStyle, Run, load_document, save_hwp, save_hwpx
from hangeul.geometry import DocumentModel, PictureGeometry
from hangeul.picture_effects import apply_tone, channel_table, pdf_alpha_mask
from hangeul.viewer import application, Document, compare


def fixture(settings):
    colors = [((i * 73) % 256, (i * 151) % 256, (i * 199) % 256) for i in range(256)]
    bitmap = Image.new("RGB", (128, 128))
    draw = ImageDraw.Draw(bitmap)
    for i, color in enumerate(colors):
        x, y = i % 16 * 8, i // 16 * 8
        draw.rectangle((x, y, x + 7, y + 7), fill=color)
    stream = BytesIO()
    bitmap.save(stream, format="PNG")
    blocks = []
    for values in settings:
        geometry = PictureGeometry(original_width_pt=96, original_height_pt=96,
            image_width_pt=96, image_height_pt=96, crop=(0, 0, 96, 96), **values)
        blocks.append(Paragraph([Run(image=stream.getvalue(), width=128, height=128, picture=geometry)],
                      style=ParaStyle(page_break=bool(blocks))))
    return DocumentModel(blocks)


def pictures(model):
    return [run.picture for block in model.blocks if isinstance(block, Paragraph)
            for run in block.runs if run.image]


class PictureEffectsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        application()

    def test_native_gray_values_and_source_alpha_are_preserved(self):
        values = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (80, 120, 160),
                  (127, 127, 127), (128, 128, 128), (255, 255, 255), (0, 0, 0)]
        image = QImage(8, 1, QImage.Format.Format_RGBA8888)
        for x, rgb in enumerate(values):
            image.setPixelColor(x, 0, QColor(*rgb, 80 + x))
        before = image.copy()
        result = apply_tone(image, "GRAY_SCALE")
        self.assertEqual([76, 149, 29, 112, 127, 128, 255, 0],
                         [result.pixelColor(x, 0).red() for x in range(8)])
        self.assertEqual(list(range(80, 88)), [result.pixelColor(x, 0).alpha() for x in range(8)])
        self.assertEqual(before, image)

    def test_black_white_uses_native_boundary_and_unknown_tone_preserves_image(self):
        image = QImage(3, 1, QImage.Format.Format_RGB32)
        for x, value in enumerate((127, 128, 129)):
            image.setPixelColor(x, 0, QColor(value, value, value))
        result = apply_tone(image, "BLACK_WHITE")
        self.assertEqual(["#000000", "#000000", "#ffffff"],
                         [result.pixelColor(x, 0).name() for x in range(3)])
        self.assertEqual(image, apply_tone(image, "REAL_PIC"))
        self.assertEqual(image, apply_tone(image, "unknown"))

    def test_native_integer_adjustments_and_extreme_contrast(self):
        # Observed Hancom PNG samples, including non-round coefficients and
        # native -100 contrast's 1 rather than the usual middle-gray formula.
        values = (0, 73, 146, 255)
        for brightness, contrast, expected in (
                (25,25,[32,129,226,255]), (-25,25,[0,0,77,222]),
                (17,37,[0,97,212,255]), (-31,-47,[0,38,77,134]),
                (0,-100,[1,1,1,1]), (0,100,[0,0,255,255])):
            self.assertEqual(expected, [channel_table(brightness,contrast)[v] for v in values])

    def test_picture_alpha_subtracts_from_bitmap_alpha_and_does_not_change_input(self):
        image=QImage(4,1,QImage.Format.Format_RGBA8888)
        for x,alpha in enumerate((0,80,128,255)):
            image.setPixelColor(x,0,QColor(50,100,150,alpha))
        before=image.copy()
        result=apply_tone(image,'REAL_PIC',alpha=100)
        self.assertEqual([0,0,28,155],[result.pixelColor(x,0).alpha() for x in range(4)])
        self.assertEqual(before,image)

    def test_independent_pdf_contains_ordered_alpha_stencils_and_endpoint_behaviour(self):
        from pypdf import PdfReader
        values=(4,10,50,128,200,252,1)
        model=fixture([dict(alpha=a) for a in values])
        for paragraph in model.blocks:paragraph.runs[0].width=paragraph.runs[0].height=8
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'stencils.hwpx';save_hwpx(model,path)
            with Document(path) as document:
                reader=PdfReader(document.pdf_path);sequence=0
                for page,alpha in zip(reader.pages,values):
                    images=[ref.get_object() for ref in page['/Resources'].get('/XObject',{}).values()
                            if ref.get_object().get('/Subtype')=='/Image']
                    stencils=[im['/Mask'].get_object() for im in images if im.get('/Mask') and im['/Width']==8]
                    if 4<=alpha<252:
                        self.assertEqual(1,len(stencils))
                        data=stencils[0].get_data();self.assertEqual(8,len(data))
                        self.assertEqual([1,0],list(stencils[0]['/Decode']))
                        packed=bytes(value^255 for value in data)
                        self.assertEqual(pdf_alpha_mask(alpha,sequence),packed)
                        sequence+=1
                    else:
                        self.assertEqual([],stencils)
                        self.assertEqual(alpha<4,bool(images))

    def test_opaque_rgba_uses_same_pdf_stencil_and_partial_source_alpha_is_diagnosed(self):
        from copy import deepcopy
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);model=fixture([dict(alpha=50)])
            save_hwpx(model,root/'rgb.hwpx')
            rgba=deepcopy(model);stream=BytesIO()
            Image.open(BytesIO(model.blocks[0].runs[0].image)).convert('RGBA').save(stream,format='PNG')
            rgba.blocks[0].runs[0].image=stream.getvalue();save_hwpx(rgba,root/'rgba.hwpx')
            with Document(root/'rgb.hwpx') as first,Document(root/'rgba.hwpx') as second:
                self.assertTrue(compare(first,second)['match'])
            bitmap=Image.open(BytesIO(stream.getvalue()));bitmap.putalpha(128);stream=BytesIO();bitmap.save(stream,format='PNG')
            rgba.blocks[0].runs[0].image=stream.getvalue();rgba.blocks[0].runs[0].picture.alpha=0
            save_hwpx(rgba,root/'partial.hwpx')
            with Document(root/'partial.hwpx') as document:
                self.assertEqual(['alpha:source-bitmap-alpha'],document.report()['layout']['image_limitations'][0]['unsupported'])

    def test_adjusted_hwp_and_hwpx_have_identical_pixels(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);model=fixture([dict(brightness=25,contrast=25),
                dict(alpha=128),dict(effect='BLACK_WHITE',brightness=50)])
            save_hwp(model,root/'a.hwp');save_hwpx(model,root/'b.hwpx')
            with Document(root/'a.hwp') as first,Document(root/'b.hwpx') as second:
                self.assertTrue(compare(first,second)['match'])

    @unittest.skipUnless(os.environ.get("NARAE_RUN_HANCOM_TESTS") == "1", "requires installed Hancom")
    def test_adjustments_match_native_png_in_both_formats(self):
        from hangeul.hwp_automation_bridge import HancomBridge
        settings=[dict(brightness=b,contrast=c) for b,c in ((0,0),(0,-100),(0,-75),(0,-25),
            (0,-10),(0,10),(0,25),(0,75),(0,100),(25,25),(-25,25),(25,-25),(-25,-25),
            (17,37),(-31,-47),(25,0),(-25,0))]
        settings += [dict(alpha=a) for a in (1,10,50,100,128,200,255)]
        settings += [dict(effect=e,brightness=b,contrast=c) for e,b,c in (
            ('GRAY_SCALE',25,25),('GRAY_SCALE',-31,-47),('BLACK_WHITE',50,0),
            ('BLACK_WHITE',25,25),('BLACK_WHITE',-25,-25))]
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            for suffix,save in (('hwp',save_hwp),('hwpx',save_hwpx)):
                source=root/('input.'+suffix);save(fixture(settings),source)
                before=hashlib.sha256(source.read_bytes()).digest()
                with HancomBridge() as bridge:
                    bridge.open_doc(source)
                    action=bridge.hwp.CreateAction('PrintToImage')
                    params=action.CreateSet();action.GetDefault(params)
                    for key,value in dict(Format=3,FileName=str(root/(suffix+'.png')),
                                          ColorDepth=24,Resolution=96,Range=6).items():
                        params.SetItem(key,value)
                    self.assertTrue(action.Execute(params))
                # PNG tone oracle checks SourceLayout's raster path. Native
                # PDF transparency intentionally uses an ordered stencil.
                from hangeul.source_layout import SourceLayout
                own=SourceLayout(load_document(source).model)
                self.assertEqual(len(settings),len(own.pages))
                for page in range(len(settings)):
                    with Image.open(root/(suffix+'%03d.png'%(page+1))) as png:
                        native=png.convert('RGB')
                    actual=own.render_page(page,96)
                    for i in range(256):
                        x=round((56.69+(i%16+.5)*6)*96/72)
                        y=round((56.69+(i//16+.5)*6)*96/72)
                        self.assertEqual(native.getpixel((x,y)),actual.pixelColor(x,y).getRgb()[:3],
                                         f'{suffix} page {page+1} patch {i}')
                self.assertEqual(before,hashlib.sha256(source.read_bytes()).digest())

    @unittest.skipUnless(os.environ.get('NARAE_RUN_HANCOM_TESTS')=='1','requires installed Hancom')
    def test_pdf_alpha_masks_all_byte_values_and_repeated_brushes(self):
        from pypdf import PdfReader
        from pypdf.generic import ContentStream
        # Small images exercise all masks cheaply; a repeated sequence checks
        # state across pages instead of checking only independent first draws.
        settings=[dict(alpha=a) for a in range(256)]
        repeats=[10,10,10,10,50,10,50,10,128,128,128,128]
        settings += [dict(alpha=a) for a in repeats]
        model=fixture(settings)
        for paragraph in model.blocks:
            run=paragraph.runs[0];run.width=run.height=8
            run.picture.original_width_pt=run.picture.original_height_pt=6
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            for ext,save in (('hwp',save_hwp),('hwpx',save_hwpx)):
                path=root/('alpha.'+ext);save(model,path)
                with Document(path,'hancom') as native:
                    reader=PdfReader(native.pdf_path);self.assertEqual(len(settings),len(reader.pages))
                    sequence=None
                    for page,values in zip(reader.pages,settings):
                        alpha=values['alpha'];masks=[];images=[]
                        objects=page['/Resources'].get('/XObject',{})
                        used={vals[0] for vals,op in ContentStream(page.get_contents(),reader).operations if op==b'Do'}
                        for name in used:
                            obj=objects[name].get_object()
                            if obj.get('/Subtype')!='/Image':continue
                            (masks if obj.get('/ImageMask') else images).append(obj)
                        if 4<=alpha<252:
                            self.assertEqual(1,len(masks),(ext,alpha))
                            self.assertEqual((8,8),(masks[0]['/Width'],masks[0]['/Height']))
                            if sequence is None:
                                # The native printer's brush state survives
                                # document exports in a reused Hwp process.
                                sequence=next(n for n in range(64) if pdf_alpha_mask(alpha,n)==masks[0].get_data())
                            self.assertEqual(pdf_alpha_mask(alpha,sequence),masks[0].get_data(),(ext,alpha,sequence))
                            sequence+=1
                        else:
                            self.assertEqual([],masks,(ext,alpha))
                            self.assertEqual(alpha<4,bool(images),(ext,alpha))

    def test_tone_only_has_no_unsupported_effect_and_both_formats_match(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            model = fixture([{"effect": "GRAY_SCALE"}, {"effect": "BLACK_WHITE"}])
            a, b = root / "tone.hwp", root / "tone.hwpx"
            save_hwp(model, a)
            save_hwpx(model, b)
            with Document(a) as first, Document(b) as second:
                self.assertTrue(compare(first, second)["match"])
                self.assertEqual([], first.report()["layout"]["image_limitations"])
                self.assertEqual([], second.report()["layout"]["image_limitations"])

    def test_unsupported_pattern_is_preserved_and_not_misread_as_black_white(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "pattern.hwp"
            save_hwp(fixture([{"effect": "PATTERN8x8"}]), path)
            self.assertEqual("PATTERN8x8", pictures(load_document(path).model)[0].effect)
            with Document(path) as document:
                entries = document.report()["layout"]["image_limitations"]
                self.assertEqual(["effect:PATTERN8x8"], entries[0]["unsupported"])

    @unittest.skipUnless(os.environ.get("NARAE_RUN_HANCOM_TESTS") == "1", "requires installed Hancom")
    def test_native_saved_brightness_and_contrast_keep_their_meaning(self):
        from hangeul.hwp_automation_bridge import HancomBridge
        settings = [{"brightness": 50}, {"contrast": 50}, {"brightness": -50}, {"contrast": -50},
                    {"brightness": 25, "contrast": -25}]
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            model = fixture(settings)
            hwpx, hwp, native_hwp = root / "source.hwpx", root / "ours.hwp", root / "native.hwp"
            save_hwpx(model, hwpx)
            save_hwp(model, hwp)
            with HancomBridge() as bridge:
                bridge.open_doc(hwpx)
                bridge.save_doc(native_hwp)
            for path in (hwpx, hwp, native_hwp):
                actual = pictures(load_document(path).model)
                self.assertEqual([(value.get("brightness", 0), value.get("contrast", 0)) for value in settings],
                                 [(p.brightness, p.contrast) for p in actual], str(path))
            # Native execution of our HWP catches a writer/reader pair that
            # consistently swaps the same fields on an internal round trip.
            with Document(hwpx, "hancom") as reference, Document(hwp, "hancom") as actual:
                self.assertTrue(compare(actual, reference)["match"])

    @unittest.skipUnless(os.environ.get("NARAE_RUN_HANCOM_TESTS") == "1", "requires installed Hancom")
    def test_exported_tones_match_native_for_256_color_patches_in_both_formats(self):
        model = fixture([{"effect": "GRAY_SCALE"}, {"effect": "BLACK_WHITE"}])
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for suffix, save in (("hwp", save_hwp), ("hwpx", save_hwpx)):
                source = root / ("tone." + suffix)
                save(model, source)
                with Document(source) as own, Document(source, "hancom") as native:
                    self.assertEqual(2, native.count)
                    self.assertEqual(2, own.count)
                    for page in range(2):
                        actual, reference = own.render(page, 144), native.render(page, 144)
                        for i in range(256):
                            x, y = round((56.69 + (i % 16 + .5) * 6) * 2), round((56.69 + (i // 16 + .5) * 6) * 2)
                            self.assertEqual(reference.pixelColor(x, y), actual.pixelColor(x, y),
                                             f"{suffix} page {page + 1} patch {i}")


if __name__ == "__main__":
    unittest.main()
