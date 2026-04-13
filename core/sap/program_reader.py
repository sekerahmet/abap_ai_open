from core.sap.connection import SAPConnectionManager

class ProgramReader:
    def __init__(self, conn_params=None):
        self.mgr = SAPConnectionManager(conn_params)

    def fetch_code(self, name):
        try:
            res = self.mgr.execute("RPY_PROGRAM_READ", PROGRAM_NAME=name.upper())
            lines = res.get("SOURCE_EXTENDED", res.get("SOURCE", []))
            code = "\n".join([line.get("LINE", "") for line in lines])
            return code, {"NAME": name, "TYPE": "PROG"}
        except Exception as e:
            return None, str(e)

    def fetch_function_module(self, name):
        try:
            res = self.mgr.execute("RPY_FUNCTIONMODULE_READ", FUNCTIONNAME=name.upper())
            lines = res.get("SOURCE_EXTENDED", res.get("SOURCE", []))
            code = "\n".join([line.get("LINE", "") for line in lines])
            if not code:
                return None, f"Function Module '{name}' not found or has no source."
            return code, {"NAME": name, "TYPE": "FUNC"}
        except Exception as e:
            return None, str(e)

    def fetch_class_source(self, class_name):
        # Fetching public and private sections using class pool includes
        class_name = class_name.upper()
        # CP = Class Pool, PU = Public, PRI = Private
        includes = [f"{class_name.ljust(30)}CP", f"{class_name.ljust(30)}PU", f"{class_name.ljust(30)}PRI"]
        combined_code = []
        for inc in includes:
            code, _ = self.fetch_code(inc)
            if code: combined_code.append(code)
        
        if combined_code:
            return "\n\n".join(combined_code), {"NAME": class_name, "TYPE": "CLAS"}
        return None, "Class source not found"
