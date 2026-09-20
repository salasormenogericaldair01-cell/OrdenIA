"""Dashboard with database-backed counts and first-use guidance."""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFrame, QGridLayout, QLabel, QLayout, QPushButton, QScrollArea, QVBoxLayout, QWidget

from ordenia.core.classifier import ExtensionClassifier
from ordenia.database.repositories import Repository
from ordenia.ui.widgets.common import display_date, page


class HomePage(QWidget):
    add_folder_requested = Signal()

    def __init__(self, repository: Repository) -> None:
        super().__init__()
        self.repository = repository
        content, layout = page("Inicio", "Una vista clara de los archivos que OrdenIA ha indexado.")
        layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(content)
        outer.addWidget(scroll)
        cards = QGridLayout()
        cards.setSpacing(14)
        self.values: dict[str, QLabel] = {}
        for index, title in enumerate(("Archivos detectados hoy", "Pendientes de revisar", "Archivos organizados", "Carpetas vigiladas")):
            card = QFrame()
            card.setObjectName("statCard")
            card_layout = QVBoxLayout(card)
            label = QLabel(title)
            label.setObjectName("muted")
            value = QLabel("0")
            value.setObjectName("statValue")
            card_layout.addWidget(label)
            card_layout.addWidget(value)
            cards.addWidget(card, index // 2, index % 2)
            self.values[title] = value
        layout.addLayout(cards)

        self.empty = QFrame()
        self.empty.setObjectName("emptyState")
        empty_layout = QVBoxLayout(self.empty)
        empty_layout.addWidget(QLabel("OrdenIA todavía no está vigilando ninguna carpeta."))
        hint = QLabel("Empieza agregando Descargas, Escritorio o cualquier carpeta que quieras organizar.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        empty_layout.addWidget(hint)
        add = QPushButton("Añadir primera carpeta")
        add.setObjectName("primaryButton")
        add.clicked.connect(self.add_folder_requested.emit)
        empty_layout.addWidget(add)
        layout.addWidget(self.empty)

        self.summary_title = QLabel("Resumen de archivos")
        self.summary_title.setObjectName("sectionTitle")
        layout.addWidget(self.summary_title)
        self.summary = QFrame()
        self.summary.setObjectName("summaryCard")
        summary_layout = QGridLayout(self.summary)
        self.category_values: dict[str, QLabel] = {}
        for index, category in enumerate((*ExtensionClassifier.CATEGORIES.keys(), "Otros")):
            label = QLabel(category)
            label.setObjectName("muted")
            value = QLabel("0")
            value.setAlignment(value.alignment())
            summary_layout.addWidget(label, index // 2, (index % 2) * 2)
            summary_layout.addWidget(value, index // 2, (index % 2) * 2 + 1)
            self.category_values[category] = value
        layout.addWidget(self.summary)
        content_title = QLabel("Índice de contenido")
        content_title.setObjectName("sectionTitle")
        layout.addWidget(content_title)
        self.content_summary = QFrame()
        self.content_summary.setObjectName("summaryCard")
        content_layout = QGridLayout(self.content_summary)
        self.content_values: dict[str, QLabel] = {}
        for index, title in enumerate(("Indexados", "Pendientes", "Procesando", "Sin texto", "No compatibles", "Errores")):
            label = QLabel(title)
            label.setObjectName("muted")
            value = QLabel("0")
            content_layout.addWidget(label, index, 0)
            content_layout.addWidget(value, index, 1)
            self.content_values[title] = value
        layout.addWidget(self.content_summary)
        self.content_empty = QLabel("Aún no hay contenido indexado. Selecciona archivos y pulsa Analizar contenido.")
        self.content_empty.setObjectName("muted")
        self.content_empty.setWordWrap(True)
        layout.addWidget(self.content_empty)
        recent_title = QLabel("Actividad reciente")
        recent_title.setObjectName("sectionTitle")
        layout.addWidget(recent_title)
        self.activity = QLabel("Aún no hay actividad.")
        self.activity.setObjectName("activity")
        self.activity.setWordWrap(True)
        layout.addWidget(self.activity)
        layout.addStretch()
        self.refresh()

    def refresh(self) -> None:
        counts = self.repository.dashboard_counts()
        folders = self.repository.list_folders()
        self.values["Archivos detectados hoy"].setText(str(counts["today"]))
        self.values["Pendientes de revisar"].setText(str(counts["pending"]))
        self.values["Archivos organizados"].setText(str(counts["organized"]))
        self.values["Carpetas vigiladas"].setText(str(sum(folder.enabled for folder in folders)))
        self.empty.setHidden(bool(folders))
        self.summary_title.setHidden(not counts["total"])
        self.summary.setHidden(not counts["total"])
        categories = self.repository.count_by_category()
        for category, label in self.category_values.items():
            label.setText(str(categories.get(category, 0)))
        content_counts = self.repository.content.counts()
        self.content_values["Indexados"].setText(str(content_counts.get("indexed", 0)))
        self.content_values["Pendientes"].setText(str(content_counts.get("pending", 0) + content_counts.get("stale", 0)))
        self.content_values["Procesando"].setText(str(content_counts.get("analyzing", 0)))
        self.content_values["Sin texto"].setText(str(content_counts.get("no_text", 0)))
        self.content_values["No compatibles"].setText(str(content_counts.get("unsupported", 0) + content_counts.get("skipped", 0)))
        self.content_values["Errores"].setText(str(content_counts.get("failed", 0)))
        self.content_empty.setHidden(content_counts.get("indexed", 0) > 0 or counts["total"] == 0)
        recent = self.repository.recent_files()
        self.activity.setText("\n".join(f"{display_date(file.detected_at)}   ·   {file.name}   ·   {file.status}" for file in recent) if recent else "Aún no hay actividad reciente.")
