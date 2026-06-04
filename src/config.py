import json
import ctypes
import ctypes.wintypes
import os
import base64
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


DEFAULT_CONFIG = {
    "api": {
        "provider": "claude",
        "providers": {
            "claude": {"api_key": "", "model": "claude-sonnet-4-20250514", "endpoint": "https://api.anthropic.com"},
            "openai": {"api_key": "", "model": "gpt-4o", "endpoint": "https://api.openai.com"},
            "deepseek": {"api_key": "", "model": "deepseek-v4-pro", "endpoint": "https://api.deepseek.com"},
            "custom": {"api_key": "", "model": "", "endpoint": ""},
        },
        "proxy": "",
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
        "chat_position": "bottom_right",
        "chat_opacity": 0.9,
        "chat_width": 420,
        "chat_height": 520,
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
        self._data = dict(DEFAULT_CONFIG)
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

    def save(self):
        raw = json.dumps(self._data, ensure_ascii=False, indent=2).encode("utf-8")
        encrypted = dpapi_encrypt(raw)
        self._config_path.write_bytes(encrypted)

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
            if k not in node:
                node[k] = {}
            node = node[k]
        node[keys[-1]] = value
        self.save()

    @property
    def api_key(self) -> str:
        provider = self._data["api"]["provider"]
        return self._data["api"]["providers"].get(provider, {}).get("api_key", "")

    @property
    def api_model(self) -> str:
        provider = self._data["api"]["provider"]
        return self._data["api"]["providers"].get(provider, {}).get("model", "")

    @property
    def api_endpoint(self) -> str:
        provider = self._data["api"]["provider"]
        return self._data["api"]["providers"].get(provider, {}).get("endpoint", "")

    @property
    def provider(self) -> str:
        return self._data["api"]["provider"]

    def _deep_merge(self, base: dict, override: dict):
        for k, v in override.items():
            if k in base and isinstance(base[k], dict) and isinstance(v, dict):
                self._deep_merge(base[k], v)
            else:
                base[k] = v
