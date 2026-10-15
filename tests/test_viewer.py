"""Observable read-only, export and full-document comparison regressions."""
from contextlib import redirect_stdout
from io import StringIO
import json
import os
import subprocess
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "1_나래엔진/narae-hangul/scripts/engine"))
from hangeul.automation import run_job
from hangeul.viewer import application, Document, compare, destination, export, main, pages


class ViewerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        application()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def fixture(self, name="sample.hwpx", second="둘째 쪽"):
        path = self.root / name
        run_job({"action": "create", "format": path.suffix[1:], "output": str(path),
                 "blocks": [{"type": "paragraph", "text": "첫째 쪽: 나래 뷰어"},
                            {"type": "paragraph", "text": second,
                             "paragraph_style": {"page_break": True}}]}, self.root)
        return path

    def cli(self, args):
        with redirect_stdout(StringIO()) as output:
            code = main(args)
        return code, json.loads(output.getvalue())

    def test_hwp_hwpx_same_reading_and_source_unchanged(self):
        a, b = self.fixture("a.hwp"), self.fixture("b.hwpx")
        before = a.read_bytes(), b.read_bytes()
        with Document(a) as first, Document(b) as second:
            self.assertEqual(2, first.count)
            self.assertIn("나래 뷰어", first.text(0))
            self.assertIn("둘째 쪽", first.text(1))
            self.assertTrue(compare(first, second)["match"])
        self.assertEqual(before, (a.read_bytes(), b.read_bytes()))

    def test_second_page_change_fails_strict_comparison(self):
        a, b = self.fixture("a.hwpx"), self.fixture("b.hwpx", "변경한 둘째 쪽")
        with Document(a) as first, Document(b) as second:
            result = compare(first, second)
        self.assertTrue(result["pages"][0]["match"])
        self.assertFalse(result["pages"][1]["match"])
        self.assertFalse(result["match"])

    def test_missing_page_fails_even_if_existing_page_matches(self):
        source = self.fixture()
        from pypdf import PdfWriter
        with Document(source) as document:
            writer = PdfWriter()
            writer.append(str(document.pdf_path), pages=(0, 1))
            reference = self.root / "one.pdf"
            writer.write(reference)
            with Document(reference) as other:
                result = compare(document, other)
        self.assertFalse(result["page_count_match"])
        self.assertEqual("missing_page", result["pages"][1]["reason"])

    def test_export_png_selection_and_pdf_full_document(self):
        source = self.fixture()
        with Document(source) as document:
            outputs = export(document, pdf=self.root / "out.pdf", png_dir=self.root / "png", selection="2")
        self.assertEqual(2, len(outputs))
        self.assertFalse((self.root / "png/page-0001.png").exists())
        self.assertTrue((self.root / "png/page-0002.png").is_file())
        with Document(self.root / "out.pdf") as document:
            self.assertEqual(2, document.count)

    def test_invalid_range_leaves_existing_output_untouched(self):
        source = self.fixture()
        target = self.root / "out.pdf"
        target.write_bytes(b"keep")
        with Document(source) as document:
            with self.assertRaises(ValueError):
                export(document, pdf=target, selection="3", overwrite=True)
        self.assertEqual(b"keep", target.read_bytes())

    def test_input_result_and_export_aliases_are_protected(self):
        source = self.fixture()
        before = source.read_bytes()
        code, result = self.cli(["read", str(source), "--result", str(source), "--overwrite"])
        self.assertEqual(1, code)
        self.assertFalse(result["success"])
        self.assertEqual(before, source.read_bytes())
        alias = self.root / "alias.hwpx"
        os.link(source, alias)
        with self.assertRaises(ValueError):
            destination(alias, [source], True)

    def test_failed_read_writes_failure_json_instead_of_reusing_success(self):
        result = self.root / "result.json"
        result.write_text('{"success":true}', encoding="utf-8")
        code, response = self.cli(["read", str(self.root / "missing.hwp"), "--result", str(result), "--overwrite"])
        self.assertEqual(1, code)
        self.assertFalse(response["success"])
        self.assertFalse(json.loads(result.read_text(encoding="utf-8"))["success"])

    def test_result_cannot_replace_existing_pdf_or_png_with_error_json(self):
        source = self.fixture()
        for target, options in ((self.root / "out.pdf", ["--pdf"]),
                                (self.root / "png/page-0001.png", ["--png-dir"])):
            target.parent.mkdir(exist_ok=True)
            target.write_bytes(b"preserve existing artifact")
            path = str(target if options[0] == "--pdf" else target.parent)
            code, _ = self.cli(["export", str(source), *options, path,
                                "--result", str(target), "--overwrite"])
            self.assertEqual(1, code)
            self.assertEqual(b"preserve existing artifact", target.read_bytes())

    def test_verify_exit_two_and_reference_protection(self):
        a, b = self.fixture("a.hwpx"), self.fixture("b.hwpx", "다른 쪽")
        code, result = self.cli(["verify", str(a), "--reference", str(b)])
        self.assertEqual(2, code)
        self.assertTrue(result["success"])
        self.assertFalse(result["comparison"]["match"])
        before = b.read_bytes()
        code, _ = self.cli(["verify", str(a), "--reference", str(b), "--result", str(b), "--overwrite"])
        self.assertEqual(1, code)
        self.assertEqual(before, b.read_bytes())

    def test_gui_has_read_only_text_and_no_editor_actions(self):
        from hangeul.viewer_gui import ViewerWindow
        with Document(self.fixture()) as document:
            window = ViewerWindow(document)
            self.assertTrue(window.page_text.isReadOnly())
            window.page.setValue(2)
            window.page_changed(1)
            self.assertIn("둘째 쪽", window.page_text.toPlainText())
            window.query.setText("나래 뷰어")
            window.find()
            self.assertEqual(1, window.page.value())
            window.view.setDocument(None)
            window.search.setDocument(None)
            window.close()

    def test_ranges_are_one_based_sorted_and_never_clamped(self):
        self.assertEqual([0, 2, 3], pages("3-4,1,3", 4))
        for value in ("0", "4-2", "1,", "all,2", "5"):
            with self.assertRaises(ValueError):
                pages(value, 4)

    def test_inactive_hancom_controller_reactivates_its_owned_document(self):
        from hangeul.hwp_automation_bridge import HancomBridge
        selected = []
        class Owned:
            def SetActive_XHwpDocument(self): selected.append(42)
        class Documents:
            Active_XHwpDocument = None
            def FindItem(self, identity):
                self.requested = identity
                return Owned() if identity == 42 else None
        class Hwp:
            XHwpDocuments = Documents()
        bridge = object.__new__(HancomBridge)
        bridge._hwp, bridge._document_id = Hwp(), 42
        bridge.activate_document()
        self.assertEqual([42], selected)

    def test_uncached_paragraph_does_not_turn_stale_following_y_into_page_break(self):
        from hangeul.formats import Paragraph, Run
        from hangeul.geometry import DocumentModel
        from hangeul.source_layout import SourceLayout
        def cached(text, y, index):
            return Paragraph([Run(text)], source_index=index, line_segments=[{
                "text_start": 0, "y_pt": y, "height_pt": 11, "text_height_pt": 11,
                "baseline_pt": 9, "spacing_pt": 6.6, "x_pt": 0, "width_pt": 480, "flags": 0}])
        first = cached("제목", 0, 0)
        changed = Paragraph([Run("새 문단의 내용 " * 40)], source_index=1)
        tail = cached("뒤따르는 문단", 20, 2)
        layout = SourceLayout(DocumentModel([first, changed, tail]))
        self.assertEqual(1, len(layout.pages))
        texts = [command for command in layout.pages[0] if command.kind == "text"]
        self.assertGreater(texts[-1].rect[1], texts[-2].rect[1])

    def test_uncached_blank_paragraph_reserves_a_line(self):
        from hangeul.formats import Paragraph, Run
        from hangeul.geometry import DocumentModel
        from hangeul.source_layout import SourceLayout
        a, blank, b = Paragraph([Run("first")]), Paragraph(), Paragraph([Run("last")])
        layout = SourceLayout(DocumentModel([a, blank, b]))
        lines = layout._paragraph_lines
        self.assertTrue(lines[id(blank)])
        self.assertGreater(lines[id(b)][0]["y_pt"], lines[id(blank)][0]["y_pt"])

    def test_none_fill_is_transparent_in_tables_and_rectangles(self):
        from copy import deepcopy
        from hangeul.formats import Table,Cell,Drawing,Paragraph,Run,save_hwpx,load_document
        from hangeul.geometry import DocumentModel
        from hangeul.source_layout import SourceLayout
        table=Table(1,1,[Cell(0,0,blocks=[Paragraph([Run('visible')])],width=100,
            background='none',height=30)],width=100,height=30,background='none')
        model=DocumentModel([table,Drawing(width=100,height=30,background='none')])
        clear=deepcopy(model)
        clear.blocks[0].background=clear.blocks[0].cells[0].background=clear.blocks[1].background=''
        self.assertEqual(SourceLayout(clear).render_page(0),SourceLayout(model).render_page(0))
        path=self.root/'no-fill.hwpx';save_hwpx(model,path)
        loaded=load_document(path).model
        loaded_table=next(b for b in loaded.blocks if isinstance(b,Table))
        self.assertEqual('',loaded_table.background)
        self.assertEqual('',loaded_table.cells[0].background)
        self.assertEqual('',next(b for b in loaded.blocks if isinstance(b,Drawing)).background)
        black=deepcopy(clear);black.blocks[0].cells[0].background='#000000'
        self.assertNotEqual(SourceLayout(clear).render_page(0),SourceLayout(black).render_page(0))

    @unittest.skipUnless(os.environ.get('NARAE_RUN_HANCOM_TESTS')=='1','requires installed Hancom/fonts')
    def test_uncached_declared_line_sizes_match_native_baseline_grid(self):
        import math
        from pypdf import PdfReader
        from pypdf.generic import ContentStream
        from hangeul.formats import Paragraph,ParaStyle,Run,TextStyle,save_hwp,save_hwpx,load_document
        from hangeul.geometry import DocumentModel
        from hangeul.source_layout import SourceLayout,TEXT_UNIT_PT
        cases=[(font,size) for font in ('함초롬바탕','맑은 고딕','Arial','한양신명조') for size in (13,20)]
        model=DocumentModel([Paragraph([Run('A가\nA가',TextStyle(font=font,size=size))],
            style=ParaStyle(page_break=i>0,line=150)) for i,(font,size) in enumerate(cases)])
        for ext,save in (('hwp',save_hwp),('hwpx',save_hwpx)):
            source=self.root/('line-height.'+ext);save(model,source)
            layout=SourceLayout(load_document(source).model)
            with Document(source,'hancom') as native:
                self.assertEqual(len(cases),native.count)
                reference=PdfReader(native.pdf_path)
                for page,case in enumerate(cases):
                    expected=sorted(set(float(vals[5]) for vals,op in ContentStream(reference.pages[page].get_contents(),reference).operations if op==b'Tm'))
                    # Hancom rounds the line origin and baseline offset
                    # separately in the printer grid. This checks line metrics,
                    # not our PDF's still-fractional painting coordinates.
                    actual=[math.floor(c.rect[1]/TEXT_UNIT_PT+.5)+math.floor(c.extra['line']['baseline_pt']/TEXT_UNIT_PT+.5)
                            for c in layout.pages[page] if c.kind=='text']
                    self.assertEqual(expected,actual,(ext,case))

    def test_serialized_affine_precision_keeps_drawing_pixels_identical(self):
        from hangeul.formats import Drawing
        from hangeul.geometry import DocumentModel
        from hangeul.source_layout import SourceLayout
        from copy import deepcopy
        drawing = Drawing(width=469.24, height=391.56)
        drawing.shape.original_width_pt = 469.24
        drawing.shape.original_height_pt = 536.44
        drawing.geometry["transform_chain"] = [("scaMatrix", [1, 0, 0, 0, .729923197375289, 0])]
        drawing.shape.transforms["scaMatrix"] = [1, 0, 0, 0, .729923197375289, 0]
        rounded = deepcopy(drawing)
        rounded.geometry["transform_chain"][0][1][4] = .729923
        first = SourceLayout(DocumentModel([drawing]))
        second = SourceLayout(DocumentModel([rounded]))
        self.assertEqual(first.render_page(0), second.render_page(0))

    def test_hwp_terminal_control_does_not_shift_negative_spaced_centered_text(self):
        from hangeul.formats import Paragraph, ParaStyle, Run, TextStyle
        from hangeul.geometry import DocumentModel
        from hangeul.source_layout import SourceLayout
        from copy import deepcopy
        style = TextStyle(size=12, letter_spacing=-16)
        paragraph = Paragraph([Run("연구대상 학교급", style, source_start=0)],
            style=ParaStyle(align="CENTER"), default_style=style,
            line_segments=[{"text_start": 0, "y_pt": 0, "height_pt": 12, "text_height_pt": 12,
                "baseline_pt": 10.2, "spacing_pt": 3.6, "x_pt": 0, "width_pt": 100, "flags": 0}])
        padded = deepcopy(paragraph)
        padded.cache_text = "연구대상 학교급\u200b"
        first = SourceLayout(DocumentModel([paragraph]))
        second = SourceLayout(DocumentModel([padded]))
        self.assertEqual(first.render_page(0), second.render_page(0))

    def test_leading_and_internal_control_slots_have_no_tracking_advance(self):
        from hangeul.formats import Paragraph,ParaStyle,Run,TextStyle
        from hangeul.geometry import DocumentModel
        from hangeul.source_layout import SourceLayout
        for spacing in (-16,16):
            style=TextStyle(size=12,letter_spacing=spacing)
            line={'text_start':0,'y_pt':0,'height_pt':12,'text_height_pt':12,
                  'baseline_pt':10.2,'spacing_pt':3.6,'x_pt':0,'width_pt':240,'flags':0}
            plain=Paragraph([Run('연구대상 학교급',style,source_start=0)],
                style=ParaStyle(align='CENTER'),default_style=style,line_segments=[line])
            padded=Paragraph([Run('연구대상',style,source_start=24),Run(' 학교급',style,source_start=36)],
                style=ParaStyle(align='CENTER'),default_style=style,line_segments=[line],
                cache_text='\u200b'*24+'연구대상'+'\u200b'*8+' 학교급')
            first=SourceLayout(DocumentModel([plain]));second=SourceLayout(DocumentModel([padded]))
            self.assertEqual(first.render_page(0),second.render_page(0),spacing)
            self.assertEqual(padded.cache_text,second.pages[0][0].extra['text'])

    def cropped_picture(self, horizontal=False, vertical=False):
        from io import BytesIO
        from PIL import Image, ImageDraw
        from hangeul.formats import Paragraph, Run
        from hangeul.geometry import DocumentModel, PictureGeometry
        image = Image.new("RGB", (400, 200))
        draw = ImageDraw.Draw(image)
        for box, color in (((0, 0, 199, 99), "red"), ((200, 0, 399, 99), "lime"),
                           ((0, 100, 199, 199), "blue"), ((200, 100, 399, 199), "yellow")):
            draw.rectangle(box, fill=color)
        stream = BytesIO()
        image.save(stream, format="PNG")
        picture = PictureGeometry(original_width_pt=300, original_height_pt=150,
            image_width_pt=300, image_height_pt=150, crop=(0, 0, 225, 75),
            flip_h=horizontal, flip_v=vertical)
        return DocumentModel([Paragraph([Run(image=stream.getvalue(), width=200, height=100, picture=picture)])])

    def test_cropped_picture_reflection_keeps_selected_source_region_in_both_formats(self):
        from hangeul.formats import save_hwp, save_hwpx, load_document
        from hangeul.source_layout import SourceLayout
        for suffix, save in (("hwp", save_hwp), ("hwpx", save_hwpx)):
            for horizontal, vertical in ((False, False), (True, False), (False, True), (True, True)):
                with self.subTest(format=suffix, horizontal=horizontal, vertical=vertical):
                    source = self.root / ("crop." + suffix)
                    save(self.cropped_picture(horizontal, vertical), source)
                    layout = SourceLayout(load_document(source).model)
                    command = next(c for c in layout.pages[0] if c.kind == "image")
                    x, y, width, height = (value / .75 for value in command.rect)
                    image = layout.render_page(0)
                    colors = [image.pixelColor(round(x + width * fraction), round(y + height / 2)).name()
                              for fraction in (.25, .5, .75)]
                    self.assertEqual(["#00ff00", "#ff0000", "#ff0000"] if horizontal
                                     else ["#ff0000", "#ff0000", "#00ff00"], colors)

    def test_read_report_identifies_remaining_pdf_transparency_difference(self):
        from hangeul.formats import save_hwpx
        model = self.cropped_picture()
        picture = model.blocks[0].runs[0].picture
        picture.alpha, picture.brightness, picture.contrast, picture.effect = 128, 20, -10, "GRAY_SCALE"
        source = self.root / "appearance.hwpx"
        save_hwpx(model, source)
        with Document(source) as document:
            report = document.report()
        limitations = report["layout"]["image_limitations"]
        self.assertEqual(1, len(limitations))
        self.assertEqual({'alpha:printer-coordinate-rounding','alpha:native-process-brush-phase'},set(limitations[0]['unsupported']))
        self.assertTrue(any("layout.image_limitations" in warning for warning in report["warnings"]))
        self.assertFalse(report["independent_parity_verified"])

    @unittest.skipUnless(os.environ.get("NARAE_RUN_HANCOM_TESTS") == "1", "requires installed Hancom")
    def test_native_cropped_picture_flips_select_the_same_image_content(self):
        from copy import deepcopy
        from hangeul.formats import ParaStyle, save_hwpx
        from hangeul.hwp_automation_bridge import HancomBridge
        model = self.cropped_picture()
        for _ in range(3):
            paragraph = deepcopy(model.blocks[0])
            paragraph.style = ParaStyle(page_break=True)
            model.blocks.append(paragraph)
        initial = self.root / "initial.hwpx"
        save_hwpx(model, initial)
        # Let Hancom serialize its own flip flags and affine matrices. Manually
        # toggled flags with identity matrices are not a native-generated case.
        with HancomBridge() as bridge:
            bridge.open_doc(initial)
            controls = bridge.controls("gso")
            self.assertEqual(4, len(controls))
            for control, actions in zip(controls, ((), ("ShapeObjHorzFlip",), ("ShapeObjVertFlip",),
                                       ("ShapeObjHorzFlip", "ShapeObjVertFlip"))):
                if not actions:
                    continue
                bridge.hwp.SetPosBySet(control.GetAnchorPos(0))
                bridge.run("SelectCtrlReverse")
                for action in actions:
                    bridge.run(action)
                bridge.run("Cancel")
            for suffix in ("hwp", "hwpx"):
                bridge.save_doc(self.root / ("native." + suffix))
        for suffix in ("hwp", "hwpx"):
            with self.subTest(format=suffix), Document(self.root / ("native." + suffix)) as independent, \
                    Document(self.root / ("native." + suffix), "hancom") as native:
                self.assertEqual(4, independent.count)
                self.assertEqual(4, native.count)
                self.assertTrue(independent.report()["source_unchanged"])
                for page in range(4):
                    actual, reference = independent.render(page, 144), native.render(page, 144)
                    # Content identity inside solid patches is independent of
                    # the exporters' different JPEG compression and edge grids.
                    for x in (180, 263, 340):
                        a, b = actual.pixelColor(x, 170), reference.pixelColor(x, 170)
                        self.assertEqual(a.green() > a.red(), b.green() > b.red())
                        self.assertLess(a.blue(), 20)
                        self.assertLess(b.blue(), 20)

    @unittest.skipUnless(os.environ.get("NARAE_RUN_HANCOM_TESTS") == "1", "requires installed Hancom")
    def test_native_export_two_pages_and_repeated_pixels(self):
        with Document(self.fixture(), "hancom") as first, Document(self.fixture("second.hwpx"), "hancom") as second:
            self.assertEqual(2, first.count)
            self.assertEqual(2, first.details["hancom_page_count"])
            self.assertTrue(compare(first, second)["match"])

    @unittest.skipUnless(os.environ.get("NARAE_RUN_HANCOM_TESTS") == "1", "requires installed Hancom")
    def test_parallel_native_cli_jobs_keep_their_own_documents(self):
        source = self.fixture()
        launcher = ROOT / "6_나래뷰어/narae_viewer.py"
        jobs = []
        for index in range(2):
            target = self.root / f"parallel-{index}.pdf"
            jobs.append(subprocess.Popen([sys.executable, "-X", "utf8", str(launcher), "export", str(source),
                "--backend", "hancom", "--pdf", str(target)], stdout=subprocess.PIPE, stderr=subprocess.PIPE))
        for job in jobs:
            stdout, stderr = job.communicate(timeout=120)
            self.assertEqual(0, job.returncode, stderr.decode("utf-8", errors="replace"))
            self.assertEqual(2, json.loads(stdout)["page_count"])
        with Document(self.root / "parallel-0.pdf") as a, Document(self.root / "parallel-1.pdf") as b:
            self.assertTrue(compare(a, b)["match"])


if __name__ == "__main__":
    unittest.main()
