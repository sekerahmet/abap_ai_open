"""
DDICReader — read-only dictionary access via RFC.

  fetch_table(name)          DDIF_FIELDINFO_GET       field metadata (table/structure/view)
  fetch_tables_batch(names)  same, one RFC session
  fetch_table_data(name, …)  RFC_READ_TABLE           data rows
  check_objects_batch(names) RFC_READ_TABLE on TADIR  {OBJ_NAME: OBJECT}
"""

from core.sap.connection import SAPConnectionManager

_OPTION_WIDTH = 72        # RFC_READ_TABLE OPTIONS rows are CHAR 72
_TADIR_CHUNK  = 40        # names per TADIR query (keeps the dynamic WHERE small)

# When the same name exists several times in TADIR (a table with a maintenance
# view generates a same-named FUGR), keep the most meaningful type.
_TYPE_PRIORITY = {"TABL": 1, "VIEW": 2, "CLAS": 3, "INTF": 4, "FUNC": 5,
                  "PROG": 6, "FUGR": 7, "TTYP": 8, "DTEL": 9, "DOMA": 10}


def split_where(where: str, width: int = _OPTION_WIDTH) -> list:
    """Split a WHERE clause into ≤72-char rows on whitespace boundaries."""
    rows, cur = [], ""
    for tok in where.split():
        if len(tok) > width:
            raise ValueError(f"WHERE token longer than {width} chars: {tok[:20]}…")
        if cur and len(cur) + 1 + len(tok) > width:
            rows.append(cur)
            cur = tok
        else:
            cur = f"{cur} {tok}" if cur else tok
    if cur:
        rows.append(cur)
    return [{"TEXT": r} for r in rows]


class DDICReader:
    def __init__(self, conn_params=None):
        self.mgr = SAPConnectionManager(conn_params)

    # ── Field metadata ────────────────────────────────────────────────────────

    @staticmethod
    def _read_fields(call, name: str):
        res = call("DDIF_FIELDINFO_GET", TABNAME=name.upper())
        fields = []
        for f in res.get("DFIES_TAB", []):
            fields.append({
                "Field":       f.get("FIELDNAME", ""),
                "Key":         "K" if f.get("KEYFLAG") else "",
                "Type":        f.get("DATATYPE", ""),
                "Len":         f.get("LENG", ""),
                "Decimals":    f.get("DECIMALS", ""),
                "DataElement": f.get("ROLLNAME", ""),
                "Domain":      f.get("DOMNAME", ""),
                "Description": f.get("FIELDTEXT", ""),
            })
        if not fields:
            return None, f"'{name}' has no fields (not a table/structure/view?)."
        code = f"* Table: {name.upper()}\n" + "\n".join(
            f"DATA {f['Field']} TYPE {f['Type']}." for f in fields)
        return code, {"NAME": name.upper(), "TYPE": "TABL", "FIELDS": fields}

    def fetch_table(self, name: str):
        try:
            return self._read_fields(self.mgr.execute, name)
        except Exception as e:
            return None, str(e)

    def fetch_tables_batch(self, names: list) -> dict:
        """{NAME: (code, attrs_or_err)} using one RFC session."""
        result = {}
        if not names:
            return result
        try:
            with self.mgr.session() as call:
                for n in names:
                    try:
                        result[n.upper()] = self._read_fields(call, n)
                    except Exception as e:
                        result[n.upper()] = (None, str(e))
        except Exception as e:
            for n in names:
                result.setdefault(n.upper(), (None, str(e)))
        return result

    # ── Table data ────────────────────────────────────────────────────────────

    def fetch_table_data(self, name: str, where_clause: str = "", max_rows: int = 200):
        """Returns (columns, rows) or (None, error_str)."""
        try:
            options = split_where(where_clause.strip()) if where_clause.strip() else []
            res = self.mgr.execute("RFC_READ_TABLE",
                                   QUERY_TABLE=name.upper(),
                                   OPTIONS=options,
                                   ROWCOUNT=max_rows,
                                   DELIMITER="|")
            columns = [f.get("FIELDNAME", "").strip() for f in res.get("FIELDS", [])]
            rows = []
            for row in res.get("DATA", []):
                values = [v.strip() for v in row.get("WA", "").split("|")]
                values += [""] * (len(columns) - len(values))
                rows.append(values[:len(columns)])
            return columns, rows
        except Exception as e:
            return None, str(e)

    # ── TADIR existence check ─────────────────────────────────────────────────

    def check_objects_batch(self, names: list) -> dict:
        """Authoritative TADIR check. Returns {OBJ_NAME: OBJECT_TYPE}."""
        unique = list(dict.fromkeys(n.upper() for n in names if n))
        if not unique:
            return {}

        mapping = {}
        try:
            with self.mgr.session() as call:
                for start in range(0, len(unique), _TADIR_CHUNK):
                    chunk = unique[start:start + _TADIR_CHUNK]
                    options = [{"TEXT": ("OR " if i else "") + f"OBJ_NAME = '{n}'"}
                               for i, n in enumerate(chunk)]
                    res = call("RFC_READ_TABLE",
                               QUERY_TABLE="TADIR",
                               OPTIONS=options,
                               FIELDS=[{"FIELDNAME": "OBJ_NAME"}, {"FIELDNAME": "OBJECT"}])
                    for row in res.get("DATA", []):
                        wa = row.get("WA", "")          # fixed width: OBJ_NAME(40) + OBJECT(4)
                        obj_name = wa[:40].strip()
                        obj_type = wa[40:].strip()
                        if not (obj_name and obj_type):
                            continue
                        existing = mapping.get(obj_name)
                        if (not existing or
                                _TYPE_PRIORITY.get(obj_type, 99) < _TYPE_PRIORITY.get(existing, 99)):
                            mapping[obj_name] = obj_type
        except Exception:
            pass
        return mapping
