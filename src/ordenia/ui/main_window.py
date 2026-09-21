from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QListWidget, QMainWindow, QMessageBox, QStackedWidget, QVBoxLayout, QWidget

from ordenia import __version__
from ordenia.database.repositories import Repository
from ordenia.services.file_service import FileService
from ordenia.ui.pages.files import FilesPage
from ordenia.ui.pages.folders import FoldersPage
from ordenia.ui.pages.history import HistoryPage
from ordenia.ui.pages.home import HomePage
from ordenia.ui.pages.settings import SettingsPage


STYLESHEET = """
QWidget { background: #10141d; color: #e8ecf5; font-family: 'Segoe UI'; font-size: 13px; }
QMainWindow { background: #10141d; }
QFrame#sidebar { background: #171d29; border-right: 1px solid #2b3444; }
QLabel#brand { font-size: 25px; font-weight: 800; color: #73cbbd; letter-spacing: 2px; }
QLabel#pageTitle { font-size: 25px; font-weight: 700; }
QLabel#sectionTitle { font-size: 17px; font-weight: 650; margin-top: 10px; }
QLabel#muted { color: #9eabc0; }
QLabel#statValue { font-size: 28px; font-weight: 700; color: #f8fbff; }
QFrame#statCard { background: #1b2230; border: 1px solid #303b4e; border-radius: 12px; padding: 12px; }
QFrame#statCard QLabel { background: transparent; }
QFrame#summaryCard, QFrame#emptyState, QFrame#detailsCard { background: #1b2230; border: 1px solid #303b4e; border-radius: 10px; padding: 8px; }
QFrame#summaryCard QLabel, QFrame#emptyState QLabel, QFrame#detailsCard QLabel { background: transparent; }
QFrame#toolbarCard { background: #171d29; border: 1px solid #303b4e; border-radius: 10px; }
QFrame#inspectorPanel { background: #171d29; border: 1px solid #303b4e; border-radius: 12px; }
QFrame#inspectorHeader { background: #1b2230; border: none; border-bottom: 1px solid #303b4e; border-top-left-radius: 11px; border-top-right-radius: 11px; }
QLabel#eyebrow { color: #73cbbd; font-size: 10px; font-weight: 700; letter-spacing: 1px; }
QLabel#inspectorTitle { color: #f8fbff; font-size: 18px; font-weight: 700; }
QLabel#inspectorMeta { color: #9eabc0; font-size: 12px; }
QTabWidget#inspectorTabs::pane { background: #151b26; border: none; border-top: 1px solid #303b4e; }
QTabWidget#inspectorTabs QTabBar::tab { background: #171d29; color: #9eabc0; border: none; border-bottom: 2px solid transparent; padding: 10px 13px; font-weight: 600; }
QTabWidget#inspectorTabs QTabBar::tab:selected { color: #dffff9; background: #1b2633; border-bottom-color: #47b9a6; }
QTabWidget#inspectorTabs QTabBar::tab:hover:!selected { color: #d5deec; background: #202939; }
QScrollArea#inspectorScroll, QWidget#inspectorBody { background: #151b26; border: none; }
QFrame#inspectorCard { background: #1b2230; border: 1px solid #303b4e; border-radius: 10px; }
QFrame#aiSuggestionCard { background: #1b2230; border: 1px solid #3b4b61; border-radius: 10px; }
QFrame#aiSuggestionCard[ready="true"] { background: #172b2b; border: 2px solid #47b9a6; }
QLabel#cardTitle { color: #f8fbff; font-size: 17px; font-weight: 700; }
QLabel#fieldLabel { color: #8f9db3; font-size: 11px; font-weight: 600; }
QLabel#fieldValue { color: #e8ecf5; }
QLabel#previewText, QLabel#reasonText { color: #d3dbea; background: #151b26; border: 1px solid #2f394a; border-radius: 7px; padding: 10px; }
QLabel#tagList[populated="true"] { color: #b8fff2; background: #203c3c; border: 1px solid #35645f; border-radius: 7px; padding: 5px 8px; }
QLabel#suggestedPath[populated="true"] { color: #b8fff2; font-weight: 700; background: #1d3537; border-radius: 7px; padding: 7px 9px; }
QLabel#readyBadge { color: #dffff9; background: #24796d; border-radius: 7px; padding: 4px 7px; font-size: 10px; font-weight: 700; }
QLabel#statusBadge { color: #c8d1df; background: #293445; border-radius: 7px; padding: 4px 8px; font-weight: 600; }
QLabel#statusBadge[tone="ready"] { color: #c9fff5; background: #1d6158; }
QLabel#statusBadge[tone="pending"] { color: #ffe4a8; background: #604a22; }
QLabel#statusBadge[tone="error"] { color: #ffd2d2; background: #6a3035; }
QSplitter::handle { background: #10141d; width: 8px; }
QSplitter::handle:hover { background: #35455c; }
QListWidget { background: transparent; border: none; outline: none; padding-top: 12px; }
QListWidget::item { padding: 12px 14px; margin: 2px 9px; border-radius: 8px; }
QListWidget::item:selected { background: #254c53; color: #d9fff5; }
QListWidget::item:hover:!selected { background: #252e3e; }
QTableWidget { background: #171d29; alternate-background-color: #1c2432; border: 1px solid #303b4e; border-radius: 8px; gridline-color: #303b4e; selection-background-color: #28525c; }
QHeaderView::section { background: #202939; color: #aebbd0; border: none; padding: 8px; font-weight: 600; }
QPushButton { background: #263245; border: 1px solid #3a4960; border-radius: 7px; padding: 9px 14px; }
QPushButton:hover { background: #35455c; }
QPushButton#primaryButton { background: #218979; border-color: #218979; color: white; font-weight: 600; }
QPushButton#primaryButton:hover { background: #2aa18e; }
QCheckBox { spacing: 10px; }
QLineEdit, QComboBox { background: #202939; border: 1px solid #3a4960; border-radius: 6px; padding: 7px; }
QProgressBar { border: 1px solid #3a4960; border-radius: 6px; text-align: center; background: #202939; }
QProgressBar::chunk { background: #218979; border-radius: 5px; }
"""


class MainWindow(QMainWindow):
    def __init__(self, repository: Repository, service: FileService) -> None:
        super().__init__()
        self.repository = repository
        self.service = service
        self.setWindowTitle(f"OrdenIA · V{__version__}")
        self.setMinimumSize(820, 480)
        self.resize(1240, 760)
        self.setStyleSheet(STYLESHEET)

        container = QWidget()
        horizontal = QHBoxLayout(container)
        horizontal.setContentsMargins(0, 0, 0, 0)
        horizontal.setSpacing(0)
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(210)
        side_layout = QVBoxLayout(sidebar)
        side_layout.setContentsMargins(12, 22, 12, 16)
        brand = QLabel("ORDENIA")
        brand.setObjectName("brand")
        brand.setAlignment(Qt.AlignmentFlag.AlignCenter)
        side_layout.addWidget(brand)
        self.navigation = QListWidget()
        self.navigation.addItems(["Inicio", "Archivos detectados", "Carpetas vigiladas", "Historial", "Configuración"])
        side_layout.addWidget(self.navigation)
        version = QLabel(f"V{__version__} · Local y seguro")
        version.setObjectName("muted")
        version.setAlignment(Qt.AlignmentFlag.AlignCenter)
        side_layout.addWidget(version)
        horizontal.addWidget(sidebar)

        self.pages = [HomePage(repository), FilesPage(repository, service), FoldersPage(repository, service), HistoryPage(repository, service), SettingsPage(repository, service)]
        self.stack = QStackedWidget()
        for page in self.pages:
            self.stack.addWidget(page)
        horizontal.addWidget(self.stack, 1)
        self.setCentralWidget(container)
        self.navigation.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.navigation.setCurrentRow(0)
        self.pages[0].add_folder_requested.connect(self._add_first_folder)
        self.service.changed.connect(self.refresh)
        self.service.error.connect(self.show_error)
        self.service.operation_finished.connect(self.show_finished)

    @Slot()
    def _add_first_folder(self) -> None:
        self.navigation.setCurrentRow(2)
        self.pages[2]._add()

    @Slot()
    def refresh(self) -> None:
        for page in self.pages[:4]:
            page.refresh()

    @Slot(str)
    def show_error(self, message: str) -> None:
        QMessageBox.warning(self, "OrdenIA", message)

    @Slot(str)
    def show_finished(self, message: str) -> None:
        if self.repository.get_setting("show_notifications", "1") == "1":
            self.statusBar().showMessage(message, 8000)

    def closeEvent(self, event: QCloseEvent) -> None:
        self.service.close()
        super().closeEvent(event)
