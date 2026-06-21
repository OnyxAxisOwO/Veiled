import ctypes
import ctypes.wintypes
from PyQt6.QtWidgets import (
    QSystemTrayIcon, QMenu, QWidgetAction, QLineEdit, QLabel,
    QWidget, QVBoxLayout, QScrollArea, QApplication,
)
from PyQt6.QtGui import QIcon, QPixmap, QPainter, QColor
from PyQt6.QtCore import pyqtSignal, QObject, Qt, QTimer

from .theme import hex_to_rgb_str
from .chat_window import MultiSelectMenu


MENU_QSS = """
    QMenu {
        background-color: #2b2b2b; color: #e0e0e0;
        border: 1px solid rgba(255,255,255,25);
        border-radius: 8px; padding: 6px;
        font-family: 'Microsoft YaHei'; font-size: 12px;
    }
    QMenu::item { padding: 6px 26px 6px 12px; border-radius: 5px; }
    QMenu::item:selected { background-color: rgba(59,130,246,160); color: white; }
    QMenu::item:disabled { color: #777; }
    QMenu::separator { height: 1px; background: rgba(255,255,255,20); margin: 5px 8px; }
    QWidget#menu_row { background: transparent; }
    QScrollArea#menu_answer_scroll { background: transparent; border: none; }
    QScrollArea#menu_answer_scroll > QWidget > QWidget { background: transparent; }
    QScrollBar:vertical { width: 6px; background: transparent; }
    QScrollBar::handle:vertical { background: rgba(255,255,255,45); border-radius: 3px; min-height: 20px; }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
    QLineEdit#menu_input {
        background: #3a3a3e; color: #ececec;
        border: 1px solid rgba(255,255,255,30); border-radius: 7px;
        padding: 6px 9px; font-size: 13px; font-family: 'Microsoft YaHei';
    }
    QLineEdit#menu_input:focus { border: 1px solid rgba(59,130,246,180); }
    QLabel#menu_caption { color: #8a8a8a; font-size: 11px; }
    QLabel#menu_answer { color: #dcdcdc; font-size: 12px; }
"""


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


class _MenuLineEdit(QLineEdit):
    """菜单内嵌输入框：回车即提交（不依赖菜单的默认键处理）。"""
    submit = pyqtSignal()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.submit.emit()
            return
        super().keyPressEvent(event)


class TrayManager(QObject):
    # 既有
    open_chat = pyqtSignal()
    open_settings = pyqtSignal()
    exit_app = pyqtSignal()
    # 新增：菜单驱动的「无窗口」交互
    ask_question = pyqtSignal(str)        # 输入框提交的问题文本
    screenshot_region = pyqtSignal()      # 区域截图提问
    screenshot_full = pyqtSignal()        # 全屏截图提问
    clipboard_ask = pyqtSignal()          # 剪贴板提问
    new_conversation = pyqtSignal()       # 新对话
    models_changed = pyqtSignal(list)     # 选中集合: [(provider_id, model_id), ...]，首项为主模型

    def __init__(self, parent=None):
        super().__init__(parent)
        self._tray: QSystemTrayIcon | None = None
        self._menu: QMenu | None = None
        self._input: _MenuLineEdit | None = None
        self._last_answer: str = ""
        self._answers: list[tuple] = []          # [(模型名, 回答), ...]；多模型时每个一项
        self._providers: list[dict] = []
        self._active_pid: str = ""
        self._active_mid: str = ""
        self._selected: list[tuple] = []         # 选中的 (pid, mid) 集合，首项为主模型
        self._menu_style: str = "native"        # native | styled
        self._accent_rgb: str = "59,130,246"     # 样式菜单高亮色（rgb），随主题色变化
        self._owner: QWidget | None = None       # 原生菜单的 owner 窗口（隐藏）
        self._native_model_map: dict[int, tuple] = {}
        self._native_copy_map: dict[int, str] = {}   # 原生菜单「复制某模型回答」命令 ID → 文本
        self._native_api_ready = False

    # ── 生命周期 ──────────────────────────────────────────────────────────────

    def show(self):
        if self._tray:
            return
        self._tray = QSystemTrayIcon()
        self._tray.setIcon(create_default_icon())
        self._tray.setToolTip("Display Adapter Helper")

        self._menu = QMenu()
        self._menu.setStyleSheet(self._styled_qss())
        # 每次弹出前重建（模型列表 / 上次回答可能已变），并尝试把键盘焦点抢给输入框
        self._menu.aboutToShow.connect(self._on_about_to_show)
        self._tray.activated.connect(self._on_activated)
        self._apply_menu_style()
        self._tray.show()

    def hide(self):
        if self._tray:
            self._tray.hide()
            self._tray = None
            self._menu = None
            self._input = None

    def _styled_qss(self) -> str:
        return MENU_QSS.replace("59,130,246", self._accent_rgb)

    def set_accent(self, accent: str):
        """设置样式菜单的高亮主题色（rgb 注入 MENU_QSS）。"""
        self._accent_rgb = hex_to_rgb_str(accent or "#3b82f6")
        if self._menu is not None:
            self._menu.setStyleSheet(self._styled_qss())

    def set_menu_style(self, style: str):
        """切换菜单实现：native = 原生 Windows 菜单；styled = 深色样式菜单（带输入框）。"""
        style = "styled" if style == "styled" else "native"
        if style == self._menu_style and self._tray is not None:
            return
        self._menu_style = style
        if self._tray is not None:
            self._apply_menu_style()

    def _apply_menu_style(self):
        if self._tray is None or self._menu is None:
            return
        if self._menu_style == "styled":
            self._rebuild_menu()
            self._tray.setContextMenu(self._menu)
        else:
            # 不挂 Qt 上下文菜单，右键交给 activated(Context) → 弹原生菜单
            self._tray.setContextMenu(None)
            self._ensure_owner()

    def _ensure_owner(self):
        # TrackPopupMenu 需要一个本进程的窗口句柄作 owner。用一个永不 show 的隐藏
        # QWidget 即可：winId() 触发原生句柄创建，但不显示、不进任务栏、不可见。
        if self._owner is None:
            self._owner = QWidget()
            self._owner.resize(0, 0)
            self._owner.winId()

    def tray_icon(self) -> QSystemTrayIcon | None:
        """暴露底层托盘图标，供通知系统复用同一个图标（避免出现两个托盘图标）。"""
        return self._tray

    # ── 数据注入 ──────────────────────────────────────────────────────────────

    def set_model_options(self, providers: list[dict], active_pid: str, active_mid: str,
                          selected: list = None):
        self._providers = providers or []
        self._active_pid = active_pid or ""
        self._active_mid = active_mid or ""
        if selected:
            self._selected = [tuple(s) for s in selected]
        elif self._active_mid:
            self._selected = [(self._active_pid, self._active_mid)]
        else:
            self._selected = []

    def set_last_answers(self, answers: list):
        """多模型回答：answers = [(模型名, 回答), ...]。供菜单「上次回答」分模型展示。"""
        self._answers = [(lbl or "", txt or "") for lbl, txt in (answers or []) if (txt or "").strip()]
        self._last_answer = self._answers[0][1] if self._answers else ""

    # ── 交互 ──────────────────────────────────────────────────────────────────

    def _on_activated(self, reason):
        R = QSystemTrayIcon.ActivationReason
        if reason == R.DoubleClick:
            self.open_chat.emit()
        elif reason == R.Context and self._menu_style == "native":
            # 原生模式不挂 Qt 菜单，右键由这里弹出 Win32 原生菜单
            self._show_native_menu()

    def _on_about_to_show(self):
        self._rebuild_menu()
        # 在菜单真正显示后再抢焦点（此时它已有原生窗口句柄）
        QTimer.singleShot(0, self._focus_input)

    def _focus_input(self):
        if not self._input or not self._menu:
            return
        # 托盘菜单默认不是前台窗口，内嵌输入框收不到键盘。把菜单窗口提到前台再给焦点。
        try:
            hwnd = int(self._menu.winId())
            ctypes.windll.user32.SetForegroundWindow(hwnd)
        except Exception:
            pass
        self._input.setFocus()

    def _submit_input(self):
        if not self._input:
            return
        text = self._input.text().strip()
        if not text:
            return
        if self._menu:
            self._menu.close()
        self.ask_question.emit(text)

    def _copy_answer(self):
        self._copy_text(self._last_answer)

    def _copy_text(self, text: str):
        app = QApplication.instance()
        if app and text:
            app.clipboard().setText(text)

    def _toggle_model(self, pid: str, mid: str):
        """原生菜单：点击模型即「勾选/取消」其参与并行（菜单已关闭，重开看勾选态）。"""
        key = (pid, mid)
        sel = list(self._selected)
        if key in sel:
            if len(sel) == 1:
                return   # 至少保留一个模型
            sel.remove(key)
        else:
            sel.append(key)
        self._selected = sel
        self.models_changed.emit([list(k) for k in sel])

    def _on_model_toggled(self, pid: str, mid: str, checked: bool, action):
        """样式菜单：可勾选项的 toggled 处理（菜单保持打开，可连续多选）。"""
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
        self.models_changed.emit([list(k) for k in sel])

    # ── 菜单构建 ──────────────────────────────────────────────────────────────

    def _rebuild_menu(self):
        m = self._menu
        if m is None:
            return
        m.clear()

        # 输入框
        self._input = _MenuLineEdit()
        self._input.setObjectName("menu_input")
        self._input.setPlaceholderText("输入问题，回车发送…")
        self._input.setMinimumWidth(300)
        self._input.setClearButtonEnabled(True)
        self._input.submit.connect(self._submit_input)
        m.addAction(self._wrap_widget(m, self._input))

        # 上次回答：单模型内嵌展示；多模型则每个模型一个子菜单
        if len(self._answers) == 1 and not self._answers[0][0]:
            m.addSeparator()
            wa = QWidgetAction(m)
            wa.setDefaultWidget(self._answer_widget(self._answers[0][1], "上次回答"))
            m.addAction(wa)
            copy_act = m.addAction("📋  复制上次回答")
            copy_act.triggered.connect(self._copy_answer)
        elif self._answers:
            m.addSeparator()
            head = m.addAction(f"上次回答（{len(self._answers)} 个模型）")
            head.setEnabled(False)
            for label, text in self._answers:
                sub = m.addMenu(f"🧠  {label or '模型'}")
                sub.setStyleSheet(self._styled_qss())
                wa = QWidgetAction(sub)
                wa.setDefaultWidget(self._answer_widget(text, label or "回答"))
                sub.addAction(wa)
                cp = sub.addAction("📋  复制该回答")
                cp.triggered.connect(lambda _checked=False, t=text: self._copy_text(t))

        # 提问入口
        m.addSeparator()
        m.addAction("📷  截图提问").triggered.connect(self.screenshot_region.emit)
        m.addAction("🖥  截全图提问").triggered.connect(self.screenshot_full.emit)
        m.addAction("📋  剪贴板提问").triggered.connect(self.clipboard_ask.emit)

        # 模型 / 对话
        m.addSeparator()
        model_menu = MultiSelectMenu(m)
        model_menu.setTitle("🧠  切换模型（可多选并行）")
        model_menu.setStyleSheet(self._styled_qss())
        self._build_model_submenu(model_menu)
        m.addMenu(model_menu)
        m.addAction("➕  新对话").triggered.connect(self.new_conversation.emit)

        # 其余
        m.addSeparator()
        m.addAction("💬  打开对话窗").triggered.connect(self.open_chat.emit)
        m.addAction("⚙  设置").triggered.connect(self.open_settings.emit)
        m.addAction("⏻  退出").triggered.connect(self.exit_app.emit)

    def _answer_widget(self, text: str, caption: str) -> QWidget:
        """把一段回答放进「标题 + 限高滚动区」的小部件，供内嵌/子菜单复用。"""
        ans = (text or "").strip()
        disp = ans if len(ans) <= 4000 else ans[:4000] + "…"
        box = QWidget()
        box.setObjectName("menu_row")
        box_l = QVBoxLayout(box)
        box_l.setContentsMargins(10, 4, 10, 4)
        box_l.setSpacing(4)
        cap = QLabel(caption)
        cap.setObjectName("menu_caption")
        ans_lbl = QLabel(disp)
        ans_lbl.setObjectName("menu_answer")
        ans_lbl.setWordWrap(True)
        ans_lbl.setMaximumWidth(340)
        ans_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        box_l.addWidget(cap)
        scroll = QScrollArea()
        scroll.setObjectName("menu_answer_scroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setMinimumWidth(320)
        scroll.setMaximumHeight(240)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(ans_lbl)
        box_l.addWidget(scroll)
        return box

    def _wrap_widget(self, menu: QMenu, widget: QWidget) -> QWidgetAction:
        wrap = QWidget()
        wrap.setObjectName("menu_row")
        layout = QVBoxLayout(wrap)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(0)
        layout.addWidget(widget)
        action = QWidgetAction(menu)
        action.setDefaultWidget(wrap)
        return action

    def _build_model_submenu(self, menu: QMenu):
        if not self._providers:
            act = menu.addAction("（尚未配置服务商）")
            act.setEnabled(False)
            return
        for p in self._providers:
            menu.addSection(p.get("name") or p.get("id") or "服务商")
            models = p.get("models", [])
            if not models:
                act = menu.addAction("  （无模型）")
                act.setEnabled(False)
                continue
            for mdl in models:
                mid = mdl.get("id", "")
                label = mdl.get("name") or mid
                if mdl.get("vision"):
                    label = f"👁 {label}"
                act = menu.addAction(label)
                act.setCheckable(True)
                act.setChecked((p.get("id"), mid) in self._selected)   # 先设勾选再连信号
                act.toggled.connect(
                    lambda checked, pid=p.get("id"), m_id=mid, a=act: self._on_model_toggled(pid, m_id, checked, a)
                )

    # ── 原生 Windows 菜单（Win32 TrackPopupMenu，无渲染、与系统一致）──────────────

    # 固定命令 ID；模型项从 100 起，运行时映射到 (provider_id, model_id)
    _CMD = {
        "screenshot_region": 1, "screenshot_full": 2, "clipboard_ask": 3,
        "new_conversation": 4, "open_chat": 5, "open_settings": 6,
        "exit_app": 7, "copy_answer": 8,
    }

    def _native_api(self):
        """配置一次 user32 函数签名（HMENU/HWND 是指针，默认 c_int 会在 64 位下截断）。"""
        u = ctypes.windll.user32
        if not self._native_api_ready:
            wt = ctypes.wintypes
            u.CreatePopupMenu.restype = ctypes.c_void_p
            u.CreatePopupMenu.argtypes = []
            u.AppendMenuW.restype = wt.BOOL
            u.AppendMenuW.argtypes = [ctypes.c_void_p, wt.UINT, ctypes.c_void_p, wt.LPCWSTR]
            u.TrackPopupMenu.restype = ctypes.c_int
            u.TrackPopupMenu.argtypes = [
                ctypes.c_void_p, wt.UINT, ctypes.c_int, ctypes.c_int,
                ctypes.c_int, wt.HWND, ctypes.c_void_p,
            ]
            u.DestroyMenu.restype = wt.BOOL
            u.DestroyMenu.argtypes = [ctypes.c_void_p]
            u.GetCursorPos.argtypes = [ctypes.POINTER(wt.POINT)]
            u.SetForegroundWindow.restype = wt.BOOL
            u.SetForegroundWindow.argtypes = [wt.HWND]
            u.PostMessageW.restype = wt.BOOL
            u.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
            self._native_api_ready = True
        return u

    def _show_native_menu(self):
        MF_STRING, MF_SEPARATOR, MF_POPUP = 0x0, 0x800, 0x10
        MF_CHECKED, MF_GRAYED = 0x8, 0x1
        TPM_RETURNCMD, TPM_NONOTIFY, TPM_RIGHTBUTTON = 0x100, 0x80, 0x2

        u = self._native_api()
        self._ensure_owner()
        self._native_model_map = {}
        hmenu = u.CreatePopupMenu()
        if not hmenu:
            return

        def item(menu, flags, cid, text):
            u.AppendMenuW(menu, flags, cid, text)

        self._native_copy_map = {}
        if len(self._answers) == 1 and not self._answers[0][0]:
            lines, more = self._answer_lines(self._answers[0][1], width=32, max_lines=8)
            item(hmenu, MF_STRING | MF_GRAYED, 0, "上次回答")
            for ln in lines:
                # 空行用一个空格占位，避免 AppendMenuW 把空串渲染异常
                item(hmenu, MF_STRING | MF_GRAYED, 0, "  " + (ln if ln else " "))
            if more:
                item(hmenu, MF_STRING | MF_GRAYED, 0, "  …（完整内容点下方「复制上次回答」）")
            item(hmenu, MF_STRING, self._CMD["copy_answer"], "复制上次回答")
            item(hmenu, MF_SEPARATOR, 0, None)
        elif self._answers:
            # 多模型：每个模型的回答各做一个子菜单项
            # 复制命令 ID 用 100000+，与模型项 ID（100+）留足间隔，杜绝碰撞
            item(hmenu, MF_STRING | MF_GRAYED, 0, f"上次回答（{len(self._answers)} 个模型）")
            copy_cid = 100000
            for label, text in self._answers:
                ans_sub = u.CreatePopupMenu()
                lines, more = self._answer_lines(text, width=32, max_lines=10)
                for ln in lines:
                    u.AppendMenuW(ans_sub, MF_STRING | MF_GRAYED, 0, "  " + (ln if ln else " "))
                if more:
                    u.AppendMenuW(ans_sub, MF_STRING | MF_GRAYED, 0, "  …（完整内容点「复制该回答」）")
                u.AppendMenuW(ans_sub, MF_SEPARATOR, 0, None)
                self._native_copy_map[copy_cid] = text
                u.AppendMenuW(ans_sub, MF_STRING, copy_cid, "复制该回答")
                copy_cid += 1
                u.AppendMenuW(hmenu, MF_POPUP, ans_sub, label or "模型")
            item(hmenu, MF_SEPARATOR, 0, None)

        item(hmenu, MF_STRING, self._CMD["screenshot_region"], "截图提问")
        item(hmenu, MF_STRING, self._CMD["screenshot_full"], "截全图提问")
        item(hmenu, MF_STRING, self._CMD["clipboard_ask"], "剪贴板提问")
        item(hmenu, MF_SEPARATOR, 0, None)

        submenu = self._build_native_model_submenu(u)
        u.AppendMenuW(hmenu, MF_POPUP, submenu, "切换模型（可多选）")
        item(hmenu, MF_STRING, self._CMD["new_conversation"], "新对话")
        item(hmenu, MF_SEPARATOR, 0, None)

        item(hmenu, MF_STRING, self._CMD["open_chat"], "打开对话窗")
        item(hmenu, MF_STRING, self._CMD["open_settings"], "设置")
        item(hmenu, MF_STRING, self._CMD["exit_app"], "退出")

        pt = ctypes.wintypes.POINT()
        u.GetCursorPos(ctypes.byref(pt))
        hwnd = int(self._owner.winId())
        # 标准做法：弹出前把 owner 设为前台，弹出后补发 WM_NULL，避免菜单点击外部不消失
        u.SetForegroundWindow(hwnd)
        cmd = u.TrackPopupMenu(
            hmenu, TPM_RETURNCMD | TPM_NONOTIFY | TPM_RIGHTBUTTON,
            pt.x, pt.y, 0, hwnd, None,
        )
        u.PostMessageW(hwnd, 0, 0, 0)
        u.DestroyMenu(hmenu)
        self._dispatch_native(int(cmd))

    def _build_native_model_submenu(self, u):
        MF_STRING, MF_CHECKED, MF_GRAYED = 0x0, 0x8, 0x1
        sub = u.CreatePopupMenu()
        cid = 100
        if not self._providers:
            u.AppendMenuW(sub, MF_STRING | MF_GRAYED, 0, "（尚未配置服务商）")
            return sub
        for p in self._providers:
            u.AppendMenuW(sub, MF_STRING | MF_GRAYED, 0, p.get("name") or p.get("id") or "服务商")
            models = p.get("models", [])
            if not models:
                u.AppendMenuW(sub, MF_STRING | MF_GRAYED, 0, "  （无模型）")
                continue
            for mdl in models:
                mid = mdl.get("id", "")
                label = mdl.get("name") or mid
                if mdl.get("vision"):
                    label = f"{label}（视觉）"
                flags = MF_STRING
                if (p.get("id"), mid) in self._selected:
                    flags |= MF_CHECKED
                self._native_model_map[cid] = (p.get("id"), mid)
                u.AppendMenuW(sub, flags, cid, label)
                cid += 1
        return sub

    def _dispatch_native(self, cmd: int):
        if cmd <= 0:
            return
        handlers = {
            self._CMD["screenshot_region"]: self.screenshot_region.emit,
            self._CMD["screenshot_full"]: self.screenshot_full.emit,
            self._CMD["clipboard_ask"]: self.clipboard_ask.emit,
            self._CMD["new_conversation"]: self.new_conversation.emit,
            self._CMD["open_chat"]: self.open_chat.emit,
            self._CMD["open_settings"]: self.open_settings.emit,
            self._CMD["exit_app"]: self.exit_app.emit,
            self._CMD["copy_answer"]: self._copy_answer,
        }
        if cmd in handlers:
            handlers[cmd]()
            return
        if cmd in self._native_copy_map:
            self._copy_text(self._native_copy_map[cmd])
            return
        pick = self._native_model_map.get(cmd)
        if pick:
            self._toggle_model(*pick)

    @staticmethod
    def _answer_lines(text: str, width: int = 32, max_lines: int = 8):
        """把答案按显示宽度折行（中日韩全角算 2 个宽度）。返回 (lines, truncated)。
        用于原生菜单：每行作为一个菜单项显示，从而实现「换行」而非省略号截断。"""
        import unicodedata
        out: list[str] = []
        for para in text.strip().splitlines():
            if not para.strip():
                out.append("")
                continue
            cur, cur_w = "", 0
            for ch in para:
                w = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
                if cur_w + w > width and cur:
                    out.append(cur)
                    cur, cur_w = ch, w
                else:
                    cur += ch
                    cur_w += w
            if cur:
                out.append(cur)
            if len(out) > max_lines:
                break
        truncated = len(out) > max_lines
        return out[:max_lines], truncated
