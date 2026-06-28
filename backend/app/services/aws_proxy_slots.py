"""AWS static-IP proxy slot definitions.

This module builds backend-only proxy URLs from server-side secrets. Public
helpers deliberately omit proxy URLs and credentials.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import quote

from app.config import settings


PROVIDER = "AWS"
DEFAULT_AWS_PROXY_HOST = "13.203.58.220"


class AWSProxySlotConfigError(ValueError):
    """Safe configuration error for AWS proxy slot setup."""


@dataclass(frozen=True)
class AWSProxySlotSpec:
    slot_number: int
    proxy_port: int
    public_ip: str
    private_ip: str
    proxy_username: str


@dataclass(frozen=True)
class AWSProxySlot:
    provider: str
    slot_number: int
    proxy_host: str
    proxy_port: int
    private_ip: str
    public_ip: str
    expected_egress_ip: str
    proxy_username: str
    proxy_url: str


AWS_PROXY_SLOT_SPECS: tuple[AWSProxySlotSpec, ...] = (
    AWSProxySlotSpec(
        slot_number=1,
        proxy_port=3001,
        public_ip="13.203.58.220",
        private_ip="172.31.43.47",
        proxy_username="nova_user_1",
    ),
    AWSProxySlotSpec(
        slot_number=2,
        proxy_port=3002,
        public_ip="13.127.93.199",
        private_ip="172.31.42.78",
        proxy_username="nova_user_2",
    ),
    AWSProxySlotSpec(
        slot_number=3,
        proxy_port=3003,
        public_ip="13.206.213.151",
        private_ip="172.31.47.205",
        proxy_username="nova_user_3",
    ),
    AWSProxySlotSpec(
        slot_number=4,
        proxy_port=3004,
        public_ip="35.154.61.32",
        private_ip="172.31.42.140",
        proxy_username="nova_user_4",
    ),
    AWSProxySlotSpec(
        slot_number=5,
        proxy_port=3005,
        public_ip="65.0.153.89",
        private_ip="172.31.45.118",
        proxy_username="nova_user_5",
    ),
)


def _enabled(config: Any) -> bool:
    return bool(getattr(config, "AWS_PROXY_SLOTS_ENABLED", False))


def _slot_password(config: Any, slot_number: int) -> str:
    per_slot = str(getattr(config, f"AWS_PROXY_SLOT_{slot_number}_PASSWORD", "") or "").strip()
    if per_slot:
        return per_slot
    return str(getattr(config, "AWS_PROXY_SHARED_PASSWORD", "") or "").strip()


def build_proxy_url(*, host: str, port: int, username: str, password: str) -> str:
    encoded_user = quote(username, safe="")
    encoded_password = quote(password, safe="")
    return f"http://{encoded_user}:{encoded_password}@{host}:{port}"


def build_aws_proxy_slots(config: Any = settings) -> list[AWSProxySlot]:
    if not _enabled(config):
        return []

    proxy_host = str(getattr(config, "AWS_PROXY_HOST", "") or "").strip()
    if not proxy_host:
        raise AWSProxySlotConfigError("AWS proxy host is missing.")

    slots: list[AWSProxySlot] = []
    for spec in AWS_PROXY_SLOT_SPECS:
        password = _slot_password(config, spec.slot_number)
        if not password:
            raise AWSProxySlotConfigError(
                f"AWS proxy credential is missing for slot {spec.slot_number}."
            )
        slots.append(
            AWSProxySlot(
                provider=PROVIDER,
                slot_number=spec.slot_number,
                proxy_host=proxy_host,
                proxy_port=spec.proxy_port,
                private_ip=spec.private_ip,
                public_ip=spec.public_ip,
                expected_egress_ip=spec.public_ip,
                proxy_username=spec.proxy_username,
                proxy_url=build_proxy_url(
                    host=proxy_host,
                    port=spec.proxy_port,
                    username=spec.proxy_username,
                    password=password,
                ),
            )
        )
    return slots


def sanitize_aws_proxy_slot(slot: AWSProxySlot) -> dict[str, Any]:
    data = asdict(slot)
    data.pop("proxy_url", None)
    return data


def sanitize_aws_proxy_slots(slots: list[AWSProxySlot]) -> list[dict[str, Any]]:
    return [sanitize_aws_proxy_slot(slot) for slot in slots]
