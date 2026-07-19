"""Conservative sanitization for untrusted operator-supplied diagnostic text."""
from __future__ import annotations

import re


REDACTED = "[REDACTED]"
DEFAULT_MAX_LENGTH = 1000

_UNSAFE_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_PRIVATE_KEY_BLOCK = re.compile(
    r"-----BEGIN[ \t]+(?:RSA[ \t]+|OPENSSH[ \t]+|EC[ \t]+|DSA[ \t]+)?"
    r"PRIVATE[ \t]+KEY-----.*?(?:-----END[ \t]+(?:RSA[ \t]+|OPENSSH[ \t]+|"
    r"EC[ \t]+|DSA[ \t]+)?PRIVATE[ \t]+KEY-----|$)",
    re.IGNORECASE | re.DOTALL,
)
_SENSITIVE_URL = re.compile(
    r"(?i)\b(?:postgresql(?:\+[a-z0-9_]+)?|postgres|mysql|mariadb|"
    r"mongodb(?:\+srv)?|redis|rediss|amqp|amqps)://[^\s\"'<>]+"
)
_AUTH_HEADER = re.compile(
    r"(?im)\b(authorization|proxy-authorization|www-authenticate|"
    r"x-api-key|api-key)\b[ \t]*(?::|=)?[ \t]*[^\r\n]*"
)
_COOKIE_HEADER = re.compile(
    r"(?im)\b(cookie|set-cookie)\b[ \t]*(?::|=)?[ \t]*[^\r\n]*"
)
_SECRET_LABEL = re.compile(
    r"""(?ix)
    \b(
        access[_ -]?token|refresh[_ -]?token|
        session[_ -]?id|sessionid|session|csrf|
        token|secret|password|passwd|pwd|
        api[_ -]?key|apikey|client[_ -]?secret|private[_ -]?key|
        credential|webhook[_ -]?secret|auth[_ -]?token|
        totp[_ -]?secret|totp|otp|one[_ -]?time[_ -]?password|
        dhan(?:[_ -]?(?:access|auth))?[_ -]?(?:token|secret|key)|
        anthropic[_ -]?(?:api[_ -]?)?(?:key|token|secret)|
        private[_ -]?webhook[_ -]?(?:secret|token|credential)
    )\b
    (?:
        [ \t]*(?:=|:)[ \t]* |
        [ \t]+
    )
    ("[^"\r\n]*"|'[^'\r\n]*'|[^\s,;]+)
    """
)
_WINDOWS_PATH = re.compile(
    r"(?i)(?<![a-z0-9_])(?:[a-z]:[\\/](?:[^ \t\r\n\"'<>|]+[\\/]?)*)"
)
_UNC_PATH = re.compile(
    r"(?<![\\\w])\\\\[^\\\s\"'<>|]+\\[^\\\s\"'<>|]+"
    r"(?:\\[^\\\s\"'<>|]+)*"
)
_UNIX_PATH = re.compile(
    r"(?<![\w:])/(?:home|root|Users|var|etc|opt|srv|tmp|private)"
    r"(?:/[^\s\"'<>]+)+"
)
_HORIZONTAL_WHITESPACE = re.compile(r"[^\S\r\n]+")
_EXCESS_NEWLINES = re.compile(r"\n{3,}")


def _redact_header(match: re.Match[str]) -> str:
    return f"{match.group(1)}: {REDACTED}"


def _redact_assignment(match: re.Match[str]) -> str:
    return f"{match.group(1)}={REDACTED}"


def sanitize_untrusted_operator_text(
    value: str | None,
    *,
    max_length: int = DEFAULT_MAX_LENGTH,
) -> str | None:
    """Return bounded, deterministic diagnostic text with secrets removed.

    Sanitization always precedes truncation so a secret cannot be exposed by
    cutting a long value before its closing delimiter. Invalid Unicode scalar
    values are replaced before persistence. The output is intentionally plain
    text and idempotent.
    """
    if value is None:
        return None
    if max_length < 0:
        max_length = 0

    try:
        text = value.encode("utf-8", "replace").decode("utf-8")
    except Exception:
        return REDACTED[:max_length] or None

    try:
        text = _UNSAFE_CONTROL.sub(" ", text)
        text = _PRIVATE_KEY_BLOCK.sub(REDACTED, text)
        text = _SENSITIVE_URL.sub(REDACTED, text)
        text = _AUTH_HEADER.sub(_redact_header, text)
        text = _COOKIE_HEADER.sub(_redact_header, text)
        text = _SECRET_LABEL.sub(_redact_assignment, text)
        text = _UNC_PATH.sub(REDACTED, text)
        text = _WINDOWS_PATH.sub(REDACTED, text)
        text = _UNIX_PATH.sub(REDACTED, text)
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        text = _HORIZONTAL_WHITESPACE.sub(" ", text)
        text = "\n".join(line.strip() for line in text.splitlines())
        text = _EXCESS_NEWLINES.sub("\n\n", text).strip()
    except Exception:
        return REDACTED[:max_length] or None

    return text[:max_length] or None
