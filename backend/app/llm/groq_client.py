import asyncio
import logging
import random
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol

import groq

logger = logging.getLogger(__name__)


class LLMUnavailable(Exception):
    """LLM provider is down / rate-limited / misconfigured after all retries."""


@dataclass
class LLMEvent:
    kind: str  # "token" | "usage"
    text: str = ""
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


@dataclass
class LLMCompletion:
    text: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


class LLM(Protocol):
    def stream(
        self, messages: list[dict], model: str, max_tokens: int, temperature: float
    ) -> AsyncIterator[LLMEvent]: ...

    async def complete(
        self, messages: list[dict], model: str, max_tokens: int, temperature: float
    ) -> LLMCompletion: ...


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, (groq.APIConnectionError, groq.RateLimitError)):
        return True
    return isinstance(exc, groq.APIStatusError) and exc.status_code >= 500


def _usage_from_chunk(chunk):
    usage = getattr(chunk, "usage", None)
    if usage is None:
        usage = getattr(getattr(chunk, "x_groq", None), "usage", None)
    return usage


class GroqLLM:
    def __init__(self, api_key: str, max_retries: int = 3, timeout_s: float = 30.0):
        self.api_key = api_key
        self.max_retries = max_retries
        # We do our own retry loop, so disable the SDK's.
        self.client = groq.AsyncGroq(api_key=api_key, max_retries=0, timeout=timeout_s) if api_key else None

    async def complete(
        self, messages: list[dict], model: str, max_tokens: int, temperature: float
    ) -> LLMCompletion:
        """Non-streaming call (used for the small query-rewrite step). Same retry policy."""
        if self.client is None:
            raise LLMUnavailable("GROQ_API_KEY is not set")
        attempt = 0
        while True:
            try:
                res = await self.client.chat.completions.create(
                    model=model, messages=messages, max_tokens=max_tokens, temperature=temperature
                )
                usage = res.usage
                return LLMCompletion(
                    text=(res.choices[0].message.content or "").strip(),
                    prompt_tokens=getattr(usage, "prompt_tokens", None),
                    completion_tokens=getattr(usage, "completion_tokens", None),
                )
            except Exception as exc:
                if not _is_retryable(exc):
                    raise
                if attempt >= self.max_retries:
                    raise LLMUnavailable(str(exc)) from exc
                await asyncio.sleep(min(8.0, 0.5 * (2**attempt)) + random.uniform(0, 0.25))
                attempt += 1

    async def stream(
        self, messages: list[dict], model: str, max_tokens: int, temperature: float
    ) -> AsyncIterator[LLMEvent]:
        if self.client is None:
            raise LLMUnavailable("GROQ_API_KEY is not set")

        attempt = 0
        while True:
            emitted = False
            try:
                stream = await self.client.chat.completions.create(
                    model=model,
                    messages=messages,
                    stream=True,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
                async for chunk in stream:
                    usage = _usage_from_chunk(chunk)
                    if usage is not None:
                        yield LLMEvent(
                            "usage",
                            prompt_tokens=getattr(usage, "prompt_tokens", None),
                            completion_tokens=getattr(usage, "completion_tokens", None),
                        )
                    if chunk.choices:
                        text = chunk.choices[0].delta.content
                        if text:
                            emitted = True
                            yield LLMEvent("token", text=text)
                return
            except Exception as exc:
                # Never retry once the user has already seen tokens.
                if emitted or not _is_retryable(exc) or attempt >= self.max_retries:
                    if _is_retryable(exc):
                        raise LLMUnavailable(str(exc)) from exc
                    raise
                delay = min(8.0, 0.5 * (2**attempt)) + random.uniform(0, 0.25)
                logger.warning("Groq error (%s); retry %d in %.2fs", type(exc).__name__, attempt + 1, delay)
                attempt += 1
                await asyncio.sleep(delay)
