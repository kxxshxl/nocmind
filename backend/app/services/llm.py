from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, ClassVar

import httpx

from app.core.config import settings
from app.core.telemetry import LLM_CALLS, LLM_LATENCY, LLM_TOKENS, span

log = logging.getLogger("nocmind.llm")


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)
    model: str = ""
    provider: str = ""


class LLMError(RuntimeError):
    """Raised when every configured provider failed or was skipped."""


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    kind: str  # demo | ollama | openai
    url: str
    model: str
    api_key: str = ""
    api_style: str = "openai"


def provider_config(name: str) -> ProviderConfig:
    name = (name or "demo").strip().lower()
    if name == "demo":
        return ProviderConfig("demo", "demo", "", "demo")
    if name == "ollama":
        return ProviderConfig("ollama", "ollama", settings.ollama_base_url.rstrip("/") + "/api/chat", settings.ollama_model)
    return ProviderConfig(name, "openai", settings.llm_base_url, settings.llm_model, settings.llm_api_key, settings.llm_api_style.lower())


def _is_transient(exc: Exception) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 429 or exc.response.status_code >= 500
    return isinstance(exc, httpx.TransportError)


def _error_status(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429:
        return "rate_limited"
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    return "error"


class LLMClient:
    """Provider-neutral chat client: Ollama (native API), OpenAI-compatible endpoints, or offline demo mode.

    Each request is attempted at most once per provider. Transient failures (429, 5xx, timeouts, connection
    errors) put that provider into a cooldown so a benchmark does not keep hammering a rate-limited API.
    """

    _cooldown_until: ClassVar[dict[str, float]] = {}

    def __init__(self, provider: str | None = None, fallback: str | None = None):
        primary = provider_config(provider or settings.llm_provider)
        fallback_name = settings.llm_fallback_provider if fallback is None else fallback
        self.providers = [primary]
        if fallback_name and fallback_name.lower() != primary.name:
            self.providers.append(provider_config(fallback_name))
        self.provider = primary.name
        self.last_provider = primary.name
        self.last_model = primary.model
        self.last_usage: dict[str, int] = {}
        self.last_latency_ms = 0.0
        self.last_status = "success"
        self.last_error = ""

    @property
    def is_demo(self) -> bool:
        return self.providers[0].kind == "demo"

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        json_mode: bool = False,
    ) -> LLMResponse:
        errors: list[str] = []
        for cfg in self.providers:
            if cfg.kind == "demo":
                return self._demo(messages, tools)
            if not cfg.url:
                errors.append(f"{cfg.name}: endpoint not configured")
                continue
            if self._cooldown_until.get(cfg.name, 0) > time.monotonic():
                LLM_CALLS.labels(cfg.name, cfg.model, "skipped_cooldown").inc()
                errors.append(f"{cfg.name}: in cooldown after a recent failure")
                continue
            started = time.perf_counter()
            try:
                with span("llm.chat", {"llm.provider": cfg.name, "llm.model": cfg.model, "llm.tools": len(tools or [])}):
                    if cfg.kind == "ollama":
                        response = await self._call_ollama(cfg, messages, tools, json_mode)
                    else:
                        response = await self._call_openai(cfg, messages, tools)
            except Exception as exc:
                self.last_latency_ms = (time.perf_counter() - started) * 1000
                status = _error_status(exc)
                LLM_CALLS.labels(cfg.name, cfg.model, status).inc()
                if _is_transient(exc):
                    self._cooldown_until[cfg.name] = time.monotonic() + settings.llm_cooldown_seconds
                detail = f"{cfg.name}: {type(exc).__name__}: {str(exc)[:200]}"
                log.warning("LLM call failed", extra={"extra_data": {"provider": cfg.name, "status": status, "error": detail}})
                errors.append(detail)
                continue
            self.last_latency_ms = (time.perf_counter() - started) * 1000
            self._cooldown_until.pop(cfg.name, None)
            LLM_LATENCY.labels(cfg.name).observe(self.last_latency_ms / 1000)
            LLM_CALLS.labels(cfg.name, cfg.model, "success").inc()
            LLM_TOKENS.labels(cfg.name, "input").inc(response.usage.get("input_tokens", 0))
            LLM_TOKENS.labels(cfg.name, "output").inc(response.usage.get("output_tokens", 0))
            self.last_provider, self.last_model, self.last_usage = cfg.name, cfg.model, response.usage
            self.last_status, self.last_error = "success", ""
            return response
        self.last_status, self.last_error, self.last_usage = "error", "; ".join(errors), {}
        raise LLMError(self.last_error or "no LLM provider available")

    async def generate_json(self, system: str, user: str, schema_hint: dict[str, Any] | None = None) -> dict[str, Any]:
        hint = ""
        if schema_hint:
            hint = ("\nReturn only a JSON object with exactly these keys, filling in real values of the given types: "
                    + json.dumps(_describe_shape(schema_hint)))
        response = await self.chat(
            [{"role": "system", "content": system + hint}, {"role": "user", "content": user}],
            json_mode=True,
        )
        if response.provider == "demo":
            return {}
        return parse_json_object(response.content)

    async def _call_openai(self, cfg: ProviderConfig, messages, tools) -> LLMResponse:
        payload: dict[str, Any] = {"model": cfg.model, "messages": messages, "temperature": settings.llm_temperature}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        headers = {"Content-Type": "application/json"}
        if cfg.api_key:
            if cfg.api_style == "azure":
                headers["api-key"] = cfg.api_key
            else:
                headers["Authorization"] = f"Bearer {cfg.api_key}"
        async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
            response = await client.post(cfg.url, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
        message = data.get("choices", [{}])[0].get("message", {})
        usage = data.get("usage") or {}
        return LLMResponse(
            content=str(message.get("content") or ""),
            tool_calls=_parse_tool_calls(message.get("tool_calls")),
            usage={"input_tokens": int(usage.get("prompt_tokens", 0) or 0), "output_tokens": int(usage.get("completion_tokens", 0) or 0)},
            model=cfg.model,
            provider=cfg.name,
        )

    async def _call_ollama(self, cfg: ProviderConfig, messages, tools, json_mode: bool) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": cfg.model,
            "messages": _to_ollama_messages(messages),
            "stream": False,
            "think": settings.ollama_think,
            "options": {"temperature": settings.llm_temperature, "num_ctx": settings.ollama_num_ctx},
        }
        if tools:
            payload["tools"] = tools
        elif json_mode:
            payload["format"] = "json"
        async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
            response = await client.post(cfg.url, json=payload)
            response.raise_for_status()
            data = response.json()
        message = data.get("message") or {}
        content = re.sub(r"<think>.*?</think>", "", str(message.get("content") or ""), flags=re.S).strip()
        return LLMResponse(
            content=content,
            tool_calls=_parse_tool_calls(message.get("tool_calls")),
            usage={"input_tokens": int(data.get("prompt_eval_count", 0) or 0), "output_tokens": int(data.get("eval_count", 0) or 0)},
            model=cfg.model,
            provider=cfg.name,
        )

    def _demo(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None) -> LLMResponse:
        """Deterministic offline mode. It exercises the same tool-calling contract for demos/tests."""
        text = "\n".join(str(m.get("content", "")) for m in messages)
        lower = text.lower()
        tool_calls: list[ToolCall] = []
        if tools:
            available = {t["function"]["name"] for t in tools}
            if "packet loss" in lower or "router" in lower or "congestion" in lower:
                plan = [
                    ("get_network_metrics", {}),
                    ("query_logs", {"service": "API Gateway", "region": "Mumbai"}),
                    ("get_topology", {"component": "API Gateway"}),
                    ("search_runbooks", {"query": "router overload packet loss high latency"}),
                    ("search_historical_incidents", {"query": "high latency packet loss router overload", "region": "Mumbai", "service": "API Gateway"}),
                ]
            elif "dns" in lower:
                plan = [
                    ("get_network_metrics", {"device": "dns-mum-01"}),
                    ("query_logs", {"service": "API Gateway", "region": "Mumbai"}),
                    ("search_runbooks", {"query": "DNS resolver failure timeout latency"}),
                    ("search_historical_incidents", {"query": "DNS timeout high latency", "region": "Mumbai", "service": "API Gateway"}),
                ]
            elif "database" in lower or "query latency" in lower:
                plan = [
                    ("get_network_metrics", {"device": "db-mum-01"}),
                    ("query_logs", {"service": "API Gateway", "region": "Mumbai"}),
                    ("search_runbooks", {"query": "database latency slow queries connection saturation"}),
                    ("search_historical_incidents", {"query": "database latency slow queries", "region": "Mumbai", "service": "API Gateway"}),
                ]
            else:
                plan = [
                    ("get_service_health", {"service": "API Gateway"}),
                    ("query_logs", {"service": "API Gateway", "region": "Mumbai"}),
                    ("check_dependencies", {"service": "API Gateway"}),
                    ("search_runbooks", {"query": "API service failure"}),
                ]
            tool_calls = [ToolCall(f"demo-{i}", name, args) for i, (name, args) in enumerate(plan) if name in available]
        self.last_provider = self.last_model = "demo"
        self.last_latency_ms = 0.0
        self.last_status, self.last_error = "success", ""
        self.last_usage = {"input_tokens": max(1, len(text) // 4), "output_tokens": 32}
        return LLMResponse(content="Offline agent selected evidence-gathering tools.", tool_calls=tool_calls, usage=self.last_usage, model="demo", provider="demo")


def _describe_shape(value: Any) -> Any:
    """Turn an example payload into a type description so small models do not echo empty example values."""
    if isinstance(value, dict):
        return {k: _describe_shape(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_describe_shape(value[0])] if value else ["<string>"]
    if isinstance(value, bool):
        return "<true|false>"
    if isinstance(value, (int, float)):
        return "<number>"
    return "<string>"


def _parse_tool_calls(raw_calls: Any) -> list[ToolCall]:
    calls: list[ToolCall] = []
    for tc in raw_calls or []:
        fn = tc.get("function", {}) if isinstance(tc, dict) else {}
        raw = fn.get("arguments", {})
        try:
            args = json.loads(raw) if isinstance(raw, str) else raw
        except json.JSONDecodeError:
            args = {}
        if not isinstance(args, dict):
            args = {}
        calls.append(ToolCall(id=str(tc.get("id") or f"call-{len(calls)}"), name=str(fn.get("name", "")), arguments=args))
    return calls


def _to_ollama_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert OpenAI-style messages: Ollama wants tool-call arguments as objects and tool results named."""
    names_by_id: dict[str, str] = {}
    converted = []
    for m in messages:
        m = dict(m)
        if m.get("tool_calls"):
            calls = []
            for tc in m["tool_calls"]:
                fn = tc.get("function", {})
                args = fn.get("arguments", {})
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {}
                names_by_id[str(tc.get("id"))] = fn.get("name", "")
                calls.append({"function": {"name": fn.get("name", ""), "arguments": args}})
            m["tool_calls"] = calls
        if m.get("role") == "tool":
            m["tool_name"] = names_by_id.get(str(m.pop("tool_call_id", "")), "")
        converted.append(m)
    return converted


def parse_json_object(text: str) -> dict[str, Any]:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I | re.S).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("LLM response did not contain a JSON object")
    return json.loads(text[start:end + 1])
