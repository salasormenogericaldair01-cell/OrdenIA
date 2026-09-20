from datetime import datetime

from PySide6.QtWidgets import QFrame, QGridLayout, QLabel, QVBoxLayout, QWidget

from ordenia.database.repositories import Repository
from ordenia.ui.widgets.common import display_date, page


class HomePage(QWidget):
    def __init__(self, repository: Repository) -> None:
        super().__init__()
        self.repository = repository
        content, layout = page("Inicio", "Tu espacio de trabajo local, bajo tu control.")
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
        recent_title = QLabel("Actividad reciente")
        recent_title.setObjectName("sectionTitle")
        layout.addWidget(recent_title)
        self.activity = QLabel("Aún no hay actividad.")
        self.activity.setWordWrap(True)
        self.activity.setAlignment(self.activity.alignment())
        layout.addWidget(self.activity)
        layout.addStretch()
        self.refresh()

    def refresh(self) -> None:
        files = self.repository.list_files()
        folders = self.repository.list_folders()
        today = datetime.now().astimezone().date()
        self.values["Archivos detectados hoy"].setText(str(sum(datetime.fromisoformat(f.detected_at).date() == today for f in files)))
        self.values["Pendientes de revisar"].setText(str(sum(f.status == "Pendiente" for f in files)))
        self.values["Archivos organizados"].setText(str(sum(f.status == "Organizado" for f in files)))
        self.values["Carpetas vigiladas"].setText(str(sum(f.enabled for f in folders)))
        recent = files[:6]
        self.activity.setText("\n".join(f"{display_date(f.detected_at)}  ·  {f.name}  ·  {f.status}" for f in recent) if recent else "Aún no hay actividad. Añade una carpeta para empezar.")
