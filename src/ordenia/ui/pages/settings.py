"""Local preferences with working destination and data actions."""

from pathlib import Path

from PySide6.QtWidgets import QCheckBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QLayout, QLineEdit, QMessageBox, QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget

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
        layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(content)
        outer.addWidget(scroll)
        for title in ("AVISOS",):
            heading = QLabel(title)
            heading.setObjectName("sectionTitle")
            layout.addWidget(heading)
        notifications = QCheckBox("Avisar al completar movimientos")
        notifications.setToolTip("Mostrar avisos al completar movimientos")
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
        ask = QCheckBox("Confirmar escaneo inicial")
        ask.setToolTip("Preguntar antes de analizar archivos existentes")
        ask.setChecked(repository.get_setting("ask_before_scan", "1") == "1")
        ask.toggled.connect(lambda checked: repository.set_setting("ask_before_scan", "1" if checked else "0"))
        layout.addWidget(ask)

        heading = QLabel("ANÁLISIS DE CONTENIDO")
        heading.setObjectName("sectionTitle")
        layout.addWidget(heading)
        automatic = QCheckBox("Análisis automático de archivos nuevos")
        automatic.setToolTip("Analizar automáticamente nuevos archivos compatibles")
        automatic.setChecked(repository.get_setting("auto_analyze_content", "0") == "1")
        automatic.toggled.connect(lambda checked: repository.set_setting("auto_analyze_content", "1" if checked else "0"))
        layout.addWidget(automatic)
        size_row = QHBoxLayout()
        size_row.addWidget(QLabel("Tamaño máximo automático:"))
        maximum = QSpinBox()
        maximum.setRange(1, 50)
        maximum.setSuffix(" MB")
        maximum.setValue(int(repository.get_setting("auto_analysis_max_mb", "50")))
        maximum.valueChanged.connect(lambda value: repository.set_setting("auto_analysis_max_mb", str(value)))
        size_row.addWidget(maximum)
        size_row.addStretch()
        layout.addLayout(size_row)
        auto_hint = QLabel("El análisis automático está desactivado por defecto. Los documentos se procesan localmente.")
        auto_hint.setWordWrap(True)
        layout.addWidget(auto_hint)

        heading = QLabel("DATOS")
        heading.setObjectName("sectionTitle")
        layout.addWidget(heading)
        location = QLabel("Base de datos local:")
        location.setObjectName("muted")
        layout.addWidget(location)
        self.database_path = QLineEdit(str(repository.db_path))
        self.database_path.setReadOnly(True)
        self.database_path.setToolTip(str(repository.db_path))
        layout.addWidget(self.database_path)
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
