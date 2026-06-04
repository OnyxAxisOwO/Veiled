import ctypes
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QComboBox, QCheckBox, QSlider, QTabWidget,
    QTextEdit, QFrame, QScrollArea, QListWidget, QListWidgetItem,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont

from .config import Config
from .setup_wizard import HotkeyInput

WDA_EXCLUDEFROMCAPTURE = 0x00000011


class SettingsPanel(QWidget):
    settings_changed = pyqtSignal()
    closed = pyqtSignal()

    def __init__(self, config: Config):
        super().__init__(None)
        self._config = config
        self.setWindowTitle("Display Adapter Configuration")
        self.setFixedSize(560, 500)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._drag_pos = None
        self._setup_ui()
        self._load_values()
        self._apply_style()

    def showEvent(self, event):
        super().showEvent(event)
        hwnd = int(self.winId())
        ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)

    def closeEvent(self, event):
        # 拦截 Alt+F4：只隐藏，不销毁。
        event.ignore()
        self._on_close()

    def _setup_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        container = QFrame()
        container.setObjectName("settings_container")
        main_layout = QVBoxLayout(container)
        main_layout.setContentsMargins(16, 12, 16, 12)

        title_bar = QHBoxLayout()
        title = QLabel("设置")
        title.setFont(QFont("Microsoft YaHei", 13, QFont.Weight.Bold))
        title.setObjectName("settings_title")
        title_bar.addWidget(title)
        title_bar.addStretch()
        close_btn = QPushButton("×")
        close_btn.setObjectName("close_btn")
        close_btn.setFixedSize(28, 28)
        close_btn.clicked.connect(self._on_close)
        title_bar.addWidget(close_btn)
        main_layout.addLayout(title_bar)

        self._tabs = QTabWidget()
        self._tabs.addTab(self._create_connection_tab(), "连接")
        self._tabs.addTab(self._create_hotkey_tab(), "快捷键")
        self._tabs.addTab(self._create_appearance_tab(), "外观")
        self._tabs.addTab(self._create_privacy_tab(), "隐私")
        self._tabs.addTab(self._create_behavior_tab(), "行为")
        self._tabs.addTab(self._create_about_tab(), "关于")
        main_layout.addWidget(self._tabs, 1)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        save_btn = QPushButton("保存")
        save_btn.setObjectName("save_btn")
        save_btn.clicked.connect(self._save)
        btn_layout.addWidget(save_btn)
        main_layout.addLayout(btn_layout)

        outer.addWidget(container)

    def _create_connection_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(8)

        layout.addWidget(QLabel("服务商:"))
        self._s_provider = QComboBox()
        self._s_provider.addItems(["Claude", "OpenAI", "DeepSeek", "自定义"])
        layout.addWidget(self._s_provider)

        layout.addWidget(QLabel("API Key:"))
        self._s_api_key = QLineEdit()
        self._s_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        layout.addWidget(self._s_api_key)

        layout.addWidget(QLabel("模型:"))
        self._s_model = QLineEdit()
        layout.addWidget(self._s_model)

        layout.addWidget(QLabel("Endpoint:"))
        self._s_endpoint = QLineEdit()
        layout.addWidget(self._s_endpoint)

        layout.addWidget(QLabel("代理:"))
        self._s_proxy = QLineEdit()
        layout.addWidget(self._s_proxy)

        layout.addStretch()
        return w

    def _create_hotkey_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(8)

        hotkeys = [
            ("唤起/隐藏对话窗", "toggle_chat"),
            ("紧急隐藏", "boss_key"),
            ("剪贴板问答", "clipboard_ask"),
            ("区域截图", "screenshot_ask"),
            ("整屏截图", "screenshot_full"),
            ("退出程序", "exit"),
        ]
        self._s_hotkeys: dict[str, HotkeyInput] = {}
        for label_text, key_name in hotkeys:
            row = QHBoxLayout()
            row.addWidget(QLabel(label_text))
            hk = HotkeyInput()
            self._s_hotkeys[key_name] = hk
            row.addWidget(hk)
            layout.addLayout(row)

        layout.addStretch()
        return w

    def _create_appearance_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(8)

        self._s_tray = QCheckBox("显示系统托盘图标")
        layout.addWidget(self._s_tray)

        layout.addWidget(QLabel("对话窗位置:"))
        self._s_position = QComboBox()
        self._s_position.addItems(["右下角", "左下角", "右上角", "左上角", "居中"])
        layout.addWidget(self._s_position)

        row = QHBoxLayout()
        row.addWidget(QLabel("透明度:"))
        self._s_opacity = QSlider(Qt.Orientation.Horizontal)
        self._s_opacity.setRange(50, 100)
        self._s_opacity_label = QLabel()
        self._s_opacity.valueChanged.connect(lambda v: self._s_opacity_label.setText(f"{v}%"))
        row.addWidget(self._s_opacity)
        row.addWidget(self._s_opacity_label)
        layout.addLayout(row)

        layout.addWidget(QLabel("通知伪装:"))
        self._s_disguise = QComboBox()
        self._s_disguise.addItems(["无伪装", "QQ", "微信", "浏览器 (Edge)"])
        layout.addWidget(self._s_disguise)

        self._s_ss_toast = QCheckBox("截图上传后弹「成功」通知")
        layout.addWidget(self._s_ss_toast)
        row_ss = QHBoxLayout()
        row_ss.addWidget(QLabel("成功提示文字:"))
        self._s_ss_text = QLineEdit()
        self._s_ss_text.setPlaceholderText("成功")
        row_ss.addWidget(self._s_ss_text)
        layout.addLayout(row_ss)

        layout.addWidget(QLabel("主题:"))
        self._s_theme = QComboBox()
        self._s_theme.addItems(["深色", "浅色"])
        layout.addWidget(self._s_theme)

        layout.addStretch()
        return w

    def _create_privacy_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(8)

        self._s_screenshot_protect = QCheckBox("截屏保护")
        layout.addWidget(self._s_screenshot_protect)

        row = QHBoxLayout()
        row.addWidget(QLabel("记录保留天数 (0=永久):"))
        self._s_retention = QLineEdit()
        self._s_retention.setFixedWidth(60)
        row.addWidget(self._s_retention)
        row.addStretch()
        layout.addLayout(row)

        self._s_save_screenshots = QCheckBox("保留截图历史")
        layout.addWidget(self._s_save_screenshots)

        self._s_clear_on_exit = QCheckBox("退出时清除所有数据")
        layout.addWidget(self._s_clear_on_exit)

        layout.addStretch()
        return w

    def _create_behavior_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(8)

        self._s_autostart = QCheckBox("开机自启动")
        layout.addWidget(self._s_autostart)

        self._s_close_on_focus = QCheckBox("点击窗口外部自动关闭对话窗")
        layout.addWidget(self._s_close_on_focus)

        layout.addWidget(QLabel("环境检测进程列表（每行一个）:"))
        self._s_processes = QTextEdit()
        self._s_processes.setMaximumHeight(80)
        layout.addWidget(self._s_processes)

        layout.addWidget(QLabel("普通对话 System Prompt:"))
        self._s_prompt_chat = QTextEdit()
        self._s_prompt_chat.setMaximumHeight(60)
        layout.addWidget(self._s_prompt_chat)

        layout.addWidget(QLabel("截图场景 System Prompt:"))
        self._s_prompt_screenshot = QTextEdit()
        self._s_prompt_screenshot.setMaximumHeight(60)
        layout.addWidget(self._s_prompt_screenshot)

        layout.addStretch()
        return w

    def _create_about_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.addStretch()
        layout.addWidget(QLabel("Windows Display Adapter Helper"))
        layout.addWidget(QLabel("版本 1.0.0"))
        layout.addStretch()
        return w

    def _load_values(self):
        c = self._config
        provider_map = {"claude": "Claude", "openai": "OpenAI", "deepseek": "DeepSeek", "custom": "自定义"}
        self._s_provider.setCurrentText(provider_map.get(c.provider, "Claude"))
        self._s_api_key.setText(c.api_key)
        self._s_model.setText(c.api_model)
        self._s_endpoint.setText(c.api_endpoint)
        self._s_proxy.setText(c.get("api.proxy", ""))

        for name, widget in self._s_hotkeys.items():
            val = c.get(f"hotkeys.{name}", "")
            widget.setText(val)
            widget._keys = val

        self._s_tray.setChecked(c.get("display.tray_icon", False))
        pos_map = {"bottom_right": "右下角", "bottom_left": "左下角", "top_right": "右上角", "top_left": "左上角", "center": "居中"}
        self._s_position.setCurrentText(pos_map.get(c.get("display.chat_position", "bottom_right"), "右下角"))
        opacity_val = int(c.get("display.chat_opacity", 0.9) * 100)
        self._s_opacity.setValue(opacity_val)
        self._s_opacity_label.setText(f"{opacity_val}%")
        disguise_map = {"none": "无伪装", "qq": "QQ", "wechat": "微信", "edge": "浏览器 (Edge)"}
        self._s_disguise.setCurrentText(disguise_map.get(c.get("display.notification_disguise", "none"), "无伪装"))
        self._s_ss_toast.setChecked(c.get("display.screenshot_success_toast", True))
        self._s_ss_text.setText(c.get("display.screenshot_success_text", "成功"))
        self._s_theme.setCurrentText("深色" if c.get("display.theme", "dark") == "dark" else "浅色")

        self._s_screenshot_protect.setChecked(c.get("display.screenshot_protection", True))
        self._s_retention.setText(str(c.get("privacy.history_retention_days", 0)))
        self._s_save_screenshots.setChecked(c.get("privacy.save_screenshots", False))
        self._s_clear_on_exit.setChecked(c.get("privacy.clear_on_exit", False))

        self._s_autostart.setChecked(c.get("display.auto_start", True))
        self._s_close_on_focus.setChecked(c.get("display.close_on_focus_lost", False))
        procs = c.get("environment.suspicious_processes", [])
        self._s_processes.setText("\n".join(procs))
        self._s_prompt_chat.setText(c.get("prompts.chat", ""))
        self._s_prompt_screenshot.setText(c.get("prompts.screenshot", ""))

    def _save(self):
        c = self._config
        provider_map = {"Claude": "claude", "OpenAI": "openai", "DeepSeek": "deepseek", "自定义": "custom"}
        provider = provider_map.get(self._s_provider.currentText(), "custom")
        c.set("api.provider", provider)
        c.set(f"api.providers.{provider}.api_key", self._s_api_key.text().strip())
        c.set(f"api.providers.{provider}.model", self._s_model.text().strip())
        c.set(f"api.providers.{provider}.endpoint", self._s_endpoint.text().strip())
        c.set("api.proxy", self._s_proxy.text().strip())

        for name, widget in self._s_hotkeys.items():
            if widget.hotkey:
                c.set(f"hotkeys.{name}", widget.hotkey)

        c.set("display.tray_icon", self._s_tray.isChecked())
        pos_map = {"右下角": "bottom_right", "左下角": "bottom_left", "右上角": "top_right", "左上角": "top_left", "居中": "center"}
        c.set("display.chat_position", pos_map.get(self._s_position.currentText(), "bottom_right"))
        c.set("display.chat_opacity", self._s_opacity.value() / 100.0)
        disguise_map = {"无伪装": "none", "QQ": "qq", "微信": "wechat", "浏览器 (Edge)": "edge"}
        c.set("display.notification_disguise", disguise_map.get(self._s_disguise.currentText(), "none"))
        c.set("display.screenshot_success_toast", self._s_ss_toast.isChecked())
        c.set("display.screenshot_success_text", self._s_ss_text.text().strip() or "成功")
        c.set("display.theme", "dark" if self._s_theme.currentText() == "深色" else "light")

        c.set("display.screenshot_protection", self._s_screenshot_protect.isChecked())
        try:
            c.set("privacy.history_retention_days", int(self._s_retention.text()))
        except ValueError:
            pass
        c.set("privacy.save_screenshots", self._s_save_screenshots.isChecked())
        c.set("privacy.clear_on_exit", self._s_clear_on_exit.isChecked())

        c.set("display.auto_start", self._s_autostart.isChecked())
        c.set("display.close_on_focus_lost", self._s_close_on_focus.isChecked())
        procs = [p.strip() for p in self._s_processes.toPlainText().split("\n") if p.strip()]
        c.set("environment.suspicious_processes", procs)
        c.set("prompts.chat", self._s_prompt_chat.toPlainText())
        c.set("prompts.screenshot", self._s_prompt_screenshot.toPlainText())

        c.save()
        self.settings_changed.emit()
        self._on_close()

    def _on_close(self):
        self.hide()
        self.closed.emit()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and event.position().y() < 40:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event):
        if self._drag_pos and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, event):
        self._drag_pos = None

    def _apply_style(self):
        self.setStyleSheet("""
            #settings_container {
                background-color: rgba(35, 35, 35, 245);
                border-radius: 10px;
                border: 1px solid rgba(255,255,255,25);
            }
            #settings_title { color: #e0e0e0; }
            #close_btn {
                background: transparent; color: #888;
                border: none; border-radius: 4px; font-size: 16px;
            }
            #close_btn:hover { background: #c42b1c; color: white; }
            QTabWidget::pane {
                border: 1px solid rgba(255,255,255,15);
                border-radius: 4px;
                background: rgba(40,40,40,200);
            }
            QTabBar::tab {
                background: rgba(50,50,50,200);
                color: #aaa;
                padding: 6px 12px;
                border: none;
                border-top-left-radius: 4px;
                border-top-right-radius: 4px;
                margin-right: 2px;
                font-size: 11px;
            }
            QTabBar::tab:selected { background: rgba(60,60,60,250); color: #e0e0e0; }
            QLabel { color: #ccc; font-size: 12px; }
            QLineEdit, QComboBox {
                background: rgba(50,50,50,200);
                color: #e0e0e0;
                border: 1px solid rgba(255,255,255,20);
                border-radius: 4px;
                padding: 5px 8px;
                font-size: 12px;
            }
            QLineEdit:focus, QComboBox:focus { border-color: rgba(59,130,246,150); }
            QTextEdit {
                background: rgba(50,50,50,200);
                color: #e0e0e0;
                border: 1px solid rgba(255,255,255,20);
                border-radius: 4px;
                font-size: 12px;
            }
            QCheckBox { color: #ccc; spacing: 6px; }
            QCheckBox::indicator {
                width: 16px; height: 16px;
                border: 1px solid rgba(255,255,255,30);
                border-radius: 3px;
                background: rgba(50,50,50,200);
            }
            QCheckBox::indicator:checked { background: #4a90d9; border-color: #4a90d9; }
            QSlider::groove:horizontal { height: 4px; background: rgba(255,255,255,20); border-radius: 2px; }
            QSlider::handle:horizontal {
                width: 14px; height: 14px; margin: -5px 0;
                background: #4a90d9; border-radius: 7px;
            }
            #save_btn {
                padding: 7px 24px;
                background: #4a90d9; color: white;
                border: none; border-radius: 4px; font-size: 12px;
            }
            #save_btn:hover { background: #3a7bc8; }
            QComboBox QAbstractItemView {
                background: #2d2d2d; color: #e0e0e0;
                selection-background-color: #404040;
                border: 1px solid #404040;
            }
        """)
