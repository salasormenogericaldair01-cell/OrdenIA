"""Visible destination choices for adding or editing a watched folder."""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFileDialog,
                               QFormLayout, QGroupBox, QHBoxLayout, QLabel,
                               QLineEdit, QMessageBox, QPushButton, QRadioButton,
                               QVBoxLayout)

from ordenia.core.destinations import CENTRAL, CUSTOM, INSIDE, destination_root
from ordenia.database.models import WatchedFolder
from ordenia.platform.actions import default_central_root


class FolderOptionsDialog(QDialog):
    def __init__(
        self, folder: Path, existing: WatchedFolder | None = None, parent=None,
        *, central_root: Path | None = None,
    ) -> None:
        super().__init__(parent)
        self.folder = folder
        self.central_root = central_root or default_central_root()
        self.setWindowTitle("Opciones de carpeta")
        self.setMinimumWidth(580)
        outer = QVBoxLayout(self)

        form = QFormLayout()
        folder_path = QLineEdit(str(folder))
        folder_path.setReadOnly(True)
        form.addRow("Carpeta vigilada:", folder_path)
        self.subfolders = QCheckBox("Incluir subcarpetas")
        self.subfolders.setChecked(existing.include_subfolders if existing else True)
        form.addRow("Análisis:", self.subfolders)
        outer.addLayout(form)

        choices = QGroupBox("Destino de organización")
        choices_layout = QVBoxLayout(choices)
        self.strategy_buttons: dict[str, QRadioButton] = {}
        for strategy, title, root, description in (
            (INSIDE, "Dentro de la carpeta vigilada", folder / "OrdenIA", "Se crea una carpeta OrdenIA aquí."),
            (CENTRAL, "Biblioteca central", self.central_root, "Se configura en Configuración y puede compartirse entre carpetas vigiladas."),
            (CUSTOM, "Carpeta personalizada", None, "Elige una ruta para esta carpeta vigilada."),
        ):
            button = QRadioButton(title)
            self.strategy_buttons[strategy] = button
            choices_layout.addWidget(button)
            if root is not None:
                path_label = QLabel(str(root))
                path_label.setObjectName("muted")
                path_label.setWordWrap(True)
                path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                path_label.setToolTip(str(root))
                choices_layout.addWidget(path_label)
            hint = QLabel(description)
            hint.setObjectName("muted")
            hint.setWordWrap(True)
            choices_layout.addWidget(hint)
            button.toggled.connect(self._update_destination)

        custom_row = QHBoxLayout()
        self.custom_path = QLineEdit(str(existing.custom_destination) if existing and existing.custom_destination else "")
        self.custom_path.setPlaceholderText("Selecciona una carpeta personalizada")
        self.custom_path.textChanged.connect(self._update_destination)
        browse = QPushButton("Examinar")
        browse.clicked.connect(self._browse)
        custom_row.addWidget(self.custom_path)
        custom_row.addWidget(browse)
        choices_layout.addLayout(custom_row)
        self.browse_button = browse
        outer.addWidget(choices)

        self.preview = QLabel()
        self.preview.setWordWrap(True)
        self.preview.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        outer.addWidget(self.preview)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Guardar")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancelar")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

        selected = existing.destination_strategy if existing else INSIDE
        self.strategy_buttons.get(selected, self.strategy_buttons[INSIDE]).setChecked(True)
        self._update_destination()

    def _selected_strategy(self) -> str:
        return next(strategy for strategy, button in self.strategy_buttons.items() if button.isChecked())

    def _update_destination(self) -> None:
        selected = next((key for key, button in self.strategy_buttons.items() if button.isChecked()), INSIDE)
        custom = selected == CUSTOM
        self.custom_path.setEnabled(custom)
        self.browse_button.setEnabled(custom)
        if custom and not self.custom_path.text().strip():
            self.preview.setText("Destino propuesto: selecciona una carpeta personalizada.")
            return
        try:
            root = destination_root(self.folder, selected, self.central_root,
                                    Path(self.custom_path.text()).expanduser() if custom else None)
            self.preview.setText(f"Ejemplo de destino: {root / 'Documentos' / 'archivo.pdf'}\n"
                                 "OrdenIA siempre solicita confirmación antes de mover archivos.")
        except ValueError as exc:
            self.preview.setText(f"Destino no válido: {exc}")

    def _browse(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "Seleccionar destino personalizado")
        if selected:
            self.custom_path.setText(selected)

    def _accept(self) -> None:
        try:
            _, strategy, custom = self.values()
            if strategy == CUSTOM and (custom is None or not custom.is_dir()):
                raise ValueError("Selecciona una carpeta personalizada existente.")
            destination_root(self.folder, strategy, self.central_root, custom)
        except ValueError as exc:
            QMessageBox.warning(self, "OrdenIA", str(exc))
            return
        self.accept()

    def values(self) -> tuple[bool, str, Path | None]:
        strategy = self._selected_strategy()
        custom = Path(self.custom_path.text()).expanduser().resolve() if strategy == CUSTOM and self.custom_path.text().strip() else None
        return self.subfolders.isChecked(), strategy, custom
