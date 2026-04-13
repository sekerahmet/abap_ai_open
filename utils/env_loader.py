import os
import sys
from dotenv import load_dotenv

def get_env_path():
    """
    Returns the absolute path to the .env file.
    Works for both dev mode and bundled PyInstaller .exe.
    """
    if getattr(sys, 'frozen', False):
        # Running as a bundled .exe
        # 1. Check same folder as .exe
        exe_dir = os.path.dirname(sys.executable)
        env_path = os.path.join(exe_dir, ".env")
        if os.path.exists(env_path):
            return env_path
        
        # 2. Check parent folder (e.g. if we are in dist/main.exe)
        parent_dir = os.path.dirname(exe_dir)
        env_path = os.path.join(parent_dir, ".env")
        if os.path.exists(env_path):
            return env_path
    else:
        # Running as a script (dev mode)
        script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        env_path = os.path.join(script_dir, ".env")
        if os.path.exists(env_path):
            return env_path

    # Fallback to current working directory
    return ".env"

def load_robust_env():
    """Load environment variables using the robust path."""
    path = get_env_path()
    load_dotenv(path)
    return path
