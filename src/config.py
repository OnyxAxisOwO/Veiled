import json
import copy
import ctypes
import ctypes.wintypes
import os
import base64
import uuid
from pathlib import Path

CRYPT32 = ctypes.windll.crypt32
KERNEL32 = ctypes.windll.kernel32


class DATA_BLOB(ctypes.Structure):
    _fields_ = [
        ("cbData", ctypes.wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_char)),
    ]


def dpapi_encrypt(data: bytes) -> bytes:
    input_blob = DATA_BLOB(len(data), ctypes.create_string_buffer(data, len(data)))
    output_blob = DATA_BLOB()
    if not CRYPT32.CryptProtectData(
        ctypes.byref(input_blob), None, None, None, None, 0, ctypes.byref(output_blob)
    ):
        raise OSError("DPAPI encryption failed")
    encrypted = ctypes.string_at(output_blob.pbData, output_blob.cbData)
    KERNEL32.LocalFree(output_blob.pbData)
    return encrypted


def dpapi_decrypt(data: bytes) -> bytes:
    input_blob = DATA_BLOB(len(data), ctypes.create_string_buffer(data, len(data)))
    output_blob = DATA_BLOB()
    if not CRYPT32.CryptUnprotectData(
        ctypes.byref(input_blob), None, None, None, None, 0, ctypes.byref(output_blob)
    ):
        raise OSError("DPAPI decryption failed")
    decrypted = ctypes.string_at(output_blob.pbData, output_blob.cbData)
    KERNEL32.LocalFree(output_blob.pbData)
    return decrypted


# ── 展示名映射 ──────────────────────────────────────────────────────────────
# kind = 接口协议（决定请求格式），不再等同于「服务商」。一个服务商就是一组
# endpoint + key + 模型列表，用户可以创建任意多个。
KIND_MAP = {"openai": "OpenAI 兼容", "claude": "Claude (Anthropic)"}
POSITION_MAP = {"bottom_right": "右下角", "bottom_left": "左下角", "top_right": "右上角", "top_left": "左上角", "center": "居中"}

DEFAULT_VISION_PROMPT = (
    "你是一个视觉识别助手。请把图片中的全部信息完整、客观地转写成文字："
    "包含题目、选项、代码、报错、表格、界面文字等所有可见内容，"
    "保留原有结构和顺序，不要作答、不要总结、不要省略。"
)

# 用于在「获取模型列表」时猜测某模型是否支持图片输入（仅作默认勾选，用户可改）
_VISION_HINTS = (
    "gpt-4o", "gpt-4.1", "gpt-4-vision", "gpt-4-turbo", "gpt-5", "o1", "o3", "o4-",
    "vision", "claude-3", "claude-sonnet", "claude-opus", "claude-haiku",
    "gemini", "-vl", "vl-", "qwen-vl", "pixtral", "llava",
    "grok-vision", "grok-2-vision", "grok-4",
)


def model_guess_vision(model_id: str) -> bool:
    m = (model_id or "").lower()
    return any(h in m for h in _VISION_HINTS)


def new_provider_id() -> str:
    return "p_" + uuid.uuid4().hex[:8]


DEFAULT_PROVIDERS = [
    {
        "id": "openai",
        "name": "OpenAI",
        "kind": "openai",
        "endpoint": "https://api.openai.com",
        "api_key": "",
        "extra_body": "",
        "models": [
            {"id": "gpt-4o", "name": "GPT-4o", "vision": True},
            {"id": "gpt-4o-mini", "name": "GPT-4o mini", "vision": True},
        ],
    },
    {
        "id": "claude",
        "name": "Claude",
        "kind": "claude",
        "endpoint": "https://api.anthropic.com",
        "api_key": "",
        "extra_body": "",
        "models": [
            {"id": "claude-sonnet-4-20250514", "name": "Claude Sonnet 4", "vision": True},
            {"id": "claude-opus-4-20250514", "name": "Claude Opus 4", "vision": True},
        ],
    },
]

DEFAULT_CONFIG = {
    "api": {
        "active": {"provider": "openai", "model": "gpt-4o"},
        # 并行多模型：用户可一次性勾选多个模型，请求会同时广播给全部。
        # 第一个为「主模型」，与 api.active 保持同步（也是单模型时的唯一项）。
        "active_models": [{"provider": "openai", "model": "gpt-4o"}],
        # 多模型回答后追问的上下文衔接方式：primary = 只用主模型回答续接；all = 把所有回答合并写进历史。
        "multi_history_mode": "primary",
        "providers": copy.deepcopy(DEFAULT_PROVIDERS),
        "proxy": "",
        # 可选的「视觉识别中继」：主模型不支持图片时，先用这里指定的视觉模型把
        # 截图转成文字再交给主模型。现在它只引用某个服务商下的模型，不再单独存 key。
        "vision_relay": {
            "enabled": False,
            "provider": "",   # provider id
            "model": "",      # model id
            "prompt": DEFAULT_VISION_PROMPT,
        },
    },
    "hotkeys": {
        "toggle_chat": "ctrl+shift+space",
        "boss_key": "ctrl+`",
        "clipboard_ask": "ctrl+shift+q",
        "screenshot_ask": "ctrl+shift+s",
        "screenshot_full": "ctrl+shift+a",
        "exit": "ctrl+shift+alt+q",
    },
    "display": {
        "tray_icon": False,
        "menu_style": "native",   # native = 原生 Windows 菜单（TrackPopupMenu）；styled = 深色样式菜单（带输入框）
        "chat_position": "bottom_right",
        "chat_opacity": 0.9,
        "chat_width": 440,
        "chat_height": 560,
        "screenshot_protection": True,
        "auto_start": True,
        "notification_title": "",
        "screenshot_success_toast": True,
        "screenshot_success_text": "成功",
        "theme": "dark",
        "accent_color": "#3b82f6",
        "close_on_focus_lost": False,
    },
    "privacy": {
        "history_retention_days": 0,
        "save_screenshots": False,
        "clear_on_exit": False,
    },
    "environment": {
        "monitor_enabled": True,
        "notify_on_silent": True,
        "suspicious_processes": [
            "mstsc.exe", "TeamViewer.exe", "AnyDesk.exe",
            "obs64.exe", "obs32.exe", "bandicam.exe",
            "Zoom.exe", "Teams.exe", "WeMeetApp.exe",
        ],
    },
    "prompts": {
        "chat": "You are a helpful assistant. Be concise and direct.",
        "screenshot": "Look at this image and give the most concise answer possible. For multiple choice: just state the answer letter. For fill-in-the-blank: just give the answer. For code errors: give the key fix steps. For other content: one sentence summary.",
        "clipboard": "Process the following text. If it's in a foreign language, translate it. If it's a question, answer it. If it's content, summarize it. Be concise.",
        "screenshot_message": "请分析这张截图",
    },
    "first_run": True,
}


class Config:
    def __init__(self):
        self._install_dir = Path(os.environ.get(
            "DAH_INSTALL_DIR",
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "DisplayAdapterHelper")
        ))
        self._config_path = self._install_dir / "adapter_config.dat"
        self._data = copy.deepcopy(DEFAULT_CONFIG)
        self._install_dir.mkdir(parents=True, exist_ok=True)
        self.load()

    @property
    def install_dir(self) -> Path:
        return self._install_dir

    @property
    def db_path(self) -> Path:
        return self._install_dir / "cache.db"

    def load(self):
        if not self._config_path.exists():
            self.save()
            return
        loaded = None
        try:
            encrypted = self._config_path.read_bytes()
            decrypted = dpapi_decrypt(encrypted)
            loaded = json.loads(decrypted.decode("utf-8"))
            self._deep_merge(self._data, loaded)
        except Exception:
            pass
        # 旧版本配置没有 active_models：不要让 DEFAULT 里注入的 [{openai,gpt-4o}] 顶替用户的真实
        # 主模型——删掉它，迁移时再由 api.active 推导，避免升级后模型被悄悄重置。
        self._drop_injected_active_models(loaded)
        try:
            self._migrate()
        except Exception:
            # 迁移失败时退回默认 API 配置，保证程序仍能启动
            self._data["api"] = copy.deepcopy(DEFAULT_CONFIG["api"])

    def _drop_injected_active_models(self, loaded):
        """若来源配置本身不含 api.active_models，则丢弃合并进来的默认值，让其由 api.active 推导。"""
        if isinstance(loaded, dict) and "active_models" not in (loaded.get("api") or {}):
            api = self._data.get("api")
            if isinstance(api, dict):
                api.pop("active_models", None)

    def save(self):
        raw = json.dumps(self._data, ensure_ascii=False, indent=2).encode("utf-8")
        encrypted = dpapi_encrypt(raw)
        self._config_path.write_bytes(encrypted)

    def export_json(self, path: str):
        """把当前配置导出为明文 JSON（含 API Key，注意保管）。"""
        raw = json.dumps(self._data, ensure_ascii=False, indent=2)
        Path(path).write_text(raw, encoding="utf-8")

    def import_json(self, path: str):
        """从明文 JSON 导入配置：以默认配置为基底合并，缺失字段自动补全，随后加密保存。"""
        loaded = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise ValueError("配置文件格式不正确：根节点应为 JSON 对象")
        merged = copy.deepcopy(DEFAULT_CONFIG)
        self._deep_merge(merged, loaded)
        self._data = merged
        self._drop_injected_active_models(loaded)
        try:
            self._migrate()
        except Exception:
            self._data["api"] = copy.deepcopy(DEFAULT_CONFIG["api"])
        self.save()

    def get(self, dotpath: str, default=None):
        keys = dotpath.split(".")
        node = self._data
        for k in keys:
            if isinstance(node, dict) and k in node:
                node = node[k]
            else:
                return default
        return node

    def set(self, dotpath: str, value):
        keys = dotpath.split(".")
        node = self._data
        for k in keys[:-1]:
            if k not in node or not isinstance(node[k], dict):
                node[k] = {}
            node = node[k]
        node[keys[-1]] = value

    # ── 服务商 / 模型读取 ────────────────────────────────────────────────────

    def providers(self) -> list:
        p = self.get("api.providers", [])
        return p if isinstance(p, list) else []

    def get_provider(self, provider_id: str) -> dict | None:
        for p in self.providers():
            if p.get("id") == provider_id:
                return p
        return None

    def active_provider(self) -> dict | None:
        prov = self.get_provider(self.get("api.active.provider", ""))
        if prov is None:
            provs = self.providers()
            prov = provs[0] if provs else None
        return prov

    def active_model(self) -> dict | None:
        prov = self.active_provider()
        if not prov:
            return None
        models = prov.get("models", [])
        mid = self.get("api.active.model", "")
        for m in models:
            if m.get("id") == mid:
                return m
        return models[0] if models else None

    def set_active(self, provider_id: str, model_id: str):
        self.set("api.active.provider", provider_id)
        self.set("api.active.model", model_id)

    # ── 并行多模型 ────────────────────────────────────────────────────────────

    def _model_exists(self, provider_id: str, model_id: str) -> bool:
        if not provider_id or not model_id:
            return False
        prov = self.get_provider(provider_id)
        if not prov:
            return False
        return any(m.get("id") == model_id for m in prov.get("models", []))

    def active_models(self) -> list[dict]:
        """当前选中的全部模型（去重、且都真实存在）。列表首项为主模型。

        若 api.active_models 缺失/失效，则回退为「单一 api.active」一项，
        从而让所有旧的单模型代码路径无缝继续工作。"""
        out, seen = [], set()
        raw = self.get("api.active_models", None)
        if isinstance(raw, list):
            for item in raw:
                if not isinstance(item, dict):
                    continue
                pid, mid = item.get("provider", ""), item.get("model", "")
                key = (pid, mid)
                if key not in seen and self._model_exists(pid, mid):
                    out.append({"provider": pid, "model": mid})
                    seen.add(key)
        if out:
            return out
        apid, amid = self.get("api.active.provider", ""), self.get("api.active.model", "")
        if self._model_exists(apid, amid):
            return [{"provider": apid, "model": amid}]
        prov, m = self.active_provider(), self.active_model()
        if prov and m and m.get("id"):
            return [{"provider": prov.get("id"), "model": m.get("id")}]
        return []

    def set_active_models(self, models: list[dict]):
        """写入选中的模型集合；首项同步为单模型 api.active。空/全失效则忽略（保持当前）。"""
        clean, seen = [], set()
        for item in models or []:
            if not isinstance(item, dict):
                continue
            pid, mid = item.get("provider", ""), item.get("model", "")
            key = (pid, mid)
            if key not in seen and self._model_exists(pid, mid):
                clean.append({"provider": pid, "model": mid})
                seen.add(key)
        if not clean:
            return
        self.set("api.active_models", clean)
        self.set("api.active.provider", clean[0]["provider"])
        self.set("api.active.model", clean[0]["model"])

    def set_primary_model(self, provider_id: str, model_id: str):
        """设置主模型（也用于设置面板「设为默认模型」保存时）：
        若该模型已在并行选中集合内，则提到首位、保留其余并行项；
        若不在集合内，则视为切回单一模型。同时裁剪掉已失效的并行项。"""
        if not self._model_exists(provider_id, model_id):
            # 主模型无效（如该服务商无任何模型）：仅做一次有效性裁剪
            self._ensure_active_models_valid()
            return
        key = (provider_id, model_id)
        sel = [(m["provider"], m["model"]) for m in self.active_models()]
        if key in sel:
            sel = [key] + [k for k in sel if k != key]
        else:
            sel = [key]
        self.set_active_models([{"provider": p, "model": m} for p, m in sel])

    @property
    def provider(self) -> str:
        """当前激活服务商的 id（兼容旧调用）。"""
        p = self.active_provider()
        return p.get("id", "") if p else ""

    @property
    def api_kind(self) -> str:
        p = self.active_provider()
        return p.get("kind", "openai") if p else "openai"

    @property
    def api_key(self) -> str:
        p = self.active_provider()
        return p.get("api_key", "") if p else ""

    @property
    def api_endpoint(self) -> str:
        p = self.active_provider()
        return p.get("endpoint", "") if p else ""

    @property
    def api_model(self) -> str:
        m = self.active_model()
        return m.get("id", "") if m else ""

    @property
    def proxy(self) -> str:
        return self.get("api.proxy", "")

    @property
    def api_extra_body_raw(self) -> str:
        p = self.active_provider()
        return p.get("extra_body", "") if p else ""

    @property
    def api_extra_body(self) -> dict:
        return parse_extra_body(self.api_extra_body_raw)

    def active_model_supports_vision(self) -> bool:
        m = self.active_model()
        return bool(m.get("vision")) if m else False

    # ── 迁移 ────────────────────────────────────────────────────────────────

    def _migrate(self):
        """把旧版配置（api.provider 字符串 + api.providers 字典 + api.vision）
        升级为新版（api.providers 列表 + api.active 指针 + api.vision_relay）。"""
        api = self._data.setdefault("api", {})
        provs = api.get("providers")

        if isinstance(provs, list):
            # 已是新格式，补齐缺失字段即可
            api.setdefault("active", {"provider": "", "model": ""})
            api.setdefault("proxy", "")
            api.setdefault("vision_relay", copy.deepcopy(DEFAULT_CONFIG["api"]["vision_relay"]))
            api.setdefault("multi_history_mode", "primary")
            for p in provs:
                p.setdefault("id", new_provider_id())
                p.setdefault("name", p.get("id", "服务商"))
                p.setdefault("kind", "openai")
                p.setdefault("endpoint", "")
                p.setdefault("api_key", "")
                p.setdefault("extra_body", "")
                p.setdefault("models", [])
            self._ensure_active_valid()
            self._ensure_vision_relay_valid()
            self._ensure_active_models_valid()
            return

        if not isinstance(provs, dict):
            # 完全没有可用结构 → 用默认
            self._data["api"] = copy.deepcopy(DEFAULT_CONFIG["api"])
            return

        old = provs
        old_active = api.get("provider", "openai")
        name_map = {"openai": "OpenAI", "claude": "Claude", "custom": "自定义"}
        new_list = []
        for key in ("openai", "claude", "custom"):
            if key not in old:
                continue
            o = old[key] or {}
            model_id = (o.get("model") or "").strip()
            models = []
            if model_id:
                models.append({"id": model_id, "name": model_id, "vision": model_guess_vision(model_id)})
            new_list.append({
                "id": key,
                "name": name_map.get(key, key),
                "kind": "claude" if key == "claude" else "openai",
                "endpoint": o.get("endpoint", ""),
                "api_key": o.get("api_key", ""),
                "extra_body": o.get("extra_body", ""),
                "models": models,
            })
        if not new_list:
            new_list = copy.deepcopy(DEFAULT_PROVIDERS)

        active_model_id = (old.get(old_active, {}) or {}).get("model", "")

        # 旧 api.vision → vision_relay（如启用且有 key，则作为一个独立服务商保留）
        relay = {"enabled": False, "provider": "", "model": "", "prompt": DEFAULT_VISION_PROMPT}
        ov = api.get("vision", {})
        if isinstance(ov, dict):
            relay["prompt"] = ov.get("prompt", DEFAULT_VISION_PROMPT)
            vmodel = (ov.get("model") or "").strip()
            # 仅当旧中继同时具备 key 和模型时才启用，避免迁移出 model="" 的悬空中继
            if ov.get("enabled") and (ov.get("api_key") or "").strip() and vmodel:
                vid = "vision_relay"
                new_list.append({
                    "id": vid,
                    "name": "视觉中继（迁移）",
                    "kind": "claude" if ov.get("provider") == "claude" else "openai",
                    "endpoint": ov.get("endpoint", ""),
                    "api_key": ov.get("api_key", ""),
                    "extra_body": ov.get("extra_body", ""),
                    "models": ([{"id": vmodel, "name": vmodel, "vision": True}] if vmodel else []),
                })
                relay.update({"enabled": True, "provider": vid, "model": vmodel})

        active_provider_id = old_active if any(p["id"] == old_active for p in new_list) else new_list[0]["id"]
        if not active_model_id:
            ap = next((p for p in new_list if p["id"] == active_provider_id), new_list[0])
            active_model_id = ap["models"][0]["id"] if ap.get("models") else ""

        self._data["api"] = {
            "active": {"provider": active_provider_id, "model": active_model_id},
            "active_models": [{"provider": active_provider_id, "model": active_model_id}],
            "multi_history_mode": api.get("multi_history_mode", "primary"),
            "providers": new_list,
            "proxy": api.get("proxy", ""),
            "vision_relay": relay,
        }
        self._ensure_active_models_valid()

    def _ensure_active_valid(self):
        """保证 api.active 指向真实存在的服务商/模型，否则回退到第一个。"""
        provs = self.providers()
        if not provs:
            return
        prov = self.get_provider(self.get("api.active.provider", ""))
        if prov is None:
            prov = provs[0]
            self.set("api.active.provider", prov["id"])
        models = prov.get("models", [])
        mid = self.get("api.active.model", "")
        if models and not any(m.get("id") == mid for m in models):
            self.set("api.active.model", models[0]["id"])

    def _ensure_active_models_valid(self):
        """保证 api.active_models 是一组真实存在、去重的 (provider, model)，
        且首项与 api.active 同步。缺失时由单一 active 推导。"""
        models = self.active_models()   # 已做存在性校验与回退
        self.set("api.active_models", models)
        if models:
            self.set("api.active.provider", models[0]["provider"])
            self.set("api.active.model", models[0]["model"])

    def _ensure_vision_relay_valid(self):
        """若视觉中继指向的服务商/模型已不存在，则停用并清空指针，避免悬空引用。"""
        prov = self.get_provider(self.get("api.vision_relay.provider", ""))
        mid = self.get("api.vision_relay.model", "")
        ok = bool(prov and mid and any(m.get("id") == mid for m in prov.get("models", [])))
        if not ok:
            self.set("api.vision_relay.enabled", False)
            self.set("api.vision_relay.provider", "")
            self.set("api.vision_relay.model", "")

    def _deep_merge(self, base: dict, override: dict):
        for k, v in override.items():
            if k in base and isinstance(base[k], dict) and isinstance(v, dict):
                self._deep_merge(base[k], v)
            else:
                base[k] = v


def parse_extra_body(raw: str) -> dict:
    if not raw or not raw.strip():
        return {}
    try:
        val = json.loads(raw)
        return val if isinstance(val, dict) else {}
    except Exception:
        return {}
