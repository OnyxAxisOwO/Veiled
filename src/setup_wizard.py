import ctypes
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QComboBox, QCheckBox, QSlider, QStackedWidget,
    QFrame, QMessageBox,
)
from PyQt6.QtCore import Qt, pyqtSignal, QTimer
from PyQt6.QtGui import QFont

from .config import Config, POSITION_MAP, new_provider_id, model_guess_vision
from .widgets import HotkeyInput
from .api_client import ApiClient

# 预设：展示名 → (kind, 默认模型, 默认 endpoint)
_PRESETS = {
    "OpenAI": ("openai", "gpt-4o", "https://api.openai.com"),
    "Claude": ("claude", "claude-sonnet-4-20250514", "https://api.anthropic.com"),
    "自定义 (OpenAI 兼容)": ("openai", "", ""),
}

WDA_EXCLUDEFROMCAPTURE = 0x00000011


class SetupWizard(QWidget):
    finished = pyqtSignal()

    def __init__(self, config: Config):
        super().__init__(None)
        self._config = config
        self.setWindowTitle("Windows Display Adapter Helper - Setup")
        self.setFixedSize(520, 560)
        self.setWindowFlags(
            Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.CustomizeWindowHint | Qt.WindowType.WindowTitleHint
        )
        self._setup_ui()
        self._apply_style()

    def showEvent(self, event):
        super().showEvent(event)
        hwnd = int(self.winId())
        ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)

        header = QLabel("初始化配置")
        header.setFont(QFont("Microsoft YaHei", 14, QFont.Weight.Bold))
        header.setObjectName("header")
        layout.addWidget(header)

        self._step_label = QLabel("步骤 1/4 — API 配置")
        self._step_label.setObjectName("step_label")
        layout.addWidget(self._step_label)

        self._stack = QStackedWidget()
        self._stack.addWidget(self._create_api_page())
        self._stack.addWidget(self._create_hotkey_page())
        self._stack.addWidget(self._create_display_page())
        self._stack.addWidget(self._create_finish_page())
        layout.addWidget(self._stack, 1)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        self._back_btn = QPushButton("上一步")
        self._back_btn.setObjectName("nav_btn")
        self._back_btn.clicked.connect(self._go_back)
        self._back_btn.setVisible(False)
        btn_layout.addWidget(self._back_btn)

        self._next_btn = QPushButton("下一步")
        self._next_btn.setObjectName("nav_btn_primary")
        self._next_btn.clicked.connect(self._go_next)
        btn_layout.addWidget(self._next_btn)
        layout.addLayout(btn_layout)

    def _create_api_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(12)

        layout.addWidget(QLabel("AI 服务商预设:"))
        self._provider_combo = QComboBox()
        self._provider_combo.addItems(list(_PRESETS.keys()))
        self._provider_combo.currentTextChanged.connect(self._on_provider_changed)
        layout.addWidget(self._provider_combo)

        layout.addWidget(QLabel("服务商名称:"))
        self._name_input = QLineEdit()
        self._name_input.setText("OpenAI")
        layout.addWidget(self._name_input)

        layout.addWidget(QLabel("API Key:"))
        self._api_key_input = QLineEdit()
        self._api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._api_key_input.setPlaceholderText("输入你的 API Key...")
        layout.addWidget(self._api_key_input)

        key_layout = QHBoxLayout()
        self._show_key_cb = QCheckBox("显示")
        self._show_key_cb.toggled.connect(
            lambda checked: self._api_key_input.setEchoMode(
                QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
            )
        )
        key_layout.addWidget(self._show_key_cb)
        key_layout.addStretch()

        self._test_btn = QPushButton("测试连接")
        self._test_btn.clicked.connect(self._test_connection)
        key_layout.addWidget(self._test_btn)
        layout.addLayout(key_layout)

        self._test_result = QLabel("")
        self._test_result.setObjectName("test_result")
        layout.addWidget(self._test_result)

        layout.addWidget(QLabel("模型:"))
        self._model_input = QLineEdit()
        self._model_input.setText("gpt-4o")
        layout.addWidget(self._model_input)

        layout.addWidget(QLabel("API Endpoint:"))
        self._endpoint_input = QLineEdit()
        self._endpoint_input.setText("https://api.openai.com")
        layout.addWidget(self._endpoint_input)

        layout.addWidget(QLabel("代理 (可选):"))
        self._proxy_input = QLineEdit()
        self._proxy_input.setPlaceholderText("http://127.0.0.1:7890")
        layout.addWidget(self._proxy_input)

        layout.addStretch()
        return page

    def _create_hotkey_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(10)

        hotkeys = [
            ("唤起/隐藏对话窗 *", "toggle_chat", "ctrl+shift+space"),
            ("紧急隐藏（老板键）*", "boss_key", "ctrl+`"),
            ("剪贴板快捷问答", "clipboard_ask", "ctrl+shift+q"),
            ("区域截图问AI", "screenshot_ask", "ctrl+shift+s"),
            ("整屏截图问AI", "screenshot_full", "ctrl+shift+a"),
            ("退出程序 *", "exit", "ctrl+shift+alt+q"),
        ]
        self._hotkey_inputs: dict[str, HotkeyInput] = {}

        for label_text, key_name, default in hotkeys:
            row = QHBoxLayout()
            label = QLabel(label_text)
            label.setFixedWidth(180)
            row.addWidget(label)
            hk_input = HotkeyInput()
            hk_input.setText(default)
            hk_input._keys = default
            self._hotkey_inputs[key_name] = hk_input
            row.addWidget(hk_input)
            layout.addLayout(row)

        note = QLabel("带 * 号为必填项。点击输入框后直接按下快捷键组合即可录入。")
        note.setObjectName("note")
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch()
        return page

    def _create_display_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(10)

        self._tray_cb = QCheckBox("显示系统托盘图标")
        layout.addWidget(self._tray_cb)

        layout.addWidget(QLabel("对话窗弹出位置:"))
        self._position_combo = QComboBox()
        self._position_combo.addItems(["右下角", "左下角", "右上角", "左上角", "居中"])
        layout.addWidget(self._position_combo)

        opacity_layout = QHBoxLayout()
        opacity_layout.addWidget(QLabel("窗口透明度:"))
        self._opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self._opacity_slider.setRange(50, 100)
        self._opacity_slider.setValue(90)
        self._opacity_label = QLabel("90%")
        self._opacity_slider.valueChanged.connect(lambda v: self._opacity_label.setText(f"{v}%"))
        opacity_layout.addWidget(self._opacity_slider)
        opacity_layout.addWidget(self._opacity_label)
        layout.addLayout(opacity_layout)

        self._screenshot_protect_cb = QCheckBox("截屏保护（截屏/录屏不可见）")
        self._screenshot_protect_cb.setChecked(True)
        layout.addWidget(self._screenshot_protect_cb)

        self._autostart_cb = QCheckBox("开机自启动")
        self._autostart_cb.setChecked(True)
        layout.addWidget(self._autostart_cb)

        layout.addWidget(QLabel("通知标题（留空则不显示）:"))
        self._notif_title_input = QLineEdit()
        self._notif_title_input.setPlaceholderText("例如：系统消息")
        layout.addWidget(self._notif_title_input)

        layout.addStretch()
        return page

    def _create_finish_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(16)
        layout.addStretch()

        self._finish_summary = QLabel()
        self._finish_summary.setFont(QFont("Microsoft YaHei", 11))
        self._finish_summary.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._finish_summary.setWordWrap(True)
        layout.addWidget(self._finish_summary)

        tip = QLabel("点击「完成」后程序进入后台，本窗口不再出现。\n在对话窗输入 /settings 可重新打开设置。")
        tip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tip.setObjectName("note")
        tip.setWordWrap(True)
        layout.addWidget(tip)

        layout.addStretch()
        return page

    def _on_provider_changed(self, text: str):
        kind, model, endpoint = _PRESETS.get(text, ("openai", "", ""))
        self._name_input.setText("自定义" if text.startswith("自定义") else text)
        self._model_input.setText(model)
        self._endpoint_input.setText(endpoint)

    def _test_connection(self):
        kind, _m, _e = _PRESETS.get(self._provider_combo.currentText(), ("openai", "", ""))
        client = ApiClient(
            kind=kind,
            api_key=self._api_key_input.text().strip(),
            model=self._model_input.text().strip(),
            endpoint=self._endpoint_input.text().strip(),
            proxy=self._proxy_input.text().strip(),
        )
        self._test_btn.setEnabled(False)
        self._test_result.setText("测试中...")
        QTimer.singleShot(100, lambda: self._do_test(client))

    def _do_test(self, client: ApiClient):
        ok, msg = client.test_connection()
        self._test_result.setText(f"{'✓ ' if ok else '✗ '}{msg}")
        self._test_result.setStyleSheet(f"color: {'#4CAF50' if ok else '#f44336'};")
        self._test_btn.setEnabled(True)

    def _go_back(self):
        idx = self._stack.currentIndex()
        if idx > 0:
            self._stack.setCurrentIndex(idx - 1)
            self._update_nav()

    def _go_next(self):
        idx = self._stack.currentIndex()
        if idx == 0:
            if not self._api_key_input.text().strip():
                self._test_result.setText("请输入 API Key")
                self._test_result.setStyleSheet("color: #f44336;")
                return
        if idx == 3:
            self._save_and_finish()
            return
        if idx == 2:
            self._update_finish_page()

        self._stack.setCurrentIndex(idx + 1)
        self._update_nav()

    def _update_nav(self):
        idx = self._stack.currentIndex()
        step_names = ["API 配置", "快捷键绑定", "显示偏好", "完成"]
        self._step_label.setText(f"步骤 {idx+1}/4 — {step_names[idx]}")
        self._back_btn.setVisible(idx > 0)
        self._next_btn.setText("完成" if idx == 3 else "下一步")

    def _update_finish_page(self):
        toggle = self._hotkey_inputs["toggle_chat"].hotkey or "ctrl+shift+space"
        boss = self._hotkey_inputs["boss_key"].hotkey or "ctrl+`"
        self._finish_summary.setText(
            f"核心快捷键：\n\n"
            f"唤起对话窗：  {toggle.upper()}\n"
            f"紧急隐藏：  {boss.upper()}\n"
        )

    def _save_and_finish(self):
        inv_position = {v: k for k, v in POSITION_MAP.items()}

        kind, _m, _e = _PRESETS.get(self._provider_combo.currentText(), ("openai", "", ""))
        pid = new_provider_id()
        model_id = self._model_input.text().strip()
        models = []
        if model_id:
            models.append({"id": model_id, "name": model_id, "vision": model_guess_vision(model_id)})
        provider = {
            "id": pid,
            "name": self._name_input.text().strip() or self._provider_combo.currentText(),
            "kind": kind,
            "endpoint": self._endpoint_input.text().strip(),
            "api_key": self._api_key_input.text().strip(),
            "extra_body": "",
            "models": models,
        }
        self._config.set("api.providers", [provider])
        self._config.set("api.active", {"provider": pid, "model": model_id})
        self._config.set("api.proxy", self._proxy_input.text().strip())

        for name, widget in self._hotkey_inputs.items():
            if widget.hotkey:
                self._config.set(f"hotkeys.{name}", widget.hotkey)

        self._config.set("display.tray_icon", self._tray_cb.isChecked())
        self._config.set("display.chat_position", inv_position.get(self._position_combo.currentText(), "bottom_right"))
        self._config.set("display.chat_opacity", self._opacity_slider.value() / 100.0)
        self._config.set("display.screenshot_protection", self._screenshot_protect_cb.isChecked())
        self._config.set("display.auto_start", self._autostart_cb.isChecked())
        self._config.set("display.notification_title", self._notif_title_input.text().strip())

        self._config.set("first_run", False)
        self._config.save()
        self.close()
        self.finished.emit()

    def _apply_style(self):
        self.setStyleSheet("""
            QWidget {
                background-color: #f0f0f0;
                font-family: 'Microsoft YaHei', 'Segoe UI';
                font-size: 12px;
                color: #333;
            }
            #header { color: #1a1a1a; margin-bottom: 4px; }
            #step_label { color: #666; font-size: 11px; margin-bottom: 8px; }
            QLineEdit, QComboBox {
                min-height: 22px;
                padding: 6px 10px;
                border: 1px solid #ccc;
                border-radius: 4px;
                background: white;
                font-size: 12px;
            }
            QComboBox::drop-down {
                subcontrol-origin: padding; subcontrol-position: center right;
                border: none; width: 24px; background: transparent;
            }
            QComboBox::down-arrow {
                width: 0; height: 0; margin-right: 9px;
                border-left: 4px solid transparent; border-right: 4px solid transparent;
                border-top: 5px solid rgba(0,0,0,120);
            }
            QComboBox::down-arrow:on {
                border-top: none; border-bottom: 5px solid rgba(0,0,0,150);
            }
            QComboBox QAbstractItemView {
                background: white;
                color: #333;
                selection-background-color: #4a90d9;
                selection-color: white;
            }
            QLineEdit:focus, QComboBox:focus { border-color: #4a90d9; }
            QCheckBox { spacing: 6px; }
            QSlider::groove:horizontal {
                height: 4px;
                background: #ddd;
                border-radius: 2px;
            }
            QSlider::handle:horizontal {
                width: 14px; height: 14px;
                margin: -5px 0;
                background: #4a90d9;
                border-radius: 7px;
            }
            #nav_btn {
                padding: 8px 20px;
                background: #e0e0e0;
                border: 1px solid #ccc;
                border-radius: 4px;
            }
            #nav_btn:hover { background: #d0d0d0; }
            #nav_btn_primary {
                padding: 8px 20px;
                background: #4a90d9;
                color: white;
                border: none;
                border-radius: 4px;
            }
            #nav_btn_primary:hover { background: #3a7bc8; }
            #note { color: #888; font-size: 11px; }
            #test_result { font-size: 11px; }
        """)
