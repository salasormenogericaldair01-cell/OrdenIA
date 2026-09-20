"""Local preferences with working destination and data actions."""

from pathlib import Path

from PySide6.QtWidgets import QCheckBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QVBoxLayout, QWidget

from ordenia.database.repositories import Repository
from ordenia.platform.actions import open_location
from ordenia.services.file_service import FileService
from ordenia.ui.widgets.common import page


class SettingsPage(QWidget):
    def __init__(self, repository: Repository, service: FileService) -> None:
        super().__init__()
        self.repository = repository
        self.service = service
        content, layout = page("Configuración", "Preferencias locales de OrdenIA.")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(content)
        for title in ("AVISOS",):
            heading = QLabel(title)
            heading.setObjectName("sectionTitle")
            layout.addWidget(heading)
        notifications = QCheckBox("Mostrar avisos al completar movimientos")
        notifications.setChecked(repository.get_setting("show_notifications", "1") == "1")
        notifications.toggled.connect(lambda checked: repository.set_setting("show_notifications", "1" if checked else "0"))
        layout.addWidget(notifications)

        heading = QLabel("ORGANIZACIÓN")
        heading.setObjectName("sectionTitle")
        layout.addWidget(heading)
        layout.addWidget(QLabel("Destino central de OrdenIA:"))
        destination_row = QHBoxLayout()
        self.central_path = QLineEdit(str(service.central_destination()))
        self.central_path.setReadOnly(True)
        change = QPushButton("Cambiar")
        change.clicked.connect(self._change_central)
        destination_row.addWidget(self.central_path)
        destination_row.addWidget(change)
        layout.addLayout(destination_row)

        heading = QLabel("COMPORTAMIENTO")
        heading.setObjectName("sectionTitle")
        layout.addWidget(heading)
        ask = QCheckBox("Preguntar antes de analizar archivos existentes")
        ask.setChecked(repository.get_setting("ask_before_scan", "1") == "1")
        ask.toggled.connect(lambda checked: repository.set_setting("ask_before_scan", "1" if checked else "0"))
        layout.addWidget(ask)

        heading = QLabel("DATOS")
        heading.setObjectName("sectionTitle")
        layout.addWidget(heading)
        location = QLabel(f"Base de datos local:\n{repository.db_path}")
        location.setObjectName("muted")
        location.setWordWrap(True)
        layout.addWidget(location)
        open_data = QPushButton("Abrir carpeta de datos")
        open_data.clicked.connect(self._open_data)
        layout.addWidget(open_data)
        layout.addStretch()

    def _change_central(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "Seleccionar biblioteca central", str(self.service.central_destination().parent))
        if selected:
            try:
                self.service.set_central_destination(Path(selected))
                self.central_path.setText(str(self.service.central_destination()))
            except Exception as exc:
                QMessageBox.warning(self, "OrdenIA", str(exc))

    def _open_data(self) -> None:
        try:
            open_location(self.repository.db_path.parent)
        except Exception as exc:
            QMessageBox.warning(self, "OrdenIA", str(exc))
