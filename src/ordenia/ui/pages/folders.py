from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QMessageBox, QPushButton, QTableWidgetItem, QVBoxLayout, QWidget

from ordenia.database.repositories import Repository
from ordenia.services.file_service import FileService
from ordenia.ui.widgets.common import page, table


class FoldersPage(QWidget):
    def __init__(self, repository: Repository, service: FileService) -> None:
        super().__init__()
        self.repository = repository
        self.service = service
        content, layout = page("Carpetas vigiladas", "Los archivos nuevos o modificados se analizan sin moverlos automáticamente.")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(content)
        self.table = table(["Carpeta", "Vigilancia"])
        layout.addWidget(self.table)
        actions = QHBoxLayout()
        add = QPushButton("Añadir carpeta")
        add.setObjectName("primaryButton")
        add.clicked.connect(self._add)
        toggle = QPushButton("Activar / desactivar")
        toggle.clicked.connect(self._toggle)
        remove = QPushButton("Eliminar de la lista")
        remove.clicked.connect(self._remove)
        actions.addWidget(add)
        actions.addWidget(toggle)
        actions.addWidget(remove)
        actions.addStretch()
        layout.addLayout(actions)
        self.refresh()

    def _selected_id(self) -> int | None:
        row = self.table.currentRow()
        item = self.table.item(row, 0) if row >= 0 else None
        return int(item.data(Qt.ItemDataRole.UserRole)) if item else None

    def _add(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "Seleccionar carpeta")
        if selected:
            try:
                self.service.add_folder(Path(selected))
            except Exception as exc:
                QMessageBox.warning(self, "OrdenIA", str(exc))

    def _toggle(self) -> None:
        folder_id = self._selected_id()
        if folder_id is None:
            QMessageBox.information(self, "OrdenIA", "Selecciona una carpeta.")
            return
        try:
            folder = self.repository.get_folder(folder_id)
            self.service.set_folder_enabled(folder_id, not folder.enabled)
        except Exception as exc:
            QMessageBox.warning(self, "OrdenIA", str(exc))

    def _remove(self) -> None:
        folder_id = self._selected_id()
        if folder_id is None:
            QMessageBox.information(self, "OrdenIA", "Selecciona una carpeta.")
            return
        try:
            self.service.remove_folder(folder_id)
        except Exception as exc:
            QMessageBox.warning(self, "OrdenIA", str(exc))

    def refresh(self) -> None:
        folders = self.repository.list_folders()
        self.table.setRowCount(len(folders))
        for row, folder in enumerate(folders):
            path_item = QTableWidgetItem(str(folder.path))
            path_item.setData(Qt.ItemDataRole.UserRole, folder.id)
            self.table.setItem(row, 0, path_item)
            state = "Pausada" if not folder.enabled else "Activa" if folder.path.is_dir() else "No disponible"
            self.table.setItem(row, 1, QTableWidgetItem(state))
