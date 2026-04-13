import pyrfc
import threading

class SAPConnectionManager:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls, conn_params=None):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(SAPConnectionManager, cls).__new__(cls)
                cls._instance.conn = None
                cls._instance.params = conn_params
            elif conn_params and conn_params != cls._instance.params:
                # Update params and reset connection if params changed
                cls._instance.params = conn_params
                cls._instance.conn = None
            return cls._instance

    def connect(self, params=None):
        if params: self.params = params
        if not self.params:
            raise ValueError("No SAP connection parameters provided.")

        # Always attempt fresh connection — ping() can also block on dead sessions
        if self.conn:
            try:
                self.conn.close()
            except:
                pass
            self.conn = None

        try:
            self.conn = pyrfc.Connection(**self.params)
            return self.conn
        except Exception as e:
            raise ConnectionError(f"RFC Connection Failed: {e}")

    def execute(self, func_name, **kwargs):
        conn = self.connect()
        return conn.call(func_name, **kwargs)
