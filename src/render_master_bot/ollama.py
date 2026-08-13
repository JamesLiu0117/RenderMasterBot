"""Small Ollama REST client with no dependency on the optional Ollama SDK."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx


class OllamaError(RuntimeError):
    """Raised when the local Ollama service cannot satisfy a request."""


@dataclass(frozen=True)
class StructuredResponse:
    content: str
    model: str
    total_duration_ns: int | None = None
    prompt_tokens: int | None = None
    output_tokens: int | None = None


class OllamaClient:
    def __init__(
        self,
        base_url: str = "http://127.0.0.1:11434",
        timeout_seconds: float = 300.0,
        num_ctx: int = 8192,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.num_ctx = num_ctx

    def list_models(self) -> list[str]:
        try:
            response = httpx.get(f"{self.base_url}/api/tags", timeout=10.0)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise OllamaError(f"cannot reach Ollama at {self.base_url}: {exc}") from exc
        return [item["name"] for item in response.json().get("models", [])]

    def chat_structured(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        json_schema: dict[str, Any],
    ) -> StructuredResponse:
        payload = {
            "model": model,
            "messages": messages,
            "format": json_schema,
            "stream": False,
            "options": {"temperature": 0, "num_ctx": self.num_ctx},
        }
        try:
            response = httpx.post(
                f"{self.base_url}/api/chat",
                json=payload,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:500]
            raise OllamaError(f"Ollama returned {exc.response.status_code}: {detail}") from exc
        except httpx.HTTPError as exc:
            raise OllamaError(f"Ollama request failed: {exc}") from exc

        body = response.json()
        try:
            content = body["message"]["content"]
        except (KeyError, TypeError) as exc:
            raise OllamaError("Ollama response did not contain message.content") from exc
        return StructuredResponse(
            content=content,
            model=body.get("model", model),
            total_duration_ns=body.get("total_duration"),
            prompt_tokens=body.get("prompt_eval_count"),
            output_tokens=body.get("eval_count"),
        )
