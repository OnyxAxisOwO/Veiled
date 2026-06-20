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
DISGUISE_MAP = {"none": "无伪装", "qq": "QQ", "wechat": "微信", "edge": "浏览器 (Edge)"}

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
        "notification_disguise": "none",
        "screenshot_success_toast": True,
        "screenshot_success_text": "成功",
        "theme": "dark",
        "close_on_focus_lost": False,
    },
    "privacy": {
        "history_retention_days": 0,
        "save_screenshots": False,
        "clear_on_exit": False,
    },
    "environment": {
        "monitor_enabled": True,
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
        try:
            encrypted = self._config_path.read_bytes()
            decrypted = dpapi_decrypt(encrypted)
            loaded = json.loads(decrypted.decode("utf-8"))
            self._deep_merge(self._data, loaded)
        except Exception:
            pass
        try:
            self._migrate()
        except Exception:
            # 迁移失败时退回默认 API 配置，保证程序仍能启动
            self._data["api"] = copy.deepcopy(DEFAULT_CONFIG["api"])

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
            "providers": new_list,
            "proxy": api.get("proxy", ""),
            "vision_relay": relay,
        }

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
