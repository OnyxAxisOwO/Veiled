from PyQt6.QtWidgets import QLineEdit
from PyQt6.QtCore import Qt


class HotkeyInput(QLineEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setPlaceholderText("按下快捷键组合...")
        self._keys = ""

    def keyPressEvent(self, event):
        parts = []
        mods = event.modifiers()
        if mods & Qt.KeyboardModifier.ControlModifier:
            parts.append("ctrl")
        if mods & Qt.KeyboardModifier.ShiftModifier:
            parts.append("shift")
        if mods & Qt.KeyboardModifier.AltModifier:
            parts.append("alt")

        key = event.key()
        key_map = {
            Qt.Key.Key_Space: "space", Qt.Key.Key_QuoteLeft: "`",
            Qt.Key.Key_Tab: "tab", Qt.Key.Key_Return: "enter",
            Qt.Key.Key_Escape: "escape",
        }
        ignore_keys = {
            Qt.Key.Key_Control, Qt.Key.Key_Shift, Qt.Key.Key_Alt, Qt.Key.Key_Meta,
        }
        if key in ignore_keys:
            return

        key_name = key_map.get(key)
        if not key_name:
            if Qt.Key.Key_A <= key <= Qt.Key.Key_Z:
                key_name = chr(key).lower()
            elif Qt.Key.Key_0 <= key <= Qt.Key.Key_9:
                key_name = chr(key)
            elif Qt.Key.Key_F1 <= key <= Qt.Key.Key_F12:
                key_name = f"f{key - Qt.Key.Key_F1 + 1}"
            else:
                return

        parts.append(key_name)
        self._keys = "+".join(parts)
        self.setText(self._keys)

    @property
    def hotkey(self) -> str:
        return self._keys
