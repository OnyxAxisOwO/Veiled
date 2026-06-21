from PyQt6.QtWidgets import QSystemTrayIcon, QApplication
from PyQt6.QtGui import QIcon, QPixmap, QPainter, QColor
from PyQt6.QtCore import pyqtSignal, QObject


class NotificationManager(QObject):
    notification_clicked = pyqtSignal()

    def __init__(self, title: str = ""):
        super().__init__()
        self._title = title
        self._tray: QSystemTrayIcon | None = None
        self._owns_tray = False
        self._pending_text = ""

    def set_tray(self, tray: "QSystemTrayIcon | None"):
        """复用外部（菜单）托盘图标弹通知，避免出现第二个托盘图标。"""
        if tray is None or tray is self._tray:
            return
        self._tray = tray
        self._owns_tray = False
        try:
            tray.messageClicked.connect(self._on_clicked)
        except Exception:
            pass

    def set_title(self, title: str):
        self._title = title

    def _ensure_tray(self):
        if self._tray is None:
            app = QApplication.instance()
            self._tray = QSystemTrayIcon(app)
            self._owns_tray = True
            self._tray.messageClicked.connect(self._on_clicked)
            self._tray.setToolTip("AI Assistant")
            self._tray.setIcon(self._default_icon())
            self._tray.show()

    def _default_icon(self) -> QIcon:
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
        display_text = text if len(text) <= 200 else text[:197] + "..."
        self._tray.showMessage(self._title, display_text, QSystemTrayIcon.MessageIcon.NoIcon, 5000)

    def hide(self):
        # 仅在自己创建的托盘上才销毁；共享的菜单托盘交由 TrayManager 管理，
        # 这样 boss key 隐藏通知时不会连带把常驻的菜单图标也一起隐藏。
        if self._tray is not None and self._owns_tray:
            self._tray.hide()
            self._tray = None
            self._owns_tray = False

    def _on_clicked(self):
        self.notification_clicked.emit()
