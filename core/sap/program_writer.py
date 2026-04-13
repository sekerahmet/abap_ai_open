"""
ProgramWriter — upload ABAP source back to SAP via RFC.

Upload flow (always in this order):
  1. list_open_transports()   → user picks a TR
  2. assign_to_transport()    → lock object + assign to TR (RS_CORR_INSERT)
  3. check_syntax()           → optional syntax check before write
  4. write_program()          → write source lines (tries 5 candidates)

Write candidates tried in order:
  1. RPY_PROGRAM_WRITE
  2. RPY_PROGRAM_INSERT_MASTER
  3. RS_PROGRAM_WRITE
  4. RFC_ABAP_INSTALL_AND_RUN
  5. Z_ABAP_AI_WRITE_PROG  (custom Z FM — most reliable fallback)

NOTE: RFC parameter names may need adjustment depending on SAP kernel version.
      Test with a non-critical program first.
"""

from .connection import SAPConnectionManager


class ProgramWriter:
    def __init__(self, conn_params: dict):
        self._mgr = SAPConnectionManager(conn_params)

    # ── Transport Requests ────────────────────────────────────────────────────

    def list_open_transports(self, user: str = "") -> list:
        """
        Return open workbench transport requests.
        E070 → TRKORR + AS4USER  (AS4TEXT lives in E07T, not E070)
        E07T → AS4TEXT description, joined by TRKORR.
        """
        try:
            options = [
                {"TEXT": "TRSTATUS = 'D'"},
                {"TEXT": "AND TRFUNCTION = 'K'"},
            ]
            if user:
                options.append({"TEXT": f"AND AS4USER = '{user.upper()}'"})

            # Step 1: TRKORR + AS4USER from E070
            result = self._mgr.execute(
                "RFC_READ_TABLE",
                QUERY_TABLE="E070",
                DELIMITER="|",
                FIELDS=[
                    {"FIELDNAME": "TRKORR"},
                    {"FIELDNAME": "AS4USER"},
                ],
                OPTIONS=options,
            )
            rows = []
            for row in result.get("DATA", []):
                parts = [p.strip() for p in row.get("WA", "").split("|")]
                if len(parts) >= 2 and parts[0]:
                    rows.append({
                        "TRKORR":  parts[0],
                        "AS4USER": parts[1],
                        "AS4TEXT": "",
                    })

            if not rows:
                return rows

            # Step 2: descriptions from E07T (optional — don't fail if unavailable)
            try:
                trkorr_list = [r["TRKORR"] for r in rows]
                text_opts = [{"TEXT": f"TRKORR = '{trkorr_list[0]}'"}]
                for t in trkorr_list[1:]:
                    text_opts.append({"TEXT": f"OR TRKORR = '{t}'"})

                text_result = self._mgr.execute(
                    "RFC_READ_TABLE",
                    QUERY_TABLE="E07T",
                    DELIMITER="|",
                    FIELDS=[
                        {"FIELDNAME": "TRKORR"},
                        {"FIELDNAME": "AS4TEXT"},
                    ],
                    OPTIONS=text_opts,
                )
                text_map = {}
                for row in text_result.get("DATA", []):
                    parts = [p.strip() for p in row.get("WA", "").split("|")]
                    if len(parts) >= 2 and parts[0]:
                        text_map.setdefault(parts[0], parts[1])  # first entry per TRKORR

                for r in rows:
                    r["AS4TEXT"] = text_map.get(r["TRKORR"], "")
            except Exception:
                pass  # descriptions are optional

            return rows
        except Exception as e:
            return [{"TRKORR": "", "AS4USER": "", "AS4TEXT": f"ERROR: {e}"}]

    # ── Assign to TR ──────────────────────────────────────────────────────────

    def assign_to_transport(self, trkorr: str, prog_name: str,
                            obj_type: str = "PROG") -> tuple:
        """
        Lock the object and assign it to the given transport request.

        RS_CORR_INSERT OBJECT_CLASS values (different from TADIR OBJECT field):
          "ABAP"  → programs, includes, function groups
          "CLAS"  → global classes (R3TR CLAS)
        DIALOG=' ' prevents SAP from opening interactive popups.

        Returns (True, "") or (False, error_message).
        """
        # RS_CORR_INSERT uses its own class codes — not TADIR object type codes
        _class_map = {
            "PROG": "ABAP",
            "FUGR": "ABAP",
            "CLAS": "CLAS",
        }
        corr_class = _class_map.get(obj_type, "ABAP")

        try:
            self._mgr.execute(
                "RS_CORR_INSERT",
                OBJECT=prog_name.upper(),
                OBJECT_CLASS=corr_class,
                CORRNUM=trkorr,
                OPERATION="INSERT",
                DIALOG=" ",          # no interactive popups
                GLOBAL_LOCK=" ",
            )
            return True, ""
        except Exception as e:
            return False, str(e)

    # ── Syntax Check ─────────────────────────────────────────────────────────

    def check_syntax(self, prog_name: str, source_code: str) -> tuple:
        """
        Run ABAP syntax check on source_code before writing.
        Uses SYNTAX_CHECK RFC — available on most systems.
        Returns (True, "") if clean, (False, error_lines) if errors found,
        (None, "") if syntax check RFC is not available (caller may proceed).
        """
        name  = prog_name.upper()
        lines = [{"LINE": line} for line in source_code.splitlines()]
        try:
            result = self._mgr.execute(
                "SYNTAX_CHECK",
                PROGRAM=lines,
                PROG_NAME=name,
                PROG_TYPE="1",   # 1 = executable program
            )
            errors = result.get("ERRORS", [])
            warnings = result.get("WARNINGS", [])
            if errors:
                msgs = []
                for e in errors:
                    row  = e.get("LINE", "?")
                    text = e.get("MESSAGE", str(e))
                    msgs.append(f"  Line {row}: {text}")
                return False, "Syntax errors:\n" + "\n".join(msgs)
            if warnings:
                msgs = []
                for w in warnings:
                    row  = w.get("LINE", "?")
                    text = w.get("MESSAGE", str(w))
                    msgs.append(f"  Line {row}: {text}")
                return True, "Warnings:\n" + "\n".join(msgs)
            return True, ""
        except Exception:
            # Syntax check RFC not available — skip silently
            return None, ""

    # ── Write Source ──────────────────────────────────────────────────────────

    def write_program(self, prog_name: str, source_code: str) -> tuple:
        """
        Write ABAP source lines to SAP.
        Call assign_to_transport() BEFORE this.
        Tries multiple RFC candidates in order — different SAP versions expose
        different function modules for source write.
        Returns (True, "") or (False, error_message).
        """
        name  = prog_name.upper()
        lines = [{"LINE": line} for line in source_code.splitlines()]
        errors = []

        # Candidate 1: RPY_PROGRAM_WRITE (available in some systems)
        try:
            self._mgr.execute(
                "RPY_PROGRAM_WRITE",
                PROG_INF={"PROG": name},
                SOURCE_EXTENDED=lines,
            )
            return True, ""
        except Exception as e:
            errors.append(f"RPY_PROGRAM_WRITE: {e}")

        # Candidate 2: RPY_PROGRAM_INSERT_MASTER
        try:
            self._mgr.execute(
                "RPY_PROGRAM_INSERT_MASTER",
                PROG_INF={"PROG": name},
                SOURCE_EXTENDED=lines,
            )
            return True, ""
        except Exception as e:
            errors.append(f"RPY_PROGRAM_INSERT_MASTER: {e}")

        # Candidate 3: RS_PROGRAM_WRITE (older systems)
        try:
            self._mgr.execute(
                "RS_PROGRAM_WRITE",
                PROGRAMM=name,
                QUELLCODE=lines,
            )
            return True, ""
        except Exception as e:
            errors.append(f"RS_PROGRAM_WRITE: {e}")

        # Candidate 4: RFC_ABAP_INSTALL_AND_RUN
        # Import: PROGRAMNAME (SY-REPID), MODE (SY-MSGTY, default 'F')
        # Tables: PROGRAM (PROGTAB — source lines), WRITES (output, ignored)
        # Export: ERRORMESSAGE
        try:
            result = self._mgr.execute(
                "RFC_ABAP_INSTALL_AND_RUN",
                PROGRAMNAME=name,
                MODE="F",
                PROGRAM=lines,
            )
            err_msg = result.get("ERRORMESSAGE", "").strip()
            if err_msg:
                errors.append(f"RFC_ABAP_INSTALL_AND_RUN: {err_msg}")
            else:
                return True, ""
        except Exception as e:
            errors.append(f"RFC_ABAP_INSTALL_AND_RUN: {e}")

        # Candidate 5: Z_ABAP_AI_WRITE_PROG (custom Z FM — INSERT REPORT wrapper)
        # IMPORTING: IV_PROG TYPE SYREPID
        # TABLES:    IT_SOURCE LIKE ZABAP_AI_SRCLINE
        # EXCEPTIONS: WRITE_ERROR
        try:
            self._mgr.execute(
                "Z_ABAP_AI_WRITE_PROG",
                IV_PROG=name,
                IT_SOURCE=lines,
            )
            return True, ""
        except Exception as e:
            errors.append(f"Z_ABAP_AI_WRITE_PROG: {e}")

        # All candidates failed
        msg = (
            "No write RFC available on this system.\n\n"
            + "\n".join(errors)
        )
        return False, msg
