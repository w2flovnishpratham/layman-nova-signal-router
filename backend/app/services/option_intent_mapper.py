from __future__ import annotations

from dataclasses import dataclass
from typing import Any


OPTION_SIDE_BLOCKED = "OPTION_SIDE_BLOCKED"
UNSUPPORTED_ACTION = "UNSUPPORTED_ACTION"
UNSUPPORTED_INTENT = "UNSUPPORTED_INTENT"


@dataclass(frozen=True)
class OptionIntentMappingResult:
    ok: bool
    option_side: str | None = None
    error_code: str | None = None
    user_message: str | None = None


class OptionIntentMapper:
    def map(
        self,
        *,
        action: str,
        intent: str,
        side_preference: str | None = None,
    ) -> OptionIntentMappingResult:
        normalized_action = str(action or "").upper()
        normalized_intent = str(intent or "").upper()
        option_side = self._option_side_for(normalized_action, normalized_intent)
        if option_side is None:
            if normalized_action not in {"ENTRY", "EXIT", "REVERSE", "HEARTBEAT"}:
                return OptionIntentMappingResult(
                    ok=False,
                    error_code=UNSUPPORTED_ACTION,
                    user_message="This signal action is not supported by the dry-run mapper.",
                )
            return OptionIntentMappingResult(
                ok=False,
                error_code=UNSUPPORTED_INTENT,
                user_message="This signal intent is not supported for the requested action.",
            )

        if not self._side_allowed(option_side, side_preference):
            return OptionIntentMappingResult(
                ok=False,
                error_code=OPTION_SIDE_BLOCKED,
                user_message="This strategy instance is not allowed to trade that option side.",
            )
        return OptionIntentMappingResult(ok=True, option_side=option_side)

    def map_payload(self, payload: Any, *, instance: Any | None = None) -> OptionIntentMappingResult:
        return self.map(
            action=getattr(payload, "action", ""),
            intent=getattr(payload, "intent", ""),
            side_preference=side_preference_from_instance(instance),
        )

    @staticmethod
    def _option_side_for(action: str, intent: str) -> str | None:
        if action == "HEARTBEAT":
            return "NONE"
        if action == "EXIT" and intent == "FLAT":
            return "NONE"
        if action in {"ENTRY", "REVERSE"} and intent == "BULLISH":
            return "CE"
        if action in {"ENTRY", "REVERSE"} and intent == "BEARISH":
            return "PE"
        return None

    @staticmethod
    def _side_allowed(option_side: str, side_preference: str | None) -> bool:
        preference = str(side_preference or "BOTH").upper()
        if preference in {"", "BOTH", "AUTO", "NONE"} or option_side == "NONE":
            return True
        if preference == "CE":
            return option_side != "PE"
        if preference == "PE":
            return option_side != "CE"
        return True


def side_preference_from_instance(instance: Any | None) -> str | None:
    if instance is None:
        return None
    direct = getattr(instance, "side_preference", None)
    if direct:
        return str(direct)
    config = getattr(instance, "config_json", None) or {}
    if not isinstance(config, dict):
        return None
    for key in ("side_preference", "allowed_option_side", "side"):
        value = config.get(key)
        if value:
            return str(value)
    return None
