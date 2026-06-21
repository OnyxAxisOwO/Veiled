from PyQt6.QtCore import QObject, pyqtSignal


class CommandHandler(QObject):
    open_settings = pyqtSignal()
    new_conversation = pyqtSignal()
    list_conversations = pyqtSignal()
    clear_conversation = pyqtSignal()
    delete_conversation = pyqtSignal()
    switch_model = pyqtSignal()
    translate = pyqtSignal(str)
    summarize = pyqtSignal()
    export_conversation = pyqtSignal()
    show_help = pyqtSignal()
    unknown_command = pyqtSignal(str)

    HELP_TEXT = """可用功能（也可点右上角 ⋯ 菜单）：
/new — 新建对话
/list — 历史对话
/clear — 清空当前对话
/delete — 删除当前对话
/export — 导出对话
/t [文字] — 翻译（留空则翻译剪贴板）
/s — 总结剪贴板
/model — 切换模型（也可点左上角模型名）
/settings — 打开设置
/help — 显示本帮助

· 顶部模型名可随时切换服务商 / 模型；勾选多个即并行向多个模型提问
· 选择带 👁 的视觉模型即可直接发送截图与图片"""

    def handle(self, text: str):
        text = text.strip()
        if not text.startswith("/"):
            return False

        parts = text.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""

        handlers = {
            "/settings": lambda: self.open_settings.emit(),
            "/new": lambda: self.new_conversation.emit(),
            "/list": lambda: self.list_conversations.emit(),
            "/clear": lambda: self.clear_conversation.emit(),
            "/delete": lambda: self.delete_conversation.emit(),
            "/model": lambda: self.switch_model.emit(),
            "/t": lambda: self.translate.emit(arg),
            "/s": lambda: self.summarize.emit(),
            "/export": lambda: self.export_conversation.emit(),
            "/help": lambda: self.show_help.emit(),
        }

        handler = handlers.get(cmd)
        if handler:
            handler()
        else:
            self.unknown_command.emit(cmd)
        return True
