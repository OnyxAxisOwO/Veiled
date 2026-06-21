import ctypes
import time
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLineEdit,
    QPushButton, QLabel, QScrollArea, QFrame, QFileDialog, QStackedWidget,
    QMenu,
)
from PyQt6.QtCore import Qt, pyqtSignal, QPropertyAnimation, QEasingCurve, QPoint, QTimer, QSize
from PyQt6.QtGui import QFont, QKeyEvent, QPixmap, QPainter

from .theme import hex_to_rgb_str

WDA_EXCLUDEFROMCAPTURE = 0x00000011


def set_display_affinity(hwnd: int, affinity: int = WDA_EXCLUDEFROMCAPTURE):
    ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, affinity)


def set_toolwindow(hwnd: int):
    GWL_EXSTYLE = -20
    WS_EX_TOOLWINDOW = 0x00000080
    style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_TOOLWINDOW)


class MessageBubble(QFrame):
    def __init__(self, text: str, is_user: bool, parent=None, image_data: bytes = None,
                 model_label: str = ""):
        super().__init__(parent)
        self.setObjectName("user_bubble" if is_user else "ai_bubble")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 6)
        layout.setSpacing(6)

        # 多模型并行时，每条 AI 气泡顶部标注是哪个模型的回答（单模型时不显示）。
        if model_label:
            tag = QLabel(model_label)
            tag.setObjectName("model_tag")
            tag.setFont(QFont("Microsoft YaHei", 8, QFont.Weight.Bold))
            layout.addWidget(tag)

        if image_data:
            pixmap = QPixmap()
            pixmap.loadFromData(image_data)
            if not pixmap.isNull():
                max_w = 280
                if pixmap.width() > max_w:
                    pixmap = pixmap.scaledToWidth(max_w, Qt.TransformationMode.SmoothTransformation)
                img_label = QLabel()
                img_label.setObjectName("bubble_image")
                img_label.setPixmap(pixmap)
                layout.addWidget(img_label)

        self._label = QLabel(text)
        self._label.setWordWrap(True)
        self._label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._label.setFont(QFont("Microsoft YaHei", 10))
        self._label.setVisible(bool(text))
        layout.addWidget(self._label)

        self._stats_label = QLabel()
        self._stats_label.setObjectName("stats_label")
        self._stats_label.setVisible(False)
        layout.addWidget(self._stats_label)

        self._timer: QTimer | None = None
        self._t_start: float | None = None

    def start_timer(self):
        """从请求发起即开始计时，哪怕还没收到任何输出。"""
        self._t_start = time.monotonic()
        self._stats_label.setText("0.0s")
        self._stats_label.setVisible(True)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(100)

    def _tick(self):
        if self._t_start is not None:
            self._stats_label.setText(f"{time.monotonic() - self._t_start:.1f}s")

    def append_text(self, text: str):
        self._label.setText(self._label.text() + text)
        self._label.setVisible(True)

    def set_stats(self, elapsed: float = 0.0, tokens_in: int = 0, tokens_out: int = 0):
        if self._timer:
            self._timer.stop()
            self._timer = None
        if elapsed <= 0 and self._t_start is not None:
            elapsed = time.monotonic() - self._t_start
        parts = [f"{elapsed:.1f}s"]
        if tokens_in > 0:
            parts.append(f"{tokens_in} in")
        if tokens_out > 0:
            parts.append(f"{tokens_out} out")
        self._stats_label.setText("  ·  ".join(parts))
        self._stats_label.setVisible(True)


class MultiSelectMenu(QMenu):
    """点击「可勾选」项时保持菜单打开，以便一次连续勾选多个模型；
    点击普通项（如「管理服务商」）才照常关闭。"""

    def mouseReleaseEvent(self, event):
        action = self.activeAction()
        if action is not None and action.isCheckable() and action.isEnabled():
            action.trigger()   # 切换勾选并触发 toggled，但不关闭菜单
            return
        super().mouseReleaseEvent(event)


class ChatInputEdit(QLineEdit):
    submit = pyqtSignal()

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key.Key_Return and not event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self.submit.emit()
            return
        super().keyPressEvent(event)


class ConvItem(QFrame):
    clicked = pyqtSignal(str)

    def __init__(self, conv_id: str, title: str, updated_at: float, active: bool = False):
        super().__init__()
        self._conv_id = conv_id
        self.setObjectName("conv_item_active" if active else "conv_item")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)

        title_label = QLabel(title or "(无标题)")
        title_label.setObjectName("conv_title")
        title_label.setFont(QFont("Microsoft YaHei", 10))

        import time as _time
        dt = _time.localtime(updated_at)
        date_str = _time.strftime("%m/%d %H:%M", dt)
        date_label = QLabel(date_str)
        date_label.setObjectName("conv_date")
        date_label.setFont(QFont("Microsoft YaHei", 9))

        layout.addWidget(title_label, 1)
        layout.addWidget(date_label)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._conv_id)


class ChatContainer(QFrame):
    """Container frame for the chat window. The themed (translucent) background is
    painted by the QSS #chat_container rule; on top of it, in the message-area
    region, this paints the optional user background image. DPI-aware so the image
    stays crisp on scaled displays."""

    _TITLE_H = 42
    _INPUT_H = 50

    def __init__(self, parent=None, theme: str = "dark"):
        super().__init__(parent)
        self._theme = theme
        self._bg_pixmap: QPixmap | None = None
        self._bg_fill_mode = "fill"

    def set_theme(self, theme: str):
        self._theme = theme
        self.update()

    def set_background(self, pixmap: "QPixmap | None", fill_mode: str = "fill"):
        self._bg_pixmap = pixmap
        self._bg_fill_mode = fill_mode
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)  # QSS rounded themed background + border
        pm = self._bg_pixmap
        if not pm or pm.isNull():
            return
        r = self.rect().adjusted(0, self._TITLE_H, 0, -self._INPUT_H)
        if r.width() <= 0 or r.height() <= 0:
            return

        dpr = self.devicePixelRatioF()
        painter = QPainter(self)
        painter.setClipRect(r)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        mode = self._bg_fill_mode

        def _centered(img: QPixmap):
            lw, lh = img.width() / dpr, img.height() / dpr
            painter.drawPixmap(QPoint(int(r.x() + (r.width() - lw) / 2),
                                      int(r.y() + (r.height() - lh) / 2)), img)

        if mode in ("fill", "fit", "stretch"):
            target = QSize(max(1, round(r.width() * dpr)), max(1, round(r.height() * dpr)))
            am = {
                "fill": Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                "fit": Qt.AspectRatioMode.KeepAspectRatio,
                "stretch": Qt.AspectRatioMode.IgnoreAspectRatio,
            }[mode]
            scaled = pm.scaled(target, am, Qt.TransformationMode.SmoothTransformation)
            scaled.setDevicePixelRatio(dpr)
            _centered(scaled)
        elif mode == "tile":
            tile = QPixmap(pm)
            tile.setDevicePixelRatio(dpr)
            painter.drawTiledPixmap(r, tile)
        else:  # center
            disp = QPixmap(pm)
            disp.setDevicePixelRatio(dpr)
            _centered(disp)
        painter.end()


class ChatWindow(QWidget):
    message_sent = pyqtSignal(str)
    file_sent = pyqtSignal(str)
    screenshot_requested = pyqtSignal()
    command_entered = pyqtSignal(str)
    close_requested = pyqtSignal()
    open_settings_requested = pyqtSignal()
    conversation_selected = pyqtSignal(str)
    models_changed = pyqtSignal(list)             # 选中集合: [(provider_id, model_id), ...]，首项为主模型
    manage_providers_requested = pyqtSignal()

    def __init__(self, width=440, height=560, opacity=0.9, position="bottom_right",
                 screenshot_protection=True, theme="dark", accent="#3b82f6"):
        super().__init__(None)
        self._width = width
        self._height = height
        self._opacity = opacity
        self._position = position
        self._screenshot_protection = screenshot_protection
        self._theme = theme
        self._accent = accent or "#3b82f6"
        self._drag_pos = None
        self._bubbles: list[MessageBubble] = []
        self._current_ai_bubble: MessageBubble | None = None

        # 模型选择数据
        self._providers: list[dict] = []
        self._active_pid: str = ""
        self._active_mid: str = ""
        self._selected: list[tuple] = []   # 选中的 (pid, mid) 集合，首项为主模型

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(width, height)
        self._setup_ui()
        self._apply_style()

    def showEvent(self, event):
        super().showEvent(event)
        hwnd = int(self.winId())
        set_toolwindow(hwnd)
        if self._screenshot_protection:
            set_display_affinity(hwnd)
        self._position_window()
        self._fade_in()
        QTimer.singleShot(100, self._input.setFocus)

    def _position_window(self):
        from PyQt6.QtWidgets import QApplication
        screen = QApplication.primaryScreen()
        if not screen:
            return
        geo = screen.availableGeometry()
        positions = {
            "bottom_right": QPoint(geo.right() - self._width - 20, geo.bottom() - self._height - 20),
            "bottom_left": QPoint(geo.left() + 20, geo.bottom() - self._height - 20),
            "top_right": QPoint(geo.right() - self._width - 20, geo.top() + 20),
            "top_left": QPoint(geo.left() + 20, geo.top() + 20),
            "center": QPoint(
                geo.center().x() - self._width // 2,
                geo.center().y() - self._height // 2,
            ),
        }
        self.move(positions.get(self._position, positions["bottom_right"]))

    def _fade_in(self):
        self.setWindowOpacity(0.0)
        self._anim = QPropertyAnimation(self, b"windowOpacity")
        self._anim.setDuration(150)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(self._opacity)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.start()

    def _setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        container = ChatContainer(self, theme=self._theme)
        container.setObjectName("chat_container")
        self._container = container
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(0)

        # ── Title bar ──────────────────────────────────────────────────────────
        title_bar = QFrame()
        title_bar.setObjectName("title_bar")
        title_bar.setFixedHeight(42)
        title_layout = QHBoxLayout(title_bar)
        title_layout.setContentsMargins(10, 0, 6, 0)
        title_layout.setSpacing(4)

        dot = QLabel("●")
        dot.setObjectName("dot")
        dot.setFont(QFont("", 8))
        title_layout.addWidget(dot)

        # 模型切换芯片：点击弹出服务商 / 模型菜单
        self._model_btn = QPushButton("选择模型 ▾")
        self._model_btn.setObjectName("model_chip")
        self._model_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._model_btn.setToolTip("切换服务商 / 模型")
        self._model_btn.clicked.connect(self._open_model_menu)
        title_layout.addWidget(self._model_btn)

        title_layout.addStretch()

        new_btn = QPushButton("＋")
        new_btn.setObjectName("title_btn")
        new_btn.setFixedSize(30, 30)
        new_btn.setToolTip("新对话")
        new_btn.clicked.connect(lambda: self.command_entered.emit("/new"))
        title_layout.addWidget(new_btn)

        self._more_btn = QPushButton("⋯")
        self._more_btn.setObjectName("title_btn")
        self._more_btn.setFixedSize(30, 30)
        self._more_btn.setToolTip("更多")
        self._more_btn.clicked.connect(self._open_more_menu)
        title_layout.addWidget(self._more_btn)

        close_btn = QPushButton("×")
        close_btn.setObjectName("title_btn_close")
        close_btn.setFixedSize(30, 30)
        close_btn.clicked.connect(self.close_requested.emit)
        title_layout.addWidget(close_btn)

        container_layout.addWidget(title_bar)

        # ── Stacked area: messages / conversations panel ───────────────────────
        self._stack = QStackedWidget()
        self._stack.setObjectName("main_stack")
        self._stack.setAutoFillBackground(False)

        # Page 0: message scroll area
        self._scroll_area = QScrollArea()
        self._scroll_area.setObjectName("message_area")
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._scroll_area.setAutoFillBackground(False)

        self._message_container = QWidget()
        self._message_layout = QVBoxLayout(self._message_container)
        self._message_layout.setContentsMargins(10, 10, 10, 10)
        self._message_layout.setSpacing(8)
        self._message_layout.addStretch()
        self._scroll_area.setWidget(self._message_container)
        # NB: QScrollArea.setWidget() re-enables autoFillBackground on the widget,
        # so this must come AFTER setWidget or the opaque palette fill hides the
        # container's background image.
        self._message_container.setAutoFillBackground(False)
        self._scroll_area.viewport().setAutoFillBackground(False)

        self._stack.addWidget(self._scroll_area)

        # Page 1: conversations panel
        self._convs_panel = self._build_convs_panel()
        self._stack.addWidget(self._convs_panel)

        container_layout.addWidget(self._stack, 1)

        # ── Input bar ──────────────────────────────────────────────────────────
        input_bar = QFrame()
        input_bar.setObjectName("input_bar")
        input_bar.setFixedHeight(50)
        input_layout = QHBoxLayout(input_bar)
        input_layout.setContentsMargins(8, 7, 8, 7)
        input_layout.setSpacing(4)

        attach_btn = QPushButton("📎")
        attach_btn.setObjectName("icon_btn")
        attach_btn.setFixedSize(32, 32)
        attach_btn.setToolTip("发送文件 / 图片")
        attach_btn.clicked.connect(self._on_attach)
        input_layout.addWidget(attach_btn)

        screenshot_btn = QPushButton("📷")
        screenshot_btn.setObjectName("icon_btn")
        screenshot_btn.setFixedSize(32, 32)
        screenshot_btn.setToolTip("截图提问")
        screenshot_btn.clicked.connect(self.screenshot_requested.emit)
        input_layout.addWidget(screenshot_btn)

        self._input = ChatInputEdit()
        self._input.setObjectName("chat_input")
        self._input.setPlaceholderText("发消息，或点 ⋯ 查看功能…")
        self._input.submit.connect(self._on_send)
        input_layout.addWidget(self._input, 1)

        send_btn = QPushButton("发送")
        send_btn.setObjectName("send_btn")
        send_btn.setFixedSize(52, 34)
        send_btn.clicked.connect(self._on_send)
        input_layout.addWidget(send_btn)

        container_layout.addWidget(input_bar)
        main_layout.addWidget(container)

    def _build_convs_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("convs_panel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)

        header = QHBoxLayout()
        back_btn = QPushButton("← 返回")
        back_btn.setObjectName("back_btn")
        back_btn.clicked.connect(self._show_chat_view)
        header.addWidget(back_btn)
        header.addStretch()
        new_btn = QPushButton("＋ 新对话")
        new_btn.setObjectName("new_conv_btn")
        new_btn.clicked.connect(lambda: (self.command_entered.emit("/new"), self._show_chat_view()))
        header.addWidget(new_btn)
        layout.addLayout(header)

        sep = QFrame()
        sep.setObjectName("panel_sep")
        sep.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(sep)

        self._convs_scroll = QScrollArea()
        self._convs_scroll.setObjectName("convs_scroll")
        self._convs_scroll.setWidgetResizable(True)
        self._convs_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._convs_scroll.viewport().setAutoFillBackground(False)
        self._convs_inner = QWidget()
        self._convs_inner_layout = QVBoxLayout(self._convs_inner)
        self._convs_inner_layout.setContentsMargins(0, 0, 0, 0)
        self._convs_inner_layout.setSpacing(4)
        self._convs_inner_layout.addStretch()
        self._convs_scroll.setWidget(self._convs_inner)
        self._convs_inner.setAutoFillBackground(False)  # after setWidget; see message scroll note
        layout.addWidget(self._convs_scroll, 1)

        return panel

    def _show_chat_view(self):
        self._stack.setCurrentIndex(0)
        QTimer.singleShot(50, self._input.setFocus)

    def show_conversations(self, conversations: list[dict], current_id: str = ""):
        while self._convs_inner_layout.count() > 1:
            item = self._convs_inner_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        for conv in conversations:
            item = ConvItem(
                conv["id"], conv.get("title", ""),
                conv.get("updated_at", 0),
                active=(conv["id"] == current_id),
            )
            item.clicked.connect(self._on_conv_selected)
            self._convs_inner_layout.insertWidget(self._convs_inner_layout.count() - 1, item)

        self._stack.setCurrentIndex(1)

    def _on_conv_selected(self, conv_id: str):
        self._show_chat_view()
        self.conversation_selected.emit(conv_id)

    # ── Model switcher ──────────────────────────────────────────────────────

    def set_model_options(self, providers: list[dict], active_pid: str, active_mid: str,
                          selected: list = None):
        """由 app 注入可选的服务商 / 模型、当前主模型及选中集合，刷新顶部芯片。"""
        self._providers = providers or []
        self._active_pid = active_pid or ""
        self._active_mid = active_mid or ""
        if selected:
            self._selected = [tuple(s) for s in selected]
        elif self._active_mid:
            self._selected = [(self._active_pid, self._active_mid)]
        else:
            self._selected = []
        self._refresh_model_chip()

    def _model_display_name(self, pid: str, mid: str) -> tuple[str, bool]:
        for p in self._providers:
            if p.get("id") == pid:
                for m in p.get("models", []):
                    if m.get("id") == mid:
                        return (m.get("name") or mid or "?"), bool(m.get("vision"))
        return (mid or "?"), False

    def _refresh_model_chip(self):
        primary = self._selected[0] if self._selected else (self._active_pid, self._active_mid)
        name, vision = self._model_display_name(primary[0], primary[1])
        if not self._selected:
            name = "选择模型"
        label = f"👁 {name}" if vision else name
        extra = len(self._selected) - 1
        if extra > 0:
            label = f"{label}  ＋{extra}"
        self._model_btn.setText(f"{label}  ▾")
        if len(self._selected) > 1:
            names = "、".join(self._model_display_name(p, m)[0] for p, m in self._selected)
            self._model_btn.setToolTip(f"并行 {len(self._selected)} 个模型：{names}\n（再次点击可勾选/取消）")
        else:
            self._model_btn.setToolTip("切换服务商 / 模型；勾选多个即并行提问")

    def _open_model_menu(self):
        menu = self._styled_menu(multi=True)

        if not self._providers:
            act = menu.addAction("（尚未配置服务商）")
            act.setEnabled(False)
        for p in self._providers:
            menu.addSection(p.get("name") or p.get("id") or "服务商")
            models = p.get("models", [])
            if not models:
                act = menu.addAction("  （无模型）")
                act.setEnabled(False)
                continue
            for m in models:
                mid = m.get("id", "")
                label = m.get("name") or mid
                if m.get("vision"):
                    label = f"👁 {label}"
                act = menu.addAction(label)
                act.setCheckable(True)
                act.setChecked((p.get("id"), mid) in self._selected)   # 先设勾选再连信号，避免构建时误触发
                act.toggled.connect(
                    lambda checked, pid=p.get("id"), m_id=mid, a=act: self._toggle_model(pid, m_id, checked, a)
                )

        menu.addSeparator()
        manage = menu.addAction("管理服务商 / 模型…")
        manage.triggered.connect(self.manage_providers_requested.emit)

        menu.exec(self._model_btn.mapToGlobal(self._model_btn.rect().bottomLeft()))

    def _toggle_model(self, pid: str, mid: str, checked: bool, action):
        key = (pid, mid)
        sel = list(self._selected)
        if checked:
            if key not in sel:
                sel.append(key)
        else:
            if key in sel:
                if len(sel) == 1:
                    # 至少保留一个模型：撤销本次取消，屏蔽信号避免 setChecked 再触发 toggled
                    action.blockSignals(True)
                    action.setChecked(True)
                    action.blockSignals(False)
                    return
                sel.remove(key)
        self._selected = sel
        self._refresh_model_chip()
        self.models_changed.emit([list(k) for k in sel])

    # ── More menu (commands) ──────────────────────────────────────────────────

    def _open_more_menu(self):
        menu = self._styled_menu()
        items = [
            ("＋  新对话", "/new"),
            ("🕘  历史对话", "/list"),
            ("🧹  清空对话", "/clear"),
            ("🗑  删除对话", "/delete"),
            (None, None),
            ("⬇  导出对话", "/export"),
            ("🌐  翻译剪贴板", "/t"),
            ("📝  总结剪贴板", "/s"),
            (None, None),
            ("⚙  设置", "/settings"),
            ("❔  帮助", "/help"),
        ]
        for label, cmd in items:
            if label is None:
                menu.addSeparator()
                continue
            act = menu.addAction(label)
            act.triggered.connect(lambda _checked, c=cmd: self.command_entered.emit(c))
        menu.exec(self._more_btn.mapToGlobal(self._more_btn.rect().bottomLeft()))

    def _styled_menu(self, multi: bool = False) -> QMenu:
        menu = MultiSelectMenu(self) if multi else QMenu(self)
        argb = hex_to_rgb_str(self._accent)
        if self._theme == "dark":
            css = """
                QMenu {
                    background-color: #2b2b2b; color: #e0e0e0;
                    border: 1px solid rgba(255,255,255,25);
                    border-radius: 8px; padding: 5px;
                    font-family: 'Microsoft YaHei'; font-size: 12px;
                }
                QMenu::item { padding: 6px 22px; border-radius: 5px; }
                QMenu::item:selected { background-color: rgba(59,130,246,160); color: white; }
                QMenu::item:disabled { color: #777; }
                QMenu::separator { height: 1px; background: rgba(255,255,255,20); margin: 5px 8px; }
            """
        else:
            css = """
                QMenu {
                    background-color: #ffffff; color: #333;
                    border: 1px solid rgba(0,0,0,25);
                    border-radius: 8px; padding: 5px;
                    font-family: 'Microsoft YaHei'; font-size: 12px;
                }
                QMenu::item { padding: 6px 22px; border-radius: 5px; }
                QMenu::item:selected { background-color: rgba(59,130,246,180); color: white; }
                QMenu::item:disabled { color: #aaa; }
                QMenu::separator { height: 1px; background: rgba(0,0,0,15); margin: 5px 8px; }
            """
        menu.setStyleSheet(css.replace("59,130,246", argb))
        return menu

    # ── Message operations ────────────────────────────────────────────────────

    def add_user_message(self, text: str, image_data: bytes = None):
        bubble = MessageBubble(text, is_user=True, image_data=image_data)
        self._message_layout.insertWidget(self._message_layout.count() - 1, bubble)
        self._bubbles.append(bubble)
        self._scroll_to_bottom()

    def start_ai_message(self, model_label: str = "") -> MessageBubble:
        bubble = MessageBubble("", is_user=False, model_label=model_label)
        bubble.start_timer()
        self._message_layout.insertWidget(self._message_layout.count() - 1, bubble)
        self._bubbles.append(bubble)
        self._current_ai_bubble = bubble
        self._scroll_to_bottom()
        return bubble

    def add_ai_message(self, text: str) -> MessageBubble:
        """加入一条已完成的 AI 消息（用于加载历史），不计时、不显示统计。"""
        bubble = MessageBubble(text, is_user=False)
        self._message_layout.insertWidget(self._message_layout.count() - 1, bubble)
        self._bubbles.append(bubble)
        self._scroll_to_bottom()
        return bubble

    def append_ai_text(self, text: str):
        if self._current_ai_bubble:
            self._current_ai_bubble.append_text(text)
            self._scroll_to_bottom()

    def finish_ai_message(self, elapsed: float = 0.0, tokens_in: int = 0, tokens_out: int = 0):
        if self._current_ai_bubble:
            self._current_ai_bubble.set_stats(elapsed, tokens_in, tokens_out)
        self._current_ai_bubble = None

    def add_system_message(self, text: str):
        label = QLabel(text)
        label.setWordWrap(True)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setObjectName("system_msg")
        label.setStyleSheet("color: #888; font-size: 11px; padding: 4px; font-family: 'Microsoft YaHei';")
        self._message_layout.insertWidget(self._message_layout.count() - 1, label)
        self._scroll_to_bottom()

    def clear_messages(self):
        while self._message_layout.count() > 1:
            item = self._message_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        self._bubbles.clear()
        self._current_ai_bubble = None

    def scroll_to_bottom(self):
        """对外：多模型并行流式时由 app 调用。"""
        self._scroll_to_bottom()

    def _scroll_to_bottom(self):
        QTimer.singleShot(50, lambda: self._scroll_area.verticalScrollBar().setValue(
            self._scroll_area.verticalScrollBar().maximum()
        ))

    # ── Input handling ─────────────────────────────────────────────────────────

    def _on_send(self):
        text = self._input.text().strip()
        if not text:
            return
        self._input.clear()
        if self._stack.currentIndex() != 0:
            self._show_chat_view()
        if text.startswith("/"):
            self.command_entered.emit(text)
        else:
            self.message_sent.emit(text)

    def _on_attach(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择文件")
        if path:
            self.file_sent.emit(path)

    # ── Window dragging ────────────────────────────────────────────────────────

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and event.position().y() < 42:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if self._drag_pos and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_pos = None

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            if self._stack.currentIndex() == 1:
                self._show_chat_view()
            else:
                self.close_requested.emit()
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event):
        # 拦截 Alt+F4 / 系统关闭：只隐藏窗口，程序继续后台运行。
        event.ignore()
        self.hide()

    # ── Stylesheet ─────────────────────────────────────────────────────────────

    def _apply_style(self):
        accent = self._accent or "#3b82f6"
        accent_rgb = hex_to_rgb_str(accent)
        if self._theme == "dark":
            qss = """
                #chat_container {
                    background-color: rgba(28, 28, 30, 242);
                    border-radius: 14px;
                    border: 1px solid rgba(255, 255, 255, 28);
                }
                #title_bar {
                    background-color: rgba(38, 38, 40, 250);
                    border-top-left-radius: 14px;
                    border-top-right-radius: 14px;
                    border-bottom: 1px solid rgba(255,255,255,12);
                }
                #dot { color: #4CAF50; }
                #model_chip {
                    background: rgba(255,255,255,12); color: #e8e8e8;
                    border: 1px solid rgba(255,255,255,22); border-radius: 8px;
                    padding: 4px 10px; font-size: 12px; font-family: 'Microsoft YaHei';
                    text-align: left;
                }
                #model_chip:hover { background: rgba(255,255,255,26); border-color: rgba(59,130,246,150); }
                #title_btn {
                    background: transparent; color: #9aa0a6;
                    border: none; border-radius: 6px; font-size: 16px;
                }
                #title_btn:hover { background: rgba(255,255,255,22); color: #fff; }
                #title_btn_close {
                    background: transparent; color: #9aa0a6;
                    border: none; border-radius: 6px; font-size: 16px;
                }
                #title_btn_close:hover { background: #c42b1c; color: white; }
                #main_stack { }
                #message_area { border: none; }
                QScrollBar:vertical { width: 6px; background: transparent; }
                QScrollBar::handle:vertical {
                    background: rgba(255,255,255,40); border-radius: 3px; min-height: 20px;
                }
                QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
                #user_bubble {
                    background-color: rgba(59, 130, 246, 185);
                    border-radius: 12px; margin-left: 56px;
                }
                #user_bubble QLabel { color: white; }
                #user_bubble #stats_label { color: rgba(255,255,255,120); font-size: 10px; font-family: 'Consolas'; }
                #bubble_image { border-radius: 8px; }
                #ai_bubble {
                    background-color: rgba(58, 58, 62, 210);
                    border-radius: 12px; margin-right: 56px;
                }
                #ai_bubble QLabel { color: #ececec; }
                #ai_bubble #model_tag { color: rgba(59,130,246,235); font-size: 10px; }
                #ai_bubble #stats_label { color: rgba(200,200,200,120); font-size: 10px; font-family: 'Consolas'; }
                #convs_panel { }
                #convs_scroll { border: none; }
                #back_btn, #new_conv_btn {
                    background: rgba(60,60,64,190); color: #ddd;
                    border: 1px solid rgba(255,255,255,15); border-radius: 7px;
                    padding: 5px 12px; font-size: 11px; font-family: 'Microsoft YaHei';
                }
                #back_btn:hover, #new_conv_btn:hover { background: rgba(82,82,88,230); }
                #panel_sep { background: rgba(255,255,255,15); max-height: 1px; }
                #conv_item {
                    background: rgba(50,50,54,170);
                    border-radius: 8px;
                }
                #conv_item:hover { background: rgba(68,68,74,210); }
                #conv_item_active {
                    background: rgba(59,130,246,95);
                    border-radius: 8px;
                    border: 1px solid rgba(59,130,246,180);
                }
                #conv_title { color: #e3e3e3; }
                #conv_date { color: #8a8a8a; }
                #input_bar {
                    background-color: rgba(38, 38, 40, 250);
                    border-bottom-left-radius: 14px;
                    border-bottom-right-radius: 14px;
                    border-top: 1px solid rgba(255,255,255,12);
                }
                #icon_btn {
                    background: transparent; border: none;
                    font-size: 16px; border-radius: 6px;
                }
                #icon_btn:hover { background: rgba(255,255,255,22); }
                #chat_input {
                    background-color: rgba(58, 58, 62, 210); color: #ececec;
                    border: 1px solid rgba(255,255,255,20); border-radius: 9px;
                    padding: 6px 10px; font-size: 13px; font-family: 'Microsoft YaHei';
                }
                #chat_input:focus { border: 1px solid rgba(59,130,246,160); }
                #send_btn {
                    background-color: rgba(59, 130, 246, 200); color: white;
                    border: none; border-radius: 9px;
                    font-size: 12px; font-family: 'Microsoft YaHei';
                }
                #send_btn:hover { background-color: rgba(59, 130, 246, 240); }
            """
        else:
            qss = """
                #chat_container {
                    background-color: rgba(252, 252, 253, 245);
                    border-radius: 14px; border: 1px solid rgba(0,0,0,28);
                }
                #title_bar {
                    background-color: rgba(246, 246, 248, 250);
                    border-top-left-radius: 14px; border-top-right-radius: 14px;
                    border-bottom: 1px solid rgba(0,0,0,10);
                }
                #dot { color: #4CAF50; }
                #model_chip {
                    background: rgba(0,0,0,6); color: #2b2b2b;
                    border: 1px solid rgba(0,0,0,18); border-radius: 8px;
                    padding: 4px 10px; font-size: 12px; font-family: 'Microsoft YaHei';
                    text-align: left;
                }
                #model_chip:hover { background: rgba(0,0,0,12); border-color: rgba(59,130,246,150); }
                #title_btn {
                    background: transparent; color: #666;
                    border: none; border-radius: 6px; font-size: 16px;
                }
                #title_btn:hover { background: rgba(0,0,0,10); }
                #title_btn_close {
                    background: transparent; color: #666;
                    border: none; border-radius: 6px; font-size: 16px;
                }
                #title_btn_close:hover { background: #c42b1c; color: white; }
                #main_stack { }
                #message_area { border: none; }
                QScrollBar:vertical { width: 6px; background: transparent; }
                QScrollBar::handle:vertical {
                    background: rgba(0,0,0,30); border-radius: 3px; min-height: 20px;
                }
                QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
                #user_bubble {
                    background-color: rgba(59, 130, 246, 205);
                    border-radius: 12px; margin-left: 56px;
                }
                #user_bubble QLabel { color: white; }
                #user_bubble #stats_label { color: rgba(255,255,255,150); font-size: 10px; font-family: 'Consolas'; }
                #bubble_image { border-radius: 8px; }
                #ai_bubble {
                    background-color: rgba(238, 238, 240, 235);
                    border-radius: 12px; margin-right: 56px;
                }
                #ai_bubble QLabel { color: #2b2b2b; }
                #ai_bubble #model_tag { color: rgba(59,130,246,255); font-size: 10px; }
                #ai_bubble #stats_label { color: rgba(0,0,0,100); font-size: 10px; font-family: 'Consolas'; }
                #convs_panel { }
                #convs_scroll { border: none; }
                #back_btn, #new_conv_btn {
                    background: rgba(232,232,234,210); color: #333;
                    border: 1px solid rgba(0,0,0,15); border-radius: 7px;
                    padding: 5px 12px; font-size: 11px; font-family: 'Microsoft YaHei';
                }
                #back_btn:hover, #new_conv_btn:hover { background: rgba(214,214,218,255); }
                #panel_sep { background: rgba(0,0,0,12); max-height: 1px; }
                #conv_item {
                    background: rgba(240,240,242,190);
                    border-radius: 8px;
                }
                #conv_item:hover { background: rgba(222,222,226,235); }
                #conv_item_active {
                    background: rgba(59,130,246,55);
                    border-radius: 8px;
                    border: 1px solid rgba(59,130,246,150);
                }
                #conv_title { color: #2b2b2b; }
                #conv_date { color: #888; }
                #input_bar {
                    background-color: rgba(246,246,248,250);
                    border-bottom-left-radius: 14px; border-bottom-right-radius: 14px;
                    border-top: 1px solid rgba(0,0,0,10);
                }
                #icon_btn {
                    background: transparent; border: none;
                    font-size: 16px; border-radius: 6px;
                }
                #icon_btn:hover { background: rgba(0,0,0,10); }
                #chat_input {
                    background-color: white; color: #2b2b2b;
                    border: 1px solid rgba(0,0,0,15); border-radius: 9px;
                    padding: 6px 10px; font-size: 13px; font-family: 'Microsoft YaHei';
                }
                #chat_input:focus { border: 1px solid rgba(59,130,246,150); }
                #send_btn {
                    background-color: rgba(59,130,246,210); color: white;
                    border: none; border-radius: 9px;
                    font-size: 12px; font-family: 'Microsoft YaHei';
                }
                #send_btn:hover { background-color: rgba(59,130,246,255); }
            """
        self.setStyleSheet(
            qss.replace("59, 130, 246", accent_rgb)
               .replace("59,130,246", accent_rgb)
               .replace("#3b82f6", accent)
        )

    def set_background(self, path: str, fill_mode: str = "fill"):
        if path:
            pm = QPixmap(path)
            self._container.set_background(None if pm.isNull() else pm, fill_mode)
        else:
            self._container.set_background(None, fill_mode)

    def set_theme(self, theme: str):
        self._theme = theme
        self._container.set_theme(theme)
        self._apply_style()

    def set_accent(self, accent: str):
        self._accent = accent or "#3b82f6"
        self._apply_style()
