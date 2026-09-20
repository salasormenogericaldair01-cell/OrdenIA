from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QMessageBox, QPushButton, QTableWidgetItem, QVBoxLayout, QWidget

from ordenia.database.repositories import Repository
from ordenia.services.file_service import FileService
from ordenia.ui.widgets.common import display_date, display_size, page, path_item, table


class FilesPage(QWidget):
    def __init__(self, repository: Repository, service: FileService) -> None:
        super().__init__()
        self.repository = repository
        self.service = service
        content, layout = page("Archivos detectados", "Revisa las sugerencias antes de mover cualquier archivo.")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(content)
        self.table = table(["Nombre", "Categoría", "Extensión", "Tamaño", "Carpeta origen", "Fecha detectada", "Estado"], path_columns=(4,))
        layout.addWidget(self.table)
        actions = QHBoxLayout()
        self.organize_button = QPushButton("Organizar")
        self.organize_button.setObjectName("primaryButton")
        self.organize_button.clicked.connect(self._organize)
        ignore_button = QPushButton("Ignorar")
        ignore_button.clicked.connect(self._ignore)
        actions.addWidget(self.organize_button)
        actions.addWidget(ignore_button)
        actions.addStretch()
        layout.addLayout(actions)
        self.refresh()

    def _selected_id(self) -> int | None:
        row = self.table.currentRow()
        item = self.table.item(row, 0) if row >= 0 else None
        return int(item.data(Qt.ItemDataRole.UserRole)) if item else None

    def _organize(self) -> None:
        file_id = self._selected_id()
        if file_id is None:
            QMessageBox.information(self, "OrdenIA", "Selecciona un archivo pendiente.")
            return
        try:
            file = self.repository.get_file(file_id)
            if file.status != "Pendiente":
                raise ValueError("Selecciona un archivo pendiente.")
            destination = self.service.proposal(file)
        except Exception as exc:
            QMessageBox.warning(self, "OrdenIA", str(exc))
            return
        dialog = QMessageBox(self)
        dialog.setWindowTitle("Confirmar organización")
        dialog.setIcon(QMessageBox.Icon.Question)
        dialog.setText(f"Archivo: {file.name}\n\nCategoría: {file.category}\n\nDestino propuesto:\n{destination}")
        dialog.addButton("Cancelar", QMessageBox.ButtonRole.RejectRole)
        confirm = dialog.addButton("Organizar", QMessageBox.ButtonRole.AcceptRole)
        dialog.exec()
        if dialog.clickedButton() is confirm:
            self.service.organize(file_id)

    def _ignore(self) -> None:
        file_id = self._selected_id()
        if file_id is None:
            QMessageBox.information(self, "OrdenIA", "Selecciona un archivo.")
            return
        try:
            file = self.repository.get_file(file_id)
            if file.status != "Pendiente":
                raise ValueError("Solo se pueden ignorar archivos pendientes.")
            self.service.set_ignored(file_id)
        except Exception as exc:
            QMessageBox.warning(self, "OrdenIA", str(exc))

    def refresh(self) -> None:
        selected_id = self._selected_id()
        files = self.repository.list_files()
        self.table.setRowCount(len(files))
        for row, file in enumerate(files):
            values = [file.name, file.category, file.extension or "—", display_size(file.size), str(file.source_directory), display_date(file.detected_at), file.status]
            for column, value in enumerate(values):
                item = path_item(file.source_directory) if column == 4 else QTableWidgetItem(value)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, file.id)
                    item.setToolTip(file.name)
                self.table.setItem(row, column, item)
            if file.id == selected_id:
                self.table.setCurrentCell(row, 0)
