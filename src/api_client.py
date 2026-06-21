import httpx
import json
import base64
import time as _time
from typing import Generator
from PyQt6.QtCore import pyqtSignal, QThread


class ApiWorker(QThread):
    chunk_received = pyqtSignal(str)
    finished = pyqtSignal(str)
    error = pyqtSignal(str)
    stats_ready = pyqtSignal(float, int, int)   # elapsed_s, tokens_in, tokens_out

    def __init__(self, client: "ApiClient", messages: list[dict], system_prompt: str):
        super().__init__()
        self._client = client
        self._messages = messages
        self._system_prompt = system_prompt
        self._full_response = ""

    def run(self):
        t0 = _time.monotonic()
        try:
            for chunk in self._client.stream(self._messages, self._system_prompt):
                self._full_response += chunk
                self.chunk_received.emit(chunk)
            elapsed = _time.monotonic() - t0
            self.finished.emit(self._full_response)
            self.stats_ready.emit(elapsed, self._client._input_tokens, self._client._output_tokens)
        except Exception as e:
            self.error.emit(str(e))


def embed_description(content: str, desc: str) -> str:
    """把 VLM 的识别结果拼进用户消息文本，供不带视觉的 LLM 使用。"""
    desc = (desc or "").strip()
    if not desc:
        return content or ""
    block = f"[截图视觉识别结果]\n{desc}"
    return f"{content}\n\n{block}" if content else block


class VisionPipelineWorker(QThread):
    """两阶段：VLM 识别图片(非流式) → LLM 基于识别文本回答(流式)。

    复用 ApiWorker 的信号名，聊天窗口渲染逻辑无需改动；额外提供 vlm_done。
    """
    vlm_started = pyqtSignal()
    vlm_done = pyqtSignal(str)                  # VLM 识别出的文字
    chunk_received = pyqtSignal(str)
    finished = pyqtSignal(str)
    error = pyqtSignal(str)
    stats_ready = pyqtSignal(float, int, int)   # elapsed_s, tokens_in, tokens_out

    def __init__(self, vlm_client: "ApiClient", llm_client: "ApiClient",
                 messages: list[dict], llm_system_prompt: str, vlm_prompt: str):
        super().__init__()
        self._vlm = vlm_client
        self._llm = llm_client
        self._messages = messages
        self._llm_system_prompt = llm_system_prompt
        self._vlm_prompt = vlm_prompt
        self._full_response = ""

    def run(self):
        t0 = _time.monotonic()
        try:
            image = None
            for m in reversed(self._messages):
                if m.get("image"):
                    image = m["image"]
                    break
            if image is None:
                self.error.emit("没有找到要识别的图片")
                return

            # 阶段一：VLM 识别（一次性拿完整文本）
            self.vlm_started.emit()
            desc = self._vlm.describe_image(image, self._vlm_prompt)
            if not desc.strip():
                self.error.emit("视觉模型未返回任何识别结果")
                return
            self.vlm_done.emit(desc)

            # 阶段二：把图片替换为识别文本，交给 LLM 流式回答
            llm_messages = []
            for m in self._messages:
                if m.get("image"):
                    llm_messages.append({
                        "role": m["role"],
                        "content": embed_description(m.get("content", ""), desc),
                    })
                else:
                    llm_messages.append({"role": m["role"], "content": m.get("content", "")})

            for chunk in self._llm.stream(llm_messages, self._llm_system_prompt):
                self._full_response += chunk
                self.chunk_received.emit(chunk)

            elapsed = _time.monotonic() - t0
            self.finished.emit(self._full_response)
            self.stats_ready.emit(
                elapsed,
                self._vlm._input_tokens + self._llm._input_tokens,
                self._vlm._output_tokens + self._llm._output_tokens,
            )
        except Exception as e:
            self.error.emit(str(e))


class ImageDescribeWorker(QThread):
    """单阶段：把一张图片交给视觉模型识别成文字。

    多模型并行时，用它把截图「识别一次」，再把同一段文字分发给所有不支持图片的
    模型，避免每个模型各跑一遍 VLM。"""
    done = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, vlm_client: "ApiClient", image_bytes: bytes, prompt: str):
        super().__init__()
        self._vlm = vlm_client
        self._image = image_bytes
        self._prompt = prompt

    def run(self):
        try:
            desc = self._vlm.describe_image(self._image, self._prompt)
            if not desc.strip():
                self.error.emit("视觉模型未返回任何识别结果")
                return
            self.done.emit(desc)
        except Exception as e:
            self.error.emit(str(e))


class ApiClient:
    """单个模型的 HTTP 客户端。

    kind  —— 接口协议，决定请求格式："claude"（Anthropic）或其余一律按
             OpenAI 兼容协议处理。
    supports_vision —— 该模型是否能直接接收图片输入。由调用方根据模型上的
             vision 标记传入；不支持时图片会被忽略（应走视觉识别中继）。
    """

    def __init__(self, kind: str, api_key: str, model: str, endpoint: str,
                 proxy: str = "", extra_body: dict = None, supports_vision: bool = False):
        self.kind = kind or "openai"
        self.api_key = api_key
        self.model = model
        self.endpoint = (endpoint or "").rstrip("/")
        self.proxy = proxy or None
        self._extra_body = extra_body or {}
        self._supports_vision = bool(supports_vision)
        self._input_tokens = 0
        self._output_tokens = 0

    @property
    def supports_vision(self) -> bool:
        return self._supports_vision

    def _build_headers(self) -> dict:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        }
        if self.kind == "claude":
            headers["x-api-key"] = self.api_key
            headers["anthropic-version"] = "2023-06-01"
            headers["content-type"] = "application/json"
        else:
            headers["Authorization"] = f"Bearer {self.api_key}"
            headers["Content-Type"] = "application/json"
        return headers

    def _build_body(self, messages: list[dict], system_prompt: str) -> dict:
        if self.kind == "claude":
            api_messages = []
            for m in messages:
                content = []
                if m.get("image") and self.supports_vision:
                    img_b64 = base64.b64encode(m["image"]).decode()
                    content.append({
                        "type": "image",
                        "source": {"type": "base64", "media_type": "image/png", "data": img_b64},
                    })
                if m.get("content"):
                    content.append({"type": "text", "text": m["content"]})
                api_messages.append({"role": m["role"], "content": content})
            body = {
                "model": self.model,
                "max_tokens": 4096,
                "system": system_prompt,
                "messages": api_messages,
                "stream": True,
            }
            if self._extra_body:
                body.update(self._extra_body)
            return body
        else:
            api_messages = [{"role": "system", "content": system_prompt}]
            for m in messages:
                msg = {"role": m["role"], "content": []}
                if m.get("image") and self.supports_vision:
                    img_b64 = base64.b64encode(m["image"]).decode()
                    msg["content"].append({
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{img_b64}"},
                    })
                if m.get("content"):
                    msg["content"].append({"type": "text", "text": m["content"]})
                if not msg["content"]:
                    msg["content"] = ""
                elif len(msg["content"]) == 1 and msg["content"][0]["type"] == "text":
                    msg["content"] = msg["content"][0]["text"]
                api_messages.append(msg)
            body = {
                "model": self.model,
                "messages": api_messages,
                "stream": True,
                "stream_options": {"include_usage": True},  # OpenAI 兼容接口: usage in final chunk
            }
            if self._extra_body:
                body.update(self._extra_body)
            return body

    def _get_url(self) -> str:
        if self.kind == "claude":
            return f"{self.endpoint}/v1/messages"
        else:
            return f"{self.endpoint}/v1/chat/completions"

    def _update_usage(self, obj: dict):
        if self.kind == "claude":
            if obj.get("type") == "message_start":
                usage = obj.get("message", {}).get("usage", {})
                self._input_tokens = usage.get("input_tokens", 0)
            elif obj.get("type") == "message_delta":
                usage = obj.get("usage", {})
                self._output_tokens = usage.get("output_tokens", 0)
        else:
            usage = obj.get("usage")
            if usage:
                self._input_tokens = usage.get("prompt_tokens", 0)
                self._output_tokens = usage.get("completion_tokens", 0)

    def stream(self, messages: list[dict], system_prompt: str) -> Generator[str, None, None]:
        self._input_tokens = 0
        self._output_tokens = 0
        headers = self._build_headers()
        body = self._build_body(messages, system_prompt)
        url = self._get_url()

        with httpx.Client(proxy=self.proxy, timeout=120.0) as client:
            with client.stream("POST", url, headers=headers, json=body) as response:
                if response.status_code != 200:
                    response.read()
                    raise Exception(f"API error {response.status_code}: {response.text}")
                for line in response.iter_lines():
                    if not line.startswith("data: "):
                        continue
                    data = line[6:]
                    if data == "[DONE]":
                        break
                    try:
                        obj = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    self._update_usage(obj)
                    text = self._extract_text(obj)
                    if text:
                        yield text

    def _extract_text(self, obj: dict) -> str:
        if self.kind == "claude":
            if obj.get("type") == "content_block_delta":
                delta = obj.get("delta", {})
                if delta.get("type") == "text_delta":
                    return delta.get("text", "")
        else:
            choices = obj.get("choices", [])
            if choices:
                delta = choices[0].get("delta", {})
                return delta.get("content", "") or ""
        return ""

    def list_models(self) -> tuple[list, str]:
        """Return (model_ids, error). error is empty string on success."""
        try:
            headers = self._build_headers()
            url = f"{self.endpoint}/v1/models"
            with httpx.Client(proxy=self.proxy, timeout=15.0) as client:
                resp = client.get(url, headers=headers)
                if resp.status_code != 200:
                    return [], f"HTTP {resp.status_code}: {resp.text[:200]}"
                data = resp.json()
                models = [m["id"] for m in data.get("data", []) if m.get("id")]
                return models, ""
        except Exception as e:
            return [], str(e)

    def test_connection(self) -> tuple[bool, str]:
        try:
            headers = self._build_headers()
            if self.kind == "claude":
                body = {
                    "model": self.model,
                    "max_tokens": 10,
                    "messages": [{"role": "user", "content": "hi"}],
                }
                url = f"{self.endpoint}/v1/messages"
            else:
                body = {
                    "model": self.model,
                    "messages": [{"role": "user", "content": "hi"}],
                    "max_tokens": 10,
                }
                url = f"{self.endpoint}/v1/chat/completions"
            with httpx.Client(proxy=self.proxy, timeout=30.0) as client:
                resp = client.post(url, headers=headers, json=body)
                if resp.status_code == 200:
                    return True, "连接成功"
                return False, f"HTTP {resp.status_code}: {resp.text[:200]}"
        except Exception as e:
            return False, str(e)

    def describe_image(self, image_bytes: bytes, prompt: str) -> str:
        """非流式：把单张图片交给视觉模型，收集并返回完整识别文本。"""
        messages = [{"role": "user", "content": prompt, "image": image_bytes}]
        parts = []
        for chunk in self.stream(messages, ""):
            parts.append(chunk)
        return "".join(parts)

    def create_worker(self, messages: list[dict], system_prompt: str) -> "ApiWorker":
        return ApiWorker(self, messages, system_prompt)
