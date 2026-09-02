"""
ProgramReader — read-only access to ABAP source via RFC.

  fetch_code(name)             RPY_PROGRAM_READ            (programs, includes)
  fetch_many(names)            same, one RFC session for the whole batch
  fetch_function_module(name)  RPY_FUNCTIONMODULE_READ_NEW → RPY_FUNCTIONMODULE_READ
  fetch_class_source(name)     class-pool includes + method includes (via TMDIR)

Return convention: (code, attrs) on success, (None, error_str) on failure.
"""

from core.sap.connection import SAPConnectionManager

# Class-pool section includes, in display order.  SAP pads the class name to
# 30 characters with '=' before the suffix:  ZCL_X====================CU
_CLASS_SECTIONS = [
    ("CCDEF", "Local type / class definitions"),
    ("CU",    "Public section"),
    ("CO",    "Protected section"),
    ("CI",    "Private section"),
    ("CCMAC", "Macros"),
    ("CCIMP", "Local class implementations"),
]

_BASE36 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def _method_include(base: str, index: int) -> str:
    """Method include suffix is CM + 3-digit base-36 index (CM001 … CM00Z, CM010 …)."""
    digits = ""
    n = index
    for _ in range(3):
        digits = _BASE36[n % 36] + digits
        n //= 36
    return f"{base}CM{digits}"


def _join_source(res: dict) -> str:
    # SOURCE_EXTENDED (255-char lines) may be present but empty on older kernels.
    lines = res.get("SOURCE_EXTENDED") or res.get("SOURCE") or []
    return "\n".join(line.get("LINE", "") for line in lines)


class ProgramReader:
    def __init__(self, conn_params=None):
        self.mgr = SAPConnectionManager(conn_params)

    # ── Programs / includes ───────────────────────────────────────────────────

    @staticmethod
    def _read_program(call, name: str):
        res = call("RPY_PROGRAM_READ", PROGRAM_NAME=name.upper())
        code = _join_source(res)
        if not code.strip():
            return None, f"Program '{name}' not found or empty."
        return code, {"NAME": name.upper(), "TYPE": "PROG"}

    def fetch_code(self, name: str):
        try:
            return self._read_program(self.mgr.execute, name)
        except Exception as e:
            return None, str(e)

    def fetch_many(self, names: list) -> dict:
        """Read several programs in one RFC session. Returns {NAME: (code, attrs_or_err)}."""
        result = {}
        if not names:
            return result
        try:
            with self.mgr.session() as call:
                for n in names:
                    try:
                        result[n.upper()] = self._read_program(call, n)
                    except Exception as e:
                        result[n.upper()] = (None, str(e))
        except Exception as e:
            for n in names:
                result.setdefault(n.upper(), (None, str(e)))
        return result

    # ── Function modules ──────────────────────────────────────────────────────

    def fetch_function_module(self, name: str):
        name = name.upper()
        errors = []
        for fm in ("RPY_FUNCTIONMODULE_READ_NEW", "RPY_FUNCTIONMODULE_READ"):
            try:
                res = self.mgr.execute(fm, FUNCTIONNAME=name)
                code = _join_source(res)
                if code.strip():
                    return code, {"NAME": name, "TYPE": "FUNC"}
                errors.append(f"{fm}: no source returned")
            except Exception as e:
                errors.append(f"{fm}: {e}")
        return None, f"Function Module '{name}' could not be read.\n" + "\n".join(errors)

    # ── Global classes ────────────────────────────────────────────────────────

    @staticmethod
    def _class_methods(call, class_name: str) -> list:
        """[(METHODNAME, METHODINDX), …] from TMDIR, sorted by index."""
        try:
            res = call("RFC_READ_TABLE",
                       QUERY_TABLE="TMDIR",
                       DELIMITER="|",
                       FIELDS=[{"FIELDNAME": "METHODNAME"}, {"FIELDNAME": "METHODINDX"}],
                       OPTIONS=[{"TEXT": f"CLASSNAME = '{class_name}'"}])
        except Exception:
            return []
        seen = {}
        for row in res.get("DATA", []):
            parts = row.get("WA", "").split("|")
            if len(parts) < 2:
                continue
            mname = parts[0].strip()
            try:
                idx = int(parts[1].strip() or "0")
            except ValueError:
                continue
            if mname and idx > 0 and idx not in seen:
                seen[idx] = mname
        return sorted(seen.items(), key=lambda kv: kv[0])   # [(idx, name)]

    def fetch_class_source(self, class_name: str):
        cls = class_name.upper()
        base = cls.ljust(30, "=")
        parts = []
        try:
            with self.mgr.session() as call:
                for suffix, label in _CLASS_SECTIONS:
                    inc = base + suffix
                    try:
                        code, _ = self._read_program(call, inc)
                    except Exception:
                        code = None
                    if code and code.strip():
                        parts.append(self._banner(label, inc) + code)

                for idx, mname in self._class_methods(call, cls):
                    inc = _method_include(base, idx)
                    try:
                        code, _ = self._read_program(call, inc)
                    except Exception:
                        code = None
                    if code and code.strip():
                        parts.append(self._banner(f"METHOD {mname}", inc) + code)
        except Exception as e:
            return None, str(e)

        if not parts:
            return None, f"Class '{cls}' not found (no class-pool includes readable)."
        return "\n\n".join(parts), {"NAME": cls, "TYPE": "CLAS"}

    @staticmethod
    def _banner(label: str, include: str) -> str:
        bar = "*" + "=" * 72
        return f"{bar}\n* {label}   [{include.strip('=').replace('=', '')}]\n{bar}\n"
