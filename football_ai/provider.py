"""Volcengine Ark provider used only for FAE narrative generation."""

from __future__ import annotations

import os
import json
import time
from typing import Any, Dict, Optional, Tuple

import requests


class FAEError(RuntimeError):
    """Base Football AI Engine error."""


class FAEConfigurationError(FAEError):
    """Raised when an engine dependency is not configured."""


class FAEProviderError(FAEError):
    """Raised when the narrative provider fails."""


class FAEOutputError(FAEError):
    """Raised when provider output cannot be validated."""


class ArkNarrativeClient:
    """Small Ark client. Core FAE calculations never depend on this client."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout: Optional[int] = None,
        stream: bool = False,
        max_tokens: Optional[int] = None,
        thinking: Optional[str] = None,
        json_mode: bool = False,
    ):
        self.api_key = api_key or os.getenv("ARK_API_KEY", "")
        self.base_url = (
            base_url
            or os.getenv("ARK_BASE_URL")
            or "https://ark.cn-beijing.volces.com/api/coding/v3"
        ).rstrip("/")
        self.model = model or os.getenv("ARK_MODEL") or "ark-code-latest"
        configured_mode = str(os.getenv("ARK_API_MODE") or "").strip().lower()
        self.api_mode = configured_mode or (
            "chat_completions" if "/api/coding/" in self.base_url else "responses"
        )
        if self.api_mode not in {"responses", "chat_completions"}:
            raise FAEConfigurationError(
                "ARK_API_MODE 只支持 responses 或 chat_completions"
            )
        self.timeout = timeout or int(os.getenv("AI_REQUEST_TIMEOUT", "90"))
        self.stream = bool(stream)
        configured_max_tokens = (
            max_tokens
            if max_tokens is not None
            else int(os.getenv("AI_MAX_OUTPUT_TOKENS", "0"))
        )
        self.max_tokens = max(0, int(configured_max_tokens or 0))
        self.thinking = str(thinking or "").strip().lower()
        self.json_mode = bool(json_mode)
        if self.thinking not in {"", "enabled", "disabled", "auto"}:
            raise FAEConfigurationError(
                "thinking 只支持 enabled、disabled 或 auto"
            )
        self.max_retries = max(0, int(os.getenv("AI_MAX_RETRIES", "1")))

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.model and self.base_url)

    def generate(self, prompt: str) -> Tuple[str, Dict[str, Any]]:
        if not self.configured:
            raise FAEConfigurationError("火山方舟尚未配置 ARK_API_KEY 或 ARK_MODEL")

        if self.api_mode == "chat_completions":
            url = f"{self.base_url}/chat/completions"
            payload = {
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
            }
            if self.stream:
                payload["stream"] = True
                payload["stream_options"] = {"include_usage": True}
            if self.json_mode:
                payload["response_format"] = {"type": "json_object"}
        else:
            url = f"{self.base_url}/responses"
            payload = {"model": self.model, "input": prompt}
            if self.stream:
                payload["stream"] = True
        if self.max_tokens:
            payload["max_tokens"] = self.max_tokens
        if self.thinking:
            payload["thinking"] = {"type": self.thinking}

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        last_error: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            try:
                response = requests.post(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=self.timeout,
                    stream=self.stream,
                )
                if response.status_code >= 400:
                    message = self._provider_message(response)
                    error = FAEProviderError(
                        f"火山方舟请求失败({response.status_code}): {message}"
                    )
                    if (response.status_code == 429 or response.status_code >= 500) and attempt < self.max_retries:
                        last_error = error
                        time.sleep(1.5 * (attempt + 1))
                        continue
                    raise error
                if self.stream:
                    return self._stream_response(response)
                data = response.json()
                return self._response_text(data), self._response_metadata(data)
            except (requests.RequestException, ValueError) as exc:
                last_error = exc
                if attempt < self.max_retries:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                break
        raise FAEProviderError(f"火山方舟请求异常: {last_error}")

    @classmethod
    def _stream_response(
        cls, response: requests.Response
    ) -> Tuple[str, Dict[str, Any]]:
        parts = []
        response_id = None
        usage: Dict[str, Any] = {}
        # Coding gateway's event-stream response omits a UTF-8 charset, so
        # requests may otherwise decode Chinese as ISO-8859-1 mojibake.
        for raw_line in response.iter_lines(decode_unicode=False):
            line = (
                raw_line.decode("utf-8", errors="replace")
                if isinstance(raw_line, bytes)
                else str(raw_line or "")
            ).strip()
            if not line or line.startswith(":"):
                continue
            if line.startswith("data:"):
                line = line[5:].strip()
            if line == "[DONE]":
                break
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            response_id = data.get("id") or response_id
            if isinstance(data.get("usage"), dict):
                usage.update({
                    key: value for key, value in data["usage"].items()
                    if isinstance(value, (int, float))
                })
            choices = data.get("choices") or []
            if choices and isinstance(choices[0], dict):
                delta = choices[0].get("delta") or {}
                content = delta.get("content")
                if isinstance(content, str):
                    parts.append(content)
                message = choices[0].get("message") or {}
                if not parts and isinstance(message.get("content"), str):
                    parts.append(message["content"])
            if isinstance(data.get("output_text"), str):
                parts.append(data["output_text"])
        text = "".join(parts).strip()
        if not text:
            raise FAEOutputError("火山方舟流式响应中没有可用文本")
        return text, {
            "response_id": response_id,
            "usage": usage,
            "streamed": True,
        }

    @staticmethod
    def _provider_message(response: requests.Response) -> str:
        try:
            data = response.json()
            error = data.get("error") or {}
            return str(error.get("message") or data.get("message") or "未知错误")[:300]
        except ValueError:
            return response.text[:300] or "未知错误"

    @staticmethod
    def _response_text(data: Dict[str, Any]) -> str:
        if isinstance(data.get("output_text"), str) and data["output_text"].strip():
            return data["output_text"].strip()
        parts = []
        for item in data.get("output") or []:
            if not isinstance(item, dict):
                continue
            for content in item.get("content") or []:
                if isinstance(content, dict):
                    value = content.get("text") or content.get("output_text")
                    if isinstance(value, str):
                        parts.append(value)
        if parts:
            return "\n".join(parts).strip()
        choices = data.get("choices") or []
        if choices and isinstance(choices[0], dict):
            message = choices[0].get("message") or {}
            if isinstance(message.get("content"), str):
                return message["content"].strip()
        raise FAEOutputError("火山方舟响应中没有可用文本")

    @staticmethod
    def _response_metadata(data: Dict[str, Any]) -> Dict[str, Any]:
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        return {
            "response_id": data.get("id"),
            "usage": {
                key: value for key, value in usage.items()
                if isinstance(value, (int, float))
            },
        }
