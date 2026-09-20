"""Watched folder configuration and existing-file scan controls."""

from pathlib import Path

from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QLabel, QMessageBox, QProgressBar, QPushButton, QTableWidgetItem, QVBoxLayout, QWidget

from ordenia.database.repositories import Repository
from ordenia.database.models import ScanSummary
from ordenia.services.file_service import FileService
from ordenia.ui.widgets.common import page, table
from ordenia.ui.widgets.folder_options import FolderOptionsDialog


class FoldersPage(QWidget):
    def __init__(self, repository: Repository, service: FileService) -> None:
        super().__init__()
        self.repository = repository
        self.service = service
        content, layout = page("Carpetas vigiladas", "Configura la vigilancia, el escaneo y el destino de cada carpeta.")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(content)
        self.table = table(["Carpeta", "Estado", "Subcarpetas", "Destino", "Archivos registrados"])
        layout.addWidget(self.table)
        actions = QHBoxLayout()
        add = QPushButton("Añadir carpeta")
        add.setObjectName("primaryButton")
        add.clicked.connect(self._add)
        edit = QPushButton("Editar")
        edit.clicked.connect(self._edit)
        toggle = QPushButton("Activar / desactivar")
        toggle.clicked.connect(self._toggle)
        scan = QPushButton("Analizar ahora")
        scan.clicked.connect(self._scan)
        remove = QPushButton("Eliminar de la lista")
        remove.clicked.connect(self._remove)
        for button in (add, edit, toggle, scan, remove):
            actions.addWidget(button)
        actions.addStretch()
        layout.addLayout(actions)
        self.progress_label = QLabel("")
        self.progress_label.setObjectName("muted")
        self.progress_label.hide()
        self.progress = QProgressBar()
        self.progress.hide()
        layout.addWidget(self.progress_label)
        layout.addWidget(self.progress)
        self.service.scan_offer.connect(self._offer_scan)
        self.service.scan_count_finished.connect(self._count_finished)
        self.service.scan_progress.connect(self._scan_progress)
        self.service.scan_finished.connect(self._scan_finished)
        self.service.scan_failed.connect(self._scan_failed)
        self.refresh()

    def _selected_id(self) -> int | None:
        row = self.table.currentRow()
        item = self.table.item(row, 0) if row >= 0 else None
        return int(item.data(Qt.ItemDataRole.UserRole)) if item else None

    def _add(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "Seleccionar carpeta")
        if not selected:
            return
        path = Path(selected).resolve()
        if any(folder.path == path for folder in self.repository.list_folders()):
            QMessageBox.information(self, "OrdenIA", "Esta carpeta ya está en la lista. Puedes editarla o analizarla ahora.")
            return
        dialog = FolderOptionsDialog(path, parent=self)
        if not dialog.exec():
            return
        try:
            self.progress_label.setText("Contando archivos existentes...")
            self.progress_label.show()
            self.service.add_folder(path, *dialog.values())
        except Exception as exc:
            self.progress_label.hide()
            QMessageBox.warning(self, "OrdenIA", str(exc))

    def _edit(self) -> None:
        folder_id = self._selected_id()
        if folder_id is None:
            QMessageBox.information(self, "OrdenIA", "Selecciona una carpeta.")
            return
        folder = self.repository.get_folder(folder_id)
        dialog = FolderOptionsDialog(folder.path, folder, self)
        if dialog.exec():
            try:
                self.service.update_folder(folder.id, *dialog.values())
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

    def _scan(self) -> None:
        folder_id = self._selected_id()
        if folder_id is None:
            QMessageBox.information(self, "OrdenIA", "Selecciona una carpeta.")
            return
        self.progress_label.setText("Preparando análisis...")
        self.progress_label.show()
        self.service.scan_folder(folder_id)

    def _remove(self) -> None:
        folder_id = self._selected_id()
        if folder_id is None:
            QMessageBox.information(self, "OrdenIA", "Selecciona una carpeta.")
            return
        try:
            self.service.remove_folder(folder_id)
        except Exception as exc:
            QMessageBox.warning(self, "OrdenIA", str(exc))

    @Slot(int, int, bool)
    def _offer_scan(self, folder_id: int, count: int, remove_on_cancel: bool) -> None:
        self.progress_label.hide()
        dialog = QMessageBox(self)
        dialog.setWindowTitle("Archivos existentes")
        dialog.setText(f"Se encontraron {count} archivos existentes.\n\n¿Deseas analizarlos? OrdenIA solamente los registrará y clasificará. No moverá ningún archivo.")
        cancel = dialog.addButton("Cancelar", QMessageBox.ButtonRole.RejectRole)
        dialog.addButton("Omitir", QMessageBox.ButtonRole.ActionRole)
        analyze = dialog.addButton("Analizar archivos", QMessageBox.ButtonRole.AcceptRole)
        dialog.exec()
        if dialog.clickedButton() is analyze:
            self.service.scan_folder(folder_id)
        elif dialog.clickedButton() is cancel and remove_on_cancel:
            self.service.remove_folder(folder_id)

    @Slot(int, int)
    def _count_finished(self, _folder_id: int, count: int) -> None:
        self.progress_label.hide()
        if count == 0:
            self.progress_label.setText("No hay archivos existentes elegibles en esta carpeta.")
            self.progress_label.show()

    @Slot(int, int, int)
    def _scan_progress(self, folder_id: int, current: int, total: int) -> None:
        folder = self.repository.get_folder(folder_id)
        self.progress_label.setText(f"Analizando {folder.path.name}: {current} / {total}")
        self.progress_label.show()
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(current)
        self.progress.show()

    @Slot(int, object)
    def _scan_finished(self, folder_id: int, summary: ScanSummary) -> None:
        folder = self.repository.get_folder(folder_id)
        self.progress_label.setText(f"Análisis terminado en {folder.path.name}: {summary.found} encontrados; {summary.new} nuevos; {summary.existing} ya registrados; {summary.active} activos; {summary.missing} ausentes; {summary.excluded} excluidos.")
        self.progress.hide()
        self.refresh()

    @Slot(int, str)
    def _scan_failed(self, _folder_id: int, _message: str) -> None:
        self.progress.hide()
        self.progress_label.setText("No se pudo completar el análisis.")
        self.progress_label.show()

    def refresh(self) -> None:
        selected_id = self._selected_id()
        folders = self.repository.list_folders()
        counts = self.repository.registered_counts()
        self.table.setRowCount(len(folders))
        for row, folder in enumerate(folders):
            path_item = QTableWidgetItem(str(folder.path))
            path_item.setToolTip(str(folder.path))
            path_item.setData(Qt.ItemDataRole.UserRole, folder.id)
            self.table.setItem(row, 0, path_item)
            state = "Pausada" if not folder.enabled else "Activa" if folder.path.is_dir() else "No disponible"
            destination = self.service.destination_for(folder)
            values = (state, "Sí" if folder.include_subfolders else "No", str(destination), str(counts.get(folder.id, 0)))
            for column, value in enumerate(values, start=1):
                item = QTableWidgetItem(value)
                if column == 3:
                    item.setToolTip(str(destination))
                elif column == 4:
                    states = self.repository.index_counts(folder.id)
                    item.setToolTip(f"{states.get('active', 0)} activos; {states.get('missing', 0)} ausentes; {states.get('excluded', 0)} excluidos")
                self.table.setItem(row, column, item)
            if folder.id == selected_id:
                self.table.setCurrentCell(row, 0)
