import sys
import time
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QObject, QTimer, pyqtSlot

from .config import Config
from .database import Database
from .api_client import ApiClient, ApiWorker
from .hotkey import HotkeyManager
from .chat_window import ChatWindow
from .screenshot import ScreenshotOverlay
from .notification import NotificationManager
from .environment import EnvironmentMonitor
from .tray import TrayManager
from .commands import CommandHandler
from .setup_wizard import SetupWizard
from .settings_panel import SettingsPanel


class VeiledApp(QObject):
    def __init__(self):
        super().__init__()
        self._config = Config()
        self._db = Database(self._config.db_path)

        self._chat_window: ChatWindow | None = None
        self._settings_panel: SettingsPanel | None = None
        self._screenshot_overlay: ScreenshotOverlay | None = None
        self._current_worker: ApiWorker | None = None

        self._current_conv_id: str | None = None
        self._messages: list[dict] = []

        self._hotkey_mgr = HotkeyManager()
        self._hotkey_mgr.triggered.connect(self._on_hotkey)

        self._commands = CommandHandler()
        self._commands.open_settings.connect(self._show_settings)
        self._commands.new_conversation.connect(self._new_conversation)
        self._commands.list_conversations.connect(self._list_conversations)
        self._commands.clear_conversation.connect(self._clear_conversation)
        self._commands.delete_conversation.connect(self._delete_conversation)
        self._commands.switch_model.connect(self._switch_model)
        self._commands.translate.connect(self._translate)
        self._commands.summarize.connect(self._summarize)
        self._commands.export_conversation.connect(self._export_conversation)
        self._commands.show_help.connect(self._show_help)
        self._commands.unknown_command.connect(self._unknown_command)

        self._notification = NotificationManager(self._config.get("display.notification_disguise", "none"))
        self._notification.notification_clicked.connect(self._toggle_chat)

        self._env_monitor = EnvironmentMonitor(self._config.get("environment.suspicious_processes", []))
        if self._config.get("environment.monitor_enabled", True):
            self._env_monitor.start()

        self._tray = TrayManager()
        self._tray.open_chat.connect(self._toggle_chat)
        self._tray.open_settings.connect(self._show_settings)
        self._tray.exit_app.connect(self._exit_app)

    def start(self):
        if self._config.get("first_run", True):
            self._show_wizard()
        else:
            self._start_background()

    def _show_wizard(self):
        self._wizard = SetupWizard(self._config)
        self._wizard.finished.connect(self._on_wizard_finished)
        self._wizard.show()

    def _on_wizard_finished(self):
        self._wizard = None
        self._start_background()
        QTimer.singleShot(2000, self._show_welcome)

    def _start_background(self):
        self._register_hotkeys()
        self._hotkey_mgr.start()
        if self._config.get("display.tray_icon", False):
            self._tray.show()

    def _register_hotkeys(self):
        hotkeys = self._config.get("hotkeys", {})
        for name, combo in hotkeys.items():
            if combo:
                try:
                    self._hotkey_mgr.register(name, combo)
                except ValueError:
                    pass

    def _show_welcome(self):
        self._ensure_conversation()
        self._show_chat()
        if self._chat_window:
            self._chat_window.add_system_message("欢迎使用！一切就绪。")
            self._chat_window.add_system_message("按 Esc 或再按唤起热键关闭窗口。输入 /help 查看可用命令。")

    @pyqtSlot(str)
    def _on_hotkey(self, name: str):
        if self._env_monitor.is_silent and name != "boss_key":
            return

        actions = {
            "toggle_chat": self._toggle_chat,
            "boss_key": self._boss_key,
            "clipboard_ask": self._clipboard_ask,
            "screenshot_ask": self._screenshot_ask,
            "screenshot_full": self._screenshot_full,
            "exit": self._exit_app,
        }
        action = actions.get(name)
        if action:
            action()

    def _toggle_chat(self):
        if self._chat_window and self._chat_window.isVisible():
            self._hide_chat()
        else:
            self._show_chat()

    def _ensure_chat_window(self) -> ChatWindow:
        self._ensure_conversation()
        if not self._chat_window:
            self._create_chat_window()
        return self._chat_window

    def _show_chat(self):
        self._ensure_chat_window()
        if not self._chat_window.isVisible():
            self._chat_window.show()

    def _hide_chat(self):
        if self._chat_window and self._chat_window.isVisible():
            self._chat_window.hide()

    def _create_chat_window(self):
        c = self._config
        self._chat_window = ChatWindow(
            width=c.get("display.chat_width", 420),
            height=c.get("display.chat_height", 520),
            opacity=c.get("display.chat_opacity", 0.9),
            position=c.get("display.chat_position", "bottom_right"),
            screenshot_protection=c.get("display.screenshot_protection", True),
            theme=c.get("display.theme", "dark"),
        )
        self._chat_window.message_sent.connect(self._on_user_message)
        self._chat_window.command_entered.connect(self._on_command)
        self._chat_window.screenshot_requested.connect(self._screenshot_ask)
        self._chat_window.file_sent.connect(self._on_file_sent)
        self._chat_window.close_requested.connect(self._hide_chat)
        self._chat_window.open_settings_requested.connect(self._show_settings)
        self._chat_window.conversations_panel_opened.connect(self._on_conversations_panel_opened)
        self._chat_window.conversation_selected.connect(self._on_conversation_selected)

        for msg in self._messages:
            if msg["role"] == "user":
                self._chat_window.add_user_message(msg["content"], msg.get("image"))
            elif msg["role"] == "assistant":
                self._chat_window.add_ai_message(msg["content"])

    def _boss_key(self):
        if self._chat_window:
            self._chat_window.hide()
        if self._settings_panel:
            self._settings_panel.hide()
        if self._screenshot_overlay:
            self._screenshot_overlay.hide()
        self._notification.hide()

    def _clipboard_ask(self):
        if self._env_monitor.is_silent:
            return
        app = QApplication.instance()
        clipboard = app.clipboard()
        text = clipboard.text()
        if not text or not text.strip():
            return
        self._ensure_conversation()
        prompt = self._config.get("prompts.clipboard", "")
        content = f"{text.strip()}"
        self._send_to_ai(content, prompt, notify=True)

    def _screenshot_ask(self):
        if self._env_monitor.is_silent:
            return
        if self._screenshot_overlay and self._screenshot_overlay.isVisible():
            return
        if not self._build_client().supports_vision:
            self._show_chat()
            if self._chat_window:
                self._chat_window.add_system_message(
                    f"当前模型（{self._config.api_model}）不支持图片输入。\n"
                    f"截图问答请切换到 Claude、GPT-4o 等视觉模型。"
                )
            return
        self._screenshot_overlay = ScreenshotOverlay(
            self._config.get("display.screenshot_protection", True)
        )
        self._screenshot_overlay.captured.connect(self._on_screenshot_captured)
        self._screenshot_overlay.cancelled.connect(self._on_screenshot_cancelled)
        if self._chat_window:
            self._chat_window.hide()
        self._screenshot_overlay.start_capture()

    def _on_screenshot_captured(self, image_data: bytes):
        self._screenshot_overlay = None
        self._ensure_conversation()
        if self._config.get("display.screenshot_success_toast", True):
            text = self._config.get("display.screenshot_success_text", "成功") or "成功"
            self._notification.show(text)
        prompt = self._config.get("prompts.screenshot", "")
        msg = self._config.get("prompts.screenshot_message", "请分析这张截图") or "请分析这张截图"
        self._send_to_ai(msg, prompt, image_data=image_data, notify=True)

    def _on_screenshot_cancelled(self):
        self._screenshot_overlay = None

    def _screenshot_full(self):
        if self._env_monitor.is_silent:
            return
        if not self._build_client().supports_vision:
            self._show_chat()
            if self._chat_window:
                self._chat_window.add_system_message(
                    f"当前模型（{self._config.api_model}）不支持图片输入。\n"
                    f"整屏截图请切换到 Claude、GPT-4o 等视觉模型。"
                )
            return
        # 若对话窗可见，先隐藏再延迟抓屏，避免把自己截进去。
        if self._chat_window and self._chat_window.isVisible():
            self._chat_window.hide()
            QTimer.singleShot(150, self._do_full_capture)
        else:
            self._do_full_capture()

    def _do_full_capture(self):
        from .screenshot import grab_fullscreen_png
        data = grab_fullscreen_png()
        if not data:
            return
        self._ensure_conversation()
        if self._config.get("display.screenshot_success_toast", True):
            text = self._config.get("display.screenshot_success_text", "成功") or "成功"
            self._notification.show(text)
        prompt = self._config.get("prompts.screenshot", "")
        msg = self._config.get("prompts.screenshot_message", "请分析这张截图") or "请分析这张截图"
        self._send_to_ai(msg, prompt, image_data=data, notify=True)

    def _ensure_conversation(self):
        if not self._current_conv_id:
            self._current_conv_id = self._db.create_conversation(self._config.api_model)
            self._messages = []

    def _on_user_message(self, text: str):
        self._ensure_conversation()
        self._messages.append({"role": "user", "content": text})
        self._db.add_message(self._current_conv_id, "user", text)
        if self._chat_window:
            self._chat_window.add_user_message(text)
        prompt = self._config.get("prompts.chat", "")
        self._send_to_ai_stream(prompt)

    def _on_command(self, text: str):
        self._commands.handle(text)

    def _on_file_sent(self, path: str):
        from pathlib import Path
        self._ensure_conversation()
        ext = Path(path).suffix.lower()
        image_exts = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"}
        if ext in image_exts:
            if not self._build_client().supports_vision:
                self._ensure_chat_window().add_system_message(
                    f"当前模型（{self._config.api_model}）不支持图片输入。\n"
                    f"请切换到 Claude、GPT-4o 等视觉模型。"
                )
                return
            try:
                from PIL import Image
                import io
                img = Image.open(path).convert("RGB")
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                data = buf.getvalue()
            except Exception as e:
                self._ensure_chat_window().add_system_message(f"读取图片失败: {e}")
                return
            self._send_to_ai("请分析这张图片", self._config.get("prompts.chat", ""), image_data=data)
        else:
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    text = f.read(20000)
            except Exception as e:
                self._ensure_chat_window().add_system_message(f"读取文件失败: {e}")
                return
            self._send_to_ai(
                f"文件 {Path(path).name} 内容:\n\n{text}",
                self._config.get("prompts.chat", ""),
            )

    def _send_to_ai(self, content: str, system_prompt: str, image_data: bytes = None, notify: bool = False):
        self._ensure_conversation()
        msg = {"role": "user", "content": content}
        if image_data:
            msg["image"] = image_data
        self._messages.append(msg)
        self._db.add_message(self._current_conv_id, "user", content, image_data)

        self._ensure_chat_window().add_user_message(content, image_data)

        self._send_to_ai_stream(system_prompt, notify=notify)

    def _send_to_ai_stream(self, system_prompt: str, notify: bool = False):
        client = self._build_client()
        self._current_worker = client.create_worker(self._messages, system_prompt)

        self._ensure_chat_window().start_ai_message()

        self._notify_on_finish = notify

        self._current_worker.chunk_received.connect(self._on_ai_chunk)
        self._current_worker.finished.connect(self._on_ai_finished)
        self._current_worker.stats_ready.connect(self._on_ai_stats)
        self._current_worker.error.connect(self._on_ai_error)
        self._current_worker.start()

    def _on_ai_chunk(self, text: str):
        if self._chat_window:
            self._chat_window.append_ai_text(text)

    def _on_ai_finished(self, full_text: str):
        # finish_ai_message (with stats) is called by _on_ai_stats which fires right after
        self._pending_full_text = full_text
        self._messages.append({"role": "assistant", "content": full_text})
        if self._current_conv_id:
            self._db.add_message(self._current_conv_id, "assistant", full_text)
        if self._notify_on_finish and (not self._chat_window or not self._chat_window.isVisible()):
            self._notification.show(full_text)

    def _on_ai_stats(self, elapsed: float, tokens_in: int, tokens_out: int):
        if self._chat_window:
            self._chat_window.finish_ai_message(elapsed, tokens_in, tokens_out)
        self._current_worker = None

    def _on_ai_error(self, error: str):
        # Roll back the failed user message so the next request starts from last clean state
        if self._messages and self._messages[-1]["role"] == "user":
            self._messages.pop()
            if self._current_conv_id:
                self._db.delete_last_message(self._current_conv_id)
        if self._chat_window:
            self._chat_window.finish_ai_message()
            self._chat_window.add_system_message(f"错误: {error}")
        self._notification.show(f"错误: {error}")
        self._current_worker = None

    def _build_client(self) -> ApiClient:
        c = self._config
        return ApiClient(
            provider=c.provider,
            api_key=c.api_key,
            model=c.api_model,
            endpoint=c.api_endpoint,
            proxy=c.get("api.proxy", ""),
            extra_body=c.api_extra_body,
        )

    def _show_settings(self):
        if not self._settings_panel:
            self._settings_panel = SettingsPanel(self._config)
            self._settings_panel.settings_changed.connect(self._on_settings_changed)
        self._settings_panel.show()

    def _on_settings_changed(self):
        self._notification.set_disguise(self._config.get("display.notification_disguise", "none"))
        self._env_monitor.update_process_list(self._config.get("environment.suspicious_processes", []))
        if self._config.get("display.tray_icon", False):
            self._tray.show()
        else:
            self._tray.hide()
        if self._chat_window:
            self._chat_window.hide()
            self._chat_window = None

    def _new_conversation(self):
        self._current_conv_id = self._db.create_conversation(self._config.api_model)
        self._messages = []
        if self._chat_window:
            self._chat_window.clear_messages()
            self._chat_window.add_system_message("已创建新对话")

    def _list_conversations(self):
        self._show_chat()
        self._on_conversations_panel_opened()

    def _on_conversations_panel_opened(self):
        convs = self._db.list_conversations()
        if self._chat_window:
            self._chat_window.show_conversations(convs, self._current_conv_id or "")

    def _on_conversation_selected(self, conv_id: str):
        if conv_id == self._current_conv_id:
            return
        messages = self._db.get_messages(conv_id)
        self._current_conv_id = conv_id
        self._messages = [{"role": m["role"], "content": m["content"]} for m in messages]
        if self._chat_window:
            self._chat_window.clear_messages()
            for msg in self._messages:
                if msg["role"] == "user":
                    self._chat_window.add_user_message(msg["content"])
                elif msg["role"] == "assistant":
                    self._chat_window.add_ai_message(msg["content"])

    def _clear_conversation(self):
        if self._current_conv_id:
            self._db.clear_conversation(self._current_conv_id)
            self._messages = []
            if self._chat_window:
                self._chat_window.clear_messages()
                self._chat_window.add_system_message("对话已清除")

    def _delete_conversation(self):
        if self._current_conv_id:
            self._db.delete_conversation(self._current_conv_id)
            self._current_conv_id = None
            self._messages = []
            if self._chat_window:
                self._chat_window.clear_messages()
                self._chat_window.add_system_message("对话已删除")

    def _switch_model(self):
        models = {
            "claude": ["claude-sonnet-4-20250514", "claude-opus-4-20250514", "claude-haiku-4-5-20251001"],
            "openai": ["gpt-4o", "gpt-4o-mini", "o1"],
            "deepseek": ["deepseek-v4-pro", "deepseek-v4-flash", "deepseek-chat", "deepseek-reasoner"],
        }
        available = models.get(self._config.provider, [])
        if self._chat_window and available:
            lines = [f"当前: {self._config.api_model}", "可用模型:"]
            for m in available:
                lines.append(f"  - {m}")
            lines.append("请在设置中切换模型")
            self._chat_window.add_system_message("\n".join(lines))

    def _translate(self, text: str):
        if not text:
            app = QApplication.instance()
            text = app.clipboard().text()
        if not text or not text.strip():
            if self._chat_window:
                self._chat_window.add_system_message("没有可翻译的内容")
            return
        self._ensure_conversation()
        self._send_to_ai(f"请翻译以下内容:\n{text.strip()}", "你是一个翻译助手。将内容翻译为中文（如果原文是中文则翻译为英文）。只输出翻译结果。")

    def _summarize(self):
        app = QApplication.instance()
        text = app.clipboard().text()
        if not text or not text.strip():
            if self._chat_window:
                self._chat_window.add_system_message("剪贴板为空")
            return
        self._ensure_conversation()
        self._send_to_ai(f"请总结以下内容:\n{text.strip()}", "用简洁的要点总结用户提供的内容。")

    def _export_conversation(self):
        if not self._messages:
            if self._chat_window:
                self._chat_window.add_system_message("当前对话为空")
            return
        from PyQt6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getSaveFileName(None, "导出对话", "conversation.txt", "Text Files (*.txt)")
        if path:
            with open(path, "w", encoding="utf-8") as f:
                for m in self._messages:
                    role = "用户" if m["role"] == "user" else "AI"
                    f.write(f"[{role}]\n{m['content']}\n\n")
            if self._chat_window:
                self._chat_window.add_system_message(f"已导出到 {path}")

    def _show_help(self):
        if self._chat_window:
            self._chat_window.add_system_message(CommandHandler.HELP_TEXT)

    def _unknown_command(self, cmd: str):
        if self._chat_window:
            self._chat_window.add_system_message(f"未知命令: {cmd}\n输入 /help 查看帮助")

    def _exit_app(self):
        if self._config.get("privacy.clear_on_exit", False):
            import os
            db_path = self._config.db_path
            self._db.close()
            if db_path.exists():
                os.remove(db_path)
        else:
            retention = self._config.get("privacy.history_retention_days", 0)
            if retention > 0:
                self._db.cleanup_old(retention)
            self._db.close()

        self._hotkey_mgr.stop()
        self._env_monitor.stop()
        self._tray.hide()
        self._notification.hide()
        QApplication.instance().quit()
