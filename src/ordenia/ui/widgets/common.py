from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import QApplication, QAbstractItemView, QHeaderView, QLabel, QMainWindow, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget


def page(title: str, subtitle: str) -> tuple[QWidget, QVBoxLayout]:
    widget = QWidget()
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(28, 24, 28, 24)
    layout.setSpacing(16)
    heading = QLabel(title)
    heading.setObjectName("pageTitle")
    layout.addWidget(heading)
    detail = QLabel(subtitle)
    detail.setObjectName("muted")
    detail.setWordWrap(True)
    layout.addWidget(detail)
    return widget, layout


class PathTableWidget(QTableWidget):
    path_copied = Signal(str)

    def __init__(self, headers: list[str], path_columns: tuple[int, ...] = ()) -> None:
        super().__init__(0, len(headers))
        self.path_columns = frozenset(path_columns)
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self.cellEntered.connect(self._update_cursor)
        self.cellClicked.connect(self._copy_path)
        self.path_copied.connect(self._notify_copy)

    def _update_cursor(self, _row: int, column: int) -> None:
        if column in self.path_columns:
            self.viewport().setCursor(Qt.CursorShape.PointingHandCursor)
        else:
            self.viewport().unsetCursor()

    def leaveEvent(self, event: QEvent) -> None:
        self.viewport().unsetCursor()
        super().leaveEvent(event)

    def _copy_path(self, row: int, column: int) -> None:
        if column not in self.path_columns:
            return
        item = self.item(row, column)
        if item is None:
            return
        path = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(path, str):
            return
        QApplication.clipboard().setText(path)
        self.path_copied.emit(path)

    def _notify_copy(self, _path: str) -> None:
        window = self.window()
        if isinstance(window, QMainWindow):
            window.statusBar().showMessage("Ruta copiada al portapapeles", 3500)


def table(headers: list[str], path_columns: tuple[int, ...] = ()) -> PathTableWidget:
    widget = PathTableWidget(headers, path_columns)
    widget.setHorizontalHeaderLabels(headers)
    widget.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    widget.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    widget.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    widget.setAlternatingRowColors(True)
    widget.verticalHeader().hide()
    widget.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    widget.setShowGrid(False)
    return widget


def path_item(path: Path) -> QTableWidgetItem:
    item = QTableWidgetItem(str(path))
    item.setData(Qt.ItemDataRole.UserRole, str(path))
    item.setToolTip(str(path))
    return item


def display_date(value: str) -> str:
    try:
        return datetime.fromisoformat(value).strftime("%d/%m/%Y %H:%M")
    except ValueError:
        return value


def display_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"
