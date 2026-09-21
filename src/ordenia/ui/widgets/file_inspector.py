"""Compact tabbed inspector used by the detected-files page."""

from collections.abc import Iterable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)


DETAIL_FIELDS = (
    "Nombre completo", "Tipo", "Categoría", "Tamaño", "Ruta completa",
    "Carpeta origen", "Fecha detectada", "Última modificación", "Estado",
    "Destino sugerido",
)
CONTENT_FIELDS = (
    "Estado", "Extractor", "Analizado", "Páginas", "Diapositivas",
    "Caracteres", "Título", "Autor", "Asunto", "Palabras clave detectadas",
)
AI_FIELDS = (
    "Estado IA", "Tipo", "Tema", "Etiquetas", "Destino sugerido IA",
    "Confianza estimada", "Modelo", "Razón",
)


def _field_grid(labels: Iterable[str], *, status_field: str = "") -> tuple[QGridLayout, dict[str, QLabel]]:
    grid = QGridLayout()
    grid.setHorizontalSpacing(14)
    grid.setVerticalSpacing(10)
    grid.setColumnStretch(1, 1)
    values: dict[str, QLabel] = {}
    for row, label in enumerate(labels):
        title = QLabel(label)
        title.setObjectName("fieldLabel")
        title.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        value = QLabel("—")
        value.setObjectName("statusBadge" if label == status_field else "fieldValue")
        value.setWordWrap(True)
        value.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        grid.addWidget(title, row, 0)
        grid.addWidget(value, row, 1)
        values[label] = value
    return grid, values


def _scroll_page() -> tuple[QScrollArea, QWidget, QVBoxLayout]:
    scroll = QScrollArea()
    scroll.setObjectName("inspectorScroll")
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    body = QWidget()
    body.setObjectName("inspectorBody")
    layout = QVBoxLayout(body)
    layout.setContentsMargins(14, 14, 14, 14)
    layout.setSpacing(14)
    scroll.setWidget(body)
    return scroll, body, layout


class FileInspector(QFrame):
    """Right-hand inspector with focused Details, Content and AI views."""

    DETAILS_TAB = 0
    CONTENT_TAB = 1
    AI_TAB = 2

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("inspectorPanel")
        self.setMinimumWidth(240)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QFrame()
        header.setObjectName("inspectorHeader")
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(16, 14, 16, 12)
        header_layout.setSpacing(5)
        eyebrow = QLabel("ARCHIVO SELECCIONADO")
        eyebrow.setObjectName("eyebrow")
        header_layout.addWidget(eyebrow)
        self.selected_name = QLabel("Selecciona un archivo")
        self.selected_name.setObjectName("inspectorTitle")
        self.selected_name.setWordWrap(True)
        self.selected_name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.selected_name.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        header_layout.addWidget(self.selected_name)
        meta_row = QHBoxLayout()
        self.file_status = QLabel("Sin selección")
        self.file_status.setObjectName("statusBadge")
        self.selected_meta = QLabel("Elige una fila para consultar sus detalles.")
        self.selected_meta.setObjectName("inspectorMeta")
        self.selected_meta.setWordWrap(True)
        self.selected_meta.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        meta_row.addWidget(self.selected_meta, 1)
        meta_row.addWidget(self.file_status, 0, Qt.AlignmentFlag.AlignTop)
        header_layout.addLayout(meta_row)
        layout.addWidget(header)

        self.tabs = QTabWidget()
        self.tabs.setObjectName("inspectorTabs")
        self.tabs.setDocumentMode(True)
        layout.addWidget(self.tabs, 1)

        self.details_scroll, _, details_layout = _scroll_page()
        details_card = QFrame()
        details_card.setObjectName("inspectorCard")
        details_card_layout = QVBoxLayout(details_card)
        details_card_layout.setContentsMargins(14, 14, 14, 14)
        details_card_layout.setSpacing(12)
        details_title = QLabel("Información del archivo")
        details_title.setObjectName("cardTitle")
        details_card_layout.addWidget(details_title)
        details_grid, self.detail_values = _field_grid(DETAIL_FIELDS, status_field="Estado")
        details_card_layout.addLayout(details_grid)
        details_layout.addWidget(details_card)
        details_actions = QGridLayout()
        self.copy_path_button = QPushButton("Copiar ruta")
        self.open_location_button = QPushButton("Abrir ubicación")
        self.organize_button = QPushButton("Organizar")
        self.organize_button.setObjectName("primaryButton")
        self.ignore_button = QPushButton("Ignorar")
        details_actions.addWidget(self.copy_path_button, 0, 0)
        details_actions.addWidget(self.open_location_button, 0, 1)
        details_actions.addWidget(self.organize_button, 1, 0)
        details_actions.addWidget(self.ignore_button, 1, 1)
        details_actions.setColumnStretch(0, 1)
        details_actions.setColumnStretch(1, 1)
        details_layout.addLayout(details_actions)
        details_layout.addStretch()
        self.tabs.addTab(self.details_scroll, "Detalles")

        self.content_scroll, _, content_layout = _scroll_page()
        content_card = QFrame()
        content_card.setObjectName("inspectorCard")
        content_card_layout = QVBoxLayout(content_card)
        content_card_layout.setContentsMargins(14, 14, 14, 14)
        content_card_layout.setSpacing(12)
        content_title = QLabel("Análisis de contenido")
        content_title.setObjectName("cardTitle")
        content_card_layout.addWidget(content_title)
        content_grid, self.analysis_values = _field_grid(CONTENT_FIELDS, status_field="Estado")
        content_card_layout.addLayout(content_grid)
        preview_title = QLabel("VISTA PREVIA")
        preview_title.setObjectName("eyebrow")
        content_card_layout.addWidget(preview_title)
        self.content_preview = QLabel("Aún no se ha analizado el contenido.")
        self.content_preview.setObjectName("previewText")
        self.content_preview.setWordWrap(True)
        self.content_preview.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.content_preview.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        content_card_layout.addWidget(self.content_preview)
        content_layout.addWidget(content_card)
        self.analyze_button = QPushButton("Analizar contenido")
        self.analyze_button.setObjectName("primaryButton")
        content_layout.addWidget(self.analyze_button)
        content_layout.addStretch()
        self.tabs.addTab(self.content_scroll, "Contenido")

        self.ai_scroll, _, ai_layout = _scroll_page()
        self.ai_section = QFrame()
        self.ai_section.setObjectName("aiSuggestionCard")
        ai_card_layout = QVBoxLayout(self.ai_section)
        ai_card_layout.setContentsMargins(16, 16, 16, 16)
        ai_card_layout.setSpacing(12)
        ai_title_row = QHBoxLayout()
        self.ai_heading = QLabel("Análisis inteligente")
        self.ai_heading.setObjectName("cardTitle")
        self.ai_ready_badge = QLabel("SUGERENCIA LISTA")
        self.ai_ready_badge.setObjectName("readyBadge")
        self.ai_ready_badge.hide()
        ai_title_row.addWidget(self.ai_heading, 1)
        ai_title_row.addWidget(self.ai_ready_badge)
        ai_card_layout.addLayout(ai_title_row)
        self.ai_hint = QLabel("OrdenIA interpreta el contenido localmente y propone una carpeta. Tú decides si usarla.")
        self.ai_hint.setObjectName("inspectorMeta")
        self.ai_hint.setWordWrap(True)
        self.ai_hint.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        ai_card_layout.addWidget(self.ai_hint)
        ai_grid, self.ai_values = _field_grid(AI_FIELDS, status_field="Estado IA")
        self.ai_values["Etiquetas"].setObjectName("tagList")
        self.ai_values["Destino sugerido IA"].setObjectName("suggestedPath")
        self.ai_values["Razón"].setObjectName("reasonText")
        self.set_ai_content_present(False)
        ai_card_layout.addLayout(ai_grid)
        ai_layout.addWidget(self.ai_section)
        ai_actions = QGridLayout()
        self.ai_use_button = QPushButton("Usar sugerencia")
        self.ai_use_button.setObjectName("primaryButton")
        self.ai_analyze_button = QPushButton("Analizar con IA")
        self.copy_suggestion_button = QPushButton("Copiar sugerencia")
        ai_actions.addWidget(self.ai_use_button, 0, 0, 1, 2)
        ai_actions.addWidget(self.ai_analyze_button, 1, 0, 1, 2)
        ai_actions.addWidget(self.copy_suggestion_button, 2, 0, 1, 2)
        ai_actions.setColumnStretch(0, 1)
        ai_actions.setColumnStretch(1, 1)
        ai_layout.addLayout(ai_actions)
        ai_layout.addStretch()
        self.tabs.addTab(self.ai_scroll, "IA")

        self.set_ai_ready(False)

    def set_header(self, name: str | None, category: str = "", status: str = "") -> None:
        if not name:
            self.selected_name.setText("Selecciona un archivo")
            self.selected_meta.setText("Elige una fila para consultar sus detalles.")
            self.file_status.setText("Sin selección")
            self._set_tone(self.file_status, "muted")
            return
        self.selected_name.setText(name)
        self.selected_meta.setText(category)
        self.file_status.setText(status)
        self._set_tone(self.file_status, self._tone_for_status(status))

    def show_details(self) -> None:
        self.tabs.setCurrentIndex(self.DETAILS_TAB)
        self.details_scroll.verticalScrollBar().setValue(0)

    def reveal_ai(self) -> None:
        self.tabs.setCurrentIndex(self.AI_TAB)
        self.ai_scroll.verticalScrollBar().setValue(0)
        self.ai_section.setFocus(Qt.FocusReason.OtherFocusReason)

    def set_ai_ready(self, ready: bool) -> None:
        self.ai_section.setProperty("ready", ready)
        self.ai_ready_badge.setVisible(ready)
        self.tabs.tabBar().setTabData(self.AI_TAB, "ready" if ready else "")
        self.ai_section.style().unpolish(self.ai_section)
        self.ai_section.style().polish(self.ai_section)
        self.ai_section.update()

    def set_ai_content_present(self, present: bool) -> None:
        for label in (self.ai_values["Etiquetas"], self.ai_values["Destino sugerido IA"]):
            label.setProperty("populated", present)
            label.style().unpolish(label)
            label.style().polish(label)
            label.update()

    @staticmethod
    def set_status_tone(label: QLabel, status: str) -> None:
        FileInspector._set_tone(label, FileInspector._tone_for_status(status))

    @staticmethod
    def _set_tone(label: QLabel, tone: str) -> None:
        label.setProperty("tone", tone)
        label.style().unpolish(label)
        label.style().polish(label)
        label.update()

    @staticmethod
    def _tone_for_status(status: str) -> str:
        normalized = status.casefold()
        if normalized in {"listo", "indexado", "organizado"}:
            return "ready"
        if normalized in {"error", "no compatible", "ia local no disponible"}:
            return "error"
        if normalized in {"analizando", "pendiente", "sin analizar"}:
            return "pending"
        return "muted"
