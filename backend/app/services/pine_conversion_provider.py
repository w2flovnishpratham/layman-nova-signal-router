"""Provider-neutral Pine conversion client; raw provider data never escapes this module."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from app.config import settings
from app.schemas.pine_conversion import PineConversionOutput


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
