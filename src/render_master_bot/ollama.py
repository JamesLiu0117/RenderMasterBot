"""Small Ollama REST client with no dependency on the optional Ollama SDK."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import httpx


class OllamaError(RuntimeError):
    """Raised when the local Ollama service cannot satisfy a request."""


EMBEDDING_BATCH_SIZE = 32


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

    def embed_texts(self, *, model: str, texts: list[str]) -> list[list[float]]:
        if not texts or any(not text.strip() for text in texts):
            raise OllamaError("embedding input must contain non-empty text")
        embeddings: list[list[float]] = []
        for start in range(0, len(texts), EMBEDDING_BATCH_SIZE):
            batch = texts[start : start + EMBEDDING_BATCH_SIZE]
            embeddings.extend(self._embed_batch(model=model, texts=batch))
        if len(embeddings) != len(texts):
            raise OllamaError("Ollama embedding response count did not match the input count")
        return embeddings

    def _embed_batch(self, *, model: str, texts: list[str]) -> list[list[float]]:
        """Embed one bounded batch so large catalogs do not overload model runners."""

        try:
            response = httpx.post(
                f"{self.base_url}/api/embed",
                json={
                    "model": model,
                    "input": texts,
                    "truncate": True,
                    "keep_alive": "5m",
                },
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:500]
            raise OllamaError(f"Ollama returned {exc.response.status_code}: {detail}") from exc
        except httpx.HTTPError as exc:
            raise OllamaError(f"Ollama embedding request failed: {exc}") from exc

        values = response.json().get("embeddings")
        if not isinstance(values, list) or len(values) != len(texts):
            raise OllamaError("Ollama embedding response count did not match the input count")
        try:
            embeddings = [[float(value) for value in vector] for vector in values]
        except (TypeError, ValueError) as exc:
            raise OllamaError("Ollama embedding response was not numeric") from exc
        dimensions = {len(vector) for vector in embeddings}
        if not dimensions or 0 in dimensions or len(dimensions) != 1:
            raise OllamaError("Ollama embedding vectors have inconsistent dimensions")
        if any(not math.isfinite(value) for vector in embeddings for value in vector):
            raise OllamaError("Ollama embedding response contains a non-finite value")
        return embeddings

    def chat_structured(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        json_schema: dict[str, Any],
        think: bool | str | None = None,
    ) -> StructuredResponse:
        payload = {
            "model": model,
            "messages": messages,
            "format": json_schema,
            "stream": False,
            "options": {"temperature": 0, "num_ctx": self.num_ctx},
        }
        if think is not None:
            payload["think"] = think
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
        if not isinstance(content, str) or not content.strip():
            message = body.get("message") or {}
            raise OllamaError(
                "Ollama returned empty message.content "
                f"(done_reason={body.get('done_reason')!r}, "
                f"thinking_present={bool(message.get('thinking'))})"
            )
        return StructuredResponse(
            content=content,
            model=body.get("model", model),
            total_duration_ns=body.get("total_duration"),
            prompt_tokens=body.get("prompt_eval_count"),
            output_tokens=body.get("eval_count"),
        )
