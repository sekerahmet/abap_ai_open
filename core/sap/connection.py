"""
SAPConnectionManager — thin wrapper around pyrfc.Connection.

Design:
  * No shared state. Every manager instance owns only its parameter dict,
    so instances can be created freely from any thread.
  * execute()  → opens a connection, performs ONE call, closes it.
  * session()  → context manager that keeps ONE connection open for a batch
                 of calls (class includes, TADIR chunks, table batches).

pyrfc expects the key 'saprouter' (not 'router'); the UI maps it in
App.get_current_conn() before the dict reaches this class.
"""

from contextlib import contextmanager

RFC_SDK_HINT = ("SAP RFC SDK not available — copy sapnwrfc.dll, icudt50.dll, icuin50.dll, icuuc50.dll "
                "next to main.exe (see KURULUM.md / Setup check).")


def _pyrfc():
    """Import pyrfc lazily so the IDE starts (Local mode works) even without the SAP RFC SDK DLLs."""
    try:
        import pyrfc
        return pyrfc
    except Exception as e:
        raise ConnectionError(f"{RFC_SDK_HINT} [{e}]") from None


class SAPConnectionManager:
    def __init__(self, conn_params=None):
        self.params = dict(conn_params or {})

    def _open(self):
        if not self.params.get("ashost"):
            raise ValueError("No SAP connection parameters provided (ashost missing).")
        pyrfc = _pyrfc()
        try:
            return pyrfc.Connection(**self.params)
        except Exception as e:
            raise ConnectionError(f"RFC Connection Failed: {e}")

    @staticmethod
    def _close(conn):
        try:
            conn.close()
        except Exception:
            pass

    def execute(self, func_name, **kwargs):
        """Open → call → close. Safe to use concurrently from several threads."""
        conn = self._open()
        try:
            return conn.call(func_name, **kwargs)
        finally:
            self._close(conn)

    @contextmanager
    def session(self):
        """
        Keep one connection open for several calls:

            with mgr.session() as call:
                a = call("RPY_PROGRAM_READ", PROGRAM_NAME="ZA")
                b = call("RPY_PROGRAM_READ", PROGRAM_NAME="ZB")
        """
        conn = self._open()
        try:
            yield lambda func_name, **kwargs: conn.call(func_name, **kwargs)
        finally:
            self._close(conn)
