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


class ApiClient:
    # DeepSeek 标准 chat 接口为纯文本，不接受 image_url 块。
    VISION_PROVIDERS = {"claude", "openai", "custom"}

    def __init__(self, provider: str, api_key: str, model: str, endpoint: str, proxy: str = ""):
        self.provider = provider
        self.api_key = api_key
        self.model = model
        self.endpoint = endpoint.rstrip("/")
        self.proxy = proxy or None
        self._input_tokens = 0
        self._output_tokens = 0

    @property
    def supports_vision(self) -> bool:
        return self.provider in self.VISION_PROVIDERS

    def _build_headers(self) -> dict:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        }
        if self.provider == "claude":
            headers["x-api-key"] = self.api_key
            headers["anthropic-version"] = "2023-06-01"
            headers["content-type"] = "application/json"
        else:
            headers["Authorization"] = f"Bearer {self.api_key}"
            headers["Content-Type"] = "application/json"
        return headers

    def _build_body(self, messages: list[dict], system_prompt: str) -> dict:
        if self.provider == "claude":
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
            return {
                "model": self.model,
                "max_tokens": 4096,
                "system": system_prompt,
                "messages": api_messages,
                "stream": True,
            }
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
            return {
                "model": self.model,
                "messages": api_messages,
                "stream": True,
                "stream_options": {"include_usage": True},  # OpenAI/DeepSeek: usage in final chunk
            }

    def _get_url(self) -> str:
        if self.provider == "claude":
            return f"{self.endpoint}/v1/messages"
        else:
            return f"{self.endpoint}/v1/chat/completions"

    def _update_usage(self, obj: dict):
        if self.provider == "claude":
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
        if self.provider == "claude":
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

    def test_connection(self) -> tuple[bool, str]:
        try:
            headers = self._build_headers()
            if self.provider == "claude":
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

    def create_worker(self, messages: list[dict], system_prompt: str) -> "ApiWorker":
        return ApiWorker(self, messages, system_prompt)
