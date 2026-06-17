from __future__ import annotations

import argparse
import json
import sys
import uuid
from dataclasses import asdict, dataclass
from typing import Any

import httpx

from app.config import settings
from app.db.engine import database_configured
from app.services import strategy_fanout
from app.services.dhan_client import DHAN_BASE_URL


@dataclass
class CheckResult:
    name: str
    ok: bool
    message: str
    detail: dict[str, Any] | None = None


def _http_check(name: str, url: str, *, timeout: float, allow_any_http: bool = False) -> CheckResult:
    try:
        response = httpx.get(url, timeout=timeout)
        if allow_any_http:
            return CheckResult(
                name=name,
                ok=True,
                message=f"HTTP path reachable with status {response.status_code}.",
                detail={"url": url, "status_code": response.status_code},
            )
        response.raise_for_status()
    except Exception as exc:
        return CheckResult(
            name=name,
            ok=False,
            message=f"{name} failed: {type(exc).__name__}: {exc}",
            detail={"url": url},
        )
    return CheckResult(
        name=name,
        ok=True,
        message=f"{name} returned {response.status_code}.",
        detail={"url": url, "status_code": response.status_code},
    )


def _proxy_ip_check(node: dict[str, str], *, timeout: float) -> CheckResult:
    expected_ip = node["public_ip"]
    try:
        with httpx.Client(proxy=node["proxy_url"], timeout=timeout) as client:
            response = client.get("https://api.ipify.org?format=json")
            response.raise_for_status()
            observed_ip = str(response.json().get("ip") or "").strip()
    except Exception as exc:
        return CheckResult(
            name=f"egress_proxy_ip:{expected_ip}",
            ok=False,
            message=f"Proxy IP check failed for {expected_ip}: {type(exc).__name__}: {exc}",
            detail={"expected_ip": expected_ip, "observed_ip": None},
        )
    ok = observed_ip == expected_ip
    return CheckResult(
        name=f"egress_proxy_ip:{expected_ip}",
        ok=ok,
        message=(
            f"Proxy observed IP matched {expected_ip}."
            if ok
            else f"Proxy observed {observed_ip}, expected {expected_ip}."
        ),
        detail={"expected_ip": expected_ip, "observed_ip": observed_ip},
    )


def _dhan_path_check(node: dict[str, str], *, timeout: float) -> CheckResult:
    expected_ip = node["public_ip"]
    dhan_url = f"{DHAN_BASE_URL.rstrip('/')}/profile"
    try:
        with httpx.Client(proxy=node["proxy_url"], timeout=timeout) as client:
            response = client.get(dhan_url)
            # Any HTTP response means DNS/TLS/proxy path to Dhan is alive. Auth
            # failures are expected because this check never uses user tokens.
            if response.status_code >= 500:
                return CheckResult(
                    name=f"dhan_path:{expected_ip}",
                    ok=False,
                    message=f"Dhan path reached server error {response.status_code} via {expected_ip}.",
                    detail={"expected_ip": expected_ip, "status_code": response.status_code, "url": dhan_url},
                )
    except Exception as exc:
        return CheckResult(
            name=f"dhan_path:{expected_ip}",
            ok=False,
            message=f"Dhan path check failed for {expected_ip}: {type(exc).__name__}: {exc}",
            detail={"expected_ip": expected_ip, "url": dhan_url},
        )
    return CheckResult(
        name=f"dhan_path:{expected_ip}",
        ok=True,
        message=f"Dhan API path reachable via {expected_ip} with status {response.status_code}.",
        detail={"expected_ip": expected_ip, "status_code": response.status_code, "url": dhan_url},
    )


def _verify_active_assignments(*, timeout: float, failed_ips: dict[str, str], no_db_update: bool) -> list[CheckResult]:
    if not database_configured():
        if settings.EXECUTION_NODE_ROUTING_ENABLED or settings.ENABLE_LIVE_ORDERS:
            return [
                CheckResult(
                    name="active_user_egress",
                    ok=False,
                    message="DATABASE_URL is not configured; cannot verify user egress assignments.",
                )
            ]
        return [
            CheckResult(
                name="active_user_egress",
                ok=True,
                message="Database is not configured; skipping user egress assignment verification.",
            )
        ]

    try:
        assignments = strategy_fanout.active_user_egress_assignments()
    except Exception as exc:
        return [
            CheckResult(
                name="active_user_egress",
                ok=False,
                message=f"Could not load user egress assignments: {type(exc).__name__}: {exc}",
            )
        ]
    if not assignments:
        return [
            CheckResult(
                name="active_user_egress",
                ok=not (settings.EXECUTION_NODE_ROUTING_ENABLED or settings.ENABLE_LIVE_ORDERS),
                message="No active user egress assignments were found.",
            )
        ]

    results: list[CheckResult] = []
    for assignment in assignments:
        user_id = str(assignment["user_id"])
        public_ip = str(assignment.get("public_ip") or "")
        if public_ip in failed_ips:
            if not no_db_update:
                try:
                    strategy_fanout.mark_user_egress_unverified(
                        user_id,
                        error=failed_ips[public_ip],
                        observed_ip=str(assignment.get("last_observed_ip") or "") or None,
                    )
                except Exception as exc:
                    results.append(
                        CheckResult(
                            name=f"user_egress_mark_unverified:{user_id}",
                            ok=False,
                            message=f"Could not clear user egress verification: {type(exc).__name__}: {exc}",
                        )
                    )
            results.append(
                CheckResult(
                    name=f"user_egress:{user_id}",
                    ok=False,
                    message=f"Assigned egress {public_ip} failed infrastructure verification.",
                    detail={"user_id": user_id, "public_ip": public_ip, "error": failed_ips[public_ip]},
                )
            )
            continue

        if no_db_update:
            results.append(
                CheckResult(
                    name=f"user_egress:{user_id}",
                    ok=bool(assignment.get("verified")),
                    message="User egress assignment read without updating database.",
                    detail=assignment,
                )
            )
            continue

        try:
            verification = strategy_fanout.verify_user_egress(uuid.UUID(user_id), timeout=timeout)
        except Exception as exc:
            results.append(
                CheckResult(
                    name=f"user_egress:{user_id}",
                    ok=False,
                    message=f"User egress verification failed before proxy check: {type(exc).__name__}: {exc}",
                    detail={"user_id": user_id, "public_ip": public_ip},
                )
            )
            continue
        results.append(
            CheckResult(
                name=f"user_egress:{user_id}",
                ok=bool(verification.get("ok")),
                message=(
                    f"User egress verified as {verification.get('observed_ip')}."
                    if verification.get("ok")
                    else f"User egress verification failed: {verification.get('error')}"
                ),
                detail={"user_id": user_id, **verification},
            )
        )
    return results


def _default_public_health_url() -> str:
    return f"{settings.BACKEND_PUBLIC_BASE_URL.rstrip('/')}/api/health"


def _default_local_health_url() -> str:
    return f"http://127.0.0.1:{settings.BACKEND_PORT}/api/health"


def run_checks(args: argparse.Namespace) -> list[CheckResult]:
    results: list[CheckResult] = []
    results.append(_http_check("backend_health", args.backend_url, timeout=args.timeout))
    if not args.skip_public_health:
        results.append(_http_check("nginx_public_health", args.public_url, timeout=args.timeout))

    failed_ips: dict[str, str] = {}
    try:
        nodes = strategy_fanout.configured_egress_nodes()
    except Exception as exc:
        nodes = []
        message = f"Configured egress nodes are invalid: {type(exc).__name__}: {exc}"
        results.append(CheckResult(name="egress_config", ok=False, message=message))

    if not nodes:
        results.append(
            CheckResult(
                name="egress_nodes",
                ok=not (settings.EXECUTION_NODE_ROUTING_ENABLED or settings.ENABLE_LIVE_ORDERS),
                message="No egress nodes are configured.",
            )
        )
    for node in nodes:
        ip_result = _proxy_ip_check(node, timeout=args.timeout)
        dhan_result = _dhan_path_check(node, timeout=args.timeout)
        results.extend([ip_result, dhan_result])
        if not ip_result.ok:
            failed_ips[node["public_ip"]] = ip_result.message
        elif not dhan_result.ok:
            failed_ips[node["public_ip"]] = dhan_result.message

    if args.skip_user_egress:
        results.append(
            CheckResult(
                name="active_user_egress",
                ok=True,
                message="User egress assignment verification skipped by CLI flag.",
            )
        )
    else:
        results.extend(
            _verify_active_assignments(
                timeout=args.timeout,
                failed_ips=failed_ips,
                no_db_update=args.no_db_update,
            )
        )
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="NOVA pre-market production healthcheck.")
    parser.add_argument("--backend-url", default=_default_local_health_url())
    parser.add_argument("--public-url", default=_default_public_health_url())
    parser.add_argument("--skip-public-health", action="store_true")
    parser.add_argument("--timeout", type=float, default=8.0)
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    parser.add_argument("--no-db-update", action="store_true", help="Do not update user egress verification rows.")
    parser.add_argument("--skip-user-egress", action="store_true", help="Skip active user egress DB checks.")
    args = parser.parse_args()

    results = run_checks(args)
    ok = all(result.ok for result in results)
    payload = {
        "ok": ok,
        "live_gates": {
            "enable_live_orders": settings.ENABLE_LIVE_ORDERS,
            "execution_node_routing_enabled": settings.EXECUTION_NODE_ROUTING_ENABLED,
            "dhan_read_only_real_data": settings.DHAN_READ_ONLY_REAL_DATA,
        },
        "results": [asdict(result) for result in results],
    }

    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=False))
    else:
        print(f"NOVA pre-market healthcheck: {'OK' if ok else 'FAILED'}")
        for result in results:
            status = "OK" if result.ok else "FAIL"
            print(f"[{status}] {result.name}: {result.message}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
