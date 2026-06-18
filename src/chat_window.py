import ctypes
import time
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLineEdit,
    QPushButton, QLabel, QScrollArea, QFrame, QFileDialog, QStackedWidget,
    QMenu,
)
from PyQt6.QtCore import Qt, pyqtSignal, QPropertyAnimation, QEasingCurve, QPoint, QTimer
from PyQt6.QtGui import QFont, QKeyEvent, QPixmap, QActionGroup

WDA_EXCLUDEFROMCAPTURE = 0x00000011


def set_display_affinity(hwnd: int, affinity: int = WDA_EXCLUDEFROMCAPTURE):
    ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, affinity)


def set_toolwindow(hwnd: int):
    GWL_EXSTYLE = -20
    WS_EX_TOOLWINDOW = 0x00000080
    style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_TOOLWINDOW)


class MessageBubble(QFrame):
    def __init__(self, text: str, is_user: bool, parent=None, image_data: bytes = None):
        super().__init__(parent)
        self.setObjectName("user_bubble" if is_user else "ai_bubble")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 6)
        layout.setSpacing(6)

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


class ChatWindow(QWidget):
    message_sent = pyqtSignal(str)
    file_sent = pyqtSignal(str)
    screenshot_requested = pyqtSignal()
    command_entered = pyqtSignal(str)
    close_requested = pyqtSignal()
    open_settings_requested = pyqtSignal()
    conversation_selected = pyqtSignal(str)
    model_changed = pyqtSignal(str, str)          # provider_id, model_id
    manage_providers_requested = pyqtSignal()

    def __init__(self, width=440, height=560, opacity=0.9, position="bottom_right",
                 screenshot_protection=True, theme="dark"):
        super().__init__(None)
        self._width = width
        self._height = height
        self._opacity = opacity
        self._position = position
        self._screenshot_protection = screenshot_protection
        self._theme = theme
        self._drag_pos = None
        self._bubbles: list[MessageBubble] = []
        self._current_ai_bubble: MessageBubble | None = None

        # 模型选择数据
        self._providers: list[dict] = []
        self._active_pid: str = ""
        self._active_mid: str = ""

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

        container = QFrame(self)
        container.setObjectName("chat_container")
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

        # Page 0: message scroll area
        self._scroll_area = QScrollArea()
        self._scroll_area.setObjectName("message_area")
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._message_container = QWidget()
        self._message_layout = QVBoxLayout(self._message_container)
        self._message_layout.setContentsMargins(10, 10, 10, 10)
        self._message_layout.setSpacing(8)
        self._message_layout.addStretch()
        self._scroll_area.setWidget(self._message_container)

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
        self._convs_inner = QWidget()
        self._convs_inner_layout = QVBoxLayout(self._convs_inner)
        self._convs_inner_layout.setContentsMargins(0, 0, 0, 0)
        self._convs_inner_layout.setSpacing(4)
        self._convs_inner_layout.addStretch()
        self._convs_scroll.setWidget(self._convs_inner)
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

    def set_model_options(self, providers: list[dict], active_pid: str, active_mid: str):
        """由 app 注入可选的服务商 / 模型及当前激活项，刷新顶部芯片。"""
        self._providers = providers or []
        self._active_pid = active_pid or ""
        self._active_mid = active_mid or ""
        self._refresh_model_chip()

    def _refresh_model_chip(self):
        name, vision = "选择模型", False
        for p in self._providers:
            if p.get("id") == self._active_pid:
                for m in p.get("models", []):
                    if m.get("id") == self._active_mid:
                        name = m.get("name") or m.get("id") or name
                        vision = bool(m.get("vision"))
                        break
                break
        label = f"👁 {name}" if vision else name
        self._model_btn.setText(f"{label}  ▾")

    def _open_model_menu(self):
        menu = self._styled_menu()
        group = QActionGroup(menu)
        group.setExclusive(True)

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
                act.setChecked(p.get("id") == self._active_pid and mid == self._active_mid)
                group.addAction(act)
                act.triggered.connect(
                    lambda _checked, pid=p.get("id"), m_id=mid: self._on_model_picked(pid, m_id)
                )

        menu.addSeparator()
        manage = menu.addAction("管理服务商 / 模型…")
        manage.triggered.connect(self.manage_providers_requested.emit)

        menu.exec(self._model_btn.mapToGlobal(self._model_btn.rect().bottomLeft()))

    def _on_model_picked(self, pid: str, mid: str):
        if pid == self._active_pid and mid == self._active_mid:
            return
        self._active_pid, self._active_mid = pid, mid
        self._refresh_model_chip()
        self.model_changed.emit(pid, mid)

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

    def _styled_menu(self) -> QMenu:
        menu = QMenu(self)
        if self._theme == "dark":
            menu.setStyleSheet("""
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
            """)
        else:
            menu.setStyleSheet("""
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
            """)
        return menu

    # ── Message operations ────────────────────────────────────────────────────

    def add_user_message(self, text: str, image_data: bytes = None):
        bubble = MessageBubble(text, is_user=True, image_data=image_data)
        self._message_layout.insertWidget(self._message_layout.count() - 1, bubble)
        self._bubbles.append(bubble)
        self._scroll_to_bottom()

    def start_ai_message(self) -> MessageBubble:
        bubble = MessageBubble("", is_user=False)
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
        if self._theme == "dark":
            self.setStyleSheet("""
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
                #main_stack { background: transparent; }
                #message_area { background: transparent; border: none; }
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
                #ai_bubble #stats_label { color: rgba(200,200,200,120); font-size: 10px; font-family: 'Consolas'; }
                #convs_panel { background: transparent; }
                #convs_scroll { background: transparent; border: none; }
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
            """)
        else:
            self.setStyleSheet("""
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
                #main_stack { background: transparent; }
                #message_area { background: transparent; border: none; }
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
                #ai_bubble #stats_label { color: rgba(0,0,0,100); font-size: 10px; font-family: 'Consolas'; }
                #convs_panel { background: transparent; }
                #convs_scroll { background: transparent; border: none; }
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
            """)
