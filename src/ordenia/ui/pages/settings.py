from PySide6.QtWidgets import QCheckBox, QLabel, QVBoxLayout, QWidget

from ordenia.database.repositories import Repository
from ordenia.ui.widgets.common import page


class SettingsPage(QWidget):
    def __init__(self, repository: Repository) -> None:
        super().__init__()
        self.repository = repository
        content, layout = page("Configuración", "Preferencias locales de OrdenIA.")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(content)
        checkbox = QCheckBox("Mostrar avisos al completar movimientos")
        checkbox.setChecked(repository.get_setting("show_notifications", "1") == "1")
        checkbox.toggled.connect(lambda checked: repository.set_setting("show_notifications", "1" if checked else "0"))
        layout.addWidget(checkbox)
        location = QLabel(f"Base de datos local:\n{repository.db_path}")
        location.setObjectName("muted")
        location.setWordWrap(True)
        layout.addWidget(location)
        layout.addStretch()
