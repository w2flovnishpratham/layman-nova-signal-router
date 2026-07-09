from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from app.config import DISABLED_OPTION_SL_PERCENT


class SetupState(StrEnum):
    IDLE = "IDLE"
    MODE_PICKED = "MODE_PICKED"
    STRATEGY_PICKED = "STRATEGY_PICKED"
    BROKER_CONNECTED = "BROKER_CONNECTED"
    RISK_CONFIGURED = "RISK_CONFIGURED"
    EXITS_CONFIGURED = "EXITS_CONFIGURED"
    READY_TO_LAUNCH = "READY_TO_LAUNCH"
    LIVE = "LIVE"
    PAUSED = "PAUSED"
    ENDED = "ENDED"


class StrategyPayload(BaseModel):
    strategy: Literal["supertrend"]


class ModePayload(BaseModel):
    engineMode: Literal["paper", "live"]
    paperStartingBalance: float = Field(default=100000.0, ge=10000.0, le=1000000.0)


class BrokerCredsPayload(BaseModel):
    clientId: str = Field(min_length=3, max_length=32)
    accessToken: str = Field(min_length=12)


class VerifySavedBrokerCredsPayload(BaseModel):
    # Display-only masked client ID (e.g. "******4633"); the real credential
    # is never sent by the client for this command, it's read server-side
    # from the encrypted vault.
    clientId: str = Field(default="", max_length=32)


class RiskPayload(BaseModel):
    maxTrades: int | None = Field(default=5, ge=1, le=50)
    maxLoss: int | None = Field(default=3000, ge=100)
    lots: int = Field(default=1, ge=1, le=25)
    side: Literal["CE", "PE", "BOTH"] = "BOTH"


class ExitRulesPayload(BaseModel):
    mode: Literal["flip_only", "flip_tp", "custom"] = "flip_only"
    targetProfit: int | None = Field(default=None, ge=100)
    targetPct: float = Field(default=5, gt=0, le=100)
    stopLossPct: float = Field(default=DISABLED_OPTION_SL_PERCENT, gt=0, lt=100)

    @field_validator("targetProfit")
    @classmethod
    def target_required_for_profit_mode(cls, value: int | None, info: Any) -> int | None:
        if info.data.get("mode") == "flip_tp" and value is None:
            raise ValueError("targetProfit is required when mode is flip_tp")
        return value

    @model_validator(mode="after")
    def custom_stop_loss_must_be_regular(self) -> "ExitRulesPayload":
        if self.mode == "custom" and self.stopLossPct >= 80:
            raise ValueError("stopLossPct must be less than 80 when custom SL is enabled")
        return self


class RiskAndExitsPayload(BaseModel):
    risk: RiskPayload
    exits: ExitRulesPayload


class PatchRiskPayload(BaseModel):
    maxLoss: int | None = Field(default=None, ge=100)
    side: Literal["CE", "PE", "BOTH"] | None = None


class StateTransitionError(ValueError):
    pass


SETUP_ORDER = {
    SetupState.IDLE: "setup.mode",
    SetupState.MODE_PICKED: "setup.select_strategy",
    SetupState.STRATEGY_PICKED: "setup.broker_creds",
    SetupState.BROKER_CONNECTED: "setup.risk",
    SetupState.RISK_CONFIGURED: "setup.exits",
    SetupState.EXITS_CONFIGURED: "setup.confirm_live",
    SetupState.READY_TO_LAUNCH: "setup.confirm_live",
}


def validate_command(state: SetupState, command_type: str, data: dict[str, Any]) -> tuple[SetupState, dict[str, Any]]:
    try:
        if command_type == "setup.mode":
            _require_state(state, SetupState.IDLE, command_type)
            payload = ModePayload.model_validate(data).model_dump()
            return SetupState.MODE_PICKED, {
                "engineMode": payload["engineMode"],
                "paper": {"startingBalance": payload["paperStartingBalance"]},
            }

        if command_type == "setup.select_strategy":
            _require_state(state, SetupState.MODE_PICKED, command_type)
            payload = StrategyPayload.model_validate(data).model_dump()
            return SetupState.STRATEGY_PICKED, {"strategy": payload["strategy"]}

        if command_type == "setup.broker_creds":
            _require_state(state, SetupState.STRATEGY_PICKED, command_type)
            payload = BrokerCredsPayload.model_validate(data).model_dump()
            return SetupState.BROKER_CONNECTED, {
                "broker": {
                    "clientId": payload["clientId"],
                    "status": "verified",
                }
            }

        if command_type == "setup.verify_saved_broker_creds":
            _require_state(state, SetupState.STRATEGY_PICKED, command_type)
            payload = VerifySavedBrokerCredsPayload.model_validate(data).model_dump()
            return SetupState.BROKER_CONNECTED, {
                "broker": {
                    "clientId": payload["clientId"],
                    "status": "verified",
                }
            }

        if command_type == "setup.use_shared_data":
            # Paper mode on the shared market-data account — no user creds.
            _require_state(state, SetupState.STRATEGY_PICKED, command_type)
            return SetupState.BROKER_CONNECTED, {
                "broker": {
                    "clientId": "",
                    "status": "shared",
                }
            }

        if command_type == "setup.risk":
            if state not in {SetupState.BROKER_CONNECTED, SetupState.RISK_CONFIGURED}:
                _require_state(state, SetupState.BROKER_CONNECTED, command_type)
            payload = RiskPayload.model_validate(data).model_dump()
            return SetupState.RISK_CONFIGURED, {"risk": payload}

        if command_type == "setup.risk_and_exits":
            _require_state(state, SetupState.BROKER_CONNECTED, command_type)
            payload = RiskAndExitsPayload.model_validate(data)
            return SetupState.EXITS_CONFIGURED, {
                "risk": payload.risk.model_dump(),
                "exits": payload.exits.model_dump(),
            }

        if command_type == "setup.exits":
            _require_state(state, SetupState.RISK_CONFIGURED, command_type)
            payload = ExitRulesPayload.model_validate(data).model_dump()
            return SetupState.EXITS_CONFIGURED, {"exits": payload}

        if command_type == "setup.confirm_live":
            if state not in {SetupState.EXITS_CONFIGURED, SetupState.READY_TO_LAUNCH}:
                raise StateTransitionError("Finish setup before confirming live trading.")
            return SetupState.LIVE, {"live": {"confirmed": True}}

        if command_type == "session.pause":
            if state not in {SetupState.LIVE, SetupState.PAUSED}:
                raise StateTransitionError(f"{command_type} is not allowed while session state is {state}.")
            return SetupState.PAUSED, {}

        if command_type == "session.resume":
            if state not in {SetupState.LIVE, SetupState.PAUSED}:
                raise StateTransitionError(f"{command_type} is not allowed while session state is {state}.")
            return SetupState.LIVE, {}

        if command_type == "session.exit_open":
            if state not in {SetupState.LIVE, SetupState.PAUSED}:
                raise StateTransitionError("Open-position exit is only available while the engine is running or paused.")
            return state, {}

        if command_type == "session.apply_sr_suggestion":
            if state not in {SetupState.LIVE, SetupState.PAUSED}:
                raise StateTransitionError("Suggested SL/TP can only be applied while the engine is running or paused.")
            return state, {}

        if command_type == "session.kill":
            if state not in {SetupState.LIVE, SetupState.PAUSED, SetupState.READY_TO_LAUNCH}:
                raise StateTransitionError("There is no live session to stop.")
            return SetupState.ENDED, {}

        if command_type == "session.patch_risk":
            if state not in {SetupState.LIVE, SetupState.PAUSED}:
                raise StateTransitionError("Risk can only be changed while the session is live or paused.")
            payload = PatchRiskPayload.model_validate(data).model_dump(exclude_none=True)
            return state, {"risk_patch": payload}
    except ValidationError as exc:
        raise StateTransitionError(_validation_message(exc)) from exc

    raise StateTransitionError(f"Unsupported command: {command_type}")


def next_prompt_for(state: SetupState) -> str:
    if state == SetupState.IDLE:
        return "Choose Paper mode for simulation or Live mode for real-money routing."
    if state == SetupState.MODE_PICKED:
        return "Which strategy should we run today?"
    if state == SetupState.STRATEGY_PICKED:
        return "Share your Dhan Client ID and today's access token. I will verify the account before the engine starts."
    if state == SetupState.BROKER_CONNECTED:
        return "Account verified. Choose whether NOVA should route CE entries, PE entries, or both."
    if state == SetupState.RISK_CONFIGURED:
        return "Daily limits saved. Locking the exit rules now."
    if state == SetupState.EXITS_CONFIGURED:
        return "Setup complete. Review the configuration and start the engine."
    if state == SetupState.LIVE:
        return "ENGINE RUNNING & LISTENING for TradingView Supertrend webhook alerts."
    if state == SetupState.PAUSED:
        return "Session is paused after the current trade. Resume when you want signals routed again."
    if state == SetupState.ENDED:
        return "Session ended. No new signals will be routed."
    return "Continue setup."


def _require_state(actual: SetupState, expected: SetupState, command_type: str) -> None:
    if actual != expected:
        expected_command = SETUP_ORDER.get(actual)
        if expected_command:
            raise StateTransitionError(f"{command_type} is out of order. Expected {expected_command}.")
        raise StateTransitionError(f"{command_type} is not allowed while session state is {actual}.")


def _validation_message(exc: ValidationError) -> str:
    first = exc.errors()[0]
    field = ".".join(str(part) for part in first.get("loc", []))
    message = first.get("msg", "Invalid input")
    return f"{field}: {message}" if field else message
