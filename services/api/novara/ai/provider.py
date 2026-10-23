"""Model providers. The assistant talks to a ``ModelProvider``; production uses Claude
through the official Anthropic SDK, tests use ``ScriptedProvider``.

Content blocks are plain dicts in Messages API format. Assistant content is stored and
sent back exactly as received (including thinking blocks) - history is append-only.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Protocol

import anthropic

# Server-side refusal fallback ("default" mode) is opted into for models that support it.
_FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5"}
_FALLBACK_BETA = "server-side-fallback-2026-07-01"


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


@dataclass(frozen=True)
class ModelReply:
    content: list[dict]
    stop_reason: str
    usage: Usage
    model: str
    latency_ms: int = 0


class ProviderError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool):
        super().__init__(message)
        self.retryable = retryable


class ModelProvider(Protocol):
    """``on_text``, when given, receives the reply's text as it is generated (the call then streams)."""

    def create(self, *, model: str, effort: str, system: str, tools: list[dict], messages: list[dict],
               max_tokens: int, stream: bool = False,
               on_text: Callable[[str], None] | None = None) -> ModelReply: ...


class AnthropicProvider:
    """Claude via the Anthropic SDK. The client resolves credentials from the environment
    (ANTHROPIC_API_KEY, or an `ant auth login` profile)."""

    def __init__(self, client: anthropic.Anthropic | None = None, timeout_s: float = 120.0):
        self._client = client
        self._timeout_s = timeout_s

    @property
    def client(self) -> anthropic.Anthropic:
        if self._client is None:
            self._client = anthropic.Anthropic(timeout=self._timeout_s, max_retries=2)
        return self._client

    @staticmethod
    def request_kwargs(*, model: str, effort: str, system: str, tools: list[dict], messages: list[dict],
                       max_tokens: int, stream: bool = False) -> dict:
        if stream:
            # Large tool inputs (whole files) stream as they're generated. The server then no longer
            # validates them, so the caller validates every input before running a tool (tools.parse_args).
            tools = [t | {"eager_input_streaming": True} for t in tools]
        kwargs = dict(
            model=model,
            max_tokens=max_tokens,
            system=system,                 # frozen text, so the prompt-cache prefix stays stable
            tools=tools,                   # deterministic order (see tools.py) for the same reason
            messages=messages,
            output_config={"effort": effort},
            cache_control={"type": "ephemeral"},
        )
        # No `thinking` param: current models run adaptive thinking by default; effort sets depth.
        # No forced tool_choice: the default "auto" is the only mode current models accept.
        if model in _FALLBACK_MODELS:
            kwargs["betas"] = [_FALLBACK_BETA]
            kwargs["fallbacks"] = "default"
        return kwargs

    def create(self, *, model: str, effort: str, system: str, tools: list[dict], messages: list[dict],
               max_tokens: int, stream: bool = False, on_text: Callable[[str], None] | None = None) -> ModelReply:
        kwargs = self.request_kwargs(model=model, effort=effort, system=system, tools=tools, messages=messages,
                                     max_tokens=max_tokens, stream=stream)
        api = self.client.beta.messages if "betas" in kwargs else self.client.messages
        started = time.monotonic()
        try:
            if not stream and on_text is None:
                message = api.create(**kwargs)
            else:
                for attempt in range(3):
                    try:
                        with api.stream(**kwargs) as s:
                            for event in s:
                                delta = getattr(event, "delta", None) if event.type == "content_block_delta" else None
                                if on_text is not None and getattr(delta, "type", "") == "text_delta":
                                    on_text(delta.text)
                            message = s.get_final_message()
                        break
                    except ValueError:
                        # Tool input the SDK couldn't parse at all; no tool_use id to answer, so re-issue.
                        if attempt == 2:
                            raise ProviderError("the model produced unreadable tool input", retryable=True)
        except anthropic.AuthenticationError as e:
            raise ProviderError(f"AI provider rejected the credentials: {e.message}", retryable=False)
        except anthropic.BadRequestError as e:
            raise ProviderError(f"AI request was invalid: {e.message}", retryable=False)
        except anthropic.RateLimitError:
            raise ProviderError("AI provider is rate limiting requests; try again shortly", retryable=True)
        except anthropic.APIStatusError as e:
            raise ProviderError(f"AI provider error ({e.status_code})", retryable=e.status_code >= 500)
        except anthropic.APIConnectionError:
            raise ProviderError("could not reach the AI provider", retryable=True)
        return to_reply(message, latency_ms=int((time.monotonic() - started) * 1000))


def to_reply(message, *, latency_ms: int = 0) -> ModelReply:
    """Convert an SDK message to plain dicts, keeping every content block as received."""
    data = message.to_dict()
    u = data.get("usage") or {}
    return ModelReply(
        content=data["content"],
        stop_reason=data.get("stop_reason") or "",
        usage=Usage(
            input_tokens=u.get("input_tokens") or 0,
            output_tokens=u.get("output_tokens") or 0,
            cache_read_tokens=u.get("cache_read_input_tokens") or 0,
            cache_write_tokens=u.get("cache_creation_input_tokens") or 0,
        ),
        model=data.get("model") or "",
        latency_ms=latency_ms,
    )


@dataclass
class ScriptedProvider:
    """Test double: returns pre-scripted replies and records every request it receives.

    Each script entry is a ModelReply or a function of the request kwargs returning one.
    """

    script: list[ModelReply | Callable[[dict], ModelReply]] = field(default_factory=list)
    requests: list[dict] = field(default_factory=list)

    def create(self, **kwargs) -> ModelReply:
        on_text = kwargs.pop("on_text", None)
        self.requests.append(kwargs)
        if not self.script:
            raise ProviderError("scripted provider has no more replies", retryable=False)
        step = self.script.pop(0)
        reply = step(kwargs) if callable(step) else step
        if on_text is not None:
            stream_text(reply, on_text)
        return reply


def stream_text(reply: ModelReply, on_text: Callable[[str], None]) -> None:
    """Hand a finished reply's text to ``on_text`` a few words at a time, as a streamed reply would arrive."""
    for block in reply.content:
        if block.get("type") == "text":
            words = block["text"].split(" ")
            for i in range(0, len(words), 3):
                on_text(" ".join(words[i:i + 3]) + (" " if i + 3 < len(words) else ""))


def text_reply(text: str, *, model: str = "claude-opus-5-5", input_tokens: int = 1000,
               output_tokens: int = 100) -> ModelReply:
    return ModelReply(content=[{"type": "text", "text": text}], stop_reason="end_turn",
                      usage=Usage(input_tokens, output_tokens), model=model)


def tool_reply(name: str, arguments: dict, *, tool_use_id: str = "toolu_1", text: str = "",
               model: str = "claude-opus-5-5") -> ModelReply:
    content = ([{"type": "text", "text": text}] if text else []) + [
        {"type": "tool_use", "id": tool_use_id, "name": name, "input": arguments}]
    return ModelReply(content=content, stop_reason="tool_use", usage=Usage(1000, 50), model=model)
