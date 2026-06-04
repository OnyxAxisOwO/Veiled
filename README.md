# Veiled

隐身 AI 助手客户端，从安装到运行全程伪装为 Windows 系统显示驱动辅助工具。

## 功能

- **悬浮对话窗** — 全局热键唤起，无边框半透明，不出现在任务栏和 Alt+Tab
- **截图问答** — 框选屏幕区域发送给 AI，自动返回简洁答案
- **剪贴板快捷问答** — 选中文字按热键，AI 回答以系统通知弹出
- **通知伪装** — 回答通知可伪装为 QQ / 微信 / Edge 消息
- **截屏保护** — 所有窗口对截屏和录屏不可见（`SetWindowDisplayAffinity`）
- **环境感知** — 检测到录屏、远程桌面、会议软件时自动静默
- **老板键** — 一键瞬间隐藏所有界面
- **多 AI 服务商** — 支持 Claude、OpenAI、DeepSeek 及自定义 endpoint
- **本地加密存储** — 对话记录和配置使用 Windows DPAPI 加密

## 快速开始

```bash
pip install -r requirements.txt
python run.py
```

首次运行会弹出初始化向导，配置 API Key 和快捷键后程序进入后台。

## 默认快捷键

| 功能 | 快捷键 |
|------|--------|
| 唤起/隐藏对话窗 | `Ctrl+Shift+Space` |
| 老板键 | `` Ctrl+` `` |
| 剪贴板问答 | `Ctrl+Shift+Q` |
| 截图问答 | `Ctrl+Shift+S` |
| 退出程序 | `Ctrl+Shift+Alt+Q` |

## 对话内指令

| 指令 | 功能 |
|------|------|
| `/new` | 新建对话 |
| `/list` | 对话列表 |
| `/clear` | 清除当前对话 |
| `/delete` | 删除当前对话 |
| `/model` | 切换 AI 模型 |
| `/t <文字>` | 翻译 |
| `/s` | 总结剪贴板内容 |
| `/settings` | 打开设置 |
| `/export` | 导出对话 |
| `/help` | 帮助 |

## 打包

```bash
pyinstaller build.spec
```

输出 `DAHService.exe`，伪装为 Windows Display Adapter Helper Service。

## 技术栈

Python / PyQt6 / httpx / SQLite / Windows DPAPI
