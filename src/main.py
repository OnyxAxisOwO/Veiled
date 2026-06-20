import sys
import ctypes
import ctypes.wintypes
from pathlib import Path


def check_single_instance() -> bool:
    mutex_name = "Global\\DAHServiceMutex_7f3a"
    handle = ctypes.windll.kernel32.CreateMutexW(None, False, mutex_name)
    if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        return False
    return True


def main():
    if not check_single_instance():
        sys.exit(0)

    # 显式设置 AppUserModelID，否则 Windows 可能不为本进程显示 toast 通知。
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("DAHService.DisplayAdapterHelper")
    except Exception:
        pass

    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtCore import Qt

    app = QApplication(sys.argv)
    app.setApplicationName("DAHService")
    app.setQuitOnLastWindowClosed(False)
    # 用 Fusion 风格，避免 Windows 原生风格按系统深/浅色给滚动区等控件上色，
    # 导致浅色主题下窗口里仍透出系统深色。具体调色板在 VeiledApp 里按主题应用。
    app.setStyle("Fusion")

    from .app import VeiledApp
    veiled = VeiledApp()
    veiled.start()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
