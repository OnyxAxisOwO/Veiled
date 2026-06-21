import sys
import time
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QObject, QTimer, pyqtSlot

from .config import Config
from .database import Database
from .api_client import (
    ApiClient, ApiWorker, VisionPipelineWorker, ImageDescribeWorker, embed_description,
)
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

        from .theme import apply_theme
        apply_theme(QApplication.instance(), self._config.get("display.theme", "dark"),
                    self._config.get("display.accent_color", "#3b82f6"))

        self._chat_window: ChatWindow | None = None
        self._chat_struct: tuple | None = None   # 窗口结构性参数签名，变化时才重建窗口
        self._settings_panel: SettingsPanel | None = None
        self._screenshot_overlay: ScreenshotOverlay | None = None
        self._current_worker: ApiWorker | None = None

        self._current_conv_id: str | None = None
        self._messages: list[dict] = []
        self._last_answer: str = ""   # 主模型回答（单模型 / 兼容用）
        self._last_answers: list[tuple] = []   # [(模型名, 回答), ...]，供托盘菜单分模型展示

        # 并行多模型批处理状态
        self._batch: list[dict] = []
        self._batch_notify: bool = False
        self._batch_shared_desc: str = ""
        self._batch_pending: tuple | None = None
        self._vlm_worker: ImageDescribeWorker | None = None
        # 每次发起新请求 / 切换对话都自增；批处理回调据此丢弃过期批次，避免覆盖新状态
        self._batch_gen: int = 0

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

        self._notification = NotificationManager(self._config.get("display.notification_title", ""))
        self._notification.notification_clicked.connect(self._toggle_chat)

        self._env_monitor = EnvironmentMonitor(self._config.get("environment.suspicious_processes", []))
        self._env_monitor.silent_mode_changed.connect(self._on_silent_changed)
        if self._config.get("environment.monitor_enabled", True):
            self._env_monitor.start()

        self._tray = TrayManager()
        self._tray.open_chat.connect(self._toggle_chat)
        self._tray.open_settings.connect(self._show_settings)
        self._tray.exit_app.connect(self._exit_app)
        self._tray.ask_question.connect(self._on_menu_question)
        self._tray.screenshot_region.connect(self._screenshot_ask)
        self._tray.screenshot_full.connect(self._screenshot_full)
        self._tray.clipboard_ask.connect(self._clipboard_ask)
        self._tray.new_conversation.connect(self._new_conversation)
        self._tray.models_changed.connect(self._on_models_changed)
        self._tray.toggle_windowless_mode.connect(self._on_tray_toggle_windowless)

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
        # 托盘菜单现在是「无窗口」主交互入口，始终常驻；通知复用同一个图标。
        self._tray.set_menu_style(self._config.get("display.menu_style", "native"))
        self._tray.set_accent(self._config.get("display.accent_color", "#3b82f6"))
        self._tray.set_model_options(*self._model_options())
        self._tray.set_last_answers(self._last_answers)
        self._tray.set_windowless_mode(self._config.get("display.windowless_mode", False))
        self._tray.show()
        self._notification.set_tray(self._tray.tray_icon())

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

    def _notify_windowless_blocked(self):
        """无窗口模式拦截了打开窗口的操作时，按用户设置决定是否发通知。"""
        if self._config.get("display.windowless_notify", True):
            self._notification.show("无窗口模式已开启，窗口不可用。\n可通过托盘右键菜单关闭此模式。")

    def _toggle_chat(self):
        # 隐藏操作在无窗口模式下仍然允许（不拦截 hide）
        if self._chat_window and self._chat_window.isVisible():
            self._hide_chat()
            return
        if self._config.get("display.windowless_mode", False):
            self._notify_windowless_blocked()
            return
        self._show_chat()

    def _ensure_chat_window(self) -> ChatWindow:
        self._ensure_conversation()
        if not self._chat_window:
            self._create_chat_window()
        return self._chat_window

    def _show_chat(self):
        if self._config.get("display.windowless_mode", False):
            self._notify_windowless_blocked()
            return
        self._ensure_chat_window()
        if not self._chat_window.isVisible():
            self._chat_window.show()

    def _hide_chat(self):
        if self._chat_window and self._chat_window.isVisible():
            self._chat_window.hide()

    def _chat_struct_sig(self) -> tuple:
        """对话窗的结构性参数（只有这些变化才需要重建窗口；主题/背景可原地切换）。"""
        c = self._config
        return (
            c.get("display.chat_width", 420),
            c.get("display.chat_height", 520),
            c.get("display.chat_opacity", 0.9),
            c.get("display.chat_position", "bottom_right"),
            c.get("display.screenshot_protection", True),
        )

    def _create_chat_window(self):
        c = self._config
        self._chat_struct = self._chat_struct_sig()
        self._chat_window = ChatWindow(
            width=c.get("display.chat_width", 420),
            height=c.get("display.chat_height", 520),
            opacity=c.get("display.chat_opacity", 0.9),
            position=c.get("display.chat_position", "bottom_right"),
            screenshot_protection=c.get("display.screenshot_protection", True),
            theme=c.get("display.theme", "dark"),
            accent=c.get("display.accent_color", "#3b82f6"),
        )
        self._chat_window.message_sent.connect(self._on_user_message)
        self._chat_window.command_entered.connect(self._on_command)
        self._chat_window.screenshot_requested.connect(self._screenshot_ask)
        self._chat_window.file_sent.connect(self._on_file_sent)
        self._chat_window.close_requested.connect(self._hide_chat)
        self._chat_window.open_settings_requested.connect(self._show_settings)
        self._chat_window.conversation_selected.connect(self._on_conversation_selected)
        self._chat_window.conversation_delete_requested.connect(self._on_history_delete)
        self._chat_window.conversations_clear_all_requested.connect(self._on_history_clear_all)
        self._chat_window.models_changed.connect(self._on_models_changed)
        self._chat_window.manage_providers_requested.connect(self._open_providers_settings)

        self._chat_window.set_model_options(*self._model_options())
        bg_path = c.get("display.bg_image_path", "")
        bg_mode = c.get("display.bg_fill_mode", "fill")
        if bg_path:
            self._chat_window.set_background(bg_path, bg_mode)

        for msg in self._messages:
            if msg["role"] == "user":
                self._chat_window.add_user_message(msg["content"], msg.get("image"))
            elif msg["role"] == "assistant":
                self._chat_window.add_ai_message(msg["content"])

    def _surface_message(self, text: str):
        """把一条提示展示给用户：对话窗已开则进窗，否则用通知，绝不为此新开窗口。"""
        if self._chat_window and self._chat_window.isVisible():
            self._chat_window.add_system_message(text)
        else:
            self._notification.show(text)

    def _capture_unsupported_msg(self) -> str:
        return (
            f"当前模型（{self._config.api_model}）不支持图片输入。\n"
            f"请在托盘菜单「切换模型」选择带 👁 的视觉模型，或在设置中开启视觉识别中继。"
        )

    def _boss_key(self):
        if self._chat_window:
            self._chat_window.hide()
        if self._settings_panel:
            self._settings_panel.hide()
        if self._screenshot_overlay:
            self._screenshot_overlay.hide()
        self._notification.hide()

    def _on_silent_changed(self, is_silent: bool):
        # 环境检测关闭时绝不弹通知（即便配置里残留 notify_on_silent=True）
        if (self._config.get("environment.monitor_enabled", True)
                and self._config.get("environment.notify_on_silent", True)):
            if is_silent:
                names = "、".join(self._env_monitor.detected_processes)
                self._notification.show(f"⚠ 检测到监控软件（{names}），快捷键已屏蔽")
            else:
                self._notification.show("✅ 监控软件已退出，快捷键已恢复")
        if self._settings_panel:
            self._settings_panel.update_detected_processes(self._env_monitor.detected_processes)

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
        if not self._can_capture():
            self._surface_message(self._capture_unsupported_msg())
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
        if not self._can_capture():
            self._surface_message(self._capture_unsupported_msg())
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
        self._dispatch(prompt, notify=False, has_image=False)

    def _on_menu_question(self, text: str):
        """托盘菜单输入框提交的问题：走「无窗口」问答，答案经通知 + 菜单「上次回答」呈现。"""
        text = (text or "").strip()
        if not text:
            return
        if self._env_monitor.is_silent:
            return
        self._ensure_conversation()
        if text.startswith("/"):
            self._commands.handle(text)
            return
        self._messages.append({"role": "user", "content": text})
        self._db.add_message(self._current_conv_id, "user", text)
        if self._chat_window:
            self._chat_window.add_user_message(text)
        prompt = self._config.get("prompts.chat", "")
        self._dispatch(prompt, notify=True, has_image=False)

    def _on_command(self, text: str):
        self._commands.handle(text)

    def _on_file_sent(self, path: str):
        from pathlib import Path
        self._ensure_conversation()
        ext = Path(path).suffix.lower()
        image_exts = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"}
        if ext in image_exts:
            if not self._can_capture():
                self._ensure_chat_window().add_system_message(
                    f"当前模型（{self._config.api_model}）不支持图片输入。\n"
                    f"请点左上角模型名切换到带 👁 的视觉模型，或在设置中开启视觉识别中继。"
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

        # 只有对话窗已经打开时才渲染气泡；菜单/剪贴板/截图问答不创建任何窗口。
        if self._chat_window:
            self._chat_window.add_user_message(content, image_data)

        self._dispatch(system_prompt, notify=notify, has_image=bool(image_data))

    def _send_to_ai_stream(self, system_prompt: str, notify: bool = False):
        client = self._build_client()
        self._current_worker = client.create_worker(self._messages, system_prompt)

        if self._chat_window:
            self._chat_window.start_ai_message()

        self._notify_on_finish = notify

        self._current_worker.chunk_received.connect(self._on_ai_chunk)
        self._current_worker.finished.connect(self._on_ai_finished)
        self._current_worker.stats_ready.connect(self._on_ai_stats)
        self._current_worker.error.connect(self._on_ai_error)
        self._current_worker.start()

    def _send_image_pipeline(self, llm_system_prompt: str, notify: bool = False):
        vlm = self._build_vlm_client()
        llm = self._build_client()
        vlm_prompt = self._config.get("api.vision_relay.prompt", "")
        self._current_worker = VisionPipelineWorker(
            vlm, llm, self._messages, llm_system_prompt, vlm_prompt
        )
        if self._chat_window:
            self._chat_window.start_ai_message()
        self._notify_on_finish = notify
        self._current_worker.vlm_done.connect(self._on_vlm_done)
        self._current_worker.chunk_received.connect(self._on_ai_chunk)
        self._current_worker.finished.connect(self._on_ai_finished)
        self._current_worker.stats_ready.connect(self._on_ai_stats)
        self._current_worker.error.connect(self._on_ai_error)
        self._current_worker.start()

    def _on_vlm_done(self, desc: str):
        # 把识别结果固化进历史、丢掉图片字节，使后续轮次不带视觉的 LLM 仍记得图里的内容
        for m in reversed(self._messages):
            if m.get("image"):
                m["content"] = embed_description(m.get("content", ""), desc)
                m.pop("image", None)
                break

    # ── 单模型 / 多模型分发 ──────────────────────────────────────────────────

    def _dispatch(self, system_prompt: str, notify: bool = False, has_image: bool = False):
        """根据当前选中模型数量，走单模型（原有路径）或并行多模型（新路径）。"""
        self._batch_gen += 1   # 任一新请求开始，作废仍在途的旧批次
        models = self._config.active_models()
        if len(models) >= 2:
            self._start_batch(models, system_prompt, notify, self._batch_gen)
            return
        if has_image and self._vision_relay_enabled():
            self._send_image_pipeline(system_prompt, notify=notify)
        else:
            self._send_to_ai_stream(system_prompt, notify=notify)

    # ── 并行多模型 ────────────────────────────────────────────────────────────

    def _start_batch(self, models: list, system_prompt: str, notify: bool, gen: int):
        self._batch = []
        self._batch_notify = notify
        self._batch_shared_desc = ""
        image = self._last_image()
        needs_relay = image is not None and any(not self._model_supports_vision(m) for m in models)
        if needs_relay and self._vision_relay_enabled():
            # 截图只识别一次，识别文本再分发给所有不支持图片的模型
            self._batch_pending = (models, system_prompt)
            if self._chat_window and self._chat_window.isVisible():
                self._chat_window.add_system_message("正在识别图片…")
            self._start_shared_vlm(image, gen)
        else:
            self._launch_batch(models, system_prompt, "", gen)

    def _start_shared_vlm(self, image: bytes, gen: int):
        vlm = self._build_vlm_client()
        prompt = self._config.get("api.vision_relay.prompt", "")
        self._vlm_worker = ImageDescribeWorker(vlm, image, prompt)
        self._vlm_worker.done.connect(lambda desc, g=gen: self._on_shared_vlm_done(desc, g))
        self._vlm_worker.error.connect(lambda err, g=gen: self._on_shared_vlm_error(err, g))
        self._vlm_worker.start()

    def _on_shared_vlm_done(self, desc: str, gen: int):
        if gen != self._batch_gen:
            return
        self._vlm_worker = None
        if not self._batch_pending:
            return
        models, system_prompt = self._batch_pending
        self._batch_pending = None
        self._launch_batch(models, system_prompt, desc, gen)

    def _on_shared_vlm_error(self, err: str, gen: int):
        if gen != self._batch_gen:
            return
        self._vlm_worker = None
        self._batch_pending = None
        # 识别失败：回滚刚加入的用户消息，回到上一个干净状态
        if self._messages and self._messages[-1]["role"] == "user":
            self._messages.pop()
            if self._current_conv_id:
                self._db.delete_last_message(self._current_conv_id)
        if self._chat_window:
            self._chat_window.add_system_message(f"错误: {err}")
        self._notification.show(f"错误: {err}")

    def _launch_batch(self, models: list, system_prompt: str, shared_desc: str, gen: int):
        self._batch_shared_desc = shared_desc or ""
        self._batch = []
        for i, mdef in enumerate(models):
            pid, mid = mdef["provider"], mdef["model"]
            label = self._model_label(pid, mid)
            vision = self._model_supports_vision(mdef)
            msgs = self._messages_for_model(vision, shared_desc)
            client = self._build_client_for(pid, mid, vision)
            worker = client.create_worker(msgs, system_prompt)
            bubble = self._chat_window.start_ai_message(label) if self._chat_window else None
            self._batch.append({
                "label": label, "text": "", "bubble": bubble,
                "done": False, "error": None, "worker": worker,
            })
            worker.chunk_received.connect(lambda t, i=i, g=gen: self._on_batch_chunk(i, t, g))
            worker.finished.connect(lambda full, i=i, g=gen: self._on_batch_finished(i, full, g))
            worker.stats_ready.connect(lambda e, a, b, i=i, g=gen: self._on_batch_stats(i, e, a, b, g))
            worker.error.connect(lambda err, i=i, g=gen: self._on_batch_error(i, err, g))
        for slot in self._batch:
            slot["worker"].start()

    def _on_batch_chunk(self, i: int, text: str, gen: int):
        if gen != self._batch_gen:
            return
        if 0 <= i < len(self._batch):
            slot = self._batch[i]
            slot["text"] += text
            if slot["bubble"]:
                slot["bubble"].append_text(text)
                if i == 0 and self._chat_window:
                    self._chat_window.scroll_to_bottom()

    def _on_batch_finished(self, i: int, full: str, gen: int):
        if gen != self._batch_gen:
            return
        if 0 <= i < len(self._batch):
            self._batch[i]["text"] = full

    def _on_batch_stats(self, i: int, elapsed: float, tokens_in: int, tokens_out: int, gen: int):
        if gen != self._batch_gen:
            return
        if 0 <= i < len(self._batch):
            slot = self._batch[i]
            if slot["bubble"]:
                slot["bubble"].set_stats(elapsed, tokens_in, tokens_out)
            slot["done"] = True
            self._maybe_finalize_batch(gen)

    def _on_batch_error(self, i: int, err: str, gen: int):
        if gen != self._batch_gen:
            return
        if 0 <= i < len(self._batch):
            slot = self._batch[i]
            slot["error"] = err
            slot["done"] = True
            if slot["bubble"]:
                slot["bubble"].append_text(f"\n[错误] {err}")
                slot["bubble"].set_stats()
            self._maybe_finalize_batch(gen)

    def _maybe_finalize_batch(self, gen: int):
        if gen == self._batch_gen and self._batch and all(s["done"] for s in self._batch):
            self._finalize_batch()

    def _finalize_batch(self):
        batch = self._batch
        self._batch = []
        successes = [(s["label"], s["text"]) for s in batch if not s["error"] and s["text"].strip()]
        had_error = any(s["error"] for s in batch)

        # 若做过共享视觉识别，把识别文本固化进历史并丢掉图片字节，供后续追问
        if self._batch_shared_desc:
            for m in reversed(self._messages):
                if m.get("image"):
                    m["content"] = embed_description(m.get("content", ""), self._batch_shared_desc)
                    m.pop("image", None)
                    break
        self._batch_shared_desc = ""

        if successes:
            mode = self._config.get("api.multi_history_mode", "primary")
            if mode == "all":
                combined = "\n\n".join(f"【{lbl}】\n{txt}" for lbl, txt in successes)
                self._append_assistant(combined)
            else:
                primary = batch[0]
                text = primary["text"] if (not primary["error"] and primary["text"].strip()) else successes[0][1]
                self._append_assistant(text)
        elif had_error:
            # 确有失败且无任何成功：回滚用户消息，回到上一个干净状态
            if self._messages and self._messages[-1]["role"] == "user":
                self._messages.pop()
                if self._current_conv_id:
                    self._db.delete_last_message(self._current_conv_id)
        else:
            # 全部返回空但都没报错：保留用户消息（与单模型一致），落一条空回复占位
            self._append_assistant("")

        self._last_answers = successes
        self._last_answer = successes[0][1] if successes else ""
        self._tray.set_last_answers(self._last_answers)

        if self._batch_notify and (not self._chat_window or not self._chat_window.isVisible()):
            n = len(successes)
            if n >= 2:
                self._notification.show(f"{n} 个模型已回答，右键托盘图标查看")
            elif n == 1:
                self._notification.show(successes[0][1])
            elif had_error:
                err = next((s["error"] for s in batch if s["error"]), "请求失败")
                self._notification.show(f"错误: {err}")
            else:
                self._notification.show("模型未返回内容")

    def _append_assistant(self, text: str):
        self._messages.append({"role": "assistant", "content": text})
        if self._current_conv_id:
            self._db.add_message(self._current_conv_id, "assistant", text)

    # ── 多模型辅助 ────────────────────────────────────────────────────────────

    def _last_image(self) -> bytes | None:
        for m in reversed(self._messages):
            if m.get("image"):
                return m["image"]
        return None

    def _model_supports_vision(self, mdef: dict) -> bool:
        prov = self._config.get_provider(mdef.get("provider", ""))
        if not prov:
            return False
        for m in prov.get("models", []):
            if m.get("id") == mdef.get("model"):
                return bool(m.get("vision"))
        return False

    def _model_label(self, pid: str, mid: str) -> str:
        prov = self._config.get_provider(pid)
        if prov:
            for m in prov.get("models", []):
                if m.get("id") == mid:
                    return m.get("name") or mid
        return mid or "模型"

    def _messages_for_model(self, supports_vision: bool, shared_desc: str) -> list:
        """支持视觉的模型沿用带图片的历史；否则把图片替换成识别文本（或直接去掉）。"""
        if supports_vision:
            return list(self._messages)
        out = []
        for m in self._messages:
            if m.get("image"):
                out.append({"role": m["role"], "content": embed_description(m.get("content", ""), shared_desc)})
            else:
                out.append({"role": m["role"], "content": m.get("content", "")})
        return out

    def _build_client_for(self, pid: str, mid: str, supports_vision: bool) -> ApiClient:
        from .config import parse_extra_body
        prov = self._config.get_provider(pid) or {}
        return ApiClient(
            kind=prov.get("kind", "openai"),
            api_key=prov.get("api_key", ""),
            model=mid,
            endpoint=prov.get("endpoint", ""),
            proxy=self._config.proxy,
            extra_body=parse_extra_body(prov.get("extra_body", "")),
            supports_vision=supports_vision,
        )

    def _on_ai_chunk(self, text: str):
        if self._chat_window:
            self._chat_window.append_ai_text(text)

    def _on_ai_finished(self, full_text: str):
        # finish_ai_message (with stats) is called by _on_ai_stats which fires right after
        self._pending_full_text = full_text
        self._messages.append({"role": "assistant", "content": full_text})
        if self._current_conv_id:
            self._db.add_message(self._current_conv_id, "assistant", full_text)
        self._last_answer = full_text
        self._last_answers = [("", full_text)]
        self._tray.set_last_answers(self._last_answers)
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
            kind=c.api_kind,
            api_key=c.api_key,
            model=c.api_model,
            endpoint=c.api_endpoint,
            proxy=c.proxy,
            extra_body=c.api_extra_body,
            supports_vision=c.active_model_supports_vision(),
        )

    def _vision_relay_enabled(self) -> bool:
        """当前模型不支持视觉时，是否启用 VLM 中继（已开关且引用了有 key 的服务商）。"""
        c = self._config
        if not c.get("api.vision_relay.enabled", False):
            return False
        prov = c.get_provider(c.get("api.vision_relay.provider", ""))
        model = (c.get("api.vision_relay.model", "") or "").strip()
        return bool(prov and (prov.get("api_key", "") or "").strip() and model)

    def _can_capture(self) -> bool:
        """选中的任一模型能直接看图，或开了 VLM 中继，都允许截图。"""
        if self._vision_relay_enabled():
            return True
        return any(self._model_supports_vision(m) for m in self._config.active_models())

    def _build_vlm_client(self) -> ApiClient:
        from .config import parse_extra_body
        c = self._config
        prov = c.get_provider(c.get("api.vision_relay.provider", "")) or {}
        return ApiClient(
            kind=prov.get("kind", "openai"),
            api_key=prov.get("api_key", ""),
            model=c.get("api.vision_relay.model", ""),
            endpoint=prov.get("endpoint", ""),
            proxy=c.proxy,
            extra_body=parse_extra_body(prov.get("extra_body", "")),
            supports_vision=True,   # 中继模型必须能接收图片
        )

    def _model_options(self):
        """提供给对话窗 / 托盘菜单模型切换芯片的数据：(服务商列表, 主服务商, 主模型, 选中集合)。"""
        provs = []
        for p in self._config.providers():
            provs.append({
                "id": p.get("id"),
                "name": p.get("name") or p.get("id"),
                "models": [
                    {"id": m.get("id"), "name": m.get("name") or m.get("id"), "vision": bool(m.get("vision"))}
                    for m in p.get("models", []) if m.get("id")
                ],
            })
        selected = [(m["provider"], m["model"]) for m in self._config.active_models()]
        return (provs, self._config.get("api.active.provider", ""),
                self._config.get("api.active.model", ""), selected)

    def _on_models_changed(self, selected: list):
        """对话窗 / 托盘任一处改动选中集合：写回配置并同步另一处显示。"""
        models = [{"provider": p, "model": m} for p, m in (tuple(s) for s in selected) if p and m]
        if not models:
            return
        self._config.set_active_models(models)
        self._config.save()
        opts = self._model_options()
        self._tray.set_model_options(*opts)
        if self._chat_window:
            self._chat_window.set_model_options(*opts)

    def _open_providers_settings(self):
        self._show_settings()
        if self._settings_panel:
            self._settings_panel.open_providers_page()

    def _show_settings(self):
        if self._config.get("display.windowless_mode", False):
            self._notify_windowless_blocked()
            return
        if not self._settings_panel:
            self._settings_panel = SettingsPanel(self._config)
            self._settings_panel.settings_changed.connect(self._on_settings_changed)
        self._settings_panel.show()
        self._settings_panel.update_detected_processes(self._env_monitor.detected_processes)

    def _on_tray_toggle_windowless(self):
        """托盘菜单切换无窗口模式：不打开任何窗口，即时生效并保存。"""
        current = self._config.get("display.windowless_mode", False)
        new_val = not current
        self._config.set("display.windowless_mode", new_val)
        self._config.save()
        self._tray.set_windowless_mode(new_val)
        if new_val:
            if self._chat_window and self._chat_window.isVisible():
                self._chat_window.hide()
            if self._settings_panel and self._settings_panel.isVisible():
                self._settings_panel.hide()

    def _on_settings_changed(self):
        # 快捷键即时生效：重新登记全部热键，无需重启
        self._hotkey_mgr.reload(self._config.get("hotkeys", {}))

        self._notification.set_title(self._config.get("display.notification_title", ""))

        # 环境检测按开关即时启停；关闭时清空静默/检测状态并恢复快捷键
        self._env_monitor.update_process_list(self._config.get("environment.suspicious_processes", []))
        if self._config.get("environment.monitor_enabled", True):
            self._env_monitor.start()
        else:
            self._env_monitor.disable()

        new_accent = self._config.get("display.accent_color", "#3b82f6")
        # 托盘菜单常驻；设置变更后刷新菜单样式 / 强调色 / 模型列表并确保通知仍复用同一图标
        self._tray.show()
        self._tray.set_menu_style(self._config.get("display.menu_style", "native"))
        self._tray.set_accent(new_accent)
        self._tray.set_model_options(*self._model_options())
        self._notification.set_tray(self._tray.tray_icon())
        # 无窗口模式变更：同步状态到托盘，若刚开启则关闭所有窗口
        new_windowless = self._config.get("display.windowless_mode", False)
        self._tray.set_windowless_mode(new_windowless)
        if new_windowless:
            if self._chat_window and self._chat_window.isVisible():
                self._chat_window.hide()
            # 设置面板在 _save() → _on_close() 里已自行 hide，此处无需重复
        new_theme = self._config.get("display.theme", "dark")
        from .theme import apply_theme
        apply_theme(QApplication.instance(), new_theme, new_accent)
        if self._settings_panel:
            self._settings_panel._apply_style()

        if self._chat_window:
            # 服务商/模型增删后即时刷新模型切换芯片与菜单，无需重启
            self._chat_window.set_model_options(*self._model_options())
            # 主题、强调色与背景图原地切换，不销毁窗口（保留滚动位置/进行中的回复）
            self._chat_window.set_accent(new_accent)
            self._chat_window.set_theme(new_theme)
            self._chat_window.set_background(
                self._config.get("display.bg_image_path", ""),
                self._config.get("display.bg_fill_mode", "fill"),
            )
            # 仅当尺寸/位置/不透明度/截屏保护等结构性参数变化时才重建窗口
            if self._chat_struct_sig() != self._chat_struct:
                was_visible = self._chat_window.isVisible()
                self._chat_window.hide()
                self._chat_window = None
                if was_visible:
                    self._show_chat()

    def _new_conversation(self):
        self._batch_gen += 1   # 作废仍在途的批次，避免其结果落到新对话
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

    def _on_history_delete(self, conv_id: str):
        self._db.delete_conversation(conv_id)
        if conv_id == self._current_conv_id:
            self._batch_gen += 1
            self._current_conv_id = None
            self._messages = []
            if self._chat_window:
                self._chat_window.clear_messages()
        if self._chat_window:
            self._chat_window.show_conversations(
                self._db.list_conversations(), self._current_conv_id or ""
            )

    def _on_history_clear_all(self):
        from PyQt6.QtWidgets import QMessageBox
        box = QMessageBox(self._chat_window)
        box.setWindowTitle("确认删除")
        box.setText("将删除全部历史对话，此操作不可撤销。")
        box.setInformativeText("确认继续？")
        box.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        box.setDefaultButton(QMessageBox.StandardButton.No)
        box.button(QMessageBox.StandardButton.Yes).setText("删除全部")
        box.button(QMessageBox.StandardButton.No).setText("取消")
        if box.exec() != QMessageBox.StandardButton.Yes:
            return
        self._db.delete_all_conversations()
        self._batch_gen += 1
        self._current_conv_id = None
        self._messages = []
        if self._chat_window:
            self._chat_window.clear_messages()
            self._chat_window.show_conversations([], "")

    def _on_conversation_selected(self, conv_id: str):
        if conv_id == self._current_conv_id:
            return
        self._batch_gen += 1   # 作废仍在途的批次，避免其结果落到刚切换到的对话
        messages = self._db.get_messages(conv_id)
        self._current_conv_id = conv_id
        # 保留图片字节：既用于重新渲染图片气泡，也让重开的视觉对话在追问时仍带上原图
        self._messages = [
            {"role": m["role"], "content": m["content"],
             **({"image": m["image"]} if m.get("image") else {})}
            for m in messages
        ]
        if self._chat_window:
            self._chat_window.clear_messages()
            for msg in self._messages:
                if msg["role"] == "user":
                    self._chat_window.add_user_message(msg["content"], msg.get("image"))
                elif msg["role"] == "assistant":
                    self._chat_window.add_ai_message(msg["content"])

    def _clear_conversation(self):
        if not self._current_conv_id:
            self._surface_message("当前没有对话可以清空")
            return
        self._db.clear_conversation(self._current_conv_id)
        self._messages = []
        if self._chat_window:
            self._chat_window.clear_messages()
        self._surface_message("对话已清除")

    def _delete_conversation(self):
        if not self._current_conv_id:
            self._surface_message("当前没有对话可以删除")
            return
        self._db.delete_conversation(self._current_conv_id)
        self._current_conv_id = None
        self._messages = []
        if self._chat_window:
            self._chat_window.clear_messages()
        self._surface_message("对话已删除")

    def _switch_model(self):
        if not self._chat_window:
            return
        m = self._config.active_model()
        prov = self._config.active_provider()
        name = (m.get("name") or m.get("id")) if m else "(未设置)"
        prov_name = (prov.get("name") if prov else "") or ""
        self._chat_window.add_system_message(
            f"当前模型：{prov_name} · {name}\n"
            "点左上角的模型名即可切换服务商 / 模型；\n"
            "在「设置 → 服务商与模型」可新增服务商、获取模型列表、标记视觉模型。"
        )

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
