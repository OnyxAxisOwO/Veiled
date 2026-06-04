import ctypes
import subprocess
from PyQt6.QtCore import QObject, QTimer, pyqtSignal


class EnvironmentMonitor(QObject):
    silent_mode_changed = pyqtSignal(bool)

    def __init__(self, suspicious_processes: list[str]):
        super().__init__()
        self._suspicious = [p.lower() for p in suspicious_processes]
        self._silent = False
        self._taskmgr_active = False
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._check)

    @property
    def is_silent(self) -> bool:
        return self._silent

    @property
    def is_taskmgr_active(self) -> bool:
        return self._taskmgr_active

    def start(self, interval_ms: int = 3000):
        self._timer.start(interval_ms)

    def stop(self):
        self._timer.stop()

    def update_process_list(self, processes: list[str]):
        self._suspicious = [p.lower() for p in processes]

    def _check(self):
        try:
            result = subprocess.run(
                ["tasklist", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            lines = result.stdout.strip().split("\n")
            running = set()
            for line in lines:
                parts = line.strip().strip('"').split('","')
                if parts:
                    running.add(parts[0].lower().strip('"'))
        except Exception:
            return

        self._taskmgr_active = "taskmgr.exe" in running

        was_silent = self._silent
        self._silent = any(proc in running for proc in self._suspicious)
        if self._silent != was_silent:
            self.silent_mode_changed.emit(self._silent)
