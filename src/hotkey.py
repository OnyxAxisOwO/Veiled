import ctypes
import ctypes.wintypes
import threading
from PyQt6.QtCore import QObject, pyqtSignal

user32 = ctypes.windll.user32

MOD_MAP = {
    "alt": 0x0001,
    "ctrl": 0x0002,
    "control": 0x0002,
    "shift": 0x0004,
    "win": 0x0008,
}

VK_MAP = {
    "space": 0x20, "tab": 0x09, "return": 0x0D, "enter": 0x0D,
    "escape": 0x1B, "esc": 0x1B, "backspace": 0x08, "delete": 0x2E,
    "`": 0xC0, "~": 0xC0, "-": 0xBD, "=": 0xBB,
    "[": 0xDB, "]": 0xDD, "\\": 0xDC, ";": 0xBA, "'": 0xDE,
    ",": 0xBC, ".": 0xBE, "/": 0xBF,
    "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73,
    "f5": 0x74, "f6": 0x75, "f7": 0x76, "f8": 0x77,
    "f9": 0x78, "f10": 0x79, "f11": 0x7A, "f12": 0x7B,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
    "insert": 0x2D, "pause": 0x13, "capslock": 0x14,
}

for c in "abcdefghijklmnopqrstuvwxyz":
    VK_MAP[c] = ord(c.upper())
for d in "0123456789":
    VK_MAP[d] = ord(d)


def parse_hotkey(combo: str) -> tuple[int, int]:
    parts = [p.strip().lower() for p in combo.split("+")]
    modifiers = 0
    vk = 0
    for p in parts:
        if p in MOD_MAP:
            modifiers |= MOD_MAP[p]
        elif p in VK_MAP:
            vk = VK_MAP[p]
        else:
            raise ValueError(f"Unknown key: {p}")
    return modifiers, vk


class HotkeyManager(QObject):
    triggered = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._hotkeys: dict[int, str] = {}
        self._thread: threading.Thread | None = None
        self._thread_id: int = 0
        self._running = False
        self._next_id = 1

    def register(self, name: str, combo: str):
        modifiers, vk = parse_hotkey(combo)
        hk_id = self._next_id
        self._next_id += 1
        self._hotkeys[hk_id] = name
        if self._thread_id:
            ctypes.windll.user32.PostThreadMessageW(
                self._thread_id, 0x0400, hk_id, (modifiers << 16) | vk
            )
        else:
            self._pending_registrations = getattr(self, "_pending_registrations", [])
            self._pending_registrations.append((hk_id, modifiers, vk))

    def reload(self, hotkeys: dict):
        """运行时重新登记全部热键：在调用线程解析组合键，再交由工作线程注销现有
        全部热键并安装新集合，从而让改动的快捷键无需重启即时生效。"""
        specs = []
        for name, combo in hotkeys.items():
            if not combo:
                continue
            try:
                mods, vk = parse_hotkey(combo)
            except ValueError:
                continue
            specs.append((name, mods, vk))
        self._pending_reload = specs
        if self._thread_id:
            ctypes.windll.user32.PostThreadMessageW(self._thread_id, 0x0401, 0, 0)  # WM_USER+1 重载
        else:
            # 线程尚未启动：折叠进 pending 注册，让 start() 直接安装这批热键
            self._hotkeys = {}
            self._next_id = 1
            self._pending_registrations = []
            for name, mods, vk in specs:
                hk_id = self._next_id
                self._next_id += 1
                self._hotkeys[hk_id] = name
                self._pending_registrations.append((hk_id, mods, vk))
            self._pending_reload = []

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread_id:
            ctypes.windll.user32.PostThreadMessageW(self._thread_id, 0x0012, 0, 0)  # WM_QUIT
        if self._thread:
            self._thread.join(timeout=2)

    def _run(self):
        self._thread_id = ctypes.windll.kernel32.GetCurrentThreadId()

        for hk_id, mods, vk in getattr(self, "_pending_registrations", []):
            user32.RegisterHotKey(None, hk_id, mods | 0x4000, vk)  # MOD_NOREPEAT
        self._pending_registrations = []

        msg = ctypes.wintypes.MSG()
        while self._running:
            ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if ret <= 0:
                break
            if msg.message == 0x0312:  # WM_HOTKEY
                hk_id = msg.wParam
                name = self._hotkeys.get(hk_id, "")
                if name:
                    self.triggered.emit(name)
            elif msg.message == 0x0400:  # WM_USER - register hotkey from main thread
                hk_id = msg.wParam
                packed = msg.lParam
                mods = (packed >> 16) & 0xFFFF
                vk = packed & 0xFFFF
                user32.RegisterHotKey(None, hk_id, mods | 0x4000, vk)
            elif msg.message == 0x0401:  # WM_USER+1 - 注销全部并按 _pending_reload 重新登记
                for hk_id in list(self._hotkeys):
                    user32.UnregisterHotKey(None, hk_id)
                self._hotkeys = {}
                for name, mods, vk in getattr(self, "_pending_reload", []):
                    hk_id = self._next_id
                    self._next_id += 1
                    self._hotkeys[hk_id] = name
                    user32.RegisterHotKey(None, hk_id, mods | 0x4000, vk)
                self._pending_reload = []

        for hk_id in self._hotkeys:
            user32.UnregisterHotKey(None, hk_id)
        self._thread_id = 0
