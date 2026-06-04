import ctypes
import io
from PyQt6.QtWidgets import QWidget, QApplication
from PyQt6.QtCore import Qt, QRect, QPoint, pyqtSignal
from PyQt6.QtGui import QPainter, QColor, QPixmap, QPen, QCursor

WDA_EXCLUDEFROMCAPTURE = 0x00000011


def grab_fullscreen_png() -> bytes | None:
    """抓取主屏整屏并返回 PNG 字节（全分辨率）。"""
    from PyQt6.QtCore import QBuffer, QIODevice
    screen = QApplication.primaryScreen()
    if not screen:
        return None
    pixmap = screen.grabWindow(0)
    if pixmap.isNull():
        return None
    qbuf = QBuffer()
    qbuf.open(QIODevice.OpenModeFlag.WriteOnly)
    pixmap.save(qbuf, "PNG")
    return bytes(qbuf.data())


class ScreenshotOverlay(QWidget):
    captured = pyqtSignal(bytes)
    cancelled = pyqtSignal()

    def __init__(self, screenshot_protection: bool = True):
        super().__init__(None)
        self._screenshot_protection = screenshot_protection
        self._start_pos: QPoint | None = None
        self._current_pos: QPoint | None = None
        self._selecting = False
        self._full_pixmap: QPixmap | None = None
        self._dpr: float = 1.0

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setCursor(QCursor(Qt.CursorShape.CrossCursor))

    def start_capture(self):
        screen = QApplication.primaryScreen()
        if not screen:
            self.cancelled.emit()
            return
        # 在显示覆盖层之前抓屏，确保不把遮罩本身抓进去。
        self._dpr = screen.devicePixelRatio()
        pixmap = screen.grabWindow(0)
        # grabWindow 返回的是物理像素位图，设置 dpr 后绘制/坐标才与逻辑像素一致。
        pixmap.setDevicePixelRatio(self._dpr)
        self._full_pixmap = pixmap
        if pixmap.isNull():
            self.cancelled.emit()
            return

        self.setGeometry(screen.geometry())
        self.showFullScreen()
        self.raise_()
        self.activateWindow()
        self.setFocus()
        if self._screenshot_protection:
            hwnd = int(self.winId())
            ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)

    def paintEvent(self, event):
        if not self._full_pixmap:
            return
        painter = QPainter(self)
        painter.drawPixmap(0, 0, self._full_pixmap)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 100))

        if self._start_pos and self._current_pos:
            selection = QRect(self._start_pos, self._current_pos).normalized()
            painter.setClipRect(selection)
            painter.drawPixmap(0, 0, self._full_pixmap)
            painter.setClipping(False)
            pen = QPen(QColor(59, 130, 246), 2)
            painter.setPen(pen)
            painter.drawRect(selection)

        painter.end()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._start_pos = event.position().toPoint()
            self._current_pos = self._start_pos
            self._selecting = True

    def mouseMoveEvent(self, event):
        if self._selecting:
            self._current_pos = event.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._selecting:
            self._selecting = False
            if self._start_pos and self._current_pos:
                rect = QRect(self._start_pos, self._current_pos).normalized()
                if rect.width() > 10 and rect.height() > 10:
                    self._do_capture(rect)
                else:
                    self.cancelled.emit()
            self.hide()

    def _do_capture(self, rect: QRect):
        if not self._full_pixmap:
            self.cancelled.emit()
            return
        # 选区是逻辑像素，位图是物理像素，按 dpr 换算后裁剪以保证全分辨率。
        dpr = self._dpr or 1.0
        phys = QRect(
            int(rect.x() * dpr), int(rect.y() * dpr),
            int(rect.width() * dpr), int(rect.height() * dpr),
        )
        cropped = self._full_pixmap.copy(phys)
        buf = io.BytesIO()
        from PyQt6.QtCore import QBuffer, QIODevice
        qbuf = QBuffer()
        qbuf.open(QIODevice.OpenModeFlag.WriteOnly)
        cropped.save(qbuf, "PNG")
        self.captured.emit(bytes(qbuf.data()))
        self._full_pixmap = None

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self._selecting = False
            self.hide()
            self.cancelled.emit()
