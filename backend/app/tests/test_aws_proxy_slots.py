from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.services.aws_proxy_slots import (
    AWSProxySlotConfigError,
    build_aws_proxy_slots,
    sanitize_aws_proxy_slot,
)


EXPECTED_SLOTS = [
    (1, "13.203.58.220", 3001, "13.203.58.220", "172.31.43.47", "nova_user_1"),
    (2, "13.203.58.220", 3002, "13.127.93.199", "172.31.42.78", "nova_user_2"),
    (3, "13.203.58.220", 3003, "13.206.213.151", "172.31.47.205", "nova_user_3"),
    (4, "13.203.58.220", 3004, "35.154.61.32", "172.31.42.140", "nova_user_4"),
    (5, "13.203.58.220", 3005, "65.0.153.89", "172.31.45.118", "nova_user_5"),
]


def _config(**overrides):
    values = {
        "AWS_PROXY_SLOTS_ENABLED": True,
        "AWS_PROXY_HOST": "13.203.58.220",
        "AWS_PROXY_SHARED_PASSWORD": "shared-secret",
        "AWS_PROXY_SLOT_1_PASSWORD": "",
        "AWS_PROXY_SLOT_2_PASSWORD": "",
        "AWS_PROXY_SLOT_3_PASSWORD": "",
        "AWS_PROXY_SLOT_4_PASSWORD": "",
        "AWS_PROXY_SLOT_5_PASSWORD": "",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_aws_proxy_slots_disabled_returns_no_slots():
    slots = build_aws_proxy_slots(
        _config(AWS_PROXY_SLOTS_ENABLED=False, AWS_PROXY_SHARED_PASSWORD="")
    )
    assert slots == []


def test_aws_proxy_slots_return_exactly_five_slots_with_expected_coordinates():
    slots = build_aws_proxy_slots(_config())

    assert len(slots) == 5
    for slot, expected in zip(slots, EXPECTED_SLOTS, strict=True):
        slot_number, host, port, public_ip, private_ip, username = expected
        assert slot.provider == "AWS"
        assert slot.slot_number == slot_number
        assert slot.proxy_host == host
        assert slot.proxy_port == port
        assert slot.public_ip == public_ip
        assert slot.expected_egress_ip == public_ip
        assert slot.private_ip == private_ip
        assert slot.proxy_username == username


def test_aws_proxy_url_encodes_at_sign_in_password():
    slots = build_aws_proxy_slots(_config(AWS_PROXY_SHARED_PASSWORD="abc@123"))

    assert "abc%40123" in slots[0].proxy_url
    assert "abc@123" not in slots[0].proxy_url
    assert slots[0].proxy_url == "http://nova_user_1:abc%40123@13.203.58.220:3001"


def test_aws_proxy_slot_uses_per_slot_password_before_shared_password():
    slots = build_aws_proxy_slots(
        _config(AWS_PROXY_SHARED_PASSWORD="shared", AWS_PROXY_SLOT_2_PASSWORD="slot@2")
    )

    assert "shared" in slots[0].proxy_url
    assert "slot%402" in slots[1].proxy_url
    assert "shared" not in slots[1].proxy_url


def test_sanitized_aws_proxy_slot_omits_proxy_url_and_credentials():
    slot = build_aws_proxy_slots(_config(AWS_PROXY_SHARED_PASSWORD="abc@123"))[0]

    sanitized = sanitize_aws_proxy_slot(slot)
    serialized = json.dumps(sanitized, sort_keys=True)

    assert "proxy_url" not in sanitized
    assert "abc@123" not in serialized
    assert "abc%40123" not in serialized
    assert "nova_user_1:abc" not in serialized


def test_missing_aws_proxy_password_raises_safe_config_error():
    with pytest.raises(AWSProxySlotConfigError) as exc:
        build_aws_proxy_slots(_config(AWS_PROXY_SHARED_PASSWORD=""))

    message = str(exc.value)
    assert message == "AWS proxy credential is missing for slot 1."
    assert "proxy_url" not in message
    assert "@" not in message
