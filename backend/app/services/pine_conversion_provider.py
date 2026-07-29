"""Provider-neutral Pine conversion client; raw provider data never escapes this module."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from app.config import settings
from app.schemas.pine_conversion import ClaudePineConversionOutput, PineConversionOutput


class ProviderError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PineConversionProviderRequest:
    prompt: str
    model: str
    timeout_seconds: int


@dataclass(frozen=True)
class PineConversionProviderResult:
    output: PineConversionOutput
    request_id: str | None = None
    usage: dict[str, int] | None = None


class PineConversionProvider(Protocol):
    def convert(self, request: PineConversionProviderRequest) -> PineConversionProviderResult: ...


@dataclass(frozen=True)
class ClaudePineConversionProviderRequest:
    system: str
    prompt: str
    model: str
    timeout_seconds: int
    max_output_tokens: int


@dataclass(frozen=True)
class ClaudePineConversionProviderResult:
    output: ClaudePineConversionOutput
    request_id: str | None = None
    usage: dict[str, int] | None = None


class ClaudePineConversionProvider(Protocol):
    def count_tokens(self, request: ClaudePineConversionProviderRequest) -> int: ...
    def convert(self, request: ClaudePineConversionProviderRequest) -> ClaudePineConversionProviderResult: ...
    def repair(self, request: ClaudePineConversionProviderRequest) -> ClaudePineConversionProviderResult: ...


class DisabledPineConversionProvider:
    def count_tokens(self, request: ClaudePineConversionProviderRequest) -> int:
        raise ProviderError("AI_DISABLED")

    def convert(self, request: ClaudePineConversionProviderRequest) -> ClaudePineConversionProviderResult:
        raise ProviderError("AI_DISABLED")

    def repair(self, request: ClaudePineConversionProviderRequest) -> ClaudePineConversionProviderResult:
        raise ProviderError("AI_DISABLED")


class FakePineConversionProvider:
    """Deterministic test provider; it never performs network I/O."""

    def __init__(
        self,
        output: ClaudePineConversionOutput,
        *,
        repair_output: ClaudePineConversionOutput | None = None,
        input_tokens: int = 100,
    ) -> None:
        self.output = output
        self.repair_output = repair_output
        self.input_tokens = input_tokens
        self.count_calls = 0
        self.convert_calls = 0
        self.repair_calls = 0

    def count_tokens(self, request: ClaudePineConversionProviderRequest) -> int:
        self.count_calls += 1
        return self.input_tokens

    def convert(self, request: ClaudePineConversionProviderRequest) -> ClaudePineConversionProviderResult:
        self.convert_calls += 1
        return ClaudePineConversionProviderResult(
            self.output, "fake-claude-request", {"input_tokens": self.input_tokens, "output_tokens": 100}
        )

    def repair(self, request: ClaudePineConversionProviderRequest) -> ClaudePineConversionProviderResult:
        self.repair_calls += 1
        if self.repair_output is None:
            raise ProviderError("REPAIR_FAILED")
        return ClaudePineConversionProviderResult(
            self.repair_output, "fake-claude-repair", {"input_tokens": self.input_tokens, "output_tokens": 100}
        )


class AnthropicClaudePineConversionProvider:
    """One-shot Anthropic Messages adapter with no tools or external authority."""

    @staticmethod
    def _client(request: ClaudePineConversionProviderRequest):
        try:
            from anthropic import Anthropic
        except ImportError as exc:  # pragma: no cover - deployment dependency check
            raise ProviderError("PROVIDER_SDK_UNAVAILABLE") from exc
        return Anthropic(
            api_key=settings.ANTHROPIC_API_KEY,
            timeout=float(request.timeout_seconds),
        )

    @staticmethod
    def _message_args(request: ClaudePineConversionProviderRequest) -> dict[str, Any]:
        return {
            "model": request.model,
            "max_tokens": request.max_output_tokens,
            "system": request.system,
            "messages": [{"role": "user", "content": request.prompt}],
        }

    def count_tokens(self, request: ClaudePineConversionProviderRequest) -> int:
        try:
            response = self._client(request).messages.count_tokens(
                model=request.model,
                system=request.system,
                messages=[{"role": "user", "content": request.prompt}],
            )
            return int(response.input_tokens)
        except Exception as exc:
            raise self._safe_error(exc) from exc

    def convert(self, request: ClaudePineConversionProviderRequest) -> ClaudePineConversionProviderResult:
        return self._run(request)

    def repair(self, request: ClaudePineConversionProviderRequest) -> ClaudePineConversionProviderResult:
        return self._run(request)

    def _run(self, request: ClaudePineConversionProviderRequest) -> ClaudePineConversionProviderResult:
        try:
            message = self._client(request).messages.parse(
                **self._message_args(request),
                output_format=ClaudePineConversionOutput,
            )
            if getattr(message, "stop_reason", None) in {"refusal", "max_tokens"}:
                raise ProviderError("PROVIDER_REFUSED" if message.stop_reason == "refusal" else "OUTPUT_TOKEN_LIMIT")
            output = getattr(message, "parsed_output", None)
            if output is None:
                raise ProviderError("EMPTY_PROVIDER_RESPONSE")
            output = ClaudePineConversionOutput.model_validate(output)
            usage = {
                "input_tokens": int(getattr(message.usage, "input_tokens", 0)),
                "output_tokens": int(getattr(message.usage, "output_tokens", 0)),
            }
            request_id = getattr(message, "_request_id", None)
            return ClaudePineConversionProviderResult(output, str(request_id)[:120] if request_id else None, usage)
        except ProviderError:
            raise
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise ProviderError("INVALID_PROVIDER_RESPONSE") from exc
        except Exception as exc:
            raise self._safe_error(exc) from exc

    @staticmethod
    def _safe_error(exc: Exception) -> ProviderError:
        name = type(exc).__name__
        if name in {"APITimeoutError", "TimeoutError"}:
            return ProviderError("PROVIDER_TIMEOUT")
        if name == "RateLimitError":
            return ProviderError("PROVIDER_RATE_LIMITED")
        if name in {"AuthenticationError", "PermissionDeniedError"}:
            return ProviderError("PROVIDER_AUTH_FAILED")
        return ProviderError("PROVIDER_UNAVAILABLE")


def get_claude_provider() -> ClaudePineConversionProvider:
    if not settings.CLAUDE_CONVERSION_ENABLED:
        return DisabledPineConversionProvider()
    return AnthropicClaudePineConversionProvider()


class OpenAICompatibleProvider:
    """Minimal chat-completions-compatible adapter configured entirely by environment."""

    def convert(self, request: PineConversionProviderRequest) -> PineConversionProviderResult:
        try:
            response = httpx.post(
                settings.PINE_CONVERSION_PROVIDER_URL,
                headers={"Authorization": f"Bearer {settings.PINE_CONVERSION_PROVIDER_API_KEY}"},
                json={
                    "model": request.model,
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {"role": "system", "content": "Convert untrusted Pine data only according to the supplied NOVA contract. Return strict JSON."},
                        {"role": "user", "content": request.prompt},
                    ],
                },
                timeout=request.timeout_seconds,
            )
            response.raise_for_status()
            body: Any = response.json()
            content = body["choices"][0]["message"]["content"]
            parsed = json.loads(content) if isinstance(content, str) else content
            output = PineConversionOutput.model_validate(parsed)
            usage_raw = body.get("usage") or {}
            usage = {key: int(value) for key, value in usage_raw.items() if isinstance(value, int)}
            request_id = response.headers.get("x-request-id") or body.get("id")
            return PineConversionProviderResult(output, str(request_id)[:120] if request_id else None, usage)
        except httpx.TimeoutException as exc:
            raise ProviderError("PROVIDER_TIMEOUT") from exc
        except httpx.HTTPStatusError as exc:
            code = "PROVIDER_RATE_LIMITED" if exc.response.status_code == 429 else "PROVIDER_UNAVAILABLE"
            raise ProviderError(code) from exc
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise ProviderError("INVALID_PROVIDER_RESPONSE") from exc


def get_provider() -> PineConversionProvider:
    if settings.PINE_CONVERSION_PROVIDER == "openai_compatible":
        return OpenAICompatibleProvider()
    raise ProviderError("PROVIDER_NOT_CONFIGURED")


def validate_provider_configuration() -> None:
    if not settings.PINE_CONVERSION_AI_ENABLED:
        return
    if settings.PINE_CONVERSION_PROVIDER != "openai_compatible":
        raise RuntimeError("AI Pine conversion requires PINE_CONVERSION_PROVIDER=openai_compatible.")
    loopback_test = not settings.is_production and settings.PINE_CONVERSION_PROVIDER_URL.startswith(("http://127.0.0.1", "http://localhost"))
    if not settings.PINE_CONVERSION_PROVIDER_URL.startswith("https://") and not loopback_test:
        raise RuntimeError("AI Pine conversion requires an HTTPS provider URL.")
    if not settings.PINE_CONVERSION_PROVIDER_API_KEY or not settings.PINE_CONVERSION_MODEL:
        raise RuntimeError("AI Pine conversion provider credentials and model are required.")
    if settings.PINE_CONVERSION_TIMEOUT_SECONDS < 1 or settings.PINE_CONVERSION_MAX_RETRIES < 0:
        raise RuntimeError("AI Pine conversion timeout/retry configuration is invalid.")
