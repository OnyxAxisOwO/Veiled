from PyQt6.QtWidgets import QSystemTrayIcon, QMenu
from PyQt6.QtGui import QIcon, QAction, QPixmap, QPainter, QColor
from PyQt6.QtCore import pyqtSignal, QObject


def create_default_icon() -> QIcon:
    pixmap = QPixmap(32, 32)
    pixmap.fill(QColor(0, 0, 0, 0))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor(128, 128, 128))
    painter.setPen(QColor(100, 100, 100))
    painter.drawRoundedRect(2, 2, 28, 28, 4, 4)
    painter.setBrush(QColor(180, 180, 180))
    painter.drawEllipse(8, 8, 16, 16)
    painter.end()
    return QIcon(pixmap)


class TrayManager(QObject):
    open_chat = pyqtSignal()
    open_settings = pyqtSignal()
    exit_app = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._tray: QSystemTrayIcon | None = None

    def show(self):
        if self._tray:
            return
        self._tray = QSystemTrayIcon()
        self._tray.setIcon(create_default_icon())
        self._tray.setToolTip("Display Adapter Helper")

        menu = QMenu()
        menu.setStyleSheet("""
            QMenu {
                background-color: #2d2d2d;
                color: #e0e0e0;
                border: 1px solid #404040;
                padding: 4px;
                font-family: 'Segoe UI';
                font-size: 12px;
            }
            QMenu::item {
                padding: 6px 24px;
                border-radius: 3px;
            }
            QMenu::item:selected {
                background-color: #404040;
            }
            QMenu::separator {
                height: 1px;
                background: #404040;
                margin: 4px 8px;
            }
        """)

        diag_action = QAction("诊断(D)", menu)
        diag_action.triggered.connect(self.open_chat.emit)
        menu.addAction(diag_action)

        config_action = QAction("配置(C)", menu)
        config_action.triggered.connect(self.open_settings.emit)
        menu.addAction(config_action)

        menu.addSeparator()

        update_action = QAction("检查更新(U)", menu)
        menu.addAction(update_action)

        menu.addSeparator()

        stop_action = QAction("停止服务(S)", menu)
        stop_action.triggered.connect(self.exit_app.emit)
        menu.addAction(stop_action)

        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._on_activated)
        self._tray.show()

    def hide(self):
        if self._tray:
            self._tray.hide()
            self._tray = None

    def _on_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.open_chat.emit()
