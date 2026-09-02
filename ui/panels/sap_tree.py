"""SAP Objects panel: filter box + tree of objects discovered in the active program."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLineEdit, QLabel, QTreeWidget, QTreeWidgetItem


# TADIR OBJECT → (sub-fetch category, glyph)
TADIR_META = {
    "TABL": ("DICT", "▦"), "VIEW": ("DICT", "▦"),
    "CLAS": ("CLASS", "◆"), "INTF": (None, "◇"),
    "PROG": ("PROG", "▤"), "FUNC": ("FUNC", "ƒ"), "FUGR": (None, "ƒ"),
    "MSAG": (None, "✉"), "DTEL": (None, "𝑑"), "DOMA": (None, "𝑑"), "TTYP": (None, "▥"),
}
CATEGORIES = [("DICT", "Dictionary"), ("CLASS", "Classes"), ("INCLUDES", "Includes"),
              ("FORMS", "Forms / Modules"), ("FIELDS", "Local Refs")]


class SapObjectsPanel(QWidget):
    jump = Signal(int)                 # single click → line
    open_object = Signal(str, str)     # double click → (name, tadir_type)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._last = ({}, {}, "Discovered Objects")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(4)
        self.header = QLabel("Discovered Objects")
        self.header.setObjectName("muted")
        self.header.setWordWrap(True)
        lay.addWidget(self.header)
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("filter objects…")
        self.filter.setClearButtonEnabled(True)
        self.filter.textChanged.connect(lambda t: self.populate(*self._last, flt=t))
        lay.addWidget(self.filter)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Name   (click: go to line · double-click: open)", "Type"])
        self.tree.setColumnWidth(0, 260)
        self.tree.setAlternatingRowColors(False)
        self.tree.itemClicked.connect(self._clicked)
        self.tree.itemDoubleClicked.connect(self._dbl)
        lay.addWidget(self.tree, 1)
        self.roots = {}
        for key, label in CATEGORIES:
            it = QTreeWidgetItem([label, ""])
            it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            self.tree.addTopLevelItem(it)
            it.setExpanded(key in ("DICT", "CLASS", "INCLUDES"))
            self.roots[key] = it

    def populate(self, objs: dict, registry: dict, header: str = "Discovered Objects", flt=None):
        if flt is None:
            self._last = (objs, registry, header)
            flt = self.filter.text()
        flt = (flt or "").strip().upper()
        self.header.setText(header)
        for root in self.roots.values():
            root.takeChildren()
        for cat, items in objs.items():
            root = self.roots.get(cat)
            if not root:
                continue
            for o in items:
                name, line = o["name"], o.get("line", 0)
                tadir = registry.get(name, "")
                if cat == "DICT" and not tadir:
                    continue
                if flt and flt not in name:
                    continue
                glyph = TADIR_META.get(tadir, (None, "·"))[1] if tadir else "·"
                it = QTreeWidgetItem([f"{glyph}  {name}", tadir])
                it.setData(0, Qt.ItemDataRole.UserRole, (name, tadir, line))
                if tadir and TADIR_META.get(tadir, (None,))[0]:
                    it.setForeground(0, Qt.GlobalColor.white)
                root.addChild(it)
        for root in self.roots.values():
            root.setText(1, str(root.childCount()) if root.childCount() else "")

    def _clicked(self, item, _col):
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if data and data[2] > 0:
            self.jump.emit(int(data[2]))

    def _dbl(self, item, _col):
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if data:
            self.open_object.emit(data[0], data[1])
