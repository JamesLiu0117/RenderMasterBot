"""Natural-language to RenderSpec planning boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import ValidationError

from render_master_bot.models import RenderSpec
from render_master_bot.ollama import StructuredResponse
from render_master_bot.schemas import ollama_generation_schema


SYSTEM_PROMPT = """You are the scene planner for RenderMasterBot.
Return exactly one RenderSpec JSON object matching the provided schema.
Use centimeters for locations and degrees for rotations.
Use Unreal coordinates: +Z is up. Keep ordinary scene objects and local lights above the floor.
Map rotation_deg axes to Unreal exactly: x is roll, y is pitch, and z is yaw.
Use stable lowercase snake_case identifiers.
Do not emit source code, prose, Markdown, or fields not present in the schema.
If a request is ambiguous, choose a conservative default and record it in notes.
Only reference asset IDs explicitly listed in the available asset catalog.
When the request names a subject with a matching available asset, instantiate that asset in objects.
Instantiate only scene-object assets whose supplied context contains Dimensions cm evidence. Assets
without dimensions cannot be framed or executed safely; do not use them as background geometry.
Use material assets only in an object's materials list. Target the mesh's listed material slot name.
Never use a static mesh, texture, or other asset type as a material.
Treat semantic retrieval as candidates, not proof of suitability. Assign a requested material only
when its supplied name, description, or tags support the requested appearance. Otherwise leave the
materials list empty and record the missing material asset in notes.
Use physical light units. Directional lights use lux; point, spot, and rect lights use lumens or
candelas.
Represent RGB colors as normalized decimal values from 0.0 to 1.0, never as 0-255 integers.
Use fixed exposure for repeatable previews unless the request explicitly requires adaptation. A
conservative studio default is {"mode":"fixed","fixed_ev100":12.0}. For automatic exposure, use
{"mode":"auto"} and omit fixed_ev100.
"""

FORMAT_RETRY_PROMPT = """The previous RenderSpec was rejected by validation:
{validation_error}

Return one complete corrected RenderSpec JSON object. Preserve the user's request and only use the
listed assets. Do not include Markdown, commentary, or the validation error in the JSON.
"""


class StructuredChatClient(Protocol):
    def chat_structured(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        json_schema: dict,
    ) -> StructuredResponse: ...


class PlanningError(RuntimeError):
    """Raised when a model response is not a valid RenderSpec."""

    def __init__(
        self,
        message: str,
        response: StructuredResponse | None = None,
        attempt_count: int = 1,
    ):
        super().__init__(message)
        self.response = response
        self.attempt_count = attempt_count


@dataclass(frozen=True)
class PlanResult:
    spec: RenderSpec
    response: StructuredResponse
    attempt_count: int = 1


class ScenePlanner:
    def __init__(self, client: StructuredChatClient):
        self.client = client

    def plan(
        self,
        *,
        model: str,
        prompt: str,
        asset_ids: list[str] | None = None,
        asset_context: list[str] | None = None,
    ) -> PlanResult:
        assets = asset_ids or []
        if asset_context:
            catalog = "\n".join(f"- {value}" for value in asset_context)
        elif assets:
            catalog = "\n".join(f"- {asset_id}" for asset_id in assets)
        else:
            catalog = "(empty; create no scene objects)"
        user_message = (
            "Available asset catalog (only the listed asset IDs may be used):\n"
            f"{catalog}\n\nUser request:\n{prompt.strip()}"
        )
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ]
        response = self.client.chat_structured(
            model=model,
            messages=messages,
            json_schema=ollama_generation_schema(),
        )
        attempt_count = 1
        try:
            spec = RenderSpec.model_validate_json(response.content)
        except ValidationError as first_error:
            retry_prompt = FORMAT_RETRY_PROMPT.format(
                validation_error=str(first_error)[:2000]
            )
            response = self.client.chat_structured(
                model=model,
                messages=[
                    *messages,
                    {"role": "assistant", "content": response.content},
                    {"role": "user", "content": retry_prompt},
                ],
                json_schema=ollama_generation_schema(),
            )
            attempt_count = 2
            try:
                spec = RenderSpec.model_validate_json(response.content)
            except ValidationError as exc:
                raise PlanningError(
                    "model returned an invalid RenderSpec after one format retry:\n"
                    f"{exc}",
                    response=response,
                    attempt_count=attempt_count,
                ) from exc
        allowed_assets = set(assets)
        used_assets = {item.asset.asset_id for item in spec.objects}
        used_assets.update(
            assignment.material.asset_id
            for item in spec.objects
            for assignment in item.materials
        )
        unavailable_assets = sorted(used_assets - allowed_assets)
        if unavailable_assets:
            raise PlanningError(
                "model referenced assets outside the available catalog: "
                + ", ".join(unavailable_assets),
                response=response,
                attempt_count=attempt_count,
            )
        return PlanResult(
            spec=spec,
            response=response,
            attempt_count=attempt_count,
        )
