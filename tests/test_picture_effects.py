"""Native-created property order and visible picture-tone regressions."""
from io import BytesIO
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
from hangeul.picture_effects import apply_tone
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
