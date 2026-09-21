"""Local preferences with working destination and data actions."""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QLayout, QLineEdit, QMessageBox, QPushButton, QScrollArea, QSizePolicy, QSpinBox, QVBoxLayout, QWidget

from ordenia.ai.models import DEFAULT_AI_MODEL, ProviderStatus
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

        heading = QLabel("IA LOCAL")
        heading.setObjectName("sectionTitle")
        layout.addWidget(heading)
        provider_row = QHBoxLayout()
        provider_row.addWidget(QLabel("Proveedor:"))
        self.ai_provider = QComboBox()
        self.ai_provider.addItem("Ollama", "ollama")
        self.ai_provider.setEnabled(False)
        provider_row.addWidget(self.ai_provider, 1)
        layout.addLayout(provider_row)
        model_row = QHBoxLayout()
        model_row.addWidget(QLabel("Modelo:"))
        self.ai_model = QLineEdit(repository.ai.get_setting("model", DEFAULT_AI_MODEL))
        self.ai_model.setPlaceholderText(DEFAULT_AI_MODEL)
        self.ai_model.editingFinished.connect(self._save_ai_model)
        model_row.addWidget(self.ai_model, 1)
        layout.addLayout(model_row)
        status_row = QHBoxLayout()
        status_row.addWidget(QLabel("Estado:"))
        self.ai_status = QLabel("○ No comprobado")
        self.ai_status.setObjectName("muted")
        self.ai_status.setWordWrap(True)
        self.ai_status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        status_row.addWidget(self.ai_status, 1)
        check_ai = QPushButton("Comprobar conexión")
        check_ai.clicked.connect(self._check_ai)
        status_row.addWidget(check_ai)
        layout.addLayout(status_row)
        self.ai_models = QLabel("Modelos instalados: no comprobados")
        self.ai_models.setObjectName("muted")
        self.ai_models.setWordWrap(True)
        self.ai_models.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout.addWidget(self.ai_models)
        self.ai_command = QLabel(f"OrdenIA no descarga modelos. Instalación manual recomendada: ollama pull {DEFAULT_AI_MODEL}")
        self.ai_command.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.ai_command.setWordWrap(True)
        self.ai_command.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout.addWidget(self.ai_command)
        privacy = QLabel("IA local: el análisis inteligente se realiza en tu computadora mediante Ollama. "
                         "OrdenIA no envía tus documentos a servicios externos.")
        privacy.setObjectName("muted")
        privacy.setWordWrap(True)
        privacy.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout.addWidget(privacy)
        self.service.ai.provider_status.connect(self._on_ai_status)

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

    def _save_ai_model(self) -> None:
        model = self.ai_model.text().strip()
        if not model:
            model = DEFAULT_AI_MODEL
            self.ai_model.setText(model)
        self.repository.ai.set_setting("model", model)
        self.ai_status.setText("○ Configuración modificada; comprueba la conexión")

    def _check_ai(self) -> None:
        self._save_ai_model()
        self.ai_status.setText("○ Comprobando Ollama...")
        self.service.ai.check_connection()

    def _on_ai_status(self, status: ProviderStatus) -> None:
        model = self.ai_model.text().strip()
        if not status.available:
            self.ai_status.setText("○ Ollama no disponible")
            self.ai_status.setToolTip(status.message)
            self.ai_models.setText("Inicia Ollama localmente y vuelve a comprobar la conexión.")
            return
        installed = model in status.models
        self.ai_status.setText("● Disponible" if installed else "○ Modelo no instalado")
        self.ai_status.setToolTip(status.message)
        displayed = [name + (" (recomendado)" if name == DEFAULT_AI_MODEL else "") for name in status.models]
        self.ai_models.setText("Modelos instalados: " + (", ".join(displayed) if displayed else "ninguno"))
        if not installed:
            self.ai_command.setText(f"Modelo no instalado. Ejecuta manualmente: ollama pull {model}")
