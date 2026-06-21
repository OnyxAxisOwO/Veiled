import ctypes
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QComboBox, QCheckBox, QSlider, QStackedWidget,
    QTextEdit, QFrame, QScrollArea, QListWidget, QListWidgetItem,
    QFileDialog, QMessageBox,
)
from PyQt6.QtCore import Qt, pyqtSignal, QThread
from PyQt6.QtGui import QFont, QPixmap

from .config import (
    Config, KIND_MAP, POSITION_MAP, DISGUISE_MAP,
    model_guess_vision, new_provider_id, DEFAULT_VISION_PROMPT,
)
from .widgets import HotkeyInput
from .api_client import ApiClient

WDA_EXCLUDEFROMCAPTURE = 0x00000011

# 协议下拉框：展示名 → kind
_KINDS = [("OpenAI 兼容", "openai"), ("Claude (Anthropic)", "claude")]


class ModelFetchWorker(QThread):
    done = pyqtSignal(list, str)

    def __init__(self, client: ApiClient):
        super().__init__()
        self._client = client

    def run(self):
        models, error = self._client.list_models()
        self.done.emit(models, error)


class SettingsPanel(QWidget):
    settings_changed = pyqtSignal()
    closed = pyqtSignal()

    def __init__(self, config: Config):
        super().__init__(None)
        self._config = config
        self.setWindowTitle("Display Adapter Configuration")
        self.setFixedSize(800, 600)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._drag_pos = None

        # 服务商工作副本（保存时才写回 config）
        self._providers: list[dict] = []
        self._cur_prov_index = -1
        self._cur_model_index = -1
        self._active_pid = ""
        self._active_mid = ""
        self._loading = False

        self._setup_ui()
        self._load_values()
        self._apply_style()

    def showEvent(self, event):
        super().showEvent(event)
        hwnd = int(self.winId())
        ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)
        self._load_values()
        self._apply_style()

    def closeEvent(self, event):
        event.ignore()
        self._on_close()

    # ── 整体布局：侧边栏 + 堆叠页 ─────────────────────────────────────────────

    def _setup_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        container = QFrame()
        container.setObjectName("settings_container")
        root = QVBoxLayout(container)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 标题栏
        title_bar = QFrame()
        title_bar.setObjectName("settings_titlebar")
        title_bar.setFixedHeight(46)
        tb = QHBoxLayout(title_bar)
        tb.setContentsMargins(18, 0, 8, 0)
        title = QLabel("设置")
        title.setFont(QFont("Microsoft YaHei", 13, QFont.Weight.Bold))
        title.setObjectName("settings_title")
        tb.addWidget(title)
        tb.addStretch()
        close_btn = QPushButton("×")
        close_btn.setObjectName("close_btn")
        close_btn.setFixedSize(30, 30)
        close_btn.clicked.connect(self._on_close)
        tb.addWidget(close_btn)
        root.addWidget(title_bar)

        # 主体：侧边栏 | 内容
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        self._nav = QListWidget()
        self._nav.setObjectName("nav_list")
        self._nav.setFixedWidth(168)
        for label in ["服务商与模型", "视觉识别", "快捷键", "外观", "隐私", "行为", "关于"]:
            QListWidgetItem(label, self._nav)
        self._nav.currentRowChanged.connect(self._on_nav_changed)
        body.addWidget(self._nav)

        self._pages = QStackedWidget()
        self._pages.setObjectName("pages")
        self._pages.setAutoFillBackground(False)
        self._pages.addWidget(self._create_providers_page())
        self._pages.addWidget(self._create_vision_page())
        self._pages.addWidget(self._create_hotkey_page())
        self._pages.addWidget(self._create_appearance_page())
        self._pages.addWidget(self._create_privacy_page())
        self._pages.addWidget(self._create_behavior_page())
        self._pages.addWidget(self._create_about_page())
        body.addWidget(self._pages, 1)
        root.addLayout(body, 1)

        # 底部按钮条
        footer = QFrame()
        footer.setObjectName("settings_footer")
        footer.setFixedHeight(56)
        fb = QHBoxLayout(footer)
        fb.setContentsMargins(16, 0, 16, 0)
        export_btn = QPushButton("导出配置")
        export_btn.setObjectName("io_btn")
        export_btn.clicked.connect(self._export_config)
        fb.addWidget(export_btn)
        import_btn = QPushButton("导入配置")
        import_btn.setObjectName("io_btn")
        import_btn.clicked.connect(self._import_config)
        fb.addWidget(import_btn)
        fb.addStretch()
        save_btn = QPushButton("保存")
        save_btn.setObjectName("save_btn")
        save_btn.clicked.connect(self._save)
        fb.addWidget(save_btn)
        root.addWidget(footer)

        outer.addWidget(container)
        self._nav.setCurrentRow(0)

    def _scrollable(self, inner: QWidget) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(inner)
        scroll.viewport().setAutoFillBackground(False)
        return scroll

    def _on_nav_changed(self, row: int):
        self._pages.setCurrentIndex(row)
        if row == 1:  # 进入视觉识别页时，用最新的服务商列表刷新下拉框
            self._commit_model_form()
            self._commit_provider_form()
            self._refresh_relay_provider_combo()

    # ── 页 1：服务商与模型 ───────────────────────────────────────────────────

    def _create_providers_page(self) -> QWidget:
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        hint = QLabel("可创建任意多个服务商，每个服务商有独立的接口地址、密钥与模型列表。")
        hint.setObjectName("page_hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        cols = QHBoxLayout()
        cols.setSpacing(14)

        # 左：服务商列表
        left = QVBoxLayout()
        left.setSpacing(6)
        left.addWidget(QLabel("服务商"))
        self._prov_list = QListWidget()
        self._prov_list.setObjectName("sub_list")
        self._prov_list.setFixedWidth(190)
        self._prov_list.currentRowChanged.connect(self._on_provider_selected)
        left.addWidget(self._prov_list, 1)
        prov_btns = QHBoxLayout()
        add_p = QPushButton("＋ 新增")
        add_p.setObjectName("mini_btn")
        add_p.clicked.connect(self._add_provider)
        del_p = QPushButton("－ 删除")
        del_p.setObjectName("mini_btn")
        del_p.clicked.connect(self._delete_provider)
        prov_btns.addWidget(add_p)
        prov_btns.addWidget(del_p)
        left.addLayout(prov_btns)
        cols.addLayout(left)

        # 右：服务商详情 + 模型
        right = QVBoxLayout()
        right.setSpacing(7)

        row_name = QHBoxLayout()
        row_name.addWidget(QLabel("名称:"))
        self._p_name = QLineEdit()
        self._p_name.editingFinished.connect(self._on_name_edited)
        row_name.addWidget(self._p_name, 1)
        row_name.addWidget(QLabel("协议:"))
        self._p_kind = QComboBox()
        for disp, _k in _KINDS:
            self._p_kind.addItem(disp)
        self._p_kind.currentIndexChanged.connect(self._on_kind_changed)
        row_name.addWidget(self._p_kind)
        right.addLayout(row_name)

        right.addWidget(QLabel("Endpoint:"))
        self._p_endpoint = QLineEdit()
        self._p_endpoint.setPlaceholderText("https://api.openai.com")
        right.addWidget(self._p_endpoint)

        key_label = QHBoxLayout()
        key_label.addWidget(QLabel("API Key:"))
        key_label.addStretch()
        self._p_show_key = QCheckBox("显示")
        self._p_show_key.toggled.connect(
            lambda c: self._p_key.setEchoMode(
                QLineEdit.EchoMode.Normal if c else QLineEdit.EchoMode.Password)
        )
        key_label.addWidget(self._p_show_key)
        right.addLayout(key_label)
        self._p_key = QLineEdit()
        self._p_key.setEchoMode(QLineEdit.EchoMode.Password)
        right.addWidget(self._p_key)

        extra_row = QHBoxLayout()
        extra_row.addWidget(QLabel("额外请求参数 (JSON，合并进请求体):"))
        extra_row.addStretch()
        tmpl_btn = QPushButton("填入模板")
        tmpl_btn.setObjectName("mini_btn")
        tmpl_btn.clicked.connect(lambda: self._p_extra.setText('{"enable_thinking": false}'))
        extra_row.addWidget(tmpl_btn)
        right.addLayout(extra_row)
        self._p_extra = QTextEdit()
        self._p_extra.setMaximumHeight(48)
        self._p_extra.setPlaceholderText('例如关闭深度思考: {"enable_thinking": false}')
        right.addWidget(self._p_extra)

        # 模型区
        m_header = QHBoxLayout()
        m_label = QLabel("模型")
        m_label.setObjectName("section_label")
        m_header.addWidget(m_label)
        m_header.addStretch()
        self._fetch_status = QLabel("")
        self._fetch_status.setObjectName("fetch_status")
        m_header.addWidget(self._fetch_status)
        right.addLayout(m_header)

        self._model_list = QListWidget()
        self._model_list.setObjectName("sub_list")
        self._model_list.setFixedHeight(116)
        self._model_list.currentRowChanged.connect(self._on_model_selected)
        right.addWidget(self._model_list)

        m_btns = QHBoxLayout()
        self._fetch_btn = QPushButton("获取列表")
        self._fetch_btn.setObjectName("mini_btn")
        self._fetch_btn.clicked.connect(self._fetch_models)
        add_m = QPushButton("＋ 模型")
        add_m.setObjectName("mini_btn")
        add_m.clicked.connect(self._add_model)
        del_m = QPushButton("－ 删除")
        del_m.setObjectName("mini_btn")
        del_m.clicked.connect(self._delete_model)
        m_btns.addWidget(self._fetch_btn)
        m_btns.addWidget(add_m)
        m_btns.addWidget(del_m)
        m_btns.addStretch()
        right.addLayout(m_btns)

        # 模型编辑器
        editor = QHBoxLayout()
        editor.addWidget(QLabel("模型 ID:"))
        self._m_id = QLineEdit()
        self._m_id.setPlaceholderText("gpt-4o")
        editor.addWidget(self._m_id, 2)
        editor.addWidget(QLabel("显示名:"))
        self._m_name = QLineEdit()
        self._m_name.setPlaceholderText("可留空")
        editor.addWidget(self._m_name, 2)
        self._m_vision = QCheckBox("视觉 👁")
        editor.addWidget(self._m_vision)
        right.addLayout(editor)

        ed_btns = QHBoxLayout()
        apply_m = QPushButton("应用修改")
        apply_m.setObjectName("mini_btn")
        apply_m.clicked.connect(self._apply_model_edit)
        set_default = QPushButton("设为默认模型")
        set_default.setObjectName("mini_btn")
        set_default.clicked.connect(self._set_default_model)
        ed_btns.addWidget(apply_m)
        ed_btns.addWidget(set_default)
        ed_btns.addStretch()
        right.addLayout(ed_btns)

        right.addWidget(self._hline())

        proxy_row = QHBoxLayout()
        proxy_row.addWidget(QLabel("全局代理:"))
        self._p_proxy = QLineEdit()
        self._p_proxy.setPlaceholderText("http://127.0.0.1:7890（所有服务商共用，可留空）")
        proxy_row.addWidget(self._p_proxy, 1)
        right.addLayout(proxy_row)

        cols.addLayout(right, 1)
        layout.addLayout(cols, 1)
        return self._scrollable(inner)

    def _hline(self) -> QFrame:
        line = QFrame()
        line.setObjectName("hline")
        line.setFrameShape(QFrame.Shape.HLine)
        return line

    # ── 服务商 / 模型 工作副本逻辑 ────────────────────────────────────────────

    def _prov_label(self, p: dict) -> str:
        kind_disp = KIND_MAP.get(p.get("kind", "openai"), "OpenAI 兼容")
        return f"{p.get('name') or p.get('id')}  ·  {kind_disp}"

    def _model_label(self, m: dict, active: bool) -> str:
        name = m.get("name") or m.get("id") or "(未命名)"
        vis = "👁 " if m.get("vision") else ""
        star = "● " if active else ""
        mid = m.get("id", "")
        suffix = f"  ({mid})" if (m.get("name") and mid and m.get("name") != mid) else ""
        return f"{star}{vis}{name}{suffix}"

    def _reload_provider_list(self, select: int = 0):
        self._loading = True
        self._prov_list.clear()
        for p in self._providers:
            QListWidgetItem(self._prov_label(p), self._prov_list)
        self._loading = False
        if self._providers:
            self._prov_list.setCurrentRow(max(0, min(select, len(self._providers) - 1)))
        else:
            self._cur_prov_index = -1
            self._load_provider_form(None)
            self._reload_model_list()

    def _on_provider_selected(self, row: int):
        if self._loading:
            return
        # 先提交旧选择
        self._commit_model_form()
        self._commit_provider_form()
        self._cur_prov_index = row
        self._cur_model_index = -1
        prov = self._providers[row] if 0 <= row < len(self._providers) else None
        self._load_provider_form(prov)
        self._reload_model_list()
        self._fetch_status.setText("")

    def _load_provider_form(self, prov: dict | None):
        self._loading = True
        if prov is None:
            self._p_name.setText("")
            self._p_kind.setCurrentIndex(0)
            self._p_endpoint.setText("")
            self._p_key.setText("")
            self._p_extra.setText("")
            self._set_form_enabled(False)
        else:
            self._set_form_enabled(True)
            self._p_name.setText(prov.get("name", ""))
            kind = prov.get("kind", "openai")
            self._p_kind.setCurrentIndex(1 if kind == "claude" else 0)
            self._p_endpoint.setText(prov.get("endpoint", ""))
            self._p_key.setText(prov.get("api_key", ""))
            self._p_extra.setText(prov.get("extra_body", ""))
        self._loading = False

    def _set_form_enabled(self, on: bool):
        for w in (self._p_name, self._p_kind, self._p_endpoint, self._p_key,
                  self._p_extra, self._model_list, self._m_id, self._m_name,
                  self._m_vision, self._fetch_btn):
            w.setEnabled(on)

    def _commit_provider_form(self):
        if not (0 <= self._cur_prov_index < len(self._providers)):
            return
        p = self._providers[self._cur_prov_index]
        p["name"] = self._p_name.text().strip() or p.get("id", "服务商")
        p["kind"] = _KINDS[self._p_kind.currentIndex()][1]
        p["endpoint"] = self._p_endpoint.text().strip()
        p["api_key"] = self._p_key.text().strip()
        p["extra_body"] = self._p_extra.toPlainText().strip()

    def _on_name_edited(self):
        self._refresh_cur_prov_label()

    def _on_kind_changed(self, _idx: int):
        self._refresh_cur_prov_label()

    def _refresh_cur_prov_label(self):
        if self._loading or not (0 <= self._cur_prov_index < len(self._providers)):
            return
        self._commit_provider_form()
        self._loading = True
        item = self._prov_list.item(self._cur_prov_index)
        if item:
            item.setText(self._prov_label(self._providers[self._cur_prov_index]))
        self._loading = False

    def _add_provider(self):
        self._commit_model_form()
        self._commit_provider_form()
        new = {
            "id": new_provider_id(),
            "name": f"新服务商 {len(self._providers) + 1}",
            "kind": "openai",
            "endpoint": "",
            "api_key": "",
            "extra_body": "",
            "models": [],
        }
        self._providers.append(new)
        self._reload_provider_list(select=len(self._providers) - 1)

    def _delete_provider(self):
        if not (0 <= self._cur_prov_index < len(self._providers)):
            return
        p = self._providers[self._cur_prov_index]
        if QMessageBox.question(
            self, "删除服务商", f"确定删除「{p.get('name')}」及其所有模型？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        idx = self._cur_prov_index
        del self._providers[idx]
        self._cur_prov_index = -1
        self._finalize_active()   # 删掉的可能是默认服务商，立即重定位默认指针
        self._reload_provider_list(select=max(0, idx - 1))

    # ── 模型逻辑 ──────────────────────────────────────────────────────────────

    def _cur_models(self) -> list:
        if 0 <= self._cur_prov_index < len(self._providers):
            return self._providers[self._cur_prov_index].setdefault("models", [])
        return []

    def _reload_model_list(self, select: int = -1):
        self._loading = True
        self._model_list.clear()
        models = self._cur_models()
        pid = self._providers[self._cur_prov_index]["id"] if 0 <= self._cur_prov_index < len(self._providers) else ""
        for m in models:
            active = (pid == self._active_pid and m.get("id") == self._active_mid)
            QListWidgetItem(self._model_label(m, active), self._model_list)
        self._loading = False
        if models and select >= 0:
            self._model_list.setCurrentRow(min(select, len(models) - 1))
        else:
            self._cur_model_index = -1
            self._load_model_form(None)

    def _on_model_selected(self, row: int):
        if self._loading:
            return
        self._commit_model_form()
        self._cur_model_index = row
        models = self._cur_models()
        m = models[row] if 0 <= row < len(models) else None
        self._load_model_form(m)

    def _load_model_form(self, m: dict | None):
        self._loading = True
        if m is None:
            self._m_id.setText("")
            self._m_name.setText("")
            self._m_vision.setChecked(False)
        else:
            self._m_id.setText(m.get("id", ""))
            self._m_name.setText(m.get("name", ""))
            self._m_vision.setChecked(bool(m.get("vision")))
        self._loading = False

    def _commit_model_form(self):
        models = self._cur_models()
        if not (0 <= self._cur_model_index < len(models)):
            return
        m = models[self._cur_model_index]
        old_id = m.get("id", "")
        m["id"] = self._m_id.text().strip()
        m["name"] = self._m_name.text().strip()
        m["vision"] = self._m_vision.isChecked()
        # 若改的是默认模型的 id，同步默认指针
        prov = self._providers[self._cur_prov_index]
        if prov["id"] == self._active_pid and old_id == self._active_mid:
            self._active_mid = m["id"]

    def _apply_model_edit(self):
        if not (0 <= self._cur_model_index < len(self._cur_models())):
            return
        self._commit_model_form()
        self._reload_model_list(select=self._cur_model_index)

    def _add_model(self):
        if not (0 <= self._cur_prov_index < len(self._providers)):
            return
        self._commit_model_form()
        self._cur_models().append({"id": "", "name": "", "vision": False})
        self._reload_model_list(select=len(self._cur_models()) - 1)
        self._m_id.setFocus()

    def _delete_model(self):
        models = self._cur_models()
        if not (0 <= self._cur_model_index < len(models)):
            return
        idx = self._cur_model_index
        deleted = models[idx]
        prov = self._providers[self._cur_prov_index]
        del models[idx]
        self._cur_model_index = -1
        # 若删的是当前默认模型，立即重定位默认指针，使 ● 标记不丢失
        if prov.get("id") == self._active_pid and deleted.get("id") == self._active_mid:
            self._finalize_active()
        self._reload_model_list(select=max(0, idx - 1) if models else -1)

    def _set_default_model(self):
        models = self._cur_models()
        if not (0 <= self._cur_model_index < len(models)):
            return
        self._commit_model_form()
        self._active_pid = self._providers[self._cur_prov_index]["id"]
        self._active_mid = models[self._cur_model_index].get("id", "")
        self._reload_model_list(select=self._cur_model_index)

    def _fetch_models(self):
        if not (0 <= self._cur_prov_index < len(self._providers)):
            return
        self._commit_provider_form()
        p = self._providers[self._cur_prov_index]
        self._fetch_btn.setEnabled(False)
        self._fetch_status.setText("获取中…")
        self._fetch_status.setStyleSheet("color: #aaa;")
        client = ApiClient(
            kind=p.get("kind", "openai"),
            api_key=p.get("api_key", ""),
            model="",
            endpoint=p.get("endpoint", ""),
            proxy=self._p_proxy.text().strip(),
        )
        self._model_worker = ModelFetchWorker(client)
        self._model_worker.done.connect(self._on_models_fetched)
        self._model_worker.start()

    def _on_models_fetched(self, model_ids: list, error: str):
        self._fetch_btn.setEnabled(True)
        if error:
            self._fetch_status.setText(f"✗ {error[:60]}")
            self._fetch_status.setStyleSheet("color: #f06292;")
            return
        models = self._cur_models()
        existing = {m.get("id") for m in models}
        added = 0
        for mid in model_ids:
            if mid and mid not in existing:
                models.append({"id": mid, "name": "", "vision": model_guess_vision(mid)})
                existing.add(mid)
                added += 1
        self._fetch_status.setText(f"✓ 共 {len(model_ids)} 个，新增 {added}")
        self._fetch_status.setStyleSheet("color: #81c784;")
        self._reload_model_list(select=self._cur_model_index if self._cur_model_index >= 0 else -1)

    # ── 页 2：视觉识别（中继）────────────────────────────────────────────────

    def _create_vision_page(self) -> QWidget:
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(9)

        tip = QLabel(
            "直接在对话顶部选择带 👁 的视觉模型，即可发送截图 / 图片。\n"
            "下方「视觉识别中继」是可选的兜底：当前模型不支持图片时，先用指定的视觉模型"
            "把截图转成文字，再交给当前模型回答。"
        )
        tip.setObjectName("page_hint")
        tip.setWordWrap(True)
        layout.addWidget(tip)

        self._v_enabled = QCheckBox("启用视觉识别中继")
        layout.addWidget(self._v_enabled)

        layout.addWidget(QLabel("视觉模型 — 服务商:"))
        self._v_provider = QComboBox()
        self._v_provider.currentIndexChanged.connect(self._on_relay_provider_changed)
        layout.addWidget(self._v_provider)

        layout.addWidget(QLabel("视觉模型 — 模型:"))
        self._v_model = QComboBox()
        layout.addWidget(self._v_model)

        layout.addWidget(QLabel("识别提示词（告诉视觉模型如何转写图片）:"))
        self._v_prompt = QTextEdit()
        self._v_prompt.setMaximumHeight(120)
        layout.addWidget(self._v_prompt)

        layout.addStretch()
        return self._scrollable(inner)

    def _refresh_relay_provider_combo(self):
        cur_pid = self._v_provider.currentData()
        self._loading = True
        self._v_provider.clear()
        for p in self._providers:
            self._v_provider.addItem(p.get("name") or p.get("id"), p.get("id"))
        self._loading = False
        # 还原之前选中的服务商
        idx = 0
        for i in range(self._v_provider.count()):
            if self._v_provider.itemData(i) == cur_pid:
                idx = i
                break
        if self._v_provider.count():
            self._v_provider.setCurrentIndex(idx)
        self._on_relay_provider_changed()

    def _on_relay_provider_changed(self, *_):
        if self._loading:
            return
        cur_mid = self._v_model.currentData()
        pid = self._v_provider.currentData()
        prov = next((p for p in self._providers if p.get("id") == pid), None)
        self._v_model.clear()
        if prov:
            for m in prov.get("models", []):
                label = m.get("name") or m.get("id")
                if m.get("vision"):
                    label = f"👁 {label}"
                self._v_model.addItem(label, m.get("id"))
        for i in range(self._v_model.count()):
            if self._v_model.itemData(i) == cur_mid:
                self._v_model.setCurrentIndex(i)
                break

    # ── 页 3：快捷键 ──────────────────────────────────────────────────────────

    def _create_hotkey_page(self) -> QWidget:
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

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
            lab = QLabel(label_text)
            lab.setFixedWidth(150)
            row.addWidget(lab)
            hk = HotkeyInput()
            self._s_hotkeys[key_name] = hk
            row.addWidget(hk, 1)
            layout.addLayout(row)
        layout.addStretch()
        return self._scrollable(inner)

    # ── 页 4：外观 ────────────────────────────────────────────────────────────

    def _create_appearance_page(self) -> QWidget:
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(9)

        layout.addWidget(QLabel("托盘菜单样式:"))
        self._s_menu_style = QComboBox()
        self._s_menu_style.addItems(["原生 Windows 菜单（更隐蔽）", "样式菜单（深色 / 带输入框）"])
        layout.addWidget(self._s_menu_style)
        hint = QLabel("原生菜单与系统服务一致、最不显眼，但无法内嵌输入框；\n提问用「截图 / 截全图 / 剪贴板提问」。样式菜单可直接打字提问。")
        hint.setObjectName("hint_label")
        hint.setWordWrap(True)
        layout.addWidget(hint)

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

        layout.addWidget(self._hline())

        layout.addWidget(QLabel("自定义背景图片:"))
        bg_row = QHBoxLayout()
        self._s_bg_path = QLineEdit()
        self._s_bg_path.setPlaceholderText("选择图片文件…")
        self._s_bg_path.setReadOnly(True)
        browse_bg = QPushButton("浏览")
        browse_bg.setObjectName("mini_btn")
        browse_bg.clicked.connect(self._browse_bg_image)
        clear_bg = QPushButton("清除")
        clear_bg.setObjectName("mini_btn")
        clear_bg.clicked.connect(lambda: self._s_bg_path.clear())
        bg_row.addWidget(self._s_bg_path, 1)
        bg_row.addWidget(browse_bg)
        bg_row.addWidget(clear_bg)
        layout.addLayout(bg_row)

        layout.addWidget(QLabel("背景填充方式:"))
        self._s_bg_mode = QComboBox()
        self._s_bg_mode.addItems(["填充", "适应", "拉伸", "平铺", "居中"])
        layout.addWidget(self._s_bg_mode)

        layout.addStretch()
        return self._scrollable(inner)

    # ── 页 5：隐私 ────────────────────────────────────────────────────────────

    def _create_privacy_page(self) -> QWidget:
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(9)

        self._s_screenshot_protect = QCheckBox("截屏保护（窗口对截屏/录屏不可见）")
        layout.addWidget(self._s_screenshot_protect)

        row = QHBoxLayout()
        row.addWidget(QLabel("记录保留天数 (0=永久):"))
        self._s_retention = QLineEdit()
        self._s_retention.setFixedWidth(70)
        row.addWidget(self._s_retention)
        row.addStretch()
        layout.addLayout(row)

        self._s_save_screenshots = QCheckBox("保留截图历史")
        layout.addWidget(self._s_save_screenshots)

        self._s_clear_on_exit = QCheckBox("退出时清除所有数据")
        layout.addWidget(self._s_clear_on_exit)

        layout.addStretch()
        return self._scrollable(inner)

    # ── 页 6：行为 ────────────────────────────────────────────────────────────

    def _create_behavior_page(self) -> QWidget:
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(8)

        self._s_autostart = QCheckBox("开机自启动")
        layout.addWidget(self._s_autostart)

        self._s_close_on_focus = QCheckBox("点击窗口外部自动关闭对话窗")
        layout.addWidget(self._s_close_on_focus)

        self._s_monitor_enabled = QCheckBox("启用环境检测（检测到监控软件时屏蔽快捷键）")
        layout.addWidget(self._s_monitor_enabled)

        self._s_notify_on_silent = QCheckBox("检测到监控软件时发送通知")
        layout.addWidget(self._s_notify_on_silent)

        layout.addWidget(QLabel("监控进程列表（每行一个）:"))
        self._s_processes = QTextEdit()
        self._s_processes.setMaximumHeight(80)
        layout.addWidget(self._s_processes)

        self._s_detected_info = QLabel("🟢 未检测到监控软件")
        self._s_detected_info.setWordWrap(True)
        layout.addWidget(self._s_detected_info)

        def _on_monitor_toggled(enabled: bool):
            self._s_notify_on_silent.setEnabled(enabled)
            self._s_processes.setEnabled(enabled)
            self._s_detected_info.setVisible(enabled)

        self._s_monitor_enabled.toggled.connect(_on_monitor_toggled)

        layout.addWidget(QLabel("普通对话 System Prompt:"))
        self._s_prompt_chat = QTextEdit()
        self._s_prompt_chat.setMaximumHeight(56)
        layout.addWidget(self._s_prompt_chat)

        layout.addWidget(QLabel("截图场景 System Prompt:"))
        self._s_prompt_screenshot = QTextEdit()
        self._s_prompt_screenshot.setMaximumHeight(56)
        layout.addWidget(self._s_prompt_screenshot)

        layout.addWidget(QLabel("截图附带的用户消息:"))
        self._s_screenshot_message = QLineEdit()
        self._s_screenshot_message.setPlaceholderText("请分析这张截图")
        layout.addWidget(self._s_screenshot_message)

        layout.addStretch()
        return self._scrollable(inner)

    # ── 页 7：关于 ────────────────────────────────────────────────────────────

    def _create_about_page(self) -> QWidget:
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.addStretch()
        layout.addWidget(QLabel("Windows Display Adapter Helper"))
        layout.addWidget(QLabel("版本 1.8.4"))
        layout.addStretch()
        return inner

    # ── 读取 / 保存 ───────────────────────────────────────────────────────────

    def _load_values(self):
        import copy
        c = self._config
        # 服务商工作副本
        self._providers = copy.deepcopy(c.providers())
        self._active_pid = c.get("api.active.provider", "")
        self._active_mid = c.get("api.active.model", "")
        self._cur_prov_index = -1
        self._cur_model_index = -1
        self._reload_provider_list(select=self._active_index())
        self._p_proxy.setText(c.get("api.proxy", ""))

        # 视觉中继
        self._v_enabled.setChecked(c.get("api.vision_relay.enabled", False))
        self._v_prompt.setText(c.get("api.vision_relay.prompt", DEFAULT_VISION_PROMPT))
        self._refresh_relay_provider_combo()
        self._select_relay(c.get("api.vision_relay.provider", ""), c.get("api.vision_relay.model", ""))

        for name, widget in self._s_hotkeys.items():
            val = c.get(f"hotkeys.{name}", "")
            widget.setText(val)
            widget._keys = val

        self._s_menu_style.setCurrentIndex(0 if c.get("display.menu_style", "native") == "native" else 1)
        self._s_position.setCurrentText(POSITION_MAP.get(c.get("display.chat_position", "bottom_right"), "右下角"))
        opacity_val = int(c.get("display.chat_opacity", 0.9) * 100)
        self._s_opacity.setValue(opacity_val)
        self._s_opacity_label.setText(f"{opacity_val}%")
        self._s_disguise.setCurrentText(DISGUISE_MAP.get(c.get("display.notification_disguise", "none"), "无伪装"))
        self._s_ss_toast.setChecked(c.get("display.screenshot_success_toast", True))
        self._s_ss_text.setText(c.get("display.screenshot_success_text", "成功"))
        self._s_theme.setCurrentText("深色" if c.get("display.theme", "dark") == "dark" else "浅色")
        self._s_bg_path.setText(c.get("display.bg_image_path", ""))
        _bg_mode_map = {"fill": "填充", "fit": "适应", "stretch": "拉伸", "tile": "平铺", "center": "居中"}
        self._s_bg_mode.setCurrentText(_bg_mode_map.get(c.get("display.bg_fill_mode", "fill"), "填充"))

        self._s_screenshot_protect.setChecked(c.get("display.screenshot_protection", True))
        self._s_retention.setText(str(c.get("privacy.history_retention_days", 0)))
        self._s_save_screenshots.setChecked(c.get("privacy.save_screenshots", False))
        self._s_clear_on_exit.setChecked(c.get("privacy.clear_on_exit", False))

        self._s_autostart.setChecked(c.get("display.auto_start", True))
        self._s_close_on_focus.setChecked(c.get("display.close_on_focus_lost", False))
        monitor_on = c.get("environment.monitor_enabled", True)
        self._s_monitor_enabled.setChecked(monitor_on)
        self._s_notify_on_silent.setChecked(c.get("environment.notify_on_silent", True))
        self._s_notify_on_silent.setEnabled(monitor_on)
        self._s_processes.setEnabled(monitor_on)
        self._s_processes.setText("\n".join(c.get("environment.suspicious_processes", [])))
        self._s_detected_info.setVisible(monitor_on)
        self._s_prompt_chat.setText(c.get("prompts.chat", ""))
        self._s_prompt_screenshot.setText(c.get("prompts.screenshot", ""))
        self._s_screenshot_message.setText(c.get("prompts.screenshot_message", "请分析这张截图"))

    def _active_index(self) -> int:
        for i, p in enumerate(self._providers):
            if p.get("id") == self._active_pid:
                return i
        return 0

    def _select_relay(self, pid: str, mid: str):
        for i in range(self._v_provider.count()):
            if self._v_provider.itemData(i) == pid:
                self._v_provider.setCurrentIndex(i)
                break
        self._on_relay_provider_changed()
        for i in range(self._v_model.count()):
            if self._v_model.itemData(i) == mid:
                self._v_model.setCurrentIndex(i)
                break

    def _finalize_active(self):
        """保证默认模型指向真实存在的服务商/模型。"""
        prov = next((p for p in self._providers if p.get("id") == self._active_pid), None)
        if prov is None:
            prov = self._providers[0] if self._providers else None
            self._active_pid = prov["id"] if prov else ""
        if prov:
            mids = [m.get("id") for m in prov.get("models", []) if m.get("id")]
            if self._active_mid not in mids:
                self._active_mid = mids[0] if mids else ""

    def _save(self):
        c = self._config
        self._commit_model_form()
        self._commit_provider_form()

        # 清理空模型 / 空服务商内的空模型 id
        for p in self._providers:
            p["models"] = [m for m in p.get("models", []) if m.get("id")]
        self._finalize_active()

        c.set("api.providers", self._providers)
        c.set("api.active.provider", self._active_pid)
        c.set("api.active.model", self._active_mid)
        c.set("api.proxy", self._p_proxy.text().strip())

        # 视觉中继：校验用户选中的目标是否仍存在于编辑后的服务商工作副本里。
        # 若其服务商/模型已被删除或改名，则停用并清空，绝不静默改指向其它服务商。
        rpid = self._v_provider.currentData() or ""
        rmid = self._v_model.currentData() or ""
        rprov = next((p for p in self._providers if p.get("id") == rpid), None)
        if rprov is None:
            rpid, rmid = "", ""
        else:
            rmids = [m.get("id") for m in rprov.get("models", []) if m.get("id")]
            if rmid not in rmids:
                rmid = ""
        relay_ok = bool(rpid and rmid)
        c.set("api.vision_relay.enabled", self._v_enabled.isChecked() and relay_ok)
        c.set("api.vision_relay.provider", rpid)
        c.set("api.vision_relay.model", rmid)
        c.set("api.vision_relay.prompt", self._v_prompt.toPlainText())

        for name, widget in self._s_hotkeys.items():
            if widget.hotkey:
                c.set(f"hotkeys.{name}", widget.hotkey)

        inv_position = {v: k for k, v in POSITION_MAP.items()}
        inv_disguise = {v: k for k, v in DISGUISE_MAP.items()}
        c.set("display.menu_style", "native" if self._s_menu_style.currentIndex() == 0 else "styled")
        c.set("display.chat_position", inv_position.get(self._s_position.currentText(), "bottom_right"))
        c.set("display.chat_opacity", self._s_opacity.value() / 100.0)
        c.set("display.notification_disguise", inv_disguise.get(self._s_disguise.currentText(), "none"))
        c.set("display.screenshot_success_toast", self._s_ss_toast.isChecked())
        c.set("display.screenshot_success_text", self._s_ss_text.text().strip() or "成功")
        c.set("display.theme", "dark" if self._s_theme.currentText() == "深色" else "light")
        c.set("display.bg_image_path", self._s_bg_path.text().strip())
        _bg_mode_inv = {"填充": "fill", "适应": "fit", "拉伸": "stretch", "平铺": "tile", "居中": "center"}
        c.set("display.bg_fill_mode", _bg_mode_inv.get(self._s_bg_mode.currentText(), "fill"))

        c.set("display.screenshot_protection", self._s_screenshot_protect.isChecked())
        try:
            c.set("privacy.history_retention_days", int(self._s_retention.text()))
        except ValueError:
            pass
        c.set("privacy.save_screenshots", self._s_save_screenshots.isChecked())
        c.set("privacy.clear_on_exit", self._s_clear_on_exit.isChecked())

        c.set("display.auto_start", self._s_autostart.isChecked())
        c.set("display.close_on_focus_lost", self._s_close_on_focus.isChecked())
        c.set("environment.monitor_enabled", self._s_monitor_enabled.isChecked())
        c.set("environment.notify_on_silent", self._s_notify_on_silent.isChecked())
        procs = [p.strip() for p in self._s_processes.toPlainText().split("\n") if p.strip()]
        c.set("environment.suspicious_processes", procs)
        c.set("prompts.chat", self._s_prompt_chat.toPlainText())
        c.set("prompts.screenshot", self._s_prompt_screenshot.toPlainText())
        c.set("prompts.screenshot_message", self._s_screenshot_message.text().strip() or "请分析这张截图")

        c.save()
        self.settings_changed.emit()
        self._on_close()

    def _export_config(self):
        self._set_topmost(False)
        try:
            path, _ = QFileDialog.getSaveFileName(
                self, "导出配置", "adapter_config.json", "JSON 文件 (*.json)"
            )
        finally:
            self._set_topmost(True)
        if not path:
            return
        try:
            self._config.export_json(path)
        except Exception as e:
            QMessageBox.warning(self, "导出失败", str(e))
            return
        QMessageBox.information(
            self, "导出成功",
            f"已导出到：\n{path}\n\n"
            "注意：文件为明文，包含 API Key，请妥善保管。\n"
            "导出的是已保存的配置；界面上未点「保存」的改动不会包含在内。"
        )

    def _import_config(self):
        self._set_topmost(False)
        try:
            path, _ = QFileDialog.getOpenFileName(self, "导入配置", "", "JSON 文件 (*.json)")
        finally:
            self._set_topmost(True)
        if not path:
            return
        if QMessageBox.question(
            self, "确认导入",
            "导入将覆盖当前全部配置并立即保存，确定继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            self._config.import_json(path)
        except Exception as e:
            QMessageBox.warning(self, "导入失败", f"无法解析配置文件：\n{e}")
            return
        self._load_values()
        self._apply_style()
        self.settings_changed.emit()
        QMessageBox.information(
            self, "导入成功",
            "配置已导入并保存。\n热键等部分设置可能需要重启应用后生效。"
        )

    def _set_topmost(self, on: bool):
        """临时取消/恢复窗口置顶。本面板是无边框 + 置顶 + Tool 窗口，原生文件对话框
        会被它压在下面，看起来像「点了没反应」，开对话框前先放下置顶即可。"""
        try:
            hwnd = int(self.winId())
            HWND_TOPMOST, HWND_NOTOPMOST = -1, -2
            SWP_NOMOVE, SWP_NOSIZE, SWP_NOACTIVATE = 0x0002, 0x0001, 0x0010
            ctypes.windll.user32.SetWindowPos(
                hwnd, HWND_TOPMOST if on else HWND_NOTOPMOST,
                0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
        except Exception:
            pass

    def update_detected_processes(self, processes: list[str]):
        """Called by app when the env monitor detects or clears suspicious processes."""
        if not processes:
            self._s_detected_info.setText("🟢 未检测到监控软件")
            self._s_detected_info.setStyleSheet("")
        else:
            names = "、".join(processes)
            self._s_detected_info.setText(f"🔴 当前检测到：{names}")
            self._s_detected_info.setStyleSheet("color: #e05c5c; font-weight: bold;")

    def _on_disguise_changed(self, text: str):
        visible = text == "自定义"
        self._s_disguise_custom_label.setVisible(visible)
        self._s_disguise_custom.setVisible(visible)

    def _browse_bg_image(self):
        self._set_topmost(False)
        try:
            path, _ = QFileDialog.getOpenFileName(
                self, "选择背景图片", "", "图片文件 (*.png *.jpg *.jpeg *.bmp *.webp *.gif)"
            )
        finally:
            self._set_topmost(True)
        if not path:
            return
        if QPixmap(path).isNull():
            QMessageBox.warning(self, "无法加载图片",
                                "这张图片无法解码，可能格式不受支持或文件已损坏。\n请换一张 PNG / JPG 图片。")
            return
        self._s_bg_path.setText(path)

    def _on_close(self):
        self.hide()
        self.closed.emit()

    def open_providers_page(self):
        self._nav.setCurrentRow(0)

    # ── 拖动 ──────────────────────────────────────────────────────────────────

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and event.position().y() < 46:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event):
        if self._drag_pos and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, event):
        self._drag_pos = None

    # ── 样式 ──────────────────────────────────────────────────────────────────

    def _apply_style(self):
        if self._config.get("display.theme", "dark") == "light":
            self.setStyleSheet("""
                #settings_container {
                    background-color: rgba(252, 252, 253, 248);
                    border-radius: 12px;
                    border: 1px solid rgba(0,0,0,28);
                }
                #settings_titlebar {
                    background: rgba(246, 246, 248, 250);
                    border-top-left-radius: 12px; border-top-right-radius: 12px;
                    border-bottom: 1px solid rgba(0,0,0,10);
                }
                #settings_title { color: #2b2b2b; }
                #close_btn {
                    background: transparent; color: #888;
                    border: none; border-radius: 6px; font-size: 18px;
                }
                #close_btn:hover { background: #c42b1c; color: white; }
                #settings_footer {
                    background: rgba(246, 246, 248, 250);
                    border-bottom-left-radius: 12px; border-bottom-right-radius: 12px;
                    border-top: 1px solid rgba(0,0,0,10);
                }
                #nav_list {
                    background: rgba(236, 236, 240, 250);
                    border: none; outline: none;
                    border-bottom-left-radius: 12px;
                    padding: 8px 6px;
                    font-family: 'Microsoft YaHei'; font-size: 12px;
                }
                #nav_list::item {
                    color: #555; padding: 9px 12px; border-radius: 7px; margin: 2px 2px;
                }
                #nav_list::item:selected { background: rgba(59,130,246,160); color: white; }
                #nav_list::item:hover:!selected { background: rgba(0,0,0,8); color: #333; }
                #page_hint { color: #999; font-size: 11px; }
                #section_label { color: #444; font-size: 12px; font-weight: bold; }
                #fetch_status { font-size: 11px; }
                #hline { background: rgba(0,0,0,12); max-height: 1px; border: none; }
                QLabel { color: #555; font-size: 12px; }
                #sub_list {
                    background: rgba(246, 246, 248, 220); color: #333;
                    border: 1px solid rgba(0,0,0,15); border-radius: 7px;
                    outline: none; font-size: 12px; font-family: 'Microsoft YaHei';
                }
                #sub_list::item { padding: 6px 8px; border-radius: 5px; }
                #sub_list::item:selected { background: rgba(59,130,246,150); color: white; }
                #sub_list::item:hover:!selected { background: rgba(0,0,0,8); }
                QLineEdit, QComboBox {
                    background: white;
                    color: #333;
                    border: 1px solid rgba(0,0,0,15);
                    border-radius: 7px;
                    padding: 6px 9px;
                    font-size: 12px;
                }
                QLineEdit:focus, QComboBox:focus { border-color: rgba(59,130,246,150); }
                QLineEdit:disabled, QComboBox:disabled, QTextEdit:disabled { color: #aaa; }
                QTextEdit {
                    background: white;
                    color: #333;
                    border: 1px solid rgba(0,0,0,15);
                    border-radius: 7px;
                    font-size: 12px;
                }
                QCheckBox { color: #555; spacing: 6px; }
                QCheckBox::indicator {
                    width: 16px; height: 16px;
                    border: 1px solid rgba(0,0,0,20);
                    border-radius: 4px;
                    background: white;
                }
                QCheckBox::indicator:checked { background: #3b82f6; border-color: #3b82f6; }
                QSlider::groove:horizontal { height: 4px; background: rgba(0,0,0,15); border-radius: 2px; }
                QSlider::handle:horizontal {
                    width: 14px; height: 14px; margin: -5px 0;
                    background: #3b82f6; border-radius: 7px;
                }
                #mini_btn {
                    padding: 5px 12px;
                    background: rgba(0,0,0,7); color: #555;
                    border: 1px solid rgba(0,0,0,15); border-radius: 6px; font-size: 11px;
                }
                #mini_btn:hover { background: rgba(59,130,246,120); color: white; border-color: rgba(59,130,246,150); }
                #save_btn {
                    padding: 8px 28px;
                    background: #3b82f6; color: white;
                    border: none; border-radius: 8px; font-size: 12px;
                }
                #save_btn:hover { background: #2f6fe0; }
                #io_btn {
                    padding: 8px 16px;
                    background: rgba(0,0,0,7); color: #555;
                    border: 1px solid rgba(0,0,0,15); border-radius: 8px; font-size: 12px;
                }
                #io_btn:hover { background: rgba(0,0,0,14); color: #333; }
                QComboBox QAbstractItemView {
                    background: white; color: #333;
                    selection-background-color: rgba(59,130,246,160);
                    border: 1px solid rgba(0,0,0,15);
                }
                QScrollBar:vertical { width: 7px; background: transparent; }
                QScrollBar::handle:vertical { background: rgba(0,0,0,25); border-radius: 3px; min-height: 22px; }
                QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
            """)
        else:
            self.setStyleSheet("""
                #settings_container {
                    background-color: rgba(32, 32, 34, 248);
                    border-radius: 12px;
                    border: 1px solid rgba(255,255,255,28);
                }
                #settings_titlebar {
                    background: rgba(40,40,42,250);
                    border-top-left-radius: 12px; border-top-right-radius: 12px;
                    border-bottom: 1px solid rgba(255,255,255,12);
                }
                #settings_title { color: #ececec; }
                #close_btn {
                    background: transparent; color: #999;
                    border: none; border-radius: 6px; font-size: 18px;
                }
                #close_btn:hover { background: #c42b1c; color: white; }
                #settings_footer {
                    background: rgba(40,40,42,250);
                    border-bottom-left-radius: 12px; border-bottom-right-radius: 12px;
                    border-top: 1px solid rgba(255,255,255,12);
                }
                #nav_list {
                    background: rgba(26,26,28,250);
                    border: none; outline: none;
                    border-bottom-left-radius: 12px;
                    padding: 8px 6px;
                    font-family: 'Microsoft YaHei'; font-size: 12px;
                }
                #nav_list::item {
                    color: #b0b0b0; padding: 9px 12px; border-radius: 7px; margin: 2px 2px;
                }
                #nav_list::item:selected { background: rgba(59,130,246,180); color: white; }
                #nav_list::item:hover:!selected { background: rgba(255,255,255,16); color: #e0e0e0; }
                #page_hint { color: #8a8a8a; font-size: 11px; }
                #section_label { color: #cfcfcf; font-size: 12px; font-weight: bold; }
                #fetch_status { font-size: 11px; }
                #hline { background: rgba(255,255,255,15); max-height: 1px; border: none; }
                QLabel { color: #ccc; font-size: 12px; }
                #sub_list {
                    background: rgba(48,48,52,200); color: #e0e0e0;
                    border: 1px solid rgba(255,255,255,18); border-radius: 7px;
                    outline: none; font-size: 12px; font-family: 'Microsoft YaHei';
                }
                #sub_list::item { padding: 6px 8px; border-radius: 5px; }
                #sub_list::item:selected { background: rgba(59,130,246,170); color: white; }
                #sub_list::item:hover:!selected { background: rgba(255,255,255,14); }
                QLineEdit, QComboBox {
                    background: rgba(50,50,54,210);
                    color: #e8e8e8;
                    border: 1px solid rgba(255,255,255,20);
                    border-radius: 7px;
                    padding: 6px 9px;
                    font-size: 12px;
                }
                QLineEdit:focus, QComboBox:focus { border-color: rgba(59,130,246,160); }
                QLineEdit:disabled, QComboBox:disabled, QTextEdit:disabled { color: #666; }
                QTextEdit {
                    background: rgba(50,50,54,210);
                    color: #e8e8e8;
                    border: 1px solid rgba(255,255,255,20);
                    border-radius: 7px;
                    font-size: 12px;
                }
                QCheckBox { color: #ccc; spacing: 6px; }
                QCheckBox::indicator {
                    width: 16px; height: 16px;
                    border: 1px solid rgba(255,255,255,30);
                    border-radius: 4px;
                    background: rgba(50,50,54,210);
                }
                QCheckBox::indicator:checked { background: #3b82f6; border-color: #3b82f6; }
                QSlider::groove:horizontal { height: 4px; background: rgba(255,255,255,20); border-radius: 2px; }
                QSlider::handle:horizontal {
                    width: 14px; height: 14px; margin: -5px 0;
                    background: #3b82f6; border-radius: 7px;
                }
                #mini_btn {
                    padding: 5px 12px;
                    background: rgba(255,255,255,14); color: #d8d8d8;
                    border: 1px solid rgba(255,255,255,22); border-radius: 6px; font-size: 11px;
                }
                #mini_btn:hover { background: rgba(59,130,246,140); color: white; border-color: rgba(59,130,246,160); }
                #save_btn {
                    padding: 8px 28px;
                    background: #3b82f6; color: white;
                    border: none; border-radius: 8px; font-size: 12px;
                }
                #save_btn:hover { background: #2f6fe0; }
                #io_btn {
                    padding: 8px 16px;
                    background: rgba(255,255,255,12); color: #d0d0d0;
                    border: 1px solid rgba(255,255,255,20); border-radius: 8px; font-size: 12px;
                }
                #io_btn:hover { background: rgba(255,255,255,24); color: #fff; }
                QComboBox QAbstractItemView {
                    background: #2d2d2f; color: #e0e0e0;
                    selection-background-color: rgba(59,130,246,180);
                    border: 1px solid #404044;
                }
                QScrollBar:vertical { width: 7px; background: transparent; }
                QScrollBar::handle:vertical { background: rgba(255,255,255,40); border-radius: 3px; min-height: 22px; }
                QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
            """)
