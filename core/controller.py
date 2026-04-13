from core.sap.connection import SAPConnectionManager
from core.sap.program_reader import ProgramReader
from core.sap.ddic_reader import DDICReader
from core.sap.program_writer import ProgramWriter
from core.ai.gemini_client import GeminiClient

class AnalysisController:
    def __init__(self):
        self.active_ai_client = None
        self.sap_mgr = None

    def initialize_sap(self, conn_params):
        self.sap_mgr = SAPConnectionManager(conn_params)
        self.program_reader = ProgramReader(conn_params)
        self.ddic_reader = DDICReader(conn_params)

    def fetch_program(self, conn_params, name):
        if not self.sap_mgr: self.initialize_sap(conn_params)
        return self.program_reader.fetch_code(name)

    def fetch_ddic_object(self, conn_params, name):
        if not self.sap_mgr: self.initialize_sap(conn_params)
        return self.ddic_reader.fetch_table(name)

    def check_objects_batch(self, conn_params, names):
        if not self.sap_mgr: self.initialize_sap(conn_params)
        return self.ddic_reader.check_objects_batch(names)

    def fetch_class_source(self, conn_params, name):
        if not self.sap_mgr: self.initialize_sap(conn_params)
        return self.program_reader.fetch_class_source(name)

    def fetch_function_module(self, conn_params, name):
        if not self.sap_mgr: self.initialize_sap(conn_params)
        return self.program_reader.fetch_function_module(name)

    def list_transports(self, conn_params: dict, user: str = "") -> list:
        return ProgramWriter(conn_params).list_open_transports(user)

    def check_syntax(self, conn_params: dict, prog_name: str, source: str) -> tuple:
        """(True, warnings), (False, errors), or (None, '') if RFC unavailable."""
        return ProgramWriter(conn_params).check_syntax(prog_name, source)

    def upload_program(self, conn_params: dict, prog_name: str,
                       source: str, trkorr: str,
                       skip_tr_assign: bool = False) -> tuple:
        writer = ProgramWriter(conn_params)
        if not skip_tr_assign:
            ok, err = writer.assign_to_transport(trkorr, prog_name)
            if not ok:
                # Return special marker so UI can ask user whether to continue
                return False, f"TR_ASSIGN_FAILED:{err}"
        return writer.write_program(prog_name, source)

    def initialize_ai(self, gemini_key):
        self.active_ai_client = GeminiClient(api_key=gemini_key)
        return self.active_ai_client

    def send_chat(self, message):
        if not self.active_ai_client: return "Error: AI not ready."
        return self.active_ai_client.send_message(message)

    def run_analysis(self, code, attrs, mode):
        if not self.active_ai_client: return "Error: AI not ready."
        return self.active_ai_client.run_analysis(code, attrs, mode)
