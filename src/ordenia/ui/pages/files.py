"""Searchable, paged file list with details and explicit actions."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QAbstractItemView, QCheckBox, QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QProgressBar, QPushButton, QScrollArea, QSplitter, QTableWidgetItem, QVBoxLayout, QWidget

from ordenia.analysis.text_utils import preview
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
    ANALYSIS_LABELS = {"pending": "Pendiente", "analyzing": "Analizando", "indexed": "Indexado",
                       "unsupported": "No compatible", "no_text": "Sin texto", "failed": "Error",
                       "stale": "Desactualizado", "skipped": "Omitido"}

    def __init__(self, repository: Repository, service: FileService) -> None:
        super().__init__()
        self.repository = repository
        self.service = service
        self.page_index = 0
        self.sort_field = "detected_at"
        self.descending = True
        self._content_job_id = 0
        self._content_cancelled = False
        content, layout = page("Archivos detectados", "Busca, filtra y revisa los detalles antes de mover archivos.")
        layout.setContentsMargins(20, 12, 20, 12)
        layout.setSpacing(8)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(content)

        controls = QHBoxLayout()
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Buscar archivos...")
        self.search_box.setClearButtonEnabled(True)
        self.search_box.textChanged.connect(self._reset_and_refresh)
        self.search_name = QCheckBox("Nombre y ruta")
        self.search_name.setChecked(True)
        self.search_name.toggled.connect(self._reset_and_refresh)
        self.search_content = QCheckBox("Contenido")
        self.search_content.setChecked(True)
        self.search_content.toggled.connect(self._reset_and_refresh)
        self.status_filter = QComboBox()
        self.status_filter.addItems(self.STATUS_FILTERS)
        self.status_filter.currentTextChanged.connect(self._reset_and_refresh)
        self.category_filter = QComboBox()
        self.category_filter.addItems(("Todas", *ExtensionClassifier.CATEGORIES.keys(), "Otros"))
        self.category_filter.currentTextChanged.connect(self._reset_and_refresh)
        clear = QPushButton("Limpiar filtros")
        clear.clicked.connect(self.clear_filters)
        analyze_results = QPushButton("Analizar resultados")
        analyze_results.clicked.connect(self._analyze_filtered)
        controls.addWidget(self.search_box, 2)
        controls.addWidget(self.search_name)
        controls.addWidget(self.search_content)
        layout.addLayout(controls)
        filters = QGridLayout()
        filters.addWidget(QLabel("Estado:"), 0, 0)
        filters.addWidget(self.status_filter, 0, 1)
        filters.addWidget(QLabel("Categoría:"), 1, 0)
        filters.addWidget(self.category_filter, 1, 1)
        filters.addWidget(clear, 2, 0)
        filters.addWidget(analyze_results, 2, 1)
        filters.setColumnStretch(1, 1)
        filters.setVerticalSpacing(4)
        layout.addLayout(filters)

        self.table = table(["Nombre", "Categoría", "Extensión", "Tamaño", "Carpeta origen", "Fecha detectada", "Estado", "Última modificación", "Coincidencia"], path_columns=(4,))
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
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
            grid.addWidget(title, index, 0)
            grid.addWidget(value, index, 1)
            self.detail_values[label] = value
        details_layout.addLayout(grid)
        content_heading = QLabel("ANÁLISIS DE CONTENIDO")
        content_heading.setObjectName("sectionTitle")
        details_layout.addWidget(content_heading)
        content_grid = QGridLayout()
        self.analysis_values: dict[str, QLabel] = {}
        for index, label in enumerate(("Estado", "Extractor", "Analizado", "Páginas", "Diapositivas", "Caracteres", "Título", "Autor", "Asunto", "Palabras clave detectadas")):
            title = QLabel(label + ":")
            title.setObjectName("muted")
            value = QLabel("—")
            value.setWordWrap(True)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            content_grid.addWidget(title, index, 0)
            content_grid.addWidget(value, index, 1)
            self.analysis_values[label] = value
        details_layout.addLayout(content_grid)
        self.content_preview = QLabel("")
        self.content_preview.setWordWrap(True)
        self.content_preview.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        details_layout.addWidget(self.content_preview)
        actions = QGridLayout()
        self.organize_button = QPushButton("Organizar")
        self.organize_button.setObjectName("primaryButton")
        self.organize_button.clicked.connect(self._organize)
        ignore = QPushButton("Ignorar")
        ignore.clicked.connect(self._ignore)
        copy = QPushButton("Copiar ruta")
        copy.clicked.connect(self._copy_selected_path)
        locate = QPushButton("Abrir ubicación")
        locate.clicked.connect(self._open_location)
        self.analyze_button = QPushButton("Analizar contenido")
        self.analyze_button.clicked.connect(self._analyze_selected)
        for row, column, button in ((0, 0, self.organize_button), (0, 1, ignore),
                                    (1, 0, copy), (1, 1, locate)):
            actions.addWidget(button, row, column)
        actions.addWidget(self.analyze_button, 2, 0, 1, 2)
        actions.setColumnStretch(0, 1)
        actions.setColumnStretch(1, 1)
        details_layout.addLayout(actions)
        details_scroll = QScrollArea()
        details_scroll.setWidgetResizable(True)
        details_scroll.setFrameShape(QFrame.Shape.NoFrame)
        details_scroll.setWidget(details)
        splitter.addWidget(details_scroll)
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
        layout.addWidget(self.result_label)
        pagination.addStretch()
        pagination.addWidget(previous)
        pagination.addWidget(self.page_label)
        pagination.addWidget(following)
        layout.addLayout(pagination)
        analysis_progress = QHBoxLayout()
        self.content_progress_label = QLabel("")
        self.content_progress_label.setObjectName("muted")
        self.content_progress = QProgressBar()
        self.content_progress.hide()
        self.cancel_content_button = QPushButton("Cancelar análisis")
        self.cancel_content_button.clicked.connect(self._cancel_content)
        self.cancel_content_button.hide()
        analysis_progress.addWidget(self.content_progress_label)
        analysis_progress.addWidget(self.content_progress, 1)
        analysis_progress.addWidget(self.cancel_content_button)
        layout.addLayout(analysis_progress)
        self.service.content.progress.connect(self._on_content_progress)
        self.service.content.finished.connect(self._on_content_finished)
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
        if column >= len(self.SORT_FIELDS):
            return
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

    def _analyze_selected(self) -> None:
        rows = sorted({index.row() for index in self.table.selectedIndexes()})
        ids = [int(self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)) for row in rows if self.table.item(row, 0)]
        if not ids:
            file = self._selected_file()
            ids = [file.id] if file else []
        if not ids:
            QMessageBox.information(self, "OrdenIA", "Selecciona uno o varios archivos.")
            return
        self._content_job_id = self.service.analyze_content(ids, force=True)
        if self._content_job_id:
            self._content_cancelled = False
            self._on_content_progress(self._content_job_id, 0, len(ids))

    def _analyze_filtered(self) -> None:
        ids = self.repository.matching_file_ids(
            self.search_box.text(), self.STATUS_FILTERS[self.status_filter.currentText()],
            self.category_filter.currentText(), self.search_name.isChecked(), self.search_content.isChecked())
        if not ids:
            QMessageBox.information(self, "OrdenIA", "No hay archivos en los resultados actuales.")
            return
        self._content_job_id = self.service.analyze_content(ids)
        if self._content_job_id:
            self._content_cancelled = False
            self._on_content_progress(self._content_job_id, 0, len(ids))

    def _cancel_content(self) -> None:
        if self._content_job_id:
            self._content_cancelled = True
            self.service.cancel_content(self._content_job_id)
            self.content_progress_label.setText("Cancelando después del archivo actual...")

    def _on_content_progress(self, job_id: int, done: int, total: int) -> None:
        if job_id != self._content_job_id:
            return
        self.content_progress_label.setText(f"Analizando contenido: {done} / {total}")
        self.content_progress.setRange(0, max(1, total))
        self.content_progress.setValue(done)
        self.content_progress.show()
        self.cancel_content_button.show()

    def _on_content_finished(self, job_id: int, done: int, total: int) -> None:
        if job_id == self._content_job_id:
            self.content_progress_label.setText("Análisis cancelado; los trabajos restantes no se iniciaron." if self._content_cancelled
                                                else f"Análisis de contenido terminado: {done} / {total}")
            self.content_progress.hide()
            self.cancel_content_button.hide()
            self._content_job_id = 0
            self.refresh()

    def _update_details(self, *_args) -> None:
        file = self._selected_file()
        if file is None:
            for value in self.detail_values.values():
                value.setText("—")
            for value in self.analysis_values.values():
                value.setText("—")
            self.content_preview.setText("")
            self.organize_button.setEnabled(False)
            self.analyze_button.setEnabled(False)
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
        self.analyze_button.setEnabled(True)
        analysis = self.repository.content.get(file.id)
        analysis_values = {
            "Estado": self.ANALYSIS_LABELS.get(analysis.status, analysis.status),
            "Extractor": analysis.extractor or "—", "Analizado": display_date(analysis.analyzed_at) if analysis.analyzed_at else "—",
            "Páginas": str(analysis.page_count) if analysis.page_count is not None else "—",
            "Diapositivas": str(analysis.slide_count) if analysis.slide_count is not None else "—",
            "Caracteres": f"{analysis.character_count:,}".replace(",", "."),
            "Título": analysis.title or "—", "Autor": analysis.author or "—", "Asunto": analysis.subject or "—",
            "Palabras clave detectadas": ", ".join(analysis.keywords) or "—",
        }
        for label, value in analysis_values.items():
            self.analysis_values[label].setText(value)
            self.analysis_values[label].setToolTip(value)
        self.content_preview.setText(("Vista previa: " + preview(analysis.text)) if analysis.text and analysis.status == "indexed"
                                     else analysis.error or "Aún no se ha analizado el contenido.")
        self.analyze_button.setText("Reanalizar" if analysis.status not in {"pending", "analyzing"} else "Analizar contenido")

    def refresh(self) -> None:
        selected_id = self._selected_id()
        files, total = self.repository.search_files(
            self.search_box.text(), self.STATUS_FILTERS[self.status_filter.currentText()],
            self.category_filter.currentText(), self.sort_field, self.descending,
            self.PAGE_SIZE, self.page_index * self.PAGE_SIZE,
            self.search_name.isChecked(), self.search_content.isChecked(),
        )
        if not files and total and self.page_index:
            self.page_index = (total - 1) // self.PAGE_SIZE
            return self.refresh()
        self.table.blockSignals(True)
        try:
            self.table.setRowCount(len(files))
            selected_row = -1
            for row, file in enumerate(files):
                values = [file.name, file.category, file.extension or "—", display_size(file.size), str(file.source_directory), display_date(file.detected_at), file.status, display_date(file.modified_at), file.match_snippet]
                for column, value in enumerate(values):
                    item = path_item(file.source_directory) if column == 4 else QTableWidgetItem(value)
                    if column == 0:
                        item.setData(Qt.ItemDataRole.UserRole, file.id)
                        item.setToolTip(file.name)
                    if column == 8 and file.match_snippet:
                        item.setToolTip(file.match_snippet)
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
