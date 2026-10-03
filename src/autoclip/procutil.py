"""Fichier PID partagé entre le pipeline et l'application de contrôle."""

import os
import sys
from pathlib import Path

PID_FILE = Path("data/autoclip.pid")
LOG_FILE = Path("data/autoclip.log")


def pid_alive(pid: int) -> bool:
    if sys.platform == "win32":
        # Sous Windows, os.kill(pid, 0) tuerait le processus : on interroge l'API Win32.
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        kernel32.CloseHandle(handle)
        return code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def running_pid(root: Path = Path()) -> int | None:
    """PID du pipeline en cours d'exécution, ou None."""
    try:
        pid = int((root / PID_FILE).read_text())
    except (OSError, ValueError):
        return None
    return pid if pid_alive(pid) else None
