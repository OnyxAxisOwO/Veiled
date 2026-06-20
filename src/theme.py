"""Theme palettes so the app's look is driven by our own theme setting, not the
OS dark/light mode. Without an explicit style+palette, Qt on Windows 11 paints
scroll areas / stacked widgets with the system (often dark) palette, which then
bleeds through the translucent windows even when the user picked the light theme.
"""
from PyQt6.QtGui import QPalette, QColor


def _qc(hex_str: str) -> QColor:
    return QColor(hex_str)


def build_palette(theme: str) -> QPalette:
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
    p.setColor(QPalette.ColorRole.Highlight, _qc("#3b82f6"))
    p.setColor(QPalette.ColorRole.HighlightedText, _qc("#ffffff"))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, _qc(disabled))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, _qc(disabled))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, _qc(disabled))
    return p


def apply_theme(app, theme: str):
    """Apply Fusion style + our palette app-wide so every window (and native
    message boxes / menus) follows the chosen theme regardless of OS mode."""
    try:
        app.setStyle("Fusion")
    except Exception:
        pass
    app.setPalette(build_palette("light" if theme == "light" else "dark"))
