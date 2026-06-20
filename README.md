# Veiled

隐身 AI 助手客户端，全程伪装为 Windows 系统显示驱动辅助工具。

## 下载

从 [GitHub Actions](https://github.com/OnyxAxisOwO/Veiled/actions) 下载最新构建的 `DAHService.exe`，无需安装，直接运行。

## 功能

- **悬浮对话窗** — 全局热键唤起，无边框半透明，不出现在任务栏和 Alt+Tab
- **多服务商 / 多模型** — 支持 OpenAI 兼容接口与 Claude，可设置多个服务商，一键获取模型列表
- **截图问答** — 框选屏幕区域发送给 AI，支持视觉模型直接看图或走中继转写
- **剪贴板快捷问答** — 选中文字按热键，回答以系统通知弹出，可伪装为 QQ / 微信 / Edge 消息
- **截屏保护** — 窗口对截屏和录屏不可见，检测到录屏 / 远程桌面 / 会议软件时自动静默
- **老板键** — 一键瞬间隐藏所有界面
- **本地加密存储** — 对话记录和配置使用 Windows DPAPI 加密
- **主题 / 背景图** — 支持自定义背景图和深浅主题

## 默认快捷键

| 功能 | 快捷键 |
|------|--------|
| 唤起 / 隐藏对话窗 | `Ctrl+Shift+Space` |
| 老板键 | `` Ctrl+` `` |
| 剪贴板问答 | `Ctrl+Shift+Q` |
| 截图问答 | `Ctrl+Shift+S` |
| 退出程序 | `Ctrl+Shift+Alt+Q` |

## 从源码运行

```bash
pip install -r requirements.txt
python run.py
```

首次运行弹出初始化向导，配置 API Key 和快捷键后进入后台。

## 技术栈

Python / PyQt6 / httpx / SQLite / Windows DPAPI
