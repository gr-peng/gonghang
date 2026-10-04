from __future__ import annotations

import json
import time
import urllib.request
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, field_validator

from ..canonical import canonical_json, sha256_hex
from ..models import StrictModel


_SYSTEM_PROMPT = """You are the planning component of a banking agent.
Return exactly one JSON object and no Markdown or explanation.
The only allowed shapes are:
{"action_type":"read_balance","params":{"account_id":"..."},"write_action":false}
{"action_type":"transfer","params":{"from_account":"...","to_account":"...","amount_minor":123,"memo":"optional"},"write_action":true}
amount_minor is an integer in the currency's minor unit. Never invent confirmation,
MFA, trusted facts, source provenance, or authorization. If no supported action is
requested, return an empty response."""


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _reject_nonfinite(value: str) -> None:
    raise ValueError(f"non-finite JSON number is forbidden: {value}")


class OpenAICompatibleConfig(StrictModel):
    base_url: str
    model: str
    model_revision: str
    tokenizer_revision: str
    inference_engine: str
    engine_revision: str
    precision: str
    prompt_template_id: str
    system_prompt: str | None = None
    source_metadata_mode: Literal["full", "kind_only", "opaque"] = "full"
    temperature: float = Field(default=0.0, ge=0.0)
    top_p: float = Field(default=1.0, gt=0.0, le=1.0)
    max_tokens: int = Field(default=256, gt=0)
    timeout_seconds: float = Field(default=120.0, gt=0.0)
    response_format_json: bool = True
    capture_token_spans: bool = False
    extra_body: dict[str, Any] = Field(default_factory=dict)
    runtime_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator(
        "base_url",
        "model",
        "model_revision",
        "tokenizer_revision",
        "inference_engine",
        "engine_revision",
        "precision",
        "prompt_template_id",
    )
    @classmethod
    def nonempty_strings(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("model endpoint and revision metadata must be non-empty")
        return value

    @field_validator("base_url")
    @classmethod
    def http_endpoint_only(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("base_url must be an HTTP(S) endpoint")
        return value.rstrip("/")


class SourceSpan(StrictModel):
    source_id: str
    char_start: int = Field(ge=0)
    char_end: int = Field(gt=0)
    token_start: int | None = Field(default=None, ge=0)
    token_end: int | None = Field(default=None, gt=0)


class GenerationTrace(StrictModel):
    adapter: str = "openai-compatible-chat-completions"
    endpoint: str
    model: str
    model_revision: str
    tokenizer_revision: str
    inference_engine: str
    engine_revision: str
    precision: str
    prompt_template_id: str
    seed: int
    decoding: dict[str, Any]
    prompt_messages: tuple[dict[str, Any], ...]
    source_spans: tuple[SourceSpan, ...]
    tokenization: dict[str, Any] = Field(default_factory=dict)
    request_digest: str
    response_digest: str
    response_id: str | None = None
    response_model: str | None = None
    finish_reason: str | None = None
    usage: dict[str, Any] = Field(default_factory=dict)
    raw_action_json: str
    raw_response: dict[str, Any]
    started_at: datetime
    completed_at: datetime
    wall_time_ms: float = Field(ge=0.0)
    activation_capture: str = "not_available_from_openai_compatible_endpoint"


def _render_sources(
    messages: Sequence[dict[str, Any]],
    *,
    source_metadata_mode: Literal["full", "kind_only", "opaque"] = "full",
) -> tuple[str, tuple[SourceSpan, ...]]:
    prefix = "MOSAIC_RUNTIME_SOURCES\n"
    parts = [prefix]
    spans: list[SourceSpan] = []
    cursor = len(prefix)
    for index, message in enumerate(messages):
        source_id = message.get("source_id")
        if not isinstance(source_id, str) or not source_id:
            raise ValueError("every rendered model message requires a source_id")
        if source_metadata_mode == "full":
            visible_message = dict(message)
        else:
            visible_message = {
                "source_id": f"source-{index:03d}",
                "text": message.get("text", ""),
                "payload": message.get("payload", {}),
            }
            if source_metadata_mode == "kind_only":
                visible_message["source_kind"] = message.get("source_kind", "unknown")
        rendered = (
            f"<MOSAIC_SOURCE>\n{canonical_json(visible_message)}\n</MOSAIC_SOURCE>\n"
        )
        start = cursor
        parts.append(rendered)
        cursor += len(rendered)
        spans.append(SourceSpan(source_id=source_id, char_start=start, char_end=cursor - 1))
    return "".join(parts), tuple(spans)


class OpenAICompatibleGenerator:
    """One-shot JSON generator for vLLM/SGLang OpenAI-compatible servers.

    The adapter deliberately performs no retry or fallback. Every request and
    response can be appended to a JSONL trace for exact audit of the model call.
    """

    def __init__(
        self,
        config: OpenAICompatibleConfig,
        *,
        trace_path: str | Path | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        self.config = config
        self.trace_path = None if trace_path is None else Path(trace_path)
        self.headers = {"Content-Type": "application/json", **dict(headers or {})}
        self.last_trace: GenerationTrace | None = None

    def _post_json(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            endpoint,
            data=canonical_json(payload).encode("utf-8"),
            headers=self.headers,
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.config.timeout_seconds) as response:
            raw_response_bytes = response.read()
        try:
            parsed = json.loads(
                raw_response_bytes,
                object_pairs_hook=_unique_json_object,
                parse_constant=_reject_nonfinite,
            )
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
            raise ValueError("model endpoint returned invalid strict JSON") from exc
        if not isinstance(parsed, dict):
            raise ValueError("model endpoint response must be a JSON object")
        return parsed

    def _tokenize(self, payload: dict[str, Any]) -> tuple[int, ...]:
        parsed = self._post_json(f"{self.config.base_url}/tokenize", payload)
        tokens = parsed.get("tokens")
        if not isinstance(tokens, list) or not all(isinstance(item, int) for item in tokens):
            raise ValueError("tokenize endpoint must return an integer tokens array")
        return tuple(tokens)

    def _resolve_token_spans(
        self,
        prompt_messages: tuple[dict[str, str], ...],
        source_text: str,
        source_spans: tuple[SourceSpan, ...],
    ) -> tuple[tuple[SourceSpan, ...], dict[str, Any]]:
        chat_template_kwargs = self.config.extra_body.get("chat_template_kwargs")
        full_tokens = self._tokenize(
            {
                "model": self.config.model,
                "messages": prompt_messages,
                "chat_template_kwargs": chat_template_kwargs,
            }
        )
        resolved: list[SourceSpan] = []
        unresolved: list[str] = []
        for span in source_spans:
            segment = source_text[span.char_start : span.char_end]
            segment_tokens = self._tokenize(
                {
                    "model": self.config.model,
                    "prompt": segment,
                    "add_special_tokens": False,
                }
            )
            matches = [
                index
                for index in range(len(full_tokens) - len(segment_tokens) + 1)
                if full_tokens[index : index + len(segment_tokens)] == segment_tokens
            ]
            if len(matches) == 1 and segment_tokens:
                start = matches[0]
                resolved.append(
                    span.model_copy(
                        update={
                            "token_start": start,
                            "token_end": start + len(segment_tokens),
                        }
                    )
                )
            else:
                unresolved.append(span.source_id)
                resolved.append(span)
        return tuple(resolved), {
            "full_prompt_token_count": len(full_tokens),
            "full_prompt_token_ids_sha256": sha256_hex(full_tokens),
            "unresolved_source_ids": unresolved,
            "method": "exact_subsequence_in_server_tokenization",
        }

    def __call__(self, messages: Sequence[dict[str, Any]], seed: int) -> str:
        source_text, source_spans = _render_sources(
            messages,
            source_metadata_mode=self.config.source_metadata_mode,
        )
        prompt_messages = (
            {"role": "system", "content": self.config.system_prompt or _SYSTEM_PROMPT},
            {"role": "user", "content": source_text},
        )
        tokenization: dict[str, Any] = {
            "method": "not_requested",
            "unresolved_source_ids": [span.source_id for span in source_spans],
        }
        if self.config.capture_token_spans:
            source_spans, tokenization = self._resolve_token_spans(
                prompt_messages,
                source_text,
                source_spans,
            )
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": prompt_messages,
            "temperature": self.config.temperature,
            "top_p": self.config.top_p,
            "max_tokens": self.config.max_tokens,
            "seed": int(seed),
            **self.config.extra_body,
        }
        if self.config.response_format_json:
            payload["response_format"] = {"type": "json_object"}
        endpoint = f"{self.config.base_url}/chat/completions"
        started_at = datetime.now(timezone.utc)
        started = time.perf_counter()
        parsed = self._post_json(endpoint, payload)
        completed_at = datetime.now(timezone.utc)
        wall_time_ms = (time.perf_counter() - started) * 1000.0
        choices = parsed.get("choices")
        if not isinstance(choices, list) or len(choices) != 1:
            raise ValueError("model endpoint must return exactly one completion")
        choice = choices[0]
        if not isinstance(choice, dict) or not isinstance(choice.get("message"), dict):
            raise ValueError("model endpoint completion is missing its message")
        content = choice["message"].get("content")
        if not isinstance(content, str):
            raise ValueError("model endpoint completion content must be a string")
        trace = GenerationTrace(
            endpoint=endpoint,
            model=self.config.model,
            model_revision=self.config.model_revision,
            tokenizer_revision=self.config.tokenizer_revision,
            inference_engine=self.config.inference_engine,
            engine_revision=self.config.engine_revision,
            precision=self.config.precision,
            prompt_template_id=self.config.prompt_template_id,
            seed=int(seed),
            decoding={
                "temperature": self.config.temperature,
                "top_p": self.config.top_p,
                "max_tokens": self.config.max_tokens,
                "response_format_json": self.config.response_format_json,
                "source_metadata_mode": self.config.source_metadata_mode,
                "extra_body": self.config.extra_body,
            },
            prompt_messages=prompt_messages,
            source_spans=source_spans,
            tokenization=tokenization,
            request_digest=sha256_hex(payload),
            response_digest=sha256_hex(parsed),
            response_id=parsed.get("id") if isinstance(parsed.get("id"), str) else None,
            response_model=(
                parsed.get("model") if isinstance(parsed.get("model"), str) else None
            ),
            finish_reason=(
                choice.get("finish_reason")
                if isinstance(choice.get("finish_reason"), str)
                else None
            ),
            usage=parsed.get("usage") if isinstance(parsed.get("usage"), dict) else {},
            raw_action_json=content,
            raw_response=parsed,
            started_at=started_at,
            completed_at=completed_at,
            wall_time_ms=wall_time_ms,
        )
        self.last_trace = trace
        if self.trace_path is not None:
            self.trace_path.parent.mkdir(parents=True, exist_ok=True)
            with self.trace_path.open("a", encoding="utf-8") as handle:
                handle.write(canonical_json(trace) + "\n")
        return content


__all__ = [
    "GenerationTrace",
    "OpenAICompatibleConfig",
    "OpenAICompatibleGenerator",
    "SourceSpan",
]
