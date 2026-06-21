"""Theme palettes so the app's look is driven by our own theme setting, not the
OS dark/light mode. Without an explicit style+palette, Qt on Windows 11 paints
scroll areas / stacked widgets with the system (often dark) palette, which then
bleeds through the translucent windows even when the user picked the light theme.
"""
from PyQt6.QtGui import QPalette, QColor


def _qc(hex_str: str) -> QColor:
    return QColor(hex_str)


def hex_to_rgb_str(hex_str: str) -> str:
    """'#3b82f6' -> '59,130,246'，用于把强调色注入 rgba() 样式串。"""
    h = (hex_str or "").lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    try:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except (ValueError, IndexError):
        return "59,130,246"
    return f"{r},{g},{b}"


def darken(hex_str: str, factor: float = 0.82) -> str:
    """把十六进制颜色按比例调暗，用于按钮 hover 态。"""
    h = (hex_str or "").lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    try:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except (ValueError, IndexError):
        return "#2f6fe0"
    r, g, b = (max(0, min(255, int(v * factor))) for v in (r, g, b))
    return f"#{r:02x}{g:02x}{b:02x}"


def build_palette(theme: str, accent: str = "#3b82f6") -> QPalette:
    light = (theme == "light")
    if light:
        window, base, alt = "#f4f4f6", "#ffffff", "#ececef"
        text, button, btext = "#222222", "#ececee", "#222222"
        disabled = "#9a9a9a"
        tip_bg, tip_text = "#ffffff", "#222222"
    else:
        window, base, alt = "#202022", "#2c2c30", "#333338"
        text, button, btext = "#ececec", "#2a2a2c", "#ececec"
        disabled = "#777777"
        tip_bg, tip_text = "#2b2b2b", "#ececec"

    p = QPalette()
    p.setColor(QPalette.ColorRole.Window, _qc(window))
    p.setColor(QPalette.ColorRole.WindowText, _qc(text))
    p.setColor(QPalette.ColorRole.Base, _qc(base))
    p.setColor(QPalette.ColorRole.AlternateBase, _qc(alt))
    p.setColor(QPalette.ColorRole.Text, _qc(text))
    p.setColor(QPalette.ColorRole.Button, _qc(button))
    p.setColor(QPalette.ColorRole.ButtonText, _qc(btext))
    p.setColor(QPalette.ColorRole.BrightText, _qc("#ff5555"))
    p.setColor(QPalette.ColorRole.ToolTipBase, _qc(tip_bg))
    p.setColor(QPalette.ColorRole.ToolTipText, _qc(tip_text))
    p.setColor(QPalette.ColorRole.PlaceholderText, _qc(disabled))
    p.setColor(QPalette.ColorRole.Highlight, _qc(accent or "#3b82f6"))
    p.setColor(QPalette.ColorRole.HighlightedText, _qc("#ffffff"))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, _qc(disabled))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, _qc(disabled))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, _qc(disabled))
    return p


def apply_theme(app, theme: str, accent: str = "#3b82f6"):
    """Apply Fusion style + our palette app-wide so every window (and native
    message boxes / menus) follows the chosen theme regardless of OS mode."""
    try:
        app.setStyle("Fusion")
    except Exception:
        pass
    app.setPalette(build_palette("light" if theme == "light" else "dark", accent))
