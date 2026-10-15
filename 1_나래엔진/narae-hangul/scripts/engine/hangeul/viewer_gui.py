"""Viewer-only window: PDF canvas, navigation, find, PDF/PNG export."""
from PySide6.QtCore import QPointF, QModelIndex
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (QMainWindow, QToolBar, QSpinBox, QLabel, QComboBox,
    QFileDialog, QMessageBox, QDockWidget, QPlainTextEdit, QLineEdit, QPushButton, QTabWidget)
from PySide6.QtPdf import QPdfSearchModel
from PySide6.QtPdfWidgets import QPdfView
from .viewer import application, export


class ViewerWindow(QMainWindow):
    def __init__(self, document):
        super().__init__()
        self.document = document
        self.setWindowTitle(f"나래한글 뷰어 · {document.source.name} · 읽기 전용")
        self.resize(1150, 850)
        self.view = QPdfView(self)
        self.view.setDocument(document.pdf)
        self.view.setPageMode(QPdfView.PageMode.MultiPage)
        self.view.setZoomMode(QPdfView.ZoomMode.FitToWidth)
        self.setCentralWidget(self.view)
        toolbar = QToolBar("읽기 도구", self)
        self.addToolBar(toolbar)
        for label, callback, shortcut in (
            ("PDF 저장", self.save_pdf, "Ctrl+Shift+S"),
            ("쪽별 PNG", self.save_png, ""),
            ("이전 쪽", lambda: self.move(-1), "Alt+Left"),
            ("다음 쪽", lambda: self.move(1), "Alt+Right"),
        ):
            action = QAction(label, self)
            if shortcut:
                action.setShortcut(QKeySequence(shortcut))
            action.triggered.connect(callback)
            toolbar.addAction(action)
        self.page = QSpinBox()
        self.page.setRange(1, document.count)
        self.page.valueChanged.connect(lambda value: self.view.pageNavigator().jump(value - 1, QPointF()))
        toolbar.addWidget(self.page)
        toolbar.addWidget(QLabel(f" / {document.count}쪽  "))
        self.view.pageNavigator().currentPageChanged.connect(self.page_changed)
        zoom = QComboBox()
        zoom.addItems(["너비 맞춤", "쪽 맞춤", "75%", "100%", "125%", "150%", "200%"])
        zoom.currentTextChanged.connect(self.zoom)
        toolbar.addWidget(zoom)
        self.search = QPdfSearchModel(self)
        self.search.setDocument(document.pdf)
        self.view.setSearchModel(self.search)
        self.query = QLineEdit()
        self.query.setPlaceholderText("문서에서 찾기")
        self.query.textChanged.connect(self.search.setSearchString)
        self.query.returnPressed.connect(self.find)
        toolbar.addWidget(self.query)
        next_hit = QPushButton("찾기")
        next_hit.clicked.connect(self.find)
        toolbar.addWidget(next_hit)
        find = QAction(self)
        find.setShortcut(QKeySequence.StandardKey.Find)
        find.triggered.connect(self.query.setFocus)
        self.addAction(find)
        detail = QDockWidget("문서 정보", self)
        content = QPlainTextEdit()
        content.setReadOnly(True)
        content.setPlainText("\n".join([
            f"파일: {document.source.name}", f"쪽수: {document.count}",
            f"읽기 엔진: {document.backend}", "편집·HWP/HWPX 저장 기능 없음",
            "SHA256: " + document.before, "",
            *(document.details.get("warnings", [])),
        ]))
        tabs = QTabWidget()
        tabs.addTab(content, "정보")
        self.page_text = QPlainTextEdit()
        self.page_text.setReadOnly(True)
        self.page_text.setPlainText(document.text(0))
        tabs.addTab(self.page_text, "현재 쪽 내용")
        detail.setWidget(tabs)
        from PySide6.QtCore import Qt
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, detail)
        label = "한컴 기준 출력" if document.backend == "hancom" else "독립 렌더링 · 한컴과의 표시 일치는 비교 검증 필요"
        if document.backend == "pdf":
            label = "PDF 읽기"
        self.statusBar().showMessage(label)

    def page_changed(self, index):
        self.page.blockSignals(True)
        self.page.setValue(index + 1)
        self.page.blockSignals(False)
        self.page_text.setPlainText(self.document.text(index))

    def move(self, offset):
        self.page.setValue(self.page.value() + offset)

    def zoom(self, text):
        if text == "너비 맞춤":
            self.view.setZoomMode(QPdfView.ZoomMode.FitToWidth)
        elif text == "쪽 맞춤":
            self.view.setZoomMode(QPdfView.ZoomMode.FitInView)
        else:
            self.view.setZoomMode(QPdfView.ZoomMode.Custom)
            self.view.setZoomFactor(int(text[:-1]) / 100)

    def find(self):
        count = self.search.rowCount(QModelIndex())
        if not count:
            query = self.query.text().casefold()
            current = self.page.value() - 1
            for offset in range(1, self.document.count + 1):
                index = (current + offset) % self.document.count
                if query and query in self.document.text(index).casefold():
                    self.page.setValue(index + 1)
                    self.statusBar().showMessage(f"{index + 1}쪽에서 찾았습니다. 현재 쪽 내용 탭에서 확인하세요.")
                    return
            self.statusBar().showMessage("일치하는 문구가 없습니다.")
            return
        index = (self.view.currentSearchResultIndex() + 1) % count
        self.view.setCurrentSearchResultIndex(index)
        result = self.search.resultAtIndex(index)
        self.view.pageNavigator().jump(result.page(), result.location())

    def save_pdf(self):
        path, _ = QFileDialog.getSaveFileName(self, "PDF 저장", self.document.source.stem + ".pdf", "PDF (*.pdf)")
        if path:
            self.save(pdf=path, overwrite=True)

    def save_png(self):
        path = QFileDialog.getExistingDirectory(self, "쪽별 PNG 저장 폴더")
        if path:
            self.save(png_dir=path)

    def save(self, **kwargs):
        try:
            outputs = export(self.document, **kwargs)
            self.statusBar().showMessage(f"{len(outputs)}개 파일 저장 완료")
        except Exception as error:
            QMessageBox.warning(self, "출력 실패", str(error))


def show(document):
    window = ViewerWindow(document)
    window.show()
    application(gui=True).exec()
    window.view.setDocument(None)
    window.search.setDocument(None)
    window.close()
