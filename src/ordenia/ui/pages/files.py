"""Searchable, paged file list with details and explicit actions."""

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QAbstractItemView, QCheckBox, QComboBox, QFrame, QGridLayout, QHeaderView, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMessageBox, QProgressBar, QPushButton, QSizePolicy, QSplitter, QTableWidgetItem, QVBoxLayout, QWidget

from ordenia.analysis.text_utils import preview
from ordenia.core.classifier import ExtensionClassifier
from ordenia.database.models import DetectedFile
from ordenia.database.repositories import Repository
from ordenia.platform.actions import open_file, open_location
from ordenia.services.file_service import FileService
from ordenia.ui.widgets.common import display_date, display_size, page, path_item, table
from ordenia.ui.widgets.file_inspector import FileInspector


class FilesPage(QWidget):
    PAGE_SIZE = 200
    SORT_FIELDS = ("name", "category", "extension", "size", "source_directory", "detected_at", "status", "modified_at")
    STATUS_FILTERS = {"Todos": "Todos", "Pendientes": "Pendiente", "Organizados": "Organizado", "Ignorados": "Ignorado"}
    ANALYSIS_LABELS = {"pending": "Pendiente", "analyzing": "Analizando", "indexed": "Indexado",
                       "unsupported": "No compatible", "no_text": "Sin texto", "failed": "Error",
                       "stale": "Desactualizado", "skipped": "Omitido"}
    AI_LABELS = {"pending": "Sin analizar", "analyzing": "Analizando", "ready": "Listo",
                 "failed": "Error", "stale": "Desactualizado", "unavailable": "IA local no disponible"}

    def __init__(self, repository: Repository, service: FileService) -> None:
        super().__init__()
        self.repository = repository
        self.service = service
        self.page_index = 0
        self.sort_field = "detected_at"
        self.descending = True
        self._content_job_id = 0
        self._content_cancelled = False
        self._ai_job_id = 0
        self._ai_cancelled = False
        self._ai_requested_file_id: int | None = None
        self._details_file_id: int | None = None
        content, layout = page("Archivos detectados", "Busca, filtra y revisa los detalles antes de mover archivos.")
        layout.setContentsMargins(20, 12, 20, 12)
        layout.setSpacing(8)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(content)

        self.workspace_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.workspace_splitter.setChildrenCollapsible(False)

        workspace = QFrame()
        workspace.setObjectName("filesWorkspace")
        workspace_layout = QVBoxLayout(workspace)
        workspace_layout.setContentsMargins(0, 0, 0, 0)
        workspace_layout.setSpacing(8)

        toolbar = QFrame()
        toolbar.setObjectName("toolbarCard")
        toolbar.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        toolbar_layout = QGridLayout(toolbar)
        toolbar_layout.setContentsMargins(12, 10, 12, 10)
        toolbar_layout.setSpacing(8)
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Buscar archivos...")
        self.search_box.setClearButtonEnabled(True)
        self.search_box.textChanged.connect(self._reset_and_refresh)
        self.search_name = QCheckBox("Nombre y ruta")
        self.search_name.setChecked(True)
        self.search_name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.search_name.toggled.connect(self._reset_and_refresh)
        self.search_content = QCheckBox("Contenido")
        self.search_content.setChecked(True)
        self.search_content.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.search_content.toggled.connect(self._reset_and_refresh)
        self.status_filter = QComboBox()
        self.status_filter.addItems(self.STATUS_FILTERS)
        self.status_filter.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.status_filter.setMinimumContentsLength(7)
        self.status_filter.currentTextChanged.connect(self._reset_and_refresh)
        self.category_filter = QComboBox()
        self.category_filter.addItems(("Todas", *ExtensionClassifier.CATEGORIES.keys(), "Otros"))
        self.category_filter.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.category_filter.setMinimumContentsLength(9)
        self.category_filter.currentTextChanged.connect(self._reset_and_refresh)
        clear = QPushButton("Limpiar filtros")
        clear.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        clear.clicked.connect(self.clear_filters)
        analyze_results = QPushButton("Analizar resultados")
        analyze_results.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        analyze_results.clicked.connect(self._analyze_filtered)
        toolbar_layout.addWidget(self.search_box, 0, 0, 1, 4)
        toolbar_layout.addWidget(self.search_name, 1, 0, 1, 2)
        toolbar_layout.addWidget(self.search_content, 1, 2, 1, 2)
        toolbar_layout.addWidget(QLabel("Estado"), 2, 0)
        toolbar_layout.addWidget(self.status_filter, 2, 1)
        toolbar_layout.addWidget(QLabel("Categoría"), 2, 2)
        toolbar_layout.addWidget(self.category_filter, 2, 3)
        toolbar_layout.addWidget(clear, 3, 0, 1, 2)
        toolbar_layout.addWidget(analyze_results, 3, 2, 1, 2)
        toolbar_layout.setColumnStretch(1, 1)
        toolbar_layout.setColumnStretch(3, 1)
        workspace_layout.addWidget(toolbar)

        self.table = table(["Nombre", "Categoría", "Extensión", "Tamaño", "Carpeta origen", "Fecha detectada", "Estado", "Última modificación", "Coincidencia"], path_columns=(4,))
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.horizontalHeader().sectionClicked.connect(self._sort_by_column)
        self.table.currentCellChanged.connect(self._update_details)
        self.table.cellDoubleClicked.connect(self._open_selected_file)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        for column, width in enumerate((210, 125, 80, 85, 180, 135, 95, 145, 220)):
            self.table.setColumnWidth(column, width)
        workspace_layout.addWidget(self.table, 1)

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
        workspace_layout.addWidget(self.result_label)
        pagination.addStretch()
        pagination.addWidget(previous)
        pagination.addWidget(self.page_label)
        pagination.addWidget(following)
        workspace_layout.addLayout(pagination)
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
        workspace_layout.addLayout(analysis_progress)
        self.service.content.progress.connect(self._on_content_progress)
        self.service.content.finished.connect(self._on_content_finished)
        ai_progress = QHBoxLayout()
        self.ai_progress_label = QLabel("")
        self.ai_progress_label.setObjectName("muted")
        self.ai_progress_label.hide()
        self.cancel_ai_button = QPushButton("Cancelar IA pendiente")
        self.cancel_ai_button.clicked.connect(self._cancel_ai)
        self.cancel_ai_button.hide()
        ai_progress.addWidget(self.ai_progress_label)
        ai_progress.addStretch()
        ai_progress.addWidget(self.cancel_ai_button)
        workspace_layout.addLayout(ai_progress)

        self.inspector = FileInspector()
        self.detail_values = self.inspector.detail_values
        self.analysis_values = self.inspector.analysis_values
        self.ai_values = self.inspector.ai_values
        self.content_preview = self.inspector.content_preview
        self.organize_button = self.inspector.organize_button
        self.analyze_button = self.inspector.analyze_button
        self.ai_analyze_button = self.inspector.ai_analyze_button
        self.ai_use_button = self.inspector.ai_use_button
        self.copy_suggestion_button = self.inspector.copy_suggestion_button
        self.ai_section = self.inspector.ai_section
        self.ai_heading = self.inspector.ai_heading
        self.details_scroll = self.inspector.details_scroll
        self.organize_button.clicked.connect(self._organize)
        self.inspector.ignore_button.clicked.connect(self._ignore)
        self.inspector.copy_path_button.clicked.connect(self._copy_selected_path)
        self.inspector.open_location_button.clicked.connect(self._open_location)
        self.analyze_button.clicked.connect(self._analyze_selected)
        self.ai_analyze_button.clicked.connect(self._analyze_ai)
        self.ai_use_button.clicked.connect(self._use_ai_suggestion)
        self.copy_suggestion_button.clicked.connect(self._copy_ai_suggestion)

        self.workspace_splitter.addWidget(workspace)
        self.workspace_splitter.addWidget(self.inspector)
        self.workspace_splitter.setStretchFactor(0, 3)
        self.workspace_splitter.setStretchFactor(1, 2)
        self.workspace_splitter.setSizes([780, 390])
        layout.addWidget(self.workspace_splitter, 1)

        self.service.ai.progress.connect(self._on_ai_progress)
        self.service.ai.changed.connect(self._on_ai_changed)
        self.service.ai.finished.connect(self._on_ai_finished)
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

    def _analyze_ai(self) -> None:
        file = self._selected_file()
        if file is None:
            QMessageBox.information(self, "OrdenIA", "Selecciona un archivo.")
            return
        record = self.repository.ai.get(file.id)
        self._ai_job_id = self.service.analyze_with_ai([file.id], force=record.status in {"stale", "failed"})
        if self._ai_job_id:
            self._ai_requested_file_id = file.id
            self._ai_cancelled = False
            self._on_ai_progress(self._ai_job_id, 0, 1)

    def _cancel_ai(self) -> None:
        if self._ai_job_id:
            self._ai_cancelled = True
            self.service.cancel_ai(self._ai_job_id)
            self.ai_progress_label.setText("Cancelando trabajos de IA pendientes...")

    def _on_ai_progress(self, job_id: int, done: int, total: int) -> None:
        if job_id != self._ai_job_id:
            return
        self.ai_progress_label.setText(f"Análisis inteligente: {done} / {total}")
        self.ai_progress_label.show()
        self.cancel_ai_button.show()

    def _on_ai_changed(self, file_id: int) -> None:
        if self._selected_id() != file_id:
            return
        self._update_details()
        if self.repository.ai.get(file_id).status == "ready":
            QTimer.singleShot(0, self._reveal_ai_section)

    def _on_ai_finished(self, job_id: int, done: int, total: int) -> None:
        if job_id != self._ai_job_id:
            return
        self.ai_progress_label.setText("Análisis inteligente cancelado." if self._ai_cancelled
                                       else f"Análisis inteligente terminado: {done} / {total}")
        self.ai_progress_label.show()
        self.cancel_ai_button.hide()
        self._ai_job_id = 0
        requested_file_id = self._ai_requested_file_id
        self._ai_requested_file_id = None
        self.refresh()
        if (not self._ai_cancelled and requested_file_id is not None
                and self._selected_id() == requested_file_id
                and self.repository.ai.get(requested_file_id).status == "ready"):
            QTimer.singleShot(0, self._reveal_ai_section)

    def _reveal_ai_section(self) -> None:
        """Switch the inspector to the completed intelligent-analysis result."""
        self.inspector.set_ai_ready(True)
        self.inspector.reveal_ai()

    def _scroll_details_to_top(self) -> None:
        self.inspector.show_details()

    def _copy_ai_suggestion(self) -> None:
        file = self._selected_file()
        if file is None:
            return
        record = self.repository.ai.get(file.id)
        if record.status != "ready" or record.suggestion is None:
            return
        QApplication.clipboard().setText(record.suggested_path)
        self.window().statusBar().showMessage("Sugerencia copiada al portapapeles", 3500)

    def _use_ai_suggestion(self) -> None:
        file = self._selected_file()
        if file is None:
            return
        record = self.repository.ai.get(file.id)
        if record.status != "ready" or record.suggestion is None:
            QMessageBox.information(self, "OrdenIA", "Primero genera una sugerencia válida con IA local.")
            return
        chosen, accepted = QInputDialog.getText(
            self, "Usar sugerencia", "Ruta relativa dentro del destino de OrdenIA:",
            text=record.suggested_path,
        )
        if not accepted:
            return
        try:
            destination = self.service.proposal(file, chosen)
        except Exception as exc:
            QMessageBox.warning(self, "OrdenIA", str(exc))
            return
        dialog = QMessageBox(self)
        dialog.setWindowTitle("Confirmar organización")
        dialog.setIcon(QMessageBox.Icon.Question)
        dialog.setText(
            f"Archivo: {file.name}\n\nRuta sugerida: {chosen}\n\nDestino propuesto:\n{destination}\n\n"
            "La IA solo propone. El archivo se moverá únicamente si confirmas."
        )
        dialog.addButton("Cancelar", QMessageBox.ButtonRole.RejectRole)
        confirm = dialog.addButton("Organizar", QMessageBox.ButtonRole.AcceptRole)
        dialog.exec()
        if dialog.clickedButton() is confirm:
            self.service.organize(file.id, relative_group=chosen, ai_suggested_path=record.suggested_path)

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
        selected_id = self._selected_id()
        selection_changed = selected_id != self._details_file_id
        self._details_file_id = selected_id
        file = self.repository.get_file(selected_id) if selected_id is not None else None
        if file is None:
            self.inspector.set_header(None)
            self.inspector.set_ai_ready(False)
            self.inspector.set_ai_content_present(False)
            for value in self.detail_values.values():
                value.setText("—")
                value.setToolTip("")
            for value in self.analysis_values.values():
                value.setText("—")
                value.setToolTip("")
            for value in self.ai_values.values():
                value.setText("—")
                value.setToolTip("")
            self.inspector.set_status_tone(self.detail_values["Estado"], "—")
            self.inspector.set_status_tone(self.analysis_values["Estado"], "—")
            self.inspector.set_status_tone(self.ai_values["Estado IA"], "—")
            self.content_preview.setText("")
            self.organize_button.setEnabled(False)
            self.inspector.ignore_button.setEnabled(False)
            self.inspector.copy_path_button.setEnabled(False)
            self.inspector.open_location_button.setEnabled(False)
            self.analyze_button.setEnabled(False)
            self.analyze_button.setText("Analizar contenido")
            self.ai_analyze_button.setEnabled(False)
            self.ai_analyze_button.setText("Analizar con IA")
            self.ai_use_button.setEnabled(False)
            self.copy_suggestion_button.setEnabled(False)
            if selection_changed:
                QTimer.singleShot(0, self._scroll_details_to_top)
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
        self.inspector.set_header(file.name, f"{file.category} · {display_size(file.size)}", file.status)
        self.inspector.set_status_tone(self.detail_values["Estado"], file.status)
        self.organize_button.setEnabled(file.status == "Pendiente")
        self.inspector.ignore_button.setEnabled(file.status == "Pendiente")
        self.inspector.copy_path_button.setEnabled(True)
        self.inspector.open_location_button.setEnabled(True)
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
        self.inspector.set_status_tone(self.analysis_values["Estado"], analysis_values["Estado"])
        self.content_preview.setText(("Vista previa: " + preview(analysis.text)) if analysis.text and analysis.status == "indexed"
                                     else analysis.error or "Aún no se ha analizado el contenido.")
        self.analyze_button.setEnabled(analysis.status != "analyzing")
        self.analyze_button.setText("Reanalizar" if analysis.status not in {"pending", "analyzing"} else "Analizar contenido")
        ai = self.repository.ai.get(file.id)
        confidence = "—"
        if ai.status in {"ready", "stale"}:
            confidence = "Alta" if ai.confidence >= 0.8 else "Media" if ai.confidence >= 0.5 else "Baja"
        show_ai_values = ai.status in {"ready", "stale"}
        ai_values = {
            "Estado IA": self.AI_LABELS.get(ai.status, ai.status),
            "Tipo": ai.document_type if show_ai_values else "—",
            "Tema": ai.topic if show_ai_values else "—",
            "Etiquetas": " · ".join(ai.tags) if show_ai_values and ai.tags else "—",
            "Destino sugerido IA": ai.suggested_path.replace("/", " / ") if show_ai_values else "—",
            "Confianza estimada": confidence, "Modelo": ai.model or "—",
            "Razón": ai.reason if show_ai_values else ai.error or "Aún no se ha solicitado una sugerencia.",
        }
        for label, value in ai_values.items():
            self.ai_values[label].setText(value)
            self.ai_values[label].setToolTip(value)
        self.inspector.set_ai_content_present(show_ai_values)
        self.inspector.set_status_tone(self.ai_values["Estado IA"], ai_values["Estado IA"])
        self.ai_analyze_button.setEnabled(ai.status != "analyzing")
        self.ai_analyze_button.setText("Volver a analizar" if ai.status in {"ready", "stale", "failed", "unavailable"}
                                       else "Analizar con IA")
        ready_suggestion = ai.status == "ready" and ai.suggestion is not None
        self.ai_use_button.setEnabled(ready_suggestion and file.status == "Pendiente")
        self.copy_suggestion_button.setEnabled(ready_suggestion)
        self.inspector.set_ai_ready(ready_suggestion)
        if selection_changed:
            QTimer.singleShot(0, self._scroll_details_to_top)

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
