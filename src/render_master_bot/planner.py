"""Natural-language to RenderSpec planning boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from pydantic import ValidationError

from render_master_bot.models import RenderSpec
from render_master_bot.ollama import StructuredResponse
from render_master_bot.schemas import ollama_generation_schema


SYSTEM_PROMPT = """You are the scene planner for RenderMasterBot.
Return exactly one RenderSpec JSON object matching the provided schema.
Use centimeters for locations and degrees for rotations.
Use Unreal coordinates: +Z is up. Keep ordinary scene objects and local lights above the floor.
Use stable lowercase snake_case identifiers.
Do not emit source code, prose, Markdown, or fields not present in the schema.
If a request is ambiguous, choose a conservative default and record it in notes.
Only reference asset IDs explicitly listed in the available asset catalog.
When the request names a subject with a matching available asset, instantiate that asset in objects.
Use physical light units: directional lights use lux; point, spot, and rect lights use lumens or candelas.
Represent RGB colors as normalized decimal values from 0.0 to 1.0, never as 0-255 integers.
"""


class StructuredChatClient(Protocol):
    def chat_structured(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        json_schema: dict,
    ) -> StructuredResponse: ...


class PlanningError(RuntimeError):
    """Raised when a model response is not a valid RenderSpec."""

    def __init__(self, message: str, response: StructuredResponse | None = None):
        super().__init__(message)
        self.response = response


@dataclass(frozen=True)
class PlanResult:
    spec: RenderSpec
    response: StructuredResponse


class ScenePlanner:
    def __init__(self, client: StructuredChatClient):
        self.client = client

    def plan(self, *, model: str, prompt: str, asset_ids: list[str] | None = None) -> PlanResult:
        assets = asset_ids or []
        catalog = ", ".join(assets) if assets else "(empty; create no scene objects)"
        user_message = f"Available asset IDs: {catalog}\n\nUser request:\n{prompt.strip()}"
        response = self.client.chat_structured(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            json_schema=ollama_generation_schema(),
        )
        try:
            spec = RenderSpec.model_validate_json(response.content)
        except ValidationError as exc:
            raise PlanningError(
                f"model returned an invalid RenderSpec:\n{exc}",
                response=response,
            ) from exc
        allowed_assets = set(assets)
        used_assets = {item.asset.asset_id for item in spec.objects}
        unavailable_assets = sorted(used_assets - allowed_assets)
        if unavailable_assets:
            raise PlanningError(
                "model referenced assets outside the available catalog: "
                + ", ".join(unavailable_assets),
                response=response,
            )
        return PlanResult(spec=spec, response=response)
