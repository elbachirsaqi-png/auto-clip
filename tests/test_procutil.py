import os
import subprocess
import sys

from autoclip.procutil import PID_FILE, pid_alive, running_pid


def test_current_process_is_alive():
    assert pid_alive(os.getpid())


def test_finished_process_is_not_alive():
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    assert not pid_alive(proc.pid)


def test_running_pid_reads_pid_file(tmp_path):
    (tmp_path / PID_FILE).parent.mkdir(parents=True)
    (tmp_path / PID_FILE).write_text(str(os.getpid()))
    assert running_pid(tmp_path) == os.getpid()
    (tmp_path / PID_FILE).write_text("not a pid")
    assert running_pid(tmp_path) is None
