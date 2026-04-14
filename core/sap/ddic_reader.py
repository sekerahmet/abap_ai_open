from core.sap.connection import SAPConnectionManager

class DDICReader:
    def __init__(self, conn_params=None):
        self.mgr = SAPConnectionManager(conn_params)

    def fetch_table(self, name):
        try:
            # DDIF_FIELDINFO_GET for metadata
            res = self.mgr.execute("DDIF_FIELDINFO_GET", TABNAME=name.upper())
            fields = []
            for f in res.get("DFIES_TAB", []):
                fields.append({
                    "Field": f.get("FIELDNAME"),
                    "Type": f.get("DATATYPE"),
                    "Len": f.get("LENG"),
                    "Description": f.get("FIELDTEXT")
                })
            
            # Compact code representation (used as AI context)
            code = f"* Table: {name}\n" + "\n".join([f"DATA {f['Field']} TYPE {f['Type']}." for f in fields])
            return code, {"NAME": name, "TYPE": "TABLE", "FIELDS": fields}
        except Exception as e:
            return None, str(e)

    def fetch_table_data(self, name, where_clause="", max_rows=200):
        """Fetch data rows from a table via RFC_READ_TABLE.
        Returns (columns, rows) on success or (None, error_str) on failure."""
        try:
            options = []
            remaining = where_clause.strip()
            while remaining:
                options.append({"TEXT": remaining[:72]})
                remaining = remaining[72:]

            res = self.mgr.execute("RFC_READ_TABLE",
                                   QUERY_TABLE=name.upper(),
                                   OPTIONS=options,
                                   ROWCOUNT=max_rows,
                                   DELIMITER="|")

            columns = [f.get("FIELDNAME", "").strip() for f in res.get("FIELDS", [])]
            rows = []
            for row in res.get("DATA", []):
                values = [v.strip() for v in row.get("WA", "").split("|")]
                while len(values) < len(columns):
                    values.append("")
                rows.append(values[:len(columns)])

            return columns, rows
        except Exception as e:
            return None, str(e)

    def check_objects_batch(self, names):
        """Authoritative TADIR check. Returns {OBJ_NAME: OBJECT_TYPE} mapping."""
        if not names: return {}
        unique_names = list(dict.fromkeys(n.upper() for n in names if n))  # deduplicate, preserve order

        # RFC_READ_TABLE OPTIONS rows are concatenated into a WHERE clause.
        # Each row must be < 72 chars. OR goes at the START of subsequent rows.
        options = []
        for i, name in enumerate(unique_names):
            line = f"OBJ_NAME = '{name}'"
            if i > 0:
                line = "OR " + line
            options.append({"TEXT": line})

        # When the same name exists multiple times in TADIR (e.g. a table with a
        # maintenance view generates a same-named FUGR), keep the most meaningful type.
        _PRIORITY = {"TABL": 1, "VIEW": 2, "CLAS": 3, "FUNC": 4, "PROG": 5, "FUGR": 6}

        try:
            res = self.mgr.execute("RFC_READ_TABLE",
                                   QUERY_TABLE="TADIR",
                                   OPTIONS=options,
                                   FIELDS=[{"FIELDNAME": "OBJ_NAME"}, {"FIELDNAME": "OBJECT"}])
            mapping = {}
            for row in res.get("DATA", []):
                # WA contains fixed-width concatenated fields: OBJ_NAME(40) + OBJECT(4)
                wa = row.get("WA", "")
                obj_name = wa[:40].strip()
                obj_type = wa[40:].strip()
                if obj_name and obj_type:
                    existing = mapping.get(obj_name)
                    if not existing or _PRIORITY.get(obj_type, 99) < _PRIORITY.get(existing, 99):
                        mapping[obj_name] = obj_type
            return mapping
        except Exception as e:
            return {}
