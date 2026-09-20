from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox, QPushButton, QTableWidgetItem, QVBoxLayout, QWidget

from ordenia.database.repositories import Repository
from ordenia.services.file_service import FileService
from ordenia.ui.widgets.common import display_date, page, path_item, table


class HistoryPage(QWidget):
    def __init__(self, repository: Repository, service: FileService) -> None:
        super().__init__()
        self.repository = repository
        self.service = service
        content, layout = page("Historial", "Puedes deshacer un movimiento mientras el archivo siga en su destino.")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(content)
        self.table = table(["Fecha", "Operación", "Origen", "Destino", "Estado"], path_columns=(2, 3))
        layout.addWidget(self.table)
        undo = QPushButton("Deshacer")
        undo.setObjectName("primaryButton")
        undo.clicked.connect(self._undo)
        layout.addWidget(undo)
        self.refresh()

    def _undo(self) -> None:
        row = self.table.currentRow()
        item = self.table.item(row, 0) if row >= 0 else None
        if item is None:
            QMessageBox.information(self, "OrdenIA", "Selecciona un movimiento.")
            return
        operation = self.repository.get_operation(int(item.data(Qt.ItemDataRole.UserRole)))
        if operation.operation_type != "move" or operation.status != "Completado":
            QMessageBox.information(self, "OrdenIA", "Esta operación no se puede deshacer.")
            return
        self.service.undo(operation.id)

    def refresh(self) -> None:
        current = self.table.item(self.table.currentRow(), 0) if self.table.currentRow() >= 0 else None
        selected_id = current.data(Qt.ItemDataRole.UserRole) if current else None
        operations = self.repository.list_operations()
        self.table.setRowCount(len(operations))
        for row, op in enumerate(operations):
            values = [display_date(op.created_at), "Organizar" if op.operation_type == "move" else "Deshacer", str(op.original_path), str(op.destination_path), op.status]
            for column, value in enumerate(values):
                item = path_item(op.original_path if column == 2 else op.destination_path) if column in (2, 3) else QTableWidgetItem(value)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, op.id)
                if column == 4 and op.error_message:
                    item.setToolTip(op.error_message)
                self.table.setItem(row, column, item)
            if op.id == selected_id:
                self.table.setCurrentCell(row, 0)
