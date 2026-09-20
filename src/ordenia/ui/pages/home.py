"""Dashboard with database-backed counts and first-use guidance."""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFrame, QGridLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ordenia.core.classifier import ExtensionClassifier
from ordenia.database.repositories import Repository
from ordenia.ui.widgets.common import display_date, page


class HomePage(QWidget):
    add_folder_requested = Signal()

    def __init__(self, repository: Repository) -> None:
        super().__init__()
        self.repository = repository
        content, layout = page("Inicio", "Una vista clara de los archivos que OrdenIA ha indexado.")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(content)
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
        recent = self.repository.recent_files()
        self.activity.setText("\n".join(f"{display_date(file.detected_at)}   ·   {file.name}   ·   {file.status}" for file in recent) if recent else "Aún no hay actividad reciente.")
