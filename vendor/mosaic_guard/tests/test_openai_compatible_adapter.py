from __future__ import annotations

import json

import pytest

from scns_guard.adapters.openai_compatible import (
    OpenAICompatibleConfig,
    OpenAICompatibleGenerator,
)


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        del exc_type, exc, traceback

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def _config() -> OpenAICompatibleConfig:
    return OpenAICompatibleConfig(
        base_url="http://127.0.0.1:33000/v1",
        model="Qwen3.8-27B",
        model_revision="config-sha256:191e0af2",
        tokenizer_revision="tokenizer-config-sha256:b11349aa",
        inference_engine="sglang",
        engine_revision="0.5.13.post1",
        precision="bfloat16",
        prompt_template_id="mosaic-action-json-v1",
        temperature=0.0,
        top_p=1.0,
        max_tokens=256,
    )


def test_openai_generator_records_exact_request_and_response(monkeypatch, tmp_path) -> None:
    captured: list[dict] = []

    def fake_urlopen(request, timeout):
        captured.append(
            {
                "url": request.full_url,
                "timeout": timeout,
                "body": json.loads(request.data),
            }
        )
        return _FakeResponse(
            {
                "id": "chatcmpl-test",
                "model": "Qwen3.8-27B",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": '{"action_type":"read_balance","params":{"account_id":"acct-user"},"write_action":false}',
                        },
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 8, "total_tokens": 18},
            }
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    trace_path = tmp_path / "generation.jsonl"
    generator = OpenAICompatibleGenerator(_config(), trace_path=trace_path)
    result = generator(
        [
            {
                "source_id": "user-1",
                "source_kind": "user",
                "trust": "user",
                "text": "Check my balance",
                "payload": {"account_id": "acct-user"},
            }
        ],
        seed=17,
    )
    assert json.loads(result)["action_type"] == "read_balance"
    assert captured[0]["url"] == "http://127.0.0.1:33000/v1/chat/completions"
    assert captured[0]["body"]["seed"] == 17
    assert captured[0]["body"]["temperature"] == 0.0
    assert "user-1" in captured[0]["body"]["messages"][1]["content"]

    row = json.loads(trace_path.read_text(encoding="utf-8"))
    assert row["model_revision"] == "config-sha256:191e0af2"
    assert row["tokenizer_revision"] == "tokenizer-config-sha256:b11349aa"
    assert row["seed"] == 17
    assert row["usage"]["total_tokens"] == 18
    assert row["raw_action_json"] == result
    assert row["request_digest"]


def test_openai_generator_fails_once_without_retry(monkeypatch) -> None:
    calls = 0

    def fake_urlopen(request, timeout):
        nonlocal calls
        del request, timeout
        calls += 1
        return _FakeResponse({"choices": []})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    generator = OpenAICompatibleGenerator(_config())
    with pytest.raises(ValueError, match="exactly one completion"):
        generator([], seed=0)
    assert calls == 1


def test_openai_generator_resolves_source_token_span_with_server_tokenizer(
    monkeypatch,
) -> None:
    def fake_urlopen(request, timeout):
        del timeout
        body = json.loads(request.data)
        if request.full_url.endswith("/tokenize"):
            if "messages" in body:
                return _FakeResponse({"tokens": [10, 20, 30, 40]})
            return _FakeResponse({"tokens": [20, 30]})
        return _FakeResponse(
            {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": '{"action_type":"read_balance","params":{"account_id":"acct-user"},"write_action":false}'
                        },
                    }
                ]
            }
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    generator = OpenAICompatibleGenerator(
        _config().model_copy(update={"capture_token_spans": True})
    )
    generator(
        [
            {
                "source_id": "user-1",
                "source_kind": "user",
                "trust": "user",
                "text": "Check my balance",
                "payload": {"account_id": "acct-user"},
            }
        ],
        seed=0,
    )
    assert generator.last_trace is not None
    assert generator.last_trace.source_spans[0].token_start == 1
    assert generator.last_trace.source_spans[0].token_end == 3
    assert generator.last_trace.tokenization["unresolved_source_ids"] == []


def test_opaque_rendering_hides_runtime_trust_but_keeps_span_identity(monkeypatch) -> None:
    captured: list[dict] = []

    def fake_urlopen(request, timeout):
        del timeout
        captured.append(json.loads(request.data))
        return _FakeResponse(
            {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": '{"action_type":"read_balance","params":{"account_id":"acct-user"},"write_action":false}'
                        },
                    }
                ]
            }
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    generator = OpenAICompatibleGenerator(
        _config().model_copy(update={"source_metadata_mode": "opaque"})
    )
    generator(
        [
            {
                "source_id": "rag-secret-id",
                "source_kind": "rag",
                "trust": "external",
                "text": "document",
                "payload": {},
            }
        ],
        seed=0,
    )
    prompt = captured[0]["messages"][1]["content"]
    assert "rag-secret-id" not in prompt
    assert '"source_kind"' not in prompt
    assert '"trust"' not in prompt
    assert "source-000" in prompt
    assert generator.last_trace is not None
    assert generator.last_trace.source_spans[0].source_id == "rag-secret-id"


def test_openai_config_rejects_unversioned_model_metadata() -> None:
    with pytest.raises(ValueError):
        OpenAICompatibleConfig(
            base_url="http://127.0.0.1:33000/v1",
            model="Qwen3.8-27B",
            model_revision="",
            tokenizer_revision="",
            inference_engine="sglang",
            engine_revision="0.5.13.post1",
            precision="bfloat16",
            prompt_template_id="mosaic-action-json-v1",
        )


def test_summary_system_prompt_is_explicit_and_recorded(monkeypatch):
    captured = []
    def fake_urlopen(request, timeout):
        captured.append(json.loads(request.data))
        return _FakeResponse({"choices": [{"message": {"content": '{"summary":"text"}'}}]})
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    config = _config().model_copy(update={"system_prompt": "Summarize only.",
                                         "prompt_template_id": "test-summary-v1"})
    generator = OpenAICompatibleGenerator(config)
    generator([], seed=23)
    assert captured[0]["messages"][0]["content"] == "Summarize only."
    assert generator.last_trace.prompt_messages[0]["content"] == "Summarize only."


def test_opaque_identity_rename_is_an_exact_prompt_noop():
    from scns_guard.adapters.openai_compatible import _render_sources
    message = {"source_id": "original", "source_kind": "rag", "trust": "external",
               "text": "document", "payload": {"to_account": "A"}}
    renamed = {**message, "source_id": "new-runtime-id"}
    original_text, original_spans = _render_sources([message], source_metadata_mode="opaque")
    altered_text, altered_spans = _render_sources([renamed], source_metadata_mode="opaque")
    assert original_text == altered_text
    assert original_spans[0].source_id != altered_spans[0].source_id
