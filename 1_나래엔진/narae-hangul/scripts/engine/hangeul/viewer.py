"""Read-only HWP/HWPX viewer, exports and strict whole-document comparison.

The independent backend reuses SourceLayout. Hancom is an explicit reference
backend, never an implicit fallback or evidence of independent parity.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

VERSION = "0.1.0"
_APP = None


def application(gui=False):
    global _APP
    # Windows' offscreen platform exposes no installed system fonts. A normal
    # QApplication stays hidden until a window is shown and preserves fonts.
    if not gui and os.name != "nt" and not os.environ.get("DISPLAY"):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    _APP = QApplication.instance() or QApplication(["NaraeViewer"])
    return _APP


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def pages(value, count):
    """1-based inclusive user ranges; reject mistakes instead of clamping."""
    if value == "all":
        return list(range(count))
    selected = set()
    for part in value.split(","):
        bounds = part.strip().split("-")
        if len(bounds) not in (1, 2) or any(not b.isdecimal() for b in bounds):
            raise ValueError("쪽 범위는 all, 1,3-5 형식입니다.")
        start, end = int(bounds[0]), int(bounds[-1])
        if not 1 <= start <= end <= count:
            raise ValueError(f"쪽 범위가 문서의 1~{count}쪽을 벗어납니다: {part}")
        selected.update(range(start - 1, end))
    return sorted(selected)


def same_file(first, second):
    first, second = Path(first).resolve(), Path(second).resolve()
    return first == second or (first.exists() and second.exists() and first.samefile(second))


def destination(path, inputs, overwrite=False):
    path = Path(path).expanduser().resolve()
    if any(same_file(path, source) for source in inputs):
        raise ValueError("출력 경로는 입력 문서·기준 파일과 달라야 합니다.")
    if path.exists() and (not overwrite or not path.is_file()):
        raise ValueError(f"출력 파일이 이미 있습니다: {path} (--overwrite로 교체)")
    return path


def write_json(path, value, inputs=(), overwrite=False):
    path = destination(path, inputs, overwrite)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False, suffix=".json") as stream:
        stage = Path(stream.name)
        stream.write(json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8"))
    try:
        os.replace(stage, path)
    finally:
        stage.unlink(missing_ok=True)


def native_export(source, target):
    """Runs in a bounded worker. Work on a private copy in an owned COM tab."""
    from .hwp_automation_bridge import HancomBridge
    target = destination(target, [source])
    if target.suffix.lower() != ".pdf":
        raise ValueError("한컴 기준 출력의 확장자는 .pdf여야 합니다.")
    with tempfile.TemporaryDirectory(prefix="narae-read-") as folder:
        copy = Path(folder) / source.name
        shutil.copyfile(source, copy)
        with HancomBridge() as bridge:
            bridge.open_doc(copy)
            count_before = int(bridge.hwp.PageCount)
            version = str(bridge.hwp.Version)
            # SaveAs(PDF) inherits document print settings (including N-up,
            # selected ranges and odd/even pages). A verification reference
            # must explicitly export every logical page at its original size.
            action = bridge.hwp.CreateAction("PrintToPDFEx")
            settings = action.CreateSet()
            bridge.check(action.GetDefault(settings), "PrintToPDFEx defaults")
            options = {"Range": 6, "RangeCustom": "", "RangeIncludeLinkedDoc": 0,
                       "NumCopy": 1, "PrintMethod": 0, "PrintToFile": 1,
                       "FileName": str(target), "ReverseOrder": 0,
                       "EvenOddPageType": 0, "ZoomX": 100, "ZoomY": 100,
                       "PrintWithoutBlank": 0}
            for name, value in options.items():
                settings.SetItem(name, value)
            bridge.check(action.Execute(settings), "PrintToPDFEx options")
            if not target.is_file() or target.stat().st_size == 0:
                raise ValueError("한컴 PDF 출력 파일이 없습니다.")
            # Opening a document may defer pagination. PDF export forces the
            # final layout; compare its count to PageCount after that export.
            count = int(bridge.hwp.PageCount)
            return {"hancom_version": version, "hancom_page_count": count,
                    "hancom_page_count_before_export": count_before}


class Document:
    def __init__(self, source, backend="independent", timeout=120):
        self.source = Path(source).expanduser().resolve()
        if not self.source.is_file():
            raise ValueError(f"입력 문서가 없습니다: {self.source}")
        if self.source.suffix.lower() not in (".hwp", ".hwpx", ".pdf"):
            raise ValueError("HWP, HWPX 또는 PDF 파일을 선택하세요.")
        self.before = digest(self.source)
        self.backend = "pdf" if self.source.suffix.lower() == ".pdf" else backend
        self._temporary = tempfile.TemporaryDirectory(prefix="narae-viewer-")
        self.pdf_path = Path(self._temporary.name) / "view.pdf"
        self.pdf = None
        self.details = {}
        try:
            if self.backend == "pdf":
                shutil.copyfile(self.source, self.pdf_path)
            elif self.backend == "independent":
                from .revision import revision_state
                if revision_state(self.source):
                    raise ValueError("변경 추적 문서는 live-read 또는 --backend hancom으로 확인하세요.")
                from .formats import load_document
                from .source_layout import SourceLayout
                from PySide6.QtPrintSupport import QPrinter
                loaded = load_document(self.source)
                if loaded.model is None:
                    raise ValueError("이 문서의 읽기 배치 모델이 없습니다.")
                layout = SourceLayout(loaded.model)
                printer = QPrinter(QPrinter.PrinterMode.HighResolution)
                printer.setResolution(600)
                printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
                printer.setOutputFileName(str(self.pdf_path))
                printer.setFullPage(True)
                layout.print_to(printer)
                self.details = layout.geometry_report()
                self.details["import_warnings"] = loaded.warnings
            elif self.backend == "hancom":
                if os.name != "nt":
                    raise ValueError("한컴 기준 출력은 Windows와 설치된 한컴이 필요합니다.")
                result = subprocess.run(
                    [sys.executable, "-m", "hangeul.viewer", "_native", str(self.source), str(self.pdf_path)],
                    capture_output=True, encoding="utf-8", errors="replace", timeout=timeout,
                    env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])
                         + os.pathsep + os.environ.get("PYTHONPATH", ""), "PYTHONUTF8": "1"},
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                if result.returncode:
                    raise ValueError(f"한컴 기준 출력 실패: {(result.stderr or result.stdout)[-3000:]}")
                self.details = json.loads(result.stdout)
            else:
                raise ValueError(f"알 수 없는 읽기 백엔드: {backend}")
            from PySide6.QtPdf import QPdfDocument
            self.pdf = QPdfDocument()
            error = self.pdf.load(str(self.pdf_path))
            if error != QPdfDocument.Error.None_ or self.pdf.pageCount() < 1:
                raise ValueError(f"PDF 읽기에 실패했습니다: {error}")
            if "hancom_page_count" in self.details and self.details["hancom_page_count"] != self.count:
                raise ValueError("한컴 PageCount와 출력 PDF 쪽수가 다릅니다.")
            self.check_source()
        except BaseException:
            self.close()
            raise

    @property
    def count(self):
        return self.pdf.pageCount()

    def check_source(self):
        if digest(self.source) != self.before:
            raise ValueError("읽는 동안 입력 문서가 변경되었습니다. 결과를 다시 생성하세요.")

    def report(self, selection="all", text=True):
        result = {"success": True, "viewer_version": VERSION, "input": str(self.source),
                  "input_sha256": self.before, "source_unchanged": True, "read_only": True,
                  "backend": self.backend, "uses_hancom_runtime": self.backend == "hancom",
                  "independent_parity_verified": False, "page_count": self.count,
                  "warnings": self.details.get("warnings", []), "pages": []}
        for index in pages(selection, self.count):
            size = self.pdf.pagePointSize(index)
            page = {"page": index + 1, "size_pt": [size.width(), size.height()]}
            if text:
                page["text"] = self.text(index)
            result["pages"].append(page)
        result["layout"] = {key: value for key, value in self.details.items()
                            if key not in ("objects", "hft_fonts", "installed_sfnt_fonts")}
        return result

    def text(self, index):
        # QtPdf ignores render-mode-3 text. The independent renderer's exact
        # Unicode layer is intentionally invisible behind outline glyphs.
        if not hasattr(self, "_text_reader"):
            from pypdf import PdfReader
            self._text_reader = PdfReader(self.pdf_path)
        return self._text_reader.pages[index].extract_text() or ""

    def render(self, index, dpi):
        from PySide6.QtCore import QSize
        from PySide6.QtGui import QImage, QPainter, QColor
        size = self.pdf.pagePointSize(index)
        width, height = math.ceil(size.width() * dpi / 72), math.ceil(size.height() * dpi / 72)
        if width * height > 40_000_000:
            raise ValueError("쪽별 이미지가 4천만 픽셀을 초과합니다. DPI를 낮추세요.")
        result = self.pdf.render(index, QSize(width, height))
        if result.isNull():
            raise ValueError(f"{index + 1}쪽 이미지 출력 실패")
        # QtPdf returns transparent paper. Dropping alpha would turn both
        # transparent paper and black glyphs black, hiding real differences.
        paper = QImage(width, height, QImage.Format.Format_RGB32)
        paper.fill(QColor("white"))
        painter = QPainter(paper)
        painter.drawImage(0, 0, result)
        painter.end()
        paper.setDotsPerMeterX(round(dpi / .0254))
        paper.setDotsPerMeterY(round(dpi / .0254))
        return paper

    def close(self):
        if self.pdf is not None:
            self.pdf.close()
            self.pdf = None
        self._temporary.cleanup()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def export(document, pdf=None, png_dir=None, selection="all", dpi=144, overwrite=False, protected=()):
    indices = pages(selection, document.count)
    inputs = (document.source, *protected)
    outputs = []
    if pdf:
        target = destination(pdf, inputs, overwrite)
        if target.suffix.lower() != ".pdf":
            raise ValueError("PDF 출력 파일의 확장자는 .pdf여야 합니다.")
        outputs.append((document.pdf_path, target))
    if png_dir:
        directory = Path(png_dir).expanduser().resolve()
        for index in indices:
            target = destination(directory / f"page-{index+1:04}.png", inputs, overwrite)
            outputs.append((index, target))
    if not outputs:
        raise ValueError("--pdf 또는 --png-dir을 지정하세요.")
    targets = [target for _, target in outputs]
    if len(set(targets)) != len(targets):
        raise ValueError("출력 경로가 중복됩니다.")
    with tempfile.TemporaryDirectory(prefix="narae-export-") as folder:
        staged = []
        for source, target in outputs:
            stage = Path(folder) / target.name
            if isinstance(source, int):
                if not document.render(source, dpi).save(str(stage), "PNG"):
                    raise OSError("PNG 파일을 저장하지 못했습니다.")
            else:
                shutil.copyfile(source, stage)
            staged.append((stage, target))
        document.check_source()
        for stage, target in staged:
            target.parent.mkdir(parents=True, exist_ok=True)
            # Stage on the destination filesystem before atomic replacement.
            with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as stream:
                local = Path(stream.name)
            try:
                shutil.copyfile(stage, local)
                os.replace(local, target)
            finally:
                local.unlink(missing_ok=True)
    return [{"path": str(target), "sha256": digest(target)} for target in targets]


def compare(document, reference, dpi=96, diff_dir=None, overwrite=False, protected=()):
    """Compare every page at identical DPI, without fitting/alignment/tolerance."""
    from PIL import Image, ImageChops
    from PySide6.QtCore import QBuffer, QIODevice
    from io import BytesIO
    def raster(doc, index):
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        if not doc.render(index, dpi).save(buffer, "PNG"):
            raise ValueError("비교 이미지 생성 실패")
        return Image.open(BytesIO(bytes(buffer.data()))).convert("RGB")
    result = {"success": True, "match": document.count == reference.count,
              "page_count_match": document.count == reference.count,
              "page_count": document.count, "reference_page_count": reference.count,
              "dpi": dpi, "pixel_tolerance": 0, "all_pages_compared": True,
              "reference_sha256": reference.before, "reference_backend": reference.backend,
              "reference_render_sha256": digest(reference.pdf_path),
              "reference_hancom_version": reference.details.get("hancom_version"),
              "reference_input": str(reference.source), "pages": []}
    inputs = (document.source, reference.source, *protected)
    if diff_dir:
        for index in range(min(document.count, reference.count)):
            destination(Path(diff_dir) / f"diff-{index+1:04}.png", inputs, overwrite)
    for index in range(max(document.count, reference.count)):
        if index >= min(document.count, reference.count):
            result["pages"].append({"page": index + 1, "match": False, "reason": "missing_page"})
            continue
        a, b = raster(document, index), raster(reference, index)
        sa, sb = document.pdf.pagePointSize(index), reference.pdf.pagePointSize(index)
        size_match = sa == sb and a.size == b.size
        entry = {"page": index + 1, "size_match": size_match,
                 "size_pt": [sa.width(), sa.height()], "reference_size_pt": [sb.width(), sb.height()]}
        if a.size != b.size:
            entry.update(match=False, reason="page_size")
        else:
            delta = ImageChops.difference(a, b)
            mask = ImageChops.lighter(ImageChops.lighter(*delta.split()[:2]), delta.split()[2])
            changed = a.width * a.height - mask.histogram()[0]
            entry.update(match=size_match and changed == 0, changed_pixels=changed,
                         changed_fraction=changed / (a.width * a.height), difference_bbox=delta.getbbox())
            if diff_dir and not entry["match"]:
                target = Path(diff_dir).resolve() / f"diff-{index+1:04}.png"
                target.parent.mkdir(parents=True, exist_ok=True)
                delta.save(target)
                entry["diff"] = str(target)
        result["match"] = result["match"] and entry["match"]
        result["pages"].append(entry)
    document.check_source()
    reference.check_source()
    return result


def parser():
    root = argparse.ArgumentParser(description="나래한글 뷰어: 읽기 전용 HWP/HWPX/PDF")
    commands = root.add_subparsers(dest="action", required=True)
    for action in ("read", "open", "export", "verify"):
        item = commands.add_parser(action)
        item.add_argument("input")
        item.add_argument("--backend", choices=("independent", "hancom"), default="independent")
        item.add_argument("--result", help="결과 JSON 경로")
        item.add_argument("--overwrite", action="store_true")
        item.add_argument("--timeout", type=int, default=120, help="한컴 출력 제한 시간(초)")
        if action in ("read", "export"):
            item.add_argument("--pages", default="all", help="all 또는 1,3-5 (PDF는 항상 전체 쪽)")
        if action == "export":
            item.add_argument("--pdf")
            item.add_argument("--png-dir")
        if action in ("export", "verify"):
            item.add_argument("--dpi", type=int, default=96)
        if action == "verify":
            against = item.add_mutually_exclusive_group(required=True)
            against.add_argument("--reference", help="기준 PDF 또는 HWP/HWPX")
            against.add_argument("--hancom", action="store_true", help="같은 문서를 설치된 한컴으로 출력해 비교")
            item.add_argument("--reference-backend", choices=("independent", "hancom"), default="independent")
            item.add_argument("--diff-dir")
    return root


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "_native":
        try:
            result = native_export(Path(argv[1]), Path(argv[2]))
            print(json.dumps(result))
            return 0
        except Exception as error:
            print(str(error), file=sys.stderr)
            return 1
    if not argv:
        application(gui=True)
        from PySide6.QtWidgets import QFileDialog
        source, _ = QFileDialog.getOpenFileName(None, "나래한글 뷰어 · 문서 열기", "", "한글 문서 및 PDF (*.hwp *.hwpx *.pdf)")
        if not source:
            return 0
        argv = ["open", source]
    args = parser().parse_args(argv)
    response, code, result_allowed = {}, 1, False
    try:
        inputs = [Path(args.input).expanduser().resolve()]
        if getattr(args, "reference", None):
            inputs.append(Path(args.reference).expanduser().resolve())
        reserved = []
        if args.result:
            reserved.append(destination(args.result, inputs, args.overwrite))
            if getattr(args, "pdf", None) and same_file(args.result, args.pdf):
                raise ValueError("결과 JSON과 PDF 출력 경로가 같습니다.")
            for option, prefix in (("png_dir", "page"), ("diff_dir", "diff")):
                directory = getattr(args, option, None)
                if (directory and reserved[0].parent == Path(directory).expanduser().resolve()
                        and re.fullmatch(prefix + r"-\d{4,}\.png", reserved[0].name, flags=re.IGNORECASE)):
                    raise ValueError("결과 JSON과 쪽별 이미지 출력 경로가 충돌합니다.")
            result_allowed = True
        if not 1 <= args.timeout <= 1800:
            raise ValueError("--timeout은 1~1800초입니다.")
        if hasattr(args, "dpi") and not 24 <= args.dpi <= 600:
            raise ValueError("--dpi는 24~600입니다.")
        if args.action == "export" and args.pdf:
            destination(args.pdf, [*inputs, *reserved], args.overwrite)
        application(gui=args.action == "open")
        with Document(args.input, args.backend, args.timeout) as document:
            response = document.report(getattr(args, "pages", "all"), text=args.action == "read")
            if args.action == "export":
                response["outputs"] = export(document, args.pdf, args.png_dir, args.pages, args.dpi,
                                             args.overwrite, reserved)
            elif args.action == "verify":
                source = args.input if args.hancom else args.reference
                backend = "hancom" if args.hancom else args.reference_backend
                with Document(source, backend, args.timeout) as reference:
                    response["comparison"] = compare(document, reference, args.dpi, args.diff_dir,
                                                     args.overwrite, reserved)
                    response["independent_parity_verified"] = (document.backend == "independent"
                        and reference.backend == "hancom" and response["comparison"]["match"])
                code = 0 if response["comparison"]["match"] else 2
            elif args.action == "open":
                from .viewer_gui import show
                show(document)
            document.check_source()
            if args.action != "verify":
                code = 0
    except Exception as error:
        response, code = {"success": False, "error": str(error), "viewer_version": VERSION}, 1
    if args.result and result_allowed:
        try:
            write_json(args.result, response, inputs, args.overwrite)
        except Exception as error:
            response, code = {"success": False, "error": str(error)}, 1
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(response, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
