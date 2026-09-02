"""
AnalysisController — single read-only facade for all SAP operations.

Stateless: every call builds a reader from the conn_params it receives, so the
active profile in the sidebar is always the system that gets called.
"""

from core.sap.program_reader import ProgramReader
from core.sap.ddic_reader import DDICReader


class AnalysisController:
    # ── Source ────────────────────────────────────────────────────────────────
    def fetch_program(self, conn_params, name):
        return ProgramReader(conn_params).fetch_code(name)

    def fetch_programs(self, conn_params, names):
        return ProgramReader(conn_params).fetch_many(names)

    def fetch_class_source(self, conn_params, name):
        return ProgramReader(conn_params).fetch_class_source(name)

    def fetch_function_module(self, conn_params, name):
        return ProgramReader(conn_params).fetch_function_module(name)

    # ── Dictionary ────────────────────────────────────────────────────────────
    def fetch_ddic_object(self, conn_params, name):
        return DDICReader(conn_params).fetch_table(name)

    def fetch_ddic_objects(self, conn_params, names):
        return DDICReader(conn_params).fetch_tables_batch(names)

    def fetch_table_data(self, conn_params, name, where_clause="", max_rows=200):
        return DDICReader(conn_params).fetch_table_data(name, where_clause, max_rows)

    def check_objects_batch(self, conn_params, names):
        return DDICReader(conn_params).check_objects_batch(names)
