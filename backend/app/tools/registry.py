from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from app.core.config import settings
from app.core.telemetry import TOOL_CALLS, span


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    risk_level: str
    read_only: bool
    reversible: bool
    parameters: dict[str, Any]
    handler: Callable[..., Any]

    def as_openai(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec:
        if name not in self._tools:
            raise KeyError(name)
        return self._tools[name]

    def list(self, *, read_only_only: bool = False) -> list[ToolSpec]:
        tools = list(self._tools.values())
        return [t for t in tools if (t.read_only or not read_only_only)]

    def schemas(self, *, read_only_only: bool = False) -> list[dict[str, Any]]:
        return [t.as_openai() for t in self.list(read_only_only=read_only_only)]


    @staticmethod
    def _validate_arguments(spec: ToolSpec, kwargs: dict[str, Any]) -> None:
        schema = spec.parameters or {}
        props = set((schema.get("properties") or {}).keys())
        required = set(schema.get("required") or [])
        unknown = set(kwargs) - props
        missing = required - set(kwargs)
        if unknown:
            raise ValueError(f"Unexpected arguments for {spec.name}: {sorted(unknown)}")
        if missing:
            raise ValueError(f"Missing arguments for {spec.name}: {sorted(missing)}")
        for key, value in kwargs.items():
            expected = (schema.get("properties") or {}).get(key, {}).get("type")
            if expected == "string" and not isinstance(value, str):
                raise ValueError(f"{spec.name}.{key} must be a string")
            if expected == "integer" and not isinstance(value, int):
                raise ValueError(f"{spec.name}.{key} must be an integer")

    def execute(self, name: str, *, approved: bool = False, **kwargs):
        spec = self.get(name)
        try:
            self._validate_arguments(spec, kwargs)
        except ValueError as exc:
            TOOL_CALLS.labels(spec.name, spec.risk_level, "invalid_args").inc()
            return {"success": False, "error": str(exc), "tool": name}
        if spec.risk_level != "READ_ONLY" and settings.dry_run and not approved:
            result = {"success": False, "dry_run": True, "message": f"{name} blocked because DRY_RUN=true"}
            TOOL_CALLS.labels(spec.name, spec.risk_level, "blocked").inc()
            return result
        try:
            with span("tool.execute", {"tool.name": spec.name, "tool.risk": spec.risk_level}):
                result = spec.handler(**kwargs)
            TOOL_CALLS.labels(spec.name, spec.risk_level, "success").inc()
            return result
        except Exception as exc:
            TOOL_CALLS.labels(spec.name, spec.risk_level, "error").inc()
            return {"success": False, "error": str(exc), "tool": name}
