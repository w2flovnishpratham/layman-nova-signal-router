"""Controlled service-only runner for v2 paper fanout execution.

This module is intentionally not imported by startup, routes, or workers. It is
for tests and explicit internal/manual calls only.
"""
from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import select

from app.core.enums import StrategyExecutionMode
from app.core.feature_flags import MULTI_STRATEGY_FANOUT, is_feature_enabled
from app.db import models
from app.services.strategy_execution_queue_v2 import (
    V2_LOCKED,
    V2_PENDING,
    create_v2_jobs_from_plan,
)
from app.services.strategy_fanout_v2 import (
    FEATURE_FLAG_DISABLED,
    FanoutPlan,
    FanoutPlanResult,
    FanoutSkip,
    plan_strategy_fanout_v2,
)
from app.services.strategy_worker_adapter_v2 import (
    V2_COMPLETED_PAPER,
    V2_FAILED_PAPER,
    process_v2_job_paper_once,
)


MANUAL_V2_PAPER_WORKER_ID = "manual-v2-paper-runner"
PAPER_EXECUTION_MODE_SKIPPED = "PAPER_EXECUTION_MODE_SKIPPED"
NON_PAPER_JOB_SKIPPED = "NON_PAPER_JOB_SKIPPED"


@dataclass
class V2PaperFanoutRunResult:
    ok: bool
    strategy_code: str
    signal_id: str | None
    planned_count: int = 0
    job_created_count: int = 0
    processed_count: int = 0
    completed_count: int = 0
    failed_count: int = 0
    skipped_count: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)
    job_results: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return _sanitize(asdict(self))


def run_v2_paper_fanout_once(
    db: Any,
    raw_payload: dict[str, Any],
    strategy_code: str,
    worker_id: str = MANUAL_V2_PAPER_WORKER_ID,
    max_jobs: int = 10,
) -> V2PaperFanoutRunResult:
    """Plan, queue, and process one controlled v2 paper fanout cycle."""
    requested_code = str(strategy_code or raw_payload.get("strategy_code") or "").strip()
    signal_id = _raw_signal_id(raw_payload)
    if not is_feature_enabled(MULTI_STRATEGY_FANOUT):
        return V2PaperFanoutRunResult(
            ok=False,
            strategy_code=requested_code,
            signal_id=signal_id,
            errors=[
                {
                    "reason": FEATURE_FLAG_DISABLED,
                    "message": "Multi-strategy fanout is disabled.",
                }
            ],
        )

    plan_result = plan_strategy_fanout_v2(db, raw_payload, requested_code, dry_run=False)
    filtered_plan, runner_skips = _paper_only_plan_result(plan_result)
    job_result = create_v2_jobs_from_plan(db, filtered_plan)

    job_results = _process_job_refs(
        db,
        job_result.jobs,
        worker_id=worker_id,
        max_jobs=max_jobs,
    )
    completed_count = sum(1 for item in job_results if item.get("ok") is True)
    failed_count = sum(1 for item in job_results if item.get("ok") is False)
    errors = _issues_as_errors(plan_result.errors) + _issues_as_errors(job_result.errors)
    errors.extend(item for item in job_results if item.get("ok") is False)

    return V2PaperFanoutRunResult(
        ok=plan_result.ok and job_result.ok and failed_count == 0,
        strategy_code=plan_result.strategy_code,
        signal_id=plan_result.signal_id,
        planned_count=len(filtered_plan.plans),
        job_created_count=job_result.created_count,
        processed_count=len(job_results),
        completed_count=completed_count,
        failed_count=failed_count,
        skipped_count=job_result.skipped_count,
        errors=_sanitize(errors),
        job_results=_sanitize(job_results),
    )


def process_ready_v2_paper_jobs_once(
    db: Any,
    worker_id: str = MANUAL_V2_PAPER_WORKER_ID,
    max_jobs: int = 10,
) -> V2PaperFanoutRunResult:
    """Process currently pending v2 paper jobs without planning new work."""
    job_results = []
    for _ in range(_max_jobs(max_jobs)):
        job = _claim_next_pending_paper_job(db, worker_id)
        if job is None:
            break
        job_results.append(_paper_result_dict(process_v2_job_paper_once(db, job, worker_id)))

    completed_count = sum(1 for item in job_results if item.get("ok") is True)
    failed_count = sum(1 for item in job_results if item.get("ok") is False)
    return V2PaperFanoutRunResult(
        ok=failed_count == 0,
        strategy_code="",
        signal_id=None,
        processed_count=len(job_results),
        completed_count=completed_count,
        failed_count=failed_count,
        errors=_sanitize([item for item in job_results if item.get("ok") is False]),
        job_results=_sanitize(job_results),
    )


def _paper_only_plan_result(plan_result: FanoutPlanResult) -> tuple[FanoutPlanResult, list[FanoutSkip]]:
    paper_plans: list[FanoutPlan] = []
    runner_skips: list[FanoutSkip] = []
    for plan in plan_result.plans:
        if str(plan.execution_mode or "") == StrategyExecutionMode.PAPER_LIVE_DATA.value:
            paper_plans.append(plan)
            continue
        runner_skips.append(
            FanoutSkip(
                reason=PAPER_EXECUTION_MODE_SKIPPED,
                user_id=plan.user_id,
                instance_id=plan.instance_id,
                execution_mode=plan.execution_mode,
                details={"supported_execution_mode": StrategyExecutionMode.PAPER_LIVE_DATA.value},
            )
        )

    return (
        FanoutPlanResult(
            ok=plan_result.ok,
            strategy_code=plan_result.strategy_code,
            strategy_version_id=plan_result.strategy_version_id,
            signal_id=plan_result.signal_id,
            subscriber_count=plan_result.subscriber_count,
            planned_count=len(paper_plans),
            skipped_count=len(plan_result.skips) + len(runner_skips),
            error_count=plan_result.error_count,
            plans=paper_plans,
            skips=[*plan_result.skips, *runner_skips],
            errors=plan_result.errors,
            duplicate=plan_result.duplicate,
            dry_run=plan_result.dry_run,
            signal_row_id=plan_result.signal_row_id,
        ),
        runner_skips,
    )


def _process_job_refs(
    db: Any,
    job_refs: list[Any],
    *,
    worker_id: str,
    max_jobs: int,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for job_ref in job_refs[: _max_jobs(max_jobs)]:
        job = db.get(models.StrategyExecutionJob, job_ref.job_id)
        claimed = _claim_specific_paper_job(db, job, worker_id)
        if claimed is None:
            results.append(
                {
                    "ok": False,
                    "job_id": str(job_ref.job_id),
                    "status": "skipped",
                    "error_code": NON_PAPER_JOB_SKIPPED,
                    "user_message": "The job was not pending paper_live_data work.",
                }
            )
            continue
        results.append(_paper_result_dict(process_v2_job_paper_once(db, claimed, worker_id)))
    return results


def _claim_next_pending_paper_job(db: Any, worker_id: str) -> models.StrategyExecutionJob | None:
    statement = (
        select(models.StrategyExecutionJob)
        .where(
            models.StrategyExecutionJob.status == V2_PENDING,
            models.StrategyExecutionJob.execution_mode == StrategyExecutionMode.PAPER_LIVE_DATA.value,
        )
        .order_by(models.StrategyExecutionJob.created_at.asc(), models.StrategyExecutionJob.id.asc())
    )
    for job in db.scalars(statement).all():
        claimed = _claim_specific_paper_job(db, job, worker_id)
        if claimed is not None:
            return claimed
    return None


def _claim_specific_paper_job(
    db: Any,
    job: models.StrategyExecutionJob | None,
    worker_id: str,
) -> models.StrategyExecutionJob | None:
    if job is None:
        return None
    if job.status != V2_PENDING:
        return None
    if str(job.execution_mode or "") != StrategyExecutionMode.PAPER_LIVE_DATA.value:
        return None
    if _has_locked_paper_job_for_instance(db, user_id=job.user_id, instance_id=job.instance_id):
        return None
    now = models.utcnow()
    job.status = V2_LOCKED
    job.locked_at = now
    job.locked_by = worker_id
    job.attempts += 1
    job.updated_at = now
    db.flush()
    return job


def _has_locked_paper_job_for_instance(
    db: Any,
    *,
    user_id: uuid.UUID,
    instance_id: uuid.UUID | None,
) -> bool:
    if instance_id is None:
        return False
    row = db.scalar(
        select(models.StrategyExecutionJob.id).where(
            models.StrategyExecutionJob.status == V2_LOCKED,
            models.StrategyExecutionJob.user_id == user_id,
            models.StrategyExecutionJob.instance_id == instance_id,
            models.StrategyExecutionJob.execution_mode == StrategyExecutionMode.PAPER_LIVE_DATA.value,
        )
    )
    return row is not None


def _paper_result_dict(result: Any) -> dict[str, Any]:
    data = result.as_dict() if hasattr(result, "as_dict") else {}
    status = data.get("status")
    data["ok"] = bool(data.get("ok"))
    if status == V2_COMPLETED_PAPER:
        data["ok"] = True
    if status == V2_FAILED_PAPER:
        data["ok"] = False
    return _sanitize(data)


def _issues_as_errors(issues: list[Any]) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    for issue in issues:
        if hasattr(issue, "as_dict"):
            errors.append(issue.as_dict())
        elif hasattr(issue, "__dataclass_fields__"):
            errors.append(asdict(issue))
        elif isinstance(issue, dict):
            errors.append(dict(issue))
    return _sanitize(errors)


def _max_jobs(value: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return max(parsed, 0)


def _raw_signal_id(raw_payload: dict[str, Any]) -> str | None:
    value = raw_payload.get("signal_id") if isinstance(raw_payload, dict) else None
    return str(value) if value not in (None, "") else None


def _sanitize(value: Any) -> Any:
    redacted_keys = {
        "access_token",
        "headers",
        "proxy_url",
        "raw_payload",
        "raw_response",
        "request",
        "response_json",
        "response_text",
        "secret",
    }
    if is_dataclass(value) and not isinstance(value, type):
        return _sanitize(asdict(value))
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            if str(key).lower() in redacted_keys:
                continue
            sanitized[str(key)] = _sanitize(item)
        return sanitized
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize(item) for item in value]
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value
