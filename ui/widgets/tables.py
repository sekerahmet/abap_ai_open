"""Table widgets for DDIC field lists and RFC_READ_TABLE data."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QTableWidget, QTableWidgetItem, QAbstractItemView, QHeaderView

from ui import theme as T


class GridTable(QTableWidget):
    def __init__(self, columns: list, rows: list, stretch_last=True, parent=None):
        super().__init__(len(rows), len(columns), parent)
        self.setHorizontalHeaderLabels(columns)
        self.verticalHeader().setVisible(False)
        self.setAlternatingRowColors(True)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setFont(QFont(T.MONO, 10))
        self.setWordWrap(False)
        for r, row in enumerate(rows):
            for c, val in enumerate(row):
                it = QTableWidgetItem("" if val is None else str(val))
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.setItem(r, c, it)
        self.resizeColumnsToContents()
        hdr = self.horizontalHeader()
        hdr.setStretchLastSection(stretch_last)
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for c in range(len(columns)):
            self.setColumnWidth(c, min(max(self.columnWidth(c) + 16, 60), 420))


def fields_table(fields: list) -> GridTable:
    cols = ["Key", "Field", "Type", "Len", "Dec", "Data Element", "Domain", "Description"]
    rows = [(f.get("Key", ""), f.get("Field", ""), f.get("Type", ""), f.get("Len", ""),
             f.get("Decimals", ""), f.get("DataElement", ""), f.get("Domain", ""),
             f.get("Description", "")) for f in fields]
    return GridTable(cols, rows)


def data_table(columns: list, rows: list) -> GridTable:
    return GridTable(list(columns), rows, stretch_last=False)
