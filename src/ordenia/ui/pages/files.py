"""Searchable, paged file list with details and explicit actions."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QSplitter, QTableWidgetItem, QVBoxLayout, QWidget

from ordenia.core.classifier import ExtensionClassifier
from ordenia.database.models import DetectedFile
from ordenia.database.repositories import Repository
from ordenia.platform.actions import open_file, open_location
from ordenia.services.file_service import FileService
from ordenia.ui.widgets.common import display_date, display_size, page, path_item, table


class FilesPage(QWidget):
    PAGE_SIZE = 200
    SORT_FIELDS = ("name", "category", "extension", "size", "source_directory", "detected_at", "status", "modified_at")
    STATUS_FILTERS = {"Todos": "Todos", "Pendientes": "Pendiente", "Organizados": "Organizado", "Ignorados": "Ignorado"}

    def __init__(self, repository: Repository, service: FileService) -> None:
        super().__init__()
        self.repository = repository
        self.service = service
        self.page_index = 0
        self.sort_field = "detected_at"
        self.descending = True
        content, layout = page("Archivos detectados", "Busca, filtra y revisa los detalles antes de mover archivos.")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(content)

        controls = QHBoxLayout()
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Buscar archivos...")
        self.search_box.setClearButtonEnabled(True)
        self.search_box.textChanged.connect(self._reset_and_refresh)
        self.status_filter = QComboBox()
        self.status_filter.addItems(self.STATUS_FILTERS)
        self.status_filter.currentTextChanged.connect(self._reset_and_refresh)
        self.category_filter = QComboBox()
        self.category_filter.addItems(("Todas", *ExtensionClassifier.CATEGORIES.keys(), "Otros"))
        self.category_filter.currentTextChanged.connect(self._reset_and_refresh)
        clear = QPushButton("Limpiar filtros")
        clear.clicked.connect(self.clear_filters)
        controls.addWidget(self.search_box, 2)
        controls.addWidget(self.status_filter)
        controls.addWidget(self.category_filter)
        controls.addWidget(clear)
        layout.addLayout(controls)

        self.table = table(["Nombre", "Categoría", "Extensión", "Tamaño", "Carpeta origen", "Fecha detectada", "Estado", "Última modificación"], path_columns=(4,))
        self.table.horizontalHeader().sectionClicked.connect(self._sort_by_column)
        self.table.currentCellChanged.connect(self._update_details)
        self.table.cellDoubleClicked.connect(self._open_selected_file)
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self.table)

        details = QFrame()
        details.setObjectName("detailsCard")
        details_layout = QVBoxLayout(details)
        heading = QLabel("Detalles del archivo")
        heading.setObjectName("sectionTitle")
        details_layout.addWidget(heading)
        grid = QGridLayout()
        self.detail_values: dict[str, QLabel] = {}
        labels = ("Nombre completo", "Tipo", "Categoría", "Tamaño", "Ruta completa", "Carpeta origen", "Fecha detectada", "Última modificación", "Estado", "Destino sugerido")
        for index, label in enumerate(labels):
            title = QLabel(label + ":")
            title.setObjectName("muted")
            value = QLabel("—")
            value.setWordWrap(True)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            grid.addWidget(title, index // 2, (index % 2) * 2)
            grid.addWidget(value, index // 2, (index % 2) * 2 + 1)
            self.detail_values[label] = value
        details_layout.addLayout(grid)
        actions = QHBoxLayout()
        self.organize_button = QPushButton("Organizar")
        self.organize_button.setObjectName("primaryButton")
        self.organize_button.clicked.connect(self._organize)
        ignore = QPushButton("Ignorar")
        ignore.clicked.connect(self._ignore)
        copy = QPushButton("Copiar ruta")
        copy.clicked.connect(self._copy_selected_path)
        locate = QPushButton("Abrir ubicación")
        locate.clicked.connect(self._open_location)
        for button in (self.organize_button, ignore, copy, locate):
            actions.addWidget(button)
        actions.addStretch()
        details_layout.addLayout(actions)
        splitter.addWidget(details)
        splitter.setSizes([430, 250])
        layout.addWidget(splitter, 1)

        pagination = QHBoxLayout()
        self.result_label = QLabel("")
        self.result_label.setObjectName("muted")
        self.page_label = QLabel("")
        previous = QPushButton("Anterior")
        previous.clicked.connect(lambda: self._change_page(-1))
        following = QPushButton("Siguiente")
        following.clicked.connect(lambda: self._change_page(1))
        self.previous_button = previous
        self.next_button = following
        pagination.addWidget(self.result_label)
        pagination.addStretch()
        pagination.addWidget(previous)
        pagination.addWidget(self.page_label)
        pagination.addWidget(following)
        layout.addLayout(pagination)
        self.refresh()

    def _selected_id(self) -> int | None:
        row = self.table.currentRow()
        item = self.table.item(row, 0) if row >= 0 else None
        return int(item.data(Qt.ItemDataRole.UserRole)) if item else None

    def _selected_file(self) -> DetectedFile | None:
        file_id = self._selected_id()
        return self.repository.get_file(file_id) if file_id is not None else None

    def _reset_and_refresh(self, *_args) -> None:
        self.page_index = 0
        self.refresh()

    def _sort_by_column(self, column: int) -> None:
        field = self.SORT_FIELDS[column]
        self.descending = not self.descending if self.sort_field == field else False
        self.sort_field = field
        self.page_index = 0
        self.refresh()

    def _change_page(self, direction: int) -> None:
        self.page_index = max(0, self.page_index + direction)
        self.refresh()

    def clear_filters(self) -> None:
        self.search_box.clear()
        self.status_filter.setCurrentIndex(0)
        self.category_filter.setCurrentIndex(0)
        self._reset_and_refresh()

    def _organize(self) -> None:
        file = self._selected_file()
        if file is None:
            QMessageBox.information(self, "OrdenIA", "Selecciona un archivo pendiente.")
            return
        try:
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
            self.service.organize(file.id)

    def _ignore(self) -> None:
        file = self._selected_file()
        if file is None:
            QMessageBox.information(self, "OrdenIA", "Selecciona un archivo.")
            return
        try:
            if file.status != "Pendiente":
                raise ValueError("Solo se pueden ignorar archivos pendientes.")
            self.service.set_ignored(file.id)
        except Exception as exc:
            QMessageBox.warning(self, "OrdenIA", str(exc))

    def _copy_selected_path(self) -> None:
        file = self._selected_file()
        if file is not None:
            QApplication.clipboard().setText(str(file.path))
            self.window().statusBar().showMessage("Ruta copiada al portapapeles", 3500)

    def _open_location(self) -> None:
        file = self._selected_file()
        if file is not None:
            try:
                open_location(file.path)
            except Exception as exc:
                QMessageBox.warning(self, "OrdenIA", str(exc))

    def _open_selected_file(self, *_args) -> None:
        file = self._selected_file()
        if file is None:
            return
        if file.extension.lower() in {".exe", ".msi"}:
            answer = QMessageBox.question(self, "Confirmar apertura", f"¿Deseas abrir el instalador {file.name}?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return
        try:
            open_file(file.path)
        except Exception as exc:
            QMessageBox.warning(self, "OrdenIA", str(exc))

    def _update_details(self, *_args) -> None:
        file = self._selected_file()
        if file is None:
            for value in self.detail_values.values():
                value.setText("—")
            self.organize_button.setEnabled(False)
            return
        destination = "—"
        if file.status == "Pendiente":
            try:
                destination = str(self.service.proposal(file))
            except (OSError, ValueError):
                destination = "No disponible"
        values = {
            "Nombre completo": file.name, "Tipo": file.extension or "Sin extensión",
            "Categoría": file.category, "Tamaño": display_size(file.size),
            "Ruta completa": str(file.path), "Carpeta origen": str(file.source_directory),
            "Fecha detectada": display_date(file.detected_at),
            "Última modificación": display_date(file.modified_at),
            "Estado": file.status, "Destino sugerido": destination,
        }
        for label, value in values.items():
            self.detail_values[label].setText(value)
            self.detail_values[label].setToolTip(value)
        self.organize_button.setEnabled(file.status == "Pendiente")

    def refresh(self) -> None:
        selected_id = self._selected_id()
        files, total = self.repository.search_files(
            self.search_box.text(), self.STATUS_FILTERS[self.status_filter.currentText()],
            self.category_filter.currentText(), self.sort_field, self.descending,
            self.PAGE_SIZE, self.page_index * self.PAGE_SIZE,
        )
        if not files and total and self.page_index:
            self.page_index = (total - 1) // self.PAGE_SIZE
            return self.refresh()
        self.table.blockSignals(True)
        try:
            self.table.setRowCount(len(files))
            selected_row = -1
            for row, file in enumerate(files):
                values = [file.name, file.category, file.extension or "—", display_size(file.size), str(file.source_directory), display_date(file.detected_at), file.status, display_date(file.modified_at)]
                for column, value in enumerate(values):
                    item = path_item(file.source_directory) if column == 4 else QTableWidgetItem(value)
                    if column == 0:
                        item.setData(Qt.ItemDataRole.UserRole, file.id)
                        item.setToolTip(file.name)
                    self.table.setItem(row, column, item)
                if file.id == selected_id:
                    selected_row = row
            if selected_row >= 0:
                self.table.setCurrentCell(selected_row, 0)
            else:
                self.table.clearSelection()
                self.table.setCurrentCell(-1, -1)
        finally:
            self.table.blockSignals(False)
        self._update_details()
        first = self.page_index * self.PAGE_SIZE + 1 if total else 0
        last = self.page_index * self.PAGE_SIZE + len(files)
        self.result_label.setText(f"Mostrando {first}–{last} de {total} archivos")
        self.page_label.setText(str(self.page_index + 1))
        self.previous_button.setEnabled(self.page_index > 0)
        self.next_button.setEnabled(last < total)
