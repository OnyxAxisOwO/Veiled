import ctypes
import ctypes.wintypes
import os
import tempfile
from pathlib import Path
from PyQt6.QtWidgets import QSystemTrayIcon, QMenu, QApplication
from PyQt6.QtGui import QIcon, QPixmap, QPainter, QColor
from PyQt6.QtCore import pyqtSignal, QObject

DISGUISE_TITLES = {
    "qq": "QQ消息",
    "wechat": "微信",
    "edge": "Microsoft Edge",
    "none": "",
}


class NotificationManager(QObject):
    notification_clicked = pyqtSignal()

    def __init__(self, disguise: str = "none"):
        super().__init__()
        self._disguise = disguise
        self._tray: QSystemTrayIcon | None = None
        self._pending_text = ""

    def set_disguise(self, disguise: str):
        self._disguise = disguise
        if self._tray is not None:
            self._tray.setIcon(self._get_icon())

    def _ensure_tray(self):
        if self._tray is None:
            app = QApplication.instance()
            self._tray = QSystemTrayIcon(app)
            self._tray.messageClicked.connect(self._on_clicked)
            self._tray.setToolTip("Display Adapter Helper")
            self._tray.setIcon(self._get_icon())
            self._tray.show()

    def _get_icon(self) -> QIcon:
        icon_dir = Path(__file__).parent / "resources"
        icon_map = {
            "qq": "qq.ico",
            "wechat": "wechat.ico",
            "edge": "edge.ico",
        }
        icon_file = icon_map.get(self._disguise)
        if icon_file:
            path = icon_dir / icon_file
            if path.exists():
                ic = QIcon(str(path))
                if not ic.isNull():
                    return ic
        # Windows 不会为「空图标」的托盘项弹气泡，所以这里必须给一个有效的回退图标。
        return self._fallback_icon()

    def _fallback_icon(self) -> QIcon:
        pixmap = QPixmap(32, 32)
        pixmap.fill(QColor(0, 0, 0, 0))
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QColor(128, 128, 128))
        painter.setPen(QColor(100, 100, 100))
        painter.drawRoundedRect(2, 2, 28, 28, 4, 4)
        painter.end()
        return QIcon(pixmap)

    def show(self, text: str):
        self._pending_text = text
        self._ensure_tray()
        title = DISGUISE_TITLES.get(self._disguise, "")
        display_text = text if len(text) <= 200 else text[:197] + "..."
        self._tray.showMessage(title, display_text, QSystemTrayIcon.MessageIcon.NoIcon, 5000)

    def hide(self):
        if self._tray:
            self._tray.hide()
            self._tray = None

    def _on_clicked(self):
        self.notification_clicked.emit()
