"""Options selected before adding or editing a watched folder."""

from pathlib import Path

from PySide6.QtWidgets import QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout, QLineEdit, QMessageBox, QPushButton, QVBoxLayout

from ordenia.core.destinations import CENTRAL, CUSTOM, INSIDE
from ordenia.database.models import WatchedFolder


class FolderOptionsDialog(QDialog):
    def __init__(self, folder: Path, existing: WatchedFolder | None = None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Opciones de carpeta")
        self.setMinimumWidth(520)
        outer = QVBoxLayout(self)
        form = QFormLayout()
        self.subfolders = QCheckBox("Incluir subcarpetas")
        self.subfolders.setChecked(existing.include_subfolders if existing else True)
        folder_path = QLineEdit(str(folder))
        folder_path.setReadOnly(True)
        form.addRow("Carpeta:", folder_path)
        form.addRow("Análisis:", self.subfolders)
        self.strategy = QComboBox()
        self.strategy.addItem("Dentro de la carpeta vigilada", INSIDE)
        self.strategy.addItem("Biblioteca central de OrdenIA", CENTRAL)
        self.strategy.addItem("Carpeta personalizada", CUSTOM)
        self.strategy.setCurrentIndex(self.strategy.findData(existing.destination_strategy if existing else INSIDE))
        form.addRow("Destino:", self.strategy)
        custom_row = QHBoxLayout()
        self.custom_path = QLineEdit(str(existing.custom_destination) if existing and existing.custom_destination else "")
        self.custom_path.setPlaceholderText("Selecciona una carpeta")
        browse = QPushButton("Examinar")
        browse.clicked.connect(self._browse)
        custom_row.addWidget(self.custom_path)
        custom_row.addWidget(browse)
        form.addRow("Ruta personalizada:", custom_row)
        self.strategy.currentIndexChanged.connect(self._update_custom)
        self._update_custom()
        outer.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Guardar")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancelar")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    def _update_custom(self) -> None:
        self.custom_path.setEnabled(self.strategy.currentData() == CUSTOM)

    def _browse(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "Seleccionar destino personalizado")
        if selected:
            self.custom_path.setText(selected)

    def _accept(self) -> None:
        if self.strategy.currentData() == CUSTOM and (not self.custom_path.text().strip() or not Path(self.custom_path.text()).expanduser().is_dir()):
            QMessageBox.warning(self, "OrdenIA", "Selecciona una carpeta personalizada existente.")
            return
        self.accept()

    def values(self) -> tuple[bool, str, Path | None]:
        strategy = str(self.strategy.currentData())
        custom = Path(self.custom_path.text()).expanduser().resolve() if strategy == CUSTOM else None
        return self.subfolders.isChecked(), strategy, custom
