"""The production provider: request shape and response conversion, using real SDK types
with a stub client (no network)."""

from __future__ import annotations

import anthropic
import pytest
from anthropic.types.beta import BetaMessage

from novara.ai.provider import AnthropicProvider, ProviderError
from novara.ai.routing import cost_micros, resolve, validate_overrides, RouteError

TOOLS = [{"name": "search_customers", "description": "d", "input_schema": {"type": "object", "properties": {}}}]


def kwargs(model="claude-opus-5-5", effort="medium"):
    return AnthropicProvider.request_kwargs(model=model, effort=effort, system="sys", tools=TOOLS,
                                            messages=[{"role": "user", "content": "hi"}], max_tokens=16000)


def test_request_shape_for_opus():
    k = kwargs()
    assert k["model"] == "claude-opus-5-5"
    assert k["output_config"] == {"effort": "medium"}
    assert k["cache_control"] == {"type": "ephemeral"}
    assert k["fallbacks"] == "default" and k["betas"] == ["server-side-fallback-2026-07-01"]
    assert "thinking" not in k and "tool_choice" not in k and "temperature" not in k


def test_no_server_side_fallback_for_haiku():
    k = kwargs(model="claude-haiku-5-5", effort="low")
    assert "fallbacks" not in k and "betas" not in k


SDK_MESSAGE = {
    "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
    "content": [
        {"type": "thinking", "thinking": "", "signature": "abc"},
        {"type": "text", "text": "Looking that up."},
        {"type": "tool_use", "id": "toolu_9", "name": "search_customers", "input": {"query": "نخبة"}},
    ],
    "stop_reason": "tool_use", "stop_sequence": None,
    "usage": {"input_tokens": 1200, "output_tokens": 80, "cache_read_input_tokens": 900,
              "cache_creation_input_tokens": 0},
}


class StubMessages:
    def __init__(self, result=None, error=None):
        self.result, self.error, self.calls = result, error, []

    def create(self, **kw):
        self.calls.append(kw)
        if self.error:
            raise self.error
        return self.result


class StubClient:
    def __init__(self, messages):
        self.messages = messages
        self.beta = type("Beta", (), {"messages": messages})()


def test_response_is_converted_without_altering_blocks():
    stub = StubMessages(result=BetaMessage.model_validate(SDK_MESSAGE))
    reply = AnthropicProvider(client=StubClient(stub)).create(
        model="claude-opus-5-5", effort="medium", system="sys", tools=TOOLS, messages=[], max_tokens=16000)
    assert reply.content == SDK_MESSAGE["content"]
    assert reply.stop_reason == "tool_use"
    assert (reply.usage.input_tokens, reply.usage.cache_read_tokens) == (1200, 900)
    assert stub.calls[0]["fallbacks"] == "default"


def test_api_errors_become_provider_errors():
    import httpx2

    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    overloaded = anthropic.InternalServerError("overloaded", response=httpx2.Response(529, request=request), body=None)
    stub = StubMessages(error=overloaded)
    with pytest.raises(ProviderError) as e:
        AnthropicProvider(client=StubClient(stub)).create(model="claude-haiku-5-5", effort="low", system="s",
                                                          tools=[], messages=[], max_tokens=10)
    assert e.value.retryable is True


def test_cost_and_routing():
    assert cost_micros("claude-opus-5-5", input_tokens=1_000_000, output_tokens=0) == 4_000_000
    assert cost_micros("claude-opus-5-5", input_tokens=0, output_tokens=0, cache_read_tokens=1_000_000) == 200_000
    assert cost_micros("claude-haiku-5-5", input_tokens=1_000_000, output_tokens=1_000_000) == 600_000
    assert resolve("assistant", None).model == "claude-opus-5-5"
    with pytest.raises(RouteError):
        validate_overrides({"assistant": {"model": "claude-opus-5-5", "temperature": "1"}})


class StubStream:
    def __init__(self, message, fail_times: int, counter: list):
        self.message, self.fail_times, self.counter = message, fail_times, counter

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        self.counter.append(1)
        if len(self.counter) <= self.fail_times:
            raise ValueError("Unable to parse tool parameter JSON")
        return iter(())

    def get_final_message(self):
        return self.message


class StreamingMessages(StubMessages):
    def __init__(self, result, fail_times=0):
        super().__init__(result=result)
        self.fail_times, self.attempts = fail_times, []

    def stream(self, **kw):
        self.calls.append(kw)
        return StubStream(self.result, self.fail_times, self.attempts)


def test_streaming_requests_use_eager_tool_input_and_retry_unparseable_json():
    stub = StreamingMessages(BetaMessage.model_validate(SDK_MESSAGE), fail_times=1)
    reply = AnthropicProvider(client=StubClient(stub)).create(
        model="claude-opus-5-5", effort="high", system="s", tools=TOOLS, messages=[], max_tokens=64000, stream=True)
    assert reply.stop_reason == "tool_use"
    assert len(stub.attempts) == 2                                   # one unparseable attempt, then success
    assert all(t["eager_input_streaming"] is True for t in stub.calls[0]["tools"])
    assert "eager_input_streaming" not in TOOLS[0]                   # caller's definitions are not mutated

    always_bad = StreamingMessages(BetaMessage.model_validate(SDK_MESSAGE), fail_times=99)
    with pytest.raises(ProviderError):
        AnthropicProvider(client=StubClient(always_bad)).create(
            model="claude-opus-5-5", effort="high", system="s", tools=TOOLS, messages=[], max_tokens=64000, stream=True)
    assert len(always_bad.attempts) == 3


def test_non_streaming_requests_do_not_set_eager_input():
    assert all("eager_input_streaming" not in t for t in kwargs()["tools"])
