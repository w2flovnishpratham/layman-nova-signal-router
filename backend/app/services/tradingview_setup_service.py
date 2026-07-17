"""Manual TradingView setup state built on the existing private webhook pipeline."""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.db import crud, models
from app.db.engine import session_scope
from app.services import personal_pine_service as pine
from app.services import pine_conversion_service as conversion
from app.services.private_webhook_service import instance_strategy_name
from app.services.strategy_instance_service import _active_credential, _owned_instance

SETUP_TYPES = {"USER_MANAGED_TRADINGVIEW", "NOVA_MANAGED_TRADINGVIEW"}
ADMIN_STATES = {"SETUP_PENDING", "INSTALLATION_IN_PROGRESS", "ALERT_TEST_PENDING", "PAPER_VERIFICATION_PENDING", "BLOCKED", "RETIRED"}
EVIDENCE_KINDS = {"HOLD", "PAPER_ENTRY", "PAPER_EXIT", "REVERSAL"}
CLASSIFICATIONS = {"STATIC_VALIDATION_ONLY", "TRADINGVIEW_COMPILE_TEST", "REAL_TRADINGVIEW_ALERT_PAPER_TEST", "RECORDED_WEBHOOK_TEST", "SYNTHETIC_PAYLOAD_TEST", "BLOCKED"}
TRIAL_OUTCOMES = {"PASS", "FAIL", "BLOCKED"}
REQUIRED_TRIAL_TYPES = {
    "SMA_CROSSOVER", "SUPERTREND", "RSI_THRESHOLD", "EXPLICIT_EXIT", "OPPOSITE_REVERSAL",
    "STRATEGY_ENTRY_CLOSE", "INDICATOR_ALERTCONDITION", "PINE_V5", "PINE_V6", "UNSUPPORTED_UNSAFE",
}
_SECRET_KEY = re.compile(r"password|cookie|session|token|credential|secret|totp", re.I)


class SetupError(ValueError):
    def __init__(self, message: str, status_code: int = 400, code: str | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _json_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def ensure_prompt_version(db, prompt_id: str) -> models.PinePromptVersion:
    path = conversion.prompt_path(prompt_id)
    if not path.exists():
        raise SetupError("Configured prompt artifact was not found.", 500, "PROMPT_NOT_FOUND")
    content_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    row = db.get(models.PinePromptVersion, prompt_id)
    if row is None:
        row = models.PinePromptVersion(
            id=prompt_id,
            version_label=f"Manual conversion prompt {prompt_id}",
            content_sha256=content_hash,
            status="QUALIFICATION",
            change_notes="Immutable manual conversion prompt artifact.",
        )
        db.add(row)
        db.flush()
    elif row.content_sha256 != content_hash:
        raise SetupError("Configured prompt content does not match its immutable version record.", 409, "PROMPT_HASH_MISMATCH")
    return row


def ensure_current_prompt(db) -> models.PinePromptVersion:
    return ensure_prompt_version(db, conversion.manual_prompt_version())


def prompt_status() -> dict[str, Any]:
    with session_scope() as db:
        row = ensure_current_prompt(db)
        return _prompt_public(row)


def _prompt_public(row: models.PinePromptVersion) -> dict[str, Any]:
    return {
        "id": row.id, "version_label": row.version_label, "content_sha256": row.content_sha256,
        "status": row.status,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "approved_at": row.approved_at.isoformat() if row.approved_at else None,
        "ai_enabled": bool(settings.PINE_CONVERSION_AI_ENABLED),
    }


def accept_and_submit(user_id, strategy_id, candidate_id, payload) -> dict[str, Any]:
    with session_scope() as db:
        strategy, candidate = pine._owned_version(db, user_id, strategy_id, candidate_id, lock=True)
        _, original = pine._owned_version(db, user_id, strategy_id, payload.original_version_id)
        if pine._artifact(db, candidate.id).conversion_method == "ai_conversion_pending":
            raise SetupError("Accept the AI-generated candidate before submitting it.", 409, "CANDIDATE_NOT_ACCEPTED")
        if candidate.status != "ready_for_review":
            raise SetupError("Only a passing candidate can be accepted and submitted.", 409, "NOT_READY_FOR_REVIEW")
        report = pine._latest_report(db, candidate.id)
        if report is None or not report.eligible_for_review or report.source_sha256 != candidate.source_sha256:
            raise SetupError("The static validation evidence is missing or stale.", 409, "VALIDATION_STALE")
        prompt = ensure_current_prompt(db)
        if payload.prompt_version_id != prompt.id:
            raise SetupError("Unknown or inactive manual prompt version.", 422, "PROMPT_VERSION_INVALID")
        acceptance = db.scalar(select(models.PineUserAcceptance).where(models.PineUserAcceptance.candidate_version_id == candidate.id))
        if acceptance is None:
            report_hash = _json_hash({
                "id": str(report.id), "source": report.source_sha256, "validator": report.validator_version,
                "contract": report.contract_version, "status": report.status, "findings": report.findings,
            })
            acceptance = models.PineUserAcceptance(
                user_id=user_id, original_version_id=original.id, candidate_version_id=candidate.id,
                prompt_version_id=prompt.id, validation_report_id=report.id,
                setup_type=payload.setup_type,
                validation_report_sha256=report_hash,
                assumptions=[str(value)[:500] for value in payload.assumptions],
            )
            db.add(acceptance)
        candidate.status = "submitted"
        candidate.updated_at = _now()
        crud.add_audit_log(db, user_id=user_id, action="PERSONAL_PINE_ACCEPTED_AND_SUBMITTED", metadata={
            "strategy_id": str(strategy.id), "candidate_version_id": str(candidate.id),
            "original_version_id": str(original.id), "prompt_version": prompt.id,
            "validation_report_id": str(report.id),
        })
        db.flush()
        return {"version": pine._version_public(candidate, report), "acceptance": _acceptance_public(acceptance)}


def _acceptance_public(row) -> dict[str, Any]:
    return {
        "id": str(row.id), "user_id": str(row.user_id),
        "original_version_id": str(row.original_version_id), "candidate_version_id": str(row.candidate_version_id),
        "prompt_version_id": row.prompt_version_id, "validation_report_id": str(row.validation_report_id),
        "setup_type": row.setup_type,
        "validation_report_sha256": row.validation_report_sha256, "assumptions": row.assumptions or [],
        "accepted_at": row.accepted_at.isoformat() if row.accepted_at else None,
    }


def review_context(version_id) -> dict[str, Any]:
    with session_scope() as db:
        acceptance = db.scalar(select(models.PineUserAcceptance).where(models.PineUserAcceptance.candidate_version_id == uuid.UUID(str(version_id))))
        if acceptance is None:
            return {"acceptance": None, "setup": None}
        setup = db.scalar(select(models.TradingViewSetup).where(models.TradingViewSetup.approved_version_id == acceptance.candidate_version_id))
        return {"acceptance": _acceptance_public(acceptance), "setup": _setup_public(db, setup) if setup else None}


def require_acceptance(db, version_id) -> models.PineUserAcceptance:
    row = db.scalar(select(models.PineUserAcceptance).where(models.PineUserAcceptance.candidate_version_id == version_id))
    if row is None:
        raise SetupError("User acceptance is required before admin review.", 409, "USER_ACCEPTANCE_REQUIRED")
    return row


def create_setup(user_id, instance_id, setup_type: str, requested_timeframe: str | None) -> dict[str, Any]:
    if setup_type not in SETUP_TYPES:
        raise SetupError("Unsupported TradingView setup type.", 422, "INVALID_SETUP_TYPE")
    try:
        with session_scope() as db:
            instance = _owned_instance(db, user_id, instance_id, for_update=True)
            version = db.get(models.StrategyVersion, instance.strategy_version_id)
            if instance.source_journey != "PERSONAL_TRADINGVIEW" or version is None or version.status != "approved":
                raise SetupError("Link an approved personal Pine version before choosing setup.", 409, "APPROVED_VERSION_REQUIRED")
            acceptance = require_acceptance(db, version.id)
            if acceptance.setup_type != setup_type:
                raise SetupError("Reset the accepted setup choice before changing its type.", 409, "RESET_REQUIRED")
            existing = db.scalar(select(models.TradingViewSetup).where(models.TradingViewSetup.strategy_instance_id == instance.id))
            if existing:
                if existing.setup_type != setup_type:
                    raise SetupError("Reset the verified setup before changing its type.", 409, "RESET_REQUIRED")
                return _setup_public(db, existing)
            row = models.TradingViewSetup(
                user_id=user_id, strategy_instance_id=instance.id, approved_version_id=version.id,
                setup_type=setup_type, requested_timeframe=(requested_timeframe or "").strip() or None,
            )
            db.add(row); db.flush()
            crud.add_audit_log(db, user_id=user_id, action="TRADINGVIEW_SETUP_CREATED", metadata={"setup_id": str(row.id), "instance_id": str(instance.id), "setup_type": setup_type})
            return _setup_public(db, row)
    except IntegrityError as exc:
        raise SetupError("A TradingView setup already exists for this strategy instance.", 409, "SETUP_EXISTS") from exc


def get_setup(user_id, instance_id) -> dict[str, Any]:
    with session_scope() as db:
        instance = _owned_instance(db, user_id, instance_id)
        row = db.scalar(select(models.TradingViewSetup).where(models.TradingViewSetup.strategy_instance_id == instance.id))
        if row is None:
            raise SetupError("TradingView setup not found.", 404, "NOT_FOUND")
        return _setup_public(db, row)


def reset_setup(user_id, instance_id, setup_type: str, timeframe: str | None, reason: str) -> dict[str, Any]:
    with session_scope() as db:
        instance = _owned_instance(db, user_id, instance_id, for_update=True)
        row = db.scalar(select(models.TradingViewSetup).where(models.TradingViewSetup.strategy_instance_id == instance.id).with_for_update())
        if row is None:
            raise SetupError("TradingView setup not found.", 404, "NOT_FOUND")
        if setup_type not in SETUP_TYPES:
            raise SetupError("Unsupported TradingView setup type.", 422, "INVALID_SETUP_TYPE")
        row.setup_type, row.status = setup_type, "SETUP_PENDING"
        row.approved_version_id = instance.strategy_version_id
        row.requested_timeframe = (timeframe or "").strip() or None
        row.user_reported_compiled_at = row.installation_confirmed_at = None
        row.installation_metadata = None
        row.hold_signal_id = row.paper_entry_signal_id = row.paper_exit_signal_id = row.reversal_signal_id = None
        row.hold_verified_at = row.paper_entry_verified_at = row.paper_exit_verified_at = row.reversal_verified_at = None
        row.blocking_reason = row.admin_notes = None
        row.reset_count += 1; row.updated_at = _now()
        crud.add_audit_log(db, user_id=user_id, action="TRADINGVIEW_SETUP_RESET", metadata={"setup_id": str(row.id), "reason": reason[:500], "setup_type": setup_type})
        return _setup_public(db, row)


def report_compiled(user_id, instance_id) -> dict[str, Any]:
    with session_scope() as db:
        instance = _owned_instance(db, user_id, instance_id, for_update=True)
        row = _setup_for_instance(db, instance.id, lock=True)
        if row.setup_type != "USER_MANAGED_TRADINGVIEW":
            raise SetupError("Compilation reporting belongs to user-managed setup only.", 409, "ADMIN_MANAGED_SETUP")
        row.user_reported_compiled_at = _now()
        row.installation_confirmed_at = row.user_reported_compiled_at
        row.status = "ALERT_TEST_PENDING"; row.updated_at = _now()
        return _setup_public(db, row)


def _setup_for_instance(db, instance_id, *, lock=False):
    query = select(models.TradingViewSetup).where(models.TradingViewSetup.strategy_instance_id == instance_id)
    row = db.scalar(query.with_for_update() if lock else query)
    if row is None:
        raise SetupError("TradingView setup not found.", 404, "NOT_FOUND")
    return row


def _safe_installation(data: dict[str, Any]) -> dict[str, Any]:
    if any(_SECRET_KEY.search(key) for key in data):
        raise SetupError("TradingView secrets and credentials must not be stored.", 422, "SECRET_FIELD_REJECTED")
    return {key: value.isoformat() if isinstance(value, datetime) else str(value) for key, value in data.items()}


def record_installation(admin_id, setup_id, data: dict[str, Any]) -> dict[str, Any]:
    with session_scope() as db:
        row = db.scalar(select(models.TradingViewSetup).where(models.TradingViewSetup.id == uuid.UUID(str(setup_id))).with_for_update())
        if row is None:
            raise SetupError("TradingView setup not found.", 404, "NOT_FOUND")
        version = db.get(models.StrategyVersion, row.approved_version_id)
        if version is None or data["installed_version_hash"] != version.source_sha256:
            raise SetupError("Installed Pine hash must match the approved immutable version.", 409, "VERSION_HASH_MISMATCH")
        row.installation_metadata = _safe_installation(data)
        row.installation_confirmed_at = data["installed_at"]
        row.status = "ALERT_TEST_PENDING"; row.updated_at = _now()
        crud.add_audit_log(db, user_id=admin_id, action="TRADINGVIEW_INSTALLATION_RECORDED", metadata={"setup_id": str(row.id), "version_hash": version.source_sha256})
        return _setup_public(db, row, include_admin=True)


def record_verification(actor_id, instance_id, kind: str, signal_id: str, *, admin=False) -> dict[str, Any]:
    if kind not in EVIDENCE_KINDS:
        raise SetupError("Unsupported verification kind.", 422, "INVALID_EVIDENCE_KIND")
    with session_scope() as db:
        instance = db.get(models.StrategyInstance, uuid.UUID(str(instance_id))) if admin else _owned_instance(db, actor_id, instance_id)
        if instance is None:
            raise SetupError("Strategy instance not found.", 404, "NOT_FOUND")
        row = _setup_for_instance(db, instance.id, lock=True)
        if not admin and row.user_id != actor_id:
            raise SetupError("TradingView setup not found.", 404, "NOT_FOUND")
        _validate_signal_evidence(db, row, instance, kind, signal_id)
        now = _now()
        if kind == "HOLD": row.hold_signal_id, row.hold_verified_at = signal_id, now
        elif kind == "PAPER_ENTRY": row.paper_entry_signal_id, row.paper_entry_verified_at = signal_id, now
        elif kind == "PAPER_EXIT": row.paper_exit_signal_id, row.paper_exit_verified_at = signal_id, now
        else: row.reversal_signal_id, row.reversal_verified_at = signal_id, now
        row.status = "PAPER_VERIFICATION_PENDING"; row.updated_at = now
        crud.add_audit_log(db, user_id=actor_id, action="TRADINGVIEW_SERVER_EVIDENCE_PINNED", metadata={"setup_id": str(row.id), "kind": kind, "signal_id": signal_id})
        # Once HOLD + paper entry + paper exit are all confirmed, exit controlled
        # verification so the strategy becomes normally selectable/activatable.
        from app.services.strategy_instance_service import complete_verification_if_ready
        complete_verification_if_ready(db, instance)
        return _setup_public(db, row, include_admin=admin)


def _validate_signal_evidence(db, setup, instance, kind: str, signal_id: str) -> None:
    strategy_name = instance_strategy_name(instance.id)
    signal = db.scalar(select(models.StrategySignal).where(models.StrategySignal.strategy_name == strategy_name, models.StrategySignal.signal_id == signal_id))
    if signal is None or signal.status != "completed":
        raise SetupError("Completed durable signal evidence was not found.", 409, "SIGNAL_NOT_CONFIRMED")
    summary = signal.result_summary if isinstance(signal.result_summary, dict) else {}
    if kind == "HOLD":
        if summary.get("action") != "HOLD" or summary.get("reason") != "HOLD":
            raise SetupError("Signal is not confirmed HOLD evidence.", 409, "WRONG_SIGNAL_ACTION")
        return
    job = db.scalar(select(models.StrategyExecutionJob).where(
        models.StrategyExecutionJob.strategy_name == strategy_name,
        models.StrategyExecutionJob.signal_id == signal_id,
        models.StrategyExecutionJob.user_id == setup.user_id,
    ))
    if job is None or job.status != "completed" or job.execution_mode != "paper_live_data":
        raise SetupError("Confirmed PaperBroker execution evidence was not found.", 409, "PAPER_JOB_NOT_CONFIRMED")
    raw = job.signal_payload.get("raw_payload", {}) if isinstance(job.signal_payload, dict) else {}
    action = raw.get("normalized_action")
    result = job.result_summary if isinstance(job.result_summary, dict) else {}
    execution = result.get("execution_result", {}) if isinstance(result.get("execution_result"), dict) else {}
    if not execution.get("success"):
        raise SetupError("Paper execution did not report broker success.", 409, "PAPER_EXECUTION_FAILED")
    if kind == "PAPER_ENTRY" and action not in {"BUY_CE", "BUY_PE"}:
        raise SetupError("Signal is not a paper entry.", 409, "WRONG_SIGNAL_ACTION")
    if kind == "PAPER_EXIT" and action != "EXIT":
        raise SetupError("Signal is not a paper exit.", 409, "WRONG_SIGNAL_ACTION")
    if kind == "REVERSAL" and execution.get("status") != "REVERSAL_ORDER_PLACED":
        raise SetupError("Signal is not a confirmed paper reversal.", 409, "REVERSAL_NOT_CONFIRMED")


def _setup_public(db, row, *, include_admin=False) -> dict[str, Any]:
    instance = db.get(models.StrategyInstance, row.strategy_instance_id)
    version = db.get(models.StrategyVersion, row.approved_version_id)
    active_credential = bool(instance and _active_credential(db, instance.id))
    paper_enabled = bool(instance and instance.execution_mode == "paper_live_data")
    gates = {
        "approved_version": bool(version and version.status == "approved"),
        "installation_confirmed": row.installation_confirmed_at is not None,
        "hold_received": row.hold_verified_at is not None,
        "paper_entry_verified": row.paper_entry_verified_at is not None,
        "paper_exit_verified": row.paper_exit_verified_at is not None,
        "active_credential": active_credential,
        "paper_enabled": paper_enabled,
    }
    ready = all(gates.values()) and row.status not in {"BLOCKED", "RETIRED"}
    status = "READY" if ready else row.status
    pending = next((label for key, label in [
        ("approved_version", "Approved Pine version"), ("installation_confirmed", "TradingView installation"),
        ("active_credential", "Active private credential"), ("hold_received", "HOLD connectivity test"),
        ("paper_enabled", "Paper execution mode"), ("paper_entry_verified", "Confirmed paper entry"),
        ("paper_exit_verified", "Confirmed paper exit"),
    ] if not gates[key]), None)
    payload = {
        "id": str(row.id), "user_id": str(row.user_id), "strategy_instance_id": str(row.strategy_instance_id),
        "approved_version_id": str(row.approved_version_id), "setup_type": row.setup_type,
        "approved_source_sha256": version.source_sha256 if version else None,
        "status": status, "ready_for_paper": ready, "requested_timeframe": row.requested_timeframe,
        "user_reported_compiled_at": row.user_reported_compiled_at.isoformat() if row.user_reported_compiled_at else None,
        "installation_confirmed_at": row.installation_confirmed_at.isoformat() if row.installation_confirmed_at else None,
        "hold_verified_at": row.hold_verified_at.isoformat() if row.hold_verified_at else None,
        "paper_entry_verified_at": row.paper_entry_verified_at.isoformat() if row.paper_entry_verified_at else None,
        "paper_exit_verified_at": row.paper_exit_verified_at.isoformat() if row.paper_exit_verified_at else None,
        "reversal_verified_at": row.reversal_verified_at.isoformat() if row.reversal_verified_at else None,
        "blocking_reason": row.blocking_reason, "gates": gates, "blocking_step": pending,
        "who_acts_next": "Admin" if row.setup_type == "NOVA_MANAGED_TRADINGVIEW" and not row.installation_confirmed_at else "User",
        "reset_count": row.reset_count, "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }
    if include_admin:
        payload.update({
            "installation_metadata": row.installation_metadata, "admin_notes": row.admin_notes,
            "credential_status": "active" if active_credential else "missing_or_revoked",
        })
    return payload


def list_managed(*, limit=50, offset=0) -> dict[str, Any]:
    limit, offset = max(1, min(int(limit), 100)), max(0, int(offset))
    with session_scope() as db:
        query = select(models.TradingViewSetup).where(models.TradingViewSetup.setup_type == "NOVA_MANAGED_TRADINGVIEW")
        total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
        rows = db.scalars(query.order_by(models.TradingViewSetup.updated_at.desc()).limit(limit).offset(offset)).all()
        return {"setups": [_setup_public(db, row, include_admin=True) for row in rows], "total": total, "limit": limit, "offset": offset}


def admin_set_state(admin_id, setup_id, status: str, reason: str | None, notes: str | None) -> dict[str, Any]:
    if status not in ADMIN_STATES or status == "READY":
        raise SetupError("READY is derived only from server-observed gates.", 409, "READY_IS_DERIVED")
    if status in {"BLOCKED", "RETIRED"} and not (reason or "").strip():
        raise SetupError("A reason is required for blocked or retired setup.", 422, "REASON_REQUIRED")
    with session_scope() as db:
        row = db.scalar(select(models.TradingViewSetup).where(models.TradingViewSetup.id == uuid.UUID(str(setup_id))).with_for_update())
        if row is None: raise SetupError("TradingView setup not found.", 404, "NOT_FOUND")
        row.status, row.blocking_reason, row.admin_notes = status, (reason or "").strip() or None, (notes or "").strip() or None
        row.updated_at = _now()
        crud.add_audit_log(db, user_id=admin_id, action="TRADINGVIEW_SETUP_STATE_CHANGED", metadata={"setup_id": str(row.id), "status": status, "reason": row.blocking_reason})
        return _setup_public(db, row, include_admin=True)


def create_trial(admin_id, payload) -> dict[str, Any]:
    if payload.test_classification not in CLASSIFICATIONS or payload.outcome not in TRIAL_OUTCOMES:
        raise SetupError("Invalid qualification result.", 422, "INVALID_TRIAL")
    evidence = payload.evidence.model_dump(mode="json")
    if _contains_sensitive_key(evidence):
        raise SetupError("Qualification evidence must not contain credentials or session secrets.", 422, "SECRET_FIELD_REJECTED")
    with session_scope() as db:
        prompt = db.get(models.PinePromptVersion, payload.prompt_version_id) or ensure_current_prompt(db)
        if prompt.id != payload.prompt_version_id:
            raise SetupError("Prompt version not found.", 404, "PROMPT_NOT_FOUND")
        original, candidate = db.get(models.StrategyVersion, payload.original_version_id), db.get(models.StrategyVersion, payload.candidate_version_id)
        if original is None or candidate is None:
            raise SetupError("Pinned Pine version not found.", 404, "VERSION_NOT_FOUND")
        row = models.PinePromptQualificationTrial(
            prompt_version_id=prompt.id, original_version_id=original.id, candidate_version_id=candidate.id,
            strategy_type=payload.strategy_type.strip().upper(), test_classification=payload.test_classification,
            evidence=evidence, outcome=payload.outcome, notes=payload.notes,
            tester_user_id=admin_id, started_at=payload.started_at, completed_at=payload.completed_at,
        )
        db.add(row); db.flush()
        return _trial_public(row)


def _contains_sensitive_key(value: Any) -> bool:
    if isinstance(value, dict):
        return any(_SECRET_KEY.search(str(key)) or _contains_sensitive_key(item) for key, item in value.items())
    if isinstance(value, list):
        return any(_contains_sensitive_key(item) for item in value)
    return False


def _trial_public(row) -> dict[str, Any]:
    return {"id": str(row.id), "prompt_version_id": row.prompt_version_id, "original_version_id": str(row.original_version_id),
            "candidate_version_id": str(row.candidate_version_id), "strategy_type": row.strategy_type,
            "test_classification": row.test_classification, "evidence": row.evidence, "outcome": row.outcome,
            "notes": row.notes, "tester": str(row.tester_user_id) if row.tester_user_id else None,
            "started_at": row.started_at.isoformat() if row.started_at else None,
            "completed_at": row.completed_at.isoformat() if row.completed_at else None}


def list_trials(prompt_version_id: str, *, limit=100, offset=0) -> dict[str, Any]:
    limit, offset = max(1, min(int(limit), 100)), max(0, int(offset))
    with session_scope() as db:
        query = select(models.PinePromptQualificationTrial).where(models.PinePromptQualificationTrial.prompt_version_id == prompt_version_id)
        total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
        rows = db.scalars(query.order_by(models.PinePromptQualificationTrial.created_at.desc()).limit(limit).offset(offset)).all()
        return {"trials": [_trial_public(row) for row in rows], "total": total, "limit": limit, "offset": offset}


def approve_prompt(admin_id, prompt_id: str, notes: str) -> dict[str, Any]:
    with session_scope() as db:
        prompt = db.scalar(select(models.PinePromptVersion).where(models.PinePromptVersion.id == prompt_id).with_for_update())
        if prompt is None: raise SetupError("Prompt version not found.", 404, "PROMPT_NOT_FOUND")
        trials = db.scalars(select(models.PinePromptQualificationTrial).where(models.PinePromptQualificationTrial.prompt_version_id == prompt.id)).all()
        passed = {row.strategy_type for row in trials if row.outcome == "PASS"}
        if REQUIRED_TRIAL_TYPES - passed or any(row.outcome == "FAIL" for row in trials):
            raise SetupError("The mandatory qualification campaign has not passed.", 409, "QUALIFICATION_INCOMPLETE")
        supported = [row for row in trials if row.strategy_type != "UNSUPPORTED_UNSAFE"]
        if any(row.test_classification not in {"TRADINGVIEW_COMPILE_TEST", "REAL_TRADINGVIEW_ALERT_PAPER_TEST"} for row in supported):
            raise SetupError("Every supported sample requires genuine TradingView compilation evidence.", 409, "TRADINGVIEW_EVIDENCE_REQUIRED")
        for other in db.scalars(select(models.PinePromptVersion).where(models.PinePromptVersion.status == "APPROVED", models.PinePromptVersion.id != prompt.id)).all():
            other.status = "RETIRED"
        prompt.status, prompt.approved_by_user_id, prompt.approved_at, prompt.change_notes = "APPROVED", admin_id, _now(), notes
        crud.add_audit_log(db, user_id=admin_id, action="PINE_PROMPT_APPROVED_FOR_MANUAL_USE", metadata={"prompt_version": prompt.id, "content_sha256": prompt.content_sha256})
        return _prompt_public(prompt)
