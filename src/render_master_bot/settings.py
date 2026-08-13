"""Environment-backed runtime configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _positive_int(name: str, default: int) -> int:
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return value


@dataclass(frozen=True)
class Settings:
    ollama_base_url: str
    planner_model: str
    vision_model: str
    num_ctx: int
    data_dir: Path

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
            planner_model=os.getenv("RENDERMASTER_PLANNER_MODEL", "gpt-oss:20b"),
            vision_model=os.getenv("RENDERMASTER_VISION_MODEL", "qwen3.5:9b"),
            num_ctx=_positive_int("RENDERMASTER_NUM_CTX", 8192),
            data_dir=Path(os.getenv("RENDERMASTER_DATA_DIR", Path.home() / ".render-master")),
        )
